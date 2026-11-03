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

    def test_callback_cannot_tamper_revision(self):
        before = self.document.read_bytes()
        def bad(state, era):
            state['revision'] = 100
            return {}
        with self.assertRaisesRegex(registry.RegistryError, 'revision'):
            self.commit(change=bad)
        self.assertEqual(self.document.read_bytes(), before)
        self.assertFalse(self.store.audit.exists())

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

    def test_corrupt_head_blocks_managed_write_but_current_query_works(self):
        self.commit()
        self.store.audit.head_path.write_bytes(b'corrupt')
        self.assertIn('alpha', self.store.current_snapshot()['state']['owners'])
        with self.assertRaises(registry.RegistryTransactionError) as failure:
            self.commit(operation='other', digest='other')
        self.assertEqual(failure.exception.code, 'audit_damaged')
        self.assertEqual(self.store.audit.head_path.read_bytes(), b'corrupt')


if __name__ == '__main__':
    unittest.main()
