"""Deterministic protocol tests: no Pi, broker, tmux or user files are touched."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_handoff_engine_tests"
if PACKAGE not in sys.modules:
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(PACKAGE + ".engine", ROOT / "engine.py")
engine_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = engine_module
spec.loader.exec_module(engine_module)
Engine, HandoffError = engine_module.Engine, engine_module.HandoffError


def log(path, ident):
    path.write_text(json.dumps({"type": "session", "version": 3, "id": ident, "cwd": str(path.parent),
                               "timestamp": "2026-11-02T01:00:00Z"}) + "\n" +
                    json.dumps({"type": "message", "id": "entry1", "parentId": None,
                                "timestamp": "2026-11-02T01:00:01Z", "message": {"role": "user",
                                "content": [{"type": "text", "text": "目标已从 X 改为 Y；不要采用已否决方案。"}]}}) + "\n")
    path.chmod(0o600)


class Clock:
    def __init__(self):
        self.value = 0

    def now(self):
        return self.value

    async def sleep(self, seconds):
        self.value += seconds
        await asyncio.sleep(0)


class Native:
    def __init__(self, root):
        self.root = root
        source_path = root / "source.jsonl"
        log(source_path, "source-header")
        self.source = {"session_id": "opaque/source", "thread_id": "filename-is-not-header",
                       "agent_backend": "pi", "broker_pid": 100, "launch_id": "old-launch", "spawn_nonce": "old-nonce",
                       "log_path": str(source_path), "cwd": str(root), "busy": False,
                       "model_provider": "test-provider", "model": "test-model", "reasoning_effort": "high"}
        self.target = None
        self.calls = []
        self.engine = None
        self.pending = True
        self.lazy = 1
        self.emit_understanding = True
        self.emit_review = True
        self.emit_resolution = True
        self.verdict = "clear"
        self.resolution_verdict = "ready"
        self.lost = None
        self.on_list = None
        self.on_send = None
        self.create_gate = None
        self.auth = True
        self.busy_lists = 0
        self.report_transform = None

    @property
    def op(self):
        return next(iter(self.engine.operations.values()))

    def artifact(self, kind):
        op = self.op
        directory = self.engine.root / op["id"]
        path = directory / (kind + ".json")
        if path.exists():
            return
        data = {"version": 1, "operation_id": op["id"], "source_sha256": op["source_sha256"],
                "body": "以当前目标 Y 为准；已核查文件，补回遗漏约束，不重复已否决方案。"}
        if kind in ("review", "resolution"):
            data["understanding_sha256"] = op["understanding_sha256"]
            data["verdict"] = self.verdict if kind == "review" else self.resolution_verdict
        if kind == "resolution":
            data["review_sha256"] = op["review_sha256"]
        if self.report_transform:
            data = self.report_transform(kind, data)
        path.write_text(json.dumps(data))
        path.chmod(0o600)

    async def __call__(self, method, path, *, cookie, body=None, **kwargs):
        self.calls.append((method, path, body))
        if not self.auth:
            return 401, {"error": "secret credential detail"}
        if method == "GET":
            if self.target:
                if self.lazy:
                    self.lazy -= 1
                elif not self.target["log_path"]:
                    path_ = self.root / "target.jsonl"
                    log(path_, "target-header")
                    self.target["log_path"] = str(path_)
                if self.emit_understanding and self.target["log_path"]:
                    self.artifact("understanding")
            if self.busy_lists:
                self.source["busy"] = True
                self.busy_lists -= 1
            else:
                self.source["busy"] = False
            if self.on_list:
                self.on_list()
            return 200, {"sessions": [dict(self.source)] + ([dict(self.target)] if self.target else [])}
        if path == "/api/sessions":
            if self.create_gate:
                await self.create_gate.wait()
            self.target = {**self.source, "session_id": "opaque/target", "broker_pid": 200,
                           "thread_id": None, "launch_id": "launch-pending-string", "spawn_nonce": "target-nonce",
                           "log_path": None, "busy": False}
            if self.lost == "create":
                raise OSError("request accepted but response lost /private/credentials")
            return 200, {"ok": True, **({"launch_id": "launch-pending-string", "pending": True}
                                     if self.pending else {"broker_pid": 200})}
        if self.on_send:
            self.on_send(path, body)
        stage = "review" if path.endswith("opaque%2Fsource/send") else "continuation"
        if self.lost == stage:
            raise TimeoutError("uncertain PTY acceptance")
        if stage == "review" and self.emit_review:
            self.artifact("review")
        if stage == "continuation" and self.emit_resolution:
            self.artifact("resolution")
        return 200, {"ok": True}

    def mutations(self):
        return [call for call in self.calls if call[0] == "POST"]


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.native = Native(self.root)
        self.clock = Clock()
        self.engine = Engine(self.native, state_root=self.root / "state", clock=self.clock.now,
                             sleep=self.clock.sleep, timeouts={"poll": 1, "launch": 8, "understanding": 8,
                             "review": 8, "resolution": 8, "source_idle": 8}).open()
        self.native.engine = self.engine

    async def asyncTearDown(self):
        await self.engine.close()
        self.tmp.cleanup()

    async def start(self, request_id=None):
        rows = await self.engine.sessions("private-cookie")
        public = self.engine.start("opaque/source", request_id or str(uuid4()), "保留原目标",
                                   rows=rows, cookie="private-cookie")
        return public["id"]

    async def finish(self):
        while self.engine.tasks:
            await asyncio.gather(*list(self.engine.tasks.values()))
            await asyncio.sleep(0)
        return self.engine.public(self.native.op["id"])

    async def test_pending_launch_lazy_log_busy_then_idle_exact_first_prompt(self):
        self.native.busy_lists = 5
        await self.start()
        result = await self.finish()
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["reviewed"])
        mutations = self.native.mutations()
        self.assertEqual(len(mutations), 3)
        body = mutations[0][2]
        self.assertEqual(set(body), {"cwd", "model_provider", "model", "reasoning_effort", "agent_backend", "create_in_tmux", "args"})
        self.assertTrue(body["create_in_tmux"])
        self.assertEqual(len(body["args"]), 1)
        self.assertNotIn(body["args"][0][0], "-@")
        self.assertEqual(body["model_provider"], "test-provider")
        self.assertEqual(self.native.op["source_header"]["pi_session_id"], "source-header")
        self.assertNotEqual(mutations[1][2]["send_id"], mutations[2][2]["send_id"])
        self.assertIn(":review]", mutations[1][2]["text"])
        self.assertIn(":continuation]", mutations[2][2]["text"])

    async def test_numeric_broker_pid_and_corrections_single_round(self):
        self.native.pending = False
        self.native.verdict = "corrections"
        await self.start()
        self.assertEqual((await self.finish())["status"], "ready")
        self.assertEqual(len(self.native.mutations()), 3)
        self.assertEqual(self.native.op["launch"], {"broker_pid": 200})

    async def test_goal_conflict_requires_target_receipt(self):
        self.native.verdict = "conflict"
        self.native.resolution_verdict = "conflict"
        await self.start()
        result = await self.finish()
        self.assertEqual(result["status"], "conflict")
        self.assertTrue(result["reviewed"])
        self.assertEqual(len(self.native.mutations()), 3)

    async def test_http_200_without_resolution_is_not_ready(self):
        self.native.emit_resolution = False
        await self.start()
        result = await self.finish()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "resolution_timeout")

    async def test_source_route_rebound_blocks_review(self):
        def rebind():
            if self.native.target and self.native.target.get("log_path"):
                self.native.source["spawn_nonce"] = "rebound"
        self.native.on_list = rebind
        await self.start()
        result = await self.finish()
        self.assertEqual(result["error_code"], "identity_changed")
        self.assertEqual(len(self.native.mutations()), 1)

    async def test_source_header_replacement_blocks_review(self):
        def rebind():
            if self.native.target and self.native.target.get("log_path"):
                log(Path(self.native.source["log_path"]), "other-header")
        self.native.on_list = rebind
        await self.start()
        result = await self.finish()
        self.assertEqual(result["error_code"], "identity_changed")
        self.assertEqual(len(self.native.mutations()), 1)

    async def test_target_replaced_after_review_blocks_continuation(self):
        def rebind(path, body):
            if "source" in path:
                self.native.target["broker_pid"] = 201
        self.native.on_send = rebind
        await self.start()
        result = await self.finish()
        self.assertEqual(result["error_code"], "identity_changed")
        self.assertEqual(len(self.native.mutations()), 2)

    async def test_target_header_replaced_blocks_continuation(self):
        def rebind(path, body):
            if "source" in path:
                log(Path(self.native.target["log_path"]), "replacement-header")
        self.native.on_send = rebind
        await self.start()
        result = await self.finish()
        self.assertEqual(result["error_code"], "identity_changed")
        self.assertEqual(len(self.native.mutations()), 2)

    async def test_launch_without_native_identity_is_unknown(self):
        previous = self.native.__call__
        async def missing_identity(method, path, **kwargs):
            result = await previous(method, path, **kwargs)
            return (200, {"ok": True, "session_id": "opaque/target", "cwd": str(self.root)}) if method == "POST" and path == "/api/sessions" else result
        self.engine.upstream_json = missing_identity
        await self.start()
        result = await self.finish()
        self.assertEqual(result["status"], "creation_unknown")
        self.assertIsNone(result["target_session_id"])
        self.assertEqual(len(self.native.mutations()), 1)

    async def test_understanding_stale_wrong_or_malformed_rejected(self):
        for field, value in (("operation_id", str(uuid4())), ("source_sha256", "0" * 64), ("body", {})):
            with self.subTest(field=field):
                self.native.report_transform = lambda kind, data: {**data, field: value} if kind == "understanding" else data
                await self.start()
                result = await self.finish()
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["error_code"], "invalid_artifact")
                self.assertEqual(len(self.native.mutations()), 1)
                # Each case has an independent engine/state root/native, no side-effect retry.
                await self.engine.close()
                newroot = self.root / str(uuid4())
                newroot.mkdir()
                self.native = Native(newroot)
                self.engine = Engine(self.native, state_root=newroot / "state", clock=self.clock.now,
                                     sleep=self.clock.sleep, timeouts={"poll": 1}).open()
                self.native.engine = self.engine

    async def test_wrong_review_hash_rejected(self):
        self.native.report_transform = lambda kind, data: {**data, "understanding_sha256": "0" * 64} if kind == "review" else data
        await self.start()
        result = await self.finish()
        self.assertEqual(result["error_code"], "invalid_artifact")
        self.assertEqual(len(self.native.mutations()), 2)

    async def test_wrong_resolution_review_hash_never_ready(self):
        self.native.report_transform = lambda kind, data: {**data, "review_sha256": "0" * 64} if kind == "resolution" else data
        await self.start()
        result = await self.finish()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "invalid_artifact")
        self.assertEqual(len(self.native.mutations()), 3)

    async def test_forged_unreviewed_resolution_without_action_rejected(self):
        self.native.report_transform = lambda kind, data: {**data, "review_sha256": "unreviewed"} if kind == "resolution" else data
        await self.start()
        result = await self.finish()
        self.assertEqual(result["error_code"], "invalid_artifact")
        self.assertTrue(result["reviewed"])

    async def test_immutable_understanding_overwrite_blocks_continuation(self):
        def replace(path, body):
            file = self.engine.root / self.native.op["id"] / "understanding.json"
            data = json.loads(file.read_text())
            data["body"] = "被替换的理解"
            file.write_text(json.dumps(data))
        self.native.on_send = replace
        await self.start()
        result = await self.finish()
        self.assertIn(result["error_code"], ("artifact_changed", "invalid_artifact"))
        self.assertEqual(len(self.native.mutations()), 2)

    async def test_review_timeout_explicit_bypass_only(self):
        self.native.emit_review = False
        operation_id = await self.start()
        result = await self.finish()
        self.assertEqual(result["status"], "unreviewed")
        self.assertFalse(result["reviewed"])
        self.assertTrue(result["can_continue_unreviewed"])
        self.assertEqual(len(self.native.mutations()), 2)
        rows = await self.engine.sessions("fresh-cookie")
        self.engine.continue_unreviewed(operation_id, rows=rows, cookie="fresh-cookie")
        result = await self.finish()
        self.assertEqual(result["status"], "ready")
        self.assertFalse(result["reviewed"])
        self.assertEqual(self.native.op["review_sha256"], "unreviewed")
        self.assertEqual(len(self.native.mutations()), 3)
        with self.assertRaises(HandoffError):
            self.engine.continue_unreviewed(operation_id, rows=rows, cookie="fresh-cookie")

    async def test_target_rebound_blocks_explicit_bypass(self):
        self.native.emit_review = False
        operation_id = await self.start()
        await self.finish()
        self.native.target["spawn_nonce"] = "other-target"
        with self.assertRaises(HandoffError):
            self.engine.continue_unreviewed(operation_id, rows=await self.engine.sessions("fresh"), cookie="fresh")
        self.assertEqual(len(self.native.mutations()), 2)

    async def test_busy_source_times_out_without_review_send(self):
        self.native.busy_lists = 100
        await self.start()
        result = await self.finish()
        self.assertEqual(result["status"], "unreviewed")
        self.assertEqual(result["error_code"], "source_idle_timeout")
        self.assertEqual(len(self.native.mutations()), 1)

    async def test_late_review_is_used_instead_of_claiming_unreviewed(self):
        self.native.emit_review = False
        operation_id = await self.start()
        await self.finish()
        self.native.verdict = "conflict"
        self.native.resolution_verdict = "conflict"
        self.native.artifact("review")
        self.engine.continue_unreviewed(operation_id, rows=await self.engine.sessions("fresh"), cookie="fresh")
        result = await self.finish()
        self.assertEqual(result["status"], "conflict")
        self.assertTrue(result["reviewed"])

    async def test_lost_create_never_retried_even_new_request_id(self):
        self.native.lost = "create"
        first = await self.start()
        result = await self.finish()
        self.assertEqual(result["status"], "creation_unknown")
        self.assertEqual(await self.start(), first)
        self.assertEqual(len(self.native.mutations()), 1)

    async def test_lost_review_and_continuation_never_retried(self):
        self.native.lost = "review"
        first = await self.start()
        result = await self.finish()
        self.assertEqual(result["status"], "delivery_unknown")
        self.assertEqual(await self.start(), first)
        self.assertEqual(len(self.native.mutations()), 2)

    async def test_lost_continuation_is_unknown_without_retry(self):
        self.native.lost = "continuation"
        await self.start()
        result = await self.finish()
        self.assertEqual(result["status"], "delivery_unknown")
        self.assertEqual(len(self.native.mutations()), 3)

    async def test_intent_on_disk_before_native_mutation(self):
        previous = self.native.__call__
        async def inspect(method, path, **kwargs):
            if method == "POST":
                op = self.native.op
                disk = json.loads((self.engine.root / op["id"] / "state.json").read_text())
                stage = "create" if path == "/api/sessions" else "review" if "source" in path else "continuation"
                self.assertEqual(disk["intents"][stage]["result"], "unknown")
            return await previous(method, path, **kwargs)
        self.engine.upstream_json = inspect
        await self.start()
        self.assertEqual((await self.finish())["status"], "ready")

    async def test_concurrent_requests_dedupe_and_persist_alias(self):
        requests = [str(uuid4()) for _ in range(8)]
        ids = await asyncio.gather(*(self.start(r) for r in requests))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(await self.start(requests[0]), ids[0])
        await self.finish()
        self.assertEqual(len(self.native.mutations()), 3)
        second = Engine(self.native, state_root=self.engine.root).open()
        self.assertEqual(second.requests[requests[-1]], ids[0])
        await second.close()

    async def test_snapshot_worker_does_not_block_http_loop_and_is_joined(self):
        entered, release = threading.Event(), threading.Event()
        prepare = self.engine._prepare
        def slow_prepare(*args):
            entered.set()
            if not release.wait(2):
                raise RuntimeError("test worker was not released")
            return prepare(*args)
        self.engine._prepare = slow_prepare
        operation_id = await self.start()
        self.assertTrue(await asyncio.to_thread(entered.wait, 1))
        # The loop is responsive while transcript projection is in another thread.
        self.assertEqual(self.engine.public(operation_id)["status"], "creating")
        closing = asyncio.create_task(self.engine.close())
        await asyncio.sleep(0)
        self.assertFalse(closing.done())
        release.set()
        await closing
        self.assertEqual(self.engine.public(operation_id)["status"], "interrupted")
        self.assertEqual(self.native.mutations(), [])

    async def test_shutdown_awaits_and_restart_never_replays(self):
        self.native.create_gate = asyncio.Event()
        operation_id = await self.start()
        for _ in range(10):
            await asyncio.sleep(0)
        tasks = list(self.engine.tasks.values())
        await self.engine.close()
        self.assertTrue(all(t.done() for t in tasks))
        self.assertEqual(self.engine.public(operation_id)["status"], "interrupted")
        count = len(self.native.mutations())
        second = Engine(self.native, state_root=self.engine.root).open()
        self.assertEqual(second.public(operation_id)["status"], "interrupted")
        self.assertFalse(second.tasks)
        self.assertEqual(count, len(self.native.mutations()))
        await second.close()

    async def test_crash_state_marked_interrupted_and_credentials_absent(self):
        operation_id = await self.start()
        await self.finish()
        op = self.native.op
        op["status"] = "reviewing"
        self.engine._persist(op)
        second = Engine(self.native, state_root=self.engine.root).open()
        self.assertEqual(second.public(operation_id)["status"], "interrupted")
        for path in self.engine.root.rglob("*"):
            mode = path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o700 if path.is_dir() else 0o600)
            if path.is_file():
                self.assertNotIn(b"private-cookie", path.read_bytes())
                if path.name in ("state.json", "manifest.json"):
                    self.assertNotIn('"entries"', path.read_text())
        public = json.dumps(second.public(operation_id))
        self.assertNotIn(str(self.root), public)
        self.assertNotIn("source_sha256", public)
        await second.close()

    async def test_non_pi_explicit_rejection(self):
        self.native.source["agent_backend"] = "codex"
        with self.assertRaises(HandoffError) as raised:
            await self.start()
        self.assertEqual(raised.exception.status, 422)
        self.assertEqual(self.native.mutations(), [])

    async def test_nonfinite_deadline_rejected(self):
        with self.assertRaises(ValueError):
            Engine(self.native, state_root=self.root / "other", timeouts={"review": float("inf")})


if __name__ == "__main__":
    unittest.main()
