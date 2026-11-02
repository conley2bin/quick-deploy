"""Validate observer/isolation helpers with fake credentials, never real Pi."""
import asyncio
import json
import os
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import patch

from native_acceptance import FileEvents, assert_handoff_mutations, check_isolation


class NativeHarnessTests(unittest.IsolatedAsyncioTestCase):
    def test_current_mutation_chain_includes_one_bound_successor_rename(self):
        operation = {"source_session_id": "source/opaque", "target_session_id": "target/opaque",
                     "target_display_name": "原名_02"}
        calls = [{"path": path, "body": body} for path, body in [
            ("/api/sessions", {}),
            ("/api/sessions/target%2Fopaque/rename", {"name": "原名_02"}),
            ("/api/sessions/source%2Fopaque/send", {}),
            ("/api/sessions/target%2Fopaque/send", {}),
        ]]
        assert_handoff_mutations(calls, operation)
        for invalid in [calls[:1] + calls[2:], calls[:2] + calls[1:],
                        [calls[0], {"path": "/api/sessions/source%2Fopaque/rename", "body": calls[1]["body"]}, *calls[2:]],
                        [calls[0], {"path": calls[1]["path"], "body": {"name": "原名_03"}}, *calls[2:]]]:
            with self.subTest(calls=invalid), self.assertRaises(AssertionError):
                assert_handoff_mutations(invalid, operation)

    async def test_file_notifications_observe_resolution_before_task_change(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            watcher = FileEvents(root)
            try:
                resolution = root / "resolution.json"
                main = root / "main.py"
                async def published():
                    return any(item["path"] == str(resolution) for item in watcher.observations)
                resolution.write_text('{"verdict":"ready"}')
                await watcher.until(published, 1)
                main.write_text('def result():\n    return "Y"\n')
                async def changed():
                    return any(item["path"] == str(main) for item in watcher.observations)
                await watcher.until(changed, 1)
                paths = [item["path"] for item in watcher.observations]
                self.assertLess(paths.index(str(resolution)), paths.index(str(main)))
            finally:
                watcher.close()

    async def test_removed_directory_does_not_abort_event_processing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            watcher = FileEvents(root)
            try:
                # Registration after a transient native lock directory vanished.
                watcher._add_tree(root / "already-removed-lock")
                (root / "receipt.json").write_text("{}")
                async def receipt_seen():
                    return any(item["path"] == str(root / "receipt.json")
                               for item in watcher.observations)
                await watcher.until(receipt_seen, 1)
            finally:
                watcher.close()

    async def test_notification_wait_has_a_finite_deadline(self):
        with tempfile.TemporaryDirectory() as temp:
            watcher = FileEvents(Path(temp))
            try:
                async def absent():
                    return False
                with self.assertRaises(TimeoutError):
                    await watcher.until(absent, 0.01)
            finally:
                watcher.close()

    async def test_real_refresh_and_short_ttl_are_rejected_before_spawning(self):
        with tempfile.TemporaryDirectory(prefix="codoxear-handoff-acceptance-", dir="/tmp") as temp:
            root = Path(temp)
            agent = root / "home/.pi/agent"
            agent.mkdir(parents=True)
            env = {"HOME": str(root / "home"), "PI_HOME": str(root / "home/.pi"),
                   "PI_CODING_AGENT_DIR": str(agent)}
            auth = {"openai": {"type": "oauth", "access": "FAKE_UNIT_TEST_ACCESS",
                               "refresh": "TEST_ONLY_REFRESH_DISABLED", "expires": (time.time() + 2400) * 1000}}
            (agent / "auth.json").write_text(json.dumps(auth))
            with socket.socket(socket.AF_UNIX) as owned_socket, patch.dict(os.environ, env):
                owned_socket.bind(str(root / "tmux.sock"))
                self.assertEqual(check_isolation(root), root)
                auth["openai"]["refresh"] = "NOT_AN_ALLOWED_TEST_SENTINEL"
                (agent / "auth.json").write_text(json.dumps(auth))
                with self.assertRaisesRegex(ValueError, "access-only"):
                    check_isolation(root)
                auth["openai"]["refresh"] = "TEST_ONLY_REFRESH_DISABLED"
                auth["openai"]["expires"] = (time.time() + 30) * 1000
                (agent / "auth.json").write_text(json.dumps(auth))
                with self.assertRaisesRegex(ValueError, "TTL"):
                    check_isolation(root)


if __name__ == "__main__":
    unittest.main()
