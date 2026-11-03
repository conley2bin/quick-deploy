"""Joint helper/API/real-Store proof; every registry path is temporary.

Requires the canonical split Store and frozen-v1 service dependencies. Missing
ones fail at import/setup rather than silently skipping the actual integration.
The peer's state() factory supplies a codec-validated synthetic graph, not live
owners. BoundService delegates all operations to that actual API/Store; it is
only explicit test DI and call observation, never an alternative service.
"""
import concurrent.futures
import copy
from contextlib import redirect_stderr, redirect_stdout
import importlib
import importlib.util
import io as stdio
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from uuid import uuid4

from test_artifacts import context, io

s = importlib.import_module('handoff_test.succession')
engine = importlib.import_module('handoff_test.engine')
REGISTRY = Path(__file__).resolve().parents[3] / 'pi-agent/skills/responsibility-registry'
_spec = importlib.util.spec_from_file_location('_joint_peer_synthetic_fixture', REGISTRY / 'tests/test_session_succession.py')
fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture)
api = fixture.api
SOURCE, TARGET = 'opaque-source-Pi', 'actual-target-Pi'


class BoundService:
    """Real API functions with mandatory temporary Store injection on every call."""
    def __init__(self, case, module=api):
        self.case, self.module = case, module
        self.calls = []
        self.before_commit = None

    def checked_store(self, supplied):
        self.case.assertIsNone(supplied, 'Adapter must not choose a Store')
        self.case.assert_temporary()
        return self.case.store

    def prepare_owner_session_succession(self, operation_id, source_pi_session_id, *, store=None):
        temporary = self.checked_store(store)
        self.calls.append('prepare')
        return self.module.prepare_owner_session_succession(operation_id, source_pi_session_id, store=temporary)

    def build_intent_digest(self, plan, target, authority, custody):
        self.case.assert_temporary()
        return self.module.build_intent_digest(plan, target, authority, custody)

    def commit_owner_session_succession(self, plan, target, *, expected_document, authority, custody, store=None):
        temporary = self.checked_store(store)
        self.calls.append('commit')
        if self.before_commit:
            hook, self.before_commit = self.before_commit, None
            hook()
        result = self.module.commit_owner_session_succession(plan, target, expected_document=expected_document,
                                                            authority=authority, custody=custody, store=temporary)
        if result['status'] == 'committed':
            self.case.assertEqual(self.case.events()[-1]['event']['action'], 'session-succession')
        return result

    def get_owner_session_succession(self, operation_id, intent_digest, *, store=None):
        temporary = self.checked_store(store)
        self.calls.append('get')
        return self.module.get_owner_session_succession(operation_id, intent_digest, store=temporary)


class HelperStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='handoff-real-store-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root, self.document = self.base / 'private', self.base / 'current.md'
        self.store = api._sibling('registry').Store(self.root, self.document)
        self.assert_temporary()
        self.store.initialize()
        self.assert_temporary()
        self.document.write_text(api._codec.serialize_current_document(fixture.state(), fixture.ERA), encoding='utf-8')
        self.service = BoundService(self)
        self.adapter = s.Adapter(self.service)
        self.make_operation()

    def assert_temporary(self):
        self.assertEqual(self.store.root, self.root)
        self.assertEqual(self.store.document, self.document)
        for path in (self.store.root, self.store.document):
            self.assertTrue(path.is_relative_to(self.base))
            self.assertNotEqual(path, Path.home() / '.pi/agent/skills/responsibility-registry/RESPONSIBILITIES.md')

    def make_operation(self, source=SOURCE, target=TARGET):
        self.op = str(uuid4())
        self.directory = self.base / self.op
        self.directory.mkdir(mode=0o700)
        source_file = self.directory / 'source-session.jsonl'
        source_file.write_text(json.dumps(dict(type='session', version=3, id=source, cwd=str(self.base))) + '\n')
        snapshot = context.create_source_snapshot(source_file)
        self.manifest = dict(version=1, operation_id=self.op, source_sha256=snapshot['sha256'], note='',
            source=dict(session_id='source-route', pi_session_id=source, cwd=str(self.base), snapshot=snapshot),
            paths=dict(operation_dir=str(self.directory), source_context=str(self.directory / 'source-context.md'),
                       **{k: str(self.directory / (k + '.json')) for k in context.KINDS}))
        self.manifest['succession'] = s.prepare_source(self.directory, self.op, source, self.adapter)
        context.prepare_artifacts(self.directory, self.manifest)
        target_file = self.directory / 'filename-is-not-actual-id.jsonl'
        target_file.write_text(json.dumps(dict(type='session', version=3, id=target, cwd=str(self.base))) + '\n')
        info = target_file.stat()
        s.bind_target(self.directory, self.manifest, dict(session_id='target-route', launch_id='synthetic-native-launch',
            broker_pid=123, spawn_nonce='synthetic-nonce', cwd=str(self.base)),
            dict(log_path=str(target_file), pi_session_id=target, device=info.st_dev, inode=info.st_ino))
        source_proof = json.loads((self.directory / 'succession-source.json').read_text())
        self.attest('identity', dict(version=1, operation_id=self.op, challenge=source_proof['challenge'],
            pi_session_id=target, session_file=str(target_file), intercom_id=None, contact_source='unavailable',
            unknown_reason='No connected Intercom runtime exists in this synthetic integration.', source='current-session'))
        self.body = self.directory / 'body.md'
        self.body.write_text('Preserve the current accepted graph; unresolved native child custody is not transferred.')
        self.u = self.submit('understanding')
        self.attest('custody', dict(operation_id=self.op, understanding_sha256=self.u['_sha256'], custody=s.unknown_custody(source)))
        self.r = self.submit('review', verdict='clear')
        self.intent = s.freeze_intent(self.directory, self.manifest, self.u['_sha256'], self.r['_sha256'], self.adapter)

    def attest(self, kind, value):
        draft = self.directory / (kind + '-draft.json')
        draft.write_text(json.dumps(value))
        io.attest(self.directory / 'manifest.json', kind, draft)

    def submit(self, kind='resolution', **kwargs):
        return io.submit(self.directory / 'manifest.json', kind, self.body, service=self.service, **kwargs)

    def cli(self, *, check=False, inject=True):
        args = ['check-owner', '--manifest', str(self.directory / 'manifest.json')] if check else [
            'submit', '--manifest', str(self.directory / 'manifest.json'), '--kind', 'resolution',
            '--body-file', str(self.body), '--verdict', 'ready']
        # Explicit code-only injection into the ordinary CLI/Adapter path. All
        # service calls retain their real implementation and explicit temp Store.
        if not inject:
            return io.main(args)
        with patch.object(s.Adapter, 'load', return_value=self.service):
            return io.main(args)

    def allowed_toy_action(self, cli_code):
        # The smallest real downstream effect: write only after helper CLI 0.
        if cli_code == 0:
            (self.directory / 'toy-action.txt').write_text('Executed after helper permission only.\n')

    def events(self):
        self.assert_temporary()
        records = [json.loads(p.read_text()) for p in sorted(self.store.audit.ops.glob('*.prepare.json'))]
        # A durable no-owner disposition has private PREPARE metadata but no
        # registry event; count the actual event, never the transaction file.
        return [record for record in records if record['event'] is not None]

    def snapshot(self):
        self.assert_temporary()
        return self.store.current_snapshot()

    def manual(self, change):
        snapshot = self.snapshot()
        change(snapshot['state'])
        self.assert_temporary()
        self.document.write_text(api._codec.serialize_current_document(snapshot['state'], snapshot['era'], snapshot['audit_head']), encoding='utf-8')

    def fresh(self):
        return s.validate_ready(self.directory, self.manifest, self.u['_sha256'], self.r['_sha256'], self.adapter)

    def engine_resolution(self):
        # Exercise Engine's real ready-observation gate without starting a host,
        # opening production state, or making any native/broker request.
        host = object.__new__(engine.Engine)
        host.root, host.manifests, host.succession = self.base, {self.op: self.manifest}, self.adapter
        return host._artifact(dict(id=self.op, source_sha256=self.manifest['source_sha256'],
            understanding_sha256=self.u['_sha256'], review_sha256=self.r['_sha256']), 'resolution')

    def assert_noncarrier_graph(self, before):
        after = copy.deepcopy(self.snapshot()['state'])
        after['revision'] = before['revision']
        for key in ('identity', 'recovery', 'updated_at'):
            after['owners']['alpha'][key] = before['owners']['alpha'][key]
        self.assertEqual(after, before)

    def test_real_commit_precedes_ready_cli_zero_and_preserves_entire_latest_graph(self):
        # Authorized graph edits during the review window must survive. These
        # changes do not alter coordination scopes/ack terms validity.
        def edit(state):
            state['owners']['alpha']['parent'] = 'user'
            state['assignments']['active']['objective'] = 'Newest synthetic accepted objective.'
            state['assignments']['completed']['end_reason'] = 'Newest synthetic completion evidence.'
            state['coordination']['agreement']['sequencing'] = 'Newest confirmed synthetic sequence.'
        self.manual(edit)
        before = self.snapshot()['state']
        trace, original = [], context._publish_at
        def handoff_publish(fd, name, raw):
            if name == 'resolution.json':
                self.assertEqual(len(self.events()), 1)
                self.assertEqual(self.snapshot()['state']['owners']['alpha']['identity']['pi_session_id'], TARGET)
                trace.append('real-event-and-target-current-before-ready')
            return original(fd, name, raw)
        with patch.object(context, '_publish_at', handoff_publish), redirect_stdout(stdio.StringIO()):
            code = self.cli()
        self.assertEqual(code, 0)
        self.allowed_toy_action(code)
        self.assertTrue((self.directory / 'toy-action.txt').is_file())
        trace.extend(['CLI-0', 'toy-action-permitted'])
        self.assertEqual(trace, ['real-event-and-target-current-before-ready', 'CLI-0', 'toy-action-permitted'])
        self.assert_noncarrier_graph(before)
        state = self.snapshot()['state']
        self.assertFalse(any(o['identity']['pi_session_id'] == SOURCE for o in state['owners'].values()))
        self.assertEqual(state['owners']['alpha']['id'], before['owners']['alpha']['id'])
        self.assertEqual(state['owners']['child']['parent'], 'owner:alpha')
        self.assertEqual(state['owners']['alpha']['identity'], self.intent['target']['identity'])
        receipt = json.loads((self.directory / 'succession-receipt.json').read_text())['receipt']
        event = self.events()[0]['event']
        self.assertEqual(receipt['event_id'], event['id'])
        self.assertEqual(event['before']['owners']['alpha']['identity']['pi_session_id'], SOURCE)
        self.assertEqual(event['after']['owners']['alpha']['identity']['pi_session_id'], TARGET)
        self.assertEqual(event['before']['assignments'], {})
        self.assertEqual(event['after']['coordination'], {})
        raw_challenge = json.loads((self.directory / 'succession-source.json').read_text())['challenge']
        self.assertNotIn(raw_challenge, json.dumps(self.events()))
        self.assertTrue(self.fresh()['current_binding_valid'])
        self.assertEqual(self.engine_resolution()['verdict'], 'ready')
        calls = list(self.service.calls)
        with redirect_stdout(stdio.StringIO()), redirect_stderr(stdio.StringIO()):
            self.assertEqual(self.cli(), 2)
        self.assertEqual(self.service.calls, calls)
        self.assertEqual(len(self.events()), 1)

    def test_real_commit_publish_failure_recovers_original_intent_get_first_one_event(self):
        original = context._publish_at
        def handoff_publish(fd, name, raw):
            if name == 'resolution.json':
                raise OSError('synthetic local ready publication failure')
            return original(fd, name, raw)
        with patch.object(context, '_publish_at', handoff_publish), redirect_stdout(stdio.StringIO()), redirect_stderr(stdio.StringIO()):
            code = self.cli()
        self.allowed_toy_action(code)
        self.assertEqual(code, 2)
        self.assertFalse((self.directory / 'toy-action.txt').exists())
        self.assertFalse((self.directory / 'resolution.json').exists())
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.snapshot()['state']['owners']['alpha']['identity']['pi_session_id'], TARGET)
        frozen = (self.directory / 'succession-intent.json').read_bytes()
        receipt = (self.directory / 'succession-receipt.json').read_bytes()
        calls = list(self.service.calls)
        with redirect_stdout(stdio.StringIO()):
            code = self.cli()
        self.assertEqual(code, 0)
        self.allowed_toy_action(code)
        self.assertTrue((self.directory / 'toy-action.txt').is_file())
        self.assertEqual(self.service.calls, calls + ['get'])
        self.assertEqual((self.directory / 'succession-intent.json').read_bytes(), frozen)
        self.assertEqual((self.directory / 'succession-receipt.json').read_bytes(), receipt)
        self.assertEqual(len(self.events()), 1)
        self.assertTrue(self.fresh()['current_binding_valid'])

    def test_ready_survives_legitimate_same_pi_recovery_contact_and_graph_updates(self):
        self.submit(verdict='ready')
        original_receipt = (self.directory / 'succession-receipt.json').read_bytes()
        def update(state):
            state['owners']['alpha']['identity']['intercom_id'] = 'new-real-contact-same-Pi'
            state['owners']['alpha']['identity']['unknown_reason'] = ''
            state['owners']['alpha']['recovery']['summary'] = 'Child result and refreshed recovery context.'
            state['assignments']['active']['objective'] = 'Latest authorized objective after child result.'
        self.manual(update)
        with self.subTest(gate='fresh-api'):
            result = self.adapter.get(self.op, self.intent['intent_digest'])
            self.assertEqual(result['status'], 'already_committed', result)
        with self.subTest(gate='host-validate-ready'):
            self.assertTrue(self.fresh()['current_binding_valid'])
        with self.subTest(gate='engine-ready'):
            self.assertEqual(self.engine_resolution()['verdict'], 'ready')
        with self.subTest(gate='check-owner'), redirect_stdout(stdio.StringIO()), redirect_stderr(stdio.StringIO()):
            self.assertEqual(self.cli(check=True), 0)
        self.assertEqual((self.directory / 'succession-receipt.json').read_bytes(), original_receipt)
        self.assertEqual(len(self.events()), 1)

    def test_no_owner_cli_ready_changes_no_graph_revision_or_event(self):
        self.make_operation('unregistered-source-Pi', 'unregistered-target-Pi')
        before = self.snapshot()
        current = self.document.read_bytes(), self.document.stat().st_mtime_ns
        with redirect_stdout(stdio.StringIO()):
            self.assertEqual(self.cli(), 0)
        self.assertEqual(self.document.read_bytes(), current[0])
        self.assertEqual(self.document.stat().st_mtime_ns, current[1])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.events(), [])
        result = self.fresh()
        self.assertEqual(result['status'], 'not_applicable')
        self.assertTrue(result['no_owner_valid'])
        self.assertFalse(result['current_binding_valid'])
        self.assertIsNone(result['receipt']['owner_id'])
        with redirect_stdout(stdio.StringIO()), redirect_stderr(stdio.StringIO()):
            self.assertEqual(self.cli(check=True), 2)  # ready is not an appointment

    def test_ready_no_owner_disposition_becomes_superseded_when_source_registers(self):
        source = 'unregistered-source-Pi'
        self.make_operation(source, 'unregistered-target-Pi')
        self.submit(verdict='ready')
        self.manual(lambda state: state['owners']['alpha'].update(identity=fixture.identity(source)))
        result = self.adapter.get(self.op, self.intent['intent_digest'])
        self.assertEqual(result['status'], 'superseded')
        self.assertFalse(result['no_owner_valid'])
        with self.assertRaisesRegex(s.GateError, 'superseded'):
            self.fresh()
        with self.assertRaisesRegex(s.GateError, 'superseded'):
            self.engine_resolution()
        self.assertEqual(self.events(), [])
        self.assertEqual(self.snapshot()['state']['revision'], 7)

    def test_no_owner_registered_between_fresh_prepare_and_commit_blocks(self):
        source = 'unregistered-source-Pi'
        self.make_operation(source, 'unregistered-target-Pi')
        self.service.before_commit = lambda: self.manual(
            lambda state: state['owners']['alpha'].update(identity=fixture.identity(source)))
        calls = list(self.service.calls)
        with self.assertRaisesRegex(s.GateError, 'succession_source_changed'):
            self.submit(verdict='ready')
        # Real Store first rejects the stale token; fresh prepare then observes
        # the newly registered owner, so immutable-plan validation blocks retry.
        self.assertEqual(self.service.calls, calls + ['prepare', 'commit', 'prepare'])
        self.assertFalse((self.directory / 'resolution.json').exists())
        self.assertEqual(self.events(), [])
        self.assertEqual(self.snapshot()['state']['revision'], 7)
        self.assertEqual(self.snapshot()['state']['owners']['alpha']['identity']['pi_session_id'], source)

    def test_occupied_target_after_fresh_prepare_blocks_without_stealing(self):
        self.service.before_commit = lambda: self.manual(
            lambda state: state['owners']['child'].update(identity=fixture.identity(TARGET)))
        with self.assertRaisesRegex(s.GateError, 'target_identity_occupied'):
            self.submit(verdict='ready')
        self.assertFalse((self.directory / 'resolution.json').exists())
        self.assertEqual(self.events(), [])
        self.assertEqual(self.snapshot()['state']['owners']['alpha']['identity']['pi_session_id'], SOURCE)
        self.assertEqual(self.snapshot()['state']['owners']['child']['identity']['pi_session_id'], TARGET)

    def test_source_swap_after_initial_prepare_blocks_before_commit(self):
        def swap(state):
            state['owners']['alpha']['identity'] = fixture.identity('different-Pi')
            state['owners']['child']['identity'] = fixture.identity(SOURCE)
        self.manual(swap)
        calls = list(self.service.calls)
        with self.assertRaisesRegex(s.GateError, 'succession_source_changed'):
            self.submit(verdict='ready')
        self.assertEqual(self.service.calls, calls + ['prepare'])
        self.assertEqual(self.events(), [])
        self.assertFalse((self.directory / 'resolution.json').exists())

    def test_two_concurrent_ready_cli_calls_grant_one_permission_one_real_event(self):
        # One shared code-only injection across threads; no nested global patch
        # restoration races. Actual operation flock decides which CLI succeeds.
        with patch.object(s.Adapter, 'load', return_value=self.service), redirect_stdout(stdio.StringIO()), redirect_stderr(stdio.StringIO()):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                codes = list(pool.map(lambda _: self.cli(inject=False), range(2)))
        self.assertEqual(sorted(codes), [0, 2])
        self.assertEqual(self.service.calls, ['prepare', 'prepare', 'commit'])
        self.assertEqual(len(self.events()), 1)
        self.assertTrue(self.fresh()['current_binding_valid'])

    def operation_state(self):
        return {name: getattr(self, name) for name in ('op', 'directory', 'manifest', 'body', 'u', 'r', 'intent')}

    def use_operation(self, values):
        for name, value in values.items():
            setattr(self, name, value)

    def test_competing_other_source_commit_preserved_by_bounded_same_intent_cas_retry(self):
        first = self.operation_state()
        self.make_operation('synthetic-child-Pi', 'actual-child-target-Pi')
        second = self.operation_state()
        self.use_operation(first)
        original_intent = copy.deepcopy(self.intent)
        before = self.snapshot()['state']
        def competing_helper():
            self.use_operation(second)
            try:
                self.submit(verdict='ready')
            finally:
                self.use_operation(first)
        self.service.before_commit = competing_helper
        self.submit(verdict='ready')
        self.assertEqual(len(self.events()), 2)
        self.assertEqual(self.intent, original_intent)
        after = self.snapshot()['state']
        self.assertEqual(after['owners']['alpha']['identity']['pi_session_id'], TARGET)
        self.assertEqual(after['owners']['child']['identity']['pi_session_id'], 'actual-child-target-Pi')
        self.assertEqual(after['revision'], before['revision'] + 2)
        self.assertEqual(after['assignments'], before['assignments'])
        self.assertEqual(after['coordination'], before['coordination'])
        self.assertEqual(self.service.calls.count('commit'), 3)  # 1 competitor, 1 stale, 1 retry
        self.assertTrue(self.fresh()['current_binding_valid'])
        self.use_operation(second)
        self.assertTrue(self.fresh()['current_binding_valid'])

    def assert_old_carrier_rejected(self):
        result = self.adapter.get(self.op, self.intent['intent_digest'])
        self.assertEqual(result['status'], 'superseded')
        self.assertFalse(result['current_binding_valid'])
        stored = json.loads((self.directory / 'succession-receipt.json').read_text())
        # Fresh eligibility observes current; historical post-publication token
        # stays bound to the original event, never manufactured from current.
        self.assertEqual(result['receipt'], stored['receipt'])
        self.assertNotEqual(result['receipt']['document_token_after'], self.snapshot()['document_token'])
        with self.assertRaisesRegex(s.GateError, 'superseded'):
            self.fresh()
        with self.assertRaisesRegex(s.GateError, 'superseded'):
            self.engine_resolution()
        with redirect_stdout(stdio.StringIO()), redirect_stderr(stdio.StringIO()):
            self.assertEqual(self.cli(check=True), 2)

    def test_ready_target_duplicate_pi_claim_supersedes_without_rewriting_receipt(self):
        self.submit(verdict='ready')
        original = (self.directory / 'succession-receipt.json').read_bytes()
        self.manual(lambda state: state['owners']['child'].update(identity=fixture.identity(TARGET)))
        self.assert_old_carrier_rejected()
        self.assertEqual((self.directory / 'succession-receipt.json').read_bytes(), original)
        self.assertEqual(len(self.events()), 1)

    def test_actual_a_to_b_to_c_then_reset_supersedes_historical_receipts(self):
        self.submit(verdict='ready')
        first = self.operation_state()
        first_receipt = (self.directory / 'succession-receipt.json').read_bytes()
        self.make_operation(TARGET, 'actual-third-Pi')
        self.submit(verdict='ready')
        second = self.operation_state()
        self.assertTrue(self.fresh()['current_binding_valid'])
        self.use_operation(first)
        self.assert_old_carrier_rejected()
        self.assertEqual((self.directory / 'succession-receipt.json').read_bytes(), first_receipt)
        self.assertEqual(len(self.events()), 2)
        self.use_operation(second)
        self.assert_temporary()
        self.store.switch_document('clear', self.snapshot()['document_token'], 'user',
                                   'Synthetic explicit reset.', 'Synthetic test authorization.')
        self.assert_old_carrier_rejected()
        self.assertEqual(self.snapshot()['state']['owners'], {})

    def test_commit_publish_failure_then_new_carrier_cannot_recover_old_ready(self):
        original = context._publish_at
        def handoff_publish(fd, name, raw):
            if name == 'resolution.json':
                raise OSError('synthetic local ready publication failure')
            return original(fd, name, raw)
        with patch.object(context, '_publish_at', handoff_publish), self.assertRaises(context.ContextError):
            self.submit(verdict='ready')
        old = self.operation_state()
        self.make_operation(TARGET, 'actual-third-Pi')
        self.submit(verdict='ready')
        self.use_operation(old)
        calls = list(self.service.calls)
        with self.assertRaisesRegex(s.GateError, 'superseded'):
            self.submit(verdict='ready')
        self.assertEqual(self.service.calls, calls + ['get'])
        self.assertFalse((self.directory / 'resolution.json').exists())
        self.assertEqual(len(self.events()), 2)

    def test_prepare_then_actual_reset_blocks_without_new_succession(self):
        self.assert_temporary()
        self.store.switch_document('clear', self.snapshot()['document_token'], 'user',
                                   'Synthetic explicit reset.', 'Synthetic test authorization.')
        calls = list(self.service.calls)
        with self.assertRaisesRegex(s.GateError, 'succession_source_changed'):
            self.submit(verdict='ready')
        self.assertEqual(self.service.calls, calls + ['prepare'])
        self.assertFalse(any(item['event'] and item['event']['action'] == 'session-succession' for item in self.events()))
        self.assertFalse((self.directory / 'resolution.json').exists())

    def test_fixed_installed_adapter_other_cwd_poisoned_namespaces_actual_temp_store(self):
        home, elsewhere = self.base / 'home', self.base / 'other-cwd'
        scripts = home / '.pi/agent/skills/responsibility-registry/scripts'
        scripts.mkdir(parents=True)
        elsewhere.mkdir()
        for source in (REGISTRY / 'scripts').glob('*.py'):
            shutil.copyfile(source, scripts / source.name)
        poison = {name: types.ModuleType(name) for name in ('registry', 'registry_document', 'audit_storage')}
        before_path, before_cwd = list(sys.path), Path.cwd()
        try:
            with patch.dict(os.environ, HOME=str(home)), patch.dict(sys.modules, poison):
                os.chdir(elsewhere)
                installed = s.Adapter().api()  # real fixed installed-path loader
                self.assertEqual(Path(installed.__file__), scripts / 'session_succession.py')
                core = installed._sibling('registry')
                self.assertTrue(core.Store.__module__.startswith(installed._PACKAGE + '.'))
                self.store = core.Store(self.root, self.document)
                self.service = BoundService(self, installed)
                self.adapter = s.Adapter(self.service)
                self.assertEqual(sys.path, before_path)
                self.assertTrue(all(sys.modules[name] is value for name, value in poison.items()))
                self.make_operation()
                with redirect_stdout(stdio.StringIO()):
                    self.assertEqual(self.cli(), 0)
                self.assertEqual(len(self.events()), 1)
                self.assertTrue(self.fresh()['current_binding_valid'])
                self.assertTrue(all(sys.modules[name] is value for name, value in poison.items()))
        finally:
            os.chdir(before_cwd)
        self.assertEqual(sys.path, before_path)


if __name__ == '__main__':
    unittest.main()
