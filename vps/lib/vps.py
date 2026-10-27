#!/usr/bin/env python3
"""Thin SSH lifecycle for one dedicated Ubuntu24.04 amd64 3x-ui node."""
from __future__ import annotations

import argparse
import base64
import contextlib
from contextvars import ContextVar
import fcntl
import hashlib
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
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler
import uuid
import zlib

import bootstrap
import host
import link_dashboard
import ssh_proxy

# Carrier selection never enters saved target records or operation fingerprints.
SSH_PROXY = ContextVar('ssh_proxy', default=None)
SSH_OPTIONS = ['-o', 'StrictHostKeyChecking=yes', '-o', 'BatchMode=yes', '-o', 'ControlMaster=no', '-o', 'ControlPath=none',
               '-o', 'ClearAllForwardings=yes', '-o', 'ProxyCommand=none', '-o', 'ProxyJump=none', '-o', 'ConnectTimeout=15']

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
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise host.Error('Missing regular private file; refusing links') from exc
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise host.Error('Private file requires regular current-owner mode0600')
        return source.read()


@contextlib.contextmanager
def directory_fd(path, create=False, private=True):
    """Pin every directory component without following links."""
    path = Path(path).expanduser().absolute()
    if '..' in path.parts:
        raise host.Error('Parent traversal is not a private output path')
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        info = os.fstat(fd)
        if private and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
            raise host.Error('Private directory requires current owner and mode0700')
        yield fd
    finally:
        os.close(fd)


def read_at(directory, name, limit=bootstrap.MAX_PROFILE_BYTES):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > limit):
            raise host.Error('Profile generation requires bounded regular current-owner mode0600 files')
        data = source.read(limit + 1)
        if len(data) > limit:
            raise host.Error('Profile file grew beyond the bounded size')
        return data


def write_at(directory, name, data):
    try:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise host.Error('Refusing unknown or nonprivate bootstrap output')
    temporary = '.profile-' + secrets.token_hex(12)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


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


def saved_ssh_options(record):
    """Read only the bounded legacy option vocabulary; never run saved commands."""
    options = record.get('options')
    if not isinstance(options, list) or len(options) > 24 or len(options) % 2:
        raise host.Error('Invalid saved SSH options')
    retained, seen = [], set()
    for flag, value in zip(options[::2], options[1::2]):
        if not isinstance(value, str) or not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise host.Error('Invalid saved SSH option value')
        if flag == '-o' and value in SSH_OPTIONS[1::2]:
            key = value.split('=')[0]
        elif flag == '-o' and value == 'IdentitiesOnly=yes':
            key = 'IdentitiesOnly'
            retained += [flag, value]
        elif flag == '-i':
            key = flag
            retained += [flag, value]
        elif flag == '-p' and re.fullmatch(r'[0-9]{1,5}', value) and int(value) == record['port']:
            key = flag
            retained += [flag, value]
        else:
            raise host.Error('Unsupported saved SSH option; arbitrary proxy commands are never adopted')
        if key in seen:
            raise host.Error('Duplicate saved SSH option')
        seen.add(key)
    return retained


def host_key_alias(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:@\[\]-]{0,252}', value):
        raise host.Error('Unsupported SSH HostKeyAlias')
    return value


def ssh_effective(argv):
    result = host.run(['ssh', '-G', *argv], timeout=15)
    effective = {}
    for line in result.stdout.decode().splitlines():
        key, _, value = line.partition(' ')
        effective.setdefault(key, value)
    return effective


def transport_options(record):
    retained = saved_ssh_options(record)
    options = list(SSH_OPTIONS)
    proxy = SSH_PROXY.get()
    if proxy is not None:
        try:
            generated = ssh_proxy.proxy_command(proxy, record['endpoint'], record['port'])
        except ssh_proxy.Error as exc:
            raise host.Error(str(exc)) from exc
        # OpenSSH uses the FIRST value. Filter only our exact legacy none;
        # persisted arbitrary commands were rejected above, not silently removed.
        options = ['-o', 'ProxyCommand=' + generated] + [
            token for pair in zip(options[::2], options[1::2]) if pair != ('-o', 'ProxyCommand=none') for token in pair]
    return options + retained


