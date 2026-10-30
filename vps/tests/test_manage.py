"""Loopback-only HTTP acceptance, with no remote/native process."""
import contextlib
import datetime
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import manage
import vps

A, B = 'a' * 64, 'b' * 64


class LauncherEntry(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name) / 'repo'
        self.module = root / 'vps'
        self.module.mkdir(parents=True)
        shutil.copytree(vps.MODULE / 'assets', self.module / 'assets')
        (root / '.gitignore').write_text('vps/index.html\nvps/.index-*.tmp\n')
        self.state = Path(self.temp.name) / 'state'
        self.state.mkdir(mode=0o700)
        env = {'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull}
        subprocess.run(['git', 'init', '-q', str(root)], check=True, env=dict(os.environ, **env))
        patch = mock.patch.object(vps, 'MODULE', self.module)
        patch.start()
        self.addCleanup(patch.stop)
        self.index = self.module / 'index.html'

    def tearDown(self):
        self.temp.cleanup()

    def start(self):
        manager = manage.ManagementServer(vps, self.state, independent_carrier=True,
                                          mihomo=sys.executable, executor=lambda _: {})
        try:
            manager.write_launcher()
        except BaseException:
            manager.close()
            raise
        return manager

    def test_close_replaces_own_entry_with_tokenless_stopped_page(self):
        manager = self.start()
        token = manager.launch_token
        self.assertIn(token.encode(), self.index.read_bytes())
        manager.close()
        data = self.index.read_bytes()
        self.assertEqual(data, manage.STOPPED_ENTRY)
        self.assertEqual(self.index.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(token.encode(), data)
        self.assertNotIn(b'http://', data)
        self.assertIn('bash setup.sh manage'.encode(), data)
        self.assertEqual(list(self.module.glob('.index-*')), [])

    def test_second_launch_overwrites_owned_stopped_entry(self):
        self.start().close()
        manager = self.start()
        try:
            self.assertIn(manager.launch_token.encode(), self.index.read_bytes())
        finally:
            manager.close()
        self.assertEqual(self.index.read_bytes(), manage.STOPPED_ENTRY)

    def test_foreign_or_changed_entry_is_preserved(self):
        self.index.write_text('mine')
        os.chmod(self.index, 0o600)
        with self.assertRaises(manage.Refusal):
            self.start()
        self.assertEqual(self.index.read_text(), 'mine')
        self.index.unlink()
        manager = self.start()
        self.index.write_bytes(b'user edited')
        manager.close()
        self.assertEqual(self.index.read_bytes(), b'user edited')


class HTTPManagement(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name)
        os.chmod(self.state, 0o700)
        self.source = {'schema': 1, 'outcome': 'unchanged', 'generation_after': A,
                       'pending': {'present': False}, 'model': {
                           'schema': 1, 'devices': [{'name': 'company'}, {'name': 'tablet'}], 'files': {}}}
        self.machine = mock.patch.object(manage, 'machine_execute', side_effect=lambda *_: dict(self.source))
        self.machine.start()
        self.remote = mock.patch.object(vps, 'remote', side_effect=AssertionError('remote forbidden'))
        self.remote_mock = self.remote.start()
        self.executed = []
        self.release = threading.Event()
        def execute(envelope):
            self.executed.append(envelope)
            self.release.wait(5)
            return dict(self.source, outcome='committed', generation_after=B)
        self.manager = manage.ManagementServer(vps, self.state, independent_carrier=True,
                                               mihomo=sys.executable, executor=execute)
        self.thread = threading.Thread(target=self.manager.serve_forever)
        self.thread.start()
        self.headers = {'Authorization': 'Bearer ' + self.manager.bearer,
                        'Origin': self.manager.origin, 'Content-Type': 'application/json'}

    def tearDown(self):
        self.release.set()
        self.manager.close()
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())
        self.remote_mock.assert_not_called()
        self.remote.stop()
        self.machine.stop()
        self.temp.cleanup()

    def request(self, method='GET', path='/api/state', body=None, headers=None, host=None, raw=None):
        connection = http.client.HTTPConnection(*self.manager.httpd.server_address, timeout=5)
        if raw is not None:
            connection.putrequest(method, path, skip_host=True)
            for key, value in raw:
                connection.putheader(key, value)
            connection.endheaders()
        else:
            chosen = dict(self.headers if headers is None else headers)
            if host is not None:
                chosen['Host'] = host
            payload = json.dumps(body).encode() if body is not None else None
            connection.request(method, path, body=payload, headers=chosen)
        response = connection.getresponse()
        data = response.read()
        content_type = response.getheader('Content-Type', '')
        result = (response.status, json.loads(data) if 'application/json' in content_type else data,
                  dict(response.getheaders()))
        connection.close()
        return result

    def body(self, request_id='1' * 32, **changes):
        return dict({'request_id': request_id, 'action': 'add', 'names': ['reader'],
                     'expected_generation': A}, **changes)

    def test_exact_loopback_security_and_no_remote(self):
        self.assertEqual(self.manager.httpd.server_address[0], '127.0.0.1')
        for host in ['localhost:' + str(self.manager.httpd.server_port), 'evil.example', '127.0.0.1']:
            self.assertEqual(self.request(host=host)[0], 421)
        self.assertEqual(self.request(raw=[('Host', self.manager.authority),
                                           ('Host', self.manager.authority)])[0], 421)
        self.assertEqual(self.request(headers=dict(self.headers, **{'X-Forwarded-Host': 'evil'}))[0], 421)
        for origin in ['https://evil.example', 'null', None]:
            headers = dict(self.headers)
            if origin is None:
                del headers['Origin']
            else:
                headers['Origin'] = origin
            self.assertEqual(self.request('POST', '/api/jobs', self.body(), headers)[0], 403)
        for headers in [{}, {'Authorization': 'Bearer wrong'}]:
            self.assertEqual(self.request(headers=headers)[0], 401)
        for kind in ['text/plain', 'application/x-www-form-urlencoded', 'application/json; charset=utf-8']:
            self.assertEqual(self.request('POST', '/api/jobs', self.body(),
                                         dict(self.headers, **{'Content-Type': kind}))[0], 415)
        self.assertEqual(self.request('OPTIONS', '/api/jobs')[0], 405)
        self.assertEqual(self.executed, [])

    def test_stylesheet_imports_resolve_to_served_assets(self):
        css = (vps.MODULE / 'assets' / 'manage.css').read_text()
        targets = re.findall(r'@import\s+url\(["\']?([^"\')]+)', css)
        self.assertIn('/dashboard.css', targets)
        for target in ['/manage.css'] + targets:
            code, body, headers = self.request(path=target, headers={})
            self.assertEqual(code, 200, target)
            self.assertTrue(headers['Content-Type'].startswith('text/css'))
            self.assertEqual(body, (vps.MODULE / 'assets' / target.lstrip('/')).read_bytes())

    def test_session_repeat_redemption_no_secrets_in_public_page(self):
        for _ in range(2):
            code, body, headers = self.request('POST', '/api/session',
                                              {'launch_token': self.manager.launch_token},
                                              {key: value for key, value in self.headers.items()
                                               if key != 'Authorization'})
            self.assertEqual(code, 200)
            self.assertEqual(body['token'], self.manager.bearer)
            self.assertGreater(body['expires_in'], 0)
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
            self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertEqual(self.request('POST', '/api/session', {'launch_token': 'wrong'})[0], 401)
        self.manager.expires = time.monotonic() - 1
        self.assertEqual(self.request()[0], 401)

    def test_strict_schema_confirmation_and_generation_admission(self):
        for body in [self.body(extra='x'), self.body(names=['bad name']),
                     self.body(action='reset', names=['tablet']), self.body(action='shell'),
                     self.body(action='rename', names=['tablet', 'tablet'])]:
            self.assertEqual(self.request('POST', '/api/jobs', body)[0], 400)
        self.assertEqual(self.request('POST', '/api/jobs', self.body(expected_generation=B))[0], 409)
        self.manager.independent_carrier = False
        self.assertEqual(self.request('POST', '/api/jobs', self.body())[0], 409)
        self.manager.independent_carrier = True
        self.manager.expires = time.monotonic() + manage.JOB_SECONDS + 1
        self.assertEqual(self.request('POST', '/api/jobs', self.body())[0], 409)
        self.assertEqual(self.executed, [])

    def test_pending_last_device_and_explicit_recover(self):
        self.source['model']['devices'] = [{'name': 'company'}]
        body = self.body(action='remove', names=['company'], confirm={'action': 'remove', 'name': 'company'})
        self.assertEqual(self.request('POST', '/api/jobs', body)[0], 409)
        self.source['pending'] = {'present': True, 'mode': 'devices', 'operation_id': 'c' * 32}
        self.assertEqual(self.request('POST', '/api/jobs', self.body())[0], 409)
        self.assertIsNone(self.request()[1]['model'])
        self.assertEqual(self.request('POST', '/api/recover', {
            'request_id': '2' * 32, 'expected_operation_id': 'd' * 32})[0], 409)
        self.assertEqual(self.executed, [])
        self.assertEqual(self.request('POST', '/api/recover', {
            'request_id': '2' * 32, 'expected_operation_id': 'c' * 32})[0], 202)

    def test_duplicates_conflicts_busy_and_terminal_immutable(self):
        code, first, _ = self.request('POST', '/api/jobs', self.body())
        self.assertEqual(code, 202)
        self.assertEqual(self.request('POST', '/api/jobs', self.body())[1]['job_id'], first['job_id'])
        self.assertEqual(self.request('POST', '/api/jobs', self.body(names=['other']))[0], 409)
        self.assertEqual(self.request('POST', '/api/jobs', self.body(request_id='2' * 32))[0], 409)
        self.assertTrue(self.request()[1]['busy'])
        self.release.set()
        self.manager.worker.join(5)
        job_path = '/api/jobs/' + first['job_id']
        result = self.request(path=job_path)[1]
        self.assertEqual(result['outcome'], 'committed')
        self.assertEqual(self.request(path=job_path)[1], result)
        self.assertEqual(len(self.executed), 1)
        saved = self.state / 'manage-jobs' / (first['job_id'] + '.json')
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(self.manager.bearer, saved.read_text())

    def test_job_descriptor_persists_and_legacy_jobs_stay_unlabelled(self):
        cases = [(dict(), 'add', ['reader']),
                 (dict(action='rename', names=['tablet', 'phone']), 'rename', ['tablet', 'phone']),
                 (dict(action='reset', names=['tablet'], confirm={'action': 'reset', 'name': 'tablet'}),
                  'reset', ['tablet']),
                 (dict(action='remove', names=['tablet'], confirm={'action': 'remove', 'name': 'tablet'}),
                  'remove', ['tablet'])]
        for index, (changes, action, names) in enumerate(cases):
            request_id = '%032x' % (index + 10)
            code, job, _ = self.request('POST', '/api/jobs', self.body(request_id, **changes))
            self.assertEqual(code, 202)
            self.release.set()
            self.manager.worker.join(5)
            public = self.request(path='/api/jobs/' + job['job_id'])[1]
            self.assertEqual((public['action'], public['names']), (action, names))
            saved = json.loads((self.state / 'manage-jobs' / (job['job_id'] + '.json')).read_text())
            self.assertEqual((saved['job']['action'], saved['job']['names']), (action, names))
            self.assertFalse(set(public) & {'_started', 'launch_token', 'token'})
            self.assertEqual(self.request()[1]['last_job']['names'], names)
        self.release.clear()
        self.assertEqual(self.request('POST', '/api/recover', {})[0], 400)
        self.source['pending'] = {'present': True, 'mode': 'devices', 'operation_id': 'c' * 32}
        code, job, _ = self.request('POST', '/api/recover', {
            'request_id': '2' * 32, 'expected_operation_id': 'c' * 32})
        self.assertEqual(code, 202)
        recovered = self.request(path='/api/jobs/' + job['job_id'])[1]
        self.assertEqual((recovered['action'], recovered['names']), ('recover', []))
        self.release.set()
        self.manager.worker.join(5)
        self.manager.close()
        self.thread.join(5)
        second = manage.ManagementServer(vps, self.state, independent_carrier=True,
                                         mihomo=sys.executable, executor=self.manager.executor)
        try:
            self.assertEqual(sorted(tuple(v['names']) for v in second.jobs.values() if 'names' in v),
                             [(), ('reader',), ('tablet',), ('tablet',), ('tablet', 'phone')])
        finally:
            second.close()

    def test_legacy_job_without_descriptor_loads_unchanged(self):
        legacy = {'job_id': '3' * 32, 'request_id': '4' * 32, 'state': 'finished', 'outcome': 'committed',
                  'generation_before': A, 'started_at': 1.0, 'deadline_s': 1200}
        vps.host.atomic(self.state / 'manage-jobs' / ('3' * 32 + '.json'), json.dumps({
            'schema': 1, 'fingerprint': 'f' * 64, 'unknown': False, 'job': legacy}))
        self.manager.close()
        self.thread.join(5)
        second = manage.ManagementServer(vps, self.state, independent_carrier=True,
                                         mihomo=sys.executable, executor=self.manager.executor)
        try:
            self.assertEqual(second.public_job(second.jobs['3' * 32]), legacy)
        finally:
            second.close()

    def test_optional_timing_fields_are_absolute_and_additive(self):
        state = self.request()[1]
        self.assertEqual((state['job_limit_s'], state['cleanup_margin_s']),
                         (manage.JOB_SECONDS, manage.CLEANUP_MARGIN))
        start = datetime.datetime.strptime(state['new_jobs_until'], '%Y-%m-%dT%H:%M:%SZ')
        end = datetime.datetime.strptime(state['session_ends_at'], '%Y-%m-%dT%H:%M:%SZ')
        self.assertAlmostEqual((end - start).total_seconds(), manage.JOB_SECONDS + manage.CLEANUP_MARGIN, delta=1)

    def test_restart_history_unknown_does_not_poison_current_state(self):
        job = {'job_id': '3' * 32, 'request_id': '4' * 32, 'state': 'running',
               'generation_before': A, 'started_at': time.time(), '_started': time.monotonic(),
               'deadline_s': 1200}
        body = manage.validate_request(self.body(request_id='4' * 32), False)
        fingerprint = hashlib.sha256(json.dumps(
            body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        self.manager._persist(job, fingerprint)
        self.manager.close()
        self.thread.join(5)
        second = manage.ManagementServer(vps, self.state, independent_carrier=True,
                                         mihomo=sys.executable, executor=self.manager.executor)
        try:
            self.assertTrue(second.unknown)
            self.assertEqual(second.jobs['3' * 32]['outcome'], 'recovery_required')
            current = second.snapshot()
            self.assertTrue(current['writable'])
            self.assertIsNotNone(current['model'])
            duplicate = second.admit(self.body(request_id='4' * 32))
            self.assertEqual(duplicate['job_id'], '3' * 32)
            self.assertIsNone(second.worker)
        finally:
            second.close()

    def test_second_manager_is_refused_without_relabeling_active_job(self):
        job = {'job_id': '6' * 32, 'request_id': '7' * 32, 'state': 'running',
               'generation_before': A, 'started_at': time.time(), '_started': time.monotonic(),
               'deadline_s': 1200}
        self.manager._persist(job, 'fingerprint')
        with self.assertRaises(manage.Refusal) as caught:
            manage.ManagementServer(vps, self.state, independent_carrier=True,
                                    mihomo=sys.executable)
        self.assertEqual(caught.exception.code, 'manager_active')
        saved = vps.load(self.state / 'manage-jobs' / ('6' * 32 + '.json'))
        self.assertEqual(saved['job']['state'], 'running')

    def test_processgroup_cancel_is_unknown_and_reaped(self):
        self.manager.executor = None
        self.manager.stopping.set()
        real_popen = subprocess.Popen
        processes = []
        def fake_machine(argv, **kwargs):
            self.assertEqual(argv[-1], 'manage-job')
            self.assertNotIn(self.manager.bearer, ' '.join(argv))
            process = real_popen([sys.executable, '-c',
                                  'import sys,time;sys.stdin.buffer.read();time.sleep(60)'], **kwargs)
            processes.append(process)
            return process
        job = {'job_id': '5' * 32, '_started': time.monotonic()}
        with mock.patch.object(manage.subprocess, 'Popen', side_effect=fake_machine):
            result = self.manager._execute(job, {'action': 'state'})
        self.assertEqual(result['outcome'], 'recovery_required')
        self.assertTrue(result['_unknown'])
        self.assertIsNotNone(processes[0].poll())
        self.manager.process = None

    def test_machine_result_with_credentials_is_deleted_after_parse(self):
        self.manager.executor = None
        sentinel = 'PRIVATE-CREDENTIAL-SENTINEL'
        real_popen = subprocess.Popen
        payload = json.dumps({'schema': 1, 'outcome': 'unchanged', 'model': {'secret': sentinel}})
        def fake_machine(argv, **kwargs):
            return real_popen([sys.executable, '-c', 'import sys;sys.stdin.buffer.read();print(sys.argv[1])',
                               payload], **kwargs)
        job = {'job_id': '8' * 32, '_started': time.monotonic()}
        with mock.patch.object(manage.subprocess, 'Popen', side_effect=fake_machine):
            result = self.manager._execute(job, {'action': 'state'})
        self.assertEqual(result['model']['secret'], sentinel)
        for path in (self.state / 'manage-jobs').iterdir():
            if path.is_file():
                self.assertNotIn(sentinel, path.read_text(errors='ignore'))


if __name__ == '__main__':
    unittest.main()
