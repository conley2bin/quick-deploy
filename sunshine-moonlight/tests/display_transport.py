#!/usr/bin/env python3
"""Real isolated fake-SSH processes/pipes; no system SSH, HOME or network.

The optional composition test uses the accepted host's existing PRIVATE fake
runtime/native/endpoint fixture, not installed services, displays or settings.
"""
from __future__ import annotations

import base64
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

sys.dont_write_bytecode = True
MODULE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("controller_transport_under_test", MODULE / "lib/display_transport.py")
transport = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transport)
REAL_POPEN = subprocess.Popen
TARGET = {"address": "100.100.20.30", "port": 47989}
HOST = "aabbccdd-1234-4321-abcd-0123456789ab"
OTHER_HOST = "11223344-1234-4321-abcd-0123456789ab"
CAP = "a1" * 32
SNAPSHOT = "b2" * 32
TOPOLOGY = "c3" * 32
DISPLAY = "kms:card1:HDMI-A-2"
FAKE_SSH = r'''
import base64
import json
import os
import sys
import threading
import time

with open(os.environ['CASE'], encoding='utf-8') as f:
    case = json.load(f)
mode = case.get('mode', 'normal')
if mode == 'stall_input':
    time.sleep(10)
raw = sys.stdin.buffer.read() if mode != 'pressure_before_input' else None

def record():
    with open(os.environ['LOG'], 'w', encoding='utf-8') as f:
        json.dump({'argv': sys.argv[1:], 'stdin': raw.decode('utf-8'),
                   'environment': sorted(os.environ)}, f)

if raw is not None:
    record()
if mode == 'closed_pipes':
    os.close(1)
    os.close(2)
    time.sleep(10)
if mode.startswith('flood_'):
    fd = 1 if mode == 'flood_stdout' else 2
    while True:
        os.write(fd, b'x' * 4096)
time.sleep(case.get('delay', 0))

def write(fd, key):
    data = base64.b64decode(case.get(key, ''))
    offset = 0
    while offset < len(data):
        offset += os.write(fd, data[offset:offset + 1024])

workers = [threading.Thread(target=write, args=(1, 'stdout')),
           threading.Thread(target=write, args=(2, 'stderr'))]
for worker in workers:
    worker.start()
for worker in workers:
    worker.join()
if raw is None:
    raw = sys.stdin.buffer.read()
    record()
raise SystemExit(case.get('exit', 0))
'''


def screen(*, runtime="unverified", **fields):
    mapped = runtime == "verified"
    value = {"id": DISPLAY, "label": "HDMI-A-2", "primary": False, "connected": True,
             "selector": "HDMI-A-2", "selector_mapping": "mapped" if mapped else "unverified",
             "selector_candidate": mapped, "mapping_reason": None if mapped else "effective_encoder_device_unknown",
             "native_reason": None, "runtime_capture": runtime, "capture_candidate": mapped,
             "selectable": True, "selection_intent": "prepare", "reason": "preparation_required",
             "width": 3840, "height": 2160, "refresh_num": 60000, "refresh_den": 1001,
             "requested_fps": 60, "rotation": 0}
    value.update(fields)
    return value


def status_fields(revision=4, phase="available", **fields):
    value = {"revision": revision, "phase": phase, "uncertainty": None, "attempt_revision": None}
    value.update(fields)
    return value


def reply(action="list", **fields):
    value = {"version": 1, "ok": True, "action": action, **status_fields()}
    if action in {"list", "status"}:
        value.update(target=dict(TARGET), host_uniqueid=HOST, activity="idle_application",
                     runtime="unverified", runtime_reason="start_receipt_required", receipt_status="absent")
    if action == "list":
        value.update(snapshot=SNAPSHOT, topology_digest=TOPOLOGY, screens=[screen()], selection_ready=True)
    elif action == "status":
        value["sampling"] = "ownership"
    value.update(fields)
    return value


