#!/usr/bin/env python3
"""Installed fixed-stdin display controller. Linux lifecycle, native inventory.

No commands/paths/evidence are accepted from JSON. Installation supplies one
private settings file and state directory. Other OS runtime integrations and
the supervised Moonlight client are separate construction milestones.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from lifecycle import (Endpoint, HostController, InstalledSettings, canonical_uuid,
                       read_owned)
from protocol import MAX_INPUT, Refusal, request, response, unique_json
import runtime_linux as rt

MANIFEST = Path(__file__).resolve().with_name("host-settings.json")


def settings(path=MANIFEST, platform=None):
    if (platform or sys.platform) != "linux":
        raise Refusal("platform_runtime_unavailable", "owned lifecycle currently requires the Linux runtime adapter")
    data, identity = read_owned(path, 8192)
    if identity.st_mode & 0o077:
        raise Refusal("runtime_unconfigured", "installed controller settings must be owner-private")
    try:
        document = unique_json(data)
        keys = {"schema", "platform", "config_file", "apps_file", "assets_directory",
                "host_uniqueid", "base_port", "state_directory", "runtime_home", "runtime_uid",
                "unit", "build", "retry_guard_sha256"}
        if (type(document) is not dict or set(document) != keys or type(document["schema"]) is not int
                or document["schema"] != 2 or document["platform"] != "linux"):
            raise ValueError("schema")
        host_id = canonical_uuid(document["host_uniqueid"])
        port, uid = document["base_port"], document["runtime_uid"]
        if type(port) is not int or not 1029 <= port <= 65514 or type(uid) is not int or uid != os.geteuid():
            raise ValueError("service identity")
        paths = {}
        for key in ("config_file", "apps_file", "assets_directory", "state_directory", "runtime_home"):
            value = document[key]
            if (type(value) is not str or not 1 <= len(value) <= 1024 or not Path(value).is_absolute()
                    or any(ord(c) < 32 for c in value) or ".." in Path(value).parts):
                raise ValueError("path")
            paths[key] = Path(value)
        build = document["build"]
        if (type(build) is not dict or set(build) != {"executable_sha256", "source_commit", "provenance"}
                or build["source_commit"] != rt.SOURCE_COMMIT or type(build["executable_sha256"]) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", build["executable_sha256"])):
            raise ValueError("build")
        provenance = build["provenance"]
        if (type(provenance) is not dict or set(provenance) != {"kind", "reference", "artifact_sha256"}
                or provenance["kind"] not in ("verified_vendor_package", "verified_exact_source_build")
                or type(provenance["reference"]) is not str or not 1 <= len(provenance["reference"]) <= 256
                or any(ord(c) < 32 for c in provenance["reference"])
                or type(provenance["artifact_sha256"]) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", provenance["artifact_sha256"])):
            raise ValueError("qualification")
        # Provenance is a TRUSTED installer qualification, not a hash of whatever
        # bytes happened to be installed. This controller cannot certify a package.
        unit, guard = document["unit"], document["retry_guard_sha256"]
        if unit not in (rt.UNIT, rt.ALIAS) or (guard is not None and
                (type(guard) is not str or not re.fullmatch(r"[0-9a-f]{64}", guard))):
            raise ValueError("owned unit/guard")
        runtime = rt.Settings(host_id, uid, paths["runtime_home"], paths["config_file"],
                              rt.SupportedBuild(build["executable_sha256"], build["source_commit"]), unit, guard)
        return InstalledSettings(host_id, port, paths["state_directory"], paths["apps_file"],
                                 paths["assets_directory"], runtime, provenance)
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError) as exc:
        raise Refusal("runtime_unconfigured", "installed lifecycle settings are invalid or lack qualified build provenance") from exc


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Refusal("endpoint_unknown", "Sunshine endpoint redirected")


def serverinfo(address, port):
    # Pinned Sunshine PORT_HTTP=0; PORT_HTTPS=-5 is TLS. No keys/admin auth/proxies.
    url = f"http://{address}:{port}/serverinfo"
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(url, timeout=2) as stream:
            data = stream.read(4097)
        if len(data) > 4096 or b"<!DOCTYPE" in data or b"<!ENTITY" in data:
            raise ValueError("oversized/invalid serverinfo")
        root = ET.fromstring(data)
        if root.tag != "root" or root.attrib.get("status_code") != "200":
            raise ValueError("invalid serverinfo status")
        if any(len(root.findall(name)) != 1 for name in ("uniqueid", "state", "currentgame")):
            raise ValueError("missing or duplicate facts")
        host_id = canonical_uuid(root.findtext("uniqueid"))
        raw_state, current = root.findtext("state", ""), root.findtext("currentgame", "")
        if (raw_state not in ("SUNSHINE_SERVER_FREE", "SUNSHINE_SERVER_BUSY")
                or not re.fullmatch(r"[0-9]{1,10}", current) or int(current) >= (1 << 31)
                or (raw_state == "SUNSHINE_SERVER_FREE" and int(current) != 0)):
            raise ValueError("invalid application state")
        return Endpoint(host_id, "FREE" if raw_state == "SUNSHINE_SERVER_FREE" else "BUSY", int(current))
    except (OSError, urllib.error.URLError, ET.ParseError, ValueError) as exc:
        raise Refusal("endpoint_unknown", "target Sunshine serverinfo unavailable or invalid") from exc


def collect(platform, capture, configured_encoder="", *, device_type=None):
    if platform == "linux":
        from linux_kms import enumerate_kms
        return enumerate_kms(capture, configured_encoder, device_type=device_type)
    if platform == "win32":
        from windows_display import enumerate_windows
        return enumerate_windows(capture, configured_encoder)
    if platform == "darwin":
        from macos_display import enumerate_macos
        return enumerate_macos(capture, configured_encoder)
    raise Refusal("platform_unsupported", "unsupported native display OS")


def presentation(native, cfg_digest, host_id, *, runtime_capture="unverified"):
    if len(native) > 32:
        raise Refusal("backend_unavailable", "more than 32 displays")
    items, seen = [], set()
    for screen in native:
        ident = screen["id"]
        if (type(ident) is not str or not 1 <= len(ident) <= 100 or ident in seen
                or any(ord(c) < 32 or ord(c) == 127 for c in ident)):
            raise Refusal("backend_unavailable", "ambiguous native display identity")
        seen.add(ident)
        label = "".join(c for c in screen["label"] if not unicodedata.category(c).startswith("C"))[:64]
        selector = screen["selector"]
        if selector is not None and (type(selector) is not str or not 1 <= len(selector) <= 80
                                     or any(ord(c) < 32 or ord(c) == 127 for c in selector)):
            raise Refusal("backend_unavailable", "invalid native capture selector")
        mapping = screen["mapping_status"]
        if mapping not in ("mapped", "filtered", "unverified", "unavailable"):
            raise Refusal("backend_unavailable", "invalid native mapping status")
        numerator, denominator = screen["refresh_num"], screen["refresh_den"]
        fps = ((2 * numerator + denominator) // (2 * denominator)
               if type(numerator) is int and type(denominator) is int and numerator > 0 and denominator > 0 else None)
        if fps is not None and not 1 <= fps <= 240:
            fps = None
        mode_valid = (type(screen["width"]) is int and 16 <= screen["width"] <= 16384
                      and type(screen["height"]) is int and 16 <= screen["height"] <= 16384 and fps is not None)
        candidate = (screen["connected"] is True and selector is not None and mode_valid
                     and screen["native_reason"] is None and
                     (mapping == "mapped" or (mapping == "unverified" and
                       screen["mapping_reason"] in {"effective_encoder_device_unknown", "encoder_filter_required"})))
        reason = ("preparation_required" if candidate else screen["native_reason"] or
                  screen["mapping_reason"] or "refresh_unsupported")
        items.append({"id": ident, "label": label, "primary": bool(screen["primary"]),
                      "connected": bool(screen["connected"]), "selector": selector,
                      "selector_mapping": mapping, "selector_candidate": mapping == "mapped" and candidate,
                      "mapping_reason": screen["mapping_reason"], "native_reason": screen["native_reason"],
                      "runtime_capture": runtime_capture,
                      "capture_candidate": candidate and mapping == "mapped" and runtime_capture == "verified",
                      "selectable": candidate, "selection_intent": "prepare" if candidate else None,
                      "reason": reason, "width": screen["width"], "height": screen["height"],
                      "refresh_num": numerator, "refresh_den": denominator, "requested_fps": fps,
                      "rotation": screen["rotation"]})
    items.sort(key=lambda item: item["id"])
    serial = json.dumps({"screens": items, "config_digest": cfg_digest, "host_uniqueid": host_id},
                        sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return items, hashlib.sha256(serial).hexdigest()


def handle(raw, *, manifest=MANIFEST, platform=None, enumerate_native=collect, info=serverinfo,
           runtime=None, authorize_enrollment=None, sleep=None, readiness_attempts=90):
    req = request(raw)
    installed = settings(manifest, platform)
    runtime = runtime or rt.LinuxRuntime(installed.runtime, rt.LocalTransport())
    options = {"authorize_enrollment": authorize_enrollment, "readiness_attempts": readiness_attempts}
    if sleep is not None:
        options["sleep"] = sleep
    controller = HostController(installed, runtime, enumerate_native, info, presentation, **options)
    result = controller.handle(req)
    return response(True, action=req["action"], **result)


def interactive_enrollment():
    # An operator ceremony, not an RPC flag. A later installer/shim must resolve
    # the host's interactive TTY deliberately. Never echo prompts on JSON stdout.
    try:
        with open("/dev/tty", "r+", encoding="utf-8") as tty:
            tty.write("Confirm exclusive controller use, no existing/in-flight unmanaged stream, and no concurrent admin edits. Type ENROLL: ")
            tty.flush()
            return tty.readline(32).strip() == "ENROLL"
    except OSError:
        return False


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        result = handle(raw, authorize_enrollment=interactive_enrollment)
        sys.stdout.buffer.write(result)
    except Refusal as exc:
        sys.stdout.buffer.write(response(False, error={"code": exc.code, "detail": exc.detail}))
    except Exception:
        # No traceback/path/config/capability/credentials on the protected stream.
        sys.stdout.buffer.write(response(False, error={"code": "internal_error", "detail": "host operation failed; ownership retained"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