def target_record(args, current=None):
    target = getattr(args, 'ssh', None)
    if not target:
        if not current:
            raise host.Error('Supply --ssh for this target')
        if getattr(args, 'port', None) is None and getattr(args, 'identity', None) is None:
            return current
        target = current['target']
    if not re.fullmatch(r'(?:[A-Za-z_][A-Za-z0-9_-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*', target):
        raise host.Error('--ssh is user@host or a verified alias, never a shell command')
    options = list(SSH_OPTIONS)
    same_target = current is not None and target == current['target']
    port_override = getattr(args, 'port', None)
    identity = getattr(args, 'identity', None)
    if same_target:
        if port_override is None:
            port_override = current['port']
        saved = saved_ssh_options(current)
        if identity is None and '-i' in saved:
            identity = Path(saved[saved.index('-i') + 1])
    if port_override is not None:
        if not 1 <= port_override <= 65535:
            raise host.Error('Invalid SSH port')
        options += ['-p', str(port_override)]
    if identity is not None:
        options += ['-i', str(identity.expanduser().absolute()), '-o', 'IdentitiesOnly=yes']
    # Resolve aliases through OpenSSH, never parse/read private SSH config here.
    effective = ssh_effective([*options, target])
    endpoint = host.public_ip(effective.get('hostname', ''))
    user, port = effective.get('user', ''), int(effective.get('port', '0'))
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*', user) or not 1 <= port <= 65535:
        raise host.Error('Cannot resolve SSH user/port')
    alias = host_key_alias(effective.get('hostkeyalias'))
    record = {'target': target, 'options': options, 'endpoint': endpoint, 'user': user, 'port': port, 'host_key_alias': alias}
    if current and endpoint == current['endpoint']:
        if alias != host_key_alias(current.get('host_key_alias')):
            raise host.Error('SSH HostKeyAlias changed or was absent from legacy state; explicit trust review required')
        if user != current['user'] or port != current['port']:
            raise host.Error('SSH identity/port change requires explicit review, not implicit reassignment')
        record.update({key: current[key] for key in ('machine_id',) if key in current})
    return record


def remote_command(record, action):
    # Pack PUBLIC adapter bytes only. Linux limits each argv string to roughly
    # 128KiB even when total ARG_MAX is larger. Private payloads stay on stdin.
    source = (MODULE / 'lib/host.py').read_bytes()
    packed = base64.b64encode(zlib.compress(source, 9)).decode('ascii')
    bootstrap = 'import base64,zlib;exec(compile(zlib.decompress(base64.b64decode(' + repr(packed) + ')),"<string>","exec"))'
    prefix = '' if record['user'] == 'root' else 'sudo -n '
    command = prefix + 'env QD_SSH_CONNECTION="$SSH_CONNECTION" python3 -c ' + shlex.quote(bootstrap) + ' ' + shlex.quote(action)
    if len(command.encode()) >= 120 * 1024:
        raise host.Error('Compressed public adapter exceeds SSH single-argument limit; no private input sent')
    return command


def remote(record, action, request=None, stdin=None, stdout=None):
    command = remote_command(record, action)
    endpoint = host.public_ip(record['endpoint'])
    if (not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*', record['user']) or type(record['port']) is not int
            or not 1 <= record['port'] <= 65535 or not re.fullmatch(r'(?:[A-Za-z_][A-Za-z0-9_-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*', record['target'])):
        raise host.Error('Invalid saved SSH transport identity')
    # Resolve once, then bind the transport itself BEFORE any private stdin is
    # released. Alias matching/normal host-key checking remain in OpenSSH.
    pinned = ['-o', 'HostName=' + endpoint, '-o', 'User=' + record['user'], '-o', 'Port=' + str(record['port'])]
    alias = host_key_alias(record.get('host_key_alias'))
    if alias is not None:
        pinned += ['-o', 'HostKeyAlias=' + alias]
    # Missing legacy metadata means normal endpoint/port lookup, not permission
    # to adopt a new alias from mutable SSH config. -G never connects or sends stdin.
    checked = ssh_effective([*pinned, *SSH_OPTIONS, *saved_ssh_options(record), record['target']])
    if host_key_alias(checked.get('hostkeyalias')) != alias:
        raise host.Error('SSH HostKeyAlias changed; no private input sent')
    argv = ['ssh', '-T', *pinned, *transport_options(record), record['target'], command]
    kwargs = {'check': False, 'stderr': subprocess.PIPE}
    if stdin is not None:
        kwargs['stdin'] = stdin
    else:
        kwargs['input'] = host.dumps(request or {}).encode()
    kwargs['shell'] = False
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
    result['ssh_port'] = record['port']
    if record.get('machine_id'):
        result['machine_id'] = record['machine_id']
    return result


def release_lock():
    return json.loads((MODULE / 'release-lock.json').read_text())


def import_artifacts(folder, source, lock):
    # Hold only the exact three public files open. Validate the complete set
    # before copying anything into the durable cache; never import a directory.
    with directory_fd(source, private=False) as directory, contextlib.ExitStack() as stack:
        sources = []
        for component in ('panel', 'core', 'front'):
            pin = lock[component]
            if Path(pin['file']).name != pin['file']:
                raise host.Error('Invalid pinned artifact filename')
            fd = os.open(pin['file'], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            stream = stack.enter_context(os.fdopen(fd, 'rb'))
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= host.MAX_ARCHIVE:
                raise host.Error('Artifact source requires a bounded ordinary file, never a symlink')
            checksum = hashlib.sha256()
            total = 0
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > host.MAX_ARCHIVE:
                    raise host.Error('Oversized artifact source; cache unchanged')
                checksum.update(chunk)
            if checksum.hexdigest() != pin['sha256']:
                raise host.Error('Verified artifact directory SHA256 mismatch; cache unchanged')
            stream.seek(0)
            sources.append((pin, stream))
        with tempfile.TemporaryDirectory(prefix='.verified-', dir=folder) as tmp:
            stage = Path(tmp)
            for pin, stream in sources:
                path = stage / pin['file']
                with path.open('xb') as out:
                    os.chmod(path, 0o600)
                    total, checksum = 0, hashlib.sha256()
                    while chunk := stream.read(1024 * 1024):
                        total += len(chunk)
                        if total > host.MAX_ARCHIVE:
                            raise host.Error('Artifact source changed size; cache unchanged')
                        checksum.update(chunk)
                        out.write(chunk)
                    out.flush()
                    os.fsync(out.fileno())
                if checksum.hexdigest() != pin['sha256']:
                    raise host.Error('Artifact source changed after validation; cache unchanged')
            for pin, _ in sources:
                destination = folder / pin['file']
                if destination.exists() or destination.is_symlink():
                    read_private(destination)
            for pin, _ in sources:
                os.replace(stage / pin['file'], folder / pin['file'])
            directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)


