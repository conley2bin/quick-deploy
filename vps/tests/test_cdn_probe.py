"""Probe mechanisms with real private subprocess/controller/SOCKS/TLS fixtures.

No native panel/API62789, public DNS, Internet access, or production certificates.
The fixture CA is injected only here; production has no custom-CA parameter.
"""
import copy
import http.server
import hashlib
import json
import os
from pathlib import Path
import select
import socket
import socketserver
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import cdn_probe as probe

UUID = '11111111-1111-4111-8111-111111111111'
ORIGIN = '93.184.216.34'
EDGE = '104.16.1.2'
REAL_SELECT = select.select


def manifest():
    return {'endpoint': ORIGIN, 'config': {'domain': 'sub.example.org', 'proxy_port': 443, 'reality_target': 'target.example.org'},
            'reality': {'publicKey': 'R' * 43, 'shortIds': ['0123456789abcdef']},
            'tcp_cdn': {'schema': 1, 'domain': 'cdn.example.org', 'inbound_id': 3, 'host_group_id': 'cdn-group',
                        'reality_port': 9443, 'reality_tag': 'in-443-tcp', 'path': '/qd-xhttp/', 'mode': 'packet-up'},
            'hysteria2': {'schema': 1, 'inbound_id': 2},
            'accounts': {'company': {'email': 'company', 'id': UUID, 'auth': 'fixture-h2-auth-original',
                                     'subId': 'fixture-subscription-original', 'enable': True}}}


def nodes():
    return [{'name': 'SELF-cdn', 'type': 'vless', 'server': 'cdn.example.org', 'port': 443,
             'uuid': UUID, 'tls': True, 'servername': 'cdn.example.org', 'network': 'xhttp',
             'alpn': ['h2'], 'skip-cert-verify': False,
             'xhttp-opts': {'path': '/qd-xhttp/', 'host': 'cdn.example.org', 'mode': 'packet-up'},
             'client-fingerprint': 'chrome', 'udp': True},
            {'name': 'SELF-h2', 'type': 'hysteria2', 'server': ORIGIN, 'port': 443,
             'password': 'fixture-h2-auth-original', 'sni': 'sub.example.org', 'alpn': ['h3'], 'skip-cert-verify': False},
            {'name': 'SELF-main', 'type': 'vless', 'server': ORIGIN, 'port': 9443, 'uuid': UUID,
             'tls': True, 'servername': 'target.example.org', 'network': 'tcp', 'flow': 'xtls-rprx-vision',
             'reality-opts': {'public-key': 'R' * 43, 'short-id': '0123456789abcdef', 'support-x25519mlkem768': True},
             'client-fingerprint': 'chrome', 'udp': True}]


