"""Preparation/observer proofs only: no model requests or actors are started."""
import copy
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

import owner_succession_native as native


class OwnerSuccessionNativeTests(unittest.TestCase):
    def auth(self, ttl=2400):
        return dict(type='oauth', access='FAKE_UNIT_ACCESS', refresh='FAKE_REFRESH_MUST_NOT_SURVIVE',
                    expires=(time.time() + ttl) * 1000, clientId='unit-client', scopes=['unit'],
                    unexpected_secret='FAKE_MUST_NOT_SURVIVE')

    def test_access_only_copy_retains_truthful_expiry_and_account_metadata(self):
        original = self.auth()
        copied = native.oauth_access_only(original)
        self.assertEqual(copied['refresh'], 'TEST_ONLY_REFRESH_DISABLED')
        self.assertEqual(copied['expires'], original['expires'])
        self.assertEqual(copied['clientId'], original['clientId'])
        self.assertNotIn('unexpected_secret', copied)
        self.assertEqual(original['refresh'], 'FAKE_REFRESH_MUST_NOT_SURVIVE')

    def test_exact_ttl_boundary_and_invalid_entries(self):
        auth = self.auth()
        auth['expires'] = (1000 + 1200) * 1000
        native.oauth_access_only(auth, now=1000)
        with self.assertRaisesRegex(ValueError, 'auth_ttl_insufficient'):
            native.oauth_access_only(auth, now=1000.001)
        for field, value in [('type', 'api_key'), ('access', ''), ('expires', 'tomorrow')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                native.oauth_access_only({**self.auth(), field: value})

    def test_environment_is_constructed_not_inherited(self):
        with patch.dict(os.environ, dict(HOME='/real-home', SSH_AUTH_SOCK='/real-socket',
                PI_INTERCOM_SOCKET='/real-intercom', OPENAI_API_KEY='FAKE_NOT_ALLOWED',
                CODEX_WEB_PASSWORD='FAKE_NOT_ALLOWED', TMUX='/real-tmux'), clear=True):
            env = native.clean_environment(Path('/tmp/codoxear-owner-succession-unit'), '/installed/node/bin/pi')
        for forbidden in ('SSH_AUTH_SOCK', 'PI_INTERCOM_SOCKET', 'OPENAI_API_KEY', 'TMUX'):
            self.assertNotIn(forbidden, env)
        self.assertEqual(env['HOME'], '/tmp/codoxear-owner-succession-unit/home')
        self.assertIn('/installed/node/bin', env['PATH'])
        self.assertNotEqual(env['CODEX_WEB_PASSWORD'], 'FAKE_NOT_ALLOWED')

    def files(self, ttl=2400):
        parent = Path(tempfile.mkdtemp(prefix='native-owner-unit-'))
        self.addCleanup(shutil.rmtree, parent)
        auth_source = parent / 'auth.json'
        auth_source.write_text(json.dumps({'openai': self.auth(ttl)}))
        registration = dict(id=native.MODEL, provider='openai', api='openai-responses',
                            input=['text', 'image'], contextWindow=272000, maxTokens=128000,
                            thinkingLevelMap={'xhigh': 'xhigh'})
        (parent / 'models-store.json').write_text(json.dumps({'openai': {'models': [registration]}}))
        return parent, auth_source

    def prepared(self):
        parent, auth_source = self.files()
        with redirect_stdout(io.StringIO()):
            root = native.prepare(Path('/usr/bin/true'), Path('/usr/bin/python3'), auth_source)
        self.addCleanup(shutil.rmtree, root)
        return root

    def test_insufficient_ttl_preserves_first_failure_without_copy_or_process(self):
        parent, auth_source = self.files(ttl=1199)
        root = parent / 'codoxear-owner-succession-failed'
        root.mkdir()
        with patch.object(native.tempfile, 'mkdtemp', return_value=str(root)), \
                patch.object(native.subprocess, 'run') as process, redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'auth_ttl_insufficient'):
                native.prepare(Path('/usr/bin/true'), Path('/usr/bin/python3'), auth_source)
        process.assert_not_called()
        self.assertEqual(sorted(str(path.relative_to(root)) for path in root.rglob('*')),
                         ['evidence', 'evidence/result.json'])
        self.assertEqual(json.loads((root / 'evidence/result.json').read_text())['status'], 'preflight-failed')

    def test_prepared_paths_auth_model_shims_and_no_daily_config(self):
        root = self.prepared()
        auth_path = root / 'home/.pi/agent/auth.json'
        auth = json.loads(auth_path.read_text())
        self.assertEqual(auth_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(set(auth), {'openai'})
        self.assertEqual(auth['openai']['refresh'], 'TEST_ONLY_REFRESH_DISABLED')
        self.assertNotIn('unexpected_secret', auth['openai'])
        selected = json.loads((root / 'home/.pi/agent/models-store.json').read_text())
        self.assertEqual(list(selected), ['openai'])
        self.assertEqual([row['id'] for row in selected['openai']['models']], [native.MODEL])
        settings = json.loads((root / 'home/.pi/agent/settings.json').read_text())
        self.assertEqual(settings['defaultThinkingLevel'], 'xhigh')
        self.assertFalse(settings['retry']['enabled'])
        self.assertEqual(settings['extensions'], [])
        self.assertEqual(settings['packages'], [])
        shim = (root / 'bin/pi').read_text()
        self.assertIn('"$@"', shim)
        self.assertIn('--no-extensions', shim)
        self.assertIn('umask 077', shim)
        self.assertIn('/pi-launch-$$.argv', shim)
        self.assertNotIn('--system-prompt', shim)
        self.assertIn(' -S ' + str(root / 'tmux.sock'), (root / 'bin/tmux').read_text())
        self.assertFalse((root / 'tmux.sock').exists())
        self.assertTrue((root / 'home/.pi/agent/skills/responsibility-registry/scripts/session_succession.py').is_file())
        launched = native.subprocess.Popen([str(root / 'bin/pi'), '--extension', '/installed/pi_active_session_bridge.ts'])
        self.assertEqual(launched.wait(timeout=5), 0)
        launch_path = root / 'evidence' / f'pi-launch-{launched.pid}.argv'
        self.assertEqual(launch_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(launch_path.read_bytes().split(b'\0')[-3:],
                         [b'--extension', b'/installed/pi_active_session_bridge.ts', b''])

    def test_isolation_accepts_private_tmpdir_and_rejects_extra_environment(self):
        root = self.prepared()
        env = json.loads((root / 'environment.json').read_text())
        with patch.dict(os.environ, env, clear=True):
            # TMPDIR is inside root, not the root's parent: no gettempdir confusion.
            self.assertEqual(native.check_isolation(root), root)
            os.environ['SSH_AUTH_SOCK'] = '/not-allowed'
            with self.assertRaisesRegex(ValueError, 'allowlist'):
                native.check_isolation(root)
        env['SSH_AUTH_SOCK'] = '/not-allowed'
        native.private_json(root / 'environment.json', env)
        with patch.dict(os.environ, env, clear=True), self.assertRaisesRegex(ValueError, 'allowlist'):
            native.check_isolation(root)

    def test_runtime_ttl_failure_erases_access_and_password_files(self):
        import asyncio
        root = self.prepared()
        auth_path = root / 'home/.pi/agent/auth.json'
        auth = json.loads(auth_path.read_text())
        auth['openai']['expires'] = (time.time() + 1199) * 1000
        native.private_json(auth_path, auth)
        env = json.loads((root / 'environment.json').read_text())
        with patch.dict(os.environ, env, clear=True), patch.object(native.subprocess, 'run') as launch:
            with self.assertRaisesRegex(ValueError, 'auth_ttl_insufficient'):
                asyncio.run(native.run(root))
        launch.assert_not_called()
        self.assertFalse(auth_path.exists())
        self.assertFalse((root / 'environment.json').exists())
        self.assertEqual(json.loads((root / 'evidence/result.json').read_text())['status'], 'preflight-failed')

    def test_path_escape_and_symlink_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(ValueError):
                native.within(root / '../other', root)
            (root / 'link').symlink_to('/etc')
            with self.assertRaises(ValueError):
                native.within(root / 'link', root)

    def test_real_installed_default_store_uses_only_private_paths_with_uuid_graph(self):
        root = self.prepared()
        env = json.loads((root / 'environment.json').read_text())
        config = json.loads((root / 'config.json').read_text())
        spec = importlib.util.spec_from_file_location('_native_owner_unit_factory', native.REPO / 'pi-agent/skills/responsibility-registry/tests/test_session_succession.py')
        factory = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(factory)
        source = 'actual-unit-header-Pi'
        before, ids = native.synthetic_graph(factory, source, Path(config['seed']), Path(config['project']))
        self.assertTrue(all(str(uuid.UUID(key)) == key for key in before['owners']))
        self.assertEqual(before['owners'][ids['child']]['parent'], 'owner:' + ids['alpha'])
        self.assertIsNone(before['owners'][ids['superior']]['identity']['pi_session_id'])
        with patch.dict(os.environ, env, clear=True):
            api = native.load_api(root / 'home/.pi/agent/skills/responsibility-registry/scripts/session_succession.py')
            store = api._sibling('registry').Store()
            self.assertEqual(store.root, root / 'home/.pi/agent/.memory/local/responsibility-registry')
            self.assertEqual(store.document, root / 'home/.pi/agent/skills/responsibility-registry/RESPONSIBILITIES.md')
            store.initialize()
            store.document.write_text(api._codec.serialize_current_document(before, factory.ERA))
            self.assertEqual(store.current_snapshot()['state'], before)
            self.assertEqual(native.events(store), [])
            plan = api.prepare_owner_session_succession(str(uuid.uuid4()), source)
            self.assertEqual(plan['status'], 'prepared')
            self.assertEqual(plan['plan']['owner_id'], ids['alpha'])
        after = copy.deepcopy(before)
        after['revision'] += 1
        after['owners'][ids['alpha']]['identity']['pi_session_id'] = 'target-unit-Pi'
        native.assert_graph(before, after, ids['alpha'], source, 'target-unit-Pi')
        after['assignments']['active']['status'] = 'ended'
        with self.assertRaisesRegex(AssertionError, 'non-carrier'):
            native.assert_graph(before, after, ids['alpha'], source, 'target-unit-Pi')

    def trace(self):
        rows, calls, results = [], [], {}
        commands = ['cat "$CODEX_WEB_PI_ACTIVE_SESSION_FILE"',
                    'python handoff_io.py attest --kind identity',
                    'python handoff_io.py submit --kind understanding',
                    'python handoff_io.py submit --kind resolution --verdict ready',
                    'python handoff_io.py check-owner']
        outputs = [{'sessionId': 'actual-target-Pi', 'sessionFile': '/temporary/session.jsonl', 'bridgeVersion': 2, 'pid': 1234}, {'kind': 'identity', 'accepted': True}, {'kind': 'understanding'},
                   {'kind': 'resolution', 'artifact': {'verdict': 'ready'}}, {'current_binding_valid': True}]
        for index, (command, output) in enumerate(zip(commands, outputs)):
            call = dict(id=str(index), type='toolCall', name='bash', arguments={'command': command})
            result = dict(role='toolResult', toolCallId=str(index), content=[dict(type='text', text=json.dumps(output))])
            calls.append(call)
            results[str(index)] = result
            rows.extend([{'message': {'role': 'assistant', 'content': [call]}}, {'message': result}])
        edit = dict(id='edit', type='toolCall', name='edit', arguments={'path': '/temporary/project/main.py'})
        calls.append(edit)
        rows.append({'message': {'role': 'assistant', 'content': [edit]}})
        return rows, calls, results

    def test_actor_trace_requires_real_helper_outputs_before_separate_edit(self):
        rows, calls, results = self.trace()
        native.assert_actor_tools(rows, calls, results, target=True, identity=dict(pi_session_id='actual-target-Pi', session_file='/temporary/session.jsonl'))
        results['3']['isError'] = True
        with self.assertRaisesRegex(AssertionError, 'returned an error'):
            native.assert_actor_tools(rows, calls, results, target=True, identity=dict(pi_session_id='actual-target-Pi', session_file='/temporary/session.jsonl'))

    def test_forged_files_without_actor_calls_and_check_after_edit_fail(self):
        rows, calls, results = self.trace()
        with self.assertRaisesRegex(AssertionError, 'missing/duplicate'):
            native.assert_actor_tools(rows, calls[2:], results, target=True, identity=dict(pi_session_id='actual-target-Pi', session_file='/temporary/session.jsonl'))
        rows[-1], rows[-2] = rows[-2], rows[-1]
        with self.assertRaisesRegex(AssertionError, 'preceded current-owner'):
            native.assert_actor_tools(rows, calls, results, target=True, identity=dict(pi_session_id='actual-target-Pi', session_file='/temporary/session.jsonl'))

    def test_native_bridge_crosschecks_actual_manager_pid_and_explicit_extension(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'home/.local/share/codoxear/pi-active-sessions/broker-12.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(dict(sessionId='actual-Pi', sessionFile='/temporary/session.jsonl',
                bridgeVersion=2, pid=1234)))
            launch = root / 'evidence/pi-launch-1234.argv'
            launch.parent.mkdir()
            launch.write_bytes(b'node\0pi\0--no-extensions\0-e\0/installed/pi_active_session_bridge.ts\0')
            launch.chmod(0o600)
            original = Path.read_bytes
            def read_bytes(value):
                return b'pi\0' if str(value) == '/proc/1234/cmdline' else original(value)
            with patch.object(native, 'proc_identity', return_value=dict(pid=1234, ppid=12, start_ticks='99')) as process, \
                    patch.object(Path, 'read_bytes', autospec=True, side_effect=read_bytes):
                observed = native.runtime_bridge(root, dict(broker_pid=12), 'actual-Pi', '/temporary/session.jsonl')
                self.assertEqual(observed['process']['pid'], 1234)
                self.assertEqual(observed['proc_title'], ['pi'])
                self.assertEqual(observed['launch_argv_path'], str(launch))
                with self.assertRaises(AssertionError):
                    native.runtime_bridge(root, dict(broker_pid=12), 'expected-but-not-actual-Pi', '/temporary/session.jsonl')
                process.return_value = dict(pid=1234, ppid=99, start_ticks='99')
                with self.assertRaisesRegex(AssertionError, 'not the native broker child'):
                    native.runtime_bridge(root, dict(broker_pid=12), 'actual-Pi', '/temporary/session.jsonl')
                process.return_value = dict(pid=1234, ppid=12, start_ticks='99')
                launch.write_bytes(b'node\0pi\0--no-extensions\0')
                with self.assertRaisesRegex(AssertionError, 'not explicitly loaded'):
                    native.runtime_bridge(root, dict(broker_pid=12), 'actual-Pi', '/temporary/session.jsonl')

    def test_transcript_partial_final_line_is_not_a_model_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'session.jsonl'
            complete = json.dumps({'type': 'session', 'id': 'actual-Pi'}) + '\n'
            path.write_text(complete + '{"type":"message"')
            rows, calls, results = native.transcript_calls(path)
            self.assertEqual(rows, [{'type': 'session', 'id': 'actual-Pi'}])
            self.assertEqual((calls, results), ([], {}))
            path.write_text('broken-complete-line\n')
            with self.assertRaises(json.JSONDecodeError):
                native.transcript_calls(path)

    def test_source_custody_and_review_must_be_actor_produced(self):
        calls, results = [], {}
        for kind, output in [('custody', {'kind': 'custody', 'accepted': True}), ('review', {'kind': 'review'})]:
            calls.append(dict(id=kind, name='bash', arguments={'command': 'python handoff_io.py ' + ('attest' if kind == 'custody' else 'submit') + ' --kind ' + kind}))
            results[kind] = dict(content=[dict(type='text', text=json.dumps(output))])
        native.assert_actor_tools([], calls, results, target=False)
        del results['custody']
        with self.assertRaisesRegex(AssertionError, 'no actual tool result'):
            native.assert_actor_tools([], calls, results, target=False)


if __name__ == '__main__':
    unittest.main()
