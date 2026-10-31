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
import threading
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


TRIPLE = {'endpoint': '136.0.5.229', 'user': 'root', 'port': 22}
REUSED_TEXT = '已在运行，复用现有管理页面'
TARGET, ENDPOINT = 'root@136.0.5.229', '136.0.5.229'
SECRET = 'machine-uuid-must-not-leak'


def write_binding(state, target=TARGET, endpoint=ENDPOINT, port=22):
    vps.host.atomic(state / 'state.json', json.dumps({'current': {
        'target': target, 'endpoint': endpoint, 'port': port, 'machine_id': SECRET, 'user': 'root'}, 'owner': SECRET}))


class SecondManager(unittest.TestCase):
    def test_second_start_without_declared_target_refuses_without_connecting(self):
        with tempfile.TemporaryDirectory() as root:
            first = Child(self, root)  # fake connect writes no state.json: nothing to bind
            first.wait_line('管理页面: ')
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(manage_cli.vps, 'main', return_value=0) as fake, \
                    mock.patch.object(manage_cli.vps, 'MODULE', first.repo / 'vps'):
                code = manage_cli.main(['--state-dir', str(first.state)], out, err)
            self.assertEqual(code, 1)
            fake.assert_not_called()
            self.assertEqual(out.getvalue(), '')
            self.assertIn('manager_active', err.getvalue())
            self.assertIs(ms.query_state(first.state)['busy'], False)


