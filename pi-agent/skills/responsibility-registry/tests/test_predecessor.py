"""Fixture-only predecessor recovery; never run a real Pi/provider/session."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_registry import appointment, registry

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


class PredecessorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='predecessor-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'registry'
        self.document = self.base / 'RESPONSIBILITIES.md'
        self.project = self.base / 'original project'
        self.project.mkdir()
        (self.project / '.git').mkdir()
        self.session = self.base / 'original-session.jsonl'
        self.original = (json.dumps({'type': 'session', 'version': 3, 'id': 'synthetic-old-pi', 'cwd': str(self.project),
                                     'timestamp': '2025-01-01T00:00:00.000Z'}) + '\n' +
                         json.dumps({'type': 'message', 'id': 'fixture-message', 'parentId': None,
                                     'timestamp': '2025-01-01T00:00:01.000Z',
                                     'message': {'role': 'user', 'content': 'Decision: keep interface v1', 'timestamp': 1735689601000}}) + '\n')
        self.session.write_text(self.original)
        self.question = self.base / 'question.md'
        self.question.write_text('Why was interface v1 retained? Explain evidence and uncertainty.')
        self.output = self.base / 'consultation plan'
        self.cli('registry.py', 'init')
        old = appointment(path=str(self.project))
        old['owner']['identity'].update(pi_session_id='synthetic-old-pi', intercom_id='synthetic-contact', source='current-session', unknown_reason='')
        self.recovery = dict(session_file=str(self.session), cwd=str(self.project), repo=str(self.project / '.git'),
                             worktree=str(self.project), summary='Fixture handover summary', decisions='Keep interface v1',
                             pending_issues='Explain v1 rationale', context_refs=['fixture-message'],
                             source='Synthetic current-session header verified by fixture', unknown_reason='')
        old['owner']['recovery'] = self.recovery
        self.old = old
        self.cli('registry.py', 'register', '--actor', 'alpha', payload=old)
        new = appointment('beta')
        new.pop('assignment')
        self.cli('registry.py', 'register', '--actor', 'beta', payload=new)
        self.cli('registry.py', 'handover', '--actor', 'alpha', '--assignment', 'release', '--to-owner', 'beta',
                 '--new-assignment', 'release-v2', '--expected-revision', '2', '--reason', 'Fixture transfer with decision context',
                 '--pending-work', 'Verified no running fixture children')

    def cli(self, script, *args, payload=None, ok=True, env=None):
        prefix = [sys.executable, str(SCRIPTS / script)]
        if script == 'registry.py':
            prefix += ['--root', str(self.root), '--document', str(self.document)]
            if args[0] not in ['init', 'query', 'rebuild']:
                args = (*args, '--expected-document', self.cli('registry.py', 'query')['document_token'])
        result = subprocess.run(prefix + list(args), input=json.dumps(payload) if payload is not None else None,
                                text=True, capture_output=True, timeout=20, env=env)
        self.assertEqual(result.returncode, 0 if ok else 2, result.stderr)
        return json.loads(result.stdout) if ok else result.stderr

    def prepare(self, ok=True, env=None, assignment='release-v2'):
        return self.cli('predecessor.py', 'prepare', '--root', str(self.root), '--document', str(self.document), '--actor', 'beta', '--assignment', assignment,
                        '--question-file', str(self.question), '--model', 'fixture/model-not-executed', '--output', str(self.output), ok=ok, env=env)

    def state(self):
        return registry.parse_document(self.document.read_text())[0]

    def test_reset_refuses_old_actor_assignment_and_prepared_plan_remains_historical(self):
        self.prepare()
        plan = json.loads((self.output / 'plan.json').read_text())
        self.assertEqual(plan['era'], self.cli('registry.py', 'query')['era'])
        self.assertEqual(len(plan['document_token']), 64)
        self.document.unlink()
        import shutil
        shutil.copyfile(SCRIPTS.parent / 'RESPONSIBILITIES.template.md', self.document)
        other = self.base / 'new-plan'
        self.output = other
        self.assertIn('not registered', self.prepare(ok=False))
        self.assertFalse(other.exists())
        self.assertEqual(self.cli('registry.py', 'query')['owners'], {})

    def test_pre_cutover_plan_can_record_historical_advice_without_fabricating_era(self):
        self.prepare()
        plan_path = self.output / 'plan.json'
        plan = json.loads(plan_path.read_text())
        plan.pop('era'); plan.pop('document_token')
        plan_path.write_text(json.dumps(plan))
        answer = self.base / 'old-plan-advice.md'
        answer.write_text('Synthetic historical advice only')
        event = self.cli('predecessor.py', 'record', '--directory', str(self.output), '--kind', 'reconstruction',
                         '--answer-file', str(answer), '--evidence', 'old copied plan', '--uncertainty', 'era unknown')
        self.assertIsNone(event['era'])
        self.assertIsNone(event['document_token'])
        self.assertFalse(event['ownership_restored'])

    def test_locator_and_decision_snapshot_survive_owner_session_change_and_rebuild(self):
        snapshot = self.state()['assignments']['release-v2']['handover_obligations']['predecessor_context']
        self.assertEqual(snapshot['pi_session_id'], 'synthetic-old-pi')
        self.assertEqual(snapshot['recovery'], self.recovery)
        self.assertEqual(snapshot['handover_summary'], 'Fixture transfer with decision context')
        old = self.old['owner']
        old['identity']['pi_session_id'] = 'synthetic-new-session'
        old['recovery'] = None  # New current session unavailable; do not erase old snapshot.
        self.cli('registry.py', 'update-owner', '--actor', 'alpha', '--expected-revision', '3', '--reason', 'Fixture old owner starts another session', payload=old)
        self.cli('registry.py', 'rebuild')
        plan = self.prepare()
        self.assertFalse(plan['launched'])
        persisted = json.loads((self.output / 'plan.json').read_text())
        self.assertEqual(persisted['source']['session_id'], 'synthetic-old-pi')
        self.assertEqual(persisted['predecessor']['recovery']['decisions'], 'Keep interface v1')
        history = self.state()['history'][2]['after']['assignments']['release-v2']['handover_obligations']['predecessor_context']
        self.assertEqual(history, snapshot)

    def test_wrong_session_and_unavailable_file_or_cwd_refuse_without_guessing(self):
        before = self.document.read_bytes()
        self.session.write_text(self.original.replace('synthetic-old-pi', 'wrong-session'))
        self.assertIn('session ID/header mismatch', self.prepare(ok=False))
        self.assertFalse(self.output.exists())
        self.session.write_text(self.original)
        self.session.rename(self.base / 'hidden-session.jsonl')
        self.assertIn('session file unavailable', self.prepare(ok=False))
        self.assertFalse(self.output.exists())
        (self.base / 'hidden-session.jsonl').rename(self.session)
        self.project.rename(self.base / 'vanished project')
        self.assertIn('cwd unavailable', self.prepare(ok=False))
        self.assertEqual(self.document.read_bytes(), before)
        self.assertFalse(self.output.exists())

    def test_nullable_locator_refuses_even_when_a_similar_session_exists(self):
        old = self.old
        old['owner']['recovery']['session_file'] = None
        old['owner']['recovery']['unknown_reason'] = 'Fixture locator unavailable; do not search nearby sessions'
        self.cli('registry.py', 'update-owner', '--actor', 'alpha', '--expected-revision', '3', '--reason', 'Fixture missing locator', payload=old['owner'])
        old['assignment'].update(id='unknown-locator', task='second fixture direction')
        self.cli('registry.py', 'register', '--actor', 'alpha', payload=old)
        self.cli('registry.py', 'handover', '--actor', 'alpha', '--assignment', 'unknown-locator', '--to-owner', 'beta',
                 '--new-assignment', 'unknown-successor', '--expected-revision', '5', '--reason', 'Fixture unknown locator transfer',
                 '--pending-work', 'Verified no running fixture children')
        before = self.document.read_bytes()
        self.assertTrue(self.session.is_file(), 'similar known session deliberately remains nearby')
        self.assertIn('session-file locator unavailable', self.prepare(ok=False, assignment='unknown-successor'))
        self.assertFalse(self.output.exists())
        self.assertEqual(before, self.document.read_bytes())

    def test_prepare_never_launches_and_fake_execution_preserves_original_transcript(self):
        fakebin = self.base / 'bin'
        fakebin.mkdir()
        marker = self.base / 'fake-cli-call.json'
        fake = fakebin / 'pi'
        fake.write_text('#!' + sys.executable + '\nimport json,os,sys\nfrom pathlib import Path\n'
                        'Path(os.environ["FAKE_CALL"]).write_text(json.dumps({"argv":sys.argv[1:],"cwd":os.getcwd()}))\n'
                        'print("Synthetic fixture advice, not a live predecessor reply")\n')
        fake.chmod(0o700)
        env = {**os.environ, 'PATH': str(fakebin) + os.pathsep + os.environ['PATH'], 'FAKE_CALL': str(marker)}
        original_stat = self.session.stat()
        self.prepare(env=env)
        self.assertFalse(marker.exists(), 'prepare must not launch Pi')
        result = subprocess.run(['bash', str(self.output / 'launch.sh')], env=env, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        call = json.loads(marker.read_text())
        self.assertEqual(call['cwd'], str(self.project))
        argv = call['argv']
        self.assertEqual(argv[argv.index('--fork') + 1], str(self.output / 'predecessor.jsonl'))
        self.assertNotIn(str(self.session), argv)
        for flag in ['--no-tools', '--no-extensions', '--no-mcp', '--no-skills', '--no-context-files', '--no-approve', '--print']:
            self.assertIn(flag, argv)
        for flag in ['--session', '--resume', '--continue', '--no-session']:
            self.assertNotIn(flag, argv)
        self.assertEqual(self.session.read_text(), self.original)
        self.assertEqual(self.session.stat().st_mtime_ns, original_stat.st_mtime_ns)
        self.assertEqual((self.output / 'predecessor.jsonl').read_text(), self.original)
        self.assertEqual((self.output / 'predecessor.jsonl').stat().st_mode & 0o777, 0o400)

    def test_consultation_log_attributes_reconstruction_without_restoring_ownership(self):
        before = self.document.read_bytes()
        self.prepare()
        answer = self.base / 'advice.md'
        answer.write_text('Reconstruction: fixture-message says keep v1; rationale beyond that is unknown.')
        event = self.cli('predecessor.py', 'record', '--directory', str(self.output), '--kind', 'reconstruction',
                         '--answer-file', str(answer), '--evidence', 'fixture-message and handover decisions',
                         '--uncertainty', 'No live predecessor consulted; rationale is incomplete')
        self.assertFalse(event['ownership_restored'])
        self.assertEqual(event['kind'], 'reconstruction')
        self.assertEqual(event['source']['sha256'], hashlib.sha256(self.original.encode()).hexdigest())
        self.assertEqual(event['predecessor']['id'], 'alpha')
        self.assertIn('interface v1', event['question'])
        self.assertIn('unknown', event['answer'])
        events = [json.loads(line) for line in (self.output / 'consultation.jsonl').read_text().splitlines()]
        self.assertEqual([e['order'] for e in events], [1, 2])
        self.assertRegex(event['at'], r'T\d{2}:\d{2}:\d{2}\+00:00$')
        self.assertEqual(self.document.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
