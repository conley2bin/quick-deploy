#!/usr/bin/env python3
"""Finite native Pi CLI/PTY smoke test. Does not touch desktop clipboard/browser.

Usage: python3 test/native-smoke.py [--osc52 | --tmux] [--regular-start] [--artifacts DIRECTORY]
The default captures wl-copy stdin and xdg-open argv using isolated executables.
--osc52 decodes the native terminal clipboard write from the private PTY.
--tmux sends real client mouse events through an owned pi-agent tmux window.
All evidence is retained; no model requests or user session changes are made.
"""
import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pty
import re
import select
import shlex
import shutil
import signal
import struct
import subprocess
import tempfile
import termios
import time

args = argparse.ArgumentParser(description=__doc__)
args.add_argument('--artifacts', type=Path)
args.add_argument('--regular-start', action='store_true', help='Enable fullscreen through the real /settings UI')
transport = args.add_mutually_exclusive_group()
transport.add_argument('--osc52', action='store_true')
transport.add_argument('--tmux', action='store_true')
options = args.parse_args()
source = Path(__file__).resolve().parents[1]
root = Path(tempfile.mkdtemp(prefix='pi-copy-links-native-'))
artifacts = options.artifacts or root
artifacts.mkdir(parents=True, exist_ok=True)
agent = root / 'agent'
(agent / 'extensions').mkdir(parents=True)
(agent / 'extensions/pi-copy-links').symlink_to(source, target_is_directory=True)
(agent / 'extensions/fixture.ts').symlink_to(source / 'test/native-fixture.ts')
(agent / 'settings.json').write_text(json.dumps({
    'quietStartup': True, 'tuiMode': 'regular' if options.regular_start else 'fullscreen', 'defaultProvider': 'copy-links-fixture',
    'defaultModel': 'offline', 'enableInstallTelemetry': False, 'enableAnalytics': False,
    'defaultProjectTrust': 'never', 'terminal': {'images': False},
}))
code = 'target:\n\tprintf "%s\\n" "中文  spaces"  '
url = 'https://example.com/copy-links?a=1&b=two#fragment'
text = 'Native copy test\n\n- ```make\n  ' + code.replace('\n', '\n  ') + '\n  ```\n\n[OPEN_LINK](' + url + ')'
now = '2026-01-01T00:00:00.000Z'
usage = {k: 0 for k in ['input', 'output', 'cacheRead', 'cacheWrite', 'totalTokens']}
usage['cost'] = {k: 0 for k in ['input', 'output', 'cacheRead', 'cacheWrite', 'total']}
entries = [
    {'type': 'session', 'version': 3, 'id': 'copy-links-native', 'timestamp': now, 'cwd': str(root)},
    {'type': 'model_change', 'id': 'a001', 'parentId': None, 'timestamp': now, 'provider': 'copy-links-fixture', 'modelId': 'offline'},
    {'type': 'message', 'id': 'a002', 'parentId': 'a001', 'timestamp': now, 'message': {'role': 'user', 'content': 'Show fixture', 'timestamp': 0}},
    {'type': 'message', 'id': 'a003', 'parentId': 'a002', 'timestamp': now, 'message': {
        'role': 'assistant', 'content': [{'type': 'text', 'text': text}], 'api': 'openai-completions',
        'provider': 'copy-links-fixture', 'model': 'offline', 'timestamp': 0, 'stopReason': 'stop', 'usage': usage}},
]
session = root / 'session.jsonl'
session.write_text(''.join(json.dumps(entry, ensure_ascii=False) + '\n' for entry in entries))
private_bin = root / 'bin'
private_bin.mkdir()
for command, payload in [('wl-copy', "{'text': sys.stdin.read()}"), ('xdg-open', "{'argv': sys.argv[1:]}")]:
    script = private_bin / command
    script.write_text('#!/usr/bin/env python3\nimport json, os, sys\n'
                      f"with open(os.environ['PI_COPY_LINKS_TEST_DIR'] + '/{command}.jsonl', 'a') as f:\n"
                      f"    f.write(json.dumps({payload}, ensure_ascii=False) + '\\n')\n")
    script.chmod(0o755)
env = dict(os.environ, PI_CODING_AGENT_DIR=str(agent), PI_COPY_LINKS_TEST_DIR=str(root),
           PI_OFFLINE='1', PI_SKIP_VERSION_CHECK='1', TERM='xterm-256color', TERM_PROGRAM='',
           PATH=str(private_bin) + os.pathsep + os.environ['PATH'], DISPLAY='',
           WAYLAND_DISPLAY='' if options.osc52 else 'fixture', XDG_SESSION_TYPE='wayland',
           SSH_CONNECTION='fixture' if options.osc52 else '', SSH_CLIENT='', MOSH_CONNECTION='')
