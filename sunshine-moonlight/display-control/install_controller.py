#!/usr/bin/env python3
"""User-owned companion publication; no vendor update, restart or enrollment.

The shell entry owns common.sh release download/verification. This module owns
read-only qualification/discovery and create-only version/active publications.
All deployment I/O is explicit; imports do not inspect the host or HOME.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
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


# This is an installer-only transfer of authority. The RPC ABI and root shim
# deliberately have no rebind verb. Completed journals remain permanent evidence.
IDENTITY_RECORDS = ("operation.json", "reservation.json", "prepare-backup.conf", "start-receipt.json")
BASELINE = {"schema": 1, "enrollment": None, "lease": None}


def interactive_rebind():
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
        try:
            if not os.isatty(fd):
                return False
            os.write(fd, b"Confirm exclusive controller use, no existing/in-flight unmanaged stream, no concurrent admin edits, and completed official PIN pairing with the CURRENT Sunshine. Type REBIND: ")
            return os.read(fd, 64).strip() == b"REBIND"
        finally:
            os.close(fd)
    except OSError:
        return False


def identity_refused(reason):
    fail("identity_reconciliation_refused", "Identity reconciliation refused: " + reason)


def release_refused(reason):
    fail("owned_release_reconciliation_refused", "Owned release reconciliation refused: " + reason)


def interactive_release():
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
        try:
            if not os.isatty(fd):
                return False
            os.write(fd, b"Confirm the recorded official Moonlight quit was issued, exclusive controller use, no unmanaged/in-flight stream and no concurrent admin edits. Type RELEASE: ")
            return os.read(fd, 64).strip() == b"RELEASE"
        finally:
            os.close(fd)
    except OSError:
        return False


def record_identity(path):
    data = private_read(path)
    before = path.lstat()
    # Rename changes ctime; bytes, inode, permissions and mtime must survive.
    result = {"sha256": cfg.digest(data), "device": before.st_dev, "inode": before.st_ino,
              "mode": before.st_mode, "uid": before.st_uid, "size": before.st_size,
              "mtime_ns": before.st_mtime_ns}
    if private_read(path) != data or stamp(path.lstat()) != stamp(before):
        identity_refused("private record changed during evidence sampling")
    return result


class IdentityRecords:
    """Read-only old operation view, including after its exact rename."""
    def __init__(self, records):
        self.records = records

    def read(self, name, limit=lc.MAX_PRIVATE):
        data = self.records.get(name)
        if data is not None and len(data) > limit:
            identity_refused("old record exceeds its schema bound")
        return data

    def document(self, name):
        data = self.read(name)
        return unique_json(data) if data is not None else None


def identity_evidence(old, installed, desktop, records, ref, home, uid, transport, info):
    """E1–E6, with no publication or state mutation, even on failure."""
    old_obj, new_obj = settings_document(old), settings_document(installed)
    old_obj["host_uniqueid"] = installed.host_uniqueid
    if old.host_uniqueid == installed.host_uniqueid or old_obj != new_obj:
        identity_refused("fresh qualification must change only host_uniqueid")
    controller = lc.HostController(old, None, None, None, None)
    controller.files = IdentityRecords(records)
    meta = controller.meta()
    raw = records.get("reservation.json")
    if raw is None:
        identity_refused("old reservation is required")
    state = rs._decode(raw, controller.host)  # Never decode old state with the new UUID.
    status, lease = state.status, meta["lease"]
    if (status.phase is not rs.Phase.RECONCILIATION_REQUIRED
            or status.uncertainty is not rs.Uncertainty.RUNTIME_CHANGED
            or status.launch_attempted or status.active_observed or status.desktop is not None):
        identity_refused("old reservation must be runtime_changed with no launch or active evidence in this lease")
    if (meta["enrollment"] is None or meta["enrollment"]["revision"] > status.revision
            or lease is None or lease["stage"] != "restart_intent"
            or state.owner_digest is None or state.owner_digest != lease["owner_digest"]
            or status.binding is None or "prepared_revision" in lease
            or "release_revision" in lease or "cancel_transaction" in lease):
        identity_refused("enrolled restart intent and reservation owner must agree")
    try:
        generation = rt.Generation(**lease["previous_generation"])
        if (not re.fullmatch("[0-9a-f]{32}", generation.boot_id)
                or not re.fullmatch("[0-9a-f]{32}", generation.invocation_id)
                or generation.unit not in (rt.UNIT, rt.ALIAS) or generation.uid != uid
                or any(type(v) is not int or v <= 0 for v in
                       (generation.pid, generation.start_ticks, generation.start_monotonic_usec))
                or not re.fullmatch("[0-9a-f-]{36}", lease["restart_id"])
                or lc.canonical_uuid(lease["restart_id"]) != lease["restart_id"]
                or lc.service_generation(generation) != status.binding.generation
                or any(type(lease[k]) is not str or not re.fullmatch("[0-9a-f]{64}", lease[k])
                       for k in ("before_digest", "after_digest"))):
            raise ValueError("restart facts")
    except (ValueError, TypeError, KeyError):
        identity_refused("restart intent must name the reserved process birth")
    backup = records.get("prepare-backup.conf")
    if backup is None or cfg.digest(backup) != lease["before_digest"]:
        identity_refused("preserved preparation backup must match the restart intent")
    controller.receipt(desktop)  # Validate any older receipt before archiving it.
    envelope = controller.files.document("start-receipt.json")
    if envelope is not None and envelope["issuance"]["operation"] == lease["restart_id"]:
        identity_refused("this restart already has an issued receipt")
    runtime = rt.LinuxRuntime(installed.runtime, transport)
    current = runtime.observe()
    q.qualify(ref, current, transport)
    if (current.binary.process_exe_status != "verified"
            or current.config.process_environment_status != "verified"):
        identity_refused("replacement loaded executable and configuration require verified proc access")
    if (current.generation == generation or current.generation.unit != generation.unit
            or current.config.file.sha256 != lease["after_digest"]
            or lease["before_digest"] != status.binding.config_digest
            or lease["topology"] != status.binding.topology_digest
            or current.config.configured_output != lease["selector"]
            or desktop.definition_digest != lease["desktop_digest"]):
        identity_refused("qualified replacement generation and prepared config must agree")
    if envelope is not None and lc.receipt_from_json(envelope["receipt"]).generation == current.generation:
        identity_refused("replacement already has an issued receipt")
    # A missing PID is usable only together with a qualified DIFFERENT unit
    # invocation/process birth on the recorded boot. A reboot retires the entire
    # old boot. A present PID is compared by start ticks (PID reuse is allowed).
    if generation.boot_id != current.generation.boot_id:
        retirement = {"kind": "previous_boot"}
    else:
        if (generation.invocation_id == current.generation.invocation_id
                or current.generation.start_monotonic_usec <= generation.start_monotonic_usec):
            identity_refused("replacement unit invocation does not retire the old invocation")
        try:
            data = transport.read(Path("/proc") / str(generation.pid) / "stat", limit=4096)
        except FileNotFoundError:
            retirement = {"kind": "absent_old_process", "new_invocation": current.generation.invocation_id}
        except OSError:
            identity_refused("old process retirement cannot be observed")
        else:
            try:
                if type(data) is not bytes or len(data) > 4096:
                    raise ValueError("stat bound")
                text = data.decode("ascii")
                fields = text[text.rfind(")") + 2:].split()
                if not text.startswith(str(generation.pid) + " (") or len(fields) < 20:
                    raise ValueError("stat")
                ticks = int(fields[19])
                if ticks == generation.start_ticks and fields[0] not in ("Z", "X"):
                    identity_refused("reserved old process is still alive")
                retirement = {"kind": "pid_reused" if ticks != generation.start_ticks else "dead_process",
                              "start_ticks": ticks, "state": fields[0]}
            except (ValueError, UnicodeError):
                identity_refused("old process birth evidence is invalid")
    endpoint = info(current.config.bind_address, current.config.base_port)
    if (type(endpoint) is not lc.Endpoint or endpoint.state != "FREE"
            or type(endpoint.currentgame) is not int or endpoint.currentgame != 0
            or lc.canonical_uuid(endpoint.host_uniqueid) != installed.host_uniqueid):
        identity_refused("replacement public endpoint must be the new idle UUID")
    # Fresh official metadata/package qualification and discovery must repeat,
    # including apps/assets/unit/binary, not merely the public FREE bit.
    fresh, fresh_desktop = discover(ref, home, uid, transport, info)
    if settings_document(fresh) != new_obj or fresh_desktop != desktop:
        identity_refused("fresh qualification changed during reconciliation")
    after = runtime.observe()
    q.qualify(ref, after, transport)
    if (lc.authority(after) != lc.authority(current)
            or after.binary.process_exe_status != "verified"
            or after.config.process_environment_status != "verified"):
        identity_refused("replacement runtime changed during evidence sampling")
    return {"old_uuid": old.host_uniqueid, "new_uuid": installed.host_uniqueid,
            "previous_generation": asdict(generation), "current_generation": asdict(current.generation),
            "unit": asdict(current.unit), "config": asdict(current.config), "binary": asdict(current.binary),
            "desktop": asdict(desktop), "endpoint": {"uuid": installed.host_uniqueid, "state": "FREE", "currentgame": 0},
            "retirement": retirement, "restart_id": lease["restart_id"], "reservation_revision": status.revision}


def transition_records(files, journal=None):
    records, identities = {}, {}
    for name in IDENTITY_RECORDS:
        archived = name + "." + journal["id"] + ".bak" if journal else None
        data = files.read(archived, 1024 * 1024) if archived else None
        live = files.read(name, 1024 * 1024)
        path = files.path / (archived if data is not None else name)
        if data is not None:
            if live is not None and not (name == "operation.json" and live == document(BASELINE)):
                identity_refused("both old and replacement live records exist")
        else:
            data = live
        if data is not None:
            records[name], identities[name] = data, record_identity(path)
    if journal and identities != journal["records"]:
        identity_refused("old records differ from the durable transition")
    return records, identities


def active_prefix(root, serial):
    """The original reference prefix stays sealed; later appends are unrelated."""
    return {f"{n:08d}.json": record_identity(root / "active" / f"{n:08d}.json")
            for n in range(1, serial + 1)}


def identity_journal(path):
    """Validate journal structure before it can name a resumable publication."""
    try:
        value = unique_json(private_read(path, lc.MAX_PRIVATE))
        if (type(value) is not dict or set(value) != {"schema", "id", "old_version", "serial", "new_manifest",
                                                   "evidence", "records", "permanent", "history", "active_prefix", "confirmation"}
                or type(value["schema"]) is not int or value["schema"] != 1
                or type(value["id"]) is not str or not re.fullmatch("[0-9a-f]{32}", value["id"])
                or path.name != "identity-transition-" + value["id"] + ".bak"
                or type(value["old_version"]) is not str
                or not re.fullmatch(r"v[1-9][0-9]*-[0-9a-f]{64}", value["old_version"])
                or type(value["serial"]) is not int or not 1 <= value["serial"] < 512
                or value["confirmation"] != "REBIND:exclusive:no_unmanaged_stream:official_pair_completed"):
            raise ValueError("journal")
        marker = value["new_manifest"]
        if (type(marker) is not dict or set(marker) != {"schema", "release", "version", "files"}
                or type(marker["schema"]) is not int or marker["schema"] != 1
                or type(marker["release"]) is not int or marker["release"] != RELEASE
                or type(marker["version"]) is not str
                or not re.fullmatch(r"v[1-9][0-9]*-[0-9a-f]{64}", marker["version"])
                or type(marker["files"]) is not dict or set(marker["files"]) != set(SOURCES) | {"host-settings.json"}
                or any(type(v) is not str or not re.fullmatch("[0-9a-f]{64}", v) for v in marker["files"].values())
                or marker["version"] != f"v{RELEASE}-" + cfg.digest(document(marker["files"]))):
            raise ValueError("manifest")
        if (type(value["records"]) is not dict or not {"operation.json", "reservation.json", "prepare-backup.conf"} <= set(value["records"])
                or set(value["records"]) - set(IDENTITY_RECORDS)
                or type(value["permanent"]) is not dict
                or set(value["permanent"]) != {".install.lock", "state/operation.lock", "state/reservation.lock"}
                or type(value["history"]) is not dict
                or any(type(k) is not str or "/" in k or not k.endswith(".bak") for k in value["history"])
                or type(value["active_prefix"]) is not dict
                or set(value["active_prefix"]) != {f"{n:08d}.json" for n in range(1, value["serial"] + 1)}
                or type(value["evidence"]) is not dict
                or lc.canonical_uuid(value["evidence"]["old_uuid"]) == lc.canonical_uuid(value["evidence"]["new_uuid"])):
            raise ValueError("evidence")
        for identities in (value["records"], value["permanent"], value["history"], value["active_prefix"]):
            for record in identities.values():
                if (type(record) is not dict or set(record) != {"sha256", "device", "inode", "mode", "uid", "size", "mtime_ns"}
                        or type(record["sha256"]) is not str or not re.fullmatch("[0-9a-f]{64}", record["sha256"])
                        or any(type(v) is not int or v < 0 for k, v in record.items() if k != "sha256")):
                    raise ValueError("record")
        return value
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, Refusal):
        identity_refused("identity transition journal is invalid")


def reconcile_identity(root, old_version, serial, installed, desktop, files, ref, home, uid,
                       transport, info, marker, authorize):
    old = host.settings(root / "versions" / old_version / "host-settings.json", "linux")
    journals = []
    for path in sorted(files.path.glob("identity-transition-*.bak")):
        value = identity_journal(path)
        # An already committed transition is a normal installation from here on.
        references = sorted((root / "active").iterdir())
        n = value["serial"]
        if type(n) is not int or not 1 <= n < 512 or n > serial:
            identity_refused("transition active reference is invalid")
        # These original records stay immutable in BOTH pending and committed
        # paths, including the same-UUID idempotent return below. Only the bound
        # prefix/history is checked; later legitimate appends/leases may evolve.
        if active_prefix(root, n) != value["active_prefix"]:
            identity_refused("original active reference prefix differs")
        if any(record_identity(files.path / name) != expected for name, expected in value["history"].items()):
            identity_refused("original historical record differs")
        if unique_json(private_read(references[n - 1], 1024))["version"] != value["old_version"]:
            identity_refused("transition old active reference differs")
        if n < serial:
            if unique_json(private_read(references[n], 1024))["version"] != value["new_manifest"]["version"]:
                identity_refused("transition committed reference differs")
            for name, expected in value["records"].items():
                if record_identity(files.path / (name + "." + value["id"] + ".bak")) != expected:
                    identity_refused("committed transition archive differs")
            if value["permanent"] != {str(p.relative_to(root)): record_identity(p) for p in
                                      (root / ".install.lock", files.path / "operation.lock", files.path / "reservation.lock")}:
                identity_refused("committed transition permanent lock differs")
        else:
            journals.append(value)
    if len(journals) > 1:
        identity_refused("multiple uncommitted identity transitions")
    journal = journals[0] if journals else None
    if not journal and old.host_uniqueid == installed.host_uniqueid:
        # Explicit flag is idempotent after the transition, but never resets a
        # same-identity stale lease. Require a completed journal for this UUID.
        if not any(identity_journal(p)["evidence"]["new_uuid"] == installed.host_uniqueid
                   for p in files.path.glob("identity-transition-*.bak")):
            identity_refused("no changed host identity or completed transition")
        return False
    records, identities = transition_records(files, journal)
    prefix = active_prefix(root, serial)
    permanent = {str(p.relative_to(root)): record_identity(p) for p in
                 (root / ".install.lock", files.path / "operation.lock", files.path / "reservation.lock")}
    excluded = set(IDENTITY_RECORDS) | {"operation.lock", "reservation.lock"}
    if journal:
        excluded |= {"identity-transition-" + journal["id"] + ".bak"}
        excluded |= {name + "." + journal["id"] + ".bak" for name in journal["records"]}
    history = {}
    for path in files.path.iterdir():
        if path.name not in excluded:
            if not path.name.endswith(".bak"):
                identity_refused("state contains an unknown or incomplete publication")
            history[path.name] = record_identity(path)
    if private_read(files.path / "operation.lock", 1) != b"" or private_read(files.path / "reservation.lock", 1) != b"\0":
        identity_refused("permanent state lock content differs")
    evidence = identity_evidence(old, installed, desktop, records, ref, home, uid, transport, info)
    if journal:
        if (journal["old_version"] != old_version or journal["serial"] != serial
                or journal["new_manifest"] != marker or journal["evidence"] != evidence
                or journal["permanent"] != permanent or journal["history"] != history
                or journal["active_prefix"] != prefix):
            identity_refused("resume facts differ from the exact UUID-bound durable transition")
    else:
        if serial >= 512:
            identity_refused("active reference history is full")
        if authorize() is not True:
            identity_refused("local exclusive REBIND and official pairing confirmation required")
    # E8 repeats all stable evidence AFTER confirmation (also on resume).
    again, again_ids = transition_records(files, journal)
    if (again_ids != identities or identity_evidence(old, installed, desktop, again, ref, home, uid, transport, info) != evidence
            or permanent != {str(p.relative_to(root)): record_identity(p) for p in
                             (root / ".install.lock", files.path / "operation.lock", files.path / "reservation.lock")}
            or history != {p.name: record_identity(p) for p in files.path.iterdir() if p.name not in excluded}
            or active_prefix(root, serial) != prefix or active(root) != (old_version, serial)):
        identity_refused("facts changed after REBIND confirmation")
    if journal is None:
        journal = {"schema": 1, "id": os.urandom(16).hex(), "old_version": old_version, "serial": serial,
                   "new_manifest": marker, "evidence": evidence, "records": identities,
                   "permanent": permanent, "history": history, "active_prefix": prefix,
                   "confirmation": "REBIND:exclusive:no_unmanaged_stream:official_pair_completed"}
        path = files.path / ("identity-transition-" + journal["id"] + ".bak")
        if len(document(journal)) > lc.MAX_PRIVATE:
            identity_refused("transition evidence exceeds the bounded private journal")
        create(path, document(journal))  # Durable and verified BEFORE the first rename.
        if unique_json(private_read(path)) != journal:
            identity_refused("durable transition verification failed")
    for name in IDENTITY_RECORDS:
        if name not in journal["records"]:
            continue
        archive = name + "." + journal["id"] + ".bak"
        if files.read(archive, 1024 * 1024) is None:
            if record_identity(files.path / name) != journal["records"][name]:
                identity_refused("old record changed before archival")
            # Cooperative install+operation locks exclude every controller writer.
            # Destinations were checked absent; rename retains the original inode.
            files.anchor()
            os.rename(name, archive, src_dir_fd=files.fd, dst_dir_fd=files.fd)
            os.fsync(files.fd)
        if record_identity(files.path / archive) != journal["records"][name]:
            identity_refused("archive does not preserve the original record")
    if files.read("operation.json") is None:
        create(files.path / "operation.json", document(BASELINE))
    elif files.read("operation.json") != document(BASELINE):
        identity_refused("replacement operation baseline differs")
    return True


# This separate local repair NEVER transfers host identity. It retains enrollment
# and the current receipt while archiving the complete old release transaction.
# Reservation is archived first; an invalid private sentinel blocks OLD and new
# dispatchers until the final atomic availability publication.
RELEASE_RECORDS = ("reservation.json", "operation.json", "start-receipt.json", "prepare-backup.conf")
RELEASE_BLOCKED = b"\0"


def release_records(files, journal=None):
    records, identities = {}, {}
    for name in RELEASE_RECORDS:
        archive = name + "." + journal["id"] + ".bak" if journal else None
        archived = files.read(archive, 1024 * 1024) if archive else None
        live = files.read(name, 1024 * 1024)
        if archived is not None:
            if live is not None and (name not in journal["replacement"]
                                     or (live != bytes.fromhex(journal["replacement"][name])
                                         and not (name == "reservation.json" and live == RELEASE_BLOCKED)
                                         and not (name == "reservation.json" and live == bytes.fromhex(journal["blocked"])))
                                     or (live != RELEASE_BLOCKED and record_identity(files.path / name) == journal["records"][name])):
                release_refused("old and unexpected live records coexist")
            path, data = files.path / archive, archived
        else:
            path, data = files.path / name, live
        if data is None:
            release_refused("original owned release record is missing")
        records[name], identities[name] = data, record_identity(path)
    if journal and identities != journal["records"]:
        release_refused("old release records differ from the durable journal")
    return records, identities


def release_evidence(old, installed, desktop, records, ref, home, uid, transport, info):
    """Only the documented positive Desktop -> release intent -> owner loss."""
    if settings_document(old) != settings_document(installed):
        release_refused("installed identity/settings differ from fresh qualification")
    controller = lc.HostController(old, rt.LinuxRuntime(old.runtime, transport), None, info, None)
    controller.files = IdentityRecords(records)
    meta = controller.meta()
    state = rs._decode(records["reservation.json"], controller.host)
    status, lease = state.status, meta["lease"]
    if (status.phase is not rs.Phase.RECONCILIATION_REQUIRED
            or status.uncertainty is not rs.Uncertainty.OWNER_LOST
            or not status.active_observed or not status.launch_attempted
            or status.desktop is None or status.binding is None):
        release_refused("positive owned Desktop and owner_lost release are required")
    fields = {"owner_digest", "stage", "selection_desktop", "display_id", "selector", "topology",
              "desktop_digest", "prepared_revision", "release_revision", "cancel_transaction"}
    restart_fields = {"before_digest", "after_digest", "restart_id", "previous_generation"}
    if (meta["enrollment"] is None or lease is None or lease.get("stage") != "releasing"
            or lease.get("owner_digest") != state.owner_digest or state.owner_digest is None
            or set(lease) not in (fields, fields | restart_fields)
            or type(lease.get("prepared_revision")) is not int
            or type(lease.get("release_revision")) is not int
            or not (meta["enrollment"]["revision"] < lease["prepared_revision"]
                    < status.attempt_revision < lease["release_revision"]
                    and status.revision == lease["release_revision"] + 1)
            or type(lease.get("cancel_transaction")) is not str):
        release_refused("release intent, attempt, owner and revisions do not agree")
    restarted = set(lease) == fields | restart_fields
    try:
        if (lc.canonical_uuid(lease["cancel_transaction"]) != lease["cancel_transaction"]
                or lease["selection_desktop"] != asdict(status.desktop)
                or lease["desktop_digest"] != status.desktop.definition_digest
                or lease["topology"] != status.binding.topology_digest):
            raise ValueError("lease")
        if restarted and (lc.canonical_uuid(lease["restart_id"]) != lease["restart_id"]
                          or lease["after_digest"] != status.binding.config_digest
                          or cfg.digest(records["prepare-backup.conf"]) != lease["before_digest"]):
            raise ValueError("restart")
    except (ValueError, KeyError, TypeError, Refusal):
        release_refused("recorded selection/restart/cancel facts do not match the owner")
    try:
        envelope = controller.files.document("start-receipt.json")
        receipt = controller.receipt(desktop)
        matches = (receipt is not None and receipt.host_uniqueid == old.host_uniqueid
                   and envelope["issuance"]["enrollment_revision"] == meta["enrollment"]["revision"]
                   and (not restarted or (envelope["issuance"]["operation"] == lease["restart_id"]
                                          and envelope["issuance"]["previous_generation"] == lease["previous_generation"])))
    except (ValueError, UnicodeError, RecursionError, TypeError, KeyError):
        release_refused("current controlled-start receipt is malformed")
    if not matches:
        release_refused("current controlled-start receipt does not belong to this release")
    current = controller.runtime.observe(receipt=receipt)
    q.qualify(ref, current, transport)
    target = {"address": current.config.bind_address, "port": old.base_port}
    owned = controller.sample_ownership(target, require_receipt=True)
    if (owned.endpoint.state != "FREE" or owned.endpoint.currentgame != 0
            or owned.binding != rs.OwnershipBinding.from_runtime(status.binding)
            or owned.desktop != status.desktop or owned.desktop != desktop
            or owned.current.generation != receipt.generation
            or owned.current.config.configured_output != lease["selector"]
            or owned.current.binary.process_exe_status != "verified"
            or owned.current.config.process_environment_status != "verified"):
        release_refused("fresh idle owned generation/config/Desktop/receipt do not agree")
    if not restarted:
        # This is the real rev8 path: a prior controlled preparation/abort left
        # a valid receipt and selected config, so the new preparation is a no-op.
        # Its lease has NO restart_id/after_digest. Require the preserved backup
        # to project byte-exactly to the currently receipt-bound selected config.
        try:
            if cfg.select_output(records["prepare-backup.conf"], lease["selector"]) != owned.content:
                release_refused("prior preparation backup does not produce the owned config")
        except Refusal:
            release_refused("prior preparation backup is not qualified")
    def qualified_idle_info(address, port):
        endpoint = info(address, port)
        if (type(endpoint) is not lc.Endpoint or lc.canonical_uuid(endpoint.host_uniqueid) != old.host_uniqueid
                or endpoint.state != "FREE" or type(endpoint.currentgame) is not int or endpoint.currentgame != 0):
            release_refused("later qualification observed a busy or different endpoint")
        return endpoint

    fresh, fresh_desktop = discover(ref, home, uid, transport, qualified_idle_info)
    if settings_document(fresh) != settings_document(installed) or fresh_desktop != desktop:
        release_refused("qualified settings or Desktop changed during release observation")
    again = controller.runtime.observe(receipt=receipt)
    q.qualify(ref, again, transport)
    if lc.authority(again) != lc.authority(owned.current):
        release_refused("qualified host changed during release observation")
    return {"reservation_revision": status.revision, "preparation": "restarted" if restarted else "same_generation_noop",
            "release_revision": lease["release_revision"],
            "attempt_revision": status.attempt_revision, "prepared_revision": lease["prepared_revision"],
            "cancel_transaction": lease["cancel_transaction"], "owner_digest": state.owner_digest,
            "generation": asdict(current.generation), "unit": asdict(current.unit),
            "config": asdict(current.config), "binary": asdict(current.binary),
            "desktop": asdict(desktop), "receipt": cfg.digest(records["start-receipt.json"]),
            "endpoint": {"uuid": old.host_uniqueid, "state": "FREE", "currentgame": 0}}


def release_journal(path):
    try:
        value = unique_json(private_read(path, lc.MAX_PRIVATE))
        if (type(value) is not dict
                or set(value) != {"schema", "id", "version", "serial", "manifest", "evidence", "records", "replacement",
                                  "blocked", "permanent", "history", "active_prefix", "confirmation"}
                or value["schema"] != 1 or type(value["id"]) is not str
                or not re.fullmatch("[0-9a-f]{32}", value["id"])
                or path.name != "release-transition-" + value["id"] + ".bak"
                or type(value["serial"]) is not int or not 1 <= value["serial"] <= 512
                or type(value["version"]) is not str
                or not re.fullmatch(r"v[1-9][0-9]*-[0-9a-f]{64}", value["version"])
                or value["confirmation"] != "RELEASE:exclusive:official_quit_issued:no_unmanaged_stream"
                or type(value["manifest"]) is not dict
                or set(value["manifest"]) != {"schema", "release", "version", "files"}
                or value["manifest"]["schema"] != 1 or value["manifest"]["release"] != RELEASE
                or type(value["manifest"]["version"]) is not str
                or type(value["manifest"]["files"]) is not dict
                or set(value["manifest"]["files"]) != set(SOURCES) | {"host-settings.json"}
                or any(type(v) is not str or not re.fullmatch("[0-9a-f]{64}", v)
                       for v in value["manifest"]["files"].values())
                or value["manifest"]["version"] != f"v{RELEASE}-" + cfg.digest(document(value["manifest"]["files"]))
                or type(value["evidence"]) is not dict
                or type(value["records"]) is not dict or set(value["records"]) != set(RELEASE_RECORDS)
                or type(value["replacement"]) is not dict
                or set(value["replacement"]) != {"operation.json", "reservation.json", "start-receipt.json", "prepare-backup.conf"}
                or any(type(v) is not str or not re.fullmatch("(?:[0-9a-f]{2})+", v)
                       for v in value["replacement"].values())
                or type(value["blocked"]) is not str or not re.fullmatch("(?:[0-9a-f]{2})+", value["blocked"])
                or type(value["permanent"]) is not dict
                or set(value["permanent"]) != {".install.lock", "state/operation.lock", "state/reservation.lock"}
                or type(value["history"]) is not dict
                or any(type(k) is not str or "/" in k or not k.endswith(".bak") for k in value["history"])
                or type(value["active_prefix"]) is not dict
                or set(value["active_prefix"]) != {f"{n:08d}.json" for n in range(1, value["serial"] + 1)}):
            raise ValueError("journal")
        for identities in (value["records"], value["permanent"], value["history"], value["active_prefix"]):
            for record in identities.values():
                if (type(record) is not dict or set(record) != {"sha256", "device", "inode", "mode", "uid", "size", "mtime_ns"}
                        or type(record["sha256"]) is not str or not re.fullmatch("[0-9a-f]{64}", record["sha256"])
                        or any(type(v) is not int or v < 0 for k, v in record.items() if k != "sha256")):
                    raise ValueError("record")
        return value
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError, Refusal):
        release_refused("durable owned-release journal is invalid")


def repair_owned_release(root, version, serial, installed, desktop, files, ref, home, uid,
                         transport, info, marker, authorize):
    """Installer-only exact release repair; never issues quit or accepts raw FREE."""
    # The operation lock is already held; neither permanent lock may be
    # rewritten as a side effect of discovering bad content.
    if private_read(files.path / "operation.lock", 1) != b"" or private_read(files.path / "reservation.lock", 1) != b"\0":
        release_refused("permanent lock contents are invalid")
    journals = [release_journal(p) for p in sorted(files.path.glob("release-transition-*.bak"))]
    permanent_paths = (root / ".install.lock", files.path / "operation.lock", files.path / "reservation.lock")
    permanent = {str(p.relative_to(root)): record_identity(p) for p in permanent_paths}
    try:
        journals.sort(key=lambda item: item["evidence"]["reservation_revision"])
        revisions = [item["evidence"]["reservation_revision"] for item in journals]
        if any(type(rev) is not int or rev < 1 for rev in revisions) or len(set(revisions)) != len(revisions):
            raise ValueError("journal revisions")
    except (KeyError, TypeError, ValueError):
        release_refused("release journal revision evidence is invalid")
    for index, item in enumerate(journals):
        if (item["serial"] > serial or active_prefix(root, item["serial"]) != item["active_prefix"]
                or any(record_identity(files.path / name) != identity for name, identity in item["history"].items())
                or permanent != item["permanent"]):
            release_refused("original history, references or permanent locks differ")
        seal = files.read("release-committed-" + item["id"] + ".bak")
        for name, identity in item["records"].items():
            archive = name + "." + item["id"] + ".bak"
            if files.read(archive) is not None:
                if record_identity(files.path / archive) != identity:
                    release_refused("original release archive differs")
            elif (index != len(journals) - 1 or seal is not None or files.read(name) is None
                  or record_identity(files.path / name) != identity):
                release_refused("original release record differs")
        if seal is not None and seal != document({"schema": 1, "journal_sha256": cfg.digest(
                private_read(files.path / ("release-transition-" + item["id"] + ".bak")))}):
            release_refused("completion seal differs from journal")
        if index < len(journals) - 1:
            if seal is None or revisions[index + 1] < revisions[index] + 1:
                release_refused("prior release is not sealed before the later transaction")
        # Seal only the original active prefix here. The next reference is a
        # code publication, not the release commit: after available is durable,
        # a qualified later source may legitimately occupy that serial. The
        # still-pending transition is source/serial-bound below, after the live
        # reservation has been classified against its committed revision floor.
    journal = journals[-1] if journals else None
    if journal:
        floor = journal["evidence"]["reservation_revision"] + 1
        promised_raw = bytes.fromhex(journal["replacement"]["reservation.json"])
        try:
            promised = rs._decode(promised_raw, rs.HostIdentity(installed.host_uniqueid, installed.base_port)).status
        except Refusal:
            release_refused("journal replacement reservation is invalid")
        if promised.phase is not rs.Phase.AVAILABLE or promised.revision != floor:
            release_refused("journal availability revision floor is invalid")
        live = files.read("reservation.json", rs.MAX_STATE)
        if live is None or live in (RELEASE_BLOCKED, bytes.fromhex(journal["blocked"])):
            pending = True
        else:
            current = rs._decode(live, rs.HostIdentity(installed.host_uniqueid, installed.base_port)).status
            if current.revision < floor:
                # Only the still-live original owner_lost record may precede
                # the commit. Older available snapshots are detectable rollback.
                original = files.path / "reservation.json"
                if (files.read("reservation.json." + journal["id"] + ".bak") is not None
                        or record_identity(original) != journal["records"]["reservation.json"]):
                    release_refused("current reservation predates the journal revision floor")
                pending = True
            else:
                if seal is None or (current.revision == floor and live != promised_raw):
                    release_refused("available reservation contradicts the committed journal")
                pending = False
        if pending and (journal["version"], journal["serial"], journal["manifest"]) != (version, serial, marker):
            release_refused("pending release active version/source changed")
        if not pending:
            phase = validate_state(installed, desktop, files)
            if phase != rs.Phase.RECONCILIATION_REQUIRED.value:
                return False
            journal = None  # A distinct later owner_lost release needs a new journal.
    old = host.settings(root / "versions" / version / "host-settings.json", "linux")
    if settings_document(old) != settings_document(installed):
        release_refused("installed settings/UUID changed")
    records, identities = release_records(files, journal)
    prefix = active_prefix(root, serial)
    excluded = set(RELEASE_RECORDS) | {"operation.lock", "reservation.lock"}
    if journal:
        excluded |= {"release-transition-" + journal["id"] + ".bak"}
        excluded |= {name + "." + journal["id"] + ".bak" for name in RELEASE_RECORDS}
        excluded |= {"release-committed-" + journal["id"] + ".bak"}
    history = {}
    for path in files.path.iterdir():
        if path.name not in excluded:
            if not path.name.endswith(".bak"):
                release_refused("unknown or incomplete private publication")
            history[path.name] = record_identity(path)
    evidence = release_evidence(old, installed, desktop, records, ref, home, uid, transport, info)
    state = rs._decode(records["reservation.json"], rs.HostIdentity(old.host_uniqueid, old.base_port))
    meta = unique_json(records["operation.json"])
    new_meta = document({"schema": 1, "enrollment": meta["enrollment"], "lease": None})
    new_status = rs.Status(state.status.host, state.status.revision + 1, rs.Phase.AVAILABLE,
                           state.status.binding)  # historical topology, refreshed on next list/acquire
    new_state = rs._encode(rs._State(new_status))
    replacement = {"operation.json": new_meta.hex(), "reservation.json": new_state.hex(),
                   "start-receipt.json": records["start-receipt.json"].hex(),
                   "prepare-backup.conf": records["prepare-backup.conf"].hex()}
    # A valid-but-unacquirable state is published only after the old operation
    # lease has been archived. Until then the invalid sentinel blocks every
    # dispatcher, including versions unaware of this installer journal.
    blocked_state = rs._encode(rs._State(rs.Status(state.status.host, state.status.revision,
                                 rs.Phase.SELECTING, state.status.binding), state.owner_digest))
    if journal:
        if (journal["evidence"] != evidence or journal["records"] != identities
                or journal["replacement"] != replacement or journal["blocked"] != blocked_state.hex()
                or journal["permanent"] != permanent
                or journal["history"] != history or journal["active_prefix"] != prefix):
            release_refused("pending repair differs from exact qualified release evidence")
    else:
        if authorize() is not True:
            release_refused("local RELEASE/exclusivity/official-quit confirmation required")
    again, again_ids = release_records(files, journal)
    if (again_ids != identities
            or release_evidence(old, installed, desktop, again, ref, home, uid, transport, info) != evidence
            or permanent != {str(p.relative_to(root)): record_identity(p) for p in permanent_paths}
            or history != {p.name: record_identity(p) for p in files.path.iterdir() if p.name not in excluded}
            or active_prefix(root, serial) != prefix or active(root) != (version, serial)):
        release_refused("facts changed after RELEASE confirmation")
    if journal is None:
        journal = {"schema": 1, "id": os.urandom(16).hex(), "version": version, "serial": serial,
                   "manifest": marker, "evidence": evidence, "records": identities, "replacement": replacement,
                   "blocked": blocked_state.hex(),
                   "permanent": permanent, "history": history, "active_prefix": prefix,
                   "confirmation": "RELEASE:exclusive:official_quit_issued:no_unmanaged_stream"}
        path = files.path / ("release-transition-" + journal["id"] + ".bak")
        if len(document(journal)) > lc.MAX_PRIVATE:
            release_refused("owned release journal exceeds private bound")
        create(path, document(journal))
        if unique_json(private_read(path)) != journal:
            release_refused("owned release journal publication is uncertain")
        excluded |= {path.name, "release-committed-" + journal["id"] + ".bak"}
        excluded |= {name + "." + journal["id"] + ".bak" for name in RELEASE_RECORDS}
    for name in RELEASE_RECORDS:
        archive = name + "." + journal["id"] + ".bak"
        if files.read(archive, 1024 * 1024) is None:
            if record_identity(files.path / name) != journal["records"][name]:
                release_refused("old release record changed before archival")
            files.anchor()
            os.rename(name, archive, src_dir_fd=files.fd, dst_dir_fd=files.fd)
            os.fsync(files.fd)
        if record_identity(files.path / archive) != journal["records"][name]:
            release_refused("archive differs from original release record")
        if name == "reservation.json" and files.read(name) is None:
            # During this one missing-state cut, the OLD operation lease is
            # still present: old enroll/acquire cannot pass. Publish an invalid
            # private record before changing that operation lease. Every old
            # and new dispatcher then rejects reservation reads until commit.
            if (files.read("operation.json") != records["operation.json"]
                    or record_identity(files.path / "operation.json") != journal["records"]["operation.json"]):
                release_refused("old owner was lost before blocking reservation publication")
            create(files.path / name, RELEASE_BLOCKED)
    for name in ("operation.json", "start-receipt.json", "prepare-backup.conf"):
        data = bytes.fromhex(replacement[name])
        if files.read(name, 1024 * 1024) is None:
            create(files.path / name, data)
        elif files.read(name, 1024 * 1024) != data:
            release_refused("replacement release record differs from journal")
        if name == "operation.json":
            # Only after the old lease is gone may this valid, unacquirable
            # SELECTING record replace the invalid sentinel. A cut after any
            # subsequent receipt/backup write is still readable and blocked.
            if files.read("reservation.json") == RELEASE_BLOCKED:
                files.write("reservation.json", blocked_state, limit=rs.MAX_STATE)
            elif files.read("reservation.json") != blocked_state:
                release_refused("blocked precommit reservation differs from journal")
    # Validate the *prospective* records without exposing availability. This
    # remains SELECTING with no operation lease: old/new enroll and acquire both
    # refuse, and owner actions cannot authenticate. The final reservation write
    # below is the sole observable availability commit point.
    prospective = {name: bytes.fromhex(encoded) for name, encoded in replacement.items()}
    controller = lc.HostController(installed, None, None, None, None)
    controller.files = IdentityRecords(prospective)
    if (controller.meta()["lease"] is not None or controller.meta()["enrollment"] is None
            or controller.receipt(desktop) is None
            or rs._decode(prospective["reservation.json"], controller.host).status != new_status):
        release_refused("prospective release records are inconsistent")
    again, again_ids = release_records(files, journal)
    if (again_ids != identities or again != records
            or release_evidence(old, installed, desktop, again, ref, home, uid, transport, info) != evidence
            or permanent != {str(p.relative_to(root)): record_identity(p) for p in permanent_paths}
            or history != {p.name: record_identity(p) for p in files.path.iterdir() if p.name not in excluded}
            or active_prefix(root, serial) != prefix or active(root) != (version, serial)):
        release_refused("facts changed before release commit")
    seal = files.path / ("release-committed-" + journal["id"] + ".bak")
    seal_data = document({"schema": 1, "journal_sha256": cfg.digest(private_read(
        files.path / ("release-transition-" + journal["id"] + ".bak")))})
    if files.read(seal.name) is None:
        create(seal, seal_data)
    elif files.read(seal.name) != seal_data:
        release_refused("release commitment descriptor differs")
    # A seal alone is NOT a commit: a cut here retains the blocked reservation.
    # Replay checks the actual live revision floor and tolerates later leases.
    if release_evidence(old, installed, desktop, records, ref, home, uid, transport, info) != evidence:
        release_refused("host changed before availability publication")
    files.write("reservation.json", new_state, limit=rs.MAX_STATE)
    return True


def validate_transition_version(path, marker):
    """Only a journal-bound, exact subset may resume an interrupted version."""
    private_directory(path)
    names = {p.name for p in path.iterdir()}
    if names - (set(marker["files"]) | {".owned.json"}):
        identity_refused("interrupted version has unknown publications")
    for name in names - {".owned.json"}:
        if cfg.digest(private_read(path / name)) != marker["files"][name]:
            identity_refused("interrupted version differs from the bound manifest")
    if ".owned.json" in names:
        validate_version(path, marker)


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
            platform=None, reconcile_host_identity=False, authorize_rebind=interactive_rebind,
            reconcile_owned_release=False, authorize_release=interactive_release):
    if (platform or sys.platform) != "linux":
        fail("platform_runtime_unavailable", "Windows/macOS lifecycle publication is pending; no Linux settings were created")
    if reconcile_host_identity and reconcile_owned_release:
        fail("installation_unavailable", "choose exactly one local reconciliation ceremony")
    if reconcile_owned_release and publish:
        release_refused("owned release repair never publishes a system shim")
    uid = os.geteuid()
    if uid == 0 or os.getuid() != uid:
        fail("installer_identity", "run as the Sunshine desktop user, never root/sudo or a mismatched UID")
    home = (Path(pwd.getpwuid(uid).pw_dir) if home is None else Path(home)).resolve(strict=True)
    if not home.is_absolute() or home.stat().st_uid != uid:
        fail("installer_identity", "NSS home must belong to the invoking desktop user")
    source = Path(source or Path(__file__).resolve().parent)
    transport = transport or rt.LocalTransport()
    ref = q.reference(metadata, package, ubuntu, architecture, installed_version, transport)
    if reconcile_owned_release:
        def initial_idle_info(address, port):
            endpoint = info(address, port)
            if (type(endpoint) is not lc.Endpoint or endpoint.state != "FREE"
                    or type(endpoint.currentgame) is not int or endpoint.currentgame != 0):
                release_refused("initial qualification observed a busy or unknown endpoint")
            return endpoint
        installed, desktop = discover(ref, home, uid, transport, initial_idle_info)
    else:
        installed, desktop = discover(ref, home, uid, transport, info)
    root = home / RELATIVE_ROOT
    ready = check_shim()  # Reject foreign global publications BEFORE private writes.
    # HOME/.local/share ancestors may be normal parent links, but the controller
    # publication itself must be canonical. No env-selected alternate state root.
    if (reconcile_host_identity or reconcile_owned_release) and (not root.exists() or root.is_symlink()):
        fail("reconciliation_refused", "local reconciliation requires an existing installed controller")
    root.parent.mkdir(parents=True, exist_ok=True)
    if root.parent.resolve(strict=True) != root.parent:
        fail("publication_unsafe", "private installation parents below canonical HOME must not redirect the fixed lookup")
    fresh = not root.exists() and not root.is_symlink()
    if fresh and (reconcile_host_identity or reconcile_owned_release):
        fail("reconciliation_refused", "local reconciliation requires an existing installed controller")
    if fresh:
        make_directory(root)
    if reconcile_host_identity or reconcile_owned_release:
        private_read(root / ".install.lock", 1)  # Never recreate a missing permanent lock.
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
        # Do not create a missing operation lock on upgrades or erase interrupted state.
        private_read(root / "state/operation.lock", 1)
        with lc.PrivateFiles(root / "state").locked() as files:
            bound_versions = {}
            if reconcile_host_identity:
                for path in files.path.glob("identity-transition-*.bak"):
                    value = identity_journal(path)
                    bound = value["new_manifest"]
                    bound_versions[bound["version"]] = bound
            if reconcile_owned_release:
                for path in files.path.glob("release-transition-*.bak"):
                    value = release_journal(path)
                    bound = value["manifest"]
                    bound_versions[bound["version"]] = bound
            for path in (root / "versions").iterdir():
                if path.name in bound_versions:
                    validate_transition_version(path, bound_versions[path.name])
                else:
                    validate_version(path)
            data = document(settings_document(installed))
            if not (reconcile_host_identity or reconcile_owned_release):
                status = validate_state(installed, desktop, files)  # Keep strict host_mismatch.
            if old is not None:
                old_data = private_read(root / "versions" / old / "host-settings.json", 8192)
                old_obj, new_obj = unique_json(old_data), unique_json(data)
                old_obj["host_uniqueid"] = lc.canonical_uuid(old_obj["host_uniqueid"])
                if old_obj == new_obj:
                    data = old_data  # Preserve valid formatting/UUID spelling on ordinary updates.
                elif not (reconcile_host_identity or reconcile_owned_release):
                    fail("settings_conflict", "existing settings/routes/build differ from fresh qualification; preserved without automatic migration")
            payload = {name: source_read(source / name) for name in SOURCES}
            payload["host-settings.json"] = data
            hashes = {name: cfg.digest(content) for name, content in payload.items()}
            # UUID-only settings changes must identify a DISTINCT immutable version.
            version = f"v{RELEASE}-" + cfg.digest(document(hashes))
            marker = {"schema": 1, "release": RELEASE, "version": version, "files": hashes}
            target = root / "versions" / version
            resuming = version in bound_versions
            if target.exists() or target.is_symlink():
                if resuming:
                    validate_transition_version(target, marker)
                else:
                    validate_version(target, marker)
            if reconcile_host_identity:
                reconcile_identity(root, old, serial, installed, desktop, files, ref, home, uid,
                                   transport, info, marker, authorize_rebind)
                status = validate_state(installed, desktop, files)
            elif reconcile_owned_release:
                repaired = repair_owned_release(root, old, serial, installed, desktop, files, ref, home, uid,
                                                transport, info, marker, authorize_release)
                status = rs.Phase.AVAILABLE.value if repaired else validate_state(installed, desktop, files)
            if not target.exists():
                make_directory(target)
            for name, content in payload.items():
                if not (target / name).exists():
                    create(target / name, content)
            if not (target / ".owned.json").exists():
                # Marker LAST. Only the explicit UUID-bound journal permits
                # resuming exact partial bytes; ordinary upgrades remain strict.
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
    parser.add_argument("--reconcile-host-identity", action="store_true",
                        help="explicit local REBIND ceremony for a qualified identity-changing prelaunch restart")
    parser.add_argument("--reconcile-owned-release", action="store_true",
                        help="explicit local RELEASE ceremony for a qualified owner_lost releasing lease")
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
                         args.installed_version, publish=args.publish_shim,
                         reconcile_host_identity=args.reconcile_host_identity,
                         reconcile_owned_release=args.reconcile_owned_release)
        print(f"Private display controller: {result.root}; status={result.status}. Settings/state preserved on updates.")
        if not result.rpc_ready:
            print("Shim NOT PUBLISHED: noninteractive SSH RPC is not ready. Locally authorize the new system-wide /usr/bin/quick-deploy-display-control via commands/install-display-control.sh --publish-shim.", file=sys.stderr)
            return 1
        print("Fixed RPC shim ready: /usr/bin/quick-deploy-display-control (SSH PATH must include /usr/bin).")
        if result.status == "UNENROLLED":
            print("UNENROLLED: no stream adopted and no StartReceipt issued. Complete official Moonlight PIN pairing before any controlled preparation restart; when idle/exclusive, run quick-deploy-display-control --enroll from this user's interactive host terminal.")
        return 0
    except Refusal as exc:
        print(f"Display controller refused [{exc.code}]: {exc.detail}", file=sys.stderr)
        return 1
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        print("Display controller installation unavailable; existing publications/state retained. No vendor/service changes were performed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
