"""Version-1 display controller over system SSH, with bounded private pipes.

No I/O on import/construction. ``runner`` is a trusted Popen-compatible factory;
it must return a process with binary stdin/stdout/stderr pipes. The transport,
not the runner, owns delivery, concurrent draining, deadlines and reaping.
Successful list/status pins host_uniqueid before identity-less mutations.
call() returns the FULL validated success envelope, including version/ok/action.
ControllerError.code/detail/remote_refusal are public; remote_refusal is a strict
bool, true only for an exact host error over successful SSH. It does not prove
that a refused operation left no intent.
Capabilities belong only in the returned dict and subsequent JSON stdin, never
in argv or diagnostics. There are no retries or alternate transports.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import os
import re
import selectors
import subprocess
import threading
import time
import unicodedata
import uuid

__all__ = ["ControllerTransport", "ControllerError"]
VERSION = 1
REMOTE_COMMAND = "quick-deploy-display-control"
MAX_INPUT = 2048
MAX_OUTPUT = 32768
MAX_STDERR = 8192
CALL_TIMEOUT = 30.0
PREPARE_TIMEOUT = 120.0  # Host readiness alone allows 45s, plus bounded commands.
CLEANUP_TIMEOUT = 2.0
HEX = re.compile(r"[0-9a-f]{64}\Z")
CODE = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
DESTINATION = re.compile(r"(?:[A-Za-z0-9][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
DISPLAY_ID = re.compile(r"(?:kms:[A-Za-z0-9_.:-]{1,80}|win:\{[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\}|mac:[1-9][0-9]{0,9})\Z")
# Unmapped Windows rows have native-only IDs that cannot be sent to prepare.
SCREEN_ID = re.compile(DISPLAY_ID.pattern.replace("win:", "win:(?:native:)?"))
MAX_REVISION = (1 << 63) - 1
OWNER_ACTIONS = {"refresh", "prepare", "launch", "resume", "observe_active",
                 "begin_release", "finish_release", "abort", "control_lost"}
ACTIONS = OWNER_ACTIONS | {"list", "status", "enroll", "acquire"}
PHASES = {"available", "selecting", "preparing", "connecting", "active",
          "reconnecting", "releasing", "reconciliation_required"}
UNCERTAINTIES = {"needs_onboarding", "control_lost", "owner_lost",
                 "launch_unconfirmed", "closure_unconfirmed", "runtime_changed"}
STATUS_KEYS = {"revision", "phase", "uncertainty", "attempt_revision"}
SCREEN_KEYS = {"id", "label", "primary", "connected", "selector", "selector_mapping",
               "selector_candidate", "mapping_reason", "native_reason", "runtime_capture",
               "capture_candidate", "selectable", "selection_intent", "reason", "width",
               "height", "refresh_num", "refresh_den", "requested_fps", "rotation"}
# Strip entire terminal sequences, then controls (including bidi/zero-width),
# before redaction so embedded escape sequences cannot split a secret.
TERMINAL = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\|$)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_]")
ASSIGNMENT = re.compile(r"(?i)\b(capability|token|access_token|password|secret|pin)"
                        r"[\"']?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)")
LONG_SECRET = re.compile(r"[0-9a-fA-F]{64,}|[A-Za-z0-9_+/=-]{32,}")


def _sanitize(text):
    if type(text) is not str:
        return "controller operation failed"
    text = TERMINAL.sub("", text)
    text = "".join(" " if c in "\r\n\t" else c for c in text
                   if c in "\r\n\t" or not unicodedata.category(c).startswith("C"))
    text = ASSIGNMENT.sub(lambda m: m.group(1) + "=[redacted]", text)
    return LONG_SECRET.sub("[redacted]", text).strip()[:512]


class ControllerError(Exception):
    """Only sanitized code/detail in attributes, args, str and repr."""
    def __init__(self, code, detail, *, remote_refusal=False):
        self.code = (code if type(code) is str and CODE.fullmatch(code)
                     and not re.search(r"[0-9a-f]{64}", code) else "controller_error")
        self.detail = _sanitize(detail) or "controller operation failed"
        self.remote_refusal = remote_refusal is True
        super().__init__(f"{self.code}: {self.detail}")


def _integer(value, low=0, high=MAX_REVISION):
    return type(value) is int and low <= value <= high


def _hex(value):
    return type(value) is str and HEX.fullmatch(value) is not None


def _text(value, maximum, *, empty=False):
    return (type(value) is str and (0 if empty else 1) <= len(value) <= maximum
            and not any(unicodedata.category(c).startswith("C") for c in value))


def _reason(value):
    return value is None or (type(value) is str and CODE.fullmatch(value) is not None)


def _target_valid(value):
    if type(value) is not dict or set(value) != {"address", "port"}:
        return False
    try:
        address = value["address"]
        ip = ipaddress.IPv4Address(address) if type(address) is str else None
        return (ip is not None and str(ip) == address and ip in ipaddress.IPv4Network("100.64.0.0/10")
                and _integer(value["port"], 1029, 65514))
    except ValueError:
        return False


def _unique_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    def constant(_):
        raise ValueError("nonfinite number")
    return json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)


def _require(condition, detail="controller reply violates the action schema"):
    if not condition:
        raise ControllerError("invalid_response", detail)


def _screen(screen):
    _require(type(screen) is dict and set(screen) == SCREEN_KEYS)
    _require(type(screen["id"]) is str and SCREEN_ID.fullmatch(screen["id"]) is not None)
    _require(_text(screen["label"], 64, empty=True))
    for key in ("primary", "connected", "selector_candidate", "capture_candidate", "selectable"):
        _require(type(screen[key]) is bool)
    _require(screen["selector"] is None or _text(screen["selector"], 80))
    _require(screen["selector_mapping"] in {"mapped", "filtered", "unverified", "unavailable"})
    _require(screen["runtime_capture"] in {"verified", "unverified"})
    _require(all(_reason(screen[key]) for key in ("native_reason", "mapping_reason", "reason"))
             and screen["reason"] is not None)
    _require(screen["selection_intent"] is None or screen["selection_intent"] == "prepare")
    width, height, num, den = (screen[k] for k in ("width", "height", "refresh_num", "refresh_den"))
    _require((width is None and height is None) or
             (_integer(width, 16, 16384) and _integer(height, 16, 16384)))
    _require((num is None and den is None) or
             (_integer(num, 1) and _integer(den, 1) and den <= num <= 1000 * den))
    fps = (2 * num + den) // (2 * den) if num is not None else None
    if fps is not None and not 1 <= fps <= 240:
        fps = None
    _require(screen["requested_fps"] is None or _integer(screen["requested_fps"], 1, 240))
    _require(screen["requested_fps"] == fps)
    _require(screen["rotation"] is None or _integer(screen["rotation"], -1, 360))
    mapped = screen["selector_mapping"] == "mapped"
    candidate = (screen["connected"] and screen["selector"] is not None and width is not None
                 and fps is not None and screen["native_reason"] is None and
                 (mapped or (screen["selector_mapping"] == "unverified" and
                  screen["mapping_reason"] in {"effective_encoder_device_unknown", "encoder_filter_required"})))
    _require(screen["selectable"] == candidate and screen["selector_candidate"] == (mapped and candidate)
             and screen["capture_candidate"] == (candidate and mapped and screen["runtime_capture"] == "verified")
             and screen["selection_intent"] == ("prepare" if candidate else None))
    reason = "preparation_required" if candidate else screen["native_reason"] or screen["mapping_reason"] or "refresh_unsupported"
    _require(screen["reason"] == reason)
    _require(not candidate or DISPLAY_ID.fullmatch(screen["id"]) is not None)


def _screens(screens, runtime=None):
    _require(type(screens) is list and len(screens) <= 32)
    seen = set()
    for screen in screens:
        _screen(screen)
        _require(screen["id"] not in seen)
        seen.add(screen["id"])
        _require(runtime is None or screen["runtime_capture"] == runtime)


class ControllerTransport:
    def __init__(self, ssh_target: str, ssh_port: int | None, target: dict, *, runner=None):
        if (type(ssh_target) is not str or len(ssh_target) > 512 or not DESTINATION.fullmatch(ssh_target)
                or (ssh_port is not None and not _integer(ssh_port, 1, 65535)) or not _target_valid(target)):
            raise ControllerError("invalid_route", "expected validated SSH destination, SSH port and canonical Tailnet target")
        if runner is not None and not callable(runner):
            raise ControllerError("invalid_route", "runner must be a trusted Popen-compatible factory")
        self._ssh_target, self._ssh_port, self._target = ssh_target, ssh_port, dict(target)
        self._runner = subprocess.Popen if runner is None else runner
        self._host_uniqueid = None
        self._lock = threading.Lock()
        self._broken = False

    @property
    def host_uniqueid(self):
        """Canonical UUID pinned by the first valid list/status, or None."""
        return self._host_uniqueid

    def _request(self, action, fields):
        if type(action) is not str or action not in ACTIONS:
            raise ControllerError("invalid_request", "unknown controller action")
        keys = set()
        if action not in {"list", "status"}:
            keys.add("revision")
        if action in OWNER_ACTIONS:
            keys.add("capability")
        if action in {"acquire", "prepare"}:
            keys.add("snapshot")
        if action == "prepare":
            keys.add("display_id")
        if (set(fields) != keys or ("revision" in keys and not _integer(fields["revision"], 0, MAX_REVISION - 1))
                or any(not _hex(fields[key]) for key in keys & {"capability", "snapshot"})
                or ("display_id" in keys and (type(fields["display_id"]) is not str
                    or not DISPLAY_ID.fullmatch(fields["display_id"])))):
            raise ControllerError("invalid_request", "unknown, missing or invalid controller action fields")
        req = {"version": VERSION, "action": action, "target": dict(self._target), **fields}
        data = json.dumps(req, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii") + b"\n"
        if len(data) > MAX_INPUT:
            raise ControllerError("invalid_request", "controller request exceeds 2048 bytes")
        return req, data

    def _reply(self, data, req):
        try:
            reply = _unique_json(data)
        except (ValueError, UnicodeError, RecursionError):
            raise ControllerError("invalid_response", "controller reply is not unique-key UTF-8 JSON") from None
        _require(type(reply) is dict and type(reply.get("version")) is int and reply["version"] == VERSION
                 and type(reply.get("ok")) is bool)
        if not reply["ok"]:
            error = reply.get("error")
            _require(set(reply) == {"version", "ok", "error"} and type(error) is dict
                     and set(error) == {"code", "detail"})
            _require(type(error["code"]) is str and CODE.fullmatch(error["code"]) is not None
                     and type(error["detail"]) is str and 1 <= len(error["detail"]) <= 512)
            raise ControllerError(error["code"], error["detail"], remote_refusal=True)
        action = req["action"]
        _require(reply.get("action") == action)
        extras = {
            "list": {"target", "host_uniqueid", "activity", "snapshot", "topology_digest", "screens",
                     "selection_ready", "runtime", "runtime_reason", "receipt_status"},
            "status": {"target", "host_uniqueid", "activity", "sampling", "runtime", "runtime_reason", "receipt_status"},
            "enroll": set(), "acquire": {"capability", "snapshot"},
            "refresh": {"capability", "snapshot", "topology_digest", "screens"},
            "prepare": {"capability", "display", "snapshot", "topology_digest", "restarted",
                        "launch_authorized", "desktop_app_id", "desktop_title", "app_resolution"},
            "launch": {"capability", "desktop_app_id", "desktop_title", "display", "quit_policy", "app_resolution"},
            "resume": {"capability"}, "observe_active": {"capability"}, "begin_release": {"capability"},
            "finish_release": {"closure_basis"}, "abort": set(), "control_lost": set(),
        }[action]
        if action == "abort" and "sampling" in reply:
            extras = {"sampling"}
        _require(set(reply) == {"version", "ok", "action"} | STATUS_KEYS | extras)
        revision, phase, uncertainty, attempt = (reply[key] for key in
                                                 ("revision", "phase", "uncertainty", "attempt_revision"))
        _require(_integer(revision) and type(phase) is str and phase in PHASES
                 and (uncertainty is None or type(uncertainty) is str and uncertainty in UNCERTAINTIES)
                 and (attempt is None or _integer(attempt, 1, revision)))
        _require((phase == "reconciliation_required") == (uncertainty is not None))
        if phase in {"available", "selecting", "preparing"}:
            _require(attempt is None)
        elif phase != "reconciliation_required":
            _require(attempt is not None)
        if action not in {"list", "status"}:
            expected_phase = {"enroll": "available", "acquire": "selecting", "refresh": "selecting",
                              "prepare": "preparing", "launch": "connecting", "resume": "reconnecting",
                              "observe_active": "active", "begin_release": "releasing",
                              "finish_release": "available", "abort": "available",
                              "control_lost": "reconciliation_required"}[action]
            _require(phase == expected_phase)
            if action == "control_lost":
                _require(req["revision"] <= revision <= req["revision"] + 1)
            else:
                _require(revision == req["revision"] + (2 if action == "prepare" else 1))
            if action in {"launch", "resume"}:
                _require(attempt == revision)
            elif action in {"observe_active", "begin_release"}:
                _require(attempt <= req["revision"])
        if "capability" in extras:
            _require(_hex(reply["capability"]))
            if "capability" in req:
                _require(hmac.compare_digest(reply["capability"], req["capability"]), "controller changed the owner capability")
        for key in extras & {"snapshot", "topology_digest"}:
            _require(_hex(reply[key]))
        if action == "acquire":
            _require(reply["snapshot"] == req["snapshot"], "controller acquisition changed the presented snapshot")
        if action in {"list", "status"}:
            _require(_target_valid(reply["target"]))
            if reply["target"] != self._target:
                raise ControllerError("target_mismatch", "controller returned a different Tailnet target")
            host_id = reply["host_uniqueid"]
            try:
                valid_id = type(host_id) is str and str(uuid.UUID(host_id)) == host_id
            except ValueError:
                valid_id = False
            _require(valid_id, "controller instance identity is not a canonical UUID")
            if self._host_uniqueid is not None and self._host_uniqueid != host_id:
                raise ControllerError("identity_mismatch", "controller Sunshine instance changed from the pinned host")
            _require(reply["activity"] in {"busy", "idle_application"}
                     and reply["runtime"] in {"verified", "unverified"} and _reason(reply["runtime_reason"])
                     and reply["receipt_status"] in {"absent", "stale_definition", "current_candidate", "stale_runtime"})
            _require(reply["runtime"] != "verified" or reply["runtime_reason"] is None)
        if action in {"status", "abort"} and "sampling" in extras:
            _require(reply["sampling"] == "ownership")
        if action == "list":
            _require(type(reply["selection_ready"]) is bool)
            _require(not reply["selection_ready"] or (phase == "available" and reply["activity"] == "idle_application"))
            _screens(reply["screens"], reply["runtime"])
        elif action == "refresh":
            _screens(reply["screens"])
        elif action in {"prepare", "launch"}:
            _screen(reply["display"])
            _require(reply["display"]["capture_candidate"] and _integer(reply["desktop_app_id"], 1, (1 << 31) - 1)
                     and reply["desktop_title"] == "Desktop"
                     and reply["app_resolution"] == "fresh_case_insensitive_title_and_id_match")
            if action == "prepare":
                _require(reply["display"]["id"] == req["display_id"] and type(reply["restarted"]) is bool
                         and reply["launch_authorized"] is True)
            else:
                _require(reply["quit_policy"] == "explicit_quit")
        elif action == "finish_release":
            _require(reply["closure_basis"] == "same_generation_owned_desktop_cancelled")
        # Pin only after the ENTIRE reply is accepted; malformed menus cannot pin.
        if action in {"list", "status"}:
            self._host_uniqueid = reply["host_uniqueid"]
        return reply

    def _argv(self):
        argv = ["ssh", "-T", "-x", "-a", "-o", "ClearAllForwardings=yes"]
        if self._ssh_port is not None:
            argv += ["-p", str(self._ssh_port)]
        return argv + ["--", self._ssh_target, REMOTE_COMMAND]

    @staticmethod
    def _posix_pipes(process, data, deadline):
        sinks = [bytearray(), bytearray()]
        limits = [MAX_OUTPUT, MAX_STDERR]
        sent = 0
        with selectors.DefaultSelector() as selector:
            for stream, index in ((process.stdout, 0), (process.stderr, 1), (process.stdin, 2)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_WRITE if index == 2 else selectors.EVENT_READ, index)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ControllerError("transport_timeout", "controller deadline expired; operation outcome may be unknown")
                for key, _ in selector.select(remaining):
                    index = key.data
                    if index == 2:
                        try:
                            sent += os.write(key.fd, data[sent:])
                        except BlockingIOError:
                            continue
                        if sent == len(data):
                            selector.unregister(key.fileobj)
                            key.fileobj.close()  # Host reads to EOF, not to a newline.
                    else:
                        try:
                            chunk = os.read(key.fd, min(4096, limits[index] - len(sinks[index]) + 1))
                        except BlockingIOError:
                            continue
                        if not chunk:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                        elif len(sinks[index]) + len(chunk) > limits[index]:
                            raise ControllerError("transport_output_limit", "controller stdout or stderr exceeded its bound; outcome may be unknown")
                        else:
                            sinks[index].extend(chunk)
        return bytes(sinks[0]), bytes(sinks[1])

    @staticmethod
    def _threaded_pipes(process, data, deadline, workers):
        # Windows selectors cannot watch subprocess pipes. Three bounded workers
        # use the identical binary-pipe contract; no new transport/auth path.
        sinks = [bytearray(), bytearray()]
        errors = []
        wake = threading.Event()
        def read(stream, index, limit):
            try:
                while True:
                    chunk = stream.read(min(4096, limit - len(sinks[index]) + 1))
                    if not chunk:
                        break
                    if len(sinks[index]) + len(chunk) > limit:
                        errors.append("transport_output_limit")
                        break
                    sinks[index].extend(chunk)
            except (OSError, ValueError):
                errors.append("transport_io")
            finally:
                try:
                    stream.close()
                except OSError:
                    errors.append("transport_io")
                wake.set()
        def write():
            try:
                sent = 0
                while sent < len(data):
                    count = process.stdin.write(data[sent:])
                    if not count:
                        raise OSError("stdin closed")
                    sent += count
            except (OSError, ValueError):
                errors.append("transport_io")
            finally:
                try:
                    process.stdin.close()
                except OSError:
                    errors.append("transport_io")
                wake.set()
        def completed(function, args, done):
            try:
                function(*args)
            finally:
                done.set()
        for function, args in ((read, (process.stdout, 0, MAX_OUTPUT)),
                               (read, (process.stderr, 1, MAX_STDERR)), (write, ())):
            done = threading.Event()
            worker = threading.Thread(target=completed, args=(function, args, done), daemon=True)
            worker._display_complete = done
            workers.append(worker)
            worker.start()
        while any(worker.is_alive() for worker in workers):
            if errors:
                raise ControllerError(errors[0], "controller pipe failed or exceeded its bound; outcome may be unknown")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ControllerError("transport_timeout", "controller deadline expired; operation outcome may be unknown")
            wake.wait(min(remaining, 0.02))
            wake.clear()
        if errors:
            raise ControllerError(errors[0], "controller pipe failed or exceeded its bound; outcome may be unknown")
        return bytes(sinks[0]), bytes(sinks[1])

    def _cleanup(self, process, workers, interruptions, deadline):
        failed = False
        def step(operation):
            nonlocal failed
            # These owned operations are idempotent. One interrupted attempt
            # may have completed its effect; retry once, never retry the RPC.
            for _ in range(2):
                try:
                    return True, operation()
                except KeyboardInterrupt as exc:
                    if not interruptions:
                        interruptions.append(exc)
                except (OSError, ValueError, subprocess.SubprocessError):
                    break
            failed = True
            return False, None

        known, code = step(process.poll)
        if not known or code is None:
            step(process.kill)  # Only this locally owned SSH child.
        reaped, code = step(lambda: process.wait(timeout=min(CLEANUP_TIMEOUT,
                                         max(0, deadline - time.monotonic()))))
        if not reaped or code is None:
            failed = True
        # A failure/interruption above must not skip the remaining resources.
        for stream in (process.stdin, process.stdout, process.stderr):
            step(stream.close)
            known, closed = step(lambda stream=stream: stream.closed)
            if not known or not closed:
                failed = True
        for worker in workers:
            step(lambda worker=worker: worker.join(max(0, deadline - time.monotonic())))
            # Interrupted Thread.join may mark a still-running thread stopped.
            # Its target's own acknowledgement proves no pipe work remains.
            known, finished = step(lambda worker=worker: worker._display_complete.wait(
                                                    max(0, deadline - time.monotonic())))
            if not known or not finished:
                failed = True
        return not failed

    def _run(self, data, timeout):
        deadline = time.monotonic() + timeout
        process, workers = None, []
        interruptions = []  # Preserve the first interruption through cleanup.
        try:
            # Keep the controlling terminal for SSH's own normal auth/host-key
            # interaction. No setsid, BatchMode or permissive key policy.
            process = self._runner(self._argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, shell=False, close_fds=True, bufsize=0)
            stdout, stderr = (self._posix_pipes(process, data, deadline) if os.name == "posix" else
                              self._threaded_pipes(process, data, deadline, workers))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(REMOTE_COMMAND, timeout)
            code = process.wait(timeout=remaining)
            if code != 0:
                detail = _sanitize(stderr.decode("utf-8", errors="replace"))
                raise ControllerError("ssh_failed", f"SSH exited {code}; {detail or 'controller unavailable'}; operation outcome may be unknown")
            return stdout
        except ControllerError:
            raise
        except subprocess.TimeoutExpired:
            raise ControllerError("transport_timeout", "controller deadline expired; operation outcome may be unknown") from None
        except KeyboardInterrupt as exc:
            interruptions.append(exc)
            raise ControllerError("transport_interrupted", "controller call interrupted; operation outcome may be unknown") from None
        except (OSError, ValueError, subprocess.SubprocessError):
            raise ControllerError("transport_io", "system SSH could not deliver the controller request; outcome may be unknown") from None
        finally:
            if process is not None:
                self._broken = True  # Reuse requires a completed disposal proof.
                cleanup_deadline, cleaned = None, False
                # Covers cleanup entry/transition interruptions too. Repeated
                # interruption is an explicit failure, not an unbounded loop.
                for _ in range(2):
                    try:
                        if cleanup_deadline is None:
                            cleanup_deadline = time.monotonic() + 2 * CLEANUP_TIMEOUT
                        cleaned = self._cleanup(process, workers, interruptions, cleanup_deadline)
                        if cleaned:
                            self._broken = False
                        break
                    except KeyboardInterrupt as exc:
                        if not interruptions:
                            interruptions.append(exc)
                        cleaned, self._broken = False, True
                    except (OSError, ValueError, subprocess.SubprocessError):
                        break
                if not cleaned:
                    detail = "interrupted controller cleanup incomplete" if interruptions else "controller cleanup incomplete"
                    raise ControllerError("transport_cleanup", detail + "; transport cannot be reused; operation outcome may be unknown") from None
                if interruptions:
                    raise ControllerError("transport_interrupted", "controller call interrupted; owned local resources cleaned; remote outcome may be unknown") from None

    def call(self, action: str, **fields) -> dict:
        """Return {version: 1, ok: True, action: action, ...}, fully validated."""
        req, data = self._request(action, fields)
        if not self._lock.acquire(blocking=False):
            raise ControllerError("transport_busy", "another controller call is already in progress")
        try:
            if self._broken:
                raise ControllerError("transport_cleanup", "controller transport cannot be reused after failed pipe cleanup")
            if action not in {"list", "status"} and self._host_uniqueid is None:
                raise ControllerError("identity_unpinned", "call list or status before an ownership operation")
            stdout = self._run(data, PREPARE_TIMEOUT if action == "prepare" else CALL_TIMEOUT)
            try:
                return self._reply(stdout, req)
            except (TypeError, ValueError, KeyError, RecursionError):
                # Wrong JSON types (including unhashable values) never escape as
                # raw exceptions or include protocol data in their messages.
                raise ControllerError("invalid_response", "controller reply violates the action schema") from None
        except KeyboardInterrupt:
            # A signal between finalizer statements cannot prove disposal.
            self._broken = True
            raise ControllerError("transport_cleanup", "controller finalization interrupted; transport cannot be reused; outcome may be unknown") from None
        finally:
            try:
                self._lock.release()
            except KeyboardInterrupt:
                self._broken = True
                try:
                    if self._lock.locked():
                        self._lock.release()
                except KeyboardInterrupt:
                    pass
                raise ControllerError("transport_cleanup", "controller lock cleanup interrupted; transport cannot be reused; outcome may be unknown") from None
