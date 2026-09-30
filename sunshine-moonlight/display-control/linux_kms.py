"""Linux DRM/KMS read-only current scanout. Mirrors Sunshine kmsgrab's connector-name mapping.

Sunshine pinned 63d35f7: kmsgrab.cpp kms_display_names counts non-cursor planes
with fb_id, associates CRTC with a connector, then accepts TYPE-index names.
A duplicate connector name, clone CRTC or multiple active planes is deliberately
ineligible: upstream's index/name fallback can otherwise capture another output.
"""
from __future__ import annotations

import ctypes as C
import ctypes.util
from fractions import Fraction
import os
from pathlib import Path
import re

from protocol import Refusal

U32 = C.c_uint32
U64 = C.c_uint64


class Mode(C.Structure):
    _fields_ = [("clock", U32), ("hdisplay", C.c_uint16), ("hsync_start", C.c_uint16),
                ("hsync_end", C.c_uint16), ("htotal", C.c_uint16), ("hskew", C.c_uint16),
                ("vdisplay", C.c_uint16), ("vsync_start", C.c_uint16), ("vsync_end", C.c_uint16),
                ("vtotal", C.c_uint16), ("vscan", C.c_uint16), ("vrefresh", U32),
                ("flags", U32), ("type", U32), ("name", C.c_char * 32)]


class Resources(C.Structure):
    _fields_ = [("count_fbs", C.c_int), ("fbs", C.POINTER(U32)),
                ("count_crtcs", C.c_int), ("crtcs", C.POINTER(U32)),
                ("count_connectors", C.c_int), ("connectors", C.POINTER(U32)),
                ("count_encoders", C.c_int), ("encoders", C.POINTER(U32)),
                ("min_width", U32), ("max_width", U32), ("min_height", U32), ("max_height", U32)]


class Connector(C.Structure):
    _fields_ = [("connector_id", U32), ("encoder_id", U32), ("connector_type", U32),
                ("connector_type_id", U32), ("connection", C.c_int), ("mmWidth", U32),
                ("mmHeight", U32), ("subpixel", C.c_int), ("count_modes", C.c_int),
                ("modes", C.POINTER(Mode)), ("count_props", C.c_int),
                ("props", C.POINTER(U32)), ("prop_values", C.POINTER(U64)),
                ("count_encoders", C.c_int), ("encoders", C.POINTER(U32))]


class Encoder(C.Structure):
    _fields_ = [("encoder_id", U32), ("encoder_type", U32), ("crtc_id", U32),
                ("possible_crtcs", U32), ("possible_clones", U32)]


class Crtc(C.Structure):
    _fields_ = [("crtc_id", U32), ("buffer_id", U32), ("x", U32), ("y", U32),
                ("width", U32), ("height", U32), ("mode_valid", C.c_int),
                ("mode", Mode), ("gamma_size", C.c_int)]


class DrmVersion(C.Structure):
    _fields_ = [("major", C.c_int), ("minor", C.c_int), ("patch", C.c_int),
                ("name_len", C.c_int), ("name", C.c_char_p),
                ("date_len", C.c_int), ("date", C.c_char_p),
                ("desc_len", C.c_int), ("desc", C.c_char_p)]


class PlaneResources(C.Structure):
    _fields_ = [("count_planes", U32), ("planes", C.POINTER(U32))]


class Plane(C.Structure):
    _fields_ = [("count_formats", U32), ("formats", C.POINTER(U32)), ("plane_id", U32),
                ("crtc_id", U32), ("fb_id", U32), ("crtc_x", U32), ("crtc_y", U32),
                ("x", U32), ("y", U32), ("possible_crtcs", U32), ("gamma_size", U32)]


class ObjectProperties(C.Structure):
    _fields_ = [("count_props", U32), ("props", C.POINTER(U32)), ("prop_values", C.POINTER(U64))]


class Property(C.Structure):
    _fields_ = [("prop_id", U32), ("flags", U32), ("name", C.c_char * 32),
                ("count_values", C.c_int), ("values", C.POINTER(U64)),
                ("count_enums", C.c_int), ("enums", C.c_void_p),
                ("count_blobs", C.c_int), ("blob_ids", C.POINTER(U32))]


