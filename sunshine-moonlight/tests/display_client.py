#!/usr/bin/python3
"""Real shell/Python entrypoint + fake official executable/adapter effects.

Private PTYs establish terminal gating and inherited stdio only. The injected
chooser/transport do NOT prove their implementations, GUI UI, or captured image.
Every child uses isolated HOME/PATH; fixture sitecustomize tripwires networking.
"""
from __future__ import annotations
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import types
import unittest
from unittest import mock

sys.dont_write_bytecode = True
TESTS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("connector_fixture", TESTS / "run_server.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
sys.path.insert(0, str(TESTS.parent / "lib"))
import display_client as client
import display_transport as transport



class EntryTests(fixture.ConnectorFixture, unittest.TestCase):
    def control(self):
        return [json.loads(line) for line in self.control_log.read_text().splitlines()]

    def control_actions(self):
        return [event["action"] for event in self.control()]

    def host(self):
        return json.loads(self.control_state.read_text())

    def assert_available(self):
        self.assertEqual(self.host()["phase"], "available")
        self.assertIsNone(self.host()["attempt_revision"])

    def test_real_default_full_order_and_repeated_success(self):
        self.write_default()
        for _ in range(2):
            result = self.invoke("desktop")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.actions(), ["list", "list", "stream", "quit"])
            self.assertEqual(self.recorded()["argv"][1:], fixture.STREAM_ARGS)
            self.assertEqual(self.records()[-1]["argv"][1:], ["quit", "--", "100.64.0.2:47989"])
            self.assert_available()
        actions = self.control_actions()
        for expected in ("list", "acquire", "prepare", "launch", "observe_active", "begin_release", "finish_release"):
            self.assertEqual(actions.count(expected), 2 if expected != "observe_active" else actions.count(expected))
        for i, event in enumerate(self.control()):
            if event["action"] == "begin_release":
                self.assertTrue(event["active_before"])
                self.assertEqual(self.control()[i+1]["action"], "finish_release")
                self.assertFalse(self.control()[i+1]["active_before"])
        # No capability log/argv and no default-path ssh/browser/Tailscale action.
        self.assertNotIn('b' * 64, self.control_log.read_text() + self.log.read_text() + result.stderr)
        self.assert_no_browser()
        self.assert_no_tailscale()

    def test_nonterminal_refuses_before_controller_or_moonlight(self):
        self.write_default()
        result = self.invoke("desktop", terminal=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("需要交互终端", result.stderr)
        self.assertFalse(self.control_log.exists())
        self.assert_no_program()

    def test_resource_free_import_help_list_and_ssh_with_missing_components(self):
        self.write_default()
        for name in ("display_transport.py", "screen_picker.py", "display_client.py"):
            (self.module / "lib" / name).unlink()
        result = self.invoke("--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["desktop", "laptop"])
        result = self.invoke("--ssh", "laptop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded()["argv"][1:], ["-p", "2222", "laptop-alias"])
        self.assertFalse(self.control_log.exists())
        self.assertFalse(self.pick_log.exists())

    def test_missing_transport_and_picker_are_explicit_no_fallback(self):
        self.write_default()
        (self.module / "lib/display_transport.py").unlink()
        result = self.invoke("desktop")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("缺少 display_transport", result.stderr)
        self.assert_no_program()
        (self.module / "lib/display_transport.py").write_text(fixture.FAKE_CONTROLLER)
        (self.module / "lib/screen_picker.py").unlink()
        result = self.invoke("desktop")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("缺少 screen_picker", result.stderr)
        self.assert_available()
        self.assertEqual(self.actions(), ["list"])

    def test_busy_unready_refusal_no_acquire_no_adoption(self):
        self.write_default()
        for extra in ({"FAKE_BUSY":"1"}, {"FAKE_UNREADY":"1"}):
            result = self.invoke("desktop", extra_env=extra)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("acquire", self.control_actions())
            self.assertNotIn("enroll", self.control_actions())
            self.assertNotIn("begin_release", self.control_actions())
            self.assert_no_program()

    def test_esc_and_interrupt_and_typed_picker_error_clean_abort(self):
        self.write_default()
        for mode, expected in (("cancel", 0), ("interrupt",130), ("error",1), ("wrong",1)):
            result = self.invoke("desktop", extra_env={"FAKE_PICK":mode})
            self.assertEqual(result.returncode, expected, result.stderr)
            self.assert_available()
            self.assertEqual(self.actions(), ["list"])
            self.assertNotIn("prepare", self.control_actions())
        self.assertEqual(self.control_actions().count("abort"), 4)

    def test_list_acquire_race_refreshes_read_only_then_acquires(self):
        self.write_default()
        result = self.invoke("desktop", extra_env={"FAKE_ACQUIRE_RACE":"1","FAKE_PICK":"cancel"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.control_actions(), ["construct","list","acquire","list","acquire","status","abort"])
        self.assert_available()

    def test_stale_snapshot_re_presents_real_refreshed_ids_and_snapshots(self):
        self.write_default()
        result = self.invoke("desktop", extra_env={"FAKE_STALE":"1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        picks = [json.loads(line) for line in self.pick_log.read_text().splitlines()]
        self.assertEqual([p["screens"][0]["id"] for p in picks], ["kms:card1:DP-3","kms:card1:HDMI-A-2"])
        prepares = [r for r in self.control() if r["action"] == "prepare"]
        self.assertEqual([(r["display_id"],r["snapshot"]) for r in prepares],
                         [("kms:card1:DP-3",'a'*64),("kms:card1:HDMI-A-2",'c'*64)])
        self.assert_available()

    def test_stale_refresh_esc_works_and_status_is_not_menu(self):
        self.write_default()
        result = self.invoke("desktop", extra_env={"FAKE_STALE":"1","FAKE_PICK":"cancel-refresh"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.control_actions(), ["construct","list","acquire","prepare","status","refresh","status","abort"])
        self.assert_available()
        self.assertEqual(self.actions(), ["list"])

    def test_refused_capture_preflight_cleans_only_fresh_known_owner(self):
        self.write_default()
        result = self.invoke("desktop", extra_env={"FAKE_REFUSE_ACTION":"prepare"})
        self.assertNotEqual(result.returncode, 0)
        self.assert_available()
        self.assertIn("控制器 prepare 拒绝请求（code: backend_unavailable）。", result.stderr)
        clean_calls = ["construct", "list", "acquire", "prepare", "status", "abort"]
        self.assertEqual(self.control_actions(), clean_calls)
        result = self.invoke("desktop", extra_env={"FAKE_REFUSE_ACTION":"prepare","FAKE_REFUSE_DIRTY":"1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.host()["phase"], "reconciliation_required")
        self.assertEqual(self.control_actions(), clean_calls + ["construct", "list", "acquire", "prepare", "status"])
        self.assertEqual(self.actions(), ["list"])

    def test_completed_refusal_code_visible_without_repeating_action(self):
        self.write_default()
        for action, code in (("acquire", "generation_mismatch"),
                             ("prepare", "capture_correspondence_unproven")):
            with self.subTest(action=action):
                self.control_state.unlink(missing_ok=True)
                self.control_log.unlink(missing_ok=True)
                result = self.invoke("desktop", extra_env={
                    "FAKE_REFUSE_ACTION": action, "FAKE_REFUSE_DIRTY": "1", "FAKE_REFUSE_CODE": code})
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(f"错误: 控制器 {action} 拒绝请求（code: {code}）。", result.stderr)
                expected = ["construct", "list", "acquire"]
                if action == "prepare":
                    expected += ["prepare", "status"]
                    self.assertEqual(self.actions(), ["list"])
                    self.assertIn("未确认释放主机预约", result.stderr)
                else:
                    self.assert_no_program()
                self.assertEqual(self.control_actions(), expected)
                self.assertEqual(self.host()["phase"], "reconciliation_required")
                self.assertNotIn("sanitized fixture controller error", result.stderr)
                self.assertNotIn("b" * 64, result.stderr)

    def test_unknown_mutations_never_clean_abort_retry_or_quit(self):
        for action, expected_phase in (("acquire","selecting"),("prepare","preparing"),("launch","connecting")):
            with self.subTest(action=action):
                self.control_state.unlink(missing_ok=True)
                self.control_log.unlink(missing_ok=True)
                self.write_default()
                result = self.invoke("desktop", extra_env={"FAKE_LOST_ACTION":action})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"错误: 控制器 {action} 响应不确定。", result.stderr)
                self.assertEqual(self.host()["phase"], expected_phase)
                self.assertEqual(self.control_actions().count(action), 1)
                self.assertNotIn("abort", self.control_actions())
                self.assertNotIn("control_lost", self.control_actions())
                if action == "acquire":
                    self.assert_no_program()
                else:
                    self.assertEqual(self.actions(), ["list"] if action == "prepare" else ["list", "list"])

    def test_wrong_host_status_never_refreshes_or_aborts(self):
        self.write_default()
        result = self.invoke("desktop", extra_env={"FAKE_STALE":"1","FAKE_WRONG_STATUS_HOST":"1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.host()["phase"], "selecting")
        self.assertNotIn("refresh", self.control_actions())
        self.assertNotIn("abort", self.control_actions())

    def test_pairing_inside_selecting_before_prepare_and_fresh_gate_after(self):
        self.write_default()
        result = self.invoke("desktop", extra_env={"FAKE_INITIAL_PAIRED": "0"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.actions(), ["list", "pair", "list", "list", "stream", "quit"])
        self.assertEqual([r["controller_phase"] for r in self.records()[:4]],
                         ["selecting", "selecting", "selecting", "preparing"])
        actions = self.control_actions()
        self.assertGreaterEqual(actions.count("observe_active"), 1)
        self.assertEqual([a for a in actions if a != "observe_active"],
                         ["construct", "list", "acquire", "prepare", "launch", "begin_release", "finish_release"])
        self.assert_available()

    def test_initial_pair_and_qpa_failures_clean_abort_before_picker_prepare(self):
        self.write_default()
        cases = ({"FAKE_INITIAL_PAIRED": "0", "FAKE_PAIR_EFFECT": "none"},
                 {"FAKE_INITIAL_PAIRED": "0", "FAKE_PAIR_EXIT": "1", "FAKE_PAIR_EFFECT": "none"},
                 {"FAKE_LIST_MODE": "stdout-unknown"})
        for env in cases:
            with self.subTest(env=env):
                start = len(self.control()) if self.control_log.exists() else 0
                result = self.invoke("desktop", extra_env=env)
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.control_actions()[start:], ["construct", "list", "acquire", "status", "abort"])
                self.assertFalse(self.pick_log.exists())
                self.assert_available()

    def test_busy_refuses_unpaired_before_official_pair_no_extra_rpc(self):
        self.write_default()
        result = self.invoke("desktop", extra_env={"FAKE_BUSY": "1", "FAKE_INITIAL_PAIRED": "0"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.control_actions(), ["construct", "list"])
        self.assert_no_program()
        self.assertFalse(self.pick_log.exists())

    def test_postprepare_catalogue_failure_aborts_completed_prepare_not_launch(self):
        self.write_default()
        result = self.invoke("desktop", extra_env={"FAKE_LIST_SEQUENCE": "paired,network"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.actions(), ["list", "list"])
        self.assertEqual([r["controller_phase"] for r in self.records()], ["selecting", "preparing"])
        self.assertEqual(self.control_actions(), ["construct", "list", "acquire", "prepare", "status", "abort"])
        self.assert_available()

    def test_initial_catalogue_failure_requires_fresh_owner_for_abort(self):
        self.write_default()
        result = self.invoke("desktop", extra_env={"FAKE_LIST_MODE": "stdout-unknown", "FAKE_WRONG_STATUS_HOST": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.host()["phase"], "selecting")
        self.assertEqual(self.control_actions(), ["construct", "list", "acquire", "status"])
        self.assertNotIn("abort", self.control_actions())

    def test_identity_refusal_remediation_is_fixed_and_retains_dirty_lease(self):
        self.write_default()
        result = self.invoke("desktop", extra_env={"FAKE_REFUSE_ACTION": "prepare", "FAKE_REFUSE_DIRTY": "1",
                                                   "FAKE_REFUSE_CODE": "host_identity_changed"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.host()["phase"], "reconciliation_required")
        self.assertIn("--reconcile-host-identity", result.stderr)
        self.assertIn("官方 Moonlight", result.stderr)
        self.assertNotIn("abort", self.control_actions())
        self.assertNotIn("launch", self.control_actions())
        message = str(client.RpcFailure("prepare", "host_identity_changed", True))
        self.assertEqual(message, client.REFUSAL_MESSAGES["host_identity_changed"])
        self.assertNotIn("sanitized fixture controller error", message)
        self.assertNotIn("--reconcile-host-identity", str(client.RpcFailure("prepare", "host_identity_changed", False)))

    def test_stock_first_lowercase_title_uniqueness_gate(self):
        self.write_default()
        # Stock resolves the first str.lower title match; duplicates (including
        # Kelvin K, which str.lower maps to k) are ambiguous and refuse launch.
        for catalogue in ("desktop\nDesktop", "DesKtop\nDesktop"):
            result = self.invoke("desktop", extra_env={"FAKE_CATALOGUE":catalogue})
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.actions(), ["list"])
            self.assertNotIn("launch", self.control_actions())
            self.assert_available()
        # One unambiguous Desktop title proceeds; long-s is not a match.
        result = self.invoke("desktop", extra_env={"FAKE_CATALOGUE":"De\u017ftop\nDesktop"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.actions(), ["list", "list", "stream", "quit"])
        self.assert_available()

    def test_malformed_or_missing_desktop_catalogue_fails_before_launch(self):
        self.write_default()
        for catalogue in ("", "Steam Big Picture", "Desktop\n\nOther", "Desktop\r", "Desktop\n" + "x\n" * 200):
            result = self.invoke("desktop", extra_env={"FAKE_CATALOGUE":catalogue})
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.actions(), ["list"])
            self.assert_available()
        self.assertNotIn("launch", self.control_actions())

    def test_gui_exit_zero_without_positive_observation_is_retained(self):
        self.write_default()
        result = self.invoke("desktop", extra_env={"FAKE_QUICK_EXIT":"1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.actions(), ["list", "list", "stream"])
        self.assertEqual(self.host()["phase"], "reconciliation_required")
        self.assertIn("exit0", result.stderr)
        self.assertNotIn("begin_release", self.control_actions())
        self.assertNotIn("abort", self.control_actions())

    def test_observation_loss_stops_owned_child_without_quit_or_abort(self):
        self.write_default()
        result = self.invoke("desktop", extra_env={"FAKE_LOST_ACTION":"observe_active","FAKE_STREAM_HOLD":"10"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.actions(), ["list", "list", "stream"])
        self.assertEqual(self.host()["phase"], "active")
        self.assertNotIn("begin_release", self.control_actions())
        self.assertNotIn("abort", self.control_actions())

    def test_ambiguous_begin_release_never_dispatches_quit(self):
        self.write_default()
        result = self.invoke("desktop", extra_env={"FAKE_LOST_ACTION":"begin_release"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.host()["phase"], "releasing")
        self.assertEqual(self.actions(), ["list", "list", "stream"])
        self.assertNotIn("finish_release", self.control_actions())

    def test_quit_zero_without_confirmed_closure_retains(self):
        self.write_default()
        result = self.invoke("desktop", extra_env={"FAKE_QUIT_EFFECT":"none"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.actions(), ["list", "list", "stream","quit"])
        self.assertEqual(self.host()["phase"], "reconciliation_required")
        self.assertEqual(self.control_actions().count("finish_release"), 1)
        self.assertNotIn("已确认本次受管 Desktop 关闭", result.stderr)

    def test_confirmed_closure_nonzero_quit_still_releases_but_reports_code(self):
        self.write_default()
        result = self.invoke("desktop", extra_env={"FAKE_QUIT_EXIT":"9"})
        self.assertEqual(result.returncode, 9, result.stderr)
        self.assertIn("本地进程返回非零；主机确认", result.stderr)
        self.assert_available()

    def test_nonzero_quit_without_host_cancel_retains(self):
        self.write_default()
        result = self.invoke("desktop", extra_env={"FAKE_QUIT_EXIT": "9", "FAKE_QUIT_EFFECT": "none"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.actions(), ["list", "list", "stream", "quit"])
        self.assertEqual(self.control_actions().count("finish_release"), 1)
        self.assertEqual(self.host()["phase"], "reconciliation_required")
        self.assertNotIn("主机确认 Desktop 已关闭", result.stderr)

    def short_quit_deadline(self):
        # Only the private copied fixture client has a short deadline. The real
        # command's stock 45s deadline remains untouched.
        path = self.module / "lib/display_client.py"
        original = path.read_text()
        self.assertIn("QUERY_TIMEOUT = 45  # stock", original)
        path.write_text(original.replace("QUERY_TIMEOUT = 45  # stock", "QUERY_TIMEOUT = 0.25  # stock"))

    def test_quit_timeout_after_actual_cancel_uses_fresh_host_closure_once(self):
        self.write_default()
        self.short_quit_deadline()
        result = self.invoke("desktop", extra_env={"FAKE_QUIT_HOLD": "3"})
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assert_available()
        self.assertEqual(self.actions(), ["list", "list", "stream", "quit"])
        self.assertEqual(self.control_actions()[-3:], ["begin_release", "status", "finish_release"])
        self.assertIn("本地进程超时；主机确认", result.stderr)
        self.assertNotIn("需要主机可信对账后再连接", result.stderr)

    def test_quit_timeout_without_cancel_retains_and_never_reissues_quit(self):
        self.write_default()
        self.short_quit_deadline()
        result = self.invoke("desktop", extra_env={"FAKE_QUIT_HOLD": "3", "FAKE_QUIT_EFFECT": "none"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.actions(), ["list", "list", "stream", "quit"])
        self.assertEqual(self.control_actions()[-3:], ["begin_release", "status", "control_lost"])
        self.assertNotIn("finish_release", self.control_actions())
        self.assertEqual((self.host()["phase"], self.host()["uncertainty"]),
                         ("reconciliation_required", "owner_lost"))

    def test_quit_timeout_status_or_finish_uncertainty_never_guesses_cleanup(self):
        self.short_quit_deadline()
        for action in ("status", "finish_release"):
            with self.subTest(action=action):
                self.control_state.unlink(missing_ok=True)
                self.control_log.unlink(missing_ok=True)
                self.state.unlink(missing_ok=True)
                self.write_default()
                result = self.invoke("desktop", extra_env={"FAKE_QUIT_HOLD": "3", "FAKE_LOST_ACTION": action})
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.actions(), ["list", "list", "stream", "quit"])
                self.assertEqual(self.control_actions().count("status"), 1)
                self.assertEqual(self.control_actions().count("finish_release"), 0 if action == "status" else 1)
                self.assertNotIn("control_lost", self.control_actions())
                self.assertIn("未确认释放主机预约", result.stderr)

    def test_quit_timeout_malformed_status_or_finish_revision_retains_without_extra_rpc(self):
        self.short_quit_deadline()
        for action in ("status", "finish_release"):
            with self.subTest(action=action):
                self.control_state.unlink(missing_ok=True)
                self.control_log.unlink(missing_ok=True)
                self.state.unlink(missing_ok=True)
                self.write_default()
                result = self.invoke("desktop", extra_env={"FAKE_QUIT_HOLD": "3", "FAKE_MALFORM_REV_ACTION": action})
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.actions(), ["list", "list", "stream", "quit"])
                self.assertEqual(self.control_actions().count(action), 1)
                self.assertNotIn("control_lost", self.control_actions())
                self.assertIn("未确认释放主机预约", result.stderr)

    def test_quit_timeout_changed_host_status_retains_without_finish(self):
        self.write_default()
        self.short_quit_deadline()
        result = self.invoke("desktop", extra_env={"FAKE_QUIT_HOLD": "3", "FAKE_WRONG_STATUS_HOST": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.control_actions()[-2:], ["begin_release", "status"])
        self.assertNotIn("finish_release", self.control_actions())
        self.assertNotIn("control_lost", self.control_actions())

    def test_explicit_binary_path_and_gui_environment_preserved(self):
        self.write_default()
        path = self.root / "official install with spaces" / "Moonlight"
        self.write_program(path, fixture.FAKE_MOONLIGHT)
        result = self.invoke("--moonlight-binary", str(path), "desktop", extra_env={
            "DISPLAY":":fixture", "WAYLAND_DISPLAY":"wayland-fixture", "XAUTHORITY":"fixture authority", "LC_ALL":"C.UTF-8"})
        self.assertEqual(result.returncode, 0, result.stderr)
        records = self.records()
        self.assertEqual(records[0]["lc_all"], "C")
        self.assertEqual(records[1]["lc_all"], "C")
        self.assertEqual(records[2]["lc_all"], "C.UTF-8")
        for record in records:
            self.assertEqual(record["argv"][0], str(path))
            self.assertEqual(record["display"],":fixture")
            self.assertEqual(record["wayland_display"],"wayland-fixture")
            self.assertEqual(record["xauthority"],"fixture authority")

    def test_signal_during_prepare_known_ack_cleanly_aborts(self):
        self.write_default()
        result = self.invoke("desktop", extra_env={"FAKE_SIGNAL_ACTION":"prepare"})
        self.assertEqual(result.returncode, 143, result.stderr)
        self.assert_available()
        self.assertEqual(self.actions(), ["list"])

    def test_signal_after_launch_ack_does_not_dispatch_or_clean_abort(self):
        self.write_default()
        result = self.invoke("desktop", extra_env={"FAKE_SIGNAL_ACTION":"launch"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.actions(), ["list", "list"])
        self.assertEqual(self.host()["phase"], "reconciliation_required")
        self.assertNotIn("abort", self.control_actions())

    def test_sigterm_active_stream_reaps_child_then_owned_quit_release(self):
        import pty
        self.write_default()
        master, slave = pty.openpty()
        captured = bytearray()
        env = dict(self.env, FAKE_STREAM_HOLD="30")
        child = subprocess.Popen([str(self.module / "run_server.sh"),"desktop"], env=env,
                                 stdin=slave, stdout=slave, stderr=slave)
        os.close(slave)
        def drain():
            while True:
                try:
                    data = os.read(master,4096)
                except OSError:
                    return
                if not data:
                    return
                captured.extend(data)
        reader = threading.Thread(target=drain,daemon=True)
        reader.start()
        try:
            deadline = time.monotonic()+10
            while time.monotonic()<deadline:
                if self.control_state.exists() and self.host()["phase"] == "active":
                    break
                if child.poll() is not None:
                    self.fail(captured.decode('utf-8','replace'))
                time.sleep(0.02)
            else:
                self.fail("no injected active state")
            os.kill(child.pid,signal.SIGTERM)
            self.assertEqual(child.wait(timeout=10),143, captured.decode('utf-8','replace'))
            self.assert_available()
            self.assertEqual(self.actions(), ["list", "list", "stream","quit"])
            stream_pid = self.recorded()["pid"]
            with self.assertRaises(ProcessLookupError):
                os.kill(stream_pid,0)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            reader.join(timeout=1)
            os.close(master)


class RefusalBoundaryTests(unittest.TestCase):
    """Actual protocol validation with dummy bytes instead of any SSH process."""
    CAP = "b" * 64
    DETAIL = "synthetic_detail capability=" + CAP + "; PIN=7319; /private/dummy/path; raw_payload"

    def refusal(self, code):
        return {"version": 1, "ok": False, "error": {"code": code, "detail": self.DETAIL}}

    def rpc_failure(self, response):
        target = {"address": "100.64.0.2", "port": 47989}
        runner = mock.Mock(side_effect=AssertionError("real SSH forbidden"))
        controller = transport.ControllerTransport("dummy-host", None, target, runner=runner)
        output, official = io.StringIO(), mock.Mock()
        session = client.Session("dummy", target, controller, mock.Mock(), official, mock.Mock(),
                                 mock.Mock(), io.StringIO(), output)
        session.ticket = ticket = client.Ticket(2, "selecting", None, self.CAP)
        status = {"version": 1, "ok": True, "action": "status", "target": target,
                  "host_uniqueid": "00000000-0000-0000-0000-000000000001", "revision": 2,
                  "phase": "selecting", "uncertainty": None, "attempt_revision": None,
                  "activity": "idle_application", "sampling": "ownership", "runtime": "unverified",
                  "runtime_reason": "start_receipt_required", "receipt_status": "absent"}
        wire_response = response if isinstance(response, (bytes, Exception)) else json.dumps(response).encode()
        with mock.patch.object(controller, "_run", side_effect=[json.dumps(status).encode(), wire_response]) as wire:
            session.rpc("status")  # Pin the dummy identity through the real validator.
            with self.assertRaises(client.RpcFailure) as caught:
                session.owned("prepare", snapshot="a" * 64, display_id="kms:card1:DP-3")
            session.say("错误: " + str(caught.exception))
        self.assertEqual([json.loads(call.args[0])["action"] for call in wire.call_args_list],
                         ["status", "prepare"])
        self.assertIs(session.ticket, ticket)
        runner.assert_not_called()
        self.assertEqual(official.mock_calls, [])
        failure = caught.exception
        public = output.getvalue() + str(failure) + repr(failure) + repr(failure.args)
        for secret in (self.CAP, "7319", "synthetic_detail", "/private/dummy", "raw_payload", '"version"'):
            self.assertNotIn(secret, public)
        return session, failure, output.getvalue()

    def test_validated_codes_visible_without_detail_or_envelope(self):
        for code in ("generation_mismatch", "capture_correspondence_unproven", "readiness_failed", "config_changed"):
            with self.subTest(code=code):
                session, failure, output = self.rpc_failure(self.refusal(code))
                self.assertIs(failure.remote_refusal, True)
                self.assertFalse(session.uncertain)
                self.assertEqual(failure.code, code)
                self.assertEqual(output, f"错误: 控制器 prepare 拒绝请求（code: {code}）。\n")

    def test_installed_rpc_catchall_bytes_are_a_visible_completed_refusal(self):
        spec = importlib.util.spec_from_file_location(
            "refusal_installed_adapter", TESTS.parent / "display-control/install_controller.py")
        installer = importlib.util.module_from_spec(spec)
        # Import source only; no publication, settings or installed host lookup.
        with mock.patch.dict(sys.modules, {spec.name: installer}), mock.patch.object(sys, "path", list(sys.path)):
            spec.loader.exec_module(installer)
            request = {"version": 1, "action": "prepare", "target": {"address": "100.64.0.2", "port": 47989},
                       "revision": 2, "capability": self.CAP, "snapshot": "a" * 64, "display_id": "kms:card1:DP-3"}
            source = types.SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode()))
            sink = types.SimpleNamespace(buffer=io.BytesIO())
            handler = mock.Mock(side_effect=RuntimeError(self.DETAIL))
            with mock.patch.object(installer.os, "geteuid", return_value=1000), \
                 mock.patch.object(installer.os, "getuid", return_value=1000), \
                 mock.patch.object(installer.host, "settings", side_effect=AssertionError("real settings forbidden")), \
                 mock.patch.object(installer.cfg, "load", side_effect=AssertionError("real config forbidden")), \
                 mock.patch("socket.socket.connect", side_effect=AssertionError("network forbidden")), \
                 mock.patch("subprocess.Popen", side_effect=AssertionError("process forbidden")):
                exit_code = installer.rpc_main(stdin=source, stdout=sink, stderr=io.StringIO(), handle=handler)
            self.assertEqual(exit_code, 0)
            handler.assert_called_once()
            self.assertEqual(json.loads(handler.call_args.args[0]), request)
            payload = sink.buffer.getvalue()
            self.assertEqual(json.loads(payload)["error"]["code"], "installation_unavailable")
            session, failure, output = self.rpc_failure(payload)
        self.assertIs(failure.remote_refusal, True)
        self.assertFalse(session.uncertain)
        self.assertEqual(failure.code, "installation_unavailable")
        self.assertEqual(output, "错误: 控制器 prepare 拒绝请求（code: installation_unavailable）。\n")

    def test_unknown_code_shaped_private_data_stays_generic_refusal(self):
        for code in ("future_refusal", "pin_7319", "private_dummy_path", self.CAP,
                     "capability_" + self.CAP, "generation_mismatch_pin_7319", "x" * 80):
            with self.subTest(code=code):
                session, failure, output = self.rpc_failure(self.refusal(code))
                self.assertIs(failure.remote_refusal, True)
                self.assertFalse(session.uncertain)
                self.assertEqual(output, "错误: 控制器 prepare 拒绝请求。\n")

    def test_malformed_response_remains_uncertain_without_payload(self):
        responses = [self.refusal(code) for code in (
            None, True, 7319, [], {"capability": self.CAP}, "x" * 81,
            "generation_mismatch\nPIN=7319", "\x1b[31mpin_7319", "/private/dummy/path", "\u202epin_7319")]
        responses += [dict(self.refusal("generation_mismatch"), capability=self.CAP),
                      {"version": 1, "ok": False, "error": {"code": "generation_mismatch", "detail": self.DETAIL * 8}},
                      {"version": 1, "ok": True, "action": "prepare", "capability": self.CAP}]
        for response in responses:
            with self.subTest(response=response):
                session, failure, output = self.rpc_failure(response)
                self.assertIs(failure.remote_refusal, False)
                self.assertTrue(session.uncertain)
                self.assertEqual(output, "错误: 控制器 prepare 响应不确定。\n")

    def test_transport_failure_never_becomes_refusal_or_exposes_code_detail(self):
        for code in ("transport_timeout", "invalid_response", "generation_mismatch"):
            with self.subTest(code=code):
                session, failure, output = self.rpc_failure(transport.ControllerError(code, self.DETAIL))
                self.assertIs(failure.remote_refusal, False)
                self.assertTrue(session.uncertain)
                self.assertEqual(output, "错误: 控制器 prepare 响应不确定。\n")


class PureTests(unittest.TestCase):
    def test_untrusted_exception_code_and_refusal_flag_stay_generic(self):
        class StringSubclass(str):
            pass
        for code in (None, 7319, ["generation_mismatch"], {"capability": "b" * 64},
                     StringSubclass("generation_mismatch"), "pin_7319", "x" * 10000):
            self.assertEqual(str(client.RpcFailure("prepare", code, True)), "控制器 prepare 拒绝请求。")
        for flag in (False, None, 0, 1, "true", [], {}):
            self.assertEqual(str(client.RpcFailure("prepare", "generation_mismatch", flag)),
                             "控制器 prepare 响应不确定。")

    def test_plain_catalogue_title_lowercase_without_casefold_or_trim(self):
        client.check_desktop_catalogue("Desktop")
        client.check_desktop_catalogue(" unrelated, title \nDesktop")
        client.check_desktop_catalogue("De\u017ftop\nDesktop")  # long-s never matches
        client.check_desktop_catalogue("DesKtop")  # Kelvin K str.lower == desktop
        for value in ("", "\n", "Steam Big Picture", " Desktop", "Desktop\r",
                      "Desktop\nDesktop", "DesKtop\nDesktop", "Desktop\n\nOther",
                      "Desktop\n" + "x\n" * 200):
            with self.assertRaises(client.ClientError):
                client.check_desktop_catalogue(value)

    def test_current_mode_fps_and_bitrate_policy_bounds(self):
        mode = dict(width=3840,height=2160,refresh_num=60000,refresh_den=1001,requested_fps=60)
        args = client.mode_argv(mode)
        self.assertEqual(args[:6],["--resolution","3840x2160","--fps","60","--bitrate","20000"])
        for changed in ({"requested_fps":59},{"width":16385},{"refresh_num":9000,"refresh_den":1000,"requested_fps":9}, {"height":True}):
            with self.assertRaises(client.ClientError):
                client.mode_argv(dict(mode,**changed))
        low = client.mode_argv(dict(width=16,height=16,refresh_num=10,refresh_den=1,requested_fps=10))
        high = client.mode_argv(dict(width=16384,height=16384,refresh_num=240,refresh_den=1,requested_fps=240))
        self.assertEqual(low[5],"500")
        self.assertEqual(high[5],"500000")

    def test_platform_discovery_exact_linux_wrapper_mac_bundle_windows_path(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            wrapper = home / '.local/bin/moonlight'
            wrapper.parent.mkdir(parents=True)
            wrapper.write_text('#!/bin/sh\n')
            wrapper.chmod(0o755)
            self.assertEqual(client.executable(platform='linux',home=home),str(wrapper))
            wrapper.chmod(0o600)
            with self.assertRaises(client.ClientError):
                client.executable(platform='linux',home=home,which=lambda _: '/unrelated-version/moonlight')
            app = home / 'Applications/Moonlight.app/Contents/MacOS/Moonlight'
            app.parent.mkdir(parents=True)
            app.write_text('fake installed executable')
            app.chmod(0o755)
            # Do not read an actual /Applications app on a developer's machine.
            is_file = Path.is_file
            with mock.patch.object(Path,'is_file',lambda p: False if str(p).startswith('/Applications/') else is_file(p)):
                self.assertEqual(client.executable(platform='darwin',home=home,which=lambda _: None),str(app))
            exe = home / 'Moonlight.exe'
            exe.write_text('fake installed executable')
            exe.chmod(0o755)
            self.assertEqual(client.executable(platform='win32',home=home,which=lambda _: str(exe)),str(exe))
            with self.assertRaises(client.ClientError):
                client.executable(platform='win32',home=home,which=lambda _: None)

    def test_native_windows_launcher_names_python_core(self):
        launcher = (TESTS.parent/'run_server.cmd').read_text()
        self.assertIn('py -3 "%~dp0service\\run-server.py" %*',launcher)
        self.assertNotIn('/home/',launcher)
        self.assertNotIn('run_server.sh',launcher)

    def test_real_kernel_lock_excludes_second_process_and_survives_reuse(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ,TMPDIR=tmp,PYTHONDONTWRITEBYTECODE='1')
            code = ("import sys,time; sys.path.insert(0,sys.argv[1]); from display_client import host_lock; "
                    "target={'address':'100.64.0.2','port':47989}; "
                    "ctx=host_lock(target); ctx.__enter__(); print('held',flush=True); time.sleep(float(sys.argv[2])); ctx.__exit__(None,None,None)")
            first = subprocess.Popen([sys.executable,'-B','-c',code,str(TESTS.parent/'lib'),'10'],env=env,
                                     stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                self.assertEqual(first.stdout.readline(),'held\n')
                second = subprocess.run([sys.executable,'-B','-c',code,str(TESTS.parent/'lib'),'0'],env=env,
                                        capture_output=True,text=True,timeout=5)
                self.assertNotEqual(second.returncode,0)
                self.assertIn('本机已有连接器',second.stderr)
            finally:
                first.terminate()
                first.communicate(timeout=5)
            third = subprocess.run([sys.executable,'-B','-c',code,str(TESTS.parent/'lib'),'0'],env=env,
                                   capture_output=True,text=True,timeout=5)
            self.assertEqual(third.returncode,0,third.stderr)
            self.assertEqual(len(list(Path(tmp).glob('quick-deploy-display-*.lock'))),1)


if __name__ == '__main__':
    unittest.main()
