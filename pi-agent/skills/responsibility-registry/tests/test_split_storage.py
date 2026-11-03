"""Real v3 Store publication/recovery and bounded-history behavior."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

from test_registry import registry, appointment
import registry_document as codec
from audit_storage import encoded, sha


class SplitStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.document = self.root / 'RESPONSIBILITIES.md'
        self.store = registry.Store(self.root, self.document)
        self.store.initialize()
        self.calls = 0

    def change(self, state, era):
        self.calls += 1
        p = appointment()
        # Use the actual CLI business mutator, not a fabricated Store result.
        from types import SimpleNamespace
        return registry.mutate_records(state, SimpleNamespace(command='register', actor='alpha'), p)

    def commit(self, operation='synthetic-operation', digest='synthetic-digest', token=None, change=None, **extra):
        return self.store.transact_current(operation, digest, dict(test='real records'),
            token or self.store.current_snapshot()['document_token'], change or self.change,
            action='register', actor='alpha', reason='Synthetic user appointment', authority='synthetic user message', **extra)

    def recover(self):
        return self.store.recover_transaction('user', 'explicit synthetic recovery', 'synthetic user authority')

    def test_five_field_current_real_commit_same_op_before_stale_cas(self):
        before = self.store.current_snapshot()
        self.assertEqual(set(before), {'state', 'era', 'document_token', 'format', 'audit_head', 'store_id'})
        self.assertEqual(set(before['state']), {'version', 'revision', 'owners', 'assignments', 'coordination'})
        result = self.commit(token=before['document_token'])
        self.assertEqual(result['outcome']['status'], 'COMMITTED')
        self.assertEqual(result['current']['state']['revision'], 1)
        self.assertEqual(result['prepare']['event']['after']['owners']['alpha']['parent'], 'user')
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.commit(token=before['document_token'])['prepare'], result['prepare'])
        self.assertEqual(self.calls, 1)
        with self.assertRaises(registry.RegistryTransactionError) as conflict:
            self.commit(digest='different', token=before['document_token'])
        self.assertEqual(conflict.exception.code, 'operation_intent_mismatch')

    def test_noop_receipt_private_no_revision_no_current_write(self):
        before = self.document.read_bytes()
        snapshot = self.store.current_snapshot()
        result = self.commit(change=lambda state, era: {'no_owner': True}, persist_noop=True)
        self.assertEqual(result['outcome']['status'], 'NOT_APPLICABLE')
        self.assertIsNone(result['prepare']['event'])
        self.assertEqual(self.document.read_bytes(), before)
        self.assertEqual(self.store.current_snapshot(), snapshot)
        self.assertEqual(self.commit(change=lambda *_: self.fail('replayed noop'))['outcome']['status'], 'NOT_APPLICABLE')

    def test_missing_root_or_lock_has_typed_read_only_unavailable_errors(self):
        for condition in ('missing-root', 'missing-lock', 'root-permissions', 'symlink-lock'):
            with self.subTest(condition=condition), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                root, document = base / 'private', base / 'current.md'
                document.write_text(codec.serialize_current_document(registry.current_fields(registry.empty()), codec.TEMPLATE_ERA))
                if condition != 'missing-root':
                    root.mkdir(mode=0o700)
                if condition == 'root-permissions':
                    root.chmod(0o755)
                if condition == 'symlink-lock':
                    target = base / 'sentinel'
                    target.write_bytes(b'unchanged lock target')
                    target.chmod(0o600)
                    (root / 'registry.lock').symlink_to(target)
                store = registry.Store(root, document)
                calls = (
                    ('snapshot', store.current_snapshot),
                    ('status', lambda: store.operation_status('op', 'digest')),
                    ('transact', lambda: store.transact_current('op', 'digest', {}, 'token', lambda *_: self.fail('callback before lock'),
                        action='test', actor='user', reason='test', authority='test')))
                raw = document.read_bytes()
                for name, call in calls:
                    with self.subTest(entry=name), self.assertRaises(registry.RegistryTransactionError) as failure:
                        call()
                    self.assertEqual(failure.exception.code, 'registry_unavailable')
                    self.assertEqual(failure.exception.commit_state, 'unknown' if name == 'status' else 'not_committed')
                    self.assertFalse(failure.exception.retryable)
                    self.assertEqual(document.read_bytes(), raw)
                if condition == 'missing-root':
                    self.assertFalse(root.exists())
                elif condition == 'symlink-lock':
                    self.assertTrue((root / 'registry.lock').is_symlink())
                    self.assertEqual(target.read_bytes(), b'unchanged lock target')
                else:
                    self.assertFalse((root / 'registry.lock').exists())

    def test_fresh_v3_initialization_persists_explicit_empty_audit_head(self):
        self.assertTrue(self.store.audit.head_path.is_file())
        marker = self.store.audit.read(self.store.audit.head_path)
        self.assertEqual(marker, dict(schema=1, op_id=None, prepare_sha256=None,
                                      outcome_sha256=None, last_committed=None))
        self.assertIsNone(self.store.audit.head())
        self.assertIsNone(self.store.current_snapshot()['audit_head'])
        self.assertIsNone(self.store.operation_status('absent', 'digest')['prepare'])

    def test_lost_audit_root_or_head_never_reprovisions_even_after_no_owner_noop(self):
        import shutil
        from types import SimpleNamespace
        for prior in ('commit', 'no-owner-noop', 'fresh'):
            for lost in ('audit-root', 'head', 'ops', 'legacy', 'checkpoints'):
                with self.subTest(prior=prior, lost=lost), tempfile.TemporaryDirectory() as directory:
                    store = registry.Store(directory, Path(directory) / 'current.md')
                    store.initialize()
                    if prior != 'fresh':
                        result = store.transact_current('original', 'digest', {}, store.current_snapshot()['document_token'],
                            self.change if prior == 'commit' else lambda *_: {'no_owner': True},
                            action='register', actor='alpha', reason='synthetic authority', authority='synthetic',
                            persist_noop=prior == 'no-owner-noop')
                        self.assertEqual(result['outcome']['status'], 'COMMITTED' if prior == 'commit' else 'NOT_APPLICABLE')
                    before = store.current_snapshot()
                    if prior != 'commit':
                        self.assertIsNone(before['audit_head'])
                    path = {'audit-root': store.audit.path, 'head': store.audit.head_path,
                            'ops': store.audit.ops, 'legacy': store.audit.legacy, 'checkpoints': store.audit.checkpoints}[lost]
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                    raw, inode = store.document.read_bytes(), store.document.stat().st_ino
                    self.assertEqual(store.current_snapshot(), before)
                    query = store.query_current(SimpleNamespace(owner=None, project=None, task=None, repo=None, active=True))
                    self.assertFalse(query['history_loaded'])
                    for same_op in (False, True):
                        operation = 'original' if same_op else 'new'
                        with self.assertRaises(registry.RegistryTransactionError) as failure:
                            store.transact_current(operation, 'digest', {}, before['document_token'],
                                lambda *_: self.fail('callback ran after audit loss'), action='test', actor='user',
                                reason='test', authority='test', persist_noop=True)
                        self.assertIn(failure.exception.code, ('audit_unavailable', 'audit_damaged'))
                        self.assertEqual(failure.exception.commit_state, 'unknown')
                        with self.assertRaises(registry.RegistryTransactionError) as status_failure:
                            store.operation_status(operation, 'digest')
                        self.assertEqual(status_failure.exception.commit_state, 'unknown')
                    with self.assertRaises((OSError, registry.RegistryError)):
                        store.history_snapshot()  # Explicit history cannot report lost evidence as empty.
                    self.assertFalse(path.exists())
                    self.assertEqual(store.document.read_bytes(), raw)
                    self.assertEqual(store.document.stat().st_ino, inode)

    def test_real_crlf_v3_file_uses_split_store_without_relaxing_full_v2(self):
        before = self.store.current_snapshot()
        raw = self.document.read_bytes().replace(b'\n', b'\r\n')
        self.document.write_bytes(raw)
        with self.assertRaises(registry.RegistryError):
            codec.parse_document(raw.decode('utf-8'))
        with patch('registry.parse_document', side_effect=AssertionError('v3 dispatched to full v2')):
            current = self.store.current_snapshot()
            self.assertEqual(current['state'], before['state'])
            self.assertEqual(current['format'], codec.SPLIT_FORMAT)
            noop = self.commit(change=lambda *_: {'no_owner': True}, persist_noop=True)
            self.assertEqual(noop['outcome']['status'], 'NOT_APPLICABLE')
            self.assertEqual(self.document.read_bytes(), raw)
            self.assertEqual(self.commit(operation='register-crlf', digest='register-crlf')['outcome']['status'], 'COMMITTED')

    def test_explicit_initialization_recovers_provisioning_but_never_repairs_existing_v3(self):
        import shutil
        # An existing current file is never permission to initialize lost audit.
        shutil.rmtree(self.store.audit.path)
        before = self.document.read_bytes(), self.document.stat().st_ino
        self.assertFalse(self.store.initialize()['initialized'])
        self.assertFalse(self.store.audit.exists())
        self.assertEqual((self.document.read_bytes(), self.document.stat().st_ino), before)
        # Only a genuine pending fresh initialization can resume provisioning.
        for cut in ('provision_head_temp_synced', 'provision_head_published', 'provision_head_synced'):
            with self.subTest(cut=cut), tempfile.TemporaryDirectory() as directory:
                store = registry.Store(directory, Path(directory) / 'current.md')
                def fail(phase):
                    if phase == cut:
                        raise OSError('synthetic fresh provision interruption')
                store.audit.checkpoint = fail
                with self.assertRaises(OSError):
                    store.initialize()
                self.assertEqual(store.control()['status'], 'pending')
                self.assertFalse(store.document.exists())
                store.audit.checkpoint = lambda _: None
                store.recover_initialization()
                self.assertEqual(store.control()['status'], 'completed')
                self.assertIsNone(store.audit.head())
                self.assertEqual(store.current_snapshot()['format'], codec.SPLIT_FORMAT)
                self.assertEqual(store.transact_current('op', 'digest', {}, store.current_snapshot()['document_token'],
                    self.change, action='register', actor='alpha', reason='test', authority='test')['outcome']['status'], 'COMMITTED')

    def test_initialize_never_recreates_current_or_lock_over_runtime_evidence(self):
        for lost in ('current-and-control', 'lock', 'lock-and-control', 'lock-and-audit'):
            with self.subTest(lost=lost), tempfile.TemporaryDirectory() as directory:
                import shutil
                store = registry.Store(directory, Path(directory) / 'current.md')
                store.initialize()
                lock = store.root / 'registry.lock'
                control = store.root / 'document-control.json'
                if lost == 'current-and-control':
                    store.document.unlink()
                    control.unlink()
                    head, inode = store.audit.head_path.read_bytes(), lock.stat().st_ino
                    with self.assertRaises(registry.RegistryError):
                        store.initialize()
                    self.assertFalse(store.document.exists())
                    self.assertFalse(control.exists())
                    self.assertEqual(store.audit.head_path.read_bytes(), head)
                    self.assertEqual(lock.stat().st_ino, inode)
                else:
                    lock.unlink()
                    if lost == 'lock-and-control':
                        control.unlink()
                    if lost == 'lock-and-audit':
                        shutil.rmtree(store.audit.path)
                    raw = store.document.read_bytes()
                    for call in (store.initialize, store.migrate):
                        with self.assertRaises(registry.RegistryTransactionError) as failure:
                            call()
                        self.assertEqual(failure.exception.code, 'registry_unavailable')
                        self.assertEqual(failure.exception.commit_state, 'not_committed')
                        self.assertFalse(lock.exists())
                        self.assertEqual(store.document.read_bytes(), raw)

    def test_pending_recovery_refuses_missing_head_without_changing_any_evidence(self):
        def fail(phase):
            if phase == 'current_replaced':
                raise OSError('synthetic pending transaction')
        self.store.audit.checkpoint = self.store.transaction_checkpoint = fail
        with self.assertRaises(registry.RegistryTransactionError):
            self.commit()
        self.store.audit.checkpoint = self.store.transaction_checkpoint = lambda _: None
        self.store.audit.head_path.unlink()
        pending, raw = self.store.audit.pending_path.read_bytes(), self.document.read_bytes()
        outcome_path = self.store.audit.outcome_path(self.store._transaction_id('synthetic-operation'))
        self.assertFalse(outcome_path.exists())
        with self.assertRaises(registry.RegistryTransactionError) as failure:
            self.recover()
        self.assertEqual(failure.exception.code, 'audit_damaged')
        self.assertEqual(failure.exception.commit_state, 'unknown')
        self.assertFalse(self.store.audit.head_path.exists())
        self.assertFalse(outcome_path.exists())
        self.assertEqual(self.store.audit.pending_path.read_bytes(), pending)
        self.assertEqual(self.document.read_bytes(), raw)

    def test_concurrent_same_operation_executes_callback_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        token = self.store.current_snapshot()['document_token']
        barrier = Barrier(4)
        def attempt(_):
            barrier.wait(timeout=5)
            return self.commit(token=token)
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(attempt, range(4)))
        self.assertEqual(self.calls, 1)
        self.assertEqual({value['transaction_id'] for value in results}, {results[0]['transaction_id']})
        self.assertTrue(all(value['outcome']['status'] == 'COMMITTED' for value in results))
        self.assertEqual(self.store.current_snapshot()['state']['revision'], 1)

    def test_callback_cannot_tamper_revision(self):
        before = self.document.read_bytes()
        head = self.store.audit.head_path.read_bytes()
        def bad(state, era):
            state['revision'] = 100
            return {}
        with self.assertRaisesRegex(registry.RegistryError, 'revision'):
            self.commit(change=bad)
        self.assertEqual(self.document.read_bytes(), before)
        self.assertEqual(self.store.audit.head_path.read_bytes(), head)
        self.assertIsNone(self.store.audit.pending())

    def test_all_fault_boundaries_recover_without_replaying_current(self):
        phases = ('pending_temp_synced', 'pending_published', 'pending_synced',
                  'prepare_temp_synced', 'prepare_published', 'prepare_synced',
                  'current_temp_synced', 'current_before_replace', 'current_replaced', 'current_synced',
                  'outcome_temp_synced', 'outcome_published', 'outcome_synced',
                  'head_temp_synced', 'head_published', 'head_synced',
                  'pending_before_unlink', 'pending_unlinked', 'pending_cleanup_synced')
        for phase in phases:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                store = registry.Store(directory, Path(directory) / 'current.md')
                store.initialize()
                before = store.document.read_bytes()
                token = store.current_snapshot()['document_token']
                count = []
                def change(state, era):
                    count.append(True)
                    return self.change(state, era)
                def crash(actual):
                    if actual == phase:
                        raise OSError('fault:' + phase)
                store.transaction_checkpoint = crash
                store.audit.checkpoint = crash
                with self.assertRaises(registry.RegistryTransactionError):
                    store.transact_current('op', 'digest', {}, token, change, action='register', actor='alpha', reason='test', authority='test')
                observed = store.document.read_bytes()
                # Status/query must be side-effect free even when pending.
                status = store.operation_status('op', 'digest')
                self.assertEqual(store.document.read_bytes(), observed)
                store.transaction_checkpoint = lambda _: None
                store.audit.checkpoint = lambda _: None
                if store.audit.pending() is not None:
                    # Audit metadata still uses replace, so forbid only current.
                    original = __import__('os').replace
                    def metadata_only(source, target):
                        self.assertNotEqual(Path(target), store.document)
                        return original(source, target)
                    with patch('os.replace', side_effect=metadata_only):
                        status = store.recover_transaction('user', 'recover test', 'synthetic evidence')
                    self.assertEqual(status['outcome']['status'], 'PUBLICATION_UNKNOWN' if observed == before else 'COMMITTED')
                    self.assertFalse(status['pending'])
                elif phase == 'pending_temp_synced':
                    self.assertIsNone(status['prepare'])
                else:
                    self.assertEqual(status['outcome']['status'], 'COMMITTED')
                self.assertEqual(store.document.read_bytes(), observed)
                self.assertEqual(len(count), 1)

    def test_real_fsync_errors_at_every_transaction_sync_do_not_replay(self):
        import os
        original_sync = os.fsync
        sync_count = []
        # Explicit init already made directories and the empty head durable.
        def trace(fd):
            sync_count.append(True)
            return original_sync(fd)
        with patch('os.fsync', side_effect=trace):
            self.commit()
        self.assertGreaterEqual(len(sync_count), 10)
        for cut in range(1, len(sync_count) + 1):
            with self.subTest(fsync_call=cut), tempfile.TemporaryDirectory() as directory:
                store = registry.Store(directory, Path(directory) / 'current.md')
                store.initialize()
                before = store.document.read_bytes()
                counter = []
                def failing_sync(fd):
                    counter.append(True)
                    if len(counter) == cut:
                        raise OSError('synthetic real fsync error')
                    return original_sync(fd)
                with patch('os.fsync', side_effect=failing_sync):
                    with self.assertRaises(registry.RegistryTransactionError):
                        store.transact_current('op', 'digest', {}, store.current_snapshot()['document_token'], self.change,
                            action='register', actor='alpha', reason='test', authority='test')
                observed = store.document.read_bytes()
                status = store.operation_status('op', 'digest')
                if store.audit.pending() is not None:
                    status = store.recover_transaction('user', 'recover', 'synthetic authority')
                    self.assertEqual(status['outcome']['status'], 'PUBLICATION_UNKNOWN' if observed == before else 'COMMITTED')
                self.assertEqual(store.document.read_bytes(), observed)
                if observed != before:
                    self.assertEqual(store.current_snapshot()['state']['revision'], 1)

    def test_corrupt_immutable_prepare_or_outcome_preserves_current_and_evidence(self):
        for side in ('prepare', 'outcome'):
            with self.subTest(side=side), tempfile.TemporaryDirectory() as directory:
                store = registry.Store(directory, Path(directory) / 'current.md')
                store.initialize()
                result = store.transact_current('op', 'digest', {}, store.current_snapshot()['document_token'], self.change,
                    action='register', actor='alpha', reason='test', authority='test')
                path = store.audit.prepare_path(result['transaction_id']) if side == 'prepare' else store.audit.outcome_path(result['transaction_id'])
                path.write_bytes(b'partial-final-evidence')
                raw = store.document.read_bytes()
                self.assertIn('alpha', store.current_snapshot()['state']['owners'])
                with self.assertRaises(registry.RegistryTransactionError) as failure:
                    store.transact_current('new', 'new', {}, store.current_snapshot()['document_token'], lambda *_: {},
                        action='test', actor='user', reason='test', authority='test', persist_noop=True)
                self.assertEqual(failure.exception.code, 'audit_damaged')
                self.assertEqual(path.read_bytes(), b'partial-final-evidence')
                self.assertEqual(store.document.read_bytes(), raw)

    def test_manual_divergence_and_exact_before_restore_are_unknown_not_replayed(self):
        for manual in ('different', 'exact_before'):
            with self.subTest(manual=manual), tempfile.TemporaryDirectory() as directory:
                store = registry.Store(directory, Path(directory) / 'current.md')
                store.initialize()
                before = store.document.read_bytes()
                def crash(phase):
                    if phase == 'current_replaced':
                        raise OSError('stop after replace')
                store.audit.checkpoint = store.transaction_checkpoint = crash
                with self.assertRaises(registry.RegistryTransactionError):
                    store.transact_current('op', 'digest', {}, store.current_snapshot()['document_token'], self.change,
                        action='register', actor='alpha', reason='test', authority='test')
                if manual == 'different':
                    state, era, metadata = codec.parse_store_document(store.document.read_text())
                    state['owners']['alpha']['role'] = 'User manually edited role'
                    store.document.write_text(codec.serialize_current_document(state, era, metadata['audit_head']))
                else:
                    store.document.write_bytes(before)
                edited = store.document.read_bytes()
                store.audit.checkpoint = store.transaction_checkpoint = lambda _: None
                result = store.recover_transaction('user', 'recover', 'synthetic user evidence')
                self.assertEqual(result['outcome']['status'], 'PUBLICATION_UNKNOWN')
                self.assertEqual(store.document.read_bytes(), edited)
                self.assertEqual(store.transact_current('op', 'digest', {}, 'stale', lambda *_: self.fail('replay'),
                    action='register', actor='alpha', reason='test', authority='test')['outcome']['status'], 'PUBLICATION_UNKNOWN')
                # UNKNOWN does not permanently block a newly authorized operation.
                store.transact_current('new-op', 'new-digest', {}, store.current_snapshot()['document_token'],
                    lambda state, era: {}, action='test', actor='user', reason='new authority', authority='user', persist_noop=True)

    def test_current_and_normal_write_never_read_old_ops_or_legacy(self):
        self.commit()
        old = self.store.audit.prepare_path(self.store._transaction_id('synthetic-operation'))
        def role(value):
            def change(state, era):
                state['owners']['alpha']['role'] = value
                return {}
            return change
        self.commit(operation='second', digest='second', change=role('second role'))
        self.commit(operation='third', digest='third', change=role('third role'))
        from audit_storage import read_private
        def bounded(path):
            self.assertNotEqual(Path(path), old)
            self.assertNotIn('legacy', Path(path).parts)
            return read_private(path)
        with patch('audit_storage.read_private', side_effect=bounded), patch('os.listdir', side_effect=AssertionError('history scan')):
            self.store.current_snapshot()
            self.commit(operation='fourth', digest='fourth', change=lambda *_: {}, persist_noop=True)

    def test_split_clear_restore_previous_exact_backup_and_retained_receipts(self):
        original = self.commit()
        before = self.store.current_snapshot()
        raw = self.document.read_bytes()
        full = self.store.history_snapshot()
        cleared = self.store.switch_document('clear', before['document_token'], 'user', 'explicit clear', 'synthetic user authority')
        self.assertFalse(self.store.current_snapshot()['state']['owners'])
        self.assertEqual(self.store.history_snapshot()['history'], [])
        records = self.store.backup_candidates()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(self.store.snapshot_bytes(record)[0], raw)
        self.assertEqual(record['storage_format'], codec.SPLIT_FORMAT)
        checkpoint = self.store.audit.read(self.store.audit.checkpoints / (record['audit_checkpoint'] + '.json'))
        self.assertEqual(checkpoint['history'], full['history'])
        self.assertTrue(checkpoint['attempts'])
        empty_raw = self.document.read_bytes()
        restored = self.store.switch_document('restore', self.store.current_snapshot()['document_token'],
                                              'user', 'explicit restore', 'synthetic user authority', 'previous')
        self.assertEqual(self.store.history_snapshot(), full)
        self.assertNotEqual(restored['era'], before['era'])
        self.assertNotEqual(restored['era'], cleared['era'])
        self.assertEqual(self.store.snapshot_bytes(self.store.backup_candidates()[-1])[0], empty_raw)
        status = self.store.operation_status('synthetic-operation', 'synthetic-digest')
        self.assertEqual(status['prepare'], original['prepare'])
        self.assertNotEqual(status['current']['era'], status['prepare']['after']['era'])
        # previous is selected before adding the new backup, so it toggles back.
        self.store.switch_document('restore', restored['document_token'], 'user', 'toggle', 'synthetic user authority', 'previous')
        self.assertFalse(self.store.current_snapshot()['state']['owners'])
        self.assertEqual(len(self.store.audit.history()), 4)

    def test_restore_old_v2_target_after_migration_does_not_rewrite_legacy_backups(self):
        self.commit()
        state = self.store.history_snapshot()
        era = self.store.current_snapshot()['era']
        self.document.write_text(codec.serialize_document(state, era))
        self.store.switch_document('clear', self.store.current_snapshot()['document_token'], 'user', 'legacy clear', 'user evidence')
        old_record = self.store.backup_candidates()[0]
        old_raw = (self.document.parent / old_record['snapshot']).read_bytes()
        old_record_exact = copy.deepcopy(old_record)
        self.store.migrate_storage(self.store.current_snapshot()['document_token'], 'user', 'explicit migrate', 'user evidence')
        self.store.switch_document('restore', self.store.current_snapshot()['document_token'], 'user', 'restore old target', 'user evidence', old_record['id'])
        self.assertEqual(self.store.history_snapshot(), state)
        self.assertEqual(self.store.backup_candidates()[0], old_record_exact)
        self.assertEqual((self.document.parent / old_record['snapshot']).read_bytes(), old_raw)

    def test_split_backup_index_failure_after_publication_recovers_same_journal(self):
        self.commit()
        original = self.store.write_backup_index
        calls = []
        def fail_completed(records):
            calls.append(copy.deepcopy(records))
            if any(record['status'] == 'completed' for record in records):
                raise OSError('synthetic completed-index failure')
            return original(records)
        with patch.object(self.store, 'write_backup_index', side_effect=fail_completed):
            with self.assertRaises(registry.RegistryTransactionError) as failure:
                self.store.switch_document('clear', self.store.current_snapshot()['document_token'], 'user', 'clear', 'user evidence')
        self.assertEqual(failure.exception.commit_state, 'committed')  # immutable OUTCOME is already durable
        self.assertFalse(self.store.current_snapshot()['state']['owners'])
        self.assertFalse(self.store.switch_journal.exists())
        self.assertTrue(self.store.audit.pending())
        current = self.document.read_bytes()
        result = self.recover()
        self.assertEqual(result['outcome']['status'], 'COMMITTED')
        self.assertEqual(len(self.store.backup_candidates()), 1)
        self.assertEqual(self.document.read_bytes(), current)
        self.assertIsNone(self.store.audit.pending())

    def test_manual_revision_jump_is_current_authority_without_padding_history(self):
        self.commit()
        state, era, metadata = codec.parse_store_document(self.document.read_text())
        state['revision'] = 50
        state['owners']['alpha']['role'] = 'Manually accepted role'
        self.document.write_text(codec.serialize_current_document(state, era, metadata['audit_head']))
        def update(state, era):
            state['assignments']['release']['acceptance'] = 'New explicitly authorized acceptance'
            return {}
        result = self.commit(operation='after-manual', digest='after-manual', change=update)
        self.assertEqual(result['current']['state']['revision'], 51)
        self.assertEqual(result['current']['state']['owners']['alpha']['role'], 'Manually accepted role')
        self.assertEqual(result['prepare']['continuity']['kind'], 'unmanaged_change_observed')
        history = self.store.history_snapshot()['history']
        self.assertEqual([event['revision'] for event in history], [1, 51])
        self.assertEqual(result['prepare']['event']['before']['owners'], {})
        self.assertEqual(list(result['prepare']['event']['before']['assignments']), ['release'])

    def test_unknown_locator_does_not_turn_unknown_delta_into_committed_history(self):
        self.commit()
        def crash(phase):
            if phase == 'current_replaced':
                raise OSError('post-replace interruption')
        self.store.audit.checkpoint = self.store.transaction_checkpoint = crash
        def change(state, era):
            state['owners']['alpha']['role'] = 'Unconfirmed publication'
            return {}
        with self.assertRaises(registry.RegistryTransactionError):
            self.commit(operation='unknown', digest='unknown', change=change)
        state, era, metadata = codec.parse_store_document(self.document.read_text())
        state['owners']['alpha']['role'] = 'User accepted manual role'
        self.document.write_text(codec.serialize_current_document(state, era, metadata['audit_head']))
        self.store.audit.checkpoint = self.store.transaction_checkpoint = lambda _: None
        self.assertEqual(self.recover()['outcome']['status'], 'PUBLICATION_UNKNOWN')
        def next_change(state, era):
            state['assignments']['release']['acceptance'] = 'New acceptance'
            return {}
        self.commit(operation='later', digest='later', change=next_change)
        history = self.store.history_snapshot()['history']
        self.assertEqual([event['revision'] for event in history], [1, 3])
        self.assertEqual([attempt['outcome']['status'] for attempt in self.store.audit.history()],
                         ['COMMITTED', 'PUBLICATION_UNKNOWN', 'COMMITTED'])

    def test_missing_or_invalid_current_recovery_only_finalizes_metadata(self):
        for observed in ('missing', 'invalid'):
            with self.subTest(current=observed), tempfile.TemporaryDirectory() as directory:
                store = registry.Store(directory, Path(directory) / 'current.md')
                store.initialize()
                def crash(phase):
                    if phase == 'current_replaced':
                        raise OSError('interruption')
                store.audit.checkpoint = store.transaction_checkpoint = crash
                with self.assertRaises(registry.RegistryTransactionError):
                    store.transact_current('op', 'digest', {}, store.current_snapshot()['document_token'], self.change,
                        action='register', actor='alpha', reason='test', authority='test')
                if observed == 'missing':
                    store.document.unlink()
                else:
                    store.document.write_bytes(b'corrupt current evidence')
                store.audit.checkpoint = store.transaction_checkpoint = lambda _: None
                result = store.recover_transaction('user', 'metadata recovery', 'synthetic authority')
                self.assertEqual(result['outcome']['status'], 'PUBLICATION_UNKNOWN')
                self.assertIsNone(result['current'])
                if observed == 'missing':
                    self.assertFalse(store.document.exists())
                else:
                    self.assertEqual(store.document.read_bytes(), b'corrupt current evidence')

    def test_missing_current_switch_recovery_retains_unknown_backup_evidence(self):
        self.commit()
        def crash(phase):
            if phase == 'current_replaced':
                raise OSError('synthetic switch interruption')
        self.store.audit.checkpoint = self.store.transaction_checkpoint = crash
        with self.assertRaises(registry.RegistryTransactionError):
            self.store.switch_document('clear', self.store.current_snapshot()['document_token'], 'user', 'clear', 'user evidence')
        self.document.unlink()
        self.store.audit.checkpoint = self.store.transaction_checkpoint = lambda _: None
        result = self.recover()
        self.assertEqual(result['outcome']['status'], 'PUBLICATION_UNKNOWN')
        self.assertFalse(self.document.exists())
        records = self.store.read_backup_index()
        self.assertEqual(records[0]['status'], 'superseded-manual')
        self.assertIsNone(records[0]['reconciliation']['observed_sha256'])
        self.assertEqual(self.store.snapshot_bytes(records[0])[1]['revision'], 1)

    def test_query_history_is_opt_in_and_report_cannot_overwrite_audit(self):
        from contextlib import redirect_stdout, redirect_stderr
        import io
        self.commit()
        def cli(*arguments):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = registry.main(['--root', str(self.root), '--document', str(self.document), *arguments])
            return code, out.getvalue(), err.getvalue()
        for arguments in [('query',), ('query', '--active')]:
            with patch.object(registry.AuditStorage, 'history', side_effect=AssertionError('implicit hydration')):
                code, output, _ = cli(*arguments)
            self.assertEqual(code, 0)
            value = json.loads(output)
            self.assertFalse(value['history_loaded'])
            self.assertNotIn('history', value)
        code, output, _ = cli('query', '--history')
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output)['history_loaded'])
        self.assertEqual(len(json.loads(output)['history']), 1)
        for destination in (self.store.audit.head_path, self.store.audit.pending_path,
                            self.store.audit.ops / 'report.md', self.store.audit.path / 'receipts/report.md'):
            code, _, error = cli('rebuild', '--output', str(destination))
            self.assertEqual(code, 2)
            self.assertIn('report output cannot replace', error)

    def test_corrupt_head_blocks_managed_write_but_current_query_works(self):
        self.commit()
        self.store.audit.head_path.write_bytes(b'corrupt')
        self.assertIn('alpha', self.store.current_snapshot()['state']['owners'])
        with self.assertRaises(registry.RegistryTransactionError) as failure:
            self.commit(operation='other', digest='other')
        self.assertEqual(failure.exception.code, 'audit_damaged')
        self.assertEqual(self.store.audit.head_path.read_bytes(), b'corrupt')
        self.store.audit.head_path.unlink()  # missing is also damage when current points at retained evidence
        with self.assertRaises(registry.RegistryTransactionError) as failure:
            self.commit(operation='another', digest='another')
        self.assertEqual(failure.exception.code, 'audit_damaged')


if __name__ == '__main__':
    unittest.main()