def library():
    name = ctypes.util.find_library("drm")
    if not name:
        raise Refusal("backend_unavailable", "Linux KMS requires libdrm")
    lib = C.CDLL(name, use_errno=True)
    for method, result, args in (
        ("drmSetClientCap", C.c_int, [C.c_int, U64, U64]),
        ("drmModeGetResources", C.POINTER(Resources), [C.c_int]),
        ("drmModeGetConnector", C.POINTER(Connector), [C.c_int, U32]),
        ("drmModeGetEncoder", C.POINTER(Encoder), [C.c_int, U32]),
        ("drmModeGetCrtc", C.POINTER(Crtc), [C.c_int, U32]),
        ("drmModeGetPlaneResources", C.POINTER(PlaneResources), [C.c_int]),
        ("drmModeGetPlane", C.POINTER(Plane), [C.c_int, U32]),
        ("drmModeObjectGetProperties", C.POINTER(ObjectProperties), [C.c_int, U32, U32]),
        ("drmModeGetProperty", C.POINTER(Property), [C.c_int, U32]),
        ("drmModeGetConnectorTypeName", C.c_char_p, [U32]),
        ("drmGetVersion", C.POINTER(DrmVersion), [C.c_int]),
    ):
        func = getattr(lib, method)
        func.restype, func.argtypes = result, args
    for method, arg in (("drmModeFreeResources", Resources), ("drmModeFreeConnector", Connector),
                        ("drmModeFreeEncoder", Encoder), ("drmModeFreeCrtc", Crtc),
                        ("drmModeFreePlaneResources", PlaneResources), ("drmModeFreePlane", Plane),
                        ("drmModeFreeObjectProperties", ObjectProperties), ("drmModeFreeProperty", Property)):
        getattr(lib, method).argtypes = [C.POINTER(arg)]
    lib.drmFreeVersion.argtypes = [C.POINTER(DrmVersion)]
    return lib


def managed(lib, getter, freer, *args):
    ptr = getattr(lib, getter)(*args)
    if not ptr:
        raise Refusal("backend_unavailable", f"DRM {getter} returned no data")
    try:
        yield ptr.contents
    finally:
        getattr(lib, freer)(ptr)


from contextlib import contextmanager
managed = contextmanager(managed)


def props(lib, fd, plane_id):
    # DRM_MODE_OBJECT_PLANE = 0xeeeeeeee
    with managed(lib, "drmModeObjectGetProperties", "drmModeFreeObjectProperties", fd, plane_id, 0xeeeeeeee) as entries:
        if entries.count_props > 256:
            raise Refusal("backend_unavailable", "too many DRM properties")
        result = {}
        for i in range(entries.count_props):
            with managed(lib, "drmModeGetProperty", "drmModeFreeProperty", fd, entries.props[i]) as prop:
                result[prop.name.decode("ascii", "replace")] = int(entries.prop_values[i])
        return result


def nominal_rate(mode):
    # DRM clock is kHz; interlace/doublescan/vscan affect field cadence.
    if not mode.clock or not mode.htotal or not mode.vtotal:
        return (0, 1)
    rate = Fraction(mode.clock * 1000, mode.htotal * mode.vtotal)
    if mode.flags & (1 << 4):
        rate *= 2
    if mode.flags & (1 << 5):
        rate /= 2
    if mode.vscan > 1:
        rate /= mode.vscan
    return (rate.numerator, rate.denominator)


