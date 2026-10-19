#!/usr/bin/env python3
"""Thin SSH lifecycle for one dedicated Ubuntu24.04 amd64 3x-ui node."""
from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.request import build_opener, ProxyHandler
import uuid

import host

MODULE = Path(__file__).resolve().parents[1]
DEFAULT_STATE = Path.home() / '.local/state/quick-deploy-private/vps'


def private_dir(path):
    path = Path(path)
    if path.is_symlink():
        raise host.Error('Refusing symlink private directory')
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    st = path.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise host.Error('Private directory requires current owner and mode0700')
    return path


def read_private(path):
    if path.is_symlink() or not path.is_file():
        raise host.Error('Missing regular private file')
    st = path.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise host.Error('Private file requires current owner and mode0600')
    return path.read_bytes()


def load(path):
    try:
        return json.loads(read_private(path))
    except ValueError as exc:
        raise host.Error('Invalid private JSON; preserved without regeneration') from exc


def check_legacy(state):
    for name in ('identity.json', 'inventory.json', 'generated'):
        if (state / name).exists() or (state / name).is_symlink():
            raise host.Error('Legacy sing-box/dual-role private state detected; preserved. Use a separate --state-dir for 3x-ui; migration is not automatic')


@contextlib.contextmanager
def locked(state):
    check_legacy(state)
    private_dir(state)
    fd = os.open(state / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError as exc:
        raise host.Error('Another VPS command owns this state directory') from exc
    finally:
        os.close(fd)


def confirm(message):
    if input(message + '\nType YES: ').strip() != 'YES':
        raise host.Error('Not confirmed; no publication authorized')


def configuration(args, saved=None):
    values = dict(saved or {})
    for key, prompt in [('domain', 'Grey DNS subscription hostname'), ('reality_target', 'Verified REALITY TLS1.3+h2 target hostname')]:
        provided = getattr(args, key, None)
        if saved and provided and provided != saved[key]:
            raise host.Error('Configuration differs from stored deployment; no silent domain/target change')
        if provided:
            values[key] = provided
        if not values.get(key):
            if not sys.stdin.isatty():
                raise host.Error('First setup requires --domain and --reality-target (or interactive input)')
            values[key] = input(prompt + ': ').strip()
    contact = getattr(args, 'acme_email', None)
    if saved:
        if contact is not None and contact != saved['acme_email']:
            raise host.Error('ACME contact differs from stored deployment; no implicit contact change')
    else:
        values['acme_email'] = '' if contact is None else contact
    port = getattr(args, 'proxy_port', None)
    if saved:
        if port is not None and port != saved['proxy_port']:
            raise host.Error('proxy_port differs from stored deployment; no implicit listener migration')
    else:
        values['proxy_port'] = host.DEFAULT_PROXY_PORT if port is None else port
    host.validate_config(values)
    return values


def state_document(state, args, create=False):
    path = state / 'state.json'
    if path.exists():
        document = load(path)
        if document.get('format') != host.FORMAT:
            raise host.Error('Unknown local state; no automatic import or regeneration')
        configuration(args, document['config'])
        return document
    if not create:
        raise host.Error('No 3x-ui state; deploy first, or restore with --backup and explicit --ssh')
    return {'format': host.FORMAT, 'owner': str(uuid.uuid4()), 'config': configuration(args), 'current': None,
            'previous': [], 'backup': None}


def target_record(args, current=None):
    target = getattr(args, 'ssh', None)
    if not target:
        if not current:
            raise host.Error('Supply --ssh for this target')
        return current
    if not re.fullmatch(r'(?:[A-Za-z_][A-Za-z0-9_-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*', target):
        raise host.Error('--ssh is user@host or a verified alias, never a shell command')
    options = ['-o', 'StrictHostKeyChecking=yes', '-o', 'BatchMode=yes', '-o', 'ControlMaster=no', '-o', 'ControlPath=none',
               '-o', 'ClearAllForwardings=yes', '-o', 'ProxyCommand=none', '-o', 'ProxyJump=none', '-o', 'ConnectTimeout=15']
    same_target = current is not None and target == current['target']
    port_override = getattr(args, 'port', None)
    identity = getattr(args, 'identity', None)
    if same_target:
        if port_override is None:
            port_override = current['port']
        saved = current['options']
        if identity is None and '-i' in saved:
            identity = Path(saved[saved.index('-i') + 1])
    if port_override is not None:
        if not 1 <= port_override <= 65535:
            raise host.Error('Invalid SSH port')
        options += ['-p', str(port_override)]
    if identity is not None:
        options += ['-i', str(identity.expanduser().absolute()), '-o', 'IdentitiesOnly=yes']
    # Resolve aliases through OpenSSH, never parse/read private SSH config here.
    result = host.run(['ssh', '-G', *options, target], timeout=15)
    effective = {}
    for line in result.stdout.decode().splitlines():
        key, _, value = line.partition(' ')
        effective.setdefault(key, value)
    endpoint = host.public_ip(effective.get('hostname', ''))
    user, port = effective.get('user', ''), int(effective.get('port', '0'))
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*', user) or not 1 <= port <= 65535:
        raise host.Error('Cannot resolve SSH user/port')
    record = {'target': target, 'options': options, 'endpoint': endpoint, 'user': user, 'port': port}
    if current and endpoint == current['endpoint']:
        if user != current['user'] or port != current['port']:
            raise host.Error('SSH identity/port change requires explicit review, not implicit reassignment')
        record.update({key: current[key] for key in ('machine_id',) if key in current})
    return record


def remote(record, action, request=None, stdin=None, stdout=None):
    # Source is public code in argv; payloads/credentials travel ONLY over stdin.
    source = (MODULE / 'lib/host.py').read_text()
    prefix = '' if record['user'] == 'root' else 'sudo -n '
    command = prefix + 'env QD_SSH_CONNECTION="$SSH_CONNECTION" python3 -c ' + shlex.quote(source) + ' ' + shlex.quote(action)
    endpoint = host.public_ip(record['endpoint'])
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*', record['user']) or not 1 <= int(record['port']) <= 65535:
        raise host.Error('Invalid saved SSH transport identity')
    # Resolve once, then bind the transport itself BEFORE any private stdin is
    # released. Alias matching/normal host-key checking remain in OpenSSH.
    pinned = ['-o', 'HostName=' + endpoint, '-o', 'User=' + record['user'], '-o', 'Port=' + str(record['port'])]
    argv = ['ssh', '-T', *pinned, *record['options'], record['target'], command]
    kwargs = {'check': False, 'stderr': subprocess.PIPE}
    if stdin is not None:
        kwargs['stdin'] = stdin
    else:
        kwargs['input'] = host.dumps(request or {}).encode()
    kwargs['stdout'] = stdout or subprocess.PIPE
    result = subprocess.run(argv, **kwargs)
    if result.returncode:
        # Native host code emits redacted errors; OpenSSH errors contain no feed
        # payload, and are necessary for diagnosing host-key/authentication faults.
        error = result.stderr.decode(errors='replace').strip()
        raise host.Error(error[:1500] or 'SSH operation failed; inspect persistent pending state')
    if stdout is not None:
        return None
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise host.Error('Invalid SSH protocol response; no state committed locally') from exc


def request_for(document, record):
    result = {key: document[key] for key in ('owner', 'config')}
    result['endpoint'] = record['endpoint']
    if record.get('machine_id'):
        result['machine_id'] = record['machine_id']
    return result


def release_lock():
    return json.loads((MODULE / 'release-lock.json').read_text())


def artifacts(state, lock):
    folder = private_dir(state / 'artifacts')
    # A dead proxy selected in the management PC must not be required to fetch
    # new-node releases. This changes no environment/client settings.
    opener = build_opener(ProxyHandler({}))
    for component in ('panel', 'core', 'front'):
        pin = lock[component]
        path = folder / pin['file']
        if path.exists():
            read_private(path)
        else:
            if not pin['url'].startswith('https://github.com/'):
                raise host.Error('Only pinned official GitHub release URLs are supported')
            fd, tmp = tempfile.mkstemp(prefix='.download-', dir=folder)
            try:
                with os.fdopen(fd, 'wb') as out, opener.open(pin['url'], timeout=90) as source:
                    total = 0
                    while chunk := source.read(1024 * 1024):
                        total += len(chunk)
                        if total > host.MAX_ARCHIVE:
                            raise host.Error('Oversized release artifact')
                        out.write(chunk)
                    out.flush()
                    os.fsync(out.fileno())
                if host.digest(Path(tmp)) != pin['sha256']:
                    raise host.Error('Official release artifact digest mismatch')
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
    host.verify_artifacts(folder, lock)
    return folder


def upload_bundle(request, folder=None, backup=None):
    stream = tempfile.TemporaryFile()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        data = host.dumps(request).encode()
        item = tarfile.TarInfo('request.json')
        item.size, item.mode = len(data), 0o600
        archive.addfile(item, io.BytesIO(data))
        archive.add(MODULE / 'lib/host.py', arcname='host.py', recursive=False)
        if backup:
            archive.add(backup, arcname='restore.tar', recursive=False)
        elif 'device_action' not in request:
            for component in ('panel', 'core', 'front'):
                path = folder / request['lock'][component]['file']
                archive.add(path, arcname='artifacts/' + path.name, recursive=False)
    stream.seek(0)
    return stream


def inspect_backup(path, lock):
    read_private(path)
    with tempfile.TemporaryDirectory(prefix='qd-backup-inspect-') as tmp:
        stage = Path(tmp)
        with path.open('rb') as stream:
            host.safe_unpack(stream, stage)
        return host.verify_backup(stage, lock)


def backup_remote(state, document, record):
    directory = private_dir(state / 'backups')
    path = directory / (time.strftime('%Y%m%dT%H%M%S') + '-' + secrets.token_hex(4) + '.tar')
    own_pending = not (state / 'pending.json').exists()
    if own_pending:
        host.atomic(state / 'pending.json', host.dumps({'record': record, 'mode': 'backup'}))
    try:
        with path.open('xb') as out:
            os.chmod(path, 0o600)
            source = (MODULE / 'lib/host.py').read_bytes()
            request = dict(request_for(document, record), adapter_b64=base64.b64encode(source).decode(),
                           adapter_sha256=host.digest(MODULE / 'lib/host.py'))
            remote(record, 'backup', request, stdout=out)
            out.flush()
            os.fsync(out.fileno())
        manifest = inspect_backup(path, release_lock())
        if manifest['owner'] != document['owner'] or manifest['endpoint'] != record['endpoint']:
            raise host.Error('Exported backup owner/endpoint mismatch')
        if own_pending:
            (state / 'pending.json').unlink()
        return path
    except BaseException:
        if path.exists():
            path.rename(path.with_suffix('.incomplete'))
        raise


def resolve_mihomo(binary=None):
    selected = binary or shutil.which('verge-mihomo') or shutil.which('mihomo')
    resolved = shutil.which(os.path.expanduser(str(selected))) if selected else None
    if not resolved:
        raise host.Error('Install native Mihomo or use --mihomo with an available executable; no skipped format gate')
    return str(Path(resolved).absolute())


def validate_outputs(state, document, record, binary=None, backup=None):
    binary = resolve_mihomo(binary)
    manifest = inspect_backup(backup, release_lock())
    outputs = remote(record, 'outputs', request_for(document, record))
    expected = {d + ext for d in host.device_names(manifest) if manifest['accounts'][d]['enable'] for ext in ('.yaml', '.txt')}
    if set(outputs) != expected:
        raise host.Error('Native outputs disagree with verified backup roster')
    output = private_dir(state / 'outputs')
    with tempfile.TemporaryDirectory(prefix='qd-native-check-') as tmp:
        root = Path(tmp)
        # Both are genuine V2Ray geodata shipped with the pinned Xray release;
        # -t needs these to validate GEOIP/GEOSITE rules without downloading.
        with tarfile.open(backup) as archive:
            for name in ('geoip.dat', 'geosite.dat'):
                data = archive.extractfile('deployment/bin/' + name).read()
                host.atomic(root / name, data)
        for name, encoded in outputs.items():
            if name not in expected:
                raise host.Error('Unexpected native output filename')
            data = base64.b64decode(encoded, validate=True)
            host.validate_feed_scope(manifest, name.rsplit('.', 1)[0], data, 'sub' if name.endswith('.txt') else 'mihomo')
            host.atomic(output / name, data)
            if name.endswith('.yaml'):
                config = root / name
                host.atomic(config, data)
                host.run([binary, '-t', '-d', root, '-f', config], timeout=60)
    manifest = inspect_backup(backup, release_lock())
    host.atomic(output / 'subscription-urls.json', host.dumps(subscription_urls(manifest)))


def subscription_urls(manifest):
    return {device: {'mihomo': f"https://{manifest['config']['domain']}:8443/mihomo/{account['subId']}",
                     'shadowrocket': f"https://{manifest['config']['domain']}:8443/sub/{account['subId']}"}
            for device in host.device_names(manifest) if (account := manifest['accounts'][device])['enable']}


def export_urls(state):
    """Offline convenience copy from committed authority, never candidate outputs."""
    if (state / 'pending.json').exists() or (state / 'pending.json').is_symlink():
        raise host.Error('Pending transaction is ambiguous; use recover before export')
    document = load(state / 'state.json')
    if document.get('format') != host.FORMAT or not document.get('current') or not document.get('backup'):
        raise host.Error('Export requires saved committed state and its verified backup')
    manifest = inspect_backup(Path(document['backup']), release_lock())
    if (manifest['owner'] != document['owner'] or manifest['config'] != document['config']
            or manifest['endpoint'] != document['current']['endpoint']):
        raise host.Error('Saved backup does not match committed owner/config/endpoint')
    destination = MODULE / 'subscription-urls.json'
    temporary = '.subscription-urls-' + secrets.token_hex(12) + '.tmp'
    if MODULE.is_symlink() or destination.is_symlink():
        raise host.Error('Refusing symlink subscription export destination')
    # Check actual Git behavior before placing any credentials in the repository.
    root = host.run(['git', '-C', MODULE, 'rev-parse', '--show-toplevel']).stdout.decode().strip()
    if Path(root) != MODULE.parent:
        raise host.Error('Export requires the complete quick-deploy Git worktree')
    if host.run(['git', '-C', root, 'ls-files', '--', 'vps/subscription-urls.json', 'vps/.subscription-urls-*.tmp']).stdout:
        raise host.Error('Refusing tracked subscription export paths; no automatic untracking')
    for name in (destination.name, temporary):
        try:
            host.run(['git', '-C', root, 'check-ignore', '-q', '--', 'vps/' + name])
        except host.Error as exc:
            raise host.Error('Subscription export and its temporary files must be Git-ignored') from exc
    # Pin the existing code directory without changing its normal permissions.
    directory = os.open(MODULE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    created = False
    try:
        info = os.fstat(directory)
        if info.st_uid != os.getuid():
            raise host.Error('Export directory requires current owner')
        try:
            info = os.stat(destination.name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise host.Error('Existing export must be a regular current-owner mode0600 file, not a symlink')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        created = True
        with os.fdopen(fd, 'wb') as out:
            os.fchmod(out.fileno(), 0o600)
            out.write(host.dumps(subscription_urls(manifest)).encode())
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, destination.name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            if created:
                os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        finally:
            os.close(directory)
    return destination


def export_after_commit(state):
    try:
        path = export_urls(state)
    except (host.Error, OSError, ValueError) as exc:
        raise host.Error('Deployment already committed; local subscription export failed. '
                         'Fix the local destination/Git exclusion and retry with setup.sh export; no redeploy needed') from exc
    print('Private subscription URL copy: ' + str(path))


def remember_commit(state, document, record, backup, result):
    previous = document['current']
    if previous and previous['endpoint'] != record['endpoint'] and previous not in document['previous']:
        document['previous'].append(previous)
    record = dict(record, machine_id=result['machine_id'])
    document.update(current=record, backup=str(backup.absolute()))
    host.atomic(state / 'state.json', host.dumps(document))
    if (state / 'pending.json').exists():
        (state / 'pending.json').unlink()


def deploy(state, document, args):
    lock = release_lock()
    if (state / 'pending.json').exists():
        raise host.Error('Local pending transaction exists; use recover before deploy/replace')
    record = target_record(args, document['current'])
    replacing = args.command in ('replace', 'restore')
    if args.command == 'deploy' and document['current'] and record['endpoint'] != document['current']['endpoint']:
        raise host.Error('Different host: use replace; never silently take over')
    backup = args.backup if replacing and args.backup else (Path(document['backup']) if replacing and document['backup'] else None)
    if replacing:
        if not backup:
            raise host.Error('Replacement requires an off-node backup; old host will never be contacted')
        manifest = inspect_backup(backup.expanduser(), lock)
        if manifest['owner'] != document['owner'] or manifest['config'] != document['config']:
            raise host.Error('Backup does not belong to this deployment')
        if document['current'] and record['endpoint'] == document['current']['endpoint']:
            raise host.Error('Replace/restore targets must be fresh hosts, not the currently owned node')
    request = request_for(document, record)
    info = remote(record, 'preflight', request)
    record['machine_id'] = info['machine_id']
    request = dict(request_for(document, record), lock=lock, mode=args.command,
                   adapter_sha256=host.digest(MODULE / 'lib/host.py'))
    confirm(f"Preflight passed for the explicit SSH target. This dedicated node will run REALITY{document['config']['proxy_port']}, TLS subscriptions8443 and HTTP01 only on80. SSH/firewall/Tailscale stay unchanged. Proceed?")
    folder = None if backup else artifacts(state, lock)
    pending = {'record': record, 'mode': args.command, 'backup': None}
    host.atomic(state / 'pending.json', host.dumps(pending))
    # The first setup config/ownership is durable before remote activation.
    host.atomic(state / 'state.json', host.dumps(document))
    try:
        with upload_bundle(request, folder, backup) as stream:
            prepared = remote(record, 'prepare', stdin=stream)
        offnode = backup_remote(state, document, record)
        pending['backup'] = str(offnode.absolute())
        host.atomic(state / 'pending.json', host.dumps(pending))
        validate_outputs(state, document, record, args.mihomo, offnode)
        # New SSH process, not a reused control connection; trusted certificate
        # and backend validation have already succeeded before this receipt.
        if prepared['unchanged']:
            result = remote(record, 'status', request_for(document, record))
        else:
            confirm('Native core/three full Mihomo profiles and scoped URI feeds validated; trusted TLS and a complete unencrypted OFF-NODE backup exist. Publish the six exact device feed paths? Real phone/desktop connectivity remains a separate user test.')
            result = remote(record, 'commit', dict(request_for(document, record), offnode_sha256=host.digest(offnode)))
        remember_commit(state, document, record, offnode, result)
    except BaseException:
        print('Transaction incomplete. Local pending intent is retained; run recover. No SSH, firewall, DNS or PC network policy was changed.', file=sys.stderr)
        raise
    print('Deployment committed. Private native profiles and subscription URLs: ' + str(state / 'outputs'))
    print('Off-node backup is UNENCRYPTED (0700/0600): ' + str(offnode))
    if replacing:
        print('Operator must update grey DNS to the new endpoint and REFRESH clients. Old node was not contacted or revoked; cold replacement has downtime.')
    export_after_commit(state)


def devices(state, document, args):
    record = document['current']
    if not record:
        raise host.Error('No committed node')
    if (state / 'pending.json').exists():
        raise host.Error('Local transaction pending; run recover before devices')
    request = request_for(document, record)
    if args.device_action == 'list':
        roster = remote(record, 'devices-list', request)
        for name, item in roster.items():
            print(name + (' enabled' if item['enabled'] else ' disabled'))
        return
    request.update(device_action=args.device_action, names=args.names,
                   operation_id=secrets.token_hex(16), adapter_sha256=host.digest(MODULE / 'lib/host.py'))
    host.device_request(request)  # names/shape before any remote backup/restart
    binary = resolve_mihomo(args.mihomo)
    checked = remote(record, 'status', request_for(document, record))
    if checked.get('adapter_sha256') not in (host.LEGACY_ADAPTER_SHA256, request['adapter_sha256']):
        raise host.Error('Unknown stored adapter digest; no device backup/service mutation')
    confirm('Device management restarts the owned core/front: ALL devices may briefly reconnect. '
            'Remove/rotate revoke old credentials; historical backups still contain them. Proceed?')
    before = backup_remote(state, document, record)
    pending = {'record': record, 'mode': 'devices', 'request': request, 'backup': None, 'before': str(before), 'mihomo': binary}
    host.atomic(state / 'pending.json', host.dumps(pending))
    try:
        with upload_bundle(request) as stream:
            prepared = remote(record, 'devices-prepare', stdin=stream)
        if not prepared.get('committed') and not prepared.get('unchanged'):
            candidate = backup_remote(state, document, record)
            pending['backup'] = str(candidate)
            host.atomic(state / 'pending.json', host.dumps(pending))
            validate_outputs(state, document, record, binary, candidate)
            remote(record, 'devices-commit', dict(request, offnode_sha256=host.digest(candidate)))
        # The post-publication snapshot, not the candidate backup, becomes local
        # authority. A failure here leaves pending intent for committed recovery.
        after = backup_remote(state, document, record)
        validate_outputs(state, document, record, binary, after)
        result = remote(record, 'status', request_for(document, record))
        remember_commit(state, document, record, after, result)
    except BaseException:
        print('Device operation incomplete or already committed: pending intent retained. Run recover; '
              'do not repeat rotate until reconciliation completes.', file=sys.stderr)
        raise
    export_after_commit(state)
    print('Device roster committed; latest off-node backup and protected URL export updated.')


def recover_local(state, document, mihomo=None):
    if not (state / 'pending.json').exists():
        if not document.get('current'):
            raise host.Error('No saved current node or pending transaction')
        host.atomic(state / 'pending.json', host.dumps({'record': document['current'], 'mode': 'backup'}))
    pending = load(state / 'pending.json')
    record = pending['record']
    request = pending['request'] if pending.get('mode') == 'devices' else request_for(document, record)
    result = remote(record, 'abort', request)
    if result.get('pending'):
        print('Recovery queued/waiting; local pending retained. Retry recover after the owned recovery unit exits.')
        return
    if result.get('committed'):
        status = result if pending.get('mode') == 'backup' and result.get('front_running') is False else remote(record, 'status', request_for(document, record))
        backup = backup_remote(state, document, record)
        if pending.get('mode') == 'devices':
            binary = resolve_mihomo(mihomo or pending.get('mihomo'))
            pending['mihomo'] = binary
            host.atomic(state / 'pending.json', host.dumps(pending))
            validate_outputs(state, document, record, binary=binary, backup=backup)
        remember_commit(state, document, record, backup, status)
        print('Remote commit had succeeded; reconciled ownership and made a new off-node backup.')
        export_after_commit(state)
    else:
        (state / 'pending.json').unlink()
        if pending.get('mode') == 'devices':
            print('Device operation did not commit; original device state restored or unchanged. Private rollback evidence retained.')
        else:
            print('Candidate stopped/disabled; private failed state retained on the target. Current/old host unchanged.')


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('deploy', 'replace', 'restore', 'preflight', 'status', 'backup', 'recover', 'export'):
        a = sub.add_parser(name)
        if name in ('deploy', 'replace', 'restore', 'preflight'):
            a.add_argument('--ssh', required=True, help='trusted SSH alias or user@public-IPv4; host key must already be verified')
            a.add_argument('--port', type=int, help='SSH port ONLY; otherwise use verified SSH config')
            a.add_argument('--proxy-port', type=int, help='first setup REALITY listener; default443, saved once')
            a.add_argument('--identity', type=Path)
            for flag in ('domain', 'reality-target'):
                a.add_argument('--' + flag)
            a.add_argument('--acme-email', help='optional explicit CA contact; default none, saved contact retained')
        if name in ('deploy', 'replace', 'restore'):
            a.add_argument('--backup', type=Path, help='existing private unencrypted recovery archive')
            a.add_argument('--mihomo', help='native core executable for profile validation')
        if name == 'recover':
            a.add_argument('--mihomo', help='override retained native core path for committed recovery')
        if name == 'restore':
            a.set_defaults(backup_required=True)
    a = sub.add_parser('devices', help='manage manifest-owned device credentials')
    operations = a.add_subparsers(dest='device_action', required=True)
    operations.add_parser('list', help='names/enabled status only, no credentials')
    for action in ('add', 'remove', 'rotate'):
        operation = operations.add_parser(action)
        operation.add_argument('names', nargs='+' if action == 'add' else 1)
        operation.add_argument('--mihomo', help='native core executable for format validation')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    os.umask(0o077)
    state = args.state_dir.expanduser().absolute()
    try:
        with locked(state):
            if args.command == 'restore' and not args.backup:
                raise host.Error('Controlled restore requires an explicit --backup and --ssh')
            if args.command == 'restore' and not (state / 'state.json').exists():
                manifest = inspect_backup(args.backup.expanduser(), release_lock())
                document = {'format': host.FORMAT, 'owner': manifest['owner'], 'config': manifest['config'],
                            'current': None, 'previous': [], 'backup': str(args.backup.expanduser().absolute())}
                configuration(args, document['config'])
            else:
                document = state_document(state, args, args.command in ('deploy', 'preflight'))
            if args.command in ('deploy', 'replace', 'restore'):
                deploy(state, document, args)
            elif args.command == 'devices':
                devices(state, document, args)
            elif args.command == 'preflight':
                record = target_record(args, document['current'])
                print(host.dumps(remote(record, 'preflight', request_for(document, record))), end='')
            elif args.command == 'recover':
                recover_local(state, document, args.mihomo)
            elif args.command == 'export':
                print('Private saved-snapshot subscription URLs: ' + str(export_urls(state)))
            else:
                record = document['current']
                if not record:
                    raise host.Error('No committed current node; use recover for pending work')
                if args.command == 'status':
                    print(host.dumps(remote(record, 'status', request_for(document, record))), end='')
                else:
                    path = backup_remote(state, document, record)
                    document['backup'] = str(path.absolute())
                    host.atomic(state / 'state.json', host.dumps(document))
                    print('Complete UNENCRYPTED off-node backup (0700/0600): ' + str(path))
        return 0
    except (host.Error, OSError, ValueError, EOFError) as exc:
        print('vps: ' + str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('vps: interrupted; inspect pending state with recover', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