def artifacts(state, lock, source=None):
    folder = private_dir(state / 'artifacts')
    if source is not None:
        import_artifacts(folder, source, lock)
    # A dead proxy selected in the management PC must not be required to fetch
    # new-node releases. This changes no environment/client settings.
    opener = build_opener(ProxyHandler({}))
    for component in ('panel', 'core', 'front'):
        pin = lock[component]
        path = folder / pin['file']
        if path.exists() or path.is_symlink():
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
            except HTTPError as exc:
                raise host.Error(f'Pinned {component} artifact unavailable (HTTP{exc.code}); use deploy --artifacts-dir with a verified original release set. No version or mirror fallback') from exc
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
        elif 'device_action' not in request and request.get('mode') != 'transport-upgrade':
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


def checked_backup(document, record, backup):
    manifest = inspect_backup(Path(backup), release_lock())
    if (manifest['owner'] != document['owner'] or manifest['config'] != document['config']
            or manifest['endpoint'] != record['endpoint']):
        raise host.Error('Saved backup does not match committed owner/config/endpoint')
    return manifest


def profile_names(manifest):
    return {name + ext for name in host.device_names(manifest) if manifest['accounts'][name]['enable']
            for ext in ('.yaml', '.txt')}


def generation_binding(manifest, digest):
    return {'format': 1, 'backup_sha256': digest, 'owner': manifest['owner'], 'node_id': manifest['node_id'],
            'endpoint': manifest['endpoint'], 'config': manifest['config'],
            'roster': {name: manifest['accounts'][name]['enable'] for name in host.device_names(manifest)},
            'accounts_sha256': hashlib.sha256(json.dumps(manifest['accounts'], sort_keys=True).encode()).hexdigest()}


def read_generation(state, manifest, digest):
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise host.Error('Invalid committed native generation identity')
    with directory_fd(state / 'generations' / digest) as directory:
        metadata = json.loads(read_at(directory, 'metadata.json'))
        if not isinstance(metadata, dict) or not isinstance(metadata.get('files'), dict):
            raise host.Error('Invalid native generation metadata')
        binding = generation_binding(manifest, digest)
        if {key: value for key, value in metadata.items() if key != 'files'} != binding:
            raise host.Error('Native generation does not match verified committed backup')
        expected = profile_names(manifest)
        if set(metadata.get('files', {})) != expected or set(os.listdir(directory)) != expected | {'metadata.json'}:
            raise host.Error('Native generation roster/files changed')
        files = {}
        for name in expected:
            data = read_at(directory, name)
            if hashlib.sha256(data).hexdigest() != metadata['files'][name]:
                raise host.Error('Native generation file hash changed')
            host.validate_feed_scope(manifest, name.rsplit('.', 1)[0], data, 'sub' if name.endswith('.txt') else 'mihomo')
            files[name] = data
        return files


def store_generation(state, manifest, digest, files):
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest) or set(files) != profile_names(manifest):
        raise host.Error('Invalid native generation identity or roster')
    parent = state / 'generations'
    with directory_fd(parent, create=True) as directory:
        try:
            os.mkdir(digest, 0o700, dir_fd=directory)
            os.fsync(directory)
        except FileExistsError:
            if read_generation(state, manifest, digest) != files:
                raise host.Error('Immutable native generation already exists with different bytes')
            return
    # A partial generation is preserved and refused, never overwritten on retry.
    with directory_fd(parent / digest) as directory:
        for name, data in files.items():
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            with os.fdopen(fd, 'wb') as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
        metadata = dict(generation_binding(manifest, digest), files={name: hashlib.sha256(data).hexdigest() for name, data in files.items()})
        write_at(directory, 'metadata.json', host.dumps(metadata).encode())
        os.fsync(directory)


