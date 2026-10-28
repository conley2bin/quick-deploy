"""Pinned native dual-transport module regression tests, entirely local.

QD_VPS_ARTIFACTS must contain the three release-lock assets.  Native is composed,
not inherited: importing this file does not duplicate the legacy test suite.
No installer, SSH, systemctl, public destination, ACME, or live client is used.
Readiness/rollback uses the existing fixture's scheduler fake, not an OS reboot.
"""
import base64
import copy
from contextlib import closing
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import ssl
import subprocess
import tarfile
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.parse import parse_qs, unquote, urlsplit

import test_native as native

host, vps = native.host, native.vps


def decoded(value):
    return json.loads(value) if isinstance(value, str) else value


@unittest.skipUnless(os.environ.get('QD_VPS_ARTIFACTS'),
                     'Explicit locked native artifacts required; mocks are not native proof')
class NativeDual(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        native.Native.setUpClass()
        cls.addClassCleanup(native.Native.doClassCleanups)

    def setUp(self):
        for name in ('dual_enabled', 'hysteria_certificate_paths', 'enable_hysteria'):
            self.assertTrue(callable(getattr(host, name, None)), 'Missing frozen host helper: ' + name)
        self.fx = native.Native('test_native_api_scope_core_mihomo_and_no_rotation')
        self.addCleanup(self.fx.doCleanups)
        self.fx.setUp()
        self.fx.device_fixture()
        self.assertFalse(host.dual_enabled(self.fx.manifest), 'Fresh native TCP candidate must not require an unissued certificate')
        self.cert_relative = Path('caddy/data/caddy/certificates/fixture/sub.example.org.crt')
        self.key_relative = self.cert_relative.with_suffix('.key')
        self.make_certificate()
        # Certificate selection stays production-owned; only this fixture's
        # already-owned test pair substitutes for an actual HTTP01 issuance.
        self.enterContext(mock.patch.object(host, 'hysteria_certificate_paths', side_effect=self.certificate_paths))
        real_call = host.API.call
        def loopback_add(api, path, data=None):
            if path == '/panel/api/inbounds/add' and data.get('protocol') == 'hysteria':
                data = dict(data, listen='127.0.0.1')
            return real_call(api, path, data)
        self.enterContext(mock.patch.object(host.API, 'call', new=loopback_add))
        self.addCleanup(self.preserve_failure_evidence)

    def preserve_failure_evidence(self):
        result = self._outcome.result
        if not any(test is self for test, _ in result.failures + result.errors):
            return
        # Collect while the owned panel/core and fixture files still exist.
        # Keep raw native logs private; stdout receives only an artifact path.
        destination = Path(tempfile.mkdtemp(prefix='qd-native-dual-failure-'))
        os.chmod(destination, 0o700)
        summary = {'test': self.id(), 'expected_core_version': self.fx.lock['core']['version']}
        def retain(name, data):
            path = destination / name
            path.write_bytes(data if isinstance(data, bytes) else data.encode())
            path.chmod(0o600)
        try:
            status = host.API(self.manifest).call('/panel/api/server/status') or {}
            xray = status.get('xray', {})
            summary['xray'] = {key: xray.get(key) for key in ('state', 'version', 'error')}
        except Exception as error:
            summary['status_error_type'] = type(error).__name__
        try:
            processes = subprocess.run(['ps', '-o', 'pid,ppid,etimes,comm', '--ppid', str(self.fx.proc.pid)],
                                       capture_output=True, timeout=5)
            retain('owned-processes.txt', processes.stdout)
            listeners = subprocess.run(['ss', '-H', '-lntup'], capture_output=True, timeout=5)
            ports = {self.manifest['config']['proxy_port'], host.ADMIN_PORT, host.SUB_PORT, host.API_PORT}
            lines = [line for line in listeners.stdout.decode().splitlines()
                     if any(':' + str(port) in line for port in ports)]
            retain('fixture-listeners.txt', '\n'.join(lines))
        except Exception as error:
            summary['process_evidence_error_type'] = type(error).__name__
        try:
            check = subprocess.run([str(self.folder / 'bin/xray-linux-amd64'), 'run', '-test',
                                    '-config', str(self.folder / 'bin/config.json')],
                                   env=dict(os.environ, XRAY_LOCATION_ASSET=str(self.folder / 'bin')),
                                   capture_output=True, timeout=15)
            summary['config_check_returncode'] = check.returncode
            retain('config-check.txt', check.stdout + check.stderr)
        except Exception as error:
            summary['config_check_error_type'] = type(error).__name__
        self.fx.log.flush()
        logs = [self.folder / 'native.log'] + list((self.folder / 'logs').rglob('*'))
        for index, log in enumerate(logs):
            if log.is_file() and not log.is_symlink():
                retain(str(index) + '-' + log.name, log.read_bytes()[-256 * 1024:])
        for port, log in getattr(self, '_client_log_paths', {}).items():
            if log.is_file(): retain('client-' + str(port) + '.log', log.read_bytes()[-128 * 1024:])
        summary['last_https'] = getattr(self, 'last_https_diagnostic', None)
        retain('summary.json', json.dumps(summary, indent=2))
        print('Private native failure evidence: ' + str(destination))

    @property
    def folder(self):
        return self.fx.work

    @property
    def manifest(self):
        return self.fx.manifest

    def api(self):
        return host.wait_api(self.manifest)

    def certificate_paths(self, folder, manifest):
        cert, key = Path(folder) / self.cert_relative, Path(folder) / self.key_relative
        if not cert.is_file() or not key.is_file():
            raise host.Error('Fixture certificate has not been issued')
        return cert.absolute(), key.absolute()

    def make_certificate(self):
        cert, key = self.folder / self.cert_relative, self.folder / self.key_relative
        cert.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt',
            'ec_paramgen_curve:P-256', '-nodes', '-days', '1', '-subj', '/CN=sub.example.org',
            '-addext', 'subjectAltName=DNS:sub.example.org,DNS:target.example.org,IP:127.0.0.1',
            '-addext', 'basicConstraints=critical,CA:TRUE', '-keyout', str(key), '-out', str(cert)],
            capture_output=True, timeout=15)
        self.assertTrue(result.returncode == 0, 'Fixture certificate generation failed')
        return cert, key

    def rows(self, table, folder=None):
        self.assertIn(table, ('clients', 'client_inbounds', 'hosts', 'inbounds'))
        with closing(sqlite3.connect((folder or self.folder) / 'db/x-ui.db')) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute('select * from ' + table + ' order by 1,2')]

    def records(self):
        api = self.api()
        return {name: api.call('/panel/api/clients/get/' + name)['client'] for name in self.manifest['accounts']}

    def dto(self, name):
        inbound = next(i for i in self.api().call('/panel/api/inbounds/list') if i['protocol'] == 'vless')
        client = copy.deepcopy(next(c for c in decoded(inbound['settings'])['clients'] if c['email'] == name))
        client['limitHwid'] = self.api().call('/panel/api/clients/get/' + name)['client']['limitHwid']
        return client

    def seed_policy(self, name='company', **extra):
        client = self.dto(name)
        client.update(totalGB=1234567890, expiryTime=int(time.time() * 1000) + 86400000,
                      limitIp=2, trafficReset='monthly', trafficResetDay=6, reset=7,
                      resetMax=9, comment='dual-policy', group='dual-fixture')
        client.update(extra)
        self.api().call('/panel/api/clients/update/' + name, client)

    def enable(self):
        self.assertIsNone(host.enable_hysteria(self.folder, self.manifest, 22))
        self.assertTrue(host.dual_enabled(self.manifest))
        host.backend_health(self.folder, self.manifest)
        host.atomic(self.folder / 'manifest.json', host.dumps(self.manifest))

    def topology(self):
        inbounds = self.rows('inbounds')
        self.assertTrue(len(inbounds) == 2 and {i['protocol'] for i in inbounds} == {'vless', 'hysteria'},
                        'Exactly the owned TCP and UDP inbounds are required')
        ids = {i['id'] for i in inbounds}
        clients, joins, hosts = self.rows('clients'), self.rows('client_inbounds'), self.rows('hosts')
        expected = {(c['id'], i) for c in clients for i in ids}
        self.assertTrue(len(joins) == len(expected) and {(j['client_id'], j['inbound_id']) for j in joins} == expected,
                        'Each single global client must have both exact joins, without foreign joins')
        self.assertTrue(len(hosts) == 2 and {h['inbound_id'] for h in hosts} == ids,
                        'Both native Hosts endpoints must be present exactly once')
        self.assertTrue(all(h['address'] == self.manifest['endpoint'] and h['port'] == self.manifest['config']['proxy_port'] for h in hosts))
        host.snapshot_accounts(self.folder / 'db/x-ui.db', self.manifest)
        host.snapshot_host_group(self.folder / 'db/x-ui.db', self.manifest)

    def profile(self, name='company'):
        import yaml
        return yaml.safe_load(host.get_feed(self.manifest, name, 'mihomo'))

    def assert_native_exports(self, name='company'):
        profile = self.profile(name)
        record = self.api().call('/panel/api/clients/get/' + name)['client']
        proxies = profile['proxies']
        self.assertTrue(len(proxies) == 2 and {p['type'] for p in proxies} == {'vless', 'hysteria2'})
        h2 = next(p for p in proxies if p['type'] == 'hysteria2')
        tcp = next(p for p in proxies if p['type'] == 'vless')
        self.assertTrue(bool(record['auth']) and h2['password'] == record['auth'], 'Native Auth must own H2 password')
        self.assertTrue(tcp['uuid'] == record['uuid'] and tcp.get('flow') == 'xtls-rprx-vision')
        self.assertTrue(h2['sni'] == self.manifest['config']['domain'] and not h2.get('skip-cert-verify', False))
        group = next(g for g in profile['proxy-groups'] if g['name'] == 'PROXY')
        self.assertTrue(group['proxies'][:2] == [h2['name'], tcp['name']], 'New profiles must prefer H2 over dead TCP')
        links = base64.b64decode(host.get_feed(self.manifest, name, 'sub'), validate=True).decode().splitlines()
        self.assertTrue(len(links) == 2)
        uris = {urlsplit(link).scheme: urlsplit(link) for link in links}
        self.assertTrue(set(uris) == {'vless', 'hysteria2'})
        self.assertTrue(unquote(uris['hysteria2'].username) == record['auth'] and uris['vless'].username == record['uuid'])
        self.assertTrue(parse_qs(uris['hysteria2'].query)['sni'] == [self.manifest['config']['domain']])
        for fmt in ('sub', 'mihomo'):
            host.validate_feed_scope(self.manifest, name, host.get_feed(self.manifest, name, fmt), fmt)
        for item in ('geoip.dat', 'geosite.dat'):
            shutil.copy2(self.folder / 'bin' / item, self.folder / item)
        path = self.folder / 'native-dual.yaml'
        path.write_bytes(host.get_feed(self.manifest, name, 'mihomo'))
        result = subprocess.run([vps.resolve_mihomo(), '-t', '-d', str(self.folder), '-f', str(path)],
                                capture_output=True, timeout=30)
        self.assertTrue(result.returncode == 0, 'Full native dual profile must parse in pinned local Mihomo')
        return profile

    def archive(self, request=None):
        path = host.export_backup(request)
        stage = self.fx.root / ('dual-archive-' + str(time.monotonic_ns()))
        stage.mkdir()
        with path.open('rb') as source:
            host.safe_unpack(source, stage)
        verified = host.verify_backup(stage, self.fx.lock)
        return path, stage, verified

    def request(self):
        self.assertTrue(callable(getattr(host, 'transport_prepare', None)), 'Missing frozen transport_prepare action')
        self.assertTrue(callable(getattr(host, 'transport_commit', None)), 'Missing frozen transport_commit action')
        request = self.fx.device_request('reset', 'company')
        request.pop('device_action'); request.pop('names')
        source = (vps.MODULE / 'lib/host.py').read_bytes()
        # This is the controller's real pre-change backup sequence: hand off
        # capable recovery code before any feature intent, then verify the
        # closed TCP archive and bind its digest once for this operation.
        backup_request = dict(request, adapter_b64=base64.b64encode(source).decode())
        before, _, verified = self.archive(backup_request)
        self.assertFalse(host.dual_enabled(verified), 'Feature backup must describe the original TCP state')
        self.assertTrue(host.digest(host.ROOT / 'host.py') == request['adapter_sha256'],
                        'Verified pre-change backup did not install the current recovery adapter')
        return dict(request, mode='transport-upgrade', kind='transport-upgrade', ssh_port=22, lock=self.fx.lock,
                    before_sha256=host.digest(before), source_transports=['tcp-reality'],
                    target_transports=['tcp-reality', 'hysteria2'])

    def prepare(self, request):
        # Transport upgrade changes no release assets. Only source + bound request
        # are uploaded, as for the existing module's native device transaction.
        self.assertTrue(host.digest(host.ROOT / 'host.py') == request['adapter_sha256'],
                        'Feature preparation must already have the uploaded capable recovery adapter')
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w') as tar:
            for name, value in (('request.json', host.dumps(request).encode()),
                                ('host.py', (vps.MODULE / 'lib/host.py').read_bytes())):
                member = tarfile.TarInfo(name); member.size = len(value); member.mode = 0o600
                tar.addfile(member, io.BytesIO(value))
        data.seek(0)
        return host.transport_prepare(data)

    def finish(self, request):
        # At this point export-sha256 still names the genuine pre-change archive.
        # Accepting it would publish new H2 Auth without an off-node dual backup.
        with self.assertRaisesRegex(host.Error, 'new dual-state backup'):
            host.transport_commit(dict(request, offnode_sha256=request['before_sha256']))
        self.assertTrue(host.read_json(host.ROOT / 'pending.json')['phase'] == 'prepared',
                        'Rejected old-archive commit changed the prepared intent')
        path, _, verified = self.archive()
        self.assertTrue(host.digest(path) != request['before_sha256'], 'Candidate archive reused the pre-change digest')
        result = host.transport_commit(dict(request, offnode_sha256=host.digest(path)))
        self.fx.manifest = host.read_json(self.folder / 'manifest.json')
        self.assertTrue(result.get('committed') and host.dual_enabled(verified))
        return result

    def device(self, action, *names):
        request = self.fx.device_request(action, *names)
        self.fx.device_prepare(request)
        self.fx.device_finish(request)
        return request

    def origin(self):
        cert, key = self.certificate_paths(self.folder, self.manifest)
        class Origin(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.server.peers.append(self.client_address[0])
                body = b'native-dual-https-egress\n'
                self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self, *_): pass
        class OriginServer(http.server.ThreadingHTTPServer):
            def get_request(self):
                try:
                    return super().get_request()
                except ssl.SSLError as error:
                    self.tls_errors.append(error.reason)
                    raise
        server = OriginServer(('127.0.0.1', 0), Origin)
        server.daemon_threads = True; server.peers = []; server.tls_errors = []; server.snis = []
        server.trust_file = self.folder / ('origin-trust-' + str(server.server_port) + '.pem')
        server.trust_file.write_bytes(cert.read_bytes())
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(cert, key)
        context.set_servername_callback(lambda connection, name, original: server.snis.append(name))
        server.socket = context.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        self.fx.stop()
        with closing(sqlite3.connect(self.folder / 'db/x-ui.db')) as db:
            template = json.loads(db.execute("select value from settings where key='xrayTemplateConfig'").fetchone()[0])
        # Keep native dispatcher/egress evidence if an authenticated stream fails
        # before the origin TLS callback; all raw logs stay in the private fixture.
        template['log'] = {'access': str(self.folder / 'logs/dual-access.log'),
                           'error': str(self.folder / 'logs/dual-error.log'), 'loglevel': 'debug'}
        # Both exceptions are needed: pinned freedom blocks loopback even when
        # the routing rule chooses direct. Allow ONLY the owned origin port.
        for outbound in template['outbounds']:
            if outbound['tag'] == 'direct':
                outbound['settings'] = {'finalRules': [{'action': 'allow', 'network': 'tcp',
                    'ip': ['127.0.0.1/32'], 'port': str(server.server_port)}, {'action': 'block'}]}
        rules = template['routing']['rules']
        api_rules = [rule for rule in rules if rule.get('outboundTag') == 'api']
        template['routing']['rules'] = api_rules + [{'type': 'field', 'ip': ['127.0.0.1/32'],
            'network': 'tcp', 'port': str(server.server_port), 'outboundTag': 'direct'}] + [rule for rule in rules if rule not in api_rules]
        host.settings_write(self.folder / 'db/x-ui.db', {'xrayTemplateConfig': json.dumps(template)})
        self.fx.start(); self.api().call('/panel/api/server/restartXrayService', {})
        return server

    def client(self, profile, protocol='hysteria2', trust=None):
        proxy = copy.deepcopy(next(p for p in profile['proxies'] if p['type'] == protocol))
        # Only advertised endpoint is redirected to this disposable loopback
        # server. All native protocol/auth/TLS/REALITY fields stay intact.
        proxy['server'] = '127.0.0.1'
        proxy['port'] = self.manifest['config']['proxy_port']
        directory = self.folder / ('dual-client-' + str(time.monotonic_ns())); directory.mkdir()
        trust_file = directory / 'trust.pem'
        trust_file.write_bytes(Path(trust or self.certificate_paths(self.folder, self.manifest)[0]).read_bytes())
        port = native.free_port()
        config = {'mixed-port': port, 'bind-address': '127.0.0.1', 'allow-lan': False, 'log-level': 'info',
                  'dns': {'enable': False}, 'tun': {'enable': False}, 'proxies': [proxy],
                  'proxy-groups': [{'name': 'PROXY', 'type': 'select', 'proxies': [proxy['name']]}], 'rules': ['MATCH,PROXY']}
        return native.start_mihomo(self, directory, config, trust_file)

    def https(self, port, origin):
        def core_children():
            tasks = Path('/proc') / str(self.fx.proc.pid) / 'task'
            found = set()
            for children in tasks.glob('*/children'):
                try: found.update(children.read_text().split())
                except FileNotFoundError: pass
            return ' '.join(sorted(found))
        before_children = core_children()
        result = subprocess.run(['curl', '--silent', '--show-error', '--max-time', '4', '--noproxy', '',
            '--proxy', 'http://127.0.0.1:' + str(port), '--cacert', str(origin.trust_file),
            '--connect-to', 'sub.example.org:' + str(origin.server_port) + ':127.0.0.1:' + str(origin.server_port),
            'https://sub.example.org:' + str(origin.server_port) + '/'], capture_output=True, timeout=6)
        body_matches = result.stdout == b'native-dual-https-egress\n'
        after_children = core_children()
        log_path = getattr(self, '_client_log_paths', {}).get(port)
        log = log_path.read_text().lower() if log_path and log_path.exists() else ''
        stages = [name for name in ('certificate', 'authentication', 'timeout', 'connection refused', 'closed') if name in log]
        # Diagnostic categories and curl's local-fixture stderr only; never emit
        # native profiles, UUIDs, Auth values or full client logs on failure.
        self.last_https_diagnostic = 'curl_exit=' + str(result.returncode) + ', exact_body=' + str(body_matches) + ', stages=' + ','.join(stages) + ', origin_tls=' + ','.join(origin.tls_errors[-3:]) + ', core_children=' + before_children + '->' + after_children + ', stderr=' + result.stderr.decode(errors='replace').strip()
        return result.returncode == 0 and body_matches

    def test_enable_preserves_full_native_policy_and_exports(self):
        self.seed_policy()
        self.seed_policy('home', enable=False, limitHwid=3)
        # Seeding native policies is a supported external edit. Reconcile the
        # legacy publication projection before capturing the upgrade baseline.
        host.backend_health(self.folder, self.manifest)
        before, reality = self.records(), copy.deepcopy(self.manifest['reality'])
        before_ids = {row['email']: row['id'] for row in self.rows('clients')}
        old_paths = host.feed_paths(self.manifest)
        self.enable(); self.topology()
        after = self.records()
        for name, record in before.items():
            changed = sorted(k for k in record.keys() | after[name].keys()
                             if k not in ('auth', 'updatedAt') and
                             (k not in record or k not in after[name] or record[k] != after[name][k]))
            self.assertFalse(changed, 'Upgrade changed native fields (values withheld): ' + ','.join(changed))
            self.assertTrue(bool(after[name]['auth']))
        self.assertTrue(before_ids == {row['email']: row['id'] for row in self.rows('clients')})
        self.assertTrue(self.manifest['reality'] == reality, 'Upgrade changed REALITY identity')
        self.assertTrue(host.feed_paths(self.manifest) == old_paths, 'Upgrade changed authoritative legacy capability paths')
        self.assert_native_exports()

    def test_module_upgrade_reset_rename_remove_real_h2(self):
        origin = self.origin()
        self.seed_policy()
        request = self.request(); self.prepare(request); committed = self.finish(request)
        self.topology()
        old_profile = self.assert_native_exports(); old = self.client(old_profile)
        self.assertTrue(self.https(old, origin), 'Native module H2 did not carry verified HTTPS')
        self.assertTrue(origin.peers and set(origin.peers) == {'127.0.0.1'}, 'Owned fixture origin did not observe tunnel egress')
        self.assertTrue(origin.snis and set(origin.snis) == {'sub.example.org'}, 'HTTPS origin did not receive the verified fixture SNI')
        reset_before = self.records()
        reset = self.device('reset', 'company')
        reset_records = self.records()
        for field in ('uuid', 'subId', 'auth'):
            self.assertTrue(reset_records['company'][field] != reset_before['company'][field], 'Reset must rotate all transport credentials')
        fresh_profile = self.assert_native_exports()
        fresh = self.client(fresh_profile)
        self.assertFalse(self.https(old, origin), 'Old H2 session remained usable after module reset')
        # A new QUIC connection with old credentials distinguishes revocation
        # from a cached session merely becoming stale during the core restart.
        self.assertFalse(self.https(self.client(old_profile), origin), 'Old H2 credentials authenticated on a fresh connection')
        self.assertTrue(self.https(fresh, origin), 'Fresh H2 credentials failed after module reset')
        config = host.read_json(self.folder / 'bin/config.json')
        tcp = next(i for i in config['inbounds'] if i['protocol'] == 'vless')
        self.assertTrue(any(c['id'] == reset_records['company']['uuid'] for c in tcp['settings']['clients']) and
                        all(c['id'] != reset_before['company']['uuid'] for c in tcp['settings']['clients']),
                        'Module reset did not replace TCP authorization alongside H2')
        stable = copy.deepcopy(reset_records)
        self.assertTrue(self.fx.device_prepare(reset)['committed'])
        self.assertTrue(self.records() == stable, 'Replay rerotated native credentials')
        self.device('rename', 'company', 'tablet')
        renamed = self.records()['tablet']
        self.assertTrue({k: v for k, v in renamed.items() if k not in ('email', 'updatedAt')} ==
                        {k: v for k, v in stable['company'].items() if k not in ('email', 'updatedAt')}, 'Rename changed Auth/row/policy')
        # A module transaction restarts the core and established QUIC sessions
        # reconnect. Reuse the identical pre-rename native profile: no refresh,
        # re-import or new credential is permitted to make rename work.
        self.assertTrue(self.https(self.client(fresh_profile), origin), 'Rename revoked stable credentials')
        self.device('remove', 'tablet')
        self.assertFalse(self.https(self.client(fresh_profile), origin), 'Removed H2 credentials authenticated on a fresh connection')
        self.assertTrue('tablet' not in self.records(), 'Removed global client still exists'); self.topology()
        config = host.read_json(self.folder / 'bin/config.json')
        self.assertTrue(all(c.get('email') != 'tablet' for i in config['inbounds']
                            if i.get('protocol') in ('vless', 'hysteria') for c in i['settings']['clients']),
                        'Module remove retained an authenticator on TCP or H2')
        self.assertTrue(committed['committed'])

    def test_legacy_and_dual_archives_attest_auth_and_exact_joins(self):
        _, legacy_stage, legacy = self.archive()
        self.assertFalse(host.dual_enabled(legacy))
        original_sha = host.digest(legacy_stage / 'deployment/db/x-ui.db')
        self.enable()
        _, stage, verified = self.archive()
        self.assertTrue(host.dual_enabled(verified))
        self.assertTrue(host.digest(legacy_stage / 'deployment/db/x-ui.db') == original_sha, 'Legacy archive was mutated')
        archived = self.rows('clients', stage / 'deployment')
        self.assertTrue(all(row['auth'] for row in archived), 'Dual closed snapshot lost native Auth')
        self.assertTrue(len(self.rows('client_inbounds', stage / 'deployment')) == 2 * len(archived))
        closed = stage / 'deployment/db/x-ui.db'
        original = closed.read_bytes()
        with closing(sqlite3.connect(closed)) as db, db:
            db.execute("update clients set auth='' where email='company'")
        with self.assertRaises(host.Error): host.verify_backup(stage, self.fx.lock)
        closed.write_bytes(original)
        with closing(sqlite3.connect(closed)) as db, db:
            db.execute('delete from client_inbounds where rowid=(select rowid from client_inbounds limit 1)')
        with self.assertRaises(host.Error): host.verify_backup(stage, self.fx.lock)

    def test_transport_failure_rolls_back_queued_and_receipt_replay_is_once(self):
        # Exercise a real recognized TCP-era recovery handoff, rather than
        # starting with capable source already installed in the fixture root.
        predecessor = subprocess.check_output(['git', 'show', '6c02d25:vps/lib/host.py'])
        self.assertTrue(hashlib.sha256(predecessor).hexdigest() == host.BASELINE_ADAPTER_SHA256,
                        'Fixture predecessor does not match the reviewed TCP-only adapter')
        host.atomic(host.ROOT / 'host.py', predecessor, 0o700)
        request = self.request(); before = self.records(); joins = self.rows('client_inbounds')
        bound = host.transport_request(request)
        real_enable = host.enable_hysteria
        def partial(*args):
            real_enable(*args)
            raise host.Error('Fixture failure after native H2 creation')
        with mock.patch.object(host, 'enable_hysteria', side_effect=partial):
            with self.assertRaises(host.Error): self.prepare(request)
        # This is the existing fake scheduler, not a real reboot/systemd test.
        self.assertTrue(host.read_json(host.ROOT / 'pending.json')['phase'] == 'restore-queued',
                        'Failure recovery must journal queued restoration before readiness')
        first = host.recover(force=True)
        if first.get('pending'): self.assertTrue(host.recover(force=True).get('restored'))
        self.fx.manifest = host.read_json(self.folder / 'manifest.json')
        self.assertFalse(host.dual_enabled(self.manifest))
        self.assertTrue(self.records() == before and self.rows('client_inbounds') == joins, 'Pre-receipt failure did not restore original native state')
        with self.assertRaises(host.Error): self.prepare(dict(request, before_sha256='0' * 64))
        self.prepare(request)
        self.assertTrue(host.read_json(host.ROOT / 'pending.json')['request_sha256'] == bound,
                        'Rollback retry lost the original pre-change archive binding')
        path, _, _ = self.archive()
        self.assertTrue(host.digest(path) != request['before_sha256'], 'Retry candidate archive must differ from the original TCP backup')
        receipt = dict(request, offnode_sha256=host.digest(path))
        real_atomic = host.atomic
        def lose_ack(path, data, mode=0o600):
            real_atomic(path, data, mode)
            try: value = json.loads(data)
            except (TypeError, ValueError): return
            if isinstance(value, dict) and value.get('committed') is True and value.get('request_sha256'):
                raise OSError('Fixture acknowledgment lost after durable receipt')
        with mock.patch.object(host, 'atomic', side_effect=lose_ack):
            with self.assertRaises(OSError): host.transport_commit(receipt)
        self.assertTrue(host.recover(force=True).get('committed'))
        self.fx.manifest = host.read_json(self.folder / 'manifest.json')
        after = self.records()
        self.assertTrue(host.transport_commit(receipt).get('committed'))
        self.assertTrue(self.prepare(request).get('committed'))
        self.assertTrue(self.records() == after, 'Feature replay changed native Auth or another credential')

    def test_disabled_expired_filter_both_and_restricted_probe_never_relaxes_policy(self):
        self.enable()
        self.seed_policy('home', enable=False)
        self.seed_policy('phone', expiryTime=int(time.time() * 1000) - 60000, reset=0, resetMax=0)
        self.api().call('/panel/api/server/restartXrayService', {})
        config = host.read_json(self.folder / 'bin/config.json')
        for inbound in config['inbounds']:
            if inbound.get('protocol') in ('vless', 'hysteria'):
                self.assertTrue(all(c['email'] not in ('home', 'phone') for c in inbound['settings']['clients']),
                                'Disabled or expired account remained authorized on a transport')
        self.seed_policy('company', limitHwid=3)
        before = self.records()
        with mock.patch.object(host, 'get_feed', side_effect=host.Error('Fixture restricted feed needs authorized device headers')):
            with self.assertRaises(host.Error): host.backend_health(self.folder, self.manifest)
        self.assertTrue(self.records() == before, 'Failed generic feed probe relaxed native policy')
        # Backup must remain possible without a successful generic feed probe.
        self.archive()

    def test_missing_certificate_and_simulated_pair_replacement(self):
        cert, key = self.certificate_paths(self.folder, self.manifest)
        saved = cert.read_bytes(), key.read_bytes()
        cert.unlink(); key.unlink()
        before = self.records()
        with self.assertRaises(host.Error): host.enable_hysteria(self.folder, self.manifest, 22)
        self.assertTrue(len(self.rows('inbounds')) == 1 and self.records() == before, 'Missing cert damaged TCP candidate')
        cert.write_bytes(saved[0]); key.write_bytes(saved[1])
        origin = self.origin(); self.enable()
        profile = self.assert_native_exports(); old_root = self.folder / 'old-trust.pem'; old_root.write_bytes(cert.read_bytes())
        self.assertTrue(self.https(self.client(profile), origin), self.last_https_diagnostic)
        before = self.records(); self.make_certificate(); self.enable()
        self.assertTrue({n: {k: v for k, v in c.items() if k != 'updatedAt'} for n, c in self.records().items()} ==
                        {n: {k: v for k, v in c.items() if k != 'updatedAt'} for n, c in before.items()},
                        'Certificate refresh regenerated account Auth or policy')
        # Explicit module consumer reload, NOT a claim that the hourly builtin
        # ticker or a real Caddy ACME renewal was exercised.
        self.assertTrue(self.https(self.client(profile), origin), 'New consumer did not trust replacement H2 pair: ' + self.last_https_diagnostic)
        self.assertFalse(self.https(self.client(profile, trust=old_root), origin), 'Old trust root accepted replacement H2 certificate')


if __name__ == '__main__':
    unittest.main()
