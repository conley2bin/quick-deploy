"""Host-only CDN invariants. Real files/SQLite/journals, mocked system/native API.

No fixture binds API62789 or starts a server. Native runtime/CF acceptance is a
separate serial lane; these assertions inspect payloads, ownership and recovery.
"""
import base64
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import types
import unittest
from unittest import mock
from urllib.parse import urlencode

import test_dual_transport
import test_remote
import host


def real_function(module, name):
    original = getattr(host, name)
    function = types.FunctionType(original.__code__, module.__dict__)
    function.__defaults__ = original.__defaults__
    return function


class NativeModel:
    """Model supported API boundaries on the existing real closed-DB fixture."""
    def __init__(self, fixture, manifest):
        self.f, self.h, self.manifest = fixture, fixture.h, manifest
        self.database = self.h.ROOT / 'active/db/x-ui.db'
        self.calls, self.records, self.inbounds, self.groups = [], {}, {}, {}
        for index, (name, account) in enumerate(manifest['accounts'].items(), 1):
            self.records[name] = dict(account, id=index, uuid=account['id'], limitHwid=3, totalGB=123456,
                                      expiryTime=1893456000000, limitIp=2, reset=4, createdAt=1800000000123, updatedAt=1800000000123)
        main = dict(host.inbound_for(manifest), id=1, tag='SELF-main', disableFlow=False, policySentinel='preserved')
        active = self.h.ROOT / 'active'
        h2 = dict(host.hysteria_inbound(manifest, active / manifest['hysteria2']['certificate_file'],
                                      active / manifest['hysteria2']['key_file']), id=2)
        main['settings'] = json.dumps({'decryption': 'none', 'clients': self.clients(False), 'fallbacks': []})
        h2['settings'] = json.dumps({'version': 2, 'clients': self.clients(True)})
        self.inbounds = {1: main, 2: h2}
        for identity, group_id in ((1, manifest['host_group_id']), (2, manifest['hysteria2']['host_group_id'])):
            self.groups[group_id] = {'groupId': group_id, 'inboundIds': [identity],
                'hosts': [manifest['endpoint'] + ':443'], 'port': 443, 'security': 'same', 'sni': '',
                'hostHeader': '', 'path': '', 'alpn': [], 'allowInsecure': False, 'tags': ['owned-policy'], 'remark': '',
                'pinnedPeerCertSha256': [], 'isDisabled': False, 'isHidden': False}
        with sqlite3.connect(self.database) as db:
            for definition in ('enable integer', 'listen text', 'tag text', 'settings text', 'stream_settings text', 'disable_flow integer'):
                db.execute('alter table inbounds add column ' + definition)
            for definition in ('security text', 'sni text', 'host_header text', 'path text', 'alpn text', 'allow_insecure integer',
                               'pinned_peer_cert_sha256 text', 'verify_peer_cert_by_name text', 'exclude_from_sub_types text'):
                db.execute('alter table hosts add column ' + definition)
            db.execute('create table client_traffics(email text)')
            db.execute('create table inbound_client_ips(client_email text)')
        template = {'outbounds': [{'tag': 'direct', 'protocol': 'freedom'}, {'tag': 'blocked', 'protocol': 'blackhole'}]}
        template['routing'] = {'rules': [host.management_rule(template, manifest, 'SELF-h2')]}
        self.h.settings_write(self.database, {'xrayTemplateConfig': json.dumps(template)})
        self.sync()

    def clients(self, h2=False):
        result = []
        for name, record in self.records.items():
            item = {k: v for k, v in record.items() if k not in ('id', 'uuid', 'auth', 'createdAt', 'updatedAt')}
            item.update(id=record['uuid'], created_at=1800000000000)
            if h2: item['auth'] = record['auth']
            result.append(item)
        return result

    def sync(self):
        with sqlite3.connect(self.database) as db:
            for identity, inbound in self.inbounds.items():
                db.execute('update inbounds set remark=?,protocol=?,port=?,enable=?,listen=?,tag=?,settings=?,stream_settings=?,disable_flow=? where id=?',
                    (inbound['remark'], inbound['protocol'], inbound['port'], inbound['enable'], inbound['listen'], inbound['tag'],
                     inbound['settings'], inbound['streamSettings'], inbound.get('disableFlow', False), identity))
            for group in self.groups.values():
                address, port = group['hosts'][0].rsplit(':', 1)
                db.execute('update hosts set address=?,port=?,security=?,sni=?,host_header=?,path=?,alpn=?,allow_insecure=?,pinned_peer_cert_sha256=?,exclude_from_sub_types=? where group_id=?',
                    (address, int(port), group['security'], group['sni'], group['hostHeader'], group['path'], json.dumps(group['alpn']),
                     group['allowInsecure'], json.dumps(group.get('pinnedPeerCertSha256', [])), 'null', group['groupId']))
            for name, record in self.records.items():
                db.execute('update clients set uuid=?,sub_id=?,enable=?,flow=?,auth=? where email=?',
                           (record['uuid'], record['subId'], record['enable'], record['flow'], record['auth'], name))

    def call(self, path, data=None):
        self.calls.append((path, copy.deepcopy(data)))
        if path == '/panel/api/inbounds/list': return copy.deepcopy(list(self.inbounds.values()))
        if path == '/panel/api/hosts/list': return copy.deepcopy(list(self.groups.values()))
        if path.startswith('/panel/api/hosts/get/'): return copy.deepcopy(self.groups[path.rsplit('/', 1)[1]])
        if path.startswith('/panel/api/clients/get/'): return {'client': copy.deepcopy(self.records[path.rsplit('/', 1)[1]])}
        if path.startswith('/panel/api/hosts/update/'):
            self.groups[path.rsplit('/', 1)[1]] = copy.deepcopy(data)
        elif path.startswith('/panel/api/inbounds/update/'):
            identity = int(path.rsplit('/', 1)[1])
            self.inbounds[identity] = copy.deepcopy(data)
            if data.get('disableFlow'):
                settings = json.loads(self.inbounds[identity]['settings'])
                for client in settings['clients']: client['flow'] = ''
                self.inbounds[identity]['settings'] = json.dumps(settings)
                for record in self.records.values(): record['flow'] = ''; record['createdAt'] = 1800000000000
        elif path == '/panel/api/clients/add':
            account = data['client']; name = account['email']; identity = max(r['id'] for r in self.records.values()) + 1
            self.records[name] = dict(account, id=identity, uuid=account['id'], limitHwid=0, createdAt=1800000000000, updatedAt=1800000000000)
            with sqlite3.connect(self.database) as db:
                db.execute('insert into clients(email,uuid,sub_id,enable,flow,id,auth) values(?,?,?,?,?,?,?)',
                    (name, account['id'], account['subId'], account['enable'], account['flow'], identity, account['auth']))
                db.executemany('insert into client_inbounds values(?,?)', [(identity, i) for i in data['inboundIds']])
            self.refresh_clients()
        elif path.startswith('/panel/api/clients/update/'):
            name = path.rsplit('/', 1)[1]; record = self.records.pop(name)
            desired_name = data['email']
            record.update({k: v for k, v in data.items() if k not in ('id', 'created_at')})
            record['uuid'] = data['id']; self.records[desired_name] = record
            with sqlite3.connect(self.database) as db: db.execute('update clients set email=? where email=?', (desired_name, name))
            self.refresh_clients()
        elif path.startswith('/panel/api/clients/del/'):
            record = self.records.pop(path.rsplit('/', 1)[1])
            with sqlite3.connect(self.database) as db:
                db.execute('delete from client_inbounds where client_id=?', (record['id'],))
                db.execute('delete from clients where id=?', (record['id'],))
            self.refresh_clients()
        elif path == '/panel/api/server/restartXrayService': return None
        elif path == '/panel/api/server/status':
            return {'xray': {'state': 'running', 'version': self.h.read_json(self.h.ROOT / 'active/release-lock.json')['core']['version']}}
        else: raise AssertionError('Unmodeled native API: ' + path)
        self.sync()
        return None

    def refresh_clients(self):
        for identity, inbound in self.inbounds.items():
            settings = json.loads(inbound['settings'])
            settings['clients'] = self.clients(identity == 2)
            inbound['settings'] = json.dumps(settings)


