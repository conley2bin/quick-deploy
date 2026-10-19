#!/usr/bin/env python3
"""User-owned companion publication and explicit attended local recoveries.

Ordinary installation never updates/restarts/enrolls Sunshine. The distinct
RETIRE command alone authorizes one qualified local STOP/START. The shell owns
reference download/verification; imports do not inspect the host or HOME.
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
import uuid

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


# This separate ENROLL transition retires only positively unlaunched, failed
# preparations. It grants idle authority, never capture/launch authority. Keep
# lifecycle's RPC enrollment guard unchanged and do not nest its operation lock.
UNLAUNCHED_RECORDS = ("reservation.json", "operation.json", "start-receipt.json", "prepare-backup.conf")


def unlaunched_refused(reason):
    fail("unlaunched_retirement_refused", "Unlaunched preparation retirement refused: " + reason)


def retirement_generation(value, uid):
    try:
        if type(value) is not dict or set(value) != set(rt.Generation.__dataclass_fields__):
            raise ValueError("fields")
        generation = rt.Generation(**value)
        boot = generation.boot_id.replace("-", "")
        if (not re.fullmatch("[0-9a-f]{32}", boot)
                or generation.boot_id not in (boot, str(uuid.UUID(boot)))
                or type(generation.invocation_id) is not str
                or not re.fullmatch("[0-9a-f]{32}", generation.invocation_id)
                or generation.unit not in (rt.UNIT, rt.ALIAS) or type(generation.uid) is not int
                or generation.uid != uid or any(type(v) is not int or not 0 < v < (1 << 63)
                    for v in (generation.pid, generation.start_ticks, generation.start_monotonic_usec))):
            raise ValueError("birth")
        return replace(generation, boot_id=boot)
    except (ValueError, TypeError, AttributeError):
        unlaunched_refused("invalid exact process birth")


def retired_generation(generation, current, transport):
    """Positive boot retirement OR exact dead/absent/reused birth + newer unit."""
    if generation.unit != current.unit or generation.uid != current.uid:
        unlaunched_refused("retired generation belongs to another unit/user")
    if generation.boot_id != current.boot_id:
        return {"kind": "previous_boot"}
    if (generation.invocation_id == current.invocation_id
            or current.start_monotonic_usec <= generation.start_monotonic_usec):
        unlaunched_refused("no qualified newer invocation retires the old birth")
    try:
        data = transport.read(Path("/proc") / str(generation.pid) / "stat", limit=4096)
    except FileNotFoundError:
        return {"kind": "absent_old_process", "new_invocation": current.invocation_id}
    except OSError:
        unlaunched_refused("old process retirement is inaccessible")
    try:
        if type(data) is not bytes or len(data) > 4096:
            raise ValueError("bound")
        text = data.decode("ascii")
        end = text.rfind(")")
        fields = text[end + 2:].split() if end > 0 else []
        if (not text.startswith(str(generation.pid) + " (") or len(fields) < 20
                or not re.fullmatch("[1-9][0-9]*", fields[19]) or len(fields[0]) != 1
                or fields[0] not in "RSDZTtWXxKPI"):
            raise ValueError("stat")
        ticks = int(fields[19])
        if ticks == generation.start_ticks and fields[0] not in ("Z", "X", "x"):
            unlaunched_refused("the exact old process birth remains alive")
        return {"kind": "pid_reused" if ticks != generation.start_ticks else "dead_process",
                "start_ticks": ticks, "state": fields[0]}
    except (ValueError, UnicodeError):
        unlaunched_refused("invalid old process birth evidence")


def retirement_receipt(controller, desktop, current, lease, enrollment, before, after, transport, *, committed=False):
    """Pending startup authority and committed read-only preservation are distinct."""
    if controller.files.read("start-receipt.json") is None:
        return {"kind": "absent"}, None
    receipt = controller.receipt(desktop)  # Schema only; explicit qualification follows.
    envelope = controller.files.document("start-receipt.json")
    if receipt is None:
        unlaunched_refused("receipt Desktop context differs")
    try:
        issuance = envelope["issuance"]
        issued = retirement_generation(asdict(receipt.generation), current.generation.uid)
        previous = retirement_generation(issuance["previous_generation"], current.generation.uid)
        def fingerprint(value, uid, executable=False):
            ident = value.identity
            if (type(value.sha256) is not str or not re.fullmatch("[0-9a-f]{64}", value.sha256)
                    or any(type(v) is not int or v < 0 for v in asdict(ident).values())
                    or not stat.S_ISREG(ident.mode) or ident.uid != uid or ident.mode & 0o022
                    or (executable and not ident.mode & 0o111)):
                raise ValueError("fingerprint")
        fingerprint(receipt.config, current.generation.uid)
        fingerprint(receipt.executable, 0, True)
        if (receipt.host_uniqueid != controller.host.uuid
                or receipt.qualified_build != controller.settings.runtime.build
                or receipt.executable.sha256 != current.binary.file.sha256
                or receipt.executable.identity.size != current.binary.file.identity.size
                or receipt.unit_digest != current.unit.ownership_digest
                or receipt.config_path != current.config.path or receipt.config_root != current.config.root
                or receipt.manager_environment_digest != current.config.manager_environment_digest
                or type(issuance["enrollment_revision"]) is not int
                or not 1 <= issuance["enrollment_revision"] <= enrollment["revision"]
                or previous.unit != issued.unit or previous.boot_id != issued.boot_id
                or previous.invocation_id == issued.invocation_id
                or previous.start_monotonic_usec >= issued.start_monotonic_usec):
            raise ValueError("context")
        intent = lease is not None and issuance["operation"] == lease["restart_id"]
        if intent:
            if (previous != retirement_generation(lease["previous_generation"], issued.uid)
                    or issuance["enrollment_revision"] != enrollment["revision"]
                    or receipt.config.sha256 != cfg.digest(after)
                    or receipt.config.identity.size != len(after)):
                raise ValueError("intent")
            kind = "this_restart"
        else:
            # Full known bytes and stable context explain preservation, never
            # current authority. Boot inequality cannot establish chronology.
            if (receipt.config.sha256 not in (cfg.digest(before), cfg.digest(after))
                    or receipt.config.identity.size != (len(before) if receipt.config.sha256 == cfg.digest(before) else len(after))):
                raise ValueError("historical config")
            kind = "independent_history" if committed else "retired_history"
            if not committed:
                reserved = retirement_generation(lease["previous_generation"], issued.uid)
                if issued == current.generation or previous == reserved:
                    raise ValueError("unrelated current issuer contradicts pending intent")
                if issued.boot_id == reserved.boot_id:
                    if issued != reserved and (issued.invocation_id == reserved.invocation_id
                            or issued.start_monotonic_usec >= reserved.start_monotonic_usec):
                        raise ValueError("receipt is not prior to the reserved birth")
                    kind = "prior_history"  # An actual same-boot ordering proof.
                elif issued.boot_id == current.generation.boot_id:
                    raise ValueError("unrelated post-reboot issuer contradicts pending intent")
                # A third boot proves only retired/noncurrent preservation.
                # Do not invent its order relative to the reserved boot.
    except (ValueError, KeyError, TypeError, AttributeError):
        unlaunched_refused("receipt lacks qualified host/build/unit/root/config/Desktop/issuance context")
    matching = False
    try:
        controller.runtime._match_receipt(receipt, current)
        matching = True
    except Refusal as exc:
        if exc.code != "receipt_mismatch":
            raise
    if matching and not intent:
        if not committed:
            unlaunched_refused("an unrelated current receipt cannot authorize a pending preparation")
        kind = "independent_current"  # Read-only replay; no new authority.
    retirement = None
    if not matching:
        # A same-generation after receipt can remain after positive disk CAS
        # rollback; it is historical for disk ownership, not a loaded-B claim.
        rollback_split = (intent and issued == current.generation
                          and lease["stage"] == "rolled_back_requires_reconciliation")
        if not rollback_split:
            retirement = retired_generation(issued, current.generation, transport)
    authority_receipt = receipt if matching and intent and not committed else None
    return {"kind": kind, "current_match": matching, "retirement": retirement}, authority_receipt


def unlaunched_evidence(installed, records, transport, info):
    controller = lc.HostController(installed, rt.LinuxRuntime(installed.runtime, transport), None, info, None)
    controller.files = IdentityRecords(records)
    meta = controller.meta()
    state = rs._decode(records["reservation.json"], controller.host)
    status, lease = state.status, meta["lease"]
    fields = {"owner_digest", "stage", "selection_desktop", "display_id", "selector", "topology",
              "desktop_digest", "before_digest", "after_digest", "restart_id", "previous_generation"}
    if (status.phase is not rs.Phase.RECONCILIATION_REQUIRED
            or status.uncertainty is not rs.Uncertainty.RUNTIME_CHANGED
            or status.launch_attempted or status.active_observed or status.desktop is not None
            or status.binding is None or state.owner_digest is None or meta["enrollment"] is None
            or meta["enrollment"]["revision"] >= status.revision or status.revision >= rs.MAX_REVISION
            or lease is None or set(lease) != fields or lease["owner_digest"] != state.owner_digest
            or lease["stage"] not in ("restart_intent", "rolled_back_requires_reconciliation")):
        unlaunched_refused("exact enrolled, owned, unlaunched runtime_changed preparation required")
    try:
        previous = retirement_generation(lease["previous_generation"], installed.runtime.uid)
        bound = status.binding.generation
        if (lc.service_generation(previous) != replace(bound, boot_id=bound.boot_id.replace("-", ""))
                or lc.canonical_uuid(lease["restart_id"]) != lease["restart_id"]
                or any(type(lease[k]) is not str or not re.fullmatch("[0-9a-f]{64}", lease[k])
                       for k in ("before_digest", "after_digest", "topology", "desktop_digest"))
                or lease["before_digest"] != status.binding.config_digest
                or lease["topology"] != status.binding.topology_digest
                or type(lease["selector"]) is not str or not 1 <= len(lease["selector"]) <= 80
                or any(ord(c) < 32 or ord(c) == 127 for c in lease["selector"])
                or type(lease["display_id"]) is not str or not re.fullmatch(
                    r"kms:card[0-9]+:" + re.escape(lease["selector"]), lease["display_id"])):
            raise ValueError("intent")
    except (ValueError, TypeError, AttributeError):
        unlaunched_refused("restart intent does not match reserved generation/config/topology")
    before = records["prepare-backup.conf"]
    after = cfg.select_output(before, lease["selector"])
    if (cfg.digest(before) != lease["before_digest"] or cfg.digest(after) != lease["after_digest"]
            or cfg.get_settings(before)["capture"] != "kms"):
        unlaunched_refused("backup/selection reconstruction does not match KMS before/after context")
    disk, _ = cfg.load(installed.runtime.config_file)
    if ((lease["stage"] == "restart_intent" and disk != after)
            or (lease["stage"] == "rolled_back_requires_reconciliation" and (before == after or disk != before))):
        unlaunched_refused("disk is neither the exact planned after-config nor positive CAS rollback")
    facts = cfg.get_settings(disk)
    owned = controller.sample_ownership({"address": facts["bind_address"], "port": installed.base_port}, use_receipt=False)
    current, desktop = owned.current, owned.desktop
    if (owned.content != disk or owned.endpoint.state != "FREE" or owned.endpoint.currentgame != 0
            or not current.binary.supported_build or current.host_uniqueid != installed.host_uniqueid
            or current.binary.file.sha256 != installed.runtime.build.executable_sha256
            or current.generation.unit != previous.unit or current.generation.uid != previous.uid
            or desktop != controller.selection_desktop(lease) or desktop.definition_digest != lease["desktop_digest"]
            or any(lc.inspected_desktop(installed, content, current.config.root) != desktop for content in (before, after))):
        unlaunched_refused("fresh qualified runtime/endpoint/Desktop/context differs")
    retirement = retired_generation(previous, current.generation, transport)
    receipt_context, receipt = retirement_receipt(controller, desktop, current, lease, meta["enrollment"], before, after, transport)
    if receipt is not None:
        checked = controller.runtime.observe(receipt=receipt)
        if lc.authority(checked) != lc.authority(current):
            unlaunched_refused("receipt-bound current runtime changed")
    elif (current.binary.process_exe_status != "verified"
            or current.config.process_environment_status != "verified"):
        unlaunched_refused("current executable/config-root require verified proc access without a matching receipt")
    again = controller.sample_ownership({"address": facts["bind_address"], "port": installed.base_port}, use_receipt=False)
    if (lc.authority(again.current) != lc.authority(current) or again.desktop != desktop
            or again.content != disk or again.endpoint != owned.endpoint
            or again.current.binary != current.binary
            or again.current.config.process_environment_status != current.config.process_environment_status):
        unlaunched_refused("runtime/config/Desktop/endpoint changed during observation")
    # No reconstructed A fingerprint is substituted for actual disk B, and no
    # historical receipt is consumed as current loaded/capture authority.
    return {"reservation_revision": status.revision, "stage": lease["stage"], "owner_digest": state.owner_digest,
            "restart_id": lease["restart_id"], "previous_generation": asdict(previous), "retirement": retirement,
            "generation": asdict(current.generation), "unit": asdict(current.unit), "config": asdict(current.config),
            "binary": asdict(current.binary), "desktop": asdict(desktop), "receipt_context": receipt_context,
            "endpoint": {"uuid": installed.host_uniqueid, "state": "FREE", "currentgame": 0}}


def unlaunched_records(files, journal=None):
    records, identities = {}, {}
    expected = journal["records"] if journal else None
    for name in UNLAUNCHED_RECORDS:
        archive = name + "." + journal["id"] + ".bak" if journal else None
        archived = files.read(archive, 1024 * 1024) if archive else None
        live = files.read(name, 1024 * 1024)
        if expected is not None and name not in expected:
            if archived is not None or live is not None:
                unlaunched_refused("originally absent receipt appeared")
            continue
        if archived is not None:
            allowed = {bytes.fromhex(journal["replacement"][name])}
            if name == "reservation.json":
                allowed |= {RELEASE_BLOCKED, bytes.fromhex(journal["blocked"])}
            if live is not None and live not in allowed:
                unlaunched_refused("unexpected live replacement record")
            path, data = files.path / archive, archived
        else:
            path, data = files.path / name, live
        if data is None:
            if name != "start-receipt.json" or expected is not None:
                unlaunched_refused("original preparation record missing")
            continue
        records[name], identities[name] = data, record_identity(path)
    if journal and identities != expected:
        unlaunched_refused("original preparation records differ from journal")
    return records, identities


def unlaunched_journal(path):
    try:
        value = unique_json(private_read(path, lc.MAX_PRIVATE))
        required = {"schema", "id", "version", "serial", "manifest", "evidence", "records", "replacement",
                    "blocked", "permanent", "history", "active_prefix", "confirmation"}
        if (type(value) is not dict or set(value) != required or type(value["schema"]) is not int or value["schema"] != 1
                or type(value["id"]) is not str or not re.fullmatch("[0-9a-f]{32}", value["id"])
                or path.name != "unlaunched-transition-" + value["id"] + ".bak"
                or type(value["serial"]) is not int or not 1 <= value["serial"] <= 512
                or value["confirmation"] != "ENROLL:exclusive:no_unmanaged_stream:no_admin_edits"
                or type(value["evidence"]) is not dict
                or type(value["evidence"]["reservation_revision"]) is not int
                or not 1 <= value["evidence"]["reservation_revision"] < rs.MAX_REVISION
                or type(value["records"]) is not dict
                or not {"reservation.json", "operation.json", "prepare-backup.conf"} <= set(value["records"])
                or set(value["records"]) - set(UNLAUNCHED_RECORDS)
                or type(value["replacement"]) is not dict or set(value["replacement"]) != set(value["records"])
                or any(type(v) is not str or not re.fullmatch("(?:[0-9a-f]{2})+", v) for v in value["replacement"].values())
                or type(value["blocked"]) is not str or not re.fullmatch("(?:[0-9a-f]{2})+", value["blocked"])
                or type(value["manifest"]) is not dict
                or set(value["manifest"]) != {"schema", "release", "version", "files"}
                or type(value["manifest"]["schema"]) is not int or value["manifest"]["schema"] != 1
                or type(value["manifest"]["release"]) is not int or value["manifest"]["release"] != RELEASE
                or value["manifest"]["version"] != value["version"]
                or type(value["manifest"]["files"]) is not dict
                or set(value["manifest"]["files"]) != set(SOURCES) | {"host-settings.json"}
                or any(type(v) is not str or not re.fullmatch("[0-9a-f]{64}", v) for v in value["manifest"]["files"].values())
                or value["version"] != f"v{RELEASE}-" + cfg.digest(document(value["manifest"]["files"]))
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
                    raise ValueError("identity")
        return value
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError, Refusal):
        unlaunched_refused("invalid durable preparation-retirement journal")


def unlaunched_identity(path, data, expected=None):
    """Pin intended bytes and a stable private-file identity, never a fresh hash alone."""
    if private_read(path) != data:
        unlaunched_refused("intended publication bytes differ")
    identity = record_identity(path)
    if (identity["sha256"] != cfg.digest(data) or identity["size"] != len(data)
            or (expected is not None and identity != expected)
            or private_read(path) != data or record_identity(path) != identity):
        unlaunched_refused("intended publication identity differs")
    return identity


def unlaunched_commitment(journal, records, installed):
    """Derive the promise from the actual immutable original reservation, not the journal's floor."""
    controller = lc.HostController(installed, None, None, None, None)
    controller.files = IdentityRecords(records)
    meta = controller.meta()
    original = rs._decode(records["reservation.json"], controller.host)
    status, lease = original.status, meta["lease"]
    if (status.phase is not rs.Phase.RECONCILIATION_REQUIRED
            or status.uncertainty is not rs.Uncertainty.RUNTIME_CHANGED
            or status.desktop is not None or status.launch_attempted or status.active_observed
            or original.owner_digest is None or status.binding is None
            or meta["enrollment"] is None or lease is None or lease["owner_digest"] != original.owner_digest
            or lease["stage"] not in ("restart_intent", "rolled_back_requires_reconciliation")
            or not meta["enrollment"]["revision"] < status.revision < rs.MAX_REVISION
            or journal["evidence"]["reservation_revision"] != status.revision):
        unlaunched_refused("journal contradicts the original reservation revision/lifecycle")
    try:
        generation = retirement_generation(journal["evidence"]["generation"], installed.runtime.uid)
        disk_digest = journal["evidence"]["config"]["file"]["sha256"]
        before = records["prepare-backup.conf"]
        after = cfg.select_output(before, lease["selector"])
        if (cfg.digest(before) != lease["before_digest"] or cfg.digest(after) != lease["after_digest"]
                or status.binding.config_digest != lease["before_digest"]
                or disk_digest != (lease["after_digest"] if lease["stage"] == "restart_intent" else lease["before_digest"])
                or (lease["stage"] == "rolled_back_requires_reconciliation" and before == after)):
            raise ValueError("config promise")
        binding = rs.RuntimeBinding(status.host, lc.service_generation(generation), status.binding.topology_digest, disk_digest)
        available = rs._encode(rs._State(rs.Status(status.host, status.revision + 1, rs.Phase.AVAILABLE, binding)))
        blocked = rs._encode(rs._State(rs.Status(status.host, status.revision, rs.Phase.SELECTING, status.binding),
                                      original.owner_digest))
        replacement = {name: data.hex() for name, data in records.items()}
        replacement["operation.json"] = document({"schema": 1, "enrollment": meta["enrollment"], "lease": None}).hex()
        replacement["reservation.json"] = available.hex()
        if journal["replacement"] != replacement or journal["blocked"] != blocked.hex():
            raise ValueError("compound promise")
    except (ValueError, KeyError, TypeError, AttributeError):
        unlaunched_refused("journal promise differs from original enrollment/config and exact R+1")
    return status.revision + 1, available, blocked


