"""Linux host operation coordinator. Installed authority, never RPC evidence.

The operation flock spans every external action; reservation.json spans the
stream. Controlled receipts are issued only after persisted restart intent,
old-process termination, a stable NEW generation and source-matched probe.
The single-authority/no-out-of-band-write contract is essential: file sampling
attests controlled startup, not cryptographic proof of Sunshine's loaded bytes.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time
import uuid
import zlib

import configuration as config
from protocol import Refusal, unique_json
import reservation as rs
import runtime_linux as rt

MAX_PRIVATE = 32768


class CapturePreflightRefusal(Refusal):
    """Rejected read-only capture evidence, never evidence of lost ownership."""


def hashed(value):
    return config.digest(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True).encode("ascii"))


def canonical_uuid(value):
    try:
        if type(value) is not str or not re.fullmatch(r"[0-9a-fA-F-]{36}", value):
            raise ValueError("invalid UUID")
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise Refusal("target_mismatch", "Sunshine instance identity must be a UUID") from exc


@dataclass(frozen=True)
class InstalledSettings:
    host_uniqueid: str
    base_port: int
    state_directory: Path
    apps_file: Path
    assets_directory: Path
    runtime: rt.Settings
    qualification: dict


@dataclass(frozen=True)
class Endpoint:
    host_uniqueid: str
    state: str  # FREE/BUSY, never a session counter
    currentgame: int


class PrivateFiles:
    """Fixed-name private files in one anchored, installation-created directory."""
    def __init__(self, directory):
        self.path = directory
        self.fd = None

    @staticmethod
    def check(info, directory=False):
        if (not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
                or info.st_uid != os.geteuid() or info.st_mode & 0o077
                or (not directory and info.st_nlink != 1)):
            raise Refusal("operation_unsafe", "controller state must be owner-private and unlinked")

    @contextmanager
    def locked(self):
        if os.name != "posix":
            raise Refusal("platform_runtime_unavailable", "Linux operation coordinator requires POSIX locking")
        import fcntl
        try:
            before = self.path.lstat()
            self.check(before, True)
            self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            opened = os.fstat(self.fd)
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise Refusal("operation_unsafe", "controller state directory changed")
            self.directory_identity = opened.st_dev, opened.st_ino
            lock = os.open("operation.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                           0o600, dir_fd=self.fd)
            try:
                self.check(os.fstat(lock))
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise Refusal("operation_contended", "another complete host operation is in progress") from exc
                try:
                    self.anchor()
                    linked = os.stat("operation.lock", dir_fd=self.fd, follow_symlinks=False)
                    if config.stable_identity(linked) != config.stable_identity(os.fstat(lock)):
                        raise Refusal("operation_unsafe", "permanent operation lock was replaced")
                    yield self
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
            finally:
                os.close(lock)
        except OSError as exc:
            raise Refusal("operation_unavailable", "private operation coordinator unavailable") from exc
        finally:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None

    def anchor(self):
        current = self.path.lstat()
        self.check(current, True)
        if (current.st_dev, current.st_ino) != self.directory_identity:
            raise Refusal("operation_unsafe", "controller state directory was replaced")

    def read(self, name, limit=MAX_PRIVATE):
        self.anchor()
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        except FileNotFoundError:
            return None
        try:
            before = os.fstat(fd)
            self.check(before)
            if before.st_size > limit:
                raise Refusal("operation_corrupt", "private record exceeds bound")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                data = stream.read(limit + 1)
            after = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            if (len(data) != before.st_size or config.stable_identity(before) != config.stable_identity(after)
                    or config.stable_identity(before) != config.stable_identity(os.fstat(fd))):
                raise Refusal("operation_unsafe", "private record changed while read")
            return data
        finally:
            os.close(fd)

    def write(self, name, data, limit=MAX_PRIVATE):
        if len(data) > limit:
            raise Refusal("operation_corrupt", "private record exceeds bound")
        previous = self.read(name, limit)
        temp = ".operation-" + secrets.token_hex(12)
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if self.read(name, limit) != previous:
                raise Refusal("operation_unsafe", "private record changed before publication")
            self.anchor()
            os.replace(temp, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.fsync(self.fd)
        finally:
            try:
                os.unlink(temp, dir_fd=self.fd)
            except FileNotFoundError:
                pass

    def document(self, name):
        data = self.read(name)
        if data is None:
            return None
        try:
            result = unique_json(data)
            if type(result) is not dict:
                raise ValueError("not an object")
            return result
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise Refusal("operation_corrupt", "private JSON record invalid; trusted repair required") from exc

    def save(self, name, value):
        self.write(name, json.dumps(value, sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=True).encode("ascii") + b"\n")


def topology_digest(native):
    facts = getattr(native, "topology_facts", None)
    if facts is None:
        facts = [{key: row.get(key) for key in
                  ("id", "connected", "width", "height", "refresh_num", "refresh_den",
                   "rotation", "card", "connector_id", "crtc_id")} for row in native]
        facts.sort(key=lambda row: row["id"])
    return hashed(facts)


def service_generation(generation):
    return rs.ServiceGeneration(generation.unit + ":" + str(generation.uid), generation.boot_id,
                                generation.pid, f"{generation.invocation_id}:{generation.start_ticks}:"
                                f"{generation.start_monotonic_usec}")


def authority(current):
    return (current.host_uniqueid, current.generation, current.unit, current.config.path,
            current.config.root, current.config.file, current.config.manager_environment_digest,
            current.binary.file, current.binary.supported_build)


def start_context(current):
    # Process birth must change; unit/config/binary/environment MUST NOT.
    return authority(current)[:1] + authority(current)[2:]


def _app_id(value):
    crc = zlib.crc32(value.encode("utf-8"))
    signed = crc if crc < (1 << 31) else crc - (1 << 32)
    if signed in (0, -(1 << 31)):
        raise Refusal("desktop_unqualified", "app ID falls outside positive signed client range")
    return abs(signed)


def inspected_desktop(settings, content, root):
    """Pinned process.cpp633-784: CRC32(name + PNG SHA256), indexed on collision.

    Support literal names and installed asset basenames; refuse environment or
    working-directory-dependent image identities. Reject aliases under the
    stock client's lowercase title lookup, including Unicode Kelvin-sign K.
    The client must also freshly confirm title resolution matches this exact
    ID before dispatch; a returned ID is not a CLI numeric app selector.
    No credentials are read.
    """
    controls = config.entries(content, keys=config.MANAGED | {b"file_apps", b"global_prep_cmd"},
                              lists={b"global_prep_cmd"})
    raw = controls.get(b"file_apps", (b"apps.json",))[0]
    try:
        path = Path(raw.decode("utf-8"))
    except UnicodeError as exc:
        raise Refusal("desktop_unqualified", "apps path invalid") from exc
    path = path if path.is_absolute() else Path(root) / path
    if path != settings.apps_file:
        raise Refusal("desktop_unqualified", "actual apps file differs from installed path")
    data, _ = read_owned(path, 1024 * 1024)
    try:
        tree = unique_json(data)
        if (type(tree) is not dict or type(tree.get("apps")) is not list or
                not 1 <= len(tree["apps"]) <= 128 or type(tree.get("env")) is not dict
                or len(tree["env"]) > 128 or any(type(k) is not str or type(v) is not str
                    or not 1 <= len(k) <= 128 or len(v) > 4096 for k, v in tree["env"].items())):
            raise ValueError("ambiguous apps/environment")
        ids, desktops, assets = set(), [], []
        for index, app in enumerate(tree["apps"]):
            if type(app) is not dict:
                raise ValueError("ambiguous app")
            name, image = app.get("name"), app.get("image-path", "")
            if (type(name) is not str or not 1 <= len(name.encode("utf-8")) <= 128 or "$" in name
                    or any(ord(c) < 32 for c in name) or type(image) is not str):
                raise ValueError("nonliteral app identity")
            image_hash = ""
            if image not in ("", "box.png", str(settings.assets_directory / "box.png")):
                if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}\.png", image):
                    raise ValueError("unqualified image path")
                png, _ = read_owned(settings.assets_directory / image, 8 * 1024 * 1024, asset=True)
                if not png.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError("invalid PNG identity")
                image_hash = config.digest(png)
                assets.append((image, image_hash))
            base = name + image_hash
            app_id = _app_id(base)
            if app_id in ids:
                app_id = _app_id(base + str(index))
            if app_id in ids:
                raise ValueError("colliding app IDs")
            ids.add(app_id)
            # Stock Moonlight startstream.cpp144-152 picks the FIRST title
            # whose QString::toLower equals "desktop". For this ASCII target,
            # Unicode lowercase includes Kelvin-sign K, not long-s casefolding.
            # Reject every differently spelled alias, independently of order.
            if name.lower() == "desktop" and name != "Desktop":
                raise ValueError("ambiguous stock-client Desktop title")
            if name == "Desktop":
                if (app.get("cmd", "") != "" or app.get("prep-cmd", []) != [] or
                        app.get("detached", []) != [] or
                        (app.get("exclude-global-prep-cmd", False) is not True and
                         controls.get(b"global_prep_cmd", (b"[]",))[0] != b"[]")):
                    raise ValueError("Desktop command/preparation hooks")
                desktops.append(app_id)
        if len(desktops) != 1:
            raise ValueError("missing or ambiguous Desktop")
        return rs.PositiveDesktop(desktops[0], hashed({"apps": config.digest(data), "assets": assets,
                                                      "global_prep": controls.get(b"global_prep_cmd", (b"[]",))[0].decode()}))
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise Refusal("desktop_unqualified", "Desktop must have a source-proven positive ID and no commands/hooks") from exc


def read_owned(path, limit, *, asset=False):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit
                    or before.st_uid not in ((0, os.geteuid()) if asset else (os.geteuid(),))
                    or before.st_mode & 0o022):
                raise Refusal("runtime_unconfigured", "installed file ownership/type/bounds invalid")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                data = stream.read(limit + 1)
            if (len(data) != before.st_size or config.stable_identity(before) != config.stable_identity(path.lstat())
                    or config.stable_identity(before) != config.stable_identity(os.fstat(fd))):
                raise Refusal("runtime_unconfigured", "installed file changed during read")
            return data, before
        finally:
            os.close(fd)
    except OSError as exc:
        raise Refusal("runtime_unconfigured", "required installed file unavailable") from exc


def receipt_from_json(obj):
    def construct(kind, data):
        if type(data) is not dict or set(data) != set(kind.__dataclass_fields__):
            raise ValueError("invalid receipt fields")
        return kind(**data)
    try:
        obj = dict(obj)
        for key, kind in (("generation", rt.Generation), ("qualified_build", rt.SupportedBuild)):
            obj[key] = construct(kind, obj[key])
        for key in ("config", "executable"):
            fingerprint = dict(obj[key])
            fingerprint["identity"] = construct(rt.FileIdentity, fingerprint["identity"])
            obj[key] = construct(rt.Fingerprint, fingerprint)
        return construct(rt.StartReceipt, obj)
    except (TypeError, ValueError, KeyError) as exc:
        raise Refusal("operation_corrupt", "protected start receipt invalid") from exc


@dataclass(frozen=True)
class OwnershipSample:
    current: rt.RuntimeObservation
    endpoint: Endpoint
    binding: rs.OwnershipBinding
    desktop: rs.PositiveDesktop
    content: bytes
    identity: os.stat_result


class HostController:
    def __init__(self, settings, runtime, enumerate_native, info, presentation, *,
                 authorize_enrollment=None, sleep=time.sleep, readiness_attempts=90):
        self.settings, self.runtime = settings, runtime
        self.enumerate_native, self.info, self.presentation = enumerate_native, info, presentation
        self.authorize_enrollment, self.sleep = authorize_enrollment, sleep
        self.readiness_attempts = readiness_attempts
        self.host = rs.HostIdentity(canonical_uuid(settings.host_uniqueid), settings.base_port)
        self.store = rs.ReservationStore(settings.state_directory, self.host)
        self.files = PrivateFiles(settings.state_directory)

    def endpoint(self, target, current):
        if (current.config.bind_address != target["address"] or current.config.base_port != target["port"]
                or target["port"] != self.settings.base_port or
                canonical_uuid(current.host_uniqueid) != self.host.uuid):
            raise Refusal("target_mismatch", "runtime bind address/base port/host differs from streaming target")
        info = self.info(target["address"], target["port"])
        if (type(info) is not Endpoint or canonical_uuid(info.host_uniqueid) != self.host.uuid
                or type(info.currentgame) is not int or not 0 <= info.currentgame < (1 << 31)
                or info.state not in ("FREE", "BUSY") or (info.state == "FREE" and info.currentgame != 0)):
            raise Refusal("endpoint_unknown", "pinned Sunshine application state unavailable or contradictory")
        return info

    def meta(self):
        value = self.files.document("operation.json")
        if value is None:
            return {"schema": 1, "enrollment": None, "lease": None}
        try:
            if (set(value) != {"schema", "enrollment", "lease"} or type(value["schema"]) is not int or value["schema"] != 1):
                raise ValueError("schema")
            enrollment, lease = value["enrollment"], value["lease"]
            if enrollment is not None:
                if (type(enrollment) is not dict or set(enrollment) != {"ceremony", "revision"}
                        or type(enrollment["revision"]) is not int or not 1 <= enrollment["revision"] < (1 << 63)
                        or str(uuid.UUID(enrollment["ceremony"])) != enrollment["ceremony"]):
                    raise ValueError("enrollment")
            if lease is not None:
                stages = {"selecting", "preparing", "write_intent", "config_written", "restart_intent",
                          "attested", "releasing", "rolled_back_requires_reconciliation"}
                allowed = {"owner_digest", "stage", "display_id", "selector", "topology", "desktop_digest",
                           "before_digest", "after_digest", "restart_id", "previous_generation",
                           "release_revision", "cancel_transaction", "selection_desktop", "prepared_revision"}
                if (type(lease) is not dict or not set(lease) <= allowed or lease.get("stage") not in stages
                        or type(lease.get("owner_digest")) is not str
                        or not re.fullmatch(r"[0-9a-f]{64}", lease["owner_digest"])):
                    raise ValueError("lease")
                if lease["stage"] != "selecting" and not {"display_id", "selector", "topology", "desktop_digest"} <= set(lease):
                    raise ValueError("selection")
                if lease["stage"] == "releasing" and not {"release_revision", "cancel_transaction"} <= set(lease):
                    raise ValueError("release")
                if "selection_desktop" in lease:
                    self.selection_desktop(lease)
                if "prepared_revision" in lease and (type(lease["prepared_revision"]) is not int
                        or not 1 <= lease["prepared_revision"] < (1 << 63)):
                    raise ValueError("prepared revision")
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise Refusal("operation_corrupt", "private operation schema invalid; trusted repair required") from exc
        return value

    def save(self, meta):
        self.files.save("operation.json", meta)

    def receipt(self, desktop):
        envelope = self.files.document("start-receipt.json")
        self.receipt_status = "absent"
        if envelope is None:
            return None
        if (set(envelope) != {"schema", "receipt", "desktop_digest", "issuance"}
                or type(envelope["schema"]) is not int or envelope["schema"] != 1):
            raise Refusal("operation_corrupt", "protected receipt envelope invalid")
        receipt = receipt_from_json(envelope["receipt"])
        try:
            issuance = envelope["issuance"]
            if (type(issuance) is not dict or set(issuance) != {"operation", "previous_generation", "enrollment_revision"}
                    or str(uuid.UUID(issuance["operation"])) != issuance["operation"]
                    or type(issuance["enrollment_revision"]) is not int or issuance["enrollment_revision"] < 1
                    or type(issuance["previous_generation"]) is not dict
                    or set(issuance["previous_generation"]) != set(rt.Generation.__dataclass_fields__)
                    or issuance["previous_generation"] == asdict(receipt.generation)):
                raise ValueError("issuance")
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise Refusal("operation_corrupt", "controlled-start receipt issuance record invalid") from exc
        if envelope["desktop_digest"] != desktop.definition_digest:
            self.receipt_status = "stale_definition"
            return None
        self.receipt_status = "current_candidate"
        return receipt

    def sample_ownership(self, target, *, use_receipt=True, require_receipt=False):
        """Fresh owned process/config/app facts, not display/capture authorization.

        Runtime observe still applies ALL unit/process policy checks. Receipt
        matching attests the controlled process/config/apps context even when
        the current capture probe or native display access is unavailable.
        """
        current = self.runtime.observe()
        content, identity = config.load(self.settings.runtime.config_file)
        if (config.digest(content) != current.config.file.sha256 or
                rt.FileIdentity.from_stat(identity) != current.config.file.identity):
            raise Refusal("config_changed", "runtime config sampling differs from current owned file")
        desktop = inspected_desktop(self.settings, content, current.config.root)
        receipt = self.receipt(desktop) if use_receipt else None
        if receipt is None and require_receipt:
            raise Refusal("ownership_unattested", "owned closure requires the controlled process/config/definition receipt")
        if receipt is not None:
            try:
                current = self.runtime.observe(receipt=receipt)
            except Refusal as exc:
                if exc.code != "receipt_mismatch":
                    raise
                self.receipt_status = "stale_runtime"
                if require_receipt:
                    raise Refusal("ownership_changed", "owned process/config receipt no longer matches") from exc
                # Selection may authorize a new controlled start, never closure.
                current = self.runtime.observe()
        endpoint = self.endpoint(target, current)
        again = self.runtime.observe(receipt=receipt if current.capture.receipt_bound else None)
        if authority(current) != authority(again):
            raise Refusal("observation_changed", "owned process/config changed around endpoint observation")
        binding = rs.OwnershipBinding(self.host, service_generation(again.generation), again.config.file.sha256)
        return OwnershipSample(again, endpoint, binding, desktop, content, identity)

    def sample(self, target, *, use_receipt=True):
        owned = self.sample_ownership(target, use_receipt=use_receipt)
        current, endpoint, desktop = owned.current, owned.endpoint, owned.desktop
        receipt = self.receipt(desktop) if current.capture.receipt_bound else None
        device = current.capture.effective_mem_type if current.capture.status == "verified" else None
        try:
            native = self.enumerate_native("linux", current.config.configured_capture,
                                           current.config.configured_encoder, device_type=device)
            screens, _ = self.presentation(native, current.config.file.sha256, self.host.uuid,
                                           runtime_capture=current.capture.status)
            topology = topology_digest(native)
        except Refusal as exc:
            if exc.code == "backend_unavailable":
                raise CapturePreflightRefusal(exc.code, exc.detail) from exc
            raise
        again = self.runtime.observe(receipt=receipt if current.capture.receipt_bound else None)
        if authority(current) != authority(again):
            raise Refusal("observation_changed", "owned runtime changed around native observation")
        if current.capture != again.capture:
            raise CapturePreflightRefusal("capture_changed", "capture probe changed during native preflight")
        binding = rs.RuntimeBinding(self.host, service_generation(current.generation), topology,
                                    current.config.file.sha256)
        snapshot = hashed({"topology": topology, "host": asdict(self.host),
                           "generation": asdict(current.generation), "config": asdict(current.config.file),
                           "unit": asdict(current.unit), "binary": asdict(current.binary.file),
                           "desktop": asdict(desktop), "mapping": [(s["id"], s["selector_mapping"]) for s in screens],
                           "device": device})
        return current, endpoint, native, screens, binding, snapshot, desktop, owned.content, owned.identity

    @staticmethod
    def idle(endpoint):
        if endpoint.state != "FREE" or endpoint.currentgame != 0:
            raise Refusal("host_busy", "existing or unknown application must remain untouched")

    @staticmethod
    def qualified(current):
        if not current.binary.supported_build:
            raise Refusal("build_unqualified", "installed binary lacks independent qualification for the pinned lifecycle source")

    @staticmethod
    def status_fields(status):
        return {"revision": status.revision, "phase": status.phase.value,
                "uncertainty": status.uncertainty.value if status.uncertainty else None,
                "attempt_revision": status.attempt_revision}

    def owner(self, req, meta):
        status = self.store.status()
        if status.revision != req["revision"]:
            raise Refusal("stale_revision", "reservation revision differs")
        lease = meta["lease"]
        if not lease or lease.get("owner_digest") != config.digest(req["capability"].encode("ascii")):
            raise Refusal("owner_mismatch", "operation is not owned by this capability")
        return rs.Reservation(rs.Capability(req["capability"]), status)

    def block(self, reason=rs.Uncertainty.CONTROL_LOST):
        status = self.store.status()
        if status.phase is not rs.Phase.RECONCILIATION_REQUIRED:
            self.store.require_reconciliation(status.revision, reason)

    def ticket_fields(self, ticket):
        return {**self.status_fields(ticket.status), "capability": ticket.capability.value}

    @staticmethod
    def selection_desktop(lease):
        record = lease.get("selection_desktop")
        if type(record) is not dict or set(record) != {"app_id", "definition_digest"}:
            raise Refusal("operation_corrupt", "selection has no inspected definition baseline")
        return rs.PositiveDesktop(**record)

    @staticmethod
    def pristine_selection(lease):
        return (lease is not None and lease.get("stage") == "selecting"
                and set(lease) == {"owner_digest", "stage", "selection_desktop"})

    @staticmethod
    def completed_preparation(ticket, lease):
        return (ticket.status.phase is rs.Phase.PREPARING and ticket.status.desktop is not None
                and not ticket.status.launch_attempted and lease.get("stage") == "attested"
                and lease.get("prepared_revision") == ticket.status.revision
                and lease.get("desktop_digest") == ticket.status.desktop.definition_digest)

    def preflight_failed(self, req, ticket, failure):
        """Preserve only a freshly proven owner BEFORE any new external intent.

        No phase repair and no arbitrary Refusal suppression: origin must be a
        known read-only capture gate. The same capability/revision/phase and
        private operation proof are re-read, then ownership is observed anew.
        """
        if ticket is None:
            return
        if isinstance(failure, CapturePreflightRefusal) and req["action"] == "observe_active":
            # A controlled launch makes the vendor re-run its capture/encoder
            # probe; the bounded journal window can read that probe mid-write.
            # While the same owner attempt is fresh and still unconfirmed, that
            # read-only transient is "not yet confirmed", never owner loss: the
            # client already retries launch_unconfirmed until its deadline.
            try:
                meta = self.meta()
                latest = self.owner(req, meta)
                lease = meta["lease"]
                settling = (latest == ticket and latest.status.phase is rs.Phase.CONNECTING
                            and latest.status.launch_attempted and not latest.status.active_observed
                            and lease is not None and lease.get("stage") == "attested")
            except Refusal:
                settling = False
            if settling:
                raise Refusal("launch_unconfirmed",
                              "capture evidence still settling after controlled launch") from failure
        known = False
        if isinstance(failure, CapturePreflightRefusal):
            try:
                meta = self.meta()
                latest = self.owner(req, meta)
                lease = meta["lease"]
                phase = latest.status.phase
                selecting = (req["action"] in {"refresh", "prepare"} and phase is rs.Phase.SELECTING
                             and self.pristine_selection(lease) and not latest.status.launch_attempted)
                active = (req["action"] == "resume" and phase is rs.Phase.ACTIVE
                          and latest.status.active_observed and lease.get("stage") == "attested")
                prepared = req["action"] == "launch" and self.completed_preparation(latest, lease)
                if latest == ticket and (selecting or active or prepared):
                    owned = self.sample_ownership(req["target"], require_receipt=not selecting)
                    self.qualified(owned.current)
                    expected = self.selection_desktop(lease) if selecting else latest.status.desktop
                    same = (owned.binding == rs.OwnershipBinding.from_runtime(latest.status.binding)
                            and owned.desktop == expected
                            and (selecting or owned.desktop.definition_digest == lease.get("desktop_digest")))
                    if active:
                        same = same and owned.endpoint.state == "BUSY" and owned.endpoint.currentgame == expected.app_id
                    else:
                        same = same and owned.endpoint.state == "FREE" and owned.endpoint.currentgame == 0
                    known = same and self.store.status() == ticket.status and self.meta() == meta
            except Refusal:
                known = False  # Missing/changed ownership proof is a block, never fallback evidence.
        if not known:
            self.block(rs.Uncertainty.RUNTIME_CHANGED)
            if isinstance(failure, CapturePreflightRefusal):
                raise Refusal("ownership_changed", "fresh ownership or completed-operation proof unavailable after capture refusal; reconciliation required") from failure

    def check_selected(self, sampled, lease):
        current, endpoint, native, screens, binding, _, desktop, _, _ = sampled
        if desktop.definition_digest != lease["desktop_digest"]:
            raise Refusal("desktop_changed", "inspected Desktop changed")
        if binding.topology_digest != lease["topology"]:
            raise CapturePreflightRefusal("selection_changed", "physical topology changed")
        matches = [row for row in native if row["id"] == lease["display_id"]]
        if len(matches) != 1 or matches[0]["selector"] != lease["selector"]:
            raise CapturePreflightRefusal("selection_changed", "selected native display/selector is missing")
        selected = matches[0]
        if (current.capture.status != "verified" or selected["mapping_status"] != "mapped"
                or selected["native_reason"] is not None or current.config.configured_output != lease["selector"]
                or type(selected.get("connector_id")) is not int or
                current.capture.probe.connector_ids != (selected["connector_id"],)
                or sum(row.get("connector_id") == selected["connector_id"] for row in native
                       if row["mapping_status"] != "filtered" and row["native_reason"] is None) != 1):
            raise CapturePreflightRefusal("capture_correspondence_unproven", "fresh probe/native connector does not match the exact selected output")
        return next(s for s in screens if s["id"] == lease["display_id"])

    def retired(self, old, new):
        if new.generation.boot_id != old.generation.boot_id:
            raise Refusal("generation_changed", "boot changed during controlled operation")
        try:
            data = self.runtime.transport.read(Path("/proc") / str(old.generation.pid) / "stat", limit=4096)
        except FileNotFoundError:
            return True
        except OSError as exc:
            raise Refusal("termination_unknown", "old process termination cannot be observed") from exc
        if type(data) is not bytes or len(data) > 4096:
            raise Refusal("termination_unknown", "old process identity observation invalid")
        try:
            text = data.decode("ascii")
            end = text.rfind(")")
            fields = text[end + 2:].split()
            if not text.startswith(str(old.generation.pid) + " (") or len(fields) < 20:
                raise ValueError("invalid stat")
            return int(fields[19]) != old.generation.start_ticks or fields[0] in ("Z", "X")
        except (ValueError, UnicodeError) as exc:
            raise Refusal("termination_unknown", "old process identity observation invalid") from exc

    def issue_start(self, before, target, meta, desktop):
        last = None
        deadline = time.monotonic() + 45  # Includes vendor ExecStartPre sleep5 and startup probe.
        for _ in range(self.readiness_attempts):
            if time.monotonic() >= deadline:
                break
            try:
                after = self.runtime.observe()
                self.idle(self.endpoint(target, after))
                if after.generation == before.generation:
                    raise Refusal("readiness_pending", "restart has not produced a new generation")
                if start_context(after) != start_context(before) or not after.binary.supported_build:
                    raise Refusal("start_context_changed", "unit/config/environment/qualified binary changed across startup")
                if not self.retired(before, after):
                    raise Refusal("readiness_pending", "old generation remains alive")
                if after.capture.probe is None:
                    raise Refusal("readiness_pending", "new generation has no completed current probe")
                content, identity = config.load(self.settings.runtime.config_file)
                now_desktop = inspected_desktop(self.settings, content, after.config.root)
                if now_desktop != desktop:
                    raise Refusal("desktop_changed", "apps/asset definitions changed across startup")
                # This is the ONLY receipt constructor: under durable external intent,
                # never from request fields or a manifest/static receipt.
                receipt = rt.StartReceipt(after.host_uniqueid, after.generation, after.unit.ownership_digest,
                                          after.config.path, after.config.root, after.config.file,
                                          after.config.manager_environment_digest, after.binary.file,
                                          self.settings.runtime.build)
                verified = self.runtime.observe(receipt=receipt)
                self.idle(self.endpoint(target, verified))
                if authority(verified) != authority(after) or verified.capture.status != "verified":
                    raise Refusal("readiness_pending", "startup facts/probe not stable and verified")
                self.files.save("start-receipt.json", {"schema": 1, "receipt": asdict(receipt),
                                "desktop_digest": desktop.definition_digest,
                                "issuance": {"operation": meta["lease"]["restart_id"],
                                             "previous_generation": asdict(before.generation),
                                             "enrollment_revision": meta["enrollment"]["revision"]}})
                return verified
            except Refusal as exc:
                last = exc
                if exc.code in {"start_context_changed", "desktop_changed", "target_mismatch", "host_busy", "generation_changed"}:
                    raise
            self.sleep(0.5)
        raise Refusal("readiness_failed", "controlled restart did not establish new stable endpoint/probe readiness") from last

    def prepare(self, req, meta, ticket, sampled):
        current, endpoint, native, screens, binding, snapshot, desktop, content, identity = sampled
        self.idle(endpoint)
        self.qualified(current)
        if snapshot != req["snapshot"]:
            raise Refusal("stale_snapshot", "display/config/service snapshot differs from the presented menu")
        choices = [s for s in screens if s["id"] == req["display_id"]]
        if len(choices) != 1 or not choices[0]["selectable"] or current.config.configured_capture != "kms":
            raise Refusal("invalid_selection", "display is not an eligible preparation intent")
        selected = choices[0]
        ticket = self.store.begin_prepare(ticket, rs.IdleRuntime(binding, 0))
        lease = meta["lease"]
        lease.update(stage="preparing", display_id=selected["id"], selector=selected["selector"],
                     topology=binding.topology_digest, desktop_digest=desktop.definition_digest)
        self.save(meta)
        updated = config.select_output(content, selected["selector"])
        wrote, dispatched, written_identity = False, False, None
        try:
            noop = False
            if updated == content:
                try:
                    self.check_selected(sampled, lease)
                    noop = True
                except Refusal as exc:
                    if exc.code != "capture_correspondence_unproven":
                        raise
                    # A historical encoder/connector proof can be stale while
                    # current owned idle preparation is fully authorized. Use
                    # the ordinary controlled restart; do not erase the receipt.
            if not noop:
                self.files.write("prepare-backup.conf", content, limit=1024 * 1024)
                lease.update(stage="write_intent", before_digest=config.digest(content),
                             after_digest=config.digest(updated))
                self.save(meta)
                if updated != content:
                    config.replace_if_same(self.settings.runtime.config_file, content, identity, updated)
                    wrote = True
                written, written_identity = config.load(self.settings.runtime.config_file)
                if written != updated:
                    raise Refusal("config_changed", "selection transaction bytes changed")
                lease["stage"] = "config_written"
                self.save(meta)
                fresh = self.runtime.observe()
                if (fresh.generation != current.generation or fresh.unit != current.unit or
                        fresh.binary.file != current.binary.file or fresh.config.file.sha256 != config.digest(updated)):
                    raise Refusal("start_context_changed", "runtime changed before owned restart")
                controller = self
                class Owner:
                    @contextmanager
                    def restart_window(self, expected):
                        yield self

                    def revalidate_idle(self, observed):
                        status = controller.store.status()
                        if status != ticket.status or status.phase is not rs.Phase.PREPARING:
                            raise Refusal("owner_mismatch", "restart ownership changed")
                        controller.idle(controller.endpoint(req["target"], observed))
                        return rt.OwnerIdleEvidence(observed.host_uniqueid, observed.generation,
                                  observed.config.file.sha256, status.revision, meta["enrollment"]["revision"],
                                  "preparing", desktop.app_id, "FREE", 0, "enrolled_no_launch", False)

                    def mark_restart_attempted(self, observed):
                        nonlocal dispatched
                        lease.update(stage="restart_intent", restart_id=str(uuid.uuid4()),
                                     previous_generation=asdict(observed.generation))
                        controller.save(meta)  # durable BEFORE queueing fixed systemd operation
                        dispatched = True
                self.runtime.guarded_restart(fresh, owner=Owner())
                self.issue_start(fresh, req["target"], meta, desktop)
            final = self.sample(req["target"])
            self.idle(final[1])
            selected = self.check_selected(final, lease)
            if (config.digest(final[7]) != config.digest(updated) or
                    (written_identity is not None and config.stable_identity(final[8]) != config.stable_identity(written_identity))):
                raise Refusal("config_changed", "selection config changed before preparation completion")
            ticket = self.store.prepared(ticket, rs.PreparedRuntime(binding, final[4], desktop, 0,
                              binding.generation if final[4].generation != binding.generation else None))
            lease.update(stage="attested", prepared_revision=ticket.status.revision)
            self.save(meta)
            return {**self.ticket_fields(ticket), "display": selected, "snapshot": final[5],
                    "topology_digest": final[4].topology_digest, "restarted": not noop,
                    "launch_authorized": True, "desktop_app_id": desktop.app_id, "desktop_title": "Desktop",
                    "app_resolution": "fresh_case_insensitive_title_and_id_match"}
        except Exception:
            # Restore only our unchanged bytes, while independently observing idle.
            # A queued/uncertain restart never becomes a clean abort. Restored files
            # do not imply the new process loaded them: reconciliation retains owner.
            if wrote and written_identity is not None:
                try:
                    observed = self.runtime.observe()
                    self.idle(self.endpoint(req["target"], observed))
                    if (observed.unit == current.unit and observed.binary.file == current.binary.file and
                            config.same(self.settings.runtime.config_file, updated, written_identity)):
                        config.replace_if_same(self.settings.runtime.config_file, updated, written_identity, content)
                        lease["stage"] = "rolled_back_requires_reconciliation"
                        self.save(meta)
                except (Refusal, OSError):
                    pass
            self.block(rs.Uncertainty.RUNTIME_CHANGED if dispatched else rs.Uncertainty.CONTROL_LOST)
            raise

    def close_owned(self, req, meta, ticket, owned):
        """Close only the pinned owned app; capture loss is not ownership loss."""
        endpoint, binding, desktop = owned.endpoint, owned.binding, owned.desktop
        lease = meta["lease"]
        try:
            if req["action"] == "begin_release":
                if endpoint.state != "BUSY":
                    raise Refusal("application_mismatch", "owned positive Desktop required before release")
                ticket = self.store.begin_release(ticket, rs.OwnedDesktopRunning(binding, desktop, endpoint.currentgame))
                lease.update(stage="releasing", release_revision=ticket.status.revision,
                             cancel_transaction=str(uuid.uuid4()))
                self.save(meta)
                return self.ticket_fields(ticket)
            self.idle(endpoint)
            if lease.get("stage") != "releasing" or lease.get("release_revision") != ticket.status.revision:
                raise Refusal("closure_unconfirmed", "durable positive-before-cancel release intent is missing")
            # Pinned nvhttp.cancel terminates sessions before clearing placebo.
            # Same owned generation/config/definition and fresh FREE after our
            # positive RELEASING intent prove closure under single authority.
            # No current display topology/probe or caller completion flag does.
            status = self.store.release(ticket, rs.DesktopClosure(binding, desktop, 0,
                                       ticket.status.revision, lease["cancel_transaction"]))
            meta["lease"] = None
            self.save(meta)
            return {**self.status_fields(status), "closure_basis": "same_generation_owned_desktop_cancelled"}
        except Refusal as exc:
            if exc.code not in {"host_busy", "phase_mismatch"}:
                self.block(rs.Uncertainty.CLOSURE_UNCONFIRMED if req["action"] == "finish_release"
                           else rs.Uncertainty.RUNTIME_CHANGED)
            raise

    def handle(self, req):
        # Read-only requests also use this lock: no partial transaction menu/status.
        with self.files.locked():
            meta = self.meta()
            action, target = req["action"], req["target"]
            status = self.store.status()
            ticket = self.owner(req, meta) if action in {"refresh", "prepare", "launch", "resume", "observe_active",
                          "begin_release", "finish_release", "abort", "control_lost"} else None
            if action == "control_lost":
                self.block(rs.Uncertainty.OWNER_LOST)
                return self.status_fields(self.store.status())
            cancelling_selection = action == "abort" and ticket.status.phase is rs.Phase.SELECTING
            cancelling_prepared = (action == "abort" and ticket.status.phase is rs.Phase.PREPARING
                                   and self.completed_preparation(ticket, meta["lease"]))
            if cancelling_selection and not self.pristine_selection(meta["lease"]):
                self.block()
                raise Refusal("reconciliation_required", "external selection intent cannot be cleanly cancelled")
            if action in {"status", "begin_release", "finish_release"} or cancelling_selection or cancelling_prepared:
                try:
                    owned = self.sample_ownership(target, require_receipt=cancelling_prepared or action in {"begin_release", "finish_release"})
                except Refusal:
                    if ticket is not None:
                        self.block(rs.Uncertainty.CLOSURE_UNCONFIRMED if action == "finish_release"
                                   else rs.Uncertainty.RUNTIME_CHANGED)
                    raise
                if cancelling_selection or cancelling_prepared:
                    self.idle(owned.endpoint)
                    self.qualified(owned.current)
                    expected = self.selection_desktop(meta["lease"]) if cancelling_selection else ticket.status.desktop
                    if owned.desktop != expected:
                        self.block(rs.Uncertainty.RUNTIME_CHANGED)
                        raise Refusal("desktop_changed", "owned cleanup definition no longer matches")
                    evidence = (rs.SelectionAbort(owned.binding, 0) if cancelling_selection else
                                rs.PreparedAbort(owned.binding, owned.desktop, 0, meta["lease"]["prepared_revision"]))
                    status = self.store.abort(ticket, evidence)
                    meta["lease"] = None
                    self.save(meta)
                    return {**self.status_fields(status), "sampling": "ownership"}
                if action == "status":
                    return {**self.status_fields(status), "target": target, "host_uniqueid": self.host.uuid,
                            "activity": "busy" if owned.endpoint.state != "FREE" else "idle_application",
                            "sampling": "ownership", "runtime": owned.current.capture.status,
                            "runtime_reason": owned.current.capture.reason, "receipt_status": self.receipt_status}
                return self.close_owned(req, meta, ticket, owned)
            try:
                sampled = self.sample(target)
            except Refusal as exc:
                self.preflight_failed(req, ticket, exc)
                raise
            current, endpoint, native, screens, binding, snapshot, desktop, _, _ = sampled
            if action == "list":
                return {**self.status_fields(status), "target": target, "host_uniqueid": self.host.uuid,
                        "activity": "busy" if endpoint.state != "FREE" or endpoint.currentgame else "idle_application",
                        "snapshot": snapshot, "topology_digest": binding.topology_digest,
                        "screens": screens, "selection_ready": (status.phase is rs.Phase.AVAILABLE
                            and endpoint.state == "FREE" and endpoint.currentgame == 0
                            and current.binary.supported_build and meta["enrollment"] is not None),
                        "runtime": current.capture.status, "runtime_reason": current.capture.reason,
                        "receipt_status": self.receipt_status}
            if action == "enroll":
                if status.revision != req["revision"] or status.phase is not rs.Phase.RECONCILIATION_REQUIRED or meta["lease"]:
                    raise Refusal("enrollment_refused", "enrollment cannot replace a current managed owner/baseline")
                self.idle(endpoint)
                self.qualified(current)
                if self.authorize_enrollment is None or self.authorize_enrollment() is not True:
                    raise Refusal("operator_authorization_required", "explicit exclusive host onboarding ceremony required")
                sampled = self.sample(target)  # authorization alone is not fresh host evidence
                self.idle(sampled[1])
                self.qualified(sampled[0])
                ceremony = str(uuid.uuid4())
                status = self.store.reconcile(req["revision"], rs.ExclusiveOnboarding(rs.IdleRuntime(sampled[4], 0), ceremony))
                meta["enrollment"] = {"ceremony": ceremony, "revision": status.revision}
                self.save(meta)
                return self.status_fields(status)
            if action == "acquire":
                self.idle(endpoint)
                self.qualified(current)
                if snapshot != req["snapshot"]:
                    raise Refusal("stale_snapshot", "current menu no longer matches")
                if not meta["enrollment"]:
                    raise Refusal("operator_authorization_required", "host is not enrolled")
                ticket = self.store.acquire(req["revision"], rs.IdleRuntime(binding, 0))
                meta["lease"] = {"owner_digest": config.digest(ticket.capability.value.encode("ascii")),
                                  "stage": "selecting", "selection_desktop": asdict(desktop)}
                self.save(meta)
                return {**self.ticket_fields(ticket), "snapshot": snapshot}
            if action == "refresh":
                self.idle(endpoint)
                self.qualified(current)
                # SELECTING is untouched intent. Any external-operation journal
                # entry forbids this exception, even if a phase was corrupted.
                if not self.pristine_selection(meta["lease"]):
                    raise Refusal("phase_mismatch", "only untouched selection intent may refresh")
                if desktop != self.selection_desktop(meta["lease"]):
                    self.block(rs.Uncertainty.RUNTIME_CHANGED)
                    raise Refusal("desktop_changed", "selection definition changed")
                ticket = self.store.refresh_selection(ticket, rs.IdleRuntime(binding, 0))
                return {**self.ticket_fields(ticket), "snapshot": snapshot, "topology_digest": binding.topology_digest,
                        "screens": screens}
            if action == "prepare":
                return self.prepare(req, meta, ticket, sampled)
            lease = meta["lease"]
            if action == "abort":
                self.idle(endpoint)
                if ticket.status.launch_attempted:
                    self.block(rs.Uncertainty.LAUNCH_UNCONFIRMED)
                    raise Refusal("reconciliation_required", "attempted launch cannot be cleanly aborted")
                if ticket.status.phase is rs.Phase.SELECTING and not self.pristine_selection(lease):
                    self.block()
                    raise Refusal("reconciliation_required", "external selection intent cannot be cleanly cancelled")
                if ticket.status.phase is rs.Phase.PREPARING:
                    self.block()
                    raise Refusal("reconciliation_required", "incomplete preparation cannot be assumed clean")
                status = self.store.abort(ticket, rs.PrelaunchAbort(binding, 0))
                meta["lease"] = None
                self.save(meta)
                return self.status_fields(status)
            try:
                selected = self.check_selected(sampled, lease)
            except Refusal as exc:
                self.preflight_failed(req, ticket, exc)
                raise
            # Failures after durable launch/resume intent are NOT preflight.
            try:
                if action == "launch":
                    self.idle(endpoint)
                    ticket = self.store.attempt_launch(ticket, rs.IdleRuntime(binding, 0))
                    return {**self.ticket_fields(ticket), "desktop_app_id": desktop.app_id,
                            "desktop_title": "Desktop", "display": selected, "quit_policy": "explicit_quit",
                            "app_resolution": "fresh_case_insensitive_title_and_id_match"}
                if action == "observe_active":
                    if endpoint.state != "BUSY":
                        raise Refusal("launch_unconfirmed", "matching positive Desktop has not been observed")
                    ticket = self.store.observe_active(ticket, rs.DesktopActive(binding, desktop, endpoint.currentgame,
                                                                              ticket.status.attempt_revision))
                elif action == "resume":
                    if endpoint.state != "BUSY":
                        raise Refusal("application_mismatch", "owned positive Desktop required before resume")
                    ticket = self.store.attempt_reconnect(ticket, rs.DesktopRunning(binding, desktop, endpoint.currentgame))
                return self.ticket_fields(ticket)
            except Refusal as exc:
                if exc.code not in {"launch_unconfirmed", "host_busy", "phase_mismatch"}:
                    self.block(rs.Uncertainty.RUNTIME_CHANGED)
                raise