def validate_outputs(state, document, record, binary=None, backup=None):
    binary = resolve_mihomo(binary)
    manifest = checked_backup(document, record, backup)
    digest = host.digest(backup)
    outputs = remote(record, 'outputs', request_for(document, record))
    if not isinstance(outputs, dict) or set(outputs) != profile_names(manifest):
        raise host.Error('Native outputs disagree with verified backup roster')
    files = {}
    with tempfile.TemporaryDirectory(prefix='qd-native-check-') as tmp:
        root = Path(tmp)
        with tarfile.open(backup) as archive:
            for name in ('geoip.dat', 'geosite.dat'):
                data = archive.extractfile('deployment/bin/' + name).read()
                host.atomic(root / name, data)
        for name, encoded in outputs.items():
            if not isinstance(encoded, str) or len(encoded) > 2 * bootstrap.MAX_PROFILE_BYTES:
                raise host.Error('Oversized native output')
            data = base64.b64decode(encoded, validate=True)
            if len(data) > bootstrap.MAX_PROFILE_BYTES:
                raise host.Error('Oversized native output')
            host.validate_feed_scope(manifest, name.rsplit('.', 1)[0], data, 'sub' if name.endswith('.txt') else 'mihomo')
            if name.endswith('.yaml'):
                config = root / name
                host.atomic(config, data)
                host.run([binary, '-t', '-d', root, '-f', config], timeout=60)
            files[name] = data
    if host.digest(backup) != digest:
        raise host.Error('Verified backup changed during native validation')
    store_generation(state, manifest, digest, files)
    return digest


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


def committed_dashboard_source(state):
    if (state / 'pending.json').exists() or (state / 'pending.json').is_symlink():
        raise host.Error('Pending transaction is ambiguous; use recover before dashboard/bootstrap export')
    document = load(state / 'state.json')
    if document.get('format') != host.FORMAT or not document.get('current') or not document.get('backup'):
        raise host.Error('Bootstrap requires saved committed state')
    manifest = checked_backup(document, document['current'], document['backup'])
    digest = host.digest(document['backup'])
    if document.get('native_generation') != digest or not manifest.get('published'):
        raise host.Error('No committed native generation; capture with deploy or backup through a working SSH carrier first')
    if not host.dual_enabled(manifest):
        raise host.Error('Offline dual bootstrap requires a committed dual-transport snapshot')
    return manifest, digest, read_generation(state, manifest, digest)


def dashboard_render(manifest, digest, files, device=None, extra_downloads=None):
    assets = {name: (MODULE / 'assets' / name).read_bytes()
              for name in ('dashboard.html', 'dashboard.css', 'dashboard.js')}
    return link_dashboard.render(manifest, files, subscription_urls(manifest), digest, assets,
                                 device, extra_downloads)


def publish_dashboard(destination, index, assets, selected=None):
    """Immutable downloads + index-last publication; prune only a bound inventory."""
    destination = destination.expanduser().absolute()
    if any((parent / '.git').exists() or (parent / '.git').is_symlink()
           for parent in (destination, *destination.parents)):
        raise host.Error('Dashboard/bootstrap output must be outside Git worktrees')
    selected = selected or {}
    current = {name: hashlib.sha256(data).hexdigest() for name, data in dict(assets, **selected).items()}
    # Validate our own intended names with the same closed vocabulary used on disk.
    link_dashboard.inventory(link_dashboard.inventory_bytes(current))
    with directory_fd(destination, create=True) as directory:
        try:
            fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise host.Error('Another export owns this dashboard directory') from None
        names = set(os.listdir(directory))
        if any(name.endswith('.management.yaml') and name not in selected for name in names):
            raise host.Error('Existing management runtime requires explicit refresh or manual cleanup; no files changed')
        previous = (link_dashboard.inventory(read_at(directory, link_dashboard.INVENTORY,
                    link_dashboard.MAX_INVENTORY_BYTES)) if link_dashboard.INVENTORY in names else {})
        if 'index.html' in names and link_dashboard.INVENTORY not in names:
            raise host.Error('Refusing an unowned dashboard index; no files changed')
        # Every touched old/new path is checked before the first credential write.
        for name in set(previous) | set(current) | set(selected) | {'index.html'}:
            if name not in names:
                continue
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise host.Error('Refusing unknown or nonprivate dashboard/bootstrap output')
            if name in previous:
                # Legacy bootstrap paths are intentionally mutable; an interrupted
                # update may leave either generation there. The page never uses them.
                if (link_dashboard.ASSET_NAME.fullmatch(name) and
                        hashlib.sha256(read_at(directory, name)).hexdigest() != previous[name]):
                    raise host.Error('Managed dashboard asset changed; no files changed')
            elif name in assets:
                raise host.Error('Refusing an unowned dashboard asset; no files changed')
        # A durable union also owns partial assets after an interrupted publication.
        write_at(directory, link_dashboard.INVENTORY,
                 link_dashboard.inventory_bytes(dict(previous, **current)))
        for name, data in assets.items():
            if name not in names:
                write_at(directory, name, data)
        for name, data in selected.items():
            write_at(directory, name, data)
        write_at(directory, 'index.html', index)
        for name in set(previous) - set(current):
            try:
                os.unlink(name, dir_fd=directory)
            except FileNotFoundError:
                pass
        os.fsync(directory)
        write_at(directory, link_dashboard.INVENTORY, link_dashboard.inventory_bytes(current))
    return destination


def export_dashboard(state, args):
    manifest, digest, files = committed_dashboard_source(state)
    _, index, assets = dashboard_render(manifest, digest, files, args.device)
    destination = publish_dashboard(args.output_dir or state / 'dashboard', index, assets)
    print('Private offline dashboard snapshot: ' + str(destination / 'index.html'))
    return destination


