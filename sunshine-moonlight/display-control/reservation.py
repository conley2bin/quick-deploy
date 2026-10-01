"""Durable, single-authority host ownership; no protocol handler or runtime probes.

Only the installed internal controller supplies the private state directory and
constructs evidence records. These records are *contracts for trusted adapters*,
not claims a remote JSON request may make. In particular:

* IdleRuntime is host/endpoint/process/config/topology-validated application
  state, not a universal session counter.
* PositiveDesktop names an inspected positive-ID, empty-command Desktop whose
  definition digest and pinned Sunshine lifecycle have been checked.
* PreparedRuntime requires verified readiness/capture mapping and, on restart,
  actual termination of the previous Sunshine generation.
* DesktopActive follows this reservation's persisted launch/resume attempt.
* OwnedDesktopRunning/DesktopClosure use OwnershipBinding: fresh host,
  process generation and config, plus the exact inspected app. They authorize
  cleanup, not resume/launch; current topology/capture usability is irrelevant.
  DesktopClosure requires completed authenticated cancellation of the owned
  Desktop AFTER begin_release, then same-generation application state zero.
  Moonlight GUI exit0, EOF, timeout and raw FREE cannot construct this evidence.
* ServiceTermination proves the old SUNSHINE generation is dead, not the caller
  PID; ExclusiveOnboarding is the explicit operator exclusivity/idle ceremony,
  including no in-flight launch, not an automatic response to FREE.

Persist an attempt BEFORE external launch/resume; stop on every persistence
error. An interrupted operation keeps ownership. Capability.value belongs only
in private SSH-protected stdin/transport, never argv, logs or public status.
Windows uses restricted native owner/DACL checks and nonblocking one-byte locks;
its native execution requires separate platform acceptance.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from enum import Enum
import errno
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import uuid

from protocol import Refusal

__all__ = ["HostIdentity", "ServiceGeneration", "RuntimeBinding", "OwnershipBinding", "PositiveDesktop",
           "Phase", "Uncertainty", "Capability", "Status", "Reservation", "IdleRuntime",
           "PreparedRuntime", "DesktopActive", "DesktopRunning", "OwnedDesktopRunning", "DesktopClosure",
           "PrelaunchAbort", "SelectionAbort", "PreparedAbort", "ServiceTermination", "ExclusiveOnboarding", "ReservationStore"]

STATE_FILE = "reservation.json"
LOCK_FILE = "reservation.lock"  # Permanent inode: never unlink it to release ownership.
MAX_STATE = 16384
MAX_REVISION = (1 << 63) - 1
HEX = re.compile(r"[0-9a-f]{64}\Z")
ATOM = re.compile(r"[A-Za-z0-9_.:@/-]{1,128}\Z")


def _invalid(detail):
    raise Refusal("invalid_evidence", detail)


def _integer(value, low=0, high=MAX_REVISION):
    return type(value) is int and low <= value <= high


def _hex(value):
    return type(value) is str and HEX.fullmatch(value) is not None


def _uuid(value):
    if type(value) is not str:
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _typed(value, kind):
    if type(value) is not kind:
        _invalid("expected trusted " + kind.__name__ + " record")


@dataclass(frozen=True)
class HostIdentity:
    uuid: str
    base_port: int

    def __post_init__(self):
        if not _uuid(self.uuid) or not _integer(self.base_port, 1029, 65514):
            _invalid("host requires canonical instance UUID and base port")


@dataclass(frozen=True)
class ServiceGeneration:
    service_id: str
    boot_id: str
    pid: int
    start_id: str

    def __post_init__(self):
        if (any(type(v) is not str or not ATOM.fullmatch(v)
                for v in (self.service_id, self.boot_id, self.start_id)) or
                not _integer(self.pid, 1, (1 << 32) - 1)):
            _invalid("service requires owned identity, boot identity, PID and process birth identity")


@dataclass(frozen=True)
class RuntimeBinding:
    host: HostIdentity
    generation: ServiceGeneration
    topology_digest: str
    config_digest: str

    def __post_init__(self):
        _typed(self.host, HostIdentity)
        _typed(self.generation, ServiceGeneration)
        if not _hex(self.topology_digest) or not _hex(self.config_digest):
            _invalid("runtime requires topology and configuration SHA256 snapshots")


@dataclass(frozen=True)
class OwnershipBinding:
    """Fresh non-capture ownership facts. Deliberately contains no topology."""
    host: HostIdentity
    generation: ServiceGeneration
    config_digest: str

    def __post_init__(self):
        _typed(self.host, HostIdentity)
        _typed(self.generation, ServiceGeneration)
        if not _hex(self.config_digest):
            _invalid("ownership requires host/process/config SHA256 facts")

    @classmethod
    def from_runtime(cls, binding: RuntimeBinding):
        _typed(binding, RuntimeBinding)
        return cls(binding.host, binding.generation, binding.config_digest)


@dataclass(frozen=True)
class PositiveDesktop:
    app_id: int
    definition_digest: str

    def __post_init__(self):
        if not _integer(self.app_id, 1, (1 << 31) - 1) or not _hex(self.definition_digest):
            _invalid("Desktop requires positive app ID and inspected definition SHA256")


class Phase(str, Enum):
    AVAILABLE = "available"
    SELECTING = "selecting"
    PREPARING = "preparing"
    CONNECTING = "connecting"
    ACTIVE = "active"
    RECONNECTING = "reconnecting"
    RELEASING = "releasing"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class Uncertainty(str, Enum):
    NEEDS_ONBOARDING = "needs_onboarding"
    CONTROL_LOST = "control_lost"
    OWNER_LOST = "owner_lost"
    LAUNCH_UNCONFIRMED = "launch_unconfirmed"
    CLOSURE_UNCONFIRMED = "closure_unconfirmed"
    RUNTIME_CHANGED = "runtime_changed"


@dataclass(frozen=True, repr=False)
class Capability:
    value: str

    def __post_init__(self):
        if not _hex(self.value):
            _invalid("capability must be a 256-bit secret")

    def __repr__(self):
        return "Capability(<redacted>)"


@dataclass(frozen=True)
class Status:
    host: HostIdentity
    revision: int
    phase: Phase
    # Last full selection snapshot, not a claim that current displays remain
    # unchanged. Cleanup observes OwnershipBinding; acquire refreshes topology.
    binding: RuntimeBinding | None = None
    desktop: PositiveDesktop | None = None
    attempt_revision: int | None = None
    active_observed: bool = False
    uncertainty: Uncertainty | None = None

    @property
    def launch_attempted(self) -> bool:
        return self.attempt_revision is not None


@dataclass(frozen=True)
class Reservation:
    capability: Capability = field(repr=False)
    status: Status


@dataclass(frozen=True)
class IdleRuntime:
    binding: RuntimeBinding
    application_id: int


@dataclass(frozen=True)
class PreparedRuntime:
    previous: RuntimeBinding
    current: RuntimeBinding
    desktop: PositiveDesktop
    application_id: int
    terminated_generation: ServiceGeneration | None = None


@dataclass(frozen=True)
class DesktopActive:
    binding: RuntimeBinding
    desktop: PositiveDesktop
    application_id: int
    attempt_revision: int


@dataclass(frozen=True)
class DesktopRunning:
    binding: RuntimeBinding
    desktop: PositiveDesktop
    application_id: int


@dataclass(frozen=True)
class OwnedDesktopRunning:
    binding: OwnershipBinding
    desktop: PositiveDesktop
    application_id: int


@dataclass(frozen=True)
class DesktopClosure:
    binding: OwnershipBinding
    desktop: PositiveDesktop
    application_id: int
    release_revision: int
    cancel_transaction: str  # Local UUID correlating completed authenticated cancel.


@dataclass(frozen=True)
class SelectionAbort:
    binding: OwnershipBinding
    application_id: int  # Untouched SELECTING only; no native/capture claim.


@dataclass(frozen=True)
class PreparedAbort:
    binding: OwnershipBinding
    desktop: PositiveDesktop
    application_id: int
    prepared_revision: int  # Actual completed prepared transition, never mere PREPARING.


@dataclass(frozen=True)
class PrelaunchAbort:
    binding: RuntimeBinding
    application_id: int  # Includes verified transaction cleanup; no external launch issued.


@dataclass(frozen=True)
class ServiceTermination:
    terminated_generation: ServiceGeneration
    current: IdleRuntime


@dataclass(frozen=True)
class ExclusiveOnboarding:
    current: IdleRuntime
    ceremony_id: str  # Trusted operator ceremony, never a remote "force" field.


@dataclass(frozen=True)
class _State:
    status: Status
    owner_digest: str | None = field(default=None, repr=False)


def _host_json(host):
    return {"uuid": host.uuid, "base_port": host.base_port}


def _generation_json(generation):
    return {"service_id": generation.service_id, "boot_id": generation.boot_id,
            "pid": generation.pid, "start_id": generation.start_id}


def _binding_json(binding):
    if binding is None:
        return None
    return {"host": _host_json(binding.host), "generation": _generation_json(binding.generation),
            "topology_digest": binding.topology_digest, "config_digest": binding.config_digest}


def _desktop_json(desktop):
    return None if desktop is None else {"app_id": desktop.app_id,
                                        "definition_digest": desktop.definition_digest}


def _object(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError("invalid object fields")
    return value


def _parse_host(value):
    return HostIdentity(**_object(value, ("uuid", "base_port")))


def _parse_generation(value):
    return ServiceGeneration(**_object(value, ("service_id", "boot_id", "pid", "start_id")))


def _parse_binding(value):
    if value is None:
        return None
    obj = _object(value, ("host", "generation", "topology_digest", "config_digest"))
    return RuntimeBinding(_parse_host(obj["host"]), _parse_generation(obj["generation"]),
                          obj["topology_digest"], obj["config_digest"])


def _encode(state):
    status = state.status
    data = {"schema": 1, "host": _host_json(status.host), "revision": status.revision,
            "phase": status.phase.value, "binding": _binding_json(status.binding),
            "desktop": _desktop_json(status.desktop), "attempt_revision": status.attempt_revision,
            "active_observed": status.active_observed,
            "uncertainty": status.uncertainty.value if status.uncertainty else None,
            "owner_digest": state.owner_digest}
    return json.dumps(data, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n"


def _decode(data, host):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        obj = json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
        _object(obj, ("schema", "host", "revision", "phase", "binding", "desktop",
                      "attempt_revision", "active_observed", "uncertainty", "owner_digest"))
        if type(obj["schema"]) is not int or obj["schema"] != 1:
            raise ValueError("invalid schema")
        stored_host = _parse_host(obj["host"])
        binding = _parse_binding(obj["binding"])
        desktop = (PositiveDesktop(**_object(obj["desktop"], ("app_id", "definition_digest")))
                   if obj["desktop"] is not None else None)
        phase = Phase(obj["phase"])
        uncertainty = Uncertainty(obj["uncertainty"]) if obj["uncertainty"] is not None else None
        owner, revision, attempt, active = (obj[k] for k in
                                           ("owner_digest", "revision", "attempt_revision", "active_observed"))
        if not _integer(revision, 1) or type(active) is not bool or (owner is not None and not _hex(owner)):
            raise ValueError("invalid revision/owner/active")
        if binding is not None and binding.host != stored_host:
            raise ValueError("inconsistent binding")
        if attempt is not None and (not _integer(attempt, 1, revision) or desktop is None):
            raise ValueError("invalid attempt")
        if active and attempt is None:
            raise ValueError("active without attempt")
        if owner is None and phase not in (Phase.AVAILABLE, Phase.RECONCILIATION_REQUIRED):
            raise ValueError("phase without owner")
        if owner is None and (desktop is not None or attempt is not None or active):
            raise ValueError("lifecycle without owner")
        if phase is not Phase.RECONCILIATION_REQUIRED and binding is None:
            raise ValueError("phase without binding")
        if owner is not None and binding is None:
            raise ValueError("owner without binding")
        if (phase is Phase.RECONCILIATION_REQUIRED) != (uncertainty is not None):
            raise ValueError("inconsistent uncertainty")
        if phase is Phase.AVAILABLE and owner is not None:
            raise ValueError("available with owner")
        if phase is Phase.SELECTING and desktop is not None:
            raise ValueError("selecting with Desktop")
        if phase in (Phase.SELECTING, Phase.PREPARING) and (attempt is not None or active):
            raise ValueError("prelaunch with attempt")
        if phase is Phase.CONNECTING and (attempt is None or active):
            raise ValueError("invalid connecting lifecycle")
        if phase in (Phase.ACTIVE, Phase.RECONNECTING, Phase.RELEASING) and not active:
            raise ValueError("unobserved active lifecycle")
        result = _State(Status(stored_host, revision, phase, binding, desktop, attempt, active, uncertainty), owner)
    except (ValueError, TypeError, UnicodeError, RecursionError, Refusal) as exc:
        raise Refusal("reservation_corrupt", "persistent reservation is invalid; trusted repair required") from exc
    if stored_host != host:
        raise Refusal("host_mismatch", "reservation belongs to a different host UUID/base port")
    return result


def _identity(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid


def _stamp(info):
    return _identity(info) + (info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _metadata(info, directory=False):
    valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if (not valid_type or getattr(info, "st_file_attributes", 0) & 0x400 or
            (not directory and info.st_nlink != 1)):
        raise Refusal("reservation_unsafe", "reservation artifacts must be unlinked regular files/private directory")
    if os.name != "nt" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise Refusal("reservation_unsafe", "reservation artifacts must be owned and owner-private")


def _windows_private_handle(handle):
    """Check kernel-object owner/DACL; POSIX mode bits do not describe NT ACLs.

    Permit current token user, LocalSystem and Builtin Administrators only.
    Null/unknown DACL/ACE types refuse. Native APIs are intentionally lazy so
    Linux fixture tests cannot accidentally exercise installed Windows state.
    """
    import ctypes as C
    from ctypes import wintypes as W

    adv = C.WinDLL("advapi32", use_last_error=True)
    kernel = C.WinDLL("kernel32", use_last_error=True)
    adv.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
    adv.OpenProcessToken.restype = W.BOOL
    adv.GetTokenInformation.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]
    adv.GetTokenInformation.restype = W.BOOL
    adv.GetSecurityInfo.argtypes = [W.HANDLE, C.c_int, W.DWORD, C.POINTER(C.c_void_p),
                                    C.c_void_p, C.POINTER(C.c_void_p), C.c_void_p, C.POINTER(C.c_void_p)]
    adv.GetSecurityInfo.restype = W.DWORD
    adv.EqualSid.argtypes = [C.c_void_p, C.c_void_p]
    adv.EqualSid.restype = W.BOOL
    adv.GetAclInformation.argtypes = [C.c_void_p, C.c_void_p, W.DWORD, C.c_int]
    adv.GetAclInformation.restype = W.BOOL
    adv.GetAce.argtypes = [C.c_void_p, W.DWORD, C.POINTER(C.c_void_p)]
    adv.GetAce.restype = W.BOOL
    adv.IsValidSid.argtypes = [C.c_void_p]
    adv.IsValidSid.restype = W.BOOL
    adv.GetLengthSid.argtypes = [C.c_void_p]
    adv.GetLengthSid.restype = W.DWORD
    adv.ConvertStringSidToSidW.argtypes = [W.LPCWSTR, C.POINTER(C.c_void_p)]
    adv.ConvertStringSidToSidW.restype = W.BOOL
    kernel.GetCurrentProcess.restype = W.HANDLE
    kernel.CloseHandle.argtypes = [W.HANDLE]
    kernel.LocalFree.argtypes = [C.c_void_p]
    kernel.LocalFree.restype = C.c_void_p
    token, descriptor = W.HANDLE(), C.c_void_p()
    allocated = []

    def checked(value):
        if not value:
            raise Refusal("reservation_unsafe", "Windows reservation security could not be established")

    try:
        checked(adv.OpenProcessToken(kernel.GetCurrentProcess(), 0x8, C.byref(token)))
        size = W.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, C.byref(size))  # TokenUser size query.
        checked(0 < size.value <= 65536)
        user_buffer = C.create_string_buffer(size.value)
        checked(adv.GetTokenInformation(token, 1, user_buffer, size, C.byref(size)))
        user = C.c_void_p.from_buffer(user_buffer).value  # TOKEN_USER begins with SID pointer.
        owner, acl = C.c_void_p(), C.c_void_p()
        checked(adv.GetSecurityInfo(handle, 1, 0x1 | 0x4, C.byref(owner), None,
                                    C.byref(acl), None, C.byref(descriptor)) == 0)
        checked(owner.value and acl.value and adv.EqualSid(owner, user))
        allowed = [user]
        for sid_text in ("S-1-5-18", "S-1-5-32-544"):
            sid = C.c_void_p()
            checked(adv.ConvertStringSidToSidW(sid_text, C.byref(sid)))
            allocated.append(sid)
            allowed.append(sid.value)

        class AclSize(C.Structure):
            _fields_ = [("count", W.DWORD), ("used", W.DWORD), ("free", W.DWORD)]

        sizes = AclSize()
        checked(adv.GetAclInformation(acl, C.byref(sizes), C.sizeof(sizes), 2))
        checked(sizes.count <= 1024)
        for index in range(sizes.count):
            ace = C.c_void_p()
            checked(adv.GetAce(acl, index, C.byref(ace)))
            header = C.string_at(ace, 4)
            ace_type, ace_size = header[0], int.from_bytes(header[2:4], "little")
            checked(ace_type in (0, 1) and ace_size >= 12)  # ALLOW or DENY; unfamiliar ACE refuses.
            sid_address = ace.value + 8
            checked(adv.IsValidSid(sid_address) and adv.GetLengthSid(sid_address) <= ace_size - 8)
            if ace_type == 0:
                checked(any(adv.EqualSid(sid_address, permitted) for permitted in allowed))
    finally:
        for sid in allocated:
            kernel.LocalFree(sid)
        if descriptor.value:
            kernel.LocalFree(descriptor)
        if token.value:
            kernel.CloseHandle(token)


def _windows_private_directory(path):
    import ctypes as C
    from ctypes import wintypes as W
    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p,
                                   W.DWORD, W.DWORD, W.HANDLE]
    kernel.CreateFileW.restype = W.HANDLE
    kernel.CloseHandle.argtypes = [W.HANDLE]
    # OPEN_REPARSE_POINT prevents this handle from silently following a junction.
    handle = kernel.CreateFileW(str(path), 0x20000, 0x1 | 0x2 | 0x4, None, 3,
                                 0x02000000 | 0x00200000, None)
    if handle == C.c_void_p(-1).value:
        raise Refusal("reservation_unsafe", "private Windows state directory unavailable")
    try:
        _windows_private_handle(handle)
    finally:
        kernel.CloseHandle(handle)


@contextmanager
def _byte_lock(fd, api):
    """Windows fixture seam: initialized byte zero, nonblocking, no implicit retries."""
    acquired = False
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            api.locking(fd, api.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise Refusal("reservation_contended", "another state update holds the Windows byte lock") from exc
            raise
        acquired = True
        yield
    finally:
        if acquired:
            os.lseek(fd, 0, os.SEEK_SET)
            api.locking(fd, api.LK_UNLCK, 1)


@contextmanager
def _kernel_lock(fd):
    if os.name == "nt":
        import msvcrt
        with _byte_lock(fd, msvcrt):
            yield
    else:
        import fcntl
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Refusal("reservation_contended", "another state update holds the POSIX lock") from exc
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)


class _Directory:
    def __init__(self, path):
        self.path, self.fd, self.info = path, None, None

    def __enter__(self):
        before = self.path.lstat()
        _metadata(before, directory=True)
        if os.name == "nt":
            _windows_private_directory(self.path)
            self.info = before
        else:
            self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self.info = os.fstat(self.fd)
        try:
            _metadata(self.info, directory=True)
            if _identity(before) != _identity(self.info):
                raise Refusal("reservation_unsafe", "state directory changed while opened")
            self.check()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def check(self):
        now = self.path.lstat()
        _metadata(now, directory=True)
        if _identity(now) != _identity(self.info):
            raise Refusal("reservation_unsafe", "state directory was replaced")
        if os.name == "nt":
            _windows_private_directory(self.path)

    def stat(self, name):
        if self.fd is None:
            return (self.path / name).lstat()
        return os.stat(name, dir_fd=self.fd, follow_symlinks=False)

    def open(self, name, flags):
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        return os.open(self.path / name, flags, 0o600) if self.fd is None else os.open(name, flags, 0o600, dir_fd=self.fd)

    def verify(self, name, fd, before=None):
        info = os.fstat(fd)
        _metadata(info)
        if before is not None and _stamp(before) != _stamp(info):
            raise Refusal("reservation_unsafe", "state artifact changed while opened")
        if _stamp(self.stat(name)) != _stamp(info):
            raise Refusal("reservation_unsafe", "state artifact replaced while opened")
        if os.name == "nt":
            import msvcrt
            _windows_private_handle(msvcrt.get_osfhandle(fd))
        self.check()
        return info

    def unlink(self, name):
        if self.fd is None:
            os.unlink(self.path / name)
        else:
            os.unlink(name, dir_fd=self.fd)

    def publish(self, source, absent):
        if self.fd is None:
            if absent:
                os.link(self.path / source, self.path / STATE_FILE)
            else:
                os.replace(self.path / source, self.path / STATE_FILE)
        elif absent:
            os.link(source, STATE_FILE, src_dir_fd=self.fd, dst_dir_fd=self.fd, follow_symlinks=False)
        else:
            os.replace(source, STATE_FILE, src_dir_fd=self.fd, dst_dir_fd=self.fd)

    def sync(self):
        if self.fd is not None:
            try:
                os.fsync(self.fd)
            except OSError as exc:
                if exc.errno not in (errno.EINVAL, errno.ENOTSUP):
                    raise
        # Windows: os.fsync flushes each file; Python has no directory fsync.


class ReservationStore:
    """Installation-selected private directory + immutable host identity.

    Each mutation returns a new revision-bearing Reservation (or terminal
    Status). Old tickets and old lifecycle observations are unusable. No method
    discovers/creates the directory, touches Sunshine, or accepts public JSON.
    """
    def __init__(self, directory: Path, host: HostIdentity):
        _typed(host, HostIdentity)
        if not isinstance(directory, Path) or not directory.is_absolute():
            _invalid("state directory must be a trusted absolute Path")
        self.directory, self.host = directory, host

    @contextmanager
    def _transaction(self):
        try:
            with _Directory(self.directory) as directory:
                try:
                    fd = directory.open(LOCK_FILE, os.O_RDWR | os.O_CREAT | os.O_EXCL)
                except FileExistsError:
                    before = directory.stat(LOCK_FILE)
                    _metadata(before)
                    fd = directory.open(LOCK_FILE, os.O_RDWR)
                else:
                    before = None
                    try:
                        if os.write(fd, b"\0") != 1:
                            raise OSError("short lock write")
                        os.fsync(fd)
                        directory.sync()
                    except BaseException:
                        os.close(fd)
                        raise
                try:
                    info = directory.verify(LOCK_FILE, fd, before)
                    if info.st_size != 1:
                        raise Refusal("reservation_unsafe", "lock must contain one initialized byte; trusted repair required")
                    with _kernel_lock(fd):
                        directory.verify(LOCK_FILE, fd)
                        yield directory
                        directory.verify(LOCK_FILE, fd)
                finally:
                    os.close(fd)
        except OSError as exc:
            raise Refusal("reservation_io", "reservation storage/lock unavailable; ownership is not released") from exc

    def _load(self, directory):
        try:
            before = directory.stat(STATE_FILE)
        except FileNotFoundError:
            return _State(Status(self.host, 0, Phase.RECONCILIATION_REQUIRED,
                                 uncertainty=Uncertainty.NEEDS_ONBOARDING)), None
        _metadata(before)
        if not 1 <= before.st_size <= MAX_STATE:
            raise Refusal("reservation_corrupt", "reservation exceeds its size bound or is empty")
        fd = directory.open(STATE_FILE, os.O_RDONLY)
        try:
            opened = directory.verify(STATE_FILE, fd, before)
            with os.fdopen(fd, "rb", closefd=False) as stream:
                data = stream.read(MAX_STATE + 1)
            after = directory.verify(STATE_FILE, fd)
            if _stamp(opened) != _stamp(after) or len(data) != opened.st_size:
                raise Refusal("reservation_corrupt", "reservation changed during read")
        finally:
            os.close(fd)
        return _decode(data, self.host), (_stamp(after), data)

    def _save(self, directory, previous_stamp, state):
        data = _encode(state)
        if len(data) > MAX_STATE:
            raise Refusal("reservation_corrupt", "internal reservation exceeds bound")
        name = ".reservation-write-" + secrets.token_hex(16)
        fd = directory.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            try:
                directory.verify(name, fd)
                with os.fdopen(fd, "wb", closefd=False) as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(fd)
                directory.verify(name, fd)
            finally:
                os.close(fd)
            directory.check()
            _, now_stamp = self._load(directory)
            if now_stamp != previous_stamp:
                raise Refusal("reservation_corrupt", "reservation changed outside its serialized update")
            directory.publish(name, absent=previous_stamp is None)
            if previous_stamp is None:
                # Sync the readable one-link state, not its temporary hardlink alias.
                directory.unlink(name)
            directory.sync()
        except OSError as exc:
            raise Refusal("persistence_uncertain", "reservation publication may be incomplete; stop and reconcile") from exc
        finally:
            try:
                directory.unlink(name)
            except FileNotFoundError:
                pass

    @staticmethod
    def _revision(state, expected):
        if not _integer(expected) or expected != state.status.revision:
            raise Refusal("stale_revision", "reservation revision changed; stale request refused")
        if expected == MAX_REVISION:
            raise Refusal("reservation_corrupt", "reservation revision exhausted")

    def _owner(self, state, ticket):
        _typed(ticket, Reservation)
        _typed(ticket.capability, Capability)
        _typed(ticket.status, Status)
        candidate = hashlib.sha256(ticket.capability.value.encode("ascii")).hexdigest()
        if state.owner_digest is None or not hmac.compare_digest(state.owner_digest, candidate):
            raise Refusal("not_owner", "capability does not own this host reservation")
        self._revision(state, ticket.status.revision)
        if ticket.status != state.status:
            raise Refusal("binding_mismatch", "ticket does not match the durable reservation")

    @staticmethod
    def _phase(state, *allowed):
        if state.status.phase not in allowed:
            raise Refusal("phase_mismatch", "operation is not permitted in the durable reservation phase")

    def _update(self, directory, stamp, state, **changes):
        new = _State(replace(state.status, revision=state.status.revision + 1, **changes), state.owner_digest)
        self._save(directory, stamp, new)
        return new

    def _bound(self, directory, stamp, state, binding):
        _typed(binding, RuntimeBinding)
        if binding != state.status.binding:
            self._update(directory, stamp, state, phase=Phase.RECONCILIATION_REQUIRED,
                         uncertainty=Uncertainty.RUNTIME_CHANGED)
            raise Refusal("runtime_changed", "host/process/config/topology changed; reconciliation required")

    def _ownership_bound(self, directory, stamp, state, binding):
        _typed(binding, OwnershipBinding)
        if binding != OwnershipBinding.from_runtime(state.status.binding):
            self._update(directory, stamp, state, phase=Phase.RECONCILIATION_REQUIRED,
                         uncertainty=Uncertainty.RUNTIME_CHANGED)
            raise Refusal("runtime_changed", "owned host/process/config changed; reconciliation required")

    @staticmethod
    def _application(value, expected):
        if not _integer(value, 0, (1 << 31) - 1) or value != expected:
            raise Refusal("application_mismatch", "required owned Desktop/idle application observation missing")

    def status(self) -> Status:
        with self._transaction() as directory:
            return self._load(directory)[0].status

    def reconcile(self, expected_revision: int,
                  evidence: ServiceTermination | ExclusiveOnboarding) -> Status:
        """Only explicit trusted reconciliation; never expires or adopts a stream."""
        if type(evidence) not in (ServiceTermination, ExclusiveOnboarding):
            _invalid("specific service-termination or exclusive-onboarding evidence required")
        _typed(evidence.current, IdleRuntime)
        _typed(evidence.current.binding, RuntimeBinding)
        self._application(evidence.current.application_id, 0)
        if evidence.current.binding.host != self.host:
            raise Refusal("host_mismatch", "reconciliation endpoint differs from installed host")
        if type(evidence) is ExclusiveOnboarding and not _uuid(evidence.ceremony_id):
            _invalid("explicit operator ceremony identifier required")
        with self._transaction() as directory:
            state, stamp = self._load(directory)
            self._revision(state, expected_revision)
            self._phase(state, Phase.RECONCILIATION_REQUIRED)
            if type(evidence) is ServiceTermination:
                _typed(evidence.terminated_generation, ServiceGeneration)
                old = state.status.binding
                current = evidence.current.binding
                if (old is None or evidence.terminated_generation != old.generation or
                        current.generation == old.generation or
                        current.generation.service_id != old.generation.service_id):
                    raise Refusal("generation_mismatch", "termination must cover reserved Sunshine generation and verified replacement")
            status = Status(self.host, expected_revision + 1, Phase.AVAILABLE, evidence.current.binding)
            self._save(directory, stamp, _State(status))
            return status

    def acquire(self, expected_revision: int, idle: IdleRuntime) -> Reservation:
        _typed(idle, IdleRuntime)
        _typed(idle.binding, RuntimeBinding)
        self._application(idle.application_id, 0)
        if idle.binding.host != self.host:
            raise Refusal("host_mismatch", "observed endpoint differs from installed host")
        with self._transaction() as directory:
            state, stamp = self._load(directory)
            self._revision(state, expected_revision)
            self._phase(state, Phase.AVAILABLE)
            if idle.binding.generation != state.status.binding.generation:
                self._update(directory, stamp, state, phase=Phase.RECONCILIATION_REQUIRED,
                             uncertainty=Uncertainty.RUNTIME_CHANGED)
                raise Refusal("generation_mismatch", "available baseline service generation changed; reconcile first")
            # With no owner, the current native/config snapshots may be refreshed.
            capability = Capability(secrets.token_hex(32))
            status = Status(self.host, expected_revision + 1, Phase.SELECTING, idle.binding)
            self._save(directory, stamp, _State(status, hashlib.sha256(capability.value.encode("ascii")).hexdigest()))
            return Reservation(capability, status)

    def refresh_selection(self, ticket: Reservation, idle: IdleRuntime) -> Reservation:
        """Refresh only untouched SELECTING intent, never external/capture authority.

        The coordinator must hold its operation mutex and establish no external
        intent. PREPARING precedes every external operation. Fresh host, exact
        generation/config and idle state stay fixed; only observed topology may
        change. Every refresh issues a new revision under the same capability.
        """
        _typed(idle, IdleRuntime)
        _typed(idle.binding, RuntimeBinding)
        self._application(idle.application_id, 0)
        with self._transaction() as directory:
            state, stamp = self._load(directory)
            self._owner(state, ticket)
            self._phase(state, Phase.SELECTING)
            self._ownership_bound(directory, stamp, state, OwnershipBinding.from_runtime(idle.binding))
            new = self._update(directory, stamp, state, binding=idle.binding)
            return Reservation(ticket.capability, new.status)

    def begin_prepare(self, ticket: Reservation, idle: IdleRuntime) -> Reservation:
        _typed(idle, IdleRuntime)
        self._application(idle.application_id, 0)
        with self._transaction() as directory:
            state, stamp = self._load(directory)
            self._owner(state, ticket)
            self._phase(state, Phase.SELECTING)
            self._bound(directory, stamp, state, idle.binding)
            new = self._update(directory, stamp, state, phase=Phase.PREPARING)
            return Reservation(ticket.capability, new.status)

    def prepared(self, ticket: Reservation, evidence: PreparedRuntime) -> Reservation:
        _typed(evidence, PreparedRuntime)
        _typed(evidence.current, RuntimeBinding)
        _typed(evidence.desktop, PositiveDesktop)
        self._application(evidence.application_id, 0)
        with self._transaction() as directory:
            state, stamp = self._load(directory)
            self._owner(state, ticket)
            self._phase(state, Phase.PREPARING)
            if state.status.desktop is not None:
                raise Refusal("phase_mismatch", "preparation already attested")
            self._bound(directory, stamp, state, evidence.previous)
            before, after = state.status.binding, evidence.current
            if (after.host != self.host or after.topology_digest != before.topology_digest or
                    after.generation.service_id != before.generation.service_id):
                self._update(directory, stamp, state, phase=Phase.RECONCILIATION_REQUIRED,
                             uncertainty=Uncertainty.RUNTIME_CHANGED)
                raise Refusal("binding_mismatch", "prepared endpoint/topology/service identity differs")
            retired = evidence.terminated_generation
            if ((after.generation != before.generation and retired != before.generation) or
                    (after.generation == before.generation and retired is not None)):
                raise Refusal("generation_mismatch", "prepared restart requires termination of the exact previous generation")
            new = self._update(directory, stamp, state, binding=after, desktop=evidence.desktop)
            return Reservation(ticket.capability, new.status)

    def attempt_launch(self, ticket: Reservation, idle: IdleRuntime) -> Reservation:
        """Write CONNECTING before issuing any launch; failure afterwards is uncertain."""
        _typed(idle, IdleRuntime)
        self._application(idle.application_id, 0)
        with self._transaction() as directory:
            state, stamp = self._load(directory)
            self._owner(state, ticket)
            self._phase(state, Phase.PREPARING)
            if state.status.desktop is None:
                raise Refusal("phase_mismatch", "verified preparation/positive-ID Desktop required before launch")
            self._bound(directory, stamp, state, idle.binding)
            new = self._update(directory, stamp, state, phase=Phase.CONNECTING,
                               attempt_revision=state.status.revision + 1)
            return Reservation(ticket.capability, new.status)

    def observe_active(self, ticket: Reservation, evidence: DesktopActive) -> Reservation:
        _typed(evidence, DesktopActive)
        with self._transaction() as directory:
            state, stamp = self._load(directory)
            self._owner(state, ticket)
            self._phase(state, Phase.CONNECTING, Phase.RECONNECTING)
            self._bound(directory, stamp, state, evidence.binding)
            if not _integer(evidence.attempt_revision, 1) or evidence.attempt_revision != state.status.attempt_revision:
                raise Refusal("stale_observation", "active observation does not follow this persisted attempt")
            self._desktop(state, evidence)
            new = self._update(directory, stamp, state, phase=Phase.ACTIVE, active_observed=True)
            return Reservation(ticket.capability, new.status)

    def _desktop(self, state, evidence):
        _typed(evidence.desktop, PositiveDesktop)
        if evidence.desktop != state.status.desktop:
            raise Refusal("application_mismatch", "observation is not the inspected owned Desktop")
        self._application(evidence.application_id, state.status.desktop.app_id)

    def attempt_reconnect(self, ticket: Reservation, running: DesktopRunning) -> Reservation:
        """Persist RECONNECTING before resume; never acquires/adopts another app."""
        _typed(running, DesktopRunning)
        with self._transaction() as directory:
            state, stamp = self._load(directory)
            self._owner(state, ticket)
            self._phase(state, Phase.ACTIVE)
            self._bound(directory, stamp, state, running.binding)
            self._desktop(state, running)
            new = self._update(directory, stamp, state, phase=Phase.RECONNECTING,
                               attempt_revision=state.status.revision + 1)
            return Reservation(ticket.capability, new.status)

    def begin_release(self, ticket: Reservation, running: OwnedDesktopRunning) -> Reservation:
        """Observed owned app may quit even when capture is no longer usable."""
        _typed(running, OwnedDesktopRunning)
        with self._transaction() as directory:
            state, stamp = self._load(directory)
            self._owner(state, ticket)
            self._phase(state, Phase.ACTIVE)
            self._ownership_bound(directory, stamp, state, running.binding)
            self._desktop(state, running)
            new = self._update(directory, stamp, state, phase=Phase.RELEASING)
            return Reservation(ticket.capability, new.status)

    def release(self, ticket: Reservation, evidence: DesktopClosure) -> Status:
        _typed(evidence, DesktopClosure)
        if not _uuid(evidence.cancel_transaction):
            _invalid("completed authenticated cancel correlation required")
        self._application(evidence.application_id, 0)
        with self._transaction() as directory:
            state, stamp = self._load(directory)
            self._owner(state, ticket)
            self._phase(state, Phase.RELEASING)
            self._ownership_bound(directory, stamp, state, evidence.binding)
            _typed(evidence.desktop, PositiveDesktop)
            if evidence.desktop != state.status.desktop:
                raise Refusal("application_mismatch", "closure is not for the owned positive-ID Desktop")
            if not _integer(evidence.release_revision, 1) or evidence.release_revision != state.status.revision:
                raise Refusal("stale_observation", "closure must follow this persisted release intent")
            # Keep the historical full selection snapshot. No fresh topology is
            # claimed by ownership-only closure; acquire must observe it again.
            status = Status(self.host, state.status.revision + 1, Phase.AVAILABLE, state.status.binding)
            self._save(directory, stamp, _State(status))
            return status

    def abort(self, ticket: Reservation, evidence: PrelaunchAbort | SelectionAbort | PreparedAbort) -> Status:
        """Untouched selection cancellation or verified completed prelaunch cleanup."""
        if type(evidence) not in (PrelaunchAbort, SelectionAbort, PreparedAbort):
            _invalid("trusted selection-cancel or completed prelaunch-cleanup evidence required")
        self._application(evidence.application_id, 0)
        with self._transaction() as directory:
            state, stamp = self._load(directory)
            self._owner(state, ticket)
            if state.status.launch_attempted:
                if state.status.phase is not Phase.RECONCILIATION_REQUIRED:
                    self._update(directory, stamp, state, phase=Phase.RECONCILIATION_REQUIRED,
                                 uncertainty=Uncertainty.LAUNCH_UNCONFIRMED)
                raise Refusal("reconciliation_required", "launch/resume was attempted; prelaunch abort cannot release ownership")
            if type(evidence) is PreparedAbort:
                self._phase(state, Phase.PREPARING)
                _typed(evidence.desktop, PositiveDesktop)
                if state.status.desktop is None or state.status.desktop != evidence.desktop:
                    raise Refusal("preparation_unconfirmed", "completed preparation and exact inspected Desktop required")
                if not _integer(evidence.prepared_revision, 1) or evidence.prepared_revision != state.status.revision:
                    raise Refusal("stale_observation", "cleanup must follow the actual completed preparation revision")
                self._ownership_bound(directory, stamp, state, evidence.binding)
                status = Status(self.host, state.status.revision + 1, Phase.AVAILABLE, state.status.binding)
                self._save(directory, stamp, _State(status))
                return status  # Full topology remains historical; no synthetic old-topology observation.
            if type(evidence) is SelectionAbort:
                self._phase(state, Phase.SELECTING)
                self._ownership_bound(directory, stamp, state, evidence.binding)
                status = Status(self.host, state.status.revision + 1, Phase.AVAILABLE, state.status.binding)
                self._save(directory, stamp, _State(status))
                return status  # Last full snapshot stays historical, never fabricated fresh topology.
            self._phase(state, Phase.SELECTING, Phase.PREPARING)
            _typed(evidence.binding, RuntimeBinding)
            if state.status.phase is Phase.SELECTING:
                # No external work has started. Publish the genuinely observed
                # current topology instead of pretending the old one is fresh.
                self._ownership_bound(directory, stamp, state, OwnershipBinding.from_runtime(evidence.binding))
            else:
                self._bound(directory, stamp, state, evidence.binding)
            status = Status(self.host, state.status.revision + 1, Phase.AVAILABLE, evidence.binding)
            self._save(directory, stamp, _State(status))
            return status

    def require_reconciliation(self, expected_revision: int, reason: Uncertainty) -> Status:
        """Trusted loss observation, including owner death; can only block, never free."""
        _typed(reason, Uncertainty)
        if reason is Uncertainty.NEEDS_ONBOARDING:
            _invalid("needs_onboarding is reserved for missing state")
        with self._transaction() as directory:
            state, stamp = self._load(directory)
            self._revision(state, expected_revision)
            return self._update(directory, stamp, state, phase=Phase.RECONCILIATION_REQUIRED,
                                uncertainty=reason).status
