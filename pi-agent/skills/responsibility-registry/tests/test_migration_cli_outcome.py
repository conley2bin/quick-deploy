"""Migration execution qualifies historical receipts against temporary current only."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

from test_registry import registry, appointment
import registry_document as codec


class MigrationCliOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.document = self.root / 'current.md'
        self.store = registry.Store(self.root, self.document)
        state = registry.empty()
        registry.mutate(state, SimpleNamespace(command='register', actor='alpha'), appointment())
        self.era = str(uuid.uuid4())
        self.raw = codec.serialize_document(state, self.era).encode('utf-8')
        self.document.write_bytes(self.raw)
        (self.root / 'registry.lock').touch(mode=0o600)
        self.token = self.store.current_snapshot()['document_token']
        self.operation = 'synthetic-migration'
        self.reason = 'synthetic explicit migration'
        self.authority = 'synthetic user authority'

    def cli(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = registry.main(['--root', str(self.root), '--document', str(self.document),
                'migrate-storage', '--expected-document', self.token, '--operation-id', self.operation,
                '--actor', 'user', '--reason', self.reason, '--authority', self.authority])
        return code, out.getvalue(), err.getvalue()

    def files(self):
        return {str(path.relative_to(self.root)): (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
                for path in self.root.rglob('*') if path.is_file()}

    def fail_publication(self, phase):
        def crash(actual):
            if actual == phase:
                raise OSError('synthetic migration fault: ' + phase)
        with patch.object(registry.Store, 'transaction_checkpoint', side_effect=crash):
            code, output, error = self.cli()
        self.assertNotEqual(code, 0)
        self.assertEqual(output, '')
        return json.loads(error)

    def assert_rejected_unchanged(self, error_code, commit_state):
        before = self.files()
        code, output, error = self.cli()
        self.assertEqual(code, 2, error)
        self.assertEqual(output, '')
        failure = json.loads(error)
        self.assertEqual(failure['error'], error_code)
        self.assertEqual(failure['commit_state'], commit_state)
        self.assertEqual(failure['operation_id'], self.operation)
        self.assertFalse(failure['retryable'])
        self.assertEqual(self.files(), before)
        return failure

    def test_publication_unknown_exact_before_retry_is_not_success(self):
        self.assertEqual(self.fail_publication('current_replaced')['commit_state'], 'unknown')
        self.document.write_bytes(self.raw)
        recovered = self.store.recover_transaction('user', 'synthetic recovery', self.authority)
        self.assertEqual(recovered['outcome']['status'], 'PUBLICATION_UNKNOWN')
        digest = recovered['prepare']['intent_digest']
        before = self.files()
        historical = self.store.operation_status(self.operation, digest)
        self.assertTrue(historical['result']['migrated'])
        self.assertEqual(historical['current']['format'], codec.FORMAT)
        self.assertEqual(self.files(), before)
        self.assert_rejected_unchanged('commit_unknown', 'unknown')
        self.assertEqual(self.document.read_bytes(), self.raw)

    def test_pending_and_aborted_retry_do_not_report_success(self):
        self.assertEqual(self.fail_publication('current_temp_synced')['commit_state'], 'not_committed')
        self.assert_rejected_unchanged('transaction_pending', 'unknown')
        # Recovery cannot prove an unchanged current was never published. Seed
        # a known historical ABORTED receipt in this isolated fixture instead.
        prepare = self.store.audit.pending()['prepare']
        outcome = self.store._outcome(prepare, 'ABORTED', 'synthetic_prepublication_abort',
                                      self.store.current_snapshot())
        self.store.audit.finish(prepare, outcome)
        self.assert_rejected_unchanged('migration_not_committed', 'not_committed')

    def test_committed_receipt_with_replaced_current_is_historical_only(self):
        code, output, error = self.cli()
        self.assertEqual(code, 0, error)
        receipt = json.loads(output)
        state = self.store.current_snapshot()['state']
        replacements = (self.raw, codec.serialize_current_document(state, str(uuid.uuid4()),
                        receipt['transaction_id']).encode('utf-8'), b'invalid synthetic current\n', None)
        for replacement in replacements:
            with self.subTest(current_kind='missing' if replacement is None else replacement[:80]):
                if replacement is None:
                    self.document.unlink()
                else:
                    self.document.write_bytes(replacement)
                failure = self.assert_rejected_unchanged('migration_historical', 'committed')
                self.assertIn('historical', failure['message'])
        self.assertEqual(self.store.operation_status(self.operation,
                         receipt['prepare']['intent_digest'])['outcome']['status'], 'COMMITTED')

    def test_first_commit_retry_and_later_managed_change_use_fresh_current(self):
        code, output, error = self.cli()
        self.assertEqual(code, 0, error)
        first = json.loads(output)
        self.assertEqual(first['outcome']['status'], 'COMMITTED')
        self.assertEqual(first['current']['format'], codec.SPLIT_FORMAT)
        archived = self.store.audit.legacy / (first['transaction_id'] + '.md')
        self.assertEqual(archived.read_bytes(), self.raw)
        def change(state, era):
            return registry.mutate_records(state, SimpleNamespace(command='register', actor='beta'),
                                           appointment('beta', 'second', path='/example/beta'))
        for mutate in (False, True):
            with self.subTest(managed_change=mutate):
                if mutate:
                    current = self.store.current_snapshot()
                    self.store.transact_current('later-managed', 'synthetic-digest', {},
                        current['document_token'], change, action='register', actor='beta',
                        reason='synthetic second appointment', authority=self.authority)
                before = self.files()
                code, output, error = self.cli()
                self.assertEqual(code, 0, error)
                retry = json.loads(output)
                self.assertEqual(retry['qualification'], 'already-committed-current-era')
                self.assertEqual(retry['current'], self.store.current_snapshot())
                self.assertEqual(retry['outcome'], first['outcome'])
                self.assertEqual(retry['prepare'], first['prepare'])
                self.assertEqual(self.files(), before)
                if mutate:
                    self.assertNotEqual(retry['current']['document_token'],
                                        first['outcome']['document_token_after'])


if __name__ == '__main__':
    unittest.main()
