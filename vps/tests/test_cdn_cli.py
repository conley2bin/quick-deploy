"""Controller contract tests: no Host implementation, SSH, panel or native API.

Explicit Host API stubs model the frozen interface while the Host owner works
independently. Existing test_dual_cli exercises real archive/generation readers;
these tests discriminate controller ordering, proof binding and reconciliation.
"""
import contextlib
import copy
from datetime import datetime, timezone, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import select
import signal
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tarfile
import time
import unittest
import uuid
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import cdn_probe
import host
import vps
import test_cdn_probe as probe_fixtures
from test_cdn_probe import FAKE_CLIENT, manifest as node_manifest, nodes

REAL_CDN_PROBE = cdn_probe.validate_cdn_candidate


def transport_contract(request):
    """Only the documented request shape/hash; not a fake Host migration."""
    required = ('owner', 'machine_id', 'endpoint', 'config', 'operation_id', 'adapter_sha256',
                'ssh_port', 'mode', 'before_sha256', 'feature', 'source_transports', 'target_transports', 'tcp_cdn')
    if (set(required) - request.keys() or request['feature'] != 'tcp-cdn' or
            request['source_transports'] != ['tcp-reality', 'hysteria2'] or
            request['target_transports'] != ['tcp-reality', 'tcp-xhttp-cdn', 'hysteria2'] or
            set(request['tcp_cdn']) != {'schema', 'domain', 'reality_port'}):
        raise host.Error('contract shape mismatch')
    return hashlib.sha256(host.dumps({key: request[key] for key in required}).encode()).hexdigest()


class CDNCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='qd-cdn-cli-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = vps.private_dir(self.root / 'private')
        self.config = {'domain': 'sub.example.org', 'reality_target': 'target.example.org', 'acme_email': '', 'proxy_port': 443}
        self.record = {'endpoint': '93.184.216.34', 'target': 'saved-alias', 'machine_id': 'b' * 32,
                       'port': 22, 'user': 'root', 'options': vps.SSH_OPTIONS, 'host_key_alias': None}
        self.before = node_manifest()
        self.before.pop('tcp_cdn')
        self.before.update(owner='owner-fixture', config=self.config, node_id='node-fixture', published=True)
        self.candidate = dict(copy.deepcopy(self.before), tcp_cdn=node_manifest()['tcp_cdn'], published=False)
        self.final = copy.deepcopy(self.candidate)
        self.after = dict(copy.deepcopy(self.final), published=True)
        self.paths = {}
        for name, manifest in [('before', self.before), ('candidate', self.candidate), ('final', self.final), ('after', self.after)]:
            path = self.state / (name + '.tar')
            host.atomic(path, name.encode())
            self.paths[path] = manifest
            setattr(self, name + '_path', path)
        self.doc = {'format': host.FORMAT, 'owner': self.before['owner'], 'config': self.config,
                    'current': self.record, 'previous': [], 'backup': str(self.before_path)}
        host.atomic(self.state / 'state.json', host.dumps(self.doc))
        self.args = vps.parser().parse_args(['cdn', '--domain', 'cdn.example.org', '--interface', 'eno1',
                                           '--dns-server', '1.1.1.1', '--mihomo', '/fixture/mihomo'])
        self.events, self.backups = [], [self.before_path, self.candidate_path, self.final_path, self.after_path]
        self.auth_change = None
        self.auth_nonce = str(uuid.uuid4())
        self.diagnostic_active = self.witness_ready = False
        self.committed = False
        self.proof_change = None
        self.prepare_change = None
        self.lost_ack = False
        self.queued = False
        self.patch(host, 'cdn_enabled', side_effect=lambda m: 'tcp_cdn' in m, create=True)
        self.patch(host, 'cdn_proof_digest', side_effect=lambda proof: hashlib.sha256(cdn_probe.canonical(proof)).hexdigest(), create=True)
        self.patch(host, 'transport_request', side_effect=transport_contract)
        self.patch(host, 'dual_enabled', return_value=True)
        self.patch(host, 'validate_feed_scope')
        self.patch(vps, 'checked_backup', side_effect=lambda doc, rec, path: self.paths[Path(path)])
        self.patch(vps, 'cdn_candidate_identity', side_effect=lambda path, manifest, files, interface:
                   copy.deepcopy({'manifest': manifest, 'files': files}))
        self.patch(vps, 'resolve_mihomo', return_value='/fixture/mihomo')
        self.patch(cdn_probe, 'environment')
        self.patch(cdn_probe, 'resolve_a', return_value=[self.record['endpoint']])
        self.patch(vps, 'backup_remote', side_effect=self.backup)
        self.patch(vps, 'validate_outputs', side_effect=self.outputs)
        self.patch(vps, 'remote', side_effect=self.remote)
        self.patch(vps, 'acknowledge_orange', side_effect=lambda *_: self.events.append('ack'))
        self.patch(cdn_probe, 'validate_cdn_candidate', side_effect=self.probe)
        self.patch(vps, 'export_after_commit', side_effect=lambda *_: self.events.append('export'))
        self.patch(sys, 'stdout', new=io.StringIO())
        self.patch(sys, 'stderr', new=io.StringIO())

    def patch(self, obj, name, **kwargs):
        patch = mock.patch.object(obj, name, **kwargs)
        result = patch.start(); self.addCleanup(patch.stop)
        return result

    def backup(self, *_):
        self.events.append('backup')
        return self.backups.pop(0)

    def outputs(self, state, doc, record, binary, backup):
        self.events.append('outputs')
        manifest = self.paths[Path(backup)]
        data = cdn_probe.canonical({'proxies': nodes()})
        vps.store_generation(state, manifest, host.digest(backup), {'company.yaml': data, 'company.txt': b'native-uri-fixture'})
        return host.digest(backup)

    def status(self):
        return {'adapter_sha256': host.digest(vps.MODULE / 'lib/host.py'), 'needs_transport_upgrade': False,
                'transports': ['tcp-reality', 'tcp-xhttp-cdn', 'hysteria2'] if self.committed else ['tcp-reality', 'hysteria2'],
                'machine_id': self.record['machine_id'], 'node_id': self.before['node_id']}

    def remote(self, record, action, request=None, stdin=None, **kwargs):
        self.events.append(action)
        self.assertEqual(record, self.record)
        if action == 'status': return self.status()
        if action == 'transport-prepare':
            import tarfile
            with tarfile.open(fileobj=stdin) as archive:
                self.assertEqual(set(archive.getnames()), {'host.py', 'request.json'})
                self.request = json.load(archive.extractfile('request.json'))
            self.assertNotIn('tcp_cdn', vps.load(self.state / 'state.json'))
            self.assertEqual(self.request['config'], self.config)
            self.assertEqual(self.request['before_sha256'], host.digest(self.before_path))
            self.assertEqual(self.events[:3], ['status', 'backup', 'transport-prepare'])
            result = {'prepared': True, 'deadline': time.time() + 600,
                      'request_sha256': host.transport_request(self.request),
                      'transports': self.request['target_transports'], 'tcp_cdn': self.candidate['tcp_cdn']}
            if self.prepare_change: self.prepare_change(result)
            return result
        if action == 'transport-auth-begin':
            self.assertEqual(request, self.request)
            self.diagnostic_active = True
            result = {'schema': 1, 'operation_id': request['operation_id'],
                      'request_fingerprint': host.transport_request(request), 'nonce': self.auth_nonce,
                      'native_identity_sha256': 'e' * 64, 'inbound_id': 3, 'inbound_tag': 'SELF-cdn'}
            if self.auth_change: self.auth_change(action, result)
            return result
        if action == 'transport-auth-finish':
            self.assertEqual(request, dict(self.request, cdn_auth_nonce=self.auth_nonce))
            self.assertTrue(self.diagnostic_active)
            self.diagnostic_active = False
            self.witness_ready = True
            result = {'schema': 1, 'operation_id': request['operation_id'],
                      'request_fingerprint': host.transport_request(request),
                      'nonce_sha256': hashlib.sha256(self.auth_nonce.encode()).hexdigest(),
                      'rejected': True, 'native_identity_sha256': 'e' * 64, 'inbound_id': 3, 'inbound_tag': 'SELF-cdn'}
            self.verdict = copy.deepcopy(result)
            if self.auth_change: self.auth_change(action, result)
            return result
        if action == 'transport-commit':
            self.assertFalse(self.diagnostic_active)
            self.assertTrue(self.witness_ready)
            self.assertEqual(request['offnode_sha256'], host.digest(self.final_path))
            pending = vps.load(self.state / 'pending.json')
            self.assertEqual(pending['backup'], str(self.final_path))
            self.assertNotEqual(request['offnode_sha256'], self.request['before_sha256'])
            proof_path = self.state / 'cdn-proofs' / (self.request['operation_id'] + '.json')
            self.assertEqual(proof_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(vps.load(proof_path), request['cdn_proof'])
            self.assertNotIn('tcp_cdn', vps.load(self.state / 'state.json'))
            self.receipt = {'committed': True, 'request_sha256': host.transport_request(self.request),
                            'offnode_sha256': request['offnode_sha256'], 'cdn_proof_sha256': request['cdn_proof_sha256'],
                            'cdn_auth_verdict': copy.deepcopy(self.verdict)}
            self.committed = True
            if self.lost_ack: raise host.Error('lost commit ACK')
            return self.receipt
        if action == 'abort':
            self.assertEqual(request, self.request)
            if self.queued: return {'queued': True}
            self.diagnostic_active = False
            return self.receipt if self.committed else {'committed': False}
        raise AssertionError('Unmocked remote action: ' + action)

    def probe(self, **kwargs):
        self.events.append('probe')
        self.assertEqual(kwargs['profile_bytes'], cdn_probe.canonical({'proxies': nodes()}))
        self.assertEqual(kwargs['before_manifest'], self.before if not self.committed else self.after)
        begin = cdn_probe.auth_binding(kwargs['auth_begin'](), kwargs['request'], kwargs['request_fingerprint'], kwargs['manifest'])
        cdn_probe.auth_binding(kwargs['auth_finish'](begin['nonce']), kwargs['request'],
                               kwargs['request_fingerprint'], kwargs['manifest'], begin=begin)
        final_digest = kwargs['final_candidate']()
        account = self.after['accounts']['company']
        proof = {'schema': 1, 'operation_id': kwargs['request']['operation_id'],
                 'request_fingerprint': kwargs['request_fingerprint'], 'candidate_sha256': final_digest,
                 'domain': 'cdn.example.org', 'device_identity_sha256': hashlib.sha256(cdn_probe.canonical({
                     'device': 'company', 'id': account['id'], 'auth': account['auth']})).hexdigest(),
                 'observed_at': datetime.now(timezone.utc).isoformat(), 'interface': 'eno1',
                 'edge_ips': ['104.16.1.2'], 'checks': dict.fromkeys(cdn_probe.CHECKS, True)}
        if self.proof_change: self.proof_change(proof)
        return proof

    def run_cdn(self):
        return vps.cdn(self.state, self.doc, self.args)

    def test_cli_separates_cdn_domain_and_requires_public_resolver_underlay(self):
        self.assertEqual(self.args.cdn_domain, 'cdn.example.org')
        self.assertFalse(hasattr(self.args, 'domain'))
        self.assertEqual(vps.configuration(self.args, self.config), self.config)
        with self.assertRaises(SystemExit):
            vps.parser().parse_args(['cdn', '--domain', 'cdn.example.org', '--interface', 'eno1'])
        with mock.patch.object(sys, 'stderr', new=io.StringIO()):
            for command in ('deploy', 'preflight', 'status', 'backup', 'recover', 'export', 'dashboard'):
                with self.subTest(command=command), self.assertRaises(SystemExit):
                    vps.parser().parse_args([command, '--reality-port', '10443'])
        for flag in ('--ssh', '--proxy-port', '--reality-target'):
            with self.assertRaises(SystemExit):
                vps.parser().parse_args(['cdn', '--domain', 'cdn.example.org', '--interface', 'eno1',
                                        '--dns-server', '1.1.1.1', flag, 'anything'])

    def test_explicit_port_proposal_fingerprint_and_reserved_ports(self):
        self.assertEqual(self.args.reality_port, 9443)
        for port in (9443, 10443):
            request, fingerprint = vps.cdn_request(self.doc, self.record, 'cdn.example.org', self.before_path, 'a' * 64, port)
            self.assertEqual(request['tcp_cdn'], {'schema': 1, 'domain': 'cdn.example.org', 'reality_port': port})
            self.assertEqual(request['config']['proxy_port'], 443)
            changed = dict(request, tcp_cdn=dict(request['tcp_cdn'], reality_port=8444))
            self.assertNotEqual(fingerprint, host.transport_request(changed))
        for value in (None, True, '9443', 0, 65536, 22, 80, 443, 8443, 2080,
                      host.ADMIN_PORT, host.SUB_PORT, host.API_PORT):
            with self.subTest(port=value), self.assertRaisesRegex(host.Error, 'non-reserved'):
                vps.cdn_request(self.doc, self.record, 'cdn.example.org', self.before_path, 'a' * 64, value)
        with self.assertRaises(host.Error):
            vps.cdn_reality_port(9443, 9443)
        self.assertEqual(vps.cdn_reality_port(65535, 2222), 65535)
        self.assertEqual(self.events, [])

    def test_port_or_obsolete_topology_prepare_tamper_aborts(self):
        for change in ({'transports': ['tcp-xhttp-cdn', 'hysteria2']},
                       {'tcp_cdn': dict(self.candidate['tcp_cdn'], reality_port=10443)},
                       {'tcp_cdn': dict(self.candidate['tcp_cdn'], reality_tag='in-9443-tcp')}):
            self.events.clear(); self.backups = [self.before_path, self.candidate_path]
            self.prepare_change = lambda result: result.update(change)
            with self.subTest(change=change), self.assertRaisesRegex(host.Error, 'prepare differs'):
                self.run_cdn()
            self.assertNotIn('probe', self.events)
            self.assertNotIn('transport-commit', self.events)
            self.assertIn('abort', self.events)

    def test_reserved_port_and_target_hostname_refuse_before_remote_mutation(self):
        for value in (80, 443, 8443, 2080, 22, 0, 65536):
            self.args.reality_port = value
            with self.subTest(port=value), self.assertRaises(host.Error): self.run_cdn()
        self.args.reality_port = 9443
        for domain in ('sub.example.org', 'target.example.org'):
            self.args.cdn_domain = domain
            with self.assertRaisesRegex(host.Error, 'separate hostname'): self.run_cdn()
        self.assertEqual(self.events, [])

    def test_candidate_proof_before_commit_published_archive_only_projection(self):
        self.run_cdn()
        self.assertEqual(self.events, ['status', 'backup', 'transport-prepare', 'backup', 'outputs', 'ack', 'probe',
                                      'transport-auth-begin', 'transport-auth-finish', 'backup', 'outputs',
                                      'transport-commit', 'backup', 'outputs', 'status', 'export'])
        saved = vps.load(self.state / 'state.json')
        self.assertEqual(saved['tcp_cdn'], {'schema': 1, 'domain': 'cdn.example.org', 'reality_port': 9443})
        self.assertEqual(saved['backup'], str(self.after_path))
        self.assertEqual(saved['config'], self.config)
        self.assertFalse((self.state / 'pending.json').exists())
        proof = vps.load(self.state / 'cdn-proofs' / (self.request['operation_id'] + '.json'))
        self.assertNotIn(self.after['accounts']['company']['id'], json.dumps(proof))
        self.assertEqual(proof['candidate_sha256'], host.digest(self.final_path))
        self.assertNotEqual(proof['candidate_sha256'], host.digest(self.candidate_path))

    def test_invalid_proofs_never_commit_and_abort_original_mode(self):
        modifications = [lambda p: p.pop('checks'), lambda p: p.update(candidate_sha256='d' * 64),
                         lambda p: p.update(device_identity_sha256='e' * 64),
                         lambda p: p.update(domain='wrong.example.org'), lambda p: p.update(schema=True),
                         lambda p: p['checks'].update(edge_tls=False), lambda p: p.update(edge_ips=['93.184.216.34']),
                         lambda p: p.update(observed_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()),
                         lambda p: p.update(secret='must-not-be-written')]
        for change in modifications:
            with self.subTest(change=change):
                self.events.clear(); self.backups = [self.before_path, self.candidate_path, self.final_path]
                self.proof_change = change
                with self.assertRaises(host.Error): self.run_cdn()
                self.assertNotIn('transport-commit', self.events); self.assertIn('abort', self.events)
                self.assertNotIn('tcp_cdn', vps.load(self.state / 'state.json'))
                self.assertFalse((self.state / 'pending.json').exists())
                self.assertFalse((self.state / 'cdn-proofs').exists())

    def test_failed_probe_or_eof_abort_and_queued_recovery_retains_pending(self):
        self.queued = True
        with mock.patch.object(vps, 'acknowledge_orange', side_effect=host.Error('EOF')):
            with self.assertRaisesRegex(host.Error, 'EOF'): self.run_cdn()
        self.assertTrue((self.state / 'pending.json').exists())
        self.assertNotIn('probe', self.events); self.assertNotIn('transport-commit', self.events)
        self.assertNotIn('tcp_cdn', vps.load(self.state / 'state.json'))

    def test_missing_expired_or_too_short_prepare_deadline_aborts(self):
        for deadline in (None, time.time() - 1, time.time() + 30, float('inf')):
            self.events.clear(); self.backups = [self.before_path]
            self.prepare_change = lambda prepared: prepared.update(deadline=deadline)
            with self.subTest(deadline=deadline), self.assertRaises(host.Error): self.run_cdn()
            self.assertIn('abort', self.events); self.assertNotIn('ack', self.events)
            self.assertFalse((self.state / 'pending.json').exists())

    def test_lost_ack_reconciles_exact_receipt_without_replay_or_reproof(self):
        self.lost_ack = True
        self.run_cdn()
        self.assertEqual(self.events.count('transport-prepare'), 1)
        self.assertEqual(self.events.count('transport-commit'), 1)
        self.assertEqual(self.events.count('probe'), 1)
        self.assertEqual(self.events.count('abort'), 1)
        self.assertEqual(vps.load(self.state / 'state.json')['tcp_cdn']['domain'], 'cdn.example.org')
        self.assertFalse((self.state / 'pending.json').exists())

    def test_receipt_wrong_proof_hash_preserves_pending_instead_of_adopting(self):
        self.lost_ack = True
        remote = self.remote
        def changed(*args, **kwargs):
            if args[1] == 'abort':
                self.receipt['cdn_proof_sha256'] = 'f' * 64
            return remote(*args, **kwargs)
        with mock.patch.object(vps, 'remote', side_effect=changed), self.assertRaises(host.Error):
            self.run_cdn()
        self.assertTrue((self.state / 'pending.json').exists())
        self.assertNotIn('tcp_cdn', vps.load(self.state / 'state.json'))

    def test_lost_ack_receipt_wrong_role_nonce_or_verdict_type_retains_pending(self):
        self.lost_ack = True
        self.run_cdn()
        pending = {'request': self.request, 'backup': str(self.final_path),
                   'cdn_proof_sha256': self.receipt['cdn_proof_sha256']}
        good = copy.deepcopy(self.receipt)
        for change in ({'inbound_id': 1}, {'inbound_tag': 'in-443-tcp'}, {'inbound_tag': 'SELF-main'},
                       {'nonce_sha256': 'f' * 64}, {'schema': True}, {'inbound_id': 3.0},
                       {'native_identity_sha256': 'f' * 64}, {'unexpected': 'extra'}):
            receipt = dict(good, cdn_auth_verdict=dict(good['cdn_auth_verdict'], **change))
            with self.subTest(change=change), self.assertRaisesRegex(host.Error, 'receipt'):
                vps.check_cdn_receipt(self.state, pending, receipt)
        with self.assertRaises(host.Error):
            vps.check_cdn_receipt(self.state, pending, dict(good, cdn_auth_verdict=None))
        tampered = dict(pending, request=dict(self.request, tcp_cdn=dict(self.request['tcp_cdn'], reality_port=10443)))
        with self.assertRaisesRegex(host.Error, 'receipt'):
            vps.check_cdn_receipt(self.state, tampered, good)
        vps.check_cdn_receipt(self.state, pending, good)
        self.assertEqual(self.events.count('transport-prepare'), 1)

    def test_non443_pending_legacy_foreign_domain_refuse_without_mutation(self):
        self.doc['config']['proxy_port'] = 8443
        with self.assertRaisesRegex(host.Error, 'port443'): self.run_cdn()
        self.assertEqual(self.events, [])
        self.doc['config']['proxy_port'] = 443
        host.atomic(self.state / 'pending.json', '{}')
        with self.assertRaisesRegex(host.Error, 'pending'): self.run_cdn()
        self.assertEqual(self.events, [])
        (self.state / 'pending.json').unlink()
        with mock.patch.object(vps, 'remote', return_value=dict(self.status(), needs_transport_upgrade=True)):
            with self.assertRaisesRegex(host.Error, 'topology'): self.run_cdn()
        self.assertNotIn('backup', self.events)

    def test_same_domain_refuses_fresh_proof_without_prepared_witness(self):
        self.committed = True
        self.doc.update(backup=str(self.after_path), tcp_cdn={'schema': 1, 'domain': 'cdn.example.org'})
        self.backups = [self.after_path]
        original = (self.state / 'state.json').read_bytes()
        with self.assertRaisesRegex(host.Error, 'already CDN.*No fresh proof'):
            self.run_cdn()
        self.assertEqual(self.events, ['status'])
        self.assertEqual((self.state / 'state.json').read_bytes(), original)
        self.assertFalse((self.state / 'pending.json').exists())
        self.args.reality_port = 10443; self.events.clear()
        with self.assertRaisesRegex(host.Error, 'different CDN domain or REALITY port'): self.run_cdn()
        self.args.reality_port = 9443
        self.args.cdn_domain = 'different.example.org'; self.events.clear()
        with self.assertRaisesRegex(host.Error, 'different CDN domain or REALITY port'): self.run_cdn()
        self.assertNotIn('backup', self.events)

    def test_backup_capture_failure_still_projects_verified_published_cdn(self):
        with mock.patch.object(vps, 'validate_outputs', side_effect=host.Error('native output unavailable')):
            with self.assertRaisesRegex(host.Error, 'Backup committed; offline snapshot not captured'):
                vps.backup_committed(self.state, self.doc, self.record, self.after_path)
        saved = vps.load(self.state / 'state.json')
        self.assertEqual(saved['tcp_cdn'], {'schema': 1, 'domain': 'cdn.example.org', 'reality_port': 9443})
        self.assertEqual(saved['backup'], str(self.after_path))
        self.assertNotIn('native_generation', saved)

    def test_ordinary_deploy_retains_archive_port_without_new_acceptance(self):
        self.committed = True
        self.doc.update(backup=str(self.after_path), tcp_cdn={'schema': 1, 'domain': 'stale.example.org', 'reality_port': 10443})
        self.backups = [self.after_path]
        remote = self.remote
        def unchanged(record, action, *args, **kwargs):
            if action == 'prepare':
                self.events.append(action)
                return {'unchanged': True, 'needs_transport_upgrade': False}
            return remote(record, action, *args, **kwargs)
        with mock.patch.object(vps, 'remote', side_effect=unchanged), mock.patch.object(vps, 'artifacts'), \
                mock.patch.object(vps, 'upload_bundle', return_value=contextlib.nullcontext(io.BytesIO())):
            vps.deploy_existing(self.state, self.doc, self.record, vps.parser().parse_args(['deploy']))
        self.assertEqual(self.events, ['status', 'prepare', 'backup', 'outputs', 'status', 'export'])
        self.assertEqual(self.doc['tcp_cdn'], {'schema': 1, 'domain': 'cdn.example.org', 'reality_port': 9443})
        self.assertNotIn('probe', self.events)
        self.assertNotIn('transport-prepare', self.events)
        self.assertFalse((self.state / 'pending.json').exists())

    def test_preflight_gray_origin_and_prerequisite_failures_do_not_mutate(self):
        with mock.patch.object(cdn_probe, 'resolve_a', return_value=['104.16.1.2']):
            with self.assertRaisesRegex(host.Error, 'gray/DNS-only'): self.run_cdn()
        self.assertEqual(self.events, ['status'])
        self.events.clear()
        with mock.patch.object(cdn_probe, 'environment', side_effect=cdn_probe.Error('prerequisite absent')):
            with self.assertRaisesRegex(host.Error, 'prerequisite absent'): self.run_cdn()
        self.assertEqual(self.events, [])

    def test_candidate_archive_mutation_during_probe_refuses_commit(self):
        self.proof_change = lambda proof: host.atomic(self.candidate_path, b'changed-after-validation')
        with self.assertRaisesRegex(host.Error, 'archive changed'): self.run_cdn()
        self.assertNotIn('transport-commit', self.events); self.assertIn('abort', self.events)
        self.assertNotIn('tcp_cdn', vps.load(self.state / 'state.json'))

    def test_accept_all_injected_reset_missing_native_verdict_aborts_controller(self):
        fixture = probe_fixtures.Probe('test_accept_all_one_wrong_attempt_reset_without_witness_fails')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        (fixture.root / 'mode').write_text('accept-reset')
        def probe(**kwargs):
            kwargs['binary'] = str(fixture.binary)
            return REAL_CDN_PROBE(**kwargs)
        def remote(*args, **kwargs):
            result = self.remote(*args, **kwargs)
            return None if args[1] == 'transport-auth-finish' else result
        with mock.patch.object(cdn_probe, 'validate_cdn_candidate', side_effect=probe), \
                mock.patch.object(cdn_probe, 'resolve_a', side_effect=[[self.record['endpoint']], ['104.16.1.2']]), \
                mock.patch.object(cdn_probe, 'edge_tls', return_value={'peer': '104.16.1.2', 'port': 443}), \
                mock.patch.object(cdn_probe, 'socket_peers', return_value={('104.16.1.2', 443)}), \
                mock.patch.object(cdn_probe.select, 'select', wraps=probe_fixtures.select_without_idle), \
                mock.patch.object(vps, 'remote', side_effect=remote):
            with self.assertRaisesRegex(host.Error, 'witness'):
                self.run_cdn()
        self.assertEqual(sum(e.get('event') == 'injected-reset' for e in fixture.events()), 1)
        fixture.assert_processes_clean()
        self.assertNotIn('transport-commit', self.events)
        self.assertIn('transport-auth-finish', self.events)
        self.assertIn('abort', self.events)
        self.assertFalse(self.diagnostic_active)
        self.assertFalse((self.state / 'cdn-proofs').exists())
        self.assertFalse((self.state / 'pending.json').exists())

    def test_matching_restored_witness_real_probe_checks_bind_final_b(self):
        fixture = probe_fixtures.Probe('test_five_fresh_clients_negative_valid_reconnect_and_h2_proof')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        def probe(**kwargs):
            kwargs['binary'] = str(fixture.binary)
            return REAL_CDN_PROBE(**kwargs)
        with mock.patch.object(cdn_probe, 'validate_cdn_candidate', side_effect=probe), \
                mock.patch.object(cdn_probe, 'resolve_a', side_effect=[[self.record['endpoint']], ['104.16.1.2']]), \
                mock.patch.object(cdn_probe, 'edge_tls', return_value={'peer': '104.16.1.2', 'port': 443}), \
                mock.patch.object(cdn_probe, 'socket_peers', return_value={('104.16.1.2', 443)}), \
                mock.patch.object(cdn_probe.select, 'select', wraps=probe_fixtures.select_without_idle):
            self.run_cdn()
        fixture.assert_processes_clean()
        self.assertEqual(len([e for e in fixture.events() if 'pid' in e]), 5)
        self.assertEqual(sum(e.get('uuid_sha256') == hashlib.sha256(self.auth_nonce.encode()).hexdigest()
                             for e in fixture.events()), 1)
        self.assertIn('transport-commit', self.events)
        self.assertFalse(self.diagnostic_active)
        proof = vps.load(self.state / 'cdn-proofs' / (self.request['operation_id'] + '.json'))
        self.assertEqual(proof['candidate_sha256'], host.digest(self.final_path))
        self.assertNotEqual(proof['candidate_sha256'], host.digest(self.candidate_path))

    def test_final_b_identity_policy_or_topology_change_aborts_without_proof(self):
        changes = [lambda m: m['accounts']['company'].update(id=str(uuid.uuid4())),
                   lambda m: m['accounts']['company'].update(auth='changed-private-auth'),
                   lambda m: m['accounts']['company'].update(subId='changed-private-subid'),
                   lambda m: m['accounts']['company'].update(totalGB=9),
                   lambda m: m['hysteria2'].update(inbound_id=3),
                   lambda m: m['tcp_cdn'].update(reality_port=10443),
                   lambda m: m['tcp_cdn'].update(reality_tag='in-9443-tcp'),
                   lambda m: m['tcp_cdn'].update(host_group_id='foreign-group'),
                   lambda m: m['tcp_cdn'].update(inbound_id=4)]
        for index, change in enumerate(changes):
            with self.subTest(change=change):
                self.events.clear()
                host.atomic(self.final_path, ('final-native-variant-' + str(index)).encode())
                self.backups = [self.before_path, self.candidate_path, self.final_path]
                self.final.clear(); self.final.update(copy.deepcopy(self.candidate))
                change(self.final)
                with self.assertRaisesRegex(host.Error, 'changed native'):
                    self.run_cdn()
                self.assertNotIn('transport-commit', self.events)
                self.assertIn('abort', self.events)
                self.assertFalse(self.diagnostic_active)
                self.assertFalse((self.state / 'cdn-proofs').exists())

    def test_proof_for_pre_diagnostic_a_and_mutated_final_b_never_commit(self):
        self.proof_change = lambda proof: proof.update(candidate_sha256=host.digest(self.candidate_path))
        with self.assertRaisesRegex(host.Error, 'mismatched proof'):
            self.run_cdn()
        self.assertNotIn('transport-commit', self.events)
        self.backups = [self.before_path, self.candidate_path, self.final_path]
        self.events.clear()
        self.proof_change = lambda proof: host.atomic(self.final_path, b'changed-final-after-validation')
        with self.assertRaisesRegex(host.Error, 'archive changed'):
            self.run_cdn()
        self.assertNotIn('transport-commit', self.events)
        self.assertFalse((self.state / 'cdn-proofs').exists())

    def test_native_callback_mismatch_restoration_failure_or_early_cancel_abort(self):
        changes = [('transport-auth-begin', {'operation_id': 'f' * 32}),
                   ('transport-auth-begin', {'request_fingerprint': 'f' * 64}),
                   ('transport-auth-begin', {'native_identity_sha256': None}),
                   ('transport-auth-finish', {'nonce_sha256': 'f' * 64}),
                   ('transport-auth-finish', {'native_identity_sha256': 'f' * 64}),
                   ('transport-auth-finish', {'rejected': False}),
                   ('transport-auth-begin', {'inbound_id': 1}),
                   ('transport-auth-begin', {'inbound_tag': 'SELF-main'}),
                   ('transport-auth-finish', {'inbound_id': 1}),
                   ('transport-auth-finish', {'inbound_tag': 'in-443-tcp'})]
        for action, change in changes:
            with self.subTest(action=action, change=change):
                self.events.clear(); self.backups = [self.before_path, self.candidate_path]
                self.auth_change = lambda current, result: result.update(change) if current == action else None
                with self.assertRaises(host.Error): self.run_cdn()
                self.assertNotIn('transport-commit', self.events)
                self.assertIn('abort', self.events)
                self.assertFalse(self.diagnostic_active)
        self.auth_change = None
        for cancel in (False, True):
            self.events.clear(); self.backups = [self.before_path, self.candidate_path]
            def early(**kwargs):
                kwargs['auth_begin']()
                self.assertTrue(self.diagnostic_active)
                if cancel: os.kill(os.getpid(), signal.SIGTERM)
                raise host.Error('restoration failed')
            with mock.patch.object(cdn_probe, 'validate_cdn_candidate', side_effect=early):
                with self.assertRaises(cdn_probe.Cancelled if cancel else host.Error): self.run_cdn()
            self.assertNotIn('transport-auth-finish', self.events)
            self.assertIn('abort', self.events)
            self.assertFalse(self.diagnostic_active)
            self.assertNotIn('transport-commit', self.events)
            self.assertFalse((self.state / 'cdn-proofs').exists())

    def test_lost_ack_receipt_for_a_instead_of_final_b_retains_pending(self):
        self.lost_ack = True
        remote = self.remote
        def stale(*args, **kwargs):
            if args[1] == 'abort': self.receipt['offnode_sha256'] = host.digest(self.candidate_path)
            return remote(*args, **kwargs)
        with mock.patch.object(vps, 'remote', side_effect=stale), self.assertRaises(host.Error):
            self.run_cdn()
        pending = vps.load(self.state / 'pending.json')
        self.assertEqual(pending['backup'], str(self.final_path))
        self.assertNotIn('tcp_cdn', vps.load(self.state / 'state.json'))

    def test_generation_binding_legacy_shape_unchanged_cdn_adds_metadata_only(self):
        before = vps.generation_binding(self.before, 'd' * 64)
        cdn = vps.generation_binding(self.after, 'd' * 64)
        self.assertNotIn('tcp_cdn', before)
        self.assertEqual({k: v for k, v in cdn.items() if k != 'tcp_cdn'}, before)
        self.assertEqual(cdn['tcp_cdn'], self.after['tcp_cdn'])

    def test_backup_only_projection_and_old_mode_restore_clear_it(self):
        self.doc['tcp_cdn'] = {'schema': 1, 'domain': 'stale.example.org'}
        result = {'machine_id': self.record['machine_id'], 'node_id': self.after['node_id']}
        vps.remember_commit(self.state, self.doc, self.record, self.after_path, result, use_generation=False)
        self.assertEqual(self.doc['tcp_cdn'], {'schema': 1, 'domain': 'cdn.example.org', 'reality_port': 9443})
        vps.remember_commit(self.state, self.doc, self.record, self.before_path, result, use_generation=False)
        self.assertNotIn('tcp_cdn', self.doc)
        with self.assertRaisesRegex(host.Error, 'published'):
            vps.remember_commit(self.state, self.doc, self.record, self.candidate_path, result,
                                use_generation=False, verified_manifest=self.candidate)

    def test_sigterm_restores_previous_handler_after_cancellation_and_success(self):
        previous = signal.getsignal(signal.SIGTERM)
        restored = mock.Mock()
        signal.signal(signal.SIGTERM, restored)
        try:
            with mock.patch.object(cdn_probe, 'validate_cdn_candidate',
                                   side_effect=lambda **_: os.kill(os.getpid(), signal.SIGTERM)):
                with self.assertRaises(cdn_probe.Cancelled):
                    self.run_cdn()
            self.assertIs(signal.getsignal(signal.SIGTERM), restored)
            self.assertNotIn('transport-commit', self.events)
            self.assertIn('abort', self.events)
            self.assertFalse((self.state / 'pending.json').exists())
            self.backups = [self.before_path, self.candidate_path, self.final_path, self.after_path]
            self.events.clear()
            self.run_cdn()
            self.assertIs(signal.getsignal(signal.SIGTERM), restored)
            restored.assert_not_called()
        finally:
            signal.signal(signal.SIGTERM, previous)

    def test_first_sigterm_during_reconciliation_finishes_abort(self):
        remote = self.remote
        def abort(*args, **kwargs):
            if args[1] == 'abort':
                os.kill(os.getpid(), signal.SIGTERM)
                os.kill(os.getpid(), signal.SIGTERM)
            return remote(*args, **kwargs)
        previous = signal.getsignal(signal.SIGTERM)
        with mock.patch.object(cdn_probe, 'validate_cdn_candidate', side_effect=host.Error('failed probe')), \
                mock.patch.object(vps, 'remote', side_effect=abort):
            with self.assertRaises(cdn_probe.Cancelled):
                self.run_cdn()
        self.assertIs(signal.getsignal(signal.SIGTERM), previous)
        self.assertFalse((self.state / 'pending.json').exists())
        self.assertNotIn('transport-commit', self.events)

    def test_acknowledgment_real_pipe_is_bounded_eof_timeout_or_exact_orange(self):
        # Temporarily restore the actual implementation, not input()/isatty().
        acknowledge = ORIGINAL_ACK
        for data, expect in ((b'ORANGE\n', None), (b'', 'EOF'), (b'yes\n', 'not ORANGE'), (b'x' * 66, 'input limit')):
            read, write = os.pipe()
            os.write(write, data); os.close(write)
            with os.fdopen(read) as stream, mock.patch.object(sys, 'stdin', stream):
                if expect:
                    with self.assertRaisesRegex(host.Error, expect): acknowledge('cdn.example.org', time.time() + 300)
                else:
                    acknowledge('cdn.example.org', time.time() + 300)
        read, write = os.pipe()
        try:
            with os.fdopen(read) as stream, mock.patch.object(sys, 'stdin', stream):
                with self.assertRaisesRegex(host.Error, 'timed out'):
                    acknowledge('cdn.example.org', time.time() + cdn_probe.PROBE_SECONDS + cdn_probe.COMMIT_MARGIN + .01)
        finally:
            os.close(write)


# Run the real CLI dispatch, Client ownership/cleanup and pending reconciliation
# in a fresh interpreter. Only remote operations and public acceptance are fake.
SIGTERM_CONTROLLER = r'''
import contextlib,json,os,pathlib,signal,sys,time
from unittest import mock
sys.path.insert(0,sys.argv[1])
from test_cdn_cli import CDNCLI,FAKE_CLIENT,nodes
import cdn_probe,host,vps
phase,outcome=sys.argv[2:4]; events,release=map(int,sys.argv[4:6])
test=CDNCLI();test.setUp()
binary=test.root/'fake-mihomo';binary.write_text(FAKE_CLIENT);binary.chmod(0o700)
(test.root/'origin-port').write_text('0');(test.root/'mode').write_text('normal')
clients=[];spawned=[]
def announce(stage):
 os.write(events,(json.dumps({'stage':stage,'clients':[{'pid':(c.process or spawned[-1]).pid,'folder':c.temporary.name} for c in clients]})+'\n').encode())
def hold(stage):
 announce(stage);os.read(release,1)
original_control=cdn_probe.Client.control
original_popen=cdn_probe.subprocess.Popen
original_killpg=cdn_probe.os.killpg
original_remote=test.remote
previous=signal.getsignal(signal.SIGTERM)
def control(client,*args,**kwargs):
 if phase=='enter' and args[0]=='PUT':hold('owned-ready')
 return original_control(client,*args,**kwargs)
def popen(*args,**kwargs):
 child=original_popen(*args,**kwargs)
 if phase=='spawn':
  # Popen has spawned, but Client.process has not yet received the object.
  spawned.append(child)
  hold('owned-ready')
 return child
def killpg(pid,sig):
 if sig==signal.SIGTERM and phase in ('cleanup','repeat'):
  hold('owned-ready' if phase=='cleanup' else 'cleanup-ready')
 return original_killpg(pid,sig)
def remote(*args,**kwargs):
 if args[1]=='abort' and outcome=='failed':raise host.Error('fixture reconciliation unavailable')
 return original_remote(*args,**kwargs)
def probe(**kwargs):
 kwargs['auth_begin']()
 with contextlib.ExitStack() as stack:
  for node in (nodes()[:2] if phase=='nested' else nodes()[:1]):
   client=cdn_probe.Client(str(binary),node,'cdn.example.org','104.16.1.2','eno1','1.1.1.1',cdn_probe.Budget(time.time()+30))
   clients.append(client);stack.enter_context(client)
  if phase!='cleanup':hold('owned-ready')
 raise AssertionError('cancelled probe returned without cancellation')
test.queued=outcome=='queued'
with mock.patch.object(cdn_probe,'validate_cdn_candidate',side_effect=probe), \
     mock.patch.object(cdn_probe.Client,'control',control), \
     mock.patch.object(cdn_probe.subprocess,'Popen',side_effect=popen), \
     mock.patch.object(cdn_probe.os,'killpg',side_effect=killpg), \
     mock.patch.object(vps,'remote',side_effect=remote):
 code=vps.main(['--state-dir',str(test.state),'cdn','--domain','cdn.example.org','--interface','eno1','--dns-server','1.1.1.1'])
report={'stage':'result','code':code,'events':test.events,'pending':(test.state/'pending.json').exists(),
        'saved_cdn':'tcp_cdn' in vps.load(test.state/'state.json'),'proof':(test.state/'cdn-proofs').exists(),
        'stderr':sys.stderr.getvalue(),'restored':signal.getsignal(signal.SIGTERM)==previous,
        'diagnostic_active':test.diagnostic_active}
if report['pending']:report['pending_mode']=vps.load(test.state/'pending.json')['mode']
os.write(events,(json.dumps(report)+'\n').encode())
test.doCleanups()
sys.exit(code)
'''


class SIGTERMSubprocess(unittest.TestCase):
    def test_term_unwinds_owned_clients_and_reconciles_or_retains_pending(self):
        cases = [('active', 'restored'), ('enter', 'queued'), ('spawn', 'restored'),
                 ('nested', 'failed'), ('cleanup', 'restored'), ('repeat', 'restored')]
        for phase, outcome in cases:
            with self.subTest(phase=phase, outcome=outcome):
                self.run_controller(phase, outcome)

    def run_controller(self, phase, outcome):
        read, write = os.pipe()
        release, send = os.pipe()
        child = subprocess.Popen([sys.executable, '-B', '-c', SIGTERM_CONTROLLER,
            str(Path(__file__).resolve().parent), phase, outcome, str(write), str(release)],
            pass_fds=(write, release), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        os.close(write); os.close(release)
        clients = []
        deadline = time.monotonic() + 12
        def event():
            data = bytearray()
            while not data.endswith(b'\n'):
                remaining = deadline - time.monotonic()
                self.assertGreater(remaining, 0, 'controller lifecycle deadline expired')
                self.assertTrue(select.select([read], [], [], remaining)[0], 'controller event timeout')
                chunk = os.read(read, 1)
                self.assertTrue(chunk, 'controller exited before lifecycle event')
                data.extend(chunk)
            return json.loads(data)
        try:
            ready = event()
            self.assertEqual(ready['stage'], 'owned-ready')
            clients = ready['clients']
            self.assertEqual(len(clients), 2 if phase == 'nested' else 1)
            for client in clients:
                self.assertTrue(Path('/proc', str(client['pid'])).exists())
                config = Path(client['folder']) / 'config.json'
                self.assertEqual(config.stat().st_mode & 0o777, 0o600)
                node = json.loads(config.read_text())['proxies'][0]
                self.assertIn(node.get('uuid', node.get('password')),
                              ('11111111-1111-4111-8111-111111111111', 'fixture-h2-auth-original'))
            os.kill(child.pid, signal.SIGTERM)
            if phase in ('spawn', 'cleanup'):
                os.write(send, b'x')
            if phase == 'repeat':
                self.assertEqual(event()['stage'], 'cleanup-ready')
                os.kill(child.pid, signal.SIGTERM)
                os.write(send, b'x')
            result = event()
            stdout, stderr = child.communicate(timeout=max(.1, deadline - time.monotonic()))
            self.assertEqual(child.returncode, 143, (stdout, stderr))
            self.assertEqual(result['code'], 143)
            self.assertTrue(result['restored'])
            self.assertNotIn('transport-commit', result['events'])
            self.assertFalse(result['saved_cdn']); self.assertFalse(result['proof'])
            self.assertEqual(result['pending'], outcome != 'restored')
            self.assertEqual(result['diagnostic_active'], result['pending'])
            if result['pending']:
                self.assertEqual(result['pending_mode'], 'transport-upgrade')
                self.assertIn('pending', result['stderr'])
            for client in clients:
                self.assertFalse(Path('/proc', str(client['pid'])).exists())
                self.assertFalse(Path(client['folder']).exists())
        finally:
            if child.poll() is None:
                with contextlib.suppress(BrokenPipeError):
                    os.write(send, b'xxx')
                child.terminate()
                try:
                    child.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
            child.communicate(timeout=3)
            os.close(read); os.close(send)
            # Test failures must not strand fake credential-bearing processes.
            for client in clients:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(client['pid'], signal.SIGKILL)
                shutil.rmtree(client['folder'], ignore_errors=True)


class LegacyControllerCompatibility(unittest.TestCase):
    def test_featureless_fingerprint_vectors_remain_exact(self):
        request = {'owner': 'fixture-owner', 'machine_id': 'b' * 32, 'endpoint': '93.184.216.34',
                   'config': {'domain': 'sub.example.org', 'reality_target': 'target.example.org',
                              'acme_email': '', 'proxy_port': 443},
                   'operation_id': 'a' * 32, 'adapter_sha256': 'c' * 64, 'ssh_port': 22,
                   'mode': 'transport-upgrade', 'before_sha256': 'd' * 64}
        self.assertEqual(host.transport_request(request), '473ced2f7344584b39998e28c6af041b063d6ee1c71b486c9742fec6a3b261d2')
        request.update(source_transports=['tcp-reality'], target_transports=['tcp-reality', 'hysteria2'])
        self.assertEqual(host.transport_request(request), '1fa65ad2db8be94772857db42c1d2b5d3713ea53a5c6f9309bb644c726177d78')
        # Featureless generation projection is byte-semantically unchanged too.
        old = node_manifest(); old.pop('tcp_cdn'); old.pop('hysteria2')
        old.update(owner='fixture-owner', node_id='fixture-node')
        self.assertNotIn('tcp_cdn', vps.generation_binding(old, 'd' * 64))


class CombinedHostContract(unittest.TestCase):
    """Real integration gate, deliberately NOT stubbed to hide old BASE Host.

    BASE8f1da15 rejects the new proposal: this gate must fail until the separately
    owned Host implementation is integrated. Controller-only suites exclude it.
    """
    def test_request_proposal_full_metadata_and_proof_share_host_contract(self):
        with tempfile.TemporaryDirectory(prefix='qd-cdn-binding-') as folder:
            before = Path(folder) / 'before.tar'
            host.atomic(before, b'closed-source-fixture')
            document = {'owner': 'fixture-owner', 'config': {'domain': 'sub.example.org',
                'reality_target': 'target.example.org', 'acme_email': '', 'proxy_port': 443}}
            record = {'machine_id': 'b' * 32, 'endpoint': '93.184.216.34', 'port': 22}
            request, fingerprint = vps.cdn_request(document, record, 'cdn.example.org', before, 'a' * 64, 9443)
            self.assertEqual(fingerprint, host.transport_request(request))
            self.assertEqual(fingerprint, transport_contract(request))
            self.assertNotEqual(fingerprint, host.transport_request(dict(request,
                tcp_cdn=dict(request['tcp_cdn'], reality_port=10443))))
            self.assertEqual(request['tcp_cdn'], {'schema': 1, 'domain': 'cdn.example.org', 'reality_port': 9443})
            target = host.new_manifest(document['owner'], document['config'], record['endpoint'])
            target.update(host_group_id='main-group', management={'ssh_port': 22}, hysteria2={
                'schema': 1, 'inbound_id': 2, 'host_group_id': 'h2-group',
                'certificate_file': 'caddy/data/caddy/certificates/issuer/sub.example.org.crt',
                'key_file': 'caddy/data/caddy/certificates/issuer/sub.example.org.key'})
            target['tcp_cdn'] = dict(request['tcp_cdn'], inbound_id=3, host_group_id='cdn-group', reality_tag='in-443-tcp', path='/qd-xhttp/', mode='packet-up',
                certificate_file='caddy/data/caddy/certificates/issuer/cdn.example.org.crt',
                key_file='caddy/data/caddy/certificates/issuer/cdn.example.org.key')
            self.assertTrue(host.cdn_enabled(target))
            self.assertEqual(request['target_transports'], host.transports(target))
            proof = {'schema': 1, 'operation_id': request['operation_id'], 'request_fingerprint': fingerprint,
                'candidate_sha256': 'c' * 64, 'domain': request['tcp_cdn']['domain'],
                'device_identity_sha256': 'd' * 64, 'observed_at': datetime.now(timezone.utc).isoformat(),
                'interface': 'eno1', 'edge_ips': ['104.16.1.2'], 'checks': dict.fromkeys(cdn_probe.CHECKS, True)}
            proof_hash = hashlib.sha256(cdn_probe.canonical(proof)).hexdigest()
            self.assertEqual(host.cdn_proof_digest(proof), proof_hash)
            self.assertEqual(host.validate_cdn_proof(dict(request, offnode_sha256='c' * 64,
                cdn_proof=proof, cdn_proof_sha256=proof_hash), fingerprint), proof)
            # Proposal and accepted metadata have different roles: request must
            # never smuggle candidate paths or assume they already exist.
            with self.assertRaises(host.Error):
                host.transport_request(dict(request, tcp_cdn=target['tcp_cdn']))


class NativeCandidateContinuity(unittest.TestCase):
    """Closed SQLite/fake native mappings only; no native panel/process/API."""
    def test_full_policy_rows_mappings_topology_and_raw_template_vs_traffic(self):
        with tempfile.TemporaryDirectory(prefix='qd-cdn-continuity-') as folder:
            root = Path(folder)
            db_path = root / 'native.db'
            manifest = node_manifest()
            manifest['config'].update(proxy_port=443, reality_target='target.example.org')
            manifest['management'] = {'ssh_port': 22}
            manifest['hysteria2'].update(host_group_id='h2-group',
                certificate_file='caddy/data/caddy/certificates/issuer/sub.example.org.crt',
                key_file='caddy/data/caddy/certificates/issuer/sub.example.org.key')
            manifest['tcp_cdn'].update(inbound_id=3,
                certificate_file='caddy/data/caddy/certificates/issuer/cdn.example.org.crt',
                key_file='caddy/data/caddy/certificates/issuer/cdn.example.org.key')
            files = {'company.yaml': cdn_probe.canonical({'proxies': nodes()})}
            with sqlite3.connect(db_path) as db:
                db.executescript('''
                    create table clients(id integer primary key,uuid text,auth text,sub_id text,
                        enable integer,total_gb integer,expiry_time integer,limit_hwid integer,flow text);
                    create table inbounds(id integer primary key,settings text,stream_settings text,
                        sniffing text,up integer,down integer,total integer,last_traffic_reset_time integer);
                    create table hosts(id integer primary key,inbound_id integer,address text,alpn text);
                    create table client_inbounds(client_id integer,inbound_id integer,flow_override text);
                    create table settings(id integer primary key,key text,value text);
                    insert into inbounds values(1,'{"clients":[],"decryption":"none"}',
                        '{"network":"tcp","security":"reality","port":9443,"tag":"in-443-tcp"}','{}',1,2,1000,0);
                    insert into inbounds values(2,'{"version":2}','{"network":"hysteria"}','{}',3,4,1000,0);
                    insert into inbounds values(3,'{"clients":[],"decryption":"none"}',
                        '{"network":"xhttp","routing":{"rules":["first","second"]}}','{}',0,0,1000,0);
                    insert into hosts values(1,1,'93.184.216.34:9443','[]');
                    insert into hosts values(2,2,'93.184.216.34','["h3"]');
                    insert into hosts values(3,3,'cdn.example.org','["h2"]');
                    insert into client_inbounds values(1,1,'xtls-rprx-vision'),(1,2,''),(1,3,'');
                    insert into settings values(1,'xrayTemplateConfig','{"log":{"loglevel":"none"}}');
                ''')
                db.executescript('''alter table inbounds add column port integer;
                    alter table inbounds add column tag text;
                    update inbounds set port=9443,tag='in-443-tcp' where id=1;
                    update inbounds set port=443,tag='SELF-h2' where id=2;
                    update inbounds set port=2080,tag='SELF-cdn' where id=3;''')
                account = manifest['accounts']['company']
                db.execute('insert into clients values(1,?,?,?,1,12345,1893456000000,3,?)',
                           (account['id'], account['auth'], account['subId'], 'xtls-rprx-vision'))
            archive = root / 'candidate.tar'
            def identity():
                with tarfile.open(archive, 'w') as out:
                    out.add(db_path, arcname='deployment/db/x-ui.db')
                # Only Host's old metadata discriminator is a temporary dependency
                # stub. SQLite extraction, all native roles and continuity are real.
                with mock.patch.object(host, 'cdn_metadata'):
                    return vps.cdn_candidate_identity(archive, manifest, files, 'eno1')
            original = identity()
            with sqlite3.connect(db_path) as db:
                db.execute('update inbounds set up=up+10,down=down+20,last_traffic_reset_time=50')
                db.execute('update inbounds set settings=? where id=1', ('{ "decryption":"none", "clients":[] }',))
            self.assertEqual(identity(), original)
            mutations = [('clients', 'uuid', str(uuid.uuid4())), ('clients', 'auth', 'changed'),
                         ('clients', 'sub_id', 'changed'), ('clients', 'enable', 0),
                         ('clients', 'total_gb', 9), ('clients', 'expiry_time', 0),
                         ('clients', 'limit_hwid', 0), ('clients', 'flow', 'changed'),
                         ('inbounds', 'total', 9), ('hosts', 'address', 'changed'),
                         ('inbounds', 'stream_settings', '{"network":"xhttp","routing":{"rules":["second","first"]}}'),
                         ('settings', 'value', '{ "log":{"loglevel":"none"}}')]
            for table, column, value in mutations:
                with self.subTest(table=table, column=column), sqlite3.connect(db_path) as db:
                    old = db.execute('select ' + column + ' from ' + table + ' where id=1').fetchone()[0]
                    db.execute('update ' + table + ' set ' + column + '=? where id=1', (value,)); db.commit()
                    self.assertNotEqual(identity(), original)
                    db.execute('update ' + table + ' set ' + column + '=? where id=1', (old,))
            with sqlite3.connect(db_path) as db:
                db.execute('delete from client_inbounds where inbound_id=2')
            self.assertNotEqual(identity(), original)
            with sqlite3.connect(db_path) as db:
                db.execute("insert into client_inbounds values(1,2,'')")
            for inbound_id in (1, 2, 3):
                for column, value in (('port', 10443), ('tag', 'changed-native-tag')):
                    with self.subTest(inbound=inbound_id, column=column), sqlite3.connect(db_path) as db:
                        old = db.execute('select ' + column + ' from inbounds where id=?', (inbound_id,)).fetchone()[0]
                        db.execute('update inbounds set ' + column + '=? where id=?', (value, inbound_id)); db.commit()
                        self.assertNotEqual(identity(), original)
                        db.execute('update inbounds set ' + column + '=? where id=?', (old, inbound_id))
                with sqlite3.connect(db_path) as db:
                    old = db.execute('select flow_override from client_inbounds where inbound_id=?', (inbound_id,)).fetchone()[0]
                    db.execute('update client_inbounds set flow_override=? where inbound_id=?', ('wrong-flow', inbound_id)); db.commit()
                    self.assertNotEqual(identity(), original)
                    db.execute('update client_inbounds set flow_override=? where inbound_id=?', (old, inbound_id))
            for index in range(3):
                changed_nodes = nodes(); changed_nodes[index]['client-fingerprint'] = 'firefox'
                files['company.yaml'] = cdn_probe.canonical({'proxies': changed_nodes})
                self.assertNotEqual(identity(), original)
            files['company.yaml'] = cdn_probe.canonical({'proxies': nodes()})
            self.assertEqual(identity(), original)


ORIGINAL_ACK = vps.acknowledge_orange

if __name__ == '__main__':
    unittest.main()