def unlaunched_live(files, replacement, blocked, expected=None):
    """Intermediate absence is not readiness: require the complete actual blocked baseline."""
    identities, records = {}, {}
    for name in UNLAUNCHED_RECORDS:
        intended = blocked if name == "reservation.json" else (
            bytes.fromhex(replacement[name]) if name in replacement else None)
        live = files.read(name, 1024 * 1024)
        if live != intended:
            unlaunched_refused("complete live replacement baseline is missing or changed")
        if intended is not None:
            records[name] = live
            identities[name] = unlaunched_identity(files.path / name, intended,
                                                   expected.get(name) if expected is not None else None)
    if expected is not None and identities != expected:
        unlaunched_refused("live replacement identity set differs")
    return records, identities


def unlaunched_descriptor(files, journal, journal_data, journal_identity):
    """A derived promise binds intended journal and blocked-live identities.

    Descriptor identity is pinned within an invocation; cross-invocation replay
    requires exact typed content/semantics, not an invented self-inode witness.
    """
    name = "unlaunched-committed-" + journal["id"] + ".bak"
    data = files.read(name)
    if data is None:
        return None, None, None
    try:
        value = unique_json(data)
        if (type(value) is not dict or set(value) != {"schema", "journal_sha256", "journal_identity", "live_records"}
                or type(value["schema"]) is not int or value["schema"] != 1
                or value["journal_sha256"] != cfg.digest(journal_data)
                or type(value["journal_identity"]) is not dict
                or value["journal_identity"] != journal_identity
                or any(type(v) is not int or v < 0 for k, v in value["journal_identity"].items() if k != "sha256")
                or type(value["live_records"]) is not dict or set(value["live_records"]) != set(journal["replacement"])):
            raise ValueError("descriptor")
        for record_name, identity in value["live_records"].items():
            intended = bytes.fromhex(journal["blocked"] if record_name == "reservation.json" else journal["replacement"][record_name])
            if (type(identity) is not dict
                    or set(identity) != {"sha256", "device", "inode", "mode", "uid", "size", "mtime_ns"}
                    or identity["sha256"] != cfg.digest(intended) or identity["size"] != len(intended)
                    or any(type(v) is not int or v < 0 for k, v in identity.items() if k != "sha256")
                    or not stat.S_ISREG(identity["mode"]) or stat.S_IMODE(identity["mode"]) != 0o600
                    or identity["uid"] != os.geteuid()):
                raise ValueError("live identity")
        if data != document(value):
            raise ValueError("descriptor bytes")
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
        unlaunched_refused("descriptor differs from intended journal and live baseline")
    identity = unlaunched_identity(files.path / name, data)
    return data, identity, value["live_records"]


def retire_unlaunched(root, version, serial, installed, files, transport, info, marker, authorize):
    """Owned-release ordering; independent ENROLL proof and actual compound readiness."""
    anchors = {p: private_directory(p) for p in (root, root / "versions", root / "active", files.path,
                                                root / "versions" / version)}
    permanent_paths = (root / ".install.lock", files.path / "operation.lock", files.path / "reservation.lock")
    if (private_read(permanent_paths[0], 1) != b"\0" or private_read(permanent_paths[1], 1) != b""
            or private_read(permanent_paths[2], 1) != b"\0"):
        unlaunched_refused("permanent lock bytes differ")
    permanent = {str(p.relative_to(root)): record_identity(p) for p in permanent_paths}
    journals = [unlaunched_journal(p) for p in files.path.glob("unlaunched-transition-*.bak")]
    journals.sort(key=lambda item: item["evidence"]["reservation_revision"])
    revisions = [item["evidence"]["reservation_revision"] for item in journals]
    if len(set(revisions)) != len(revisions):
        unlaunched_refused("duplicate retirement revision")
    journal_pins, descriptor_pins, commitments = {}, {}, {}
    for index, item in enumerate(journals):
        path = files.path / ("unlaunched-transition-" + item["id"] + ".bak")
        intended = document(item)
        journal_identity = unlaunched_identity(path, intended)
        journal_pins[item["id"]] = intended, journal_identity
        if (item["serial"] > serial or active_prefix(root, item["serial"]) != item["active_prefix"]
                or any(record_identity(files.path / name) != identity for name, identity in item["history"].items())
                or permanent != item["permanent"]):
            unlaunched_refused("original history/active prefix/permanent locks differ")
        seal = files.read("unlaunched-committed-" + item["id"] + ".bak")
        originals = {}
        for name, identity in item["records"].items():
            archive = name + "." + item["id"] + ".bak"
            data = files.read(archive, 1024 * 1024)
            if data is not None:
                if record_identity(files.path / archive) != identity:
                    unlaunched_refused("original archive differs")
            elif (index != len(journals) - 1 or seal is not None or files.read(name, 1024 * 1024) is None
                  or record_identity(files.path / name) != identity):
                unlaunched_refused("original preparation record differs")
            else:
                data = files.read(name, 1024 * 1024)
            originals[name] = data
        if any(name not in item["records"] and files.read(name + "." + item["id"] + ".bak", 1024 * 1024) is not None
               for name in UNLAUNCHED_RECORDS):
            unlaunched_refused("journal omitted an existing original archive")
        # Even a self-consistently altered journal/descriptor cannot redefine
        # the floor: R and preserved enrollment/bytes come from the originals.
        commitments[item["id"]] = unlaunched_commitment(item, originals, installed)
        descriptor_pins[item["id"]] = unlaunched_descriptor(files, item, intended, journal_identity)
        if descriptor_pins[item["id"]][0] != seal:
            unlaunched_refused("descriptor changed during replay classification")
        if index < len(journals) - 1 and (seal is None or revisions[index + 1] < commitments[item["id"]][0]):
            unlaunched_refused("prior retirement is unsealed")
    if any(p.name not in {"unlaunched-committed-" + item["id"] + ".bak" for item in journals}
           for p in files.path.glob("unlaunched-committed-*.bak")):
        unlaunched_refused("descriptor has no matching intended journal")

    def check_promises():
        for key, (data, identity) in journal_pins.items():
            unlaunched_identity(files.path / ("unlaunched-transition-" + key + ".bak"), data, identity)
        for key, (data, identity, _) in descriptor_pins.items():
            path = files.path / ("unlaunched-committed-" + key + ".bak")
            if data is None:
                if files.read(path.name) is not None:
                    unlaunched_refused("unintended descriptor appeared")
            else:
                unlaunched_identity(path, data, identity)

    def check_installation():
        if (permanent != {str(p.relative_to(root)): record_identity(p) for p in permanent_paths}
                or any(private_directory(p) != identity for p, identity in anchors.items())
                or active(root) != (version, serial) or validate_version(root / "versions" / version) != marker):
            unlaunched_refused("installation/active source or permanent locks changed")

    journal = journals[-1] if journals else None
    if journal:
        floor, promised_raw, blocked = commitments[journal["id"]]
        seal_data, _, sealed_live = descriptor_pins[journal["id"]]
        live = files.read("reservation.json", rs.MAX_STATE)
        pending = live is None or live in (RELEASE_BLOCKED, blocked)
        if not pending:
            current = rs._decode(live, rs.HostIdentity(installed.host_uniqueid, installed.base_port))
            if current.status.revision < floor:
                if (files.read("reservation.json." + journal["id"] + ".bak") is not None
                        or record_identity(files.path / "reservation.json") != journal["records"]["reservation.json"]):
                    unlaunched_refused("reservation rollback below original R+1 floor")
                pending = True
            elif seal_data is None or (current.status.revision == floor and live != promised_raw):
                unlaunched_refused("reservation contradicts sealed commit")
        # Classify actual commit BEFORE original precommit live bytes/source.
        # A committed descriptor does NOT freeze later legitimate live records.
        if not pending:
            disk = cfg.load(installed.runtime.config_file)[0]
            desktop = lc.inspected_desktop(installed, disk, installed.runtime.config_file.parent)
            validate_state(installed, desktop, files)
            if current.status.phase is rs.Phase.RECONCILIATION_REQUIRED and unique_json(files.read("operation.json"))["lease"] is None:
                check_promises()
                check_installation()
                return None  # Keep ordinary explicit lease-free onboarding.
            if current.status.phase is not rs.Phase.RECONCILIATION_REQUIRED:
                controller = lc.HostController(installed, rt.LinuxRuntime(installed.runtime, transport), None, info, None)
                controller.files = files
                context_lease, context_enrollment, before, after = None, controller.meta()["enrollment"], disk, disk
                live_receipt = files.read("start-receipt.json")
                for item in journals:
                    if (live_receipt is not None and "start-receipt.json" in item["records"]
                            and live_receipt == files.read("start-receipt.json." + item["id"] + ".bak")):
                        archived_meta = unique_json(files.read("operation.json." + item["id"] + ".bak"))
                        context_lease, context_enrollment = archived_meta["lease"], archived_meta["enrollment"]
                        before = files.read("prepare-backup.conf." + item["id"] + ".bak", 1024 * 1024)
                        after = cfg.select_output(before, context_lease["selector"])
                        break
                retirement_receipt(controller, desktop, controller.runtime.observe(), context_lease,
                                   context_enrollment, before, after, transport, committed=True)
                check_promises()
                check_installation()
                return response(True, action="enroll", **lc.HostController.status_fields(current.status))
            journal = None
        elif (journal["version"], journal["serial"], journal["manifest"]) != (version, serial, marker):
            unlaunched_refused("pending retirement source/settings changed")
        elif seal_data is not None:
            # Once readiness was sealed there is no legitimate missing-copy
            # cut. Do not heal a deleted/changed live record behind the seal.
            unlaunched_live(files, journal["replacement"], blocked, sealed_live)
    records, identities = unlaunched_records(files, journal)
    prefix = active_prefix(root, serial)
    excluded = set(UNLAUNCHED_RECORDS) | {"operation.lock", "reservation.lock"}
    if journal:
        excluded |= {"unlaunched-transition-" + journal["id"] + ".bak", "unlaunched-committed-" + journal["id"] + ".bak"}
        excluded |= {name + "." + journal["id"] + ".bak" for name in journal["records"]}
    def history():
        result = {}
        for path in files.path.iterdir():
            if path.name not in excluded:
                if not path.name.endswith(".bak"):
                    unlaunched_refused("unknown/incomplete private publication")
                result[path.name] = record_identity(path)
        return result
    original_history = history()
    evidence = unlaunched_evidence(installed, records, transport, info)
    state = rs._decode(records["reservation.json"], rs.HostIdentity(installed.host_uniqueid, installed.base_port))
    meta = unique_json(records["operation.json"])
    # Historical topology grants no screen authorization. Ordinary list/acquire
    # refresh native metadata; generation and ACTUAL disk digest are fresh.
    binding = rs.RuntimeBinding(state.status.host, lc.service_generation(rt.Generation(**evidence["generation"])),
                                state.status.binding.topology_digest, evidence["config"]["file"]["sha256"])
    new_status = rs.Status(state.status.host, state.status.revision + 1, rs.Phase.AVAILABLE, binding)
    new_state = rs._encode(rs._State(new_status))
    replacement = {name: data.hex() for name, data in records.items()}
    replacement["operation.json"] = document({"schema": 1, "enrollment": meta["enrollment"], "lease": None}).hex()
    replacement["reservation.json"] = new_state.hex()
    blocked = rs._encode(rs._State(rs.Status(state.status.host, state.status.revision, rs.Phase.SELECTING,
                                           state.status.binding), state.owner_digest))
    if journal:
        if (journal["evidence"] != evidence or journal["records"] != identities or journal["replacement"] != replacement
                or journal["blocked"] != blocked.hex() or journal["permanent"] != permanent
                or journal["history"] != original_history or journal["active_prefix"] != prefix):
            unlaunched_refused("pending retirement facts differ")
    elif authorize() is not True:
        unlaunched_refused("local exclusive ENROLL confirmation required")
    ready_ids = descriptor_pins[journal["id"]][2] if journal else None
    def repeat():
        check_promises()
        again, again_ids = unlaunched_records(files, journal)
        if (again != records or again_ids != identities or unlaunched_evidence(installed, again, transport, info) != evidence
                or history() != original_history or active_prefix(root, serial) != prefix):
            unlaunched_refused("facts changed after ENROLL or before availability commit")
        check_installation()
        if ready_ids is not None:
            # Runtime/endpoint sampling above may have invalidated the actual
            # originals. Final readiness never substitutes restored live copies
            # or intermediate missing-record tolerance for physical archives.
            for name in UNLAUNCHED_RECORDS:
                archive = name + "." + journal["id"] + ".bak"
                original = records.get(name)
                if (files.read(archive, 1024 * 1024) != original
                        or (original is not None and record_identity(files.path / archive) != identities[name])):
                    unlaunched_refused("original archives changed after final external observation")
            unlaunched_live(files, replacement, blocked, ready_ids)
        check_promises()  # Filesystem checks only; no further external observations.
    repeat()
    if journal is None:
        journal = {"schema": 1, "id": os.urandom(16).hex(), "version": version, "serial": serial, "manifest": marker,
                   "evidence": evidence, "records": identities, "replacement": replacement, "blocked": blocked.hex(),
                   "permanent": permanent, "history": original_history, "active_prefix": prefix,
                   "confirmation": "ENROLL:exclusive:no_unmanaged_stream:no_admin_edits"}
        journal_data = document(journal)
        if len(journal_data) > lc.MAX_PRIVATE:
            unlaunched_refused("retirement journal exceeds private bound")
        path = files.path / ("unlaunched-transition-" + journal["id"] + ".bak")
        create(path, journal_data)
        journal_pins[journal["id"]] = journal_data, unlaunched_identity(path, journal_data)
        descriptor_pins[journal["id"]] = None, None, None
        unlaunched_commitment(journal, records, installed)
        excluded |= {path.name, "unlaunched-committed-" + journal["id"] + ".bak"}
        excluded |= {name + "." + journal["id"] + ".bak" for name in records}
    for name in UNLAUNCHED_RECORDS:  # reservation FIRST, lease remains until sentinel
        if name not in records:
            continue
        archive = name + "." + journal["id"] + ".bak"
        if files.read(archive, 1024 * 1024) is None:
            if record_identity(files.path / name) != identities[name]:
                unlaunched_refused("record changed before archival")
            files.anchor()
            os.rename(name, archive, src_dir_fd=files.fd, dst_dir_fd=files.fd)
            os.fsync(files.fd)
        if record_identity(files.path / archive) != identities[name]:
            unlaunched_refused("archive lost original bytes/inode/mode/mtime")
        if name == "reservation.json" and files.read(name) is None:
            if (files.read("operation.json") != records["operation.json"]
                    or record_identity(files.path / "operation.json") != identities["operation.json"]):
                unlaunched_refused("old lease disappeared before sentinel")
            create(files.path / name, RELEASE_BLOCKED)
    for name in ("operation.json", "start-receipt.json", "prepare-backup.conf"):
        if name not in replacement:
            continue
        data = bytes.fromhex(replacement[name])
        if files.read(name, 1024 * 1024) is None:
            create(files.path / name, data)
        elif files.read(name, 1024 * 1024) != data:
            unlaunched_refused("replacement differs from journal")
        if name == "operation.json":
            if files.read("reservation.json") == RELEASE_BLOCKED:
                files.write("reservation.json", blocked, limit=rs.MAX_STATE)
            elif files.read("reservation.json") != blocked:
                unlaunched_refused("blocked precommit state differs")
    actual, actual_ids = unlaunched_live(files, replacement, blocked, ready_ids)
    candidate = lc.HostController(installed, None, None, None, None)
    candidate.files = IdentityRecords(actual)
    if (candidate.meta() != {"schema": 1, "enrollment": meta["enrollment"], "lease": None}
            or rs._decode(actual["reservation.json"], candidate.host) != rs._decode(blocked, candidate.host)):
        unlaunched_refused("actual live compound blocked baseline differs")
    ready_ids = actual_ids
    repeat()
    journal_data, journal_identity = journal_pins[journal["id"]]
    seal = files.path / ("unlaunched-committed-" + journal["id"] + ".bak")
    seal_data = document({"schema": 1, "journal_sha256": cfg.digest(journal_data),
                          "journal_identity": journal_identity, "live_records": ready_ids})
    if descriptor_pins[journal["id"]][0] is None:
        create(seal, seal_data)
        descriptor_pins[journal["id"]] = seal_data, unlaunched_identity(seal, seal_data), ready_ids
    elif descriptor_pins[journal["id"]][0] != seal_data:
        unlaunched_refused("commitment descriptor differs from exact readiness")
    repeat()  # Positively require descriptor + complete actual live state now.
    files.write("reservation.json", new_state, limit=rs.MAX_STATE)  # Sole availability commit.
    return response(True, action="enroll", **lc.HostController.status_fields(new_status))


