import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import manage_auth
import manage_service as ms


class FakeSystemctl:
    """Stub for ms.systemctl: records verbs, tracks active state, never touches real systemd."""
    def __init__(self):
        self.calls, self.active = [], False

    def __call__(self, *args):
        self.calls.append(args)
        out = ''
        if args[0] == 'is-active':
            out = 'active\n' if self.active else 'inactive\n'
        if args[0] in ('start', 'restart'):
            self.active = True
        if args[0] == 'stop':
            self.active = False
        return subprocess.CompletedProcess(args, 0, out, '')

    def verbs(self):
        return [c[0] for c in self.calls]


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state = self.root / 'state'
        self.state.mkdir(mode=0o700)
        self.units = self.root / 'units'
        self.fake = FakeSystemctl()
        for patch in (mock.patch.object(ms, 'systemctl', self.fake),
                      mock.patch.object(ms, 'unit_dir', lambda: self.units),
                      mock.patch.object(ms, 'MODULE', self.root / 'repo dir'),
                      mock.patch.object(ms, 'code_digest', lambda repo=None: 'd' * 64),
                      mock.patch.object(ms, 'linger', lambda: 'no')):
            patch.start()
            self.addCleanup(patch.stop)
        self.unit = self.units / ms.UNIT_NAME

    def run_cli(self, *argv):
        out = io.StringIO()
        with mock.patch('sys.stderr', io.StringIO()) as err:
            code = ms.main(['--state-dir', str(self.state), *argv], out)
        return code, out.getvalue(), err.getvalue()

    def master(self):
        return (manage_auth.service_dir(self.state) / 'master.key').read_bytes()