# The listener deliberately ACKs SOCKS while Running=False and the selector is
# already populated. Only an authenticated identical-config PUT enables ingress.
FAKE_CLIENT = r'''#!/usr/bin/env python3
import hashlib,http.server,json,os,pathlib,select,socket,socketserver,struct,sys,threading
if '-v' in sys.argv:
 print('Mihomo Meta v1.19.32');sys.exit(0)
path=pathlib.Path(sys.argv[sys.argv.index('-f')+1]);cfg=json.loads(path.read_text())
root=pathlib.Path(__file__).parent; origin=int((root/'origin-port').read_text())
mode=(root/'mode').read_text(); running=False
node=cfg['proxies'][0]
def record(item):
 with (root/'events').open('a') as f:f.write(json.dumps(item)+'\n')
record({'pid':os.getpid(),'config':str(path),'argv':sys.argv,'role':node['name']})
class Control(http.server.BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_PUT(self):
  global running
  if self.headers.get('Authorization')!='Bearer '+cfg['secret']:
   self.send_response(401);self.end_headers();return
  data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  if data!={'path':str(path)} or json.loads(path.read_text())!=cfg:
   self.send_response(400);self.end_headers();return
  record({'event':'apply','running_before':running})
  running=True
  self.send_response(200 if mode=='bad-barrier' else 204);self.end_headers()
 def do_GET(self):
  if self.headers.get('Authorization')!='Bearer '+cfg['secret']:
   self.send_response(401);self.end_headers();return
  record({'event':'selected','running':running})
  group={'now':node['name'],'all':[node['name']]}
  if mode=='fallback-selector':group['all']+=['SELF-main','SELF-h2','DIRECT']
  data=json.dumps(group).encode()
  self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
class Socks(socketserver.BaseRequestHandler):
 def handle(self):
  s=self.request;s.settimeout(5)
  def exact(n):
   data=b''
   while len(data)<n:
    p=s.recv(n-len(data))
    if not p:raise EOFError()
    data+=p
   return data
  try:
   if exact(3)!=b'\x05\x01\x00':return
   s.sendall(b'\x05\x00');hdr=exact(4)
   size=exact(1)[0] if hdr[3]==3 else 4
   exact(size+2);s.sendall(b'\x05\x00\x00\x01\x7f\x00\x00\x01\x01\xbb')
   record({'event':'socks','running':running,'type':node['type'],
           'uuid_sha256':hashlib.sha256(node.get('uuid','').encode()).hexdigest()})
   wrong=(node['type']=='vless' and node['uuid']!='11111111-1111-4111-8111-111111111111')
   if wrong and mode=='accept-reset':
    record({'event':'injected-reset'});return
   if not running or (wrong and mode not in ('accept-wrong','accept-reset')):return
   if mode=='hang':threading.Event().wait(3);return
   with socket.create_connection(('127.0.0.1',origin)) as dest:
    while True:
     ready,_,_=select.select([s,dest],[],[],5)
     if not ready:return
     for src in ready:
      data=src.recv(65536)
      if not data:return
      (dest if src is s else s).sendall(data)
  except (OSError,EOFError):pass
class Server(socketserver.ThreadingTCPServer):
 allow_reuse_address=True;daemon_threads=True
controller=int(cfg['external-controller'].split(':')[1]);mixed=cfg['mixed-port']
a=http.server.ThreadingHTTPServer(('127.0.0.1',controller),Control)
b=Server(('127.0.0.1',mixed),Socks)
threading.Thread(target=a.serve_forever,daemon=True).start()
threading.Thread(target=b.serve_forever,daemon=True).start()
print('RESTful API listening at: 127.0.0.1:'+str(controller),flush=True)
print('Mixed(http+socks) proxy listening at: 127.0.0.1:'+str(mixed),flush=True)
threading.Event().wait()
'''


