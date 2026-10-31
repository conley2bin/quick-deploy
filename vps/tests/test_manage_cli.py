"""Foreground manage.sh: fake connect, real ManagementServer, real child process signals (temp state, no SSH)."""
import io
import json
import os
from pathlib import Path
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

LIB = Path(__file__).resolve().parents[1] / 'lib'
sys.path.insert(0, str(LIB))
import manage
import manage_auth
import manage_cli
import manage_service as ms
import vps
from test_manage_service_integration import free_port, private_repo

WRAPPER = '''
import json, sys
sys.path.insert(0, sys.argv[1] + "/vps/lib")
import manage_cli
def fake_main(argv):
    open(sys.argv[2], "a").write(json.dumps(argv) + "\\n")
    return 0
manage_cli.vps.main = fake_main
sys.exit(manage_cli.main(sys.argv[3:]))
'''


class Child:
    def __init__(self, test, root, extra=(), pending=False):
        self.repo = private_repo(root)
        self.state = Path(root) / 'state'
        self.state.mkdir(mode=0o700)
        self.port = free_port()
        manage_auth.ensure_state(self.state, self.repo, port=self.port)
        if pending:
            (self.state / 'pending.json').write_text('{}')
        self.calls = Path(root) / 'calls.jsonl'
        env = {k: v for k, v in os.environ.items() if k != 'BROWSER'}
        self.proc = subprocess.Popen(
            [sys.executable, '-B', '-c', WRAPPER, str(self.repo), str(self.calls), '--state-dir', str(self.state), *extra],
            env=env, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        test.addCleanup(self.kill)

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.communicate()

    def wait_line(self, prefix, timeout=20):
        end, buf = time.time() + timeout, b''
        while time.time() < end:
            if select.select([self.proc.stdout], [], [], 0.2)[0]:
                chunk = os.read(self.proc.stdout.fileno(), 4096)
                if not chunk:
                    break
                buf += chunk
                if prefix.encode() in buf and buf.endswith(b'\n') and buf.count(b'\n') >= 2:
                    return buf.decode()
        self.kill()
        raise AssertionError('no %r line; got %r' % (prefix, buf))


class ForegroundProcess(unittest.TestCase):
    def test_default_connects_prints_link_serves_and_sigint_releases_everything(self):
        with tempfile.TemporaryDirectory() as root:
            child = Child(self, root)
            text = child.wait_line('管理页面: ')
            line = [l for l in text.splitlines() if l.startswith('管理页面: ')][0]
            entry = child.repo / 'vps/index.html'
            self.assertEqual(line, '管理页面: ' + entry.as_uri())
            self.assertNotRegex(line, r'[?#]|key|token')
            self.assertIn('保持此窗口运行；Ctrl+C 关闭；页面刷新有效，无固定时限', text)
            self.assertTrue(entry.is_file())
            self.assertEqual(json.loads(child.calls.read_text().splitlines()[0]),
                             ['--state-dir', str(child.state), 'connect', '--ssh', 'root@136.0.5.229'])
            self.assertIs(ms.query_state(child.state)['busy'], False)
            child.proc.send_signal(2)
            out, err = child.proc.communicate(timeout=30)
            self.assertEqual(child.proc.returncode, 0, err.decode()[:400])
            with socket.socket() as sock:  # port released (no listener; TIME_WAIT only)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(('127.0.0.1', child.port))
            self.assertFalse(ms.legacy_manager_active(child.state))  # session flock released

    def test_sigterm_also_stops(self):
        with tempfile.TemporaryDirectory() as root:
            child = Child(self, root)
            child.wait_line('管理页面: ')
            child.proc.terminate()
            child.proc.communicate(timeout=30)
            self.assertEqual(child.proc.returncode, 0)

    def test_sighup_terminal_close_stops_cleanly(self):
        with tempfile.TemporaryDirectory() as root:
            child = Child(self, root)
            child.wait_line('管理页面: ')
            self.assertEqual(ms.query_state(child.state)['service_mode'], 'foreground')
            child.proc.send_signal(1)
            child.proc.communicate(timeout=30)
            self.assertEqual(child.proc.returncode, 0)
            self.assertFalse(ms.legacy_manager_active(child.state))

    def test_pending_state_skips_connect_but_still_serves_for_recovery(self):
        with tempfile.TemporaryDirectory() as root:
            child = Child(self, root, pending=True)
            child.wait_line('管理页面: ')
            self.assertFalse(child.calls.exists())
            child.proc.send_signal(2)
            _, err = child.proc.communicate(timeout=30)
            self.assertIn('显式恢复', err.decode())


JOB_WRAPPER = '''
import json, os, subprocess, sys, threading, time
sys.path.insert(0, sys.argv[1] + "/vps/lib")
import manage, manage_cli
manage.STOP_GRACE = 0.2  # test speed only; production stays 20
real = subprocess.Popen
pidfile = sys.argv[2]
def fake_popen(argv, **kw):
    return real(["sh", "-c", "echo $$ > " + pidfile + "; exec sleep 60"], stdin=kw["stdin"], stdout=kw["stdout"],
                stderr=kw["stderr"], start_new_session=True)
class Server(manage.ManagementServer):
    def write_launcher(self):
        path = super().write_launcher()
        job = {"job_id": "a" * 32, "request_id": "b" * 32, "state": "running", "started_at": time.time(),
               "_started": time.monotonic(), "elapsed_s": 0, "deadline_s": 1200, "generation_before": None,
               "action": "add", "names": ["x"]}
        self._persist(job, "f")
        self.jobs[job["job_id"]] = job
        self.last_job = job["job_id"]
        manage.subprocess.Popen = fake_popen
        self.worker = threading.Thread(target=self._run, args=(job, "f", {}), daemon=False)
        self.worker.start()
        return path
manage.ManagementServer = Server
manage_cli.vps.main = lambda argv: 0
sys.exit(manage_cli.main(sys.argv[3:]))
'''


class ActiveJobStop(unittest.TestCase):
    def test_sighup_with_active_job_ends_group_and_records_unknown(self):
        with tempfile.TemporaryDirectory() as root:
            child = Child(self, root)
            child.proc.kill()
            child.proc.communicate()
            pidfile = Path(root) / 'job.pid'
            proc = subprocess.Popen([sys.executable, '-B', '-c', JOB_WRAPPER, str(child.repo), str(pidfile),
                                     '--state-dir', str(child.state)], cwd=root, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.addCleanup(lambda: proc.poll() is None and proc.kill())
            end = time.time() + 20
            while time.time() < end and not (pidfile.exists() and pidfile.read_text().strip()):
                time.sleep(0.05)
            pid = int(pidfile.read_text())
            os.kill(pid, 0)
            proc.send_signal(1)
            _, err = proc.communicate(timeout=30)
            self.assertEqual(proc.returncode, 0, err.decode()[-400:])
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
            with self.assertRaises(ProcessLookupError):
                os.killpg(pid, 0)
            record = json.loads((child.state / 'manage-jobs' / ('a' * 32 + '.json')).read_text())
            self.assertEqual(record['job']['state'], 'finished')
            self.assertEqual(record['job']['error']['code'], 'interrupted_unknown')
            self.assertTrue(record['unknown'])
            with socket.socket() as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(('127.0.0.1', child.port))
            self.assertFalse(ms.legacy_manager_active(child.state))


class SecondManager(unittest.TestCase):
    def test_second_default_start_refuses_without_connecting(self):
        with tempfile.TemporaryDirectory() as root:
            first = Child(self, root)
            first.wait_line('管理页面: ')
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(manage_cli.vps, 'main', return_value=0) as fake:
                code = manage_cli.main(['--state-dir', str(first.state)], out, err)
            self.assertEqual(code, 1)
            fake.assert_not_called()
            self.assertEqual(out.getvalue(), '')
            self.assertIn('manager_active', err.getvalue())
            self.assertIs(ms.query_state(first.state)['busy'], False)


class Carrier(unittest.TestCase):
    def snapshot(self, runtime):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        state = Path(temp.name) / 'state'
        state.mkdir(mode=0o700)
        manage_auth.ensure_state(state, Path(temp.name), port=free_port())
        server = manage.ManagementServer(vps, state, runtime_carrier=runtime)
        self.addCleanup(server.close)
        return server.snapshot(), server

    def test_direct_runtime_carrier_needs_no_carrier_json(self):
        snap, server = self.snapshot({'ssh_proxy': None, 'independent': True})
        self.assertEqual(snap['carrier'], {'configured': True, 'listening': True, 'mode': 'direct'})
        self.assertEqual(server.carrier(), (None, None))
        self.assertFalse((server.state / 'manage-service/carrier.json').exists())

    def test_proxy_without_ack_is_read_only_with_specific_reason(self):
        snap, server = self.snapshot({'ssh_proxy': 'socks5://127.0.0.1:9', 'independent': False})
        self.assertEqual(snap['writable_reason'], 'independent_carrier_required')
        self.assertFalse(snap['writable'])

    def test_acknowledged_proxy_listener_down_is_reported(self):
        port = free_port()
        snap, server = self.snapshot({'ssh_proxy': 'socks5://127.0.0.1:%d' % port, 'independent': True})
        self.assertEqual(snap['writable_reason'], 'carrier_listener_down')

    def test_default_none_keeps_carrier_json_semantics(self):
        snap, server = self.snapshot(None)
        self.assertEqual(snap['writable_reason'], 'carrier_not_configured')
        self.assertEqual(server.stop_grace, 0)


class Cli(unittest.TestCase):
    def run_main(self, argv, state):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(manage_cli.vps, 'main', return_value=0) as fake:
            code = manage_cli.main(['--state-dir', str(state), *argv], out, err)
        return code, fake, out.getvalue(), err.getvalue()

    def test_retired_service_commands_explain_and_install_nothing(self):
        for command in manage_cli.RETIRED:
            code, fake, _, err = self.run_main([command], '/nonexistent')
            self.assertEqual(code, 2)
            self.assertIn('前台', err)
            fake.assert_not_called()

    def test_devices_connect_first_then_delegate_with_proxy(self):
        with tempfile.TemporaryDirectory() as root:
            code, fake, _, _ = self.run_main(['--ssh-proxy', 'socks5://127.0.0.1:1080', 'devices', 'add', 'alice'], root)
            self.assertEqual(code, 0)
            first, second = (c.args[0] for c in fake.call_args_list)
            self.assertEqual(first[2:5], ['connect', '--ssh', 'root@136.0.5.229'])
            self.assertIn('--ssh-proxy', first)
            self.assertEqual(second[:5], ['--state-dir', str(Path(root)), 'devices', 'add', 'alice'])

    def test_dashboard_is_delegated_after_connect_without_proxy_flag(self):
        with tempfile.TemporaryDirectory() as root:
            code, fake, _, _ = self.run_main(['--ssh-proxy', 'socks5://127.0.0.1:1080', 'dashboard', '--output', 'x'], root)
            self.assertEqual(code, 0)
            first, second = (c.args[0] for c in fake.call_args_list)
            self.assertEqual(first[2], 'connect')
            self.assertEqual(second, ['--state-dir', str(Path(root)), 'dashboard', '--output', 'x'])

    def test_recover_never_connects_and_pending_skips_connect(self):
        with tempfile.TemporaryDirectory() as root:
            code, fake, _, _ = self.run_main(['recover'], root)
            self.assertEqual([c.args[0][2] for c in fake.call_args_list], ['recover'])
            (Path(root) / 'pending.json').write_text('{}')
            code, fake, _, err = self.run_main(['devices', 'list'], root)
            self.assertEqual([c.args[0][2] for c in fake.call_args_list], ['devices'])
            self.assertIn('显式恢复', err)

    def test_explicit_mihomo_is_resolved_and_passed_to_server(self):
        seen = {}
        def fake_run(vps_module, state, carrier, ready, prepare=None, mihomo=None):
            seen['mihomo'] = mihomo
            return 0
        with tempfile.TemporaryDirectory() as root, mock.patch.object(manage_cli.manage, 'run_foreground', fake_run), \
                mock.patch.object(manage_cli.vps, 'resolve_mihomo', return_value='/abs/mihomo') as resolve:
            out, err = io.StringIO(), io.StringIO()
            manage_cli.main(['--state-dir', root, '--mihomo', 'mih'], out, err)
            resolve.assert_called_once_with('mih')
            self.assertEqual(seen['mihomo'], '/abs/mihomo')
            manage_cli.main(['--state-dir', root], out, err)
            self.assertIsNone(seen['mihomo'])

    def test_ctrl_c_during_connect_is_concise_130(self):
        with tempfile.TemporaryDirectory() as root:
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(manage_cli.vps, 'main', side_effect=KeyboardInterrupt):
                code = manage_cli.main(['--state-dir', root], out, err)
            self.assertEqual(code, 130); self.assertTrue(err.getvalue().endswith('已取消连接\n')); self.assertNotIn('Traceback', err.getvalue())

    def test_explicit_ssh_overrides_and_skips_config(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.object(
                manage_cli.target_config, 'default_ssh', side_effect=AssertionError('config read')):
            for argv in (['--ssh', 'me@example.org', 'devices', 'list'], ['devices', 'list', '--ssh', 'me@example.org']):
                code, fake, _, _ = self.run_main(argv, root)
                self.assertEqual(code, 0)
                self.assertEqual(fake.call_args_list[0].args[0][2:5], ['connect', '--ssh', 'me@example.org'])

    def test_default_serve_connects_only_after_lock_and_before_ready(self):
        order = []
        with tempfile.TemporaryDirectory() as root:
            def fake_run(vps_module, state, carrier, ready, prepare=None, mihomo=None):
                order.append('locked')
                prepare()
                ready(Path(root) / 'index.html', 'o')
                return 0
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(manage_cli.vps, 'main', side_effect=lambda a: order.append('connect') or 0), \
                    mock.patch.object(manage_cli.manage, 'run_foreground', fake_run), \
                    mock.patch.object(ms, 'query_state'):
                self.assertEqual(manage_cli.main(['--state-dir', root], out, err), 0)
            self.assertEqual(order, ['locked', 'connect'])
            self.assertIn('管理页面: ', out.getvalue())

    def test_connect_failure_propagates_and_never_announces(self):
        ready = mock.Mock()
        def fake_run(vps_module, state, carrier, ready_cb, prepare=None, mihomo=None):
            prepare()
            ready_cb(None, None)
        with tempfile.TemporaryDirectory() as root, mock.patch.object(manage_cli.manage, 'run_foreground', fake_run):
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(manage_cli.vps, 'main', return_value=1):
                code = manage_cli.main(['--state-dir', root], out, err)
            self.assertEqual((code, out.getvalue()), (1, ''))

    def test_hyperlink_osc8_only_on_tty_and_plain_uri_present(self):
        uri = 'file:///a/vps/index.html'
        self.assertEqual(manage_cli.hyperlink(uri, False), uri)
        linked = manage_cli.hyperlink(uri, True)
        self.assertTrue(linked.startswith('\x1b]8;;' + uri + '\x1b\\') and uri + '\x1b]8;;\x1b\\' in linked)

    def test_no_open_is_ignored_and_no_service_or_opener_code(self):
        self.assertTrue(manage_cli.parser().parse_args(['--no-open']).no_open)
        source = (LIB / 'manage_cli.py').read_text()
        for word in ('systemctl', 'xdg-open', 'subprocess'):
            self.assertNotIn(word, source)


if __name__ == '__main__':
    unittest.main()
