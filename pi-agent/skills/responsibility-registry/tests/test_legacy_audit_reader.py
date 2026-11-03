"""Source-anchored legacy evidence and explicit read-only historical consumers."""
import copy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

from test_registry import registry, appointment
import registry_document as codec
from audit_storage import encoded, sha


class LegacyAuditReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.document = self.root / 'current.md'
        self.store = registry.Store(self.root, self.document)
        # Establish a full v2 source with a unique, genuinely historical context.
        self.legacy = registry.empty()
        registry.mutate(self.legacy, SimpleNamespace(command='register', actor='alpha'), appointment())
        self.context = 'SYNTHETIC_LEGACY_OLD_CONTEXT_8871'
        self.legacy['history'][0]['reason'] = self.context
        self.era = str(uuid.uuid4())
        self.raw = codec.serialize_document(self.legacy, self.era).encode('utf-8')
        self.document.write_bytes(self.raw)
        (self.root / 'registry.lock').touch(mode=0o600)
        code, output, error = self.cli('migrate-storage', '--expected-document', self.store.current_snapshot()['document_token'],
            '--actor', 'user', '--reason', 'synthetic explicit migration', '--authority', 'synthetic user authority',
            '--operation-id', 'synthetic-migration')
        self.assertEqual(code, 0, error)
        self.migration = json.loads(output)
        self.op = self.migration['transaction_id']
        self.archive = self.store.audit.legacy / (self.op + '.md')
        self.manifest_path = self.store.audit.legacy / (self.op + '.manifest.json')

    def cli(self, *arguments):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = registry.main(['--root', str(self.root), '--document', str(self.document), *arguments])
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def files(self):
        return {str(path.relative_to(self.root)): (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
                for path in self.root.rglob('*') if path.is_file()}

    def replace_pair(self):
        replacement = copy.deepcopy(self.legacy)
        replacement['history'][0]['reason'] = 'Different historical appointment evidence'
        raw = codec.serialize_document(replacement, self.era).encode('utf-8')
        self.assertEqual(registry.current_fields(replacement), registry.current_fields(self.legacy))
        self.assertNotEqual(sha(raw), self.migration['prepare']['base']['document_sha256'])
        manifest = self.store.audit.read(self.manifest_path)
        manifest.update(sha256=sha(raw), bytes=len(raw))
        self.archive.write_bytes(raw)
        self.manifest_path.write_bytes(encoded(manifest))
        return manifest

    def clear(self):
        return self.store.switch_document('clear', self.store.current_snapshot()['document_token'],
            'user', 'synthetic clear', 'synthetic user authority')

    def test_wrong_valid_raw_manifest_pair_rejects_history_and_new_checkpoint(self):
        self.replace_pair()
        before = self.files()
        for read in (self.store.history_snapshot, self.clear):
            with self.subTest(reader=read.__name__), self.assertRaisesRegex(registry.RegistryError, 'legacy.*source'):
                read()
            self.assertEqual(self.files(), before)
        # Current authority does not depend on the damaged historical source.
        self.assertEqual(self.store.current_snapshot()['state'], registry.current_fields(self.legacy))
        code, _, error = self.cli('audit', '--transaction', self.op, '--history')
        self.assertEqual(code, 2)
        self.assertIn('audit_damaged', error)
        self.assertEqual(self.files(), before)

    def test_rehashed_checkpoint_cannot_reauthenticate_wrong_embedded_pair(self):
        self.clear()
        record = self.store.backup_candidates()[0]
        checkpoint_path = self.store.audit.checkpoints / (record['audit_checkpoint'] + '.json')
        checkpoint = self.store.audit.read(checkpoint_path)
        manifest = self.replace_pair()
        checkpoint['legacy_archives'][self.op] = manifest
        checkpoint['history'][0]['reason'] = 'Different historical appointment evidence'
        checkpoint_path.write_bytes(encoded(checkpoint))
        # A new outer hash cannot override the included migration source anchor.
        record = dict(record, audit_checkpoint_sha256=sha(encoded(checkpoint)))
        records = self.store.read_backup_index()
        records[0] = record
        self.store.write_backup_index(records)
        before = self.files()
        for read in (lambda: self.store._read_checkpoint(record['audit_checkpoint'], record['audit_checkpoint_sha256']),
                     lambda: self.store.switch_document('restore', self.store.current_snapshot()['document_token'],
                         'user', 'synthetic restore', 'synthetic authority', record['id'])):
            with self.subTest(reader=read), self.assertRaisesRegex(registry.RegistryError, 'legacy.*source'):
                read()
            self.assertEqual(self.files(), before)
        self.assertEqual(self.store.current_snapshot()['state']['owners'], {})

    def test_legacy_manifest_metadata_must_match_migration_prepare(self):
        original = self.manifest_path.read_bytes()
        for field, value in (('archive_id', str(uuid.uuid4())), ('format', codec.SPLIT_FORMAT),
                             ('era', str(uuid.uuid4())), ('revision', 2), ('bytes', len(self.raw) + 1)):
            with self.subTest(field=field):
                manifest = self.store.audit.read(self.manifest_path)
                manifest[field] = value
                self.manifest_path.write_bytes(encoded(manifest))
                before = self.files()
                with self.assertRaises(registry.RegistryError):
                    self.store.history_snapshot()
                self.assertEqual(self.files(), before)
                self.manifest_path.write_bytes(original)

    def test_checkpoint_embedded_metadata_and_anchor_coverage_are_validated(self):
        self.clear()
        record = self.store.backup_candidates()[0]
        checkpoint_path = self.store.audit.checkpoints / (record['audit_checkpoint'] + '.json')
        original = self.store.audit.read(checkpoint_path)
        variants = []
        bad = copy.deepcopy(original)
        bad['legacy_archives'][self.op]['era'] = str(uuid.uuid4())
        variants.append(bad)
        bad = copy.deepcopy(original)
        bad['attempts'] = []  # Self-consistent archive is not a migration anchor.
        variants.append(bad)
        bad = copy.deepcopy(original)
        bad['legacy_archives'] = {}
        variants.append(bad)
        for checkpoint in variants:
            with self.subTest(checkpoint=checkpoint):
                checkpoint_path.write_bytes(encoded(checkpoint))
                before = self.files()
                with self.assertRaises(registry.RegistryError):
                    self.store._read_checkpoint(record['audit_checkpoint'], sha(encoded(checkpoint)))
                self.assertEqual(self.files(), before)

    def test_valid_legacy_archive_and_current_checkpoint_remain_readable(self):
        self.assertEqual(self.store.history_snapshot(), self.legacy)
        self.clear()
        record = self.store.backup_candidates()[0]
        checkpoint = self.store._read_checkpoint(record['audit_checkpoint'], record['audit_checkpoint_sha256'])
        self.assertEqual(checkpoint['history'], self.legacy['history'])
        self.store.switch_document('restore', self.store.current_snapshot()['document_token'],
            'user', 'synthetic restore', 'synthetic authority', record['id'])
        self.assertEqual(self.store.history_snapshot(), self.legacy)

    def assert_selected_history(self):
        before = self.files()
        with patch.object(registry.Store, 'read_document', side_effect=AssertionError('selector opened current')):
            selected = self.store.audit_history_at(self.op)
            code, output, error = self.cli('audit', '--transaction', self.op, '--history')
        self.assertEqual(code, 0, error)
        self.assertEqual(json.loads(output), selected)
        self.assertEqual(selected['selected']['transaction_id'], self.op)
        self.assertEqual(selected['selected']['status'], 'COMMITTED')
        self.assertEqual(selected['source_era'], self.era)
        self.assertEqual(selected['revision'], 1)
        self.assertTrue(selected['history_loaded'])
        self.assertFalse(selected['current_authority'])
        self.assertEqual(selected['history'], self.legacy['history'])
        self.assertNotIn('document_token', selected)
        self.assertNotIn('current', selected)
        self.assertEqual(self.files(), before)
        return selected

    def test_public_history_locator_survives_clear_and_restore_another_branch(self):
        code, output, error = self.cli('clear', '--expected-document', self.store.current_snapshot()['document_token'],
            '--actor', 'user', '--reason', 'synthetic clear', '--authority', 'synthetic user authority')
        self.assertEqual(code, 0, error)
        current = self.store.current_snapshot()
        code, output, error = self.cli('query', '--history')
        self.assertEqual(code, 0, error)
        self.assertEqual(json.loads(output)['history'], [])
        self.assert_selected_history()
        self.assertEqual(self.store.current_snapshot(), current)
        # Restore populated history, then the other (empty) checkpoint branch.
        self.store.switch_document('restore', current['document_token'], 'user', 'synthetic restore',
            'synthetic authority', 'previous')
        self.store.switch_document('restore', self.store.current_snapshot()['document_token'], 'user',
            'synthetic other branch', 'synthetic authority', 'previous')
        self.assertEqual(self.store.history_snapshot()['history'], [])
        self.assert_selected_history()

    def test_history_locator_does_not_require_or_rebuild_current_document(self):
        self.clear()
        self.document.unlink()
        self.assert_selected_history()
        self.assertFalse(self.document.exists())
        self.document.write_bytes(b'invalid current must remain untouched')
        self.assert_selected_history()
        self.assertEqual(self.document.read_bytes(), b'invalid current must remain untouched')

    def test_v3_reset_and_restored_checkpoint_have_their_own_selected_history(self):
        cleared = self.clear()
        before = self.files()
        selected = self.store.audit_history_at(cleared['transaction_id'])
        self.assertEqual(selected['source_era'], cleared['era'])
        self.assertEqual(selected['revision'], 0)
        self.assertEqual(selected['history'], [])
        self.assertEqual(self.files(), before)
        restored = self.store.switch_document('restore', self.store.current_snapshot()['document_token'],
            'user', 'synthetic restore', 'synthetic authority', 'previous')
        before = self.files()
        selected = self.store.audit_history_at(restored['transaction_id'])
        self.assertEqual(selected['source_era'], restored['era'])
        self.assertEqual(selected['revision'], 1)
        self.assertEqual(selected['history'], self.legacy['history'])
        self.assertEqual(self.files(), before)

    def test_committed_outcome_before_cleanup_is_read_only_history(self):
        def fail(phase):
            if phase == 'outcome_synced':
                raise OSError('synthetic metadata cleanup interruption')
        self.store.audit.checkpoint = self.store.transaction_checkpoint = fail
        def change(state, era):
            state['owners']['alpha']['role'] = 'Committed historical role'
            return {}
        with self.assertRaises(registry.RegistryTransactionError):
            self.store.transact_current('synthetic-committed-pending', 'digest', {},
                self.store.current_snapshot()['document_token'], change, action='update-owner', actor='user',
                reason='synthetic committed event', authority='synthetic')
        before = self.files()
        op = self.store._transaction_id('synthetic-committed-pending')
        selected = self.store.audit_history_at(op)
        self.assertEqual(selected['selected']['status'], 'COMMITTED')
        self.assertTrue(selected['pending'])
        self.assertEqual([event['revision'] for event in selected['history']], [1, 2])
        self.assertEqual(selected['history'][-1]['reason'], 'synthetic committed event')
        self.assertEqual(self.files(), before)

    def test_empty_template_migration_has_selectable_empty_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / 'empty.md'
            document.write_text(codec.serialize_document(registry.empty(), codec.TEMPLATE_ERA), encoding='utf-8')
            (root / 'registry.lock').touch(mode=0o600)
            store = registry.Store(root, document)
            migrated = store.migrate_storage(store.current_snapshot()['document_token'], 'user',
                'synthetic empty migration', 'synthetic authority')
            self.assertIsNone(migrated['prepare']['after']['audit_head'])
            with patch.object(registry.Store, 'read_document', side_effect=AssertionError('selector opened current')):
                selected = store.audit_history_at(migrated['transaction_id'])
            self.assertEqual(selected['source_era'], codec.TEMPLATE_ERA)
            self.assertEqual(selected['revision'], 0)
            self.assertEqual(selected['history'], [])
            self.assertTrue(selected['history_loaded'])
            self.assertFalse(selected['current_authority'])

    def test_cli_process_reads_legacy_context_with_current_missing(self):
        self.clear()
        self.document.unlink()
        before = self.files()
        result = subprocess.run([sys.executable, '-B', registry.__file__, '--root', str(self.root),
            '--document', str(self.document), 'audit', '--transaction', self.op, '--history'],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        selected = json.loads(result.stdout)
        self.assertEqual(selected['history'], self.legacy['history'])
        self.assertIn(self.context, result.stdout)
        self.assertFalse(selected['current_authority'])
        self.assertFalse(self.document.exists())
        self.assertEqual(self.files(), before)

    def test_attempt_only_selector_does_not_hydrate_or_read_current(self):
        self.clear()
        before = self.files()
        with patch.object(registry.Store, 'read_document', side_effect=AssertionError('selector opened current')), \
             patch.object(registry.Store, '_history_at', side_effect=AssertionError('implicit history hydration')):
            code, output, error = self.cli('audit', '--transaction', self.op)
        self.assertEqual(code, 0, error)
        selected = json.loads(output)
        self.assertEqual(selected['selected']['transaction_id'], self.op)
        self.assertEqual(selected['prepare'], self.migration['prepare'])
        self.assertFalse(selected['history_loaded'])
        self.assertFalse(selected['current_authority'])
        self.assertNotIn('history', selected)
        self.assertEqual(self.files(), before)

    def test_noncommitted_attempt_is_evidence_not_committed_history(self):
        token = self.store.current_snapshot()['document_token']
        result = self.store.transact_current('synthetic-noop', 'digest', {}, token, lambda *_: {},
            action='test', actor='user', reason='synthetic noop', authority='synthetic', persist_noop=True)
        noop = result['transaction_id']
        def fail(phase):
            if phase == 'current_replaced':
                raise OSError('synthetic interrupted publication')
        self.store.audit.checkpoint = self.store.transaction_checkpoint = fail
        def change(state, era):
            state['owners']['alpha']['role'] = 'Unconfirmed role'
            return {}
        with self.assertRaises(registry.RegistryTransactionError):
            self.store.transact_current('synthetic-unknown', 'digest', {}, token, change,
                action='update-owner', actor='user', reason='synthetic change', authority='synthetic')
        unknown = self.store._transaction_id('synthetic-unknown')
        before = self.files()
        # Unresolved journal is visible without auto-recovering it.
        code, output, error = self.cli('audit', '--transaction', unknown)
        self.assertEqual(code, 0, error)
        self.assertTrue(json.loads(output)['pending'])
        self.assertIsNone(json.loads(output)['outcome'])
        code, _, error = self.cli('audit', '--transaction', unknown, '--history')
        self.assertEqual(code, 2)
        self.assertIn('history_unavailable', error)
        self.assertEqual(self.files(), before)
        # Explicitly produce UNKNOWN, outside the read-only selector under test.
        self.store.audit.checkpoint = self.store.transaction_checkpoint = lambda _: None
        self.document.unlink()
        self.store.recover_transaction('user', 'synthetic explicit recovery', 'synthetic authority')
        for op, status in ((unknown, 'PUBLICATION_UNKNOWN'), (noop, 'NOT_APPLICABLE')):
            with self.subTest(status=status):
                before = self.files()
                code, output, error = self.cli('audit', '--transaction', op)
                self.assertEqual(code, 0, error)
                evidence = json.loads(output)
                self.assertEqual(evidence['selected']['status'], status)
                self.assertIsNone(evidence['source_era'])
                self.assertIsNone(evidence['revision'])
                self.assertFalse(evidence['history_loaded'])
                self.assertFalse(evidence['current_authority'])
                code, _, error = self.cli('audit', '--transaction', op, '--history')
                self.assertEqual(code, 2)
                self.assertIn('history_unavailable', error)
                self.assertEqual(self.files(), before)

    def test_invalid_unknown_and_conflicting_selectors_preserve_files(self):
        cases = [
            ('audit', '--transaction', '../arbitrary.md', '--history'),
            ('audit', '--transaction', '{' + self.op + '}', '--history'),
            ('audit', '--transaction', str(uuid.uuid4()), '--history'),
            ('audit', '--transaction', self.op, '--operation-id', 'synthetic-migration'),
            ('audit', '--transaction', self.op, '--intent-digest', 'digest'),
            ('audit', '--history'),
            ('audit', '--intent-digest', 'digest'),
            ('audit', '--operation-id', 'synthetic-migration'),
            ('audit', '--operation-id', 'synthetic-migration', '--intent-digest', 'digest', '--history'),
        ]
        before = self.files()
        for arguments in cases:
            with self.subTest(arguments=arguments):
                code, output, error = self.cli(*arguments)
                self.assertEqual(code, 2)
                self.assertFalse(output)
                self.assertTrue(error)
                self.assertEqual(self.files(), before)

    def test_selector_requires_existing_root_and_stable_lock(self):
        # Current absence never authorizes provisioning missing private metadata.
        self.document.unlink()
        lock = self.root / 'registry.lock'
        lock.unlink()
        before = self.files()
        code, _, error = self.cli('audit', '--transaction', self.op, '--history')
        self.assertEqual(code, 2)
        self.assertIn('registry_unavailable', error)
        self.assertFalse(lock.exists())
        self.assertEqual(self.files(), before)
        absent = self.root / 'absent-private-root'
        store = registry.Store(absent, self.document)
        with self.assertRaises(registry.RegistryTransactionError) as failure:
            store.audit_history_at(self.op)
        self.assertEqual(failure.exception.code, 'registry_unavailable')
        self.assertFalse(absent.exists())

    def test_damaged_audit_head_does_not_trigger_recovery_or_repair(self):
        self.clear()
        self.store.audit.head_path.write_bytes(b'damaged private head')
        before = self.files()
        code, _, error = self.cli('audit', '--transaction', self.op, '--history')
        self.assertEqual(code, 2)
        self.assertIn('audit_damaged', error)
        self.assertEqual(self.files(), before)
        self.assertEqual(self.store.current_snapshot()['state']['owners'], {})

    def test_default_audit_and_query_history_contracts_are_unchanged(self):
        code, output, error = self.cli('audit')
        self.assertEqual(code, 0, error)
        self.assertEqual(json.loads(output), {'attempts': self.store.audit.history()})
        code, output, error = self.cli('audit', '--operation-id', 'synthetic-migration',
            '--intent-digest', self.migration['prepare']['intent_digest'])
        self.assertEqual(code, 0, error)
        self.assertEqual(json.loads(output)['prepare'], self.migration['prepare'])
        code, output, error = self.cli('query', '--history')
        self.assertEqual(code, 0, error)
        self.assertEqual(json.loads(output)['history'], self.legacy['history'])

    def test_current_reads_and_writes_keep_bounded_history_io(self):
        # Three new publications make the migration op older than head/last_commit.
        for n in range(3):
            def change(state, era):
                state['owners']['alpha']['role'] = 'synthetic role ' + str(n)
                return {}
            self.store.transact_current('role-' + str(n), 'digest', {}, self.store.current_snapshot()['document_token'],
                change, action='update-owner', actor='user', reason='synthetic update', authority='synthetic')
        from audit_storage import read_private
        def bounded(path):
            self.assertNotEqual(Path(path), self.store.audit.prepare_path(self.op))
            self.assertNotIn(self.store.audit.legacy, Path(path).parents)
            return read_private(path)
        with patch('audit_storage.read_private', side_effect=bounded), \
             patch.object(registry, 'read_private', side_effect=bounded), \
             patch('os.listdir', side_effect=AssertionError('directory enumeration')), \
             patch('os.scandir', side_effect=AssertionError('directory enumeration')), \
             patch.object(registry.Store, '_read_legacy_archive', side_effect=AssertionError('normal path legacy hydration')):
            self.store.current_snapshot()
            for arguments in (('query',), ('query', '--active')):
                code, output, error = self.cli(*arguments)
                self.assertEqual(code, 0, error)
                self.assertFalse(json.loads(output)['history_loaded'])
            def change(state, era):
                state['owners']['alpha']['role'] = 'bounded final write'
                return {}
            self.store.transact_current('bounded-final', 'digest', {}, self.store.current_snapshot()['document_token'],
                change, action='update-owner', actor='user', reason='synthetic update', authority='synthetic')


if __name__ == '__main__':
    unittest.main()
