"""Private auth state and signed-protocol primitives; temp states only."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import manage
import manage_auth


class AuthState(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name) / 'state'
        self.state.mkdir(mode=0o700)
        self.dir = self.state / 'manage-service'

    def tearDown(self):
        self.temp.cleanup()

    def test_created_once_with_private_modes_and_preserved(self):
        config = manage_auth.ensure_state(self.state, '/repo', 50000)
        self.assertEqual(config, {'schema': 1, 'port': 50000, 'repo': '/repo'})
        self.assertEqual(self.dir.stat().st_mode & 0o777, 0o700)
        for name in ('master.key', 'config.json'):
            self.assertEqual((self.dir / name).stat().st_mode & 0o777, 0o600)
        master = (self.dir / 'master.key').read_bytes()
        self.assertEqual(len(master), 32)
        self.assertEqual(manage_auth.ensure_state(self.state, '/other', 1234), config)
        self.assertEqual(manage_auth.load_state(self.state), (config, master))
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ['config.json', 'master.key'])
        self.assertNotIn(master.hex(), (self.dir / 'config.json').read_text())

    def test_default_port(self):
        self.assertEqual(manage_auth.ensure_state(self.state, '/repo')['port'], 47831)

    def test_unsafe_state_is_refused_not_repaired(self):
        manage_auth.ensure_state(self.state, '/repo')
        os.chmod(self.dir / 'master.key', 0o640)
        with self.assertRaises(manage_auth.AuthError):
            manage_auth.load_state(self.state)
        self.assertEqual((self.dir / 'master.key').stat().st_mode & 0o777, 0o640)
        os.chmod(self.dir / 'master.key', 0o600)
        os.chmod(self.dir, 0o755)
        with self.assertRaises(manage_auth.AuthError):
            manage_auth.ensure_state(self.state, '/repo')
        os.chmod(self.dir, 0o700)
        (self.dir / 'config.json').write_text(json.dumps({'schema': 1, 'port': 0, 'repo': '/r'}))
        os.chmod(self.dir / 'config.json', 0o600)
        with self.assertRaises(manage_auth.AuthError):
            manage_auth.load_state(self.state)

    def test_non_object_config_raises_autherror(self):
        manage_auth.ensure_state(self.state, '/repo')
        for text in ('[]', 'null', '7', '"x"', 'true', '\xff'):
            (self.dir / 'config.json').write_bytes(text.encode('latin-1'))
            os.chmod(self.dir / 'config.json', 0o600)
            with self.assertRaises(manage_auth.AuthError):
                manage_auth.load_state(self.state)
            with self.assertRaises(manage_auth.AuthError):
                manage_auth.ensure_state(self.state, '/repo')

    def test_symlinks_are_refused(self):
        elsewhere = Path(self.temp.name) / 'elsewhere'
        elsewhere.mkdir(mode=0o700)
        self.dir.symlink_to(elsewhere)
        with self.assertRaises(manage_auth.AuthError):
            manage_auth.ensure_state(self.state, '/repo')
        self.assertEqual(list(elsewhere.iterdir()), [])
        self.dir.unlink()
        manage_auth.ensure_state(self.state, '/repo')
        key = self.dir / 'master.key'
        real = Path(self.temp.name) / 'real.key'
        real.write_bytes(key.read_bytes())
        os.chmod(real, 0o600)
        key.unlink()
        key.symlink_to(real)
        with self.assertRaises(manage_auth.AuthError):
            manage_auth.load_state(self.state)

    def test_derivations_follow_contract(self):
        master = bytes(range(32))
        boot = manage_auth.bootstrap_key(master)
        self.assertEqual(boot, hmac.new(master, b'qd-manager-bootstrap-v1', hashlib.sha256).digest())
        ctx = '\n'.join(['1', 'http://127.0.0.1:1', 'e' * 32, 's' * 32, 'c' * 32, 'n' * 32])
        self.assertEqual(manage_auth.context('http://127.0.0.1:1', 'e' * 32, 's' * 32, 'c' * 32, 'n' * 32), ctx)
        self.assertEqual(manage_auth.server_proof(boot, ctx),
                         hmac.new(boot, ('qd-server-v1\n' + ctx).encode(), hashlib.sha256).hexdigest())
        ks = manage_auth.session_key(boot, ctx)
        self.assertEqual(ks, hmac.new(boot, ('qd-session-v1\n' + ctx).encode(), hashlib.sha256).digest())
        body = b'{"a":1}'
        self.assertEqual(manage_auth.request_mac(ks, 'E', 'N', 'POST', '/api/jobs', body),
                         hmac.new(ks, ('qd-request-v1\nE\nN\nPOST\n/api/jobs\n' + hashlib.sha256(body).hexdigest()).encode(),
                                  hashlib.sha256).hexdigest())
        self.assertEqual(manage_auth.response_mac(ks, 'E', 'N', 202, b''),
                         hmac.new(ks, ('qd-response-v1\nE\nN\n202\n' + hashlib.sha256(b'').hexdigest()).encode(),
                                  hashlib.sha256).hexdigest())

    def test_key_reuse_across_restart_rotation_changes_proofs(self):
        manage_auth.ensure_state(self.state, '/repo')
        first = manage_auth.bootstrap_key(manage_auth.load_state(self.state)[1])
        self.assertEqual(first, manage_auth.bootstrap_key(manage_auth.load_state(self.state)[1]))
        (self.dir / 'master.key').unlink()
        manage_auth.ensure_state(self.state, '/repo')
        self.assertNotEqual(first, manage_auth.bootstrap_key(manage_auth.load_state(self.state)[1]))


class SessionStore(unittest.TestCase):
    def test_replay_idle_and_bounds(self):
        now = [0.0]
        store = manage_auth.Sessions(clock=lambda: now[0])
        store.add('s', b'k')
        self.assertEqual(store.consume('s', 'n1'), 'ok')
        self.assertEqual(store.consume('s', 'n1'), 'replay')
        now[0] += manage_auth.SESSION_IDLE - 1
        self.assertIsNotNone(store.get('s'))
        store.consume('s', 'n2')
        now[0] += manage_auth.SESSION_IDLE + 1
        self.assertIsNone(store.get('s'))
        store.add('t', b'k')
        for i in range(manage_auth.MAX_NONCES):
            self.assertEqual(store.consume('t', str(i)), 'ok')
        self.assertEqual(store.consume('t', 'overflow'), 'replay')
        self.assertIsNone(store.get('t'))


class ServiceEntry(unittest.TestCase):
    def test_sd_notify_datagram(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, 'notify')
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
                server.bind(path)
                server.settimeout(2)
                with mock.patch.dict(os.environ, {'NOTIFY_SOCKET': path}):
                    manage.sd_notify('READY=1')
                self.assertEqual(server.recv(64), b'READY=1')
        with mock.patch.dict(os.environ, {}, clear=True):
            manage.sd_notify('READY=1')

    def test_cli_requires_exactly_one_internal_mode(self):
        self.assertEqual(manage.main([]), 2)
        self.assertEqual(manage.main(['--job', '--serve']), 2)


if __name__ == '__main__':
    unittest.main()