# Distinct installer-only authority for an attempted, never-confirmed launch.
# Keep it in the existing immutable source set: the fixed shim is byte-frozen.
ATTEMPT_CONFIRMATION = "RETIRE:one_stop_start:exclusive:old_clients_helpers_stopped:no_replay:no_admin_edits"
ATTEMPT_PHASES = ("stop-issued", "stop-completed", "stopped", "start-issued", "start-completed", "successor", "committed")


def attempt_refused(reason):
    fail("attempt_retirement_refused", "Attempted launch retirement refused: " + reason)


def interactive_retire():
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
        try:
            if not os.isatty(fd):
                return False
            os.write(fd, b"WARNING: authorize ONE Sunshine user-service STOP then START. All old Moonlight clients, coordinators and helpers must already be stopped; no other clients, authenticated replay sender, unmanaged/in-flight stream or concurrent administrator. Maintain this exclusive window until completion. Interrupted/uncertain control is NEVER automatically repeated. Type RETIRE: ")
            return os.read(fd, 64).strip() == b"RETIRE"
        finally:
            os.close(fd)
    except OSError:
        return False


def attempt_shape(installed, records):
    controller = lc.HostController(installed, None, None, None, None)
    controller.files = IdentityRecords(records)
    state = rs._decode(records["reservation.json"], controller.host)
    status, meta = state.status, controller.meta()
    lease = meta["lease"]
    fields = {"owner_digest", "stage", "selection_desktop", "display_id", "selector", "topology",
              "desktop_digest", "prepared_revision"}
    restart = {"before_digest", "after_digest", "restart_id", "previous_generation"}
    if (status.phase is not rs.Phase.RECONCILIATION_REQUIRED or status.uncertainty is not rs.Uncertainty.OWNER_LOST
            or not status.launch_attempted or status.active_observed or status.desktop is None
            or status.binding is None or state.owner_digest is None or meta["enrollment"] is None
            or lease is None or set(lease) not in (fields, fields | restart)
            or lease["stage"] != "attested" or lease["owner_digest"] != state.owner_digest
            or not meta["enrollment"]["revision"] < lease["prepared_revision"] < status.attempt_revision
            or status.revision != status.attempt_revision + 1 or status.revision >= rs.MAX_REVISION
            or lease["selection_desktop"] != asdict(status.desktop)
            or lease["desktop_digest"] != status.desktop.definition_digest
            or lease["topology"] != status.binding.topology_digest):
        attempt_refused("exact enrolled owner_lost/attempt/active=false/attested lease required")
    if (type(lease["selector"]) is not str or not 1 <= len(lease["selector"]) <= 80
            or any(ord(c) < 32 or ord(c) == 127 for c in lease["selector"])
            or type(lease["display_id"]) is not str
            or not re.fullmatch(r"kms:card[0-9]+:" + re.escape(lease["selector"]), lease["display_id"])):
        attempt_refused("recorded selected KMS display is not exact")
    before = records["prepare-backup.conf"]
    after = cfg.select_output(before, lease["selector"])
    if cfg.get_settings(after)["capture"] != "kms" or cfg.digest(after) != status.binding.config_digest:
        attempt_refused("preserved backup does not produce the exact attempted config")
    if restart <= set(lease):
        if (cfg.digest(before) != lease["before_digest"] or cfg.digest(after) != lease["after_digest"]
                or lc.canonical_uuid(lease["restart_id"]) != lease["restart_id"]):
            attempt_refused("preparation intent/backup differs")
        retirement_generation(lease["previous_generation"], installed.runtime.uid)
    desktop = lc.inspected_desktop(installed, after, installed.runtime.config_file.parent)
    if desktop != status.desktop or lc.inspected_desktop(installed, before, installed.runtime.config_file.parent) != desktop:
        attempt_refused("known empty-command no-hook Desktop is required")
    receipt = controller.receipt(desktop)
    envelope = controller.files.document("start-receipt.json")
    if (receipt is None or lc.service_generation(receipt.generation) != status.binding.generation
            or envelope["issuance"]["enrollment_revision"] != meta["enrollment"]["revision"]
            or (restart <= set(lease) and (envelope["issuance"]["operation"] != lease["restart_id"]
                                         or envelope["issuance"]["previous_generation"] != lease["previous_generation"]))):
        attempt_refused("original current controlled-start receipt is required, not a foreign receipt")
    return controller, state, meta, desktop, before, after, receipt


def attempt_sample(installed, records, transport, info, admitted=None):
    controller, state, meta, desktop, before, after, receipt = attempt_shape(installed, records)
    controller.runtime = rt.LinuxRuntime(installed.runtime, transport)
    controller.info = info
    facts = cfg.get_settings(after)
    owned = controller.sample_ownership({"address": facts["bind_address"], "port": installed.base_port}, use_receipt=False)
    current = owned.current
    if (owned.content != after or owned.desktop != desktop or owned.endpoint.state != "FREE" or owned.endpoint.currentgame != 0
            or not current.binary.supported_build or current.binary.process_exe_status != "verified"
            or current.config.process_environment_status != "verified" or current.generation.unit != rt.UNIT):
        attempt_refused("current qualified proc/root/build/config/idle/Desktop facts are required")
    receipt_lease = meta["lease"] if "restart_id" in meta["lease"] else None
    retirement_receipt(controller, desktop, current, receipt_lease, meta["enrollment"], before, after, transport, committed=True)
    if admitted is None:
        controller.runtime._match_receipt(receipt, current)
        if owned.binding != rs.OwnershipBinding.from_runtime(state.status.binding):
            attempt_refused("runtime differs from the exact original attempted generation")
    evidence = {"generation": asdict(current.generation), "unit": asdict(current.unit), "config": asdict(current.config),
                "binary": asdict(current.binary), "desktop": asdict(desktop),
                "endpoint": {"uuid": installed.host_uniqueid, "state": "FREE", "currentgame": 0}}
    if admitted is not None:
        old = retirement_generation(admitted["evidence"]["generation"], installed.runtime.uid)
        if (current.generation.boot_id != old.boot_id or current.generation.uid != old.uid
                or current.generation.invocation_id == old.invocation_id
                or (current.generation.pid, current.generation.start_ticks) == (old.pid, old.start_ticks)
                or current.generation.start_monotonic_usec <= old.start_monotonic_usec
                or any(evidence[k] != admitted["evidence"][k] for k in evidence if k != "generation")):
            attempt_refused("successor must be a fresh same-UUID/unit/UID/disk/root/build/Desktop generation")
    boundary = rt.AttemptRetirement(installed.runtime, transport).bind(current)
    return evidence, boundary


def attempt_boundary(value, installed):
    try:
        if type(value) is not dict or set(value) != set(rt.RetirementBoundary.__dataclass_fields__):
            raise ValueError("boundary")
        gen = retirement_generation(value["generation"], installed.runtime.uid)
        props = value["properties"]
        if (type(props) is not dict or set(props) != set(rt.RETIREMENT_PROPERTIES)
                or any(type(v) is not str for v in props.values())
                or any(props[k] != v for k, v in rt.RETIREMENT_POLICY.items())
                or props["ActiveState"] != "active" or props["SubState"] != "running"
                or props["MainPID"] != str(gen.pid) or props["InvocationID"] != gen.invocation_id
                or value["cgroup"] != props["ControlGroup"]
                or value["cgroup"] != f"/user.slice/user-{gen.uid}.slice/user@{gen.uid}.service/app.slice/{rt.UNIT}"):
            raise ValueError("scope")
        identities = [value[k] for k in ("cgroup_identity", "parent_identity")]
        if any(type(row) is not list or len(row) != 5 or any(type(x) is not int or x < 0 for x in row)
               or not stat.S_ISDIR(row[2]) or row[3] not in (0, gen.uid) for row in identities):
            raise ValueError("cgroup identity")
        return rt.RetirementBoundary(gen, props, value["cgroup"], *(tuple(row) for row in identities))
    except (KeyError, TypeError, ValueError):
        attempt_refused("invalid internally collected retirement boundary")


def attempt_witness(value, boundary):
    try:
        terminal = value["terminal"]
        if (type(value) is not dict or set(value) != set(rt.StopWitness.__dataclass_fields__)
                or value["boundary"] != json.loads(document(asdict(boundary)))
                or type(terminal) is not dict or set(terminal) != set(rt.RETIREMENT_PROPERTIES)
                or any(type(v) is not str for v in terminal.values())
                or any(terminal[k] != v for k, v in rt.RETIREMENT_POLICY.items())
                or terminal["ActiveState"] != "inactive" or terminal["SubState"] != "dead" or terminal["MainPID"] != "0"
                or terminal["InvocationID"] not in ("", boundary.generation.invocation_id)
                or terminal["ControlGroup"] not in ("", boundary.cgroup)
                or value["birth_retirement"] not in ("absent", "dead", "reused")
                or value["cgroup_retirement"] not in ("hierarchical_empty", "removed")):
            raise ValueError("witness")
        return rt.StopWitness(boundary, terminal, value["birth_retirement"], value["cgroup_retirement"])
    except (KeyError, ValueError, TypeError):
        attempt_refused("invalid persisted STOPPED witness; never infer it from a running successor")


def attempt_admission(installed, records, admitted):
    """Decode the durable promise against the actual originals, including receipt."""
    _, _, _, desktop, _, after, receipt = attempt_shape(installed, records)
    evidence = admitted["evidence"]
    facts = cfg.get_settings(after)
    expected_config = {"path": receipt.config_path, "root": receipt.config_root, "file": asdict(receipt.config),
                       "manager_environment_digest": receipt.manager_environment_digest,
                       "process_environment_status": "verified", "configured_capture": facts["capture"],
                       "configured_encoder": facts["encoder"], "configured_output": facts["output_name"],
                       "bind_address": facts["bind_address"], "base_port": facts["port"], "loaded_digest": None}
    if (set(admitted) != {"evidence", "boundary"}
            or set(evidence) != {"generation", "unit", "config", "binary", "desktop", "endpoint"}
            or evidence["generation"] != asdict(receipt.generation) or evidence["config"] != expected_config
            or set(evidence["unit"]) != set(rt.UnitRecord.__dataclass_fields__)
            or evidence["unit"]["ownership_digest"] != receipt.unit_digest or evidence["unit"]["name"] != rt.UNIT
            or evidence["unit"]["fragment_path"] not in ("/usr/lib/systemd/user/"+rt.UNIT, "/lib/systemd/user/"+rt.UNIT)
            or evidence["binary"] != {"path": str(rt.EXECUTABLE), "file": asdict(receipt.executable),
                                      "process_exe_status": "verified", "supported_build": True}
            or evidence["desktop"] != asdict(desktop)
            or evidence["endpoint"] != {"uuid": installed.host_uniqueid, "state": "FREE", "currentgame": 0}):
        attempt_refused("intent differs from original controlled-start/host/config/Desktop context")
    boundary = attempt_boundary(json.loads(document(admitted["boundary"])), installed)
    if asdict(boundary.generation) != asdict(receipt.generation):
        attempt_refused("intent boundary differs from the original exact birth")
    return boundary


def attempt_promise(installed, records, evidence, admitted):
    _, state, meta, _, _, after, _ = attempt_shape(installed, records)
    old = attempt_admission(installed, records, admitted).generation
    gen = retirement_generation(evidence["generation"], installed.runtime.uid)
    if (set(evidence) != set(admitted["evidence"]) or gen.unit != old.unit or gen.boot_id != old.boot_id
            or gen.invocation_id == old.invocation_id or (gen.pid, gen.start_ticks) == (old.pid, old.start_ticks)
            or gen.start_monotonic_usec <= old.start_monotonic_usec
            or any(evidence[k] != admitted["evidence"][k] for k in evidence if k != "generation")):
        attempt_refused("successor promise differs from the original context/fresh-generation requirement")
    return attempt_compound(state, meta, records, gen)


def attempt_compound(state, meta, records, generation):
    """Pure byte projection shared by the real promise and size-only specimens."""
    binding = rs.RuntimeBinding(state.status.host, lc.service_generation(generation), state.status.binding.topology_digest,
                                state.status.binding.config_digest)
    available = rs._encode(rs._State(rs.Status(state.status.host, state.status.revision + 1, rs.Phase.AVAILABLE, binding)))
    blocked = rs._encode(rs._State(rs.Status(state.status.host, state.status.revision, rs.Phase.SELECTING, state.status.binding), state.owner_digest))
    replacement = {name: data.hex() for name, data in records.items()}
    replacement["reservation.json"] = available.hex()
    replacement["operation.json"] = document({"schema": 1, "enrollment": meta["enrollment"], "lease": None}).hex()
    return state.status.revision + 1, available, blocked, replacement


def attempt_phase_data(journal, value):
    return document({"schema": 1, "journal_sha256": cfg.digest(document(journal)), **value})


def attempt_identity_ceiling(size, uid):
    # New private records are regular0600, own-UID files. Linux stat64 dev_t /
    # ino_t are unsigned64; nonnegative time_t64 seconds plus nanoseconds bound
    # mtime_ns. These domains are checked, not guessed decimal headroom.
    return {"sha256": "f"*64, "device": (1 << 64)-1, "inode": (1 << 64)-1,
            "mode": stat.S_IFREG | 0o600, "uid": uid, "size": size,
            "mtime_ns": ((1 << 63)-1)*1_000_000_000 + 999_999_999}


def attempt_identity_domain(identity, size, uid):
    ceiling = attempt_identity_ceiling(size, uid)
    if (set(identity) != set(ceiling) or identity["mode"] != ceiling["mode"] or identity["uid"] != uid
            or identity["size"] != size or type(identity["sha256"]) is not str
            or not re.fullmatch("[0-9a-f]{64}", identity["sha256"])
            or any(type(v) is not int or not 0 <= v <= ceiling[k] for k, v in identity.items() if k != "sha256")):
        attempt_refused("private record identity is outside the supported Linux stat64 serialization domain")


