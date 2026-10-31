"""Loopback-only HTTP acceptance, with no remote/native process."""
import contextlib
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import manage
import manage_auth
import vps

A, B = 'a' * 64, 'b' * 64


def stub_renderer(assets, origin, bootstrap_hex):
    return ('<!doctype html><p>%s %s</p>' % (origin, bootstrap_hex)).encode()


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
        for patch in (mock.patch.object(vps, 'MODULE', self.module),
                      mock.patch.dict(sys.modules, {'manage_entry': mock.Mock(render_entry=stub_renderer)})):
            patch.start()
            self.addCleanup(patch.stop)
        self.index = self.module / 'index.html'

    def tearDown(self):
        self.temp.cleanup()

    def start(self):
        manager = manage.ManagementServer(vps, self.state, port=0, executor=lambda _: {})
        try:
            manager.write_launcher()
        except BaseException:
            manager.close()
            raise
        return manager

    def test_entry_has_origin_and_bootstrap_only_and_survives_close(self):
        manager = self.start()
        data = self.index.read_bytes()
        self.assertIn(manager.origin.encode(), data)
        self.assertIn(manager.bootstrap.hex().encode(), data)
        self.assertNotIn(manage_auth.load_state(self.state)[1].hex().encode(), data)
        self.assertEqual(self.index.stat().st_mode & 0o777, 0o600)
        manager.close()
        self.assertEqual(self.index.read_bytes(), data)
        self.assertEqual(list(self.module.glob('.index-*')), [])

    def test_restart_regenerates_owned_entry_with_same_key_new_epoch(self):
        first = self.start()
        key, epoch = first.bootstrap, first.epoch
        first.close()
        second = self.start()
        try:
            self.assertEqual(second.bootstrap, key)
            self.assertNotEqual(second.epoch, epoch)
            self.assertIn(second.origin.encode(), self.index.read_bytes())
        finally:
            second.close()

    def test_missing_renderer_and_foreign_entry_are_refused(self):
        with mock.patch.dict(sys.modules, {'manage_entry': None}):
            with self.assertRaises(manage.Refusal) as caught:
                self.start()
        self.assertEqual(caught.exception.code, 'entry_renderer_unavailable')
        self.index.write_text('mine')
        os.chmod(self.index, 0o600)
        with self.assertRaises(manage.Refusal):
            self.start()
        self.assertEqual(self.index.read_text(), 'mine')


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
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(128)
        self.write_carrier({'schema': 1, 'ssh_proxy': 'socks5://127.0.0.1:%d' % self.listener.getsockname()[1],
                            'independent': True})
        self.manager = manage.ManagementServer(vps, self.state, port=0, executor=execute)
        self.thread = threading.Thread(target=self.manager.serve_forever)
        self.thread.start()
        self.headers = {'Origin': 'null', 'Content-Type': 'application/json'}
        self.session = None

    def write_carrier(self, value):
        directory = self.state / 'manage-service'
        directory.mkdir(mode=0o700, exist_ok=True)
        vps.host.atomic(directory / 'carrier.json', json.dumps(value))

    def tearDown(self):
        self.release.set()
        self.manager.close()
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())
        self.listener.close()
        self.remote_mock.assert_not_called()
        self.remote.stop()
        self.machine.stop()
        self.temp.cleanup()

    def raw_request(self, method, path, payload=None, headers=None, host=None, raw=None):
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
            connection.request(method, path, body=payload, headers=chosen)
        response = connection.getresponse()
        data = response.read()
        result = (response.status, data, dict(response.getheaders()))
        connection.close()
        return result

    def hello(self, key=None):
        nonce = secrets.token_hex(16)
        code, data, headers = self.raw_request('POST', '/api/hello', json.dumps(
            {'version': 1, 'client_nonce': nonce}).encode())
        self.assertEqual(code, 200)
        value = json.loads(data)
        ctx = manage_auth.context(value['origin'], value['epoch'], value['session_id'], nonce,
                                  value['server_nonce'])
        key = key or self.manager.bootstrap
        self.assertEqual(value['origin'], self.manager.origin)
        self.assertTrue(hmac.compare_digest(value['proof'], manage_auth.server_proof(key, ctx)))
        return value['session_id'], manage_auth.session_key(key, ctx), value['epoch']

    def request(self, method='GET', path='/api/state', body=None, headers=None, host=None, raw=None,
                hello=None, nonce=None, tamper=False, verify=True):
        """Signed request; returns (status, json, headers). Response MAC is verified when present."""
        if hello is None:
            hello = self.session = self.session or self.hello()
        session, ks, epoch = hello
        payload = json.dumps(body).encode() if body is not None else b''
        nonce = nonce or secrets.token_hex(16)
        mac = manage_auth.request_mac(ks, epoch, nonce, method, path, payload)
        chosen = dict(self.headers if headers is None else headers)
        chosen.update({'X-QD-Session': session, 'X-QD-Nonce': nonce,
                       'X-QD-MAC': ('0' * 64 if tamper else mac)})
        code, data, response = self.raw_request(method, path, payload or None, chosen, host, raw)
        if verify and 'X-QD-MAC' in response:
            self.assertEqual(response['X-QD-MAC'], manage_auth.response_mac(ks, epoch, nonce, code, data))
        return code, (json.loads(data) if data and response.get('Content-Type', '').startswith(
            'application/json') else data), response

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
        body = self.body()
        for origin in ['https://evil.example', 'http://' + self.manager.authority, None]:
            headers = dict(self.headers)
            if origin is None:
                del headers['Origin']
            else:
                headers['Origin'] = origin
            self.assertEqual(self.request('POST', '/api/jobs', body, headers)[0], 403)
            self.assertEqual(self.request(headers=headers)[0], 403)
        for kind in ['text/plain', 'application/x-www-form-urlencoded', 'application/json; charset=utf-8']:
            self.assertEqual(self.request('POST', '/api/jobs', body,
                                         dict(self.headers, **{'Content-Type': kind}))[0], 415)
        self.assertEqual(self.executed, [])

    def test_cors_is_null_origin_only_and_exposes_mac(self):
        code, _, headers = self.raw_request('OPTIONS', '/api/jobs')
        self.assertEqual(code, 204)
        self.assertEqual(headers['Access-Control-Allow-Origin'], 'null')
        self.assertEqual(headers['Access-Control-Allow-Methods'], 'GET, POST, OPTIONS')
        for name in ['Content-Type', 'X-QD-Session', 'X-QD-Nonce', 'X-QD-MAC']:
            self.assertIn(name, headers['Access-Control-Allow-Headers'])
        self.assertNotIn('Access-Control-Allow-Credentials', headers)
        self.assertEqual(self.raw_request('OPTIONS', '/api/jobs', headers={'Origin': 'https://evil.example'})[0], 403)
        code, _, headers = self.request()
        self.assertEqual(headers['Access-Control-Expose-Headers'], 'X-QD-MAC')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        for name in headers:
            self.assertNotIn(name.lower(), {'set-cookie'})

    def test_root_is_plain_health_and_serves_no_assets(self):
        code, data, _ = self.raw_request('GET', '/', headers={})
        self.assertEqual(code, 200)
        self.assertNotIn(self.manager.bootstrap.hex().encode(), data)
        self.assertNotIn(b'<script', data)
        for path in ['/manage.js', '/manage.html', '/manage.css', '/dashboard.css']:
            self.assertEqual(self.raw_request('GET', path, headers={})[0], 404)

    def test_hello_proof_context_and_per_hello_keys(self):
        first, second = self.hello(), self.hello()
        self.assertNotEqual(first[0], second[0])
        self.assertNotEqual(first[1], second[1])
        self.assertEqual(first[2], self.manager.epoch)
        wrong = secrets.token_bytes(32)
        with self.assertRaises(AssertionError):
            self.hello(wrong)  # client holding another key rejects the proof
        for bad in [b'{}', b'{"version":2,"client_nonce":"%s"}' % (b'a' * 32),
                    b'{"version":1,"client_nonce":"short"}', b'{"version":true,"client_nonce":"%s"}' % (b'a' * 32),
                    b'{"version":1,"client_nonce":"%s","x":1}' % (b'a' * 32)]:
            self.assertEqual(self.raw_request('POST', '/api/hello', bad)[0], 400)
        self.assertEqual(self.raw_request('POST', '/api/hello', b'{}', {'Origin': 'x', 'Content-Type': 'application/json'})[0], 403)

    def test_signed_auth_rejects_bad_mac_unknown_session_headers_and_replay(self):
        self.assertEqual(self.request(tamper=True)[0], 401)
        self.assertEqual(self.request(hello=('f' * 32, b'k' * 32, self.manager.epoch))[0], 401)
        self.assertEqual(self.raw_request('GET', '/api/state')[0], 401)
        self.assertEqual(self.raw_request('GET', '/api/state', headers=dict(
            self.headers, **{'X-QD-Session': 'x', 'X-QD-Nonce': 'y', 'X-QD-MAC': 'z'}))[0], 401)
        nonce = 'd' * 32
        self.assertEqual(self.request(nonce=nonce)[0], 200)
        code, data, headers = self.request(nonce=nonce, verify=False)
        self.assertEqual((code, data['error']['code']), (401, 'replay'))
        self.assertNotIn('X-QD-MAC', headers)
        # MAC binds method, exact path, body, epoch: wrong-path or wrong-body signature fails.
        session, ks, epoch = self.session
        sig = manage_auth.request_mac(ks, epoch, 'e' * 32, 'GET', '/api/state?x=1', b'')
        headers = dict(self.headers, **{'X-QD-Session': session, 'X-QD-Nonce': 'e' * 32, 'X-QD-MAC': sig})
        self.assertEqual(self.raw_request('GET', '/api/state', headers=headers)[0], 401)
        self.assertEqual(self.raw_request('GET', '/api/state?x=1', headers=headers)[0], 404)
        self.assertEqual(self.executed, [])

    def test_response_mac_covers_known_session_errors_and_detects_forgery(self):
        code, data, headers = self.request('POST', '/api/jobs', {'bad': 1})
        self.assertEqual(code, 400)
        session, ks, epoch = self.session
        self.assertEqual(len(headers['X-QD-MAC']), 64)
        # request() already asserted the MAC matches; a forged body must not.
        forged = manage_auth.response_mac(ks, epoch, 'a' * 32, code, b'{}')
        self.assertNotEqual(forged, headers['X-QD-MAC'])
        code, data, headers = self.request(path='/api/jobs/' + '9' * 32)
        self.assertEqual(code, 404)
        self.assertIn('X-QD-MAC', headers)

    def test_new_epoch_per_start_and_old_session_unknown(self):
        old_epoch, old_session = self.manager.epoch, self.request() and self.session
        self.manager.close()
        self.thread.join(5)
        second = manage.ManagementServer(vps, self.state, port=0, executor=self.manager.executor)
        try:
            self.assertNotEqual(second.epoch, old_epoch)
            self.assertIsNone(second.sessions.get(old_session[0]))
        finally:
            second.close()

    def test_no_session_deadline_fields(self):
        state = self.request()[1]
        self.assertEqual(state['service_mode'], 'persistent')
        for key in ('session_ends_at', 'new_jobs_until'):
            self.assertNotIn(key, state)
        self.assertTrue(state['writable'])

    def test_idle_session_expires_after_ttl_and_lru_is_bounded(self):
        self.request()
        self.manager.sessions.items[self.session[0]]['last'] -= manage_auth.SESSION_IDLE + 1
        code, data, _ = self.request(verify=False)
        self.assertEqual((code, data['error']['code']), (401, 'unknown_session'))
        for _ in range(manage_auth.MAX_SESSIONS + 3):
            self.manager.sessions.add(secrets.token_hex(16), b'k' * 32)
        self.assertEqual(len(self.manager.sessions.items), manage_auth.MAX_SESSIONS)

    def test_strict_schema_confirmation_and_generation_admission(self):
        for body in [self.body(extra='x'), self.body(names=['bad name']),
                     self.body(action='reset', names=['tablet']), self.body(action='shell'),
                     self.body(action='rename', names=['tablet', 'tablet'])]:
            self.assertEqual(self.request('POST', '/api/jobs', body)[0], 400)
        self.assertEqual(self.request('POST', '/api/jobs', self.body(expected_generation=B))[0], 409)
        self.listener.close()
        code, data, _ = self.request('POST', '/api/jobs', self.body())
        self.assertEqual((code, data['error']['code']), (409, 'carrier_listener_down'))
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
        self.assertNotIn(self.manager.bootstrap.hex(), saved.read_text())

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
            self.assertFalse(set(public) & {'_started', 'token'})
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
        second = manage.ManagementServer(vps, self.state, port=0, executor=self.manager.executor)
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
        second = manage.ManagementServer(vps, self.state, port=0, executor=self.manager.executor)
        try:
            self.assertEqual(second.public_job(second.jobs['3' * 32]), legacy)
        finally:
            second.close()

    def test_carrier_states_and_frozen_route(self):
        state = self.request()[1]
        self.assertEqual(state['carrier'], {'configured': True, 'listening': True, 'mode': 'proxy'})
        self.assertTrue(state['writable'])
        port = self.listener.getsockname()[1]
        code, job, _ = self.request('POST', '/api/jobs', self.body())
        self.assertEqual(code, 202)
        self.manager.worker.join(0.2)
        self.assertEqual(self.executed[0]['ssh_proxy'], 'socks5://127.0.0.1:%d' % port)
        self.assertIsNone(self.executed[0]['mihomo'])
        self.release.set()
        self.manager.worker.join(5)
        os.unlink(self.state / 'manage-service' / 'carrier.json')
        state = self.request()[1]
        self.assertEqual((state['writable'], state['writable_reason']), (False, 'carrier_not_configured'))
        self.assertEqual(state['carrier'], {'configured': False, 'listening': False, 'mode': None})
        self.assertIsNotNone(state['model'])
        code, data, _ = self.request('POST', '/api/jobs', self.body(request_id='9' * 32))
        self.assertEqual((code, data['error']['code']), (409, 'carrier_not_configured'))
        carrier = self.state / 'manage-service' / 'carrier.json'
        port_url = 'socks5://127.0.0.1:%d' % port
        bad = [b'{not json', json.dumps({'schema': 2, 'ssh_proxy': port_url, 'independent': True}).encode(),
               json.dumps({'schema': 1, 'ssh_proxy': 'socks5://example.com:1', 'independent': True}).encode(),
               json.dumps({'schema': 1, 'ssh_proxy': port_url, 'independent': False}).encode(),
               json.dumps({'schema': 1, 'ssh_proxy': port_url, 'independent': True, 'x': 1}).encode()]
        for index, data in enumerate(bad + [None]):
            if data is None:  # present but unsafe mode
                data = bad[0]
            vps.host.atomic(carrier, data, 0o600 if index < len(bad) else 0o644)
            state = self.request()[1]
            self.assertEqual((state['writable'], state['writable_reason']), (False, 'carrier_invalid'), index)
            self.assertIsNotNone(state['model'])
            code, data, _ = self.request('POST', '/api/jobs', self.body(request_id='%032x' % (index + 20)))
            self.assertEqual((code, data['error']['code']), (409, 'carrier_invalid'))
            self.assertNotIn('example.com', json.dumps(data))
        self.assertEqual(len(self.executed), 1)

    def test_acknowledged_direct_carrier_admits_none_route_without_probe(self):
        self.listener.close()
        self.write_carrier({'schema': 1, 'ssh_proxy': None, 'independent': True})
        state = self.request()[1]
        self.assertEqual(state['carrier'], {'configured': True, 'listening': True, 'mode': 'direct'})
        self.assertTrue(state['writable'])
        self.assertEqual(self.request('POST', '/api/jobs', self.body())[0], 202)
        self.manager.worker.join(0.2)
        self.assertIsNone(self.executed[0]['ssh_proxy'])
        self.release.set()
        self.manager.worker.join(5)
        self.write_carrier({'schema': 1, 'ssh_proxy': None, 'independent': False})
        code, data, _ = self.request('POST', '/api/jobs', self.body(request_id='8' * 32))
        self.assertEqual((code, data['error']['code']), (409, 'carrier_invalid'))
        self.assertEqual(len(self.executed), 1)

    def test_runtime_mihomo_is_frozen_into_job_envelope_and_default_stays_none(self):
        self.assertEqual(self.request('POST', '/api/jobs', self.body())[0], 202)
        self.manager.worker.join(0.2)
        self.assertIsNone(self.executed[0]['mihomo'])
        self.release.set()
        self.manager.worker.join(5)
        self.manager.runtime_mihomo = '/custom/bin/mihomo'
        self.assertEqual(self.request('POST', '/api/jobs', self.body(request_id='9' * 32))[0], 202)
        self.manager.worker.join(5)
        self.assertEqual(self.executed[1]['mihomo'], '/custom/bin/mihomo')

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
        second = manage.ManagementServer(vps, self.state, port=0, executor=self.manager.executor)
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
            manage.ManagementServer(vps, self.state, port=0)
        self.assertEqual(caught.exception.code, 'manager_active')
        saved = vps.load(self.state / 'manage-jobs' / ('6' * 32 + '.json'))
        self.assertEqual(saved['job']['state'], 'running')

    def test_processgroup_cancel_is_unknown_and_reaped(self):
        self.manager.executor = None
        self.manager.stopping.set()
        real_popen = subprocess.Popen
        processes = []
        def fake_machine(argv, **kwargs):
            self.assertEqual(argv[-1], '--job')
            self.assertEqual(Path(argv[2]).name, 'manage.py')
            self.assertNotIn(self.manager.bootstrap.hex(), ' '.join(argv))
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
