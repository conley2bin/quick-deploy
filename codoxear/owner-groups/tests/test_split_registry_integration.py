"""Fresh v3 and explicit legacy migration through the real read-only web reader."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fixture
import registry
from test_registry_cache import counted_loads

codec = fixture.registry_document
p = fixture.projection


class SplitRegistryIntegrationTests(unittest.TestCase):
    def test_fresh_v3_fixture_projects_current_without_full_history(self):
        with tempfile.TemporaryDirectory(prefix='owner-fresh-split-') as temp:
            root = Path(temp)
            state = fixture.build(root, storage_format=codec.SPLIT_FORMAT)
            document = root / 'RESPONSIBILITIES.md'
            parsed, era, metadata = codec.parse_store_document(document.read_text(encoding='utf-8'))
            self.assertEqual(parsed, state)
            self.assertEqual(metadata['format'], codec.SPLIT_FORMAT)
            self.assertEqual(set(state), {'version', 'revision', 'owners', 'assignments', 'coordination'})
            self.assertGreater(state['revision'], 0)
            with self.assertRaises(codec.RegistryError):
                codec.parse_document(document.read_text(encoding='utf-8'))
            with self.assertRaises(ValueError):
                p.project(state)
            expected = dict(p.project_current(state, codec), era=era)
            self.assertEqual(len(expected['owners']), 11)
            reader = p.RegistryReader(root, document, fixture.CODEC)
            with counted_loads() as cold:
                self.assertEqual(reader(), expected)
            self.assertEqual(cold, {'parse': 1, 'read': 1, 'load': 1})
            with counted_loads() as hot:
                self.assertEqual(reader(), expected)
            self.assertEqual(hot, {'parse': 0, 'read': 0, 'load': 0})
            # Valid current remains independently readable if private history is unavailable.
            with patch.object(registry.AuditStorage, 'read', side_effect=AssertionError('web audit read')):
                self.assertEqual(p.read_registry(root, document, fixture.CODEC), expected)

    def test_legacy_migration_invalidates_cache_without_changing_current_projection(self):
        with tempfile.TemporaryDirectory(prefix='owner-migrate-split-') as temp:
            root = Path(temp)
            full = fixture.build(root, storage_format=codec.FORMAT)
            document = root / 'RESPONSIBILITIES.md'
            raw = document.read_bytes()
            self.assertEqual(codec.parse_document(raw.decode('utf-8'))[0], full)
            self.assertEqual(len(full['history']), full['revision'])
            store = registry.Store(root, document)
            before = store.current_snapshot()
            reader = p.RegistryReader(root, document, fixture.CODEC)
            expected = dict(p.project(full), era=before['era'])
            self.assertEqual(reader(), expected)
            result = store.migrate_storage(before['document_token'], 'user',
                'Synthetic cross-surface migration', 'Synthetic explicit test authorization',
                operation_id='owner-reader-migration')
            self.assertEqual(result['outcome']['status'], 'COMMITTED')
            current = store.current_snapshot()
            self.assertEqual(current['format'], codec.SPLIT_FORMAT)
            self.assertEqual(current['era'], before['era'])
            self.assertEqual(current['state'], before['state'])
            self.assertEqual(store.history_snapshot(), full)
            self.assertEqual((store.audit.legacy / (result['transaction_id'] + '.md')).read_bytes(), raw)
            with counted_loads() as migrated:
                self.assertEqual(reader(), expected)
            self.assertEqual(migrated, {'parse': 1, 'read': 1, 'load': 1})
            with counted_loads() as hot:
                self.assertEqual(reader(), expected)
            self.assertEqual(hot, {'parse': 0, 'read': 0, 'load': 0})
            # The next real current-only publication also invalidates the same app cache.
            def change(state, era):
                state['owners']['ops']['role'] = 'Synthetic current role after migration'
                return {'updated': True}
            committed = store.transact_current('owner-reader-update', 'synthetic-role-intent', {},
                current['document_token'], change, action='update-owner', actor='ops',
                reason='Synthetic authorized role change', authority='Synthetic test authorization')
            self.assertEqual(committed['outcome']['status'], 'COMMITTED')
            after = copy.deepcopy(current['state'])
            after['revision'] += 1
            after['owners']['ops']['role'] = 'Synthetic current role after migration'
            with counted_loads() as changed:
                self.assertEqual(reader(), dict(p.project_current(after, codec), era=before['era']))
            self.assertEqual(changed, {'parse': 1, 'read': 1, 'load': 1})


if __name__ == '__main__':
    unittest.main()