def attempt_record_bounds(installed, records, journal):
    """Serialize complete ceilings BEFORE intent/control; specimens are never evidence.

    Successor context must equal admission except generation; ownership PID is
    uint32 and positive ticks/monotonic fields are below2**63. Every other future
    choice is finite: terminal strings, witness enums, fixed control completions
    and stat64 identities. Hex originals remain exact, including whitespace.
    """
    _, state, meta, _, _, _, _ = attempt_shape(installed, records)
    boundary = attempt_admission(installed, records, journal["admission"])
    uid = installed.runtime.uid
    sizes = {"intent": len(document(journal))}
    identities = {}
    def add(key, value):
        sizes[key] = len(attempt_phase_data(journal, value))
        identities[key] = attempt_identity_ceiling(sizes[key], uid)
    add("stop-issued", {})
    add("stop-completed", {"issued_identity": identities["stop-issued"],
                            "completion": asdict(rt.ControlCompletion("stop", rt.UNIT, uid))})
    terminal = {**boundary.properties, "ActiveState": "inactive", "SubState": "dead", "MainPID": "0"}
    # Retaining old invocation/cgroup is longer than their allowed empty forms.
    witness = rt.StopWitness(boundary, terminal, "absent", "hierarchical_empty")
    add("stopped", {"witness": asdict(witness), "completed_identity": identities["stop-completed"]})
    add("start-issued", {"stopped_identity": identities["stopped"]})
    add("start-completed", {"issued_identity": identities["start-issued"],
                             "completion": asdict(rt.ControlCompletion("start", rt.UNIT, uid))})
    upper_generation = replace(boundary.generation, invocation_id="f"*32, pid=(1 << 32)-1,
                               start_ticks=(1 << 63)-1, start_monotonic_usec=(1 << 63)-1)
    evidence = {**journal["admission"]["evidence"], "generation": asdict(upper_generation)}
    _, available, blocked, replacement = attempt_compound(state, meta, records, upper_generation)
    add("successor", {"proofs": dict(identities), "evidence": evidence,
                      "replacement": replacement, "blocked": blocked.hex()})
    live = {name: blocked if name == "reservation.json" else bytes.fromhex(replacement[name]) for name in RELEASE_RECORDS}
    add("committed", {"journal_identity": attempt_identity_ceiling(sizes["intent"], uid), "proofs": dict(identities),
                      "live_records": {name: attempt_identity_ceiling(len(data), uid) for name, data in live.items()}})
    sizes["reservation"] = len(available)
    return sizes


def retire_attempt(root, version, serial, installed, files, transport, info, marker, authorize, certificate_originals=None):
    """One local owner, one monotonic phase chain, one final availability write."""
    anchors = {p: private_directory(p) for p in (root, root/"versions", root/"active", files.path, root/"versions"/version)}
    paths = (root/".install.lock", files.path/"operation.lock", files.path/"reservation.lock")
    if tuple(private_read(p, 1) for p in paths) != (b"\0", b"", b"\0"):
        attempt_refused("permanent lock bytes differ")
    permanent = {str(p.relative_to(root)): record_identity(p) for p in paths}
    pins, record_bounds = {}, {}
    external = external_authorities(root, installed, files, originals=certificate_originals)
    if any(not entry[3] for entry in external):
        attempt_refused("incomplete zero-control authority; RETIRE must not issue any service control")
    sealed_external = {entry[0]["id"] for entry in external}

    def pin(name, data=None):
        path = files.path/name
        if data is None:
            data = private_read(path, lc.MAX_PRIVATE)
        identity = unlaunched_identity(path, data, pins[name][1] if name in pins else None)
        attempt_identity_domain(identity, len(data), installed.runtime.uid)
        if name in pins and pins[name][0] != data:
            attempt_refused("intended phase bytes changed within invocation")
        pins[name] = data, identity
        return identity

    def check_pins():
        for name, (data, identity) in pins.items():
            unlaunched_identity(files.path/name, data, identity)
        external_original_fence(certificate_originals)

    def installation():
        if (any(private_directory(p) != identity for p, identity in anchors.items())
                or permanent != {str(p.relative_to(root)): record_identity(p) for p in paths}
                or active(root) != (version, serial) or validate_version(root/"versions"/version) != marker):
            attempt_refused("installation/source/references/permanent locks changed")
        external_original_fence(certificate_originals)

    def phase_name(journal, phase):
        return "attempt-" + phase + "-" + journal["id"] + ".bak"

    def phase(journal, key, value=None):
        name = phase_name(journal, key)
        if value is not None:
            data = attempt_phase_data(journal, value)
            if len(data) > min(lc.MAX_PRIVATE, record_bounds[journal["id"]][key]):
                attempt_refused("phase exceeds its preflight serialization ceiling; not published")
            if files.read(name) is None:
                create(files.path/name, data)
            elif files.read(name) != data:
                attempt_refused("phase contradicts its intended immutable proof")
        raw = files.read(name)
        if raw is None:
            return None
        obj = unique_json(raw)
        if (type(obj) is not dict or obj.get("schema") != 1 or type(obj["schema"]) is not int
                or obj.get("journal_sha256") != cfg.digest(document(journal)) or raw != document(obj)):
            attempt_refused("invalid phase/journal binding")
        pin(name, raw)
        return obj

    def budget(journal, records):
        sizes = attempt_record_bounds(installed, records, journal)
        for key, size in sizes.items():
            limit = rs.MAX_STATE if key == "reservation" else lc.MAX_PRIVATE
            if size > limit:
                attempt_refused(f"{key} serialization ceiling {size} exceeds {limit}; no new intent/control issued")
        record_bounds[journal["id"]] = sizes

    def originals(journal):
        result = {}
        for name in RELEASE_RECORDS:
            archive = name + "." + journal["id"] + ".bak"
            raw = files.read(archive, 1024*1024)
            path = files.path/archive if raw is not None else files.path/name
            if raw is None:
                raw = files.read(name, 1024*1024)
            if raw is None or record_identity(path) != journal["records"][name]:
                attempt_refused("original record/archive physical identity differs")
            result[name] = raw
        return result

    def check_history(journal):
        if (journal["serial"] > serial or active_prefix(root, journal["serial"]) != journal["active_prefix"]
                or journal["permanent"] != permanent
                or any(record_identity(files.path/name) != identity for name, identity in journal["history"].items())):
            attempt_refused("original history/active prefix/locks differ")

    def identity_map(value):
        keys = {"sha256", "device", "inode", "mode", "uid", "size", "mtime_ns"}
        return (type(value) is dict and all(type(row) is dict and set(row) == keys
                and type(row["sha256"]) is str and re.fullmatch("[0-9a-f]{64}", row["sha256"])
                and all(type(v) is int and v >= 0 for k, v in row.items() if k != "sha256")
                for row in value.values()))

    journals = []
    for path in files.path.glob("attempt-transition-*.bak"):
        raw = private_read(path, lc.MAX_PRIVATE)
        item = unique_json(raw)
        required = {"schema", "id", "version", "serial", "manifest", "admission", "records", "permanent", "history", "active_prefix", "confirmation"}
        if (type(item) is not dict or set(item) != required or item["schema"] != 1 or type(item["schema"]) is not int
                or type(item["id"]) is not str or not re.fullmatch("[0-9a-f]{32}", item["id"])
                or path.name != "attempt-transition-"+item["id"]+".bak" or raw != document(item)
                or type(item["serial"]) is not int or not 1 <= item["serial"] <= 512
                or type(item["version"]) is not str or not re.fullmatch(r"v[1-9][0-9]*-[0-9a-f]{64}", item["version"])
                or item["confirmation"] != ATTEMPT_CONFIRMATION
                or any(not identity_map(item[k]) for k in ("records", "permanent", "history", "active_prefix"))
                or set(item["records"]) != set(RELEASE_RECORDS)
                or set(item["permanent"]) != {str(p.relative_to(root)) for p in paths}
                or any(type(name) is not str or "/" in name or not name.endswith(".bak") for name in item["history"])
                or set(item["active_prefix"]) != {f"{n:08d}.json" for n in range(1, item["serial"]+1)}):
            attempt_refused("invalid durable attempted-launch intent")
        pin(path.name, raw)
        records = originals(item)
        _, state, _, _, _, _, _ = attempt_shape(installed, records)
        attempt_admission(installed, records, item["admission"])
        if (validate_version(root/"versions"/item["version"]) != item["manifest"]
                or unique_json(private_read(root/"active"/f'{item["serial"]:08d}.json'))["version"] != item["version"]):
            attempt_refused("intent source authority differs from its original active prefix")
        check_history(item)
        budget(item, records)
        journals.append((state.status.revision, item))
    journals.sort(key=lambda row: row[0])
    if len({rev for rev, _ in journals}) != len(journals):
        attempt_refused("duplicate attempted-launch revision")
    known_phases = {phase_name(j, k) for _, j in journals for k in ATTEMPT_PHASES}
    if any(p.name not in known_phases for p in files.path.glob("attempt-*.bak") if not p.name.startswith("attempt-transition-")):
        attempt_refused("phase has no original intent")
    journal = None
    for index, (revision, item) in enumerate(journals):
        if item["id"] in sealed_external:
            continue  # Exact, physically validated external seal; old chain stays INCOMPLETE.
        prior = None
        proofs = {}
        for key in ATTEMPT_PHASES:
            obj = phase(item, key)
            if obj is None:
                if any(files.read(phase_name(item, later)) is not None for later in ATTEMPT_PHASES[ATTEMPT_PHASES.index(key)+1:]):
                    attempt_refused("phase chain has a missing predecessor")
                break
            base = {"schema", "journal_sha256"}
            if key == "stop-issued" and set(obj) != base:
                attempt_refused("invalid STOP issued marker")
            if key in ("stop-completed", "start-completed"):
                action = key.split("-")[0]
                if (set(obj) != base | {"issued_identity", "completion"}
                        or obj["issued_identity"] != proofs[action+"-issued"]
                        or obj["completion"] != asdict(rt.ControlCompletion(action, rt.UNIT, installed.runtime.uid))):
                    attempt_refused("control sender completion/reaping is not bound to exact issued control")
            if key == "stopped":
                if set(obj) != base | {"witness", "completed_identity"} or obj["completed_identity"] != proofs["stop-completed"]:
                    attempt_refused("invalid STOPPED phase/completed-control binding")
                attempt_witness(obj["witness"], attempt_boundary(item["admission"]["boundary"], installed))
            if key == "start-issued" and (set(obj) != base | {"stopped_identity"}
                    or obj["stopped_identity"] != proofs["stopped"]):
                attempt_refused("START is not bound to the persisted STOPPED witness")
            if key == "successor":
                if (set(obj) != base | {"proofs", "evidence", "replacement", "blocked"} or obj["proofs"] != proofs):
                    attempt_refused("successor differs from the ordered service proofs")
                _, _, blocked, replacement = attempt_promise(installed, originals(item), obj["evidence"], item["admission"])
                if obj["blocked"] != blocked.hex() or obj["replacement"] != replacement:
                    attempt_refused("successor promise contradicts original R+1/enrollment/records")
            if key == "committed":
                successor = phase(item, "successor")
                if (set(obj) != base | {"journal_identity", "proofs", "live_records"}
                        or obj["journal_identity"] != pins["attempt-transition-"+item["id"]+".bak"][1]
                        or obj["proofs"] != proofs or set(obj["live_records"]) != set(RELEASE_RECORDS)):
                    attempt_refused("descriptor differs from intent/phase proofs/live record set")
                for name, identity in obj["live_records"].items():
                    intended = bytes.fromhex(successor["blocked"] if name == "reservation.json" else successor["replacement"][name])
                    if (set(identity) != {"sha256", "device", "inode", "mode", "uid", "size", "mtime_ns"}
                            or any(type(v) is not int or v < 0 for k, v in identity.items() if k != "sha256")
                            or identity["sha256"] != cfg.digest(intended) or identity["size"] != len(intended)
                            or stat.S_IMODE(identity["mode"]) != 0o600 or not stat.S_ISREG(identity["mode"])
                            or identity["uid"] != os.geteuid()):
                        attempt_refused("descriptor has invalid live physical promise")
            proofs[key] = pins[phase_name(item, key)][1]
            prior = key
        successor = phase(item, "successor")
        live = files.read("reservation.json", rs.MAX_STATE)
        floor = revision + 1  # Derived from decoded immutable original, never a declared floor.
        blocked = bytes.fromhex(successor["blocked"]) if successor else None
        pending = live is None or live == RELEASE_BLOCKED or live == blocked
        if not pending:
            current = rs._decode(live, rs.HostIdentity(installed.host_uniqueid, installed.base_port))
            if current.status.revision < floor:
                if (files.read("reservation.json."+item["id"]+".bak") is not None
                        or record_identity(files.path/"reservation.json") != item["records"]["reservation.json"]):
                    attempt_refused("reservation rollback below the original-derived R+1 floor")
                pending = True
            elif (prior != "committed" or (current.status.revision == floor and live != bytes.fromhex(successor["replacement"]["reservation.json"]))):
                attempt_refused("live commit contradicts its descriptor and original R+1")
        if pending:
            if index != len(journals)-1 or (item["version"], item["serial"], item["manifest"]) != (version, serial, marker):
                attempt_refused("pending recovery source/serial changed or an earlier transaction is incomplete")
            journal = item
        elif index == len(journals)-1:
            disk = cfg.load(installed.runtime.config_file)[0]
            desktop = lc.inspected_desktop(installed, disk, installed.runtime.config_file.parent)
            validate_state(installed, desktop, files)
            if current.status.phase is not rs.Phase.RECONCILIATION_REQUIRED:
                # Read-only committed replay permits legitimate later lease,
                # receipt and source evolution; retained originals stay sealed.
                replay = lc.HostController(installed, rt.LinuxRuntime(installed.runtime, transport), None, info, None)
                replay.files = files
                _, old_state, old_meta, old_desktop, before, after, _ = attempt_shape(installed, originals(item))
                original_receipt = files.read("start-receipt.json") == originals(item)["start-receipt.json"]
                context = old_meta["lease"] if original_receipt and "restart_id" in old_meta["lease"] else None
                retirement_receipt(replay, desktop, replay.runtime.observe(), context, replay.meta()["enrollment"],
                                   before if original_receipt else disk, after if original_receipt else disk, transport, committed=True)
                for _, older in journals:
                    check_history(older)
                    originals(older)
                check_pins(); installation()
                return response(True, action="reconcile-attempted-launch", **lc.HostController.status_fields(current.status))

    if journals and all(item["id"] in sealed_external for _, item in journals):
        current = rs._decode(files.read("reservation.json", rs.MAX_STATE), rs.HostIdentity(installed.host_uniqueid, installed.base_port))
        if current.status.phase is not rs.Phase.RECONCILIATION_REQUIRED:
            return external_replay(installed, files, transport, info, certificate_originals)
    records = originals(journal) if journal else {name: files.read(name, 1024*1024) for name in RELEASE_RECORDS}
    if any(data is None for data in records.values()):
        attempt_refused("all four original records are required")
    identities = journal["records"] if journal else {name: record_identity(files.path/name) for name in RELEASE_RECORDS}
    excluded = set(RELEASE_RECORDS) | {"operation.lock", "reservation.lock"}
    def exclude(item):
        excluded.update({"attempt-transition-"+item["id"]+".bak", *(phase_name(item, k) for k in ATTEMPT_PHASES),
                         *(name+"."+item["id"]+".bak" for name in RELEASE_RECORDS)})
    if journal:
        exclude(journal)
    def history():
        result = {}
        for path in files.path.iterdir():
            if path.name not in excluded:
                if not path.name.endswith(".bak"):
                    attempt_refused("unknown/incomplete private publication")
                result[path.name] = record_identity(path)
        return result
    original_history = journal["history"] if journal else history()
    prefix = journal["active_prefix"] if journal else active_prefix(root, serial)
    def physical(final=False, ready=None):
        # This function performs filesystem checks ONLY. Call it after the LAST
        # external observation, including all current/previous archives.
        check_pins(); installation()
        if history() != original_history or active_prefix(root, serial) != prefix:
            attempt_refused("history or active references changed")
        for _, prior_item in journals:
            check_history(prior_item)
            originals(prior_item)
        for name, data in records.items():
            archive = name+"."+journal["id"]+".bak" if journal else None
            archived = files.read(archive, 1024*1024) if archive else None
            path = files.path/archive if archived is not None else files.path/name
            if (final and archived is None) or private_read(path) != data or record_identity(path) != identities[name]:
                attempt_refused("original archive/live bytes or physical identity changed")
        if ready is not None:
            unlaunched_live(files, successor["replacement"], bytes.fromhex(successor["blocked"]), ready)
        check_pins()
    if journal is None:
        evidence, boundary = attempt_sample(installed, records, transport, info)
        if authorize() is not True:
            attempt_refused("attended RETIRE exclusive-sender/service-stop-start authorization required")
        again, again_boundary = attempt_sample(installed, records, transport, info)
        if evidence != again or boundary != again_boundary:
            attempt_refused("admission changed after confirmation")
        physical()
        journal = {"schema": 1, "id": os.urandom(16).hex(), "version": version, "serial": serial, "manifest": marker,
                   "admission": {"evidence": evidence, "boundary": asdict(boundary)}, "records": identities,
                   "permanent": permanent, "history": original_history, "active_prefix": prefix, "confirmation": ATTEMPT_CONFIRMATION}
        attempt_admission(installed, records, journal["admission"])
        budget(journal, records)  # ALL future records, before any durable intent or control.
        data = document(journal)
        name = "attempt-transition-"+journal["id"]+".bak"
        create(files.path/name, data); pin(name, data); exclude(journal)
    boundary = attempt_boundary(json.loads(document(journal["admission"]["boundary"])), installed)
    adapter = rt.AttemptRetirement(installed.runtime, transport)
    issued = phase(journal, "stop-issued")
    if issued is None:
        evidence, now = attempt_sample(installed, records, transport, info)
        if evidence != journal["admission"]["evidence"] or now != boundary:
            attempt_refused("original generation/context drifted before STOP")
        physical()
        phase(journal, "stop-issued", {})  # Durable BEFORE control; never repeat uncertain STOP.
        physical()
        completion = adapter.control("stop")
        phase(journal, "stop-completed", {"issued_identity": pins[phase_name(journal, "stop-issued")][1],
                                          "completion": asdict(completion)})
    if phase(journal, "stop-completed") is None:
        attempt_refused("STOP sender completion is unknown; no retry or snapshot-based continuation. Separately approved bounded intervention required")
    stopped = phase(journal, "stopped")
    if stopped is None:
        witness = adapter.stopped(boundary)  # Must happen while terminal, BEFORE path reuse.
        physical()
        stopped = phase(journal, "stopped", {"witness": asdict(witness), "completed_identity": pins[phase_name(journal, "stop-completed")][1]})
    witness = attempt_witness(stopped["witness"], boundary)
    start = phase(journal, "start-issued")
    if start is None:
        if adapter.stopped(boundary) != witness:
            attempt_refused("stopped interval changed before the sole START")
        physical()
        start = phase(journal, "start-issued", {"stopped_identity": pins[phase_name(journal, "stopped")][1]})
        physical()
        completion = adapter.control("start")
        phase(journal, "start-completed", {"issued_identity": pins[phase_name(journal, "start-issued")][1],
                                           "completion": asdict(completion)})
    if phase(journal, "start-completed") is None:
        attempt_refused("START sender completion is unknown; no retry or snapshot-based continuation. Separately approved bounded intervention required")
    # If START was issued but is still inactive/unknown, observation refuses;
    # elapsed time, missing process and FREE never authorize a repeated START.
    successor = phase(journal, "successor")
    evidence, _ = attempt_sample(installed, records, transport, info, journal["admission"])
    _, available, blocked, replacement = attempt_promise(installed, records, evidence, journal["admission"])
    service_proofs = {k: pins[phase_name(journal, k)][1] for k in ATTEMPT_PHASES[:5]}
    wanted = {"proofs": service_proofs, "evidence": evidence, "replacement": replacement, "blocked": blocked.hex()}
    if successor is not None and successor != {"schema": 1, "journal_sha256": cfg.digest(document(journal)), **wanted}:
        attempt_refused("successor facts differ from the durable ordered proof")
    physical()
    successor = phase(journal, "successor", wanted)
    descriptor = phase(journal, "committed")
    ready = descriptor["live_records"] if descriptor else None
    if ready is not None:
        unlaunched_live(files, replacement, blocked, ready)
    # Accepted archival/sentinel ordering: old dispatchers cannot enroll during
    # the missing reservation interval or bypass the compound pending repair.
    for name in RELEASE_RECORDS:
        archive = name+"."+journal["id"]+".bak"
        if files.read(archive, 1024*1024) is None:
            if record_identity(files.path/name) != identities[name]:
                attempt_refused("original changed before archival")
            files.anchor(); os.rename(name, archive, src_dir_fd=files.fd, dst_dir_fd=files.fd); os.fsync(files.fd)
        if record_identity(files.path/archive) != identities[name]:
            attempt_refused("archive lost original physical identity")
        if name == "reservation.json" and files.read(name) is None:
            if files.read("operation.json") != records["operation.json"] or record_identity(files.path/"operation.json") != identities["operation.json"]:
                attempt_refused("old lease disappeared before sentinel")
            create(files.path/name, RELEASE_BLOCKED)
    for name in ("operation.json", "start-receipt.json", "prepare-backup.conf"):
        data = bytes.fromhex(replacement[name])
        if files.read(name, 1024*1024) is None:
            create(files.path/name, data)
        elif files.read(name, 1024*1024) != data:
            attempt_refused("live replacement differs")
        if name == "operation.json":
            if files.read("reservation.json") == RELEASE_BLOCKED:
                files.write("reservation.json", blocked, limit=rs.MAX_STATE)
            elif files.read("reservation.json") != blocked:
                attempt_refused("actual blocked compound baseline differs")
    _, ready_ids = unlaunched_live(files, replacement, blocked, ready)
    for name, identity in ready_ids.items():
        intended = blocked if name == "reservation.json" else bytes.fromhex(replacement[name])
        attempt_identity_domain(identity, len(intended), installed.runtime.uid)
    descriptor_value = {"journal_identity": pins["attempt-transition-"+journal["id"]+".bak"][1],
                        "proofs": {k: pins[phase_name(journal, k)][1] for k in ATTEMPT_PHASES[:6]}, "live_records": ready_ids}
    phase(journal, "committed", descriptor_value)
    # The final external observation is followed by ALL physical/archive/live/
    # intent/descriptor/history/lock checks. No external call follows this fence.
    final_evidence, _ = attempt_sample(installed, records, transport, info, journal["admission"])
    if final_evidence != evidence:
        attempt_refused("final successor is busy, changed or unknown")
    physical(final=True, ready=ready_ids)
    files.write("reservation.json", available, limit=rs.MAX_STATE)  # SOLE availability publication.
    return response(True, action="reconcile-attempted-launch", **lc.HostController.status_fields(rs._decode(available, rs.HostIdentity(installed.host_uniqueid, installed.base_port)).status))


