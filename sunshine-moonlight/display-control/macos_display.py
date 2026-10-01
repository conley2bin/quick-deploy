"""macOS CoreGraphics active/online display IDs and physical current modes.

Pinned Sunshine platform/macos/display.mm parses output_name as CGDirectDisplayID
(decimal) and captures AVVideo with that same ID. No logical Retina point size
or mode-list maximum is used. CoreGraphics reports 0 Hz for some adaptive modes;
these are visible but not selectable until a current cadence is knowable.
"""
from __future__ import annotations

import ctypes as C
from fractions import Fraction

from protocol import Refusal

U32 = C.c_uint32


def screens_from_coregraphics(displays):
    if len(displays) > 32:
        raise Refusal("backend_unavailable", "more than 32 macOS displays")
    result = []
    for d in displays:
        width, height, num, den = d["mode"] if d["mode"] else (None, None, None, None)
        native_reason = None
        if not d["mode"]:
            native_reason = "current_mode_unavailable"
        elif not 16 <= width <= 16384 or not 16 <= height <= 16384:
            width = height = None
            native_reason = "invalid_current_dimensions"
        elif num is None or den is None or den <= 0 or not 1 <= num / den <= 1000:
            num = den = None
            native_reason = "current_refresh_unknown"
        mapping_reason = None
        if not d["active"]:
            mapping_reason = "inactive"
        elif d["mirror_of"]:
            mapping_reason = "mirrored_display"
        elif native_reason:
            mapping_reason = native_reason
        result.append({"id": f"mac:{d['id']}", "selector": str(d["id"]),
                       "label": f"Display {d['id']}", "primary": d["primary"],
                       "connected": d["online"], "native_reason": native_reason,
                       "mapping_status": "unavailable" if mapping_reason else "mapped",
                       "mapping_reason": mapping_reason,
                       "width": width, "height": height, "refresh_num": num, "refresh_den": den,
                       "rotation": d["rotation"]})
    return result


def enumerate_macos(capture, configured_encoder=""):
    # CoreGraphics mode facts remain useful when the effective capture pipeline
    # is unverified; never pass an explicit unsupported backend off as mapped.
    cg = C.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    cg.CGGetOnlineDisplayList.argtypes = [U32, C.POINTER(U32), C.POINTER(U32)]
    cg.CGGetOnlineDisplayList.restype = C.c_int32
    cg.CGDisplayCopyDisplayMode.argtypes = [U32]
    cg.CGDisplayCopyDisplayMode.restype = C.c_void_p
    cg.CGDisplayModeGetPixelWidth.argtypes = cg.CGDisplayModeGetPixelHeight.argtypes = [C.c_void_p]
    cg.CGDisplayModeGetPixelWidth.restype = cg.CGDisplayModeGetPixelHeight.restype = C.c_size_t
    cg.CGDisplayModeGetRefreshRate.argtypes = [C.c_void_p]
    cg.CGDisplayModeGetRefreshRate.restype = C.c_double
    cg.CGDisplayModeRelease.argtypes = [C.c_void_p]
    for fn in ("CGDisplayIsActive", "CGDisplayIsMain", "CGDisplayMirrorsDisplay"):
        getattr(cg, fn).argtypes = [U32]
        getattr(cg, fn).restype = U32
    cg.CGDisplayRotation.argtypes = [U32]
    cg.CGDisplayRotation.restype = C.c_double
    count = U32()
    if cg.CGGetOnlineDisplayList(0, None, C.byref(count)) or count.value > 32:
        raise Refusal("backend_unavailable", "CoreGraphics online displays unavailable or too numerous")
    ids = (U32 * max(1, count.value))()
    if cg.CGGetOnlineDisplayList(max(1, count.value), ids, C.byref(count)):
        raise Refusal("backend_unavailable", "CoreGraphics display topology changed")
    data = []
    for display_id in ids[:count.value]:
        mode = cg.CGDisplayCopyDisplayMode(display_id)
        current = None
        if mode:
            try:
                width = cg.CGDisplayModeGetPixelWidth(mode)
                height = cg.CGDisplayModeGetPixelHeight(mode)
                hz = cg.CGDisplayModeGetRefreshRate(mode)
                rate = Fraction(str(hz)).limit_denominator(1_000_000) if 1 <= hz <= 1000 else None
                current = (width, height, rate.numerator if rate else None,
                           rate.denominator if rate else None)
            finally:
                cg.CGDisplayModeRelease(mode)
        data.append({"id": int(display_id), "active": bool(cg.CGDisplayIsActive(display_id)),
                     "online": True, "primary": bool(cg.CGDisplayIsMain(display_id)),
                     "mirror_of": int(cg.CGDisplayMirrorsDisplay(display_id)),
                     "rotation": int(round(cg.CGDisplayRotation(display_id))), "mode": current})
    screens = screens_from_coregraphics(data)
    if capture:
        for screen in screens:
            if screen["mapping_status"] == "mapped":
                screen["mapping_status"] = "unverified"
                screen["mapping_reason"] = "capture_backend_unverified"
    return screens
