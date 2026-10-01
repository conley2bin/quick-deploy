#!/usr/bin/env python3
"""User-owned companion publication; no vendor update, restart or enrollment.

The shell entry owns common.sh release download/verification. This module owns
read-only qualification/discovery and create-only version/active publications.
All deployment I/O is explicit; imports do not inspect the host or HOME.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, replace
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import sys

# -I excludes the caller's cwd/PYTHONPATH, including the script directory.
# Only the explicit local/validated installed source directory is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import configuration as cfg
import host
import lifecycle as lc
from protocol import MAX_INPUT, Refusal, request, response, unique_json
import qualification as q
import reservation as rs
import runtime_linux as rt

RELEASE = 1
RELATIVE_ROOT = Path(".local/share/quick-deploy/display-control")
SHIM_PATH = Path("/usr/bin/quick-deploy-display-control")
SOURCES = ("host.py", "lifecycle.py", "configuration.py", "protocol.py", "reservation.py",
           "runtime_linux.py", "linux_kms.py", "windows_display.py", "macos_display.py",
           "qualification.py", "install_controller.py")
MARKER = "# quick-deploy display-control fixed dispatcher v1"

# Root-owned but NOT setuid. NSS/EUID, not HOME/USER/XDG, chooses the one private
# installation. Only no-argument RPC and deliberate --enroll are dispatchable.
SHIM = '''#!/usr/bin/python3 -I
# quick-deploy display-control fixed dispatcher v1
import hashlib, json, os, pathlib, pwd, re, stat, sys

def refuse():
    sys.stdout.write('{"version":1,"ok":false,"error":{"code":"controller_publication_unavailable","detail":"owned controller publication unavailable; run the local installer"}}\\n')
    raise SystemExit(1)

def read(path, uid, limit=1048576):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > limit):
            refuse()
        data = os.read(fd, limit + 1)
        after = path.lstat()
        stamp = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if len(data) != info.st_size or stamp(info) != stamp(after) or stamp(info) != stamp(os.fstat(fd)):
            refuse()
        return data
    finally:
        os.close(fd)

def directory(path, uid):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
        refuse()
    return info.st_dev, info.st_ino

def unique(data):
    def pairs(items):
        obj = {}
        for k, v in items:
            if k in obj: refuse()
            obj[k] = v
        return obj
    return json.loads(data, object_pairs_hook=pairs)

def dispatch():
    uid = os.geteuid()
    if uid == 0 or os.getuid() != uid or sys.argv[1:] not in ([], ['--enroll']):
        refuse()  # BEFORE NSS resolution or executing any user code.
    home = pathlib.Path(pwd.getpwuid(uid).pw_dir)
    if not home.is_absolute(): refuse()
    root = home.resolve(strict=True) / '.local/share/quick-deploy/display-control'
    anchors = {path: directory(path, uid) for path in (root, root/'active', root/'versions', root/'state')}
    records = sorted((root/'active').iterdir())
    if not 1 <= len(records) <= 512: refuse()
    active = None
    for serial, path in enumerate(records, 1):
        if path.name != f'{serial:08d}.json': refuse()
        obj = unique(read(path, uid, 1024))
        if (type(obj) is not dict or set(obj) != {'schema','version'} or type(obj['schema']) is not int
                or obj['schema'] != 1 or type(obj['version']) is not str
                or not re.fullmatch(r'v[1-9][0-9]*-[0-9a-f]{64}', obj['version'])): refuse()
        active = obj['version']
    version = root/'versions'/active
    anchors[version] = directory(version, uid)
    manifest = unique(read(version/'.owned.json', uid, 8192))
    names = {'host.py','lifecycle.py','configuration.py','protocol.py','reservation.py',
             'runtime_linux.py','linux_kms.py','windows_display.py','macos_display.py',
             'qualification.py','install_controller.py','host-settings.json'}
    if (type(manifest) is not dict or set(manifest) != {'schema','release','version','files'}
            or type(manifest['schema']) is not int or manifest['schema'] != 1
            or type(manifest['release']) is not int or manifest['release'] < 1
            or manifest['version'] != active or type(manifest['files']) is not dict
            or set(manifest['files']) != names
            or {p.name for p in version.iterdir()} != names | {'.owned.json'}): refuse()
    for name, digest in manifest['files'].items():
        if type(digest) is not str or not re.fullmatch('[0-9a-f]{64}', digest): refuse()
        if hashlib.sha256(read(version/name, uid)).hexdigest() != digest: refuse()
    settings = unique(read(version/'host-settings.json', uid, 8192))
    if (settings.get('runtime_uid') != uid or settings.get('state_directory') != str(root/'state')
            or settings.get('platform') != 'linux'): refuse()
    if any(directory(path, uid) != identity for path, identity in anchors.items()): refuse()
    # -I ignores PYTHONDONTWRITEBYTECODE. Explicit -B keeps immutable versions
    # free of interpreter-created cache entries without relaxing their manifest.
    os.execv('/usr/bin/python3', ['/usr/bin/python3', '-I', '-B', str(version/'install_controller.py'),
                                'enroll' if sys.argv[1:] else 'rpc'])

try:
    dispatch()
except (OSError, ValueError, TypeError, KeyError, RecursionError):
    refuse()
'''.encode("ascii")


def fail(code, detail):
    raise Refusal(code, detail)


def stamp(value):
    return cfg.stable_identity(value)


def private_directory(path):
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        fail("publication_unsafe", "controller directories must be owned, private and not symlinks")
    return info.st_dev, info.st_ino


def private_read(path, limit=1024 * 1024):
    data, info = lc.read_owned(path, limit)
    if stat.S_IMODE(info.st_mode) != 0o600:
        fail("publication_unsafe", "controller files must be mode0600")
    return data


def source_read(path, limit=1024 * 1024):
    # Operator-invoked checkout input, NOT installed runtime authority. Git
    # checkouts commonly have group-writable source; published copies are0600.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.geteuid())
                or info.st_nlink != 1 or info.st_mode & 0o002 or info.st_size > limit):
            fail("publication_unsafe", "installer source must be owned, regular, unlinked and bounded")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
        if len(data) != info.st_size or stamp(info) != stamp(path.lstat()) or stamp(info) != stamp(os.fstat(fd)):
            fail("publication_race", "installer source changed during snapshot")
        return data
    finally:
        os.close(fd)


def create(path, data, mode=0o600):
    """Atomic create-only regular publication; remove hardlink alias before sync."""
    parent = path.parent
    anchor = parent.lstat()
    fd_dir = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temp = ".install-" + os.urandom(12).hex()
    try:
        if (anchor.st_dev, anchor.st_ino) != (os.fstat(fd_dir).st_dev, os.fstat(fd_dir).st_ino):
            fail("publication_race", "publication directory changed")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=fd_dir)
        try:
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), mode)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if (parent.lstat().st_dev, parent.lstat().st_ino) != (anchor.st_dev, anchor.st_ino):
                fail("publication_race", "publication parent was replaced")
            os.link(temp, path.name, src_dir_fd=fd_dir, dst_dir_fd=fd_dir, follow_symlinks=False)
            os.unlink(temp, dir_fd=fd_dir)
            os.fsync(fd_dir)
        finally:
            try:
                os.unlink(temp, dir_fd=fd_dir)
            except FileNotFoundError:
                pass
    except FileExistsError as exc:
        fail("publication_race", "new publication target appeared; it was not overwritten")
    finally:
        os.close(fd_dir)


def make_directory(path):
    try:
        path.mkdir(mode=0o700)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except FileExistsError:
        fail("publication_race", "directory publication collided; existing target retained")
    private_directory(path)


def document(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n"


@contextmanager
def installation_lock(root):
    before = private_directory(root)
    path = root / ".install.lock"
    if not path.exists() and not path.is_symlink():
        create(path, b"\0")
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        linked = path.lstat()
        private_read(path, 1)
        if stamp(linked) != stamp(os.fstat(fd)) or linked.st_size != 1:
            fail("publication_unsafe", "permanent installation lock is invalid")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail("installation_contended", "another installer holds the permanent publication lock")
        if private_directory(root) != before:
            fail("publication_race", "private controller root changed")
        yield
        if private_directory(root) != before or stamp(path.lstat()) != stamp(os.fstat(fd)):
            fail("publication_race", "private root/installation lock changed")
    finally:
        os.close(fd)


def active(root):
    records = sorted((root / "active").iterdir())
    if len(records) > 512:
        fail("publication_unsafe", "active reference history exceeds its bound")
    result = None
    for serial, path in enumerate(records, 1):
        if path.name != f"{serial:08d}.json":
            fail("publication_unsafe", "active references are incomplete or foreign")
        obj = unique_json(private_read(path, 1024))
        if (type(obj) is not dict or set(obj) != {"schema", "version"} or type(obj["schema"]) is not int
                or obj["schema"] != 1 or type(obj["version"]) is not str
                or not re.fullmatch(r"v[1-9][0-9]*-[0-9a-f]{64}", obj["version"])):
            fail("publication_unsafe", "active reference is invalid")
        result = obj["version"]
        validate_version(root / "versions" / result)
    return result, len(records)


def validate_version(path, expected=None):
    private_directory(path)
    obj = unique_json(private_read(path / ".owned.json", 8192))
    if (type(obj) is not dict or set(obj) != {"schema", "release", "version", "files"}
            or type(obj["schema"]) is not int or obj["schema"] != 1
            or type(obj["release"]) is not int or not 1 <= obj["release"] <= RELEASE
            or obj["version"] != path.name or type(obj["files"]) is not dict
            or set(obj["files"]) != set(SOURCES) | {"host-settings.json"}
            or {p.name for p in path.iterdir()} != set(obj["files"]) | {".owned.json"}):
        fail("publication_unsafe", "controller version is foreign, incomplete or newer than this installer")
    for name, digest in obj["files"].items():
        if (type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or cfg.digest(private_read(path / name)) != digest):
            fail("publication_unsafe", "published controller code/settings differs from its ownership manifest")
    if expected is not None and obj != expected:
        fail("publication_conflict", "version target already contains another controller publication")
    host.settings(path / "host-settings.json", "linux")
    return obj


def discover(ref, home, uid, transport, info):
    """Accepted Linux resolver/unit/process policy; no receipt or service mutation."""
    seed = rt.Settings("00000000-0000-0000-0000-000000000000", uid, home,
                       home / ".config/sunshine/sunshine.conf", rt.SupportedBuild(ref.executable_sha256))
    runtime = rt.LinuxRuntime(seed, transport)
    env = rt._environment(runtime._run(("/usr/bin/systemctl", "--user", "show-environment")).decode("utf-8"), "\n")
    manager_home = Path(env.get("HOME") or home)
    if not manager_home.is_absolute() or transport.realpath(manager_home) != transport.realpath(home):
        fail("runtime_unconfigured", "user manager HOME belongs to a different runtime; no install-as-user model exists")
    root = runtime._resolve(env)
    xdg = Path(env.get("XDG_CONFIG_HOME") or manager_home / ".config")
    guard = xdg / "systemd/user" / (rt.UNIT + ".d") / "check-tailnet.py"
    guard_hash = None
    try:
        guard_data, guard_fp = runtime._file(guard, rt.MAX_CONFIG, owner=uid)
    except FileNotFoundError:
        pass
    else:
        # A guard hash is qualification only when it matches this audited source.
        source = source_read(Path(__file__).resolve().parent.parent / "service/check-tailnet.py", rt.MAX_CONFIG)
        if guard_data != source:
            fail("unit_unowned", "retry guard differs from audited common.sh service source")
        guard_hash = guard_fp.sha256
    runtime = rt.LinuxRuntime(replace(seed, config_file=root / "sunshine.conf", retry_guard_sha256=guard_hash), transport)
    try:
        current = runtime.observe()
    except Refusal as exc:
        if exc.code == "unit_unowned":
            fail("unit_unowned", "loaded vendor/retry policy must be owned and NeedDaemonReload=no; companion publishes no units and never reloads/restarts to bypass this refusal")
        raise
    qualification = q.qualify(ref, current, transport)
    endpoint = info(current.config.bind_address, current.config.base_port)
    if type(endpoint) is not lc.Endpoint:
        fail("endpoint_unknown", "public pinned serverinfo returned invalid facts")
    host_id = lc.canonical_uuid(endpoint.host_uniqueid)
    content, identity = cfg.load(root / "sunshine.conf")
    if cfg.digest(content) != current.config.file.sha256 or rt.FileIdentity.from_stat(identity) != current.config.file.identity:
        fail("config_changed", "configuration changed during qualification")
    controls = cfg.entries(content, keys=cfg.MANAGED | {b"file_apps"})
    raw_apps = controls.get(b"file_apps", (b"apps.json",))[0].decode("utf-8", "strict")
    apps = Path(raw_apps)
    apps = apps if apps.is_absolute() else root / apps
    if ".." in apps.parts or any(ord(c) < 32 for c in str(apps)):
        fail("desktop_unqualified", "native apps path is not a supported literal absolute/relative path")
    installed = lc.InstalledSettings(host_id, current.config.base_port, home / RELATIVE_ROOT / "state",
                                    apps, q.ASSETS, replace(runtime.settings, host_uniqueid=host_id), qualification)
    desktop = lc.inspected_desktop(installed, content, root)
    # Qualification is stable across the endpoint/apps sampling, not just at its start.
    after = runtime.observe()
    if lc.authority(current) != lc.authority(after):
        fail("runtime_changed", "runtime generation/config/unit/binary changed during installation")
    q.qualify(ref, after, transport)
    return installed, desktop


def settings_document(installed):
    runtime = installed.runtime
    return {"schema": 2, "platform": "linux", "config_file": str(runtime.config_file),
            "apps_file": str(installed.apps_file), "assets_directory": str(installed.assets_directory),
            "host_uniqueid": installed.host_uniqueid, "base_port": installed.base_port,
            "state_directory": str(installed.state_directory), "runtime_home": str(runtime.home),
            "runtime_uid": runtime.uid, "unit": runtime.unit, "retry_guard_sha256": runtime.retry_guard_sha256,
            "build": {"executable_sha256": runtime.build.executable_sha256, "source_commit": runtime.build.source_commit,
                      "provenance": installed.qualification}}


def validate_state(installed, desktop, files):
    """Read existing schemas WITHOUT status(), which could create/migrate locks."""
    names = set(os.listdir(installed.state_directory))
    for name in names:
        private_read(installed.state_directory / name)
    known = {"operation.lock", "reservation.lock", "reservation.json", "operation.json", "start-receipt.json", "prepare-backup.conf"}
    if names - known:
        # Preserve the host coordinator's real prepare-backup.conf and private
        # backups, but not ambiguous interrupted temporary publications.
        if any(not name.endswith(".bak") for name in names - known):
            fail("operation_corrupt", "state contains incomplete/unknown publications; trusted repair required")
    if private_read(installed.state_directory / "operation.lock", 1) != b"":
        fail("operation_corrupt", "operation lock content is invalid")
    if private_read(installed.state_directory / "reservation.lock", 1) != b"\0":
        fail("reservation_corrupt", "reservation lock content is invalid")
    controller = lc.HostController(installed, None, None, None, None)
    controller.files = files
    meta = controller.meta()
    data = files.read("reservation.json", rs.MAX_STATE)
    receipt = files.read("start-receipt.json")
    if data is None:
        if meta != {"schema": 1, "enrollment": None, "lease": None} or receipt is not None:
            fail("operation_corrupt", "state lacks its reservation baseline; enrollment was not reset")
        return "UNENROLLED"
    state = rs._decode(data, controller.host)
    if meta["enrollment"] is None:
        fail("operation_corrupt", "reservation lacks its enrollment operation record")
    lease = meta["lease"]
    if ((state.owner_digest is None) != (lease is None)
            or (lease is not None and lease["owner_digest"] != state.owner_digest)
            or meta["enrollment"]["revision"] > state.status.revision):
        fail("operation_corrupt", "reservation and operation ownership are inconsistent")
    if receipt is not None:
        controller.receipt(desktop)  # Structural issuance validation; stale receipts are PRESERVED.
    return state.status.phase.value


def shim_ready(path=SHIM_PATH):
    try:
        data, info = lc.read_owned(path, 32768, asset=True)
    except FileNotFoundError:
        return False
    except Refusal:
        if not path.exists() and not path.is_symlink():
            return False
        raise
    if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o755 or data != SHIM:
        fail("shim_conflict", "fixed system shim is foreign/modified; retained without overwrite")
    return True


# Minimal separately privileged publication program. The stdin content is pinned
# inside the program, and no target path/owner/user-controlled code is executed.
def publish_shim(transport):
    code = '''import hashlib, os, pathlib, stat, sys
if os.geteuid() != 0 or os.getuid() != 0: raise SystemExit(1)
path = pathlib.Path('/usr/bin/quick-deploy-display-control')
data = sys.stdin.buffer.read(32769)
if len(data) > 32768 or hashlib.sha256(data).hexdigest() != EXPECTED: raise SystemExit(1)
parent = path.parent
fd_dir = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    parent_info = os.fstat(fd_dir)
    if parent_info.st_uid != 0 or parent_info.st_mode & 0o022: raise SystemExit(1)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd_dir)
    except FileNotFoundError:
        fd = None
    if fd is not None:
        try:
            s = os.fstat(fd)
            if not stat.S_ISREG(s.st_mode) or s.st_uid != 0 or s.st_nlink != 1 or stat.S_IMODE(s.st_mode) != 0o755 or os.read(fd, 32769) != data: raise SystemExit(1)
            linked = os.stat(path.name, dir_fd=fd_dir, follow_symlinks=False)
            if (s.st_dev, s.st_ino) != (linked.st_dev, linked.st_ino): raise SystemExit(1)
        finally: os.close(fd)
    else:
        temp = '.qd-display-' + os.urandom(12).hex()
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o755, dir_fd=fd_dir)
        try:
            with os.fdopen(fd, 'wb') as f:
                os.fchmod(f.fileno(), 0o755); f.write(data); f.flush(); os.fsync(f.fileno())
            os.link(temp, path.name, src_dir_fd=fd_dir, dst_dir_fd=fd_dir, follow_symlinks=False)
            os.unlink(temp, dir_fd=fd_dir); os.fsync(fd_dir)
        finally:
            try: os.unlink(temp, dir_fd=fd_dir)
            except FileNotFoundError: pass
finally: os.close(fd_dir)
'''.replace("EXPECTED", repr(cfg.digest(SHIM)))
    # LocalTransport intentionally has no stdin interface for ordinary host
    # commands. This one INSTALLER-only sudo step is explicit, never RPC.
    import subprocess
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
    result = subprocess.run(("/usr/bin/sudo", "--", "/usr/bin/python3", "-I", "-c", code),
                            input=SHIM, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            env=env, timeout=120, check=False)
    if result.returncode != 0:
        fail("shim_publication_failed", "explicit privileged shim publication failed or collided; private install may already be complete")


@dataclass(frozen=True)
class Result:
    root: Path
    version: str
    status: str
    rpc_ready: bool


def install(metadata, package, ubuntu, architecture, installed_version, *, source=None, home=None,
            transport=None, info=host.serverinfo, publish=False, check_shim=shim_ready, publisher=publish_shim,
            platform=None):
    if (platform or sys.platform) != "linux":
        fail("platform_runtime_unavailable", "Windows/macOS lifecycle publication is pending; no Linux settings were created")
    uid = os.geteuid()
    if uid == 0 or os.getuid() != uid:
        fail("installer_identity", "run as the Sunshine desktop user, never root/sudo or a mismatched UID")
    home = (Path(pwd.getpwuid(uid).pw_dir) if home is None else Path(home)).resolve(strict=True)
    if not home.is_absolute() or home.stat().st_uid != uid:
        fail("installer_identity", "NSS home must belong to the invoking desktop user")
    source = Path(source or Path(__file__).resolve().parent)
    transport = transport or rt.LocalTransport()
    ref = q.reference(metadata, package, ubuntu, architecture, installed_version, transport)
    installed, desktop = discover(ref, home, uid, transport, info)
    root = home / RELATIVE_ROOT
    ready = check_shim()  # Reject foreign global publications BEFORE private writes.
    # HOME/.local/share ancestors may be normal parent links, but the controller
    # publication itself must be canonical. No env-selected alternate state root.
    root.parent.mkdir(parents=True, exist_ok=True)
    if root.parent.resolve(strict=True) != root.parent:
        fail("publication_unsafe", "private installation parents below canonical HOME must not redirect the fixed lookup")
    fresh = not root.exists() and not root.is_symlink()
    if fresh:
        make_directory(root)
    with installation_lock(root):
        if fresh:
            for name in ("versions", "active", "state"):
                make_directory(root / name)
            create(root / "state/operation.lock", b"")
            create(root / "state/reservation.lock", b"\0")
        if {p.name for p in root.iterdir()} != {".install.lock", "versions", "active", "state"}:
            fail("publication_unsafe", "private installation has foreign/incomplete publications")
        anchors = {p: private_directory(p) for p in (root / "versions", root / "active", root / "state")}
        old, serial = active(root)
        if (old is None) != fresh:
            fail("publication_unsafe", "installation lacks a complete active reference; trusted repair required")
        for path in (root / "versions").iterdir():
            validate_version(path)
        # Do not create a missing operation lock on upgrades or erase interrupted state.
        private_read(root / "state/operation.lock", 1)
        with lc.PrivateFiles(root / "state").locked() as files:
            status = validate_state(installed, desktop, files)
            data = document(settings_document(installed))
            if old is not None:
                old_data = private_read(root / "versions" / old / "host-settings.json", 8192)
                old_obj, new_obj = unique_json(old_data), unique_json(data)
                old_obj["host_uniqueid"] = lc.canonical_uuid(old_obj["host_uniqueid"])
                if old_obj != new_obj:
                    fail("settings_conflict", "existing settings/routes/build differ from fresh qualification; preserved without automatic migration")
                data = old_data  # Preserve even formatting/UUID spelling from valid installed settings.
            payload = {name: source_read(source / name) for name in SOURCES}
            version_hash = cfg.digest(document({name: cfg.digest(content) for name, content in payload.items()}))
            version = f"v{RELEASE}-{version_hash}"
            payload["host-settings.json"] = data
            marker = {"schema": 1, "release": RELEASE, "version": version,
                      "files": {name: cfg.digest(content) for name, content in payload.items()}}
            target = root / "versions" / version
            if target.exists() or target.is_symlink():
                validate_version(target, marker)
            else:
                make_directory(target)
                for name, content in payload.items():
                    create(target / name, content)
                # Ownership completion marker is LAST. An interrupted version is
                # retained and explicitly refused, never silently repaired/adopted.
                create(target / ".owned.json", document(marker))
                validate_version(target, marker)
            if any(private_directory(p) != identity for p, identity in anchors.items()) or active(root) != (old, serial):
                fail("publication_race", "active reference or private directories changed before publication")
            if old != version:
                if serial >= 512:
                    fail("publication_unsafe", "active history full; explicit maintenance required")
                create(root / "active" / f"{serial + 1:08d}.json", document({"schema": 1, "version": version}))
    if not ready and publish:
        publisher(transport)
        ready = check_shim()
        if not ready:
            fail("shim_publication_failed", "privileged publisher returned without the exact root-owned shim")
    return Result(root, version, status, ready)


def rpc_main(*, enroll=False, manifest=host.MANIFEST, handle=host.handle, interactive=host.interactive_enrollment,
             stdin=None, stdout=None, stderr=None):
    stdin, stdout, stderr = stdin or sys.stdin, stdout or sys.stdout, stderr or sys.stderr
    try:
        if os.geteuid() == 0 or os.getuid() != os.geteuid():
            fail("installer_identity", "controller must run as its unprivileged installed user")
        if enroll:
            if not stdin.isatty() or not stderr.isatty():
                fail("operator_authorization_required", "run quick-deploy-display-control --enroll in the host user's interactive terminal")
            installed = host.settings(manifest, "linux")
            content, _ = cfg.load(installed.runtime.config_file)
            facts = cfg.get_settings(content)
            target = {"address": facts["bind_address"], "port": installed.base_port}
            status_req = document({"version": 1, "action": "status", "target": target})
            status = unique_json(handle(status_req, manifest=manifest, authorize_enrollment=lambda: False))
            if status.get("ok") is not True:
                fail("operator_authorization_required", "ownership status unavailable; enrollment did not start")
            raw = document({"version": 1, "action": "enroll", "target": target, "revision": status["revision"]})
            result = handle(raw, manifest=manifest, authorize_enrollment=interactive)
        else:
            raw = stdin.buffer.read(MAX_INPUT + 1)
            if request(raw)["action"] == "enroll":
                fail("operator_authorization_required", "RPC never enrolls; use the explicit interactive local --enroll command")
            result = handle(raw, manifest=manifest, authorize_enrollment=lambda: False)
        stdout.buffer.write(result)
        return 0
    except Refusal as exc:
        stdout.buffer.write(response(False, error={"code": exc.code, "detail": exc.detail}))
        # A completed RPC error envelope has host.main's exit0 contract. Nonzero
        # SSH means transport uncertainty, not a confirmed host refusal. Local
        # interactive enrollment remains a CLI operation with failure exit1.
        return 1 if enroll else 0
    except Exception:
        stdout.buffer.write(response(False, error={"code": "installation_unavailable", "detail": "controller operation failed; private ownership retained"}))
        return 1 if enroll else 0


def main():
    if sys.argv[1:] in (["rpc"], ["enroll"]):
        return rpc_main(enroll=sys.argv[1] == "enroll")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("asset", "install"))
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--ubuntu", required=True)
    parser.add_argument("--architecture", required=True)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--installed-version")
    parser.add_argument("--publish-shim", action="store_true")
    args = parser.parse_args()
    try:
        metadata, _ = lc.read_owned(args.metadata, 1024 * 1024)
        if args.action == "asset":
            selected = q.asset(metadata, args.ubuntu, args.architecture)
            print(selected.name, selected.url, selected.sha256, selected.size, sep="\n")
            return 0
        if args.package is None or args.installed_version is None:
            fail("installation_unavailable", "reference package and installed package version are required")
        result = install(metadata, args.package, args.ubuntu, args.architecture,
                         args.installed_version, publish=args.publish_shim)
        print(f"Private display controller: {result.root}; status={result.status}. Settings/state preserved on updates.")
        if not result.rpc_ready:
            print("Shim NOT PUBLISHED: noninteractive SSH RPC is not ready. Locally authorize the new system-wide /usr/bin/quick-deploy-display-control via commands/install-display-control.sh --publish-shim.", file=sys.stderr)
            return 1
        print("Fixed RPC shim ready: /usr/bin/quick-deploy-display-control (SSH PATH must include /usr/bin).")
        if result.status == "UNENROLLED":
            print("UNENROLLED: no stream adopted and no StartReceipt issued. When idle/exclusive, run quick-deploy-display-control --enroll from this user's interactive host terminal.")
        return 0
    except Refusal as exc:
        print(f"Display controller refused [{exc.code}]: {exc.detail}", file=sys.stderr)
        return 1
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        print("Display controller installation unavailable; existing publications/state retained. No vendor/service changes were performed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