def local_attempt_retirement(*, authorize=interactive_retire, transport=None, info=None, original_certificates=()):
    """Installer-only dispatch using NSS and the current immutable installation."""
    if sys.platform != "linux" or os.geteuid() == 0 or os.getuid() != os.geteuid():
        attempt_refused("only the unprivileged local Linux desktop user is supported")
    uid = os.geteuid()
    home = Path(pwd.getpwuid(uid).pw_dir).resolve(strict=True)
    root = home/RELATIVE_ROOT
    if (home.stat().st_uid != uid or root.parent.resolve(strict=True) != root.parent):
        attempt_refused("fixed NSS home/installation must be canonical and owned")
    certificate_originals = external_originals(original_certificates, root)
    private_read(root/".install.lock", 1)
    with installation_lock(root):
        if {p.name for p in root.iterdir()} != {".install.lock", "versions", "active", "state"}:
            attempt_refused("foreign/incomplete fixed installation")
        version, serial = active(root)
        if version is None:
            attempt_refused("no installed source authority")
        marker = validate_version(root/"versions"/version)
        # The shell may live in a checkout, but every executing source module
        # must be the exact current published source. No checkout-only import.
        source = Path(__file__).resolve().parent
        if any(cfg.digest(source_read(source/name)) != marker["files"][name] for name in SOURCES):
            attempt_refused("publish this exact source normally before attempting local retirement")
        installed = host.settings(root/"versions"/version/"host-settings.json", "linux")
        if installed.runtime.home != home or installed.state_directory != root/"state" or installed.runtime.uid != uid:
            attempt_refused("installed roots/user differ from fixed NSS authority")
        for path in (root/"versions").iterdir():
            validate_version(path)
        private_read(root/"state/operation.lock", 1); private_read(root/"state/reservation.lock", 1)
        try:
            with lc.PrivateFiles(root/"state").locked() as files:
                return retire_attempt(root, version, serial, installed, files, transport or rt.RetirementTransport(home, uid),
                                      info or host.serverinfo, marker, authorize, certificate_originals)
        except Refusal as exc:
            if exc.code == "unlaunched_retirement_refused":
                # Reuse strict private-record/receipt validators, never that
                # ceremony's admission authority or its operator-facing label.
                attempt_refused(exc.detail.partition(": ")[2])
            raise


# This compatibility allowlist names audited SOURCE BYTES, never a host, UUID,
# transaction, pathname, or native generation. Later authorities may also name
# an exact source already approved by a validated earlier external authority.
EXTERNAL_LEGACY_SOURCE = {
    "host.py": "d895a8c28ba2daac2b2de9d5c26aab3a0db97cd37e2d3b5cbe5b95c541bad004",
    "lifecycle.py": "23ef4b2522d1d3570917c8f05074d03c31df82d8c9d87b612d5a48323d41d5a0",
    "configuration.py": "0dd34a836917e6d13dcc7796d74da85ad4422fac87886ed22910c9a175d26cdc",
    "protocol.py": "3f8e5ca758ee67fb1c14950ed292c18a61d8f11f3fe8460ef7c690ab2e49e63d",
    "reservation.py": "225d78a8ab20bec9a8d4818b147cc72f8aac8bc22328abfe23acf569c41b8049",
    "runtime_linux.py": "545eb7586a927879b671b5c859e77877c357bd6d148e3eda0697891a02c119f3",
    "linux_kms.py": "83740b9afe3c2ced6a3fa4777b681d14940f2b918acaaef36862d8bf5af5a780",
    "windows_display.py": "eeea165ae1f3871af65daf0945e8b0d58dddfb35de99e59c4dcff0e503c4160c",
    "macos_display.py": "c03a3b02a49a8c829a80689f194e235ba52b3c305dec706a3efbbdd948b59053",
    "qualification.py": "3e2db06317780d7e6f6298e1e7dc8b62fa4210b7a73a04bbfe6f3909b3409e04",
    "install_controller.py": "aed69ce362bd607317cb43c54a65311293b87aa98226dec06cca395dc46682c3",
}
EXTERNAL_COMPONENT_RUNTIME = "5991cec9370266e6ed2ad203cbc3cee4d146cda685004fa76498245699f35f02"
EXTERNAL_ROLES = ("kernel", "anchored", "context", "lifecycle")
EXTERNAL_UNKNOWN = {key: "unknown" for key in
                    ("historical_global_jobs", "historical_repeated_typed_policy", "historical_process_fds",
                     "historical_loaded_config", "historical_capture", "historical_monotonic_observation")}
EXTERNAL_KIND = "operator_attested_components_not_stop_witness"


def external_refused(reason):
    fail("external_successor_refused", "Zero-control reconciliation refused: " + reason)


