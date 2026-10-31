#!/usr/bin/env python3
"""User-level lifecycle for the persistent local VPS manager (manage.sh).

One systemd --user unit runs `serve`; this CLI installs, controls and opens it.
No root, no linger, no browser on login. Secrets never appear in argv or output.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import http.client
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import manage_auth
import ssh_proxy

UNIT_NAME = 'quick-deploy-vps-manage.service'
MARKER = '# quick-deploy-vps-manage: managed unit schema=1'
SCRIPT = Path(__file__).resolve()
MODULE = SCRIPT.parents[1]
DEFAULT_STATE = Path.home() / '.local/state/quick-deploy-private/vps'
SYSTEMCTL_TIMEOUT = 45
COMMANDS = ('install', 'start', 'stop', 'status', 'restart', 'open', 'uninstall', 'rotate', 'carrier', 'serve')


class ServiceError(Exception):
    pass


# ---------------------------------------------------------------- unit file

def unit_dir():
    return Path.home() / '.config/systemd/user'


def quote_exec(word):
    """One systemd ExecStart word: double-quoted, \\ \" escaped, % and $ doubled."""
    if any(ord(c) < 32 or ord(c) == 127 for c in word):
        raise ServiceError('Control character in a service path')
    return '"%s"' % word.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$')


DIGEST_ASSETS = ('manage.html', 'manage.js', 'manage.css', 'dashboard.css')


def code_digest(repo=None):
    """SHA-256 over the manager code the service imports/serves (never index.html, keys, domain.txt, state)."""
    repo = Path(repo or MODULE)
    files = sorted((repo / 'lib').glob('*.py')) + [repo / 'assets' / name for name in DIGEST_ASSETS]
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(repo).as_posix().encode() + b'\0' + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def render_unit(state, repo=None, python=None, script=SCRIPT, digest=None):
    repo = repo or MODULE
    digest = digest or code_digest(repo)
    python = python or sys.executable
    for value in (python, str(script), str(state), str(repo)):
        if not os.path.isabs(value):
            raise ServiceError('Service paths must be absolute: %s' % value)
    argv = [python, '-B', str(script), 'serve', '--state-dir', str(state)]
    working = str(repo)
    if any(ord(c) < 32 for c in working):
        raise ServiceError('Control character in repository path')
    return '\n'.join([
        MARKER,
        '# source-digest: ' + digest,
        '[Unit]',
        'Description=Quick Deploy VPS local manager (loopback, file-UI authenticated)',
        '',
        '[Service]',
        'Type=notify',
        'NotifyAccess=main',
        'ExecStart=' + ' '.join(quote_exec(a) for a in argv),
        'WorkingDirectory=' + working.replace('%', '%%'),
        'Restart=always',
        'RestartSec=3',
        'TimeoutStartSec=15',
        'TimeoutStopSec=30',
        'UMask=0077',
        '',
        '[Install]',
        'WantedBy=default.target',
        ''])