def read_card(lib, card):
    fd = os.open(card, os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
    try:
        if lib.drmSetClientCap(fd, 2, 1):
            raise Refusal("backend_unavailable", "DRM universal planes unavailable")
        version = lib.drmGetVersion(fd)
        try:
            nvidia = version.contents.name[:version.contents.name_len].startswith(b"nvidia-drm") if version and version.contents.name else None
        finally:
            if version:
                lib.drmFreeVersion(version)
        with managed(lib, "drmModeGetResources", "drmModeFreeResources", fd) as resources:
            if resources.count_connectors > 64 or resources.count_connectors < 0:
                raise Refusal("backend_unavailable", "invalid DRM connector count")
            connectors = []
            for i in range(resources.count_connectors):
                with managed(lib, "drmModeGetConnector", "drmModeFreeConnector", fd, resources.connectors[i]) as conn:
                    name = lib.drmModeGetConnectorTypeName(conn.connector_type)
                    if not name:
                        raise Refusal("backend_unavailable", "unknown DRM connector type")
                    # Sunshine retains inactive connectors in card_descriptors
                    # while mapping output_name. Keep them for alias analysis;
                    # screens_from_kms hides unused slots from the UI.
                    crtc_id = 0
                    if conn.encoder_id:
                        with managed(lib, "drmModeGetEncoder", "drmModeFreeEncoder", fd, conn.encoder_id) as enc:
                            crtc_id = int(enc.crtc_id)
                    item = {"card": card.name, "connector": f"{name.decode('ascii')}-{conn.connector_type_id}",
                            "crtc": crtc_id, "connected": conn.connection == 1, "mode": None,
                            "planes": [], "rotation": 0, "gpu_nvidia": nvidia}
                    if crtc_id:
                        with managed(lib, "drmModeGetCrtc", "drmModeFreeCrtc", fd, crtc_id) as crtc:
                            if crtc.mode_valid:
                                num, den = nominal_rate(crtc.mode)
                                item["mode"] = (int(crtc.width), int(crtc.height), num, den)
                    connectors.append(item)
            with managed(lib, "drmModeGetPlaneResources", "drmModeFreePlaneResources", fd) as planes:
                if planes.count_planes > 256:
                    raise Refusal("backend_unavailable", "too many DRM planes")
                for i in range(planes.count_planes):
                    with managed(lib, "drmModeGetPlane", "drmModeFreePlane", fd, planes.planes[i]) as plane:
                        if not plane.fb_id or not plane.crtc_id:
                            continue
                        p = props(lib, fd, plane.plane_id)
                        if p.get("type") == 2:
                            continue
                        # fb_id!=0 means an active scanout. GETFB/PRIME would test
                        # this unprivileged Python process, not Sunshine's separate
                        # privileged worker; never export framebuffer handles here.
                        for conn in connectors:
                            if conn["crtc"] == plane.crtc_id:
                                conn["planes"].append(int(plane.plane_id))
                                # Sunshine only supports the 270-degree orientation; the
                                # other rotated KMS modes must not advertise correct capture.
                                rotation = p.get("rotation", 1)
                                conn["rotation"] = {1: 0, 2: 90, 4: 180, 8: 270}.get(rotation, -1)
            return connectors
    finally:
        os.close(fd)


def card_in_capture_list(connector, device_type, configured_encoder):
    """Pinned kms_display_names card filtering; None means driver/type unknown."""
    nvidia = connector.get("gpu_nvidia")
    if device_type in (None, "system"):
        return True
    if device_type == "vaapi":
        return not nvidia if nvidia is not None else None
    if device_type == "cuda":
        return True if configured_encoder == "nvenc" else nvidia
    return None


def screens_from_kms(connectors, capture, *, device_type=None, configured_encoder=""):
    """Native facts and source-derived selector membership, not service attestation.

    device_type is Sunshine's *effective* mem_type_e (cuda/vaapi/system), not
    a guess from encoder= in an unverified config. Production list does not
    have that runtime evidence yet, so it passes None and refuses membership.
    """
    connected = [c for c in connectors if c["connected"]]
    if len(connected) > 32:
        raise Refusal("backend_unavailable", "more than 32 connected DRM outputs")
    # Sunshine's mapping walks every connector descriptor on each card that
    # survived its encoder filter, including disconnected ports with CRTC=0.
    # Keep unknown-vendor cards too: they may collide and must not be ignored.
    names = [c["connector"] for c in connectors
             if card_in_capture_list(c, device_type, configured_encoder) is not False]
    result = []
    for c in connected:
        ident = f"kms:{c['card']}:{c['connector']}"
        mode = c["mode"]
        width, height, num, den = mode if mode else (None, None, None, None)
        native_reason = None
        if not mode or not c["planes"]:
            native_reason = "no_active_scanout"
        elif not 16 <= width <= 16384 or not 16 <= height <= 16384:
            native_reason = "invalid_current_dimensions"
            width = height = None
        elif c["rotation"] not in (0, 270):
            native_reason = "unsupported_kms_rotation"
        if width is not None and c["rotation"] == 270:
            width, height = height, width  # Pinned kmsgrab swaps 270° viewport axes.
        if num is not None and (den <= 0 or not 1 <= num / den <= 1000):
            num = den = None
            native_reason = native_reason or "current_refresh_unknown"

        mapping_reason = None
        if native_reason:
            mapping = "unavailable"
            mapping_reason = native_reason
        elif capture != "kms":
            mapping = "unverified"
            mapping_reason = "capture_backend_unverified"
        elif card_in_capture_list(c, device_type, configured_encoder) is False:
            mapping = "filtered"
            mapping_reason = ("vaapi_skips_nvidia_card" if device_type == "vaapi"
                              else "cuda_skips_non_nvidia_card")
        elif names.count(c["connector"]) != 1:
            mapping = "unavailable"
            mapping_reason = "ambiguous_capture_name"
        elif sum(1 for other in connected if other["crtc"] and other["crtc"] == c["crtc"] and other["card"] == c["card"]) != 1:
            mapping = "unavailable"
            mapping_reason = "mirrored_crtc"
        elif len(c["planes"]) != 1:
            mapping = "unavailable"
            mapping_reason = "ambiguous_scanout_planes"
        elif device_type is None:
            mapping = "unverified"
            mapping_reason = "effective_encoder_device_unknown"
        elif device_type not in ("cuda", "vaapi", "system"):
            mapping = "unverified"
            mapping_reason = "effective_encoder_device_unsupported"
        elif card_in_capture_list(c, device_type, configured_encoder) is None:
            mapping = "unverified"
            mapping_reason = "gpu_driver_unknown"
        else:
            mapping = "mapped"
        result.append({"id": ident, "label": c["connector"], "selector": c["connector"],
                       "primary": False, "connected": True, "native_reason": native_reason,
                       "mapping_status": mapping, "mapping_reason": mapping_reason,
                       "width": width, "height": height, "refresh_num": num,
                       "refresh_den": den, "rotation": c["rotation"]})
    return result


def enumerate_kms(capture, configured_encoder=""):
    cards = sorted(p for p in Path("/dev/dri").glob("card[0-9]*") if re.fullmatch(r"card[0-9]+", p.name))
    if not cards:
        raise Refusal("backend_unavailable", "no /dev/dri/cardN KMS devices")
    lib = library()
    connectors = []
    for card in cards:
        try:
            connectors.extend(read_card(lib, card))
        except (OSError, Refusal) as exc:
            raise Refusal("backend_unavailable", f"KMS card {card.name} cannot be fully inspected") from exc
    return screens_from_kms(connectors, capture, configured_encoder=configured_encoder)