class Live(unittest.TestCase):
    """A real foreground-mode ManagementServer (bound to the startup record, page written) plus repeat starts via manage_cli.main."""
    def setUp(self, bind=True):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = private_repo(self.root)
        self.state = self.root / 'state'
        self.state.mkdir(mode=0o700)
        manage_auth.ensure_state(self.state, self.repo, port=free_port())
        write_binding(self.state)
        patch = mock.patch.object(vps, 'MODULE', self.repo / 'vps')
        patch.start()
        self.addCleanup(patch.stop)
        resolve = mock.patch.object(vps, 'target_record', return_value=TRIPLE)
        self.resolve = resolve.start()
        self.addCleanup(resolve.stop)
        self.server = manage.ManagementServer(vps, self.state, runtime_carrier={'ssh_proxy': None, 'independent': True})
        self.addCleanup(self.server.close)
        if bind:
            self.server.bind_target()
        self.entry = self.server.write_launcher()
        self.server._serving = True
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(self.server.httpd.shutdown)

    def repeat(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(manage_cli.vps, 'main', return_value=0) as fake, \
                mock.patch.object(manage.ManagementServer, 'write_launcher', side_effect=AssertionError('launcher write')):
            code = manage_cli.main(['--state-dir', str(self.state), *argv], out, err)
        return code, fake, out.getvalue(), err.getvalue()


class RepeatStart(Live):
    def test_same_vps_reuses_link_without_connect_post_or_page_rewrite(self):
        before = self.entry.read_bytes()
        stat_before = os.stat(self.entry)
        code, fake, out, err = self.repeat()
        self.assertEqual((code, err), (0, ''))
        fake.assert_not_called()
        self.assertIn('管理页面: ' + self.entry.as_uri(), out)
        self.assertIn('已在运行，复用现有管理页面；原终端负责生命周期', out)
        self.assertIn('直连', out)
        self.assertEqual(self.entry.read_bytes(), before)
        self.assertEqual(os.stat(self.entry).st_mtime_ns, stat_before.st_mtime_ns)
        self.assertEqual((self.server.jobs, self.server.worker), ({}, None))  # no admitted job
        self.assertIs(ms.query_state(self.state)['busy'], False)  # owner unchanged and serving
        self.assertFalse((self.state / 'manage-service/carrier.json').exists())

    def test_explicit_ssh_equal_to_running_target_reuses(self):
        code, fake, out, _ = self.repeat('--ssh', TARGET)
        self.assertEqual(code, 0)
        fake.assert_not_called()

    def test_busy_manager_page_is_reused_but_nothing_is_refreshed(self):
        gate = threading.Event()
        job = {'job_id': 'a' * 32, 'request_id': 'b' * 32, 'state': 'running', 'started_at': time.time(),
               '_started': time.monotonic(), 'elapsed_s': 0, 'deadline_s': 1200, 'generation_before': None,
               'action': 'add', 'names': ['x']}
        self.server.jobs[job['job_id']] = job
        self.server.last_job = job['job_id']
        self.server.worker = threading.Thread(target=gate.wait)
        self.server.worker.start()
        self.addCleanup(self.server.worker.join, 5)
        self.addCleanup(gate.set)
        snap = ms.query_state(self.state)
        self.assertTrue(snap['busy'] and snap['model'] is None)
        self.assertEqual(snap['target'], {'target': TARGET, 'endpoint': ENDPOINT, 'ssh_user': 'root', 'ssh_port': 22})  # binding survives busy
        code, fake, out, _ = self.repeat()
        self.assertEqual(code, 0)
        fake.assert_not_called()
        self.assertIn('管理页面: ' + self.entry.as_uri(), out)
        self.assertIn('正在执行任务', out)
        self.assertEqual(self.server.requests, {})

    def test_signed_state_target_is_public_only(self):
        snap = ms.query_state(self.state)
        self.assertEqual(snap['target'], {'target': TARGET, 'endpoint': ENDPOINT, 'ssh_user': 'root', 'ssh_port': 22})
        self.assertNotIn(SECRET, json.dumps(snap))

    def test_different_vps_is_refused_with_both_targets_and_no_link(self):
        self.resolve.return_value = dict(TRIPLE, endpoint='203.0.113.50')
        code, fake, out, err = self.repeat('--ssh', 'ops@other-host')
        self.assertEqual((code, out), (1, ''))
        fake.assert_not_called()
        for text in ('manager_active', TARGET, 'ops@other-host', '203.0.113.50', '新参数未应用', '不会替你关闭'):
            self.assertIn(text, err)
        self.assertIs(ms.query_state(self.state)['busy'], False)

    def test_alias_resolving_to_the_same_triple_is_the_same_vps(self):
        code, fake, out, err = self.repeat('--ssh', 'company-vps')  # different raw string, same endpoint/user/port
        self.assertEqual((code, err), (0, ''))
        fake.assert_not_called()
        self.assertIn(self.entry.as_uri(), out)

    def test_unresolvable_different_string_is_not_called_a_different_vps(self):
        self.resolve.side_effect = vps.host.Error('no ssh')
        code, _, out, err = self.repeat('--ssh', 'company-vps')
        self.assertEqual((code, out), (1, ''))
        self.assertIn('未确认同机', err)
        self.assertNotIn('不是同一台', err)

    def test_implicit_user_or_port_drift_and_explicit_port_are_refused(self):
        self.resolve.return_value = dict(TRIPLE, port=2222)  # ~/.ssh/config now says Port 2222
        code, _, out, err = self.repeat()
        self.assertEqual((code, out), (1, ''))
        self.assertIn('SSH 用户/端口', err)
        self.resolve.return_value = dict(TRIPLE, user='ops')
        code, _, out, err = self.repeat()
        self.assertEqual((code, out), (1, ''))
        self.assertIn('ops@', err)
        self.resolve.return_value = dict(TRIPLE, port=2200)
        code, _, out, err = self.repeat('--port', '2200')
        self.assertEqual((code, out), (1, ''))
        self.assertIn('2200', err)

    def test_explicit_route_identity_or_mihomo_is_never_claimed_applied(self):
        for argv in (['--ssh-proxy', 'socks5://127.0.0.1:1080'], ['--ssh-proxy', 'socks5://127.0.0.1:1080', '--independent-carrier'],
                     ['--identity', '/tmp/id'], ['--mihomo', '/tmp/mihomo']):
            with mock.patch.object(vps, 'resolve_mihomo', return_value='/abs/mihomo'):
                code, fake, out, err = self.repeat(*argv)
            self.assertEqual((code, out), (1, ''), argv)
            fake.assert_not_called()
            self.assertIn(argv[0], err)
            self.assertIn('不能声称已生效', err)

    def test_missing_or_foreign_page_file_is_rejected_without_a_dead_link(self):
        original = self.entry.read_bytes()
        self.entry.write_bytes(original.replace(self.server.bootstrap.hex().encode(), b'0' * 64))
        os.chmod(self.entry, 0o600)
        code, _, out, err = self.repeat()
        self.assertEqual((code, out), (1, ''))
        self.assertIn('缺失、损坏或不属于它', err)
        self.entry.unlink()
        code, _, out, err = self.repeat()
        self.assertEqual((code, out), (1, ''))
        self.assertIn('缺失、损坏或不属于它', err)
        self.assertFalse(self.entry.exists())  # not regenerated

    def test_wrong_key_fails_signature_check_and_sends_no_job_or_link(self):
        key = self.state / 'manage-service/master.key'
        key.write_bytes(b'\x01' * 32)  # the running server still holds the original master
        code, fake, out, err = self.repeat()
        self.assertEqual((code, out), (1, ''))
        fake.assert_not_called()
        self.assertIn('没有通过签名状态验证', err)
        self.assertEqual(self.server.requests, {})

    def test_config_error_surfaces_before_the_active_check(self):
        (self.repo / 'vps/config').write_text('VPS_SSH=bad value\nDOMAIN=example.com\n')
        code, _, out, err = self.repeat()
        self.assertEqual((code, out), (1, ''))
        self.assertIn('config', err)
        self.assertNotIn('config.txt', err)


class BindTarget(unittest.TestCase):
    def server(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        state = Path(temp.name) / 'state'
        state.mkdir(mode=0o700)
        manage_auth.ensure_state(state, Path(temp.name), port=free_port())
        server = manage.ManagementServer(vps, state, runtime_carrier={'ssh_proxy': None, 'independent': True})
        self.addCleanup(server.close)
        return server

    def test_absent_state_is_an_unbound_legacy_path_not_an_error(self):
        server = self.server()
        server.bind_target()
        self.assertIsNone(server.target)

    def test_corrupt_or_recordless_state_fails_instead_of_pretending_legacy(self):
        server = self.server()
        (server.state / 'state.json').write_text('{not json')
        os.chmod(server.state / 'state.json', 0o600)
        with self.assertRaises(vps.host.Error):
            server.bind_target()
        vps.host.atomic(server.state / 'state.json', json.dumps({'current': None}))
        with self.assertRaises(vps.host.Error):
            server.bind_target()
        write_binding(server.state)
        server.bind_target()
        self.assertEqual(server.target, {'target': TARGET, 'endpoint': ENDPOINT, 'ssh_user': 'root', 'ssh_port': 22})


class LegacyOwner(Live):
    """Managers that predate the signed `target` binding."""
    def setUp(self):
        super().setUp(bind=False)

    def test_old_foreground_idle_is_reused_only_when_signed_endpoint_matches_the_state_record(self):
        snap = ms.query_state(self.state)
        self.assertIsNone(snap['target'])
        self.assertEqual(snap['model']['snapshot']['endpoint'], ENDPOINT) if snap['model'] else None

    def test_old_foreground_busy_has_no_endpoint_and_gets_actionable_old_version_error(self):
        gate = threading.Event()
        job = {'job_id': 'a' * 32, 'request_id': 'b' * 32, 'state': 'running', 'started_at': time.time(),
               '_started': time.monotonic(), 'elapsed_s': 0, 'deadline_s': 1200, 'generation_before': None,
               'action': 'add', 'names': ['x']}
        self.server.jobs[job['job_id']] = job
        self.server.last_job = job['job_id']
        self.server.worker = threading.Thread(target=gate.wait)
        self.server.worker.start()
        self.addCleanup(self.server.worker.join, 5)
        self.addCleanup(gate.set)
        code, fake, out, err = self.repeat()
        self.assertEqual((code, out), (1, ''))
        fake.assert_not_called()
        self.assertIn('旧版前台管理器', err)
        self.assertIn('无法证明', err)

    def test_old_foreground_idle_with_matching_signed_endpoint_and_record_reuses(self):
        fake_model = {'snapshot': {'endpoint': ENDPOINT}, 'devices': []}
        with mock.patch.object(manage, 'machine_execute', return_value={'pending': {'present': False}, 'model': fake_model,
                                                                         'generation_after': None}):
            code, fake, out, err = self.repeat()
            self.assertEqual((code, err), (0, ''))
            fake.assert_not_called()
            self.assertIn('旧版管理器', out)
            self.assertIn(self.entry.as_uri(), out)
            write_binding(self.state, endpoint='203.0.113.9')  # record disagrees with the signed live endpoint
            code, _, out, err = self.repeat()
            self.assertEqual((code, out), (1, ''))
            self.assertIn('无法证明', err)

    def test_persistent_service_is_a_legacy_conflict_not_a_foreground_owner(self):
        self.server.runtime_carrier = None  # persistent-mode service reports service_mode=persistent
        code, fake, out, err = self.repeat()
        self.assertEqual(code, 1)
        fake.assert_not_called()
        self.assertIn('legacy_service_conflict', err)
        self.assertIn('没有“原终端”', err)
        self.assertNotIn(REUSED_TEXT, out)
        self.assertIn('旧版常驻服务页面', out)


class UnverifiedHolder(unittest.TestCase):
    """The lock is held but nothing signed answers: explicit diagnostic, no HTTP mutation, nothing killed/stolen/deleted."""
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = private_repo(self.root)
        self.state = self.root / 'state'
        self.state.mkdir(mode=0o700)
        self.port = free_port()
        manage_auth.ensure_state(self.state, self.repo, port=self.port)
        jobs = self.state / 'manage-jobs'
        jobs.mkdir(mode=0o700)
        self.lock = os.open(jobs / '.session.lock', os.O_CREAT | os.O_RDWR, 0o600)
        import fcntl
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(os.close, self.lock)
        patch = mock.patch.object(vps, 'MODULE', self.repo / 'vps')
        patch.start()
        self.addCleanup(patch.stop)

    def listener(self, handler):
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', self.port))
        sock.listen(4)
        seen = []

        def run():
            sock.settimeout(8)
            try:
                while True:
                    conn, _ = sock.accept()
                    conn.settimeout(1)
                    try:
                        seen.append(conn.recv(4096).split(b' ', 2)[:2])
                    except OSError:
                        pass
                    handler(conn)
            except OSError:
                pass
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(sock.close)
        return seen

    def check(self, phrase, seen=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(manage_cli.vps, 'main', return_value=0) as fake:
            code = manage_cli.main(['--state-dir', str(self.state)], out, err)
        self.assertEqual((code, out.getvalue()), (1, ''))
        fake.assert_not_called()
        self.assertIn(phrase, err.getvalue())
        self.assertIn('未终止进程、未接管锁、未删除文件', err.getvalue())
        self.assertTrue((self.state / 'manage-jobs/.session.lock').exists())
        if seen is not None:
            self.assertTrue(all(item[0] == b'POST' and item[1] == b'/api/hello' or item[0] == b'GET' for item in seen), seen)  # read-only protocol

    def test_nothing_listening(self):
        self.check('管理端口未监听')

    def test_unsigned_json_impostor_on_the_port(self):
        def junk(conn):
            conn.sendall(b'HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\n{}')
            conn.close()
        self.check('没有通过签名状态验证', self.listener(junk))

    def test_hung_listener_times_out(self):
        held = []
        self.check('可能正在连接/启动（原终端尚未 ready），请稍后重试', self.listener(held.append))
        for conn in held:
            conn.close()


class PendingBinding(unittest.TestCase):
    def test_pending_skip_refuses_a_different_configured_target_but_serves_the_original(self):
        with tempfile.TemporaryDirectory() as root:
            state = Path(root)
            write_binding(state)
            (state / 'pending.json').write_text('{}')
            args = manage_cli.parser().parse_args(['--ssh', 'ops@other-host'])
            with mock.patch.object(manage_cli.vps, 'main') as fake, self.assertRaises(manage_cli.host.Error) as caught:
                manage_cli.refresh(args, state, io.StringIO())
            fake.assert_not_called()
            self.assertIn(TARGET, str(caught.exception))
            self.assertIn('ops@other-host', str(caught.exception))
            err = io.StringIO()
            self.assertFalse(manage_cli.refresh(manage_cli.parser().parse_args(['--ssh', TARGET]), state, err))
            self.assertIn('显式恢复', err.getvalue())


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

    def test_help_names_config_not_config_txt(self):
        text = manage_cli.parser().format_help()
        self.assertIn('config', text)
        self.assertNotIn('config.txt', text)
        self.assertNotIn('config.txt', (LIB / 'manage_cli.py').read_text())

    def test_no_open_is_ignored_and_no_service_or_opener_code(self):
        self.assertTrue(manage_cli.parser().parse_args(['--no-open']).no_open)
        source = (LIB / 'manage_cli.py').read_text()
        for word in ('systemctl', 'xdg-open', 'subprocess'):
            self.assertNotIn(word, source)


if __name__ == '__main__':
    unittest.main()
