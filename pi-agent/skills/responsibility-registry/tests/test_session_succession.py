"""Synthetic service policy tests; ProtocolStore is NOT transaction durability evidence."""
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import uuid

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/session_succession.py'
SPEC = importlib.util.spec_from_file_location('_test_succession_api', SCRIPT)
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)
ERA = '10000000-0000-4000-8000-000000000001'
OP = '20000000-0000-4000-8000-000000000002'
STAMP = '2026-01-01T00:00:00+00:00'
ENVELOPE = {'schema', 'status', 'operation_id', 'intent_digest', 'plan', 'receipt',
            'current_binding_valid', 'no_owner_valid', 'error'}
RECEIPT = {'version', 'operation_id', 'intent_digest', 'store_id', 'era', 'owner_id',
           'source_pi_session_id', 'target_pi_session_id', 'outcome', 'revision_before',
           'revision_after', 'transaction_id', 'event_id', 'document_token_after'}


def identity(pi, contact='synthetic-contact'):
    return dict(pi_session_id=pi, intercom_id=contact, source='current-session',
                unknown_reason='' if contact is not None else 'No connected target contact was available.')


def recovery():
    return dict(session_file='/synthetic/target-session', cwd='/synthetic/worktree',
                repo='/synthetic/repo', worktree='/synthetic/worktree', summary='Target owns the synthetic continuation.',
                decisions='Keep the accepted synthetic goal.', pending_issues='Synthetic review remains pending.',
                context_refs=['/synthetic/task/PROMPT.md'], source='Synthetic selected header and target report.', unknown_reason='')


def target(op=OP, pi='opaque-target-Pi'):
    return dict(identity=identity(pi), recovery=recovery(), evidence=dict(operation_id=op, challenge_sha256='a' * 64,
                broker_session_id='synthetic-broker-route', launch_binding='synthetic-launch-window',
                session_header_sha256='b' * 64, verification_source='codoxear-bound-target/header+challenged-report-v1',
                contact_source='target-intercom-status'))


def authority(owner='alpha'):
    return dict(issuer='owner:' + owner if owner else 'user', reason='Authorized synthetic continuation.',
                evidence='Synthetic explicit source-owner authorization.')


def custody(source='opaque-source-Pi'):
    return dict(status='unknown', source_supervisor_pi_session_id=source, active_runs=[],
                evidence='Synthetic caller cannot enumerate native children.', successor_result_delivery='Old supervisor remains bounded relay; unresolved runs are not transferred.',
                unknown_reason='Native child custody is not observable from registry state.')


def state():
    owners = {}
    for oid, parent, pi in [('alpha', 'owner:superior', 'opaque-source-Pi'),
                            ('superior', 'user', 'synthetic-superior-Pi'),
                            ('child', 'owner:alpha', 'synthetic-child-Pi')]:
        owners[oid] = dict(id=oid, name='Synthetic ' + oid, role='Synthetic test owner', parent=parent,
                           identity=identity(pi), recovery=recovery(), created_at=STAMP, updated_at=STAMP,
                           appointment_source={'issuer': 'user', 'source': 'Synthetic appointment.'})
    assignments = {}
    for aid, oid, status in [('active', 'alpha', 'active'), ('completed', 'alpha', 'ended'),
                              ('child-task', 'child', 'active')]:
        assignments[aid] = dict(id=aid, owner=oid, project={'id': 'synthetic-project', 'name': 'Synthetic project', 'path': '/synthetic'},
                                repo={'id': 'synthetic-repo', 'path': '/synthetic/.git'}, task=aid,
                                objective='Keep latest accepted objective.', acceptance='Synthetic accepted outcome.',
                                depends_on=[{'kind': 'task', 'project': 'upstream-synthetic', 'task': 'build'}],
                                goal_source={'issuer': 'user', 'source': 'Synthetic user goal.'}, status=status,
                                appointment='Synthetic assignment.', created_at=STAMP, updated_at=STAMP,
                                end_reason='Synthetic accepted completion.' if status == 'ended' else None, successor=None)
    coordination = dict(id='agreement', repo=assignments['active']['repo'],
                        scopes={'active': {'owner': 'alpha', 'write_scope': ['synthetic/**'], 'assignment_updated_at': STAMP},
                                'child-task': {'owner': 'child', 'write_scope': ['other/**'], 'assignment_updated_at': STAMP}},
                        shared_interfaces='Synthetic schema only.', workspace={'mode': 'isolated-worktrees', 'plan': 'Separate synthetic paths.'},
                        sequencing='Review then integrate.', integration_owner='alpha', acceptance='Synthetic accepted tests.', status='confirmed',
                        acknowledgements={oid: {'at': STAMP, 'evidence': 'Synthetic explicit acknowledgement.'} for oid in ('alpha', 'child')},
                        created_at=STAMP, updated_at=STAMP)
    result = dict(version=1, revision=7, owners=owners, assignments=assignments, coordination={'agreement': coordination})
    api._codec.validate_current_envelope(result, ERA)
    return result


class StoreError(Exception):
    def __init__(self, code, commit_state='not_committed', retryable=False):
        super().__init__('Untrusted internal path /private/example MUST NOT appear in response.')
        self.code, self.commit_state, self.retryable = code, commit_state, retryable


