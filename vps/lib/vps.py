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
        else:
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
    try:
        with path.open('xb') as out:
            os.chmod(path, 0o600)
            remote(record, 'backup', request_for(document, record), stdout=out)
            out.flush()
            os.fsync(out.fileno())
        manifest = inspect_backup(path, release_lock())
        if manifest['owner'] != document['owner'] or manifest['endpoint'] != record['endpoint']:
            raise host.Error('Exported backup owner/endpoint mismatch')
        return path
    except BaseException:
        if path.exists():
            path.rename(path.with_suffix('.incomplete'))
        raise


def validate_outputs(state, document, record, binary=None, backup=None):
    binary = binary or shutil.which('verge-mihomo') or shutil.which('mihomo')
    if not binary:
        raise host.Error('Install native Mihomo or use --mihomo; no version whitelist or skipped format gate')
    outputs = remote(record, 'outputs', request_for(document, record))
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
            if name not in {d + ext for d in host.DEVICES for ext in ('.yaml', '.txt')}:
                raise host.Error('Unexpected native output filename')
            data = base64.b64decode(encoded, validate=True)
            host.atomic(output / name, data)
            if name.endswith('.yaml'):
                config = root / name
                host.atomic(config, data)
                host.run([binary, '-t', '-d', root, '-f', config], timeout=60)
    manifest = inspect_backup(backup, release_lock())
    urls = {device: {'mihomo': f"https://{document['config']['domain']}:8443/mihomo/{manifest['accounts'][device]['subId']}",
                     'shadowrocket': f"https://{document['config']['domain']}:8443/sub/{manifest['accounts'][device]['subId']}"}
            for device in host.DEVICES if manifest['accounts'][device]['enable']}
    host.atomic(output / 'subscription-urls.json', host.dumps(urls))


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
        print('Deployment committed. Private native profiles and subscription URLs: ' + str(state / 'outputs'))
        print('Off-node backup is UNENCRYPTED (0700/0600): ' + str(offnode))
        if replacing:
            print('Operator must update grey DNS to the new endpoint and REFRESH clients. Old node was not contacted or revoked; cold replacement has downtime.')
    except BaseException:
        print('Transaction incomplete. Local pending intent is retained; run recover. No SSH, firewall, DNS or PC network policy was changed.', file=sys.stderr)
        raise


def recover_local(state, document):
    pending = load(state / 'pending.json')
    record = pending['record']
    result = remote(record, 'abort', request_for(document, record))
    if result.get('committed'):
        status = remote(record, 'status', request_for(document, record))
        backup = backup_remote(state, document, record)
        remember_commit(state, document, record, backup, status)
        print('Remote commit had succeeded; reconciled ownership and made a new off-node backup.')
    else:
        (state / 'pending.json').unlink()
        print('Candidate stopped/disabled; private failed state retained on the target. Current/old host unchanged.')


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('deploy', 'replace', 'restore', 'preflight', 'status', 'backup', 'recover'):
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
        if name == 'restore':
            a.set_defaults(backup_required=True)
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
            elif args.command == 'preflight':
                record = target_record(args, document['current'])
                print(host.dumps(remote(record, 'preflight', request_for(document, record))), end='')
            elif args.command == 'recover':
                recover_local(state, document)
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