class FakeRunner:
    """Never consult PATH, SSH config, HOME or credentials: absolute fake only."""
    def __init__(self, root):
        self.root = Path(root)
        self.exe = str(self.root / "ssh")
        self.case = self.root / "case.json"
        self.log = self.root / "request.json"
        Path(self.exe).write_text("#!" + sys.executable + "\n" + FAKE_SSH)
        Path(self.exe).chmod(0o700)
        self.calls = []
        self.children = []
        self.started = threading.Event()

    def configure(self, stdout=None, *, stderr=b"", **fields):
        if stdout is None:
            stdout = reply()
        if type(stdout) is dict:
            stdout = json.dumps(stdout, ensure_ascii=True, separators=(",", ":")).encode() + b"\n"
        self.case.write_text(json.dumps({"stdout": base64.b64encode(stdout).decode(),
                                        "stderr": base64.b64encode(stderr).decode(), **fields}))

    def __call__(self, argv, **kwargs):
        assert argv[0] == "ssh" and argv[-1] == "quick-deploy-display-control", "unfixed SSH invocation"
        assert kwargs == {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                          "shell": False, "close_fds": True, "bufsize": 0}, "unexpected auth/process changes"
        self.calls.append(list(argv))
        command = [self.exe] if os.name == "posix" else [sys.executable, self.exe]
        env = {"CASE": str(self.case), "LOG": str(self.log)}
        if os.name == "nt" and "SystemRoot" in os.environ:
            env["SystemRoot"] = os.environ["SystemRoot"]
        child = REAL_POPEN([*command, *argv[1:]], env=env, **kwargs)
        self.children.append(child)
        self.started.set()
        return child


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runner = FakeRunner(self.temp.name)
        self.runner.configure()
        self.controller = transport.ControllerTransport("person@host-alias", 2202, TARGET, runner=self.runner)
        for patch in (mock.patch.object(subprocess, "Popen", side_effect=AssertionError("system SSH/process forbidden")),
                      mock.patch("socket.socket", side_effect=AssertionError("network forbidden")),
                      mock.patch.object(Path, "home", side_effect=AssertionError("real HOME forbidden"))):
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.assert_reaped)

    def assert_reaped(self):
        for child in self.runner.children:
            self.assertIsNotNone(child.poll(), "owned fake SSH not reaped")
            self.assertTrue(all(stream.closed for stream in (child.stdin, child.stdout, child.stderr)))

    def pin(self):
        self.runner.configure()
        self.controller.call("list")

    def assert_error(self, action="list", *, code="invalid_response", remote_refusal=False, **fields):
        with self.assertRaises(transport.ControllerError) as caught:
            self.controller.call(action, **fields)
        error = caught.exception
        self.assertEqual(error.code, code)
        self.assertIs(type(error.remote_refusal), bool)
        self.assertIs(error.remote_refusal, remote_refusal)
        public = str(error) + repr(error) + repr(error.args) + repr(vars(error))
        self.assertNotIn(CAP, public)
        self.assertNotIn("\x1b", public)
        return error

    def test_fixed_argv_port_and_private_accepted_stdin(self):
        self.pin()
        value = reply("acquire", revision=5, phase="selecting", capability=CAP, snapshot=SNAPSHOT)
        self.runner.configure(value)
        self.assertTrue(self.controller.call("acquire", revision=4, snapshot=SNAPSHOT) == value, "acquire reply changed")
        expected = ["ssh", "-T", "-x", "-a", "-o", "ClearAllForwardings=yes", "-p", "2202", "--",
                    "person@host-alias", "quick-deploy-display-control"]
        self.assertEqual(self.runner.calls[-1], expected)
        logged = json.loads(self.runner.log.read_bytes())
        self.assertEqual(logged["argv"], expected[1:])
        raw = logged["stdin"].encode()
        self.assertLessEqual(len(raw), 2048)
        sys.path.insert(0, str(MODULE / "display-control"))
        import protocol
        self.assertEqual(protocol.request(raw), {"version": 1, "action": "acquire", "target": TARGET,
                                                 "revision": 4, "snapshot": SNAPSHOT})
        self.assertNotIn(CAP, repr(expected))
        self.assertNotIn("HOME", logged["environment"])

    def test_default_ssh_port_is_not_overridden(self):
        controller = transport.ControllerTransport("alias", None, TARGET, runner=self.runner)
        controller.call("list")
        self.assertNotIn("-p", self.runner.calls[-1])
        self.assertEqual(self.runner.calls[-1][-3:], ["--", "alias", "quick-deploy-display-control"])

    def test_import_constructor_and_validation_have_no_process_io(self):
        fresh = importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(fresh)
        controller = fresh.ControllerTransport("alias", None, TARGET)
        self.assertIsNone(controller.host_uniqueid)
        for action, fields in (("list", {"path": "private.yaml"}), ("execute", {"command": "echo"}),
                               ("prepare", {"revision": True, "capability": CAP, "snapshot": SNAPSHOT, "display_id": DISPLAY}),
                               ("status", {"target": TARGET}), ("acquire", {"revision": -1, "snapshot": SNAPSHOT}),
                               ("abort", {"revision": 4, "capability": "invalid-token"}),
                               ("prepare", {"revision": 4, "capability": CAP, "snapshot": SNAPSHOT,
                                            "display_id": "kms:card0:HDMI; command"})):
            with self.subTest(action=action, fields=list(fields)):
                self.assert_error(action, code="invalid_request", **fields)
        self.assertEqual(self.runner.children, [])

    def test_invalid_route_option_injection_and_types(self):
        for ssh, port, target in (("-oProxyCommand=bad", None, TARGET), ("host;command", None, TARGET),
                                  ("host\ncommand", None, TARGET), ("alias", True, TARGET), ("alias", "22", TARGET),
                                  ("alias", 0, TARGET), ("alias", 65536, TARGET),
                                  ("alias", None, {"address": "127.0.0.1", "port": 47989}),
                                  ("alias", None, {"address": "100.064.0.1", "port": 47989}),
                                  ("alias", None, {"address": "100.64.0.1", "port": True})):
            with self.subTest(ssh=ssh, port=port):
                with self.assertRaises(transport.ControllerError) as caught:
                    transport.ControllerTransport(ssh, port, target, runner=self.runner)
                self.assertEqual(caught.exception.code, "invalid_route")
        self.assertEqual(self.runner.children, [])

    def test_mutations_require_pinned_read(self):
        self.assert_error("acquire", code="identity_unpinned", revision=4, snapshot=SNAPSHOT)
        self.assertEqual(self.runner.children, [])

    def test_target_is_copied_and_identity_is_pinned_not_hostname(self):
        target = dict(TARGET)
        controller = transport.ControllerTransport("unrelated-ssh-name", None, target, runner=self.runner)
        target["address"] = "100.64.0.99"
        controller.call("list")
        self.assertEqual(controller.host_uniqueid, HOST)
        self.runner.configure(reply("status"))
        controller.call("status")
        self.assertEqual(json.loads(json.loads(self.runner.log.read_bytes())["stdin"])["target"], TARGET)

    def test_changed_target_and_uuid_are_refused_without_repinning(self):
        self.pin()
        for fields, code in (({"target": {"address": "100.100.20.31", "port": 47989}}, "target_mismatch"),
                              ({"target": {"address": TARGET["address"], "port": 48000}}, "target_mismatch"),
                              ({"host_uniqueid": OTHER_HOST}, "identity_mismatch"),
                              ({"host_uniqueid": HOST.upper()}, "invalid_response"),
                              ({"host_uniqueid": "ssh-hostname"}, "invalid_response")):
            with self.subTest(field=next(iter(fields))):
                self.runner.configure(reply("status", **fields))
                self.assert_error("status", code=code)
                self.assertEqual(self.controller.host_uniqueid, HOST)

    def test_malformed_menu_cannot_pin_identity(self):
        value = reply()
        value["screens"][0]["width"] = True
        self.runner.configure(value)
        self.assert_error()
        self.assertIsNone(self.controller.host_uniqueid)

    def test_json_framing_duplicates_utf8_and_nonfinite(self):
        for raw in (b"not json " + CAP.encode(), b"banner\n{}", b"{}{}", b"\xff", b"[]", b"null",
                    b'{"version":1,"version":1,"ok":true}',
                    json.dumps(reply()).replace('"address":', '"address":"100.64.0.1","address":').encode(),
                    b'{"version":1,"ok":false,"error":{"code":"host_busy","detail":"x","detail":"y"}}',
                    b'{"version":1,"ok":false,"error":{"code":"host_busy","detail":NaN}}',
                    b"[" * 1500 + b"]" * 1500):
            with self.subTest(case=raw[:16]):
                self.runner.configure(raw)
                self.assert_error()

    def test_success_envelope_and_status_types_are_strict(self):
        for fields in ({"version": True}, {"version": 2}, {"ok": 1}, {"action": "status"},
                       {"revision": True}, {"revision": -1}, {"revision": 1 << 63}, {"phase": []},
                       {"phase": "prepared"}, {"uncertainty": "owner_lost"}, {"attempt_revision": 5},
                       {"capability": CAP}, {"runtime": "ready"}, {"receipt_status": "trusted"},
                       {"selection_ready": 1}, {"selection_ready": True, "activity": "busy"}):
            with self.subTest(fields=list(fields)):
                self.runner.configure(reply(**fields))
                self.assert_error()
        value = reply()
        del value["host_uniqueid"]
        self.runner.configure(value)
        self.assert_error()

    def test_menu_modes_ids_flags_and_no_hidden_default(self):
        mutations = [{"id": "anything"}, {"id": DISPLAY + ";rm"}, {"label": "screen\x1b[31m"},
                     {"selector_mapping": []}, {"width": True}, {"width": 16385}, {"height": None},
                     {"refresh_num": 0}, {"refresh_den": 0}, {"refresh_num": 1.0}, {"refresh_den": None},
                     {"requested_fps": 59}, {"requested_fps": True}, {"primary": 1},
                     {"selectable": False}, {"capture_candidate": True}, {"selection_intent": "launch"},
                     {"native_reason": "bad\nreason"}, {"rotation": True}]
        for fields in mutations:
            with self.subTest(fields=list(fields)):
                self.runner.configure(reply(screens=[screen(**fields)]))
                self.assert_error()
        for screens in ([screen(), screen()], [screen()] * 33, None):
            self.runner.configure(reply(screens=screens))
            self.assert_error()
        self.runner.configure(reply(screens=[]))
        self.assertEqual(self.controller.call("list")["screens"], [])

    def test_unknown_mode_facts_and_native_only_windows_id_are_preserved(self):
        row = screen(width=1920, height=1080, refresh_num=None, refresh_den=None, requested_fps=None,
                     selectable=False, selection_intent=None, native_reason="current_refresh_unknown",
                     mapping_reason="current_refresh_unknown", selector_mapping="unavailable", reason="current_refresh_unknown")
        self.runner.configure(reply(screens=[row]))
        self.assertEqual(self.controller.call("list")["screens"], [row])
        row.update(id="win:native:{12345678-1234-1234-1234-123456789abc}", selector=None)
        self.runner.configure(reply(screens=[row]))
        self.assertEqual(self.controller.call("list")["screens"][0]["id"], row["id"])

    def test_request_and_all_success_action_shapes(self):
        self.pin()
        records = [
            ("enroll", {}, reply("enroll", revision=5)),
            ("acquire", {"snapshot": SNAPSHOT}, reply("acquire", revision=5, phase="selecting", capability=CAP, snapshot=SNAPSHOT)),
            ("refresh", {"capability": CAP}, reply("refresh", revision=5, phase="selecting", capability=CAP,
                                                   snapshot=SNAPSHOT, topology_digest=TOPOLOGY, screens=[screen()])),
            ("prepare", {"capability": CAP, "snapshot": SNAPSHOT, "display_id": DISPLAY},
             reply("prepare", revision=6, phase="preparing", capability=CAP, display=screen(runtime="verified"),
                   snapshot=SNAPSHOT, topology_digest=TOPOLOGY, restarted=True, launch_authorized=True,
                   desktop_app_id=12345, desktop_title="Desktop", app_resolution="fresh_case_insensitive_title_and_id_match")),
            ("launch", {"capability": CAP}, reply("launch", revision=5, phase="connecting", attempt_revision=5, capability=CAP,
                                                 display=screen(runtime="verified"), desktop_app_id=12345, desktop_title="Desktop",
                                                 app_resolution="fresh_case_insensitive_title_and_id_match", quit_policy="explicit_quit")),
            ("resume", {"capability": CAP}, reply("resume", revision=5, phase="reconnecting", attempt_revision=5, capability=CAP)),
            ("observe_active", {"capability": CAP}, reply("observe_active", revision=5, phase="active", attempt_revision=4, capability=CAP)),
            ("begin_release", {"capability": CAP}, reply("begin_release", revision=5, phase="releasing", attempt_revision=4, capability=CAP)),
            ("finish_release", {"capability": CAP}, reply("finish_release", revision=5, closure_basis="same_generation_owned_desktop_cancelled")),
            ("abort", {"capability": CAP}, reply("abort", revision=5)),
            ("abort", {"capability": CAP}, reply("abort", revision=5, sampling="ownership")),
            ("control_lost", {"capability": CAP}, reply("control_lost", revision=5, phase="reconciliation_required", uncertainty="owner_lost")),
            ("control_lost", {"capability": CAP}, reply("control_lost", revision=4, phase="reconciliation_required", uncertainty="control_lost")),
        ]
        sys.path.insert(0, str(MODULE / "display-control"))
        import protocol
        for action, fields, value in records:
            with self.subTest(action=action, variant=list(fields)):
                self.runner.configure(value)
                self.assertTrue(self.controller.call(action, revision=4, **fields) == value, "action reply changed")
                request = json.loads(self.runner.log.read_bytes())["stdin"].encode()
                self.assertEqual(protocol.request(request)["action"], action)
                self.assertNotIn(CAP, repr(self.runner.calls[-1]))

    def test_owner_revision_snapshot_and_action_bindings(self):
        self.pin()
        valid = reply("acquire", revision=5, phase="selecting", capability=CAP, snapshot=SNAPSHOT)
        for fields in ({"capability": "short-token"}, {"snapshot": "invalid"}, {"snapshot": TOPOLOGY},
                       {"revision": 4}, {"revision": 6}, {"phase": "active"}, {"attempt_revision": 5},
                       {"host_uniqueid": OTHER_HOST}):
            self.runner.configure({**valid, **fields})
            self.assert_error("acquire", revision=4, snapshot=SNAPSHOT)
        valid = reply("refresh", revision=5, phase="selecting", capability=CAP,
                      snapshot=SNAPSHOT, topology_digest=TOPOLOGY, screens=[screen()])
        self.runner.configure({**valid, "capability": "d4" * 32})
        self.assert_error("refresh", revision=4, capability=CAP)

    def test_prepared_display_and_desktop_authority_are_exact(self):
        self.pin()
        valid = reply("prepare", revision=6, phase="preparing", capability=CAP, display=screen(runtime="verified"),
                      snapshot=SNAPSHOT, topology_digest=TOPOLOGY, restarted=False, launch_authorized=True,
                      desktop_app_id=12345, desktop_title="Desktop", app_resolution="fresh_case_insensitive_title_and_id_match")
        for fields in ({"desktop_app_id": True}, {"desktop_app_id": 0}, {"desktop_app_id": 1 << 31},
                       {"desktop_title": "desktop"}, {"app_resolution": "assumed"}, {"launch_authorized": 1},
                       {"launch_authorized": False}, {"restarted": 1}, {"display": screen()},
                       {"display": screen(runtime="verified", id="kms:card0:eDP-1")}):
            self.runner.configure({**valid, **fields})
            self.assert_error("prepare", revision=4, capability=CAP, snapshot=SNAPSHOT, display_id=DISPLAY)

    def test_error_code_detail_redaction_and_repr(self):
        detail = "owned preflight refused; capability=" + CAP + "; token=short-private-value; \x1b]52;c;secret\x07still held\x1b[31m\u202e"
        self.runner.configure({"version": 1, "ok": False, "error": {"code": "capture_correspondence_unproven", "detail": detail}})
        error = self.assert_error(code="capture_correspondence_unproven", remote_refusal=True)
        self.assertIn("owned preflight refused", error.detail)
        self.assertIn("still held", error.detail)
        self.assertIn("[redacted]", error.detail)
        self.assertNotIn("short-private-value", repr(error))
        self.assertNotIn("52;c", error.detail)
        self.assertNotIn("\u202e", error.detail)
        direct = transport.ControllerError(CAP, CAP)
        self.assertNotIn(CAP, repr(direct))
        self.assertIs(direct.remote_refusal, False)
        for flag in (None, 0, 1, "true", [], {}):
            self.assertIs(transport.ControllerError("host_busy", "owned refusal", remote_refusal=flag).remote_refusal, False)
        self.assertIs(transport.ControllerError("host_busy", "owned refusal", remote_refusal=True).remote_refusal, True)

    def test_invalid_error_envelope_never_exposes_its_data(self):
        for error in ({"code": "host_busy", "detail": CAP, "capability": CAP},
                      {"code": CAP + "\x1b", "detail": "x"}, {"code": "host_busy", "detail": {}},
                      {"code": "host_busy", "detail": "x" * 513}):
            self.runner.configure({"version": 1, "ok": False, "error": error})
            self.assert_error()

    def test_auth_host_key_and_missing_shim_diagnostics_no_stdout_dump(self):
        for diagnostic in ("Permission denied (publickey).", "Host key verification failed.",
                           "sh: quick-deploy-display-control: command not found"):
            self.runner.configure(stdout=CAP.encode(), stderr=(diagnostic + "\ncapability=" + CAP + "\x1b[31m").encode(), exit=255)
            error = self.assert_error(code="ssh_failed")
            self.assertIn(diagnostic, error.detail)
            self.assertIn("255", error.detail)
        self.assertNotIn("StrictHostKeyChecking", repr(self.runner.calls))
        self.assertNotIn("BatchMode", repr(self.runner.calls))

    def test_valid_stdout_cannot_override_nonzero_ssh(self):
        refusal = {"version": 1, "ok": False, "error": {"code": "host_busy", "detail": "ownership retained"}}
        for envelope in (reply(), refusal):
            self.runner.configure(envelope, stderr=b"Host key verification failed.", exit=255)
            self.assert_error(code="ssh_failed", remote_refusal=False)
            self.assertIsNone(self.controller.host_uniqueid)

    def test_redaction_precedes_diagnostic_truncation_and_controls(self):
        diagnostic = "preflight " + "x " * 244 + CAP[:32] + "\x1b[31m\u200b" + CAP[32:]
        self.runner.configure(stdout=b"", stderr=diagnostic.encode(), exit=255)
        error = self.assert_error(code="ssh_failed")
        self.assertLessEqual(len(error.detail), 512)
        self.assertNotIn(CAP[:24], error.detail)
        self.assertIn("preflight", error.detail)

    def test_both_stream_pressure_before_stdin_no_deadlock(self):
        raw = json.dumps(reply()).encode()
        raw += b" " * (transport.MAX_OUTPUT - len(raw))
        self.runner.configure(raw, stderr=b"diagnostic\n" * 744, mode="pressure_before_input")
        before = time.monotonic()
        self.assertEqual(self.controller.call("list")["host_uniqueid"], HOST)
        self.assertLess(time.monotonic() - before, 3)
        self.assertEqual(json.loads(json.loads(self.runner.log.read_bytes())["stdin"])["action"], "list")

    def test_exact_stream_bounds_and_overflow_kill_and_reap(self):
        self.runner.configure(reply(), stderr=b"x" * transport.MAX_STDERR)
        self.controller.call("list")
        for key in ("stdout", "stderr"):
            with self.subTest(stream=key):
                fields = {key: b"x" * ((transport.MAX_OUTPUT if key == "stdout" else transport.MAX_STDERR) + 1)}
                self.runner.configure(**fields)
                self.assert_error(code="transport_output_limit")
        for mode in ("flood_stdout", "flood_stderr"):
            self.runner.configure(mode=mode)
            before = time.monotonic()
            self.assert_error(code="transport_output_limit")
            self.assertLess(time.monotonic() - before, 2)

    def test_prepare_has_longer_deadline_and_timeout_is_explicit(self):
        self.pin()
        prepared = reply("prepare", revision=6, phase="preparing", capability=CAP, display=screen(runtime="verified"),
                         snapshot=SNAPSHOT, topology_digest=TOPOLOGY, restarted=True, launch_authorized=True,
                         desktop_app_id=12345, desktop_title="Desktop", app_resolution="fresh_case_insensitive_title_and_id_match")
        self.assertGreater(transport.PREPARE_TIMEOUT, 45 + 5)
        self.assertGreater(transport.PREPARE_TIMEOUT, transport.CALL_TIMEOUT)
        with mock.patch.object(transport, "CALL_TIMEOUT", 0.1), mock.patch.object(transport, "PREPARE_TIMEOUT", 0.8):
            self.runner.configure(prepared, delay=0.25)
            self.assertTrue(self.controller.call("prepare", revision=4, capability=CAP, snapshot=SNAPSHOT, display_id=DISPLAY)["restarted"])
            self.runner.configure(reply(), delay=0.25)
            self.assert_error(code="transport_timeout")
        with mock.patch.object(transport, "PREPARE_TIMEOUT", 0.1):
            self.runner.configure(prepared, delay=10)
            before = time.monotonic()
            self.assert_error("prepare", code="transport_timeout", revision=4, capability=CAP, snapshot=SNAPSHOT, display_id=DISPLAY)
            self.assertLess(time.monotonic() - before, 2)

    def test_unread_input_and_process_after_pipe_eof_have_deadlines(self):
        with mock.patch.object(transport, "CALL_TIMEOUT", 0.1):
            for mode in ("stall_input", "closed_pipes"):
                self.runner.configure(mode=mode)
                self.assert_error(code="transport_timeout")

    def test_threaded_pipe_adapter_pressure_overflow_and_timeout(self):
        # This exercises the Windows pipe algorithm on real POSIX pipes, not
        # a claim of native Windows SSH/console acceptance.
        with mock.patch.object(transport, "os", types.SimpleNamespace(name="nt")):
            raw = json.dumps(reply()).encode()
            self.runner.configure(raw + b" " * (transport.MAX_OUTPUT - len(raw)), stderr=b"x" * 8192,
                                  mode="pressure_before_input")
            self.controller.call("list")
            self.runner.configure(mode="flood_stderr")
            self.assert_error(code="transport_output_limit")
            with mock.patch.object(transport, "CALL_TIMEOUT", 0.1):
                self.runner.configure(mode="stall_input")
                self.assert_error(code="transport_timeout")
        self.assertFalse(self.controller._broken)

    def test_signal_interruption_reaps_only_owned_child(self):
        self.runner.configure(delay=10)
        with mock.patch.object(transport.ControllerTransport, "_posix_pipes", side_effect=KeyboardInterrupt), \
             mock.patch.object(transport.ControllerTransport, "_threaded_pipes", side_effect=KeyboardInterrupt):
            self.assert_error(code="transport_interrupted")
        self.assertNotEqual(self.runner.children[-1].returncode, 0)

    def cleanup_fault(self, algorithm, boundary, *, after=False, failure=False, repeated=False, hold_ack=False):
        """Actual lingering child/pipes; faults only in owned main-thread cleanup."""
        main = threading.get_ident()
        state = {"active": False, "armed": False, "hits": 0, "ack_patched": False}
        operations, workers = [], []
        release_ack = threading.Event()
        self.runner.log.unlink(missing_ok=True)
        self.runner.configure(delay=20)  # Real stdin EOF precedes the timeout.

        def point(name, moment):
            if threading.get_ident() != main:
                return
            if not state["active"] and name not in {"flag", "lock"}:
                return
            if not state["armed"]:
                return
            operations.append((name, moment))
            if name != boundary or moment != ("after" if after else "before"):
                return
            state["hits"] += 1
            if state["hits"] == 1 or repeated:
                raise KeyboardInterrupt(CAP)
            if failure:
                raise OSError(CAP)

        def operation(name, callback):
            point(name, "before")
            result = callback()
            point(name, "after")
            return result

        class Stream:
            def __init__(self, stream, name):
                self.stream, self.name = stream, name
            def __getattr__(self, name):
                return getattr(self.stream, name)
            def close(self):
                return operation(self.name, self.stream.close)

        class Process:
            def __init__(self, child):
                self.child = child
                self.streams = {name: Stream(getattr(child, name), "close_" + name)
                                for name in ("stdin", "stdout", "stderr")}
            def __getattr__(self, name):
                if name == "stdin":
                    return operation("resource_transition", lambda: self.streams[name])
                return self.streams[name] if name in self.streams else getattr(self.child, name)
            def poll(self):
                return operation("poll", self.child.poll)
            def kill(self):
                return operation("kill", self.child.kill)
            def wait(self, **kwargs):
                return operation("wait", lambda: self.child.wait(**kwargs))

        class Controller(transport.ControllerTransport):
            def __setattr__(self, name, value):
                if name == "_broken" and value is False:
                    operation("flag", lambda: object.__setattr__(self, name, value))
                else:
                    object.__setattr__(self, name, value)

        controller = Controller("private-fake", None, TARGET,
                                runner=lambda *args, **kwargs: Process(self.runner(*args, **kwargs)))
        real_lock = controller._lock
        class Lock:
            def __getattr__(self, name):
                return getattr(real_lock, name)
            def release(self):
                return operation("lock", real_lock.release)
        controller._lock = Lock()
        original_cleanup, original_join = controller._cleanup, threading.Thread.join
        def cleanup(process, owned_workers, *args):
            state["active"] = True
            workers[:] = owned_workers
            try:
                if workers and not state["ack_patched"]:
                    state["ack_patched"] = True
                    for index, worker in enumerate(workers):
                        wait = worker._display_complete.wait
                        def acknowledged(*args, name="ack_" + str(index), wait=wait, **kwargs):
                            return operation(name, lambda: wait(*args, **kwargs))
                        patches.enter_context(mock.patch.object(worker._display_complete, "wait", acknowledged))
                    if hold_ack:
                        marker = workers[0]._display_complete
                        set_done = marker.set
                        def held():
                            release_ack.wait(2)
                            set_done()
                        patches.enter_context(mock.patch.object(marker, "set", held))
                        # Models join's misleading stopped-thread observation:
                        # a target acknowledgement, not is_alive, must decide.
                        patches.enter_context(mock.patch.object(workers[0], "is_alive", return_value=False))
                point("entry", "before")
                result = original_cleanup(process, owned_workers, *args)
                point("return", "after")
                return result
            finally:
                state["active"] = False
        def join(worker, *args, **kwargs):
            name = "join_" + str(workers.index(worker)) if worker in workers else "other_join"
            return operation(name, lambda: original_join(worker, *args, **kwargs))

        start, before_dispatch = time.monotonic(), len(self.runner.calls)
        expected = "transport_cleanup" if failure or repeated or hold_ack or boundary == "lock" else "transport_interrupted"
        error = None
        try:
            with ExitStack() as patches:
                patches.enter_context(mock.patch.object(transport, "CALL_TIMEOUT", 0.12))
                patches.enter_context(mock.patch.object(transport, "CLEANUP_TIMEOUT", 0.08))
                patches.enter_context(mock.patch.object(controller, "_cleanup", side_effect=cleanup))
                patches.enter_context(mock.patch.object(threading.Thread, "join", join))
                if algorithm == "threaded":
                    patches.enter_context(mock.patch.object(transport, "os", types.SimpleNamespace(name="nt")))
                state["armed"] = True
                try:
                    controller.call("list")
                except BaseException as exc:
                    error = exc
            state["armed"] = False
            self.assertTrue(isinstance(error, transport.ControllerError), "cleanup escaped an untyped interruption")
            self.assertEqual(error.code, expected)
            self.assertIs(error.remote_refusal, False)
            self.assertIn("interrupted", error.detail)
            self.assertNotIn(CAP, str(error) + repr(error) + repr(vars(error)))
            self.assertLess(time.monotonic() - start, 1.5)
            self.assertTrue(self.runner.log.exists(), "fake did not consume request EOF")
            self.assertEqual(len(self.runner.calls), before_dispatch + 1, "RPC was retried")
            self.assertGreater(state["hits"], 0, "boundary was not exercised")
            self.assertFalse(real_lock.locked())
            child = self.runner.children[-1]
            if expected == "transport_interrupted":
                self.assertIsNotNone(child.poll())
                self.assertTrue(all(s.closed for s in (child.stdin, child.stdout, child.stderr)))
                self.assertTrue(all(not w.is_alive() for w in workers))
                self.assertFalse(controller._broken)
            else:
                self.assertTrue(controller._broken)
                with self.assertRaises(transport.ControllerError) as refused:
                    controller.call("status")
                self.assertEqual(refused.exception.code, "transport_cleanup")
                self.assertIs(refused.exception.remote_refusal, False)
                self.assertEqual(len(self.runner.calls), before_dispatch + 1)
                if boundary in {"kill", "wait", "close_stdout"}:
                    self.assertIn(("close_stderr", "before"), operations)
                    if algorithm == "threaded":
                        self.assertIn(("join_2", "before"), operations)
            if repeated:
                self.assertEqual(state["hits"], 2, "cleanup callback retry was not bounded")
        finally:
            state["armed"] = False
            release_ack.set()
            # A deliberate cleanup-failure test may retain a live owned child;
            # the harness disposes only that private child, never a process group.
            for child in self.runner.children[before_dispatch:]:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=2)
                for stream in (child.stdin, child.stdout, child.stderr):
                    stream.close()
            for worker in workers:
                original_join(worker, 1)
                self.assertFalse(worker.is_alive())
            if real_lock.locked():
                real_lock.release()

    def test_cleanup_interruptions_at_each_owned_resource(self):
        for algorithm in ("posix", "threaded"):
            boundaries = ["poll", "kill", "wait", "close_stdin", "close_stdout", "close_stderr"]
            if algorithm == "threaded":
                boundaries += ["join_0", "join_1", "join_2", "ack_0", "ack_1", "ack_2"]
            for boundary in boundaries:
                for after in (False, True):
                    with self.subTest(algorithm=algorithm, boundary=boundary, after=after):
                        self.cleanup_fault(algorithm, boundary, after=after)

    def test_cleanup_entry_and_transition_interruptions(self):
        for algorithm in ("posix", "threaded"):
            for boundary, after in (("entry", False), ("resource_transition", False),
                                    ("resource_transition", True), ("return", True),
                                    ("flag", False), ("flag", True), ("lock", False), ("lock", True)):
                with self.subTest(algorithm=algorithm, boundary=boundary, after=after):
                    self.cleanup_fault(algorithm, boundary, after=after)

    def test_interruption_then_cleanup_failure_retains_first_and_breaks_reuse(self):
        for algorithm in ("posix", "threaded"):
            boundaries = ["kill", "wait", "close_stdout"]
            if algorithm == "threaded":
                boundaries.append("join_0")
            for boundary in boundaries:
                with self.subTest(algorithm=algorithm, boundary=boundary):
                    self.cleanup_fault(algorithm, boundary, failure=True)

    def test_cleanup_requires_worker_ack_after_interrupted_join(self):
        self.cleanup_fault("threaded", "join_0", hold_ack=True)

    def test_repeated_cleanup_interruptions_are_bounded(self):
        for algorithm in ("posix", "threaded"):
            for boundary in ("poll", "entry"):
                with self.subTest(algorithm=algorithm, boundary=boundary):
                    self.cleanup_fault(algorithm, boundary, repeated=True)

    def test_concurrent_calls_refuse_without_spawning_extra_process(self):
        self.runner.configure(delay=0.2)
        errors = []
        def call():
            try:
                self.controller.call("list")
            except BaseException as exc:
                errors.append(type(exc).__name__)
        worker = threading.Thread(target=call)
        worker.start()
        try:
            self.assertTrue(self.runner.started.wait(1))
            self.assert_error("status", code="transport_busy")
        finally:
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(self.runner.children), 1)

    def test_spawn_failure_does_not_expose_runner_exception(self):
        def broken(*args, **kwargs):
            raise OSError(CAP)
        controller = transport.ControllerTransport("alias", None, TARGET, runner=broken)
        with self.assertRaises(transport.ControllerError) as caught:
            controller.call("list")
        self.assertEqual(caught.exception.code, "transport_io")
        self.assertNotIn(CAP, repr(caught.exception))

    @unittest.skipUnless(os.name == "posix", "accepted host fake lifecycle fixture is Linux-backed")
    def test_actual_accepted_host_response_composition(self):
        sys.path.insert(0, str(MODULE / "tests"))
        sys.path.insert(0, str(MODULE / "display-control"))
        import display_lifecycle as fixture_module
        with tempfile.TemporaryDirectory() as root:
            fixture = fixture_module.Fixture(root)
            def call(action, **fields):
                actual = fixture.rpc(action, **fields)
                self.runner.configure(actual)
                self.assertTrue(self.controller.call(action, **fields) == actual, "accepted host reply changed")
                return actual
            status = call("status")
            call("enroll", revision=status["revision"])
            menu = call("list")
            ticket = call("acquire", revision=menu["revision"], snapshot=menu["snapshot"])
            def owner(action, **fields):
                return call(action, revision=ticket["revision"], capability=ticket["capability"], **fields)
            ticket = owner("refresh")
            ticket = owner("prepare", snapshot=ticket["snapshot"], display_id=fixture_module.SECOND)
            ticket = owner("launch")
            fixture.app = ticket["desktop_app_id"]
            ticket = owner("observe_active")
            ticket = owner("resume")
            ticket = owner("observe_active")
            ticket = owner("begin_release")
            fixture.app = 0
            closed = owner("finish_release")
            self.assertEqual(closed["phase"], "available")
            menu = call("list")
            ticket = call("acquire", revision=menu["revision"], snapshot=menu["snapshot"])
            ticket = owner("abort")
            menu = call("list")
            ticket = call("acquire", revision=menu["revision"], snapshot=menu["snapshot"])
            owner("control_lost")
            self.assertEqual(call("status")["phase"], "reconciliation_required")


if __name__ == "__main__":
    unittest.main()