env.pop('TMUX', None)
pi = shutil.which('pi')
assert pi, 'pi is missing from PATH'
cli = [pi, '--offline', '--no-skills', '--no-prompt-templates', '--no-themes', '--session', str(session)]
pane = None
project_session = None
old_mouse = None

def tmux(*argv, check=True):
    return subprocess.run(['tmux', '-L', 'pi-agent', *argv], check=check, text=True, capture_output=True)

def live_pane():
    # display-message can silently fall back to another pane for a vanished -t.
    rows = tmux('list-panes', '-a', '-F', '#{pane_id}\t#{pane_current_path}').stdout.splitlines()
    return next((row.split('\t', 1)[1] for row in rows if row.startswith(pane + '\t')), None)

if options.tmux:
    project = Path(subprocess.check_output(['git', '-C', str(source), 'rev-parse', '--show-toplevel'], text=True).strip()).resolve()
    safe = re.sub('[^a-z0-9-]', '-', project.name.lower()).strip('-')[:24] or 'project'
    project_session = safe + '-' + hashlib.sha256(str(project).encode()).hexdigest()[:10]
    identity = hashlib.sha256(b'task=copy-links-native\0run=mouse-smoke').hexdigest()[:8]
    slot = 100 + int(identity, 16) % 9000
    if tmux('has-session', '-t', project_session, check=False).returncode:
        tmux('new-session', '-d', '-s', project_session, '-n', 'anchor', '-c', str(project))
    actual = tmux('display-message', '-p', '-t', project_session, '#{session_path}').stdout.strip()
    assert Path(actual).resolve() == project, (actual, project)
    print('Owned project session inventory:', tmux('list-panes', '-s', '-t', project_session,
          '-F', '#{window_id} #{pane_id} #{pane_current_path} #{pane_current_command}').stdout.strip())
    tmux('set-option', '-t', project_session, 'renumber-windows', 'off')
    # Pass only test-specific non-secret environment overrides, not the parent's credentials.
    keys = ['PI_CODING_AGENT_DIR', 'PI_COPY_LINKS_TEST_DIR', 'PI_OFFLINE', 'PI_SKIP_VERSION_CHECK',
            'PATH', 'DISPLAY', 'WAYLAND_DISPLAY', 'XDG_SESSION_TYPE', 'SSH_CONNECTION', 'SSH_CLIENT', 'MOSH_CONNECTION']
    command = shlex.join(['env', *[key + '=' + env[key] for key in keys], *cli])
    created = tmux('new-window', '-d', '-P', '-F', '#{window_id}:#{pane_id}', '-t', f'{project_session}:{slot}',
                   '-n', 'copy-links-native-' + identity, '-c', str(root), command).stdout.strip()
    window, pane = created.split(':')
    assert window.startswith('@') and pane.startswith('%'), created
    tmux('set-window-option', '-t', window, 'remain-on-exit', 'off')
    old_mouse = tmux('show-options', '-v', '-t', project_session, 'mouse').stdout.strip()
    tmux('set-option', '-t', project_session, 'mouse', 'on')
    tmux('select-window', '-t', window)
    cli = ['tmux', '-L', 'pi-agent', 'attach-session', '-t', project_session]
pid, fd = pty.fork()
if pid == 0:
    os.chdir(root)
    os.execvpe(cli[0], cli, env)
fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 90, 0, 0))
wire = bytearray()

def drain(seconds=0.15):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        ready, _, _ = select.select([fd], [], [], max(0, min(0.05, until-time.monotonic())))
        if ready:
            try:
                data = os.read(fd, 65536)
                if not data:
                    break
                wire.extend(data)
            except OSError:
                break

def send(value):
    os.write(fd, value if isinstance(value, bytes) else value.encode())
    drain()

def wait_for(predicate, description, seconds=15):
    until = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > until:
            raise AssertionError('Timed out: ' + description)
        drain()