def _owned_file(path):
    """Return the unit text of an existing owned unit, None when absent; refuse anything else."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o133):
        raise ServiceError('Refusing %s: not a regular file owned by you with safe mode' % path)
    text = Path(path).read_text()
    if text.splitlines()[:1] != [MARKER]:
        raise ServiceError('Refusing to overwrite foreign unit %s' % path)
    return text


def _atomic_write(path, data, mode=0o600):
    directory = Path(path).parent
    fd, temporary = tempfile.mkstemp(prefix='.tmp-', dir=directory)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    except BaseException:
        if os.path.lexists(temporary):
            os.unlink(temporary)
        raise


# ---------------------------------------------------------------- systemctl

def systemctl(*args):
    """Bounded fixed `systemctl --user` verbs only."""
    if args[0] not in {'daemon-reload', 'enable', 'disable', 'start', 'stop', 'restart', 'is-active', 'is-enabled', 'reset-failed'}:
        raise ServiceError('Unsupported systemctl verb')
    try:
        return subprocess.run(['systemctl', '--user', *args], stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=SYSTEMCTL_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ServiceError('systemctl --user %s failed: %s' % (args[0], exc)) from exc


def _checked(*args):
    result = systemctl(*args)
    if result.returncode != 0:
        raise ServiceError('systemctl --user %s failed: %s' % (' '.join(args), (result.stderr or result.stdout).strip()[:300]))


def linger():
    """Read-only fact: does the user service survive logout? Never enables linger."""
    try:
        result = subprocess.run(['loginctl', 'show-user', str(os.getuid()), '-p', 'Linger', '--value'],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return 'unknown'
    return result.stdout.strip() or 'unknown' if result.returncode == 0 else 'unknown'


def is_active():
    return systemctl('is-active', UNIT_NAME).stdout.strip() == 'active'


# ---------------------------------------------------------------- state / client

def _require_user():
    if os.geteuid() == 0:
        raise ServiceError('Refusing to run as root; this is a per-user service')


def legacy_manager_active(state):
    """True when a foreground/legacy manager holds the session flock (not our unit)."""
    path = Path(state) / 'manage-jobs/.session.lock'
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)


def query_state(state, timeout=3):
    """Signed GET /api/state. Returns the decoded snapshot; raises ServiceError otherwise."""
    config, master = manage_auth.load_state(state)
    key = manage_auth.bootstrap_key(master)
    origin = 'http://127.0.0.1:%d' % config['port']
    nonce_c = secrets.token_hex(16)
    try:
        conn = http.client.HTTPConnection('127.0.0.1', config['port'], timeout=timeout)
        body = json.dumps({'version': 1, 'client_nonce': nonce_c}).encode()
        conn.request('POST', '/api/hello', body, {'Origin': 'null', 'Content-Type': 'application/json'})
        hello = json.loads(conn.getresponse().read(65536))
        conn.close()
        ctx = manage_auth.context(origin, hello['epoch'], hello['session_id'], nonce_c, hello['server_nonce'])
        if (hello.get('origin') != origin or hello.get('client_nonce') != nonce_c
                or not hmac.compare_digest(hello['proof'], manage_auth.server_proof(key, ctx))):
            raise ServiceError('Service identity proof failed')
        ks = manage_auth.session_key(key, ctx)
        nonce = secrets.token_hex(16)
        mac = manage_auth.request_mac(ks, hello['epoch'], nonce, 'GET', '/api/state', b'')
        conn = http.client.HTTPConnection('127.0.0.1', config['port'], timeout=timeout)
        conn.request('GET', '/api/state', headers={'Origin': 'null', 'Content-Type': 'application/json',
                                                   'X-QD-Session': hello['session_id'], 'X-QD-Nonce': nonce, 'X-QD-MAC': mac})
        response = conn.getresponse()
        raw = response.read(1 << 20)
        got = response.getheader('X-QD-MAC') or ''
        conn.close()
        if not hmac.compare_digest(got, manage_auth.response_mac(ks, hello['epoch'], nonce, response.status, raw)):
            raise ServiceError('Service response signature failed')
        if response.status != 200:
            raise ServiceError('Service state query returned %d' % response.status)
        return json.loads(raw)
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException) as exc:
        raise ServiceError('Service not reachable: %s' % type(exc).__name__) from exc


def require_idle(state, what):
    """Refuse unless the running service positively reports no active job."""
    if not is_active():
        return
    try:
        snapshot = query_state(state)
    except ServiceError as exc:
        raise ServiceError('Cannot %s: job state unknown (%s); try again when the service responds' % (what, exc)) from exc
    if snapshot.get('busy') is not False:
        raise ServiceError('Cannot %s while a job is active or its state is unknown; wait for it to finish' % what)


# ---------------------------------------------------------------- carrier

def carrier_path(state):
    return manage_auth.service_dir(state) / 'carrier.json'


def carrier_set(state, proxy_url, acknowledged, direct=False):
    """proxy_url xor direct. Direct is stored as ssh_proxy null: explicit, never a fallback."""
    if not acknowledged:
        raise ServiceError('carrier set requires --independent-carrier: acknowledge this SSH route is independent of EVERY managed VPS account/entry')
    if direct == bool(proxy_url):
        raise ServiceError('carrier set needs exactly one of --ssh-proxy URL or --direct')
    url = None if direct else ssh_proxy.parse_proxy(proxy_url).url
    manage_auth.load_state(state)
    path = carrier_path(state)
    if os.path.lexists(path):
        manage_auth._check(path, 0o600)
    _atomic_write(path, json.dumps({'schema': 1, 'ssh_proxy': url, 'independent': True}, separators=(',', ':')).encode())
    return url


def carrier_clear(state):
    path = carrier_path(state)
    if not os.path.lexists(path):
        return False
    manage_auth._check(path, 0o600)
    os.unlink(path)
    return True


def carrier_summary(state):
    path = carrier_path(state)
    if not os.path.lexists(path):
        return 'none (read-only subscriptions/model)'
    try:
        manage_auth._check(path, 0o600)
        value = json.loads(manage_auth._read(path))
        if value['ssh_proxy'] is None:
            return 'direct SSH configured (remote reachability NOT tested)'
        return 'configured %s' % ssh_proxy.parse_proxy(value['ssh_proxy']).url
    except (OSError, ValueError, KeyError, manage_auth.AuthError, ssh_proxy.Error):
        return 'INVALID carrier.json (writes disabled)'


# ---------------------------------------------------------------- lifecycle

def _sync_repo(state, repo):
    """Config's repo field follows the install source; port and master are untouched."""
    config, _ = manage_auth.load_state(state)
    if config['repo'] != str(repo):
        config['repo'] = str(repo)
        _atomic_write(manage_auth.service_dir(state) / 'config.json', json.dumps(config, separators=(',', ':')).encode())


