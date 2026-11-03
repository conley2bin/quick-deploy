"""Permission ordering against the frozen API double, not real Store evidence."""
import concurrent.futures
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from test_artifacts import context, io
from succession_mock import Service

s = importlib.import_module('handoff_test.succession')


class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'operation'
        self.directory.mkdir(mode=0o700)
        self.op = str(uuid4())
        self.service = Service()
        self.adapter = s.Adapter(self.service)
        source = Path(self.temp.name) / 'source.jsonl'
        source.write_text(json.dumps(dict(type='session', version=3, id='actual-source', cwd=self.temp.name)) + '\n')
        snapshot = context.create_source_snapshot(source)
        self.manifest = dict(version=1, operation_id=self.op, source_sha256=snapshot['sha256'], note='',
            source=dict(session_id='source-route', pi_session_id='actual-source', cwd=self.temp.name, snapshot=snapshot),
            paths=dict(operation_dir=str(self.directory), source_context=str(self.directory / 'source-context.md'),
                       **{k: str(self.directory / (k + '.json')) for k in context.KINDS}))
        self.manifest['succession'] = s.prepare_source(self.directory, self.op, 'actual-source', self.adapter)
        context.prepare_artifacts(self.directory, self.manifest)
        self.target = Path(self.temp.name) / 'filename-is-not-id.jsonl'
        self.target.write_text(json.dumps(dict(type='session', version=3, id='actual-target', cwd=self.temp.name)) + '\n')
        info = self.target.stat()
        s.bind_target(self.directory, self.manifest, dict(session_id='target-route', launch_id='launch',
            broker_pid=123, spawn_nonce='nonce', cwd=self.temp.name),
            dict(log_path=str(self.target), pi_session_id='actual-target', device=info.st_dev, inode=info.st_ino))
        private_source = json.loads((self.directory / 'succession-source.json').read_text())
        identity = dict(version=1, operation_id=self.op, challenge=private_source['challenge'],
            pi_session_id='actual-target', session_file=str(self.target), intercom_id=None,
            contact_source='unavailable', unknown_reason='actual Intercom status tool unavailable', source='current-session')
        self.identity_draft = self.directory / 'identity-draft.json'
        self.identity_draft.write_text(json.dumps(identity))
        io.attest(self.directory / 'manifest.json', 'identity', self.identity_draft)
        self.body = self.directory / 'body.md'
        self.body.write_text('Current evidence and verified goal; next action Y.')
        self.u = self.submit('understanding')
        custody = dict(operation_id=self.op, understanding_sha256=self.u['_sha256'],
                       custody=s.unknown_custody('actual-source'))
        draft = self.directory / 'custody-draft.json'
        draft.write_text(json.dumps(custody))
        io.attest(self.directory / 'manifest.json', 'custody', draft)
        self.r = self.submit('review', verdict='clear')
        self.intent = s.freeze_intent(self.directory, self.manifest, self.u['_sha256'], self.r['_sha256'], self.adapter)

    def submit(self, kind, **kwargs):
        return io.submit(self.directory / 'manifest.json', kind, self.body, service=self.service, **kwargs)

    def test_commit_ready_success_then_action_and_duplicate_no_new_permission(self):
        trace = []
        original = context._publish_at
        def publish(fd, name, raw):
            if name == 'resolution.json':
                self.assertEqual(self.service.events, 1)
                trace.append('ready-publish')
            return original(fd, name, raw)
        with patch.object(context, '_publish_at', publish):
            self.submit('resolution', verdict='ready')
            trace.extend(['CLI-0', 'toy-action'])
        self.assertEqual(self.service.calls, ['prepare', 'prepare', 'commit'])
        self.assertEqual(trace, ['ready-publish', 'CLI-0', 'toy-action'])
        calls = list(self.service.calls)
        with self.assertRaises(context.ContextError):
            self.submit('resolution', verdict='ready')
        self.assertEqual(calls, self.service.calls)
        self.assertEqual(self.service.events, 1)

    def test_conflict_never_calls_commit(self):
        self.submit('resolution', verdict='conflict')
        self.assertEqual(self.service.calls, ['prepare'])
        self.assertFalse((self.directory / 'succession-attempt.json').exists())

    def test_commit_then_publish_failure_get_recovers_without_second_event(self):
        original = context._publish_at
        def fail(fd, name, raw):
            if name == 'resolution.json':
                raise OSError('publish failed')
            return original(fd, name, raw)
        with patch.object(context, '_publish_at', fail), self.assertRaises(context.ContextError):
            self.submit('resolution', verdict='ready')
        self.assertEqual(self.service.events, 1)
        self.assertFalse((self.directory / 'resolution.json').exists())
        self.submit('resolution', verdict='ready')
        self.assertEqual(self.service.calls, ['prepare', 'prepare', 'commit', 'get'])
        self.assertEqual(self.service.events, 1)

    def test_unknown_commit_get_first_not_found_does_not_recommit(self):
        original = self.service.commit_owner_session_succession
        def lost(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('private secret transport path')
        self.service.commit_owner_session_succession = lost
        with self.assertRaisesRegex(s.GateError, 'outcome_unknown'):
            self.submit('resolution', verdict='ready')
        self.service.receipts.clear()  # not_found is NOT proof that the unknown call had no effects.
        with self.assertRaisesRegex(s.GateError, 'not_found'):
            self.submit('resolution', verdict='ready')
        self.assertEqual(self.service.events, 1)
        self.assertEqual(self.service.calls[-1], 'get')
        self.assertFalse((self.directory / 'resolution.json').exists())

    def test_superseded_receipt_blocks_recovery_and_host_validation(self):
        original = context._publish_at
        def fail(fd, name, raw):
            if name == 'resolution.json':
                raise OSError('publication failure')
            return original(fd, name, raw)
        with patch.object(context, '_publish_at', fail), self.assertRaises(context.ContextError):
            self.submit('resolution', verdict='ready')
        self.service.valid = False
        with self.assertRaisesRegex(s.GateError, 'superseded'):
            self.submit('resolution', verdict='ready')
        with self.assertRaisesRegex(s.GateError, 'superseded'):
            s.validate_ready(self.directory, self.manifest, self.u['_sha256'], self.r['_sha256'], self.adapter)
        self.assertFalse((self.directory / 'resolution.json').exists())

    def test_no_owner_no_event_or_revision_and_fresh_absence_check(self):
        # Independent fixture starts no-owner, rather than mutating a frozen owner plan.
        self.service.owner = None
        for filename in ('succession-source.json', 'succession-intent.json'):
            (self.directory / filename).unlink()
        marker = s.prepare_source(self.directory, self.op, 'actual-source', self.adapter)
        self.manifest['succession'] = marker
        source = json.loads((self.directory / 'succession-source.json').read_text())
        report = json.loads((self.directory / 'succession-identity.json').read_text())
        report['challenge'] = source['challenge']
        (self.directory / 'succession-identity.json').write_bytes(context._encode(report))
        proof = json.loads((self.directory / 'succession-target.json').read_text())
        proof['challenge_sha256'] = marker['challenge_sha256']
        (self.directory / 'succession-target.json').write_bytes(context._encode(proof))
        (self.directory / 'manifest.json').write_bytes(context._encode(self.manifest))
        s.freeze_intent(self.directory, self.manifest, self.u['_sha256'], self.r['_sha256'], self.adapter)
        self.submit('resolution', verdict='ready')
        self.assertEqual(self.service.events, 0)
        self.assertEqual(self.service.revision, 7)
        self.assertEqual(json.loads((self.directory / 'succession-receipt.json').read_text())['status'], 'not_applicable')

    def test_refresh_preserves_digest_latest_revision_and_bounded_conflict_retry(self):
        digest = self.intent['intent_digest']
        self.service.revision = 99
        self.service.commit_error = dict(code='document_conflict', message='fresh document needed', retryable=True, commit_state='not_committed')
        self.submit('resolution', verdict='ready')
        self.assertEqual(self.service.calls, ['prepare', 'prepare', 'commit', 'prepare', 'commit'])
        receipt = json.loads((self.directory / 'succession-receipt.json').read_text())
        self.assertEqual(receipt['intent_digest'], digest)
        self.assertEqual(receipt['receipt']['revision_before'], 99)
        self.assertEqual(json.loads((self.directory / 'succession-intent.json').read_text()), self.intent)

    def test_changed_source_or_reset_blocks_before_commit(self):
        for key in ('owner',):
            self.service.owner = 'changed-owner'
            with self.assertRaisesRegex(s.GateError, 'source_changed'):
                self.submit('resolution', verdict='ready')
        self.assertNotIn('commit', self.service.calls)

    def test_occupied_needs_recovery_and_nonretryable_conflicts_block_ready(self):
        self.service.commit_error = dict(code='target_identity_occupied', message='occupied', retryable=False, commit_state='not_committed')
        with self.assertRaisesRegex(s.GateError, 'conflict'):
            self.submit('resolution', verdict='ready')
        self.assertEqual(self.service.events, 0)
        self.assertFalse((self.directory / 'resolution.json').exists())

    def test_digest_mutation_header_replacement_and_identity_report_mismatch(self):
        intent = dict(self.intent)
        intent['intent_digest'] = '0' * 64
        (self.directory / 'succession-intent.json').write_bytes(context._encode(intent))
        with self.assertRaisesRegex(s.GateError, 'digest_changed'):
            self.submit('resolution', verdict='ready')
        self.assertNotIn('commit', self.service.calls)
        (self.directory / 'succession-intent.json').write_bytes(context._encode(self.intent))
        self.target.write_text(json.dumps(dict(type='session', id='different-target')) + '\n')
        with self.assertRaisesRegex(s.GateError, 'target_changed'):
            self.submit('resolution', verdict='ready')

    def test_concurrent_helpers_only_one_permission_one_event(self):
        def attempt(_):
            try:
                return self.submit('resolution', verdict='ready')
            except context.ContextError:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(attempt, range(5)))
        self.assertEqual(sum(item is not None for item in results), 1)
        self.assertEqual(self.service.events, 1)
        self.assertEqual(self.service.calls.count('commit'), 1)

    def test_strict_envelope_missing_api_and_no_raw_secret_in_receipt(self):
        with self.assertRaises(s.GateError):
            s.Adapter(object()).prepare(self.op, 'source')
        original = self.service.prepare_owner_session_succession
        self.service.prepare_owner_session_succession = lambda *a, **k: {**original(*a, **k), 'extra': True}
        with self.assertRaises(s.GateError):
            self.adapter.prepare(self.op, 'actual-source')
        self.service.prepare_owner_session_succession = original
        self.submit('resolution', verdict='ready')
        receipt = (self.directory / 'succession-receipt.json').read_text()
        challenge = json.loads((self.directory / 'succession-source.json').read_text())['challenge']
        self.assertNotIn(challenge, receipt)
        self.assertNotIn('active_runs', receipt)
        self.assertEqual(self.intent['custody']['status'], 'unknown')
        self.assertIsNone(self.intent['target']['identity']['intercom_id'])


if __name__ == '__main__':
    unittest.main()