def export_bootstrap(state, args):
    manifest, digest, files = committed_dashboard_source(state)
    if args.device not in host.device_names(manifest) or not manifest['accounts'][args.device]['enable']:
        raise host.Error('Bootstrap device is removed, disabled or unknown')
    device = args.device
    selected = link_dashboard.native_material(device, files, subscription_urls(manifest))
    management = getattr(args, 'management_interface', None)
    port = 10808 if args.management_port is None else args.management_port
    binary = None
    management_name = device + '.management.yaml'
    destination = args.bootstrap_dir.expanduser().absolute()
    # An old runtime may authenticate with revoked keys. Never silently keep it
    # or guess that a legacy unbound file is ours to delete.
    if destination.exists():
        with directory_fd(destination) as directory:
            names = set(os.listdir(directory))
            legacy_unknown = ('management.yaml' in names and
                (device != 'management' or read_at(directory, 'management.yaml') != files[device + '.yaml']))
            if legacy_unknown or (management is None and any(name.endswith('.management.yaml') for name in names)):
                raise host.Error('Existing management runtime requires explicit refresh or manual cleanup; no files changed')
    if management is not None:
        try:
            selected[management_name] = bootstrap.render_management(files[device + '.yaml'], endpoint=manifest['endpoint'],
                proxy_port=manifest['config']['proxy_port'], ssh_port=manifest['management']['ssh_port'],
                domain=manifest['config']['domain'], interface=management, listen_port=port)
        except bootstrap.BootstrapError as exc:
            raise host.Error(str(exc)) from exc
        binary = resolve_mihomo(args.mihomo)
        with tempfile.TemporaryDirectory(prefix='qd-management-check-') as tmp:
            path = Path(tmp) / management_name
            host.atomic(path, selected[management_name])
            host.run([binary, '-t', '-d', tmp, '-f', path], timeout=60)
    _, index, assets = dashboard_render(manifest, digest, files, device, {device: selected})
    destination = publish_dashboard(destination, index, assets, selected)
    for name in (*selected, 'index.html'):
        print(str(destination / name))
    if management is not None:
        print(shlex.join([binary, '-d', str(destination), '-f', str(destination / management_name)]))
        print(shlex.join(['bash', str(MODULE / 'setup.sh'), '--state-dir', str(state), 'status', '--ssh-proxy', f'socks5://127.0.0.1:{port}']))
    return destination


def export_after_commit(state):
    try:
        path = export_urls(state)
    except (host.Error, OSError, ValueError) as exc:
        raise host.Error('Deployment already committed; local subscription export failed. '
                         'Fix the local destination/Git exclusion and retry with setup.sh export; no redeploy needed') from exc
    print('Private subscription URL copy: ' + str(path))


