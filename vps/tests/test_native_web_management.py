"""Real loopback HTTP -> machine adapter -> native THREE-role device lifecycle.

Run this single class serially, with an exclusive API62789 lease. Retained pinned
artifacts and QD_NATIVE_CDN_EVIDENCE are mandatory; no skipped native evidence.
Only the SSH carrier/system scheduling boundaries of ThreeFixture are replaced.
Real controller locking/backups/validation/commits/exports and HTTP admission run.
All raw credentials and native logs stay under the private evidence directory.
"""
import argparse
import base64
import copy
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
import time
import unittest
from unittest import mock
import uuid
from urllib.parse import urlsplit

import test_native_three_lifecycle as three

host, vps, native = three.host, three.vps, three.native
import manage_auth


class SigningClient:
    """Bounded persistent-API test client: verified hello, signed requests, verified responses.

    The bootstrap key is read from the private manage-service state exactly as the
    entry-file generator does; a wrong key can never pass the server proof.
    """
    def __init__(self, test, authority, origin, key):
        self.test, self.authority, self.origin, self.key = test, authority, origin, key
        self.hello_value = None

    def raw(self, method, path, payload, headers):
        host, _, port = self.authority.partition(':')
        connection = http.client.HTTPConnection(host, int(port), timeout=10)
        try:
            connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for name, value in headers.items():
                connection.putheader(name, value)
            if method == 'POST':
                connection.putheader('Content-Length', str(len(payload)))
            connection.endheaders(payload if method == 'POST' else None)
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def hello(self):
        nonce = secrets.token_hex(16)
        status, raw, _ = self.raw('POST', '/api/hello', json.dumps({'version': 1, 'client_nonce': nonce}).encode(),
                                  {'Host': self.authority, 'Origin': 'null', 'Content-Type': 'application/json'})
        self.test.assertEqual(status, 200, 'Real HTTP signed hello failed')
        value = json.loads(raw)
        # Verify BEFORE any key is used or any API request is sent.
        self.test.assertTrue(value['version'] == 1 and value['origin'] == self.origin and
                             value['client_nonce'] == nonce, 'Hello binding mismatch')
        ctx = manage_auth.context(value['origin'], value['epoch'], value['session_id'], nonce, value['server_nonce'])
        if not hmac.compare_digest(value['proof'], manage_auth.server_proof(self.key, ctx)):
            raise AssertionError('Server proof rejected (impostor or wrong bootstrap key)')
        self.hello_value = (value['session_id'], manage_auth.session_key(self.key, ctx), value['epoch'])
        return self.hello_value

    def request(self, method, path, body=None, *, sign=True, headers=None, tamper=None, nonce=None, session=None):
        payload = b'' if body is None else json.dumps(body).encode()
        session_id, ks, epoch = self.hello_value or self.hello()
        nonce = nonce or secrets.token_hex(16)
        base = {'Host': self.authority, 'Origin': 'null'}
        if method == 'POST':
            base['Content-Type'] = 'application/json'
        if sign:
            mac = manage_auth.request_mac(ks, epoch, nonce, method, path, payload)
            base.update({'X-QD-Session': session or session_id, 'X-QD-Nonce': nonce,
                         'X-QD-MAC': '0' * 64 if tamper == 'mac' else mac})
        base.update(headers or {})
        sent = payload + b' ' if tamper == 'body' else payload
        status, raw, values = self.raw(method, path, sent, base)
        self.test.assertEqual(values.get('Cache-Control'), 'no-store')
        self.test.assertIn(values.get('Access-Control-Allow-Origin'), (None, 'null'))
        self.test.assertFalse(any(key.lower() == 'access-control-allow-credentials' for key in values))
        # A known-session answer must carry a valid response MAC before it is parsed.
        if sign and not tamper and not session and status in (200, 202, 404, 409):
            self.test.assertEqual(values.get('X-QD-MAC'), manage_auth.response_mac(ks, epoch, nonce, status, raw),
                                  'Missing/invalid response MAC')
        return status, raw, values, nonce


