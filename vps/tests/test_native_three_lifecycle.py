"""Pinned THREE-role lifecycle from an already-triple INPUT, not migration proof.

Setup uses complete real native API objects and exact approved metadata/readbacks;
it does not run or attest module transport-prepare/commit, Cloudflare or ACME.
Only OS scheduling/preflight, external target DNS/CA preflight, local CA issuance
and disposable high-port socket coordinates are fixture boundaries. Module ownership, account CRUD, archive,
restore/replace, native DB/serializers and all three authenticators remain real.

Run native classes serially ONLY after the parent grants API127.0.0.1:62789.
QD_VPS_ARTIFACTS and QD_NATIVE_CDN_EVIDENCE must be explicit/private. The pure
NativeThreeMappings class requires neither artifacts nor a native process.
"""
import base64
import copy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import ssl
import subprocess
import time
import unittest
from unittest import mock
import uuid
from urllib.parse import parse_qs, unquote, urlsplit

import test_native_cdn_triple as capability
import test_native_dual_recovery as recovery

host, vps, native, dual = capability.host, capability.vps, capability.native, capability.dual
ROLES = ('reality', 'cdn', 'h2')
VISION = 'xtls-rprx-vision'
REQUIRED = 'Explicit pinned artifacts/private evidence and parent API62789 lease required'


def native_nodes(profile):
    """Select by native transport/security, never the first VLESS/name substring."""
    result = {}
    for node in profile['proxies']:
        if node.get('type') == 'hysteria2':
            role = 'h2'
        elif node.get('type') == 'vless' and node.get('network') == 'xhttp' and not node.get('reality-opts'):
            role = 'cdn'
        elif node.get('type') == 'vless' and node.get('reality-opts') and node.get('network', 'tcp') == 'tcp':
            role = 'reality'
        else:
            raise ValueError('Foreign native mapping')
        if role in result:
            raise ValueError('Duplicate native role')
        result[role] = node
    if set(result) != set(ROLES):
        raise ValueError('Exactly THREE native roles required')
    return result


def coordinate_node(profile, role, address, port):
    """Copy the complete native map, retaining every non-coordinate field."""
    node = copy.deepcopy(native_nodes(profile)[role])
    node.update(server=address, port=port)
    return node


class NativeThreeMappings(unittest.TestCase):
    def profile(self):
        # Synthetic vectors only, not a credential/profile encoder for live use.
        return {'proxies': [
            {'name': 'SELF-main', 'type': 'vless', 'network': 'tcp', 'server': '192.0.2.1',
             'port': 9443, 'flow': VISION, 'reality-opts': {'public-key': 'synthetic', 'short-id': 'ab'},
             'native-extension': {'values': ['retain']}},
            {'name': 'SELF-cdn', 'type': 'vless', 'network': 'xhttp', 'server': 'cdn.example.org',
             'port': 443, 'tls': True, 'servername': 'cdn.example.org', 'alpn': ['h2'],
             'xhttp-opts': {'host': 'cdn.example.org', 'path': '/qd-xhttp/', 'mode': 'packet-up'},
             'native-extension': {'values': ['retain']}},
            {'name': 'SELF-h2', 'type': 'hysteria2', 'server': '192.0.2.1', 'port': 443,
             'sni': 'sub.example.org', 'native-extension': {'values': ['retain']}}]}

    def test_two_vless_roles_select_independently_of_order(self):
        profile = self.profile()
        for nodes in (profile['proxies'], list(reversed(profile['proxies']))):
            selected = native_nodes({'proxies': nodes})
            self.assertEqual(selected['cdn']['network'], 'xhttp')
            self.assertEqual(selected['reality']['flow'], VISION)
            self.assertEqual(selected['h2']['type'], 'hysteria2')

    def test_complete_maps_and_original_are_preserved(self):
        profile = self.profile()
        before = copy.deepcopy(profile)
        for role in ROLES:
            with self.subTest(role=role):
                redirected = coordinate_node(profile, role, '127.0.0.1', 23456)
                self.assertEqual(redirected, dict(native_nodes(profile)[role], server='127.0.0.1', port=23456))
                redirected['native-extension']['values'].append('isolated')
                self.assertEqual(profile, before)

    def test_missing_duplicate_foreign_and_ambiguous_roles_refuse(self):
        nodes = self.profile()['proxies']
        for invalid in (nodes[:2], nodes + [nodes[0]], nodes + [{'type': 'direct'}],
                        [nodes[0], dict(nodes[1], **{'reality-opts': {'public-key': 'ambiguous'}}), nodes[2]]):
            with self.assertRaises(ValueError):
                native_nodes({'proxies': invalid})

    def test_tag_seam_compares_complete_decoded_inbound_policy(self):
        expected = {'id': 7, 'port': 9443, 'tag': 'in-443-tcp',
            'settings': json.dumps({'clients': [{'id': 'synthetic', 'flow': VISION}]}),
            'streamSettings': json.dumps({'security': 'reality', 'realitySettings': {'privateKey': 'synthetic'}}),
            'sniffing': json.dumps({'enabled': True}), 'clientStats': [{'inboundId': 2}]}
        actual = {k: dual.decoded(v) if k in ('settings', 'streamSettings', 'sniffing') else v
                  for k, v in expected.items()}
        actual['clientStats'] = [{'inboundId': 3}]
        ThreeFixture.inbound_contract(self, expected, actual)
        for field, wrong in (('tag', 'in-9443-tcp'), ('port', 10443),
                             ('settings', {'clients': [{'id': 'synthetic', 'flow': ''}]})):
            with self.subTest(field=field), self.assertRaises(AssertionError):
                ThreeFixture.inbound_contract(self, expected, dict(actual, **{field: wrong}))