def retire_renamed_outputs(state, request):
    if request.get('device_action') != 'rename':
        return
    host.device_request(request)
    directory = os.open(state / 'outputs', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for extension in ('.yaml', '.txt'):
            name = request['names'][0] + extension
            try:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise host.Error('Rename already committed; refusing unknown old profile output. Preserve it and retry recover')
            os.unlink(name, dir_fd=directory)
        os.fsync(directory)
    finally:
        os.close(directory)


def sync_outputs(state, document, files=None, manifest=None):
    """Only committed bytes enter outputs; prune only the old verified roster."""
    output = state / 'outputs'
    if files is None and not output.exists() and not output.is_symlink():
        return
    old_names = set()
    if document.get('backup') and document.get('current'):
        previous = checked_backup(document, document['current'], document['backup'])
        old_names = {name + ext for name in host.device_names(previous) for ext in ('.yaml', '.txt')} | {'subscription-urls.json'}
    selected = {} if files is None else dict(files, **{'subscription-urls.json': host.dumps(subscription_urls(manifest)).encode()})
    with directory_fd(output, create=files is not None) as directory:
        for name in old_names | set(selected):
            try:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise host.Error('Commit accepted; refusing unknown convenience output. Preserve it and retry recover')
        for name, data in selected.items():
            write_at(directory, name, data)
        for name in old_names - set(selected):
            try:
                os.unlink(name, dir_fd=directory)
            except FileNotFoundError:
                pass
        os.fsync(directory)


def remember_commit(state, document, record, backup, result, use_generation=True):
    previous = document['current']
    if previous and previous['endpoint'] != record['endpoint'] and previous not in document['previous']:
        document['previous'].append(previous)
    record = dict(record, machine_id=result['machine_id'])
    digest = host.digest(backup)
    files = manifest = None
    if use_generation and ((state / 'generations' / digest).exists() or (state / 'generations' / digest).is_symlink()):
        manifest = checked_backup(document, record, backup)
        if result.get('node_id', manifest['node_id']) != manifest['node_id'] or not manifest.get('published'):
            raise host.Error('Commit result does not match published native snapshot')
        files = read_generation(state, manifest, digest)
    try:
        sync_outputs(state, document, files, manifest)
    except (host.Error, OSError, ValueError, tarfile.TarError):
        if use_generation:
            raise
        # Backup-only failure is reported by the caller. Preserve unknown files,
        # but never keep their offline authority or strand the verified backup.
    if files is not None:
        document['native_generation'] = digest
    else:
        document.pop('native_generation', None)
    document.update(current=record, backup=str(backup.absolute()))
    host.atomic(state / 'state.json', host.dumps(document))
    if (state / 'pending.json').exists():
        pending = load(state / 'pending.json')
        retire_renamed_outputs(state, pending.get('request', {}))
        (state / 'pending.json').unlink()


def backup_committed(state, document, record, backup, mihomo=None):
    # A feed/core failure may invalidate an offline snapshot, never a verified
    # published backup. Bind it to the CURRENT node, not an uncommitted candidate.
    current = document.get('current')
    if not current or any(current.get(key) != record.get(key) for key in ('endpoint', 'machine_id', 'port')):
        raise host.Error('Backup does not match committed current identity')
    old = checked_backup(document, current, document['backup'])
    manifest = checked_backup(document, record, backup)
    if not manifest.get('published') or manifest['node_id'] != old['node_id']:
        raise host.Error('Backup is not the published committed current node')
    result = {'machine_id': record['machine_id'], 'node_id': manifest['node_id']}
    try:
        validate_outputs(state, document, record, mihomo, backup)
        remember_commit(state, document, record, backup, result)
    except (host.Error, OSError, ValueError, tarfile.TarError):
        remember_commit(state, document, record, backup, result, use_generation=False)
        raise host.Error('Backup committed; offline snapshot not captured. Latest offline outputs invalidated; retry capture through a working carrier/core') from None


def deploy_existing(state, document, record, args):
    request = request_for(document, record)
    checked = remote(record, 'status', request)
    adapter = host.digest(MODULE / 'lib/host.py')
    if not host.accepted_adapter_digest(checked.get('adapter_sha256'), adapter):
        raise host.Error('Unknown stored adapter digest; no transport backup/service mutation')
    upgrading = checked.get('needs_transport_upgrade')
    expected = ['tcp-reality'] if upgrading is True else ['tcp-reality', 'hysteria2']
    if type(upgrading) is not bool or checked.get('transports') != expected:
        raise host.Error('Unrecognized owned transport topology; no implicit migration')
    binary = resolve_mihomo(args.mihomo)
    if upgrading and getattr(args, 'artifacts_dir', None) is not None:
        artifacts(state, release_lock(), args.artifacts_dir)
    if not upgrading:
        # Retain prepare's native projection/Caddy allowlist reconciliation.
        # Its unchanged branch never creates Auth or restarts the native core.
        request.update(lock=release_lock(), mode='deploy', adapter_sha256=adapter)
        folder = artifacts(state, request['lock'], getattr(args, 'artifacts_dir', None))
        with upload_bundle(request, folder=folder) as stream:
            prepared = remote(record, 'prepare', stdin=stream)
        if not prepared.get('unchanged') or prepared.get('needs_transport_upgrade'):
            raise host.Error('Expected unchanged owned dual node; use recover before retry')
        backup = backup_remote(state, document, record)
        validate_outputs(state, document, record, binary, backup)
        checked = remote(record, 'status', request_for(document, record))
        remember_commit(state, document, record, backup, checked)
        export_after_commit(state)
        print('Owned dual transport unchanged; committed native snapshot refreshed.')
        return
    before = backup_remote(state, document, record)
    manifest = checked_backup(document, record, before)
    if host.dual_enabled(manifest) or manifest['node_id'] != checked.get('node_id'):
        raise host.Error('Verified pre-change archive differs from legacy status')
    request.update(mode='transport-upgrade', operation_id=secrets.token_hex(16), adapter_sha256=adapter,
                   before_sha256=host.digest(before), source_transports=['tcp-reality'],
                   target_transports=['tcp-reality', 'hysteria2'])
    host.transport_request(request)
    pending = {'record': record, 'mode': 'transport-upgrade', 'request': request, 'before': str(before.absolute()),
               'backup': None, 'mihomo': binary}
    host.atomic(state / 'pending.json', host.dumps(pending))
    try:
        with upload_bundle(request) as stream:
            prepared = remote(record, 'transport-prepare', stdin=stream)
        if prepared.get('pending') or prepared.get('queued'):
            print('Transport preparation waiting; pending retained. Use recover with the same target.')
            return
        if not prepared.get('committed') and not prepared.get('unchanged'):
            candidate = backup_remote(state, document, record)
            pending['backup'] = str(candidate.absolute())
            host.atomic(state / 'pending.json', host.dumps(pending))
            validate_outputs(state, document, record, binary, candidate)
            remote(record, 'transport-commit', dict(request, offnode_sha256=host.digest(candidate)))
        after = backup_remote(state, document, record)
        validate_outputs(state, document, record, binary, after)
        result = remote(record, 'status', request_for(document, record))
        remember_commit(state, document, record, after, result)
    except BaseException:
        print('Transport upgrade incomplete or already committed; pending intent retained. Use recover, never replay credentials.', file=sys.stderr)
        raise
    export_after_commit(state)
    print('Native TCP/REALITY + UDP/Hysteria2 upgrade committed; original account identities retained.')


def deploy(state, document, args):
    lock = release_lock()
    if (state / 'pending.json').exists():
        raise host.Error('Local pending transaction exists; use recover before deploy/replace')
    record = target_record(args, document['current'])
    replacing = args.command in ('replace', 'restore')
    if args.command == 'deploy' and document['current'] and record['endpoint'] != document['current']['endpoint']:
        raise host.Error('Different host: use replace; never silently take over')
    if args.command == 'deploy' and document['current']:
        return deploy_existing(state, document, record, args)
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
    print(f"Preflight passed. This dedicated node will run TCP/REALITY + UDP/Hysteria2 on{document['config']['proxy_port']}, TLS subscriptions8443 and HTTP01 on80. SSH/firewall/Tailscale stay unchanged.")
    folder = None if backup else artifacts(state, lock, getattr(args, 'artifacts_dir', None))
    pending = {'record': record, 'mode': args.command, 'backup': None, 'mihomo': resolve_mihomo(args.mihomo)}
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
            print('Native core/full Mihomo profiles and scoped URI feeds validated; trusted TLS and a complete unencrypted OFF-NODE backup exist. Publishing validated device feed paths. Real phone/desktop connectivity remains a separate user test.')
            result = remote(record, 'commit', dict(request_for(document, record), offnode_sha256=host.digest(offnode)))
        if not prepared['unchanged']:
            offnode = backup_remote(state, document, record)
            pending['backup'] = str(offnode.absolute())
            host.atomic(state / 'pending.json', host.dumps(pending))
            validate_outputs(state, document, record, pending['mihomo'], offnode)
            result = remote(record, 'status', request_for(document, record))
        remember_commit(state, document, record, offnode, result)
    except BaseException:
        print('Transaction incomplete. Local pending intent is retained; run recover. No SSH, firewall, DNS or PC network policy was changed.', file=sys.stderr)
        raise
    print('Deployment committed. Export an enabled device with export --device NAME --bootstrap-dir PRIVATE_DIR.')
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
    status_request = request_for(document, record)
    if request['device_action'] == 'rename':
        status_request['rename_target'] = request['names'][1]
    checked = remote(record, 'status', status_request)
    if not host.accepted_adapter_digest(checked.get('adapter_sha256'), request['adapter_sha256']):
        raise host.Error('Unknown stored adapter digest; no device backup/service mutation')
    if request['device_action'] == 'rename':
        if not isinstance(checked.get('devices'), list):
            raise host.Error('Missing owned roster in read-only status')
        host.validate_rename_names(request['names'], checked['devices'])
        if checked.get('rename_target_has_history') is not False:
            raise host.Error('Rename target history is occupied or unverified; no changes made')
    print('Device management restarts the owned core/front: ALL devices may briefly reconnect. '
          'Remove/reset revoke old credentials; rename preserves keys/URLs. Historical backups contain old keys.')
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
              'do not repeat reset/rename until reconciliation completes.', file=sys.stderr)
        raise
    export_after_commit(state)
    print('Device roster committed; latest off-node backup and protected URL export updated.')