class Install(Base):
    def test_unit_is_absolute_quoted_notify_restart_and_secret_free(self):
        self.assertEqual(self.run_cli('install')[0], 0)
        text = self.unit.read_text()
        self.assertIn('Type=notify\nNotifyAccess=main\n', text)
        self.assertIn('Restart=always', text)
        self.assertIn('TimeoutStartSec=15', text)
        self.assertIn('TimeoutStopSec=30', text)
        self.assertIn('WantedBy=default.target', text)
        exec_line = [l for l in text.splitlines() if l.startswith('ExecStart=')][0]
        self.assertIn('"%s"' % sys.executable, exec_line)
        self.assertIn('"%s"' % ms.SCRIPT, exec_line)
        self.assertIn('"serve" "--state-dir" "%s"' % self.state, exec_line)
        self.assertIn('WorkingDirectory=%s' % (self.root / 'repo dir'), text)
        self.assertNotIn(self.master().hex(), text)
        self.assertEqual(stat.S_IMODE(self.unit.stat().st_mode), 0o600)
        for verb in ('daemon-reload', 'enable', 'start'):
            self.assertIn(verb, self.fake.verbs())
        self.assertNotIn(('enable', '--now'), self.fake.calls)
        self.assertEqual(stat.S_IMODE((self.state / 'manage-service').stat().st_mode), 0o700)

    def test_quote_escapes(self):
        self.assertEqual(ms.quote_exec('a b"c\\d%e$f'), '"a b\\"c\\\\d%%e$$f"')
        with self.assertRaises(ms.ServiceError):
            ms.quote_exec('bad\nline')

    def test_idempotent_preserves_master_and_does_not_restart_unchanged(self):
        self.run_cli('install')
        key, before = self.master(), self.unit.read_text()
        self.fake.calls.clear()
        self.assertEqual(self.run_cli('install')[0], 0)
        self.assertEqual((self.master(), self.unit.read_text()), (key, before))
        self.assertNotIn('daemon-reload', self.fake.verbs())
        self.assertNotIn('restart', self.fake.verbs())

    def test_changed_source_updates_unit_and_config_but_not_key(self):
        self.run_cli('install')
        key = self.master()
        self.fake.active = True
        with mock.patch.object(ms, 'MODULE', self.root / 'moved'), \
                mock.patch.object(ms, 'query_state', lambda s: {'busy': False}):
            self.assertEqual(self.run_cli('install')[0], 0)
        self.assertIn('WorkingDirectory=%s' % (self.root / 'moved'), self.unit.read_text())
        self.assertEqual(manage_auth.load_state(self.state)[0]['repo'], str(self.root / 'moved'))
        self.assertEqual(self.master(), key)
        self.assertIn('restart', self.fake.verbs())

    def test_changed_source_with_busy_job_refuses_restart(self):
        self.run_cli('install')
        self.fake.active = True
        with mock.patch.object(ms, 'MODULE', self.root / 'moved'), \
                mock.patch.object(ms, 'query_state', lambda s: {'busy': True}):
            code, _, err = self.run_cli('install')
        self.assertEqual(code, 1)
        self.assertNotIn('restart', self.fake.verbs())

    def test_foreign_symlink_and_mode_units_refused_untouched(self):
        self.units.mkdir()
        self.unit.write_text('[Service]\nExecStart=/bin/true\n')
        self.unit.chmod(0o600)
        code, _, err = self.run_cli('install')
        self.assertEqual(code, 1)
        self.assertIn('foreign', err)
        self.assertEqual(self.unit.read_text(), '[Service]\nExecStart=/bin/true\n')
        self.unit.unlink()
        target = self.root / 'target'
        target.write_text('x')
        self.unit.symlink_to(target)
        self.assertEqual(self.run_cli('install')[0], 1)
        self.assertEqual(target.read_text(), 'x')
        self.unit.unlink()
        self.unit.write_text(ms.render_unit(self.state))
        self.unit.chmod(0o666)
        self.assertEqual(self.run_cli('install')[0], 1)
        self.assertNotIn('enable', self.fake.verbs())

    def test_root_refused(self):
        with mock.patch.object(os, 'geteuid', lambda: 0):
            code, _, err = self.run_cli('install')
        self.assertEqual(code, 1)
        self.assertIn('root', err)
        self.assertEqual(self.fake.calls, [])

    def test_legacy_manager_flock_blocks_install_unilaterally(self):
        jobs = self.state / 'manage-jobs'
        jobs.mkdir()
        import fcntl
        fd = os.open(jobs / '.session.lock', os.O_CREAT | os.O_RDWR, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        code, _, err = self.run_cli('install')
        self.assertEqual(code, 1)
        self.assertIn('legacy', err)
        self.assertNotIn('start', self.fake.verbs())
        self.assertNotIn('stop', self.fake.verbs())


class SourceUpdate(unittest.TestCase):
    """Real code_digest over a private copy of vps/: updated code must restart the service via install."""
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.module = root / 'vps'
        import shutil
        shutil.copytree(Path(__file__).resolve().parents[1], self.module,
                        ignore=shutil.ignore_patterns('__pycache__', 'tests', 'index.html'))
        self.state = root / 'state'
        self.state.mkdir(mode=0o700)
        self.fake = FakeSystemctl()
        self.units = root / 'units'
        self.busy = False
        for patch in (mock.patch.object(ms, 'systemctl', self.fake), mock.patch.object(ms, 'unit_dir', lambda: self.units),
                      mock.patch.object(ms, 'MODULE', self.module), mock.patch.object(ms, 'linger', lambda: 'no'),
                      mock.patch.object(ms, 'query_state', lambda s: {'busy': self.busy})):
            patch.start()
            self.addCleanup(patch.stop)

    def install(self):
        self.fake.calls.clear()
        with mock.patch('sys.stderr', io.StringIO()) as err:
            return ms.main(['--state-dir', str(self.state), 'install'], io.StringIO()), err.getvalue()

    def test_digest_tracks_code_not_private_files(self):
        base = ms.code_digest(self.module)
        for name in ('index.html', 'config', 'subscription-urls.json'):
            (self.module / name).write_text('x')
        self.assertEqual(ms.code_digest(self.module), base)
        asset = self.module / 'assets/manage.js'
        asset.write_bytes(asset.read_bytes() + b' ')
        self.assertNotEqual(ms.code_digest(self.module), base)

    def test_missing_asset_is_an_error_not_a_masked_digest(self):
        (self.module / 'assets/manage.css').unlink()
        with self.assertRaises(OSError):
            ms.code_digest(self.module)

    def test_unchanged_install_idle_noop_changed_install_restarts_when_idle(self):
        self.assertEqual(self.install()[0], 0)
        key = (self.state / 'manage-service/master.key').read_bytes()
        self.fake.active = True
        self.assertEqual(self.install()[0], 0)
        self.assertNotIn('restart', self.fake.verbs())
        asset = self.module / 'assets/manage.js'
        asset.write_bytes(asset.read_bytes() + b' ')
        self.busy = True
        before = (self.units / ms.UNIT_NAME).read_text()
        code, _ = self.install()
        self.assertEqual(code, 1)  # active job: never auto-restarted
        self.assertNotIn('restart', self.fake.verbs())
        self.busy = False
        self.assertEqual(self.install()[0], 0)
        self.assertIn('restart', self.fake.verbs())
        self.assertNotEqual((self.units / ms.UNIT_NAME).read_text(), before)
        self.assertEqual((self.state / 'manage-service/master.key').read_bytes(), key)
        self.assertEqual(self.install()[0], 0)
        self.assertNotIn('restart', self.fake.verbs())


class Lifecycle(Base):
    def setUp(self):
        super().setUp()
        self.run_cli('install')
        self.fake.calls.clear()

    def test_stop_restart_rotate_refuse_when_busy_or_unknown(self):
        for busy in ({'busy': True}, {}, {'busy': None}):
            with mock.patch.object(ms, 'query_state', lambda s, b=busy: b):
                for cmd in ('stop', 'restart', 'rotate', 'uninstall'):
                    with self.subTest(busy=busy, cmd=cmd):
                        self.assertEqual(self.run_cli(cmd)[0], 1)
        key = self.master()
        self.assertEqual(self.master(), key)
        self.assertTrue(self.unit.exists())
        self.assertFalse({'stop', 'restart', 'disable'} & set(self.fake.verbs()))

    def test_unreachable_active_service_refuses(self):
        self.fake.active = True
        def down(state):
            raise ms.ServiceError('Service not reachable')
        with mock.patch.object(ms, 'query_state', down):
            self.assertEqual(self.run_cli('stop')[0], 1)
        self.assertTrue(self.fake.active)

    def test_idle_stop_restart_rotate_uninstall(self):
        self.fake.active = True
        key = self.master()
        with mock.patch.object(ms, 'query_state', lambda s: {'busy': False}):
            self.assertEqual(self.run_cli('restart')[0], 0)
            self.assertEqual(self.run_cli('rotate')[0], 0)
            self.assertNotEqual(self.master(), key)
            self.assertEqual(len(self.master()), 32)
            self.assertEqual(stat.S_IMODE((manage_auth.service_dir(self.state) / 'master.key').stat().st_mode), 0o600)
            self.assertEqual(self.run_cli('stop')[0], 0)
            self.assertEqual(self.run_cli('uninstall')[0], 0)
        self.assertFalse(self.unit.exists())
        self.assertTrue((manage_auth.service_dir(self.state) / 'master.key').exists())
        self.assertIn('disable', self.fake.verbs())

    def test_uninstall_refuses_foreign_unit(self):
        self.unit.write_text('[Service]\n')
        self.unit.chmod(0o600)
        self.assertEqual(self.run_cli('uninstall')[0], 1)
        self.assertTrue(self.unit.exists())

    def test_status_reports_safe_facts_only(self):
        self.fake.active = True
        def down(state):
            raise ms.ServiceError('Service not reachable: ConnectionRefusedError')
        with mock.patch.object(ms, 'query_state', down):
            code, out, _ = self.run_cli('status')
        self.assertEqual(code, 0)
        for text in ('active: active', 'port: 47831', 'carrier: none', 'linger: no', 'not reachable'):
            self.assertIn(text, out)
        self.assertNotIn(self.master().hex(), out)
        self.assertNotIn(manage_auth.bootstrap_key(self.master()).hex(), out)

    def test_status_warns_when_repo_moved(self):
        with mock.patch.object(ms, 'MODULE', self.root / 'elsewhere'), \
                mock.patch.object(ms, 'query_state', lambda s: {'busy': False, 'writable': False}):
            _, out, _ = self.run_cli('status')
        self.assertIn('run: manage.sh install', out)

    def test_status_not_installed(self):
        self.unit.unlink()
        self.assertEqual(self.run_cli('status')[0], 3)


class Carrier(Base):
    def setUp(self):
        super().setUp()
        self.run_cli('install')

    def path(self):
        return manage_auth.service_dir(self.state) / 'carrier.json'

    def test_requires_ack_and_safe_url_and_no_silent_fallback(self):
        self.assertEqual(self.run_cli('carrier', 'set', '--ssh-proxy', 'socks5://127.0.0.1:1080')[0], 1)
        self.assertFalse(self.path().exists())
        for bad in ('socks5://10.0.0.1:1080', 'http://user:p@127.0.0.1:80', 'socks5://127.0.0.1:99999', ''):
            self.assertEqual(self.run_cli('carrier', 'set', '--independent-carrier', '--ssh-proxy', bad)[0], 1, bad)
        self.assertFalse(self.path().exists())
        self.assertEqual(self.run_cli('carrier', 'set', '--independent-carrier')[0], 1)

    def test_set_and_clear(self):
        self.assertEqual(self.run_cli('carrier', 'set', '--independent-carrier', '--ssh-proxy', 'socks5://127.0.0.1:1080')[0], 0)
        self.assertEqual(json.loads(self.path().read_text()), {'schema': 1, 'ssh_proxy': 'socks5://127.0.0.1:1080', 'independent': True})
        self.assertEqual(stat.S_IMODE(self.path().stat().st_mode), 0o600)
        self.fake.calls.clear()
        self.assertEqual(self.run_cli('carrier', 'clear')[0], 0)
        self.assertFalse(self.path().exists())
        self.assertEqual(self.fake.calls, [])

    def test_direct_is_explicit_null_exclusive_and_needs_ack(self):
        self.assertEqual(self.run_cli('carrier', 'set', '--direct')[0], 1)
        self.assertFalse(self.path().exists())
        both = self.run_cli('carrier', 'set', '--direct', '--ssh-proxy', 'socks5://127.0.0.1:1080', '--independent-carrier')
        self.assertEqual(both[0], 1)
        self.assertFalse(self.path().exists())
        self.assertEqual(self.run_cli('carrier', 'set', '--independent-carrier')[0], 1)
        self.assertFalse(self.path().exists())
        self.assertEqual(self.run_cli('carrier', 'set', '--direct', '--independent-carrier')[0], 0)
        self.assertEqual(self.path().read_text(), '{"schema":1,"ssh_proxy":null,"independent":true}')
        self.assertIn('direct SSH configured (remote reachability NOT tested)', ms.carrier_summary(self.state))
        self.assertEqual(self.run_cli('carrier', 'clear')[0], 0)
        self.assertIn('none', ms.carrier_summary(self.state))

    def test_direct_flag_only_valid_for_carrier(self):
        self.assertEqual(self.run_cli('status', '--direct')[0], 1)

    def test_set_replaces_only_owned_safe_file(self):
        self.path().write_text('x')
        self.path().chmod(0o644)
        self.assertEqual(self.run_cli('carrier', 'set', '--independent-carrier', '--ssh-proxy', 'http://127.0.0.1:8080')[0], 1)
        self.assertEqual(self.path().read_text(), 'x')

    def test_usage_errors(self):
        self.assertEqual(self.run_cli('carrier')[0], 1)
        self.assertEqual(self.run_cli('status', '--ssh-proxy', 'socks5://127.0.0.1:1')[0], 1)


class Open(Base):
    def entry(self):
        (self.root / 'repo dir').mkdir()
        (self.root / 'repo dir/index.html').write_text('<html>')
        return self.root / 'repo dir/index.html'

    def run_default(self, *argv, which='/fake/xdg-open', run=None):
        run = run or mock.Mock(return_value=subprocess.CompletedProcess([], 0))
        real = subprocess.run
        with mock.patch.dict(os.environ, {'DISPLAY': ':fake', 'BROWSER': 'evil %s'}), \
                mock.patch.object(ms.shutil, 'which', return_value=which), \
                mock.patch.object(ms.subprocess, 'run', side_effect=lambda a, **k: run(a, **k) if a[0] == which else real(a, **k)):
            return self.run_cli(*argv), run

    def test_default_installs_then_opens_file_uri_once(self):
        entry = self.entry()
        (code, out, err), run = self.run_default()
        self.assertEqual(code, 0)
        self.assertTrue(self.unit.exists())
        run.assert_called_once()
        argv, kwargs = run.call_args
        self.assertEqual(argv[0], ['/fake/xdg-open', entry.as_uri()])
        self.assertFalse(kwargs.get('shell'))
        self.assertNotIn('BROWSER', kwargs['env'])
        self.assertIn('timeout', kwargs)
        self.assertNotIn(self.master().hex(), out + err)
        self.assertNotIn(manage_auth.bootstrap_key(self.master()).hex(), out + err)

    def test_no_open_never_calls_opener(self):
        self.entry()
        (code, _, _), run = self.run_default('--no-open')
        self.assertEqual(code, 0)
        run.assert_not_called()
        self.assertTrue(self.unit.exists())

    def test_opener_failures_are_clear_and_service_persists(self):
        entry = self.entry()
        for label, run in (('nonzero', mock.Mock(return_value=subprocess.CompletedProcess([], 3))),
                           ('timeout', mock.Mock(side_effect=subprocess.TimeoutExpired('x', 1))),
                           ('oserror', mock.Mock(side_effect=OSError('boom')))):
            with self.subTest(label):
                self.fake.calls.clear()
                (code, out, _), _ = self.run_default(run=run)
                self.assertEqual(code, 1)
                self.assertIn(str(entry), out)
                self.assertIn('keeps running', out)
                self.assertNotIn('stop', self.fake.verbs())
                self.assertTrue(self.unit.exists())

    def test_missing_opener_or_display(self):
        entry = self.entry()
        (code, out, _), _ = self.run_default(which=None)
        self.assertEqual(code, 1)
        self.assertIn('no xdg-open', out)
        with mock.patch.dict(os.environ, {}, clear=True):
            code, out, _ = self.run_cli('open')
        self.assertEqual(code, 1)
        self.assertIn('DISPLAY', out)
        self.assertIn(str(entry), out)

    def test_open_without_entry_errors(self):
        code, _, err = self.run_cli('open')
        self.assertEqual(code, 1)
        self.assertIn('not generated', err)


class Serve(Base):
    def test_serve_delegates_to_run_service(self):
        fake_manage, fake_vps = mock.Mock(), mock.Mock()
        fake_manage.run_service.return_value = 0
        with mock.patch.dict(sys.modules, {'manage': fake_manage, 'vps': fake_vps}):
            self.assertEqual(ms.main(['serve', '--state-dir', str(self.state)]), 0)
        fake_manage.run_service.assert_called_once_with(fake_vps, self.state)


class QueryClient(Base):
    """query_state against a tiny in-process signed server built on the real manage_auth primitives."""
    def test_rejects_wrong_proof(self):
        import http.server, threading
        manage_auth.ensure_state(self.state, self.root)
        port = manage_auth.load_state(self.state)[0]['port']

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_POST(self):
                n = json.loads(self.rfile.read(int(self.headers['Content-Length'])))['client_nonce']
                body = json.dumps({'version': 1, 'origin': 'http://127.0.0.1:%d' % port, 'epoch': '0' * 32, 'session_id': '1' * 32,
                                   'client_nonce': n, 'server_nonce': '2' * 32, 'proof': '3' * 64}).encode()
                self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        try:
            server = http.server.HTTPServer(('127.0.0.1', port), H)
        except OSError:
            self.skipTest('port busy')
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with self.assertRaises(ms.ServiceError):
                ms.query_state(self.state)
        finally:
            server.shutdown(); server.server_close()


if __name__ == '__main__':
    unittest.main()
