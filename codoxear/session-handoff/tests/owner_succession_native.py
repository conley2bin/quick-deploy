"""Opt-in, one-shot native Sol Pi + synthetic-owner acceptance.

Preparation uses an explicit OpenAI OAuth or DexGem API-key branch; no fallback.
Run preparation separately; submit --run ROOT through the bounded job service.
The normal unittest suite never starts Pi, tmux, a server, or a model request.
Raw evidence stays in the private temporary root. No production URL is accepted.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import copy
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import secrets
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import uuid

import aiohttp
from aiohttp.test_utils import TestServer

from integration_fixture import (prepare_fixture, project_fingerprint, assert_unchanged,
    assert_intended_action, assert_startup_submitted, read_correlated_chain)
from native_acceptance import FileEvents, private_json, load_host, assert_handoff_mutations

MODEL = 'gpt-6.1-sol'
PROVIDERS = ('openai', 'dexgem-responses')
DEXGEM_URL = 'https://litellm-direct.dex-gem.ai/v1'
BUDGET = 900
MARGIN = 300
REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / 'pi-agent/skills/responsibility-registry/scripts'
HELPER = Path(__file__).resolve().parents[1] / 'handoff_io.py'


def within(path, root):
    path, root = Path(path), Path(root).resolve()
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('path escapes the private temporary root')
    return path


def oauth_access_only(entry, now=None):
    """No refresh credential, extended expiry, or unknown auth metadata survives."""
    now = time.time() if now is None else now
    if (entry.get('type') != 'oauth' or not isinstance(entry.get('access'), str)
            or not entry['access'] or not isinstance(entry.get('expires'), (int, float))):
        raise ValueError('registered openai OAuth entry is unavailable')
    remaining = entry['expires'] / 1000 - now
    if remaining < BUDGET + MARGIN:
        raise ValueError(f'auth_ttl_insufficient: {int(remaining)}s remaining; require 1200s; no refresh attempted')
    result = {key: entry[key] for key in ('type', 'access', 'expires', 'clientId', 'scopes', 'accountId') if key in entry}
    result['refresh'] = 'TEST_ONLY_REFRESH_DISABLED'
    return result


def dexgem_public_registration(registry, *, isolated=False):
    """Keep the registered public route, never its credential expression."""
    providers = registry['providers']
    route = providers['dexgem-responses']
    selected = [row for row in route['models'] if row.get('id') == MODEL]
    if len(selected) != 1:
        raise ValueError('exact DexGem Sol registration required')
    model = selected[0]
    provider_fields = {'name', 'baseUrl', 'api', 'compat', 'authHeader', 'models', 'apiKey'}
    model_fields = {'id', 'name', 'provider', 'api', 'baseUrl', 'reasoning', 'thinkingLevelMap',
                    'input', 'contextWindow', 'maxTokens', 'cost', 'compat', 'inputLimits',
                    'promptCache', 'samplingParams', 'samplingParamsByThinkingLevel'}
    if (set(route) - provider_fields or set(model) - model_fields
            or route.get('baseUrl') != DEXGEM_URL or route.get('api') != 'openai-responses'
            or model.get('api', route['api']) != route['api']
            or model.get('baseUrl', route['baseUrl']) != route['baseUrl']
            or model.get('provider', 'dexgem-responses') != 'dexgem-responses'
            or model.get('reasoning') is not True or model.get('input') != ['text', 'image']
            or model.get('contextWindow') != 690000 or model.get('maxTokens') != 128000
            or model.get('thinkingLevelMap', {}).get('xhigh') != 'xhigh'):
        raise ValueError('exact DexGem route/capabilities required')
    public = {key: value for key, value in route.items() if key not in ('apiKey', 'models')}
    public['models'] = [model]
    # Metadata is data, not executable configuration or an external file reference.
    def check(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ('apiKey', 'headers', 'env', 'oauth'):
                    raise ValueError('credential-bearing public metadata')
                check(key)
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)
        elif isinstance(value, str) and value != DEXGEM_URL:
            if any(char in value for char in '!$~/\\\n\r`'):
                raise ValueError('non-public model metadata')
    check(public)
    result = {'providers': {'dexgem-responses': public}}
    if isolated and registry != result:
        raise ValueError('isolated DexGem registration must contain only the selected public model')
    return result


def dexgem_credentials(models_source, registration_home):
    """Directly read ONE registered regular file; never evaluate a command.

    registration_home is an explicitly verified original same-host HOME, pinned
    by its canonical models.json location, not inferred from the actor/caller env.
    All source-side exceptions are replaced before reaching public evidence.
    """
    stage = 'registration'
    try:
        home, source = Path(registration_home), Path(models_source)
        if (not home.is_absolute() or '..' in home.parts or home.resolve() != home
                or not home.is_dir() or source != home / '.pi/agent/models.json'
                or source.is_symlink() or not source.is_file()):
            raise ValueError()
        registry = json.loads(source.read_text())
        public = dexgem_public_registration(registry)
        stage = 'credential source'
        expression = registry['providers']['dexgem-responses']['apiKey']
        if (not isinstance(expression, str) or not expression.startswith('!')
                or any(char in expression for char in '$`\\\n\r;|&<>()*?[]{}')):
            raise ValueError()
        words = shlex.split(expression[1:])
        if len(words) != 2 or words[0] not in ('cat', '/bin/cat', '/usr/bin/cat'):
            raise ValueError()
        operand = words[1]
        if operand.startswith('~/'):
            key_path = home / operand[2:]
            if not key_path.resolve().is_relative_to(home):
                raise ValueError()
        elif operand.startswith('/'):
            key_path = Path(operand)
        else:
            raise ValueError()
        if '..' in key_path.parts or '~' in str(key_path):
            raise ValueError()
        # O_NOFOLLOW plus fstat rejects symlinks/directories without reading them.
        fd = os.open(key_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError()
            key = stream.read().strip()  # Exactly one content read, no shell.
        stage = 'API key'
        if not key or any(char.isspace() or char in '!$`' for char in key):
            raise ValueError()
        return public, {'type': 'api_key', 'key': key}
    except Exception:
        raise ValueError('DexGem ' + stage + ' rejected; source details withheld; no fallback') from None


def clean_environment(root, pi_bin):
    """Construct, never inherit, the complete child environment."""
    root = Path(root).resolve()
    home = root / 'home'
    return {
        'HOME': str(home), 'PI_HOME': str(home / '.pi'),
        'PI_CODING_AGENT_DIR': str(home / '.pi/agent'),
        'PATH': os.pathsep.join((str(root / 'bin'), str(Path(pi_bin).absolute().parent), '/usr/local/bin', '/usr/bin', '/bin')),
        'USER': 'isolated-test', 'LOGNAME': 'isolated-test', 'LANG': 'C.UTF-8',
        'TERM': 'xterm-256color', 'SHELL': '/bin/bash', 'TMPDIR': str(root / 'tmp'),
        'XDG_CONFIG_HOME': str(home / '.config'), 'XDG_CACHE_HOME': str(home / '.cache'),
        'XDG_STATE_HOME': str(home / '.local/state'), 'XDG_DATA_HOME': str(home / '.local/share'),
        'PYTHONDONTWRITEBYTECODE': '1', 'PI_OFFLINE': '1', 'PI_TELEMETRY': '0',
        'PI_BIN': str(root / 'bin/pi'), 'CODEX_WEB_PASSWORD': secrets.token_urlsafe(32),
        'CODEX_WEB_TMUX_SESSION': 'acceptance',
    }


def load_api(path):
    spec = importlib.util.spec_from_file_location('_native_owner_api_' + uuid.uuid4().hex, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def synthetic_graph(factory, source_pi, source_file, project):
    """Reuse peer's validated graph, with fresh UUID owners and no real contacts."""
    state = factory.state()
    ids = {key: str(uuid.uuid4()) for key in state['owners']}
    def owner_ref(value):
        return 'owner:' + ids[value[6:]] if value.startswith('owner:') else value
    owners = {}
    for key, row in state['owners'].items():
        row['id'], row['parent'] = ids[key], owner_ref(row['parent'])
        row['identity'] = dict(pi_session_id=source_pi if key == 'alpha' else None,
            intercom_id=None, source='current-session' if key == 'alpha' else 'unavailable',
            unknown_reason='Dedicated synthetic fixture; no Intercom contact or secondary runtime exists.')
        row['recovery'].update(session_file=str(source_file) if key == 'alpha' else None,
            cwd=str(project), repo=str(project), worktree=str(project), context_refs=[],
            unknown_reason='Secondary synthetic owners have no runtime.' if key != 'alpha' else '')
        owners[ids[key]] = row
    state['owners'] = owners
    second_active = copy.deepcopy(state['assignments']['active'])
    second_active.update(id='second-active', task='second-active', objective='Second synthetic accepted objective.')
    state['assignments']['second-active'] = second_active
    for row in state['assignments'].values():
        row['owner'] = ids[row['owner']]
        row['project']['path'] = str(project)
        row['repo']['path'] = str(project / '.git')
    for row in state['coordination'].values():
        row['repo']['path'] = str(project / '.git')
        row['integration_owner'] = ids[row['integration_owner']]
        row['acknowledgements'] = {ids[key]: value for key, value in row['acknowledgements'].items()}
        for scope in row['scopes'].values():
            scope['owner'] = ids[scope['owner']]
    factory.api._codec.validate_current_envelope(state, factory.ERA)
    return state, ids