def external_object(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        external_refused("unknown/missing authority fields")
    return value


def external_text(value, limit=512):
    if (type(value) is not str or not 1 <= len(value.encode("utf-8", "strict")) <= limit
            or value.strip().casefold() in ("unknown", "unaccounted", "unsure", "n/a", "none", "?", "pending")
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        external_refused("concrete actor/action/issuer/helper/provenance facts required")
    return value


def external_hash(value):
    if type(value) is not str or not re.fullmatch("[0-9a-f]{64}", value):
        external_refused("invalid content/source digest")
    return value


def external_utc(value):
    """Declared UTC point instant as exact integer nanoseconds; no float,
    fractional truncation or implicit precision/clock-drift allowance.
    """
    from datetime import datetime
    external_text(value, 64)
    match = re.fullmatch(r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(?:\.([0-9]{1,9}))?(?:Z|\+00:00)", value)
    try:
        if match is None:
            raise ValueError("clock")
        delta = datetime.fromisoformat(match[1]) - datetime(1970, 1, 1)
        return (delta.days * 86400 + delta.seconds) * 1_000_000_000 + int((match[2] or "0").ljust(9, "0"))
    except ValueError:
        external_refused("component clock must be an explicit UTC point with at most nanosecond precision")


def external_certificate(certificate, raw, predecessor, original_set, approved, serial, boundary, version_prefix):
    """Bounded attested components, NOT attempt_witness() or a machine proof importer.

    Raw producer objects supply positive birth/dirfd/terminal and manager links.
    The informed local operator supplies the historical causal order and the
    external action/holder/issuer envelope. Neither brand silently becomes the
    other. Missing past jobs/FD/proc/loaded/capture samples stay explicitly unknown.
    This function is pure; it performs no service, process, endpoint or file I/O.
    """
    external_object(certificate, {"schema", "kind", "predecessor", "approved_source", "external", "account",
                                  "ordering", "unknown", "attachments", "source_version_prefix"})
    if (type(certificate["schema"]) is not int or certificate["schema"] != 1
            or certificate["kind"] != EXTERNAL_KIND or certificate["unknown"] != EXTERNAL_UNKNOWN
            or set(raw) != set(EXTERNAL_ROLES)):
        external_refused("unsupported certificate/unknown historical evidence brand")
    old = predecessor["admission"]["boundary"]
    oldgen = old["generation"]
    gen = asdict(boundary.generation)
    if tuple(boundary.parent_identity) != tuple(old["parent_identity"]):
        external_refused("fresh successor parent differs from original anchored parent")
    expected = {"id": predecessor["id"], "intent_sha256": cfg.digest(document(predecessor)),
                "records_sha256": cfg.digest(document(original_set)),
                "source_manifest_sha256": cfg.digest(document(predecessor["manifest"])),
                "settings_sha256": predecessor["manifest"]["files"]["host-settings.json"]}
    if certificate["predecessor"] != expected or certificate["approved_source"] != {
            "version": approved["version"], "serial": serial, "manifest_sha256": cfg.digest(document(approved)),
            "settings_sha256": approved["files"]["host-settings.json"]}:
        external_refused("certificate does not bind exact originals and approved source append")
    preserved = certificate["source_version_prefix"]
    if (type(preserved) is not dict or not {predecessor["version"], approved["version"]} <= set(preserved)
            or any(type(n) is not str or not re.fullmatch("v1-[0-9a-f]{64}", n) for n in preserved)):
        external_refused("invalid complete certificate source-version prefix")
    for digest in preserved.values():
        external_hash(digest)
    if preserved != version_prefix:
        external_refused("immutable certificate source-prefix commitment differs from the full preservation promise")
    external_object(certificate["external"], {"generation", "job_id", "start_cursor", "completed_cursor"})
    ext = certificate["external"]
    if (ext["generation"] != gen or type(ext["job_id"]) is not str
            or not re.fullmatch("[1-9][0-9]{0,9}", ext["job_id"])):
        external_refused("external generation/job differs from fresh qualified successor")
    external_text(ext["start_cursor"]); external_text(ext["completed_cursor"])
    if ext["start_cursor"] == ext["completed_cursor"]:
        external_refused("external start/completion provenance is not distinct")
    external_object(certificate["account"], {"actor", "action", "issuer", "issuer_completion", "issuer_completion_brand",
        "helpers", "specific_outstanding_work", "outside_request_holders", "request_fd_handoff", "process_migration",
        "authenticated_replay", "other_deviations", "policy_config_changes", "exclusive_window"})
    account = certificate["account"]
    for key in ("actor", "action", "issuer", "issuer_completion"):
        external_text(account[key])
    if (account["issuer_completion_brand"] != "operator_attested_action_finished_not_child_reaping"
            or type(account["helpers"]) is not list or len(account["helpers"]) > 16
            or any(account[key] != "none" for key in ("specific_outstanding_work", "outside_request_holders",
                "request_fd_handoff", "process_migration", "authenticated_replay", "other_deviations", "policy_config_changes"))
            or account["exclusive_window"] != "renewed_until_commit"):
        external_refused("unaccounted specific controls/helpers/holders/replay or changed policy/exclusive window")
    helper_ids = set()
    for helper in account["helpers"]:
        external_object(helper, {"identity", "work", "completion", "state"})
        for key in ("identity", "work", "completion"):
            external_text(helper[key])
        if helper["state"] != "finished_no_committed_work" or helper["identity"] in helper_ids:
            external_refused("unfinished/duplicate external helper")
        helper_ids.add(helper["identity"])
    retirement_generation(ext["generation"], gen["uid"])
    if (type(certificate["ordering"]) is not dict
            or any(type(certificate["ordering"].get(k)) is not bool for k in
                   ("components_after_old_admission", "scope_absence_before_external_start", "unaccounted_clock_changes"))):
        external_refused("causal ordering assertions must be explicit booleans")
    admission_identity = original_set["attempt-transition-" + predecessor["id"] + ".bak"]
    attested_identity = external_object(certificate["ordering"].get("old_admission_intent_identity"), admission_identity)
    attempt_identity_domain(attested_identity, admission_identity["size"], gen["uid"])
    if certificate["ordering"] != {"brand": "operator_attested_causal_order", "old_invocation": oldgen["invocation_id"],
            "external_invocation": gen["invocation_id"], "scope_absence_before_external_start": True,
            "components_after_old_admission": True, "old_admission_intent_identity": admission_identity,
            "clock_basis": "same_host_realtime_utc_and_sealed_mtime_ns", "unaccounted_clock_changes": False}:
        external_refused("historical post-admission causal interval/same-host clock envelope is not attested")
    external_object(certificate["attachments"], EXTERNAL_ROLES)
    objects = {}
    for role in EXTERNAL_ROLES:
        meta = external_object(certificate["attachments"][role], {"sha256", "observer", "clock"})
        external_text(meta["observer"])
        external_object(meta["clock"], {"basis", "value"})
        if (meta["sha256"] != cfg.digest(raw[role]) or meta["clock"]["basis"] != "UTC"
                or len(raw[role]) > lc.MAX_PRIVATE):
            external_refused("raw component hash/clock/bound differs")
        objects[role] = unique_json(raw[role])  # Includes duplicate/type rejection, never filenames.
        if type(objects[role]) is not dict:
            external_refused("component attachment is not an object")
        observed = objects[role].get("utc" if role == "kernel" else "observed_utc")
        if meta["clock"]["value"] != observed:
            external_refused("component provenance clock differs from raw producer")
        external_utc(observed)
    kernel, anchored, context, lifecycle = (objects[k] for k in EXTERNAL_ROLES)
    components = kernel.get("components")
    target = str(rt.CGROUP_ROOT / old["cgroup"].lstrip("/"))
    if (type(components) is not dict or components.get("boot") != {"value": oldgen["boot_id"]}
            or components.get("old_birth") != {"value": "absent"}
            or components.get("cgroup_path") != {"value": target}
            or components.get("parent_identity") != {"value": old["parent_identity"]}):
        external_refused("positive historical old-birth/boot/parent component links absent")
    terminal = {"MainPID": "0", "ControlPID": "0", "ControlGroup": "", "ActiveState": "inactive", "SubState": "dead",
                "Job": "", "InvocationID": oldgen["invocation_id"]}
    if (anchored.get("transaction") != predecessor["id"] or anchored.get("target") != target
            or anchored.get("expected_parent") != old["parent_identity"] or anchored.get("targeted_scope_removed") is not True
            or anchored.get("runtime_sha256") != EXTERNAL_COMPONENT_RUNTIME
            or anchored.get("native_unit_properties") != terminal or type(anchored.get("native_unit_query_exit")) is not int
            or anchored["native_unit_query_exit"] != 0 or anchored.get("start_issued_record_exists") is not False):
        external_refused("anchored positive dirfd/terminal component is missing or from an unknown collector")
    external_hash(anchored.get("source_snapshot_sha256"))  # Provenance only; not an imported source authority.
    unit, proc = context.get("native_unit"), context.get("current_proc")
    if (context.get("old_generation") != oldgen or context.get("current_boot_id") != gen["boot_id"]
            or context.get("same_boot") is not True or context.get("old_scope_identity") != old["cgroup_identity"]
            or type(unit) is not dict or type(proc) is not dict or unit.get("MainPID") != str(gen["pid"])
            or unit.get("InvocationID") != gen["invocation_id"] or unit.get("Id") != rt.UNIT
            or unit.get("ActiveState") != "active" or unit.get("SubState") != "running"
            or unit.get("ControlPID") != "0" or unit.get("NRestarts") != "0" or unit.get("Job") != ""
            or unit.get("ControlGroup") != boundary.cgroup or proc.get("pid") != str(gen["pid"])
            or type(proc.get("birth")) is not int or proc["birth"] != gen["start_ticks"]
            or proc.get("exe") != str(rt.EXECUTABLE) or proc.get("cgroup") != "0::" + boundary.cgroup + "\n"
            or unit.get("ExecMainStartTimestampMonotonic") != str(gen["start_monotonic_usec"])):
        external_refused("raw external context does not link the exact old and fresh current generations")
    scope = context.get("current_old_path_identity")
    if (type(scope) is not dict or [scope.get(k) for k in ("device", "inode", "mode", "uid", "gid")] != list(boundary.cgroup_identity)
            or list(boundary.cgroup_identity) == old["cgroup_identity"]):
        external_refused("external scope replacement/current physical identity differs")
    if (type(lifecycle.get("journal_exit")) is not int or lifecycle["journal_exit"] != 0
            or lifecycle.get("journal_stderr") != "" or type(lifecycle.get("manager_events")) is not list
            or len(lifecycle["manager_events"]) > 256):
        external_refused("invalid scoped manager-event provenance")
    linked = []
    for cursor in (ext["start_cursor"], ext["completed_cursor"]):
        rows = [row for row in lifecycle["manager_events"] if type(row) is dict and row.get("__CURSOR") == cursor]
        if len(rows) != 1:
            external_refused("missing/duplicate exact external manager event")
        row = rows[0]
        if (any(row.get(k) != v for k, v in {"_BOOT_ID": gen["boot_id"], "_UID": str(gen["uid"]),
                "_COMM": "systemd", "USER_UNIT": rt.UNIT, "USER_INVOCATION_ID": gen["invocation_id"],
                "JOB_TYPE": "start", "JOB_ID": ext["job_id"]}.items())
                or any(type(row.get(k)) is not str or not re.fullmatch("[1-9][0-9]{0,19}", row[k])
                       for k in ("__MONOTONIC_TIMESTAMP", "__REALTIME_TIMESTAMP"))):
            external_refused("manager event has wrong causal generation/unit/job/clock links")
        linked.append(row)
    if (linked[0].get("MESSAGE_ID") != "7d4958e842da4a758f6c1cdc7b36dcc5"
            or linked[1].get("MESSAGE_ID") != "39f53479d3a045ac8e11786248231fbf"
            or not oldgen["start_monotonic_usec"] < int(linked[0]["__MONOTONIC_TIMESTAMP"]) <= gen["start_monotonic_usec"]
                    <= int(linked[1]["__MONOTONIC_TIMESTAMP"])
            or int(linked[0]["__REALTIME_TIMESTAMP"]) > int(linked[1]["__REALTIME_TIMESTAMP"])):
        external_refused("manager start/completion ordering contradicts the linked invocation")
    # The original intent was created AFTER qualified old-generation admission.
    # Its already sealed mtime supplies a conservative observable lower bound,
    # not an invented admission wall/monotonic timestamp. Equality is allowed:
    # admission precedes intent creation. UTC strings denote point instants at
    # their stated decimal precision; even 1ns predating is rejected, with no
    # spare headroom. These consistency checks require the separate truthful
    # same-host/no-unaccounted-clock-change and causal-interval attestation.
    control_times = [original_set["attempt-" + role + "-" + predecessor["id"] + ".bak"]["mtime_ns"]
                     for role in ("transition", "stop-issued", "stop-completed")]
    start_ns = int(linked[0]["__REALTIME_TIMESTAMP"]) * 1000
    if (any(type(value) is not int for value in control_times)
            or not control_times[0] <= control_times[1] <= control_times[2] <= start_ns):
        external_refused("sealed original admission/control realtime anchors contradict the external Start")
    if not control_times[0] <= external_utc(kernel["utc"]) <= external_utc(anchored["observed_utc"]) < start_ns:
        external_refused("component realtime interval predates old admission intent or contradicts external Start order")
    return certificate


def external_name(identifier, role):
    return "external-" + role + "-" + identifier + ".bak"


def external_predecessor(root, installed, files, identifier):
    """Read the exact incomplete three-phase predecessor; never repair a phase."""
    if type(identifier) is not str or not re.fullmatch("[0-9a-f]{32}", identifier):
        external_refused("invalid predecessor identifier")
    name = "attempt-transition-" + identifier + ".bak"
    data = private_read(files.path/name, lc.MAX_PRIVATE)
    old = external_object(unique_json(data), {"schema", "id", "version", "serial", "manifest", "admission", "records",
                                            "permanent", "history", "active_prefix", "confirmation"})
    if (data != document(old) or old["id"] != identifier or type(old["schema"]) is not int or old["schema"] != 1
            or old["confirmation"] != ATTEMPT_CONFIRMATION or type(old["serial"]) is not int
            or not 1 <= old["serial"] < 512 or type(old["version"]) is not str
            or not re.fullmatch("v1-[0-9a-f]{64}", old["version"])
            or set(old["records"]) != set(RELEASE_RECORDS) or type(old["history"]) is not dict
            or any(type(n) is not str or "/" in n or not n.endswith(".bak") for n in old["history"])
            or old["manifest"] != validate_version(root/"versions"/old["version"])
            or old["active_prefix"] != active_prefix(root, old["serial"])
            or unique_json(private_read(root/"active"/f'{old["serial"]:08d}.json')) != {"schema": 1, "version": old["version"]}):
        external_refused("unknown predecessor source/schema/reference prefix")
    locks = (root/".install.lock", files.path/"operation.lock", files.path/"reservation.lock")
    permanent = {str(p.relative_to(root)): record_identity(p) for p in locks}
    if old["permanent"] != permanent or tuple(private_read(p, 1) for p in locks) != (b"\0", b"", b"\0"):
        external_refused("predecessor permanent lock identity/bytes differ")
    original_set = dict(old["history"])
    records = {}
    for record in RELEASE_RECORDS:
        archive = files.path/(record + "." + identifier + ".bak")
        path = archive if archive.exists() or archive.is_symlink() else files.path/record
        records[record] = private_read(path)
        if record_identity(path) != old["records"][record] or record in original_set:
            external_refused("predecessor original physical record differs")
        original_set[record] = old["records"][record]
    attempt_admission(installed, records, old["admission"])
    phase_ids = {}
    for phase in ("stop-issued", "stop-completed"):
        path = files.path/("attempt-" + phase + "-" + identifier + ".bak")
        raw = private_read(path, lc.MAX_PRIVATE)
        wanted = {} if phase == "stop-issued" else {
            "issued_identity": phase_ids["stop-issued"],
            "completion": asdict(rt.ControlCompletion("stop", rt.UNIT, installed.runtime.uid))}
        if raw != attempt_phase_data(old, wanted):
            external_refused("STOP is issued-only or not internally acknowledged/reaped against exact issued marker")
        phase_ids[phase] = record_identity(path)
        if path.name in original_set:
            external_refused("predecessor phase was already declared as old history")
        original_set[path.name] = phase_ids[phase]
    if any(files.read("attempt-"+phase+"-"+identifier+".bak") is not None for phase in ATTEMPT_PHASES[2:]):
        external_refused("only the incomplete intent/STOP-issued/STOP-completed chain may be superseded")
    for path in (files.path/name, files.path/"operation.lock", files.path/"reservation.lock"):
        if path.name in original_set:
            external_refused("predecessor evidence/history aliases a permanent record")
        original_set[path.name] = record_identity(path)
    for n, ident in original_set.items():
        attempt_identity_domain(ident, ident["size"], installed.runtime.uid)
        if n not in RELEASE_RECORDS and record_identity(files.path/n) != ident:
            external_refused("predecessor history physical identity differs")
    return old, records, original_set


def external_source_bridge(root, predecessor, version, serial, marker, trusted=()):
    """Exactly one normal ACTIVE publication, unchanged settings, no adoption."""
    old = predecessor["manifest"]
    code = {n: old["files"][n] for n in SOURCES}
    corrected = {**EXTERNAL_LEGACY_SOURCE, "runtime_linux.py": EXTERNAL_COMPONENT_RUNTIME}
    permitted = [EXTERNAL_LEGACY_SOURCE, corrected,
                 {n: cfg.digest(source_read(Path(__file__).resolve().parent/n)) for n in SOURCES}]
    permitted.extend({n: item["files"][n] for n in SOURCES} for item in trusted)
    if (code not in permitted or serial != predecessor["serial"]+1 or version == predecessor["version"]
            or marker != validate_version(root/"versions"/version)
            or old["files"]["host-settings.json"] != marker["files"]["host-settings.json"]
            or private_read(root/"versions"/version/"host-settings.json")
               != private_read(root/"versions"/predecessor["version"]/"host-settings.json")):
        external_refused("unknown/forked predecessor source or not one qualified unchanged-settings source append")
    sources = {}
    for item in (old, marker):
        if item["version"] != "v"+str(item["release"])+"-"+cfg.digest(document(item["files"])):
            external_refused("source version does not derive from its exact source/settings manifest")
        folder = root/"versions"/item["version"]
        sources[item["version"]] = {n: record_identity(folder/n) for n in (*SOURCES, "host-settings.json", ".owned.json")}
    return sources


def interactive_external(certificate):
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
        try:
            if not os.isatty(fd):
                return False
            summary = {key: certificate[key] for key in ("predecessor", "approved_source", "external", "source_version_prefix")}
            summary["account"] = {key: certificate["account"][key] for key in ("actor", "action", "issuer", "issuer_completion", "helpers")}
            os.write(fd, b"ZERO service controls. Attest this bounded factual external-START account: " + document(summary)
                + b"Historical components are operator-attested AFTER the bound old admission, under the same host UTC/realtime/mtime clock with no unaccounted changes, NOT a machine StopWitness; unknown past fields remain unknown. "
                  b"Confirm the recorded Start was the sole deviation, all specific issuer/helper work is finished, no outside old request/FD holder, "
                  b"handoff/migration/authenticated replay or outstanding/queued control remains, unchanged config/policy, and exclusive use until commit. "
                  b"Preserve the old receipt as history and permanently relinquish the original unused START. Type RECONCILE: ")
            return os.read(fd, 64).strip() == b"RECONCILE"
        finally:
            os.close(fd)
    except OSError:
        return False


@dataclass(frozen=True)
class OriginalCertificate:
    """One explicitly selected local input, never a path/hash from a journal."""
    path: Path
    data: bytes
    identity: dict


def external_originals(paths, root):
    """Snapshot bounded owned originals selected by local argv/API, not cache.

    Original selection is the local operator's trust boundary. No source/state
    record may select or redirect these files, and copies inside the mutable
    controller installation are deliberately ineligible as independent inputs.
    """
    if type(paths) not in (tuple, list) or len(paths) > 512:
        external_refused("at most512 explicit local original certificates are supported")
    result, physical = {}, set()
    for selected in paths:
        path = Path(selected).absolute()
        data = private_read(path, lc.MAX_PRIVATE)
        identity = record_identity(path)
        if path.resolve(strict=True).is_relative_to(root):
            external_refused("select the retained original certificate outside the controller installation, never its cache")
        obj = external_object(unique_json(data), {"schema", "kind", "predecessor", "approved_source", "external", "account",
            "ordering", "unknown", "attachments", "source_version_prefix"})
        predecessor = external_object(obj["predecessor"], {"id", "intent_sha256", "records_sha256", "source_manifest_sha256", "settings_sha256"})
        identifier = predecessor["id"]
        if (data != document(obj) or type(obj["schema"]) is not int or obj["schema"] != 1 or obj["kind"] != EXTERNAL_KIND
                or type(identifier) is not str or not re.fullmatch("[0-9a-f]{32}", identifier)
                or identity["sha256"] != cfg.digest(data)):
            external_refused("original certificate must be canonical, owned and linked to an exact predecessor")
        attempt_identity_domain(identity, len(data), os.geteuid())
        key = identity["device"], identity["inode"]
        if identifier in result or key in physical:
            external_refused("duplicate original certificate/predecessor/physical input")
        physical.add(key)
        result[identifier] = OriginalCertificate(path, data, identity)
    external_original_fence(result)
    return result


def external_original_fence(originals):
    """Filesystem-only check; callers repeat AFTER their last external sample."""
    if originals is None:
        return
    if type(originals) is not dict:
        external_refused("explicit local original-certificate snapshots required")
    for proof in originals.values():
        if (type(proof) is not OriginalCertificate or record_identity(proof.path) != proof.identity
                or private_read(proof.path, lc.MAX_PRIVATE) != proof.data):
            external_refused("selected original certificate changed physically/bytes after observation")


def external_original(originals, identifier):
    if type(originals) is not dict or identifier not in originals:
        external_refused("explicit original certificate required for every supersession; cached evidence is not an independent root")
    external_original_fence(originals)
    return originals[identifier]


def external_version_prefix(root):
    result = {}
    for folder in (root/"versions").iterdir():
        marker = validate_version(folder)
        if marker["version"] != "v"+str(marker["release"])+"-"+cfg.digest(document(marker["files"])):
            # Archival objects only: this audited v1 publisher named SOURCES
            # before adding settings. validate_version above still verifies the
            # settings hash; the physical commitment below still includes it.
            if not (marker["release"] == 1
                    and marker["files"]["install_controller.py"] == "4af7b73adbd39e0768356e5067e5aba9b054551d6ceab58fa8582021ed76a71f"
                    and marker["version"] == "v1-"+cfg.digest(document({n: h for n, h in marker["files"].items() if n != "host-settings.json"}))):
                external_refused("unknown/forked source version in preserved installation")
        result[folder.name] = cfg.digest(document({p.name: record_identity(p) for p in folder.iterdir()}))
    return result


def external_original_path(files, identifier, name):
    archived = files.path/(name+"."+identifier+".bak")
    return archived if archived.exists() or archived.is_symlink() else files.path/name


def external_descendant_floor(root, installed, files, identifier, floor, sealed, originals=None):
    """A later exact attempted intent can preserve an earlier published floor
    while its own reservation is temporarily missing/sentinel/SELECTING. Its
    sealed history MUST physically include this complete authority and archives;
    an invented .bak or a declared numeric floor supplies no such lineage.
    """
    original = external_original(originals, identifier)
    if files.read(external_name(identifier, "certificate")) != original.data:
        external_refused("descendant floor cache differs from explicitly selected original certificate")
    for path in files.path.glob("attempt-transition-*.bak"):
        if path.name == "attempt-transition-"+identifier+".bak":
            continue
        raw = private_read(path, lc.MAX_PRIVATE)
        later = unique_json(raw)
        if (type(later) is not dict or raw != document(later) or type(later.get("history")) is not dict
                or any(later["history"].get(n) != ident for n, ident in sealed.items())):
            continue
        required = {"schema", "id", "version", "serial", "manifest", "admission", "records", "permanent", "history", "active_prefix", "confirmation"}
        if (set(later) != required or type(later["id"]) is not str or not re.fullmatch("[0-9a-f]{32}", later["id"])
                or path.name != "attempt-transition-"+later["id"]+".bak"
                or later["confirmation"] != ATTEMPT_CONFIRMATION or type(later["schema"]) is not int or later["schema"] != 1
                or type(later["version"]) is not str or not re.fullmatch("v1-[0-9a-f]{64}", later["version"])
                or any(type(n) is not str or "/" in n or not n.endswith(".bak") for n in later["history"])
                or type(later["serial"]) is not int or not 1 <= later["serial"] <= 512
                or later["active_prefix"] != active_prefix(root, later["serial"])
                or later["manifest"] != validate_version(root/"versions"/later["version"])
                or set(later["records"]) != set(RELEASE_RECORDS)):
            external_refused("later floor witness has unknown source/schema/history")
        locks = (root/".install.lock", files.path/"operation.lock", files.path/"reservation.lock")
        if later["permanent"] != {str(p.relative_to(root)): record_identity(p) for p in locks}:
            external_refused("later floor witness changed permanent locks")
        for name, identity in later["history"].items():
            attempt_identity_domain(identity, identity["size"], installed.runtime.uid)
            if record_identity(files.path/name) != identity:
                external_refused("later floor witness has changed physical history")
        if unique_json(private_read(root/"active"/f'{later["serial"]:08d}.json')) != {"schema": 1, "version": later["version"]}:
            external_refused("later floor witness source reference contradicts manifest")
        records = {}
        for name in RELEASE_RECORDS:
            original = external_original_path(files, later["id"], name)
            if record_identity(original) != later["records"][name]:
                external_refused("later original floor witness was replaced")
            records[name] = private_read(original)
        attempt_admission(installed, records, later["admission"])
        _, state, _, _, _, _, _ = attempt_shape(installed, records)
        if state.status.revision >= floor:
            return True
    return False


def external_authorities(root, installed, files, *, supplied=None, originals=None, current=None):
    """Validate exact sealed supersessions; never ignore an incomplete journal.

    Returns ordered (intent, promise, descriptor, committed) tuples. This is
    filesystem-only and performs no writes. A partial authority without its
    original supplied attachments stays blocked, including in ordinary RETIRE.
    """
    entries, known, trusted = [], set(), []
    candidates = []
    for path in files.path.glob("external-transition-*.bak"):
        raw = private_read(path, lc.MAX_PRIVATE)
        item = external_object(unique_json(raw), {"schema", "kind", "id", "predecessor_sha256", "original_set", "version", "serial",
            "manifest", "sources", "version_prefix", "prefix", "permanent", "certificate", "input_provenance", "promise_sha256",
            "confirmation", "predecessor_disposition", "unused_original_start"})
        if (type(item["schema"]) is not int or item["schema"] != 1 or item["kind"] != EXTERNAL_KIND
                or raw != document(item) or type(item["id"]) is not str or not re.fullmatch("[0-9a-f]{32}", item["id"])
                or path.name != external_name(item["id"], "transition") or item["confirmation"] != "RECONCILE"
                or item["predecessor_disposition"] != "INCOMPLETE_SUPERSEDED"
                or item["unused_original_start"] != "PERMANENTLY_RELINQUISHED_NO_CONTROL"
                or type(item["version"]) is not str or not re.fullmatch("v1-[0-9a-f]{64}", item["version"])
                or type(item["serial"]) is not int or not 2 <= item["serial"] <= 512):
            external_refused("invalid external authority/schema/disposition")
        candidates.append(item)
    candidates.sort(key=lambda item: item["serial"])
    if len({item["id"] for item in candidates}) != len(candidates) or len({item["serial"] for item in candidates}) != len(candidates):
        external_refused("duplicate/resealed external authority/source append")
    required = {item["id"] for item in candidates} | ({current} if current is not None else set())
    if required and (type(originals) is not dict or not required <= set(originals)):
        external_refused("explicit original certificate required for every supersession; cached evidence is not an independent root")
    if originals is not None and set(originals) != required:
        external_refused("unknown/unused original certificate; select exactly the supersession history and current authority")
    external_original_fence(originals)
    for item in candidates:
        identifier = item["id"]
        original = external_original(originals, identifier)
        bound_certificate = unique_json(original.data)
        if (document(item["certificate"]) != original.data or type(item["input_provenance"]) is not dict
                or item["input_provenance"].get("certificate") != original.identity):
            external_refused("cached certificate/provenance differs from explicitly selected original certificate")
        roles = ("transition", "certificate", *EXTERNAL_ROLES, "successor", "committed")
        known.update(external_name(identifier, role) for role in roles)
        old, records, original_set = external_predecessor(root, installed, files, identifier)
        source_ids = external_source_bridge(root, old, item["version"], item["serial"], item["manifest"], trusted)
        all_versions = external_version_prefix(root)
        if (item["predecessor_sha256"] != cfg.digest(document(old)) or item["original_set"] != original_set
                or item["sources"] != source_ids or item["prefix"] != active_prefix(root, item["serial"])
                or item["permanent"] != old["permanent"]):
            external_refused("external predecessor/source/prefix/physical lineage differs")
        external_object(item["input_provenance"], ("certificate", *EXTERNAL_ROLES))
        raw_inputs = {}
        attachment_ids = {}
        for role in ("certificate", *EXTERNAL_ROLES):
            expected = item["input_provenance"][role]
            attempt_identity_domain(expected, expected["size"], installed.runtime.uid)
            stored = files.read(external_name(identifier, role))
            if stored is not None:
                attachment_ids[role] = record_identity(files.path/external_name(identifier, role))
            if role == "certificate":
                if stored is not None and stored != original.data:
                    external_refused("cached certificate bytes differ from selected independent original")
                stored = original.data
            elif stored is None:
                if supplied is None or role not in supplied:
                    external_refused("pending authority attachment missing; exact local input required, no controls")
                stored = supplied[role]
            if cfg.digest(stored) != expected["sha256"] or len(stored) != expected["size"]:
                external_refused("raw attachment differs from bound owned input provenance")
            raw_inputs[role] = stored
        if raw_inputs["certificate"] != document(item["certificate"]):
            external_refused("certificate bytes differ from bound canonical operator account")
        # Only the operator-selected external original defines the promise.
        # Every cache/provenance/link may be coherently resealed; none selects
        # this input or its path. The same pinned original is mandatory on the
        # ordinary RETIRE, explicit-entry, partial, floor and read-only paths.
        preserved = bound_certificate.get("source_version_prefix")
        if type(preserved) is not dict or item["version_prefix"] != preserved:
            external_refused("non-shrinkable source prefix differs from original raw certificate")
        referenced = set()
        for number in range(1, item["serial"] + 1):
            ref = unique_json(private_read(root/"active"/f"{number:08d}.json"))
            if (type(ref) is not dict or set(ref) != {"schema", "version"} or type(ref["schema"]) is not int or ref["schema"] != 1
                    or type(ref["version"]) is not str or not re.fullmatch("v1-[0-9a-f]{64}", ref["version"])):
                external_refused("unknown preserved source reference")
            referenced.add(ref["version"])
        if not referenced <= set(preserved) or any(all_versions.get(n) != digest for n, digest in preserved.items()):
            external_refused("complete certificate source-version physical prefix or sealed-reference coverage differs")
        promised = files.read(external_name(identifier, "successor"))
        descriptor = files.read(external_name(identifier, "committed"))
        promise = None
        if promised is not None:
            obj = external_object(unique_json(promised), {"schema", "intent_sha256", "intent_identity", "attachments", "promise"})
            if (promised != document(obj) or type(obj["schema"]) is not int or obj["schema"] != 1
                    or obj["intent_sha256"] != cfg.digest(document(item))
                    or obj["intent_identity"] != record_identity(files.path/external_name(identifier, "transition"))
                    or set(attachment_ids) != {"certificate", *EXTERNAL_ROLES} or obj["attachments"] != attachment_ids):
                external_refused("successor does not seal exact intent/attachment physical identities")
            promise = external_object(obj["promise"], {"evidence", "boundary", "replacement", "blocked"})
            if cfg.digest(document(promise)) != item["promise_sha256"]:
                external_refused("successor differs from pre-authority fixed promise")
            _, _, blocked, replacement = attempt_promise(installed, records, promise["evidence"], old["admission"])
            if promise["replacement"] != replacement or promise["blocked"] != blocked.hex():
                external_refused("external promise redefines original-derived R+1/records/enrollment")
            boundary = attempt_boundary(promise["boundary"], installed)
            if asdict(boundary.generation) != promise["evidence"]["generation"]:
                external_refused("successor scope generation differs from its qualified evidence")
        else:
            # Partial intent has no machine-generated successor evidence yet.
            # The provided raw context can validate the bounded certificate, but
            # only the real current attempt_sample may authorize its publication.
            context = unique_json(raw_inputs["context"])
            gen = retirement_generation(item["certificate"]["external"]["generation"], installed.runtime.uid)
            raw_scope = context.get("current_old_path_identity", {})
            boundary = rt.RetirementBoundary(gen, {}, old["admission"]["boundary"]["cgroup"],
                tuple(raw_scope.get(k) for k in ("device", "inode", "mode", "uid", "gid")),
                tuple(old["admission"]["boundary"]["parent_identity"]))
        external_certificate(bound_certificate, {k: raw_inputs[k] for k in EXTERNAL_ROLES}, old, original_set,
                             item["manifest"], item["serial"], boundary, preserved)
        desc = None
        if descriptor is not None:
            desc = external_object(unique_json(descriptor), {"schema", "intent_identity", "successor_identity", "live_records"})
            if (descriptor != document(desc) or type(desc["schema"]) is not int or desc["schema"] != 1 or promise is None
                    or desc["intent_identity"] != record_identity(files.path/external_name(identifier, "transition"))
                    or desc["successor_identity"] != record_identity(files.path/external_name(identifier, "successor"))
                    or set(desc["live_records"]) != set(RELEASE_RECORDS)):
                external_refused("invalid sealed-supersession descriptor")
            for name, identity in desc["live_records"].items():
                intended = bytes.fromhex(promise["blocked"] if name == "reservation.json" else promise["replacement"][name])
                attempt_identity_domain(identity, len(intended), installed.runtime.uid)
                if identity["sha256"] != cfg.digest(intended):
                    external_refused("descriptor live records contradict original-derived promise")
        floor = attempt_shape(installed, records)[1].status.revision + 1
        live = files.read("reservation.json", rs.MAX_STATE)
        pending = live is None or live == RELEASE_BLOCKED or (promise is not None and live == bytes.fromhex(promise["blocked"]))
        if not pending:
            current = rs._decode(live, rs.HostIdentity(installed.host_uniqueid, installed.base_port))
            if current.status.revision < floor:
                if desc is not None or record_identity(files.path/"reservation.json") != old["records"]["reservation.json"]:
                    external_refused("reservation rollback below exact original-derived external floor")
                pending = True
            elif desc is None or (current.status.revision == floor and live != bytes.fromhex(promise["replacement"]["reservation.json"])):
                external_refused("unsealed or counterfeit external availability")
        if pending and desc is not None:
            sealed = {external_name(identifier, role): record_identity(files.path/external_name(identifier, role)) for role in roles}
            sealed.update({name+"."+identifier+".bak": old["records"][name] for name in RELEASE_RECORDS})
            if external_descendant_floor(root, installed, files, identifier, floor, sealed, originals):
                pending = False
        if pending and all_versions != preserved:
            external_refused("pending authority source set differs from its complete original certificate prefix")
        entries.append((item, promise, desc, not pending))
        if not pending:
            # All four originals MUST still be real archives, not live aliases.
            for name in RELEASE_RECORDS:
                if record_identity(files.path/(name+"."+identifier+".bak")) != old["records"][name]:
                    external_refused("sealed original archive missing/replaced")
            trusted.append(item["manifest"])
    if any(p.name not in known for p in files.path.glob("external-*.bak")):
        external_refused("orphan/unknown external authority artifact")
    external_original_fence(originals)
    return entries


def external_replay(installed, files, transport, info, originals=None):
    root = installed.state_directory.parent
    external_authorities(root, installed, files, originals=originals)
    version, serial = active(root)
    directories = (root, root/"versions", root/"active", files.path, *(root/"versions").iterdir())
    anchors = {p: private_directory(p) for p in directories}
    def snapshot():
        paths = [root/".install.lock", *files.path.iterdir(), *(root/"active").iterdir()]
        paths.extend(p for folder in (root/"versions").iterdir() for p in folder.iterdir())
        return {str(p.relative_to(root)): record_identity(p) for p in paths}
    before = snapshot()
    disk = cfg.load(installed.runtime.config_file)[0]
    desktop = lc.inspected_desktop(installed, disk, installed.runtime.config_file.parent)
    validate_state(installed, desktop, files)
    controller = lc.HostController(installed, rt.LinuxRuntime(installed.runtime, transport), None, info, None)
    controller.files = files
    current = controller.runtime.observe()
    retirement_receipt(controller, desktop, current, None, controller.meta()["enrollment"], disk, disk, transport, committed=True)
    state = rs._decode(files.read("reservation.json", rs.MAX_STATE), controller.host)
    if (active(root) != (version, serial) or any(private_directory(p) != ident for p, ident in anchors.items())
            or before != snapshot()):
        external_refused("replay source/history/live evidence changed after last observation")
    external_authorities(root, installed, files, originals=originals)
    external_original_fence(originals)
    return response(True, action="reconcile-external-successor", **lc.HostController.status_fields(state.status))


def reconcile_external(root, version, serial, installed, files, transport, info, marker, inputs, input_ids, authorize, origin_fence=lambda: None, originals=None):
    """Distinct attended authority: NO control call, on any branch or replay."""
    certificate = unique_json(inputs["certificate"])
    if inputs["certificate"] != document(certificate):
        external_refused("certificate must be bounded canonical unique JSON")
    external_object(certificate, {"schema", "kind", "predecessor", "approved_source", "external", "account", "ordering", "unknown", "attachments", "source_version_prefix"})
    identifier = certificate["predecessor"]["id"]
    selected = external_original(originals, identifier)
    if selected.data != inputs["certificate"] or selected.identity != input_ids["certificate"]:
        external_refused("current certificate differs from its explicitly selected original snapshot")
    entries = external_authorities(root, installed, files, supplied=inputs, originals=originals, current=identifier)
    existing = next((entry for entry in entries if entry[0]["id"] == identifier), None)
    if existing is not None and (existing[0]["certificate"] != certificate or existing[0]["input_provenance"] != input_ids):
        external_refused("supplied operator account/provenance contradicts the original authority")
    if any(not entry[3] and entry is not existing for entry in entries):
        external_refused("another external authority is incomplete")
    if existing is not None and existing[3]:
        result = external_replay(installed, files, transport, info, originals)
        external_authorities(root, installed, files, supplied=inputs, originals=originals, current=identifier)
        origin_fence(); external_original_fence(originals)
        return result
    old, records, original_set = external_predecessor(root, installed, files, identifier)
    if existing is None and any(files.read(name+"."+identifier+".bak") is not None for name in RELEASE_RECORDS):
        external_refused("originals were archived without their exact external authority; never reseal/restart recovery")
    sources = external_source_bridge(root, old, version, serial, marker, [entry[0]["manifest"] for entry in entries if entry[3]])
    anchors = {p: private_directory(p) for p in (root, root/"versions", root/"active", files.path,
                                              root/"versions"/old["version"], root/"versions"/version)}
    prefix = active_prefix(root, serial)
    version_prefix = external_version_prefix(root)
    pins = {}
    item = existing[0] if existing else None
    successor = existing[1] if existing else None
    descriptor = existing[2] if existing else None
    def pin(role):
        name = external_name(identifier, role)
        data = private_read(files.path/name, lc.MAX_PRIVATE)
        identity = record_identity(files.path/name)
        if name in pins and pins[name] != (data, identity):
            external_refused("new authority artifact changed physically")
        pins[name] = data, identity
        return identity
    for role in ("transition", "certificate", *EXTERNAL_ROLES, "successor", "committed"):
        if files.read(external_name(identifier, role)) is not None:
            pin(role)
    def physical(final=False, ready=None):
        # Filesystem-only: MUST follow the last external observation.
        if (any(private_directory(p) != identity for p, identity in anchors.items()) or active(root) != (version, serial)
                or active_prefix(root, serial) != prefix or validate_version(root/"versions"/version) != marker
                or external_version_prefix(root) != version_prefix):
            external_refused("installation/source/active references changed")
        old_now, records_now, set_now = external_predecessor(root, installed, files, identifier)
        if old_now != old or records_now != records or set_now != original_set:
            external_refused("original predecessor evidence/history/locks changed")
        if external_source_bridge(root, old, version, serial, marker, [entry[0]["manifest"] for entry in entries if entry[3]]) != sources:
            external_refused("old/new source physical identities changed")
        allowed = set(original_set) | {name+"."+identifier+".bak" for name in RELEASE_RECORDS} | set(pins)
        if any(p.name not in allowed for p in files.path.iterdir()):
            external_refused("unknown artifact/history after last observation")
        for name, (data, identity) in pins.items():
            if private_read(files.path/name) != data or record_identity(files.path/name) != identity:
                external_refused("new authority/attachment/descriptor replaced after observation")
        for entry in entries:
            if entry[0]["id"] != identifier:
                external_authorities(root, installed, files, supplied=inputs, originals=originals, current=identifier)
                break
        if final:
            for name in RELEASE_RECORDS:
                if record_identity(files.path/(name+"."+identifier+".bak")) != old["records"][name]:
                    external_refused("final original archive missing/replaced")
        if ready is not None:
            unlaunched_live(files, successor["replacement"], bytes.fromhex(successor["blocked"]), ready)
        origin_fence(); external_original_fence(originals)
    evidence, boundary = attempt_sample(installed, records, transport, info, old["admission"])
    external_certificate(certificate, {k: inputs[k] for k in EXTERNAL_ROLES}, old, original_set, marker, serial, boundary, version_prefix)
    _, available, blocked, replacement = attempt_promise(installed, records, evidence, old["admission"])
    promise = unique_json(document({"evidence": evidence, "boundary": asdict(boundary), "replacement": replacement, "blocked": blocked.hex()}))
    if successor is not None and successor != promise:
        external_refused("current successor differs from durable qualified promise")
    wanted = {"schema": 1, "kind": EXTERNAL_KIND, "id": identifier, "predecessor_sha256": cfg.digest(document(old)),
        "original_set": original_set, "version": version, "serial": serial, "manifest": marker, "sources": sources,
        "version_prefix": version_prefix, "prefix": prefix, "permanent": old["permanent"], "certificate": certificate, "input_provenance": input_ids,
        "promise_sha256": cfg.digest(document(promise)), "confirmation": "RECONCILE", "predecessor_disposition": "INCOMPLETE_SUPERSEDED",
        "unused_original_start": "PERMANENTLY_RELINQUISHED_NO_CONTROL"}
    if item is not None and item != wanted:
        external_refused("pending authority/source/fixed promise differs")
    # Complete specimens, including future physical identities, BEFORE the first
    # durable authority. These ceilings are serialization only, never evidence.
    ceiling = lambda data: attempt_identity_ceiling(len(data), installed.runtime.uid)
    intent_data = document(wanted)
    future_successor = document({"schema": 1, "intent_sha256": cfg.digest(intent_data), "intent_identity": ceiling(intent_data),
        "attachments": {k: ceiling(inputs[k]) for k in inputs}, "promise": promise})
    future_descriptor = document({"schema": 1, "intent_identity": ceiling(intent_data), "successor_identity": ceiling(future_successor),
        "live_records": {k: ceiling(blocked if k == "reservation.json" else bytes.fromhex(replacement[k])) for k in RELEASE_RECORDS}})
    specimens = {"transition": intent_data, "successor": future_successor, "committed": future_descriptor, **inputs,
                 **{"live-"+n: bytes.fromhex(data) for n, data in replacement.items()}}
    for key, data in specimens.items():
        limit = rs.MAX_STATE if key == "live-reservation.json" else lc.MAX_PRIVATE
        if len(data) > limit:
            external_refused(f"{key} complete serialization ceiling {len(data)} exceeds {limit}; no authority written")
    bounds = {k: len(v) for k, v in specimens.items()}
    def publish(role, data):
        if len(data) > bounds[role]:
            external_refused("new phase exceeds its complete pre-authority ceiling")
        name = external_name(identifier, role)
        if files.read(name) is None:
            create(files.path/name, data)
        elif files.read(name) != data:
            external_refused("new phase differs from exact intended bytes")
        pin(role)
    if item is None:
        if authorize(certificate) is not True:
            external_refused("informed local RECONCILE factual attestation/exclusive window required")
        again, again_boundary = attempt_sample(installed, records, transport, info, old["admission"])
        if again != evidence or again_boundary != boundary:
            external_refused("successor changed after factual confirmation")
        physical()
        publish("transition", intent_data)
        item = wanted
    physical()
    for role in ("certificate", *EXTERNAL_ROLES):
        publish(role, inputs[role])
    successor_data = document({"schema": 1, "intent_sha256": cfg.digest(intent_data), "intent_identity": pin("transition"),
        "attachments": {role: pin(role) for role in ("certificate", *EXTERNAL_ROLES)}, "promise": promise})
    publish("successor", successor_data)
    successor = promise
    if descriptor is not None:
        unlaunched_live(files, replacement, blocked, descriptor["live_records"])
    # Old lease first; missing reservation -> invalid sentinel -> lease-free
    # SELECTING. Existing dispatchers cannot acquire or enroll at any cut.
    for name in RELEASE_RECORDS:
        archive = name+"."+identifier+".bak"
        if files.read(archive) is None:
            if record_identity(files.path/name) != old["records"][name]:
                external_refused("original changed before archival")
            files.anchor(); os.rename(name, archive, src_dir_fd=files.fd, dst_dir_fd=files.fd); os.fsync(files.fd)
        if record_identity(files.path/archive) != old["records"][name]:
            external_refused("archive lost original physical identity")
        if name == "reservation.json" and files.read(name) is None:
            if record_identity(files.path/"operation.json") != old["records"]["operation.json"]:
                external_refused("old lease disappeared before sentinel")
            create(files.path/name, RELEASE_BLOCKED)
    for name in ("operation.json", "start-receipt.json", "prepare-backup.conf"):
        data = bytes.fromhex(replacement[name])
        if files.read(name) is None:
            create(files.path/name, data)
        elif files.read(name) != data:
            external_refused("live replacement contradicts original-derived compound")
        if name == "operation.json":
            if files.read("reservation.json") == RELEASE_BLOCKED:
                files.write("reservation.json", blocked, limit=rs.MAX_STATE)
            elif files.read("reservation.json") != blocked:
                external_refused("lease-free compound baseline differs")
    _, ready = unlaunched_live(files, replacement, blocked, descriptor["live_records"] if descriptor else None)
    for name, identity in ready.items():
        attempt_identity_domain(identity, len(blocked if name == "reservation.json" else bytes.fromhex(replacement[name])), installed.runtime.uid)
    publish("committed", document({"schema": 1, "intent_identity": pin("transition"), "successor_identity": pin("successor"), "live_records": ready}))
    final, final_boundary = attempt_sample(installed, records, transport, info, old["admission"])
    if final != evidence or final_boundary != boundary:
        external_refused("final successor/typed policy/jobs/scope changed or busy")
    physical(final=True, ready=ready)
    files.write("reservation.json", available, limit=rs.MAX_STATE)  # Sole availability publication; no later external call.
    return response(True, action="reconcile-external-successor", **lc.HostController.status_fields(
        rs._decode(available, rs.HostIdentity(installed.host_uniqueid, installed.base_port)).status))


def local_external_successor(certificate, kernel=None, anchored=None, context=None, lifecycle=None, *, authorize=interactive_external, transport=None, info=None, original_certificates=()):
    """Installer-only fixed NSS/TTY authority; input paths are local evidence,
    never settings/source selectors. No environment-selected or RPC dispatch.
    """
    if sys.platform != "linux" or os.geteuid() == 0 or os.getuid() != os.geteuid():
        external_refused("unprivileged local Linux desktop user required")
    uid = os.geteuid()
    home = Path(pwd.getpwuid(uid).pw_dir).resolve(strict=True)
    root = home/RELATIVE_ROOT
    if home.stat().st_uid != uid or root.parent.resolve(strict=True) != root.parent:
        external_refused("fixed canonical owned NSS installation required")
    if type(original_certificates) not in (tuple, list):
        external_refused("earlier original certificates must be explicit local file inputs")
    certificate_originals = external_originals((certificate, *original_certificates), root)
    components = (kernel, anchored, context, lifecycle)
    if any(p is None for p in components) and not all(p is None for p in components):
        external_refused("provide all four current component files, or none for sealed read-only replay")
    read_only = all(p is None for p in components)
    paths = {"certificate": certificate} if read_only else dict(zip(("certificate", *EXTERNAL_ROLES), (certificate, *components)))
    if len({Path(p).absolute() for p in paths.values()}) != len(paths):
        external_refused("distinct owned local certificate/component inputs required")
    inputs = {k: private_read(Path(p), lc.MAX_PRIVATE) for k, p in paths.items()}
    identities = {k: record_identity(Path(p)) for k, p in paths.items()}
    if any(identities[k]["sha256"] != cfg.digest(data) for k, data in inputs.items()):
        external_refused("input changed during owned provenance snapshot")
    private_read(root/".install.lock", 1)
    with installation_lock(root):
        if {p.name for p in root.iterdir()} != {".install.lock", "versions", "active", "state"}:
            external_refused("foreign/incomplete installation")
        version, serial = active(root)
        if version is None:
            external_refused("normal qualified ACTIVE source publication required")
        marker = validate_version(root/"versions"/version)
        source = Path(__file__).resolve().parent
        if any(cfg.digest(source_read(source/n)) != marker["files"][n] for n in SOURCES):
            external_refused("publish this exact source normally; no checkout/source adoption")
        installed = host.settings(root/"versions"/version/"host-settings.json", "linux")
        if installed.runtime.home != home or installed.state_directory != root/"state" or installed.runtime.uid != uid:
            external_refused("installed host/user/root differs from fixed NSS authority")
        for path in (root/"versions").iterdir():
            validate_version(path)
        private_read(root/"state/operation.lock", 1); private_read(root/"state/reservation.lock", 1)
        with lc.PrivateFiles(root/"state").locked() as files:
            if read_only:
                # A selected original alone suffices for an ALREADY sealed
                # history. It never creates/completes an authority, restores a
                # missing component, or publishes availability.
                entries = external_authorities(root, installed, files, originals=certificate_originals)
                identifier = unique_json(inputs["certificate"])["predecessor"]["id"]
                if not any(item["id"] == identifier and committed for item, _, _, committed in entries):
                    external_refused("component-free input is only for an already committed read-only supersession")
                result = external_replay(installed, files, transport or rt.RetirementTransport(home, uid), info or host.serverinfo, certificate_originals)
                external_original_fence(certificate_originals)
                return result
            def origin_fence():
                for key, path in paths.items():
                    if record_identity(Path(path)) != identities[key] or private_read(Path(path), lc.MAX_PRIVATE) != inputs[key]:
                        external_refused("original owned input changed after last observation")
            return reconcile_external(root, version, serial, installed, files, transport or rt.RetirementTransport(home, uid),
                                      info or host.serverinfo, marker, inputs, identities, authorize, origin_fence, certificate_originals)


def local_enroll_retirement(manifest, authorize, *, transport=None, info=None):
    """Return None for ordinary onboarding; exceptional authority is fixed-NSS local."""
    installed = host.settings(manifest, "linux")
    probe = lc.HostController(installed, None, None, None, None)
    probe.files = IdentityRecords({"operation.json": private_read(installed.state_directory / "operation.json")}
                                 if (installed.state_directory / "operation.json").exists() else {})
    if probe.meta()["lease"] is None and not list(installed.state_directory.glob("unlaunched-transition-*.bak")):
        return None
    uid = os.geteuid()
    home = Path(pwd.getpwuid(uid).pw_dir).resolve(strict=True)
    root = home / RELATIVE_ROOT
    if (home.stat().st_uid != uid or installed.runtime.home != home
            or installed.state_directory != root / "state" or root.parent.resolve(strict=True) != root.parent):
        unlaunched_refused("settings/installation parents do not belong to fixed canonical NSS home/state")
    private_read(root / ".install.lock", 1)  # never recreate missing permanent locks
    with installation_lock(root):
        anchors = {p: private_directory(p) for p in (root, root / "versions", root / "active", root / "state")}
        if {p.name for p in root.iterdir()} != {".install.lock", "versions", "active", "state"}:
            unlaunched_refused("foreign/incomplete installation")
        version, serial = active(root)
        expected = root / "versions" / str(version) / "host-settings.json"
        if Path(manifest) != expected or host.settings(expected, "linux") != installed:
            unlaunched_refused("caller is not the current immutable installed version")
        for path in (root / "versions").iterdir():
            validate_version(path)
        marker = validate_version(expected.parent)
        private_read(root / "state/operation.lock", 1)
        private_read(root / "state/reservation.lock", 1)
        with lc.PrivateFiles(root / "state").locked() as files:
            result = retire_unlaunched(root, version, serial, installed, files,
                                      transport or rt.LocalTransport(), info or host.serverinfo, marker, authorize)
            if any(private_directory(p) != identity for p, identity in anchors.items()):
                unlaunched_refused("installation directory identity changed")
            return result


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
            retired = local_enroll_retirement(manifest, interactive)
            if retired is not None:
                stdout.buffer.write(retired)
                return 0
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
    if sys.argv[1:2] == ["reconcile-external-successor"]:
        parser = argparse.ArgumentParser(description="Attended local zero-service-control reconciliation")
        parser.add_argument("action", choices=("reconcile-external-successor",))
        parser.add_argument("--certificate", required=True, type=Path)
        for key in EXTERNAL_ROLES:
            parser.add_argument("--"+key, type=Path)
        parser.add_argument("--original-certificate", action="append", type=Path, default=[], help="retained original for each earlier supersession; repeat per history")
        args = parser.parse_args()
        components = [getattr(args, key) for key in EXTERNAL_ROLES]
        if any(value is None for value in components) and not all(value is None for value in components):
            parser.error("provide all four current components, or none for committed read-only replay")
        present = ("certificate",) if all(value is None for value in components) else ("certificate", *EXTERNAL_ROLES)
        if (len(sys.argv[2:]) != 2*len(present) + 2*len(args.original_certificate)
                or any(sys.argv[2:].count("--"+k) != 1 for k in present)
                or sys.argv[2:].count("--original-certificate") != len(args.original_certificate)):
            parser.error("supply each current input once and earlier originals as explicit flag/file pairs")
        try:
            sys.stdout.buffer.write(local_external_successor(*(getattr(args, k) for k in ("certificate", *EXTERNAL_ROLES)), original_certificates=args.original_certificate))
            return 0
        except Refusal as exc:
            print(f"Display controller refused [{exc.code}]: {exc.detail}", file=sys.stderr)
        except Exception:
            print("Zero-control reconciliation incomplete or final publication outcome unconfirmed; original evidence retained. No service controls were issued. Re-run only the exact sealed continuation; unknown actor/helper facts require a bounded factual account, never another STOP/START.", file=sys.stderr)
        return 1
    if sys.argv[1:2] == ["reconcile-attempted-launch"]:
        parser = argparse.ArgumentParser(description="Attended local RETIRE with read-only original supersession proofs")
        parser.add_argument("action", choices=("reconcile-attempted-launch",))
        parser.add_argument("--original-certificate", action="append", type=Path, default=[], help="retained original for every prior supersession; repeat per history")
        args = parser.parse_args()
        if (len(sys.argv[2:]) != 2*len(args.original_certificate)
                or sys.argv[2:].count("--original-certificate") != len(args.original_certificate)):
            parser.error("supply original certificates as explicit repeated flag/file pairs")
        try:
            sys.stdout.buffer.write(local_attempt_retirement(original_certificates=args.original_certificate))
            return 0
        except Refusal as exc:
            print(f"Display controller refused [{exc.code}]: {exc.detail}", file=sys.stderr)
        except Exception:
            print("Attempt retirement incomplete; ownership/evidence retained. Issued STOP/START is never automatically repeated; rerun only to collect phase-bound evidence.", file=sys.stderr)
        return 1
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
