"""Pinned three-entry native capability; loopback/high ports, no Cloudflare claim.

Explicit QD_VPS_ARTIFACTS and private QD_NATIVE_CDN_EVIDENCE are required.
Run serially: this fixture exclusively owns native API 127.0.0.1:62789.
Production dual validators are deliberately not used after the topology change.
"""
import base64
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import time
import unittest
import uuid
from urllib.parse import parse_qs, unquote, urlsplit

import test_native_cdn as cdn

host, vps, native, dual = cdn.host, cdn.vps, cdn.native, cdn.dual
VISION = 'xtls-rprx-vision'


@unittest.skipUnless(os.environ.get('QD_VPS_ARTIFACTS') and os.environ.get('QD_NATIVE_CDN_EVIDENCE'),
                     'Explicit locked artifacts and private evidence directory required')
class NativeCDNTriple(unittest.TestCase):
    # Compose the already-correct shared private ApplyConfig/204 client barrier,
    # digest checks, event readiness and private retention, not its two-node test.
    setUpClass = classmethod(cdn.NativeCDN.setUpClass.__func__)
    setUp = cdn.NativeCDN.setUp
    retain = cdn.NativeCDN.retain
    spawn = cdn.NativeCDN.spawn
    client = cdn.NativeCDN.client
    https = cdn.NativeCDN.https
    await_core_started = cdn.NativeCDN.await_core_started

    def save(self, name, value):
        (self.evidence / name).write_text(json.dumps(value, indent=2))

    def front(self, port, backend=None):
        """Actual trusted TLS/h2 Caddy, with no UDP/h3 listener."""
        folder = self.fx.folder / ('triple-front-' + str(port))
        folder.mkdir()
        if backend is None:
            domain = 'target.example.org'
            cert, key = self.fx.certificate_paths(self.fx.folder, self.fx.manifest)
            handler = ' respond "reality-target"\n'
        else:
            domain = 'cdn.example.org'
            cert, key = folder / 'ca.pem', folder / 'key.pem'
            result = subprocess.run(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt',
                'ec_paramgen_curve:P-256', '-nodes', '-days', '1', '-subj', '/CN=' + domain,
                '-addext', 'subjectAltName=DNS:' + domain, '-addext', 'basicConstraints=critical,CA:TRUE',
                '-keyout', str(key), '-out', str(cert)], capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, 'Fixture CA generation failed')
            handler = ' reverse_proxy 127.0.0.1:%d {\n  flush_interval -1\n }\n' % backend
        access = folder / 'access.jsonl'
        config = folder / 'Caddyfile'
        config.write_text('{\n admin off\n auto_https off\n servers {\n  protocols h1 h2\n }\n}\n' +
            'https://%s:%d {\n bind 127.0.0.1\n tls %s %s\n log {\n  output file %s\n  format json\n }\n%s}\n' %
            (domain, port, cert, key, access, handler))
        self.spawn([str(self.fx.folder / 'bin/caddy'), 'run', '--config', str(config), '--adapter', 'caddyfile'],
                   folder, dict(os.environ), 'serving initial configuration', folder / 'private.log')
        return cert, access

    def triple_exports(self, before, baseline, reality_port, front_port):
        import yaml
        profiles = {}
        for name, account in before.items():
            raw = host.get_feed(self.fx.manifest, name, 'mihomo')
            (self.evidence / (name + '.native.yaml')).write_bytes(raw)
            profile = yaml.safe_load(raw)
            nodes = profile['proxies']
            self.assertEqual(len(nodes), 3, 'Exactly three native device-scoped nodes required')
            self.assertEqual(len({p['name'] for p in nodes}), 3)
            reality = next(p for p in nodes if p.get('reality-opts'))
            h2 = next(p for p in nodes if p['type'] == 'hysteria2')
            xhttp = next(p for p in nodes if p.get('network') == 'xhttp')
            original = baseline[name]['proxies']
            old_reality = next(p for p in original if p['type'] == 'vless')
            self.assertEqual(reality, dict(old_reality, port=reality_port))
            self.assertEqual(h2, next(p for p in original if p['type'] == 'hysteria2'))
            self.assertEqual(reality['flow'], VISION)
            self.assertEqual(xhttp['uuid'], account['uuid'])
            self.assertEqual((xhttp['server'], xhttp['port'], xhttp['tls']), ('cdn.example.org', front_port, True))
            self.assertEqual(xhttp['servername'], 'cdn.example.org')
            self.assertEqual(xhttp['alpn'], ['h2'])
            self.assertFalse(xhttp.get('flow'))
            self.assertFalse(xhttp.get('skip-cert-verify', False))
            self.assertEqual(xhttp['xhttp-opts'], {'path': '/native-xhttp', 'host': 'cdn.example.org', 'mode': 'packet-up'})
            self.assertEqual({p['uuid'] for p in nodes if p['type'] == 'vless'}, {account['uuid']})
            self.assertEqual(h2['password'], account['auth'])
            group = next(g for g in profile['proxy-groups'] if g['name'] == 'PROXY')
            self.assertEqual(group['proxies'][0], h2['name'], 'Existing H2 preference changed')
            raw = host.get_feed(self.fx.manifest, name, 'sub')
            (self.evidence / (name + '.native.sub')).write_bytes(raw)
            links = [urlsplit(line) for line in base64.b64decode(raw, validate=True).decode().splitlines()]
            self.assertEqual(len(links), 3)
            self.assertEqual(sorted(p.scheme for p in links), ['hysteria2', 'vless', 'vless'])
            h2_uri = next(p for p in links if p.scheme == 'hysteria2')
            self.assertEqual(unquote(h2_uri.username), account['auth'])
            old_links = [urlsplit(line) for line in base64.b64decode(self.baseline_uri[name], validate=True).decode().splitlines()]
            self.assertEqual(h2_uri, next(p for p in old_links if p.scheme == 'hysteria2'))
            r_uri = next(p for p in links if parse_qs(p.query).get('security') == ['reality'])
            old_uri = next(p for p in old_links if p.scheme == 'vless')
            self.assertEqual(r_uri.username, account['uuid'])
            self.assertEqual(r_uri.port, reality_port)
            self.assertEqual(parse_qs(r_uri.query), parse_qs(old_uri.query))
            self.assertEqual(parse_qs(r_uri.query)['flow'], [VISION])
            x_uri = next(p for p in links if parse_qs(p.query).get('type') == ['xhttp'])
            self.assertEqual((x_uri.username, x_uri.hostname, x_uri.port), (account['uuid'], 'cdn.example.org', front_port))
            query = parse_qs(x_uri.query)
            for key, value in {'security': 'tls', 'sni': 'cdn.example.org', 'alpn': 'h2', 'path': '/native-xhttp', 'mode': 'packet-up'}.items():
                self.assertEqual(query.get(key), [value])
            self.assertEqual(json.loads(query['extra'][0]), {'mode': 'packet-up'})
            self.assertFalse(query.get('flow'))
            profiles[name] = profile
        return profiles

    def test_retained_reality_vision_h2_and_added_xhttp(self):
        import yaml
        fx = self.fx
        self.summary['lock_digests'] = self.lock_digests
        version = subprocess.run([vps.resolve_mihomo(), '-v'], capture_output=True, timeout=5)
        self.assertIn(b'v1.19.32', version.stdout)
        fx.seed_policy()
        host.backend_health(fx.folder, fx.manifest)
        fx.enable(); fx.topology()
        baseline = {name: fx.profile(name) for name in fx.manifest['accounts']}
        fx.assert_native_exports()
        self.baseline_uri = {name: host.get_feed(fx.manifest, name, 'sub') for name in baseline}
        before = fx.records()
        old_rows = {table: fx.rows(table) for table in ('clients', 'client_inbounds', 'hosts', 'inbounds')}
        self.save('before-records.json', before); self.save('before-db.json', old_rows)
        old_inbounds = fx.api().call('/panel/api/inbounds/list')
        self.save('before-api-inbounds.json', old_inbounds)
        earliest = {name: account['createdAt'] for name, account in before.items()}
        for inbound in old_inbounds:
            for client in dual.decoded(inbound['settings'])['clients']:
                if client.get('created_at', 0) > 0:
                    earliest[client['email']] = min(earliest[client['email']], client['created_at'])
        archive, _, _ = fx.archive()
        import shutil
        shutil.copy2(archive, self.evidence / 'original-closed-dual.tar')
        archive_sha = host.digest(archive)
        self.summary['original_archive_sha256'] = archive_sha
        # Fixture-only local origin routing/logging; account SQL is never used.
        origin = fx.origin(); self.await_core_started()
        original_port = fx.manifest['config']['proxy_port']
        h2_node = next(p for p in baseline['company']['proxies'] if p['type'] == 'hysteria2')
        h2_cert = fx.certificate_paths(fx.folder, fx.manifest)[0]
        self.assertTrue(self.https(self.client(h2_node, original_port, h2_cert), origin), 'Original H2 baseline failed')
        target_port, reality_port, backend_port = native.free_port(), native.free_port(), native.free_port()
        self.assertEqual(len({target_port, reality_port, backend_port, original_port}), 4)
        self.front(target_port)
        api = fx.api()
        main = copy.deepcopy(next(i for i in api.call('/panel/api/inbounds/list') if i['protocol'] == 'vless'))
        main_id = main['id']
        stream_before = dual.decoded(main['streamSettings'])
        stream = copy.deepcopy(stream_before)
        stream['realitySettings']['dest'] = '127.0.0.1:' + str(target_port)  # fixture coordinate only
        main.update(port=reality_port, streamSettings=json.dumps(stream))
        self.save('reality-update-payload.json', main)
        api.call('/panel/api/inbounds/update/' + str(main_id), main)
        group = api.call('/panel/api/hosts/get/' + fx.manifest['host_group_id'])
        group['port'] = reality_port
        # Native group update replaces physical Host row ID; logical group ID
        # and bound inbound remain. Parent explicitly approved this boundary.
        api.call('/panel/api/hosts/update/' + group['groupId'], group)
        cert, access = self.front(original_port, backend_port)
        payload = {'remark': 'SELF-cdn', 'tag': 'SELF-cdn', 'enable': True, 'listen': '127.0.0.1',
            'port': backend_port, 'protocol': 'vless', 'disableFlow': True,
            'settings': copy.deepcopy(main['settings']),
            'streamSettings': json.dumps({'network': 'xhttp', 'security': 'none',
                'xhttpSettings': {'path': '/native-xhttp', 'host': 'cdn.example.org', 'mode': 'packet-up'}}),
            'sniffing': main['sniffing']}
        self.save('xhttp-add-payload.json', payload)
        api.call('/panel/api/inbounds/add', payload)
        cdn_in = next(i for i in api.call('/panel/api/inbounds/list') if i['remark'] == 'SELF-cdn')
        cdn_id = cdn_in['id']
        host_payload = {'inboundIds': [cdn_id], 'hosts': ['cdn.example.org'], 'remark': 'SELF-cdn',
            'port': original_port, 'security': 'tls', 'sni': 'cdn.example.org', 'hostHeader': 'cdn.example.org',
            'path': '/native-xhttp', 'alpn': ['h2'], 'allowInsecure': False}
        self.save('xhttp-host-payload.json', host_payload)
        api.call('/panel/api/hosts/add', host_payload)
        after = fx.records()
        rows = {table: fx.rows(table) for table in old_rows}
        inbounds = api.call('/panel/api/inbounds/list')
        self.save('after-records.json', after); self.save('after-db.json', rows)
        self.save('after-api-inbounds.json', inbounds)
        self.assertEqual(len(rows['inbounds']), 3)
        self.assertEqual(len(rows['hosts']), 3)
        ids = {i['id'] for i in inbounds}
        self.assertEqual(ids, {i['id'] for i in old_inbounds} | {cdn_id})
        self.assertEqual({c['email']: c['id'] for c in rows['clients']}, {c['email']: c['id'] for c in old_rows['clients']})
        self.assertEqual(len(rows['client_inbounds']), 3 * len(before))
        self.assertEqual({(j['client_id'], j['inbound_id']) for j in rows['client_inbounds']},
                         {(c['id'], i) for c in rows['clients'] for i in ids})
        self.assertEqual([j for j in rows['client_inbounds'] if j['inbound_id'] != cdn_id], old_rows['client_inbounds'])
        self.assertTrue(all(j['flow_override'] == (VISION if j['inbound_id'] == main_id else '') for j in rows['client_inbounds']))
        h2_id = fx.manifest['hysteria2']['inbound_id']
        h2_before = next(i for i in old_inbounds if i['id'] == h2_id)
        h2_after = next(i for i in inbounds if i['id'] == h2_id)
        self.assertEqual({k: v for k, v in h2_before.items() if k != 'clientStats'},
                         {k: v for k, v in h2_after.items() if k != 'clientStats'})
        self.assertEqual(next(i for i in rows['inbounds'] if i['id'] == h2_id),
                         next(i for i in old_rows['inbounds'] if i['id'] == h2_id))
        # Pinned AddClientStat upserts shared email's traffic.inbound_id to the
        # newly added inbound; this is a projection association, not H2 config.
        self.assertEqual(h2_after['clientStats'], [dict(row, inboundId=cdn_id) for row in h2_before['clientStats']])
        self.summary['native_shared_client_stats_inbound_id'] = {'before': h2_id, 'after': cdn_id}
        self.assertEqual([h for h in rows['hosts'] if h['inbound_id'] == h2_id], [h for h in old_rows['hosts'] if h['inbound_id'] == h2_id])
        old_host = next(h for h in old_rows['hosts'] if h['inbound_id'] == main_id)
        new_host = next(h for h in rows['hosts'] if h['inbound_id'] == main_id)
        self.assertEqual({k: v for k, v in old_host.items() if k not in ('id', 'port')},
                         {k: v for k, v in new_host.items() if k not in ('id', 'port')})
        self.assertNotEqual(old_host['id'], new_host['id'])
        self.assertEqual(new_host['port'], reality_port)
        self.summary['reality_host_row_ids'] = [old_host['id'], new_host['id']]
        real = next(i for i in inbounds if i['id'] == main_id)
        self.assertFalse(real['disableFlow']); self.assertTrue(cdn_in['disableFlow'])
        self.assertEqual(dual.decoded(real['streamSettings']), stream)
        changed = {}
        for name in before:
            changed[name] = sorted(k for k in before[name].keys() | after[name].keys() if before[name].get(k) != after[name].get(k))
            self.assertFalse(set(changed[name]) - {'createdAt', 'updatedAt'}, 'Global policy/identity changed: ' + ','.join(changed[name]))
            self.assertEqual(after[name]['flow'], VISION)
            self.assertEqual(after[name]['createdAt'], earliest[name])
            self.assertLess(abs(after[name]['createdAt'] - before[name]['createdAt']), 1000)
            self.assertEqual(after[name]['createdAt'] // 1000, before[name]['createdAt'] // 1000)
        self.summary['record_changed_fields'] = changed
        self.summary['created_at_precision_changed'] = [n for n in before if before[n]['createdAt'] != after[n]['createdAt']]
        # Also compare every persisted global column (not just the API DTO).
        for old, new in zip(old_rows['clients'], rows['clients']):
            self.assertEqual({k: v for k, v in old.items() if k not in ('created_at', 'updated_at')},
                             {k: v for k, v in new.items() if k not in ('created_at', 'updated_at')})
        profiles = self.triple_exports(before, baseline, reality_port, original_port)
        generated = api.call('/panel/api/server/getConfigJson')
        self.save('generated-xray.json', generated)
        self.await_core_started()
        xr = next(i for i in generated['inbounds'] if i.get('protocol') == 'vless' and i['port'] == reality_port)
        xx = next(i for i in generated['inbounds'] if i.get('tag') == cdn_in['tag'])
        xh = next(i for i in generated['inbounds'] if i.get('tag') == 'SELF-h2')
        expected_uuid = {a['uuid'] for a in before.values()}
        self.assertEqual({c['id'] for c in xr['settings']['clients']}, expected_uuid)
        self.assertEqual({c['id'] for c in xx['settings']['clients']}, expected_uuid)
        self.assertTrue(all(c.get('flow') == VISION for c in xr['settings']['clients']))
        self.assertTrue(all(not c.get('flow') for c in xx['settings']['clients']))
        self.assertEqual((xx['listen'], xx['streamSettings']['security'], xx['port']), ('127.0.0.1', 'none', backend_port))
        self.assertEqual(xh['port'], original_port)
        self.assertEqual(xh['streamSettings']['network'], 'hysteria')
        self.assertEqual({c['auth'] for c in xh['settings']['clients']}, {a['auth'] for a in before.values()})
        node = next(p for p in profiles['company']['proxies'] if p.get('network') == 'xhttp')
        original_reality = next(p for p in baseline['company']['proxies'] if p.get('reality-opts'))
        self.assertTrue(self.https(self.client(original_reality, reality_port, h2_cert), origin), 'Original UUID/REALITY keys/Vision failed on new port')
        self.summary['original_reality_vision_new_port'] = True
        self.assertTrue(self.https(self.client(node, original_port, cert), origin), 'Valid XHTTP failed')
        self.summary['xhttp_before_negative'] = True
        error_log = fx.folder / 'logs/dual-error.log'
        offset, hits = error_log.stat().st_size, len(origin.peers)
        def normalized(value):
            raw = bytearray(uuid.UUID(value).bytes); raw[6:8] = b'\0\0'; return bytes(raw)
        wrong = copy.deepcopy(node)
        wrong['uuid'] = str(uuid.uuid4())
        self.assertNotIn(normalized(wrong['uuid']), {normalized(a['uuid']) for a in before.values()})
        self.save('wrong-uuid-private.json', {'nonce': wrong['uuid'], 'logOffset': offset})
        self.assertFalse(self.https(self.client(wrong, original_port, cert), origin), 'Wrong UUID authenticated')
        self.assertEqual(len(origin.peers), hits, 'Wrong UUID reached origin')
        with error_log.open('rb') as log:
            log.seek(offset); delta = log.read().decode(errors='replace')
        verdict = [line for line in delta.splitlines() if 'invalid request user id: ' + wrong['uuid'] in line]
        self.assertTrue(verdict, 'No exact fresh native invalid-user nonce verdict')
        self.assertTrue(all(re.search(r'\[\d+\]', line) for line in verdict), 'Native connection identity missing')
        self.save('native-auth-verdict-private.json', verdict)
        self.summary['fresh_wrong_uuid_native_rejected_no_origin'] = True
        self.assertTrue(self.https(self.client(node, original_port, cert), origin), 'Valid XHTTP did not recover')
        self.assertTrue(self.https(self.client(h2_node, original_port, h2_cert), origin), 'Original H2 fresh reconnect failed')
        self.summary['valid_xhttp_recovery_and_original_h2_reconnect'] = True
        logs = [json.loads(line) for line in access.read_text().splitlines() if line.strip()]
        self.assertEqual({entry['request']['proto'] for entry in logs}, {'HTTP/2.0'})
        self.assertEqual({entry['request']['tls']['proto'] for entry in logs}, {'h2'})
        self.assertTrue({'POST', 'GET'} <= {entry['request']['method'] for entry in logs})
        self.summary['caddy_http2_requests'] = len(logs)
        self.assertEqual(host.digest(self.evidence / 'original-closed-dual.tar'), archive_sha)
        self.summary.update(global_accounts=len(before), inbounds=3, hosts=3, joins=len(rows['client_inbounds']),
            original_closed_archive_unchanged=True, native_scoped_three_node_feeds=len(profiles),
            scope='local high-port trusted TLS/h2 native triple capability; no public Cloudflare/production',
            port_contract='Caddy TCP and unchanged H2 UDP share original numeric port; REALITY independent TCP')


if __name__ == '__main__':
    unittest.main()