def h2_users(raw):
    """Strict pinned TypedMessage JSON contract; missing fields are not empty."""
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {'users'} or not isinstance(value['users'], list):
            raise ValueError()
        result = {}
        auths = set()
        for user in value['users']:
            if not isinstance(user, dict) or set(user) - {'email', 'level', 'account'}:
                raise ValueError()
            email, account = user.get('email'), user.get('account')
            if not isinstance(email, str) or not email or ('level' in user and type(user['level']) is not int):
                raise ValueError()
            if (not isinstance(account, dict) or set(account) != {'auth', '_TypedMessage_'} or
                    account['_TypedMessage_'] != 'xray.proxy.hysteria.account.Account' or
                    not isinstance(account['auth'], str) or not account['auth']):
                raise ValueError()
            if email in result or account['auth'] in auths:
                raise ValueError()
            result[email] = account['auth']
            auths.add(account['auth'])
        return result
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ValueError('Pinned H2 live registry schema mismatch (values withheld)') from None


def normalized_uuid(value):
    value = bytearray(uuid.UUID(value).bytes)
    value[6:8] = b'\0\0'
    return bytes(value)


class H2RegistryParser(unittest.TestCase):
    def test_strict_schema_and_omission(self):
        account = {'auth': 'synthetic-auth', '_TypedMessage_': 'xray.proxy.hysteria.account.Account'}
        user = {'email': 'synthetic', 'account': account}
        self.assertEqual(h2_users(json.dumps({'users': [user]})), {'synthetic': 'synthetic-auth'})
        self.assertEqual(h2_users('{"users":[]}'), {})
        for bad in ({}, {'users': None}, {'users': [None]}, {'users': [user, user]},
                    {'users': [dict(user, account={})]}, {'users': [dict(user, email='')]},
                    {'users': [dict(user, account=dict(account, auth=''))]},
                    {'users': [dict(user, account=dict(account, _TypedMessage_='foreign'))]}):
            with self.assertRaisesRegex(ValueError, 'values withheld'):
                h2_users(json.dumps(bad))