class ThreeFixture:
    # Compose shared process/digest/readiness helpers, not any capability tests.
    setUpClass = classmethod(capability.NativeCDNTriple.setUpClass.__func__)
    spawn = capability.NativeCDNTriple.spawn
    def await_core_started(self):
        # Native updates can reopen the logger without restarting Xray; that
        # logger epoch has no new process-start banner. Attest the actual gRPC
        # service and BOTH live role registries instead of a banner substring.
        result = subprocess.run([str(self.fx.folder / 'bin/xray-linux-amd64'),
            'api', 'statssys', '--server=127.0.0.1:62789', '--timeout=6'],
            capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, 'Owned Xray API readiness failed')
        status = self.fx.api().call('/panel/api/server/status')['xray']
        self.assertEqual((status['state'], status['version']), ('running', self.fx.fx.lock['core']['version']))
        expected = {r['email']: {'id': r['uuid'], 'flow': VISION}
                    for r in self.fx.records().values() if r['enable']}
        for tag, flow in ((self.original_tag, VISION), ('SELF-cdn', '')):
            users = host.auth_live_users(self.fx.folder, tag)
            self.assertTrue(users == {name: dict(value, flow=flow) for name, value in expected.items()},
                            'Actual live role registry differs from native authority (values withheld)')
    earliest = recovery.RecoveryFixture.earliest
    record_contract = recovery.RecoveryFixture.record_contract
    unchanged = recovery.RecoveryFixture.unchanged

    def save(self, name, value):
        host.atomic(self.evidence / name, json.dumps(value, indent=2))

    def setUp(self):
        root = Path(os.environ['QD_NATIVE_CDN_EVIDENCE']).resolve()
        git = vps.MODULE.parent.resolve()
        self.assertFalse(root == git or git in root.parents, 'Private native evidence must be outside Git')
        self.evidence = root / self._testMethodName
        self.evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.evidence.chmod(0o700)
        version = subprocess.run([vps.resolve_mihomo(), '-v'], capture_output=True, timeout=5)
        self.assertEqual(version.returncode, 0, 'Pinned local Mihomo version check failed')
        self.assertIn(b'v1.19.32', version.stdout, 'Unexpected local Mihomo version')
        self.save('runtime-pins.json', {'mihomo': '1.19.32', 'release_lock_sha256': self.lock_digests})
        self.fx = dual.NativeDual('test_enable_preserves_full_native_policy_and_exports')
        self.fx._outcome = self._outcome
        self.addCleanup(self.fx.doCleanups)
        self.fx.setUp()
        self.front = None
        self.access_logs = []
        self.client_directories = []
        self.addCleanup(self.stop_front)
        self.addCleanup(self.retain)
        self.enterContext(mock.patch.object(host, 'CDN_PORT', self.fx.manifest['config']['proxy_port']))
        self.enterContext(mock.patch.object(host, 'XHTTP_PORT', native.free_port()))
        self.fx.seed_policy()
        self.fx.seed_policy('home', enable=False, limitHwid=3)
        host.backend_health(self.fx.folder, self.fx.manifest)
        self.fx.enable()
        self.build_triple_input()
        self.enterContext(mock.patch.object(host, 'ctl', side_effect=self.control))
        self.start_front()
        self.topology()
        self.target_port = native.free_port()
        self.start_reality_target()

    def build_triple_input(self):
        """Real supported API construction; deliberately NOT enable_cdn/commit."""
        fx, api = self.fx, self.fx.api()
        before = fx.records()
        earliest = self.earliest(before)
        old = {table: fx.rows(table) for table in ('clients', 'client_inbounds', 'hosts', 'inbounds')}
        inbounds = api.call('/panel/api/inbounds/list')
        main = copy.deepcopy(next(i for i in inbounds if i['remark'] == 'SELF-main'))
        self.main_id = main['id']
        self.original_tag = main['tag']
        self.original_group = fx.manifest['host_group_id']
        self.original_reality = copy.deepcopy(fx.manifest['reality'])
        port = native.free_port()
        self.assertNotIn(port, (host.CDN_PORT, host.XHTTP_PORT, host.ADMIN_PORT, host.SUB_PORT, host.API_PORT, 22, 80, 8443))
        stream = copy.deepcopy(dual.decoded(main['streamSettings']))
        stream['externalProxy'][0]['port'] = port
        main.update(port=port, tag=self.original_tag, listen='0.0.0.0', streamSettings=json.dumps(stream))
        self.save('input-reality-update.private.json', main)
        api.call('/panel/api/inbounds/update/' + str(self.main_id), main)
        moved = next(i for i in api.call('/panel/api/inbounds/list') if i['id'] == self.main_id)
        # Pinned UpdateInbound regenerates an unchanged old AUTOMATIC tag when
        # the port moves. Verify that exact transition before the second update;
        # never silently adopt or overwrite an unexplained native tag.
        self.assertEqual(self.original_tag, 'in-' + str(fx.manifest['config']['proxy_port']) + '-tcp')
        self.assertEqual(moved['tag'], 'in-' + str(port) + '-tcp')
        self.inbound_contract(dict(main, tag=moved['tag']), moved)
        for name, record in before.items():
            self.record_contract(record, fx.records()[name], earliest[name], ('updatedAt',))
        self.persisted_contract(old['clients'], fx.rows('clients'), ('created_at', 'updated_at'))
        retagged = copy.deepcopy(moved)
        retagged['tag'] = self.original_tag  # now explicitly different from current
        self.save('input-reality-retag.private.json', retagged)
        api.call('/panel/api/inbounds/update/' + str(self.main_id), retagged)
        main = next(i for i in api.call('/panel/api/inbounds/list') if i['id'] == self.main_id)
        self.assertEqual(main['tag'], self.original_tag, 'Second native update failed to retain original tag')
        self.inbound_contract(retagged, main)
        self.save('input-reality-final-readback.private.json', main)
        group = api.call('/panel/api/hosts/get/' + self.original_group)
        group.update(hosts=[fx.manifest['endpoint']], port=port)
        api.call('/panel/api/hosts/update/' + group['groupId'], group)
        groups_before = {g['groupId'] for g in api.call('/panel/api/hosts/list')}
        payload = {'remark': 'SELF-cdn', 'tag': 'SELF-cdn', 'enable': True, 'listen': '127.0.0.1',
            'port': host.XHTTP_PORT, 'protocol': 'vless', 'disableFlow': True,
            'settings': copy.deepcopy(main['settings']), 'sniffing': main['sniffing'],
            'streamSettings': json.dumps({'network': 'xhttp', 'security': 'none',
                'xhttpSettings': {'host': 'cdn.example.org', 'path': host.XHTTP_PATH, 'mode': host.XHTTP_MODE}})}
        self.save('input-xhttp-add.private.json', payload)
        api.call('/panel/api/inbounds/add', payload)
        current = api.call('/panel/api/inbounds/list')
        added = [i for i in current if i['id'] not in {i['id'] for i in inbounds}]
        self.assertEqual(len(added), 1)
        cdn = added[0]
        api.call('/panel/api/hosts/add', {'inboundIds': [cdn['id']], 'hosts': ['cdn.example.org'],
            'remark': 'SELF-cdn', 'port': host.CDN_PORT, 'security': 'tls', 'sni': 'cdn.example.org',
            'hostHeader': 'cdn.example.org', 'path': host.XHTTP_PATH, 'alpn': ['h2'], 'allowInsecure': False})
        groups = [g for g in api.call('/panel/api/hosts/list') if g['groupId'] not in groups_before]
        self.assertEqual(len(groups), 1)
        cert = fx.folder / 'caddy/data/caddy/certificates/fixture/cdn.example.org.crt'
        made = subprocess.run(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt',
            'ec_paramgen_curve:P-256', '-nodes', '-days', '1', '-subj', '/CN=cdn.example.org',
            '-addext', 'subjectAltName=DNS:cdn.example.org', '-addext', 'basicConstraints=critical,CA:TRUE',
            '-keyout', str(cert.with_suffix('.key')), '-out', str(cert)], capture_output=True, timeout=15)
        self.assertEqual(made.returncode, 0, 'Local CDN CA issuance failed')
        fx.manifest['tcp_cdn'] = dict(schema=1, domain='cdn.example.org', inbound_id=cdn['id'],
            host_group_id=groups[0]['groupId'], reality_port=port, reality_tag=self.original_tag,
            path=host.XHTTP_PATH, mode=host.XHTTP_MODE,
            certificate_file=str(cert.relative_to(fx.folder)), key_file=str(cert.with_suffix('.key').relative_to(fx.folder)))
        after = fx.records()
        for name, record in before.items():
            self.record_contract(record, after[name], earliest[name], ('updatedAt',))
        self.persisted_contract(old['clients'], fx.rows('clients'), ('created_at', 'updated_at'))
        h2_id = fx.manifest['hysteria2']['inbound_id']
        self.assertEqual([h for h in old['hosts'] if h['inbound_id'] == h2_id],
                         [h for h in fx.rows('hosts') if h['inbound_id'] == h2_id])
        self.assertEqual(next(i for i in old['inbounds'] if i['id'] == h2_id),
                         next(i for i in fx.rows('inbounds') if i['id'] == h2_id))
        self.assertEqual([j for j in fx.rows('client_inbounds') if j['inbound_id'] != cdn['id']], old['client_inbounds'])
        replaced = {'id', 'port', 'created_at', 'updated_at'}
        old_host = next(h for h in old['hosts'] if h['inbound_id'] == self.main_id)
        new_host = next(h for h in fx.rows('hosts') if h['inbound_id'] == self.main_id)
        self.assertNotEqual(old_host['id'], new_host['id'])
        self.assertEqual({k: v for k, v in old_host.items() if k not in replaced},
                         {k: v for k, v in new_host.items() if k not in replaced})
        self.assertEqual(new_host['port'], port)
        self.assertGreaterEqual(new_host['created_at'], old_host['created_at'])
        self.assertEqual(new_host['created_at'], new_host['updated_at'])
        before_h2 = next(i for i in inbounds if i['id'] == h2_id)
        after_h2 = next(i for i in current if i['id'] == h2_id)
        self.assertEqual(after_h2['clientStats'], [dict(s, inboundId=cdn['id']) for s in before_h2['clientStats']])
        self.assertEqual({k: v for k, v in before_h2.items() if k != 'clientStats'},
                         {k: v for k, v in after_h2.items() if k != 'clientStats'})
        # Complete supported native settings read/update selects the approved
        # THREE-node template, not a hand-built subscription serializer.
        settings = api.call('/panel/api/setting/all', {})
        settings['subClashRules'] = host.clash_rules(fx.manifest)
        api.call('/panel/api/setting/update', settings)
        self.assertEqual(api.call('/panel/api/setting/all', {})['subClashRules'], settings['subClashRules'])
        fx.fx.stop(); fx.fx.start(); fx.api()
        # Let actual ownership/account/output code attest the constructed INPUT.
        # Restart/tag preservation is part of input attestation, not migration.
        host.backend_health(fx.folder, fx.manifest)
        fx.manifest['published'] = True
        host.atomic(fx.folder / 'manifest.json', host.dumps(fx.manifest))
        host.front_config(fx.folder, fx.manifest, True)
        for name, record in before.items():
            self.record_contract(record, fx.records()[name], earliest[name], ('updatedAt',))
        self.save('input-records.private.json', fx.records())
        self.save('input-db.private.json', {t: fx.rows(t) for t in old})
        self.save('scope.json', {'input': 'already-triple via real native API/readbacks',
            'module_migration_commit_proof': False, 'public_cloudflare': False, 'production_443_acme': False,
            'createdAt_boundary': 'predicted earliest native timestamp; same second; delta<1000ms',
            'shared_client_stats': 'inboundId is new association, not membership or accounting accuracy'})

    def inbound_contract(self, expected, actual):
        # API JSON fields are decoded on GET; compare semantic complete objects.
        # clientStats is the documented shared association, not inbound policy.
        def policy(inbound):
            return {k: dual.decoded(v) if k in ('settings', 'streamSettings', 'sniffing') else v
                    for k, v in inbound.items() if k != 'clientStats'}
        old, new = policy(expected), policy(actual)
        changed = sorted(k for k in old.keys() | new.keys() if old.get(k) != new.get(k))
        self.assertFalse(changed, 'Native inbound fields changed across tag seam (values withheld): ' + ','.join(changed))

    def persisted_contract(self, before, after, except_fields=()):
        """Full persisted globals, including fields omitted from Client DTOs."""
        self.assertEqual({r['id'] for r in before}, {r['id'] for r in after}, 'Native global row IDs changed')
        for old in before:
            new = next(r for r in after if r['id'] == old['id'])
            changed = sorted(k for k in old.keys() | new.keys()
                             if k not in except_fields and old.get(k) != new.get(k))
            self.assertFalse(changed, 'Changed persisted Client fields (values withheld): ' + ','.join(changed))

    def seed_three_policy(self, name):
        # Main-ID scoped full DTO: CDN is another VLESS with empty effective
        # Flow, so NativeDual.dto's first-VLESS assumption is unsuitable here.
        api = self.fx.api()
        main = next(i for i in api.call('/panel/api/inbounds/list') if i['id'] == self.main_id)
        client = copy.deepcopy(next(c for c in dual.decoded(main['settings'])['clients'] if c['email'] == name))
        record = api.call('/panel/api/clients/get/' + name)['client']
        client.update(auth=record['auth'], limitHwid=record['limitHwid'], totalGB=1234567890,
            expiryTime=int(time.time() * 1000) + 86400000, limitIp=2, trafficReset='monthly',
            trafficResetDay=6, reset=7, resetMax=9, comment='three-policy', group='three-fixture')
        api.call('/panel/api/clients/update/' + name, client)
        self.topology()

    def control(self, action, *units):
        if action == 'daemon-reload':
            return
        if '--no-block' in units:
            self.assertEqual(action, 'start')
            self.assertEqual(units.count('--no-block'), 1)
            units = tuple(unit for unit in units if unit != '--no-block')
        for unit in units:
            if action in ('enable', 'disable') or unit in (host.RECOVER, host.TIMER):
                continue
            self.assertIn(unit, (host.PANEL, host.FRONT))
            self.assertIn(action, ('start', 'stop', 'restart'))
            if unit == host.PANEL:
                if action in ('stop', 'restart'):
                    self.fx.fx.stop()
                if action in ('start', 'restart'):
                    self.fx.fx.start()
                    self.fx.api()
            else:
                if action in ('stop', 'restart'):
                    self.stop_front()
                if action in ('start', 'restart'):
                    self.start_front()

    def origin(self):
        origin = self.fx.origin()
        # The shared traffic fixture inserts its narrow loopback-origin rule
        # first. Preserve the real module's management-rule-first invariant.
        self.fx.fx.stop()
        raw, template = host.auth_template(self.fx.folder)
        rule = host.management_rule(template, self.fx.manifest, 'SELF-h2')
        rules = template['routing']['rules']
        self.assertEqual(sum(item == rule for item in rules), 1)
        template['routing']['rules'] = [rule] + [item for item in rules if item != rule]
        host.settings_write(self.fx.folder / 'db/x-ui.db', {'xrayTemplateConfig': json.dumps(template)})
        self.fx.fx.start()
        host.hysteria_health(self.fx.folder, self.fx.manifest, self.fx.api())
        return origin

    def stop_front(self):
        if self.front is not None and self.front.poll() is None:
            os.killpg(self.front.pid, signal.SIGTERM)
            self.front.wait(timeout=8)
        self.front = None

    def start_front(self):
        if self.front is not None:
            self.assertIsNone(self.front.poll(), 'Owned Caddy unexpectedly stopped')
            return
        cert, key = host.cdn_certificate_paths(self.fx.folder, host.read_json(self.fx.folder / 'manifest.json'))
        folder = self.fx.fx.root / ('three-front-' + str(time.monotonic_ns()))
        folder.mkdir(mode=0o700)
        config, access = folder / 'Caddyfile', folder / 'access.jsonl'
        self.access_logs.append(access)
        # Match the service scheduler's finite stop: held XHTTP requests must
        # not make this disposable Caddy drain indefinitely before PANEL stops.
        config.write_text('{\n admin off\n auto_https off\n grace_period 1s\n servers {\n  protocols h1 h2\n }\n}\n' +
            'https://cdn.example.org:%d {\n bind 127.0.0.1\n tls %s %s\n log {\n  output file %s\n  format json\n }\n' %
            (host.CDN_PORT, cert, key, access) +
            ' @xhttp path %s %s*\n handle @xhttp {\n  reverse_proxy 127.0.0.1:%d {\n   flush_interval -1\n  }\n }\n respond 404\n}\n' %
            (host.XHTTP_PATH, host.XHTTP_PATH, host.XHTTP_PORT))
        self.front = self.spawn([str(self.fx.folder / 'bin/caddy'), 'run', '--config', str(config), '--adapter', 'caddyfile'],
            folder, dict(os.environ), 'serving initial configuration', folder / 'private.log')
        context = ssl.create_default_context(cafile=str(cert))
        with socket.create_connection(('127.0.0.1', host.CDN_PORT), timeout=4) as raw:
            with context.wrap_socket(raw, server_hostname='cdn.example.org') as stream:
                stream.sendall(b'GET / HTTP/1.1\r\nHost: cdn.example.org\r\nConnection: close\r\n\r\n')
                self.assertTrue(stream.recv(1024).startswith(b'HTTP/1.1 404'))

    def start_reality_target(self):
        folder = self.fx.fx.root / ('three-target-' + str(time.monotonic_ns()))
        folder.mkdir(mode=0o700)
        cert, key = self.fx.certificate_paths(self.fx.folder, self.fx.manifest)
        config = folder / 'Caddyfile'
        config.write_text('{\n admin off\n auto_https off\n servers {\n  protocols h1 h2\n }\n}\n' +
            'https://target.example.org:%d {\n bind 127.0.0.1\n tls %s %s\n respond "fixture-target"\n}\n' %
            (self.target_port, cert, key))
        self.spawn([str(self.fx.folder / 'bin/caddy'), 'run', '--config', str(config), '--adapter', 'caddyfile'],
            folder, dict(os.environ), 'serving initial configuration', folder / 'private.log')

    @contextmanager
    def reality_coordinate(self):
        # Traffic-only target redirection. Restore the exact native object before
        # any module ownership/CRUD/archive operation; never mock its validator.
        api = self.fx.api()
        original = next(i for i in api.call('/panel/api/inbounds/list') if i['id'] == self.main_id)
        changed = copy.deepcopy(original)
        stream = dual.decoded(changed['streamSettings'])
        stream['realitySettings']['dest'] = '127.0.0.1:' + str(self.target_port)
        changed['streamSettings'] = json.dumps(stream)
        api.call('/panel/api/inbounds/update/' + str(self.main_id), changed)
        try:
            self.await_core_started()
            yield
        finally:
            # Full fresh DTO avoids writing stale policy after unrelated calls.
            restored = next(i for i in api.call('/panel/api/inbounds/list') if i['id'] == self.main_id)
            fresh = dual.decoded(restored['streamSettings'])
            fresh['realitySettings']['dest'] = dual.decoded(original['streamSettings'])['realitySettings']['dest']
            restored['streamSettings'] = json.dumps(fresh)
            api.call('/panel/api/inbounds/update/' + str(self.main_id), restored)

    def topology(self):
        fx = self.fx
        self.assertEqual(host.transports(fx.manifest), ['tcp-reality', 'tcp-xhttp-cdn', 'hysteria2'])
        inbounds, clients = fx.rows('inbounds'), fx.rows('clients')
        joins, hosts = fx.rows('client_inbounds'), fx.rows('hosts')
        ids = {i['id'] for i in inbounds}
        self.assertEqual(len(inbounds), 3)
        self.assertEqual(ids, {self.main_id, fx.manifest['tcp_cdn']['inbound_id'], fx.manifest['hysteria2']['inbound_id']})
        self.assertEqual(len(hosts), 3)
        self.assertEqual({h['inbound_id'] for h in hosts}, ids)
        self.assertTrue(all(not h['is_hidden'] and not h['is_disabled'] for h in hosts))
        expected = {(c['id'], i) for c in clients for i in ids}
        self.assertEqual(len(joins), len(expected))
        self.assertEqual({(j['client_id'], j['inbound_id']) for j in joins}, expected)
        self.assertTrue(all(j['flow_override'] == (VISION if j['inbound_id'] == self.main_id else '') for j in joins))
        self.assertEqual(fx.manifest['host_group_id'], self.original_group)
        self.assertEqual(fx.manifest['tcp_cdn']['reality_tag'], self.original_tag)
        for row in clients:
            self.assertEqual(row['flow'], VISION)
        api = fx.api()
        main = next(i for i in api.call('/panel/api/inbounds/list') if i['id'] == self.main_id)
        self.assertEqual((main['tag'], main['port'], main['remark']),
                         (self.original_tag, fx.manifest['tcp_cdn']['reality_port'], 'SELF-main'))
        self.assertTrue(dual.decoded(main['streamSettings'])['realitySettings']['privateKey'] == fx.manifest['reality']['privateKey'],
                        'Native REALITY key differs from manifest (values withheld)')
        for role, inbound_id, group_id, address, port in (
                ('reality', self.main_id, self.original_group, fx.manifest['endpoint'], fx.manifest['tcp_cdn']['reality_port']),
                ('cdn', fx.manifest['tcp_cdn']['inbound_id'], fx.manifest['tcp_cdn']['host_group_id'], 'cdn.example.org', host.CDN_PORT),
                ('h2', fx.manifest['hysteria2']['inbound_id'], fx.manifest['hysteria2']['host_group_id'], fx.manifest['endpoint'], host.CDN_PORT)):
            group = api.call('/panel/api/hosts/get/' + group_id)
            self.assertEqual((group['inboundIds'], group['hosts'], group['port']), ([inbound_id], [address + ':' + str(port)], port), role)
        host.snapshot_accounts(fx.folder / 'db/x-ui.db', fx.manifest)
        host.snapshot_host_group(fx.folder / 'db/x-ui.db', fx.manifest)
        host.owned_host_group(fx.folder, api, fx.manifest)
        self.generated()

    def generated(self):
        config, records = self.fx.api().call('/panel/api/server/getConfigJson'), self.fx.records()
        expected = {r['uuid'] for r in records.values() if r['enable']}
        expected_auth = {r['auth'] for r in records.values() if r['enable']}
        real = next(i for i in config['inbounds'] if i.get('tag') == self.original_tag)
        cdn = next(i for i in config['inbounds'] if i.get('tag') == 'SELF-cdn')
        h2 = next(i for i in config['inbounds'] if i.get('tag') == 'SELF-h2')
        for inbound, flow in ((real, VISION), (cdn, '')):
            self.assertTrue({c['id'] for c in inbound['settings']['clients']} == expected,
                            'Generated VLESS authenticators differ from global authority (values withheld)')
            self.assertTrue(all(c.get('flow', '') == flow for c in inbound['settings']['clients']))
        self.assertTrue({c['auth'] for c in h2['settings']['clients']} == expected_auth,
                        'Generated H2 authenticators differ from global authority (values withheld)')
        self.assertEqual((real['port'], h2['port']), (self.fx.manifest['tcp_cdn']['reality_port'], host.CDN_PORT))
        self.assertEqual((cdn['listen'], cdn['port'], cdn['streamSettings']['security']), ('127.0.0.1', host.XHTTP_PORT, 'none'))
        self.save('generated-' + str(time.monotonic_ns()) + '.private.json', config)
        return config

    def exports(self, name='company'):
        import yaml
        record = self.fx.records()[name]
        files = {fmt: host.get_feed(self.fx.manifest, name, fmt) for fmt in ('mihomo', 'sub')}
        for fmt, raw in files.items():
            host.validate_feed_scope(self.fx.manifest, name, raw, fmt)
            host.atomic(self.evidence / (name + '-' + str(time.monotonic_ns()) + '.native.' + fmt), raw)
        profile = yaml.safe_load(files['mihomo'])
        nodes = native_nodes(profile)
        reality, cdn, h2 = (nodes[r] for r in ROLES)
        self.assertTrue({reality['uuid'], cdn['uuid']} == {record['uuid']}, 'Native VLESS UUID mismatch (values withheld)')
        self.assertTrue(h2['password'] == record['auth'], 'Native H2 Auth mismatch (values withheld)')
        self.assertEqual((reality['server'], reality['port'], reality['flow']),
                         (self.fx.manifest['endpoint'], self.fx.manifest['tcp_cdn']['reality_port'], VISION))
        self.assertEqual((cdn['server'], cdn['port'], cdn['network'], cdn['tls']), ('cdn.example.org', host.CDN_PORT, 'xhttp', True))
        self.assertEqual(cdn['xhttp-opts'], {'host': 'cdn.example.org', 'path': host.XHTTP_PATH, 'mode': host.XHTTP_MODE})
        self.assertEqual((cdn['servername'], cdn['alpn']), ('cdn.example.org', ['h2']))
        self.assertFalse(cdn.get('flow'))
        self.assertEqual((h2['server'], h2['port'], h2['sni']), (self.fx.manifest['endpoint'], host.CDN_PORT, 'sub.example.org'))
        self.assertTrue(all(not n.get('skip-cert-verify', False) for n in nodes.values()))
        group = next(g for g in profile['proxy-groups'] if g['name'] == 'PROXY')
        self.assertEqual(group['proxies'], [h2['name'], cdn['name'], reality['name'], 'DIRECT'])
        feed = next(g for g in profile['proxy-groups'] if g['name'] == 'FEED')
        self.assertEqual(feed['proxies'], [h2['name'], 'DIRECT'])
        links = [urlsplit(line) for line in base64.b64decode(files['sub'], validate=True).decode().splitlines()]
        self.assertEqual(len(links), 3)
        self.assertEqual(sorted(p.scheme for p in links), ['hysteria2', 'vless', 'vless'])
        h_uri = next(p for p in links if p.scheme == 'hysteria2')
        r_uri = next(p for p in links if parse_qs(p.query).get('security') == ['reality'])
        x_uri = next(p for p in links if parse_qs(p.query).get('type') == ['xhttp'])
        self.assertTrue((r_uri.username, r_uri.hostname, r_uri.port) == (record['uuid'], reality['server'], reality['port']),
                        'Native REALITY URI identity/endpoint mismatch (values withheld)')
        self.assertEqual(parse_qs(r_uri.query)['flow'], [VISION])
        self.assertTrue((x_uri.username, x_uri.hostname, x_uri.port) == (record['uuid'], 'cdn.example.org', host.CDN_PORT),
                        'Native CDN URI identity/endpoint mismatch (values withheld)')
        self.assertEqual(json.loads(parse_qs(x_uri.query)['extra'][0]), {'mode': host.XHTTP_MODE})
        self.assertFalse(parse_qs(x_uri.query).get('flow'))
        self.assertTrue(unquote(h_uri.username) == record['auth'], 'Native H2 URI Auth mismatch (values withheld)')
        return profile, files

    def client(self, profile, role, owner):
        directory = self.fx.fx.root / ('three-client-' + str(time.monotonic_ns()))
        directory.mkdir(mode=0o700)
        self.client_directories.append(directory)
        port = self.fx.manifest['tcp_cdn']['reality_port'] if role == 'reality' else host.CDN_PORT
        node = coordinate_node(profile, role, '127.0.0.1', port)
        cert = (host.cdn_certificate_paths(self.fx.folder, self.fx.manifest)[0] if role == 'cdn'
                else self.fx.certificate_paths(self.fx.folder, self.fx.manifest)[0])
        config = {'mixed-port': native.free_port(), 'bind-address': '127.0.0.1', 'allow-lan': False,
            'log-level': 'debug', 'dns': {'enable': False}, 'tun': {'enable': False}, 'proxies': [node],
            'proxy-groups': [{'name': 'PROXY', 'type': 'select', 'proxies': [node['name']]}], 'rules': ['MATCH,PROXY']}
        mixed = native.start_mihomo(owner, directory, config, cert)
        self.save('readiness-' + str(time.monotonic_ns()) + '.json', owner._mihomo_readiness[-1])
        return mixed

    def fresh(self, profile, role, origin, expected):
        owner = unittest.TestCase()
        before = len(origin.peers)
        try:
            port = self.client(profile, role, owner)
            result = subprocess.run(['curl', '--silent', '--show-error', '--max-time', '4', '--noproxy', '',
                '--proxy', 'socks5h://127.0.0.1:' + str(port), '--cacert', str(origin.trust_file),
                '--connect-to', 'sub.example.org:' + str(origin.server_port) + ':127.0.0.1:' + str(origin.server_port),
                'https://sub.example.org:' + str(origin.server_port) + '/'], capture_output=True, timeout=6)
            host.atomic(self.evidence / ('curl-' + role + '-' + str(time.monotonic_ns()) + '.private.log'), result.stderr)
            works = result.returncode == 0 and result.stdout == b'native-dual-https-egress\n'
            self.assertEqual(works, expected, role + ' fresh native HTTPS; curl_exit=' + str(result.returncode))
            self.assertEqual(len(origin.peers) > before, expected, role + ' authenticated origin-hit boundary')
        finally:
            owner.doCleanups()

    def all_three(self, profile, origin, expected=True):
        self.await_core_started()
        with self.reality_coordinate():
            self.fresh(profile, 'reality', origin, expected)
        for role in ('cdn', 'h2'):
            self.fresh(profile, role, origin, expected)

    def wrong_uuid(self, profile, origin):
        def normalized(value):
            raw = bytearray(uuid.UUID(value).bytes)
            raw[6:8] = b'\0\0'
            return bytes(raw)
        registered = {normalized(r['uuid']) for r in self.fx.records().values()}
        nonce = str(uuid.uuid4())
        while normalized(nonce) in registered:
            nonce = str(uuid.uuid4())
        negative = copy.deepcopy(profile)
        native_nodes(negative)['cdn']['uuid'] = nonce
        log = self.fx.folder / 'logs/dual-error.log'
        offset = log.stat().st_size
        self.fresh(negative, 'cdn', origin, False)
        with log.open('rb') as source:
            source.seek(offset)
            lines = source.read().decode(errors='replace').splitlines()
        verdict = [line for line in lines if 'invalid request user id: ' + nonce in line]
        self.assertTrue(verdict, 'Missing exact fresh native wrong-UUID nonce verdict')
        self.assertTrue(all(re.search(r'\[\d+\]', line) for line in verdict))
        self.save('wrong-uuid-' + str(time.monotonic_ns()) + '.private.json', {'nonce': nonce, 'offset': offset, 'verdict': verdict})
        self.fresh(profile, 'cdn', origin, True)

    def held_stream(self, profile, role, origin):
        """Existing native SOCKS tunnel mechanism, with real inner TLS/keepalive."""
        owner = unittest.TestCase()
        self.addCleanup(owner.doCleanups)
        port = self.client(profile, role, owner)
        raw = socket.create_connection(('127.0.0.1', port), timeout=4)
        self.addCleanup(raw.close)
        raw.sendall(b'\x05\x01\x00')
        self.assertEqual(raw.recv(2), b'\x05\x00')
        raw.sendall(b'\x05\x01\x00\x01\x7f\x00\x00\x01' + origin.server_port.to_bytes(2, 'big'))
        reply = raw.recv(10)
        self.assertTrue(len(reply) == 10 and reply[1] == 0, 'Native SOCKS CONNECT failed')
        stream = ssl.create_default_context(cafile=str(origin.trust_file)).wrap_socket(raw, server_hostname='sub.example.org')
        self.addCleanup(stream.close)
        self.assert_stream_alive(stream)
        return stream

    def assert_stream_alive(self, stream):
        stream.sendall(b'GET / HTTP/1.1\r\nHost: sub.example.org\r\nConnection: keep-alive\r\n\r\n')
        data = b''
        while b'\r\n\r\n' not in data:
            chunk = stream.recv(4096)
            self.assertTrue(chunk, 'Existing stream closed before mutation')
            data += chunk
        headers, body = data.split(b'\r\n\r\n', 1)
        self.assertTrue(headers.startswith(b'HTTP/1.1 200'))
        length = int(next(line.split(b':', 1)[1] for line in headers.split(b'\r\n') if line.lower().startswith(b'content-length:')))
        while len(body) < length:
            chunk = stream.recv(4096)
            self.assertTrue(chunk, 'Existing stream body truncated before mutation')
            body += chunk
        self.assertEqual(body, b'native-dual-https-egress\n')

    def assert_stream_revoked(self, stream, origin):
        hits = len(origin.peers)  # The origin records EVERY HTTP request.
        try:
            stream.sendall(b'GET /revoked-stream HTTP/1.1\r\nHost: sub.example.org\r\n\r\n')
            self.assertEqual(stream.recv(4096), b'', 'Revoked established stream still usable')
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, ssl.SSLEOFError, ssl.SSLZeroReturnError, TimeoutError):
            # Mihomo may retain its local SOCKS socket after the server tunnel
            # is gone. No response AND no authenticated origin request is the
            # revocation boundary; local EOF timing is not server authority.
            pass
        finally:
            stream.close()
        self.assertEqual(len(origin.peers), hits, 'Revoked held stream reached authenticated origin')

    def http2_evidence(self):
        entries = [json.loads(line) for path in self.access_logs if path.exists()
                   for line in path.read_text().splitlines() if line.strip()]
        requests = [entry.get('request', {}) for entry in entries]
        for method in ('POST', 'GET'):
            self.assertTrue(any(r.get('method') == method and r.get('proto') == 'HTTP/2.0' and
                r.get('tls', {}).get('proto') == 'h2' for r in requests), 'Missing actual Caddy TLS/h2 ' + method)

    def retain(self):
        if self.fx.fx.log and not self.fx.fx.log.closed:
            self.fx.fx.log.flush()
        if self.fx.folder.exists():
            shutil.copytree(self.fx.folder, self.evidence / 'fixture', dirs_exist_ok=True)
        for index, directory in enumerate(self.client_directories):
            shutil.copytree(directory, self.evidence / ('client-' + str(index)), dirs_exist_ok=True)
        for index, path in enumerate(self.access_logs):
            if path.exists():
                host.atomic(self.evidence / ('front-' + str(index) + '.private.jsonl'), path.read_bytes())
        for root, dirs, files in os.walk(self.evidence):
            Path(root).chmod(0o700)
            for name in files:
                (Path(root) / name).chmod(0o600)


