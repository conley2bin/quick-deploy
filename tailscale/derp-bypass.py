#!/usr/bin/env python3
"""One narrowly scoped tailscaled DERP proxy exception; no package/Clash changes."""
import argparse
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
import uuid

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


def node_identity():
    d = json.loads(run(['tailscale', 'status', '--json']))
    s = d.get('Self', {})
    return {'id': s.get('ID'), 'ips': s.get('TailscaleIPs'), 'host': s.get('HostName'),
            'state': d.get('BackendState')}


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
                    {'config': config, 'rules': get('/rules'), 'selectors': selectors_now}.items()}
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


def rollback(state):
    plan = load_plan(state)
    # Never overwrite a concurrent operator's change, even during automatic recovery.
    now = TARGET.read_bytes() if TARGET.exists() else None
    old = (state/'previous.conf').read_bytes() if plan['previous_exists'] else None
    if old is not None and sha(old) != plan['previous_hash']:
        raise RuntimeError('original backup hash mismatch; refusing recovery with corrupt data')
    if now == old:
        release(state)
        record(state, 'rolled-back-no-change')
        return
    if now is None or sha(now) != plan['candidate_hash']:
        raise RuntimeError('drop-in changed outside this job; refusing to overwrite it')
    if other_dropins() != plan['other_dropins']:
        raise RuntimeError('another service drop-in changed; manual recovery required')
    if old is None:
        TARGET.unlink()
    else:
        atomic(TARGET, old, plan['previous_mode'])
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'restart', SERVICE], 60)
    run(['systemctl', 'is-active', '--quiet', SERVICE])
    release(state)
    record(state, 'rolled-back', node=node_identity())


def guard(state):
    plan = load_plan(state)
    atomic(state/'guard-ready', b'ready', 0o644)
    deadline = time.monotonic()+TIMEOUT
    try:
        while time.monotonic() < deadline:
            if (state/'rollback-now').exists():
                break
            if (state/'ack/confirm').exists():
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
    result = run(prefix+['new-window', '-d', '-P', '-F', '#{window_id}:#{pane_id}', '-t', f'{session}:{slot}', '-n', task+'-'+digest, '-c', str(repo), shlex.join(command)]).strip()
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
            plan = load_plan(args.state)
            if LOCK.exists():
                if LOCK.read_text().strip() != str(args.state.resolve()):
                    raise RuntimeError('a different job is pending')
                atomic(args.state/'rollback-now', b'rollback', 0o644)
            else:
                tmux_window(pathlib.Path(plan['repo']), 'tailscale-derp-rollback', str(uuid.uuid4()),
                            [sys.executable, str(args.state/'entry.py'), '_guard', str(args.state)])
                atomic(args.state/'rollback-now', b'rollback', 0o644)
        elif args.action == '_worker':
            worker(args.state)
        else:
            try:
                guard(args.state)
            except Exception as e:
                record(args.state, 'recovery-needs-operator', error=str(e))
                raise
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        print('DERP bypass: '+str(e), file=sys.stderr)
        sys.exit(1)
