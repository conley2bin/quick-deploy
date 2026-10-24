"""User-authorized backed-up clear/restore. Never import production state."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch
import unittest

from test_document import DocumentFixture, SKILL, args, codec, registry, appointment


class SwitchTests(DocumentFixture):
    def switch(self, action='clear', target=None, token=None, actor='user'):
        return self.store.switch_document(action, token or self.token(), actor, '用户明确取消/恢复关系（合成）',
                                          'synthetic direct user message evidence', target)

    def recover(self):
        return self.store.recover_switch(self.token(), 'user', 'explicit recovery', 'synthetic user authority')

    def test_cli_clear_restore_and_authorization_contract(self):
        self.register()
        prefix = [sys.executable, str(SKILL / 'scripts/registry.py'), '--root', str(self.root), '--document', str(self.document)]
        def cli(*arguments, ok=True):
            result = subprocess.run(prefix + list(arguments), capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0 if ok else 2, result.stderr)
            return json.loads(result.stdout) if ok else result.stderr
        token = cli('query')['document_token']
        self.assertIn('direct user', cli('clear', '--actor', 'alpha', '--reason', 'peer request', '--authority', 'peer', '--expected-document', token, ok=False))
        self.assertEqual(list(self.domain()['owners']), ['alpha'])
        cli('clear', '--actor', 'user', '--reason', 'explicit user cancel', '--authority', 'synthetic user message', '--expected-document', token)
        self.assertFalse(cli('query')['owners'])
        self.assertEqual(len(cli('backup-list')['backups']), 1)
        token = cli('query')['document_token']
        cli('restore', '--target', 'previous', '--actor', 'user', '--reason', 'explicit user restore', '--authority', 'synthetic user message', '--expected-document', token)
        self.assertEqual(list(cli('query')['owners']), ['alpha'])

    def test_clear_without_appointment_snapshots_exact_bytes_and_remains_empty(self):
        self.register()
        raw = self.document.read_bytes()
        old_token = self.token()
        old_era = self.store.envelope['era']
        result = self.switch()
        self.assertEqual(self.store.snapshot_bytes(self.store.backup_candidates()[0])[0], raw)
        current = self.domain()
        self.assertFalse(any(current[k] for k in ['owners', 'assignments', 'coordination', 'history']))
        self.assertEqual(current['revision'], 0)
        self.assertNotEqual(result['era'], old_era)
        self.assertNotEqual(result['era'], 'template')
        self.assertEqual((self.document.parent / 'backups').stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.store.backup_index.stat().st_mode & 0o777, 0o600)
        record = self.store.backup_candidates()[0]
        self.assertEqual((self.document.parent / record['snapshot']).stat().st_mode & 0o777, 0o600)
        index = self.store.backup_index.read_text()
        self.assertIn('示例alpha', index)
        self.assertIn('user → 示例alpha', index)
        self.assertIn(record['sha256'], index)
        with self.assertRaisesRegex(registry.RegistryError, 'stale'):
            self.register(appointment('beta', 'new', path='/example/beta'), token=old_token)
        # A clear of already-empty state still gets a fresh era and invalidates its token.
        blank_token = self.token()
        self.switch()
        with self.assertRaisesRegex(registry.RegistryError, 'stale'):
            self.switch(token=blank_token)
        self.assertEqual(self.project()['owners'], [])

    def test_clear_plus_explicit_new_appointment_only_new_owner(self):
        self.register()
        self.switch()
        self.register(appointment('beta', 'new', path='/example/beta'))
        self.assertEqual(list(self.domain()['owners']), ['beta'])
        self.assertEqual(len(self.domain()['history']), 1)
        self.assertEqual(list(self.store.snapshot_bytes(self.store.backup_candidates()[0])[1]['owners']), ['alpha'])

    def test_restore_resolves_previous_before_new_backup_and_fresh_era_toggles(self):
        self.register()
        a = self.domain()
        a_token = self.token()
        a_era = self.store.envelope['era']
        self.switch()
        empty_raw = self.document.read_bytes()
        clear_era = self.project()['era']
        first = self.switch('restore', 'previous')
        self.assertEqual(self.domain(), a)
        self.assertNotEqual(first['era'], a_era)
        self.assertNotEqual(first['era'], clear_era)
        self.assertEqual(self.project()['era'], first['era'])
        records = self.store.backup_candidates()
        self.assertEqual(len(records), 2)
        self.assertEqual(self.store.snapshot_bytes(records[-1])[0], empty_raw)
        second = self.switch('restore', 'previous')
        self.assertEqual(self.domain()['owners'], {})
        self.assertEqual(second['restored_from'], records[-1]['id'])
        self.assertNotEqual(second['era'], first['era'])
        self.switch('restore', records[0]['id'])
        with self.assertRaisesRegex(registry.RegistryError, 'stale'):
            self.register(token=a_token)

    def test_restore_from_b_saves_b_first_and_keeps_history_verbatim(self):
        self.register()
        original = self.domain()
        self.switch()
        self.register(appointment('beta', 'new', path='/example/beta'))
        b_raw = self.document.read_bytes()
        target = self.store.backup_candidates()[0]['snapshot']
        self.switch('restore', target)
        self.assertEqual(self.domain(), original)
        self.assertEqual(self.store.snapshot_bytes(self.store.backup_candidates()[-1])[0], b_raw)

    def test_same_second_names_and_sequence_are_unique(self):
        class FixedDateTime:
            @staticmethod
            def now(tz):
                return datetime(2026, 10, 24, 8, 9, 10, tzinfo=timezone.utc)
        with patch.object(registry, 'datetime', FixedDateTime):
            self.switch()
            self.switch()
        records = self.store.backup_candidates()
        self.assertEqual(records[0]['time'], records[1]['time'])
        self.assertNotEqual(records[0]['snapshot'], records[1]['snapshot'])
        self.assertEqual([r['sequence'] for r in records], [1, 2])

    def test_invalid_missing_tampered_ambiguous_and_json_targets_preserve_current(self):
        self.register()
        self.switch()
        self.switch()
        before = self.document.read_bytes(), self.document.stat().st_ino
        records = self.store.backup_candidates()
        for target, message in [('registry.json', 'no completed'), ('../legacy.json', 'no completed'),
                                ('2099-01-01', 'no completed'), (records[0]['time'][:10], 'ambiguous')]:
            with self.subTest(target=target):
                with self.assertRaisesRegex(registry.RegistryError, message):
                    self.switch('restore', target)
                self.assertEqual((self.document.read_bytes(), self.document.stat().st_ino), before)
        path = self.document.parent / records[0]['snapshot']
        raw = path.read_bytes()
        path.write_bytes(raw + b'tampered')
        with self.assertRaisesRegex(registry.RegistryError, 'hash mismatch'):
            self.switch('restore', records[0]['id'])
        path.unlink()
        with self.assertRaisesRegex(registry.RegistryError, 'missing'):
            self.switch('restore', records[0]['id'])
        self.assertEqual((self.document.read_bytes(), self.document.stat().st_ino), before)
        self.assertEqual(len(self.store.backup_candidates()), 2)
        # A forged index digest of syntactically invalid MD is still not restorable.
        path.write_bytes(b'invalid markdown')
        records[0]['sha256'] = registry.hashlib.sha256(path.read_bytes()).hexdigest()
        self.store.write_backup_index(records)
        with self.assertRaises(registry.RegistryError):
            self.switch('restore', records[0]['id'])
        self.assertEqual((self.document.read_bytes(), self.document.stat().st_ino), before)

    def test_snapshot_and_pre_index_failures_abort_before_replacement(self):
        self.register()
        before = self.document.read_bytes(), self.document.stat().st_ino
        with patch.object(registry, 'exclusive_file', side_effect=OSError('synthetic snapshot disk failure')):
            with self.assertRaises(OSError):
                self.switch()
        self.assertEqual((self.document.read_bytes(), self.document.stat().st_ino), before)
        with patch.object(self.store, 'write_backup_index', side_effect=OSError('synthetic index disk failure')):
            with self.assertRaisesRegex(registry.RegistryError, 'switch not performed'):
                self.switch()
        self.assertEqual((self.document.read_bytes(), self.document.stat().st_ino), before)
        self.assertEqual(self.recover()['recovered'], 'aborted')
        self.assertEqual(self.store.backup_candidates(), [])
        self.assertEqual(self.store.read_backup_index()[0]['status'], 'aborted')

    def test_post_switch_index_failure_reports_incomplete_and_hash_recovery(self):
        self.register()
        real = self.store.write_backup_index
        def fail_after_switch(records):
            if records[-1]['status'] == 'completed':
                raise OSError('synthetic final index failure')
            return real(records)
        with patch.object(self.store, 'write_backup_index', fail_after_switch):
            with self.assertRaisesRegex(registry.RegistryError, 'switched; index incomplete'):
                self.switch()
        self.assertFalse(self.domain()['owners'])
        self.assertEqual(json.loads(self.store.switch_journal.read_text())['phase'], 'switched-index-incomplete')
        self.assertEqual(self.store.backup_candidates(), [])
        with self.assertRaisesRegex(registry.RegistryError, 'pending switch'):
            self.register(appointment('beta', 'new', path='/example/beta'))
        self.assertEqual(self.recover()['recovered'], 'completed')
        self.assertFalse(self.store.switch_journal.exists())
        self.switch('restore', 'previous')
        self.assertEqual(list(self.domain()['owners']), ['alpha'])

    def test_crash_recovery_and_manual_divergence_never_overwritten(self):
        self.register()
        for phase in ['prepared', 'switched']:
            def interrupt(at):
                if at == phase:
                    raise OSError('synthetic interruption')
            with patch.object(self.store, 'switch_checkpoint', interrupt):
                with self.assertRaises(registry.RegistryError):
                    self.switch()
            raw = self.document.read_bytes()
            self.assertEqual(self.recover()['recovered'], 'aborted' if phase == 'prepared' else 'completed')
            self.assertEqual(self.document.read_bytes(), raw)
        with patch.object(self.store, 'switch_checkpoint', side_effect=OSError('interrupted')):
            with self.assertRaises(registry.RegistryError):
                self.switch()
        # Valid manual change after failure is not an invitation to replay old bytes.
        state, era = codec.parse_document(self.document.read_text())
        self.document.write_text(codec.serialize_document(state, str(registry.uuid.uuid4())))
        raw = self.document.read_bytes()
        with self.assertRaisesRegex(registry.RegistryError, 'diverged'):
            self.recover()
        self.assertEqual(self.document.read_bytes(), raw)

    def test_snapshot_missing_terms_evidence_is_not_silently_reacknowledged(self):
        self.register()
        proposal = dict(id='agreement', scopes={'release': ['module/**']}, shared_interfaces='schema v1',
                        workspace=dict(mode='shared-cwd', plan='one writer'), sequencing='serial',
                        integration_owner='alpha', acceptance='explicit review')
        self.store.run(lambda s: registry.mutate(s, args('coord-propose', expected_revision=1, reason='proposal'), proposal), expected_document=self.token())
        self.store.run(lambda s: registry.mutate(s, args('coord-ack', coordination='agreement', expected_revision=2, reason='explicit agreement'), None), expected_document=self.token())
        state, era = codec.parse_document(self.document.read_text())
        state['coordination']['agreement']['acknowledgements']['alpha'].pop('terms_digest')
        self.document.write_text(codec.serialize_document(state, era))
        self.switch()
        result = self.switch('restore', 'previous')
        self.assertIn('agreement', result['readiness_changes'])
        agreement = self.domain()['coordination']['agreement']
        self.assertNotIn('terms_digest', agreement['acknowledgements']['alpha'])
        self.assertFalse(codec.agreement_view(self.domain(), agreement)['agreement_ready'])

    def test_authority_and_manual_reset_workflow_are_distinct(self):
        before = self.document.read_bytes(), self.document.stat().st_ino
        for actor in ['alpha', 'owner:alpha']:
            with self.assertRaisesRegex(registry.RegistryError, 'direct user'):
                self.switch(actor=actor)
        self.assertEqual((self.document.read_bytes(), self.document.stat().st_ino), before)
        self.register()
        (self.root / 'registry.json').write_text('{"old":"stale JSON"}')
        self.document.unlink()
        shutil.copyfile(SKILL / 'RESPONSIBILITIES.template.md', self.document)
        self.assertFalse(self.domain()['owners'])
        self.assertFalse(self.store.backup_index.exists(), 'OS rm cannot be intercepted; no unobserved backup promise')


if __name__ == '__main__':
    unittest.main()