@unittest.skipUnless(os.environ.get('QD_VPS_ARTIFACTS') and os.environ.get('QD_NATIVE_CDN_EVIDENCE'), REQUIRED)
class NativeThreeDevices(ThreeFixture, unittest.TestCase):
    def test_actual_module_add_rename_reset_remove_all_three(self):
        fx = self.fx
        original, joins = fx.records(), fx.rows('client_inbounds')
        original_globals = fx.rows('clients')
        fx.device('add', 'tablet')
        self.topology()
        self.assertEqual(len(fx.rows('clients')), len(original) + 1)
        self.assertTrue(all(j in fx.rows('client_inbounds') for j in joins))
        for name, record in original.items():
            self.unchanged(record, fx.records()[name])
        self.persisted_contract(original_globals, [r for r in fx.rows('clients') if r['email'] != 'tablet'])
        self.seed_three_policy('tablet')
        host.backend_health(fx.folder, fx.manifest)
        before, earliest = fx.records(), self.earliest(fx.records())
        profile, _ = self.exports('tablet')
        origin = self.origin()
        origin.RequestHandlerClass.protocol_version = 'HTTP/1.1'
        self.all_three(profile, origin)
        self.wrong_uuid(profile, origin)
        rename_globals = fx.rows('clients')
        rename_joins = fx.rows('client_inbounds')
        fx.device('rename', 'tablet', 'reader')
        renamed = fx.records()
        self.record_contract(before['tablet'], renamed['reader'], earliest['tablet'], ('email', 'updatedAt'))
        self.assertEqual(renamed['reader']['id'], before['tablet']['id'])
        self.persisted_contract(rename_globals, fx.rows('clients'), ('email', 'created_at', 'updated_at'))
        self.assertEqual(fx.rows('client_inbounds'), rename_joins, 'Rename changed full join metadata')
        for name, record in original.items():
            self.unchanged(record, renamed[name])
        self.topology(); self.exports('reader')
        self.all_three(profile, origin)  # exact pre-rename native maps remain valid
        earliest = self.earliest(renamed)
        reset_globals = fx.rows('clients')
        reset_joins = fx.rows('client_inbounds')
        reset = fx.device('reset', 'reader')
        current = fx.records()
        self.record_contract(renamed['reader'], current['reader'], earliest['reader'], ('uuid', 'auth', 'subId', 'updatedAt'))
        self.assertTrue(all(current['reader'][k] != renamed['reader'][k] for k in ('uuid', 'auth', 'subId')))
        self.persisted_contract(reset_globals, fx.rows('clients'), ('uuid', 'auth', 'sub_id', 'created_at', 'updated_at'))
        self.assertEqual(fx.rows('client_inbounds'), reset_joins, 'Reset changed full join metadata')
        for name, record in original.items():
            self.unchanged(record, current[name])
        self.topology()
        stable = copy.deepcopy(current)
        self.assertTrue(fx.fx.device_prepare(reset)['committed'])
        self.assertTrue(fx.records() == stable, 'Reset replay rotated credentials more than once (values withheld)')
        self.all_three(profile, origin, False)
        fresh, _ = self.exports('reader')
        self.all_three(fresh, origin)
        # Keep three actual authenticated streams. Restore handshake coordinate
        # BEFORE the module mutation, and prove each stream still alive then.
        with self.reality_coordinate():
            held = {'reality': self.held_stream(fresh, 'reality', origin)}
        held.update({role: self.held_stream(fresh, role, origin) for role in ('cdn', 'h2')})
        for stream in held.values():
            self.assert_stream_alive(stream)
        previous_panel = fx.fx.proc
        fx.device('remove', 'reader')
        self.assertIsNotNone(previous_panel.poll(), 'Old native panel/core process group still running')
        self.assertNotEqual(fx.fx.proc.pid, previous_panel.pid)
        for stream in held.values():
            self.assert_stream_revoked(stream, origin)
        self.assertNotIn('reader', fx.records())
        self.assertTrue(all(c['id'] != stable['reader']['id'] for c in fx.rows('clients')))
        self.assertTrue(all(j['client_id'] != stable['reader']['id'] for j in fx.rows('client_inbounds')))
        self.topology()
        self.all_three(fresh, origin, False)
        for name, record in original.items():
            self.unchanged(record, fx.records()[name])
        control, _ = self.exports('company')
        self.all_three(control, origin)
        self.http2_evidence()


