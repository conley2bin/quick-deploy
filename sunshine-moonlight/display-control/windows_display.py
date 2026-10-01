"""Windows DisplayConfig modes and Sunshine libdisplaydevice capture IDs.

Pinned Sunshine submodule libdisplaydevice 6e9722f: win_api_layer.cpp
getDeviceId hashes EDID + stable UTF-16 instance ID segments (or the UTF-16
monitor path) with Boost name_generator_sha1's zero UUID namespace. The GUID
maps through getDisplayName to DISPLAYCONFIG_SOURCE_DEVICE_NAME, which is the
DXGI output name. A DISPLAY1 ordinal alone is NOT Sunshine's output_name.
"""
from __future__ import annotations

import ctypes as C
import hashlib
import uuid

from protocol import Refusal

U8, U16, U32 = C.c_uint8, C.c_uint16, C.c_uint32


class LUID(C.Structure):
    _fields_ = [("LowPart", U32), ("HighPart", C.c_int32)]


class Rational(C.Structure):
    _fields_ = [("Numerator", U32), ("Denominator", U32)]


class Source(C.Structure):
    _fields_ = [("adapterId", LUID), ("id", U32), ("modeIndex", U32), ("statusFlags", U32)]


class Target(C.Structure):
    _fields_ = [("adapterId", LUID), ("id", U32), ("modeIndex", U32),
                ("outputTechnology", U32), ("rotation", U32), ("scaling", U32),
                ("refreshRate", Rational), ("scanLineOrdering", U32),
                ("targetAvailable", C.c_int32), ("statusFlags", U32)]


class Path(C.Structure):
    _fields_ = [("sourceInfo", Source), ("targetInfo", Target), ("flags", U32)]


class Mode(C.Structure):
    _fields_ = [("infoType", U32), ("id", U32), ("adapterId", LUID), ("data", U8 * 48)]


class Header(C.Structure):
    _fields_ = [("type", U32), ("size", U32), ("adapterId", LUID), ("id", U32)]


class TargetName(C.Structure):
    _fields_ = [("header", Header), ("flags", U32), ("outputTechnology", U32),
                ("edidManufactureId", U16), ("edidProductCodeId", U16),
                ("connectorInstance", U32), ("friendly", C.c_wchar * 64),
                ("devicePath", C.c_wchar * 128)]


class SourceName(C.Structure):
    _fields_ = [("header", Header), ("viewGdiDeviceName", C.c_wchar * 32)]


class GUID(C.Structure):
    _fields_ = [("Data1", U32), ("Data2", U16), ("Data3", U16), ("Data4", U8 * 8)]


class Interface(C.Structure):
    _fields_ = [("cbSize", U32), ("InterfaceClassGuid", GUID), ("Flags", U32), ("Reserved", C.c_void_p)]


class Device(C.Structure):
    _fields_ = [("cbSize", U32), ("ClassGuid", GUID), ("DevInst", U32), ("Reserved", C.c_void_p)]


MONITOR_GUID = GUID(0xe6f07b5f, 0xee97, 0x4a90, (U8 * 8)(0xb0, 0x76, 0x33, 0xf5, 0x7b, 0xf4, 0xea, 0xa7))
QDC_ALL_PATHS = 0x1
QDC_ONLY_ACTIVE_PATHS = 0x2
QDC_VIRTUAL_MODE_AWARE = 0x10


def sunshine_device_id(device_path: str, instance_id: str | None, edid: bytes | None):
    data = b""
    if instance_id is not None and edid:
        first = instance_id.find("&")
        unstable = instance_id.find("&", first + 1) if first >= 0 else -1
        semi = instance_id.find("&", unstable + 1) if unstable >= 0 else -1
        if semi >= 0:
            # Upstream resizes std::wstring to the required WinAPI character
            # count, which includes its terminal NUL. The suffix span hashes
            # that NUL too; Python's .value would otherwise silently drop it.
            data = edid + (instance_id[:unstable] + instance_id[semi:].rstrip("\0") + "\0").encode("utf-16-le")
    if not data:
        data = device_path.encode("utf-16-le")
    # Boost name_generator_sha1 prepends the zero namespace UUID, then sets
    # RFC4122 v5 version and variant bits on the digest's first 16 bytes.
    raw = bytearray(hashlib.sha1(bytes(16) + data).digest()[:16])
    raw[6] = (raw[6] & 15) | 0x50
    raw[8] = (raw[8] & 63) | 0x80
    return "{" + str(uuid.UUID(bytes=bytes(raw))) + "}"


