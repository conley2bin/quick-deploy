"""Real ManagementServer / real `serve` subprocess against the signed protocol (temp state only)."""
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

LIB = Path(__file__).resolve().parents[1] / 'lib'
sys.path.insert(0, str(LIB))
import manage
import manage_auth
import manage_service as ms
import vps


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def private_repo(root):
    """A throwaway git worktree holding a copy of vps/ so the entry file never lands in the real repo."""
    repo = Path(root) / 'repo'
    source = Path(__file__).resolve().parents[1]
    shutil.copytree(source, repo / 'vps', ignore=shutil.ignore_patterns('__pycache__', 'tests', 'index.html'))
    (repo / '.gitignore').write_text('vps/index.html\nvps/.index-*.tmp\n')
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    subprocess.run(['git', 'init', '-q', str(repo)], check=True, env=env, timeout=20)
    return repo


class RealServerQuery(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state = Path(temp.name) / 'state'
        self.state.mkdir(mode=0o700)
        self.port = free_port()
        manage_auth.ensure_state(self.state, Path(temp.name), port=self.port)
        self.server = manage.ManagementServer(vps, self.state)
        self.assertEqual(self.server.httpd.server_port, self.port)
        self.server._serving = True
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(self.server.close)

    def test_signed_state_query_succeeds_with_boolean_busy(self):
        snapshot = ms.query_state(self.state)
        self.assertIs(snapshot['busy'], False)
        self.assertIn('writable', snapshot)
        # No deployed state fixture: the engine reports the invalid source through the signed API.
        self.assertIsNone(snapshot.get('model'))
        self.assertFalse(snapshot['writable'])

    def test_repeated_queries_use_fresh_sessions(self):
        for _ in range(3):
            self.assertIs(ms.query_state(self.state)['busy'], False)

    def test_status_command_reports_authenticated_service(self):
        import io
        out = io.StringIO()
        if True:
            # status only needs a unit to exist; reuse the real query path, stub just the unit lookup.
            from unittest import mock
            with mock.patch.object(ms, 'unit_dir', lambda: Path(self.state) / 'units'), \
                    mock.patch.object(ms, 'systemctl', lambda *a: subprocess.CompletedProcess(a, 0, 'active\n', '')), \
                    mock.patch.object(ms, 'linger', lambda: 'no'):
                (Path(self.state) / 'units').mkdir()
                unit = Path(self.state) / 'units' / ms.UNIT_NAME
                unit.write_text(ms.render_unit(self.state))
                unit.chmod(0o600)
                self.assertEqual(ms.status(self.state, out), 0)
        self.assertIn('reachable, authenticated; busy=False', out.getvalue())

    def test_rotated_key_is_rejected_by_old_server(self):
        (manage_auth.service_dir(self.state) / 'master.key').write_bytes(b'\x01' * 32)
        with self.assertRaises(ms.ServiceError) as caught:
            ms.query_state(self.state)
        self.assertIn('proof', str(caught.exception))


class ServeSubprocess(unittest.TestCase):
    def test_serve_notifies_ready_after_bind_and_stops_on_sigterm(self):
        with tempfile.TemporaryDirectory() as root:
            repo = private_repo(root)
            state = Path(root) / 'state'
            state.mkdir(mode=0o700)
            port = free_port()
            manage_auth.ensure_state(state, repo, port=port)
            master = (manage_auth.service_dir(state) / 'master.key').read_bytes()
            notify = Path(root) / 'n.sock'
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            self.addCleanup(sock.close)
            sock.bind(str(notify))
            sock.settimeout(15)
            env = {k: v for k, v in os.environ.items() if k != 'BROWSER'}
            env['NOTIFY_SOCKET'] = str(notify)
            argv = [sys.executable, '-B', str(repo / 'vps/lib/manage_service.py'), 'serve', '--state-dir', str(state)]
            proc = subprocess.Popen(argv, env=env, cwd=repo, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                try:
                    message = sock.recv(256)
                except socket.timeout:
                    proc.kill()
                    self.fail('no READY=1; stderr: %s' % proc.communicate(timeout=10)[1].decode()[:500])
                self.assertEqual(message, b'READY=1')
                self.assertIsNone(proc.poll())
                # Ready means answering: the very first signed query must work.
                self.assertIs(ms.query_state(state)['busy'], False)
                entry = repo / 'vps/index.html'
                self.assertTrue(entry.is_file())
                self.assertEqual(entry.stat().st_mode & 0o777, 0o600)
                self.assertIn(b'127.0.0.1:%d' % port, entry.read_bytes())
                self.assertNotIn(master.hex().encode(), entry.read_bytes())
            finally:
                proc.send_signal(signal.SIGTERM)
                try:
                    _, err = proc.communicate(timeout=20)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
                    self.fail('serve did not exit on SIGTERM')
            self.assertEqual(proc.returncode, 0, err.decode()[:500])
            # Restart preserves master key and port.
            self.assertEqual((manage_auth.service_dir(state) / 'master.key').read_bytes(), master)
            self.assertEqual(manage_auth.load_state(state)[0]['port'], port)

    def test_unit_execstart_command_is_a_real_serve_invocation(self):
        import shlex
        state = Path('/tmp/some state')
        line = [l for l in ms.render_unit(state).splitlines() if l.startswith('ExecStart=')][0][len('ExecStart='):]
        words = shlex.split(line)
        parsed = ms.parser().parse_args(words[3:])
        self.assertEqual((parsed.command, parsed.state_dir), ('serve', state))
        self.assertEqual(words[:3], [sys.executable, '-B', str(ms.SCRIPT)])


if __name__ == '__main__':
    unittest.main()
