"""Permission ordering against the frozen API double, not real Store evidence."""
import concurrent.futures
import copy
import importlib
import json
import io as stdio
from contextlib import redirect_stdout, redirect_stderr
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

    def test_null_token_committed_receipt_allows_ready_without_second_permission(self):
        self.service.null_document_token_after = True
        self.submit('resolution', verdict='ready')
        receipt = json.loads((self.directory / 'succession-receipt.json').read_text())
        self.assertEqual(receipt['status'], 'committed')
        self.assertTrue(receipt['current_binding_valid'])
        self.assertIsNone(receipt['receipt']['document_token_after'])
        self.assertTrue((self.directory / 'resolution.json').exists())
        self.assertEqual(self.service.calls, ['prepare', 'prepare', 'commit'])
        self.assertEqual(self.service.events, 1)
        s.validate_ready(self.directory, self.manifest, self.u['_sha256'], self.r['_sha256'], self.adapter)
        calls = list(self.service.calls)
        with self.assertRaises(context.ContextError):
            self.submit('resolution', verdict='ready')
        self.assertEqual(self.service.calls, calls)
        self.assertEqual(self.service.events, 1)

    def test_null_token_publish_failure_get_first_recovers_one_event(self):
        self.service.null_document_token_after = True
        original = context._publish_at
        def fail(fd, name, raw):
            if name == 'resolution.json':
                raise OSError('publication failure')
            return original(fd, name, raw)
        with patch.object(context, '_publish_at', fail), self.assertRaises(context.ContextError):
            self.submit('resolution', verdict='ready')
        receipt = json.loads((self.directory / 'succession-receipt.json').read_text())
        self.assertEqual(receipt['status'], 'committed')
        self.assertIsNone(receipt['receipt']['document_token_after'])
        self.assertEqual(self.service.events, 1)
        self.assertFalse((self.directory / 'resolution.json').exists())
        self.submit('resolution', verdict='ready')
        self.assertEqual(json.loads((self.directory / 'succession-receipt.json').read_text()), receipt)
        self.assertEqual(self.service.calls, ['prepare', 'prepare', 'commit', 'get'])
        self.assertEqual(self.service.events, 1)
        self.assertTrue((self.directory / 'resolution.json').exists())
        self.assertEqual(json.loads((self.directory / 'succession-intent.json').read_text()), self.intent)
        fresh = s.validate_ready(self.directory, self.manifest, self.u['_sha256'], self.r['_sha256'], self.adapter)
        self.assertEqual(fresh['status'], 'already_committed')
        self.assertTrue(fresh['current_binding_valid'])
        self.assertIsNone(fresh['receipt']['document_token_after'])

    def test_null_token_lost_response_get_first_recovers_original_intent(self):
        self.service.null_document_token_after = True
        original = self.service.commit_owner_session_succession
        def lost(*args, **kwargs):
            original(*args, **kwargs)
            self.service.owner = None  # A fresh prepare must not replace the original owner plan.
            raise OSError('response lost after durable transaction')
        self.service.commit_owner_session_succession = lost
        with self.assertRaisesRegex(s.GateError, 'outcome_unknown'):
            self.submit('resolution', verdict='ready')
        self.assertEqual(self.service.events, 1)
        self.assertFalse((self.directory / 'resolution.json').exists())
        self.submit('resolution', verdict='ready')
        receipt = json.loads((self.directory / 'succession-receipt.json').read_text())
        self.assertEqual(receipt['status'], 'already_committed')
        self.assertIsNone(receipt['receipt']['document_token_after'])
        self.assertEqual(self.service.calls, ['prepare', 'prepare', 'commit', 'get'])
        self.assertEqual(self.service.events, 1)
        self.assertTrue((self.directory / 'resolution.json').exists())
        self.assertEqual(json.loads((self.directory / 'succession-intent.json').read_text()), self.intent)

    def test_nullable_token_does_not_relax_flags_correlation_or_other_receipt_fields(self):
        self.service.null_document_token_after = True
        intent = self.intent
        value = self.service.commit_owner_session_succession(intent['plan'], intent['target'],
            expected_document=intent['plan']['document_token'], authority=intent['authority'], custody=intent['custody'])
        self.adapter.validate_response('commit_owner_session_succession', self.op, value,
                                       digest=intent['intent_digest'])
        for field, invalid in (
            ('document_token_after', ['', ' ', 0, 1, False, True, {}, []]),
            ('store_id', [None, '', False]),
            ('operation_id', [str(uuid4())]),
            ('intent_digest', ['0' * 64]),
            ('outcome', ['unknown']),
        ):
            for item in invalid:
                with self.subTest(receipt_field=field, value=item):
                    broken = copy.deepcopy(value)
                    broken['receipt'][field] = item
                    with self.assertRaises(s.GateError):
                        self.adapter.validate_response('commit_owner_session_succession', self.op, broken,
                                                       digest=intent['intent_digest'])
        for field, invalid in (
            ('current_binding_valid', [False, None, 1, 'true']),
            ('no_owner_valid', [True, None, 0]),
            ('operation_id', [str(uuid4())]),
            ('intent_digest', ['0' * 64]),
            ('status', ['unknown']),
        ):
            for item in invalid:
                with self.subTest(envelope_field=field, value=item):
                    broken = copy.deepcopy(value)
                    broken[field] = item
                    with self.assertRaises(s.GateError):
                        self.adapter.validate_response('commit_owner_session_succession', self.op, broken,
                                                       digest=intent['intent_digest'])
        broken = copy.deepcopy(value)
        broken['receipt']['extra'] = 'not allowed'
        with self.assertRaises(s.GateError):
            self.adapter.validate_response('commit_owner_session_succession', self.op, broken,
                                           digest=intent['intent_digest'])
        self.assertEqual(self.service.events, 1)
        self.assertFalse((self.directory / 'resolution.json').exists())

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

    def test_lost_commit_response_recovers_exact_receipt_without_reprepare(self):
        original = self.service.commit_owner_session_succession
        def lost(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('response lost after durable transaction')
        self.service.commit_owner_session_succession = lost
        with self.assertRaisesRegex(s.GateError, 'outcome_unknown'):
            self.submit('resolution', verdict='ready')
        self.assertFalse((self.directory / 'resolution.json').exists())
        self.assertFalse((self.directory / 'succession-receipt.json').exists())
        self.submit('resolution', verdict='ready')
        self.assertEqual(self.service.events, 1)
        self.assertEqual(self.service.calls, ['prepare', 'prepare', 'commit', 'get'])
        self.assertTrue((self.directory / 'resolution.json').exists())

    def test_superseded_null_token_blocks_recovery_and_host_validation(self):
        self.service.null_document_token_after = True
        original = context._publish_at
        def fail(fd, name, raw):
            if name == 'resolution.json':
                raise OSError('publication failure')
            return original(fd, name, raw)
        with patch.object(context, '_publish_at', fail), self.assertRaises(context.ContextError):
            self.submit('resolution', verdict='ready')
        self.service.valid = False
        receipt = self.adapter.get(self.op, self.intent['intent_digest'])
        self.assertEqual(receipt['status'], 'superseded')
        self.assertFalse(receipt['current_binding_valid'])
        self.assertFalse(receipt['no_owner_valid'])
        self.assertIsNone(receipt['receipt']['document_token_after'])
        with self.assertRaisesRegex(s.GateError, 'superseded'):
            self.submit('resolution', verdict='ready')
        with self.assertRaisesRegex(s.GateError, 'superseded'):
            s.validate_ready(self.directory, self.manifest, self.u['_sha256'], self.r['_sha256'], self.adapter)
        self.assertFalse((self.directory / 'resolution.json').exists())
        self.assertEqual(self.service.events, 1)

    def test_no_owner_null_token_no_event_or_revision_and_fresh_absence_check(self):
        # Independent fixture starts no-owner, rather than mutating a frozen owner plan.
        self.service.owner = None
        self.service.null_document_token_after = True
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
        receipt = json.loads((self.directory / 'succession-receipt.json').read_text())
        self.assertEqual(receipt['status'], 'not_applicable')
        self.assertTrue(receipt['no_owner_valid'])
        self.assertFalse(receipt['current_binding_valid'])
        self.assertIsNone(receipt['receipt']['document_token_after'])
        self.assertTrue((self.directory / 'resolution.json').exists())
        s.validate_ready(self.directory, self.manifest, self.u['_sha256'], self.r['_sha256'], self.adapter)

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

    def test_actual_cli_return_zero_precedes_toy_action(self):
        stdout = stdio.StringIO()
        argv = ['submit', '--manifest', str(self.directory / 'manifest.json'), '--kind', 'resolution',
                '--body-file', str(self.body), '--verdict', 'ready']
        with patch.object(s.Adapter, 'load', return_value=self.service), redirect_stdout(stdout):
            code = io.main(argv)
        self.assertEqual(code, 0)
        self.assertEqual(self.service.events, 1)
        self.assertTrue((self.directory / 'resolution.json').exists())
        action = self.directory / 'toy-action.txt'
        if code == 0:
            action.write_text('Y')
        self.assertEqual(action.read_text(), 'Y')
        with patch.object(s.Adapter, 'load', return_value=self.service), redirect_stderr(stdio.StringIO()):
            self.assertEqual(io.main(argv), 2)
        self.assertEqual(self.service.events, 1)

    def test_needs_recovery_never_allows_ready(self):
        def pending(plan, target, *, expected_document, authority, custody, store=None):
            digest = self.service.build_intent_digest(plan, target, authority, custody)
            return self.service.envelope(self.op, 'needs_recovery', digest=digest,
                error=dict(code='transaction_pending', message='pending', retryable=False, commit_state='unknown'))
        self.service.commit_owner_session_succession = pending
        with self.assertRaisesRegex(s.GateError, 'needs_recovery'):
            self.submit('resolution', verdict='ready')
        self.assertFalse((self.directory / 'resolution.json').exists())
        self.assertEqual(self.service.events, 0)

    def test_era_reset_prevents_commit(self):
        prepare = self.service.prepare_owner_session_succession
        def reset(*args, **kwargs):
            value = prepare(*args, **kwargs)
            value['plan']['era'] = 'reset-era'
            return value
        self.service.prepare_owner_session_succession = reset
        with self.assertRaisesRegex(s.GateError, 'source_changed'):
            self.submit('resolution', verdict='ready')
        self.assertNotIn('commit', self.service.calls)

    def test_contact_from_connected_status_and_wrong_nonce_rejected(self):
        report = json.loads((self.directory / 'succession-identity.json').read_text())
        report.update(intercom_id='actual-connected-contact', contact_source='target-intercom-status', unknown_reason='')
        (self.directory / 'succession-identity.json').unlink()
        self.identity_draft.write_text(json.dumps(report))
        io.attest(self.directory / 'manifest.json', 'identity', self.identity_draft)
        (self.directory / 'succession-intent.json').unlink()
        intent = s.freeze_intent(self.directory, self.manifest, self.u['_sha256'], self.r['_sha256'], self.adapter)
        self.assertEqual(intent['target']['identity']['intercom_id'], 'actual-connected-contact')
        report['challenge'] = 'incorrect-private-nonce'
        (self.directory / 'succession-identity.json').write_bytes(context._encode(report))
        with self.assertRaises(s.GateError):
            self.submit('resolution', verdict='ready')
        self.assertNotIn('commit', self.service.calls)

    def test_invalid_receipt_capsule_blocks_host_ready(self):
        self.submit('resolution', verdict='ready')
        path = self.directory / 'succession-receipt.json'
        receipt = json.loads(path.read_text())
        receipt['unexpected'] = 'invalid capsule'
        path.write_bytes(context._encode(receipt))
        with self.assertRaises(s.GateError):
            s.validate_ready(self.directory, self.manifest, self.u['_sha256'], self.r['_sha256'], self.adapter)

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