@unittest.skipUnless(os.environ.get('QD_VPS_ARTIFACTS') and os.environ.get('QD_NATIVE_CDN_EVIDENCE'), REQUIRED)
class NativeThreeCold(ThreeFixture, unittest.TestCase):
    def cold(self, mode):
        fx = self.fx
        before, joins = fx.records(), fx.rows('client_inbounds')
        global_rows = fx.rows('clients')
        earliest = self.earliest(before)
        original = copy.deepcopy(fx.manifest)
        hosts = fx.rows('hosts')
        old_profile, old_files = self.exports()
        cert_bytes = {original[role][key]: (fx.folder / original[role][key]).read_bytes()
                      for role in ('tcp_cdn', 'hysteria2') for key in ('certificate_file', 'key_file')}
        exported, _, verified = fx.archive()
        archive = self.evidence / ('original-closed-triple-' + mode + '.tar')
        shutil.copy2(exported, archive); archive.chmod(0o600)
        old_digest = host.digest(archive)
        self.assertEqual(verified['tcp_cdn'], original['tcp_cdn'])
        source_origin = self.origin()
        self.all_three(old_profile, source_origin)
        self.stop_front(); fx.fx.stop()
        target, units = fx.fx.root / ('fresh-three-' + mode), fx.fx.root / ('fresh-three-units-' + mode)
        units.mkdir(mode=0o700)
        endpoint = '93.184.216.35' if mode == 'restore' else '93.184.216.36'
        request = dict(owner=original['owner'], endpoint=endpoint, config=original['config'], mode=mode,
            lock=fx.fx.lock, machine_id=host.machine_id(), ssh_port=22,
            adapter_sha256=host.digest(vps.MODULE / 'lib/host.py'))
        info = dict(machine_id=host.machine_id(), owned=False, endpoint=endpoint, ssh_port=22)
        # Dedicated-guest/OS scheduling and external public target preflight are
        # boundaries. Strict local-CA REALITY target traffic is exercised above
        # and below; prepare/configure/ownership/API/archive/commit stay real.
        with mock.patch.object(host, 'ROOT', target), mock.patch.object(host, 'UNITS', units), \
                mock.patch.object(host, 'preflight', return_value=info), \
                mock.patch.object(host, 'check_target', side_effect=lambda config: self.assertEqual(
                    config['reality_target'], 'target.example.org')):
            fx.fx.work = target / 'active'
            with vps.upload_bundle(request, backup=archive) as stream:
                prepared = host.prepare(stream)
            self.assertFalse(prepared['unchanged'])
            fx.fx.manifest = host.read_json(fx.folder / 'manifest.json')
            self.assertEqual(fx.manifest['tcp_cdn'], original['tcp_cdn'])
            self.assertEqual(fx.manifest['hysteria2'], original['hysteria2'])
            self.assertEqual(fx.manifest['endpoint'], endpoint)
            self.assertEqual(fx.manifest['config'], original['config'])
            self.assertEqual(fx.rows('client_inbounds'), joins, 'Cold path changed complete join metadata')
            self.topology()
            after = fx.records()
            allowed_db = ('created_at', 'updated_at', 'uuid', 'auth') if mode == 'replace' else ('created_at', 'updated_at')
            self.persisted_contract(global_rows, fx.rows('clients'), allowed_db)
            for name, record in before.items():
                allowed = ('uuid', 'auth', 'updatedAt') if mode == 'replace' else ('updatedAt',)
                self.record_contract(record, after[name], earliest[name], allowed)
                self.assertTrue(after[name]['subId'] == record['subId'], 'Cold path changed SubID (values withheld)')
                for key in ('uuid', 'auth'):
                    self.assertEqual(after[name][key] == record[key], mode == 'restore')
            for key in ('privateKey', 'publicKey', 'shortIds'):
                self.assertEqual(fx.manifest['reality'][key] == original['reality'][key], mode == 'restore')
            for relative, data in cert_bytes.items():
                self.assertTrue((fx.folder / relative).read_bytes() == data, 'Cold path changed certificate/key bytes (values withheld)')
            # Native Host updates recreate physical ID+auto timestamps only.
            # Every other full Host policy, logical group and inbound is exact.
            current_hosts = fx.rows('hosts')
            for old in hosts:
                current = next(h for h in current_hosts if h['inbound_id'] == old['inbound_id'])
                expected = dict(old)
                if old['inbound_id'] != original['tcp_cdn']['inbound_id']:
                    expected['address'] = endpoint
                    ignored = {'id', 'created_at', 'updated_at'}
                    self.assertGreaterEqual(current['created_at'], old['created_at'])
                    self.assertEqual(current['created_at'], current['updated_at'])
                else:
                    ignored = set()
                self.assertEqual({k: v for k, v in current.items() if k not in ignored},
                                 {k: v for k, v in expected.items() if k not in ignored})
            config = self.generated()
            h2 = next(i for i in config['inbounds'] if i.get('tag') == 'SELF-h2')
            cert, key = fx.certificate_paths(fx.folder, fx.manifest)
            self.assertEqual(h2['streamSettings']['tlsSettings']['certificates'],
                [{'certificateFile': str(cert), 'keyFile': str(key), 'oneTimeLoading': False}])
            profile, files = self.exports()
            if mode == 'restore':
                for fmt in files:
                    old = base64.b64decode(old_files[fmt]) if fmt == 'sub' else old_files[fmt]
                    new = base64.b64decode(files[fmt]) if fmt == 'sub' else files[fmt]
                    self.assertTrue(new == old.replace(original['endpoint'].encode(), endpoint.encode()),
                                    'Native restore bytes changed beyond numeric endpoint relocation (values withheld)')
            candidate, _, _ = fx.archive()
            committed = host.commit(dict(request, offnode_sha256=host.digest(candidate)))
            self.assertTrue(committed['published'])
            self.assertTrue(host.commit(dict(request, offnode_sha256=host.digest(candidate)))['published'])
            self.assertTrue(fx.records() == after, 'Repeated publication rerotated native credentials (values withheld)')
            fx.fx.manifest = host.read_json(fx.folder / 'manifest.json')
            origin = self.origin()
            self.all_three(old_profile, origin, mode == 'restore')
            self.all_three(profile, origin)
            self.assertTrue(fx.records() == after, 'Commit/health rerotated identities/policies (values withheld)')
            self.assertEqual(host.digest(exported), old_digest, 'Original source closed archive changed')
            self.assertEqual(host.digest(archive), old_digest, 'Retained original archive changed')
            self.http2_evidence()
            self.stop_front(); fx.fx.stop()

    def test_actual_cold_restore_preserves_three_roles_and_native_exports(self):
        self.cold('restore')

    def test_actual_cold_replace_rotates_once_preserves_subids_and_policies(self):
        self.cold('replace')


if __name__ == '__main__':
    unittest.main()