class NativeThreeBridge(three.ThreeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get('QD_VPS_ARTIFACTS') or not os.environ.get('QD_NATIVE_CDN_EVIDENCE'):
            raise AssertionError('Explicit pinned artifacts/private evidence/API62789 lease required')
        three.ThreeFixture.setUpClass.__func__(cls)

    def setUp(self):
        # Capture actual company authority before any fixture or MODULE redirect.
        self.real_module = vps.MODULE
        company_module = Path(os.environ.get('QD_NATIVE_REAL_MODULE', str(self.real_module)))
        real_state = Path.home() / '.local/state/quick-deploy-private/vps'
        self.protected = [company_module / 'subscription-urls.json', company_module / 'index.html',
                          real_state / 'state.json', real_state / 'outputs/subscription-urls.json']
        self.protected_hashes = self.hashes()
        self.addCleanup(self.assert_protected)
        super().setUp()
        self.remote_actions = []
        self.fail_after_commit = False
        self.backup_count = 0
        self.gate_entered, self.gate_release = threading.Event(), threading.Event()
        self.gate = False
        self.enterContext(mock.patch.object(vps, 'remote', side_effect=self.remote))
        self.isolate_module()
        import manage
        self.manage = manage

    def hashes(self):
        return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
                for path in self.protected}

    def assert_protected(self):
        self.assertTrue(self.hashes() == self.protected_hashes,
                        'Real company state/URL/launcher changed (values withheld)')

    def isolate_module(self):
        root = self.evidence / 'full-worktree'
        root.mkdir(mode=0o700)
        shutil.copytree(self.real_module, root / 'vps', ignore=shutil.ignore_patterns(
            'subscription-urls.json', 'index.html', '.subscription-urls-*.tmp', '.index-*.tmp', '__pycache__'))
        shutil.copy2(self.real_module.parent / '.gitignore', root / '.gitignore')
        # The qualification source is frozen read-only; the private worktree copy must be writable.
        for path in [root, *root.rglob('*')]:
            if path.is_dir() and not path.is_symlink():
                path.chmod(0o700)
        # An isolated complete module in an actual Git worktree, not a no-op
        # export mock. Native output stays ignored just as in the real repo.
        made = subprocess.run(['git', 'init', '-q', str(root)], capture_output=True, timeout=10)
        self.assertEqual(made.returncode, 0, 'Private full-worktree init failed')
        self.enterContext(mock.patch.object(vps, 'MODULE', root / 'vps'))
        self.state = self.evidence / 'operator-state'
        self.state.mkdir(mode=0o700)
        self.save('isolation.json', {'real_authority_hashes_before': self.protected_hashes,
            'host_sha256': host.digest(vps.MODULE / 'lib/host.py'), 'private_git_root': str(root)})

    def remote(self, record, action, request=None, stdin=None, stdout=None):
        self.remote_actions.append(action)
        if self.gate and action == 'devices-prepare':
            self.gate_entered.set()
            if not self.gate_release.wait(20):
                raise AssertionError('HTTP busy witness gate not released')
        if action == 'devices-prepare':
            result = host.devices_prepare(stdin)
        elif action == 'devices-commit':
            result = host.devices_commit(request)
        elif action == 'abort':
            result = host.abort(request)
        elif action == 'backup':
            self.backup_count += 1
            if self.fail_after_commit and self.backup_count == 3:
                raise host.Error('fixture transport lost after committed rotation')
            with host.export_backup(request).open('rb') as source:
                shutil.copyfileobj(source, stdout)
            result = None
        elif action == 'outputs':
            manifest = host.read_json(self.fx.folder / 'manifest.json')
            result = {name: base64.b64encode(data).decode() for name, data in
                      host.backend_health(self.fx.folder, manifest).items()}
        elif action == 'devices-list':
            manifest = host.read_json(self.fx.folder / 'manifest.json')
            result = {name: {'enabled': r['enable']} for name, r in host.snapshot_accounts(
                self.fx.folder / 'db/x-ui.db', manifest).items()}
        elif action == 'status':
            manifest = host.read_json(self.fx.folder / 'manifest.json')
            result = {'machine_id': host.machine_id(), 'node_id': manifest['node_id'],
                'healthy': True, 'adapter_sha256': host.digest(host.ROOT / 'host.py'),
                'devices': list(manifest['accounts'])}
            if request and 'rename_target' in request:
                result['rename_target_has_history'] = bool(host.rename_target_has_history(
                    self.fx.folder / 'db/x-ui.db', request['rename_target']))
        else:
            raise AssertionError('Unexpected native carrier action: ' + action)
        self.fx.fx.manifest = host.read_json(self.fx.folder / 'manifest.json')
        return result

    def live_h2(self):
        command = [str(self.fx.folder / 'bin/xray-linux-amd64'), 'api', 'inbounduser',
                   '--server=127.0.0.1:62789', '-tag=SELF-h2', '-timeout=6']
        result = subprocess.run(command, capture_output=True, timeout=8)
        stem = 'h2-live-' + str(time.monotonic_ns())
        host.atomic(self.evidence / (stem + '.private.json'), result.stdout)
        host.atomic(self.evidence / (stem + '.private.stderr'), result.stderr)
        self.assertEqual(result.returncode, 0, 'No H2 registry witness on pinned bytes')
        users = h2_users(result.stdout)
        records = self.fx.records()
        expected = {name: r['auth'] for name, r in records.items() if r['enable']}
        self.assertTrue(users == expected, 'Live H2 map differs from enabled authority (values withheld)')
        config = self.fx.api().call('/panel/api/server/getConfigJson')
        inbound = next(i for i in config['inbounds'] if i.get('tag') == 'SELF-h2')
        generated = {r['email']: r['auth'] for r in inbound['settings']['clients']}
        self.assertTrue(users == generated, 'Live H2 map differs from generated map (values withheld)')
        self.assertNotIn('auth', inbound['streamSettings']['hysteriaSettings'])
        pairs = sorted((name, hashlib.sha256(auth.encode()).hexdigest()) for name, auth in users.items())
        self.save(stem + '.summary.json', {'exact_enabled_map': True, 'exact_generated_map': True,
            'map_digest': hashlib.sha256(json.dumps(pairs).encode()).hexdigest()})
        return users

    def epoch(self):
        parent = self.fx.fx.proc.pid
        children = set()
        for path in (Path('/proc') / str(parent) / 'task').glob('*/children'):
            children.update(path.read_text().split())
        found = []
        for pid in children:
            stat = Path('/proc') / pid / 'stat'
            if stat.exists() and 'xray' in (Path('/proc') / pid / 'comm').read_text():
                fields = stat.read_text().rsplit(')', 1)[1].split()
                found.append((int(pid), fields[19]))
        self.assertEqual(len(found), 1, 'Exactly one owned native Xray child required')
        return found[0]

    def bootstrap_state(self):
        manifest = self.fx.manifest
        record = {'endpoint': manifest['endpoint'], 'machine_id': host.machine_id(), 'port': 22}
        document = {'format': host.FORMAT, 'owner': manifest['owner'], 'config': manifest['config'],
                    'current': record, 'previous': [], 'backup': None}
        with vps.locked(self.state):
            backup = vps.backup_remote(self.state, document, record)
            vps.validate_outputs(self.state, document, record, vps.resolve_mihomo(), backup)
            vps.remember_commit(self.state, document, record, backup,
                                {'machine_id': host.machine_id(), 'node_id': manifest['node_id']})
            vps.export_after_commit(self.state)

    def start_bridge(self):
        # Complete private state is created by the real constructor (master key,
        # config) in the isolated operator state. No product port or SSH carrier:
        # an explicit acknowledged DIRECT carrier is the fixture's injected remote.
        mihomo = vps.resolve_mihomo()
        self.manager = self.manage.ManagementServer(vps, self.state, port=0, executor=lambda envelope:
            self.manage.machine_execute(vps, self.state, dict(envelope, mihomo=mihomo)))
        vps.host.atomic(self.state / 'manage-service/carrier.json',
                        json.dumps({'schema': 1, 'ssh_proxy': None, 'independent': True}))
        self.bridge_thread = threading.Thread(target=self.manager.serve_forever, daemon=True)
        self.bridge_thread.start()
        def stop():
            self.gate_release.set()
            self.manager.close()
            self.bridge_thread.join(5)
        self.addCleanup(stop)
        self.assertEqual(self.manager.httpd.server_address[0], '127.0.0.1')
        self.assertNotEqual(self.manager.httpd.server_port, 47831, 'Product port must not be used')
        _, master = manage_auth.load_state(self.state)
        self.assertTrue(callable(self.client), 'Fixture client() must not be shadowed')
        self.signed = SigningClient(self, self.manager.authority, self.manager.origin,
                                    manage_auth.bootstrap_key(master))
        self.signed.hello()

    def http(self, method, path, body=None, *, auth=True, headers=None, tamper=None):
        status, raw, _, _ = self.signed.request(method, path, body, sign=auth, headers=headers, tamper=tamper)
        return status, raw, json.loads(raw) if raw else None

    def snapshot(self):
        status, _, body = self.http('GET', '/api/state')
        self.assertEqual(status, 200)
        self.assertTrue(body['generation'] == vps.load(self.state / 'state.json')['native_generation'],
                        'HTTP generation differs from committed state (values withheld)')
        self.assertTrue(body['generation'] == host.digest(vps.load(self.state / 'state.json')['backup']))
        self.assertFalse(body['pending']['present'])
        return body

    def model_profile(self, state, name):
        import yaml
        model = state['model']
        entry = next(d for d in model['devices'] if d['name'] == name)
        self.assertEqual(len(entry['links']), 5)
        self.assertTrue(entry['enabled'])
        downloads = {item['label']: base64.b64decode(model['files'][item['path']], validate=True)
                     for item in entry['downloads']}
        raw = downloads['原生 Mihomo 配置']
        self.assertTrue(raw == host.get_feed(self.fx.manifest, name, 'mihomo'),
                        'HTTP model YAML differs from real native subscription (values withheld)')
        profile = yaml.safe_load(raw)
        three.native_nodes(profile)
        self.live_h2()
        self.topology()
        return profile

    def request(self, action, names, generation):
        body = {'request_id': secrets.token_hex(16), 'action': action, 'names': names,
                'expected_generation': generation}
        if action in ('reset', 'remove'):
            body['confirm'] = {'action': action, 'name': names[0]}
        return body

    def finish_job(self, admitted, outcome='committed'):
        self.manager.worker.join(1150)
        self.assertFalse(self.manager.worker.is_alive(), 'Bounded native machine job did not finish')
        status, _, job = self.http('GET', '/api/jobs/' + admitted['job_id'])
        self.assertEqual(status, 200)
        self.assertEqual(job['state'], 'finished')
        self.assertEqual(job.get('outcome'), outcome, 'Native HTTP outcome mismatch; private diagnostics retained')
        return job

    def mutate(self, action, names, state):
        body = self.request(action, names, state['generation'])
        status, _, admitted = self.http('POST', '/api/jobs', body)
        self.assertEqual(status, 202, 'Native HTTP job not admitted')
        self.finish_job(admitted)
        new = self.snapshot()
        self.assertNotEqual(new['generation'], state['generation'])
        # Idempotent repeated request refers to the same terminal job, no action.
        calls = len(self.remote_actions)
        status, _, replay = self.http('POST', '/api/jobs', body)
        self.assertEqual(status, 202)
        self.assertEqual(replay['job_id'], admitted['job_id'])
        self.assertEqual(len(self.remote_actions), calls)
        self.save(action + '.summary.json', {'http_native_commit': True,
            'generation': new['generation'], 'idempotent_request': True})
        return new

    def rejected_before_native(self, state):
        before = {table: self.fx.rows(table) for table in ('clients', 'client_inbounds')}
        calls = len(self.remote_actions)
        body = self.request('add', ['forbidden'], state['generation'])
        cases = [('GET', '/api/state', None, True, {'Host': 'rebinding.invalid'}, None),
                 ('GET', '/api/state', None, False, {}, None),
                 ('POST', '/api/jobs', body, False, {}, None),
                 ('POST', '/api/jobs', body, True, {'Origin': 'https://foreign.invalid'}, None),
                 ('POST', '/api/jobs', body, True, {'Content-Type': 'application/x-www-form-urlencoded'}, None),
                 ('OPTIONS', '/api/jobs', None, True, {'Origin': 'https://foreign.invalid'}, None),
                 ('GET', '/api/state', None, True, {'Origin': 'http://' + self.manager.authority}, None),
                 ('GET', '/api/state', None, True, {'X-QD-Session': secrets.token_hex(16)}, None),
                 ('GET', '/api/state', None, True, {}, 'mac'),
                 ('POST', '/api/jobs', body, True, {}, 'mac'),
                 ('POST', '/api/jobs', body, True, {}, 'body')]
        for method, path, payload, auth, headers, tamper in cases:
            status, _, _ = self.http(method, path, payload, auth=auth, headers=headers, tamper=tamper)
            self.assertIn(status, (400, 401, 403, 405, 415, 421))
            self.assertEqual(len(self.remote_actions), calls, 'Rejected HTTP reached real native carrier')
            self.assertTrue(before == {table: self.fx.rows(table) for table in before},
                            'Rejected HTTP changed native DB (values withheld)')
        # Replay of a verified request (GET and POST) is rejected before admission.
        stale = self.request('add', ['forbidden'], '0' * 64)
        for method, path, payload, first in (('GET', '/api/state', None, 200), ('POST', '/api/jobs', stale, 409)):
            status, _, _, nonce = self.signed.request(method, path, payload)
            self.assertEqual(status, first)
            self.assertEqual(self.signed.request(method, path, payload, nonce=nonce)[0], 401, 'Replay accepted')
        # A client using any other bootstrap key can neither pass the proof nor sign.
        impostor = SigningClient(self, self.manager.authority, self.manager.origin, secrets.token_bytes(32))
        with self.assertRaisesRegex(AssertionError, 'Server proof rejected'):
            impostor.hello()
        self.assertEqual(len(self.remote_actions), calls)
        self.assertEqual(self.http('POST', '/api/jobs', stale)[0], 409)
        with vps.locked(self.state):
            self.assertEqual(self.http('POST', '/api/jobs', body)[0], 409)
        self.assertEqual(len(self.remote_actions), calls)
        self.save('http-rejection.summary.json', {'cases': len(cases), 'zero_native_calls': True,
            'zero_native_row_changes': True, 'stale_and_locked_rejected': True})

    def vless_rejected(self, rejected, correct, origin, credential):
        registered = {normalized_uuid(r['uuid']) for r in self.fx.records().values() if r['enable']}
        self.assertTrue(normalized_uuid(credential) not in registered, 'Rejected UUID remains registered (values withheld)')
        for role, context in (('reality', self.reality_coordinate), ('cdn', None)):
            from contextlib import nullcontext
            with context() if context else nullcontext():
                self.await_core_started()
                epoch = self.epoch()
                path = self.fx.folder / 'logs/dual-error.log'
                offset = path.stat().st_size
                self.fresh(rejected, role, origin, False)
                with path.open('rb') as stream:
                    stream.seek(offset)
                    verdict = 'invalid request user id: ' + credential in stream.read().decode(errors='replace')
                self.assertTrue(verdict, role + ' missing exact post-offset native UUID rejection')
                self.fresh(correct, role, origin, True)
                self.assertEqual(self.epoch(), epoch, 'VLESS paired positive changed core epoch')
                self.save('vless-' + role + '-' + str(time.monotonic_ns()) + '.summary.json',
                          {'exact_native_rejection': True, 'same_epoch_positive': True, 'epoch': epoch})

    def h2_rejected(self, rejected, correct, origin, credential):
        users = self.live_h2()
        self.assertTrue(credential not in users.values(), 'Rejected Auth unexpectedly live (values withheld)')
        epoch = self.epoch()
        first = len(self.client_directories)
        self.fresh(rejected, 'h2', origin, False)
        logs = [path / 'private.log' for path in self.client_directories[first:]]
        text = '\n'.join(path.read_text() for path in logs)
        # Freeze the actual pinned client's explicit HTTP auth verdict, never EOF.
        verdict = bool(re.search(r'(?i)(?:auth[^\n]*404|404[^\n]*auth)', text))
        self.assertTrue(verdict, 'Missing pinned H2 client authentication 404; inspect private client logs')
        self.fresh(correct, 'h2', origin, True)
        self.assertEqual(self.epoch(), epoch, 'H2 paired positive changed core epoch')
        self.save('h2-reject-' + str(time.monotonic_ns()) + '.summary.json',
                  {'live_absent': True, 'client_auth_404': True, 'same_epoch_positive': True, 'epoch': epoch})

    def subid_rejected(self, subid, old):
        self.assertFalse(any(subid in path for path in host.feed_paths(self.fx.manifest)))
        for prefix in ('/mihomo/', '/sub/'):
            connection = http.client.HTTPConnection('127.0.0.1', host.SUB_PORT, timeout=8)
            try:
                connection.request('GET', prefix + subid)
                response = connection.getresponse()
                raw = response.read()
                self.assertEqual(response.status, 404, 'Old SubID still published by real native subserver')
                self.assertTrue(all(value.encode() not in raw for value in old.values()),
                                'Revoked subscription disclosed credentials (values withheld)')
            finally:
                connection.close()

    def assert_model_revoked(self, state, old):
        raw = json.dumps(state).encode()
        material = raw + b''.join(base64.b64decode(value) for value in state['model']['files'].values())
        self.assertTrue(all(value.encode() not in material for value in old.values()),
                        'New HTTP model contains revoked credentials (values withheld)')

    def test_http_native_add_rename_reset_remove(self):
        # First native contact explicitly confirms actual pinned H2 JSON before
        # any CRUD, rather than silently substituting generated configuration.
        self.live_h2()
        origin = self.origin()
        origin.RequestHandlerClass.protocol_version = 'HTTP/1.1'
        self.bootstrap_state()
        self.start_bridge()
        state = self.snapshot()
        self.assertTrue(state['writable'])
        self.rejected_before_native(state)
        original = copy.deepcopy(self.fx.records())
        control = self.model_profile(state, 'company')
        self.all_three(control, origin)
        # Hold the real prepare call just long enough to witness HTTP busy.
        self.gate = True
        body = self.request('add', ['tablet'], state['generation'])
        status, _, admitted = self.http('POST', '/api/jobs', body)
        self.assertEqual(status, 202)
        try:
            self.assertTrue(self.gate_entered.wait(120), 'Native prepare boundary not reached')
            calls = len(self.remote_actions)
            self.assertEqual(self.http('POST', '/api/jobs', self.request(
                'add', ['forbidden'], state['generation']))[0], 409)
            self.assertEqual(len(self.remote_actions), calls)
        finally:
            self.gate = False
            self.gate_release.set()
        self.finish_job(admitted)
        state = self.snapshot()
        profile = self.model_profile(state, 'tablet')
        self.all_three(profile, origin)
        state = self.mutate('rename', ['tablet', 'reader'], state)
        renamed = self.model_profile(state, 'reader')
        old = {key: self.fx.records()['reader'][key] for key in ('uuid', 'auth', 'subId')}
        self.assertTrue(three.native_nodes(profile) == three.native_nodes(renamed),
                        'Rename changed credential/profile maps (values withheld)')
        self.all_three(profile, origin)
        state = self.mutate('reset', ['reader'], state)
        fresh = self.model_profile(state, 'reader')
        current = {key: self.fx.records()['reader'][key] for key in old}
        self.assertTrue(all(current[key] != old[key] for key in old), 'Reset did not rotate all credentials')
        self.assert_model_revoked(state, old)
        self.vless_rejected(profile, fresh, origin, old['uuid'])
        self.h2_rejected(profile, fresh, origin, old['auth'])
        self.subid_rejected(old['subId'], old)
        wrong = copy.deepcopy(fresh)
        nonce = str(uuid.uuid4())
        self.assertTrue(normalized_uuid(nonce) not in {normalized_uuid(r['uuid']) for r in self.fx.records().values()},
                        'Random nonce collides with native registry (values withheld)')
        for role in ('reality', 'cdn'):
            three.native_nodes(wrong)[role]['uuid'] = nonce
        self.vless_rejected(wrong, fresh, origin, nonce)
        wrong_auth = copy.deepcopy(fresh)
        three.native_nodes(wrong_auth)['h2']['password'] = secrets.token_hex(24)
        self.h2_rejected(wrong_auth, fresh, origin, three.native_nodes(wrong_auth)['h2']['password'])
        self.all_three(fresh, origin)
        with self.reality_coordinate():
            held = [self.held_stream(fresh, 'reality', origin)]
        held.extend(self.held_stream(fresh, role, origin) for role in ('cdn', 'h2'))
        for stream in held:
            self.assert_stream_alive(stream)
        previous_panel = self.fx.fx.proc
        state = self.mutate('remove', ['reader'], state)
        self.assertIsNotNone(previous_panel.poll(), 'Old native panel/core process group survived removal')
        for stream in held:
            self.assert_stream_revoked(stream, origin)
        self.assertNotIn('reader', self.fx.records())
        self.assert_model_revoked(state, current)
        control = self.model_profile(state, 'company')
        self.vless_rejected(fresh, control, origin, current['uuid'])
        self.h2_rejected(fresh, control, origin, current['auth'])
        self.subid_rejected(current['subId'], current)
        self.all_three(control, origin)
        for name, record in original.items():
            self.unchanged(record, self.fx.records()[name])
        self.http2_evidence()
        self.assert_protected()
        self.save('RESULT.json', {'http_to_native_full_chain': True, 'actions': ['add', 'rename', 'reset', 'remove'],
            'all_three_positive': True, 'old_and_nonce_uuid_native_verdicts_both_vless': True,
            'h2_live_registry_and_old_random_auth_404_same_epoch_positive': True,
            'held_streams_revoked': True, 'old_subid_404_no_credentials': True,
            'control_company_preserved': True, 'real_exports_and_generation': True,
            'company_authority_unchanged': True, 'host_sha256': host.digest(vps.MODULE / 'lib/host.py'),
            'residual_scope': ['Blank H2 password on-wire rejection not claimed; no bespoke QUIC client.',
                               'Postcommit lost-result recovery covered by existing native controller test, not this HTTP run.']})
