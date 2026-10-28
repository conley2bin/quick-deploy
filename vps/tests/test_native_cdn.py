"""Local-only pinned native XHTTP capability, composed from NativeDual.

Two-entry REALITY-to-XHTTP conversion; not module migration or Cloudflare acceptance. Requires explicit
QD_VPS_ARTIFACTS and QD_NATIVE_CDN_EVIDENCE (private directory outside Git). Run
serially: the native panel always owns API 127.0.0.1:62789. No TUN/insecure TLS.
"""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import time
import unittest
from urllib.parse import parse_qs, unquote, urlsplit

import test_native_dual as dual

host, vps, native = dual.host, dual.vps, dual.native


@unittest.skipUnless(os.environ.get('QD_VPS_ARTIFACTS') and os.environ.get('QD_NATIVE_CDN_EVIDENCE'),
                     'Explicit locked artifacts and private evidence directory required')
class NativeCDN(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Verify every asset BEFORE any native fixture can execute a binary.
        cls.lock_digests = {}
        for component in ('panel', 'core', 'front'):
            lock = vps.release_lock()[component]
            with (Path(os.environ['QD_VPS_ARTIFACTS']) / lock['file']).open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if digest != lock['sha256']:
                raise AssertionError('Release-lock digest mismatch: ' + component)
            cls.lock_digests[component] = digest
        dual.NativeDual.setUpClass()
        cls.addClassCleanup(dual.NativeDual.doClassCleanups)

    def setUp(self):
        self.evidence = Path(os.environ['QD_NATIVE_CDN_EVIDENCE']).resolve()
        self.evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.evidence.chmod(0o700)
        self.fx = dual.NativeDual('test_enable_preserves_full_native_policy_and_exports')
        self.fx._outcome = self._outcome
        self.addCleanup(self.fx.doCleanups)
        self.fx.setUp()
        self.summary = {}
        self.addCleanup(self.retain)

    def retain(self):
        # Save before the composed fixture destroys its temporary tree.
        self.summary['last_https_diagnostic'] = getattr(self.fx, 'last_https_diagnostic', None)
        self.summary['mihomo_readiness'] = getattr(self.fx, '_mihomo_readiness', [])
        # Drain the composed fixture's client processes before copying logs.
        self.fx.doCleanups()
        if not self.fx.fx.log.closed:
            self.fx.fx.log.flush()
        target = self.evidence / 'fixture'
        shutil.copytree(self.fx.folder, target, dirs_exist_ok=True)
        (self.evidence / 'summary.json').write_text(json.dumps(self.summary, indent=2))
        for root, dirs, files in os.walk(self.evidence):
            Path(root).chmod(0o700)
            for name in files:
                (Path(root) / name).chmod(0o600)

    def spawn(self, argv, directory, environment, marker, logfile):
        proc = subprocess.Popen(argv, cwd=directory, env=environment, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, start_new_session=True)
        def stop():
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
            worker.join(timeout=2)
            proc.stdout.close()
        self.addCleanup(stop)
        ready = threading.Event()
        def capture():
            with logfile.open('w') as output:
                for line in proc.stdout:
                    output.write(line); output.flush()
                    if marker in line:
                        ready.set()
        worker = threading.Thread(target=capture, daemon=True)
        worker.start()
        self.assertTrue(ready.wait(10), 'Fixture readiness missing; private log: ' + str(logfile))
        return proc

    def caddy(self, backend_port):
        folder = self.fx.folder / 'cdn-front'
        folder.mkdir()
        cert, key = folder / 'ca.pem', folder / 'key.pem'
        made = subprocess.run(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt',
            'ec_paramgen_curve:P-256', '-nodes', '-days', '1', '-subj', '/CN=cdn.example.org',
            '-addext', 'subjectAltName=DNS:cdn.example.org', '-addext', 'basicConstraints=critical,CA:TRUE',
            '-keyout', str(key), '-out', str(cert)], capture_output=True, timeout=15)
        self.assertEqual(made.returncode, 0, 'Fixture CA generation failed')
        port = native.free_port()
        access = folder / 'access.jsonl'
        config = folder / 'Caddyfile'
        config.write_text('''{
 admin off
 auto_https off
 servers {
  protocols h1 h2
 }
}
https://cdn.example.org:%d {
 bind 127.0.0.1
 tls %s %s
 log {
  output file %s
  format json
 }
 reverse_proxy 127.0.0.1:%d {
  flush_interval -1
 }
}
''' % (port, cert, key, access, backend_port))
        self.spawn([str(self.fx.folder / 'bin/caddy'), 'run', '--config', str(config), '--adapter', 'caddyfile'],
                   folder, dict(os.environ), 'serving initial configuration', folder / 'private.log')
        return port, cert, access

    def client(self, proxy, port, cert):
        proxy = copy.deepcopy(proxy)
        # Only endpoint redirection; protocol, UUID, flow, TLS/SNI/ALPN/path/mode
        # remain the genuine native node. No hand-built protocol mapping.
        proxy.update(server='127.0.0.1', port=port)
        folder = self.fx.folder / ('cdn-client-' + str(time.monotonic_ns()))
        folder.mkdir()
        mixed = native.free_port()
        config = {'mixed-port': mixed, 'bind-address': '127.0.0.1', 'allow-lan': False,
                  'log-level': 'debug', 'dns': {'enable': False}, 'tun': {'enable': False},
                  'proxies': [proxy], 'proxy-groups': [{'name': 'PROXY', 'type': 'select',
                  'proxies': [proxy['name']]}], 'rules': ['MATCH,PROXY']}
        return native.start_mihomo(self.fx, folder, config, cert)

    def https(self, port, origin):
        result = subprocess.run(['curl', '--verbose', '--silent', '--show-error', '--max-time', '4',
            '--noproxy', '', '--proxy', 'socks5h://127.0.0.1:' + str(port), '--cacert', str(origin.trust_file),
            '--connect-to', 'sub.example.org:' + str(origin.server_port) + ':127.0.0.1:' + str(origin.server_port),
            'https://sub.example.org:' + str(origin.server_port) + '/'], capture_output=True, timeout=6)
        (self.evidence / ('curl-' + str(port) + '.private.log')).write_bytes(result.stderr)
        success = result.returncode == 0 and result.stdout == b'native-dual-https-egress\n'
        self.fx.last_https_diagnostic = 'curl_exit=' + str(result.returncode) + ', exact_marker=' + str(success)
        return success

    def await_core_started(self):
        # Pinned CLI uses grpc.DialContext(WithBlock) with the explicit timeout;
        # this checks actual owned API service readiness, without sleeps or a
        # dependency on the desktop's exhaustible inotify watch quota.
        result = subprocess.run([str(self.fx.folder / 'bin/xray-linux-amd64'),
            'api', 'statssys', '--server=127.0.0.1:62789', '--timeout=6'],
            capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, 'Owned Xray API readiness failed')
        data = (self.fx.folder / 'logs/dual-error.log').read_text(errors='replace')
        start, closed = data.rfind('Logger started'), data.rfind('Logger closing')
        self.assertGreater(start, closed, 'Owned Xray logger is closing')
        self.assertIn('Xray ' + self.fx.fx.lock['core']['version'] + ' started', data[start:])

    def test_native_shared_accounts_tls_h2_packet_up(self):
        fx = self.fx
        self.summary['lock_digests'] = self.lock_digests
        version = subprocess.run([vps.resolve_mihomo(), '-v'], capture_output=True, timeout=5)
        self.assertIn(b'v1.19.32', version.stdout)
        self.summary['mihomo_version'] = '1.19.32'
        fx.seed_policy()
        host.backend_health(fx.folder, fx.manifest)
        fx.enable(); fx.topology()
        baseline = fx.assert_native_exports()
        self.summary['dual_baseline_health'] = True
        before = fx.records()
        old_joins, old_hosts = fx.rows('client_inbounds'), fx.rows('hosts')
        old_inbounds = fx.api().call('/panel/api/inbounds/list')
        earliest = {name: account['createdAt'] for name, account in before.items()}
        for item in old_inbounds:
            for client in dual.decoded(item['settings'])['clients']:
                if client.get('created_at', 0) > 0:
                    earliest[client['email']] = min(earliest[client['email']], client['created_at'])
        archive, _, _ = fx.archive()
        shutil.copy2(archive, self.evidence / 'before-native-dual.tar')
        self.summary['before_archive_sha256'] = host.digest(archive)
        # IMPORTANT: origin exceptions follow ALL module baseline checks.
        origin = fx.origin()
        self.await_core_started()
        h2_node = next(p for p in baseline['proxies'] if p['type'] == 'hysteria2')
        h2_cert = fx.certificate_paths(fx.folder, fx.manifest)[0]
        h2_port = fx.manifest['config']['proxy_port']
        self.assertTrue(self.https(self.client(h2_node, h2_port, h2_cert), origin), 'Baseline H2 failed: ' + getattr(fx, 'last_https_diagnostic', ''))
        self.summary['h2_before'] = True
        api = fx.api()
        inbound = copy.deepcopy(next(i for i in api.call('/panel/api/inbounds/list') if i['protocol'] == 'vless'))
        backend_port = native.free_port()
        front_port, cert, access = self.caddy(backend_port)
        inbound.update(remark='SELF-cdn', tag='SELF-cdn', listen='127.0.0.1', port=backend_port,
            disableFlow=True, streamSettings=json.dumps({'network': 'xhttp', 'security': 'none',
                'xhttpSettings': {'path': '/native-xhttp', 'host': 'cdn.example.org', 'mode': 'packet-up'}}))
        # Preserve the complete native settings/roster; disableFlow strips only
        # the existing main join's Vision flow through the supported API.
        api.call('/panel/api/inbounds/update/' + str(inbound['id']), inbound)
        group = api.call('/panel/api/hosts/get/' + fx.manifest['host_group_id'])
        group.update(inboundIds=[inbound['id']], hosts=['cdn.example.org'], remark='SELF-cdn',
                     port=front_port, security='tls', sni='cdn.example.org', hostHeader='cdn.example.org',
                     path='/native-xhttp', alpn=['h2'], allowInsecure=False)
        api.call('/panel/api/hosts/update/' + group['groupId'], group)
        self.summary['ordinary_inbound_and_host_update'] = True
        self.summary['local_tls_front_port'] = front_port
        after = fx.records()
        changes = {}
        for name in before:
            changed = sorted(k for k in before[name].keys() | after[name].keys() if before[name].get(k) != after[name].get(k))
            changes[name] = changed
            self.assertFalse(set(changed) - {'flow', 'createdAt', 'updatedAt'}, 'Conversion changed credentials/policies: ' + ','.join(changed))
            self.assertEqual(after[name]['createdAt'], earliest[name], 'Unexpected timestamp normalization')
            self.assertEqual(before[name]['createdAt'] // 1000, after[name]['createdAt'] // 1000)
            self.assertEqual(after[name]['flow'], '', 'Retired Vision flow remains global')
        self.summary['record_changed_fields'] = changes
        self.summary['created_at_precision_changed'] = [name for name in before if before[name]['createdAt'] != after[name]['createdAt']]
        self.assertEqual([h for h in old_hosts if h['inbound_id'] != inbound['id']],
                         [h for h in fx.rows('hosts') if h['inbound_id'] != inbound['id']])
        self.assertEqual(len(fx.rows('hosts')), 2)
        self.assertEqual(len(fx.rows('inbounds')), 2)
        joins = fx.rows('client_inbounds')
        self.assertEqual({(j['client_id'], j['inbound_id']) for j in joins},
                         {(j['client_id'], j['inbound_id']) for j in old_joins})
        self.assertEqual(len(joins), 2 * len(before))
        self.assertEqual([j for j in old_joins if j['inbound_id'] != inbound['id']],
                         [j for j in joins if j['inbound_id'] != inbound['id']])
        self.assertTrue(all(j['flow_override'] == '' for j in joins))
        self.summary['global_accounts'] = len(before)
        self.summary['joins'] = len(joins)
        profiles = {}
        import yaml
        for name, account in after.items():
            if not account['enable']:
                continue
            data = host.get_feed(fx.manifest, name, 'mihomo')
            (self.evidence / (name + '.native.yaml')).write_bytes(data)
            profile = yaml.safe_load(data)
            self.assertEqual(len(profile['proxies']), 2, 'Expected exactly two native scoped nodes')
            xhttp = next(p for p in profile['proxies'] if p.get('network') == 'xhttp')
            self.assertEqual(xhttp['uuid'], account['uuid'])
            self.assertEqual((xhttp['server'], xhttp['port'], xhttp['tls']), ('cdn.example.org', front_port, True))
            self.assertEqual(xhttp.get('servername'), 'cdn.example.org')
            self.assertEqual(xhttp.get('alpn'), ['h2'])
            self.assertFalse(xhttp.get('flow')); self.assertFalse(xhttp.get('skip-cert-verify', False))
            self.assertEqual(xhttp['xhttp-opts'], {'path': '/native-xhttp', 'host': 'cdn.example.org', 'mode': 'packet-up'})
            self.assertTrue(all(p.get('uuid', account['uuid']) == account['uuid'] for p in profile['proxies']))
            self.assertEqual(next(p for p in profile['proxies'] if p['type'] == 'hysteria2')['password'], before[name]['auth'])
            uri_data = host.get_feed(fx.manifest, name, 'sub')
            (self.evidence / (name + '.native.sub')).write_bytes(uri_data)
            links = [urlsplit(link) for link in base64.b64decode(uri_data, validate=True).decode().splitlines()]
            self.assertEqual(len(links), 2)
            h2_uri = next(link for link in links if link.scheme == 'hysteria2')
            self.assertTrue(unquote(h2_uri.username) == before[name]['auth'], 'Native H2 URI Auth changed')
            cdn_uri = next(link for link in links if parse_qs(link.query).get('type') == ['xhttp'])
            self.assertEqual(cdn_uri.username, account['uuid'])
            self.assertEqual((cdn_uri.hostname, cdn_uri.port), ('cdn.example.org', front_port))
            query = parse_qs(cdn_uri.query)
            for key, value in {'security': 'tls', 'sni': 'cdn.example.org', 'alpn': 'h2', 'path': '/native-xhttp', 'mode': 'packet-up'}.items():
                self.assertEqual(query.get(key), [value])
            self.assertFalse(query.get('flow'))
            profiles[name] = profile
        self.summary['two_node_scoped_feeds'] = len(profiles)
        # Native UpdateInbound hot-replaces its listener synchronously. Fetch
        # native generation directly; avoid racing an unrelated async restart.
        config = api.call('/panel/api/server/getConfigJson')
        (self.evidence / 'generated-xray.json').write_text(json.dumps(config))
        self.await_core_started()
        state = api.call('/panel/api/server/status')['xray']
        self.assertEqual(state['state'], 'running')
        cdn = next(i for i in config['inbounds'] if i.get('tag') == 'SELF-cdn')
        self.assertEqual((cdn['listen'], cdn['streamSettings']['security']), ('127.0.0.1', 'none'))
        self.assertTrue(all(not c.get('flow') for c in cdn['settings']['clients']))
        self.assertEqual({c['id'] for c in cdn['settings']['clients']}, {a['uuid'] for a in after.values() if a['enable']})
        self.summary['generated_xray_plain_loopback_empty_flow'] = True
        node = next(p for p in profiles['company']['proxies'] if p.get('network') == 'xhttp')
        self.assertTrue(self.https(self.client(node, front_port, cert), origin), 'Initial correct XHTTP UUID failed: ' + getattr(fx, 'last_https_diagnostic', ''))
        self.summary['xhttp_https_before_negative'] = True
        origin_hits = len(origin.peers)
        wrong = copy.deepcopy(node); wrong['uuid'] = '00000000-0000-4000-8000-000000000001'
        self.assertFalse(self.https(self.client(wrong, front_port, cert), origin), 'Wrong UUID authenticated')
        self.assertEqual(len(origin.peers), origin_hits, 'Wrong UUID reached HTTPS origin')
        self.assertIn('invalid request user id', (fx.folder / 'logs/dual-error.log').read_text(),
                      'Wrong UUID did not reach native Xray authentication')
        self.summary['wrong_uuid_rejected'] = True
        self.assertTrue(self.https(self.client(node, front_port, cert), origin), 'Correct XHTTP UUID failed: ' + getattr(fx, 'last_https_diagnostic', ''))
        self.summary['xhttp_https_marker'] = True
        self.assertTrue(self.https(self.client(h2_node, h2_port, h2_cert), origin), 'Original H2 credentials failed fresh reconnect')
        self.summary['old_h2_fresh_reconnect'] = True
        logs = [json.loads(line) for line in access.read_text().splitlines() if line.strip()]
        protocols = {entry.get('request', {}).get('proto') for entry in logs}
        self.assertIn('HTTP/2.0', protocols, 'No actual HTTP/2 access evidence')
        self.assertTrue(any(entry.get('request', {}).get('method') == 'POST' for entry in logs))
        self.assertTrue(any(entry.get('request', {}).get('method') == 'GET' for entry in logs))
        self.summary['caddy_client_protocols'] = sorted(protocols)
        self.assertEqual(host.digest(self.evidence / 'before-native-dual.tar'), self.summary['before_archive_sha256'])
        self.summary['original_closed_archive_unchanged'] = True
        self.summary['origin_peer_addresses'] = sorted(set(origin.peers))
        self.summary['scope'] = 'two-entry native + local trusted TLS reverse proxy only; no Cloudflare'
        self.summary['production_port_contract'] = 'TCP443 Caddy; UDP443 unchanged Hysteria2; fixture uses high ports'


if __name__ == '__main__':
    unittest.main()
