#!/usr/bin/env python3
"""One narrowly scoped tailscaled DERP proxy exception; no package/Clash changes."""
import argparse
import contextlib
import fcntl
import hashlib
import http.client
import json
import os
import pathlib
import pwd
import re
import selectors
import shlex
import socket
import subprocess
import sys
import tempfile
import time

SERVICE = 'tailscaled.service'
DERP = ('derp.dex-gem.ai:1443', '39.184.215.16:1443')
DEFAULTS = ('localhost', '127.0.0.1', '::1')
MARKER = '# Managed by quick-deploy/tailscale/derp-bypass.py'
TARGET = pathlib.Path('/etc/systemd/system/tailscaled.service.d/zz-quick-deploy-derp-bypass.conf')
LOCK = pathlib.Path('/run/lock/quick-deploy-tailscale-derp-bypass')
REPO = pathlib.Path(__file__).resolve().parent.parent
TIMEOUT = 600
PROXY_KEYS = ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy')


def run(args, timeout=20):
    p = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if p.returncode:
        # Do not relay arbitrary command output: Environment may contain proxy credentials.
        detail = re.sub(r'https?://\S+', '<URL omitted>', p.stderr.strip())[:1500]
        raise RuntimeError(f'{args[0]} {args[1] if len(args)>1 else ""} failed ({p.returncode}): {detail}')
    return p.stdout


def sha(data):
    return hashlib.sha256(data).hexdigest()


def environment(text):
    return dict(item.split('=', 1) for item in shlex.split(text) if '=' in item)


def merged_no_proxy(env):
    result = []
    for value in [env.get('NO_PROXY', ''), env.get('no_proxy', ''), ','.join(DEFAULTS + DERP)]:
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('NO_PROXY contains control characters; refusing to rewrite')
        for entry in value.split(','):
            entry = entry.strip()
            if entry and entry not in result:
                result.append(entry)
    return ','.join(result)


def environment_lines(env):
    value = merged_no_proxy(env).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    return '\n'.join(f'Environment="{key}={value}"' for key in ('NO_PROXY', 'no_proxy')) + '\n'


def policy_present(env):
    required = set(DEFAULTS + DERP)
    return all(required <= {x.strip() for x in env.get(k, '').split(',')} for k in ('NO_PROXY', 'no_proxy'))


def dropin(env):
    return (MARKER + '\n[Service]\n' + environment_lines(env)).encode()


