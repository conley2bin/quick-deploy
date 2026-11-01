"""Server-side READ-ONLY half of `connect`: observe the current managed VPS.

Executed by the client wrapper in a namespace separate from the UNCHANGED public
host.py (loaded as a non-main module `h`). It never calls export_backup,
install_backup_adapter, adapter_source, ctl or atomic() on anything under
ROOT/UNITS; the only writes are a private scratch tree outside both.

pull produces the STANDARD archive (same shape export_backup makes) from the
live tree WITHOUT stopping the front. Consistency is optimistic: owned material
and the credential/policy/host-group snapshot are compared before and after
collection and the copied TLS pairs are re-verified; any observed change is a
clean refusal (never an outage).
"""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import ssl
import stat
import tempfile

LOCK_PATH = Path('/run/lock/qd-3x-ui.lock')
SKIP_NAMES = ('logs', 'x-ui.db')
SKIP_SUFFIXES = ('.db-wal', '.db-shm')
# Native traffic/liveness counters move on every connection; nothing else may.
COUNTERS = frozenset({'up', 'down', 'all_time', 'last_online', 'allTime', 'lastOnline'})
# Same legacy/foreign proxy locations host.preflight refuses on a fresh server.
LEGACY_PATHS = ('/var/lib/quick-deploy-vps', '/etc/x-ui', '/usr/local/x-ui', '/opt/3x-ui', '/etc/sing-box/config.json')
# In-flight operation markers under ROOT: (relative path, failed_check).
INTENTS = (('pending.json', 'pending'), ('backup-stop.json', 'backup_stop'), ('device-rollback/intent.json', 'device_intent'))
SECRET_TEXT = re.compile(r'[A-Za-z][A-Za-z0-9+.-]*://\S+|[A-Za-z0-9_-]{16,}')
TOP_FILES = ('manifest.json', 'release-lock.json', 'binary-hashes.json', 'Caddyfile')


@contextlib.contextmanager
def locked(h):
    """Serial-operation mutex, nonblocking. /run is volatile, so a fresh boot may
    lack the file: creating this coordination file is not a config/account/
    service write, and absence says nothing about whether a host is managed."""
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_NOFOLLOW | os.O_RDWR, 0o600)
    except OSError as exc:
        raise h.Error('Cannot open the managed host lock') from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise h.Error('Unsafe managed host lock file')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise h.Error('Managed host is busy with another operation; retry later') from exc
        yield
    finally:
        os.close(fd)