def win_last_error():
    return C.get_last_error()


def read_monitor_edition(path):
    setup = C.WinDLL("setupapi", use_last_error=True)
    registry = C.WinDLL("advapi32", use_last_error=True)
    setup.SetupDiGetClassDevsW.restype = C.c_void_p
    setup.SetupDiGetClassDevsW.argtypes = [C.POINTER(GUID), C.c_void_p, C.c_void_p, U32]
    setup.SetupDiEnumDeviceInterfaces.argtypes = [C.c_void_p, C.c_void_p, C.POINTER(GUID), U32, C.POINTER(Interface)]
    setup.SetupDiGetDeviceInterfaceDetailW.argtypes = [C.c_void_p, C.POINTER(Interface), C.c_void_p,
                                                        U32, C.POINTER(U32), C.POINTER(Device)]
    setup.SetupDiGetDeviceInstanceIdW.argtypes = [C.c_void_p, C.POINTER(Device), C.c_wchar_p, U32, C.POINTER(U32)]
    setup.SetupDiOpenDevRegKey.argtypes = [C.c_void_p, C.POINTER(Device), U32, U32, U32, U32]
    setup.SetupDiOpenDevRegKey.restype = C.c_void_p
    setup.SetupDiDestroyDeviceInfoList.argtypes = [C.c_void_p]
    registry.RegQueryValueExW.argtypes = [C.c_void_p, C.c_wchar_p, C.c_void_p, C.c_void_p,
                                           C.c_void_p, C.POINTER(U32)]
    registry.RegCloseKey.argtypes = [C.c_void_p]
    handle = setup.SetupDiGetClassDevsW(C.byref(MONITOR_GUID), None, None, 0x10)
    if not handle or handle == C.c_void_p(-1).value:
        raise Refusal("identity_unverified", "Windows monitor interface enumeration unavailable")
    incomplete = False
    try:
        for index in range(256):
            interface = Interface()
            interface.cbSize = C.sizeof(interface)
            if not setup.SetupDiEnumDeviceInterfaces(handle, None, C.byref(MONITOR_GUID), index, C.byref(interface)):
                if win_last_error() == 259:  # ERROR_NO_MORE_ITEMS, not any transient API error
                    if incomplete:
                        raise Refusal("identity_unverified", "Windows monitor interface enumeration was interrupted")
                    return None, None, "path_fallback_unverified"
                incomplete = True  # pinned libdisplaydevice continues at the next index
                continue
            needed = U32()
            setup.SetupDiGetDeviceInterfaceDetailW(handle, C.byref(interface), None, 0, C.byref(needed), None)
            if not 8 <= needed.value <= 4096:
                incomplete = True
                continue
            buffer = C.create_string_buffer(needed.value)
            C.cast(buffer, C.POINTER(U32))[0] = 8 if C.sizeof(C.c_void_p) == 8 else 6
            device = Device()
            device.cbSize = C.sizeof(device)
            if not setup.SetupDiGetDeviceInterfaceDetailW(handle, C.byref(interface), buffer, needed,
                                                            None, C.byref(device)):
                incomplete = True
                continue
            try:
                candidate = buffer.raw[4:].decode("utf-16-le").split("\0", 1)[0]
            except UnicodeError as error:
                raise Refusal("identity_unverified", "Windows monitor interface path is malformed") from error
            if candidate.casefold() != path.casefold():
                continue
            chars = U32()
            setup.SetupDiGetDeviceInstanceIdW(handle, C.byref(device), None, 0, C.byref(chars))
            if not 1 <= chars.value <= 4096:
                return None, None, "path_fallback_unverified"
            name = C.create_unicode_buffer(chars.value)
            if not setup.SetupDiGetDeviceInstanceIdW(handle, C.byref(device), name, chars, None):
                return None, None, "path_fallback_unverified"
            # Sunshine's SetupDiOpenDevRegKey/DIREG_DEV then RegQueryValueExW.
            reg = setup.SetupDiOpenDevRegKey(handle, C.byref(device), 1, 0, 1, 0x20019)
            if not reg or reg == C.c_void_p(-1).value:
                return None, None, "path_fallback_unverified"
            try:
                size = U32()
                if registry.RegQueryValueExW(C.c_void_p(reg), "EDID", None, None, None, C.byref(size)) or not 1 <= size.value <= 4096:
                    return None, None, "path_fallback_unverified"
                allocated = size.value
                value = (U8 * allocated)()
                if registry.RegQueryValueExW(C.c_void_p(reg), "EDID", None, None, value, C.byref(size)):
                    return None, None, "path_fallback_unverified"
                if size.value != allocated:
                    # Upstream hashes its initially sized, zero-filled vector;
                    # a changing EDID read is not a verified matching byte set.
                    return None, None, "path_fallback_unverified"
                first = name.value.find("&")
                second = name.value.find("&", first + 1) if first >= 0 else -1
                third = name.value.find("&", second + 1) if second >= 0 else -1
                if third < 0:
                    return None, None, "path_fallback_unverified"
                return name.value, bytes(value[:size.value]), "edid_instance"
            finally:
                registry.RegCloseKey(C.c_void_p(reg))
        raise Refusal("identity_unverified", "Windows monitor interface scan exceeded 256 entries")
    finally:
        setup.SetupDiDestroyDeviceInfoList(C.c_void_p(handle))