class ProtocolStore:
    """In-memory protocol/policy double: no lock, filesystem, fsync or recovery claims."""
    def __init__(self, initial=None):
        self.state = copy.deepcopy(state() if initial is None else initial)
        self.era, self.format, self.store_id = ERA, 'responsibility-registry/v3', 'synthetic-store'
        self.audit_head, self.generation = None, 0
        self.records, self.events, self.calls = {}, [], []

    def current_snapshot(self):
        self.calls.append('current_snapshot')
        return copy.deepcopy(dict(state=self.state, era=self.era, document_token='synthetic-token-' + str(self.generation),
                                  format=self.format, audit_head=self.audit_head, store_id=self.store_id))

    def operation_status(self, op, digest):
        self.calls.append('operation_status')
        if op in self.records:
            if self.records[op]['prepare']['intent_digest'] != digest:
                raise StoreError('operation_intent_mismatch')
            record = copy.deepcopy(self.records[op])
            record['current'] = self.current_snapshot()
            return record
        return dict(transaction_id=str(uuid.uuid5(uuid.NAMESPACE_URL, op)), prepare=None, outcome=None,
                    result=None, current=self.current_snapshot(), pending=False)

    def transact_current(self, op, digest, intent, expected_document, change, **meta):
        self.calls.append('transact_current')
        prior = self.operation_status(op, digest)
        if prior['prepare'] is not None:
            return prior
        before = self.current_snapshot()
        if before['document_token'] != expected_document:
            raise StoreError('document_conflict', retryable=True)
        changed = copy.deepcopy(self.state)
        result = change(changed, self.era)
        api._codec.validate_current_envelope(changed, self.era)
        event = None
        transaction_id = prior['transaction_id']
        if changed != self.state:
            changed['revision'] += 1
            event = dict(id=f'event-{changed["revision"]:08d}', revision=changed['revision'], **meta,
                         before=copy.deepcopy(self.state['owners']), after=copy.deepcopy(changed['owners']))
            self.events.append(event)
            self.state, self.audit_head = changed, transaction_id
            self.generation += 1
        elif not meta['persist_noop']:
            raise AssertionError('Succession must not claim unchanged source as success.')
        after = self.current_snapshot()
        record = dict(transaction_id=transaction_id, prepare=dict(intent=copy.deepcopy(intent), intent_digest=digest,
                      base=dict(revision=before['state']['revision'], document_token=before['document_token']),
                      after=dict(revision=after['state']['revision']), event=event),
                      outcome=dict(status='COMMITTED' if event else 'NOT_APPLICABLE', document_token_after=after['document_token']),
                      result=result, current=after, pending=False)
        self.records[op] = copy.deepcopy(record)
        return record

    def edit(self, fn):
        fn(self.state)
        api._codec.validate_current_envelope(self.state, self.era)
        self.generation += 1


class SuccessionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.store = ProtocolStore()
        self.plan = api.prepare_owner_session_succession(OP, 'opaque-source-Pi', store=self.store)['plan']
        self.target, self.authority, self.custody = target(), authority(), custody()

    def commit(self, **updates):
        arguments = dict(plan=self.plan, target=self.target, expected_document=self.plan['document_token'],
                         authority=self.authority, custody=self.custody, store=self.store)
        arguments.update(updates)
        return api.commit_owner_session_succession(**arguments)

    def digest(self):
        return api.build_intent_digest(self.plan, self.target, self.authority, self.custody)

    def envelope(self, result, status):
        self.assertEqual(set(result), ENVELOPE)
        self.assertEqual(result['schema'], api.SCHEMA)
        self.assertEqual(result['status'], status, result)
        json.dumps(result, allow_nan=False)
        if result['error'] is not None:
            self.assertEqual(set(result['error']), {'code', 'message', 'retryable', 'commit_state'})
        if result['receipt'] is not None:
            self.assertEqual(set(result['receipt']), RECEIPT)
        if status not in ('committed', 'already_committed', 'not_applicable'):
            self.assertFalse(result['current_binding_valid'])
            self.assertFalse(result['no_owner_valid'])
        return result

    def test_prepare_exact_plan_is_read_only_complete_current(self):
        self.store.calls.clear()
        result = self.envelope(api.prepare_owner_session_succession(OP, 'opaque-source-Pi', store=self.store), 'prepared')
        self.assertEqual(set(result['plan']), set(api._PLAN_FIELDS))
        self.assertEqual(result['plan']['owner_id'], 'alpha')
        self.assertEqual(self.store.calls, ['current_snapshot'])
        self.assertIsNone(result['receipt'])
        self.assertIsNone(result['intent_digest'])
        self.assertFalse(self.store.events)
        broken = copy.deepcopy(self.store.state)
        broken['assignments']['completed']['owner'] = 'absent'
        self.store.state = broken
        result = self.envelope(api.prepare_owner_session_succession(OP, 'opaque-source-Pi', store=self.store), 'unavailable')
        self.assertEqual(result['error']['code'], 'registry_invalid')

    def test_operation_ids_are_canonical_any_version_and_pi_is_opaque(self):
        for op in ('ABCDEF00-0000-4000-8000-000000000002', ' ' + OP, OP + '\n', '../' + OP, OP.replace('-', ''), 1, None):
            result = self.envelope(api.prepare_owner_session_succession(op, 'opaque-source-Pi', store=self.store), 'invalid_request')
            self.assertEqual(result['error']['code'], 'invalid_operation_id')
        for op in (str(uuid.uuid1()), str(uuid.uuid4()), str(uuid.uuid5(uuid.NAMESPACE_URL, 'synthetic'))):
            result = self.envelope(api.prepare_owner_session_succession(op, 'Pi:opaque/non-uuid', store=self.store), 'prepared')
            self.assertIsNone(result['plan']['owner_id'])
        for pi in ('', '  ', None, 42, '\ud800'):
            self.envelope(api.prepare_owner_session_succession(OP, pi, store=self.store), 'invalid_request')

    def test_frozen_digest_matches_exact_canonical_json_and_excludes_freshness(self):
        immutable = {key: self.plan[key] for key in api._IMMUTABLE_PLAN}
        expected = dict(version=1, plan=immutable, target=self.target, authority=self.authority, custody=self.custody)
        digest = hashlib.sha256(json.dumps(expected, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        self.assertEqual(self.digest(), digest)
        fresh = dict(self.plan, document_token='new-observation-token', observation_revision=99)
        self.assertEqual(api.build_intent_digest(fresh, self.target, self.authority, self.custody), digest)
        for field, value in [('era', str(uuid.uuid4())), ('owner_id', 'child'), ('store_id', 'other'), ('source_pi_session_id', 'other')]:
            changed = dict(self.plan, **{field: value})
            auth = authority('child') if field == 'owner_id' else self.authority
            child_custody = custody('other') if field == 'source_pi_session_id' else self.custody
            self.assertNotEqual(api.build_intent_digest(changed, self.target, auth, child_custody), digest)
        for section in ('identity', 'recovery', 'evidence'):
            changed = copy.deepcopy(self.target)
            field = {'identity': 'pi_session_id', 'recovery': 'summary', 'evidence': 'launch_binding'}[section]
            changed[section][field] += '-changed'
            self.assertNotEqual(api.build_intent_digest(self.plan, changed, self.authority, self.custody), digest)

    def test_invalid_schemas_are_typed_and_have_no_effect(self):
        changes = [
            ('plan', lambda p: p.update(extra='no'), 'invalid_plan'),
            ('plan', lambda p: p.update(version=True), 'invalid_plan'),
            ('plan', lambda p: p.update(observation_revision=True), 'invalid_plan'),
            ('target', lambda t: t.update(extra='no'), 'invalid_target'),
            ('target', lambda t: t['evidence'].update(nonce='RAW-SECRET-MUST-NOT-PERSIST'), 'invalid_target'),
            ('target', lambda t: t['evidence'].update(challenge_sha256='A' * 64), 'invalid_target'),
            ('target', lambda t: t['evidence'].update(operation_id=str(uuid.uuid4())), 'invalid_target'),
            ('target', lambda t: t['recovery'].update(cwd='relative'), 'invalid_target'),
            ('target', lambda t: t['recovery'].update(summary=float('nan')), 'invalid_target'),
            ('target', lambda t: t['recovery'].update(summary=float('inf')), 'invalid_target'),
            ('target', lambda t: t['recovery'].update(summary='\ud800'), 'invalid_target'),
            ('target', lambda t: t['recovery'].update(context_refs=('not-a-JSON-array',)), 'invalid_target'),
            ('target', lambda t: t['recovery']['context_refs'].append(t['recovery']['context_refs']), 'invalid_target'),
            ('target', lambda t: t['identity'].update(source='intercom-status'), 'invalid_target'),
            ('authority', lambda a: a.update(issuer='owner:superior'), 'invalid_authority'),
            ('authority', lambda a: a.update(issuer='owner:child'), 'invalid_authority'),
            ('authority', lambda a: a.update(evidence=''), 'invalid_authority'),
            ('custody', lambda c: c.update(status='none_verified', unknown_reason=''), 'valid'),
            ('custody', lambda c: c.update(status='known_active', unknown_reason=''), 'invalid_custody'),
            ('custody', lambda c: c.update(unknown_reason=''), 'invalid_custody'),
            ('custody', lambda c: c.update(source_supervisor_pi_session_id='different'), 'invalid_custody'),
        ]
        for name, change, code in changes:
            with self.subTest(name=name, code=code):
                values = dict(plan=copy.deepcopy(self.plan), target=copy.deepcopy(self.target), authority=copy.deepcopy(self.authority), custody=copy.deepcopy(self.custody))
                change(values[name])
                if code == 'valid':
                    api.build_intent_digest(**values)
                    continue
                with self.assertRaises(api.SuccessionValidationError) as caught:
                    api.build_intent_digest(**values)
                self.assertEqual(caught.exception.code, code)
                result = self.envelope(self.commit(**values), 'invalid_request')
                self.assertEqual(result['error']['code'], code)
        self.assertFalse(self.store.events)
        self.assertFalse(self.store.records)

    def test_null_contact_unknown_recovery_and_partial_custody_are_honest(self):
        self.target['identity'] = identity('opaque-target-Pi', None)
        self.target['evidence']['contact_source'] = 'unavailable'
        for field in ('repo', 'worktree', 'decisions'):
            self.target['recovery'][field] = None
        self.target['recovery']['unknown_reason'] = 'Synthetic exact unavailable fields.'
        self.custody['active_runs'] = [dict(run_id='synthetic-native-run', custodian_pi_session_id='opaque-source-Pi',
                                          custodian_intercom_id=None, result_delivery='Old source relays the bounded result.')]
        result = self.envelope(self.commit(), 'committed')
        owner = self.store.state['owners']['alpha']
        self.assertIsNone(owner['identity']['intercom_id'])
        self.assertEqual(self.store.records[OP]['prepare']['intent']['custody']['status'], 'unknown')
        self.assertTrue(result['current_binding_valid'])
        self.assertNotIn('nonce', json.dumps(self.store.records))

    def test_only_identity_recovery_updated_at_change_latest_graph_preserved(self):
        def latest(s):
            s['owners']['alpha']['parent'] = 'user'
            s['assignments']['active']['objective'] = 'The newest approved objective.'
            s['assignments']['completed']['end_reason'] = 'Newest accepted completion record.'
            s['coordination']['agreement']['sequencing'] = 'Newest coordination sequencing.'
        self.store.edit(latest)
        self.envelope(self.commit(), 'conflict')
        self.plan = api.prepare_owner_session_succession(OP, 'opaque-source-Pi', store=self.store)['plan']
        before = copy.deepcopy(self.store.state)
        result = self.envelope(self.commit(), 'committed')
        after = copy.deepcopy(self.store.state)
        after['revision'] = before['revision']
        for key in ('identity', 'recovery', 'updated_at'):
            after['owners']['alpha'][key] = before['owners']['alpha'][key]
        self.assertEqual(after, before)
        self.assertEqual(result['receipt']['revision_after'], before['revision'] + 1)
        event = self.store.events[0]
        self.assertEqual(event['action'], 'session-succession')
        self.assertEqual(event['actor'], 'alpha')
        self.assertEqual(event['before']['alpha']['identity']['pi_session_id'], 'opaque-source-Pi')
        self.assertEqual(event['after']['alpha']['identity'], self.target['identity'])

    def test_no_owner_durable_noop_no_revision_event_and_registration_conflicts(self):
        source = 'synthetic-unregistered-Pi'
        self.plan = api.prepare_owner_session_succession(OP, source, store=self.store)['plan']
        self.authority, self.custody = authority(None), custody(source)
        before = copy.deepcopy(self.store.state)
        result = self.envelope(self.commit(), 'not_applicable')
        self.assertTrue(result['no_owner_valid'])
        self.assertFalse(result['current_binding_valid'])
        self.assertIsNone(result['receipt']['owner_id'])
        self.assertIsNone(result['receipt']['event_id'])
        self.assertEqual(result['receipt']['revision_before'], result['receipt']['revision_after'])
        self.assertEqual(self.store.state, before)
        self.assertFalse(self.store.events)
        self.assertIn(OP, self.store.records)
        self.store.edit(lambda s: s['owners']['child'].update(identity=identity(source)))
        self.envelope(api.get_owner_session_succession(OP, self.digest(), store=self.store), 'superseded')
        self.store.records.clear()
        self.plan['document_token'] = self.store.current_snapshot()['document_token']
        result = self.envelope(self.commit(), 'conflict')
        self.assertEqual(result['error']['code'], 'source_registration_changed')

    def test_template_no_owner_plan_is_valid(self):
        self.store = ProtocolStore(dict(version=1, revision=0, owners={}, assignments={}, coordination={}))
        self.store.era = 'template'
        self.plan = api.prepare_owner_session_succession(OP, 'opaque-source-Pi', store=self.store)['plan']
        self.authority = authority(None)
        self.envelope(self.commit(), 'not_applicable')

    def test_duplicate_source_and_pi_occupied_not_contact_equality(self):
        self.store.edit(lambda s: s['owners']['child'].update(identity=identity('opaque-source-Pi')))
        result = self.envelope(api.prepare_owner_session_succession(OP, 'opaque-source-Pi', store=self.store), 'conflict')
        self.assertEqual(result['error']['code'], 'source_identity_conflict')
        self.plan['document_token'] = self.store.current_snapshot()['document_token']
        self.assertEqual(self.envelope(self.commit(), 'conflict')['error']['code'], 'source_identity_conflict')
        self.store = ProtocolStore()
        self.store.edit(lambda s: s['owners']['child'].update(identity=identity('opaque-target-Pi')))
        self.plan['document_token'] = self.store.current_snapshot()['document_token']
        self.assertEqual(self.envelope(self.commit(), 'conflict')['error']['code'], 'target_identity_occupied')
        self.assertFalse(self.store.events)
        self.store = ProtocolStore()  # All contacts are equal, all actual Pi identities differ.
        self.plan['document_token'] = self.store.current_snapshot()['document_token']
        self.envelope(self.commit(), 'committed')

    def test_source_owner_store_era_and_already_target_conditions(self):
        cases = [
            (lambda s: s['owners']['alpha'].update(identity=identity('other-Pi')), 'source_identity_conflict'),
            (lambda s: s['owners']['child'].update(identity=identity('opaque-source-Pi')), 'source_identity_conflict'),
            (lambda s: s['owners']['alpha'].update(identity=identity('opaque-target-Pi')), 'source_identity_conflict'),
        ]
        for edit, expected in cases:
            self.store = ProtocolStore()
            self.store.edit(edit)
            self.plan['document_token'] = self.store.current_snapshot()['document_token']
            self.assertEqual(self.envelope(self.commit(), 'conflict')['error']['code'], expected)
            self.assertFalse(self.store.events)
        self.store = ProtocolStore()
        self.plan['document_token'] = self.store.current_snapshot()['document_token']
        self.store.era = str(uuid.uuid4())
        self.assertEqual(self.envelope(self.commit(), 'conflict')['error']['code'], 'era_changed')
        self.store.era = ERA
        self.store.store_id = 'different-store'
        self.assertEqual(self.envelope(self.commit(), 'conflict')['error']['code'], 'store_mismatch')
        self.store.store_id = self.plan['store_id']
        self.target['identity']['pi_session_id'] = 'opaque-source-Pi'
        self.assertEqual(self.envelope(self.commit(), 'conflict')['error']['code'], 'source_identity_conflict')

    def test_missing_owner_and_rebound_source_are_distinct_conflicts(self):
        def remove_owner(s):
            del s['owners']['alpha']
            s['assignments'] = {aid: a for aid, a in s['assignments'].items() if a['owner'] != 'alpha'}
            s['coordination'] = {}
        self.store.edit(remove_owner)
        self.plan['document_token'] = self.store.current_snapshot()['document_token']
        result = self.envelope(self.commit(), 'conflict')
        self.assertEqual(result['error']['code'], 'source_owner_missing')
        self.store = ProtocolStore()
        def rebind_source(s):
            s['owners']['alpha']['identity'] = identity('another-Pi')
            s['owners']['child']['identity'] = identity('opaque-source-Pi')
        self.store.edit(rebind_source)
        self.plan['document_token'] = self.store.current_snapshot()['document_token']
        result = self.envelope(self.commit(), 'conflict')
        self.assertEqual(result['error']['code'], 'source_owner_changed')
        self.assertFalse(self.store.events)

    def test_same_op_receipt_precedes_stale_cas_and_changed_source(self):
        first = self.envelope(self.commit(), 'committed')
        self.store.calls.clear()
        retry = self.envelope(self.commit(expected_document='stale-on-purpose'), 'already_committed')
        self.assertEqual(first['receipt'], retry['receipt'])
        self.assertNotIn('transact_current', self.store.calls)
        self.assertEqual(len(self.store.events), 1)
        self.target['recovery']['summary'] += ' changed'
        result = self.envelope(self.commit(), 'conflict')
        self.assertEqual(result['error']['code'], 'operation_intent_mismatch')
        self.assertEqual(len(self.store.events), 1)

    def test_old_receipt_superseded_after_successive_carrier_and_reset_or_manual_identity(self):
        self.envelope(self.commit(), 'committed')
        old_digest = self.digest()
        old_plan, old_target, old_custody = self.plan, self.target, self.custody
        op2 = str(uuid.uuid4())
        self.plan = api.prepare_owner_session_succession(op2, 'opaque-target-Pi', store=self.store)['plan']
        self.target, self.custody = target(op2, 'opaque-third-Pi'), custody('opaque-target-Pi')
        self.envelope(self.commit(), 'committed')
        self.envelope(api.get_owner_session_succession(OP, old_digest, store=self.store), 'superseded')
        self.plan, self.target, self.custody = old_plan, old_target, old_custody
        self.envelope(self.commit(), 'superseded')
        self.assertEqual(len(self.store.events), 2)
        self.store = ProtocolStore()
        self.envelope(self.commit(), 'committed')
        self.store.edit(lambda s: s['owners']['alpha']['identity'].update(intercom_id='manual-new-contact'))
        self.envelope(api.get_owner_session_succession(OP, old_digest, store=self.store), 'superseded')
        self.store.state['owners']['alpha']['identity'] = copy.deepcopy(self.target['identity'])
        self.store.era = str(uuid.uuid4())
        self.envelope(api.get_owner_session_succession(OP, old_digest, store=self.store), 'superseded')

    def test_get_is_read_only_no_repair_and_known_receipt_pending_blocks(self):
        digest = self.digest()
        self.store.calls.clear()
        self.envelope(api.get_owner_session_succession(OP, digest, store=self.store), 'not_found')
        self.assertNotIn('transact_current', self.store.calls)
        self.envelope(self.commit(), 'committed')
        self.store.records[OP]['pending'] = True
        before = copy.deepcopy(self.store.records)
        self.store.calls.clear()
        result = self.envelope(api.get_owner_session_succession(OP, digest, store=self.store), 'needs_recovery')
        self.assertEqual(result['error']['code'], 'transaction_pending')
        self.assertEqual(result['error']['commit_state'], 'committed')
        self.assertEqual(self.store.records, before)
        self.assertNotIn('transact_current', self.store.calls)
        self.store.records[OP]['pending'] = False
        self.store.records[OP]['outcome']['status'] = 'PUBLICATION_UNKNOWN'
        result = self.envelope(api.get_owner_session_succession(OP, digest, store=self.store), 'needs_recovery')
        self.assertEqual(result['error']['commit_state'], 'unknown')
        self.store.records[OP]['outcome'] = None
        result = self.envelope(api.get_owner_session_succession(OP, digest, store=self.store), 'needs_recovery')
        self.assertEqual(result['error']['commit_state'], 'unknown')
        self.assertEqual(len(self.store.events), 1)

    def test_error_mapping_sanitized_unknown_effect_and_programmer_error_propagates(self):
        def fails(code, effect, retryable=False):
            def status(*args):
                raise StoreError(code, effect, retryable)
            self.store.operation_status = status
        for code, status, effect in [('document_conflict', 'conflict', 'not_committed'),
                                     ('audit_unavailable', 'unavailable', 'unknown'),
                                     ('commit_unknown', 'needs_recovery', 'unknown'),
                                     ('registry_invalid', 'unavailable', 'committed'),
                                     ('codec_incompatible', 'unavailable', 'not_committed')]:
            fails(code, effect)
            result = self.envelope(api.get_owner_session_succession(OP, self.digest(), store=self.store), status)
            self.assertEqual(result['error']['commit_state'], effect)
            self.assertNotIn('/private/', result['error']['message'])
        def programmer(*args):
            raise RuntimeError('unexpected defect')
        self.store.operation_status = programmer
        with self.assertRaisesRegex(RuntimeError, 'unexpected defect'):
            api.get_owner_session_succession(OP, self.digest(), store=self.store)
        for value in ('bad', 'A' * 64, None):
            result = self.envelope(api.get_owner_session_succession(OP, value, store=self.store), 'invalid_request')
            self.assertEqual(result['error']['code'], 'invalid_digest')

    def test_requires_v3_commit_but_prepare_observes_v2_and_absent_protocol_fails(self):
        self.store.format = 'responsibility-registry/v2'
        self.envelope(api.prepare_owner_session_succession(OP, 'opaque-source-Pi', store=self.store), 'prepared')
        result = self.envelope(self.commit(), 'unavailable')
        self.assertEqual(result['error']['code'], 'storage_not_migrated')
        self.assertFalse(self.store.records)
        result = self.envelope(api.prepare_owner_session_succession(OP, 'opaque-source-Pi', store=object()), 'unavailable')
        self.assertEqual(result['error']['code'], 'codec_incompatible')

    def test_null_receipt_token_never_fabricated_from_sha_or_stale_token(self):
        self.envelope(self.commit(), 'committed')
        record = self.store.records[OP]
        record['outcome'].pop('document_token_after')
        # Even an unchanged matching current cannot supply a missing historical
        # publication/recovery observation; it qualifies only the current flags.
        result = self.envelope(api.get_owner_session_succession(OP, self.digest(), store=self.store), 'already_committed')
        self.assertIsNone(result['receipt']['document_token_after'])
        self.assertTrue(result['current_binding_valid'])
        record['prepare']['after']['document_sha256'] = 'd' * 64
        self.store.edit(lambda s: s['assignments']['active'].update(objective='New valid manual current.'))
        self.store.state['revision'] += 1
        result = self.envelope(api.get_owner_session_succession(OP, self.digest(), store=self.store), 'already_committed')
        self.assertIsNone(result['receipt']['document_token_after'])
        self.assertTrue(result['current_binding_valid'])

    def test_corrupt_service_receipt_is_not_ready(self):
        self.envelope(self.commit(), 'committed')
        self.store.records[OP]['prepare']['intent']['target']['identity']['intercom_id'] = 'tampered'
        result = self.envelope(api.get_owner_session_succession(OP, self.digest(), store=self.store), 'unavailable')
        self.assertEqual(result['error']['code'], 'audit_damaged')
        self.assertEqual(result['error']['commit_state'], 'committed')

    def test_private_loader_uses_fresh_source_not_timestamp_valid_pyc(self):
        with tempfile.TemporaryDirectory() as directory:
            scripts = Path(directory) / 'scripts'
            scripts.mkdir()
            for source in SCRIPT.parent.glob('*.py'):
                shutil.copyfile(source, scripts / source.name)
            codec = scripts / 'registry_document.py'
            source = codec.read_text()
            codec.write_text(source + "\nSTALE_TEST_MARKER = 'old'\n")
            stamp = codec.stat()
            py_compile.compile(str(codec), doraise=True)
            codec.write_text(source + "\nSTALE_TEST_MARKER = 'new'\n")
            os.utime(codec, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            spec = importlib.util.spec_from_file_location('_fresh_source_service', scripts / SCRIPT.name)
            loaded = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(loaded)
            self.assertEqual(loaded._codec.STALE_TEST_MARKER, 'new')
            self.assertIs(loaded._sibling('registry').RegistryError, loaded._codec.RegistryError)

    def test_absolute_importlib_load_from_other_cwd_keeps_generic_modules_untouched(self):
        code = '''
import importlib.util, pathlib, sys, types
before_path = list(sys.path)
poison = {name: types.ModuleType(name) for name in ('registry', 'registry_document', 'audit_storage')}
sys.modules.update(poison)
spec = importlib.util.spec_from_file_location('_fixed_installed_service', sys.argv[1])
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)
assert service.SCHEMA == 'responsibility-session-succession/v1'
core = service._sibling('registry')
assert core.Store.__module__.startswith(service._PACKAGE + '.')
assert core.RegistryError is service._codec.RegistryError
assert all(sys.modules[name] is module for name, module in poison.items())
assert sys.path == before_path
assert callable(service.build_intent_digest)
print('absolute loader and private sibling Store verified')
'''
        with tempfile.TemporaryDirectory() as other_cwd:
            result = subprocess.run([sys.executable, '-I', '-c', code, str(SCRIPT)], cwd=other_cwd,
                                    text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('private sibling Store verified', result.stdout)


class ActualStoreSuccessionTests(unittest.TestCase):
    """Temporary real Store, real lock/journal/publication; no live runtime paths."""
    def setUp(self):
        self.core = api._sibling('registry')
        if not callable(getattr(self.core.Store, 'transact_current', None)):
            self.skipTest('Real current transaction Store dependency is not installed in this checkout.')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.root = self.directory / 'private'
        self.document = self.directory / 'current.md'
        self.store = self.new_store()
        self.store.initialize()
        # A synthetic manually authored v3 graph is current authority. It does not
        # invent old events for its observation revision or read any real owner.
        self.document.write_text(api._codec.serialize_current_document(state(), ERA), encoding='utf-8')
        self.plan = self.prepare(OP, 'opaque-source-Pi')
        self.target, self.authority, self.custody = target(), authority(), custody()

    def new_store(self):
        return self.core.Store(self.root, self.document)

    def prepare(self, op, source):
        result = api.prepare_owner_session_succession(op, source, store=self.new_store())
        self.assertEqual(result['status'], 'prepared', result)
        return result['plan']

    def commit(self, **updates):
        arguments = dict(plan=self.plan, target=self.target, expected_document=self.plan['document_token'],
                         authority=self.authority, custody=self.custody, store=self.new_store())
        arguments.update(updates)
        result = api.commit_owner_session_succession(**arguments)
        self.assertEqual(set(result), ENVELOPE)
        return result

    def digest(self):
        return api.build_intent_digest(self.plan, self.target, self.authority, self.custody)

    def get(self, op=None, digest=None):
        return api.get_owner_session_succession(op or OP, digest or self.digest(), store=self.new_store())

    def manual(self, fn, *, era=None):
        snapshot = self.store.current_snapshot()
        fn(snapshot['state'])
        self.document.write_text(api._codec.serialize_current_document(snapshot['state'], era or snapshot['era'], snapshot['audit_head']), encoding='utf-8')

    def files(self):
        return {str(path.relative_to(self.directory)): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.directory.rglob('*') if path.is_file()}

    def prepares(self):
        return [json.loads(path.read_text()) for path in self.store.audit.ops.glob('*.prepare.json')]

    def test_real_same_op_one_event_fresh_store_receipt_and_get_read_only(self):
        before = self.store.current_snapshot()
        first = self.commit()
        self.assertEqual(first['status'], 'committed', first)
        self.assertTrue(first['current_binding_valid'])
        self.assertEqual(first['receipt']['document_token_after'], self.store.current_snapshot()['document_token'])
        self.assertEqual(set(first['receipt']), RECEIPT)
        after = self.files()
        repeated = self.commit(expected_document='intentionally-stale')
        self.assertEqual(repeated['status'], 'already_committed', repeated)
        self.assertEqual(repeated['receipt'], first['receipt'])
        self.assertEqual(self.get()['status'], 'already_committed')
        self.assertEqual(self.files(), after)
        prepares = self.prepares()
        self.assertEqual(len(prepares), 1)
        event = prepares[0]['event']
        self.assertEqual(event['action'], 'session-succession')
        self.assertEqual(event['actor'], 'alpha')
        self.assertEqual(event['revision'], before['state']['revision'] + 1)
        self.assertEqual(set(event['before']['owners']), {'alpha'})
        self.assertEqual(event['before']['owners']['alpha']['identity']['pi_session_id'], 'opaque-source-Pi')
        self.assertEqual(event['after']['owners']['alpha']['identity'], self.target['identity'])
        self.assertEqual(event['before']['assignments'], {})
        self.assertEqual(event['after']['coordination'], {})
        changed = copy.deepcopy(self.target)
        changed['recovery']['summary'] += ' changed intent'
        conflict = self.commit(target=changed)
        self.assertEqual(conflict['error']['code'], 'operation_intent_mismatch')
        self.assertEqual(self.files(), after)

    def test_real_latest_graph_cas_and_all_noncarrier_fields_preserved(self):
        digest = self.digest()
        def changes(s):
            s['owners']['alpha']['parent'] = 'user'
            s['assignments']['active']['objective'] = 'Newest authorized synthetic objective.'
            s['assignments']['active']['depends_on'].append({'kind': 'project', 'project': 'new-dependency'})
            s['assignments']['completed']['end_reason'] = 'Newest completion evidence.'
            s['coordination']['agreement']['sequencing'] = 'Newest confirmed sequencing.'
        self.manual(changes)
        before_bytes = self.document.read_bytes()
        stale = self.commit()
        self.assertEqual(stale['status'], 'conflict', stale)
        self.assertEqual(stale['error']['code'], 'document_conflict')
        self.assertEqual(self.document.read_bytes(), before_bytes)
        self.assertFalse(self.store.audit.exists())
        self.plan = self.prepare(OP, 'opaque-source-Pi')
        self.assertEqual(self.digest(), digest)
        before = self.store.current_snapshot()['state']
        result = self.commit()
        self.assertEqual(result['status'], 'committed', result)
        after = self.store.current_snapshot()['state']
        after['revision'] = before['revision']
        for field in ('identity', 'recovery', 'updated_at'):
            after['owners']['alpha'][field] = before['owners']['alpha'][field]
        self.assertEqual(after, before)

    def test_real_store_in_another_module_namespace_uses_structured_errors(self):
        package_name = '_synthetic_foreign_registry_' + uuid.uuid4().hex
        package = types.ModuleType(package_name)
        package.__path__ = [str(SCRIPT.parent)]
        sys.modules[package_name] = package
        def cleanup():
            for name in list(sys.modules):
                if name == package_name or name.startswith(package_name + '.'):
                    sys.modules.pop(name, None)
        self.addCleanup(cleanup)
        spec = importlib.util.spec_from_file_location(package_name + '.registry', SCRIPT.parent / 'registry.py')
        foreign = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = foreign
        spec.loader.exec_module(foreign)
        self.assertIsNot(foreign.RegistryTransactionError, self.core.RegistryTransactionError)
        foreign_store = foreign.Store(self.root, self.document)
        conflict = self.commit(store=foreign_store, expected_document='stale-on-purpose')
        self.assertEqual(conflict['status'], 'conflict', conflict)
        self.assertEqual(conflict['error']['code'], 'document_conflict')
        self.assertTrue(conflict['error']['retryable'])
        self.assertEqual(self.commit(store=foreign_store)['status'], 'committed')
        changed = copy.deepcopy(self.target)
        changed['recovery']['summary'] += ' foreign namespace changed intent'
        conflict = self.commit(store=foreign_store, target=changed)
        self.assertEqual(conflict['error']['code'], 'operation_intent_mismatch', conflict)
        self.assertEqual(len(self.prepares()), 1)

    def test_real_no_owner_persists_receipt_not_current_revision_or_event(self):
        source = 'synthetic-no-owner-Pi'
        self.plan = self.prepare(OP, source)
        self.authority, self.custody = authority(None), custody(source)
        before = self.document.read_bytes(), self.document.stat().st_mtime_ns, self.store.current_snapshot()
        first = self.commit()
        self.assertEqual(first['status'], 'not_applicable', first)
        self.assertTrue(first['no_owner_valid'])
        self.assertFalse(first['current_binding_valid'])
        self.assertIsNone(first['receipt']['owner_id'])
        self.assertIsNone(first['receipt']['event_id'])
        self.assertEqual((self.document.read_bytes(), self.document.stat().st_mtime_ns, self.store.current_snapshot()), before)
        self.assertEqual(len(self.prepares()), 1)
        self.assertIsNone(self.prepares()[0]['event'])
        after = self.files()
        self.assertEqual(self.get()['status'], 'not_applicable')
        self.assertEqual(self.commit(expected_document='old-token')['status'], 'not_applicable')
        self.assertEqual(self.files(), after)
        self.manual(lambda s: s['owners']['child'].update(identity=identity(source)))
        self.assertEqual(self.get()['status'], 'superseded')

    def test_real_new_source_registration_conflicts_without_private_operation(self):
        source = 'synthetic-no-owner-Pi'
        self.plan = self.prepare(OP, source)
        self.authority, self.custody = authority(None), custody(source)
        self.manual(lambda s: s['owners']['child'].update(identity=identity(source)))
        self.plan['document_token'] = self.store.current_snapshot()['document_token']
        before = self.files()
        result = self.commit()
        self.assertEqual(result['error']['code'], 'source_registration_changed', result)
        self.assertEqual(self.files(), before)

    def test_real_later_carrier_manual_identity_and_clear_restore_supersede(self):
        self.assertEqual(self.commit()['status'], 'committed')
        original_digest = self.digest()
        op2 = str(uuid.uuid4())
        next_plan = self.prepare(op2, 'opaque-target-Pi')
        result = self.commit(plan=next_plan, target=target(op2, 'opaque-third-Pi'),
                             expected_document=next_plan['document_token'], custody=custody('opaque-target-Pi'))
        self.assertEqual(result['status'], 'committed', result)
        self.assertEqual(self.get(digest=original_digest)['status'], 'superseded')
        self.assertEqual(self.commit()['status'], 'superseded')
        self.assertEqual(len(self.prepares()), 2)
        current_digest = api.build_intent_digest(next_plan, target(op2, 'opaque-third-Pi'), self.authority, custody('opaque-target-Pi'))
        self.manual(lambda s: s['owners']['alpha']['identity'].update(intercom_id='new-manual-contact'))
        self.assertEqual(self.get(op2, current_digest)['status'], 'superseded')
        self.manual(lambda s: s['owners']['alpha'].update(identity=identity('opaque-third-Pi')))
        self.assertEqual(self.get(op2, current_digest)['status'], 'already_committed')
        clear = self.store.switch_document('clear', self.store.current_snapshot()['document_token'],
                                           'user', 'Synthetic explicit clear.', 'Synthetic user authorization.')
        self.assertEqual(self.get(op2, current_digest)['status'], 'superseded')
        self.store.switch_document('restore', self.store.current_snapshot()['document_token'],
                                   'user', 'Synthetic explicit restore.', 'Synthetic user authorization.', target='previous')
        self.assertNotEqual(self.store.current_snapshot()['era'], next_plan['era'])
        self.assertEqual(self.store.current_snapshot()['state']['owners']['alpha']['identity']['pi_session_id'], 'opaque-third-Pi')
        self.assertEqual(self.get(op2, current_digest)['status'], 'superseded')

    def test_real_same_source_same_op_concurrency_one_event(self):
        barrier = threading.Barrier(2)
        def attempt(_):
            store = self.new_store()
            original_status = store.operation_status
            def synchronized_status(op, digest):
                result = original_status(op, digest)
                barrier.wait(timeout=10)
                return result
            store.operation_status = synchronized_status
            return self.commit(store=store)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(sorted(result['status'] for result in results), ['already_committed', 'committed'], results)
        self.assertEqual(len(self.prepares()), 1)
        self.assertEqual(self.store.current_snapshot()['state']['revision'], self.plan['observation_revision'] + 1)

    def test_real_different_sources_concurrent_cas_then_same_intent_retry(self):
        op2 = str(uuid.uuid4())
        plan2 = self.prepare(op2, 'synthetic-child-Pi')
        parameters = [dict(plan=self.plan, target=self.target, authority=self.authority, custody=self.custody),
                      dict(plan=plan2, target=target(op2, 'synthetic-child-target-Pi'), authority=authority('child'), custody=custody('synthetic-child-Pi'))]
        barrier = threading.Barrier(2)
        def attempt(index):
            barrier.wait(timeout=10)
            return self.commit(**parameters[index], expected_document=parameters[index]['plan']['document_token'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(sorted(result['status'] for result in results), ['committed', 'conflict'], results)
        loser = next(index for index, result in enumerate(results) if result['status'] == 'conflict')
        self.assertEqual(results[loser]['error']['code'], 'document_conflict')
        params = parameters[loser]
        fresh = self.prepare(params['plan']['operation_id'], params['plan']['source_pi_session_id'])
        self.assertEqual({k: fresh[k] for k in api._IMMUTABLE_PLAN}, {k: params['plan'][k] for k in api._IMMUTABLE_PLAN})
        frozen_digest = api.build_intent_digest(**params)
        params['plan'] = fresh
        self.assertEqual(api.build_intent_digest(**params), frozen_digest)
        result = self.commit(**params, expected_document=fresh['document_token'])
        self.assertEqual(result['status'], 'committed', result)
        current = self.store.current_snapshot()['state']
        self.assertEqual(current['owners']['alpha']['identity']['pi_session_id'], 'opaque-target-Pi')
        self.assertEqual(current['owners']['child']['identity']['pi_session_id'], 'synthetic-child-target-Pi')
        self.assertEqual(len(self.prepares()), 2)

    def test_real_same_source_other_op_cannot_steal_after_fresh_token(self):
        competing_op = str(uuid.uuid4())
        competing_plan = self.prepare(competing_op, 'opaque-source-Pi')
        self.assertEqual(self.commit()['status'], 'committed')
        result = self.commit(plan=competing_plan, target=target(competing_op, 'synthetic-competing-target'),
                             expected_document=self.store.current_snapshot()['document_token'])
        self.assertEqual(result['status'], 'conflict', result)
        self.assertEqual(result['error']['code'], 'source_identity_conflict')
        self.assertEqual(len(self.prepares()), 1)

    def test_real_pending_get_does_not_repair_and_explicit_recovery_never_republishes(self):
        def crash(phase):
            if phase == 'current_replaced':
                raise OSError('Synthetic fault after current publication.')
        self.store.transaction_checkpoint = self.store.audit.checkpoint = crash
        failed = self.commit(store=self.store)
        self.assertEqual(failed['status'], 'needs_recovery', failed)
        self.assertEqual(failed['error']['commit_state'], 'unknown')
        after = self.files()
        status = self.get()
        self.assertEqual(status['status'], 'needs_recovery', status)
        self.assertEqual(status['error']['code'], 'transaction_pending')
        self.assertEqual(self.files(), after)
        current_bytes = self.document.read_bytes()
        self.new_store().recover_transaction('user', 'Synthetic explicit recovery.', 'Synthetic user authority.')
        self.assertEqual(self.document.read_bytes(), current_bytes)
        self.assertEqual(self.get()['status'], 'already_committed')
        self.assertEqual(len(self.prepares()), 1)
        self.assertEqual(self.commit()['status'], 'already_committed')

    def test_real_unknown_after_manual_reversion_never_assumes_abort_or_replays(self):
        original = self.document.read_bytes()
        def crash(phase):
            if phase == 'current_replaced':
                raise OSError('Synthetic fault after current publication.')
        self.store.transaction_checkpoint = self.store.audit.checkpoint = crash
        self.assertEqual(self.commit(store=self.store)['status'], 'needs_recovery')
        self.document.write_bytes(original)  # Valid manual current; history cannot prove rename never happened.
        self.new_store().recover_transaction('user', 'Synthetic explicit recovery.', 'Synthetic user authority.')
        current = self.files()
        result = self.get()
        self.assertEqual(result['status'], 'needs_recovery', result)
        self.assertEqual(result['error']['commit_state'], 'unknown')
        self.assertEqual(self.commit()['status'], 'needs_recovery')
        self.assertEqual(self.files(), current)
        self.assertEqual(self.document.read_bytes(), original)
        self.assertEqual(len(self.prepares()), 1)


if __name__ == '__main__':
    unittest.main()
