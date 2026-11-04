"""HTTP seam tests with an in-process fake native transport and temporary artifacts."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from test_engine import Native, Clock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("_handoff_feature_tests", ROOT / "feature.py")
feature = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = feature
spec.loader.exec_module(feature)


class Host:
    prefix = "/prefix"
    html_status = "injected"

    def __init__(self, native):
        self.upstream_json = native
        self.scripts = {}

    def same_origin(self, request):
        return (request.headers.get("Origin") == "http://native.test"
                and request.headers.get("Sec-Fetch-Site", "same-origin") == "same-origin")

    def json_response(self, data, status=200):
        return web.json_response(data, status=status, headers={"Cache-Control": "no-store, private"})

    def add_script(self, namespace, body):
        self.scripts[namespace] = body


class FeatureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.native = Native(self.root)
        self.host = Host(self.native)
        self.app = web.Application()
        # Frontend owns these files. Exercise the install contract without writing
        # placeholder sibling modules/assets into this lane's worktree.
        with patch.object(Path, "read_bytes", return_value=b"/* frozen frontend fixture */"):
            self.engine = feature.install(self.app, self.host, state_root=self.root / "state", succession_service=self.native.service,
                                          timeouts={"poll": 1, "launch": 8, "understanding": 8,
                                                    "review": 8, "resolution": 8, "source_idle": 8})
        self.clock = Clock()
        self.engine.clock, self.engine.sleep = self.clock.now, self.clock.sleep
        self.native.engine = self.engine
        self.server = TestServer(self.app, handler_cancellation=True)
        self.client = TestClient(self.server)
        await self.client.start_server()
        self.headers = {"Origin": "http://native.test", "Cookie": "session=private-test"}
        self.base = "/prefix/__session_handoff__/"

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    def start_body(self):
        return {"source_session_id": "opaque/source", "request_id": str(uuid4()), "note": "保留目标 Y"}

    async def post(self, action, body, headers=None):
        response = await self.client.post(self.base + action, json=body,
                                          headers=self.headers if headers is None else headers)
        return response, await response.json()

    async def finish(self):
        while self.engine.tasks:
            await asyncio.gather(*list(self.engine.tasks.values()))
            await asyncio.sleep(0)

    async def test_envelope_public_fields_and_frozen_script_install(self):
        response, data = await self.post("start", self.start_body())
        self.assertEqual(response.status, 200)
        self.assertEqual(set(data), {"operation"})
        op = data["operation"]
        self.assertEqual(set(op), {"id", "request_id", "source_session_id", "target_session_id", "status",
                                   "message", "reviewed", "can_continue_unreviewed", "updated_at", "target_settings"})
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)
        await self.finish()
        response, data = await self.post("status", {"operation_id": op["id"]})
        self.assertEqual(data["operation"]["status"], "ready")
        self.assertNotIn(str(self.root), json.dumps(data))
        self.assertIn("__session_handoff__", self.host.scripts)

    async def test_fresh_auth_on_all_state_requests_and_no_error_leak(self):
        _, data = await self.post("start", self.start_body())
        await self.finish()
        op = data["operation"]["id"]
        self.native.auth = False
        for action, body in (("start", self.start_body()), ("status", {"operation_id": op}),
                             ("continue", {"operation_id": op})):
            response, data = await self.post(action, body)
            self.assertEqual(response.status, 401)
            self.assertEqual(data["error"], "authentication_required")
            self.assertNotIn("secret", json.dumps(data))

    async def test_cross_site_missing_origin_denied_before_native(self):
        for headers in ({}, {"Origin": "https://evil.test"},
                        {"Origin": "http://native.test", "Sec-Fetch-Site": "cross-site"}):
            response, data = await self.post("start", self.start_body(), headers)
            self.assertEqual(response.status, 403)
            self.assertEqual(data["error"], "cross_origin")
        self.assertEqual(self.native.calls, [])

    async def test_strict_post_unknown_routes_and_override_rejection(self):
        for field in ("path", "cwd", "model", "url", "callback_url", "provider", "operation_id",
                      "agent_backend", "args", "create_in_tmux", "reasoning_effort"):
            response, data = await self.post("start", {**self.start_body(), field: "/arbitrary"})
            self.assertEqual(response.status, 400)
        response = await self.client.get(self.base + "status", headers=self.headers)
        self.assertEqual(response.status, 405)
        response = await self.client.post(self.base + "unknown", json={}, headers=self.headers)
        self.assertEqual(response.status, 404)
        self.assertEqual(self.native.mutations(), [])

    async def test_malformed_bounded_and_nonobject_inputs(self):
        for body in ([], None, {"source_session_id": "x", "request_id": "bad"},
                     {**self.start_body(), "note": "x" * 2001}, {**self.start_body(), "note": 1},
                     {**self.start_body(), "source_session_id": "\x00"},
                     {**self.start_body(), "request_id": 3}):
            response, _ = await self.post("start", body)
            self.assertEqual(response.status, 400)
        response = await self.client.post(self.base + "start", data="{bad", headers={**self.headers, "Content-Type": "application/json"})
        self.assertEqual(response.status, 400)
        response = await self.client.post(self.base + "start", data="x" * 9000, headers={**self.headers, "Content-Type": "application/json"})
        self.assertEqual(response.status, 400)
        self.assertEqual(self.native.mutations(), [])

    async def test_concurrent_http_requests_dedupe(self):
        bodies = [self.start_body() for _ in range(5)]
        results = await asyncio.gather(*(self.post("start", body) for body in bodies))
        ids = {data["operation"]["id"] for _, data in results}
        self.assertEqual(len(ids), 1)
        await self.finish()
        self.assertEqual(len(self.native.mutations()), 4)
        _, retry = await self.post("start", bodies[-1])
        self.assertIn(retry["operation"]["id"], ids)

    async def test_browser_disconnect_does_not_cancel_owned_task(self):
        self.native.create_gate = asyncio.Event()
        response, data = await self.post("start", self.start_body())
        operation_id = data["operation"]["id"]
        response.close()
        # Close browser connection pool only, not the server/app.
        await self.client.session.close()
        self.assertIn(operation_id, self.engine.tasks)
        self.assertFalse(self.engine.tasks[operation_id].done())
        self.native.create_gate.set()
        await self.finish()
        self.assertEqual(self.engine.public(operation_id)["status"], "ready")

    async def test_cleanup_cancels_and_awaits_background_work(self):
        self.native.create_gate = asyncio.Event()
        _, data = await self.post("start", self.start_body())
        operation_id = data["operation"]["id"]
        tasks = list(self.engine.tasks.values())
        await self.client.close()
        self.assertTrue(all(task.done() for task in tasks))
        self.assertFalse(self.engine.tasks)
        self.assertEqual(self.engine.public(operation_id)["status"], "interrupted")

    async def test_authenticated_explicit_continue_only_after_timeout(self):
        self.native.emit_review = False
        _, data = await self.post("start", self.start_body())
        operation_id = data["operation"]["id"]
        await self.finish()
        response, data = await self.post("status", {"operation_id": operation_id})
        self.assertEqual(data["operation"]["status"], "unreviewed")
        self.assertEqual(len(self.native.mutations()), 3)
        response, _ = await self.post("continue", {"operation_id": operation_id, "unreviewed": True})
        self.assertEqual(response.status, 400)
        response, _ = await self.post("continue", {"operation_id": operation_id})
        self.assertEqual(response.status, 200)
        await self.finish()
        self.assertFalse(self.engine.public(operation_id)["reviewed"])
        self.assertEqual(self.engine.public(operation_id)["status"], "ready")
        response, _ = await self.post("continue", {"operation_id": operation_id})
        self.assertEqual(response.status, 409)

    async def test_status_reconciles_late_receipt_only_after_fresh_auth(self):
        self.native.emit_resolution = False
        _, data = await self.post("start", self.start_body())
        operation_id = data["operation"]["id"]
        await self.finish()
        _, data = await self.post("status", {"operation_id": operation_id})
        self.assertEqual(data["operation"]["status"], "delivery_unknown")
        _, duplicate = await self.post("start", self.start_body())
        self.assertEqual(duplicate["operation"]["id"], operation_id)
        self.native.artifact("resolution")
        self.native.auth = False
        response, _ = await self.post("status", {"operation_id": operation_id})
        self.assertEqual(response.status, 401)
        self.assertEqual(self.engine.public(operation_id)["status"], "delivery_unknown")
        self.native.auth = True
        response, data = await self.post("status", {"operation_id": operation_id})
        self.assertEqual(response.status, 200)
        self.assertEqual(data["operation"]["status"], "ready")
        self.assertEqual(len(self.native.mutations()), 4)
        self.assertFalse(self.engine.tasks)

    async def test_sequence_limit_is_4xx_before_any_creation(self):
        self.native.source["display_name"] = "接力_99"
        for extra in ({}, {"target": {"model_provider": "p1", "model": "shared", "reasoning_effort": "high"}}):
            response, data = await self.post("start", {**self.start_body(), **extra})
            self.assertEqual(response.status, 409)
            self.assertEqual(data["error"], "sequence_limit")
        self.assertEqual(self.native.mutations(), [])
        self.assertEqual(self.engine.operations, {})
        self.assertEqual(list(self.engine.root.iterdir()), [])

    async def test_target_legacy_and_explicit_each_use_one_fresh_listing(self):
        for explicit in (False, True):
            body = self.start_body()
            if explicit:
                body["target"] = {"model_provider": "p1", "model": "shared", "reasoning_effort": "high"}
            before = len(self.native.calls)
            response, data = await self.post("start", body)
            self.assertEqual(response.status, 200)
            self.assertEqual(self.native.calls[before:], [("GET", "/api/sessions", None)])
            self.assertEqual(data["operation"]["target_settings"]["origin"], "explicit" if explicit else "inherited")
            await self.finish()
            create = self.native.mutations()[-4][2]
            self.assertEqual(set(create), {"cwd", "model_provider", "model", "reasoning_effort",
                                          "agent_backend", "create_in_tmux", "args"})
            expected = body.get("target", {k: self.native.source[k] for k in
                                           ("model_provider", "model", "reasoning_effort")})
            self.assertEqual({k: create[k] for k in expected}, expected)

    async def test_target_shape_matrix_rejected_before_engine_start_no_effects(self):
        good = {"model_provider": "p1", "model": "shared", "reasoning_effort": "high"}
        targets = [None, [], "p1/shared", 3, {}, {"model": "shared"}, {**good, "cwd": "/bad"}]
        for key in good:
            for value in (None, False, 1, [], {}, "", " padded", "padded ", "a\x00b", "a\x7fb",
                          "a\x85b", "x" * 257):
                targets.append({**good, key: value})
        targets.append({**good, "reasoning_effort": "HIGH"})
        with patch.object(self.engine, "start", wraps=self.engine.start) as start:
            for target in targets:
                with self.subTest(target=target):
                    response, data = await self.post("start", {**self.start_body(), "target": target})
                    self.assertEqual((response.status, data.get("error")), (400, "invalid_input"))
            start.assert_not_called()
        self.assertEqual(self.engine.operations, {})
        self.assertEqual(self.engine.requests, {})
        self.assertEqual(list(self.engine.root.iterdir()), [])
        self.assertEqual(self.native.mutations(), [])

    async def test_target_missing_or_malformed_defaults_leave_auth_and_legacy_working(self):
        for defaults in (None, [], "bad"):
            self.native.defaults = defaults
            body = self.start_body()
            response, data = await self.post("start", {**body, "target": {
                "model_provider": "p1", "model": "shared", "reasoning_effort": "high"}})
            self.assertEqual((response.status, data["error"]), (422, "target_unverifiable"))
            response, data = await self.post("start", body)
            self.assertEqual(response.status, 200)
            operation_id = data["operation"]["id"]
            await self.finish()
            response, data = await self.post("status", {"operation_id": operation_id})
            self.assertEqual((response.status, data["operation"]["status"]), (200, "ready"))
        self.assertEqual(len(self.native.mutations()), 12)

    async def test_target_absent_defaults_do_not_break_auth_status_or_unreviewed_continue(self):
        previous = self.native.__call__
        async def no_defaults(method, path, **kwargs):
            status, data = await previous(method, path, **kwargs)
            if method == "GET":
                data.pop("new_session_defaults", None)
            return status, data
        self.engine.upstream_json = no_defaults
        self.native.emit_review = False
        response, data = await self.post("start", self.start_body())
        self.assertEqual(response.status, 200)
        operation_id = data["operation"]["id"]
        await self.finish()
        response, data = await self.post("status", {"operation_id": operation_id})
        self.assertEqual((response.status, data["operation"]["status"]), (200, "unreviewed"))
        response, _ = await self.post("continue", {"operation_id": operation_id})
        self.assertEqual(response.status, 200)
        await self.finish()
        response, data = await self.post("status", {"operation_id": operation_id})
        self.assertEqual((response.status, data["operation"]["status"]), (200, "ready"))
        self.assertEqual(len(self.native.mutations()), 4)

    async def test_non_pi_returns_clear_4xx(self):
        self.native.source["agent_backend"] = "codex"
        response, data = await self.post("start", self.start_body())
        self.assertEqual(response.status, 422)
        self.assertEqual(data["error"], "unsupported_backend")
        self.assertEqual(self.native.mutations(), [])


if __name__ == "__main__":
    unittest.main()