class Probe(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='qd-cdn-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cert, self.key = self.root / 'cert.pem', self.root / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256',
                        '-nodes', '-days', '1', '-subj', '/CN=www.python.org', '-addext',
                        'subjectAltName=DNS:www.python.org,DNS:api.ipify.org', '-keyout', str(self.key),
                        '-out', str(self.cert)], check=True, capture_output=True, timeout=5)
        class Origin(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                body = ORIGIN.encode() if self.headers.get('Host') == 'api.ipify.org' else b'public-https-fixture'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.origin = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Origin)
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(self.cert, self.key)
        self.origin.socket = server_context.wrap_socket(self.origin.socket, server_side=True)
        self.thread = threading.Thread(target=self.origin.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_origin)
        self.binary = self.root / 'fake-mihomo'
        self.binary.write_text(FAKE_CLIENT)
        self.binary.chmod(0o700)
        (self.root / 'origin-port').write_text(str(self.origin.server_port))
        (self.root / 'mode').write_text('normal')
        self.context = ssl.create_default_context(cafile=self.cert)
        patch = mock.patch.object(probe, 'strict_context', return_value=self.context)
        patch.start(); self.addCleanup(patch.stop)
        self.m = manifest()
        self.request = {'feature': 'tcp-cdn', 'tcp_cdn': {'schema': 1, 'domain': 'cdn.example.org', 'reality_port': 9443},
                        'source_transports': ['tcp-reality', 'hysteria2'],
                        'target_transports': ['tcp-reality', 'tcp-xhttp-cdn', 'hysteria2'], 'operation_id': 'a' * 32}
        self.auth_events = []
        self.nonce = str(uuid.uuid4())

    def auth_begin(self):
        self.auth_events.append('begin')
        self.assertFalse((self.root / 'events').exists())
        return {'schema': 1, 'operation_id': self.request['operation_id'], 'request_fingerprint': 'b' * 64,
                'nonce': self.nonce, 'native_identity_sha256': 'd' * 64, 'inbound_id': 3, 'inbound_tag': 'SELF-cdn'}

    def auth_finish(self, nonce):
        self.assertEqual(nonce, self.nonce)
        self.assertEqual(len([e for e in self.events() if 'pid' in e]), 2)
        self.assert_processes_clean()
        self.auth_events.append('restored')
        return {'schema': 1, 'operation_id': self.request['operation_id'], 'request_fingerprint': 'b' * 64,
                'nonce_sha256': hashlib.sha256(nonce.encode()).hexdigest(), 'rejected': True,
                'native_identity_sha256': 'd' * 64, 'inbound_id': 3, 'inbound_tag': 'SELF-cdn'}

    def final_candidate(self):
        self.assertEqual(self.auth_events, ['begin', 'restored'])
        self.assertEqual(len([e for e in self.events() if 'pid' in e]), 5)
        self.assert_processes_clean()
        self.auth_events.append('final-B')
        return 'e' * 64

    def stop_origin(self):
        self.origin.shutdown(); self.origin.server_close(); self.thread.join()

    def client(self, node=None):
        return probe.Client(str(self.binary), node or nodes()[0], 'cdn.example.org', EDGE, 'eno1',
                            '1.1.1.1', probe.Budget(time.time() + 30))

    def events(self):
        return [json.loads(line) for line in (self.root / 'events').read_text().splitlines()]

    def run_proof(self, **changes):
        options = dict(profile_bytes=probe.canonical({'proxies': nodes()}), manifest=self.m,
                       before_manifest=copy.deepcopy(self.m), device='company', request=self.request,
                       request_fingerprint='b' * 64, candidate_sha256='c' * 64, interface='eno1',
                       dns_server='1.1.1.1', binary=str(self.binary), deadline=time.time() + 100,
                       auth_begin=self.auth_begin, auth_finish=self.auth_finish, final_candidate=self.final_candidate)
        options.update(changes)
        with mock.patch.object(probe, 'resolve_a', return_value=[EDGE]), \
                mock.patch.object(probe, 'edge_tls', return_value={'peer': EDGE, 'port': 443}), \
                mock.patch.object(probe, 'socket_peers', return_value={(EDGE, 443)}), \
                mock.patch.object(probe.select, 'select', wraps=select_without_idle):
            return probe.validate_cdn_candidate(**options)

    def assert_processes_clean(self):
        for event in self.events():
            if 'pid' in event:
                self.assertFalse(Path('/proc', str(event['pid'])).exists())
                self.assertFalse(Path(event['config']).parent.exists())
                self.assertNotIn(UUID, ' '.join(event['argv']))
                self.assertNotIn('fixture-h2-auth', ' '.join(event['argv']))

    def test_real_private_controller_running_barrier_socks_strict_tls_and_cleanup(self):
        with self.client() as client:
            folder, pid = Path(client.temporary.name), client.process.pid
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
            self.assertEqual((folder / 'config.json').stat().st_mode & 0o777, 0o600)
            self.assertGreaterEqual(len(client.secret), 64)
            self.assertEqual(client.https('www.python.org')[0], b'public-https-fixture')
            secret = client.secret; client.secret = 'incorrect'
            self.assertEqual(client.control('GET', '/proxies/PROXY')[0], 401)
            client.secret = secret
        actions = [event for event in self.events() if 'event' in event]
        self.assertEqual([x['event'] for x in actions], ['apply', 'selected', 'socks'])
        self.assertFalse(actions[0]['running_before'])
        self.assertTrue(actions[1]['running'])
        self.assertTrue(actions[2]['running'])
        self.assertFalse(folder.exists()); self.assertFalse(Path('/proc', str(pid)).exists())

    def test_non204_barrier_refuses_and_stops_owned_process(self):
        (self.root / 'mode').write_text('bad-barrier')
        with self.assertRaisesRegex(probe.Error, 'readiness barrier'):
            with self.client(): self.fail('not Running')
        self.assert_processes_clean()
        self.assertFalse(any(e.get('event') == 'socks' for e in self.events()))

    def test_apply204_with_any_fallback_selector_is_refused_before_traffic(self):
        (self.root / 'mode').write_text('fallback-selector')
        with self.assertRaisesRegex(probe.Error, 'selected/all'):
            with self.client(): self.fail('fallback route must never be used')
        self.assert_processes_clean()
        self.assertFalse(any(e.get('event') == 'socks' for e in self.events()))

    def test_real_tls_wrong_hostname_is_not_authentication_rejection(self):
        with self.client() as client:
            with self.assertRaisesRegex(probe.Error, 'CA/hostname') as error:
                client.https('wrong-name.example.org')
            self.assertNotIsInstance(error.exception, probe.Rejected)
        self.assert_processes_clean()

    def test_five_fresh_clients_negative_valid_reconnect_and_h2_proof(self):
        samples = []
        proof = self.run_proof(observations=samples)
        self.assertEqual(set(proof), {'schema', 'operation_id', 'request_fingerprint', 'candidate_sha256', 'domain',
            'device_identity_sha256', 'observed_at', 'interface', 'edge_ips', 'checks'})
        self.assertTrue(all(proof['checks'].values()))
        self.assertEqual(proof['candidate_sha256'], 'e' * 64)
        self.assertEqual(self.auth_events, ['begin', 'restored', 'final-B'])
        self.assertNotIn(UUID, json.dumps(proof)); self.assertNotIn('fixture-h2-auth', json.dumps(proof))
        self.assertEqual(len([e for e in self.events() if 'pid' in e]), 5)
        self.assertEqual([e['type'] for e in self.events() if e.get('event') == 'socks'],
                         ['vless', 'vless', 'vless', 'vless', 'vless', 'vless', 'hysteria2', 'hysteria2'])
        self.assertEqual(len(samples), 8)
        self.assertEqual(sum(e.get('uuid_sha256') == hashlib.sha256(self.nonce.encode()).hexdigest()
                             for e in self.events()), 1)
        self.assert_processes_clean()

    def test_wrong_uuid_acceptance_fails_instead_of_falling_back(self):
        (self.root / 'mode').write_text('accept-wrong')
        with self.assertRaisesRegex(probe.Error, 'Wrong UUID was accepted'):
            self.run_proof()
        self.assert_processes_clean()

    def test_accept_all_one_wrong_attempt_reset_without_witness_fails(self):
        (self.root / 'mode').write_text('accept-reset')
        with self.assertRaisesRegex(probe.Error, 'witness'):
            self.run_proof(auth_finish=lambda _: None)
        self.assertEqual(sum(e.get('event') == 'injected-reset' for e in self.events()), 1)
        self.assertEqual(len([e for e in self.events() if 'pid' in e]), 2)
        self.assertEqual(self.auth_events, ['begin'])
        self.assert_processes_clean()

    def test_mismatched_or_unrestored_witness_never_runs_postrestore_clients(self):
        changes = [dict(nonce_sha256='f' * 64), dict(operation_id='f' * 32),
                   dict(request_fingerprint='f' * 64), dict(native_identity_sha256='f' * 64),
                   dict(rejected=False), dict(schema=True), dict(inbound_id=1), dict(inbound_tag='in-443-tcp')]
        original = self.auth_finish
        for change in changes:
            with self.subTest(change=change):
                self.auth_events.clear()
                (self.root / 'events').unlink(missing_ok=True)
                def finish(nonce):
                    return dict(original(nonce), **change)
                with self.assertRaises(probe.Error):
                    self.run_proof(auth_finish=finish)
                self.assertEqual(len([e for e in self.events() if 'pid' in e]), 2)
                self.assert_processes_clean()
        (self.root / 'events').unlink()
        self.auth_events.clear()
        with self.assertRaisesRegex(probe.Error, 'restoration failed'):
            self.run_proof(auth_finish=mock.Mock(side_effect=probe.Error('restoration failed')))
        self.assertEqual(len([e for e in self.events() if 'pid' in e]), 2)
        self.assert_processes_clean()

    def test_begin_requires_exact_operation_fingerprint_identity_nonce_and_schema(self):
        original = self.auth_begin
        changes = [dict(operation_id='f' * 32), dict(request_fingerprint='f' * 64),
                   dict(native_identity_sha256=None), dict(nonce='malformed-private-nonce'),
                   dict(schema=True), dict(raw_log='untrusted-extra-metadata'), dict(inbound_id=1), dict(inbound_tag='SELF-main')]
        for change in changes:
            self.auth_events.clear()
            with self.subTest(change=change), self.assertRaises(probe.Error):
                self.run_proof(auth_begin=lambda: dict(original(), **change))
            self.assertFalse((self.root / 'events').exists())

    def test_nonce_excludes_normalized_registered_users_before_any_client(self):
        # The full wire UUID differs while pinned ProcessUUID is identical.
        raw = bytearray(uuid.UUID(UUID).bytes); raw[6:8] = b'\x55\x66'
        self.nonce = str(uuid.UUID(bytes=bytes(raw)))
        with self.assertRaisesRegex(probe.Error, 'normalized identity'):
            self.run_proof()
        self.assertFalse((self.root / 'events').exists())

    def test_no_actual_edge_socket_fails_even_with_trace_and_https(self):
        with mock.patch.object(probe, 'socket_peers', return_value=set()):
            with self.client() as client:
                with self.assertRaisesRegex(probe.Error, 'actual pinned'):
                    client.https('www.python.org', edge_ip=EDGE)
        self.assert_processes_clean()

    def test_timeout_is_inconclusive_not_uuid_rejection_and_cleans_up(self):
        (self.root / 'mode').write_text('hang')
        with self.client() as client:
            client.budget.end = time.monotonic() + 0.1
            with self.assertRaisesRegex(probe.Error, 'timed out') as error:
                client.https('www.python.org')
            self.assertNotIsInstance(error.exception, probe.Rejected)
        self.assert_processes_clean()

    def test_native_mappings_preserved_and_unsafe_overrides_rejected(self):
        original = nodes()
        cdn, h2, identity = probe.native_nodes(probe.canonical({'proxies': original}), self.m, 'company', 'eno1')
        self.assertEqual([cdn, h2], original[:2]); self.assertEqual(len(identity), 64)
        for key, value in [('skip-cert-verify', True), ('tls', False), ('uuid', str(__import__('uuid').uuid4())),
                           ('server', ORIGIN), ('flow', 'xtls-rprx-vision'), ('ca-str', 'fake-ca'),
                           ('dialer-proxy', 'DIRECT'), ('servername', 'wrong.example.org')]:
            modified = nodes(); modified[0][key] = value
            with self.subTest(key=key), self.assertRaises(probe.Error):
                probe.native_nodes(probe.canonical({'proxies': modified}), self.m, 'company', 'eno1')
        with self.assertRaises(probe.Error):
            probe.native_nodes(b'proxies: []\nproxies: []\n', self.m, 'company', 'eno1')

    def test_first_vless_is_reality_but_only_exact_cdn_role_is_selected(self):
        original = nodes()
        reordered = [original[2], original[1], original[0]]
        cdn, h2, _ = probe.native_nodes(probe.canonical({'proxies': reordered}), self.m, 'company', 'eno1')
        self.assertEqual(cdn, original[0])
        self.assertEqual(h2, original[1])
        runtime = probe.runtime(cdn, 'cdn.example.org', EDGE, 'eno1', '1.1.1.1', 12345, 12346, 'secret')
        self.assertEqual(runtime['proxies'], [original[0]])
        self.assertEqual(runtime['proxy-groups'][0]['proxies'], ['SELF-cdn'])
        self.run_proof(profile_bytes=probe.canonical({'proxies': reordered}))
        self.assertEqual([e['role'] for e in self.events() if 'pid' in e], ['SELF-cdn'] * 4 + ['SELF-h2'])
        self.assert_processes_clean()
        for bad in ([original[2], original[1]], [original[2], original[1], original[2]]):
            with self.assertRaises(probe.Error):
                probe.native_nodes(probe.canonical({'proxies': bad}), self.m, 'company', 'eno1')

    def test_each_native_role_wrong_port_flow_or_credential_is_rejected(self):
        cases = [(0, 'port', 9443), (0, 'flow', 'xtls-rprx-vision'), (0, 'uuid', str(uuid.uuid4())),
                 (1, 'port', 9443), (1, 'password', 'wrong-auth'), (1, 'sni', 'cdn.example.org'), (1, 'flow', 'xtls-rprx-vision'),
                 (2, 'port', 443), (2, 'flow', ''), (2, 'uuid', str(uuid.uuid4())),
                 (2, 'network', 'xhttp'), (2, 'reality-opts', {'public-key': 'wrong', 'short-id': 'bad'})]
        for index, key, value in cases:
            modified = nodes(); modified[index][key] = value
            with self.subTest(role=modified[index]['name'], key=key), self.assertRaises(probe.Error):
                probe.native_nodes(probe.canonical({'proxies': modified}), self.m, 'company', 'eno1')

    def test_port_or_obsolete_two_target_request_refuses_before_any_client(self):
        for change in ({'tcp_cdn': {'schema': 1, 'domain': 'cdn.example.org', 'reality_port': 10443}},
                       {'tcp_cdn': {'schema': 1, 'domain': 'cdn.example.org'}},
                       {'target_transports': ['tcp-xhttp-cdn', 'hysteria2']}):
            with self.subTest(change=change), self.assertRaisesRegex(probe.Error, 'binding'):
                self.run_proof(request=dict(self.request, **change))
        self.assertFalse((self.root / 'events').exists())

    def test_stale_h2_and_missing_request_binding_refuse_before_clients(self):
        old = copy.deepcopy(self.m); old['accounts']['company']['auth'] = 'different-original-auth'
        with self.assertRaisesRegex(probe.Error, 'original Hysteria2'):
            self.run_proof(before_manifest=old)
        with self.assertRaisesRegex(probe.Error, 'binding'):
            self.run_proof(request=dict(self.request, operation_id=''))
        self.assertFalse((self.root / 'events').exists())

    def test_gray_fake_private_edges_and_expired_deadline_refuse(self):
        for ips in ([ORIGIN], ['198.18.0.2'], ['127.0.0.1'], ['10.1.1.1'], ['8.8.8.8'], []):
            with self.subTest(ips=ips), self.assertRaises(probe.Error):
                probe.edge_addresses(ips, ORIGIN)
        self.assertEqual(probe.edge_addresses([EDGE], ORIGIN), [EDGE])
        with self.assertRaisesRegex(probe.Error, 'deadline'):
            probe.Budget(time.time() - 1)

    def test_runtime_copies_complete_node_no_tun_no_ambient_routes(self):
        node = nodes()[0]
        runtime = probe.runtime(node, 'cdn.example.org', EDGE, 'eno1', '1.1.1.1', 12345, 12346, 'random-secret')
        self.assertEqual(runtime['proxies'], [node]); self.assertIsNot(runtime['proxies'][0], node)
        self.assertEqual(runtime['hosts'], {'cdn.example.org': EDGE})
        self.assertEqual(runtime['tun'], {'enable': False})
        self.assertEqual(runtime['rules'], ['MATCH,PROXY'])
        self.assertEqual(runtime['external-controller'], '127.0.0.1:12346')
        self.assertFalse(runtime['dns']['use-system-hosts']); self.assertEqual(runtime['interface-name'], 'eno1')

    def test_dns_identity_and_public_answer_validation(self):
        name = b'\x03cdn\x07example\x03org\0'
        question = name + b'\0\x01\0\x01'
        packet = struct.pack('!6H', 123, 0x8400, 1, 1, 0, 0) + question
        packet += b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 30, 4) + socket.inet_aton(EDGE)
        self.assertEqual(probe.dns_answers(packet, 123, 'cdn.example.org'), [EDGE])
        cached = struct.pack('!6H', 123, 0x8180, 1, 1, 0, 0) + packet[12:]
        with self.assertRaisesRegex(probe.Error, 'authoritative'):
            probe.dns_answers(cached, 123, 'cdn.example.org')
        for txid, domain, data in ((124, 'cdn.example.org', packet), (123, 'wrong.example.org', packet),
                                    (123, 'cdn.example.org', packet[:-3]),
                                    (123, 'cdn.example.org', packet[:-4] + socket.inet_aton('198.18.0.1'))):
            with self.assertRaises(probe.Error): probe.dns_answers(data, txid, domain)


