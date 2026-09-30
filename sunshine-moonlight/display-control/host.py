#!/usr/bin/env python3
"""Read-only version-1 Sunshine display inventory over fixed JSON stdin/stdout.

Installation owns an adjacent host-settings.json (never supplied by the remote
request). No prepare/restart is enabled. The approved single-launch-authority
reservation and lifecycle verification belong to a later milestone.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from configuration import digest, get_settings, load
from protocol import MAX_INPUT, Refusal, request, response

MANIFEST = Path(__file__).resolve().with_name("host-settings.json")
PLATFORM = {"linux": "linux_kms", "win32": "windows_display", "darwin": "macos_display"}


def settings(path=MANIFEST, platform=None):
    if platform is None:
        platform = sys.platform
    expected = {"linux": "linux", "win32": "windows", "darwin": "macos"}.get(platform)
    if not expected:
        raise Refusal("platform_unsupported", "only Linux, Windows and macOS are implemented")
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
            raise Refusal("runtime_unconfigured", "owned settings must be a bounded regular file")
        if os.name != "nt" and info.st_uid != os.geteuid():
            raise Refusal("runtime_unconfigured", "settings ownership differs from controller user")
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise Refusal("runtime_unconfigured", "installed controller settings missing or invalid") from exc
    if type(document) is not dict or set(document) != {"schema", "platform", "config_file", "host_uniqueid"}:
        raise Refusal("runtime_unconfigured", "settings schema is not version 1")
    if type(document["schema"]) is not int or document["schema"] != 1 or document["platform"] != expected:
        raise Refusal("runtime_unconfigured", "installed platform/schema differs")
    file = document["config_file"]
    identity = document["host_uniqueid"]
    if (type(file) is not str or not Path(file).is_absolute() or "\x00" in file or
            type(identity) is not str or not re.fullmatch(r"[A-Za-z0-9-]{8,128}", identity)):
        raise Refusal("runtime_unconfigured", "settings config path or pinned Sunshine ID invalid")
    return Path(file), identity


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Refusal("endpoint_unknown", "Sunshine endpoint redirected")


def serverinfo(address, port):
    # This GET has no Sunshine administrator credentials. A proxy would permit
    # a request elsewhere; disable ambient HTTP proxies and all redirects.
    # Pinned Sunshine PORT_HTTP=0; PORT_HTTPS=-5 is TLS, not plaintext HTTP.
    url = f"http://{address}:{port}/serverinfo"
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(url, timeout=2) as stream:
            data = stream.read(4097)
        if len(data) > 4096:
            raise ValueError("oversized serverinfo")
        root = ET.fromstring(data)
        if root.tag != "root" or root.attrib.get("status_code") != "200":
            raise ValueError("invalid serverinfo status")
        host_id = root.findtext("uniqueid", "")
        raw_state = root.findtext("state", "")
        current = root.findtext("currentgame", "")
        if not re.fullmatch(r"[A-Za-z0-9-]{8,128}", host_id) or raw_state not in (
                "SUNSHINE_SERVER_FREE", "SUNSHINE_SERVER_BUSY") or not re.fullmatch(r"[0-9]+", current):
            raise ValueError("missing Sunshine identity/state")
        # proc.running() can overapproximate busy and has no session count.
        return host_id, "busy" if raw_state == "SUNSHINE_SERVER_BUSY" or int(current) else "unknown"
    except (OSError, urllib.error.URLError, ET.ParseError, ValueError) as exc:
        raise Refusal("endpoint_unknown", "target Sunshine serverinfo unavailable or invalid") from exc


def collect(platform, capture, configured_encoder=""):
    if platform == "linux":
        from linux_kms import enumerate_kms
        return enumerate_kms(capture, configured_encoder)
    if platform == "win32":
        from windows_display import enumerate_windows
        return enumerate_windows(capture, configured_encoder)
    if platform == "darwin":
        from macos_display import enumerate_macos
        return enumerate_macos(capture, configured_encoder)
    raise Refusal("platform_unsupported", "unsupported host OS")


def presentation(native, cfg_digest, host_id):
    if len(native) > 32:
        raise Refusal("backend_unavailable", "more than 32 displays")
    items = []
    seen = set()
    for screen in native:
        ident = screen["id"]
        if type(ident) is not str or not 1 <= len(ident) <= 100 or ident in seen or any(ord(c) < 32 or ord(c) == 127 for c in ident):
            raise Refusal("backend_unavailable", "ambiguous native display identity")
        seen.add(ident)
        label = "".join(c if 32 <= ord(c) < 127 else "?" for c in screen["label"])[:64]
        selector = screen["selector"]
        if selector is not None and (type(selector) is not str or not 1 <= len(selector) <= 80 or
                                     any(ord(c) < 32 or ord(c) == 127 for c in selector)):
            raise Refusal("backend_unavailable", "invalid native capture selector")
        mapping = screen["mapping_status"]
        if mapping not in ("mapped", "filtered", "unverified", "unavailable"):
            raise Refusal("backend_unavailable", "invalid native mapping status")
        numerator, denominator = screen["refresh_num"], screen["refresh_den"]
        fps = (2 * numerator + denominator) // (2 * denominator) if numerator and denominator else None
        if fps is not None and not 1 <= fps <= 240:
            fps = None
        selector_candidate = mapping == "mapped" and screen["native_reason"] is None and fps is not None
        reason = screen["mapping_reason"] or screen["native_reason"] or (
            "refresh_unsupported" if fps is None else "runtime_capture_unverified")
        items.append({"id": ident, "label": label, "primary": bool(screen["primary"]),
                      "connected": bool(screen["connected"]), "selector": selector,
                      "selector_mapping": mapping, "selector_candidate": selector_candidate,
                      "mapping_reason": screen["mapping_reason"],
                      "native_reason": screen["native_reason"], "runtime_capture": "unverified",
                      "capture_candidate": False, "selectable": False, "reason": reason,
                      "width": screen["width"], "height": screen["height"],
                      "refresh_num": numerator, "refresh_den": denominator,
                      "requested_fps": fps, "rotation": screen["rotation"]})
    items.sort(key=lambda item: item["id"])
    serial = json.dumps({"screens": items, "config_digest": cfg_digest, "host_uniqueid": host_id},
                        sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return items, hashlib.sha256(serial).hexdigest()


def handle(raw, *, manifest=MANIFEST, platform=None, enumerate_native=collect, info=serverinfo):
    req = request(raw)
    if req["action"] == "prepare":
        raise Refusal("reservation_unavailable", "host switching awaits single-authority reservation and verified lifecycle ownership")
    effective = platform or sys.platform
    path, pinned = settings(manifest, effective)
    content, _ = load(path)
    cfg = get_settings(content)
    target = req["target"]
    if cfg["address_family"] != "ipv4" or cfg["bind_address"] != target["address"] or cfg["port"] != target["port"]:
        raise Refusal("target_mismatch", "configured Sunshine bind address/base port differs from streaming target")
    if ipaddress.IPv4Address(cfg["bind_address"]) not in ipaddress.IPv4Network("100.64.0.0/10"):
        raise Refusal("target_mismatch", "configured address is not Tailnet")
    host_id, activity = info(target["address"], target["port"])
    if host_id != pinned:
        raise Refusal("target_mismatch", "Sunshine server uniqueid differs from pinned controller host identity")
    native = enumerate_native(effective, cfg["capture"], cfg["encoder"])
    screens, snapshot = presentation(native, digest(content), host_id)
    return response(True, action="list", target=target, host_uniqueid=host_id,
                    activity=activity, snapshot=snapshot, screens=screens,
                    selection_ready=False, runtime="lifecycle_unverified",
                    configured_capture=cfg["capture"] or "automatic",
                    configured_encoder=cfg["encoder"] or "automatic")


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        result = handle(raw)
        sys.stdout.buffer.write(result)
    except Refusal as exc:
        sys.stdout.buffer.write(response(False, error={"code": exc.code, "detail": exc.detail}))
    except Exception:
        # No traceback/path/config/credentials on the SSH protocol stream.
        sys.stdout.buffer.write(response(False, error={"code": "internal_error", "detail": "host inventory failed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
