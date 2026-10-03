#!/usr/bin/python3
"""Real shell/Python entrypoint + fake official executable/adapter effects.

Private PTYs establish terminal gating and inherited stdio only. The injected
chooser/transport do NOT prove their implementations, GUI UI, or captured image.
Every child uses isolated HOME/PATH; fixture sitecustomize tripwires networking.
"""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

sys.dont_write_bytecode = True
TESTS = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("connector_fixture", TESTS / "run_server.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
sys.path.insert(0, str(TESTS.parent / "lib"))
import display_client as client



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
            self.assertEqual(self.actions(), ["list", "stream", "quit"])
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
        self.assert_no_program()

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
            self.assert_no_program()
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
        self.assert_no_program()

    def test_refused_capture_preflight_cleans_only_fresh_known_owner(self):
        self.write_default()
        result = self.invoke("desktop", extra_env={"FAKE_REFUSE_ACTION":"prepare"})
        self.assertNotEqual(result.returncode, 0)
        self.assert_available()
        self.assertIn("abort", self.control_actions())
        result = self.invoke("desktop", extra_env={"FAKE_REFUSE_ACTION":"prepare","FAKE_REFUSE_DIRTY":"1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.host()["phase"], "reconciliation_required")
        self.assertEqual(self.control_actions().count("abort"), 1)
        self.assert_no_program()

    def test_unknown_mutations_never_clean_abort_retry_or_quit(self):
        for action, expected_phase in (("acquire","selecting"),("prepare","preparing"),("launch","connecting")):
            with self.subTest(action=action):
                self.control_state.unlink(missing_ok=True)
                self.control_log.unlink(missing_ok=True)
                self.write_default()
                result = self.invoke("desktop", extra_env={"FAKE_LOST_ACTION":action})
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.host()["phase"], expected_phase)
                self.assertEqual(self.control_actions().count(action), 1)
                self.assertNotIn("abort", self.control_actions())
                self.assertNotIn("control_lost", self.control_actions())
                self.assert_no_program() if action != "launch" else self.assertEqual(self.actions(), ["list"])

    def test_wrong_host_status_never_refreshes_or_aborts(self):
        self.write_default()
        result = self.invoke("desktop", extra_env={"FAKE_STALE":"1","FAKE_WRONG_STATUS_HOST":"1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.host()["phase"], "selecting")
        self.assertNotIn("refresh", self.control_actions())
        self.assertNotIn("abort", self.control_actions())

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
        self.assertEqual(self.actions(), ["list", "stream", "quit"])
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
        self.assertEqual(self.actions(), ["list","stream"])
        self.assertEqual(self.host()["phase"], "reconciliation_required")
        self.assertIn("exit0", result.stderr)
        self.assertNotIn("begin_release", self.control_actions())
        self.assertNotIn("abort", self.control_actions())

    def test_observation_loss_stops_owned_child_without_quit_or_abort(self):
        self.write_default()
        result = self.invoke("desktop", extra_env={"FAKE_LOST_ACTION":"observe_active","FAKE_STREAM_HOLD":"10"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.actions(), ["list","stream"])
        self.assertEqual(self.host()["phase"], "active")
        self.assertNotIn("begin_release", self.control_actions())
        self.assertNotIn("abort", self.control_actions())

    def test_ambiguous_begin_release_never_dispatches_quit(self):
        self.write_default()
        result = self.invoke("desktop", extra_env={"FAKE_LOST_ACTION":"begin_release"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.host()["phase"], "releasing")
        self.assertEqual(self.actions(), ["list","stream"])
        self.assertNotIn("finish_release", self.control_actions())

    def test_quit_zero_without_confirmed_closure_retains(self):
        self.write_default()
        result = self.invoke("desktop", extra_env={"FAKE_QUIT_EFFECT":"none"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.actions(), ["list","stream","quit"])
        self.assertEqual(self.host()["phase"], "reconciliation_required")
        self.assertEqual(self.control_actions().count("finish_release"), 1)
        self.assertNotIn("已确认本次受管 Desktop 关闭", result.stderr)

    def test_confirmed_closure_nonzero_quit_still_releases_but_reports_code(self):
        self.write_default()
        result = self.invoke("desktop", extra_env={"FAKE_QUIT_EXIT":"9"})
        self.assertEqual(result.returncode, 9, result.stderr)
        self.assert_available()

    def test_explicit_binary_path_and_gui_environment_preserved(self):
        self.write_default()
        path = self.root / "official install with spaces" / "Moonlight"
        self.write_program(path, fixture.FAKE_MOONLIGHT)
        result = self.invoke("--moonlight-binary", str(path), "desktop", extra_env={
            "DISPLAY":":fixture", "WAYLAND_DISPLAY":"wayland-fixture", "XAUTHORITY":"fixture authority", "LC_ALL":"C.UTF-8"})
        self.assertEqual(result.returncode, 0, result.stderr)
        records = self.records()
        self.assertEqual(records[0]["lc_all"], "C")
        self.assertEqual(records[1]["lc_all"], "C.UTF-8")
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
        self.assert_no_program()

    def test_signal_after_launch_ack_does_not_dispatch_or_clean_abort(self):
        self.write_default()
        result = self.invoke("desktop", extra_env={"FAKE_SIGNAL_ACTION":"launch"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.actions(), ["list"])
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
            self.assertEqual(self.actions(),["list","stream","quit"])
            stream_pid = self.recorded()["pid"]
            with self.assertRaises(ProcessLookupError):
                os.kill(stream_pid,0)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            reader.join(timeout=1)
            os.close(master)


class PureTests(unittest.TestCase):
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