def snapshot(name):
    path = root / 'snapshot.json'
    path.unlink(missing_ok=True)
    send('/fixture-snapshot\r')
    wait_for(path.exists, 'snapshot ' + name)
    value = json.loads(path.read_text())
    (artifacts / (name + '.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2))
    return value

def mouse_sequence(target, modifiers=0, edge='M'):
    x, y = target['x'] + 1, target['y'] + 1
    if pane:
        left, top = map(int, tmux('display-message', '-p', '-t', pane, '#{pane_left} #{pane_top}').stdout.split())
        x += left
        y += top
    return f'\x1b[<{modifiers};{x};{y}{edge}'

def click(target, modifiers=0):
    send(mouse_sequence(target, modifiers) + mouse_sequence(target, modifiers, 'm'))

def records(command):
    path = root / (command + '.jsonl')
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

def clipboard():
    if options.osc52:
        return [base64.b64decode(value).decode() for value in re.findall(rb'\x1b\]52;c;([A-Za-z0-9+/=]*)\x07', wire)]
    return [value['text'] for value in records('wl-copy')]

exited = False
try:
    if options.regular_start:
        wait_for(lambda: 'pi-copy-links 已加载'.encode() in wire, 'regular-mode activation notice')
        send('/settings\r')
        send('tui mode')
        send('\r')
        wait_for(lambda: json.loads((agent / 'settings.json').read_text()).get('tuiMode') == 'fullscreen',
                 'fullscreen selected in real settings UI')
        send('\x1b')
    # This label comes from the extension-decorated production renderer, not a mock.
    wait_for(lambda: '[复制]'.encode() in wire, 'native copy button')
    first = snapshot('initial')
    assert first['mode'] == 'fullscreen', first
    button = next(t for t in first['targets'] if t['url'].startswith('pi-copy://'))
    link = next(t for t in first['targets'] if t['url'] == url)
    click(button)
    wait_for(lambda: len(clipboard()) == 1, 'clipboard delivery')
    assert clipboard() == [code], repr(clipboard())
    button_copies = [clipboard()[-1]]
    after_copy = snapshot('after-copy')
    link = next(t for t in after_copy['targets'] if t['url'] == url)
    click(link)
    assert records('xdg-open') == [], 'unmodified click opened a browser'
    click(link, 16)
    wait_for(lambda: len(records('xdg-open')) == 1, 'Ctrl-click browser handoff')
    assert records('xdg-open') == [{'argv': [url]}]
    # Exercise tmux's lost-press modifier transition while dragging away and back.
    # Forwarded motion must prevent the release-recovery path from opening a URL.
    click(link)
    away = dict(link, x=link['x'] + 2)
    send(mouse_sequence(link, 16) + mouse_sequence(away, 48) +
         mouse_sequence(link, 48) + mouse_sequence(link, 16, 'm'))
    drain(0.2)
    assert records('xdg-open') == [{'argv': [url]}], 'Ctrl-drag opened a browser'
    # Native double-click/selection can itself write OPEN_LINK to the clipboard.
    # Compare each button's new write, rather than mistaking native selection for
    # a duplicate or failed code-copy operation.
    before_reload = clipboard()
    send('/reload\r')
    wait_for(lambda: b'Reloaded' in wire, 'real /reload')
    drain(0.3)
    restored = snapshot('reloaded')
    buttons = [t for t in restored['targets'] if t['url'].startswith('pi-copy://')]
    assert len(buttons) == 1, buttons
    assert buttons[0]['url'] != button['url'], 'reload reused old action registry'
    click(buttons[0])
    wait_for(lambda: len(clipboard()) > len(before_reload), 'copy after /reload')
    assert clipboard() == before_reload + [code]
    button_copies.append(clipboard()[-1])
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', 28, 42, 0, 0))
    drain(0.3)
    narrow = snapshot('narrow')
    before_narrow = clipboard()
    click(next(t for t in narrow['targets'] if t['url'].startswith('pi-copy://')))
    wait_for(lambda: len(clipboard()) > len(before_narrow), 'copy after narrow resize')
    assert clipboard() == before_narrow + [code]
    button_copies.append(clipboard()[-1])
    send('/fixture-quit\r')
    def stopped():
        global exited
        if pane:
            if live_pane() is not None:
                return False
            os.kill(pid, signal.SIGTERM)  # Exact test-owned attachment client, not the server.
            os.waitpid(pid, 0)
            exited = True
        else:
            result, _ = os.waitpid(pid, os.WNOHANG)
            exited = bool(result)
        return exited
    wait_for(stopped, 'graceful exit')
    result = {'result': 'passed', 'transport': 'OSC52' if options.osc52 else 'tmux → wl-copy stdin' if options.tmux else 'wl-copy stdin',
              'regularToFullscreen': options.regular_start,
              'buttonCopies': button_copies, 'clipboardWrites': clipboard(),
              'browser': records('xdg-open'), 'scratch': str(root)}
    (artifacts / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))
finally:
    (artifacts / 'terminal.raw').write_bytes(wire)
    if (root / 'input.raw').exists() and (root / 'input.raw').resolve() != (artifacts / 'input.raw').resolve():
        shutil.copyfile(root / 'input.raw', artifacts / 'input.raw')
    if not exited:
        # Only the exact child started by this test; never touch a shared server/session.
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        os.waitpid(pid, 0)
    os.close(fd)
    if pane:
        current = live_pane()
        if current is not None:
            assert current == str(root), 'Test pane ownership changed; refusing cleanup'
            tmux('kill-pane', '-t', pane)
    if project_session and old_mouse is not None:
        tmux('set-option', '-t', project_session, 'mouse', old_mouse)
