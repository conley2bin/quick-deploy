"""Cold/add regressions composed from NativeDual, not its inherited test suite.

Explicit locked artifacts only; no SSH, systemd, public ACME or production.
Functional tests check the pinned API's exact earliest-timestamp normalization.
KNOWN_NATIVE_LIMIT tests retain exact fractional createdAt equality: a corrected
upstream must produce unexpected successes, requiring review/repinning, not a
quietly broadened metadata allowance. Their setUp checks all other invariants;
only the final timestamp equality is inside @expectedFailure.
"""
import copy
import os
import unittest

import test_native_dual as dual

host = dual.host
NATIVE_REQUIRED = 'Explicit locked native artifacts required; skips are not native proof'


class RecoveryFixture:
    @classmethod
    def setUpClass(cls):
        dual.NativeDual.setUpClass()
        cls.addClassCleanup(dual.NativeDual.doClassCleanups)

    def setUp(self):
        self.fx = dual.NativeDual('test_enable_preserves_full_native_policy_and_exports')
        self.fx._outcome = self._outcome
        self.addCleanup(self.fx.doCleanups)
        self.fx.setUp()

    def unchanged(self, before, after, except_fields=()):
        changed = sorted(k for k in before.keys() | after.keys()
                         if k not in except_fields and
                         (k not in before or k not in after or before[k] != after[k]))
        self.assertFalse(changed, 'Changed native record fields (values withheld): ' + ','.join(changed))

    def upgrade(self):
        request = self.fx.request()
        self.fx.prepare(request)
        self.fx.finish(request)
        self.fx.topology()
        # Select genuine fractional global metadata, never patch account SQL.
        # A rare exact-second creation gets another supported native device add.
        for attempt in range(4):
            if any(r['createdAt'] % 1000 for r in self.fx.records().values()):
                return
            self.fx.device('add', 'fractional' + str(attempt))
        self.fail('Native fixture did not produce fractional global createdAt')

    def earliest(self, records):
        expected = {name: row['createdAt'] for name, row in records.items()}
        for inbound in self.fx.api().call('/panel/api/inbounds/list'):
            for client in dual.decoded(inbound['settings'])['clients']:
                value = client.get('created_at', 0)
                if client['email'] in expected and value > 0:
                    expected[client['email']] = min(expected[client['email']], value)
        return expected

    def record_contract(self, before, after, earliest, except_fields=(), check_normalization=True):
        self.unchanged(before, after, (*except_fields, 'createdAt'))
        if check_normalization:
            # This is a prediction from BEFORE the mutation, not a tolerance for
            # arbitrary timestamp loss. Both native representations are millis.
            self.assertEqual(after['createdAt'], earliest,
                             'Creation time differs from known native earliest stored metadata')
            self.assertEqual(before['createdAt'] // 1000, earliest // 1000)
            self.assertGreaterEqual(before['createdAt'] - earliest, 0)
            self.assertLess(before['createdAt'] - earliest, 1000)

    def cold(self, modes=(False, True), check_normalization=True):
        self.fx.seed_policy()
        self.fx.seed_policy('home', enable=False, limitHwid=3)
        host.backend_health(self.fx.folder, self.fx.manifest)
        self.upgrade()
        before, joins = self.fx.records(), self.fx.rows('client_inbounds')
        earliest = self.earliest(before)
        old_profile = self.fx.assert_native_exports()
        original_reality = copy.deepcopy(self.fx.manifest['reality'])
        archive, _, _ = self.fx.archive()
        original_digest = host.digest(archive)
        self.fx.fx.stop()
        pairs = []
        for rotate in modes:
            stage = self.fx.fx.root / ('cold-' + str(rotate))
            stage.mkdir()
            with archive.open('rb') as stream:
                host.safe_unpack(stream, stage)
            recovered = host.verify_backup(stage, self.fx.fx.lock)
            self.fx.fx.work, self.fx.fx.manifest = stage / 'deployment', recovered
            endpoint = '93.184.216.36' if rotate else '93.184.216.35'
            self.fx.manifest['endpoint'] = endpoint
            host.install_runtime(self.fx.folder, self.fx.fx.lock)
            if rotate:
                self.fx.manifest['reality'] = host.native_keys(self.fx.folder)
            self.fx.fx.start()
            try:
                self.assertEqual(self.fx.records(), before, 'Closed archive DB changed native records')
                host.configure_native(self.fx.folder, self.fx.manifest, False, rotate=rotate)
                configured = self.fx.records()
                host.enable_hysteria(self.fx.folder, self.fx.manifest, 22)
                host.backend_health(self.fx.folder, self.fx.manifest)
                after = self.fx.records()
                self.fx.topology()
                self.assertEqual(self.fx.rows('client_inbounds'), joins, 'Cold mutation changed join metadata')
                self.assertTrue((self.fx.manifest['reality'] == original_reality) is not rotate)
                for name, record in before.items():
                    for field in ('uuid', 'auth'):
                        self.assertTrue((after[name][field] == record[field]) is not rotate,
                                        'Unexpected cold credential preservation/rotation')
                    allowed = ('updatedAt', 'uuid', 'auth') if rotate else ('updatedAt',)
                    self.record_contract(record, after[name], earliest[name], allowed, check_normalization)
                    if check_normalization:
                        self.assertEqual(configured[name]['createdAt'], earliest[name])
                    pairs.append((record['createdAt'], after[name]['createdAt']))
                profile = self.fx.assert_native_exports()
                self.assertTrue(all(p['server'] == endpoint for p in profile['proxies']),
                                'Native feed retained old endpoint')
                h2 = next(i for i in self.fx.api().call('/panel/api/inbounds/list') if i['protocol'] == 'hysteria')
                certificates = dual.decoded(h2['streamSettings'])['tlsSettings']['certificates']
                cert, key = self.fx.certificate_paths(self.fx.folder, self.fx.manifest)
                self.assertEqual(certificates, [{'certificateFile': str(cert), 'keyFile': str(key),
                                                'oneTimeLoading': False}], 'Cold cert paths not rewritten')
                host.backend_health(self.fx.folder, self.fx.manifest)
                self.assertEqual(after, self.fx.records(), 'Health rerotated account')
                # Both narrow origin routing AND freedom.finalRules exceptions
                # come from the existing fixture, AFTER all mutations/health.
                origin = self.fx.origin()
                old_works = self.fx.https(self.fx.client(old_profile), origin)
                self.assertEqual(old_works, not rotate, 'Cold original H2 Auth restore/revocation failed')
                self.assertTrue(self.fx.https(self.fx.client(profile), origin),
                                'Cold native H2 failed strict fixture HTTPS')
                self.assertEqual(host.digest(archive), original_digest, 'Original closed archive changed')
            finally:
                self.fx.fx.stop()
        return pairs

    def device_case(self, action, check_normalization=True):
        self.upgrade()
        before = self.fx.records()
        earliest = self.earliest(before)
        target = next(name for name, row in before.items() if row['createdAt'] % 1000)
        names = (target, 'tablet') if action == 'rename' else (target,)
        self.fx.device(action, *names)
        after = self.fx.records()
        renamed = 'tablet' if action == 'rename' else target
        allowed = ('email', 'updatedAt') if action == 'rename' else ('uuid', 'auth', 'subId', 'updatedAt')
        for field in ('uuid', 'auth', 'subId'):
            self.assertEqual(after[renamed][field] == before[target][field], action == 'rename')
        self.record_contract(before[target], after[renamed], earliest[target], allowed, check_normalization)
        for name in before.keys() - {target}:
            self.unchanged(before[name], after[name])
        self.fx.topology()
        return [(before[target]['createdAt'], after[renamed]['createdAt'])]


@unittest.skipUnless(os.environ.get('QD_VPS_ARTIFACTS'), NATIVE_REQUIRED)
class NativeDualRecovery(RecoveryFixture, unittest.TestCase):
    def test_dual_upgrade_add_both_feeds_and_real_h2(self):
        self.fx.seed_policy()
        self.upgrade()
        before, joins = self.fx.records(), self.fx.rows('client_inbounds')
        self.fx.device('add', 'tablet')
        self.fx.topology()
        for name, record in before.items():
            self.unchanged(record, self.fx.records()[name])
        self.assertTrue(all(row in self.fx.rows('client_inbounds') for row in joins))
        profile = self.fx.assert_native_exports('tablet')
        origin = self.fx.origin()
        self.assertTrue(self.fx.https(self.fx.client(profile), origin), 'New dual account failed strict H2 HTTPS')

    def test_cold_restore_replace_auth_policy_and_known_earliest_timestamp(self):
        self.cold()

    def test_reset_auth_policy_and_known_earliest_timestamp(self):
        self.device_case('reset')

    def test_rename_auth_policy_and_known_earliest_timestamp(self):
        self.device_case('rename')


@unittest.skipUnless(os.environ.get('QD_VPS_ARTIFACTS'), NATIVE_REQUIRED)
class KnownNativeCreatedAtLimit(RecoveryFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        # Setup failures/errors are NOT expected failures. Auth, policies, joins,
        # endpoints, certs and real cold H2 must work before testing precision.
        operation = self._testMethodName.rsplit('_', 1)[-1]
        self.pairs = (self.cold((operation == 'replace',), check_normalization=False)
                      if operation in ('restore', 'replace') else
                      self.device_case(operation, check_normalization=False))

    def exact_created_at(self):
        self.assertEqual([before for before, _ in self.pairs], [after for _, after in self.pairs],
                         'KNOWN_NATIVE_LIMIT: pinned API loses fractional global createdAt')

    @unittest.expectedFailure
    def test_KNOWN_NATIVE_LIMIT_fractional_createdAt_cold_restore(self):
        self.exact_created_at()

    @unittest.expectedFailure
    def test_KNOWN_NATIVE_LIMIT_fractional_createdAt_cold_replace(self):
        self.exact_created_at()

    @unittest.expectedFailure
    def test_KNOWN_NATIVE_LIMIT_fractional_createdAt_reset(self):
        self.exact_created_at()

    @unittest.expectedFailure
    def test_KNOWN_NATIVE_LIMIT_fractional_createdAt_rename(self):
        self.exact_created_at()


if __name__ == '__main__':
    unittest.main()