class CdnHost(unittest.TestCase):
    def setUp(self):
        self.base = test_dual_transport.FeatureTransaction('runTest')
        self.base.setUp(); self.addCleanup(self.base.doCleanups)
        self.f, self.h = self.base.f, self.base.h
        self.h.transport_prepare(self.base.upload())
        self.h.atomic(self.h.ROOT / 'export-sha256', 'b' * 64)
        self.h.transport_commit(dict(self.base.request, offnode_sha256='b' * 64))
        self.manifest = self.h.read_json(self.h.ROOT / 'active/manifest.json')
        self.active = self.h.ROOT / 'active'
        self.model = NativeModel(self.f, self.manifest)
        self.h.API.return_value = self.model; self.h.wait_api.return_value = self.model
        self.h.owned_host_group.side_effect = real_function(self.h, 'owned_host_group')
        self.f.patch(self.h, 'core_check')
        self.f.patch(self.h, 'hysteria_health', side_effect=real_function(self.h, 'hysteria_health'))
        self.f.patch(self.h, 'check_cdn_dns')
        self.f.patch(self.h, 'tls_hostname_probe')
        self.f.patch(self.h, 'certificate_paths', side_effect=lambda folder, domain, metadata=None:
            ((Path(folder) / 'caddy/data/caddy/certificates/fixture' / (domain + '.crt')).absolute(),
             (Path(folder) / 'caddy/data/caddy/certificates/fixture' / (domain + '.key')).absolute()))
        self.h.backend_health.side_effect = real_function(self.h, 'backend_health')
        def emitted(manifest, device, fmt):
            view = types.SimpleNamespace(account=manifest['accounts'][device])
            text = (CdnFeeds.uri(view) if fmt == 'sub' else CdnFeeds.yaml(view)).replace('93.184.216.34', manifest['endpoint'])
            return base64.b64encode(text.encode()) if fmt == 'sub' else text.encode()
        self.f.patch(self.h, 'get_feed', side_effect=emitted)
        self.h.front_config.side_effect = lambda folder, manifest, published, bootstrap_domain=None: self.h.atomic(
            folder / 'Caddyfile', self.h.caddyfile(manifest, published, bootstrap_domain))
        self.store = self.active / 'caddy'
        self.h.atomic(self.store / 'data/caddy/certificates/fixture/sub.example.org.crt', b'original-sub-cert')
        self.h.atomic(self.store / 'data/caddy/certificates/fixture/sub.example.org.key', b'original-sub-key')
        self.h.atomic(self.store / 'data/caddy/acme/account.json', b'original-account-renewal')
        self.h.atomic(self.store / 'config/caddy/autosave.json', b'original-autosave')
        self.before = {str(p.relative_to(self.store)): p.read_bytes() for p in self.store.rglob('*') if p.is_file()}
        self.h.atomic(self.h.ROOT / 'export-sha256', 'c' * 64)
        self.request = dict(self.base.request, operation_id='2' * 32, before_sha256='c' * 64,
            feature='tcp-cdn', source_transports=['tcp-reality', 'hysteria2'], target_transports=['tcp-xhttp-cdn', 'hysteria2'],
            tcp_cdn={'schema': 1, 'domain': 'cdn.example.org'})
        self.original = (self.active / 'manifest.json').read_bytes()

    def prepare(self):
        return self.h.transport_prepare(self.base.upload(self.request))

    def proof_request(self, candidate='d' * 64):
        proof = {'schema': 1, 'operation_id': self.request['operation_id'], 'request_fingerprint': self.h.transport_request(self.request),
            'candidate_sha256': candidate, 'domain': 'cdn.example.org', 'device_identity_sha256': 'e' * 64,
            'observed_at': datetime.now(timezone.utc).isoformat(), 'interface': 'eno1', 'edge_ips': ['104.16.1.1'],
            'checks': {key: True for key in ('edge_tls', 'edge_cloudflare', 'xhttp_https', 'vps_egress',
                      'wrong_uuid_rejected', 'valid_again', 'reconnect', 'hysteria2_preserved')}}
        return dict(self.request, offnode_sha256=candidate, cdn_proof=proof, cdn_proof_sha256=self.h.cdn_proof_digest(proof))

    def test_exact_modes_metadata_and_invalid_extra_topology(self):
        self.assertFalse(host.cdn_enabled(self.manifest)); self.assertTrue(host.dual_enabled(self.manifest))
        self.prepare(); current = self.h.read_json(self.active / 'manifest.json')
        self.assertTrue(host.cdn_enabled(current)); self.assertTrue(host.dual_enabled(current))
        self.assertEqual(host.main_tag(current), 'SELF-cdn')
        self.assertEqual(host.transports(current), ['tcp-xhttp-cdn', 'hysteria2'])
        for mutate in (lambda m: m.pop('hysteria2'), lambda m: m['tcp_cdn'].update(extra=True),
                       lambda m: m['tcp_cdn'].update(mode='auto'), lambda m: m['tcp_cdn'].update(path='/other'),
                       lambda m: m['tcp_cdn'].update(schema=True), lambda m: m['tcp_cdn'].update(inbound_id=2),
                       lambda m: m['config'].update(proxy_port=2080)):
            invalid = copy.deepcopy(current); mutate(invalid)
            with self.assertRaises(host.Error): host.cdn_enabled(invalid)
        self.assertEqual(host.snapshot_accounts(self.model.database, current), current['accounts'])
        self.assertEqual(host.snapshot_host_group(self.model.database, current), self.manifest['host_group_id'])
        for statement in ("insert into hosts(group_id,inbound_id,address,port,is_hidden) values('hidden',1,'cdn.example.org',443,1)",
                          'insert into client_inbounds values(1,1)', "update inbounds set listen='0.0.0.0' where id=1",
                          "update hosts set sni='foreign.example.org' where inbound_id=1",
                          "update hosts set pinned_peer_cert_sha256='[\"custom\"]' where inbound_id=1"):
            saved = self.model.database.read_bytes()
            with sqlite3.connect(self.model.database) as db: db.execute(statement)
            with self.assertRaises(host.Error): host.snapshot_accounts(self.model.database, current)
            self.model.database.write_bytes(saved)

    def test_in_place_payload_full_policies_hosts_h2_and_two_joins(self):
        h2 = copy.deepcopy(self.model.inbounds[2]); h2group = copy.deepcopy(self.model.groups[self.manifest['hysteria2']['host_group_id']])
        records = copy.deepcopy(self.model.records)
        with sqlite3.connect(self.model.database) as db: joins = db.execute('select * from client_inbounds').fetchall()
        result = self.prepare()
        self.assertTrue(result['prepared']); self.assertGreater(result['deadline'], 0)
        current = self.h.read_json(self.active / 'manifest.json')
        self.assertEqual(current['tcp_cdn']['inbound_id'], 1)
        self.assertEqual(current['host_group_id'], self.manifest['host_group_id'])
        self.assertEqual(self.model.inbounds[2], h2); self.assertEqual(self.model.groups[h2group['groupId']], h2group)
        self.assertEqual(current['hysteria2'], self.manifest['hysteria2'])
        with sqlite3.connect(self.model.database) as db:
            self.assertEqual(db.execute('select * from client_inbounds').fetchall(), joins)
            self.assertEqual(db.execute('select count(*) from inbounds').fetchone()[0], 2)
            self.assertEqual(db.execute('select count(*) from hosts').fetchone()[0], 2)
        updates = [(path, data) for path, data in self.model.calls if data is not None]
        self.assertFalse(any('/attach' in path or path.endswith('/add') for path, _ in updates))
        payload = next(data for path, data in updates if path == '/panel/api/inbounds/update/1')
        self.assertIs(payload['disableFlow'], True)
        self.assertEqual(payload['policySentinel'], 'preserved')
        self.assertEqual(json.loads(payload['settings'])['fallbacks'], [])
        for name, record in records.items():
            self.assertEqual({k: v for k, v in self.model.records[name].items() if k not in ('flow', 'createdAt')},
                             {k: v for k, v in record.items() if k not in ('flow', 'createdAt')})
        host.cdn_host_health(self.model.groups[current['host_group_id']], current)
        self.assertFalse(current['published'])
        self.assertNotIn('@feed', (self.active / 'Caddyfile').read_text())

    def test_source_transport_and_original_h2_negative_fields_refuse_before_intent(self):
        original = copy.deepcopy(self.model.inbounds)
        for mutate in (lambda: self.model.inbounds[1].update(listen='127.0.0.1'),
                       lambda: self.model.inbounds[1].update(streamSettings=json.dumps({'network': 'xhttp', 'security': 'none',
                           'externalProxy': [{'forceTls': 'same', 'dest': self.manifest['endpoint'], 'port': 443, 'remark': ''}]})),
                       lambda: self.model.inbounds[2].update(settings=json.dumps({'version': 2, 'clients': []})),
                       lambda: self.model.inbounds[2].update(streamSettings=json.dumps(dict(
                           json.loads(original[2]['streamSettings']), tlsSettings={'serverName': 'cdn.example.org', 'alpn': ['h3']})))):
            self.model.inbounds = copy.deepcopy(original); mutate(); self.model.sync()
            with mock.patch.object(self.h, 'atomic', wraps=self.h.atomic) as writes:
                with self.assertRaises(self.h.Error): self.prepare()
                writes.assert_not_called()
            self.assertFalse((self.h.ROOT / 'pending.json').exists())
        self.model.inbounds = original; self.model.sync()

    def test_front_bootstrap_final_path_and_caddy_never_udp443(self):
        staging = host.caddyfile(self.manifest, False, bootstrap_domain='cdn.example.org')
        self.assertIn('https://cdn.example.org:8443', staging); self.assertNotIn('https://cdn.example.org:443', staging)
        self.assertNotIn('reverse_proxy 127.0.0.1:2080', staging)
        self.prepare(); current = self.h.read_json(self.active / 'manifest.json')
        front = host.caddyfile(current, True)
        self.assertIn('protocols h1 h2\n', front); self.assertNotIn('h3', front)
        self.assertIn('https://cdn.example.org:443', front); self.assertIn('https://sub.example.org:8443', front)
        self.assertIn('http://sub.example.org:80', front); self.assertIn('flush_interval -1', front)
        self.assertIn('@xhttp path /qd-xhttp/ /qd-xhttp/*', front)
        self.assertNotIn('handle_path', front); self.assertNotIn('uri strip', front)
        self.assertEqual(host.tcp_owner(current, 443), (host.FRONT, 'caddy'))
        self.assertEqual(host.tcp_owner(current, 2080), (host.PANEL, 'xray-linux-amd64'))
        self.assertEqual(host.tcp_owner(self.manifest, 443), (host.PANEL, 'xray-linux-amd64'))
        self.assertNotIn(2080, host.required_ports(self.manifest['config']))
        self.assertIn(2080, host.required_ports(current['config'], current))
        inline = json.loads(host.clash_rules(current))
        self.assertEqual(inline['proxy-groups'][0]['proxies'], ['SELF-h2', 'SELF-cdn', 'DIRECT'])
        self.assertEqual(inline['proxy-groups'][1]['proxies'], ['SELF-h2', 'DIRECT'])
        self.assertEqual(inline['rules'][:2], json.loads(host.clash_rules(self.manifest))['rules'][:2])
        self.assertEqual(inline['rules'][2], 'DOMAIN,cdn.example.org,DIRECT')

    def test_conflicting2080_before_intent_and_current_guard_before_mutation(self):
        original_run = self.h.run.side_effect
        self.h.run.side_effect = lambda argv, **kw: subprocess.CompletedProcess(argv, 0,
            b'LISTEN 0 8 127.0.0.1:2080 0.0.0.0:* users:(("foreign",pid=1,fd=1))\n') if argv == ['ss', '-H', '-ltnp'] else original_run(argv, **kw)
        with mock.patch.object(self.h, 'atomic', wraps=self.h.atomic) as writes:
            with self.assertRaisesRegex(self.h.Error, '2080'): self.prepare()
            writes.assert_not_called()
        self.assertFalse((self.h.ROOT / 'pending.json').exists()); self.assertFalse((self.h.ROOT / 'device-rollback').exists())
        self.h.run.side_effect = original_run
        predecessor = self.base.old_adapter
        self.h.atomic(self.h.ROOT / 'host.py', predecessor, 0o700)
        with mock.patch.object(self.h, 'atomic', wraps=self.h.atomic) as writes:
            with self.assertRaisesRegex(self.h.Error, 'Current recovery adapter'): self.prepare()
            writes.assert_not_called()

    def test_fingerprints_old_bytes_and_new_exact_proposal_source_target(self):
        old = self.base.request
        keys = ('owner', 'machine_id', 'endpoint', 'config', 'operation_id', 'adapter_sha256', 'ssh_port', 'mode', 'before_sha256')
        self.assertEqual(host.transport_request(old), hashlib.sha256(host.dumps({k: old[k] for k in keys}).encode()).hexdigest())
        binding = host.transport_request(self.request)
        changed = copy.deepcopy(self.request); changed['tcp_cdn']['domain'] = 'other.example.org'
        self.assertNotEqual(binding, host.transport_request(changed))
        for change in ({'feature': 'unknown'}, {'source_transports': ['tcp-reality']}, {'target_transports': ['tcp-reality', 'hysteria2']},
                       {'before_sha256': ''}, {'tcp_cdn': {'schema': 1, 'domain': 'cdn.example.org', 'path': '/other'}}):
            with self.assertRaises(host.Error): host.transport_request(dict(self.request, **change))

    def test_proof_exact_binding_distinct_candidate_and_lost_ack(self):
        self.prepare(); self.h.atomic(self.h.ROOT / 'export-sha256', 'd' * 64)
        request = self.proof_request()
        for field, bad in (('operation_id', '3' * 32), ('request_fingerprint', '0' * 64), ('candidate_sha256', 'c' * 64),
                           ('domain', 'other.example.org'), ('schema', True), ('observed_at', 'not-UTC'),
                           ('edge_ips', ['127.0.0.1']), ('checks', {k: k != 'wrong_uuid_rejected' for k in request['cdn_proof']['checks']})):
            invalid = copy.deepcopy(request); invalid['cdn_proof'][field] = bad
            invalid['cdn_proof_sha256'] = host.cdn_proof_digest(invalid['cdn_proof'])
            with mock.patch.object(self.h, 'front_config', wraps=self.h.front_config) as front:
                with self.assertRaises(self.h.Error): self.h.transport_commit(invalid)
                front.assert_not_called()
        invalid = dict(request, cdn_proof_sha256='0' * 64)
        with self.assertRaisesRegex(self.h.Error, 'digest'): self.h.transport_commit(invalid)
        before = self.proof_request('c' * 64)
        self.h.atomic(self.h.ROOT / 'export-sha256', 'c' * 64)
        with self.assertRaisesRegex(self.h.Error, 'new dual-state backup'): self.h.transport_commit(before)
        self.h.atomic(self.h.ROOT / 'export-sha256', 'd' * 64)
        committed = self.h.transport_commit(request)
        self.assertTrue(committed['committed']); self.assertEqual(committed['transports'], self.request['target_transports'])
        self.assertEqual(committed['cdn_proof'], request['cdn_proof'])
        self.assertEqual(committed['cdn_proof_sha256'], request['cdn_proof_sha256'])
        calls = len(self.model.calls)
        self.assertEqual(self.h.transport_commit(request), committed)
        self.assertEqual(self.h.abort(self.request), committed)
        self.assertEqual(len(self.model.calls), calls, 'Lost ACK must not replay native mutation or rotate Auth')
        with self.assertRaises(self.h.Error): self.h.transport_commit(invalid)

    def test_new_feature_dual_source_is_not_false_noop(self):
        result = self.prepare()
        self.assertNotIn('unchanged', result)
        self.assertEqual(result['transports'], ['tcp-xhttp-cdn', 'hysteria2'])
        self.assertEqual(result, self.prepare())

    def test_candidate_archive_contains_target_and_closed_store_not_before_topology(self):
        self.prepare()
        archive = self.h.export_backup()
        stage = self.f.root / 'verified-cdn-archive'; stage.mkdir()
        with archive.open('rb') as stream: self.h.safe_unpack(stream, stage)
        manifest = self.h.verify_backup(stage, self.f.request['lock'])
        self.assertEqual(host.transports(manifest), ['tcp-xhttp-cdn', 'hysteria2'])
        self.assertEqual(manifest['tcp_cdn']['domain'], 'cdn.example.org')
        for relative, content in self.before.items():
            self.assertEqual((stage / 'deployment/caddy' / relative).read_bytes(), content)
        self.assertEqual(host.snapshot_accounts(stage / 'deployment/db/x-ui.db', manifest), manifest['accounts'])
        front = stage / 'deployment/Caddyfile'; original = front.read_bytes()
        front.write_bytes(original.replace(b'https://cdn.example.org:443', b'https://cdn.example.org:8443'))
        with self.assertRaisesRegex(self.h.Error, 'front configuration'): self.h.verify_backup(stage, self.f.request['lock'])

    def test_prepare_cold_cdn_restore_replace_preserves_mode_and_archive(self):
        self.prepare(); self.h.atomic(self.active / 'bin/config.json', '{}')
        archive = self.f.root / 'source-cdn.tar'; archive.write_bytes(self.h.export_backup().read_bytes())
        source_sha = host.digest(archive)
        original = self.h.read_json(self.active / 'manifest.json')
        model = {key: copy.deepcopy(getattr(self.model, key)) for key in ('records', 'inbounds', 'groups')}
        self.h.configure_native.side_effect = real_function(self.h, 'configure_native')
        self.h.enable_hysteria.side_effect = real_function(self.h, 'enable_hysteria')
        old_install = self.h.install_runtime.side_effect
        def install(folder, lock):
            old_install(folder, lock); self.model.database = folder / 'db/x-ui.db'
        self.h.install_runtime.side_effect = install
        self.h.check_target.reset_mock()
        for mode, endpoint in (('restore', '93.184.216.35'), ('replace', '93.184.216.36')):
            for key, value in model.items(): setattr(self.model, key, copy.deepcopy(value))
            self.model.calls.clear()
            target = self.f.root / ('fresh-' + mode); units = self.f.root / ('new-units-' + mode); units.mkdir()
            request = dict(self.f.request, mode=mode, endpoint=endpoint)
            with mock.patch.object(self.h, 'ROOT', target), mock.patch.object(self.h, 'UNITS', units), \
                    mock.patch.dict(os.environ, QD_SSH_CONNECTION='198.51.100.1 12345 ' + endpoint + ' 22'):
                import vps
                with vps.upload_bundle(request, backup=archive) as stream: self.h.prepare(stream)
                restored = self.h.read_json(target / 'active/manifest.json')
                self.assertEqual(restored['tcp_cdn'], original['tcp_cdn'])
                self.assertEqual(restored['hysteria2'], original['hysteria2'])
                self.assertEqual(restored['endpoint'], endpoint)
                self.assertEqual(self.model.groups[restored['host_group_id']]['hosts'], ['cdn.example.org:443'])
                self.assertEqual(self.model.groups[restored['hysteria2']['host_group_id']]['hosts'], [endpoint + ':443'])
                for name, account in original['accounts'].items():
                    after = restored['accounts'][name]
                    self.assertEqual(after['id'] == account['id'], mode == 'restore')
                    self.assertEqual(after['auth'] == account['auth'], mode == 'restore')
                    self.assertEqual(after['subId'], account['subId'])
                self.assertEqual(len([p for p, _ in self.model.calls if '/panel/api/clients/update/' in p]),
                                 len(original['accounts']) if mode == 'replace' else 0)
        self.assertEqual(host.digest(archive), source_sha)
        self.h.check_target.assert_not_called()

    def test_closed_store_snapshot_before_bootstrap_and_exact_rollback(self):
        original_bootstrap = self.h.bootstrap_cdn_certificate
        def issuance(folder, manifest, domain):
            pending = self.h.read_json(self.h.ROOT / 'pending.json')
            self.assertEqual(pending['phase'], 'ready'); self.assertIs(pending['caddy_snapshot'], True)
            saved = self.h.ROOT / 'device-rollback/caddy'
            self.assertEqual({str(p.relative_to(saved)): p.read_bytes() for p in saved.rglob('*') if p.is_file()}, self.before)
            self.h.atomic(self.store / 'data/caddy/certificates/fixture/cdn.example.org.crt', b'new-cdn')
            self.h.atomic(self.store / 'data/caddy/certificates/fixture/cdn.example.org.key', b'new-cdn-key')
            return original_bootstrap(folder, manifest, domain)
        with mock.patch.object(self.h, 'bootstrap_cdn_certificate', side_effect=issuance): self.prepare()
        # Separate scenarios: valid renewal bytes, partial pair, duplicate path,
        # and access-mode drift all restore the closed ORIGINAL tree, not guesses.
        self.h.atomic(self.store / 'data/caddy/certificates/fixture/sub.example.org.crt', b'half-renewed')
        self.h.atomic(self.store / 'data/caddy/certificates/duplicate/sub.example.org.crt', b'duplicate')
        (self.store / 'data/caddy/certificates/fixture/sub.example.org.key').chmod(0o000)
        self.f.events.clear()
        result = self.h.abort(self.request)
        self.assertTrue(result['queued'])
        self.assertEqual(self.f.events[:2], [('stop', self.h.FRONT), ('stop', self.h.PANEL)])
        self.assertEqual((self.active / 'manifest.json').read_bytes(), self.original)
        self.assertNotIn('https://cdn.example.org:443', (self.active / 'Caddyfile').read_text())
        self.assertEqual({str(p.relative_to(self.store)): p.read_bytes() for p in self.store.rglob('*') if p.is_file()}, self.before)
        for path in self.store.rglob('*'): self.assertEqual(path.stat().st_mode & 0o777, 0o600 if path.is_file() else 0o700)
        self.assertFalse(self.h.abort(self.request)['pending'])
        self.h.hysteria_health.assert_called()

    def publish(self):
        self.prepare(); self.h.atomic(self.h.ROOT / 'export-sha256', 'd' * 64)
        self.h.transport_commit(self.proof_request())

    def test_add_rename_reset_remove_single_authority_and_two_joins(self):
        self.publish()
        original = copy.deepcopy(self.model.records['company'])
        for index, (action, names) in enumerate((('add', ['tablet']), ('rename', ['company', 'work']),
                                                ('reset', ['work']), ('remove', ['tablet'])), 3):
            request = {k: v for k, v in self.base.request.items() if k not in ('mode', 'before_sha256')}
            request.update(device_action=action, names=names, operation_id=str(index) * 32)
            self.h.devices_prepare(self.base.upload(request))
            current = self.h.read_json(self.active / 'manifest.json')
            with sqlite3.connect(self.model.database) as db:
                joins = db.execute('select client_id,inbound_id from client_inbounds').fetchall()
                self.assertEqual(len(joins), 2 * len(current['accounts']))
                self.assertEqual({i for _, i in joins}, {1, 2})
            if action == 'add':
                self.assertEqual(current['accounts']['tablet']['flow'], '')
                self.assertTrue(current['accounts']['tablet']['auth'])
            elif action == 'rename':
                renamed = self.model.records['work']
                self.assertNotIn('company', self.model.records)
                self.assertEqual({k: v for k, v in renamed.items() if k != 'email'}, {k: v for k, v in original.items() if k != 'email'})
            elif action == 'reset':
                reset = self.model.records['work']
                self.assertNotEqual(reset['uuid'], original['uuid']); self.assertNotEqual(reset['auth'], original['auth'])
                self.assertNotEqual(reset['subId'], original['subId'])
                for field in ('limitHwid', 'totalGB', 'expiryTime', 'limitIp', 'reset', 'id'):
                    self.assertEqual(reset[field], original[field])
            else:
                self.assertNotIn('tablet', self.model.records); self.assertNotIn('tablet', current['accounts'])
            self.h.atomic(self.h.ROOT / 'export-sha256', str(index) * 64)
            self.h.devices_commit(dict(request, offnode_sha256=str(index) * 64))
        self.assertEqual(self.h.read_json(self.active / 'manifest.json')['tcp_cdn']['inbound_id'], 1)

    def test_cold_configure_preserves_cdn_host_mode_and_rotates_once_only_on_replace(self):
        self.prepare(); source = self.h.read_json(self.active / 'manifest.json')
        (self.active / 'bin/config.json').write_text('{}')
        main_group = copy.deepcopy(self.model.groups[source['host_group_id']])
        before = copy.deepcopy(self.model.records)
        target = copy.deepcopy(source); target['endpoint'] = '93.184.216.35'
        real_function(self.h, 'configure_native')(self.active, target, fresh=False, rotate=False)
        self.assertEqual(self.model.groups[target['host_group_id']], main_group)
        self.assertEqual(self.model.records, before)
        self.assertEqual(self.model.groups[target['hysteria2']['host_group_id']]['hosts'], ['93.184.216.35:443'])
        self.assertEqual(json.loads(self.model.inbounds[2]['streamSettings'])['externalProxy'][0]['dest'], '93.184.216.35')
        self.assertEqual(json.loads(self.model.inbounds[2]['streamSettings'])['tlsSettings']['certificates'],
            [{'certificateFile': str(self.active / target['hysteria2']['certificate_file']),
              'keyFile': str(self.active / target['hysteria2']['key_file']), 'oneTimeLoading': False}])
        self.h.atomic(self.active / 'manifest.json', self.h.dumps(target))
        self.model.calls.clear()
        real_function(self.h, 'configure_native')(self.active, target, fresh=False, rotate=True)
        self.assertEqual(self.model.groups[target['host_group_id']], main_group)
        for name, record in before.items():
            after = self.model.records[name]
            self.assertNotEqual(after['uuid'], record['uuid']); self.assertNotEqual(after['auth'], record['auth'])
            for key in ('subId', 'limitHwid', 'limitIp', 'totalGB', 'expiryTime', 'enable'):
                self.assertEqual(after[key], record[key])
        updates = [p for p, _ in self.model.calls if '/panel/api/clients/update/' in p]
        self.assertEqual(len(updates), len(before))

    def test_snapshot_crash_before_ready_ignores_incomplete_caddy_copy(self):
        def hard_loss(source, target):
            target.mkdir(mode=0o700)
            self.h.atomic(target / 'poison', b'never-restore-partial')
            os._exit(77)
        pid = os.fork()
        if pid == 0:
            with mock.patch.object(self.h, 'copy_caddy_tree', side_effect=hard_loss): self.prepare()
            os._exit(99)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 77)
        journal = self.h.read_json(self.h.ROOT / 'pending.json')
        self.assertEqual(journal['phase'], 'snapshot'); self.assertNotIn('caddy_snapshot', journal)
        self.assertTrue(self.h.abort(self.request)['queued'])
        self.assertEqual({str(p.relative_to(self.store)): p.read_bytes() for p in self.store.rglob('*') if p.is_file()}, self.before)
        self.assertFalse(self.h.abort(self.request)['pending'])

    def test_recovery_h2_binding_auth_failure_keeps_pending_and_consumers_stopped(self):
        self.prepare(); self.assertTrue(self.h.abort(self.request)['queued'])
        h2 = json.loads(self.model.inbounds[2]['settings']); h2['clients'][0]['auth'] = 'wrong'
        self.model.inbounds[2]['settings'] = json.dumps(h2)
        with self.assertRaisesRegex(self.h.Error, 'authorization'): self.h.abort(self.request)
        self.assertTrue((self.h.ROOT / 'pending.json').exists())
        self.assertNotIn(self.h.FRONT, self.f.services); self.assertNotIn(self.h.PANEL, self.f.services)
        self.assertTrue((self.h.ROOT / 'device-rollback/caddy').is_dir())

    def test_typed_runtime_sockets_and_preflight_do_not_adopt_proposed_caddy_owner(self):
        self.prepare(); current = self.h.read_json(self.active / 'manifest.json')
        lines = []
        for index, port in enumerate(host.required_ports(current['config'], current), 10):
            unit, binary = host.tcp_owner(current, port)
            proc = self.f.root / 'proc' / str(index); proc.mkdir(exist_ok=True)
            (proc / 'cgroup').write_text('0::/system.slice/' + unit)
            (proc / 'exe').symlink_to(self.active / 'bin' / binary)
            address = '127.0.0.1' if port in (host.ADMIN_PORT, host.SUB_PORT, host.API_PORT, host.XHTTP_PORT) else '0.0.0.0'
            lines.append(f'LISTEN 0 8 {address}:{port} 0.0.0.0:* users:(("native",pid={index},fd=1))')
        udp_pid = self.f.root / 'proc/99'; udp_pid.mkdir()
        (udp_pid / 'cgroup').write_text('0::/system.slice/' + host.PANEL)
        (udp_pid / 'exe').symlink_to(self.active / 'bin/xray-linux-amd64')
        udp = b'UNCONN 0 0 0.0.0.0:443 0.0.0.0:* users:(("xray",pid=99,fd=1))\n'
        old_run = self.h.run.side_effect
        def inventory(argv, **kwargs):
            if argv == ['ss', '-H', '-ltnp']: return subprocess.CompletedProcess(argv, 0, ('\n'.join(lines) + '\n').encode())
            if argv == ['ss', '-H', '-lunp']: return subprocess.CompletedProcess(argv, 0, udp)
            return old_run(argv, **kwargs)
        self.h.run.side_effect = inventory
        self.f.process_health()
        backend = next(i for i, line in enumerate(lines) if '127.0.0.1:2080 ' in line)
        original = lines[backend]; lines[backend] = original.replace('127.0.0.1:2080', '0.0.0.0:2080')
        with self.assertRaisesRegex(self.h.Error, 'escaped loopback'): self.f.process_health()
        lines[backend] = original
        # Caddy UDP443 is a different socket and is never accepted as H2.
        (udp_pid / 'cgroup').write_text('0::/system.slice/' + host.FRONT)
        with self.assertRaisesRegex(self.h.Error, 'Hysteria2 UDP'): self.f.process_health()
        (udp_pid / 'cgroup').write_text('0::/system.slice/' + host.PANEL)
        self.h.disarm(self.h.ROOT / 'pending.json')
        self.assertTrue(self.h.preflight(self.request)['owned'])
        source = copy.deepcopy(self.manifest); self.h.atomic(self.active / 'manifest.json', self.h.dumps(source))
        with self.assertRaisesRegex(self.h.Error, 'Foreign process|2080'): self.h.preflight(self.request)


