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


class DexGemOfflineTests(unittest.TestCase):
    """Only synthetic config/key files and /usr/bin/true; never real Pi/auth."""
    KEY = 'SYNTHETIC_DEXGEM_UNIT_KEY'

    def files(self, expression=None):
        parent = Path(tempfile.mkdtemp(prefix='dexgem-owner-unit-'))
        self.addCleanup(shutil.rmtree, parent)
        home = parent / 'original-home'
        source = home / '.pi/agent/models.json'
        source.parent.mkdir(parents=True)
        key = home / 'synthetic-key.private'
        key.write_text(self.KEY + '\n')
        model = dict(id=native.MODEL, name='Sol (synthetic registration)', reasoning=True,
            input=['text', 'image'], contextWindow=690000, maxTokens=128000,
            thinkingLevelMap={'high': 'high', 'xhigh': 'xhigh'},
            compat={'supportsStrictMode': True})
        route = dict(baseUrl=native.DEXGEM_URL, api='openai-responses',
            compat={'supportsDeveloperRole': True, 'supportsLongCacheRetention': False},
            apiKey=expression or '!cat ~/synthetic-key.private',
            models=[model, {'id': 'UNSELECTED_MODEL', 'apiKey': 'MUST_NOT_COPY'}])
        registry = {'providers': {'dexgem-responses': route, 'unselected': {'apiKey': 'MUST_NOT_COPY'}}}
        source.write_text(json.dumps(registry))
        return home, source, key, registry

    def prepared(self, files=None):
        home, source, key, registry = files or self.files()
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (source, key)}
        with redirect_stdout(io.StringIO()):
            root = native.prepare('/usr/bin/true', '/usr/bin/python3', provider='dexgem-responses',
                                  models_source=source, registration_home=home)
        self.addCleanup(shutil.rmtree, root)
        self.assertEqual(before, {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in before})
        return root

    def test_literal_tilde_uses_pinned_registration_home_not_inherited_or_actor_home(self):
        files = self.files()
        home, source, key, registry = files
        other = home.parent / 'wrong-actor-home'
        other.mkdir()
        (other / key.name).write_text('WRONG_HOME_KEY')
        with patch.dict(os.environ, {'HOME': str(other), 'DEXGEM_API_KEY': 'INHERITED_BAD_KEY'}, clear=True):
            root = self.prepared(files)
        auth = json.loads((root / 'home/.pi/agent/auth.json').read_text())
        self.assertEqual(auth, {'dexgem-responses': {'type': 'api_key', 'key': self.KEY}})
        self.assertNotEqual(root / 'home', home)
        env = json.loads((root / 'environment.json').read_text())
        self.assertEqual(env['HOME'], str(root / 'home'))
        self.assertNotIn('DEXGEM_API_KEY', env)
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(native.check_isolation(root), root)

    def test_all_exact_cat_executables_and_absolute_operand_read_once_without_execution(self):
        home, source, key, registry = self.files()
        for executable in ('cat', '/bin/cat', '/usr/bin/cat'):
            with self.subTest(executable=executable):
                registry['providers']['dexgem-responses']['apiKey'] = '!' + executable + ' ' + str(key)
                source.write_text(json.dumps(registry))
                with patch.object(native.os, 'open', wraps=native.os.open) as opened, \
                        patch.object(native.subprocess, 'run') as execute:
                    public, auth = native.dexgem_credentials(source, home)
                self.assertEqual(auth['key'], self.KEY)
                self.assertEqual(opened.call_count, 1)
                self.assertEqual(opened.call_args.args[0], key)
                execute.assert_not_called()
                self.assertNotIn('apiKey', public['providers']['dexgem-responses'])

    def test_only_selected_public_model_and_private_literal_auth_survive(self):
        files = self.files()
        home, source, key, registry = files
        root = self.prepared(files)
        agent = root / 'home/.pi/agent'
        auth_path = agent / 'auth.json'
        self.assertEqual(auth_path.stat().st_mode & 0o777, 0o600)
        public = json.loads((agent / 'models.json').read_text())
        selected = public['providers']['dexgem-responses']
        route = registry['providers']['dexgem-responses']
        self.assertEqual(selected, {**{k: v for k, v in route.items() if k not in ('models', 'apiKey')},
                                    'models': [route['models'][0]]})
        self.assertFalse((agent / 'models-store.json').exists())
        settings = json.loads((agent / 'settings.json').read_text())
        self.assertEqual(settings['defaultProvider'], 'dexgem-responses')
        self.assertEqual(settings['enabledModels'], ['dexgem-responses/' + native.MODEL])
        report = json.loads((root / 'evidence/preparation.json').read_text())
        self.assertEqual(report['model'], 'dexgem-responses/' + native.MODEL + ':xhigh')
        self.assertEqual(report['auth_type'], 'api_key')
        self.assertEqual(report['expiry'], 'unknown')
        self.assertIsNone(report['auth_ttl_seconds'])
        # Fake executable captures the actual exec argv, without starting Pi.
        process = native.subprocess.Popen([str(root / 'bin/pi'), '--model', 'dexgem-responses/' + native.MODEL])
        self.assertEqual(process.wait(timeout=5), 0)
        self.assertTrue(list((root / 'evidence').glob('pi-launch-*.argv')))
        for path in root.rglob('*'):
            if path.is_file() and path != auth_path:
                value = path.read_bytes()
                for forbidden in (self.KEY, str(key), str(home), route['apiKey'], 'UNSELECTED_MODEL', 'INHERITED_BAD_KEY'):
                    self.assertNotIn(forbidden.encode(), value, str(path.relative_to(root)))
        native.remove_private_credentials(root)
        self.assertFalse(auth_path.exists())
        self.assertFalse((root / 'environment.json').exists())

    def test_rejects_tilde_users_env_escapes_extra_args_and_commands_before_key_read(self):
        home, source, key, registry = self.files()
        invalid = ['!cat ~other/key', '!cat $HOME/key', '!cat ${HOME}/key', '!cat ../key',
            '!cat ~/../key', '!cat ' + str(home / '../key'), '!cat ~/synthetic-key.private extra',
            '!cat -- ~/synthetic-key.private', '!cat -n', '!echo ' + str(key), '!sh -c cat',
            '!cat ~/synthetic-key.private; true', '!cat ~/synthetic-key.private | cat',
            '!cat `pwd`', '!cat "$(pwd)"', '!cat ~/synthetic-key.private\ntrue',
            '!cat ~/synthetic*', '!cat > ' + str(key), 'literal-key', '!env cat ' + str(key)]
        for expression in invalid:
            with self.subTest(expression=expression):
                registry['providers']['dexgem-responses']['apiKey'] = expression
                source.write_text(json.dumps(registry))
                with patch.object(native.os, 'open') as opened, self.assertRaises(ValueError) as error:
                    native.dexgem_credentials(source, home)
                opened.assert_not_called()
                self.assertNotIn(str(home), str(error.exception))
                self.assertNotIn(expression, str(error.exception))

    def test_original_home_must_match_fixed_registered_location(self):
        home, source, key, registry = self.files()
        for other_home, other_source in ((home.parent, source), (home, key), (Path('relative'), source),
                                         (home / '..' / home.name, source)):
            with self.subTest(home=other_home), patch.object(native.os, 'open') as opened:
                with self.assertRaisesRegex(ValueError, 'registration rejected'):
                    native.dexgem_credentials(other_source, other_home)
                opened.assert_not_called()

    def test_wrong_route_api_capabilities_headers_and_metadata_fail_before_key_read(self):
        home, source, key, original = self.files()
        mutations = [('provider', 'baseUrl', 'https://wrong.invalid/v1'),
            ('provider', 'api', 'openai-completions'), ('provider', 'headers', {}),
            ('model', 'headers', {}), ('model', 'api', 'openai-completions'),
            ('model', 'baseUrl', 'https://wrong.invalid/v1'), ('model', 'id', 'gpt-6-astra'),
            ('model', 'reasoning', False), ('model', 'thinkingLevelMap', {'xhigh': None}),
            ('model', 'thinkingLevelMap', {'xhigh': 'high'}), ('model', 'input', ['text']),
            ('model', 'contextWindow', 272000), ('model', 'maxTokens', 120000),
            ('model', 'name', '!cat /synthetic/private'), ('model', 'compat', {'headers': {}})]
        for level, field, value in mutations:
            with self.subTest(level=level, field=field):
                registry = copy.deepcopy(original)
                route = registry['providers']['dexgem-responses']
                (route if level == 'provider' else route['models'][0])[field] = value
                source.write_text(json.dumps(registry))
                with patch.object(native.os, 'open') as opened, self.assertRaises(ValueError):
                    native.dexgem_credentials(source, home)
                opened.assert_not_called()

    def test_missing_empty_commandlike_and_nonregular_key_errors_are_redacted(self):
        import traceback
        home, source, key, registry = self.files()
        def rejected():
            with self.assertRaises(ValueError) as error:
                native.dexgem_credentials(source, home)
            text = ''.join(traceback.format_exception(error.exception))
            for forbidden in (str(home), str(key), self.KEY, registry['providers']['dexgem-responses']['apiKey']):
                self.assertNotIn(forbidden, text)
        for value in ('', '  \n', '!cat /do-not-execute', '$ENV', 'two\nkeys'):
            key.write_text(value)
            rejected()
        key.unlink()
        rejected()
        key.mkdir()
        rejected()
        key.rmdir()
        other = home / 'other-private'
        other.write_text(self.KEY)
        key.symlink_to(other)
        rejected()
        key.unlink()
        (home / 'escape').symlink_to(home.parent, target_is_directory=True)
        registry['providers']['dexgem-responses']['apiKey'] = '!cat ~/escape/outside'
        source.write_text(json.dumps(registry))
        rejected()

    def test_preparation_failure_after_auth_write_erases_auth_env_and_redacts_every_output(self):
        home, source, key, registry = self.files()
        root = Path(tempfile.mkdtemp(prefix='codoxear-owner-succession-'))
        self.addCleanup(shutil.rmtree, root)
        original = native.private_json
        def fault(path, value):
            if Path(path).name == 'config.json':
                raise OSError(str(key) + ' ' + self.KEY)
            original(path, value)
        output = io.StringIO()
        with patch.object(native.tempfile, 'mkdtemp', return_value=str(root)), \
                patch.object(native, 'private_json', side_effect=fault), redirect_stdout(output):
            with self.assertRaises(ValueError) as error:
                native.prepare('/usr/bin/true', '/usr/bin/python3', provider='dexgem-responses',
                               models_source=source, registration_home=home)
        self.assertFalse((root / 'home/.pi/agent/auth.json').exists())
        self.assertFalse((root / 'environment.json').exists())
        public = output.getvalue() + str(error.exception) + (root / 'evidence/result.json').read_text()
        self.assertNotIn(str(key), public)
        self.assertNotIn(self.KEY, public)
        self.assertNotIn(registry['providers']['dexgem-responses']['apiKey'], public)

    def test_isolation_rejects_extra_auth_unsafe_modes_roots_env_and_public_registration(self):
        root = self.prepared()
        agent = root / 'home/.pi/agent'
        auth_path = agent / 'auth.json'
        auth = json.loads(auth_path.read_text())
        public = json.loads((agent / 'models.json').read_text())
        env = json.loads((root / 'environment.json').read_text())
        variants = [{**auth, 'openai': {'type': 'api_key', 'key': 'OTHER'}},
            {'dexgem-responses': {**auth['dexgem-responses'], 'env': {'HOME': '/untrusted'}}},
            {'dexgem-responses': {'type': 'oauth', 'key': self.KEY}},
            {'dexgem-responses': {'type': 'api_key', 'key': '!cat /untrusted'}}]
        with patch.dict(os.environ, env, clear=True):
            for variant in variants:
                native.private_json(auth_path, variant)
                with self.assertRaises(ValueError):
                    native.check_isolation(root)
            native.private_json(auth_path, auth)
            auth_path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'auth must be private'):
                native.check_isolation(root)
            auth_path.chmod(0o600)
            root.chmod(0o755)
            with self.assertRaisesRegex(ValueError, 'root must be private'):
                native.check_isolation(root)
            root.chmod(0o700)
            alias = root.parent / (root.name + '-alias')
            alias.symlink_to(root, target_is_directory=True)
            try:
                with self.assertRaisesRegex(ValueError, 'symlink'):
                    native.check_isolation(alias)
            finally:
                alias.unlink()
            tampered = copy.deepcopy(public)
            tampered['providers']['dexgem-responses']['apiKey'] = '!cat /untrusted'
            native.private_json(agent / 'models.json', tampered)
            with self.assertRaisesRegex(ValueError, 'registration rejected'):
                native.check_isolation(root)
            native.private_json(agent / 'models.json', public)
            native.private_json(agent / 'models-store.json', {})
            with self.assertRaisesRegex(ValueError, 'models-store'):
                native.check_isolation(root)
            (agent / 'models-store.json').unlink()
            os.environ['DEXGEM_API_KEY'] = 'INHERITED'
            with self.assertRaisesRegex(ValueError, 'allowlist'):
                native.check_isolation(root)

    def test_runtime_preflight_and_post_preflight_faults_cleanup_without_launches(self):
        import asyncio
        for stage in ('isolation', 'catalog'):
            with self.subTest(stage=stage):
                root = self.prepared()
                env = json.loads((root / 'environment.json').read_text())
                if stage == 'isolation':
                    native.private_json(root / 'home/.pi/agent/auth.json', {'unexpected': {}})
                with patch.dict(os.environ, env, clear=True), \
                        patch.object(native.subprocess, 'run', side_effect=RuntimeError('synthetic catalog failure')) as launch, \
                        patch.object(native, 'FileEvents') as watcher, redirect_stdout(io.StringIO()):
                    watcher.return_value.observations = []
                    with self.assertRaises((ValueError, RuntimeError)):
                        asyncio.run(native.run(root))
                self.assertEqual(launch.call_count, 0 if stage == 'isolation' else 1)
                self.assertFalse((root / 'home/.pi/agent/auth.json').exists())
                self.assertFalse((root / 'environment.json').exists())
                if stage == 'catalog':
                    cleanup = json.loads((root / 'evidence/cleanup.json').read_text())
                    self.assertEqual(cleanup['remaining_owned'], [])
                    self.assertEqual(cleanup['errors'], [])

    def test_explicit_model_request_and_source_payload_contract(self):
        # Shared model-request and source-payload contract only; observe checks actual successor requests.
        for provider in native.PROVIDERS:
            with self.subTest(provider=provider):
                source = native.source_request('/synthetic/project', '/synthetic/seed.jsonl', provider)
                native.assert_model_request(source, provider)
                self.assertEqual(source['args'][:2], ['--session', '/synthetic/seed.jsonl'])
                with self.assertRaises(AssertionError):
                    native.assert_model_request({**source, 'model_provider': 'fallback'}, provider)
                self.assertEqual(native.model_request(provider), dict(model_provider=provider,
                    model=native.MODEL, reasoning_effort='xhigh'))
        with self.assertRaisesRegex(ValueError, 'no fallback'):
            native.model_request('unregistered')

    def test_cli_requires_explicit_dexgem_paths_and_rejects_mixed_auth_branches(self):
        with patch.object(native, 'prepare') as prepared:
            native.main(['--prepare', '--provider', 'dexgem-responses', '--pi-bin', '/synthetic/pi',
                '--native-python', '/synthetic/python', '--models-source', '/synthetic/home/.pi/agent/models.json',
                '--registration-home', '/synthetic/home'])
            self.assertEqual(prepared.call_args.kwargs['provider'], 'dexgem-responses')
            self.assertEqual(prepared.call_args.kwargs['registration_home'], Path('/synthetic/home'))
            with redirect_stdout(io.StringIO()), patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit):
                native.main(['--prepare', '--provider', 'dexgem-responses', '--pi-bin', '/synthetic/pi',
                    '--native-python', '/synthetic/python', '--auth-source', '/synthetic/auth'])


if __name__ == '__main__':
    unittest.main()