def file_sha(h, path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise h.Error('Unreadable owned file') from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise h.Error('Non-regular owned file')
        digest = hashlib.sha256()
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            for block in iter(lambda: stream.read(1 << 20), b''):
                digest.update(block)
        return digest.hexdigest()
    finally:
        os.close(fd)


def skipped(name):
    return name in SKIP_NAMES or name.endswith(SKIP_SUFFIXES)


def tree_state(h, root):
    """relpath -> sha256 for every regular file; links/specials are refused."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise h.Error('Invalid owned directory')
    result = {}
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = sorted(n for n in names if not skipped(n))
        for name in names:
            if not stat.S_ISDIR((Path(directory) / name).lstat().st_mode):
                raise h.Error('Link/special directory in owned tree')
        for name in sorted(files):
            if not skipped(name):
                path = Path(directory) / name
                result[str(path.relative_to(root))] = file_sha(h, path)
    return result


def adapters(h):
    """Installed adapter bytes plus pinned retained predecessors, read only."""
    installed = h.regular_adapter_bytes(h.ROOT / 'host.py')
    return installed, {name: h.read_previous_adapter(pin) for pin, name in h.ADAPTER_PREDECESSORS.items()}


def material(h):
    """Everything outside the DB that the archive must carry unchanged."""
    root, active = h.ROOT, h.ROOT / 'active'
    installed, previous = adapters(h)
    return {'owner': file_sha(h, root / 'owner.json'), 'adapter': hashlib.sha256(installed).hexdigest(),
            'previous': {n: hashlib.sha256(d).hexdigest() for n, d in previous.items() if d is not None},
            'units': {n: file_sha(h, h.UNITS / n) for n in h.UNIT_NAMES},
            'top': {n: file_sha(h, active / n) for n in TOP_FILES},
            'tree': tree_state(h, active / 'caddy')}


def db_state(h, manifest, database):
    """Credential, policy and host-group state; traffic counters excluded."""
    result = {'accounts': h.snapshot_accounts(database, manifest), 'group': h.snapshot_host_group(database, manifest)}
    with contextlib.closing(sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True)) as db:
        for table in ('clients', 'inbounds', 'client_inbounds', 'hosts'):
            columns = [c[1] for c in db.execute('pragma table_info(' + table + ')') if c[1] not in COUNTERS]
            if columns:
                result[table] = sorted(map(repr, db.execute('select ' + ','.join(columns) + ' from ' + table)))
    return result


def ssh_port(h, request):
    parts = os.environ.get('QD_SSH_CONNECTION', os.environ.get('SSH_CONNECTION', '')).split()
    if len(parts) != 4 or parts[2] != h.public_ip(request.get('endpoint')):
        raise h.Error('Wrong SSH endpoint for private operation')
    return int(parts[3])


def observe(h, request):
    """Validate the live deployment against the request; returns (marker, manifest).

    Runs under the lock. Read-only; refuses pending/backup-stop/unpublished.
    """
    root, active = h.ROOT, h.ROOT / 'active'
    port = ssh_port(h, request)
    if root.is_symlink() or not root.is_dir():
        raise h.Error('No managed deployment on this host')
    if (root / 'pending.json').exists():
        raise h.Error('Pending transaction on host: recover before connecting')
    if (root / 'backup-stop.json').exists():
        raise h.Error('Interrupted backup stop on host: recover before connecting')
    marker = h.read_json(root / 'owner.json')
    if marker.get('format') != h.FORMAT or marker.get('machine_id') != h.machine_id():
        raise h.Error('Host owner marker does not match this machine')
    h.reject_symlinks(active)
    manifest = h.read_json(active / 'manifest.json')
    if manifest.get('format') != h.FORMAT or marker.get('owner') != manifest.get('owner'):
        raise h.Error('Host owner marker disagrees with active manifest')
    if manifest['endpoint'] != h.public_ip(request['endpoint']):
        raise h.Error('Requested endpoint differs from deployment endpoint')
    h.validate_config(manifest['config'])
    if 'management' in manifest and manifest['management'] != {'ssh_port': port}:
        raise h.Error('Management SSH port differs from owned deployment')
    if not manifest['published']:
        raise h.Error('Deployment is not published; nothing stable to connect to')
    actual = {'owner': marker['owner'], 'machine_id': marker['machine_id'], 'node_id': manifest['node_id'],
              'config': manifest['config']}
    lock = h.read_json(active / 'release-lock.json')
    actual['lock'] = lock
    for key, value in actual.items():
        if request.get(key) is not None and request[key] != value:
            raise h.Error('Host ' + key + ' differs from the request')
    h.verify_artifacts(active / 'artifacts', lock)
    h.binary_health(active)
    for name, text in h.unit_texts().items():
        path = h.UNITS / name
        if path.is_symlink() or path.read_text() != text:
            raise h.Error('Foreign or modified owned unit; manual review required')
    return marker, manifest


def connect_info(h, request):
    os.umask(0o077)
    with locked(h):
        marker, manifest = observe(h, request)
        h.snapshot_accounts(h.ROOT / 'active/db/x-ui.db', manifest)
    return {'format': h.FORMAT, 'owner': marker['owner'], 'machine_id': marker['machine_id'],
            'node_id': manifest['node_id'], 'endpoint': manifest['endpoint'],
            'config': manifest['config'], 'published': True}


def verify_tls(h, deployment, manifest):
    """Copied pairs must be present, consistent and loadable, per typed path."""
    pairs = {h.hysteria_certificate_paths(deployment, manifest)}
    if h.cdn_enabled(manifest):
        pairs.add(h.cdn_certificate_paths(deployment, manifest))
    for cert, key in pairs:
        try:
            ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(str(cert), str(key))
        except (ssl.SSLError, OSError) as exc:
            raise h.Error('Copied TLS certificate/private key pair is unusable') from exc


def scratch_dir(h):
    path = Path(tempfile.mkdtemp(prefix='qd-connect-')).resolve()
    for owned in (h.ROOT.resolve(), h.UNITS.resolve()):
        if path == owned or owned in path.parents:
            shutil.rmtree(path, ignore_errors=True)
            raise h.Error('Refusing scratch inside owned storage')
    return path


def build_archive(h, request, scratch):
    """Collect, verify and return the path of a standard archive in scratch."""
    root, active = h.ROOT, h.ROOT / 'active'
    marker, manifest = observe(h, request)
    database = active / 'db/x-ui.db'
    installed, previous = adapters(h)
    if request.get('adapter_sha256') is not None and not h.accepted_adapter_digest(
            hashlib.sha256(installed).hexdigest(), request['adapter_sha256']):
        raise h.Error('Unknown stored backup adapter')
    before = material(h)
    first = db_state(h, manifest, database)
    stage = scratch / 'stage'
    stage.mkdir(mode=0o700)
    copied = stage / 'deployment'
    shutil.copytree(active, copied, symlinks=True,
                    ignore=shutil.ignore_patterns(*SKIP_NAMES, *('*' + s for s in SKIP_SUFFIXES)))
    h.reject_symlinks(copied)
    metadata = h.db_snapshot(database, copied / 'db/x-ui.db')
    if tree_state(h, copied / 'caddy') != before['tree'] or any(
            file_sha(h, copied / n) != before['top'][n] for n in TOP_FILES):
        raise h.Error('Owned material changed while copying; retry')
    # Accounts and host group come from the closed copy: the live DB, not the
    # manifest cache, owns names (a module-native rename updates the manifest).
    manifest['accounts'] = h.snapshot_accounts(copied / 'db/x-ui.db', manifest)
    manifest['host_group_id'] = h.snapshot_host_group(copied / 'db/x-ui.db', manifest)
    h.atomic(copied / 'manifest.json', h.dumps(manifest))
    verify_tls(h, copied, manifest)
    metadata.update(format=h.FORMAT, encryption='none', panel_version=h.read_json(active / 'release-lock.json')['panel']['version'])
    h.atomic(stage / 'database-schema.sql', metadata.pop('schema'))
    h.atomic(stage / 'backup.json', h.dumps(metadata))
    for name in h.UNIT_NAMES:
        h.atomic(stage / 'units' / name, (h.UNITS / name).read_bytes())
    h.atomic(stage / 'host.py', installed)
    for name, data in previous.items():
        if data is not None:
            h.atomic(stage / name, data)
    # Re-observe: identity, owned material and native state equal the start.
    observe(h, request)
    second = db_state(h, manifest, database)
    if material(h) != before or second != first or manifest['accounts'] != first['accounts']:
        raise h.Error('Managed state changed during collection; retry')
    archive = scratch / 'backup.tar'
    h.archive_tree(stage, archive)
    shutil.rmtree(stage)
    # The standard acceptance check, on the exact bytes that will be streamed.
    check = scratch / 'verify'
    check.mkdir(mode=0o700)
    with archive.open('rb') as stream:
        h.safe_unpack(stream, check)
    h.verify_backup(check, h.read_json(active / 'release-lock.json'))
    shutil.rmtree(check)
    return archive


def connect_pull(h, request, stdout):
    os.umask(0o077)
    for key in ('owner', 'machine_id', 'node_id', 'config', 'endpoint', 'lock'):
        if request.get(key) is None:
            raise h.Error('Pull request missing ' + key)
    scratch = scratch_dir(h)
    try:
        with locked(h):
            archive = build_archive(h, request, scratch)
        # Verified; nothing was written to stdout before this point.
        with archive.open('rb') as stream:
            shutil.copyfileobj(stream, stdout)
        stdout.flush()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def roster_digest(accounts):
    """Shared by the host probe and the client: credentials never leave the host."""
    canonical = json.dumps(accounts, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def safe_text(error):
    """Host messages are curated, but native/API causes can quote ids, auth or feed URIs."""
    return SECRET_TEXT.sub('<redacted>', str(error))[:240]


def present(path):
    try:
        os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    return True


class Classified(Exception):
    def __init__(self, kind, check, error, **facts):
        super().__init__(error)
        self.kind, self.check, self.error, self.facts = kind, check, error, facts


def probe_reply(kind, check=None, error=None, **facts):
    reply = {'kind': kind, 'owner': None, 'machine_id': None, 'node_id': None, 'config': None, 'transports': [],
             'tcp_cdn': None, 'versions': None, 'lock_match': False, 'adapter_sha256': None,
             'health': {'ok': check is None, 'failed_check': check, 'error': error},
             'front_drift': False, 'roster_digest': None}
    reply.update(facts)
    return reply


def pending_intent(h, **facts):
    for name, check in INTENTS:
        if present(h.ROOT / name):
            raise Classified('pending', check, 'Operation in flight or interrupted on host (' + name + ')', **facts)


def absent_check(h):
    """Genuinely undeployed means no owned root, legacy tree, unit name or listener on a required port."""
    for path in LEGACY_PATHS:
        if present(path):
            raise Classified('foreign', 'legacy_path', 'Legacy/foreign proxy state detected at ' + path)
    for name in h.UNIT_NAMES:
        if present(h.UNITS / name):
            raise Classified('foreign', 'unit_conflict', 'Foreign unit name present: ' + name)
    ports = {str(p) for p in h.required_ports({'proxy_port': h.DEFAULT_PROXY_PORT})}
    for flag in ('-ltn', '-lun'):
        for line in h.run(['ss', '-H', flag], timeout=10).stdout.decode().splitlines():
            columns = line.split()
            if len(columns) < 4:
                raise h.Error('Cannot parse listener inventory')
            if columns[3].rsplit(':', 1)[-1] in ports:
                raise Classified('foreign', 'listener', 'Required port ' + columns[3].rsplit(':', 1)[-1] + ' is already in use')


def classify(h, request):
    """Under the lock. Read-only; the only owned-storage reads are marker/manifest/health."""
    root, active = h.ROOT, h.ROOT / 'active'
    port = ssh_port(h, request)
    if root.is_symlink():
        raise Classified('foreign', 'root_link', 'Owned root is a symlink')
    if not present(root):
        absent_check(h)
        return probe_reply('absent')
    if not root.is_dir():
        raise Classified('foreign', 'root_not_directory', 'State root is not a directory')
    if not present(root / 'owner.json'):
        raise Classified('foreign', 'unmarked_root', 'Unmarked state tree; never adopted implicitly')
    try:
        marker = h.read_json(root / 'owner.json')
    except h.Error as exc:
        raise Classified('foreign', 'marker', 'Owner marker unreadable') from exc
    if (not isinstance(marker, dict) or set(marker) != {'format', 'owner', 'machine_id'} or marker['format'] != h.FORMAT or
            not isinstance(marker['owner'], str) or not isinstance(marker['machine_id'], str)):
        raise Classified('foreign', 'marker', 'Owner marker is not a recognized quick-deploy marker')
    facts = {'owner': marker['owner'], 'machine_id': marker['machine_id']}
    if marker['machine_id'] != h.machine_id():
        raise Classified('foreign', 'machine', 'Owner marker belongs to a different machine', **facts)
    pending_intent(h, **facts)
    if not present(active):
        return probe_reply('owned_empty', **facts)
    if not present(active / 'manifest.json'):
        raise Classified('foreign', 'active_tree', 'Active tree has no manifest', **facts)
    try:
        h.reject_symlinks(active)
        manifest = h.read_json(active / 'manifest.json')
        if manifest.get('format') != h.FORMAT or marker['owner'] != manifest.get('owner'):
            raise Classified('foreign', 'owner_mismatch', 'Host owner marker disagrees with active manifest', **facts)
        if manifest['endpoint'] != h.public_ip(request['endpoint']):
            raise Classified('foreign', 'endpoint', 'Requested endpoint differs from deployment endpoint', **facts)
        h.validate_config(manifest['config'])
        if 'management' in manifest and manifest['management'] != {'ssh_port': port}:
            raise Classified('foreign', 'management_port', 'Management SSH port differs from owned deployment', **facts)
        facts.update(node_id=manifest['node_id'], config=manifest['config'], transports=h.transports(manifest),
                     tcp_cdn={'schema': 1, 'domain': manifest['tcp_cdn']['domain']} if h.cdn_enabled(manifest) else None)
        h.device_names(manifest)
    except h.Error as exc:
        raise Classified('foreign', 'manifest', safe_text(exc), **facts) from exc
    try:
        installed = h.regular_adapter_bytes(root / 'host.py')
    except (OSError, h.Error) as exc:
        raise Classified('foreign', 'adapter', 'Recovery adapter unreadable or not a regular file', **facts) from exc
    installed_sha = hashlib.sha256(installed).hexdigest()
    facts['adapter_sha256'] = installed_sha
    if request.get('adapter_sha256') is not None and not h.accepted_adapter_digest(installed_sha, request['adapter_sha256']):
        raise Classified('foreign', 'adapter', 'Unsupported installed adapter', **facts)
    if not manifest['published']:
        raise Classified('pending', 'unpublished', 'Deployment is not published', **facts)
    return owned_health(h, request, active, manifest, facts)


def owned_health(h, request, active, manifest, facts):
    """Run the existing host checks in order; the first expected failure names the failed stage."""
    state = {}

    def release():
        lock = h.read_json(active / 'release-lock.json')
        try:
            facts['versions'] = {k: lock[k]['version'] for k in ('panel', 'core', 'front')}
        except (KeyError, TypeError) as exc:
            raise h.Error('Malformed installed release lock') from exc
        facts['lock_match'] = request.get('lock') is None or lock == request['lock']
        state['lock'] = lock
        if not facts['lock_match']:
            raise h.Error('Installed release differs from the pinned release; explicit upgrade required')

    def units():
        for name, text in h.unit_texts().items():
            path = h.UNITS / name
            if path.is_symlink() or not path.is_file() or path.read_text() != text:
                raise h.Error('Foreign, missing or modified owned unit; manual review required')

    def native():
        h.backend_health(active, manifest)  # feeds are validated and dropped, never returned
        state['native'] = True

    stages = (('release', release), ('artifacts', lambda: h.verify_artifacts(active / 'artifacts', state['lock'])),
              ('binary', lambda: h.binary_health(active)), ('units', units), ('native', native),
              ('tls', lambda: h.tls_probe(manifest)), ('process', h.process_health))
    failed = error = None
    for name, stage in stages:
        try:
            stage()
        except (h.Error, OSError, sqlite3.Error) as exc:
            # Expected runtime failures: host.Error plus raw TLS (ssl.SSLError is an
            # OSError), socket, /proc and SQLite errors. Malformed data stays a crash.
            failed = name
            error = safe_text(exc) if isinstance(exc, h.Error) else type(exc).__name__ + ': ' + safe_text(getattr(exc, 'strerror', None) or exc.args)
            break
    accounts = manifest.get('accounts') if state.get('native') else None
    if accounts is None:
        try:
            accounts = h.snapshot_accounts(active / 'db/x-ui.db', manifest)
        except (h.Error, sqlite3.Error, OSError):
            accounts = None
    drift = False
    if state.get('native'):
        try:
            drift = (active / 'Caddyfile').read_text() != h.caddyfile(manifest, manifest['published'])
        except (OSError, UnicodeDecodeError):
            if failed is None:
                failed, error = 'front', 'Active Caddyfile unreadable'
    return probe_reply('owned', failed, error, front_drift=drift,
                       roster_digest=None if accounts is None else roster_digest(accounts), **facts)


def ensure_probe(h, request):
    """Read-only classification of the host for `setup.sh`; see the probe wire contract."""
    os.umask(0o077)
    stack = contextlib.ExitStack()
    try:
        try:
            stack.enter_context(locked(h))
        except h.Error as exc:
            if not isinstance(exc.__cause__, BlockingIOError):
                raise
            try:
                pending_intent(h)
            except Classified as busy:
                return probe_reply('pending', busy.check, busy.error)
            return probe_reply('pending', 'busy', 'Managed host is busy with another operation')
        with stack:
            return classify(h, request)
    except Classified as result:
        return probe_reply(result.kind, result.check, result.error, **result.facts)
    finally:
        stack.close()


def main(h, action, stdin, stdout):
    request = json.load(stdin)
    if action == 'connect-info':
        stdout.write(h.dumps(connect_info(h, request)).encode())
    elif action == 'connect-pull':
        connect_pull(h, request, stdout)
    elif action == 'ensure-probe':
        stdout.write(h.dumps(ensure_probe(h, request)).encode())
    else:
        raise h.Error('Unknown connect operation')