class DNSAndOuter(unittest.TestCase):
    def test_direct_authoritative_query_rd_zero_after_gray_not_recursive_cache(self):
        replies, queries = [ORIGIN, EDGE], []
        class DNS(socketserver.BaseRequestHandler):
            def handle(self):
                def exact(count):
                    result = b''
                    while len(result) < count:
                        result += self.request.recv(count - len(result))
                    return result
                length = struct.unpack('!H', exact(2))[0]
                query = exact(length); queries.append(query)
                txid = struct.unpack('!H', query[:2])[0]
                answer = struct.pack('!6H', txid, 0x8400, 1, 1, 0, 0) + query[12:]
                answer += b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 300, 4) + socket.inet_aton(replies.pop(0))
                self.request.sendall(struct.pack('!H', len(answer)) + answer)
        server = socketserver.TCPServer(('127.0.0.1', 0), DNS)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        class Bound(socket.socket):
            def connect(self, address):
                if address != ('173.245.58.51', 53): raise AssertionError('unexpected DNS target')
                return super().connect(server.server_address)
        def bound(interface, kind, budget):
            self.assertEqual(interface, 'eno1'); self.assertEqual(kind, socket.SOCK_STREAM)
            sock = Bound(socket.AF_INET, kind); sock.settimeout(budget.left()); return sock
        try:
            with mock.patch.object(probe, 'bound_socket', side_effect=bound):
                self.assertEqual(probe.resolve_a('cdn.example.org', 'eno1', '173.245.58.51', probe.Budget(time.time() + 5)), [ORIGIN])
                self.assertEqual(probe.resolve_a('cdn.example.org', 'eno1', '173.245.58.51', probe.Budget(time.time() + 5)), [EDGE])
            self.assertEqual(len(queries), 2)
            self.assertTrue(all(struct.unpack('!H', query[2:4])[0] == 0 for query in queries))
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_outer_requires_strict_tls_matching_socket_and_cloudflare_trace(self):
        sock, tls, context = mock.MagicMock(), mock.MagicMock(), mock.MagicMock()
        sock.__enter__.return_value = sock; tls.__enter__.return_value = tls
        tls.getpeername.return_value = (EDGE, 443); tls.version.return_value = 'TLSv1.3'
        context.wrap_socket.return_value = tls
        response = (200, {'server': 'cloudflare', 'cf-ray': '01234abcdef-SJC'},
                    b'h=cdn.example.org\nhttp=http/1.1\ntls=TLSv1.3\n')
        with mock.patch.object(probe, 'bound_socket', return_value=sock), \
                mock.patch.object(probe, 'strict_context', return_value=context), \
                mock.patch.object(probe, 'http_get', return_value=response) as get:
            result = probe.edge_tls('cdn.example.org', EDGE, 'eno1', probe.Budget(time.time() + 5))
            self.assertEqual(result['peer'], EDGE)
            context.wrap_socket.assert_called_once_with(sock, server_hostname='cdn.example.org', do_handshake_on_connect=False)
            tls.do_handshake.assert_called_once()
            tls.getpeername.return_value = (ORIGIN, 443)
            with self.assertRaisesRegex(probe.Error, 'socket endpoint'):
                probe.edge_tls('cdn.example.org', EDGE, 'eno1', probe.Budget(time.time() + 5))
            tls.getpeername.return_value = (EDGE, 443)
            get.return_value = (200, {'server': 'cloudflare'}, response[2])
            with self.assertRaisesRegex(probe.Error, 'trace evidence'):
                probe.edge_tls('cdn.example.org', EDGE, 'eno1', probe.Budget(time.time() + 5))

    def test_strict_context_ignores_invocation_custom_ca_override(self):
        with mock.patch.dict(os.environ, {'SSL_CERT_FILE': '/missing/invocation-ca.pem', 'SSL_CERT_DIR': '/missing/ca'}):
            context = probe.strict_context()
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)
            self.assertNotIn('SSL_CERT_FILE', probe.client_environment())

    def test_socket_peer_evidence_is_process_owned_not_controller_claim(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); listener.listen()
            address, port = listener.getsockname()
            script = 'import socket,sys;s=socket.create_connection(("127.0.0.1",int(sys.argv[1])));print("connected",flush=True);sys.stdin.read(1)'
            child = subprocess.Popen([sys.executable, '-c', script, str(port)], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            try:
                accepted, _ = listener.accept()
                with accepted:
                    self.assertEqual(child.stdout.readline().strip(), b'connected')
                    self.assertIn((address, port), probe.socket_peers(child.pid))
            finally:
                child.communicate(b'x', timeout=3)


def select_without_idle(reads, writes, errors, timeout):
    # Remove only the declared idle measurement from unit duration; socket work
    # happens inside fixture child interpreters and remains actual network I/O.
    if not reads and not writes and not errors:
        return [], [], []
    return REAL_SELECT(reads, writes, errors, timeout)


if __name__ == '__main__':
    unittest.main()