def install(state, out):
    _require_user()
    state = Path(state)
    repo = MODULE
    manage_auth.ensure_state(state, repo)
    _sync_repo(state, repo)
    unit = unit_dir() / UNIT_NAME
    wanted = render_unit(state)
    existing = _owned_file(unit)
    if not is_active() and legacy_manager_active(state):
        raise ServiceError('A foreground/legacy manager is active on this state; stop it first (not stopped automatically)')
    changed = existing != wanted
    if changed and is_active():
        require_idle(state, 'restart the updated service')  # before the unit text changes, so a refused install can be retried
    if changed:
        unit.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(unit, wanted.encode())
        _checked('daemon-reload')
    _checked('enable', UNIT_NAME)
    if changed and is_active():
        _checked('restart', UNIT_NAME)
    else:
        _checked('start', UNIT_NAME)
    print('Service %s %s and started (port %d).' % (UNIT_NAME, 'updated' if changed and existing else 'installed' if changed else 'already installed',
                                                     manage_auth.load_state(state)[0]['port']), file=out)


def _installed():
    if _owned_file(unit_dir() / UNIT_NAME) is None:
        raise ServiceError('Service not installed; run: manage.sh install')


def start(state, out):
    _require_user()
    _installed()
    if not is_active() and legacy_manager_active(state):
        raise ServiceError('A foreground/legacy manager is active on this state; stop it first (not stopped automatically)')
    _checked('start', UNIT_NAME)
    print('Service started.', file=out)


def stop(state, out):
    _installed()
    require_idle(state, 'stop')
    _checked('stop', UNIT_NAME)
    print('Service stopped. The entry page stays but cannot reach the service until start.', file=out)


def restart(state, out):
    _installed()
    require_idle(state, 'restart')
    _checked('restart', UNIT_NAME)
    print('Service restarted.', file=out)


def uninstall(state, out):
    unit = unit_dir() / UNIT_NAME
    if _owned_file(unit) is None:
        print('Service not installed.', file=out)
        return
    require_idle(state, 'uninstall')
    _checked('stop', UNIT_NAME)
    _checked('disable', UNIT_NAME)
    os.unlink(unit)
    _checked('daemon-reload')
    print('Service removed. Private state (master key, config, carrier) was kept; delete %s yourself to discard it.'
          % manage_auth.service_dir(state), file=out)


def rotate(state, out):
    _installed()
    manage_auth.load_state(state)
    require_idle(state, 'rotate the key')
    was_active = is_active()
    if was_active:
        _checked('stop', UNIT_NAME)
    _atomic_write(manage_auth.service_dir(state) / 'master.key', secrets.token_bytes(32))
    if was_active:
        _checked('start', UNIT_NAME)
    print('Master key rotated; the previous entry HTML no longer authenticates.%s' % (
        ' Entry page regenerated.' if was_active else ' Run start to regenerate the entry page.'), file=out)


def status(state, out):
    unit = unit_dir() / UNIT_NAME
    try:
        installed = _owned_file(unit) is not None
    except ServiceError as exc:
        print('unit: %s' % exc, file=out)
        return 1
    print('unit: %s' % ('installed' if installed else 'not installed'), file=out)
    if not installed:
        return 3
    print('active: %s' % systemctl('is-active', UNIT_NAME).stdout.strip(), file=out)
    print('enabled: %s' % systemctl('is-enabled', UNIT_NAME).stdout.strip(), file=out)
    try:
        config, _ = manage_auth.load_state(state)
    except manage_auth.AuthError as exc:
        print('state: %s' % exc, file=out)
        return 1
    print('port: %d' % config['port'], file=out)
    print('linger: %s (service runs from login until logout unless yes; not changed by this tool)' % linger(), file=out)
    if config['repo'] != str(MODULE) or _owned_file(unit) != render_unit(state):
        print('WARNING: unit/repo differs from this checkout (moved or interpreter changed); run: manage.sh install', file=out)
    print('carrier: %s' % carrier_summary(state), file=out)
    print('entry: %s' % ('present' if (MODULE / 'index.html').is_file() else 'missing'), file=out)
    if legacy_manager_active(state) and not is_active():
        print('WARNING: a legacy foreground manager holds the session lock', file=out)
    try:
        snapshot = query_state(state)
        pending = snapshot.get('pending')
        print('service: reachable, authenticated; busy=%s writable=%s reason=%s pending=%s' % (
            snapshot.get('busy'), snapshot.get('writable'), snapshot.get('writable_reason'),
            pending.get('present') if isinstance(pending, dict) else None), file=out)
    except ServiceError as exc:
        print('service: %s' % exc, file=out)
    return 0


