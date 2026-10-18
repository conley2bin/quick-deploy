"""Internal systemd-user Sunshine authority; no CLI or host.prepare integration.

Native adapters remain the live monitor source. A journal FinalProbe establishes
a completed encoder choice, not a monitor inventory, live capture health or
captured-image proof. Ordinary later KMS capture attempts reuse that choice;
a new Trying-encoder trial supersedes it. Attempt references remain labelled
as attempts, never promoted to successful frames or output identity.
Capture authority additionally needs a StartReceipt issued by the trusted owner
of a controlled start. Stock Sunshine logs no loaded-file hash. The owner must
bind stable config/binary facts across its exclusive start, a NEW generation and
matching endpoint, and independently qualify the binary against SOURCE_COMMIT.
Before/after file checks require the accepted no-out-of-band-admin-write policy;
they are not cryptographic evidence of bytes read by Sunshine.

All paths/settings, receipts and restart-owner objects are trusted INTERNAL data,
never remote JSON. Merely constructing these dataclasses is not authentication.
Only the later durable reservation owner may implement RestartOwner: it must
hold exclusive ownership through this window, establish a reconciled idle
baseline or confirmed closure of its positive-ID Desktop, re-query the pinned
endpoint, and durably mark the restart attempt before dispatch. Raw FREE,
expiry, PID disappearance and a caller's verified=true do not satisfy that API.
A restart request grants neither release nor capture/launch readiness.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, replace
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import stat
import subprocess
import time
from typing import Protocol

from configuration import get_settings
from protocol import Refusal

SOURCE_COMMIT = "63d35f702ee9e362e43263742981836ec0710384"
UNIT = "app-dev.lizardbyte.app.Sunshine.service"
ALIAS = "sunshine.service"
_UNIT_BUS_OBJECTS = {
    UNIT: "/org/freedesktop/systemd1/unit/app_2ddev_2elizardbyte_2eapp_2eSunshine_2eservice",
    ALIAS: "/org/freedesktop/systemd1/unit/sunshine_2eservice",
}
EXECUTABLE = Path("/usr/bin/sunshine")
MAX_CONFIG = 1024 * 1024
MAX_BINARY = 256 * 1024 * 1024
MAX_COMMAND = 1024 * 1024
MAX_JOURNAL = 2048
PROPERTIES = (
    "Id", "LoadState", "ActiveState", "SubState", "MainPID", "InvocationID",
    "ExecMainStartTimestampMonotonic", "FragmentPath", "DropInPaths", "ExecStart",
    "ExecStartPre", "Environment", "EnvironmentFiles", "ConfigurationDirectory",
    "Restart", "RestartUSec", "StartLimitIntervalUSec", "PartOf", "NeedDaemonReload",
)
ENV_KEYS = ("HOME", "XDG_CONFIG_HOME", "CONFIGURATION_DIRECTORY")
JOURNAL_FIELDS = (
    "MESSAGE", "__CURSOR", "__MONOTONIC_TIMESTAMP", "_BOOT_ID", "_PID", "_UID",
    "_SYSTEMD_USER_UNIT", "_SYSTEMD_INVOCATION_ID", "_EXE", "_TRANSPORT",
)
HEX = re.compile(r"[0-9a-f]{64}\Z")
ID128 = re.compile(r"[0-9a-f]{32}\Z")
PREFIX = re.compile(r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}\]: (Verbose|Debug|Info|Warning|Error|Fatal): (.*)\Z")
FINAL = re.compile(r"Found H\.264 encoder: ([A-Za-z0-9_]+) \[(nvenc|vaapi|software)\]\Z")
ENCODERS = {"nvenc": ("h264_nvenc", "cuda"), "vaapi": ("h264_vaapi", "vaapi"),
            "software": ("libx264", "system")}
BACKENDS = {"Screencasting with KMS": "kms", "Screencasting with NvFBC": "nvfbc",
            "Screencasting with Wayland's protocol": "wlr", "Screencasting with X11": "x11",
            "Screencasting with XDG portal": "portal", "Screencasting with KWin ScreenCast": "kwin"}


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    mode: int
    uid: int
    gid: int
    size: int
    mtime_ns: int
    ctime_ns: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> FileIdentity:
        return cls(value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
                   value.st_size, value.st_mtime_ns, value.st_ctime_ns)


@dataclass(frozen=True)
class Fingerprint:
    identity: FileIdentity
    sha256: str


class Transport(Protocol):
    def run(self, argv: tuple[str, ...], *, timeout: float, max_output: int) -> CommandResult: ...
    def read(self, path: Path, *, limit: int) -> bytes: ...
    def identity(self, path: Path, *, follow: bool = False) -> FileIdentity: ...
    def realpath(self, path: Path) -> Path: ...
    def readlink(self, path: Path) -> str: ...
    def listdir(self, path: Path) -> tuple[str, ...]: ...


class LocalTransport:
    """Explicit opt-in real local I/O. No subprocess, service or file read on import.

    Both stdout/stderr together are bounded *while reading*, not after run().
    Overflow/timeout kills and reaps the child and is an explicit failure. A
    systemctl restart may already have queued a job: that remains uncertain.
    No shell, sudo, external helper or alternate Sunshine process is used.
    """
    def environment(self):
        env = {key: os.environ[key] for key in
               ("HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "XDG_CONFIG_HOME")
               if key in os.environ}
        env.update(LC_ALL="C", PATH="/usr/bin:/bin", SYSTEMD_PAGER="", SYSTEMD_COLORS="0")
        return env

    def run(self, argv, *, timeout, max_output):
        env = self.environment()
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=env, close_fds=True)
        sinks = [bytearray(), bytearray()]
        deadline = time.monotonic() + timeout
        try:
            with selectors.DefaultSelector() as selector:
                for stream, index in ((process.stdout, 0), (process.stderr, 1)):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ, index)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("bounded command timed out")
                    for key, _ in selector.select(remaining):
                        chunk = os.read(key.fd, min(65536, max_output + 1))
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        sinks[key.data].extend(chunk)
                        if sum(map(len, sinks)) > max_output:
                            raise ValueError("bounded command output exceeded limit")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("bounded command timed out")
            return CommandResult(process.wait(timeout=remaining), bytes(sinks[0]), bytes(sinks[1]))
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()
            process.stderr.close()

    def read(self, path, *, limit):
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("read target is not a regular file")
            chunks, size = [], 0
            while True:
                chunk = os.read(fd, min(65536, limit + 1 - size))
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
                size += len(chunk)
                if size > limit:
                    raise ValueError("bounded file exceeded limit")
        finally:
            os.close(fd)

    def identity(self, path, *, follow=False):
        return FileIdentity.from_stat(path.stat() if follow else path.lstat())

    def realpath(self, path):
        return path.resolve(strict=True)

    def readlink(self, path):
        return os.readlink(path)

    def listdir(self, path):
        names = []
        with os.scandir(path) as entries:
            for entry in entries:
                names.append(entry.name)
                if len(names) > 128:
                    raise ValueError("directory entry limit exceeded")
        return tuple(names)


@dataclass(frozen=True)
class SupportedBuild:
    """Installer independently qualifies these bytes; source text alone is not proof."""
    executable_sha256: str
    source_commit: str = SOURCE_COMMIT


@dataclass(frozen=True)
class Settings:
    host_uniqueid: str
    uid: int
    home: Path
    config_file: Path
    build: SupportedBuild
    unit: str = UNIT
    retry_guard_sha256: str | None = None


@dataclass(frozen=True)
class Generation:
    boot_id: str
    unit: str
    uid: int
    invocation_id: str
    pid: int
    start_ticks: int
    start_monotonic_usec: int


@dataclass(frozen=True)
class UnitRecord:
    name: str
    fragment_path: str
    ownership_digest: str


@dataclass(frozen=True)
class ConfigRecord:
    path: str
    root: str
    file: Fingerprint
    manager_environment_digest: str
    process_environment_status: str
    configured_capture: str
    configured_encoder: str
    configured_output: str
    bind_address: str
    base_port: int
    loaded_digest: str | None = None


@dataclass(frozen=True)
class BinaryRecord:
    path: str
    file: Fingerprint
    process_exe_status: str
    supported_build: bool


@dataclass(frozen=True)
class StartReceipt:
    """Attested start facts from an enrolled lifecycle owner, never client input.

    Issuance also requires NEW-generation + pinned endpoint verification and
    exclusive/stable files across startup. Observe matches this receipt; it does
    not create it or claim that two stat checks prove what Sunshine read.
    """
    host_uniqueid: str
    generation: Generation
    unit_digest: str
    config_path: str
    config_root: str
    config: Fingerprint
    manager_environment_digest: str
    executable: Fingerprint
    qualified_build: SupportedBuild


@dataclass(frozen=True)
class JournalRef:
    cursor: str
    monotonic_usec: int
    event: str


@dataclass(frozen=True)
class FinalProbe:
    backend: str
    encoder: str
    h264_codec: str
    mem_type: str
    source_commit: str
    connector_ids: tuple[int, ...]
    evidence: tuple[JournalRef, ...]


@dataclass(frozen=True)
class CaptureRecord:
    status: str
    reason: str | None
    probe: FinalProbe | None
    effective_backend: str | None
    effective_encoder: str | None
    effective_mem_type: str | None
    receipt_bound: bool


@dataclass(frozen=True)
class RuntimeObservation:
    host_uniqueid: str
    generation: Generation
    unit: UnitRecord
    config: ConfigRecord
    binary: BinaryRecord
    capture: CaptureRecord
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class OwnerIdleEvidence:
    host_uniqueid: str
    generation: Generation
    config_digest: str
    reservation_revision: int
    enrollment_revision: int
    phase: str
    positive_desktop_app_id: int
    application_state: str
    currentgame: int
    idle_basis: str
    launch_attempted: bool


class RestartWindow(Protocol):
    def revalidate_idle(self, current: RuntimeObservation) -> OwnerIdleEvidence: ...
    def mark_restart_attempted(self, current: RuntimeObservation) -> None: ...


class RestartOwner(Protocol):
    def restart_window(self, expected: RuntimeObservation) -> AbstractContextManager[RestartWindow]: ...


@dataclass(frozen=True)
class RestartResult:
    previous_generation: Generation
    config_digest: str
    status: str = "restart_queued"
    required_followup: tuple[str, ...] = (
        "new_verified_generation", "pinned_endpoint_identity", "stable_start_receipt", "verified_final_probe",
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_digest(value) -> str:
    return _sha(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _text(data: bytes) -> str:
    return data.decode("utf-8", "strict")


def _authority_facts(value: RuntimeObservation) -> tuple:
    """Restart binds owned facts, not optional journal/receipt evidence status."""
    return (value.host_uniqueid, value.generation, value.unit, value.config.path, value.config.root,
            value.config.file, value.config.manager_environment_digest,
            value.config.process_environment_status, value.binary)


def _environment(text: str, separator: str) -> dict[str, str]:
    result = {}
    for line in text.split(separator):
        key, eq, value = line.partition("=")
        if key not in ENV_KEYS:
            continue
        if not eq or key in result or any(c in value for c in "\0\r\n"):
            raise Refusal("config_resolution_ambiguous", "ambiguous service configuration environment")
        result[key] = value
    return result


def _commands(text: str) -> tuple[tuple[str, str, str], ...]:
    # Same systemctl structured command prefix used by common.sh. Reject any
    # unmatched/second command; runtime pid/timestamp/result suffixes are ignored.
    blocks = re.findall(r"\{ ([^{}]*) \}", text)
    if not blocks or re.sub(r"\{ [^{}]* \}", "", text).strip():
        raise Refusal("unit_unowned", "unrecognized effective command structure")
    result = []
    for block in blocks:
        match = re.match(r"path=(.*?) ; argv\[\]=(.*?) ; ignore_errors=(yes|no) ;", block)
        if not match:
            raise Refusal("unit_unowned", "unrecognized effective command prefix")
        result.append(match.groups())
    return tuple(result)


def _retry_bytes(guard: Path, root: Path) -> bytes:
    def quote(value):
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'
    return ("# quick-deploy/sunshine-moonlight: managed retry policy\n[Unit]\n"
            "StartLimitIntervalSec=0\nPartOf=graphical-session.target\n\n[Service]\n"
            "Restart=on-failure\nRestartSec=5s\nExecStartPre=:/usr/bin/python3 "
            + quote(guard) + " " + quote(root) + "\n").encode()


class LinuxRuntime:
    """Immutable observations and a separate owner-guarded restart request."""
    def __init__(self, settings: Settings, transport: Transport):
        if (type(settings) is not Settings or settings.unit not in (UNIT, ALIAS)
                or type(settings.uid) is not int or settings.uid <= 0
                or not re.fullmatch(r"[A-Za-z0-9-]{8,128}", settings.host_uniqueid)
                or type(settings.build) is not SupportedBuild
                or settings.build.source_commit != SOURCE_COMMIT
                or not HEX.fullmatch(settings.build.executable_sha256)):
            raise Refusal("runtime_unconfigured", "invalid trusted runtime enrollment")
        for path in (settings.home, settings.config_file):
            if not isinstance(path, Path) or not path.is_absolute() or any(c in str(path) for c in "\0\r\n"):
                raise Refusal("runtime_unconfigured", "enrolled paths must be absolute single-line paths")
        if settings.config_file.name != "sunshine.conf":
            raise Refusal("runtime_unconfigured", "only the default Sunshine config filename is owned")
        if settings.retry_guard_sha256 is not None and not HEX.fullmatch(settings.retry_guard_sha256):
            raise Refusal("runtime_unconfigured", "invalid enrolled retry guard digest")
        self.settings, self.transport = settings, transport

    def _run(self, argv: tuple[str, ...]) -> bytes:
        try:
            result = self.transport.run(argv, timeout=5.0, max_output=MAX_COMMAND)
            if (type(result) is not CommandResult or type(result.returncode) is not int or type(result.stdout) is not bytes
                    or type(result.stderr) is not bytes or len(result.stdout) + len(result.stderr) > MAX_COMMAND):
                raise ValueError("invalid bounded transport result")
            if result.returncode != 0:
                raise Refusal("command_failed", "owned service observation command failed")
            return result.stdout
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise Refusal("transport_unavailable", "bounded local service transport failed") from exc

    def _read(self, path: Path, limit: int) -> bytes:
        data = self.transport.read(path, limit=limit)
        if type(data) is not bytes or len(data) > limit:
            raise Refusal("observation_overflow", "file observation exceeds its bound")
        return data

    def _file(self, path: Path, limit: int, *, owner: int) -> tuple[bytes, Fingerprint]:
        before = self.transport.identity(path)
        if (not stat.S_ISREG(before.mode) or before.uid != owner or before.size > limit
                or before.mode & 0o022):
            raise Refusal("file_unowned", "authority file must be owned, bounded, regular and not publicly writable")
        data = self._read(path, limit)
        if len(data) != before.size or self.transport.identity(path) != before:
            raise Refusal("file_changed", "authority file changed while observed")
        return data, Fingerprint(before, _sha(data))

    def _resolve(self, env: dict[str, str]) -> Path:
        home = env.get("HOME") or str(self.settings.home)
        root = env.get("CONFIGURATION_DIRECTORY") or env.get("XDG_CONFIG_HOME") or str(Path(home) / ".config")
        if ":" in env.get("CONFIGURATION_DIRECTORY", "") or not Path(root).is_absolute() or any(c in root for c in "\0\r\n"):
            raise Refusal("config_resolution_ambiguous", "unsupported Sunshine configuration root")
        return self.transport.realpath(Path(root) / "sunshine")

    def _unit(self, props: dict[str, str], env: dict[str, str], root: Path) -> UnitRecord:
        if (props["Id"] not in (UNIT, ALIAS) or props["LoadState"] != "loaded"
                or props["NeedDaemonReload"] != "no"
                or any(props[key] for key in ("Environment", "EnvironmentFiles", "ConfigurationDirectory"))):
            raise Refusal("unit_unowned", "unit overrides or unloaded service policy are unsupported")
        allowed = {f"{prefix}/{name}" for prefix in ("/usr/lib/systemd/user", "/lib/systemd/user")
                   for name in (UNIT, ALIAS)}
        if props["FragmentPath"] not in allowed:
            raise Refusal("unit_unowned", "custom Sunshine unit fragment refused")
        if _commands(props["ExecStart"]) != ((str(EXECUTABLE), str(EXECUTABLE), "no"),):
            raise Refusal("unit_unowned", "custom Sunshine ExecStart refused")
        xdg = Path(env.get("XDG_CONFIG_HOME") or str(Path(env.get("HOME") or self.settings.home) / ".config"))
        retry_dir = xdg / "systemd/user" / (UNIT + ".d")
        drop, guard = retry_dir / "quick-deploy-retry.conf", retry_dir / "check-tailnet.py"
        present = set()
        for directory in (retry_dir, xdg / "systemd/user" / (ALIAS + ".d")):
            try:
                info = self.transport.identity(directory)
                if not stat.S_ISDIR(info.mode):
                    raise Refusal("unit_unowned", "linked/non-directory Sunshine override directory")
                names = self.transport.listdir(directory)
                if len(names) > 128 or len(set(names)) != len(names):
                    raise Refusal("unit_unowned", "ambiguous override directory")
            except FileNotFoundError:
                continue
            for name in names:
                if "/" in name or name in (".", ".."):
                    raise Refusal("unit_unowned", "invalid override filename")
                path = directory / name
                if name.endswith(".conf") and path != drop:
                    raise Refusal("unit_unowned", "unowned Sunshine drop-in (including not-yet-loaded files)")
                if path in (drop, guard):
                    present.add(path)
        drops = shlex.split(props["DropInPaths"])
        policy_facts = []
        if present:
            if present != {drop, guard} or len(drops) != 1 or self.settings.retry_guard_sha256 is None:
                raise Refusal("unit_unowned", "incomplete or unenrolled managed retry policy")
            loaded = Path(drops[0])
            if (not loaded.is_absolute() or loaded.name != drop.name
                    or not stat.S_ISDIR(self.transport.identity(loaded.parent).mode)
                    or not stat.S_ISREG(self.transport.identity(loaded).mode)
                    or self.transport.realpath(loaded) != self.transport.realpath(drop)):
                raise Refusal("unit_unowned", "foreign or linked loaded retry policy")
            drop_data, drop_file = self._file(drop, 16384, owner=self.settings.uid)
            _, guard_file = self._file(guard, MAX_CONFIG, owner=self.settings.uid)
            if drop_data != _retry_bytes(guard, root) or guard_file.sha256 != self.settings.retry_guard_sha256:
                raise Refusal("unit_unowned", "modified retry policy/guard or configuration root")
            if (props["Restart"], props["RestartUSec"], props["StartLimitIntervalUSec"], props["PartOf"]) != (
                    "on-failure", "5s", "0", "graphical-session.target"):
                raise Refusal("unit_unowned", "effective retry policy differs from installed policy")
            expected_pre = (("/bin/sleep", "/bin/sleep 5", "no"),
                            ("/usr/bin/python3", f"/usr/bin/python3 {guard} {root}", "no"))
            policy_facts = [asdict(drop_file), asdict(guard_file)]
        else:
            if drops:
                raise Refusal("unit_unowned", "unknown loaded Sunshine drop-in")
            expected_pre = (("/bin/sleep", "/bin/sleep 5", "no"),)
        if _commands(props["ExecStartPre"]) != expected_pre:
            raise Refusal("unit_unowned", "vendor sleep or managed prestart command was overridden")
        stable = {key: props[key] for key in PROPERTIES if key not in (
            "MainPID", "InvocationID", "ExecMainStartTimestampMonotonic", "ActiveState", "SubState", "ExecStart", "ExecStartPre")}
        _, fragment = self._file(Path(props["FragmentPath"]), 65536, owner=0)
        stable.update(commands=[_commands(props["ExecStart"]), expected_pre], policy_files=policy_facts,
                      fragment=asdict(fragment))
        return UnitRecord(props["Id"], props["FragmentPath"], _json_digest(stable))

    def _require_empty_environment_files(self, unit_id: str) -> None:
        """Only this omitted special array may be recovered from typed evidence.

        systemctl255 omits empty EnvironmentFiles even with --all. busctl's
        lossless JSON carries the a(sb) signature; never infer emptiness from
        absence, failure, a different signature or a request-supplied object.
        GetUnit resolves aliases, but the returned object must equal the fixed
        path for the already observed canonical Id before it is queried.
        """
        if unit_id not in _UNIT_BUS_OBJECTS:
            raise Refusal("unit_unowned", "unsupported systemd user unit identity")
        expected_object = _UNIT_BUS_OBJECTS[unit_id]
        prefix = ("/usr/bin/busctl", "--user", "--json=short", "--auto-start=no",
                  "--allow-interactive-authorization=no")

        def typed(raw: bytes, signature: str):
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate D-Bus JSON field")
                    result[key] = value
                return result
            try:
                value = json.loads(_text(raw), object_pairs_hook=unique)
                if (type(value) is not dict or set(value) != {"type", "data"}
                        or value["type"] != signature):
                    raise ValueError("unexpected D-Bus signature/envelope")
                return value["data"]
            except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
                raise Refusal("unit_ambiguous", "invalid typed systemd property proof") from exc

        resolved = typed(self._run((*prefix, "call", "org.freedesktop.systemd1",
                                   "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager",
                                   "GetUnit", "s", self.settings.unit)), "o")
        if resolved != [expected_object]:
            raise Refusal("unit_ambiguous", "GetUnit did not identify the observed owned unit")
        files = typed(self._run((*prefix, "get-property", "org.freedesktop.systemd1",
                                expected_object, "org.freedesktop.systemd1.Service", "EnvironmentFiles")), "a(sb)")
        if (type(files) is not list or any(type(row) is not list or len(row) != 2
                or type(row[0]) is not str or type(row[1]) is not bool for row in files)):
            raise Refusal("unit_ambiguous", "invalid EnvironmentFiles array data")
        if files:
            raise Refusal("unit_unowned", "unit EnvironmentFiles override is unsupported")

    def _sample(self) -> RuntimeObservation:
        show = ("/usr/bin/systemctl", "--user", "show", "--no-pager", self.settings.unit,
                "--property=" + ",".join(PROPERTIES))
        props = {}
        for line in _text(self._run(show)).splitlines():
            key, eq, value = line.partition("=")
            if not eq or key not in PROPERTIES or (key in props and key not in ("ExecStart", "ExecStartPre")):
                raise Refusal("unit_ambiguous", "unexpected or duplicate scalar systemd property")
            if key in ("ExecStart", "ExecStartPre"):
                # Native command arrays print one same-key row per entry. Keep
                # ALL entries in order; _unit still checks the exact owned tuple.
                # Validate each row so an empty/malformed extra row is not lost.
                _commands(value)
                value = props[key] + " " + value if key in props else value
            props[key] = value
        missing = set(PROPERTIES) - set(props)
        if missing - {"EnvironmentFiles"}:
            raise Refusal("unit_ambiguous", "incomplete systemd unit observation")
        if "EnvironmentFiles" in missing:
            self._require_empty_environment_files(props["Id"])
            props["EnvironmentFiles"] = ""  # Explicit typed-empty proof, not an absent-property default.
        if props["ActiveState"] != "active" or props["SubState"] != "running":
            raise Refusal("service_not_running", "Sunshine user service is not active/running")
        env = _environment(_text(self._run(("/usr/bin/systemctl", "--user", "show-environment"))), "\n")
        root = self._resolve(env)
        enrolled = self.settings.config_file
        # Resolve parents only: resolving the leaf first could hide its symlink.
        if self.transport.realpath(enrolled.parent) != root:
            raise Refusal("config_resolution_changed", "enrolled path differs from actual manager configuration root")
        config_path = root / "sunshine.conf"
        data, config_file = self._file(enrolled, MAX_CONFIG, owner=self.settings.uid)
        cfg = get_settings(data)  # Reuse native top-level/bracket cursor and strict scalar rules.
        try:
            address = ipaddress.IPv4Address(cfg["bind_address"])
        except ValueError as exc:
            raise Refusal("config_binding_unowned", "managed Sunshine binding must be a Tailnet IPv4") from exc
        if (cfg["address_family"] != "ipv4" or str(address) != cfg["bind_address"]
                or address not in ipaddress.IPv4Network("100.64.0.0/10")):
            raise Refusal("config_binding_unowned", "managed Sunshine binding must retain ipv4/Tailnet semantics")
        if cfg["capture"] not in ("", "kms", "portal", "x11", "nvfbc", "wlr", "kwin"):
            raise Refusal("config_ambiguous", "unsupported native capture configuration")
        unit = self._unit(props, env, root)
        if not re.fullmatch(r"[1-9][0-9]{0,9}", props["MainPID"]) or not ID128.fullmatch(props["InvocationID"]):
            raise Refusal("generation_unknown", "missing user service process/invocation identity")
        started = props["ExecMainStartTimestampMonotonic"]
        if not re.fullmatch(r"[1-9][0-9]{0,19}", started):
            raise Refusal("generation_unknown", "missing monotonic service start timestamp")
        pid = int(props["MainPID"])
        boot = _text(self._read(Path("/proc/sys/kernel/random/boot_id"), 64)).strip().replace("-", "")
        if not ID128.fullmatch(boot):
            raise Refusal("generation_unknown", "invalid kernel boot identity")
        proc = Path("/proc") / str(pid)
        proc_stat = _text(self._read(proc / "stat", 4096))
        end = proc_stat.rfind(")")
        fields = proc_stat[end + 2:].split() if end > 0 else []
        if (not proc_stat.startswith(f"{pid} (") or len(fields) < 20 or fields[0] in ("Z", "X")
                or not fields[19].isdigit() or int(fields[19]) <= 0):
            raise Refusal("generation_unknown", "invalid or dead proc process generation")
        status = _text(self._read(proc / "status", 16384))
        uids = re.findall(r"^Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$", status, re.M)
        if len(uids) != 1 or any(int(uid) != self.settings.uid for uid in uids[0]):
            raise Refusal("process_unowned", "Sunshine process belongs to another user")
        if self._read(proc / "cmdline", 4096) != b"/usr/bin/sunshine\0":
            raise Refusal("process_unowned", "Sunshine process has a custom executable/config argv")
        groups = _text(self._read(proc / "cgroup", 16384)).splitlines()
        if not any(line.startswith("0::/") and line.rsplit("/", 1)[-1] == unit.name for line in groups):
            raise Refusal("process_unowned", "Sunshine process is outside the observed user unit cgroup")
        _, binary_file = self._file(EXECUTABLE, MAX_BINARY, owner=0)
        limitations = []
        try:
            if (self.transport.readlink(proc / "exe") != str(EXECUTABLE)
                    or self.transport.identity(proc / "exe", follow=True) != binary_file.identity):
                raise Refusal("binary_changed", "running executable is old/deleted or differs from installed binary")
            exe_status = "verified"
        except PermissionError:
            exe_status = "capability_limited"
            limitations.append("proc_exe_inaccessible: current running bytes require trusted start receipt")
        try:
            process_env = _environment(_text(self._read(proc / "environ", 65536)), "\0")
            if self._resolve(process_env) != root:
                raise Refusal("config_resolution_changed", "running process configuration root differs from manager")
            process_env_status = "verified"
        except PermissionError:
            process_env_status = "capability_limited"
            limitations.append("proc_environ_inaccessible: startup config root requires trusted start receipt")
        generation = Generation(boot, unit.name, self.settings.uid, props["InvocationID"], pid,
                                int(fields[19]), int(started))
        config = ConfigRecord(str(config_path), str(root), config_file, _json_digest(env), process_env_status,
                              cfg["capture"], cfg["encoder"], cfg["output_name"], cfg["bind_address"], cfg["port"])
        binary = BinaryRecord(str(EXECUTABLE), binary_file, exe_status,
                              binary_file.sha256 == self.settings.build.executable_sha256)
        return RuntimeObservation(self.settings.host_uniqueid, generation, unit, config, binary,
                                  CaptureRecord("unverified", "not_observed", None, None, None, None, False),
                                  tuple(limitations))

    def _match_receipt(self, receipt: StartReceipt, current: RuntimeObservation):
        if (type(receipt) is not StartReceipt or receipt.host_uniqueid != current.host_uniqueid
                or receipt.generation != current.generation or receipt.unit_digest != current.unit.ownership_digest
                or receipt.config_path != current.config.path or receipt.config_root != current.config.root
                or receipt.config != current.config.file
                or receipt.manager_environment_digest != current.config.manager_environment_digest
                or receipt.executable != current.binary.file or receipt.qualified_build != self.settings.build
                or not current.binary.supported_build):
            raise Refusal("receipt_mismatch", "startup receipt is stale, unqualified or differs from current authority facts")

    def _journal(self, current: RuntimeObservation) -> tuple[FinalProbe | None, str | None]:
        gen = current.generation
        # Select stdout before the bounded journal window; still validate every returned record below.
        argv = ("/usr/bin/journalctl", "--user", "--no-pager", "--output=json", "--all",
                f"--lines={MAX_JOURNAL + 1}", "--output-fields=" + ",".join(JOURNAL_FIELDS),
                "_BOOT_ID=" + gen.boot_id, "_SYSTEMD_USER_UNIT=" + gen.unit,
                "_UID=" + str(gen.uid), "_PID=" + str(gen.pid), "_SYSTEMD_INVOCATION_ID=" + gen.invocation_id,
                "_TRANSPORT=stdout")
        try:
            lines = self._run(argv).splitlines()
        except Refusal as exc:
            return None, "journal_unavailable:" + exc.code
        if len(lines) > MAX_JOURNAL:
            return None, "journal_window_overflow"
        previous, cursors, refs, config_logs = 0, set(), [], {}
        startup, trial, backend, final, connectors = False, None, None, None, []
        config_closed, fallback = False, False
        reason = "no_completed_probe"
        expected_cfg = {"capture": current.config.configured_capture, "encoder": current.config.configured_encoder,
                        "output_name": current.config.configured_output, "bind_address": current.config.bind_address,
                        "port": str(current.config.base_port), "address_family": "ipv4"}
        def unique(items):
            obj = {}
            for key, value in items:
                if key in obj:
                    raise ValueError("duplicate journal JSON field")
                obj[key] = value
            return obj
        try:
            for raw in lines:
                record = json.loads(_text(raw), object_pairs_hook=unique)
                if type(record) is not dict or any(type(record.get(key)) is not str for key in JOURNAL_FIELDS):
                    return None, "journal_fields_ambiguous"
                if any(record[key] != value for key, value in (
                        ("_BOOT_ID", gen.boot_id), ("_PID", str(gen.pid)), ("_UID", str(gen.uid)),
                        ("_SYSTEMD_USER_UNIT", gen.unit), ("_SYSTEMD_INVOCATION_ID", gen.invocation_id),
                        ("_EXE", str(EXECUTABLE)), ("_TRANSPORT", "stdout"))):
                    return None, "journal_generation_mismatch"
                cursor, mono = record["__CURSOR"], record["__MONOTONIC_TIMESTAMP"]
                if (not cursor or len(cursor) > 512 or cursor in cursors or not mono.isdigit()
                        or int(mono) < gen.start_monotonic_usec or int(mono) < previous):
                    return None, "journal_order_ambiguous"
                cursors.add(cursor)
                previous = int(mono)
                parsed = PREFIX.fullmatch(record["MESSAGE"])
                if not parsed:
                    # Never match a substring, a multiline message or another formatter.
                    if any(token in record["MESSAGE"] for token in ("Trying encoder", "Screencasting with", "Found H.264", "Sunshine version:")):
                        return None, "journal_formatter_unsupported"
                    continue
                level, message = parsed.groups()
                version = re.fullmatch(r"Sunshine version: [0-9.]+ commit: ([0-9a-f]{7,40})", message)
                if version:
                    if level != "Info" or startup or not SOURCE_COMMIT.startswith(version[1]):
                        return None, "journal_source_mismatch"
                    startup = True
                    refs = [JournalRef(cursor, previous, "supported_source_startup")]
                    continue
                setting = re.fullmatch(r"config: '([A-Za-z0-9_]+)' = (.*)", message)
                if startup and setting and setting[1] in expected_cfg:
                    if config_closed or setting[1] in config_logs:
                        return None, "journal_config_ambiguous"
                    config_logs[setting[1]] = setting[2]
                    if setting[2] != expected_cfg[setting[1]]:
                        return None, "journal_config_mismatch"
                    refs.append(JournalRef(cursor, previous, "loaded_setting:" + setting[1]))
                    continue
                if message == "// Testing for available encoders, this may generate errors. You can safely ignore those errors. //":
                    # probe_encoders clears chosen_encoder BEFORE this banner,
                    # unless its explicit preference has already completed the
                    # pending trial. Ordinary capture never emits this banner.
                    if trial is None:
                        final, backend, connectors = None, None, []
                        reason = "incomplete_or_superseded_probe"
                    continue
                tried = re.fullmatch(r"Trying encoder \[([A-Za-z0-9_]+)\]", message)
                if tried:
                    if level != "Info":
                        return None, "journal_event_level_mismatch"
                    final, connectors, backend, fallback = None, [], None, False
                    if not startup:
                        return None, "journal_startup_missing"
                    config_closed = True
                    trial = tried[1]
                    refs = [ref for ref in refs if ref.event.startswith(("supported_source", "loaded_setting"))]
                    refs.append(JournalRef(cursor, previous, "encoder_trial"))
                    reason = "incomplete_or_superseded_probe"
                    continue
                if message.startswith("Screencasting with") and message not in BACKENDS:
                    return None, "journal_backend_unsupported"
                if message in BACKENDS:
                    if level != "Info":
                        return None, "journal_event_level_mismatch"
                    if final is not None:
                        # video.cpp1593/2716 creates a display using chosen_encoder.
                        # probe_encoders3275 may have returned early; this marker
                        # does not assign or clear chosen_encoder. Preserve ONLY
                        # the qualified choice, and label this as an attempt.
                        if BACKENDS[message] != final.backend:
                            return None, "ordinary_capture_backend_changed"
                        final = replace(final, evidence=final.evidence + (
                            JournalRef(cursor, previous, "ordinary_capture_attempt"),))
                        continue
                    connectors = []
                    backend = BACKENDS[message] if trial else None
                    refs.append(JournalRef(cursor, previous, "backend_attempt"))
                    reason = "incomplete_or_superseded_probe"
                    continue
                if message.startswith("Couldn't map '") or "Falling back to first monitor" in message:
                    # A fallback cannot attest the selected output. A later NEW
                    # completed trial may recover; ordinary markers alone cannot.
                    final, fallback = None, True
                    reason = "capture_output_fallback"
                    continue
                connector = re.fullmatch(r"Found connector ID \[([1-9][0-9]{0,9})\]", message)
                if connector and trial and backend == "kms":
                    connectors.append(int(connector[1]))
                    refs.append(JournalRef(cursor, previous, "kms_connector_observed"))
                    continue
                found = FINAL.fullmatch(message)
                if message.startswith("Found H.264 encoder:"):
                    if fallback:
                        return None, "capture_output_fallback"
                    if (not found or final is not None or trial != found[2] or backend != "kms"
                            or level != "Info" or len(set(connectors)) > 1):
                        return None, "final_probe_ambiguous_or_backend_unsupported"
                    codec, mem_type = ENCODERS[trial]
                    if found[1] != codec:
                        return None, "final_codec_unsupported"
                    if any(value and key not in config_logs for key, value in expected_cfg.items()
                           if key != "port" or current.config.base_port != 47989):
                        return None, "journal_config_incomplete"
                    if current.config.configured_capture not in ("", backend):
                        return None, "journal_backend_config_mismatch"
                    refs.append(JournalRef(cursor, previous, "chosen_encoder_final"))
                    final = FinalProbe(backend, trial, codec, mem_type, SOURCE_COMMIT,
                                       tuple(sorted(set(connectors))), tuple(refs))
                    trial = None
                    reason = None
                    continue
                extra_codec = re.fullmatch(r"Found (HEVC|AV1) encoder: ([A-Za-z0-9_]+) \[([A-Za-z0-9_]+)\]", message)
                if message.startswith(("Found HEVC encoder:", "Found AV1 encoder:")):
                    if not extra_codec or final is None or extra_codec[3] != final.encoder or level != "Info":
                        return None, "final_codec_block_ambiguous"
                    continue
                # A missing preference never enters validate_encoder: the Error
                # at video.cpp3344 follows chosen_encoder=nullptr but precedes
                # the testing banner. Neither failure needs a pending header.
                preference_failed = (level == "Error" and re.fullmatch(
                    r"Couldn't find any working encoder matching \[.*\]", message))
                failed = re.fullmatch(r"Encoder \[([A-Za-z0-9_]+)\] failed", message)
                unsupported = re.fullmatch(r"Encoder \[([A-Za-z0-9_]+)\] is not supported on this GPU", message)
                if (failed or preference_failed or (unsupported and (trial is None or unsupported[1] == trial))
                        or message.startswith(("Unable to find display or encoder", "Video failed to find working encoder",
                                                  "Reinitializing capture", "Previous active display ["))):
                    final, trial, backend, connectors = None, None, None, []
                    reason = "incomplete_or_superseded_probe"
            return final, reason
        except (UnicodeError, ValueError, TypeError, RecursionError):
            return None, "journal_json_invalid"

    def observe(self, *, receipt: StartReceipt | None = None) -> RuntimeObservation:
        """Sample generation/unit/files before and after the bounded journal window."""
        try:
            before = self._sample()
            if receipt is not None:
                self._match_receipt(receipt, before)
            probe, reason = self._journal(before)
            after = self._sample()
            if before != after:
                raise Refusal("observation_changed", "generation, unit, environment, config or executable changed during observation")
            if receipt is not None:
                self._match_receipt(receipt, after)
        except (OSError, ValueError, UnicodeError) as exc:
            raise Refusal("observation_unavailable", "bounded local runtime facts unavailable") from exc
        verified = probe is not None and receipt is not None and after.binary.supported_build
        limitations = after.limitations + (
            "final_probe_attests_backend_encoder_only: native mapping, captured image and current health require integration",)
        if receipt is None:
            limitations += ("loaded_config_digest_unattested: stock logs do not bind complete loaded file bytes",)
        if not after.binary.supported_build:
            limitations += ("installed_binary_not_qualified_for_pinned_source",)
        capture = CaptureRecord("verified" if verified else "unverified",
                                None if verified else (reason or "start_receipt_required"), probe,
                                probe.backend if verified else None, probe.encoder if verified else None,
                                probe.mem_type if verified else None, receipt is not None)
        return replace(after, config=replace(after.config, loaded_digest=receipt.config.sha256 if receipt else None),
                       capture=capture, limitations=limitations)

    def guarded_restart(self, expected: RuntimeObservation, *, owner: RestartOwner) -> RestartResult:
        """Revalidate, durably mark an owner-controlled attempt, queue ONLY this unit.

        Owner context/endpoint facts are required even if capture is verified.
        No receipt is required for the first controlled restart. Exit0 is merely
        a queued systemd job; integration must observe NEW generation, endpoint,
        stable startup receipt and successful final probe before allowing launch.
        """
        if type(expected) is not RuntimeObservation or owner is None:
            raise Refusal("restart_owner_required", "durable reservation restart owner is required")
        with owner.restart_window(expected) as window:
            current = self.observe()
            if _authority_facts(current) != _authority_facts(expected):
                raise Refusal("restart_snapshot_changed", "expected generation/config/unit/binary no longer current")
            if not current.binary.supported_build:
                raise Refusal("restart_binary_unqualified", "installed Sunshine binary has no supported build enrollment")
            idle = window.revalidate_idle(current)
            if (type(idle) is not OwnerIdleEvidence or idle.host_uniqueid != current.host_uniqueid
                    or idle.generation != current.generation or idle.config_digest != current.config.file.sha256
                    or type(idle.reservation_revision) is not int or idle.reservation_revision < 1
                    or type(idle.enrollment_revision) is not int or idle.enrollment_revision < 1
                    or idle.phase != "preparing" or type(idle.positive_desktop_app_id) is not int
                    or idle.positive_desktop_app_id <= 0 or idle.application_state != "FREE"
                    or type(idle.currentgame) is not int or idle.currentgame != 0
                    or idle.idle_basis not in ("enrolled_no_launch", "owned_desktop_closed")
                    or type(idle.launch_attempted) is not bool
                    or (idle.launch_attempted and idle.idle_basis != "owned_desktop_closed")):
                raise Refusal("restart_idle_unproven", "busy/unknown/unowned lifecycle cannot restart Sunshine")
            window.mark_restart_attempted(current)
            # Owner endpoint revalidation/durable persistence can take time. Check
            # the local expected generation/config again immediately before dispatch.
            try:
                dispatch = self._sample()
            except (OSError, ValueError, UnicodeError) as exc:
                raise Refusal("restart_snapshot_changed", "authority facts unavailable before dispatch") from exc
            if _authority_facts(dispatch) != _authority_facts(current):
                raise Refusal("restart_snapshot_changed", "authority facts changed before restart dispatch")
            try:
                self._run(("/usr/bin/systemctl", "--user", "--no-block", "--no-ask-password", "restart", self.settings.unit))
            except Refusal as exc:
                raise Refusal("restart_uncertain", "restart dispatch failed/unknown; owner must retain reconciliation block") from exc
            return RestartResult(current.generation, current.config.file.sha256)


# Installer-only attempted-launch retirement. None of these properties relax
# ordinary observe(), receipts, or guarded_restart's PREPARING authority.
RETIREMENT_PROPERTIES = (
    "Id", "LoadState", "ActiveState", "SubState", "MainPID", "ControlPID", "InvocationID",
    "ControlGroup", "KillMode", "SendSIGKILL", "KillSignal", "FinalKillSignal", "TimeoutStopUSec",
    "Restart", "Delegate", "Triggers", "TriggeredBy", "FileDescriptorStoreMax", "NFileDescriptorStore",
    "Job", "ExecStop", "ExecStopPost", "BusName", "Type", "NotifyAccess", "RemainAfterExit",
    "NeedDaemonReload",
)
RETIREMENT_POLICY = {
    "Id": UNIT, "LoadState": "loaded", "KillMode": "control-group", "SendSIGKILL": "yes",
    "KillSignal": "15", "FinalKillSignal": "9", "TimeoutStopUSec": "1min 30s",
    "Restart": "on-failure", "Delegate": "no", "Triggers": "", "TriggeredBy": "",
    # Native systemctl prints the present empty (uo) Job as "Job=". Missing,
    # numeric zero and nonempty jobs are not this positive no-job observation;
    # ListJobs below independently requires the typed empty manager array.
    "FileDescriptorStoreMax": "0", "NFileDescriptorStore": "0", "Job": "",
    "ExecStop": "", "ExecStopPost": "", "BusName": "", "Type": "simple",
    "NotifyAccess": "none", "RemainAfterExit": "no", "NeedDaemonReload": "no", "ControlPID": "0",
}
CGROUP_ROOT = Path("/sys/fs/cgroup")


def retirement_refused(detail):
    raise Refusal("attempt_retirement_unproven", detail)


class RetirementTransport(LocalTransport):
    """One fixed NSS user manager, independent of caller HOME/XDG/D-Bus."""
    def __init__(self, home, uid):
        self.home, self.uid = home, uid
        directory = self.identity(Path("/run/user") / str(uid))
        if (not stat.S_ISDIR(directory.mode) or directory.uid != uid
                or stat.S_IMODE(directory.mode) != 0o700):
            retirement_refused("fixed user runtime directory is not owned/private")
        bus = self.identity(Path("/run/user") / str(uid) / "bus")
        if not stat.S_ISSOCK(bus.mode) or bus.uid != uid:
            retirement_refused("fixed user-manager bus is not owned")

    def environment(self):
        return {"HOME": str(self.home), "XDG_RUNTIME_DIR": f"/run/user/{self.uid}",
                "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{self.uid}/bus",
                "LC_ALL": "C", "PATH": "/usr/bin:/bin", "SYSTEMD_PAGER": "", "SYSTEMD_COLORS": "0"}


@dataclass(frozen=True)
class RetirementBoundary:
    generation: Generation
    properties: dict[str, str]
    cgroup: str
    cgroup_identity: tuple[int, ...]
    parent_identity: tuple[int, ...]


@dataclass(frozen=True)
class StopWitness:
    """Internal, collected before START; never accepted from RPC/CLI JSON."""
    boundary: RetirementBoundary
    terminal: dict[str, str]
    birth_retirement: str
    cgroup_retirement: str


@dataclass(frozen=True)
class ControlCompletion:
    action: str
    unit: str
    uid: int
    result: str = "acknowledged_reaped"


class AttemptRetirement:
    """Exact canonical user unit + cgroup-v2 extinction, separate from restart.

    Command success is only an acknowledgement. The caller durably records
    issued control and this stopped witness; this class never retries control.
    """
    def __init__(self, settings, transport):
        if settings.unit != UNIT:
            retirement_refused("attempt retirement requires the canonical unit")
        self.runtime = LinuxRuntime(settings, transport)
        self.settings, self.transport = settings, transport

    def _require_typed_policy(self):
        """Lossless owned-object facts; absent SHOW arrays are not empty proof.

        Sockets= is not a Service D-Bus property. systemd255 maps that directive
        to UNIT_TRIGGERED_BY; service_collect_fds uses that relation (or an
        accepting socket's UNIT_TRIGGERS edge) plus the FD store. Positively
        empty real activation relations and zero store close those manager
        handoff routes. Owned fragment/retry policy, receipt equality and the
        exclusive outside-sender contract remain independent prerequisites.
        """
        prefix = ("/usr/bin/busctl", "--user", "--json=short", "--auto-start=no",
                  "--allow-interactive-authorization=no")
        expected = _UNIT_BUS_OBJECTS[UNIT]
        def typed(argv, signature):
            def unique(items):
                obj = {}
                for key, value in items:
                    if key in obj:
                        raise ValueError("duplicate property field")
                    obj[key] = value
                return obj
            try:
                obj = json.loads(_text(self.runtime._run(argv)), object_pairs_hook=unique)
                if type(obj) is not dict or set(obj) != {"type", "data"} or obj["type"] != signature:
                    raise ValueError("typed property envelope")
                return obj["data"]
            except (ValueError, UnicodeError, RecursionError):
                retirement_refused("invalid typed retirement property proof")
        resolved = typed((*prefix, "call", "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
                          "org.freedesktop.systemd1.Manager", "GetUnit", "s", UNIT), "o")
        if resolved != [expected]:
            retirement_refused("GetUnit did not resolve the exact canonical retirement unit object")
        for interface, name, signature in (
                ("Service", "ExecStop", "a(sasbttttuii)"),
                ("Service", "ExecStopPost", "a(sasbttttuii)"),
                ("Unit", "Triggers", "as"), ("Unit", "TriggeredBy", "as"),
                ("Service", "FileDescriptorStoreMax", "u"), ("Service", "NFileDescriptorStore", "u")):
            value = typed((*prefix, "get-property", "org.freedesktop.systemd1", expected,
                           "org.freedesktop.systemd1." + interface, name), signature)
            if ((signature == "u" and (type(value) is not int or value != 0))
                    or (signature != "u" and (type(value) is not list or value != []))):
                retirement_refused("nonempty/unknown stop-hook, activation or FD-store property")

    def properties(self):
        raw = self.runtime._run(("/usr/bin/systemctl", "--user", "show", "--no-pager", UNIT,
                                 "--property=" + ",".join(RETIREMENT_PROPERTIES)))
        props = {}
        for line in _text(raw).splitlines():
            key, eq, value = line.partition("=")
            if not eq or key not in RETIREMENT_PROPERTIES or key in props:
                retirement_refused("ambiguous retirement unit properties")
            props[key] = value
        missing = set(RETIREMENT_PROPERTIES) - set(props)
        if (missing - {"ExecStop", "ExecStopPost"}
                or any(props[k] != v for k, v in RETIREMENT_POLICY.items() if k in props)):
            retirement_refused("unsupported kill/delegate/activation/FD-store/job/control policy")
        self._require_typed_policy()  # Always require positive facts, even if SHOW prints an empty token.
        for name in ("ExecStop", "ExecStopPost"):
            props[name] = ""  # Internal empty-array token, justified ONLY by the typed proof above.
        # Empty arrays are proved by a typed manager response, never omitted
        # properties. Reject any job in this exclusive attended window.
        raw = self.runtime._run(("/usr/bin/busctl", "--user", "--json=short", "--auto-start=no",
                                 "--allow-interactive-authorization=no", "call", "org.freedesktop.systemd1",
                                 "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager", "ListJobs"))
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate job field")
                result[key] = value
            return result
        try:
            reply = json.loads(_text(raw), object_pairs_hook=pairs)
            # busctl CALL JSON wraps method return arguments: the sole return
            # value is itself the job array. Unlike get-property, empty is
            # data=[[]], never data=[]. Do not infer an omitted return value.
            if (type(reply) is not dict or set(reply) != {"type", "data"}
                    or reply["type"] != "a(usssoo)" or type(reply["data"]) is not list
                    or len(reply["data"]) != 1 or type(reply["data"][0]) is not list):
                raise ValueError("method envelope")
            jobs = reply["data"][0]
            for row in jobs:
                if (type(row) is not list or len(row) != 6 or type(row[0]) is not int
                        or not 0 <= row[0] < (1 << 32)
                        or any(type(v) is not str or "\0" in v for v in row[1:])
                        or any(not re.fullmatch(r"/(?:[A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)*)?", v) for v in row[4:])):
                    raise ValueError("typed job row")
                for value in row[1:]:
                    value.encode("utf-8", "strict")  # JSON escapes must still be valid D-Bus UTF-8.
        except (ValueError, UnicodeError, RecursionError):
            retirement_refused("invalid typed ListJobs method reply")
        if jobs:
            retirement_refused("user manager has pending jobs")  # Global, including other units.
        return props

    def boot(self):
        boot = _text(self.runtime._read(Path("/proc/sys/kernel/random/boot_id"), 64)).strip().replace("-", "")
        if not ID128.fullmatch(boot):
            retirement_refused("unknown boot")
        return boot

    def directory(self, path):
        info = self.transport.identity(path)
        if (not stat.S_ISDIR(info.mode) or info.uid not in (0, self.settings.uid)
                or self.transport.realpath(path) != path):
            retirement_refused("cgroup directory is not a canonical owned directory")
        return (info.device, info.inode, info.mode, info.uid, info.gid)

    def cgroup_path(self, text):
        expected = f"/user.slice/user-{self.settings.uid}.slice/user@{self.settings.uid}.service/app.slice/{UNIT}"
        if text != expected:
            retirement_refused("unsupported cgroup-v2 service scope")
        self.directory(CGROUP_ROOT)
        controllers = self.runtime._read(CGROUP_ROOT / "cgroup.controllers", 4096)
        if not controllers.strip():
            retirement_refused("unqualified cgroup-v2 hierarchy")
        return CGROUP_ROOT / text.lstrip("/")

    def populated(self, path):
        rows = {}
        for line in _text(self.runtime._read(path / "cgroup.events", 4096)).splitlines():
            pair = line.split()
            if len(pair) != 2 or pair[0] in rows or pair[0] not in ("populated", "frozen") or pair[1] not in ("0", "1"):
                retirement_refused("ambiguous hierarchical cgroup events")
            rows[pair[0]] = pair[1]
        if set(rows) != {"populated", "frozen"} or rows["frozen"] != "0":
            retirement_refused("missing or frozen hierarchical cgroup state")
        return rows["populated"] == "1"

    def bind(self, current):
        props = self.properties()
        gen = current.generation
        if (gen.unit != UNIT or gen.uid != self.settings.uid or self.boot() != gen.boot_id
                or props["ActiveState"] != "active" or props["SubState"] != "running"
                or props["MainPID"] != str(gen.pid) or props["InvocationID"] != gen.invocation_id):
            retirement_refused("retirement boundary differs from the exact admitted birth")
        path = self.cgroup_path(props["ControlGroup"])
        groups = self.runtime._read(Path("/proc") / str(gen.pid) / "cgroup", 16384)
        if groups != ("0::" + props["ControlGroup"] + "\n").encode() or not self.populated(path):
            retirement_refused("exact process is not in the populated service subtree")
        return RetirementBoundary(gen, props, props["ControlGroup"], self.directory(path), self.directory(path.parent))

    def birth_retired(self, generation):
        try:
            raw = self.runtime._read(Path("/proc") / str(generation.pid) / "stat", 4096)
        except FileNotFoundError:
            return "absent"
        # Permission errors are intentionally not interpreted as death.
        text = _text(raw)
        end = text.rfind(")")
        fields = text[end + 2:].split() if end > 0 else []
        if (not text.startswith(str(generation.pid) + " (") or len(fields) < 20
                or not re.fullmatch("[1-9][0-9]*", fields[19]) or fields[0] not in tuple("RSDZTtWXxKPI")):
            retirement_refused("old birth is inaccessible or malformed")
        if int(fields[19]) != generation.start_ticks:
            return "reused"
        if fields[0] in ("Z", "X", "x"):
            return "dead"
        retirement_refused("exact old birth is still alive")

    def stopped(self, boundary):
        if type(boundary) is not RetirementBoundary or self.boot() != boundary.generation.boot_id:
            retirement_refused("no same-boot stopped interval is established")
        terminal = self.properties()
        if (terminal["ActiveState"] != "inactive" or terminal["SubState"] != "dead"
                or terminal["MainPID"] != "0" or terminal["InvocationID"] not in ("", boundary.generation.invocation_id)
                or terminal["ControlGroup"] not in ("", boundary.cgroup)):
            retirement_refused("stop has not completed in the exact old unit")
        birth = self.birth_retired(boundary.generation)
        path = self.cgroup_path(boundary.cgroup)
        if self.directory(path.parent) != boundary.parent_identity:
            retirement_refused("old cgroup parent changed")
        try:
            identity = self.directory(path)
        except FileNotFoundError:
            if path.name in self.transport.listdir(path.parent):
                retirement_refused("old stopped scope removal is contradictory")
            scope = "removed"
        else:
            if identity != boundary.cgroup_identity or self.populated(path):
                retirement_refused("old recursive subtree survives or path was reused before witness")
            scope = "hierarchical_empty"
        # A second terminal/job/policy sample bounds the kernel observations.
        if self.properties() != terminal or self.boot() != boundary.generation.boot_id:
            retirement_refused("terminal stop facts changed around extinction observation")
        return StopWitness(boundary, terminal, birth, scope)

    def control(self, action):
        if action not in ("stop", "start"):
            retirement_refused("unsupported service action")
        try:
            result = self.transport.run(("/usr/bin/systemctl", "--user", "--no-ask-password", action, UNIT),
                                        timeout=100.0 if action == "stop" else 60.0, max_output=MAX_COMMAND)
            if type(result) is not CommandResult or type(result.returncode) is not int or result.returncode != 0:
                raise ValueError("service control not acknowledged")
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise Refusal("attempt_control_uncertain", "issued service control is uncertain; retain block, never automatically repeat") from exc
        # LocalTransport returns only after wait()/pipe closure and reaps its
        # exact child even on failure. A crashed recovery has no such durable
        # proof: its issued marker alone must never authorize continuation.
        return ControlCompletion(action, UNIT, self.settings.uid)