class LegacyRestoreMode(unittest.TestCase):
    def test_old_archive_restore_and_replace_do_not_implicitly_add_h2(self):
        fixture = test_remote.FakeHost('runTest'); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        fixture.test_offnode_backup_replace_and_controlled_restore_preserve_owned_state()
        self.assertEqual(fixture.h.enable_hysteria.call_count, 1, 'Only the original fresh deploy opts into H2 by default')
        for mode in ('restore', 'replace'):
            restored = fixture.h.read_json(fixture.root / 'var/lib' / ('new-' + mode) / 'active/manifest.json')
            self.assertEqual(host.transports(restored), ['tcp-reality'])
            self.assertNotIn('hysteria2', restored); self.assertNotIn('tcp_cdn', restored)


class CdnFeeds(unittest.TestCase):
    def setUp(self):
        self.fx = test_dual_transport.DualSnapshot('runTest'); self.fx.setUp(); self.addCleanup(self.fx.doCleanups)
        self.fx.dualize(); self.manifest = copy.deepcopy(self.fx.manifest)
        self.manifest['tcp_cdn'] = {'schema': 1, 'domain': 'cdn.example.org', 'inbound_id': 1, 'path': host.XHTTP_PATH,
            'mode': host.XHTTP_MODE, 'certificate_file': 'caddy/data/caddy/certificates/issuer/cdn.example.org.crt',
            'key_file': 'caddy/data/caddy/certificates/issuer/cdn.example.org.key'}
        for account in self.manifest['accounts'].values(): account['flow'] = ''
        self.account = self.manifest['accounts']['company']

    def uri(self):
        vless = 'vless://' + self.account['id'] + '@cdn.example.org:443?' + urlencode({'type': 'xhttp', 'security': 'tls',
            'encryption': 'none', 'sni': 'cdn.example.org', 'host': 'cdn.example.org', 'alpn': 'h2', 'path': host.XHTTP_PATH, 'mode': host.XHTTP_MODE}) + '#SELF-cdn'
        h2 = 'hysteria2://' + self.account['auth'] + '@93.184.216.34:443?' + urlencode({'security': 'tls', 'sni': 'sub.example.org', 'alpn': 'h3'}) + '#SELF-h2'
        return vless + '\n' + h2 + '\n'

    def yaml(self):
        return f'''proxies:
    - alpn:
        - h2
      name: SELF-cdn
      type: vless
      uuid: {self.account['id']}
      server: cdn.example.org
      port: 443
      network: xhttp
      tls: true
      servername: cdn.example.org
      xhttp-opts:
        host: cdn.example.org
        path: /qd-xhttp/
        mode: packet-up
    - name: SELF-h2
      type: hysteria2
      server: 93.184.216.34
      port: 443
      password: {self.account['auth']}
      sni: sub.example.org
      alpn: [h3]
proxy-groups:
    - name: PROXY
      proxies: [SELF-h2, SELF-cdn, DIRECT]
rules:
    - DOMAIN-SUFFIX,bilibili.com,DIRECT
    - MATCH,PROXY
'''

    def test_native_cdn_uri_scope_and_reject_vision_duplicate_insecure_foreign_identity(self):
        uri = self.uri()
        def check(text): host.validate_feed_scope(self.manifest, 'company', base64.b64encode(text.encode()), 'sub')
        check(uri)
        for bad in (uri.replace('type=xhttp', 'type=xhttp&flow=xtls-rprx-vision'), uri.replace('type=xhttp', 'type=xhttp&insecure=1'),
                    uri.replace('alpn=h2', 'alpn=h2&alpn=h3'), uri.replace('sni=cdn.', 'sni=foreign.'),
                    uri.replace('host=cdn.', 'host=foreign.'), uri.replace('path=%2Fqd-xhttp%2F', 'path=%2Fother'),
                    uri.replace(self.account['id'], self.manifest['accounts']['home']['id']),
                    uri.replace(self.account['auth'], self.manifest['accounts']['home']['auth']),
                    uri.replace('type=xhttp', 'type=xhttp&pbk=stale'), uri.replace('mode=packet-up', 'mode=auto'), uri + uri):
            with self.subTest(bad=bad[:20]), self.assertRaises(host.Error): check(bad)

    def test_native_yaml_nested_role_fields_not_flat_text_matches(self):
        text = self.yaml()
        host.validate_feed_scope(self.manifest, 'company', text.encode(), 'mihomo')
        for bad in (text.replace('tls: true', 'tls: false'), text.replace('network: xhttp', 'network: tcp'),
                    text.replace('path: /qd-xhttp/', 'path: /other'), text.replace('mode: packet-up', 'mode: auto'),
                    text.replace('tls: true', 'tls: true\n      flow: xtls-rprx-vision'),
                    text.replace('tls: true', 'tls: true\n      skip-cert-verify: true'),
                    text.replace('tls: true', 'tls: true\n      dialer-proxy: OTHER'),
                    text.replace('host: cdn.', 'host: foreign.'), text.replace('alpn: [h3]', 'alpn: [h2]'),
                    text.replace('port: 443', 'port: 443\n      port: 8443', 1),
                    text.replace(self.account['auth'], self.manifest['accounts']['home']['auth'])):
            with self.assertRaises(host.Error): host.validate_feed_scope(self.manifest, 'company', bad.encode(), 'mihomo')

    def test_bootstrap_dns_rejects_edge_wrong_origin_and_aaaa(self):
        origin = '93.184.216.34'
        answer = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (origin, 443))]
        with mock.patch.object(host.socket, 'getaddrinfo', return_value=answer): host.check_cdn_dns('cdn.example.org', origin)
        for bad in ([], [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('104.16.1.1', 443))],
                    answer + [(socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('2606:4700::1', 443, 0, 0))]):
            with mock.patch.object(host.socket, 'getaddrinfo', return_value=bad), self.assertRaises(host.Error):
                host.check_cdn_dns('cdn.example.org', origin)

    def test_certificate_selection_is_per_hostname_and_preserves_h2_binding(self):
        folder = self.fx.folder
        storage = folder / 'caddy/data/caddy/certificates/issuer'; storage.mkdir(parents=True)
        for domain in ('sub.example.org', 'cdn.example.org'):
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes',
                '-days', '1', '-subj', '/CN=' + domain, '-addext', 'subjectAltName=DNS:' + domain,
                '-keyout', str(storage / (domain + '.key')), '-out', str(storage / (domain + '.crt'))],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        old = host.hysteria_certificate_paths(folder, self.manifest)
        hashes = [host.digest(path) for path in old]
        self.assertEqual(host.cdn_certificate_paths(folder, self.manifest)[0].name, 'cdn.example.org.crt')
        self.assertEqual([host.digest(path) for path in host.hysteria_certificate_paths(folder, self.manifest)], hashes)
        wrong = copy.deepcopy(self.manifest); wrong['tcp_cdn']['key_file'] = str(old[1].relative_to(folder))
        with self.assertRaises(host.Error): host.cdn_certificate_paths(folder, wrong)
        (storage / 'cdn.example.org.key').unlink(); (storage / 'cdn.example.org.key').symlink_to(old[1])
        with self.assertRaisesRegex(host.Error, 'Symlink'): host.cdn_certificate_paths(folder, self.manifest)


if __name__ == '__main__': unittest.main()