def find_opener():
    if sys.platform == 'darwin':
        return shutil.which('open')
    if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        raise OSError('no DISPLAY/WAYLAND_DISPLAY desktop session')
    return shutil.which('xdg-open')


def open_entry(out):
    """Hand only the private file URI to the system opener. Returns 0, or 1 with the manual path."""
    entry = MODULE / 'index.html'
    if not entry.is_file():
        raise ServiceError('Entry page %s not generated yet; run: manage.sh start' % entry)
    try:
        opener = find_opener()
        if opener is None:
            reason = 'no xdg-open/open on PATH'
        else:
            result = subprocess.run([opener, entry.as_uri()], shell=False, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                                    env={k: v for k, v in os.environ.items() if k != 'BROWSER'})
            reason = None if result.returncode == 0 else 'opener exited with status %d' % result.returncode
    except subprocess.TimeoutExpired:
        reason = 'opener timed out'
    except OSError as exc:
        reason = str(exc)
    if reason:
        print('Could not open a browser (%s). The service keeps running; open this file manually: %s' % (reason, entry), file=out)
        return 1
    print('Requested the system opener for %s' % entry, file=out)
    return 0


def serve(state):
    import manage
    import vps
    return manage.run_service(vps, Path(state))


# ---------------------------------------------------------------- CLI

def parser():
    p = argparse.ArgumentParser(
        prog='manage.sh', description='Persistent local VPS manager: user service lifecycle. Day to day, just open vps/index.html.',
        epilog='commands: install start stop status restart open uninstall rotate carrier {set,clear}. '
               'No command = install/start the service and open the entry page.')
    p.add_argument('command', nargs='?', choices=COMMANDS, help=argparse.SUPPRESS)
    p.add_argument('action', nargs='?', choices=('set', 'clear'), help=argparse.SUPPRESS)
    p.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    p.add_argument('--no-open', action='store_true', help='ensure the service is running but do not open a browser')
    p.add_argument('--ssh-proxy', help='carrier set: independent loopback SOCKS5/HTTP SSH carrier URL')
    p.add_argument('--direct', action='store_true', help='carrier set: explicit direct SSH (no proxy); reachability is not tested')
    p.add_argument('--independent-carrier', action='store_true', help='carrier set: acknowledge the route is independent of EVERY managed VPS entry')
    return p


def main(argv=None, out=None):
    out = out or sys.stdout
    args = parser().parse_args(argv)
    os.umask(0o077)
    state = args.state_dir.expanduser().absolute()
    command = args.command or 'open-default'
    try:
        if (command == 'carrier') != (args.action is not None) or (args.command != 'carrier' and (args.ssh_proxy or args.independent_carrier or args.direct)):
            raise ServiceError('usage: manage.sh carrier set (--ssh-proxy URL | --direct) --independent-carrier | carrier clear')
        if command == 'serve':
            return serve(state)
        if command == 'install':
            install(state, out)
        elif command == 'start':
            start(state, out)
        elif command == 'stop':
            stop(state, out)
        elif command == 'restart':
            restart(state, out)
        elif command == 'uninstall':
            uninstall(state, out)
        elif command == 'rotate':
            rotate(state, out)
        elif command == 'status':
            return status(state, out)
        elif command == 'carrier':
            if args.action == 'set':
                url = carrier_set(state, args.ssh_proxy, args.independent_carrier, args.direct)
                print('Carrier set to %s; effective for the next job (no restart).' % (url or 'direct SSH (not tested)'), file=out)
            else:
                print('Carrier cleared; writes are disabled.' if carrier_clear(state) else 'No carrier configured.', file=out)
        elif command == 'open':
            return open_entry(out)
        else:
            install(state, out)
            return 0 if args.no_open else open_entry(out)
    except (ServiceError, manage_auth.AuthError, ssh_proxy.Error, OSError) as exc:
        print('Error: %s' % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