def recover_local(state, document, mihomo=None):
    if not (state / 'pending.json').exists():
        if not document.get('current'):
            raise host.Error('No saved current node or pending transaction')
        host.atomic(state / 'pending.json', host.dumps({'record': document['current'], 'mode': 'backup', 'mihomo': mihomo}))
    pending = load(state / 'pending.json')
    record = pending['record']
    mode = pending.get('mode')
    request = pending['request'] if mode in ('devices', 'transport-upgrade') else request_for(document, record)
    if mode == 'devices':
        host.device_request(request)  # Unknown/removed actions are preserved, never translated or replayed.
    elif mode == 'transport-upgrade':
        host.transport_request(request)
    result = remote(record, 'abort', request)
    if result.get('pending') or result.get('queued'):
        print('Recovery queued/waiting; local pending retained. Retry recover after the owned recovery unit exits.')
        return
    if result.get('committed'):
        # A standalone backup preserves an intentionally inactive front; its
        # verified DB/artifact snapshot does not require publishing feeds. The
        # timer may already have completed and removed the remote stop intent.
        backup_only = mode == 'backup' or (mode not in ('devices', 'transport-upgrade') and not pending.get('request')
            and document.get('current') is not None and record == document['current'])
        if backup_only:
            backup = backup_remote(state, document, record)
            backup_committed(state, document, record, backup, mihomo or pending.get('mihomo'))
            print('Verified committed backup reconciled; offline snapshot captured.')
            export_after_commit(state)
            return
        status = remote(record, 'status', request_for(document, record))
        backup = backup_remote(state, document, record)
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
        elif pending.get('mode') == 'transport-upgrade':
            print('Transport upgrade did not commit; original native transport state restored or unchanged. Private rollback evidence retained.')
        else:
            print('Candidate stopped/disabled; private failed state retained on the target. Current/old host unchanged.')


