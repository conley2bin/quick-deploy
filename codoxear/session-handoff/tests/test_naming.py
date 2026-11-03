"""Name sequencing is display state; native launch/header remain identity."""
import unittest

import test_engine as fixture
from test_engine import Engine, HandoffError, engine_module


class SequenceTests(unittest.TestCase):
    def test_exact_terminal_ascii_sequence(self):
        for source, expected in (
            ("plain", "plain_01"), ("中文 空格 '引号' $;\"", "中文 空格 '引号' $;\"_01"),
            ("source_00", "source_01"), ("source_01", "source_02"),
            ("source_09", "source_10"), ("source_98", "source_99"),
            ("source_1", "source_1_01"), ("source_001", "source_001_01"),
            ("source_100", "source_100_01"), ("source_０１", "source_０１_01"),
            ("source_09_tail", "source_09_tail_01"), ("a ", "a _01"),
            ("x" * 77, "x" * 77 + "_01"),
        ):
            with self.subTest(source=source):
                self.assertEqual(engine_module.handoff_names({"display_name": source}), (source, expected))

    def test_native_source_authority_and_exact_legacy_fallback(self):
        self.assertEqual(engine_module.handoff_names({"display_name": "fresh_09", "alias": "stale", "cwd": "/other",
                         "tmux_window": "launch-label", "session_id": "opaque"}), ("fresh_09", "fresh_10"))
        self.assertEqual(engine_module.handoff_names({"alias": " alias ", "cwd": "/other"}), ("alias", "alias_01"))
        self.assertEqual(engine_module.handoff_names({"cwd": "/中文 path/"}), ("中文 path", "中文 path_01"))
        for row in ({"display_name": "", "alias": "not-a-fallback"}, {"cwd": "/", "session_id": "never-name"}):
            with self.assertRaises(HandoffError):
                engine_module.handoff_names(row)

    def test_refuse_cleaner_or_mirror_changes(self):
        for name in ("", "x" * 78, "a  b", " a", "a\tb", "a\nb", "a\x1bb", "a\x7fb", "a\x00b", "-flag", "a\u00a0b"):
            with self.subTest(name=name), self.assertRaises(HandoffError) as raised:
                engine_module.handoff_names({"display_name": name})
            self.assertEqual(raised.exception.code, "invalid_name")
            self.assertEqual(raised.exception.status, 422)


class NamingEngineTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.EngineTests.asyncSetUp
    asyncTearDown = fixture.EngineTests.asyncTearDown
    start = fixture.EngineTests.start
    finish = fixture.EngineTests.finish

    async def test_limit_and_invalid_names_have_no_private_operation_or_effect(self):
        for name, code in (("foo_99", "sequence_limit"), ("", "invalid_name"),
                           ("a  b", "invalid_name"), ("x" * 78, "invalid_name"), ("-x", "invalid_name")):
            self.native.source["display_name"] = name
            with self.subTest(name=name), self.assertRaises(HandoffError) as raised:
                await self.start()
            self.assertEqual(raised.exception.code, code)
            self.assertEqual(self.native.mutations(), [])
            self.assertEqual(self.engine.operations, {})
            self.assertEqual(list(self.engine.root.iterdir()), [])

    async def test_names_frozen_despite_source_change_and_uuid_retry(self):
        self.native.source.update(display_name="中文 'quoted' $;_09", alias="中文 'quoted' $;_09", tmux_window="wrong-launch-label")
        self.native.lost = "rename"
        first = await self.start()
        self.native.source.update(display_name="changed_99", alias="changed_99")
        await self.finish()
        self.assertEqual(await self.start(), first)
        op = self.native.op
        self.assertEqual(op["source_display_name"], "中文 'quoted' $;_09")
        self.assertEqual(op["target_display_name"], "中文 'quoted' $;_10")
        self.assertEqual(self.native.source["alias"], "changed_99")
        self.assertEqual(self.native.target["alias"], op["target_display_name"])
        self.assertEqual([c[1] for c in self.native.mutations()], ["/api/sessions", "/api/sessions/opaque%2Ftarget/rename"])

    async def test_lost_rename_never_replays_or_advances_on_status_or_restart(self):
        self.native.lost = "rename"
        first = await self.start()
        self.assertEqual((await self.finish())["status"], "delivery_unknown")
        self.assertEqual(self.native.op["intents"]["rename"]["result"], "unknown")
        before = list(self.native.mutations())
        for restart in (False, True):
            if restart:
                await self.engine.close()
                self.engine = Engine(self.native, succession_service=self.native.service, state_root=self.engine.root).open()
                self.native.engine = self.engine
            self.assertEqual(await self.start(), first)
            rows = await self.engine.sessions("fresh")
            self.assertEqual(self.engine.status(first, rows=rows)["status"], "delivery_unknown")
            self.assertFalse(self.engine.tasks)
            self.assertEqual(self.native.mutations(), before)

    async def test_shutdown_during_rename_keeps_frozen_name_without_replay(self):
        import asyncio
        accepted = asyncio.Event()
        blocked = asyncio.Event()
        previous = self.native.__call__
        async def pending(method, path, **kwargs):
            result = await previous(method, path, **kwargs)
            if path.endswith("/rename"):
                accepted.set()
                await blocked.wait()
            return result
        self.native.source["display_name"] = "frozen_09"
        self.engine.upstream_json = pending
        first = await self.start()
        await asyncio.wait_for(accepted.wait(), 2)
        await self.engine.close()
        self.assertEqual(self.native.op["status"], "interrupted")
        before = list(self.native.mutations())
        self.engine = Engine(self.native, succession_service=self.native.service, state_root=self.engine.root).open()
        self.native.engine = self.engine
        self.assertEqual(await self.start(), first)
        self.assertEqual(self.native.op["target_display_name"], "frozen_10")
        self.assertEqual(self.engine.status(first, rows=await self.engine.sessions("fresh"))["status"], "interrupted")
        self.assertEqual(self.native.mutations(), before)
        self.assertFalse(self.engine.tasks)

    async def test_target_rebound_after_rename_cannot_pass_name_readback(self):
        previous = self.native.__call__
        async def rebind(method, path, **kwargs):
            result = await previous(method, path, **kwargs)
            if path.endswith("/rename"):
                self.native.target["spawn_nonce"] = "replacement"
            return result
        self.engine.upstream_json = rebind
        first = await self.start()
        result = await self.finish()
        self.assertEqual((result["status"], result["error_code"]), ("delivery_unknown", "identity_changed"))
        self.assertNotIn("verified", self.native.op["intents"]["rename"])
        self.assertEqual(await self.start(), first)
        self.assertEqual(len(self.native.mutations()), 2)

    async def test_rename_accepted_then_get_503_preserves_same_operation(self):
        previous = self.native.__call__
        async def unavailable(method, path, **kwargs):
            if method == "GET" and self.native.op["intents"].get("rename"):
                return 503, {"error": "unavailable"}
            return await previous(method, path, **kwargs)
        first = await self.start()
        self.engine.upstream_json = unavailable
        result = await self.finish()
        self.assertEqual(result["status"], "delivery_unknown")
        self.assertEqual(result["error_code"], "native_unavailable")
        self.engine.upstream_json = previous
        self.assertEqual(await self.start(), first)
        self.assertEqual(len(self.native.mutations()), 2)

    async def test_alias_response_cleaner_mismatch_is_not_success(self):
        previous = self.native.__call__
        async def changed(method, path, **kwargs):
            status, data = await previous(method, path, **kwargs)
            if path.endswith("/rename"):
                data["alias"] = "silently changed"
            return status, data
        self.engine.upstream_json = changed
        await self.start()
        result = await self.finish()
        self.assertEqual((result["status"], result["error_code"]), ("delivery_unknown", "name_mismatch"))
        self.assertEqual(len(self.native.mutations()), 2)

    async def test_fresh_listing_requires_both_alias_and_display_name(self):
        for field in ("alias", "display_name"):
            previous = self.native.__call__
            async def changed(method, path, **kwargs):
                result = await previous(method, path, **kwargs)
                if path.endswith("/rename"):
                    self.native.target[field] = "different"
                return result
            self.engine.upstream_json = changed
            first = await self.start()
            result = await self.finish()
            self.assertEqual(result["error_code"], "name_mismatch")
            self.assertEqual(await self.start(), first)
            # Reset only this test's private fixture for the independent case.
            await self.asyncTearDown()
            await self.asyncSetUp()

    async def test_matching_name_cannot_hide_rebound_target_before_rename(self):
        previous = self.native.__call__
        async def rebind(method, path, **kwargs):
            if method == "GET" and self.native.op.get("target_header"):
                self.native.target.update(spawn_nonce="replacement", alias=self.native.op["target_display_name"],
                                          display_name=self.native.op["target_display_name"])
            return await previous(method, path, **kwargs)
        first = await self.start()
        self.engine.upstream_json = rebind
        result = await self.finish()
        self.assertEqual(result["error_code"], "identity_changed")
        self.assertEqual(len(self.native.mutations()), 1)
        self.engine.upstream_json = previous
        self.assertEqual(await self.start(), first)

    async def test_rename_failure_has_no_review_or_new_create(self):
        previous = self.native.__call__
        async def reject(method, path, **kwargs):
            if path.endswith("/rename"):
                self.native.calls.append((method, path, kwargs["body"]))
                return 400, {"error": "reject"}
            return await previous(method, path, **kwargs)
        self.engine.upstream_json = reject
        first = await self.start()
        self.assertEqual((await self.finish())["status"], "delivery_unknown")
        self.assertEqual(await self.start(), first)
        self.assertEqual(len(self.native.mutations()), 2)


if __name__ == "__main__":
    unittest.main()
