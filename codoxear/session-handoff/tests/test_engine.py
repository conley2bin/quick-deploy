"""Deterministic handover tests; no Pi, broker, tmux or user files are touched."""
import asyncio
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_handoff_engine_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(PACKAGE + ".engine", ROOT / "engine.py")
engine_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = engine_module
spec.loader.exec_module(engine_module)
Engine, HandoffError = engine_module.Engine, engine_module.HandoffError


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
        self.source = {"session_id": "opaque/source", "agent_backend": "pi", "broker_pid": 100,
                       "launch_id": "old-launch", "spawn_nonce": "old-nonce", "cwd": str(root), "busy": False,
                       "display_name": "项目_09", "alias": "项目_09",
                       "model_provider": "test-provider", "model": "test-model", "reasoning_effort": "high"}
        self.defaults = {"backends": {"pi": {
            "provider_choices": ["p1", "p2"], "provider_models": {"p1": ["shared"], "p2": ["other"]},
            "reasoning_efforts": ["off", "high", "xhigh"],
            "reasoning_efforts_by_model": {"p1/shared": ["off", "high"], "shared": ["xhigh"]}}}}
        self.calls = []
        self.auth = True
        self.target = None
        self.engine = None
        self.sent = False
        self.after_send_lists = 0
        self.write_after = 2
        self.idle_after = 4
        self.emit_file = True
        self.file_stamp = 1_000_000_000_000_000_001
        self.lost = None
        self.create_gate = None
        self.on_list = None
        self.pending = True

    @property
    def op(self):
        return next(reversed(self.engine.operations.values()))

    def write_handover(self, stamp=None):
        path = self.root / ".memory/handover.md"
        path.parent.mkdir(exist_ok=True)
        path.write_text("做完了接口，测试还没跑。下一步跑测试。\n")
        os.utime(path, ns=(self.file_stamp if stamp is None else stamp,) * 2)

    async def __call__(self, method, path, *, cookie, body=None, **kwargs):
        self.calls.append((method, path, deepcopy(body)))
        if not self.auth:
            return 401, {"error": "secret credential detail"}
        if method == "GET":
            if self.sent:
                self.after_send_lists += 1
                if self.emit_file and self.after_send_lists == self.write_after:
                    self.write_handover()
                self.source["busy"] = self.after_send_lists < self.idle_after
            if self.on_list:
                self.on_list()
            return 200, {"sessions": [dict(self.source)] + ([dict(self.target)] if self.target else []),
                         "new_session_defaults": self.defaults}
        if path.endswith("/send"):
            assert path == "/api/sessions/opaque%2Fsource/send", "only the source gets a send"
            self.sent = True
            stage = "send"
        elif path == "/api/sessions":
            assert self.after_send_lists >= self.idle_after, "create must wait for source idle"
            assert self.emit_file, "create must wait for handover"
            if self.create_gate:
                await self.create_gate.wait()
            self.target = {**self.source, **{k: body[k] for k in engine_module.TARGET_KEYS},
                           "session_id": "opaque/target", "broker_pid": 200, "launch_id": "new-launch",
                           "spawn_nonce": "new-nonce", "busy": False}
            stage = "create"
        else:
            assert path == "/api/sessions/opaque%2Ftarget/rename", "only the target gets renamed"
            assert set(body) == {"name"}
            self.target.update(alias=body["name"], display_name=body["name"])
            stage = "rename"
        if self.lost == stage:
            raise OSError("accepted but response lost /private/credentials")
        if stage == "create":
            return 200, {"ok": True, **({"launch_id": "new-launch"} if self.pending else {"broker_pid": 200})}
        return 200, {"ok": True, **({"alias": body["name"]} if stage == "rename" else {})}

    def mutations(self):
        return [call for call in self.calls if call[0] == "POST"]


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.native, self.clock = Native(self.root), Clock()
        self.engine = Engine(self.native, state_root=self.root / "state", clock=self.clock.now,
                             sleep=self.clock.sleep, wall_ns=lambda: 1_000_000_000_000_000_000,
                             timeouts={"poll": 1, "launch": 8, "handover": 8}).open()
        self.native.engine = self.engine

    async def asyncTearDown(self):
        await self.engine.close()
        self.tmp.cleanup()

    async def start(self, request_id=None, target=None):
        rows, catalog = await self.engine.listing("private-cookie")
        return self.engine.start("opaque/source", request_id or str(uuid4()), target,
                                 rows=rows, catalog=catalog, cookie="private-cookie")["id"]

    async def target_start(self, target=None, *, request_id=None, catalog=None, source="opaque/source", rows=None):
        return self.engine.start(source, request_id or str(uuid4()), target,
                                 rows=rows if rows is not None else [dict(self.native.source)],
                                 catalog=self.native.defaults if catalog is None else catalog, cookie="private-cookie")

    def choice(self, **fields):
        return {"model_provider": "p1", "model": "shared", "reasoning_effort": "high", **fields}

    def assert_no_operation_effects(self):
        self.assertFalse(self.engine.operations)
        self.assertFalse(self.engine.requests)
        self.assertEqual(list(self.engine.root.iterdir()), [])
        self.assertEqual(self.native.mutations(), [])

    async def finish(self):
        while self.engine.tasks:
            await asyncio.gather(*list(self.engine.tasks.values()))
            await asyncio.sleep(0)
        return self.engine.public(self.native.op["id"])

    async def test_exact_flow_messages_names_and_source_left_running(self):
        source = dict(self.native.source)
        operation_id = await self.start()
        self.assertEqual(self.engine.public(operation_id)["status"], "waiting_source")
        self.assertEqual((await self.finish())["status"], "completed")
        calls = self.native.mutations()
        self.assertEqual([c[1] for c in calls], ["/api/sessions/opaque%2Fsource/send", "/api/sessions",
                                                "/api/sessions/opaque%2Ftarget/rename"])
        self.assertEqual(calls[0][2], {"text": engine_module.build_handover_prompt(self.root), "send_id": operation_id})
        self.assertEqual(calls[1][2], {"cwd": str(self.root), "model_provider": "test-provider", "model": "test-model",
            "reasoning_effort": "high", "agent_backend": "pi", "create_in_tmux": True, "args": [engine_module.TARGET_PROMPT]})
        self.assertEqual(calls[2][2], {"name": "项目_10"})
        self.assertEqual(self.native.source, source)
        self.assertEqual(self.engine.public(operation_id)["target_session_id"], "opaque/target")
        self.assertEqual([p.suffix for p in self.engine.root.iterdir()], [".json"])

    async def test_positive_idle_required_busy_has_no_operation_or_send(self):
        for fields in ({"busy": True}, {"busy": None}, {"queue_len": 1}, {"broker_queue_len": 1},
                       {"unattended_enabled": True}, {"subagents_running": 1}, {"lost": True},
                       {"pending_attachment": True}, {"commit_unknown_send": True}):
            with self.subTest(fields=fields), self.assertRaises(HandoffError) as raised:
                await self.target_start(rows=[{**self.native.source, **fields}])
            self.assertEqual(raised.exception.message, "源会话忙，稍后再试")
            self.assert_no_operation_effects()

    async def test_source_becomes_busy_before_send_no_mutation(self):
        await self.start()
        self.native.source["busy"] = True
        result = await self.finish()
        self.assertEqual((result["status"], result["error_code"]), ("failed", "source_busy"))
        self.assertEqual(self.native.mutations(), [])

    async def test_old_or_equal_mtime_does_not_create_even_when_idle(self):
        for stamp in (999_999_999_999_999_999, 1_000_000_000_000_000_000):
            self.native.write_handover(stamp)
            self.native.emit_file = False
            operation_id = await self.start()
            result = await self.finish()
            self.assertEqual((result["status"], result["error_code"]), ("failed", "handover_timeout"))
            self.assertIn("20 分钟", result["message"])
            self.assertEqual(len(self.native.mutations()), 1 if stamp < 10**18 else 2)
            self.native.sent = False
            self.assertEqual(self.engine.status(operation_id), result)

    async def test_new_file_alone_not_enough_until_source_idle(self):
        self.native.idle_after = 100
        await self.start()
        result = await self.finish()
        self.assertEqual(result["error_code"], "handover_timeout")
        self.assertTrue((self.root / ".memory/handover.md").exists())
        self.assertEqual(len(self.native.mutations()), 1)
        self.native.source["busy"] = False
        self.native.sent = False
        self.native.after_send_lists = 0
        self.native.idle_after = 4
        await self.start()
        self.assertEqual((await self.finish())["status"], "completed")

    async def test_missing_file_and_directory_are_not_handover(self):
        self.native.emit_file = False
        await self.start()
        self.assertEqual((await self.finish())["error_code"], "handover_timeout")
        self.native.sent = False
        (self.root / ".memory/handover.md").mkdir(parents=True)
        await self.start()
        self.assertEqual((await self.finish())["error_code"], "handover_timeout")
        self.assertEqual(len(self.native.mutations()), 2)

    async def test_source_binding_or_cwd_change_blocks_send(self):
        await self.start()
        self.native.source["spawn_nonce"] = "rebound"
        self.assertEqual((await self.finish())["error_code"], "identity_changed")
        self.assertFalse(self.native.mutations())

    async def test_deduplication_and_per_source_exclusion(self):
        requests = [str(uuid4()) for _ in range(8)]
        ids = [await self.start(r) for r in requests]
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(len(self.engine.tasks), 1)
        await self.finish()
        self.assertEqual(len(self.native.mutations()), 3)
        self.assertEqual(await self.start(requests[-1]), ids[0])
        self.assertFalse(self.engine.tasks)

    async def test_all_native_failures_failed_without_automatic_retry(self):
        for stage in ("send", "create", "rename"):
            with self.subTest(stage=stage):
                self.native.lost = stage
                request = str(uuid4())
                operation_id = await self.start(request)
                result = await self.finish()
                self.assertEqual(result["status"], "failed")
                self.assertNotIn("private", json.dumps(result))
                calls = list(self.native.calls)
                self.assertEqual(await self.start(request), operation_id)
                self.engine.status(operation_id)
                self.assertEqual(self.native.calls[len(calls):], [("GET", "/api/sessions", None)])
                self.native.sent = False
                self.native.after_send_lists = 0
                self.native.source["busy"] = False

    async def test_numeric_pid_target_and_no_jsonl_needed(self):
        self.native.pending = False
        await self.start()
        self.assertEqual((await self.finish())["status"], "completed")
        self.assertFalse(list(self.root.rglob("*.jsonl")))

    async def test_creating_state_before_native_create_and_settings_durable(self):
        previous = self.native.__call__
        async def inspect(method, path, **kwargs):
            if method == "POST" and path == "/api/sessions":
                disk = json.loads((self.engine.root / (self.native.op["id"] + ".json")).read_text())
                self.assertEqual(disk["status"], "creating")
                self.assertEqual(disk["target_settings"], {**self.choice(), "origin": "explicit"})
            return await previous(method, path, **kwargs)
        self.engine.upstream_json = inspect
        target = self.choice()
        accepted = await self.target_start(target)
        target["model"] = "changed"
        accepted["target_settings"]["model"] = "public-copy-changed"
        self.assertEqual((await self.finish())["status"], "completed")
        self.assertEqual(self.native.mutations()[1][2]["model"], "shared")

    async def test_shutdown_joins_and_restart_never_replays(self):
        self.native.create_gate = asyncio.Event()
        operation_id = await self.start()
        for _ in range(50):
            await asyncio.sleep(0)
            if any(c[1] == "/api/sessions" for c in self.native.mutations()):
                break
        tasks = list(self.engine.tasks.values())
        await self.engine.close()
        self.assertTrue(all(t.done() for t in tasks))
        self.assertEqual(self.engine.public(operation_id)["message"], "服务重启，未完成")
        before = list(self.native.calls)
        second = Engine(self.native, state_root=self.engine.root).open()
        self.assertEqual(second.public(operation_id)["status"], "failed")
        self.assertFalse(second.tasks)
        self.assertEqual(self.native.calls, before)
        await second.close()

    async def test_restart_fails_unfinished_file_ignores_old_directories(self):
        operation_id = await self.start()
        await self.finish()
        self.native.op["status"] = "waiting_source"
        self.engine._persist(self.native.op)
        old = self.engine.root / str(uuid4())
        old.mkdir()
        (old / "state.json").write_text('{"status":"reviewing"}')
        before = list(self.native.calls)
        second = Engine(self.native, state_root=self.engine.root).open()
        self.assertEqual(second.public(operation_id)["message"], "服务重启，未完成")
        self.assertEqual(self.native.calls, before)
        self.assertEqual((old / "state.json").read_text(), '{"status":"reviewing"}')
        self.assertFalse(second.tasks)
        await second.close()

    async def test_non_pi_and_invalid_names_reject_before_any_operation(self):
        for row, code in (({**self.native.source, "agent_backend": "codex"}, "unsupported_backend"),
                          *[({**self.native.source, "display_name": name}, code) for name, code in
                            (("项目_99", "sequence_limit"), ("-bad", "invalid_name"), ("x" * 78, "invalid_name"),
                             ("a\nb", "invalid_name"))]):
            with self.subTest(code=code), self.assertRaises(HandoffError) as raised:
                await self.target_start(rows=[row])
            self.assertEqual(raised.exception.code, code)
            self.assert_no_operation_effects()

    async def test_name_and_settings_frozen_at_acceptance(self):
        frozen = {k: self.native.source[k] for k in self.choice()}
        await self.start()
        self.native.source.update(display_name="renamed_99", alias="renamed_99", model="changed")
        self.assertEqual((await self.finish())["status"], "completed")
        self.assertEqual(self.native.target["display_name"], "项目_10")
        self.assertEqual({k: self.native.mutations()[1][2][k] for k in frozen}, frozen)
        self.assertEqual(self.native.source["alias"], "renamed_99")

    async def test_rebound_target_before_rename_and_readback_failure(self):
        previous = self.native.__call__
        async def rebound(method, path, **kwargs):
            if method == "GET" and self.native.op.get("target_session_id"):
                self.native.target["spawn_nonce"] = "different"
            return await previous(method, path, **kwargs)
        await self.start()
        self.engine.upstream_json = rebound
        self.assertEqual((await self.finish())["error_code"], "identity_changed")
        self.assertEqual(len(self.native.mutations()), 2)

    async def test_name_response_and_readback_must_match(self):
        previous = self.native.__call__
        async def mismatch(method, path, **kwargs):
            code, data = await previous(method, path, **kwargs)
            if path.endswith("/rename"):
                self.native.target["display_name"] = "wrong"
            return code, data
        self.engine.upstream_json = mismatch
        await self.start()
        self.assertEqual((await self.finish())["error_code"], "name_mismatch")
        self.assertEqual(len(self.native.mutations()), 3)

    async def test_launch_timeout_no_matching_target_and_no_recreate(self):
        previous = self.native.__call__
        async def no_target(method, path, **kwargs):
            result = await previous(method, path, **kwargs)
            if path == "/api/sessions" and method == "POST":
                self.native.target = None
            return result
        self.engine.upstream_json = no_target
        await self.start()
        self.assertEqual((await self.finish())["error_code"], "launch_timeout")
        self.assertEqual(len(self.native.mutations()), 2)

    def test_defaults_timeout_and_nonfinite_rejected(self):
        engine = Engine(self.native, state_root=self.root / "other")
        self.assertEqual(engine.timeouts["handover"], 1200)
        with self.assertRaises(ValueError):
            Engine(self.native, timeouts={"handover": float("inf")})

    async def test_target_invalid_exact_native_pair_and_effort_no_effects(self):
        for target in (self.choice(model_provider="unknown"), self.choice(model_provider="P1"),
                       self.choice(model="Shared"), self.choice(model="other"),
                       self.choice(model_provider="p2"), self.choice(model="default"),
                       self.choice(model="DEFAULT"), self.choice(reasoning_effort="xhigh"),
                       self.choice(model_provider="p2", model="other", reasoning_effort="minimal")):
            with self.subTest(target=target):
                with self.assertRaises(HandoffError) as raised:
                    await self.target_start(target)
                self.assertEqual((raised.exception.status, raised.exception.code), (422, "target_invalid"))
                self.assert_no_operation_effects()

    async def test_target_unverifiable_no_effects_but_inherit_still_works(self):
        for catalog in ({}, {"backends": {"pi": []}},
                        {**self.native.defaults, "warnings": {"pi": None}},
                        {"backends": {"pi": {**self.native.defaults["backends"]["pi"],
                                             "reasoning_efforts": None, "reasoning_efforts_by_model": {}}}}):
            with self.subTest(catalog=catalog):
                with self.assertRaises(HandoffError) as raised:
                    await self.target_start(self.choice(), catalog=catalog)
                self.assertEqual((raised.exception.status, raised.exception.code), (422, "target_unverifiable"))
                self.assert_no_operation_effects()
        self.native.defaults = None
        with self.assertRaises(HandoffError) as raised:
            await self.target_start(self.choice())
        self.assertEqual(raised.exception.code, "target_unverifiable")
        self.assert_no_operation_effects()
        await self.target_start()
        self.assertEqual((await self.finish())["status"], "completed")

    async def test_target_backend_caps_trimmed_and_no_bare_model_fallback(self):
        pi = self.native.defaults["backends"]["pi"]
        pi["provider_choices"] = [" p2 "]
        pi["provider_models"]["p2"] = [" other "]
        pi["reasoning_efforts"] = [" XHIGH "]
        pi["reasoning_efforts_by_model"]["other"] = ["off"]
        await self.target_start(self.choice(model_provider="p2", model="other", reasoning_effort="xhigh"))
        self.assertEqual((await self.finish())["status"], "completed")
        self.assertEqual(self.native.mutations()[1][2]["reasoning_effort"], "xhigh")

    async def test_target_dedup_and_source_lease_precede_catalog_drift(self):
        request_id = str(uuid4())
        accepted = await self.target_start(self.choice(), request_id=request_id)
        state_path = self.engine.root / (accepted["id"] + ".json")
        before = state_path.read_bytes()
        self.native.defaults = None
        same = await self.target_start(self.choice(), request_id=request_id, rows=[])
        self.assertEqual(same, accepted)
        self.assertEqual(state_path.read_bytes(), before)
        for target in (None, self.choice(reasoning_effort="off")):
            with self.assertRaises(HandoffError) as raised:
                await self.target_start(target, request_id=request_id)
            self.assertEqual(raised.exception.code, "request_conflict")
            self.assertEqual(state_path.read_bytes(), before)
            with self.assertRaises(HandoffError) as raised:
                await self.target_start(target)
            self.assertEqual(raised.exception.code, "target_conflict")
            self.assertEqual(state_path.read_bytes(), before)
        with self.assertRaises(HandoffError) as raised:
            await self.target_start(self.choice(), request_id=request_id, source="different-source")
        self.assertEqual(raised.exception.code, "request_conflict")
        alias = str(uuid4())
        self.assertEqual((await self.target_start(self.choice(), request_id=alias, rows=[]))["id"], accepted["id"])
        self.assertEqual(self.engine.requests[alias], accepted["id"])
        await self.finish()
        self.assertEqual(len(self.native.mutations()), 3)

    async def test_target_explicit_needs_only_source_cwd_inherit_needs_all_four(self):
        for key in self.choice():
            self.native.source.pop(key)
        with self.assertRaises(HandoffError) as raised:
            await self.target_start()
        self.assertEqual(raised.exception.code, "settings_missing")
        self.assert_no_operation_effects()
        self.native.source.update(cwd="", display_name="valid-source-name")
        with self.assertRaises(HandoffError) as raised:
            await self.target_start(self.choice())
        self.assertEqual(raised.exception.code, "settings_missing")
        self.assert_no_operation_effects()
        self.native.source["cwd"] = str(self.root)
        await self.target_start(self.choice())
        self.assertEqual((await self.finish())["status"], "completed")

    async def test_target_native_pure_parser_launch_plan_exact_argv_env_one_prompt(self):
        native_root = os.environ.get("HANDOFF_NATIVE_SOURCE")
        if not native_root:
            self.skipTest("set HANDOFF_NATIVE_SOURCE to an existing read-only native checkout for the pure join")
        with patch.object(sys, "path", [str(Path(native_root).resolve()), *sys.path]):
            from codoxear.launch_config import parse_new_session_launch_request
            from codoxear.session_launch_plan import LaunchPlanDeps, LaunchPlanRequest, prepare_launch_plan
        await self.target_start(self.choice(model_provider="p2", model="other", reasoning_effort="xhigh"))
        self.assertEqual((await self.finish())["status"], "completed")
        body = self.native.mutations()[1][2]
        def forbidden(*args, **kwargs):
            raise AssertionError("pure plan must not discover, read configuration or launch actors")
        parsed = parse_new_session_launch_request(body, default_agent_backend="pi",
            codex_launch_defaults_provider=forbidden,
            pi_launch_defaults_provider=lambda: self.native.defaults["backends"]["pi"])
        request = LaunchPlanRequest(**vars(parsed))
        plan = prepare_launch_plan(request, deps=LaunchPlanDeps(
            resolve_dir_target=lambda cwd, **kwargs: Path(cwd), create_git_worktree=forbidden,
            codex_trust_override_for_path=lambda path: "", list_resume_candidates_for_cwd=forbidden,
            resume_candidate_from_log=forbidden, live_session_for_resume_target=forbidden,
            load_env_file=forbidden, environ={"CODEX_WEB_MODEL": "must-be-replaced"},
            dotenv_path=self.root / "absent-env", homes={"pi": self.root / "synthetic-pi-home"},
            python_executable="synthetic-python"))
        self.assertEqual(plan.argv, ["synthetic-python", "-m", "codoxear.broker", "--cwd", str(self.root), "--",
                                     "--provider", "p2", "--model", "other", "--thinking", "xhigh", body["args"][0]])
        self.assertEqual(len(body["args"]), 1)
        self.assertEqual(plan.argv.count(body["args"][0]), 1)
        self.assertEqual({k: plan.env[k] for k in ("CODEX_WEB_MODEL_PROVIDER", "CODEX_WEB_MODEL", "CODEX_WEB_REASONING_EFFORT")},
                         {"CODEX_WEB_MODEL_PROVIDER": "p2", "CODEX_WEB_MODEL": "other", "CODEX_WEB_REASONING_EFFORT": "xhigh"})
        self.assertTrue(plan.create_in_tmux)
        self.assertEqual(self.native.source["model"], "test-model")
        self.assertFalse((self.root / "synthetic-pi-home").exists())

    async def test_independent_sources_have_independent_tasks_and_choices(self):
        a = await self.target_start(self.choice())
        b_row = {**self.native.source, "session_id": "source-b", "broker_pid": 101, "spawn_nonce": "nonce-b"}
        b = await self.target_start(self.choice(model_provider="p2", model="other", reasoning_effort="xhigh"),
                                    source="source-b", rows=[b_row])
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual(len(self.engine.tasks), 2)
        self.assertEqual(self.engine.public(a["id"])["target_settings"]["model"], "shared")
        self.assertEqual(self.engine.public(b["id"])["target_settings"]["model"], "other")
        await self.engine.close()
        self.assertFalse(self.native.mutations())

    async def test_restarted_creation_fails_without_replay_or_new_request_retry(self):
        request = str(uuid4())
        operation_id = await self.start(request)
        await self.finish()
        self.native.op["status"] = "creating"
        self.engine._persist(self.native.op)
        before = list(self.native.calls)
        second = Engine(self.native, state_root=self.engine.root).open()
        self.assertEqual(second.status(operation_id)["message"], "服务重启，未完成")
        self.assertEqual(second.start("opaque/source", request, rows=[], cookie="fresh")["id"], operation_id)
        self.assertEqual(self.native.calls, before)
        self.assertFalse(second.tasks)
        await second.close()

    async def test_persistent_state_private_minimal_and_no_auth_or_transcript(self):
        operation_id = await self.start()
        await self.finish()
        path = self.engine.root / (operation_id + ".json")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.engine.root.stat().st_mode & 0o777, 0o700)
        self.assertLess(path.stat().st_size, 4096)
        state = path.read_text()
        for removed in ("private-cookie", "manifest", "sha256", "intents", "source_header", "entries"):
            self.assertNotIn(removed, state)
        await self.engine.close()
        second = Engine(self.native, state_root=self.engine.root).open()
        self.assertEqual(second.status(operation_id)["status"], "completed")
        await second.close()
