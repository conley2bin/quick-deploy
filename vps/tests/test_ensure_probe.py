"""ensure-probe on the synthetic owned host: classification and staged health, read-only.

Host runtime checks (native API, TLS, processes, artifacts) are injected fakes;
files, locks, markers and the unchanged host.py classifiers are real. Every
case also proves nothing under ROOT/UNITS changed and no ctl/backup/adapter
write was reached (test_connect_read.Fixture forbids those host functions).
"""
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import ssl
import sys
import uuid
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import connect_read
import host
from test_connect_read import ENDPOINT, Fixture, tree

UUID = '123e4567-e89b-12d3-a456-426614174000'


class EnsureProbe(Fixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.calls = []
        self.legacy = Path(self.tmp.name) / 'legacy'  # absent unless a test creates it
        self.listeners = b''
        self.failing = {}
        accounts = {n: {'id': str(uuid.uuid5(uuid.NAMESPACE_DNS, n)), 'email': n, 'enable': True, 'subId': 'sub-' + n + '-0123456789abcdef', 'auth': 'secret-' + n}
                    for n in host.device_names(self.manifest)}
        self.accounts_value = accounts

        def native(folder, manifest):
            self.stage('native')
            manifest['accounts'] = accounts
            return {'feed.txt': b'vless://' + UUID.encode()}

        for name, label in (('tls_probe', 'tls'), ('process_health', 'process'), ('verify_artifacts', 'artifacts'),
                            ('binary_health', 'binary')):
            mock.patch.object(host, name, side_effect=lambda *a, _n=label: self.stage(_n)).start()
        mock.patch.object(host, 'backend_health', side_effect=native).start()
        mock.patch.object(host, 'snapshot_accounts', side_effect=lambda *a, **k: self.accounts_value).start()
        mock.patch.object(host, 'run', side_effect=lambda argv, **k: mock.Mock(stdout=self.listeners)).start()
        mock.patch.object(connect_read, 'LEGACY_PATHS', (str(self.legacy),)).start()
        self.lock = {k: {'version': v} for k, v in (('panel', '9'), ('core', '26'), ('front', '2'))}
        (self.active / 'release-lock.json').write_text(json.dumps(self.lock))
        self.request = {'endpoint': ENDPOINT, 'lock': self.lock,
                        'adapter_sha256': hashlib.sha256((self.root / 'host.py').read_bytes()).hexdigest()}
        self.write_manifest(accounts=accounts)
        (self.active / 'Caddyfile').write_text(host.caddyfile(self.manifest, True))

    def stage(self, name):
        self.calls.append(name)
        if name in self.failing:
            raise self.failing[name]

    def probe(self, **change):
        before = tree(self.root), tree(self.units)
        out = io.BytesIO()
        connect_read.main(host, 'ensure-probe', io.BytesIO(json.dumps(self.request | change).encode()), out)
        self.assertEqual((tree(self.root), tree(self.units)), before, 'probe must not change owned storage')
        self.assertEqual(os.listdir(self.scratch), [])
        return json.loads(out.getvalue())

    def check(self, result, kind, failed=None):
        self.assertEqual(result['kind'], kind, result)
        self.assertEqual(result['health']['failed_check'], failed, result)
        self.assertEqual(result['health']['ok'], failed is None)
        self.assertEqual(set(result), {'kind', 'owner', 'machine_id', 'node_id', 'config', 'transports', 'tcp_cdn', 'versions',
                                       'lock_match', 'adapter_sha256', 'health', 'front_drift', 'roster_digest'})
        return result

    # --- owned -------------------------------------------------------------
    def test_healthy_owned_reports_facts_and_roster_digest_without_credentials(self):
        result = self.check(self.probe(), 'owned')
        self.assertEqual(self.calls, ['artifacts', 'binary', 'native', 'tls', 'process'])
        self.assertEqual((result['owner'], result['machine_id'], result['node_id'], result['config']),
                         ('fixture-owner', 'm' * 32, self.manifest['node_id'], self.manifest['config']))
        self.assertEqual(result['transports'], host.transports(self.manifest))
        self.assertTrue(result['lock_match'])
        self.assertEqual(result['adapter_sha256'], self.request['adapter_sha256'])
        self.assertEqual(result['roster_digest'], connect_read.roster_digest(self.accounts_value))
        self.assertFalse(result['front_drift'])
        text = json.dumps(result)
        for secret in ('secret-', 'sub-company', UUID, 'vless://'):
            self.assertNotIn(secret, text)

    def test_roster_digest_is_canonical_and_covers_enabled_flag(self):
        a = {'b': {'enable': True, 'x': 'é'}, 'a': {'enable': False}}
        b = {'a': {'enable': False}, 'b': {'x': 'é', 'enable': True}}
        self.assertEqual(connect_read.roster_digest(a), connect_read.roster_digest(b))
        self.assertEqual(connect_read.roster_digest(a), hashlib.sha256(
            '{"a":{"enable":false},"b":{"enable":true,"x":"é"}}'.encode()).hexdigest())
        b['a']['enable'] = True
        self.assertNotEqual(connect_read.roster_digest(a), connect_read.roster_digest(b))

    def test_each_stage_failure_is_named_and_remains_owned(self):
        error = host.Error('boom')
        for stage in ('artifacts', 'binary', 'native', 'tls', 'process'):
            with self.subTest(stage):
                self.calls.clear()
                self.failing = {stage: error}
                result = self.check(self.probe(), 'owned', stage)
                self.assertEqual(result['health']['error'], 'boom')
                self.assertEqual(self.calls[-1], stage, 'later stages must not run after the first failure')

    def test_release_mismatch_and_modified_unit_are_named(self):
        result = self.check(self.probe(lock=self.lock | {'core': {'version': '27'}}), 'owned', 'release')
        self.assertFalse(result['lock_match'])
        self.assertEqual(self.calls, [])
        self.assertEqual(result['versions'], {'panel': '9', 'core': '26', 'front': '2'})
        (self.active / 'release-lock.json').write_text('{"panel": {}}')
        self.check(self.probe(), 'owned', 'release')
        (self.active / 'release-lock.json').write_text(json.dumps(self.lock))
        (self.units / host.PANEL).write_text('tampered')
        self.check(self.probe(), 'owned', 'units')
        (self.units / host.PANEL).unlink()
        self.check(self.probe(), 'owned', 'units')

    def test_native_failure_still_reports_live_roster_digest(self):
        self.failing = {'native': host.Error('x')}
        result = self.check(self.probe(), 'owned', 'native')
        self.assertEqual(result['roster_digest'], connect_read.roster_digest(self.accounts_value))
        self.assertFalse(result['front_drift'], 'drift is unknowable without validated accounts')

    def test_error_text_never_carries_ids_uris_or_auth(self):
        self.failing = {'tls': host.Error('feed vless://%s@h:1?x=1 id %s auth AbCdEfGhIjKlMnOpQrSt' % (UUID, UUID))}
        error = self.probe()['health']['error']
        for secret in (UUID, 'vless://', 'AbCdEfGhIjKlMnOpQrSt'):
            self.assertNotIn(secret, error)
        self.assertIn('feed', error)

    def test_raw_tls_and_socket_errors_are_tls_health_failures(self):
        for error in (ssl.SSLCertVerificationError('certificate verify failed'), ConnectionRefusedError('refused')):
            self.failing = {'tls': error}
            result = self.check(self.probe(), 'owned', 'tls')
            self.assertIn(type(error).__name__, result['health']['error'])

    def test_malformed_unexpected_exception_is_surfaced_not_healthy(self):
        self.failing = {'tls': RuntimeError('unexpected')}
        with self.assertRaises(RuntimeError):
            self.probe()

    def test_front_drift_is_separate_and_healthy_only_when_everything_else_passes(self):
        (self.active / 'Caddyfile').write_text('stale\n')
        result = self.check(self.probe(), 'owned')
        self.assertTrue(result['front_drift'])
        self.failing = {'tls': host.Error('invalid certificate')}
        result = self.check(self.probe(), 'owned', 'tls')
        self.assertTrue(result['front_drift'] and not result['health']['ok'])
        self.failing = {}
        (self.active / 'Caddyfile').unlink()
        self.check(self.probe(), 'owned', 'front')

    # --- pending / owned_empty ----------------------------------------------
    def test_pending_variants_name_the_actual_intent(self):
        for rel, name in connect_read.INTENTS:
            with self.subTest(name):
                path = self.root / rel
                path.parent.mkdir(exist_ok=True)
                path.write_text('{}')
                result = self.check(self.probe(), 'pending', name)
                self.assertEqual(result['owner'], 'fixture-owner')
                self.assertEqual(self.calls, [])
                path.unlink()
        (self.root / 'device-rollback').rmdir()

    def test_busy_lock_is_pending_and_names_marker_when_present(self):
        fd = os.open(self.lockfile, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            self.check(self.probe(), 'pending', 'busy')
            (self.root / 'pending.json').write_text('{}')
            self.check(self.probe(), 'pending', 'pending')
        finally:
            os.close(fd)
        self.assertEqual(self.calls, [])

    def test_unsafe_lock_file_is_an_error_not_a_classification(self):
        self.lockfile.write_text('')
        self.lockfile.chmod(0o666)
        with self.assertRaises(host.Error):
            self.probe()

    def test_unpublished_is_pending(self):
        self.write_manifest(published=False)
        self.check(self.probe(), 'pending', 'unpublished')
        self.assertEqual(self.calls, [])

    def test_owned_empty_marker_without_active(self):
        import shutil
        shutil.rmtree(self.active)
        result = self.check(self.probe(), 'owned_empty')
        self.assertEqual((result['owner'], result['machine_id'], result['node_id']), ('fixture-owner', 'm' * 32, None))
        self.assertEqual(self.calls, [])

    # --- foreign -------------------------------------------------------------
    def test_foreign_variants_report_exact_reason(self):
        marker = self.root / 'owner.json'
        good = marker.read_text()
        marker.write_text('{broken')
        self.check(self.probe(), 'foreign', 'marker')
        marker.write_text(host.dumps({'format': host.FORMAT, 'owner': 'x', 'machine_id': 'm' * 32, 'extra': 1}))
        self.check(self.probe(), 'foreign', 'marker')
        marker.write_text(host.dumps({'format': host.FORMAT, 'owner': 'x', 'machine_id': 'o' * 32}))
        self.check(self.probe(), 'foreign', 'machine')
        marker.write_text(host.dumps({'format': host.FORMAT, 'owner': 'someone-else', 'machine_id': 'm' * 32}))
        self.check(self.probe(), 'foreign', 'owner_mismatch')
        marker.write_text(good)
        marker.unlink()
        self.check(self.probe(), 'foreign', 'unmarked_root')
        marker.write_text(good)
        self.write_manifest(endpoint='93.184.216.35')
        self.check(self.probe(), 'foreign', 'endpoint')
        self.write_manifest(endpoint=ENDPOINT, management={'ssh_port': 2222})
        self.check(self.probe(), 'foreign', 'management_port')
        self.manifest.pop('management')
        self.write_manifest(config={'domain': 'bad'})
        self.check(self.probe(), 'foreign', 'manifest')
        self.assertEqual(self.calls, [])

    def test_unsupported_adapter_and_non_regular_adapter(self):
        self.request['adapter_sha256'] = '0' * 64
        result = self.check(self.probe(), 'foreign', 'adapter')
        self.assertIsNotNone(result['adapter_sha256'])
        (self.root / 'host.py').unlink()
        (self.root / 'host.py').symlink_to(self.root / 'owner.json')
        self.check(self.probe(), 'foreign', 'adapter')
        self.assertEqual(self.calls, [])

    def test_symlinked_root_and_file_root_are_foreign(self):
        import shutil
        real = self.root.with_name('real')
        self.root.rename(real)
        self.root.symlink_to(real)
        self.check(self.probe(), 'foreign', 'root_link')
        self.root.unlink()
        real.rename(self.root)
        shutil.rmtree(self.root)
        self.root.write_text('x')
        self.check(self.probe(), 'foreign', 'root_not_directory')

    def test_corrupt_manifest_is_never_absent(self):
        (self.active / 'manifest.json').write_text('{')
        self.check(self.probe(), 'foreign', 'manifest')
        (self.active / 'manifest.json').unlink()
        self.check(self.probe(), 'foreign', 'active_tree')

    # --- absent ----------------------------------------------------------------
    def undeploy(self):
        import shutil
        shutil.rmtree(self.root)
        for name in host.UNIT_NAMES:
            (self.units / name).unlink()

    def test_absent_requires_no_root_legacy_units_or_listeners(self):
        self.undeploy()
        result = self.check(self.probe(), 'absent')
        self.assertEqual((result['owner'], result['config'], result['transports']), (None, None, []))
        self.assertTrue(self.lockfile.exists(), 'fresh-boot lock file may be created')
        self.legacy.write_text('x')
        self.check(self.probe(), 'foreign', 'legacy_path')
        self.legacy.unlink()
        (self.units / host.FRONT).write_text('old slot')
        self.check(self.probe(), 'foreign', 'unit_conflict')
        (self.units / host.FRONT).unlink()
        (self.units / host.FRONT).symlink_to('/nonexistent')
        self.check(self.probe(), 'foreign', 'unit_conflict')
        (self.units / host.FRONT).unlink()
        self.listeners = b'LISTEN 0 4096 0.0.0.0:443 0.0.0.0:*\n'
        self.check(self.probe(), 'foreign', 'listener')
        self.listeners = b'LISTEN 0 4096 [::]:22 [::]:*\n'
        self.check(self.probe(), 'absent')

    def test_absent_while_install_holds_lock_is_busy_not_absent(self):
        self.undeploy()
        fd = os.open(self.lockfile, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            self.check(self.probe(), 'pending', 'busy')
        finally:
            os.close(fd)

    def test_wrong_endpoint_request_is_an_error(self):
        with self.assertRaises(host.Error):
            self.probe(endpoint='93.184.216.99')


if __name__ == '__main__':
    unittest.main()