def query_paths(all_paths=False):
    user = C.WinDLL("user32", use_last_error=True)
    user.GetDisplayConfigBufferSizes.argtypes = [U32, C.POINTER(U32), C.POINTER(U32)]
    user.QueryDisplayConfig.argtypes = [U32, C.POINTER(U32), C.POINTER(Path), C.POINTER(U32), C.POINTER(Mode), C.c_void_p]
    user.DisplayConfigGetDeviceInfo.argtypes = [C.POINTER(Header)]
    flags = (QDC_ALL_PATHS if all_paths else QDC_ONLY_ACTIVE_PATHS) | QDC_VIRTUAL_MODE_AWARE
    for _ in range(3):
        count, modes = U32(), U32()
        error = user.GetDisplayConfigBufferSizes(flags, C.byref(count), C.byref(modes))
        if error or count.value > 256 or modes.value > 1024:
            raise Refusal("backend_unavailable", "DisplayConfig paths unavailable or too numerous")
        paths_buf, mode_buf = (Path * max(1, count.value))(), (Mode * max(1, modes.value))()
        error = user.QueryDisplayConfig(flags, C.byref(count), paths_buf, C.byref(modes), mode_buf, None)
        if error == 122:  # ERROR_INSUFFICIENT_BUFFER: topology changed
            continue
        if error:
            raise Refusal("backend_unavailable", "QueryDisplayConfig failed")
        return paths_buf[:count.value], mode_buf[:modes.value], user
    raise Refusal("backend_unavailable", "DisplayConfig topology changed during query")


def win_device_name(user, path, target):
    obj = TargetName() if target else SourceName()
    obj.header = Header(2 if target else 1, C.sizeof(obj),
                        path.targetInfo.adapterId if target else path.sourceInfo.adapterId,
                        path.targetInfo.id if target else path.sourceInfo.id)
    if user.DisplayConfigGetDeviceInfo(C.byref(obj.header)):
        raise Refusal("backend_unavailable", "DisplayConfig device identity unavailable")
    return (obj.devicePath, obj.friendly) if target else obj.viewGdiDeviceName


def screens_from_paths(paths, capture="ddx"):
    """One row per available monitor target, never a GDI adapter slot."""
    if len(paths) > 32:
        raise Refusal("backend_unavailable", "more than 32 Windows monitor targets")
    source_counts = {}
    for p in paths:
        if p["active"]:
            source_counts[p["source_key"]] = source_counts.get(p["source_key"], 0) + 1
    result = []
    for p in paths:
        w, h, n, d = p["mode"] if p["mode"] else (None, None, None, None)
        native_reason = None
        if not p["active"]:
            native_reason = "inactive"
        elif not p["mode"]:
            native_reason = "current_mode_unavailable"
        elif not 16 <= w <= 16384 or not 16 <= h <= 16384:
            w = h = None
            native_reason = "invalid_current_dimensions"
        elif not d or not 1 <= n / d <= 1000:
            n = d = None
            native_reason = "current_refresh_unknown"
        provenance = p.get("identity_provenance", "unverified")
        selector = (sunshine_device_id(p["device_path"], p.get("instance"), p.get("edid"))
                    if provenance == "edid_instance" and p["device_path"] else None)
        # A path hash is only a native row ID when acquisition failed; it is
        # never exposed as Sunshine's capture selector in this case.
        row_id = "win:" + (selector or "native:" + sunshine_device_id(p["device_path"], None, None))
        reason = None
        if native_reason:
            mapping, reason = "unavailable", native_reason
        elif source_counts[p["source_key"]] > 1:
            mapping, reason = "unavailable", "mirrored_source"
        elif not p["source_name"] or not selector:
            mapping, reason = "unverified", "capture_identity_" + provenance
        elif capture != "ddx":
            mapping, reason = "unverified", "capture_backend_unverified"
        else:
            mapping = "mapped"  # pinned GUID -> DisplayConfig GDI source -> DXGI name
        result.append({"id": row_id, "selector": selector,
                       "label": p["friendly"] or p["source_name"] or "Windows monitor", "primary": p["primary"],
                       "connected": p["available"], "native_reason": native_reason,
                       "mapping_status": mapping, "mapping_reason": reason,
                       "identity_provenance": provenance, "width": w, "height": h,
                       "refresh_num": n, "refresh_den": d, "rotation": p["rotation"]})
    return result