def prepare(pi_bin, native_python, auth_source=None, *, provider='openai',
            models_source=None, registration_home=None):
    root = Path(tempfile.mkdtemp(prefix='codoxear-owner-succession-', dir='/tmp')).resolve()
    root.chmod(0o700)
    evidence = root / 'evidence'
    evidence.mkdir(mode=0o700)
    # Fail before copying any access token, starting a server, or creating actors.
    try:
        if provider == 'openai':
            if models_source is not None or registration_home is not None:
                raise ValueError('OpenAI preparation requires only its OAuth auth-source')
            auth = oauth_access_only(json.loads(Path(auth_source).read_text())['openai'])
        elif provider == 'dexgem-responses':
            if auth_source is not None:
                raise ValueError('DexGem preparation does not read OAuth auth-source')
            public_models, auth = dexgem_credentials(models_source, registration_home)
        else:
            raise ValueError('unsupported provider; no fallback')
        pi_bin = Path(pi_bin).absolute()
        if not pi_bin.is_file():
            raise ValueError('installed Pi executable is missing')
        native_python = Path(native_python).absolute()  # Preserve venv identity, not its interpreter symlink.
        if not native_python.is_file():
            raise ValueError('installed native Python is missing')
        fixture = prepare_fixture(root / 'fixture')
        (root / 'fixture/home').rename(root / 'home')
        # The generic fixture's deterministic fake operation is not this real run.
        shutil.rmtree(root / 'fixture/state')
        (root / 'fixture/ground-truth.json').unlink()
        seed = next((root / 'home/.pi/agent/sessions/demo').glob('*.jsonl'))
        # Use only test history; remove Astra metadata inherited from the old fixture.
        seed.write_text(seed.read_text().replace('gpt-6-astra', MODEL))
        project = fixture['project']
        agents = project / 'AGENTS.md'
        agents.write_text(agents.read_text() +
            'This is an authorized temporary native handoff acceptance, not a real appointment.\n'
            'For your own current Pi identity, the installed native bridge writes SessionManager '
            'evidence at the path in your CODEX_WEB_PI_ACTIVE_SESSION_FILE environment variable. '
            'Read your own marker and its exact session header; do not infer identity from expected host data.\n'
            'No Intercom/subagent extension is loaded; report unavailable contact/custody honestly. '
            'Do not start agents, contact peers, or access another HOME. Verify toy Python with -B.\n')
        subprocess.run(['git', 'init', '-q', str(project)], check=True, capture_output=True)
        (root / 'bin').mkdir(mode=0o700)
        (root / 'tmp').mkdir(mode=0o700)
        installed = root / 'home/.pi/agent/skills/responsibility-registry/scripts'
        shutil.copytree(SCRIPTS, installed, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        provenance = {str(path.relative_to(installed)): hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in installed.rglob('*.py')}
        private_json(evidence / 'installed-scripts.json', provenance)
        env = clean_environment(root, pi_bin)
        for key in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME', 'XDG_DATA_HOME'):
            Path(env[key]).mkdir(parents=True, exist_ok=True, mode=0o700)
        settings = dict(defaultProvider=provider, defaultModel=MODEL, defaultThinkingLevel='xhigh',
            enabledModels=[provider + '/' + MODEL], defaultProjectTrust='never',
            retry=dict(enabled=False, maxRetries=0, provider=dict(maxRetries=0, timeoutMs=120000)),
            packages=[], extensions=[], skills=[], prompts=[], themes=[], enableInstallTelemetry=False,
            enableAnalytics=False, cacheWarming='off')
        private_json(root / 'home/.pi/agent/settings.json', settings)
        # Copy only this exact public model registration, not daily configuration.
        if provider == 'openai':
            registry = json.loads((Path(auth_source).parent / 'models-store.json').read_text())
            selected = [row for row in registry['openai']['models'] if row.get('id') == MODEL and row.get('provider') == 'openai']
            if len(selected) != 1 or selected[0].get('api') != 'openai-responses' or 'xhigh' not in selected[0].get('thinkingLevelMap', {}):
                raise ValueError('exact registered Sol route/capabilities unavailable; no fallback')
            if any(key in selected[0] for key in ('apiKey', 'headers')):
                raise ValueError('model registration contains credential-bearing fields')
            private_json(root / 'home/.pi/agent/models-store.json', {'openai': {**{key: value for key, value in registry['openai'].items() if key != 'models'}, 'models': selected}})
            private_json(evidence / 'selected-model.json', selected[0])
        else:
            private_json(root / 'home/.pi/agent/models.json', public_models)
            private_json(evidence / 'selected-model.json', public_models)
        private_json(root / 'home/.pi/agent/auth.json', {provider: auth})
        flags = ['--no-extensions', '--no-skills', '--no-prompt-templates', '--no-themes', '--no-mcp', '--no-approve', '--offline']
        # Explicit broker -e bridge argv remains intact despite disabled discovery.
        # Pi sets process.title, which overwrites Linux /proc/PID/cmdline.
        # Record the exact exec argv before that happens; exec preserves this PID.
        launch = shlex.quote(str(pi_bin)) + ' ' + shlex.join(flags) + ' "$@"'
        (root / 'bin/pi').write_text('#!/bin/sh\numask 077\nprintf \'%s\\0\' ' + launch +
            ' > ' + shlex.quote(str(evidence)) + '/pi-launch-$$.argv\nexec ' + launch + '\n')
        (root / 'bin/tmux').write_text('#!/bin/sh\nexec /usr/bin/tmux -S ' + shlex.quote(str(root / 'tmux.sock')) + ' -f /dev/null "$@"\n')
        for name in ('pi', 'tmux'):
            (root / 'bin' / name).chmod(0o700)
        private_json(root / 'environment.json', env)
        private_json(root / 'config.json', dict(native_python=str(native_python), pi_bin=str(pi_bin),
            project=str(project), seed=str(seed), provider=provider,
            candidate=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip()))
        auth_info = (dict(auth_type='oauth', auth_ttl_seconds=int(auth['expires'] / 1000 - time.time()),
                          refresh_disabled=True) if provider == 'openai' else
                     dict(auth_type='api_key', auth_ttl_seconds=None, expiry='unknown', refresh_disabled=True))
        private_json(evidence / 'preparation.json', dict(status='prepared', model=provider + '/' + MODEL + ':xhigh', **auth_info))
    except BaseException as exc:
        (root / 'home/.pi/agent/auth.json').unlink(missing_ok=True)
        (root / 'environment.json').unlink(missing_ok=True)
        error = str(exc) if provider != 'dexgem-responses' else 'DexGem preparation failed; source details withheld; no fallback'
        private_json(evidence / 'result.json', dict(status='preflight-failed', error_type=type(exc).__name__, error=error))
        print(json.dumps({'status': 'preflight-failed', 'root': str(root), 'error': error}))
        if provider == 'dexgem-responses':
            raise ValueError(error) from None
        raise
    print(json.dumps({'status': 'prepared', 'root': str(root)}))
    return root


def private_root(root):
    root = Path(root).absolute()
    if root.is_symlink() or root.resolve() != root:
        raise ValueError('temporary root must not be a symlink')
    if root.parent != Path('/tmp') or not root.name.startswith('codoxear-owner-succession-'):
        raise ValueError('not a fresh owner-succession temporary root')
    if root.stat().st_mode & 0o777 != 0o700:
        raise ValueError('temporary root must be private')
    return root


def remove_private_credentials(root):
    for relative in ('home/.pi/agent/auth.json', 'environment.json'):
        within(Path(root) / relative, root).unlink(missing_ok=True)


def check_isolation(root):
    root = private_root(root)
    expected = json.loads((root / 'environment.json').read_text())
    allowed = set(clean_environment(root, '/usr/bin/pi'))
    if set(expected) != allowed or dict(os.environ) != expected:
        raise ValueError('child environment is not the exact allowlist')
    for key, value in {'HOME': root / 'home', 'PI_HOME': root / 'home/.pi',
                       'PI_CODING_AGENT_DIR': root / 'home/.pi/agent'}.items():
        if expected[key] != str(value):
            raise ValueError('private HOME/Pi directory mismatch')
    for key in ('HOME', 'PI_HOME', 'PI_CODING_AGENT_DIR', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME', 'XDG_DATA_HOME', 'TMPDIR', 'PI_BIN'):
        within(expected[key], root)
    auth_path = within(root / 'home/.pi/agent/auth.json', root)
    if auth_path.stat().st_mode & 0o777 != 0o600:
        raise ValueError('auth must be private')
    auth = json.loads(auth_path.read_text())
    provider = json.loads(within(root / 'config.json', root).read_text())['provider']
    if provider not in PROVIDERS or set(auth) != {provider}:
        raise ValueError('auth contains unauthorized credentials')
    agent = root / 'home/.pi/agent'
    settings = json.loads(within(agent / 'settings.json', root).read_text())
    if (settings['defaultProvider'] != provider or settings['defaultModel'] != MODEL
            or settings['defaultThinkingLevel'] != 'xhigh' or settings['enabledModels'] != [provider + '/' + MODEL]):
        raise ValueError('isolated settings do not select the exact provider/model')
    if provider == 'openai':
        if auth[provider].get('refresh') != 'TEST_ONLY_REFRESH_DISABLED':
            raise ValueError('auth contains unauthorized credentials')
        oauth_access_only(auth[provider])
    else:
        entry = auth[provider]
        if (set(entry) != {'type', 'key'} or entry['type'] != 'api_key'
                or not isinstance(entry['key'], str) or not entry['key']
                or any(char.isspace() or char in '!$`' for char in entry['key'])):
            raise ValueError('auth must contain only a literal DexGem API key')
        if (agent / 'models-store.json').exists():
            raise ValueError('DexGem must not use models-store')
        try:
            dexgem_public_registration(json.loads(within(agent / 'models.json', root).read_text()), isolated=True)
        except Exception:
            raise ValueError('isolated DexGem public registration rejected; no fallback') from None
    return root


def proc_identity(pid):
    path = Path('/proc') / str(pid)
    try:
        fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
        return dict(pid=int(pid), ppid=int(fields[1]), start_ticks=fields[19])
    except FileNotFoundError:
        return None


def runtime_bridge(root, row, pi_id, session_file):
    marker_path = within(Path(root) / 'home/.local/share/codoxear/pi-active-sessions' / f"broker-{row['broker_pid']}.json", root)
    marker = json.loads(marker_path.read_text())
    assert marker['sessionId'] == pi_id and marker['sessionFile'] == str(session_file)
    assert marker['bridgeVersion'] == 2
    process = proc_identity(marker['pid'])
    assert process is not None, 'native bridge actor is no longer running'
    assert process['ppid'] == row['broker_pid'], 'bridge PID is not the native broker child'
    launch_path = within(Path(root) / 'evidence' / f"pi-launch-{marker['pid']}.argv", root)
    assert launch_path.stat().st_mode & 0o777 == 0o600, 'exec argv must be private'
    argv = [os.fsdecode(value) for value in launch_path.read_bytes().split(b'\0') if value]
    assert any(arg in ('-e', '--extension') and i + 1 < len(argv)
        and argv[i + 1].endswith('/pi_active_session_bridge.ts') for i, arg in enumerate(argv)), 'installed native bridge was not explicitly loaded'
    proc_title = [os.fsdecode(value) for value in Path(f"/proc/{marker['pid']}/cmdline").read_bytes().split(b'\0') if value]
    return dict(marker=marker, path=str(marker_path), process=process, argv=argv,
                launch_argv_path=str(launch_path), launch_argv_sha256=hashlib.sha256(launch_path.read_bytes()).hexdigest(),
                proc_title=proc_title, sha256=hashlib.sha256(marker_path.read_bytes()).hexdigest())


def same_process(record):
    actual = proc_identity(record['pid'])
    return actual is not None and actual['start_ticks'] == record['start_ticks']


def owned_descendants(record):
    identities = [value for path in Path('/proc').iterdir() if path.name.isdigit()
                  and (value := proc_identity(path.name)) is not None]
    owned = {record['pid']}
    while True:
        expanded = owned | {item['pid'] for item in identities if item['ppid'] in owned}
        if expanded == owned:
            break
        owned = expanded
    return [item for item in identities if item['pid'] in owned and item['pid'] != record['pid']]


async def wait_owned_exits(records, timeout=5):
    """pidfd notifications, never a process-poll sleep loop."""
    loop = asyncio.get_running_loop()
    fds, futures = [], []
    try:
        for record in records:
            if record is None or not same_process(record):
                continue
            try:
                fd = os.pidfd_open(record['pid'])
            except ProcessLookupError:
                continue
            if not same_process(record):
                os.close(fd)
                continue
            future = loop.create_future()
            def exited(future=future):
                if not future.done():
                    future.set_result(None)
            loop.add_reader(fd, exited)
            fds.append(fd)
            futures.append(future)
        if futures:
            await asyncio.wait_for(asyncio.gather(*futures), timeout)
    finally:
        for fd in fds:
            loop.remove_reader(fd)
            os.close(fd)


def events(store):
    return [record for path in sorted(store.audit.ops.glob('*.prepare.json'))
            if (record := json.loads(path.read_text()))['event'] is not None]


def assert_graph(before, after, owner, source, target):
    comparison = copy.deepcopy(after)
    comparison['revision'] = before['revision']
    for key in ('identity', 'recovery', 'updated_at'):
        comparison['owners'][owner][key] = before['owners'][owner][key]
    assert comparison == before, 'non-carrier graph changed'
    assert after['revision'] == before['revision'] + 1
    assert after['owners'][owner]['identity']['pi_session_id'] == target
    assert not any(row['identity']['pi_session_id'] == source for row in after['owners'].values())


def shell_segments(command):
    """Recognize a bounded top-level shell form; never interpret heredoc bodies."""
    segments, pending, declarations = [], [], []
    text, quote, depth, before, i = '', '', 0, '', 0

    def emit(after):
        nonlocal text, declarations, before
        if text.strip():
            try:
                words = shlex.split(text)
            except ValueError:
                words = None
            assigns, argv = {}, [] if words is not None else None
            skip = False
            for word in words or []:
                if skip:
                    skip = False
                    continue
                redirection = re.fullmatch(r'(?:\d*)?(?:<<<|<<-?|&>>?|>&|<&|<>|>>?)(.*)', word)
                if redirection:
                    skip = not redirection[1]
                elif not argv and re.match(r'^[A-Za-z_][A-Za-z_0-9]*=', word):
                    name, value = word.split('=', 1)
                    assigns[name] = value
                else:
                    argv.append(word)
            segment = SimpleNamespace(argv=argv, assigns=assigns, before=before, after=after, heredoc=None)
            segments.append(segment)
            pending.extend((segment, delimiter, tabs) for delimiter, tabs in declarations)
        text, declarations, before = '', [], after

    while i < len(command):
        char = command[i]
        if char == '\\' and quote != "'" and i + 1 < len(command):
            text += '' if command[i + 1] == '\n' else command[i:i + 2]
            i += 2
            continue
        if quote:
            text += char
            if char == quote:
                quote = ''
            i += 1
            continue
        if char in "'\"":
            quote = char
        elif char == '#' and (not text or text[-1].isspace()):
            end = command.find('\n', i)
            i = len(command) if end < 0 else end
            continue
        elif command.startswith('<<', i) and not command.startswith('<<<', i) and (not i or command[i - 1] != '<'):
            match = re.match(r'''<<(-?)[ \t]*('[^']*'|"[^"]*"|[^\s;&|<>]+)''', command[i:])
            if match:
                declarations.append((shlex.split(match[2])[0], bool(match[1])))
                text += match[0]
                i += len(match[0])
                continue
        elif char == '(':
            depth += 1
        elif char == ')':
            depth = max(0, depth - 1)
        separator = next((sep for sep in ('&&', '||', '|&', ';;', ';', '|', '&', '\n')
                          if command.startswith(sep, i)), '') if not quote and depth == 0 else ''
        if separator == '&' and ((i and command[i - 1] in '<>') or command[i:i + 2] == '&>'):
            separator = ''
        if separator:
            emit(separator)
            i += len(separator)
            if separator == '\n':
                owners = {}
                for segment, delimiter, tabs in pending:
                    body, found = '', False
                    while i < len(command):
                        end = command.find('\n', i)
                        end = len(command) if end < 0 else end + 1
                        line = command[i:end]
                        i = end
                        if (line.rstrip('\n').lstrip('\t') if tabs else line.rstrip('\n')) == delimiter:
                            found = True
                            break
                        body += line.lstrip('\t') if tabs else line
                    owners.setdefault(id(segment), []).append(body if found else None)
                    segment.heredoc = owners[id(segment)][0] if len(owners[id(segment)]) == 1 else None
                pending.clear()
            continue
        text += char
        i += 1
    emit('')
    # A final newline/semicolon (including a heredoc's delimiter) is command end.
    if segments and segments[-1].after in ('\n', ';'):
        segments[-1].after = ''
    # Control/function blocks are outside this top-level grammar, including their
    # newline-separated bodies. Fail closed rather than treating a skipped body as executed.
    controls = {'if', 'then', 'elif', 'else', 'fi', 'for', 'while', 'until', 'do', 'done', 'case', 'esac', 'function', '{', '}'}
    if any(item.argv and (item.argv[0] in controls or re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*\(\)', item.argv[0]))
           for item in segments):
        for item in segments:
            item.argv = None
    return segments


def python_invocation(segment):
    argv = segment.argv
    if not argv or not re.fullmatch(r'python(?:\d+(?:\.\d+)*)?', Path(argv[0]).name):
        return None
    flags, i = set(), 1
    while i < len(argv):
        word = argv[i]
        if word in ('-W', '-X'):
            i += 2
        elif word == '-m':
            return None
        elif word == '-c':
            return SimpleNamespace(flags=flags, mode='c', code=argv[i + 1] if i + 1 < len(argv) else None,
                                   script=None, rest=[])
        elif word == '-':
            return SimpleNamespace(flags=flags, mode='stdin', code=segment.heredoc, script=None, rest=[])
        elif re.fullmatch(r'-[a-zA-Z]+', word):
            flags.update(word[1:])
            i += 1
        else:
            return SimpleNamespace(flags=flags, mode='script', code=None, script=word, rest=argv[i + 1:])
    return SimpleNamespace(flags=flags, mode='stdin', code=segment.heredoc, script=None, rest=[])


def tool_positions(rows):
    results = {row['message']['toolCallId']: index for index, row in enumerate(rows)
               if row.get('message', {}).get('role') == 'toolResult'}
    calls = {block['id']: index for index, row in enumerate(rows)
             if row.get('message', {}).get('role') == 'assistant'
             for block in row['message'].get('content', []) if block.get('type') == 'toolCall'}
    return calls, results


def tool_text(result):
    return '\n'.join(block.get('text', '') for block in result.get('content', []) if block.get('type') == 'text')


def cli_record_ok(sub, kind, verdict, record):
    if sub == 'check-owner':
        return record == {'current_binding_valid': True} and record['current_binding_valid'] is True
    if sub == 'attest':
        return (kind in ('identity', 'custody') and
                record == {'kind': kind, 'artifact': {'kind': kind, 'accepted': True}} and
                record['artifact']['accepted'] is True)
    artifact = record.get('artifact')
    return (sub == 'submit' and kind in ('understanding', 'review', 'resolution') and
            set(record) == {'kind', 'artifact'} and record['kind'] == kind and isinstance(artifact, dict) and
            artifact.get('version') == 1 and
            {'operation_id', 'source_sha256', 'body', '_sha256'} <= artifact.keys() and
            isinstance(artifact['_sha256'], str) and re.fullmatch(r'[0-9a-fA-F]{64}', artifact['_sha256']) is not None and
            (kind == 'understanding' or ('verdict' in artifact and artifact['verdict'] == verdict)))


def helper_executions(rows, calls, results, helper):
    call_rows, result_rows = tool_positions(rows)
    executions, mentions = [], 0
    for call in calls:
        if call.get('name') != 'bash':
            continue
        command = call.get('arguments', {}).get('command', '')
        found, recognized = [], False
        for segment in shell_segments(command):
            invocation = python_invocation(segment)
            if (not invocation or invocation.script is None or
                    os.path.normpath(invocation.script) != str(helper) or not invocation.rest or
                    invocation.rest[0] not in ('attest', 'submit', 'check-owner')):
                continue
            recognized = True
            args, options = invocation.rest, {}
            for index, word in enumerate(args[1:], 1):
                name, equals, value = word.partition('=')
                if name in ('-h', '--help'):
                    options['help'] = True
                elif name in ('--kind', '--verdict'):
                    options[name] = value if equals else (args[index + 1] if index + 1 < len(args) else None)
            if options.get('help'):
                continue
            found.append(dict(call=call, sub=args[0], kind=options.get('--kind'), verdict=options.get('--verdict'),
                              call_row=call_rows.get(call['id'], -1), result_row=result_rows.get(call['id'], -1),
                              status='ok', record=None))
        mentions += int('handoff_io.py' in command and not recognized)
        if not found:
            continue
        result, records, status = results.get(call['id']), [], 'ok'
        if result is None:
            status = 'no actual tool result'
        elif result.get('isError'):
            status = 'returned an error'
        else:
            for line in tool_text(result).split('\n'):
                line = line.rstrip('\r')
                if '{"' not in line:
                    continue
                try:
                    record = json.loads(line)
                    if (not isinstance(record, dict) or set(record) not in
                            ({'kind', 'artifact'}, {'current_binding_valid'}, {'error', 'message'}) or
                            line != json.dumps(record, ensure_ascii=False, sort_keys=True)):
                        raise ValueError('noncanonical CLI record')
                    records.append(record)
                except ValueError:
                    status = 'malformed record'
            if status == 'ok' and len(records) != len(found):
                status = 'absent/ambiguous record'
        for index, execution in enumerate(found):
            execution['status'] = status
            if status == 'ok':
                record = execution['record'] = records[index]
                execution['status'] = ('unclassified' if execution['sub'] != 'check-owner' and execution['kind'] is None
                    else 'rejected' if 'error' in record else 'ok' if cli_record_ok(
                        execution['sub'], execution['kind'], execution['verdict'], record) else 'malformed record')
        executions.extend(found)
    return executions, mentions


def assert_helper_pinned(rows, helper):
    assert any(shlex.quote(str(helper)) in (content if isinstance(content, str) else
               '\n'.join(block.get('text', '') for block in content if block.get('type') == 'text'))
               for row in rows if row.get('message', {}).get('role') == 'user'
               for content in [row['message'].get('content', '')]), 'observer helper pin differs from the product prompt'


def python_goal_evidence(code):
    """Top-level result bindings only; return assert evidence and expected print lines."""
    try:
        statements = ast.parse(code).body
    except (SyntaxError, ValueError):
        return False, []
    modules, functions, values = set(), set(), set()
    asserted, printed = False, []

    def result(node):
        return ((isinstance(node, ast.Name) and node.id in values) or
                (isinstance(node, ast.Call) and not node.args and not node.keywords and
                 ((isinstance(node.func, ast.Name) and node.func.id in functions) or
                  (isinstance(node.func, ast.Attribute) and node.func.attr == 'result' and
                   isinstance(node.func.value, ast.Name) and node.func.value.id in modules))))

    def equality(node):
        return (isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq) and
                ((result(node.left) and isinstance(node.comparators[0], ast.Constant) and node.comparators[0].value == 'Y') or
                 (result(node.comparators[0]) and isinstance(node.left, ast.Constant) and node.left.value == 'Y')))

    def render(node):
        if result(node):
            return 'Y', True
        if equality(node):
            return True, True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value, False
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ('repr', 'str') and
                len(node.args) == 1 and not node.keywords and result(node.args[0])):
            return (repr('Y') if node.func.id == 'repr' else 'Y'), True
        if isinstance(node, ast.JoinedStr):
            parts, used = [], False
            for part in node.values:
                if isinstance(part, ast.FormattedValue):
                    value = render(part.value)
                    if value is None or not value[1] or part.format_spec or part.conversion not in (-1, 114, 115):
                        return None
                    parts.append(repr(value[0]) if part.conversion == 114 else str(value[0]))
                    used = True
                elif isinstance(part, ast.Constant) and isinstance(part.value, str):
                    parts.append(part.value)
                else:
                    return None
            return ''.join(parts), used
        return None

    for statement in statements:
        if isinstance(statement, ast.Assert) and equality(statement.test):
            asserted = True
        if (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call) and
                isinstance(statement.value.func, ast.Name) and statement.value.func.id == 'print' and not statement.value.keywords):
            args = [render(arg) for arg in statement.value.args]
            if args and all(arg is not None for arg in args) and any(arg[1] for arg in args):
                printed.append(' '.join(str(arg[0]) for arg in args))
        binding = (statement.targets[0].id if isinstance(statement, ast.Assign) and len(statement.targets) == 1
                   and isinstance(statement.targets[0], ast.Name) and result(statement.value) else None)
        names = {node.id for node in ast.walk(statement) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(statement.name)
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            names.update(alias.asname or alias.name.split('.')[0] for alias in statement.names)
        for bindings in (modules, functions, values):
            bindings.difference_update(names)
        if binding:
            values.add(binding)
        if isinstance(statement, ast.Import):
            modules.update(alias.asname or alias.name for alias in statement.names if alias.name == 'main')
        if isinstance(statement, ast.ImportFrom) and statement.module == 'main' and statement.level == 0:
            functions.update(alias.asname or alias.name for alias in statement.names if alias.name == 'result')
    return asserted, printed


def post_edit_verifications(rows, calls, results, edit_done_row):
    call_rows, result_rows = tool_positions(rows)
    verified = []
    for call in calls:
        if (call.get('name') != 'bash' or not (edit_done_row < call_rows.get(call['id'], -1) < result_rows.get(call['id'], -1))
                or call['id'] not in results or results[call['id']].get('isError')):
            continue
        segments = shell_segments(call.get('arguments', {}).get('command', ''))
        errexit = False
        for index, segment in enumerate(segments):
            if segment.argv and segment.argv[0] == 'set':
                for position, word in enumerate(segment.argv[1:], 1):
                    if word in ('-o', '+o') and segment.argv[position + 1:position + 2] == ['errexit']:
                        errexit = word == '-o'
                    elif word.startswith(('-', '+')) and 'e' in word[1:] and word not in ('-o', '+o'):
                        errexit = word[0] == '-'
            invocation = python_invocation(segment)
            if (not invocation or invocation.code is None or 'B' not in invocation.flags or 'O' in invocation.flags or
                    'PYTHONOPTIMIZE' in segment.assigns):
                continue
            asserted, printed = python_goal_evidence(invocation.code)
            propagates = (segment.before not in ('|', '||', '|&', '&') and
                (all(item.after in ('&&', '') for item in segments[index:]) or
                 (errexit and segment.after in ('\n', ';', ''))))
            lines = [line.rstrip('\r') for line in tool_text(results[call['id']]).split('\n')]
            if (asserted and propagates) or any(line in lines for line in printed):
                verified.append(call)
                break
    return verified


def target_turn_state(rows):
    last_user = max((index for index, row in enumerate(rows) if row.get('message', {}).get('role') == 'user'), default=-1)
    assistants = [(index, row) for index, row in enumerate(rows) if index > last_user and
                  row.get('message', {}).get('role') == 'assistant']
    if not assistants:
        return dict(state='running', stopReason=None, errorMessage=None, row_id=None)
    index, row = assistants[-1]
    message = row['message']
    reason = message.get('stopReason')
    state = 'ended' if reason == 'stop' else 'running' if reason == 'toolUse' else 'unresolved'
    if state == 'unresolved' and any(entry.get('type') == 'context_edit' and entry.get('targetId') == row.get('id')
                                     for entry in rows[index + 1:]):
        state = 'running'
    return dict(state=state, stopReason=reason, errorMessage=str(message.get('errorMessage', ''))[:200], row_id=row.get('id'))


def assert_turn_goal(project, baseline):
    try:
        assert_intended_action(project, baseline)
    except AssertionError as exc:
        raise AssertionError('target ended its turn without the intended main.py action: ' + str(exc)) from exc


def assert_actor_tools(rows, calls, results, *, target, helper=HELPER, identity=None):
    """Require pinned executions, canonical CLI records and post-edit actor proof."""
    executions, mentions = helper_executions(rows, calls, results, helper)

    def accepted(execution):
        assert execution['status'] == 'ok', 'actor helper execution not accepted: ' + execution['status']
        return execution

    def one(sub, kind):
        matches = [item for item in executions if (item['sub'], item['kind']) == (sub, kind)]
        assert len(matches) == 1, f'missing/duplicate actual actor helper execution: {sub}/{kind} (unrecognized mentions: {mentions})'
        return accepted(matches[0])

    allowed = ({('attest', 'identity'), ('submit', 'understanding'), ('submit', 'resolution'), ('check-owner', None)}
               if target else {('attest', 'custody'), ('submit', 'review')})
    assert all((item['sub'], item['kind']) in allowed for item in executions), 'unexpected helper execution'
    if not target:
        one('attest', 'custody')
        review = one('submit', 'review')
        assert review['record']['artifact']['verdict'] in ('clear', 'corrections', 'conflict')
        return []
    one('attest', 'identity')
    one('submit', 'understanding')
    ready = one('submit', 'resolution')
    assert ready['record']['artifact']['verdict'] == 'ready'
    checks = [item for item in executions if item['sub'] == 'check-owner' and item['call_row'] > ready['result_row']]
    assert checks, ('check-owner must follow actual ready helper success '
                    f'(missing post-ready actual actor helper execution; unrecognized mentions: {mentions})')
    for item in checks:
        accepted(item)
    call_rows, result_rows = tool_positions(rows)
    edits = [call for call in calls if call.get('name') in ('edit', 'write') and
             Path(call.get('arguments', {}).get('path', '')).name == 'main.py']
    assert edits, 'no separately observed actor main.py edit; manual inspection required'
    assert any(item['result_row'] < min(call_rows[call['id']] for call in edits) for item in checks), 'toy edit preceded current-owner check'
    completed = [result_rows[call['id']] for call in edits if call['id'] in results and call['id'] in result_rows and
                 not results[call['id']].get('isError')]
    assert completed, 'no successful separately observed actor main.py edit; manual inspection required'
    assert identity is not None, 'actual challenged identity is required for bridge crosscheck'
    marker_outputs = [result for result in results.values() if all(key in json.dumps(result.get('content', []))
        for key in ('sessionId', 'sessionFile', 'bridgeVersion', 'pid', identity['pi_session_id'], identity['session_file']))]
    assert marker_outputs, 'actor did not inspect actual native SessionManager bridge output'
    verified = post_edit_verifications(rows, calls, results, max(completed))
    assert verified, 'no recognized successful post-edit Python -B verification of result() == "Y"; manual inspection required'
    return verified


def transcript_calls(path):
    # Native JSONL appends may be observed before the final newline lands.
    raw = Path(path).read_text()
    lines = raw.splitlines(keepends=True)
    rows = [json.loads(line) for line in lines if line.endswith('\n') and line.strip()]
    calls, results = [], {}
    for row in rows:
        message = row.get('message', {})
        if message.get('role') == 'assistant':
            for block in message.get('content', []):
                if block.get('type') == 'toolCall':
                    calls.append(block)
        elif message.get('role') == 'toolResult':
            results[message['toolCallId']] = message
    return rows, calls, results


def test_profile(provider):
    """Only the two authorized harness profiles; production owns stage defaults."""
    if provider == 'openai':
        return BUDGET, dict(timeouts=dict(
            launch=120, understanding=240, source_idle=90, review=240, resolution=240))
    if provider == 'dexgem-responses':
        return 3600, {}  # Omit timeouts entirely: use unmodified Engine defaults.
    raise ValueError('unsupported test profile; no fallback')


def model_request(provider):
    if provider not in PROVIDERS:
        raise ValueError('unsupported provider; no fallback')
    return dict(model_provider=provider, model=MODEL, reasoning_effort='xhigh')


def assert_model_request(body, provider):
    for key, value in model_request(provider).items():
        assert body.get(key) == value, 'native request changed the selected provider/model/effort'


def source_request(project, seed, provider):
    return dict(cwd=str(project), agent_backend='pi', **model_request(provider),
        create_in_tmux=True, args=['--session', str(seed),
        'This instruction applies only to this predecessor session. Acknowledge this authorized temporary fixture '
        'with exactly READY, without an answer-key summary or task edits. Then wait to review the successor. '
        'The successor may implement the latest user goal after the normal review/resolution protocol.'])


async def run(root):
    root = private_root(root)
    evidence = within(root / 'evidence', root)
    try:
        check_isolation(root)
        config = json.loads(within(root / 'config.json', root).read_text())
        total, engine_options = test_profile(config['provider'])
    except BaseException as exc:
        private_json(evidence / 'result.json', dict(status='preflight-failed', error_type=type(exc).__name__, error=str(exc)))
        remove_private_credentials(root)
        raise
    async with asyncio.timeout(total):
        await _run(root, config, total, engine_options)


def remove_stale_tmux_socket(root, tmux_identity, descendants=(), native_identity=None):
    """Unlink only this private socket, after process and kernel absence proofs."""
    root = private_root(root)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if os.fstat(fd).st_uid != os.getuid():
            raise RuntimeError('private socket root is not owned')
        try:
            info = os.stat('tmux.sock', dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return 'private-tmux-socket-absent'
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('private tmux socket is not an owned ordinary socket')
        if tmux_identity is None:
            raise RuntimeError('private tmux server identity is unverified')
        records = [tmux_identity, native_identity, *descendants]
        if any(same_process(record) for record in records if record is not None):
            raise RuntimeError('private server or descendants are still running')
        # Reject every live kernel entry, including bound sockets not yet listening.
        path = str(root / 'tmux.sock')
        for row in Path('/proc/net/unix').read_text().splitlines()[1:]:
            fields = row.split(maxsplit=7)
            if len(fields) == 8 and fields[7] == path:
                raise RuntimeError('private tmux socket still has an active kernel entry')
        current = os.stat('tmux.sock', dir_fd=fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino, current.st_mode, current.st_uid) != (
                info.st_dev, info.st_ino, info.st_mode, info.st_uid):
            raise RuntimeError('private tmux socket changed during verification')
        os.unlink('tmux.sock', dir_fd=fd)
        return 'verified-stale-private-tmux-socket-removed'
    finally:
        os.close(fd)


async def _run(root, config, total, engine_options):
    evidence = root / 'evidence'
    provider = config['provider']
    project, seed = Path(config['project']), Path(config['seed'])
    baseline = project_fingerprint(project)
    watcher = FileEvents(root)
    native_identity = tmux_identity = None
    descendants = []
    client = front = None
    engine = None
    report = dict(status='running', model=provider + '/' + MODEL + ':xhigh', candidate=config['candidate'])
    mutations, milestones = [], []
    turn = None
    def mark(stage, **details):
        milestones.append(dict(stage=stage, time_ns=time.monotonic_ns(), **details))
        private_json(evidence / 'milestones.json', milestones)
    try:
        # Catalog inspection is local only; no Pi actor or provider call.
        catalog = subprocess.run([str(root / 'bin/pi'), '--list-models', MODEL], capture_output=True, text=True, check=True)
        (evidence / 'model-catalog.txt').write_text(catalog.stdout)
        if not any(provider in line.split() and MODEL in line.split() for line in catalog.stdout.splitlines()):
            raise ValueError('exact registered ' + provider + ' Sol model is absent; no fallback')
        subprocess.run(['tmux', 'new-session', '-d', '-c', str(root), '-s', 'acceptance', '-n', 'anchor', '/bin/bash --noprofile --norc'], check=True)
        tmux_pid = int(subprocess.check_output(['tmux', 'display-message', '-p', '#{pid}'], text=True))
        tmux_identity = proc_identity(tmux_pid)
        private_json(evidence / 'tmux-owner.json', tmux_identity)
        assert (root / 'tmux.sock').is_socket()
        # Upstream tmux does not forward HOME; verify the server's initial environment.
        server_env = subprocess.check_output(['tmux', 'show-environment', '-g', 'HOME'], text=True).strip()
        assert server_env == 'HOME=' + str(root / 'home')
        api = load_api(root / 'home/.pi/agent/skills/responsibility-registry/scripts/session_succession.py')
        store = api._sibling('registry').Store()
        for path in (store.root, store.document):
            within(path, root)
        assert store.root == root / 'home/.pi/agent/.memory/local/responsibility-registry'
        assert store.document == root / 'home/.pi/agent/skills/responsibility-registry/RESPONSIBILITIES.md'
        store.initialize()
        host = load_host()
        owner_feature, handoff = host.default_features()
        ready = evidence / 'native-ready.json'
        server_script = evidence / 'native-server.py'
        server_script.write_text('import os,json\nfrom pathlib import Path\nfrom codoxear import server\n'
            'server._init_runtime()\nserver._require_password()\n'
            "httpd=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)\n"
            f'Path({str(ready)!r}).write_text(json.dumps(dict(port=httpd.server_address[1],pid=os.getpid())))\nhttpd.serve_forever()\n')
        command = shlex.join([config['native_python'], str(server_script)]) + ' 2>' + shlex.quote(str(evidence / 'native-stderr.txt'))
        pane = subprocess.check_output(['tmux', 'new-window', '-d', '-c', str(root), '-P', '-F', '#{pane_id}', '-t', 'acceptance', '-n', 'native-web', command], text=True).strip()
        report['native_pane'] = pane
        async def native_ready():
            try:
                return json.loads(ready.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                return False
        native = await watcher.until(native_ready, 30)
        native_identity = proc_identity(native['pid'])
        private_json(evidence / 'native-owner.json', native_identity)
        port = native['port']
        assert port != 8743
        def install(app, ctx):
            nonlocal engine
            transport = ctx.upstream_json
            async def observe(method, path, **kwargs):
                nonlocal descendants
                if method == 'POST':
                    body = kwargs.get('body')
                    mutations.append(dict(path=path, body=body, time_ns=time.monotonic_ns()))
                    private_json(evidence / 'native-mutations.json', mutations)
                    descendants = list({(p['pid'], p['start_ticks']): p for p in
                        [*descendants, *owned_descendants(tmux_identity)]}.values())
                    private_json(evidence / 'processes-before-native-mutation.json', descendants)
                    if path == '/api/sessions':
                        assert_model_request(body, provider)
                        assert body['create_in_tmux'] is True and len(body['args']) == 1
                        assert not body['args'][0].startswith(('-', '@')) and 'resume_session_id' not in body
                    elif path.endswith('/send'):
                        assert_unchanged(project, baseline, 'review/continuation delivery')
                        op = next(iter(engine.operations.values()))
                        assert_startup_submitted(Path(op['target_header']['log_path']), mutations[0]['body']['args'][0],
                            source_header_id=op['source_header']['pi_session_id'])
                        mark('before-stage-send', path=path, fingerprint=project_fingerprint(project))
                return await transport(method, path, **kwargs)
            ctx.upstream_json = observe
            engine = handoff.install(app, ctx, state_root=root / 'run-state', **engine_options)
        front = TestServer(host.create_app(f'http://127.0.0.1:{port}', features=(owner_feature, SimpleNamespace(install=install))))
        await front.start_server()
        client = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar())
        async def request(method, path, body=None, cookie=''):
            async with client.request(method, front.make_url(path), json=body,
                headers={'Cookie': cookie, 'Origin': str(front.make_url('')).rstrip('/')}) as response:
                value = await response.json()
                if response.status != 200:
                    raise RuntimeError(f'native/feature {method} {path}: HTTP {response.status}')
                return value, response.cookies
        _, cookies = await request('POST', '/api/login', dict(password=os.environ['CODEX_WEB_PASSWORD']))
        cookie = '; '.join(f'{key}={value.value}' for key, value in cookies.items())
        source_body = source_request(project, seed, provider)
        assert_model_request(source_body, provider)
        private_json(evidence / 'source-create-request.json', source_body)
        created, _ = await request('POST', '/api/sessions', source_body, cookie)
        private_json(evidence / 'source-create.json', created)
        async def source_ready():
            rows, _ = await request('GET', '/api/sessions', cookie=cookie)
            key = 'launch_id' if created.get('launch_id') else 'broker_pid'
            candidates = [row for row in rows['sessions'] if row.get(key) == created.get(key)]
            if len(candidates) != 1 or candidates[0].get('busy') is not False or not candidates[0].get('log_path'):
                return False
            row = candidates[0]
            if not Path(row['log_path']).exists():
                return False
            entries, _, _ = transcript_calls(row['log_path'])
            if any(entry.get('message', {}).get('stopReason') == 'error' for entry in entries):
                raise RuntimeError('source initial prompt model error; original response remains in private transcript')
            return row if any(block.get('text', '').strip() == 'READY' for entry in entries
                if entry.get('message', {}).get('role') == 'assistant' for block in entry['message'].get('content', [])) else False
        source = await watcher.until(source_ready, 180 if provider == 'openai' else total)
        assert_model_request(source, provider)
        source_file = within(source['log_path'], root)
        source_id = json.loads(source_file.read_text().splitlines()[0])['id']
        private_json(evidence / 'source-runtime-bridge.json', runtime_bridge(root, source, source_id, source_file))
        private_json(evidence / 'source-binding.json', source)
        spec = importlib.util.spec_from_file_location('_native_synthetic_factory', REPO / 'pi-agent/skills/responsibility-registry/tests/test_session_succession.py')
        factory = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(factory)
        before, ids = synthetic_graph(factory, source_id, source_file, project)
        store.document.write_text(api._codec.serialize_current_document(before, factory.ERA))
        assert store.current_snapshot()['state'] == before
        assert len(events(store)) == 0
        private_json(evidence / 'graph-before.json', before)
        mark('synthetic-source-bound', source_pi=source_id, owner_id=ids['alpha'])
        assert_unchanged(project, baseline, 'source initialization')
        started, _ = await request('POST', '/__session_handoff__/start', dict(source_session_id=source['session_id'], request_id=str(uuid.uuid4())), cookie)
        operation_id = started['operation']['id']
        report['operation_id'] = operation_id
        await asyncio.wait_for(asyncio.shield(engine.tasks[operation_id]), 780 if provider == 'openai' else total)
        operation = engine.operations[operation_id]
        report['operation'] = engine.public(operation_id)
        if operation['status'] != 'ready':
            raise AssertionError('handoff stopped at ' + operation['status'] + ':' + operation.get('error_code', ''))
        directory = root / 'run-state' / operation_id
        manifest = json.loads((directory / 'manifest.json').read_text())
        chain = read_correlated_chain(dict(manifest=manifest, operation=directory))
        async def action_done():
            nonlocal turn
            rows, _, _ = transcript_calls(operation['target_header']['log_path'])
            turn = target_turn_state(rows)
            return turn['state'] == 'ended'
        await watcher.until(action_done, 120 if provider == 'openai' else total)
        assert_turn_goal(project, baseline)
        after = store.current_snapshot()['state']
        target_id = operation['target_header']['pi_session_id']
        assert_graph(before, after, ids['alpha'], source_id, target_id)
        actual_events = events(store)
        assert len(actual_events) == 1 and actual_events[0]['event']['action'] == 'session-succession'
        intent = json.loads((directory / 'succession-intent.json').read_text())
        receipt = api.get_owner_session_succession(operation_id, intent['intent_digest'])
        assert receipt['status'] == 'already_committed' and receipt['current_binding_valid'] is True
        identity = json.loads((directory / 'succession-identity.json').read_text())
        current_rows, _ = await request('GET', '/api/sessions', cookie=cookie)
        target_rows = [row for row in current_rows['sessions'] if row['session_id'] == operation['target_session_id']]
        assert len(target_rows) == 1
        assert_model_request(target_rows[0], provider)
        private_json(evidence / 'target-runtime-bridge.json', runtime_bridge(root, target_rows[0], target_id, identity['session_file']))
        custody = json.loads((directory / 'succession-custody.json').read_text())
        assert identity['pi_session_id'] == target_id and identity['intercom_id'] is None
        assert identity['contact_source'] == 'unavailable' and identity['unknown_reason']
        assert custody['custody']['status'] in ('unknown', 'none_verified')
        assert custody['custody']['evidence'] and custody['custody']['active_runs'] == []
        if custody['custody']['status'] == 'unknown':
            assert custody['custody']['unknown_reason']
        # none_verified still requires manual inspection of the actor's actual evidence.
        assert_handoff_mutations(mutations, operation)
        # Independent file evidence corroborates transcript helper result ordering.
        first = lambda path: min(x['time_ns'] for x in watcher.observations if x['path'] == str(path))
        order = [first(store.audit.outcome_path(actual_events[0]['op_id'])),
                 first(directory / 'succession-receipt.json'), first(directory / 'resolution.json'), first(project / 'main.py')]
        assert order == sorted(order), 'registry/receipt/ready/task file chronology failed'
        target_rows, target_calls, target_results = transcript_calls(operation['target_header']['log_path'])
        private_json(evidence / 'target-tool-trace.json', dict(calls=target_calls, results=target_results))
        assert_helper_pinned(target_rows, HELPER)
        python_checks = assert_actor_tools(target_rows, target_calls, target_results, target=True, helper=HELPER, identity=identity)
        private_json(evidence / 'graph-after.json', after)
        private_json(evidence / 'fresh-receipt.json', receipt)
        source_rows, source_calls, source_results = transcript_calls(source_file)
        assert_helper_pinned(source_rows, HELPER)
        assert_actor_tools(source_rows, source_calls, source_results, target=False, helper=HELPER)
        private_json(evidence / 'source-tool-trace.json', dict(calls=source_calls, results=source_results))
        report.update(status='native-owner-succession-and-toy-passed', source_pi=source_id, target_pi=target_id,
            owner_id=ids['alpha'], event_delta=1, current_binding_valid=True, identity_contact=None,
            custody_status=custody['custody']['status'], chain_hashes=[item['_sha256'] for item in chain],
            project_files=project_fingerprint(project), python_check_calls=[call['id'] for call in python_checks],
            semantic_manual_review='required', chronology_ns=order)
    except BaseException as exc:
        report.update(status='failed', error_type=type(exc).__name__, error=str(exc)[:500])
        if turn is not None:
            report['target_turn'] = turn
        raise
    finally:
        private_json(evidence / 'result.json', report)
        private_json(evidence / 'file-events.json', watcher.observations)
        watcher.close()
        cleanup, cleanup_errors = [], []
        try:
            if client:
                await client.close()
            if front:
                await front.close()
            if native_identity and same_process(native_identity):
                os.kill(native_identity['pid'], signal.SIGTERM)
                cleanup.append('verified-native-web-SIGTERM')
            if tmux_identity and same_process(tmux_identity):
                # Exclusive socket created above, with its exact server PID/start.
                descendants = list({(p['pid'], p['start_ticks']): p for p in
                    [*descendants, *owned_descendants(tmux_identity)]}.values())
                private_json(evidence / 'owned-processes.json', descendants)
                inventory = subprocess.check_output(['tmux', 'list-panes', '-a', '-F', '#{pane_id} #{pane_pid} #{window_id}'], text=True)
                (evidence / 'owned-panes.txt').write_text(inventory)
                stopped = subprocess.run(['tmux', 'kill-server'], capture_output=True)
                if stopped.returncode and same_process(tmux_identity):
                    raise RuntimeError('private tmux cleanup failed')
                for process in reversed(descendants):
                    if same_process(process):
                        try:
                            os.kill(process['pid'], signal.SIGTERM)
                        except ProcessLookupError:
                            pass
            await wait_owned_exits([*descendants, tmux_identity, native_identity])
            records = [*descendants, tmux_identity, native_identity]
            if any(same_process(p) for p in records if p is not None):
                raise RuntimeError('owned processes remain after cleanup')
            cleanup.append('verified-exclusive-private-tmux-and-descendants-stopped')
            cleanup.append(remove_stale_tmux_socket(root, tmux_identity, descendants, native_identity))
        except BaseException as exc:
            cleanup_errors.append(type(exc).__name__ + ': ' + str(exc)[:200])
        finally:
            remove_private_credentials(root)
            remaining = [process for process in [*descendants, tmux_identity, native_identity]
                         if process is not None and same_process(process)]
            private_json(evidence / 'cleanup.json', dict(actions=cleanup, errors=cleanup_errors,
                remaining_owned=remaining, socket_removed=not os.path.lexists(root / 'tmux.sock'),
                auth_removed=True, password_file_removed=True))
            print(json.dumps(dict(status=report['status'], evidence=str(evidence))))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare', action='store_true')
    mode.add_argument('--run', type=Path)
    parser.add_argument('--pi-bin', type=Path)
    parser.add_argument('--native-python', type=Path)
    parser.add_argument('--provider', choices=PROVIDERS, default='openai', help='explicit auth/model branch; no fallback')
    parser.add_argument('--auth-source', type=Path, help='OpenAI OAuth source only')
    parser.add_argument('--models-source', type=Path, help='DexGem registered models.json only')
    parser.add_argument('--registration-home', type=Path, help='explicitly verified original same-host HOME for models.json and literal ~/')
    parser.add_argument('--clean-child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.prepare:
        if not all((args.pi_bin, args.native_python)):
            parser.error('preparation requires explicit installed Pi and native Python paths')
        if args.provider == 'openai' and (not args.auth_source or args.models_source or args.registration_home):
            parser.error('OpenAI preparation requires only its explicit OAuth auth-source')
        if args.provider == 'dexgem-responses' and (args.auth_source or not args.models_source or not args.registration_home):
            parser.error('DexGem preparation requires models-source and verified registration-home, not auth-source')
        prepare(args.pi_bin, args.native_python, args.auth_source, provider=args.provider,
                models_source=args.models_source, registration_home=args.registration_home)
        return
    if not args.clean_child:
        root = private_root(args.run)
        within(root / 'environment.json', root)
        env = json.loads((root / 'environment.json').read_text())
        os.execve(sys.executable, [sys.executable, str(Path(__file__).resolve()), '--run', str(root), '--clean-child'], env)
    asyncio.run(run(args.run))


if __name__ == '__main__':
    main()