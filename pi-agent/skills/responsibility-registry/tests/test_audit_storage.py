"""Immutable audit IO fault tests, using only private temporary stores."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_storage import AuditStorage, encoded, sha, AuditDamaged
from registry_document import RegistryError


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.audit = AuditStorage(self.root)
        self.audit.directories(create=True)
        self.op = str(uuid.uuid4())

    def prepare(self):
        return dict(schema=1, op_id=self.op, operation_id='external', intent_digest='digest', intent={}, previous=None,
                    at='2026-01-01T00:00:00+00:00', actor='user', authority='synthetic', action='test', reason='test',
                    base=dict(document_sha256='a' * 64, era='era', revision=0, document_token='observed-token', audit_head=None),
                    after=dict(document_sha256='b' * 64, era='era', revision=1, audit_head=self.op), event=None,
                    continuity=dict(kind='unanchored', previous_committed_sha256=None), references={}, disposition='publication', result={})

    def outcome(self, prepare):
        return dict(schema=1, op_id=self.op, intent_digest='digest', prepare_sha256=sha(encoded(prepare)),
                    status='COMMITTED', basis='test', recorded_at='2026-01-01T00:00:00+00:00',
                    observed=dict(document_sha256='b' * 64, era='era', revision=1, current_valid=True),
                    document_token_after='observed-token', commit=dict(era='era', revision=1, after_sha256='b' * 64))

    def test_complete_immutable_finals_and_idempotent_finish(self):
        prepare = self.prepare()
        self.audit.begin(prepare)
        self.audit.finish(prepare, self.outcome(prepare))
        self.assertIsNone(self.audit.pending())
        self.assertEqual(self.audit.head()['last_committed']['op_id'], self.op)
        self.audit.finish(prepare, self.outcome(prepare))
        for path in (self.audit.path, self.audit.ops, self.audit.legacy):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.audit.prepare_path(self.op).stat().st_mode & 0o777, 0o600)

    def test_partial_temp_never_published_final(self):
        def crash(phase):
            if phase == 'prepare_temp_synced':
                self.assertFalse(self.audit.prepare_path(self.op).exists())
                raise RuntimeError('cut')
        self.audit.checkpoint = crash
        with self.assertRaises(RuntimeError):
            self.audit.begin(self.prepare())
        self.assertEqual(self.audit.pending()['prepare'], self.prepare())
        self.assertFalse(self.audit.prepare_path(self.op).exists())

    def test_no_overwrite_corrupt_final_evidence(self):
        path = self.audit.prepare_path(self.op)
        path.write_bytes(b'partial')
        path.chmod(0o600)
        with self.assertRaises(RegistryError):
            self.audit.begin(self.prepare())
        self.assertEqual(path.read_bytes(), b'partial')
        self.assertIsNotNone(self.audit.pending())
        with self.assertRaises(AuditDamaged):
            self.audit.lookup(self.op)

    def test_symlink_and_permissions_refused(self):
        target = self.root / 'outside'
        target.write_text('sentinel')
        self.audit.prepare_path(self.op).symlink_to(target)
        with self.assertRaises(RegistryError):
            self.audit.begin(self.prepare())
        self.assertEqual(target.read_text(), 'sentinel')
        self.audit.prepare_path(self.op).unlink()
        self.audit.ops.chmod(0o755)
        with self.assertRaises(RegistryError):
            self.audit.directories()

    def test_immutable_publishes_using_exclusive_link(self):
        original = __import__('os').link
        seen = []
        def link(source, target, **kwargs):
            seen.append(json.loads(Path(source).read_text()))
            self.assertFalse(Path(target).exists())
            return original(source, target, **kwargs)
        with patch('audit_storage.os.link', side_effect=link):
            self.audit.begin(self.prepare())
        self.assertEqual(seen, [self.prepare()])

    def test_all_metadata_publication_boundaries_retain_pending(self):
        phases = ('pending_temp_synced', 'pending_published', 'pending_synced',
                  'prepare_temp_synced', 'prepare_published', 'prepare_synced',
                  'outcome_temp_synced', 'outcome_published', 'outcome_synced',
                  'head_temp_synced', 'head_published', 'head_synced',
                  'pending_before_unlink', 'pending_unlinked', 'pending_cleanup_synced')
        for phase in phases:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                audit = AuditStorage(directory)
                audit.directories(create=True)
                def crash(actual):
                    if actual == phase:
                        raise RuntimeError(actual)
                audit.checkpoint = crash
                prepare = self.prepare()
                with self.assertRaises(RuntimeError):
                    audit.begin(prepare)
                    audit.finish(prepare, self.outcome(prepare))
                # Every final name that exists is complete JSON, never an exposed partial.
                for path in (audit.pending_path, audit.head_path, audit.prepare_path(self.op), audit.outcome_path(self.op)):
                    if path.exists():
                        self.assertEqual(encoded(json.loads(path.read_bytes())), path.read_bytes())
                if phase not in ('pending_temp_synced', 'pending_unlinked', 'pending_cleanup_synced'):
                    self.assertIsNotNone(audit.pending())


if __name__ == '__main__':
    unittest.main()