def proxy_argument(value):
    try:
        return ssh_proxy.parse_proxy(value)
    except ssh_proxy.Error as exc:
        # argparse's generic ValueError formatting would echo a rejected URI,
        # including any credentials the caller mistakenly supplied.
        raise argparse.ArgumentTypeError(str(exc)) from exc


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('deploy', 'replace', 'restore', 'preflight', 'status', 'backup', 'recover', 'export', 'dashboard'):
        a = sub.add_parser(name)
        if name not in ('export', 'dashboard'):
            a.add_argument('--ssh-proxy', type=proxy_argument,
                           help='invocation-only socks5://127.0.0.1:PORT or http://127.0.0.1:PORT; default direct')
        if name in ('deploy', 'replace', 'restore', 'preflight'):
            a.add_argument('--ssh', required=name in ('replace', 'restore'), help='trusted SSH alias or user@public-IPv4; first setup requires it, otherwise use saved current')
            a.add_argument('--port', type=int, help='SSH port ONLY; otherwise use verified SSH config')
            a.add_argument('--proxy-port', type=int, help='first setup REALITY listener; default443, saved once')
            a.add_argument('--identity', type=Path)
            for flag in ('domain', 'reality-target'):
                a.add_argument('--' + flag)
            a.add_argument('--acme-email', help='optional explicit CA contact; default none, saved contact retained')
        if name in ('deploy', 'replace', 'restore'):
            a.add_argument('--backup', type=Path, help='existing private unencrypted recovery archive')
            a.add_argument('--mihomo', help='native core executable for profile validation')
        if name in ('recover', 'backup', 'export'):
            a.add_argument('--mihomo', help='native core path for committed profile validation')
        if name == 'deploy':
            a.add_argument('--artifacts-dir', type=Path, help='explicit directory containing all three original SHA-pinned release files')
        if name == 'export':
            a.add_argument('--device', help='enabled committed device name for private offline bootstrap')
            a.add_argument('--bootstrap-dir', type=Path, help='private output directory outside Git; pair with --device')
            a.add_argument('--management-interface', help='explicit physical Linux interface; render isolated H2 management profile')
            a.add_argument('--management-port', type=int, help='loopback management listener port; default10808 with --management-interface')
        if name == 'dashboard':
            a.add_argument('--output-dir', type=Path, help='private static output outside Git; default STATE/dashboard')
            a.add_argument('--device', help='show only this enabled committed device')
        if name == 'restore':
            a.set_defaults(backup_required=True)
    a = sub.add_parser('devices', help='manage manifest-owned device credentials')
    operations = a.add_subparsers(dest='device_action', required=True)
    listing = operations.add_parser('list', help='names/enabled status only, no credentials')
    listing.add_argument('--ssh-proxy', type=proxy_argument, help='explicit loopback SSH carrier; default direct')
    for action in ('add', 'remove', 'rename', 'reset'):
        operation = operations.add_parser(action,
            help=('reset UUID and subscription key' if action == 'reset' else
                  'rename OLD NEW without changing keys/URLs' if action == 'rename' else None))
        operation.add_argument('names', nargs='+' if action == 'add' else (2 if action == 'rename' else 1),
            metavar='NAME')
        operation.add_argument('--mihomo', help='native core executable for format validation')
        operation.add_argument('--ssh-proxy', type=proxy_argument, help='explicit loopback SSH carrier; default direct')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    carrier = SSH_PROXY.set(getattr(args, 'ssh_proxy', None))
    os.umask(0o077)
    state = args.state_dir.expanduser().absolute()
    try:
        with locked(state):
            if args.command == 'export':
                if bool(args.device) != bool(args.bootstrap_dir):
                    raise host.Error('--device and --bootstrap-dir must be supplied together')
                if (args.management_interface is not None or args.management_port is not None) and not args.device:
                    raise host.Error('Management options require --device and --bootstrap-dir')
                if args.management_port is not None and (not args.management_interface or not 1 <= args.management_port <= 65535):
                    raise host.Error('--management-port requires --management-interface and port1..65535')
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
            elif args.command == 'dashboard':
                export_dashboard(state, args)
            elif args.command == 'export':
                if args.device:
                    export_bootstrap(state, args)
                else:
                    print('Private saved-snapshot subscription URLs: ' + str(export_urls(state)))
            else:
                record = document['current']
                if not record:
                    raise host.Error('No committed current node; use recover for pending work')
                if args.command == 'status':
                    print(host.dumps(remote(record, 'status', request_for(document, record))), end='')
                else:
                    if (state / 'pending.json').exists():
                        raise host.Error('Local transaction pending; use recover before backup')
                    host.atomic(state / 'pending.json', host.dumps({'record': record, 'mode': 'backup', 'mihomo': args.mihomo}))
                    path = backup_remote(state, document, record)
                    backup_committed(state, document, record, path, args.mihomo)
                    print('Complete UNENCRYPTED off-node backup and native snapshot (0700/0600): ' + str(path))
        return 0
    except (host.Error, OSError, ValueError, EOFError, tarfile.TarError) as exc:
        print('vps: ' + str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('vps: interrupted; inspect pending state with recover', file=sys.stderr)
        return 130
    finally:
        SSH_PROXY.reset(carrier)


if __name__ == '__main__':
    sys.exit(main())