def inactive_displays(user, paths, active_device_paths):
    """QDC_ALL_PATHS yields targetAvailable monitor paths, unlike adapter enumeration."""
    result, seen, active_confirmed = [], set(), set()
    for p in paths:
        if not p.targetInfo.targetAvailable:
            continue
        path, friendly = win_device_name(user, p, True)
        if not path:
            continue
        key = path.casefold()
        if p.flags & 1:  # target is active in the second, all-path snapshot
            active_confirmed.add(key)
        if key in active_device_paths:
            continue
        if key in seen:
            continue
        seen.add(key)  # multiple source routes can reach one monitor
        result.append({"active": False, "available": True, "device_path": path,
                       "source_name": "", "friendly": friendly, "source_key": None,
                       "primary": False, "mode": None, "rotation": None,
                       "identity_provenance": "inactive"})
    if active_confirmed != active_device_paths:
        raise Refusal("backend_unavailable", "Windows active monitor set changed between queries")
    return result


def enumerate_windows(capture, configured_encoder=""):
    paths, modes, user = query_paths()
    all_paths, _, _ = query_paths(all_paths=True)
    rows, active_device_paths, active_indices = [], set(), {}
    for p in paths:
        if not p.flags & 1 or not p.targetInfo.targetAvailable:
            continue
        device_path, friendly = win_device_name(user, p, True)
        if not device_path:
            raise Refusal("backend_unavailable", "active Windows monitor lacks physical target path")
        path_key = device_path.casefold()
        active_device_paths.add(path_key)
        if path_key in active_indices:
            rows[active_indices[path_key]]["identity_provenance"] = "duplicate_active_target"
            continue
        active_indices[path_key] = len(rows)
        source_name = win_device_name(user, p, False)
        # Pinned libdisplaydevice uses high-16 sourceModeInfoIdx with QDC_VIRTUAL_MODE_AWARE,
        # including observed paths without DISPLAYCONFIG_PATH_SUPPORT_VIRTUAL_MODE.
        index = (p.sourceInfo.modeIndex >> 16) & 0xffff
        mode = None
        if index < len(modes):
            m = modes[index]
            if (m.infoType == 1 and m.id == p.sourceInfo.id and
                    (m.adapterId.LowPart, m.adapterId.HighPart) ==
                    (p.sourceInfo.adapterId.LowPart, p.sourceInfo.adapterId.HighPart)):
                width, height = U32.from_buffer(m.data, 0).value, U32.from_buffer(m.data, 4).value
                x, y = C.c_int32.from_buffer(m.data, 12).value, C.c_int32.from_buffer(m.data, 16).value
                mode = (width, height, p.targetInfo.refreshRate.Numerator, p.targetInfo.refreshRate.Denominator)
        if mode is None:
            x = y = None
        try:
            instance, edid, provenance = read_monitor_edition(device_path)
        except Refusal as error:
            if error.code != "identity_unverified":
                raise
            instance, edid, provenance = None, None, "lookup_interrupted"
        rows.append({"active": True, "available": True,
                     "device_path": device_path, "source_name": source_name, "friendly": friendly,
                     "source_key": (p.sourceInfo.adapterId.LowPart, p.sourceInfo.adapterId.HighPart, p.sourceInfo.id),
                     "primary": x == y == 0 if x is not None else False,
                     "mode": mode, "rotation": {1: 0, 2: 90, 3: 180, 4: 270}.get(p.targetInfo.rotation, -1),
                     "instance": instance, "edid": edid, "identity_provenance": provenance})
    rows.extend(inactive_displays(user, all_paths, active_device_paths))
    return screens_from_paths(rows, capture)
