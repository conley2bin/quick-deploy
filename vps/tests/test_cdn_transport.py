"""Host-only CDN invariants. Real files/SQLite/journals, mocked system/native API.

Fast fixtures inspect payloads, ownership and recovery without API62789. The
explicit-artifact SourceNativeCDN class uses the sole serial native fixture lease;
it exercises actual module handlers, not public Cloudflare or production TLS.
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
import tarfile
import tempfile
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
        main = dict(host.inbound_for(manifest), id=1, tag='in-443-tcp', disableFlow=False, policySentinel='preserved')
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
            db.execute("alter table client_inbounds add column flow_override text default ''")
            db.execute('create table client_traffics(email text)')
            db.execute('create table inbound_client_ips(client_email text)')
        template = {'log': {'access': 'none', 'error': 'none', 'loglevel': 'none'},
                    'outbounds': [{'tag': 'direct', 'protocol': 'freedom'}, {'tag': 'blocked', 'protocol': 'blackhole'}]}
        template['routing'] = {'rules': [host.management_rule(template, manifest, 'SELF-h2')]}
        self.h.settings_write(self.database, {'xrayTemplateConfig': json.dumps(template)})
        self.sync()

    def clients(self, h2=False, cdn=False):
        result = []
        for name, record in self.records.items():
            item = {k: v for k, v in record.items() if k not in ('id', 'uuid', 'auth', 'createdAt', 'updatedAt')}
            item.update(id=record['uuid'], created_at=1800000000000)
            if h2: item['auth'] = record['auth']
            if h2 or cdn: item['flow'] = ''
            result.append(item)
        return result

    def sync(self):
        with sqlite3.connect(self.database) as db:
            for identity, inbound in self.inbounds.items():
                if not db.execute('select 1 from inbounds where id=?', (identity,)).fetchone():
                    db.execute('insert into inbounds(id,user_id) values(?,1)', (identity,))
                db.execute('update inbounds set remark=?,protocol=?,port=?,enable=?,listen=?,tag=?,settings=?,stream_settings=?,disable_flow=? where id=?',
                    (inbound['remark'], inbound['protocol'], inbound['port'], inbound['enable'], inbound['listen'], inbound['tag'],
                     inbound['settings'], inbound['streamSettings'], inbound.get('disableFlow', False), identity))
            for group in self.groups.values():
                address, port = group['hosts'][0].rsplit(':', 1)
                if not db.execute('select 1 from hosts where group_id=?', (group['groupId'],)).fetchone():
                    db.execute('insert into hosts(group_id,inbound_id) values(?,?)', (group['groupId'], group['inboundIds'][0]))
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
        if path == '/panel/api/inbounds/add':
            identity = max(self.inbounds) + 1
            self.inbounds[identity] = dict(copy.deepcopy(data), id=identity)
            settings = json.loads(self.inbounds[identity]['settings'])
            for client in settings['clients']: client['flow'] = ''
            self.inbounds[identity]['settings'] = json.dumps(settings)
            with sqlite3.connect(self.database) as db:
                db.executemany('insert into client_inbounds(client_id,inbound_id,flow_override) values(?,?,?)',
                    [(r['id'], identity, '') for r in self.records.values()])
            for record in self.records.values(): record['createdAt'] = 1800000000000
        elif path == '/panel/api/hosts/add':
            identity = 'cdn-group'
            self.groups[identity] = dict(copy.deepcopy(data), groupId=identity,
                hosts=[h + ':' + str(data['port']) for h in data['hosts']])
        elif path.startswith('/panel/api/hosts/update/'):
            self.groups[path.rsplit('/', 1)[1]] = copy.deepcopy(data)
        elif path.startswith('/panel/api/inbounds/update/'):
            identity = int(path.rsplit('/', 1)[1])
            old = self.inbounds[identity]
            self.inbounds[identity] = copy.deepcopy(data)
            if old['tag'] == 'in-' + str(old['port']) + '-tcp' and data.get('tag') == old['tag']:
                self.inbounds[identity]['tag'] = 'in-' + str(data['port']) + '-tcp'
            if data.get('disableFlow'):
                settings = json.loads(self.inbounds[identity]['settings'])
                for client in settings['clients']: client['flow'] = ''
                self.inbounds[identity]['settings'] = json.dumps(settings)
                for record in self.records.values(): record['createdAt'] = 1800000000000
        elif path == '/panel/api/clients/add':
            account = data['client']; name = account['email']; identity = max(r['id'] for r in self.records.values()) + 1
            self.records[name] = dict(account, id=identity, uuid=account['id'], limitHwid=0, createdAt=1800000000000, updatedAt=1800000000000)
            with sqlite3.connect(self.database) as db:
                db.execute('insert into clients(email,uuid,sub_id,enable,flow,id,auth) values(?,?,?,?,?,?,?)',
                    (name, account['id'], account['subId'], account['enable'], account['flow'], identity, account['auth']))
                db.executemany('insert into client_inbounds(client_id,inbound_id) values(?,?)', [(identity, i) for i in data['inboundIds']])
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
            settings['clients'] = self.clients(identity == 2, inbound.get('disableFlow', False))
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
            text = text.replace('x'*43, manifest['reality']['publicKey']).replace('1234567890abcdef', manifest['reality']['shortIds'][0])
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
            feature='tcp-cdn', source_transports=['tcp-reality', 'hysteria2'], target_transports=['tcp-reality', 'tcp-xhttp-cdn', 'hysteria2'],
            tcp_cdn={'schema': 1, 'domain': 'cdn.example.org', 'reality_port': 9443})
        self.original = (self.active / 'manifest.json').read_bytes()

    def prepare(self):
        return self.h.transport_prepare(self.base.upload(self.request))

    def install_auth_runtime(self):
        epoch = {'boot_id': self.h.boot_id(), 'pid': 55, 'start_ticks': 10}
        (self.active / 'logs').mkdir(mode=0o700, exist_ok=True)
        original_ctl = self.h.ctl.side_effect
        def generated():
            _, template = self.h.auth_template(self.active)
            config = copy.deepcopy(template)
            config['inbounds'] = [{'tag': i['tag'], 'protocol': i['protocol'], 'listen': i['listen'], 'port': i['port'],
                'settings': json.loads(i['settings']), 'streamSettings': json.loads(i['streamSettings'])}
                for i in self.model.inbounds.values()]
            self.h.atomic(self.active / 'bin/config.json', self.h.dumps(config))
            path = template['log']['error']
            if path != 'none':
                with Path(path).open('a') as out: out.write('native startup marker\n')
        def control(*args):
            result = original_ctl(*args)
            if args == ('start', self.h.PANEL):
                epoch['start_ticks'] += 1
                generated()
            return result
        generated()
        self.real_auth_core_epoch = self.h.auth_core_epoch
        self.f.patch(self.h, 'auth_core_epoch', side_effect=lambda folder: dict(epoch))
        self.f.patch(self.h, 'auth_live_users', side_effect=lambda folder, tag:
            {c['email']: {'id': c['id'], 'flow': c.get('flow', '')} for i in self.model.inbounds.values() if i['tag'] == tag
             for c in json.loads(i['settings'])['clients']})
        self.h.ctl.side_effect = control
        return epoch

    def complete_auth(self):
        self.install_auth_runtime()
        begun = self.h.transport_auth_begin(self.request)
        pending = self.h.read_json(self.h.ROOT / 'pending.json')
        path = self.h.auth_log_path(pending, pending['cdn_auth'])
        line = datetime.now().strftime('%Y/%m/%d %H:%M:%S.%f') + ' [Info] [123] app/proxyman/inbound: connection ends > proxy/vless/inbound: invalid request from 127.0.0.1:4567 > proxy/vless/encoding: invalid request user id: ' + begun['nonce'] + '\n'
        with path.open('a') as stream: stream.write(line)
        return self.h.transport_auth_finish(dict(self.request, cdn_auth_nonce=begun['nonce']))

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
        self.assertEqual(host.main_tag(current), 'SELF-main')
        self.assertEqual(host.reality_tag(current), 'in-443-tcp')
        self.assertEqual(host.reality_port(current), 9443)
        self.assertEqual(host.transports(current), ['tcp-reality', 'tcp-xhttp-cdn', 'hysteria2'])
        for mutate in (lambda m: m.pop('hysteria2'), lambda m: m['tcp_cdn'].update(extra=True),
                       lambda m: m['tcp_cdn'].update(mode='auto'), lambda m: m['tcp_cdn'].update(path='/other'),
                       lambda m: m['tcp_cdn'].update(schema=True), lambda m: m['tcp_cdn'].update(inbound_id=2),
                       lambda m: m['config'].update(proxy_port=2080)):
            invalid = copy.deepcopy(current); mutate(invalid)
            with self.assertRaises(host.Error): host.cdn_enabled(invalid)
        self.assertEqual(host.snapshot_accounts(self.model.database, current), current['accounts'])
        self.assertEqual(host.snapshot_host_group(self.model.database, current), self.manifest['host_group_id'])
        for statement in ("insert into hosts(group_id,inbound_id,address,port,is_hidden) values('hidden',1,'cdn.example.org',443,1)",
                          'insert into client_inbounds(client_id,inbound_id) values(1,1)', "update inbounds set listen='0.0.0.0' where id=3",
                          "update hosts set sni='foreign.example.org' where inbound_id=3",
                          "update hosts set pinned_peer_cert_sha256='[\"custom\"]' where inbound_id=3"):
            saved = self.model.database.read_bytes()
            with sqlite3.connect(self.model.database) as db: db.execute(statement)
            with self.assertRaises(host.Error): host.snapshot_accounts(self.model.database, current)
            self.model.database.write_bytes(saved)

    def test_additive_payload_full_policies_hosts_h2_and_three_joins(self):
        h2 = copy.deepcopy(self.model.inbounds[2]); h2group = copy.deepcopy(self.model.groups[self.manifest['hysteria2']['host_group_id']])
        records = copy.deepcopy(self.model.records)
        with sqlite3.connect(self.model.database) as db: joins = db.execute('select * from client_inbounds').fetchall()
        result = self.prepare()
        self.assertTrue(result['prepared']); self.assertGreater(result['deadline'], 0)
        current = self.h.read_json(self.active / 'manifest.json')
        self.assertEqual(current['tcp_cdn']['inbound_id'], 3)
        self.assertEqual(current['tcp_cdn']['host_group_id'], 'cdn-group')
        self.assertEqual(current['host_group_id'], self.manifest['host_group_id'])
        self.assertEqual(self.model.inbounds[2], h2); self.assertEqual(self.model.groups[h2group['groupId']], h2group)
        self.assertEqual(current['hysteria2'], self.manifest['hysteria2'])
        with sqlite3.connect(self.model.database) as db:
            self.assertEqual(db.execute('select * from client_inbounds where inbound_id!=3').fetchall(), joins)
            self.assertEqual(db.execute('select count(*) from client_inbounds').fetchone()[0], 9)
            self.assertEqual(db.execute('select count(*) from inbounds').fetchone()[0], 3)
            self.assertEqual(db.execute('select count(*) from hosts').fetchone()[0], 3)
        updates = [(path, data) for path, data in self.model.calls if data is not None]
        self.assertFalse(any('/attach' in path for path, _ in updates))
        self.assertEqual(sum(path == '/panel/api/inbounds/add' for path, _ in updates), 1)
        added = next(data for path, data in updates if path == '/panel/api/inbounds/add')
        self.assertIs(added['disableFlow'], True)
        self.assertNotIn('externalProxy', json.loads(added['streamSettings']))
        payload = next(data for path, data in updates if path == '/panel/api/inbounds/update/1')
        self.assertIs(payload['disableFlow'], False)
        self.assertEqual((payload['port'], payload['tag']), (9443, 'in-443-tcp'))
        self.assertEqual(sum(path == '/panel/api/inbounds/update/1' for path, _ in updates), 2)
        self.assertEqual(self.model.inbounds[1]['tag'], 'in-443-tcp')
        self.assertEqual(payload['policySentinel'], 'preserved')
        self.assertEqual(json.loads(payload['settings'])['fallbacks'], [])
        for name, record in records.items():
            self.assertEqual({k: v for k, v in self.model.records[name].items() if k not in ('createdAt',)},
                             {k: v for k, v in record.items() if k not in ('createdAt',)})
        host.cdn_host_health(self.model.groups[current['tcp_cdn']['host_group_id']], current)
        self.assertFalse(current['published'])
        self.assertNotIn('@feed', (self.active / 'Caddyfile').read_text())

    def test_source_transport_and_original_h2_negative_fields_refuse_before_intent(self):
        original = copy.deepcopy(self.model.inbounds)
        for mutate in (lambda: self.model.inbounds[1].update(tag='SELF-main'),
                       lambda: self.model.inbounds[1].update(listen='127.0.0.1'),
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
        self.assertEqual(inline['proxy-groups'][0]['proxies'], ['SELF-h2', 'SELF-cdn', 'SELF-main', 'DIRECT'])
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
        self.complete_auth()
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

    def test_already_cdn_new_operation_is_refused_but_receipt_retry_is_idempotent(self):
        self.publish()
        committed = self.h.transport_prepare(self.base.upload(self.request))
        self.assertTrue(committed['committed'])
        request = dict(self.request, operation_id='9' * 32, before_sha256='d' * 64)
        events = len(self.f.events)
        with self.assertRaisesRegex(self.h.Error, 'fresh CDN verification transaction is unsupported'):
            self.h.transport_prepare(self.base.upload(request))
        self.assertEqual(len(self.f.events), events)
        self.assertFalse((self.h.ROOT / 'pending.json').exists())

    def test_new_feature_dual_source_is_not_false_noop(self):
        result = self.prepare()
        self.assertNotIn('unchanged', result)
        self.assertEqual(result['transports'], ['tcp-reality', 'tcp-xhttp-cdn', 'hysteria2'])
        self.assertEqual(result, self.prepare())

    def test_candidate_archive_contains_target_and_closed_store_not_before_topology(self):
        self.prepare()
        archive = self.h.export_backup()
        stage = self.f.root / 'verified-cdn-archive'; stage.mkdir()
        with archive.open('rb') as stream: self.h.safe_unpack(stream, stage)
        manifest = self.h.verify_backup(stage, self.f.request['lock'])
        self.assertEqual(host.transports(manifest), ['tcp-reality', 'tcp-xhttp-cdn', 'hysteria2'])
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
                self.assertEqual(self.model.groups[restored['host_group_id']]['hosts'], [endpoint + ':9443'])
                self.assertEqual(self.model.groups[restored['tcp_cdn']['host_group_id']]['hosts'], ['cdn.example.org:443'])
                self.assertEqual(self.model.inbounds[1]['tag'], original['tcp_cdn']['reality_tag'])
                self.assertEqual(self.model.groups[restored['hysteria2']['host_group_id']]['hosts'], [endpoint + ':443'])
                for name, account in original['accounts'].items():
                    after = restored['accounts'][name]
                    self.assertEqual(after['id'] == account['id'], mode == 'restore')
                    self.assertEqual(after['auth'] == account['auth'], mode == 'restore')
                    self.assertEqual(after['subId'], account['subId'])
                self.assertEqual(len([p for p, _ in self.model.calls if '/panel/api/clients/update/' in p]),
                                 len(original['accounts']) if mode == 'replace' else 0)
        self.assertEqual(host.digest(archive), source_sha)
        self.assertEqual(self.h.check_target.call_count, 2)

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
        self.prepare(); self.complete_auth(); self.h.atomic(self.h.ROOT / 'export-sha256', 'd' * 64)
        self.h.transport_commit(self.proof_request())

    def test_correct_cdn_flow_does_not_rewrite_unaffected_native_clients(self):
        self.publish()
        current = self.h.read_json(self.active / 'manifest.json')
        self.model.calls.clear()
        self.h.reapply_role_flows(self.model, current)
        self.assertFalse(any(path.startswith('/panel/api/inbounds/update/') for path, _ in self.model.calls))
        self.model.inbounds[3]['disableFlow'] = False
        self.h.reapply_role_flows(self.model, current)
        self.assertTrue(self.model.inbounds[3]['disableFlow'])
        self.assertEqual(sum(path == '/panel/api/inbounds/update/3' for path, _ in self.model.calls), 1)

    def test_add_rename_reset_remove_single_authority_and_three_joins(self):
        self.publish()
        original = copy.deepcopy(self.model.records['company'])
        for index, (action, names) in enumerate((('add', ['tablet']), ('rename', ['company', 'work']),
                                                ('reset', ['work']), ('remove', ['tablet'])), 3):
            request = {k: v for k, v in self.base.request.items() if k not in ('mode', 'before_sha256')}
            request.update(device_action=action, names=names, operation_id=str(index) * 32)
            live_front = (self.active / 'Caddyfile').read_bytes()
            self.h.devices_prepare(self.base.upload(request))
            current = self.h.read_json(self.active / 'manifest.json')
            self.assertEqual((self.active / 'Caddyfile').read_bytes(), live_front)
            self.assertEqual((self.h.ROOT / 'device-rollback/Caddyfile').read_bytes(), live_front)
            archive = self.h.export_backup()
            stage = self.f.root / ('verified-device-' + action); stage.mkdir()
            with archive.open('rb') as stream:
                self.h.safe_unpack(stream, stage)
            candidate = self.h.verify_backup(stage, self.f.request['lock'])
            self.assertFalse(candidate['published'])
            self.assertEqual((stage / 'deployment/Caddyfile').read_text(), self.h.caddyfile(candidate, False))
            self.assertEqual((self.active / 'Caddyfile').read_bytes(), live_front)
            self.h.atomic(self.active / 'Caddyfile', live_front + b'\n# foreign front drift\n')
            with self.assertRaisesRegex(self.h.Error, 'exact prepared device snapshot'):
                self.h.export_backup()
            self.h.atomic(self.active / 'Caddyfile', live_front)
            self.assertFalse((self.h.ROOT / 'backup-stop.json').exists())
            with sqlite3.connect(self.model.database) as db:
                joins = db.execute('select client_id,inbound_id from client_inbounds').fetchall()
                self.assertEqual(len(joins), 3 * len(current['accounts']))
                self.assertEqual({i for _, i in joins}, {1, 2, 3})
            if action == 'add':
                self.assertEqual(current['accounts']['tablet']['flow'], 'xtls-rprx-vision')
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
        self.assertEqual(self.h.read_json(self.active / 'manifest.json')['tcp_cdn']['inbound_id'], 3)

    def test_cold_configure_preserves_cdn_host_mode_and_rotates_once_only_on_replace(self):
        self.prepare(); source = self.h.read_json(self.active / 'manifest.json')
        (self.active / 'bin/config.json').write_text('{}')
        main_group = copy.deepcopy(self.model.groups[source['host_group_id']])
        main_group['hosts'] = ['93.184.216.35:9443']
        cdn_group = copy.deepcopy(self.model.groups[source['tcp_cdn']['host_group_id']])
        before = copy.deepcopy(self.model.records)
        target = copy.deepcopy(source); target['endpoint'] = '93.184.216.35'
        real_function(self.h, 'configure_native')(self.active, target, fresh=False, rotate=False)
        self.assertEqual(self.model.groups[target['host_group_id']], main_group)
        self.assertEqual(self.model.records, before)
        self.assertEqual(self.model.groups[target['tcp_cdn']['host_group_id']], cdn_group)
        self.assertEqual(self.model.inbounds[1]['tag'], 'in-443-tcp')
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


class AuthWitness(unittest.TestCase):
    def setUp(self):
        self.owner = CdnHost('runTest'); self.owner.setUp(); self.addCleanup(self.owner.doCleanups)
        self.h, self.f, self.active = self.owner.h, self.owner.f, self.owner.active
        self.request = self.owner.request
        self.owner.prepare()
        self.epoch = self.owner.install_auth_runtime()
        self.original_raw = self.h.auth_template(self.active)[0]
        self.original_config = self.h.read_json(self.active / 'bin/config.json')
        self.original_records = copy.deepcopy(self.owner.model.records)

    def begin(self):
        self.begun = self.h.transport_auth_begin(self.request)
        self.pending = self.h.read_json(self.h.ROOT / 'pending.json')
        self.path = self.h.auth_log_path(self.pending, self.pending['cdn_auth'])
        return self.begun

    def append(self, nonce=None, when=None):
        nonce = nonce or self.begun['nonce']
        stamp = (when or datetime.now()).strftime('%Y/%m/%d %H:%M:%S.%f')
        with self.path.open('a') as out:
            out.write(stamp + ' [Info] [12345] app/proxyman/inbound: connection ends > proxy/vless/inbound: invalid request from 127.0.0.1:1234 > proxy/vless/encoding: invalid request user id: ' + nonce + '\n')

    def finish(self, nonce=None):
        return self.h.transport_auth_finish(dict(self.request, cdn_auth_nonce=nonce or self.begun['nonce']))

    def assert_restored(self):
        self.assertEqual(self.h.auth_template(self.active)[0], self.original_raw)
        self.assertEqual(self.h.read_json(self.active / 'bin/config.json'), self.original_config)
        self.assertEqual(self.owner.model.records, self.original_records)
        self.assertFalse(self.path.exists())

    def test_bound_fresh_verdict_restores_exact_raw_and_finish_lost_ack(self):
        raw, template = self.h.auth_template(self.active)
        # Raw spacing is part of the restore contract, not just parsed equality.
        self.original_raw = '  ' + json.dumps(template, indent=3) + '\n'
        self.h.settings_write(self.active / 'db/x-ui.db', {'xrayTemplateConfig': self.original_raw})
        with mock.patch.object(self.h, 'settings_write', wraps=self.h.settings_write) as writes:
            result = self.begin()
            self.assertEqual(set(result), {'schema', 'operation_id', 'request_fingerprint', 'nonce', 'native_identity_sha256', 'inbound_id', 'inbound_tag'})
            self.assertEqual((result['inbound_id'], result['inbound_tag']), (3, 'SELF-cdn'))
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(self.path.parent, self.active / 'logs')
            self.assertEqual(self.begin(), result, 'Begin lost ACK must reuse the nonce and logger')
            self.append()
            finished = self.finish()
            self.assertEqual(set(finished), {'schema', 'operation_id', 'request_fingerprint', 'nonce_sha256', 'rejected', 'native_identity_sha256', 'inbound_id', 'inbound_tag'})
            self.assertEqual((finished['inbound_id'], finished['inbound_tag']), (3, 'SELF-cdn'))
            self.assertTrue(finished['rejected'])
            self.assertEqual(finished['native_identity_sha256'], result['native_identity_sha256'])
            calls = self.h.ctl.call_count
            self.assertEqual(self.finish(), finished)
            self.assertEqual(self.h.ctl.call_count, calls)
            self.assertEqual(len(writes.call_args_list), 2)
            self.assertTrue(all(set(call.args[1]) == {'xrayTemplateConfig'} for call in writes.call_args_list))
        self.assert_restored()
        saved = self.h.read_json(self.h.ROOT / 'pending.json')['cdn_auth']
        self.assertNotIn('nonce', saved); self.assertNotIn('original_template', saved); self.assertNotIn('native_state', saved)
        self.assertFalse((self.h.ROOT / 'export-sha256').exists(), 'A digest must be invalidated before final B')

    def test_other_missing_stale_or_extended_nonce_never_sets_verdict(self):
        from datetime import timedelta
        for kind in ('missing', 'other', 'stale', 'extended'):
            with self.subTest(kind=kind):
                # Each scenario uses a genuinely independent transaction.
                other = AuthWitness('runTest'); other.setUp()
                try:
                    other.begin()
                    if kind == 'other': other.append('00000000-0000-4000-8000-000000000001')
                    if kind == 'stale': other.append(when=datetime.now() - timedelta(days=1))
                    if kind == 'extended': other.append(other.begun['nonce'] + 'ffff')
                    with other.assertRaisesRegex(other.h.Error, 'verdict unavailable'): other.finish()
                    other.assert_restored()
                    self.assertIsNone(other.h.read_json(other.h.ROOT / 'pending.json')['cdn_auth']['verdict'])
                    with other.assertRaises(other.h.Error): other.finish()
                finally:
                    other.doCleanups()

    def test_processuuid_registered_collision_regenerates_boundedly(self):
        import uuid
        registered = list(self.owner.model.records.values())[1]['uuid']
        raw = bytearray(uuid.UUID(registered).bytes); raw[6] ^= 0x0f; raw[7] ^= 0xff
        equivalent = uuid.UUID(bytes=bytes(raw))
        self.assertNotEqual(str(equivalent), registered)
        self.assertEqual(self.h.auth_uuid_key(str(equivalent)), self.h.auth_uuid_key(registered))
        real_uuid4 = uuid.uuid4
        fresh = real_uuid4()
        with mock.patch.object(self.h.uuid, 'uuid4', side_effect=[equivalent, fresh]) as generate:
            self.assertEqual(self.begin()['nonce'], str(fresh)); self.assertEqual(generate.call_count, 2)
        self.append(); self.finish()

    def test_truncation_or_changed_epoch_restore_without_verdict(self):
        self.begin(); self.append()
        self.path.write_bytes(b'')
        with self.assertRaisesRegex(self.h.Error, 'verdict unavailable'): self.finish()
        self.assert_restored()

    def test_wrong_inode_never_consumed_and_foreign_file_never_deleted(self):
        self.begin(); self.append()
        original = self.path.with_name('held-original')
        self.path.rename(original)
        self.path.write_bytes(original.read_bytes()); self.path.chmod(0o600)
        with self.assertRaisesRegex(self.h.Error, 'verdict unavailable'): self.finish()
        self.assertEqual(self.h.auth_template(self.active)[0], self.original_raw)
        self.assertEqual(self.h.read_json(self.active / 'bin/config.json'), self.original_config)
        self.assertTrue(self.path.exists()); self.assertTrue(original.exists())
        self.assertTrue((self.h.ROOT / 'pending.json').exists())

    def test_epoch_change_fails_after_restoration(self):
        self.begin(); self.append(); self.epoch['start_ticks'] += 1
        with self.assertRaisesRegex(self.h.Error, 'verdict unavailable'): self.finish()
        self.assert_restored()

    def test_wrong_nonce_fails_after_restoration(self):
        self.begin(); self.append()
        with self.assertRaisesRegex(self.h.Error, 'verdict unavailable'): self.finish('00000000-0000-4000-8000-000000000001')
        self.assert_restored()

    def test_truncate_regrow_over_offset_still_refuses_verdict(self):
        self.begin()
        self.path.write_bytes(b'foreign prefix replaces original startup marker\n')
        self.append()
        self.assertGreater(self.path.stat().st_size, self.pending['cdn_auth']['offset'])
        with self.assertRaisesRegex(self.h.Error, 'verdict unavailable'): self.finish()
        self.assert_restored()

    def test_crash_after_template_write_rolls_back_original_db_and_owned_file(self):
        original_write = self.h.settings_write
        def crash_after_write(database, values):
            original_write(database, values)
            if 'xrayTemplateConfig' in values and json.loads(values['xrayTemplateConfig'])['log']['loglevel'] == 'info':
                os._exit(77)
        pid = os.fork()
        if pid == 0:
            with mock.patch.object(self.h, 'settings_write', side_effect=crash_after_write): self.begin()
            os._exit(99)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 77)
        pending = self.h.read_json(self.h.ROOT / 'pending.json')
        path = self.h.auth_log_path(pending, pending['cdn_auth'])
        self.assertTrue(path.exists())
        self.assertEqual(pending['cdn_auth']['phase'], 'arming')
        self.assertTrue(self.h.abort(self.request)['queued'])
        self.assertFalse(path.exists())
        self.assertEqual((self.active / 'db/x-ui.db').read_bytes(), (self.h.ROOT / 'device-rollback/x-ui.db').read_bytes())

    def test_postrestore_policy_log_and_order_drift_cannot_commit(self):
        self.begin(); self.append(); self.finish()
        pending = self.h.read_json(self.h.ROOT / 'pending.json')
        original = self.h.read_json(self.active / 'bin/config.json')
        for mutate in (lambda c: c['outbounds'].reverse(), lambda c: c.update(log={'access': 'none', 'error': 'foreign.log', 'loglevel': 'info'})):
            config = copy.deepcopy(original); mutate(config)
            self.h.atomic(self.active / 'bin/config.json', self.h.dumps(config))
            with self.assertRaises(self.h.Error): self.h.auth_commit_verdict(self.request, pending, self.h.transport_request(self.request))
        self.h.atomic(self.active / 'bin/config.json', self.h.dumps(original))
        self.owner.model.records['company']['limitHwid'] += 1
        with self.assertRaises(self.h.Error): self.h.auth_commit_verdict(self.request, pending, self.h.transport_request(self.request))

    def test_restore_failure_leaves_guard_armed_and_commit_denied(self):
        self.begin(); self.append()
        with mock.patch.object(self.h, 'settings_write', side_effect=self.h.Error('restore failed')):
            with self.assertRaisesRegex(self.h.Error, 'restore failed'): self.finish()
        pending = self.h.read_json(self.h.ROOT / 'pending.json')
        self.assertEqual(pending['cdn_auth']['phase'], 'restoring')
        with self.assertRaisesRegex(self.h.Error, 'bound restored'):
            self.h.auth_commit_verdict(self.request, pending, self.h.transport_request(self.request))
        self.assertTrue(self.h.abort(self.request)['queued'])
        self.assertFalse(self.path.exists())

    def test_active_diagnostic_archive_and_commit_gates(self):
        self.begin()
        with self.assertRaisesRegex(self.h.Error, 'cannot enter an archive'): self.h.export_backup()
        request = self.owner.proof_request()
        self.h.atomic(self.h.ROOT / 'export-sha256', request['offnode_sha256'])
        with self.assertRaisesRegex(self.h.Error, 'bound restored'): self.h.transport_commit(request)
        self.append(); self.finish()
        with self.assertRaises((self.h.Error, FileNotFoundError)): self.h.transport_commit(request)
        self.h.atomic(self.h.ROOT / 'export-sha256', request['offnode_sha256'])
        self.assertTrue(self.h.transport_commit(request)['committed'])
        self.assertEqual(self.h.transport_commit(request)['cdn_auth_verdict']['rejected'], True)

    def test_deadline_and_crash_recovery_remove_only_owned_logger(self):
        self.begin(); directory = self.h.auth_directory(self.pending)
        unrelated = self.h.ROOT / 'auth-witness-foreign'; unrelated.mkdir(); (unrelated / 'keep').write_bytes(b'keep')
        pending = self.h.read_json(self.h.ROOT / 'pending.json'); pending['deadline'] = 0
        self.h.atomic(self.h.ROOT / 'pending.json', self.h.dumps(pending))
        self.assertTrue(self.h.recover()['queued'])
        self.assertFalse(directory.exists()); self.assertEqual((unrelated / 'keep').read_bytes(), b'keep')
        self.assertEqual(self.h.read_json(self.h.ROOT / 'pending.json')['phase'], 'restore-queued')
        self.assertEqual((self.active / 'db/x-ui.db').read_bytes(), (self.h.ROOT / 'device-rollback/x-ui.db').read_bytes())

    def test_actual_core_epoch_pins_boot_pid_start_ticks_executable_and_unit(self):
        proc = self.f.root / 'proc/55'; proc.mkdir()
        (proc / 'exe').symlink_to(self.active / 'bin/xray-linux-amd64')
        (proc / 'cgroup').write_text('0::/system.slice/' + self.h.PANEL)
        fields = ['S', '1'] + ['0'] * 18; fields[19] = '12345'
        (proc / 'stat').write_text('55 (xray core worker) ' + ' '.join(fields))
        lines = ('LISTEN 0 8 127.0.0.1:' + str(self.h.API_PORT) + ' 0.0.0.0:* users:(("xray",pid=55,fd=1))\n').encode()
        epoch = self.owner.real_auth_core_epoch
        with mock.patch.object(self.h, 'run', return_value=subprocess.CompletedProcess([], 0, lines)):
            self.assertEqual(epoch(self.active), {'boot_id': self.h.boot_id(), 'pid': 55, 'start_ticks': 12345})
            fields[19] = '12346'; (proc / 'stat').write_text('55 (xray core worker) ' + ' '.join(fields))
            self.assertEqual(epoch(self.active)['start_ticks'], 12346)
            (proc / 'cgroup').write_text('0::/system.slice/foreign.service')
            with self.assertRaisesRegex(self.h.Error, 'Foreign native core epoch'): epoch(self.active)
        with mock.patch.object(self.h, 'run', return_value=subprocess.CompletedProcess([], 0, lines.replace(b'127.0.0.1:', b'0.0.0.0:'))):
            with self.assertRaisesRegex(self.h.Error, 'unique native core epoch'): epoch(self.active)

    def test_expired_finish_restores_original_database_instead_of_returning_cached_verdict(self):
        self.begin(); self.append(); self.finish()
        pending = self.h.read_json(self.h.ROOT / 'pending.json'); pending['deadline'] = 0
        self.h.atomic(self.h.ROOT / 'pending.json', self.h.dumps(pending))
        with self.assertRaisesRegex(self.h.Error, 'lease expired'): self.finish()
        self.assertEqual(self.h.read_json(self.h.ROOT / 'pending.json')['phase'], 'restore-queued')
        self.assertEqual((self.active / 'db/x-ui.db').read_bytes(), (self.h.ROOT / 'device-rollback/x-ui.db').read_bytes())

    def test_file_replacement_rollback_restores_database_before_preserving_foreign_inode(self):
        self.begin(); self.append()
        self.path.rename(self.path.with_suffix('.owned'))
        self.path.write_bytes(b'foreign'); self.path.chmod(0o600)
        with self.assertRaisesRegex(self.h.Error, 'identity changed'): self.h.abort(self.request)
        self.assertEqual((self.active / 'db/x-ui.db').read_bytes(), (self.h.ROOT / 'device-rollback/x-ui.db').read_bytes())
        self.assertEqual(self.path.read_bytes(), b'foreign')
        self.assertNotIn(self.h.PANEL, self.f.services); self.assertNotIn(self.h.FRONT, self.f.services)

    def test_archive_refusal_does_not_stop_any_consumer(self):
        self.begin(); calls = self.h.ctl.call_count
        with self.assertRaisesRegex(self.h.Error, 'cannot enter an archive'): self.h.export_backup()
        self.assertEqual(self.h.ctl.call_count, calls)

    def test_wrong_binding_refused_without_touching_active_diagnostic(self):
        self.begin(); calls = self.h.ctl.call_count
        with self.assertRaises(self.h.Error): self.h.transport_auth_finish(dict(self.request, operation_id='f' * 32))
        self.assertEqual(self.h.ctl.call_count, calls); self.assertTrue(self.path.exists())


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
        self.manifest['tcp_cdn'] = {'schema': 1, 'domain': 'cdn.example.org', 'inbound_id': 3, 'host_group_id': 'cdn-group',
            'reality_port': 9443, 'reality_tag': 'in-443-tcp', 'path': host.XHTTP_PATH,
            'mode': host.XHTTP_MODE, 'certificate_file': 'caddy/data/caddy/certificates/issuer/cdn.example.org.crt',
            'key_file': 'caddy/data/caddy/certificates/issuer/cdn.example.org.key'}
        self.account = self.manifest['accounts']['company']

    @unittest.skipUnless(os.environ.get('QD_VPS_ARTIFACTS'), 'Pinned Caddy artifact required for real adaptation')
    def test_production_caddy_grace_is_bounded_only_for_cdn(self):
        pin = test_remote.vps.release_lock()['front']
        archive = Path(os.environ['QD_VPS_ARTIFACTS']) / pin['file']
        self.assertEqual(host.digest(archive), pin['sha256'])
        with tempfile.TemporaryDirectory(prefix='qd-cdn-grace-') as directory:
            root = Path(directory)
            binary = root / 'caddy'
            with tarfile.open(archive) as source:
                host.atomic(binary, source.extractfile('caddy').read(), 0o700)
            for mode in ('legacy', 'dual', 'cdn'):
                manifest = copy.deepcopy(self.manifest)
                if mode != 'cdn':
                    manifest.pop('tcp_cdn')
                if mode == 'legacy':
                    manifest.pop('hysteria2')
                with self.subTest(mode=mode):
                    rendered = host.caddyfile(manifest, True)
                    path = root / 'Caddyfile'
                    host.atomic(path, rendered)
                    adapted = json.loads(host.run([binary, 'adapt', '--config', path, '--adapter', 'caddyfile']).stdout)
                    http = adapted['apps']['http']
                    if mode == 'cdn':
                        self.assertEqual(http['grace_period'], 5_000_000_000)
                        self.assertLess(http['grace_period'], 60_000_000_000)
                    else:
                        self.assertNotIn('grace_period', http)
                        self.assertNotIn('grace_period', rendered)

    def uri(self):
        vless = 'vless://' + self.account['id'] + '@cdn.example.org:443?' + urlencode({'type': 'xhttp', 'security': 'tls',
            'encryption': 'none', 'sni': 'cdn.example.org', 'host': 'cdn.example.org', 'alpn': 'h2', 'path': host.XHTTP_PATH,
            'mode': host.XHTTP_MODE, 'extra': json.dumps({'mode': host.XHTTP_MODE}, separators=(',', ':'))}) + '#SELF-cdn'
        h2 = 'hysteria2://' + self.account['auth'] + '@93.184.216.34:443?' + urlencode({'security': 'tls', 'sni': 'sub.example.org', 'alpn': 'h3'}) + '#SELF-h2'
        reality = 'vless://' + self.account['id'] + '@93.184.216.34:9443?' + urlencode({
            'security': 'reality', 'flow': 'xtls-rprx-vision', 'pbk': 'x'*43, 'sid': '1234567890abcdef',
            'sni': 'target.example.org'}) + '#SELF-main'
        return reality + '\n' + vless + '\n' + h2 + '\n'

    def yaml(self):
        return f'''proxies:
    - name: SELF-main
      type: vless
      uuid: {self.account['id']}
      server: 93.184.216.34
      port: 9443
      network: tcp
      tls: true
      servername: target.example.org
      flow: xtls-rprx-vision
      reality-opts:
        public-key: {'x'*43}
        short-id: '1234567890abcdef'
        support-x25519mlkem768: true
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
      proxies: [SELF-h2, SELF-cdn, SELF-main, DIRECT]
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
                    uri.replace('type=xhttp', 'type=xhttp&pbk=stale'), uri.replace('mode=packet-up', 'mode=auto'),
                    uri.replace('%7B%22mode%22%3A%22packet-up%22%7D', '%7B%22mode%22%3A%22auto%22%7D'),
                    uri.replace('%7B%22mode%22%3A%22packet-up%22%7D', '%7B%22mode%22%3A%22packet-up%22%2C%22extra%22%3Atrue%7D'), uri + uri):
            with self.subTest(bad=bad[:20]), self.assertRaises(host.Error): check(bad)

    def test_native_yaml_nested_role_fields_not_flat_text_matches(self):
        text = self.yaml()
        host.validate_feed_scope(self.manifest, 'company', text.encode(), 'mihomo')
        # The pinned Go emitter aligns list items with their mapping key.
        # Keep both valid YAML spellings, but validate the actual emitted one.
        emitted = text.replace('        - h2', '      - h2')
        host.validate_feed_scope(self.manifest, 'company', emitted.encode(), 'mihomo')
        self.assertEqual(host.native_yaml_nodes(emitted), host.native_yaml_nodes(text))
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


@unittest.skipUnless(os.environ.get('QD_VPS_ARTIFACTS'), 'Explicit locked artifacts and serial API62789 lease required')
class SourceNativeCDN(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_native_dual as native_dual
        cls.native_dual = native_dual
        native_dual.NativeDual.setUpClass()
        cls.addClassCleanup(native_dual.NativeDual.doClassCleanups)

    def test_transport_handler_native_updates_exports_archive_and_abort(self):
        self.native_case(commit=False)

    def test_transport_handler_native_auth_final_archive_and_commit(self):
        self.native_case(commit=True)

    def native_case(self, commit):
        native = self.native_dual.native
        fx = self.native_dual.NativeDual('test_enable_preserves_full_native_policy_and_exports')
        fx._outcome = self._outcome
        self.addCleanup(fx.doCleanups)
        fx.setUp()
        evidence = os.environ.get('QD_NATIVE_AUTH_EVIDENCE')
        if evidence:
            def retain_private():
                destination = Path(evidence); destination.mkdir(mode=0o700, parents=True, exist_ok=True)
                for name in ('manifest.json', 'bin/config.json', 'db/x-ui.db', 'native.log'):
                    path = fx.folder / name
                    if path.is_file(): host.atomic(destination / ('retained-' + path.name), path.read_bytes())
                pending = host.ROOT / 'pending.json'
                if pending.is_file():
                    host.atomic(destination / 'retained-pending.json', pending.read_bytes())
                    state = host.read_json(pending)
                    if state.get('cdn_auth', {}).get('nonce'):
                        path = host.auth_log_path(state, state['cdn_auth'])
                        if path.is_file(): host.atomic(destination / 'retained-witness.log', path.read_bytes())
            self.addCleanup(retain_private)
        fx.seed_policy(); fx.enable()
        import test_native_cdn as native_cdn
        import test_native_cdn_triple as triple
        wire = native_cdn.NativeCDN('runTest')
        wire.fx = fx
        wire.evidence = Path(os.environ.get('QD_NATIVE_AUTH_EVIDENCE', str(fx.folder.parent / 'auth-evidence'))) / ('commit' if commit else 'abort')
        wire.evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.addCleanup(wire.doCleanups)
        target_port, moved_port = native.free_port(), native.free_port()
        triple.NativeCDNTriple.front(wire, target_port)
        api = fx.api()
        main = next(i for i in api.call('/panel/api/inbounds/list') if i['remark'] == 'SELF-main')
        main_stream = copy.deepcopy(host.native_object(main['streamSettings']))
        main_stream['realitySettings']['dest'] = '127.0.0.1:' + str(target_port)
        main['streamSettings'] = json.dumps(main_stream)
        api.call('/panel/api/inbounds/update/' + str(main['id']), main)
        # Reuse the existing HTTPS origin's narrow local routing exceptions, but
        # remove its debug instrumentation BEFORE capturing the original state.
        origin = fx.origin()
        fx.fx.stop()
        _, template = host.auth_template(fx.folder)
        template['log'] = {'access': 'none', 'error': 'none', 'loglevel': 'none'}
        rule = host.management_rule(template, fx.manifest, 'SELF-h2')
        template['routing']['rules'] = [rule] + [r for r in template['routing']['rules'] if r != rule]
        host.settings_write(fx.folder / 'db/x-ui.db', {'xrayTemplateConfig': json.dumps(template)})
        fx.fx.start(); host.wait_api(fx.manifest)
        original = copy.deepcopy(fx.manifest)
        records, joins = fx.records(), fx.rows('client_inbounds')
        h2_rows = [r for r in fx.rows('hosts') if r['inbound_id'] == original['hysteria2']['inbound_id']]
        exported, _, _ = fx.archive()
        before_archive = fx.folder.parent / 'closed-before-cdn.tar'
        before_archive.write_bytes(exported.read_bytes())
        before_hash = host.digest(before_archive)
        request = fx.fx.device_request('reset', 'company')
        request.pop('device_action'); request.pop('names')
        request.update(mode='transport-upgrade', feature='tcp-cdn', ssh_port=22, before_sha256=before_hash,
            source_transports=['tcp-reality', 'hysteria2'], target_transports=['tcp-reality', 'tcp-xhttp-cdn', 'hysteria2'],
            tcp_cdn={'schema': 1, 'domain': 'cdn.example.org', 'reality_port': moved_port})
        def issue(folder, manifest, domain):
            # HTTP01 issuance is an external boundary. Issue a matching private
            # fixture pair; all native mutation/health/archive code stays real.
            cert = Path(folder) / 'caddy/data/caddy/certificates/fixture' / (domain + '.crt')
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256',
                '-nodes', '-days', '1', '-subj', '/CN=' + domain, '-addext', 'subjectAltName=DNS:' + domain,
                '-keyout', str(cert.with_suffix('.key')), '-out', str(cert)],
                check=True, capture_output=True, timeout=10)
        def fixture_epoch(folder):
            # Same actual boot/PID/start-time binding; the disposable child is
            # parent-owned by this fixture rather than a production systemd unit.
            lines = host.run(['ss', '-H', '-ltnp'], timeout=5).stdout.decode().splitlines()
            import re
            pids = {pid for line in lines if len(line.split()) >= 4 and
                    line.split()[3] == '127.0.0.1:' + str(host.API_PORT)
                    for pid in re.findall(r'pid=(\d+)', line)}
            self.assertEqual(len(pids), 1)
            pid = pids.pop(); proc = Path('/proc') / pid
            self.assertEqual(os.readlink(proc / 'exe'), str(Path(folder) / 'bin/xray-linux-amd64'))
            fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
            self.assertEqual(int(fields[1]), fx.fx.proc.pid)
            return {'boot_id': host.boot_id(), 'pid': int(pid), 'start_ticks': int(fields[19])}
        source_health = host.reality_source_health
        def fixture_source_health(inbound, manifest):
            self.assertEqual(inbound['listen'], '127.0.0.1')
            stream = json.loads(inbound['streamSettings']) if isinstance(inbound['streamSettings'], str) else copy.deepcopy(inbound['streamSettings'])
            self.assertEqual(stream['realitySettings']['dest'], '127.0.0.1:' + str(target_port))
            stream['realitySettings']['dest'] = manifest['config']['reality_target'] + ':443'
            return source_health(dict(inbound, listen='0.0.0.0', streamSettings=stream), manifest)
        # Existing native fixture confines listeners to loopback/high ports.
        # Only these socket coordinates and the ACME/DNS boundary are adapted.
        with mock.patch.object(host, 'CDN_PORT', original['config']['proxy_port']), \
                mock.patch.object(host, 'XHTTP_PORT', native.free_port()), \
                mock.patch.object(host, 'bootstrap_cdn_certificate', side_effect=issue), \
                mock.patch.object(host, 'check_cdn_dns'), \
                mock.patch.object(host, 'auth_core_epoch', side_effect=fixture_epoch), \
                mock.patch.object(host, 'reality_source_health', side_effect=fixture_source_health):
            import vps
            import cdn_probe
            import yaml
            def controller_identity(archive, manifest):
                # Native API uses disposable high ports. Attest each actual
                # coordinate, then alter ONLY CDN/H2 ports for the production
                # controller's public443 role validator; full maps stay native.
                advertised = copy.deepcopy(manifest)
                advertised['config']['proxy_port'] = 443
                files = {}
                for name in host.device_names(manifest):
                    if not manifest['accounts'][name]['enable']:
                        continue
                    profile = yaml.safe_load(host.get_feed(manifest, name, 'mihomo'))
                    for node in profile['proxies']:
                        if node['name'] in ('SELF-cdn', 'SELF-h2'):
                            self.assertEqual(node['port'], host.CDN_PORT)
                            node['port'] = 443
                    files[name + '.yaml'] = yaml.safe_dump(profile).encode()
                with mock.patch.object(host, 'CDN_PORT', 443):
                    return vps.cdn_candidate_identity(archive, advertised, files, 'fixture0')
            with vps.upload_bundle(request) as stream:
                prepared = host.transport_prepare(stream)
            self.assertTrue(prepared['prepared'])
            current = host.read_json(fx.folder / 'manifest.json')
            self.assertEqual(host.transports(current), request['target_transports'])
            self.assertEqual(current['hysteria2'], original['hysteria2'])
            after_joins = fx.rows('client_inbounds')
            self.assertEqual([r for r in after_joins if r['inbound_id'] != current['tcp_cdn']['inbound_id']], joins)
            self.assertEqual(len(after_joins), 3 * len(records))
            self.assertEqual({(r['client_id'], r['inbound_id']) for r in after_joins},
                {(c['id'], i['id']) for c in fx.rows('clients') for i in fx.rows('inbounds')})
            self.assertEqual(len(fx.rows('inbounds')), 3)
            self.assertEqual(len(fx.rows('hosts')), 3)
            main_after = next(i for i in fx.api().call('/panel/api/inbounds/list') if i['remark'] == 'SELF-main')
            self.assertEqual((main_after['id'], main_after['tag'], main_after['port']),
                (main['id'], main['tag'], moved_port))
            self.assertEqual(current['tcp_cdn']['reality_tag'], main['tag'])
            self.assertEqual(current['host_group_id'], original['host_group_id'])
            self.assertEqual(host.native_object(main_after['streamSettings'])['realitySettings'], main_stream['realitySettings'])
            self.assertEqual(current['config'], original['config'])
            self.assertEqual(current['reality'], original['reality'])
            self.assertEqual([r for r in fx.rows('hosts') if r['inbound_id'] == original['hysteria2']['inbound_id']], h2_rows)
            for name, record in records.items():
                after = fx.records()[name]
                self.assertEqual({k: v for k, v in after.items() if k not in ('createdAt', 'updatedAt')},
                    {k: v for k, v in record.items() if k not in ('createdAt', 'updatedAt')})
                self.assertEqual(after['flow'], record['flow'])
            host.backend_health(fx.folder, current)
            candidate, _, verified = fx.archive()
            candidate_a = host.digest(candidate)
            controller_a = controller_identity(candidate, verified)
            self.assertNotEqual(candidate_a, before_hash)
            self.assertEqual(verified['tcp_cdn'], current['tcp_cdn'])
            front_port, cert, _ = wire.caddy(host.XHTTP_PORT)
            profile = fx.profile()
            node = next(p for p in profile['proxies'] if p.get('network') == 'xhttp')
            original_template = host.auth_template(fx.folder)[0]
            generated = host.read_json(fx.folder / 'bin/config.json')
            self.assertEqual(generated['log'], {'access': 'none', 'error': 'none', 'loglevel': 'none'})
            reality_node = next(p for p in profile['proxies'] if p['name'] == 'SELF-main')
            self.assertTrue(wire.https(wire.client(reality_node, moved_port, cert), origin))
            begun = host.transport_auth_begin(request)
            self.assertEqual((begun['inbound_id'], begun['inbound_tag']), (current['tcp_cdn']['inbound_id'], 'SELF-cdn'))
            cdn_probe.auth_binding(begun, request, host.transport_request(request), current)
            self.assertEqual(host.transport_auth_begin(request), begun)
            self.assertTrue(wire.https(wire.client(node, front_port, cert), origin))
            wrong = copy.deepcopy(node); wrong['uuid'] = begun['nonce']
            hits = len(origin.peers)
            self.assertFalse(wire.https(wire.client(wrong, front_port, cert), origin))
            self.assertEqual(len(origin.peers), hits)
            verdict_reader = host.auth_read_verdict
            def retain_closed_witness(pending, record):
                with host.auth_file(pending, record) as stream:
                    host.atomic(wire.evidence / 'closed-witness.log', stream.read())
                host.atomic(wire.evidence / 'witness-read-record.json', host.dumps(record))
                return verdict_reader(pending, record)
            with mock.patch.object(host, 'auth_read_verdict', side_effect=retain_closed_witness):
                finished = host.transport_auth_finish(dict(request, cdn_auth_nonce=begun['nonce']))
            self.assertTrue(finished['rejected'])
            cdn_probe.auth_binding(finished, request, host.transport_request(request), current, begin=begun)
            self.assertEqual((finished['inbound_id'], finished['inbound_tag']), (current['tcp_cdn']['inbound_id'], 'SELF-cdn'))
            self.assertEqual(finished['native_identity_sha256'], begun['native_identity_sha256'])
            self.assertEqual(host.auth_template(fx.folder)[0], original_template)
            self.assertEqual(host.read_json(fx.folder / 'bin/config.json'), generated)
            self.assertFalse(host.auth_directory(host.read_json(host.ROOT / 'pending.json')).exists())
            self.assertTrue(wire.https(wire.client(node, front_port, cert), origin))
            self.assertTrue(wire.https(wire.client(node, front_port, cert), origin))
            h2node = next(p for p in profile['proxies'] if p['type'] == 'hysteria2')
            h2cert = fx.certificate_paths(fx.folder, current)[0]
            self.assertTrue(wire.https(wire.client(h2node, current['config']['proxy_port'], h2cert), origin))
            self.assertEqual(host.transport_auth_finish(dict(request, cdn_auth_nonce=begun['nonce'])), finished)
            # Fresh final B is explicitly reverified against native A semantics.
            final, _, final_manifest = fx.archive()
            self.assertEqual(final_manifest['accounts'], verified['accounts'])
            self.assertEqual(final_manifest['tcp_cdn'], verified['tcp_cdn'])
            self.assertEqual(final_manifest['hysteria2'], verified['hysteria2'])
            self.assertEqual(controller_identity(final, final_manifest), controller_a)
            host.atomic(wire.evidence / 'auth-summary.json', host.dumps({'schema': 1, 'native_verdict': finished,
                'original_logging_none': True, 'temporary_info_only': True, 'exact_raw_restored': True,
                'nonlog_generated_unchanged': True, 'valid_again': True, 'reconnect': True, 'h2_preserved': True,
                'candidate_a_sha256': candidate_a, 'candidate_b_sha256': host.digest(final),
                'locked_artifacts': {key: fx.fx.lock[key]['sha256'] for key in ('panel', 'core', 'front')}}))
            if commit:
                fingerprint = host.transport_request(request)
                proof = {'schema': 1, 'operation_id': request['operation_id'], 'request_fingerprint': fingerprint,
                    'candidate_sha256': host.digest(final), 'domain': current['tcp_cdn']['domain'],
                    'device_identity_sha256': 'e' * 64, 'observed_at': datetime.now(timezone.utc).isoformat(),
                    'interface': 'fixture0', 'edge_ips': ['104.16.1.1'],
                    'checks': {k: True for k in ('edge_tls', 'edge_cloudflare', 'xhttp_https', 'vps_egress',
                        'wrong_uuid_rejected', 'valid_again', 'reconnect', 'hysteria2_preserved')}}
                # Controller's public-CF attestation is a boundary here; native
                # role/auth/template/archive and durable receipt handlers are real.
                bound = dict(request, offnode_sha256=host.digest(final), cdn_proof=proof,
                             cdn_proof_sha256=host.cdn_proof_digest(proof))
                committed = host.transport_commit(bound)
                self.assertTrue(committed['committed'])
                self.assertEqual(committed['cdn_auth_verdict'], finished)
                state = wire.evidence / 'controller-receipt'
                host.atomic(state / ('cdn-proofs/' + request['operation_id'] + '.json'), host.dumps(proof))
                host.atomic(state / ('cdn-proofs/' + request['operation_id'] + '.auth.json'), host.dumps(finished))
                vps.check_cdn_receipt(state, {'request': request, 'backup': str(final),
                    'cdn_proof_sha256': bound['cdn_proof_sha256']}, committed)
                self.assertEqual(host.transport_commit(bound), committed)
                self.assertEqual(host.abort(request), committed)
                self.assertFalse((host.ROOT / 'pending.json').exists())
                host.backend_health(fx.folder, host.read_json(fx.folder / 'manifest.json'))
            else:
                self.assertTrue(host.abort(request)['queued'])
                self.assertFalse(host.abort(request)['pending'])
                restored = host.read_json(fx.folder / 'manifest.json')
                self.assertEqual(restored, original)
                host.backend_health(fx.folder, restored)
            host.atomic(wire.evidence / 'final-manifest.json', host.dumps(host.read_json(fx.folder / 'manifest.json')))
            host.atomic(wire.evidence / 'final-database.sqlite', (fx.folder / 'db/x-ui.db').read_bytes())
        self.assertEqual(host.digest(before_archive), before_hash)


if __name__ == '__main__': unittest.main()
