import argparse
import contextlib
import http.client
import io
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import manage
import vps

SETUP = Path(__file__).resolve().parents[1]


class ManageScript(unittest.TestCase):
    def test_manage_sh_is_executable_and_parses_from_any_cwd(self):
        self.assertTrue(os.access(SETUP / 'manage.sh', os.X_OK))
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run(['bash', str(SETUP / 'manage.sh'), '--help'], cwd=cwd,
                                    capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        for flag in ('--state-dir', '--ssh-proxy', '--mihomo', '--independent-carrier', '--no-open'):
            self.assertIn(flag, result.stdout)
        self.assertNotIn('--job', result.stdout)

    def test_setup_has_no_manage_and_points_to_manage_sh(self):
        helped = subprocess.run(['bash', str(SETUP / 'setup.sh'), '--help'], capture_output=True, text=True, timeout=20)
        self.assertNotIn('manage-job', helped.stdout)
        self.assertNotIn('session-scoped', helped.stdout)
        self.assertNotRegex(helped.stdout, r'[{,]manage[,}]')
        for command in ('manage', 'manage-job'):
            result = subprocess.run(['bash', str(SETUP / 'setup.sh'), command], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 2)
            self.assertIn('manage.sh', result.stderr)

    def test_parser_defaults_open(self):
        args = manage.parser().parse_args([])
        self.assertFalse(args.no_open)
        self.assertTrue(manage.parser().parse_args(['--no-open']).no_open)


class Launch(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name) / 'repo'
        self.module = root / 'vps'
        self.module.mkdir(parents=True)
        shutil.copytree(vps.MODULE / 'assets', self.module / 'assets')
        (root / '.gitignore').write_text('vps/index.html\nvps/.index-*.tmp\n')
        self.state = Path(self.temp.name) / 'state'
        self.state.mkdir(mode=0o700)
        env = {'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull}
        subprocess.run(['git', 'init', '-q', str(root)], check=True, env=dict(os.environ, **env))
        for patch in (mock.patch.object(vps, 'MODULE', self.module),
                      mock.patch.dict(os.environ, {'DISPLAY': ':fake'})):
            patch.start()
            self.addCleanup(patch.stop)
        self.index = self.module / 'index.html'

    def run_manager(self, opener, no_open=False, which='/fake/xdg-open'):
        """opener(argv, **kw) replaces subprocess.run inside manage; it then ends the session."""
        real_run = subprocess.run
        calls = []
        def fake(argv, **kwargs):
            if argv and argv[0] == which:
                calls.append(argv)
                try:
                    return opener(argv, **kwargs)
                finally:
                    os.kill(os.getpid(), signal.SIGTERM)
            return real_run(argv, **kwargs)
        args = argparse.Namespace(independent_carrier=False, ssh_proxy=None, mihomo=sys.executable, no_open=no_open)
        out = io.StringIO()
        # Bounded stop for EVERY path (a missing opener never reaches fake()'s SIGTERM).
        stopper = threading.Timer(2, os.kill, (os.getpid(), signal.SIGTERM))
        stopper.start()
        self.addCleanup(stopper.cancel)
        with mock.patch.object(manage.shutil, 'which', return_value=which), \
                mock.patch.object(manage.subprocess, 'run', side_effect=fake), \
                contextlib.redirect_stdout(out):
            try:
                code = manage.run(vps, self.state, args)
            finally:
                stopper.cancel()
                stopper.join()
        return code, calls, out.getvalue()

    def test_opener_gets_only_file_uri_after_service_ready(self):
        seen = {}
        def opener(argv, **kwargs):
            page = self.index.read_text()
            origin = re.search(r'Session origin', '') or None
            seen['page'] = page
            seen['kwargs'] = kwargs
            url = re.search(r'url=(http://127\.0\.0\.1:\d+)/#([0-9a-f]+)"', page)
            host, port = url.group(1)[7:].split(':')
            connection = http.client.HTTPConnection(host, int(port), timeout=5)
            connection.request('GET', '/')
            seen['status'] = connection.getresponse().status
            seen['token'] = url.group(2)
            seen['url'] = url.group(0)
            return subprocess.CompletedProcess(argv, 0)
        code, calls, out = self.run_manager(opener)
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], ['/fake/xdg-open', self.index.as_uri()])
        self.assertEqual(seen['status'], 200)
        self.assertFalse(seen['kwargs'].get('shell', False))
        self.assertNotIn('BROWSER', seen['kwargs']['env'])
        self.assertIn('timeout', seen['kwargs'])
        self.assertNotIn(seen['token'], ' '.join(calls[0]))
        self.assertNotIn(seen['token'], out)
        self.assertIn('http-equiv="refresh" content="0;url=' + seen['url'][4:], seen['page'])
        self.assertIn('href="' + seen['url'][4:-1] + '"', seen['page'])
        self.assertIn('no-referrer', seen['page'])
        self.assertEqual(self.index.read_bytes(), manage.STOPPED_ENTRY)

    def test_no_open_never_calls_opener(self):
        code, calls, out = self.run_manager(lambda *_a, **_k: self.fail('opener'), no_open=True)
        self.assertEqual((code, calls), (0, []))
        self.assertIn('--no-open', out)
        self.assertIn(str(self.index), out)

    def test_failures_report_manual_path_and_server_stays_until_shutdown(self):
        for label, behaviour in (('nonzero', lambda a, **k: subprocess.CompletedProcess(a, 3)),
                                 ('timeout', mock.Mock(side_effect=subprocess.TimeoutExpired('x', 1))),
                                 ('oserror', mock.Mock(side_effect=OSError('boom')))):
            with self.subTest(label):
                state = {}
                def opener(argv, **kwargs):
                    state['page_served'] = self.index.read_bytes().startswith(b'<!doctype html>') and b'refresh' in self.index.read_bytes()
                    return behaviour(argv, **kwargs)
                code, calls, out = self.run_manager(opener)
                self.assertEqual(code, 0)
                self.assertTrue(state['page_served'])
                self.assertIn('Open this file manually', out)
                self.assertIn(str(self.index), out)
                self.assertNotIn('#', out.replace('Open this', ''))  # no token fragment printed

    def test_missing_display_or_opener_is_explicit_and_server_runs(self):
        with mock.patch.dict(os.environ, {'BROWSER': 'evil %s'}):
            os.environ.pop('DISPLAY'); os.environ.pop('WAYLAND_DISPLAY', None)
            timer = threading.Timer(0.5, os.kill, (os.getpid(), signal.SIGTERM))
            timer.start()
            args = argparse.Namespace(independent_carrier=False, ssh_proxy=None, mihomo=sys.executable, no_open=False)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(manage.run(vps, self.state, args), 0)
        self.assertIn('no DISPLAY', out.getvalue())
        self.assertIn('manually', out.getvalue())
        code, calls, out = self.run_manager(lambda *_: None, which=None)
        self.assertEqual(calls, [])
        self.assertIn('no xdg-open', out)

    def test_old_known_stopped_entry_is_owned_artifact(self):
        self.assertIn(b'bash manage.sh', manage.STOPPED_ENTRY)


if __name__ == '__main__':
    unittest.main()
