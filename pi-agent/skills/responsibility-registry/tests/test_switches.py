"""User-authorized backed-up clear/restore. Never import production state."""
import copy
from contextlib import redirect_stderr, redirect_stdout
import io
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
                                ('2099-01-01', 'minute or second precision'), (records[0]['time'][:10], 'explicit timezone')]:
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

    def test_known_unpublished_failure_finishes_aborted_without_blocking_writes(self):
        self.register()
        raw, inode = self.document.read_bytes(), self.document.stat().st_ino
        with patch.object(self.store, 'switch_checkpoint', side_effect=OSError('prepared interruption')):
            with self.assertRaisesRegex(registry.RegistryError, 'aborted recorded'):
                self.switch()
        self.assertEqual((self.document.read_bytes(), self.document.stat().st_ino), (raw, inode))
        self.assertFalse(self.store.switch_journal.exists())
        record = self.store.read_backup_index()[-1]
        self.assertEqual(record['status'], 'aborted')
        self.assertEqual(self.store.snapshot_bytes(record)[0], raw)
        self.assertEqual(self.store.backup_candidates(), [])
        self.register(appointment('beta', 'next', path='/example/beta'))
        self.switch()

    def test_prepared_race_and_later_manual_edit_recover_without_rewriting_live(self):
        self.register()
        original = self.document.read_bytes()
        real_index = self.store.write_backup_index
        def race(phase):
            if phase == 'prepared':
                state, era = codec.parse_document(self.document.read_text())
                state['owners']['alpha']['role'] = 'manual role during prepared switch'
                self.document.write_text(codec.serialize_document(state, era))
        def fail_abort(records):
            if records[-1]['status'] == 'aborted':
                raise OSError('abort index temporarily unavailable')
            return real_index(records)
        with patch.object(self.store, 'switch_checkpoint', race), patch.object(self.store, 'write_backup_index', fail_abort):
            with self.assertRaisesRegex(registry.RegistryError, 'switch not performed; recovery required'):
                self.switch()
        self.assertTrue(self.store.switch_journal.exists())
        state, era = codec.parse_document(self.document.read_text())
        state['owners']['alpha']['name'] = 'later manual edit is authoritative'
        self.document.write_text(codec.serialize_document(state, era))
        raw, inode = self.document.read_bytes(), self.document.stat().st_ino
        self.assertEqual(self.recover()['recovered'], 'superseded-manual')
        self.assertEqual((self.document.read_bytes(), self.document.stat().st_ino), (raw, inode))
        record = self.store.read_backup_index()[-1]
        self.assertEqual(record['reconciliation'], dict(observed_sha256=registry.hashlib.sha256(raw).hexdigest(), journal_phase='aborted'))
        self.assertEqual(self.store.snapshot_bytes(record)[0], original)
        self.assertFalse(self.store.switch_journal.exists())
        self.assertEqual(self.store.backup_candidates(), [])
        with self.assertRaisesRegex(registry.RegistryError, 'previous is ambiguous'):
            self.store.resolve_backup('previous')
        self.register(appointment('beta', 'next', path='/example/beta'))
        self.switch()

    def test_incomplete_switch_then_literal_template_reset_can_recover_and_register(self):
        self.register()
        original = self.document.read_bytes()
        with patch.object(self.store, 'switch_checkpoint', side_effect=lambda phase: (_ for _ in ()).throw(OSError('after publication')) if phase == 'switched' else None):
            with self.assertRaisesRegex(registry.RegistryError, 'switched; index incomplete'):
                self.switch()
        self.document.unlink()
        shutil.copyfile(SKILL / 'RESPONSIBILITIES.template.md', self.document)
        raw, inode = self.document.read_bytes(), self.document.stat().st_ino
        self.assertEqual(self.recover()['recovered'], 'superseded-manual')
        self.assertEqual((self.document.read_bytes(), self.document.stat().st_ino), (raw, inode))
        record = self.store.read_backup_index()[-1]
        self.assertEqual(record['reconciliation']['journal_phase'], 'switched-index-incomplete')
        self.assertEqual(record['reconciliation']['observed_sha256'], registry.hashlib.sha256(raw).hexdigest())
        self.assertEqual(self.store.snapshot_bytes(record)[0], original)
        self.assertEqual(self.store.backup_candidates(), [])
        self.assertFalse(self.store.switch_journal.exists())
        self.register(appointment('beta', 'new', path='/example/beta'))
        self.assertEqual(list(self.domain()['owners']), ['beta'])
        self.switch()
        self.assertEqual(len(self.store.backup_candidates()), 1)
        self.assertNotEqual(self.store.resolve_backup('previous')['id'], record['id'])

    def test_newer_superseded_previous_requires_choice_and_explicit_id_restores_latest(self):
        self.register()
        self.switch()  # R1 is completed, snapshot alpha.
        self.register(appointment('beta', 'new', path='/example/beta'))
        b = self.domain()
        with patch.object(self.store, 'switch_checkpoint', side_effect=lambda phase: (_ for _ in ()).throw(OSError('after publication')) if phase == 'switched' else None):
            with self.assertRaisesRegex(registry.RegistryError, 'switched; index incomplete'):
                self.switch()
        self.document.unlink()
        shutil.copyfile(SKILL / 'RESPONSIBILITIES.template.md', self.document)
        self.assertEqual(self.recover()['recovered'], 'superseded-manual')
        first, second = self.store.read_backup_index()
        raw, inode = self.document.read_bytes(), self.document.stat().st_ino
        index = self.store.backup_index.read_bytes()
        with self.assertRaisesRegex(registry.RegistryError, 'previous is ambiguous') as failure:
            self.switch('restore', 'previous')
        for record in [first, second]:
            self.assertIn(record['id'], str(failure.exception))
        self.assertIn('"utc"', str(failure.exception))
        self.assertIn('"local"', str(failure.exception))
        self.assertEqual((self.document.read_bytes(), self.document.stat().st_ino), (raw, inode))
        self.assertEqual(self.store.backup_index.read_bytes(), index)
        self.assertEqual(len(list((self.document.parent / 'backups').iterdir())), 2)
        self.assertEqual(self.store.resolve_backup(second['snapshot'])['id'], second['id'])
        self.switch('restore', second['id'])
        self.assertEqual(self.domain(), b)
        self.assertEqual(list(self.domain()['owners']), ['beta'])

    def test_never_published_superseded_snapshot_is_explicitly_restorable(self):
        self.register()
        original = self.domain()
        real_index = self.store.write_backup_index
        def race(phase):
            if phase == 'prepared':
                state, era = codec.parse_document(self.document.read_text())
                state['owners']['alpha']['role'] = 'valid manual role'
                self.document.write_text(codec.serialize_document(state, era))
        def fail_abort(records):
            if records[-1]['status'] == 'aborted':
                raise OSError('abort index unavailable')
            return real_index(records)
        with patch.object(self.store, 'switch_checkpoint', race), patch.object(self.store, 'write_backup_index', fail_abort):
            with self.assertRaises(registry.RegistryError):
                self.switch()
        self.assertEqual(self.recover()['recovered'], 'superseded-manual')
        record = self.store.read_backup_index()[0]
        with self.assertRaisesRegex(registry.RegistryError, 'previous is ambiguous'):
            self.switch('restore', 'previous')
        self.assertEqual(self.store.resolve_backup(Path(record['snapshot']).name)['id'], record['id'])
        self.switch('restore', record['id'])
        self.assertEqual(self.domain(), original)

    def test_rename_succeeded_before_fsync_error_is_never_recorded_aborted(self):
        self.register()
        real_write = registry.atomic_write
        def fail_after_rename(path, content, **kwargs):
            real_write(path, content, **kwargs)
            if path == self.document:
                raise OSError('directory fsync failed after successful rename')
        with patch.object(registry, 'atomic_write', fail_after_rename):
            with self.assertRaisesRegex(registry.RegistryError, 'switched; index incomplete'):
                self.switch()
        self.assertFalse(self.domain()['owners'])
        journal = json.loads(self.store.switch_journal.read_text())
        self.assertEqual(journal['phase'], 'switched-index-incomplete')
        self.assertEqual(journal['record']['status'], 'switched-index-incomplete')
        raw, inode = self.document.read_bytes(), self.document.stat().st_ino
        self.assertEqual(self.recover()['recovered'], 'completed')
        self.assertEqual((self.document.read_bytes(), self.document.stat().st_ino), (raw, inode))

    def test_published_then_manual_restore_of_before_bytes_is_superseded_not_aborted(self):
        self.register()
        original = self.document.read_bytes()
        with patch.object(self.store, 'switch_checkpoint', side_effect=lambda phase: (_ for _ in ()).throw(OSError('after publication')) if phase == 'switched' else None):
            with self.assertRaisesRegex(registry.RegistryError, 'switched; index incomplete'):
                self.switch()
        self.document.write_bytes(original)
        inode = self.document.stat().st_ino
        self.assertEqual(self.recover()['recovered'], 'superseded-manual')
        self.assertEqual((self.document.read_bytes(), self.document.stat().st_ino), (original, inode))
        self.assertEqual(self.store.backup_candidates(), [])
        self.assertFalse(self.store.switch_journal.exists())

    def test_abort_cleanup_sync_failure_keeps_recoverable_evidence(self):
        self.register()
        original = self.document.read_bytes()
        real_sync = registry.fsync_directory
        def fail_cleanup_only(path):
            if path == self.root:
                raise OSError('cleanup directory sync failed')
            return real_sync(path)
        with patch.object(self.store, 'switch_checkpoint', side_effect=OSError('before publication')), patch.object(registry, 'fsync_directory', fail_cleanup_only):
            with self.assertRaisesRegex(registry.RegistryError, 'recovery required'):
                self.switch()
        self.assertEqual(self.document.read_bytes(), original)
        self.assertTrue(self.store.switch_journal.exists())
        self.assertEqual(self.store.read_backup_index()[-1]['status'], 'aborted')
        self.assertEqual(self.recover()['recovered'], 'aborted')
        self.assertFalse(self.store.switch_journal.exists())

    def test_invalid_or_missing_live_is_diagnostic_until_valid_manual_repair(self):
        self.register()
        with patch.object(self.store, 'switch_checkpoint', side_effect=lambda phase: (_ for _ in ()).throw(OSError('after publication')) if phase == 'switched' else None):
            with self.assertRaises(registry.RegistryError):
                self.switch()
        journal = self.store.switch_journal.read_bytes()
        index = self.store.backup_index.read_bytes()
        self.document.write_bytes(b'invalid human edit; do not replace automatically')
        raw, inode = self.document.read_bytes(), self.document.stat().st_ino
        with self.assertRaisesRegex(registry.RegistryError, 'invalid'):
            self.recover()
        self.assertEqual((self.document.read_bytes(), self.document.stat().st_ino), (raw, inode))
        self.document.unlink()
        with self.assertRaisesRegex(registry.RegistryError, 'missing'):
            self.recover()
        self.assertFalse(self.document.exists())
        self.assertEqual(self.store.switch_journal.read_bytes(), journal)
        self.assertEqual(self.store.backup_index.read_bytes(), index)
        shutil.copyfile(SKILL / 'RESPONSIBILITIES.template.md', self.document)
        self.assertEqual(self.recover()['recovered'], 'superseded-manual')

    def test_aware_timestamp_minute_second_intervals_and_naive_rejection(self):
        self.register()
        with patch.object(registry, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 10, 24, 8, 13, 5, tzinfo=timezone.utc)
            self.switch()
        self.register(appointment('beta', 'new', path='/example/beta'))
        with patch.object(registry, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 10, 24, 16, 13, 9, tzinfo=timezone.utc)
            self.switch()
        records = self.store.backup_candidates()
        before = self.document.read_bytes(), self.document.stat().st_ino
        index = self.store.backup_index.read_bytes()
        paths = sorted((self.document.parent / 'backups').iterdir())
        for target in ['2026-10-24T16:13', '2026-10-24 16:13', '2026-10-24T16:13:05']:
            with self.subTest(target=target):
                with self.assertRaisesRegex(registry.RegistryError, 'explicit timezone') as failure:
                    self.switch('restore', target)
                self.assertIn('2026-10-24T08:13:05+00:00', str(failure.exception))
                self.assertIn('2026-10-24T16:13:09+00:00', str(failure.exception))
                self.assertIn('"utc"', str(failure.exception))
                self.assertIn('"local"', str(failure.exception))
                self.assertEqual((self.document.read_bytes(), self.document.stat().st_ino), before)
                self.assertEqual(self.store.backup_index.read_bytes(), index)
                self.assertEqual(sorted((self.document.parent / 'backups').iterdir()), paths)
        for target in ['2026-10-24T16:13+08:00', '2026-10-24T16:13+0800', '2026-10-24T16:13+08', '2026-10-24 16:13+08:00', '2026-10-24T16:13:05+08:00', '2026-10-24T08:13:05Z', '2026-10-24T00:13-08:00']:
            self.assertEqual(self.store.resolve_backup(target)['id'], records[0]['id'])
        for target in ['2026-10-24T16:13Z', '2026-10-24T16:13:09+00:00']:
            self.assertEqual(self.store.resolve_backup(target)['id'], records[1]['id'])
        for target in ['2026-10-24T08:14Z', '2026-10-24T08:13:06Z']:
            with self.assertRaisesRegex(registry.RegistryError, 'no completed'):
                self.store.resolve_backup(target)
        for target in ['2026-10-24', '2026-10-24T16+08:00', '2026-10-24T16:13:05.1Z']:
            with self.assertRaisesRegex(registry.RegistryError, 'minute or second precision'):
                self.switch('restore', target)
        self.switch('restore', '2026-10-24T16:13+08:00')
        self.assertEqual(list(self.domain()['owners']), ['alpha'])
        visible = self.store.backup_index.read_text().split('## Switch records (audit JSON)')[0]
        self.assertIn('- Restored from: ' + records[0]['id'], visible)
        self.assertIn('new era: ' + self.project()['era'], visible)

    def test_aware_timestamp_with_multiple_matches_lists_candidates_before_writes(self):
        self.register()
        with patch.object(registry, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 10, 24, 8, 13, 5, tzinfo=timezone.utc)
            self.switch()
            self.switch()
        before = self.document.read_bytes(), self.document.stat().st_ino
        index = self.store.backup_index.read_bytes()
        for target in ['2026-10-24T16:13+08:00', '2026-10-24T08:13:05Z']:
            with self.assertRaisesRegex(registry.RegistryError, 'ambiguous restore target') as failure:
                self.switch('restore', target)
            self.assertIn('"utc"', str(failure.exception))
            self.assertIn('"local"', str(failure.exception))
        self.assertEqual((self.document.read_bytes(), self.document.stat().st_ino), before)
        self.assertEqual(self.store.backup_index.read_bytes(), index)
        self.assertEqual(len(list((self.document.parent / 'backups').iterdir())), 2)

    def test_rebuild_output_preserves_resolved_backup_index_and_immutable_files(self):
        real = self.base / 'real-skill'
        real.mkdir()
        alias = self.base / 'aliased-skill'
        alias.symlink_to(real, target_is_directory=True)
        root = self.base / 'aliased-private'
        document = alias / 'RESPONSIBILITIES.md'
        store = registry.Store(root, document)
        store.initialize()
        (real / 'BACKUPS.md').write_text('private historical index')
        (real / 'backups').mkdir(mode=0o700)
        (real / 'backups/snapshot.md').write_text('immutable synthetic snapshot')
        (root / 'legacy-fixture.json').write_text('immutable synthetic legacy JSON')
        (root / 'legacy-fixture.dashboard.md').write_text('immutable synthetic legacy dashboard')
        protected = [real / 'BACKUPS.md', real / 'backups/snapshot.md', root / 'legacy-fixture.json', root / 'legacy-fixture.dashboard.md']
        for path in protected:
            raw, inode = path.read_bytes(), path.stat().st_ino
            output = alias / path.relative_to(real) if real in path.parents else path
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as errors:
                code = registry.main(['--root', str(root), '--document', str(document), 'rebuild', '--output', str(output)])
            self.assertEqual(code, 2)
            self.assertIn('cannot replace', errors.getvalue())
            self.assertEqual((path.read_bytes(), path.stat().st_ino), (raw, inode))
        output = self.base / 'allowed-report.md'
        with redirect_stdout(io.StringIO()):
            self.assertEqual(registry.main(['--root', str(root), '--document', str(document), 'rebuild', '--output', str(output)]), 0)
        self.assertIn('非权威', output.read_text())

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