def parser_test(binary='tailscale'):
    """Exercise the installed tshttpproxy via ts2021's pre-request verbose line.

    Kill only this diagnostic CLI after the decision line. No daemon debug API,
    login, prefs or auth key. At most a public /key GET can race termination.
    The deliberately closed loopback proxy has no credentials.
    """
    cases = [('derp.dex-gem.ai:1443', True), ('child.derp.dex-gem.ai:1443', True),
             ('39.184.215.16:1443', True), ('derp.dex-gem.ai:443', False),
             ('other.dex-gem.ai:1443', False), ('controlplane.tailscale.com:443', False),
             ('log.tailscale.com:443', False), ('39.184.215.16:443', False)]
    results = []
    for host, direct in cases:
        started = time.monotonic()
        env = {k: v for k, v in os.environ.items() if k.lower() not in
               ('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy') and k != 'REQUEST_METHOD'}
        env.update(HTTP_PROXY='http://127.0.0.1:9', HTTPS_PROXY='http://127.0.0.1:9',
                   NO_PROXY=merged_no_proxy({}), no_proxy=merged_no_proxy({}), LC_ALL='C')
        p = subprocess.Popen([binary, 'debug', 'ts2021', '--verbose', '--host', host],
                             env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        decision = None
        sel = selectors.DefaultSelector()
        sel.register(p.stdout, selectors.EVENT_READ)
        pending = b''
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and decision is None:
                if not sel.select(max(0, deadline-time.monotonic())):
                    break
                chunk = os.read(p.stdout.fileno(), 4096)
                if not chunk:
                    break
                pending += chunk
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    text = line.decode(errors='replace')
                    match = re.search(r'tshttpproxy\.ProxyFromEnvironment = \((<nil>|http://127\.0\.0\.1:9), <nil>\)', text)
                    if match:
                        decision = match.group(1)
                        break
        finally:
            sel.close()
            if p.poll() is None:
                p.terminate()
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
            p.stdout.close()
        expected = '<nil>' if direct else 'http://127.0.0.1:9'
        results.append({'host': host, 'expected': expected, 'observed': decision})
        if decision != expected:
            raise RuntimeError('installed parser decision mismatch: ' + json.dumps(results[-1]))
        time.sleep(max(0, 1-(time.monotonic()-started)))
    return results


def atomic(path, data, mode=0o600):
    fd, name = tempfile.mkstemp(prefix='.'+path.name+'.', dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def record(state, phase, **details):
    # Status contains no proxy URL, credentials or full service environment.
    atomic(state/'status.json', json.dumps({'phase': phase, 't': time.time(), **details}, indent=2).encode(), 0o644)


def load_plan(state):
    state = state.resolve(strict=True)
    p = state/'plan.json'
    if p.is_symlink() or p.stat().st_uid != 0 or state.stat().st_uid != 0:
        raise RuntimeError('require the root-owned original job directory')
    plan = json.loads(p.read_text())
    if pathlib.Path(plan['state']) != state or pathlib.Path(plan['target']) != TARGET:
        raise RuntimeError('job identity mismatch')
    return plan


def process_environment():
    pid = int(run(['systemctl', 'show', SERVICE, '-p', 'MainPID', '--value']).strip())
    if pid <= 0:
        raise RuntimeError('tailscaled has no active MainPID')
    raw = pathlib.Path(f'/proc/{pid}/environ').read_bytes()
    return dict(x.decode().split('=', 1) for x in raw.split(b'\0') if b'=' in x)


def recovery_env_hashes(env):
    # Preserve absent versus empty values without storing proxy credentials.
    return {k: sha(json.dumps(env.get(k)).encode()) for k in PROXY_KEYS+('NO_PROXY', 'no_proxy')}


def manager_environment():
    return environment(run(['systemctl', 'show', SERVICE, '-p', 'Environment', '--value']))


def original_manager(plan):
    if recovery_env_hashes(manager_environment()) != plan['original_manager_hashes']:
        raise RuntimeError('original manager proxy environment not restored')


def original_runtime(plan):
    original_manager(plan)
    run(['systemctl', 'is-active', '--quiet', SERVICE])
    if recovery_env_hashes(process_environment()) != plan['original_process_hashes']:
        raise RuntimeError('original process proxy environment not restored')
    node = node_identity()
    if node != plan['node'] or node.get('state') != 'Running':
        raise RuntimeError('original node identity/state not restored')
    return {'node': node}


def node_identity():
    d = json.loads(run(['tailscale', 'status', '--json']))
    s = d.get('Self', {})
    return {'id': s.get('ID'), 'ips': s.get('TailscaleIPs'), 'host': s.get('HostName'),
            'state': d.get('BackendState')}


def stable_rules(document):
    # Mihomo 1.19.32 includes live traffic counters in /rules. Retain routing
    # semantics (including disabled and unknown fields), not hit/miss telemetry.
    volatile = {'hitCount', 'hitAt', 'missCount', 'missAt'}
    return {**document, 'rules': [
        {**rule, **({'extra': {k: v for k, v in rule['extra'].items() if k not in volatile}}
                   if isinstance(rule.get('extra'), dict) else {})}
        for rule in document.get('rules', [])]}


def clash_state(uid):
    import yaml  # Already a dependency of clash-verge/lib/rules.py; no installation here.
    home = pathlib.Path(pwd.getpwuid(uid).pw_dir)
    f = home/'.local/share/io.github.clash-verge-rev.clash-verge-rev/clash-verge.yaml'
    cfg = yaml.safe_load(f.read_text())
    path = f'/run/clash-verge-service/users/{uid}/verge-mihomo.sock'

    class UnixHTTP(http.client.HTTPConnection):
        def connect(self):
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(5)
            self.sock.connect(path)

    def get(endpoint):
        c = UnixHTTP('localhost')
        c.request('GET', endpoint, headers={'Authorization': 'Bearer '+cfg.get('secret', '')})
        r = c.getresponse()
        data = r.read()
        c.close()
        if r.status != 200:
            raise RuntimeError('Clash read-only '+endpoint+' returned '+str(r.status))
        return json.loads(data)

    config = get('/configs')
    proxies = get('/proxies')['proxies']
    selectors_now = {k: {x: v.get(x) for x in ('type', 'now', 'all')} for k, v in proxies.items() if 'all' in v}
    fingerprints = {k: sha(json.dumps(v, sort_keys=True).encode()) for k, v in
                    {'config': config, 'rules': stable_rules(get('/rules')), 'selectors': selectors_now}.items()}
    return {'mode': config.get('mode'), 'fingerprints': fingerprints,
            'derp_connections': [{'sourcePort': c.get('metadata', {}).get('sourcePort'),
                                  'sourceIP': c.get('metadata', {}).get('sourceIP'),
                                  'type': c.get('metadata', {}).get('type'),
                                  'chains': c.get('chains'), 'rule': c.get('rule')}
                                 for c in get('/connections').get('connections', [])
                                 if c.get('metadata', {}).get('host') == 'derp.dex-gem.ai'
                                 or (c.get('metadata', {}).get('destinationIP') == '39.184.215.16'
                                     and str(c.get('metadata', {}).get('destinationPort')) == '1443')]}


def other_dropins():
    paths = shlex.split(run(['systemctl', 'show', SERVICE, '-p', 'DropInPaths', '--value']))
    fragment = run(['systemctl', 'show', SERVICE, '-p', 'FragmentPath', '--value']).strip()
    files = run(['systemctl', 'show', SERVICE, '-p', 'EnvironmentFiles', '--value'])
    paths += re.findall(r'(\S+) \(ignore_errors=(?:yes|no)\)', files)
    paths += [fragment] if fragment else []
    return {p: sha(pathlib.Path(p).read_bytes()) for p in paths if pathlib.Path(p) != TARGET}


def validate_runtime(plan):
    env = process_environment()
    if any(sha(env.get(k, '').encode()) != plan['proxy_hashes'][k] for k in PROXY_KEYS):
        raise RuntimeError('an original proxy setting changed')
    if env.get('NO_PROXY') != plan['no_proxy'] or env.get('no_proxy') != plan['no_proxy']:
        raise RuntimeError('running process did not consume both NO_PROXY values')
    node = node_identity()
    if node != plan['node'] or node['state'] != 'Running':
        raise RuntimeError('node identity/IP/backend state changed or not Running')
    current = clash_state(plan['uid'])
    if current['fingerprints'] != plan['clash']['fingerprints']:
        raise RuntimeError('Clash config/rules/selector state changed; refusing confirmation')
    ss = run(['ss', '-Htinpe', '--cgroup', '( cgroup = /system.slice/tailscaled.service )'])
    direct = [line for line in ss.splitlines() if re.search(r'\b39\.184\.215\.16:1443\b', line)
              and '/system.slice/tailscaled.service' in line]
    if not direct:
        raise RuntimeError('no actual tailscaled-owned direct DERP socket; bypass unverified')
    local_ports = set(re.findall(r'127\.0\.0\.1:(\d+)\s+127\.0\.0\.1:7897', ss))
    direct_ports = {line.split()[3].rsplit(':', 1)[-1] for line in direct}
    if any(str(c['sourcePort']) in local_ports | direct_ports for c in current['derp_connections']):
        raise RuntimeError('DERP still enters Clash through explicit proxy or TUN')
    route = json.loads(run(['ip', '-j', 'route', 'get', '39.184.215.16', 'mark', '0x80000']))
    if not route or route[0].get('dev') in ['Meta', 'tailscale0', 'lo']:
        raise RuntimeError('marked DERP underlay route is not a physical interface')
    return {'node': node, 'direct_derp_socket': direct, 'marked_derp_route': route, 'clash': current}


def release(state):
    if LOCK.exists() and LOCK.read_text().strip() == str(state):
        LOCK.unlink()


@contextlib.contextmanager
def recovery_lock(state, blocking=True):
    # One job-local kernel lock covers public handoff, recovery and confirmation.
    fd = os.open(state/'recovery.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError as e:
            raise RuntimeError('another recovery task/request already owns this job') from e
        yield
    finally:
        os.close(fd)


def frozen_entry(state, plan):
    entry = state/'entry.py'
    if (entry.is_symlink() or not entry.is_file() or entry.stat().st_uid != os.geteuid()
            or sha(entry.read_bytes()) != plan['entry_hash']):
        raise RuntimeError('frozen recovery entry ownership/hash mismatch')
    return entry


def process_identity(pid):
    proc = pathlib.Path(f'/proc/{pid}')
    fields = (proc/'stat').read_text().rsplit(') ', 1)[1].split()
    return {'pid': pid, 'start': fields[19], 'state': fields[0], 'uid': proc.stat().st_uid,
            'argv': [x.decode() for x in (proc/'cmdline').read_bytes().split(b'\0') if x]}


def guard_owner(state):
    path = state/'guard-owner.json'
    if path.is_symlink() or path.stat().st_uid != os.geteuid():
        raise RuntimeError('unowned guard identity record')
    return json.loads(path.read_text())


def guard_alive(state, plan):
    try:
        entry = frozen_entry(state, plan)
        owner = guard_owner(state)
        proc = process_identity(owner['pid'])
        if (proc['uid'] != os.geteuid() or proc['state'] in ('Z', 'X') or proc['start'] != owner['start']
                or proc['argv'][1:] != [str(entry), '_guard', str(state)]
                or pathlib.Path(proc['argv'][0]).resolve() != pathlib.Path(sys.executable).resolve()):
            return False
        pane = run(['tmux', '-L', 'pi-agent', 'display-message', '-p', '-t', owner['pane'],
                    '#{session_path}\t#{pane_id}\t#{pane_pid}\t#{pane_dead}']).strip().split('\t')
        return pane == [plan['repo'], owner['pane'], str(owner['pid']), '0']
    except (OSError, ValueError, KeyError, IndexError, RuntimeError):
        return False


def register_guard(state, plan):
    proc = process_identity(os.getpid())
    atomic(state/'guard-owner.json', json.dumps({'pid': proc['pid'], 'start': proc['start'],
                                                'pane': os.environ.get('TMUX_PANE', '')}).encode())
    if not guard_alive(state, plan):
        raise RuntimeError('frozen recovery guard process/tmux ownership not established')


def recovery_failed(state, error):
    status = json.loads((state/'status.json').read_text()) if (state/'status.json').exists() else {}
    phase = status.get('phase')
    if phase == 'recovery-needs-operator' and status.get('error') == str(error):
        return  # The same failure was already published under the job lock.
    record(state, 'recovery-needs-operator', error=str(error),
           last_phase=status.get('last_phase') if phase == 'recovery-needs-operator' else phase,
           recovery=status.get('recovery'))


def rollback(state):
    state = state.resolve(strict=True)
    with recovery_lock(state):
        try:
            plan = load_plan(state)
            # File equality is only one stage, never proof of manager/process recovery.
            now = TARGET.read_bytes() if TARGET.exists() else None
            old = (state/'previous.conf').read_bytes() if plan['previous_exists'] else None
            if old is not None and sha(old) != plan['previous_hash']:
                raise RuntimeError('original backup hash mismatch; refusing recovery with corrupt data')
            if now != old and (now is None or sha(now) != plan['candidate_hash']):
                raise RuntimeError('drop-in changed outside this job; refusing to overwrite it')
            if other_dropins() != plan['other_dropins']:
                raise RuntimeError('another service drop-in changed; manual recovery required')
            changed = now != old
            if changed:
                if old is None:
                    TARGET.unlink()
                else:
                    atomic(TARGET, old, plan['previous_mode'])
            progress = {'file_restored': True, 'manager_verified': False, 'process_verified': False}
            record(state, 'recovery-file-restored', recovery=progress)
            attempted = False
            try:
                evidence = original_runtime(plan)
            except (OSError, ValueError, RuntimeError):
                # One bounded attempt. Failure retains the lock; only an explicit
                # operator retry may start one further recovery attempt.
                attempted = True
                run(['systemctl', 'daemon-reload'])
                original_manager(plan)
                progress['manager_verified'] = True
                record(state, 'recovery-manager-restored', recovery=progress)
                run(['systemctl', 'restart', SERVICE], 60)
                evidence = original_runtime(plan)
            progress.update(manager_verified=True, process_verified=True)
            record(state, 'rolled-back' if changed or attempted else 'rolled-back-no-change',
                   recovery=progress, evidence=evidence)
            release(state)
        except Exception as e:
            # Publish non-serving state before another public request can acquire
            # serialization and mistake this exiting guard for a request receiver.
            recovery_failed(state, e)
            raise


def request_rollback(state):
    state = state.resolve(strict=True)
    plan = load_plan(state)
    with recovery_lock(state, blocking=False):
        if LOCK.exists():
            if LOCK.read_text().strip() != str(state):
                raise RuntimeError('a different job is pending')
        else:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w') as f:
                f.write(str(state)+'\n')
        status = json.loads((state/'status.json').read_text()) if (state/'status.json').exists() else {}
        terminal = status.get('phase') in ('confirmed', 'rolled-back', 'rolled-back-no-change', 'recovery-needs-operator')
        if not terminal and guard_alive(state, plan):
            atomic(state/'rollback-now', b'rollback', 0o644)
            return
        entry = frozen_entry(state, plan)
        atomic(state/'rollback-now', b'rollback', 0o644)
        # Stable job slot is a second duplicate-start refusal during handoff.
        tmux_window(pathlib.Path(plan['repo']), 'tailscale-derp-rollback', state.name,
                    [sys.executable, str(entry), '_guard', str(state)])


def guard(state):
    state = state.resolve(strict=True)
    plan = load_plan(state)
    register_guard(state, plan)
    atomic(state/'guard-ready', b'ready', 0o644)
    deadline = time.monotonic()+TIMEOUT
    try:
        while time.monotonic() < deadline:
            if (state/'rollback-now').exists():
                break
            if (state/'ack/confirm').exists():
                with recovery_lock(state):
                    if (state/'rollback-now').exists():
                        break
                    status = json.loads((state/'status.json').read_text())
                    if status['phase'] == 'verified-awaiting-confirmation':
                        evidence = validate_runtime(plan)
                        record(state, 'confirmed', evidence=evidence)
                        release(state)
                        return
            time.sleep(1)
    except Exception as e:
        record(state, 'confirmation-failed-rolling-back', error=str(e))
    rollback(state)


def worker(state):
    plan = load_plan(state)
    try:
        if not (state/'guard-ready').exists():
            raise RuntimeError('independent rollback guard not ready')
        current = TARGET.read_bytes() if TARGET.exists() else None
        previous = (state/'previous.conf').read_bytes() if plan['previous_exists'] else None
        env = process_environment()
        pid = int(run(['systemctl', 'show', SERVICE, '-p', 'MainPID', '--value']).strip())
        if (current != previous or pid != plan['original_pid'] or other_dropins() != plan['other_dropins']
                or env.get('NO_PROXY', '') != plan['original_upper'] or env.get('no_proxy', '') != plan['original_lower']
                or any(sha(env.get(k, '').encode()) != plan['proxy_hashes'][k] for k in PROXY_KEYS)):
            raise RuntimeError('pre-change service state drifted; no write')
        candidate = (state/'candidate.conf').read_bytes()
        if sha(candidate) != plan['candidate_hash']:
            raise RuntimeError('candidate hash mismatch; no write')
        atomic(TARGET, candidate, 0o644)
        record(state, 'restarting-tailscaled')
        run(['systemctl', 'daemon-reload'])
        run(['systemctl', 'restart', SERVICE], 60)
        # One low-rate discovery request opens the relay; no force-region or broad eviction.
        peer = '100.125.138.103' if '100.123.34.64' in plan['node']['ips'] else '100.123.34.64'
        try:
            run(['tailscale', 'ping', '--c=1', '--until-direct=false', '--timeout=5s', peer], 8)
        except (RuntimeError, subprocess.TimeoutExpired):
            pass  # Verification below is authoritative; this trigger may race startup.
        error = None
        deadline = time.monotonic()+45
        while time.monotonic() < deadline:
            try:
                evidence = validate_runtime(plan)
                record(state, 'verified-awaiting-confirmation', evidence=evidence,
                       confirm='After a fresh ShellGate SSH/access check, run the non-root confirm command printed by apply.')
                return
            except (RuntimeError, OSError, ValueError) as e:
                error = str(e)
                time.sleep(1)
        raise RuntimeError(error or 'runtime validation timed out')
    except Exception as e:
        record(state, 'failed-awaiting-rollback', error=str(e))
        atomic(state/'rollback-now', b'rollback', 0o644)
        raise


def tmux_window(repo, task, run_id, command):
    """Native pi-agent ownership checks; never address an existing pane by label."""
    session = 'quick-deploy-'+sha(str(repo).encode())[:10]
    prefix = ['tmux', '-L', 'pi-agent']
    exists = subprocess.run(prefix+['has-session', '-t', session], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not exists:
        run(prefix+['new-session', '-d', '-s', session, '-n', 'anchor', '-c', str(repo)])
    if run(prefix+['display-message', '-p', '-t', session, '#{session_path}']).strip() != str(repo):
        raise RuntimeError('tmux project session path mismatch')
    inventory = run(prefix+['list-panes', '-s', '-t', session, '-F', '#{pane_current_path}']).splitlines()
    if not inventory or any(pathlib.Path(p).resolve() != repo for p in inventory):
        raise RuntimeError('tmux project pane ownership unclear')
    run(prefix+['set-option', '-t', session, 'renumber-windows', 'off'])
    digest = sha(('task='+task+'\0run='+run_id).encode())[:8]
    slot = 100+int(digest, 16)%9000
    # Only this newly owned window: failed guards must vacate their fixed slot,
    # and exec makes the frozen guard itself the pane PID used for ownership.
    launch = 'tmux -L pi-agent set-option -w -t "$TMUX_PANE" remain-on-exit off && exec '+shlex.join(command)
    result = run(prefix+['new-window', '-d', '-P', '-F', '#{window_id}:#{pane_id}', '-t', f'{session}:{slot}', '-n', task+'-'+digest, '-c', str(repo), launch]).strip()
    if not re.fullmatch(r'@\d+:%\d+', result):
        raise RuntimeError('invalid tmux creation result')
    return {'session': session, 'slot': slot, 'window_pane': result, 'inventory': inventory}


def apply():
    if os.geteuid() != 0:
        raise RuntimeError('apply requires the user to run this exact entry with sudo; no password prompt here')
    if LOCK.exists():
        raise RuntimeError('another pending job exists: '+LOCK.read_text().strip())
    uid = REPO.stat().st_uid
    if uid == 0 or os.environ.get('SUDO_UID') != str(uid):
        raise RuntimeError('run sudo from this repository owner, not an unrelated root session')
    if TARGET.is_symlink() or (TARGET.exists() and not TARGET.read_text().startswith(MARKER+'\n')):
        raise RuntimeError('refusing a foreign or symlinked target drop-in')
    # EnvironmentFile assignments override Environment=. Refuse rather than pretend.
    files = run(['systemctl', 'show', SERVICE, '-p', 'EnvironmentFiles', '--value'])
    for path in re.findall(r'(\S+) \(ignore_errors=(?:yes|no)\)', files):
        for line in pathlib.Path(path).read_text().splitlines():
            if re.match(r'\s*(?:export\s+)?(?:NO_PROXY|no_proxy)\s*=', line):
                raise RuntimeError('EnvironmentFile owns NO_PROXY; this entry cannot override it: '+path)
    proxy_parser = parser_test()
    env = process_environment()
    if not env.get('HTTP_PROXY') and not env.get('http_proxy') and not env.get('HTTPS_PROXY') and not env.get('https_proxy'):
        raise RuntimeError('no existing explicit HTTP proxy; this intervention is not applicable')
    candidate = dropin(env)
    node = node_identity()
    if node['state'] != 'Running' or not ({'100.123.34.64', '100.125.138.103'} & set(node['ips'] or [])):
        raise RuntimeError('unexpected Tailscale identity/state')
    plan = {'uid': uid, 'repo': str(REPO), 'target': str(TARGET), 'node': node,
            'original_pid': int(run(['systemctl', 'show', SERVICE, '-p', 'MainPID', '--value']).strip()),
            'original_manager_hashes': recovery_env_hashes(manager_environment()),
            'original_process_hashes': recovery_env_hashes(env),
            'entry_hash': sha(pathlib.Path(__file__).read_bytes()),
            'no_proxy': merged_no_proxy(env), 'original_upper': env.get('NO_PROXY', ''),
            'original_lower': env.get('no_proxy', ''), 'proxy_hashes': {k: sha(env.get(k, '').encode()) for k in PROXY_KEYS},
            'clash': clash_state(uid), 'other_dropins': other_dropins(), 'parser_test': proxy_parser,
            'previous_exists': TARGET.exists(), 'previous_mode': (TARGET.stat().st_mode & 0o777) if TARGET.exists() else 0o644,
            'previous_hash': sha(TARGET.read_bytes()) if TARGET.exists() else None,
            'candidate_hash': sha(candidate)}
    if TARGET.exists() and TARGET.read_bytes() == candidate:
        evidence = validate_runtime(plan)
        print(json.dumps({'phase': 'already-active-no-write-no-restart', 'evidence': evidence}, indent=2))
        return
    base = REPO/'.memory/local/tailscale-derp-bypass'
    base.mkdir(parents=True, exist_ok=True)
    state = pathlib.Path(tempfile.mkdtemp(prefix=time.strftime('%Y%m%dT%H%M%S')+'-', dir=base))
    state.chmod(0o711)
    plan['state'] = str(state)
    fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(str(state)+'\n')
    guard_started = False
    try:
        atomic(state/'plan.json', json.dumps(plan, indent=2).encode())
        atomic(state/'candidate.conf', candidate)
        if TARGET.exists():
            atomic(state/'previous.conf', TARGET.read_bytes())
        unit = state/'policy-check.service'
        atomic(unit, ('[Unit]\nDescription=DERP proxy environment parser check\n[Service]\nExecStart=/usr/bin/true\n'+environment_lines(env)).encode())
        run(['systemd-analyze', 'verify', str(unit)])
        # Frozen root-owned code makes recovery independent of SSH and later edits.
        atomic(state/'entry.py', pathlib.Path(__file__).read_bytes())
        (state/'ack').mkdir(mode=0o700)
        os.chown(state/'ack', uid, pwd.getpwuid(uid).pw_gid)
        record(state, 'prepared', rollback_timeout_seconds=TIMEOUT)
        guardian = tmux_window(REPO, 'tailscale-derp-guard', state.name,
                               [sys.executable, str(state/'entry.py'), '_guard', str(state)])
        guard_started = True
        for _ in range(30):
            if (state/'guard-ready').exists():
                break
            time.sleep(.1)
        else:
            raise RuntimeError('rollback guard did not become ready; no configuration written')
        actor = tmux_window(REPO, 'tailscale-derp-apply', state.name,
                           [sys.executable, str(state/'entry.py'), '_worker', str(state)])
        atomic(state/'tmux.json', json.dumps({'guard': guardian, 'worker': actor}, indent=2).encode(), 0o644)
    except Exception:
        atomic(state/'rollback-now', b'rollback', 0o644)
        if not guard_started:
            release(state)  # No worker was launched and no configuration was written.
        raise
    print('已启动本机独立 tmux 任务；只重启 tailscaled 一次，Clash/其他应用不变。')
    print('状态: '+shlex.join([sys.executable, str(REPO/'tailscale/derp-bypass.py'), 'status', str(state)]))
    print('10分钟内完成新SSH、实际socket和其他代理核验后，由原用户确认:')
    print(shlex.join([sys.executable, str(REPO/'tailscale/derp-bypass.py'), 'confirm', str(state)]))
    print('未确认、校验失败或中断会恢复原独立drop-in并重启tailscaled。状态目录: '+str(state))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['apply', 'status', 'confirm', 'rollback', 'parser-test', 'environment-lines', 'policy-check', '_worker', '_guard'])
    p.add_argument('state', nargs='?', type=pathlib.Path)
    args = p.parse_args()
    if args.action in ['environment-lines', 'policy-check']:
        env = environment(sys.stdin.read())
        if args.action == 'environment-lines':
            print(environment_lines(env), end='')
            return 0
        return 0 if policy_present(env) else 1
    if args.action == 'parser-test':
        print(json.dumps(parser_test(), indent=2))
        return 0
    if args.action == 'apply':
        apply()
        return 0
    if not args.state:
        p.error('this action requires the printed job directory')
    if args.action == 'status':
        print((args.state/'status.json').read_text(), end='')
    elif args.action == 'confirm':
        status = json.loads((args.state/'status.json').read_text())
        if status['phase'] != 'verified-awaiting-confirmation':
            raise RuntimeError('job is not awaiting confirmation: '+status['phase'])
        atomic(args.state/'ack/confirm', b'confirmed after fresh access/path checks')
        print('Confirmation requested; inspect status again for confirmed, not merely this file.')
    else:
        if os.geteuid() != 0:
            raise RuntimeError('root required; no password prompt or alternate elevation attempted')
        if args.action == 'rollback':
            request_rollback(args.state)
        elif args.action == '_worker':
            worker(args.state)
        else:
            try:
                guard(args.state)
            except Exception as e:
                recovery_failed(args.state, e)
                raise
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        print('DERP bypass: '+str(e), file=sys.stderr)
        sys.exit(1)
