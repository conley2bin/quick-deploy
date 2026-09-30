#!/usr/bin/env python3
"""Read-only host display inventory fixtures; never query installed host/services."""
from __future__ import annotations

import ctypes as C
import io
import json
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / "display-control"
sys.path.insert(0, str(SOURCE))
import configuration as config
import host
import linux_kms as kms
import macos_display as mac
import protocol
import windows_display as windows

TARGET = {"address": "100.100.20.30", "port": 47989}


def listed(**kwargs):
    return json.dumps({"version": 1, "action": "list", "target": TARGET, **kwargs}).encode()


def kms_record(card, connector, *, crtc, connected=True, mode=None, planes=None,
               rotation=0, gpu_nvidia=False):
    return {"card": card, "connector": connector, "crtc": crtc, "connected": connected,
            "mode": mode, "planes": planes if planes is not None else [11],
            "rotation": rotation, "gpu_nvidia": gpu_nvidia}


class ProtocolTests(unittest.TestCase):
    def test_exact_schema_type_size_and_duplicate_rejections(self):
        self.assertEqual(protocol.request(listed())["target"], TARGET)
        invalid = [b"{", b"\xff", b"[]", b"null", b" " * 2049,
                   listed(extra=3),
                   b'{"version":1,"version":1,"action":"list","target":{"address":"100.100.20.30","port":47989}}',
                   b'{"version":true,"action":"list","target":{"address":"100.100.20.30","port":47989}}',
                   b'{"version":1,"action":"list","target":{"address":"100.100.20.30","port":true}}',
                   b'{"version":1,"action":"list","target":{"address":"127.0.0.1","port":47989}}',
                   b'{"version":1,"action":"os.system","target":{"address":"100.100.20.30","port":47989}}']
        for value in invalid:
            with self.subTest(value=value[:80]), self.assertRaises(protocol.Refusal):
                protocol.request(value)
        valid_prepare = json.dumps({"version": 1, "action": "prepare", "target": TARGET,
                                    "snapshot": "0" * 64, "display_id": "mac:4"}).encode()
        self.assertEqual(protocol.request(valid_prepare)["action"], "prepare")
        with self.assertRaises(protocol.Refusal):
            protocol.request(valid_prepare.replace(b'"mac:4"', b'"../../bin/sh"'))
        with self.assertRaises(protocol.Refusal):
            protocol.request(valid_prepare.replace(b'"mac:4"', b'"mac:4","path":"/tmp/x"'))


class KmsTests(unittest.TestCase):
    def test_drm_version_matches_independent_x86_64_userspace_bytes(self):
        # xf86drm.h drmVersion uses int lengths, not size_t. These native
        # offsets are independent of kms.DrmVersion and no pointer is followed.
        self.assertEqual(C.sizeof(C.c_void_p), 8)
        self.assertEqual(C.sizeof(kms.DrmVersion), 56)
        self.assertEqual((kms.DrmVersion.name_len.offset, kms.DrmVersion.name.offset,
                          kms.DrmVersion.date_len.offset, kms.DrmVersion.date.offset,
                          kms.DrmVersion.desc_len.offset, kms.DrmVersion.desc.offset),
                         (12, 16, 24, 32, 40, 48))
        raw = bytearray(56)
        name_ptr, date_ptr = 0x100012340000abcd, 0x200012340000dcba
        for offset, value in ((12, 10), (24, 8), (40, 4)):
            struct.pack_into("<i", raw, offset, value)
        for offset, value in ((16, name_ptr), (32, date_ptr), (48, 0x300012340000cdef)):
            struct.pack_into("<Q", raw, offset, value)
        self.assertEqual(C.c_int.from_buffer_copy(raw, kms.DrmVersion.name_len.offset).value, 10)
        self.assertEqual(C.c_void_p.from_buffer_copy(raw, kms.DrmVersion.name.offset).value, name_ptr)
        self.assertEqual(C.c_int.from_buffer_copy(raw, kms.DrmVersion.date_len.offset).value, 8)
        self.assertEqual(C.c_void_p.from_buffer_copy(raw, kms.DrmVersion.date.offset).value, date_ptr)

    def test_current_not_preferred_nonfirst_fractional_and_rotation(self):
        mode = kms.Mode()
        mode.clock, mode.htotal, mode.vtotal = 148352, 2200, 1125
        num, den = kms.nominal_rate(mode)
        self.assertAlmostEqual(num / den, 59.940202, places=3)
        records = [kms_record("card0", "eDP-1", crtc=11, mode=(1920, 1080, 60, 1)),
                   kms_record("card0", "DP-1", crtc=12, mode=(2160, 3840, num, den),
                              rotation=270)]
        screens = kms.screens_from_kms(records, "kms")
        self.assertEqual(screens[1]["id"], "kms:card0:DP-1")
        self.assertEqual((screens[1]["width"], screens[1]["height"]), (3840, 2160))
        self.assertEqual(screens[1]["mapping_status"], "unverified")
        self.assertEqual(screens[1]["mapping_reason"], "effective_encoder_device_unknown")
        items, _ = host.presentation(screens, "a" * 64, "server-111")
        second = next(s for s in items if s["id"] == "kms:card0:DP-1")
        self.assertEqual(second["requested_fps"], 60)
        self.assertFalse(second["capture_candidate"])
        self.assertFalse(second["selectable"])
        self.assertEqual(second["runtime_capture"], "unverified")

    def test_inactive_alias_clone_fb_and_rotation_refuse(self):
        rows = [kms_record("card0", "DP-1", crtc=21, mode=(2560, 1440, 60, 1)),
                kms_record("card1", "DP-1", crtc=32, mode=(1920, 1080, 60, 1)),
                kms_record("card0", "HDMI-A-1", crtc=21, mode=(2560, 1440, 60, 1)),
                kms_record("card0", "eDP-1", crtc=0, connected=False, mode=None, planes=[]),
                kms_record("card0", "DP-2", crtc=30, mode=(1440, 2560, 60, 1), rotation=90),
                kms_record("card0", "DP-3", crtc=31, mode=(2560, 1440, 60, 1)),
                kms_record("card0", "DP-4", crtc=33, mode=(2560, 1440, 0, 1))]
        screens = kms.screens_from_kms(rows, "kms", device_type="system")
        reasons = {s["id"]: s["mapping_reason"] for s in screens}
        self.assertEqual(reasons["kms:card0:DP-1"], "ambiguous_capture_name")
        self.assertEqual(reasons["kms:card0:HDMI-A-1"], "mirrored_crtc")
        self.assertNotIn("kms:card0:eDP-1", reasons)  # disconnected slot is not a physical screen
        self.assertEqual(reasons["kms:card0:DP-2"], "unsupported_kms_rotation")
        self.assertEqual(screens[-2]["mapping_status"], "mapped")
        self.assertEqual(reasons["kms:card0:DP-4"], "current_refresh_unknown")
        self.assertEqual(next(s for s in screens if s["id"].endswith("DP-4"))["width"], 2560)
        for backend in ("", "auto", "wlr", "portal", "x11", "nvfbc", "kwin"):
            with self.subTest(backend=backend):
                modes = kms.screens_from_kms(rows, backend)
                self.assertEqual(modes[-2]["mapping_status"], "unverified")
                self.assertEqual(modes[-2]["mapping_reason"], "capture_backend_unverified")


    def test_inactive_alias_is_mapping_evidence_after_effective_card_filter(self):
        rows = [kms_record("card0", "eDP-1", crtc=11, mode=(1920, 1080, 60, 1), gpu_nvidia=False),
                kms_record("card0", "DP-1", crtc=0, connected=False, mode=None,
                           planes=[], gpu_nvidia=False),
                kms_record("card1", "DP-1", crtc=22, mode=(3840, 2160, 60, 1), gpu_nvidia=True)]
        system = kms.screens_from_kms(rows, "kms", device_type="system")
        self.assertEqual([s["id"] for s in system], ["kms:card0:eDP-1", "kms:card1:DP-1"])
        self.assertEqual(system[1]["mapping_reason"], "ambiguous_capture_name")
        self.assertEqual(kms.screens_from_kms(rows, "kms")[1]["mapping_reason"], "ambiguous_capture_name")
        cuda = kms.screens_from_kms(rows, "kms", device_type="cuda")
        self.assertEqual(cuda[1]["mapping_status"], "mapped")  # Intel card excluded, including inactive alias
        vaapi = kms.screens_from_kms(rows, "kms", device_type="vaapi")
        self.assertEqual(vaapi[1]["mapping_reason"], "vaapi_skips_nvidia_card")

    def test_mixed_gpu_encoder_filter_and_no_helper_fb_export(self):
        rows = [kms_record("card0", "eDP-1", crtc=1, mode=(1920, 1080, 60, 1), gpu_nvidia=False),
                kms_record("card1", "DP-1", crtc=2, mode=(3840, 2160, 60, 1), gpu_nvidia=True)]
        vaapi = kms.screens_from_kms(rows, "kms", device_type="vaapi")
        self.assertEqual([s["mapping_status"] for s in vaapi], ["mapped", "filtered"])
        self.assertEqual(vaapi[1]["mapping_reason"], "vaapi_skips_nvidia_card")
        cuda = kms.screens_from_kms(rows, "kms", device_type="cuda")
        self.assertEqual([s["mapping_status"] for s in cuda], ["filtered", "mapped"])
        self.assertEqual(kms.screens_from_kms(rows, "kms", device_type="cuda", configured_encoder="nvenc")[0]["mapping_status"], "mapped")
        unknown = kms.screens_from_kms(rows, "kms")
        self.assertEqual([s["mapping_status"] for s in unknown], ["unverified", "unverified"])

        # libdrm can return a GETFB with handle=0 to an unprivileged caller.
        # Metadata enumeration must not invoke GETFB/PRIME or require that handle.
        resources = kms.Resources()
        connector_ids = (kms.U32 * 2)(11, 12)
        resources.count_connectors, resources.connectors = 2, connector_ids
        active, unused = kms.Connector(), kms.Connector()
        active.connector_type, active.connector_type_id, active.connection, active.encoder_id = 10, 3, 1, 7
        unused.connector_type, unused.connector_type_id, unused.connection = 11, 2, 2
        encoder = kms.Encoder(); encoder.crtc_id = 42
        crtc = kms.Crtc(); crtc.mode_valid, crtc.width, crtc.height = 1, 3840, 2160
        crtc.mode.clock, crtc.mode.htotal, crtc.mode.vtotal = 594000, 4400, 2250
        plane_ids = (kms.U32 * 1)(50)
        planes = kms.PlaneResources(); planes.count_planes, planes.planes = 1, plane_ids
        plane = kms.Plane(); plane.crtc_id, plane.fb_id, plane.plane_id = 42, 100, 50
        name = C.create_string_buffer(b"nvidia-drm")
        version = kms.DrmVersion(); version.name, version.name_len = C.cast(name, C.c_char_p), len(b"nvidia-drm")
        lib = mock.Mock()
        lib.drmSetClientCap.return_value = 0
        lib.drmGetVersion.return_value = C.pointer(version)
        lib.drmModeGetResources.return_value = C.pointer(resources)
        lib.drmModeGetConnector.side_effect = lambda fd, ident: C.pointer(active if ident == 11 else unused)
        lib.drmModeGetConnectorTypeName.side_effect = lambda kind: b"DP" if kind == 10 else b"HDMI-A"
        lib.drmModeGetEncoder.return_value = C.pointer(encoder)
        lib.drmModeGetCrtc.return_value = C.pointer(crtc)
        lib.drmModeGetPlaneResources.return_value = C.pointer(planes)
        lib.drmModeGetPlane.return_value = C.pointer(plane)
        lib.drmModeGetFB.return_value = mock.Mock(contents=mock.Mock(handle=0))
        with mock.patch.object(kms.os, "open", return_value=123), mock.patch.object(kms.os, "close"), \
             mock.patch.object(kms, "props", return_value={"type": 1, "rotation": 1}):
            observed = kms.read_card(lib, Path("/dev/dri/card1"))
        self.assertEqual(len(observed), 2)  # disconnected descriptor retained internally
        self.assertFalse(observed[1]["connected"])
        self.assertEqual(observed[0]["mode"], (3840, 2160, 60, 1))
        self.assertTrue(observed[0]["gpu_nvidia"])
        self.assertEqual(observed[0]["planes"], [50])
        lib.drmModeGetFB.assert_not_called()
        lib.drmPrimeHandleToFD.assert_not_called()
        visible = kms.screens_from_kms(observed, "kms")
        self.assertEqual(len(visible), 1)  # unused connector is not a screen
        self.assertEqual(visible[0]["native_reason"], None)


class WindowsTests(unittest.TestCase):
    def test_windows_native_record_nonfirst_rotated_fractional(self):
        paths = []
        modes = []
        for i, (w, h, n, d, rotation) in enumerate(((1920, 1080, 60000, 1000, 1),
                                                     (1440, 2560, 119998, 1000, 2))):
            p, m = windows.Path(), windows.Mode()
            p.flags, p.sourceInfo.id, p.sourceInfo.modeIndex = 1, i, (i << 16) | 0xffff
            p.sourceInfo.adapterId.LowPart, p.targetInfo.id = 10, i
            p.targetInfo.targetAvailable = 1
            p.targetInfo.rotation, p.targetInfo.refreshRate.Numerator, p.targetInfo.refreshRate.Denominator = rotation, n, d
            m.infoType, m.id, m.adapterId.LowPart = 1, i, 10
            C.cast(m.data, C.POINTER(windows.U32))[0] = w
            C.cast(m.data, C.POINTER(windows.U32))[1] = h
            C.cast(m.data, C.POINTER(C.c_int32))[3] = 0 if i == 0 else 1920
            C.cast(m.data, C.POINTER(C.c_int32))[4] = 0
            paths.append(p)
            modes.append(m)
        inactive = windows.Path(); inactive.targetInfo.id, inactive.targetInfo.targetAvailable = 2, 1
        unused = windows.Path(); unused.targetInfo.id, unused.targetInfo.targetAvailable = 3, 0
        alternate = windows.Path(); alternate.targetInfo.id, alternate.targetInfo.targetAvailable = 2, 1
        user = object()
        def device_name(_user, p, target):
            i = p.targetInfo.id
            return (f"\\\\?\\DISPLAY#{i}", f"Panel {i}") if target else f"\\\\.\\DISPLAY{i + 1}"
        def identity(path):
            uid = path.rsplit("#", 1)[-1]
            return rf"DISPLAY\ACI27EC\5&4FD2DE4&5&UID{uid}", bytes(range(128)), "edid_instance"
        with mock.patch.object(windows, "query_paths", side_effect=[(paths, modes, user),
                                                                     ([*paths, inactive, unused, alternate], [], user)]), \
             mock.patch.object(windows, "win_device_name", side_effect=device_name), \
             mock.patch.object(windows, "read_monitor_edition", side_effect=identity):
            screens = windows.enumerate_windows("ddx")
        second = screens[1]
        self.assertEqual((second["width"], second["height"]), (1440, 2560))
        self.assertEqual((second["refresh_num"], second["refresh_den"]), (119998, 1000))
        self.assertEqual(second["rotation"], 90)
        self.assertEqual(second["mapping_status"], "mapped")
        self.assertTrue(second["id"].startswith("win:{"))
        self.assertEqual(len(screens), 3)  # one true inactive monitor; no adapter slot/duplicate route
        self.assertEqual(screens[2]["native_reason"], "inactive")
        self.assertTrue(screens[2]["connected"])
        self.assertIsNone(screens[2]["selector"])
        items, _ = host.presentation(screens, "0" * 64, "server-111")
        presented = next(s for s in items if s["id"] == second["id"])
        self.assertEqual(presented["requested_fps"], 120)
        self.assertTrue(presented["selector_candidate"])
        self.assertFalse(presented["capture_candidate"])
        self.assertEqual(presented["runtime_capture"], "unverified")
        self.assertFalse(any(s["selectable"] for s in items))
        self.assertEqual(C.sizeof(windows.Path), 72)
        self.assertEqual(C.sizeof(windows.Mode), 64)

    def test_windows_source_mode_identity_mismatch_does_not_borrow_other_monitor(self):
        path = windows.Path()
        path.flags, path.targetInfo.targetAvailable, path.targetInfo.id = 1, 1, 5
        path.sourceInfo.id, path.sourceInfo.modeIndex, path.sourceInfo.adapterId.LowPart = 5, 0xffff, 10
        path.targetInfo.refreshRate.Numerator, path.targetInfo.refreshRate.Denominator = 60000, 1000
        mode = windows.Mode()
        mode.infoType, mode.id, mode.adapterId.LowPart = 1, 6, 10  # wrong source ID at high-16 index zero
        C.cast(mode.data, C.POINTER(windows.U32))[0] = 3840
        C.cast(mode.data, C.POINTER(windows.U32))[1] = 2160
        ident = r"DISPLAY\ACI27EC\5&4FD2DE4&5&UID5"
        with mock.patch.object(windows, "query_paths", side_effect=[([path], [mode], object()),
                                                                     ([path], [], object())]), \
             mock.patch.object(windows, "win_device_name", side_effect=lambda _u, _p, target:
                               (r"\\?\DISPLAY#5", "Panel") if target else r"\\.\DISPLAY5"), \
             mock.patch.object(windows, "read_monitor_edition", return_value=(ident, bytes(range(128)), "edid_instance")):
            screens = windows.enumerate_windows("ddx")
        self.assertEqual(len(screens), 1)
        self.assertIsNone(screens[0]["width"])
        self.assertEqual(screens[0]["native_reason"], "current_mode_unavailable")
        self.assertEqual(screens[0]["mapping_status"], "unavailable")

    def test_real_query_flags_and_active_set_change_refuse_mixed_snapshot(self):
        user, sizes_flags, query_flags, topology_args = mock.Mock(), [], [], []

        def buffer_sizes(flags, path_count, mode_count):
            sizes_flags.append(flags)
            if flags & 0x4:  # QDC_DATABASE_CURRENT requires a topology-ID output
                return 87  # ERROR_INVALID_PARAMETER
            C.cast(path_count, C.POINTER(windows.U32))[0] = 1 if flags == 0x12 else 2
            C.cast(mode_count, C.POINTER(windows.U32))[0] = 0
            return 0

        def query(flags, path_count, paths, mode_count, modes, topology):
            query_flags.append(flags)
            topology_args.append(topology)
            if flags == 0x12:  # A active in the first query
                paths[0].flags, paths[0].targetInfo.id, paths[0].targetInfo.targetAvailable = 1, 10, 1
            elif flags == 0x11:  # A deactivated, B activated before all-path snapshot
                paths[0].flags, paths[0].targetInfo.id, paths[0].targetInfo.targetAvailable = 0, 10, 1
                paths[1].flags, paths[1].targetInfo.id, paths[1].targetInfo.targetAvailable = 1, 11, 1
            else:
                return 87
            return 0

        user.GetDisplayConfigBufferSizes.side_effect = buffer_sizes
        user.QueryDisplayConfig.side_effect = query
        def identity(_user, path, target):
            label = "A" if path.targetInfo.id == 10 else "B"
            return (rf"\\?\DISPLAY#{label}", f"Panel {label}") if target else r"\\.\DISPLAY1"
        with mock.patch.object(windows.C, "WinDLL", create=True, return_value=user), \
             mock.patch.object(windows, "win_device_name", side_effect=identity), \
             mock.patch.object(windows, "read_monitor_edition", return_value=(None, None, "path_fallback_unverified")):
            with self.assertRaisesRegex(protocol.Refusal, "active monitor set changed"):
                windows.enumerate_windows("ddx")
        self.assertEqual(sizes_flags, [0x12, 0x11])
        self.assertEqual(query_flags, [0x12, 0x11])
        self.assertEqual(topology_args, [None, None])

    def test_sunshine_id_edid_instance_and_fallback_differ(self):
        path = r"\\?\DISPLAY#ACI27EC#5&4FD2DE4&5&UID4352"
        instance = r"DISPLAY\ACI27EC\5&4FD2DE4&5&UID4352"
        edid = bytes(range(128))
        stable = windows.sunshine_device_id(path, instance, edid)
        self.assertRegex(stable, r"^\{[0-9a-f-]{36}\}$")
        self.assertEqual(stable, windows.sunshine_device_id(path, r"DISPLAY\ACI27EC\5&4FD2DE4&9&UID4352", edid))
        self.assertNotEqual(stable, windows.sunshine_device_id(path, None, None))
        self.assertEqual(stable, "{21fcc300-cc06-599f-bda6-d571761763c1}")

    def test_interrupted_setupapi_lookup_never_publishes_path_hash_selector(self):
        setup, registry = mock.Mock(), mock.Mock()
        setup.SetupDiGetClassDevsW.return_value = 1234
        setup.SetupDiEnumDeviceInterfaces.return_value = 0
        # Upstream continues after ERROR_ACCESS_DENIED at index 0 and only
        # stops on ERROR_NO_MORE_ITEMS at index 1. An incomplete search must
        # refuse selector provenance, not silently use an attractive path GUID.
        with mock.patch.object(windows.C, "WinDLL", create=True,
                               side_effect=lambda name, **_: setup if name == "setupapi" else registry), \
             mock.patch.object(windows, "win_last_error", side_effect=[5, 259]):
            with self.assertRaisesRegex(protocol.Refusal, "interrupted"):
                windows.read_monitor_edition(r"\\?\DISPLAY#target")
        self.assertEqual(setup.SetupDiEnumDeviceInterfaces.call_count, 2)
        setup.SetupDiDestroyDeviceInfoList.assert_called_once()
        row = {"source_key": (1, 0, 1), "active": True, "available": True,
               "device_path": r"\\?\DISPLAY#target", "source_name": r"\\.\DISPLAY1",
               "friendly": "Panel", "primary": True, "mode": (1920, 1080, 60, 1),
               "rotation": 0, "identity_provenance": "lookup_interrupted"}
        screen = windows.screens_from_paths([row])[0]
        self.assertEqual(screen["width"], 1920)
        self.assertIsNone(screen["selector"])
        self.assertEqual(screen["mapping_reason"], "capture_identity_lookup_interrupted")

    def test_mirror_and_missing_modes_refused(self):
        base = {"source_key": (1, 0, 1), "active": True, "available": True,
                "device_path": r"\\?\DISPLAY#A", "source_name": r"\\.\DISPLAY1", "friendly": "A",
                "primary": True, "mode": (1920, 1080, 60000, 1000), "rotation": 0,
                "instance": r"DISPLAY\ACI27EC\5&4FD2DE4&5&UID4352", "edid": bytes(range(128)),
                "identity_provenance": "edid_instance"}
        cloned = dict(base, device_path=r"\\?\DISPLAY#B", friendly="B")
        self.assertEqual([s["mapping_reason"] for s in windows.screens_from_paths([base, cloned])],
                         ["mirrored_source", "mirrored_source"])
        missing = windows.screens_from_paths([dict(base, mode=None)])
        self.assertEqual(missing[0]["native_reason"], "current_mode_unavailable")
        bad_rate = windows.screens_from_paths([dict(base, mode=(1920, 1080, 60, 0))])
        self.assertEqual(bad_rate[0]["native_reason"], "current_refresh_unknown")
        self.assertEqual(bad_rate[0]["width"], 1920)
        fallback = windows.screens_from_paths([dict(base, identity_provenance="path_fallback_unverified",
                                                        instance=None, edid=None)])
        self.assertIsNone(fallback[0]["selector"])
        self.assertEqual(fallback[0]["mapping_status"], "unverified")
        self.assertEqual(windows.screens_from_paths([base], "")[0]["mapping_reason"], "capture_backend_unverified")


class MacTests(unittest.TestCase):
    def test_native_coregraphics_pixel_size_current_refresh_and_mirror(self):
        class Fake:
            pass
        fake = Fake()
        def setfunc(name, func):
            setattr(fake, name, mock.Mock(side_effect=func))
        def online(_max, array, count):
            C.cast(count, C.POINTER(mac.U32))[0] = 4
            if array is not None:
                array[0], array[1], array[2], array[3] = 41, 42, 43, 44
            return 0
        setfunc("CGGetOnlineDisplayList", online)
        setfunc("CGDisplayCopyDisplayMode", lambda ident: ident)
        setfunc("CGDisplayModeGetPixelWidth", lambda ident: {41: 1920, 42: 3840, 43: 1920, 44: 3840}[ident])
        setfunc("CGDisplayModeGetPixelHeight", lambda ident: {41: 1080, 42: 2160, 43: 1080, 44: 2160}[ident])
        setfunc("CGDisplayModeGetRefreshRate", lambda ident: {41: 60, 42: 59.94, 43: 60, 44: 0}[ident])
        setfunc("CGDisplayModeRelease", lambda _: None)
        setfunc("CGDisplayIsActive", lambda _: 1)
        setfunc("CGDisplayIsMain", lambda ident: int(ident == 41))
        setfunc("CGDisplayMirrorsDisplay", lambda ident: 41 if ident == 43 else 0)
        setfunc("CGDisplayRotation", lambda ident: 90 if ident == 42 else 0)
        with mock.patch.object(mac.C, "CDLL", return_value=fake):
            screens = mac.enumerate_macos("")
        self.assertEqual((screens[1]["width"], screens[1]["height"]), (3840, 2160))
        self.assertEqual(screens[1]["id"], "mac:42")
        self.assertEqual(screens[1]["rotation"], 90)
        self.assertAlmostEqual(screens[1]["refresh_num"] / screens[1]["refresh_den"], 59.94)
        self.assertEqual(screens[1]["mapping_status"], "mapped")
        self.assertEqual(screens[2]["mapping_reason"], "mirrored_display")
        self.assertEqual((screens[3]["width"], screens[3]["height"]), (3840, 2160))
        self.assertIsNone(screens[3]["refresh_num"])
        self.assertEqual(screens[3]["native_reason"], "current_refresh_unknown")
        unknown = mac.screens_from_coregraphics([dict(id=4, active=True, online=True, primary=False,
                                                      mirror_of=0, rotation=0, mode=None)])
        self.assertEqual(unknown[0]["native_reason"], "current_mode_unavailable")
        invalid = mac.screens_from_coregraphics([dict(id=4, active=True, online=True, primary=False,
                                                      mirror_of=0, rotation=0, mode=(3840, 2160, 0, 1))])
        self.assertEqual(invalid[0]["native_reason"], "current_refresh_unknown")
        self.assertEqual(invalid[0]["width"], 3840)
        with mock.patch.object(mac.C, "CDLL", return_value=fake):
            unsupported = mac.enumerate_macos("ddx")
        self.assertEqual(unsupported[1]["mapping_reason"], "capture_backend_unverified")
        self.assertEqual(unsupported[2]["mapping_reason"], "mirrored_display")


class CliTests(unittest.TestCase):
    def config_and_manifest(self, directory):
        conf = directory / "sunshine.conf"
        conf.write_bytes(b"bind_address = 100.100.20.30\naddress_family = ipv4\nport = 47989\n"
                         b"capture = kms\nencoder = vaapi\noutput_name = DP-1\ncredentials = secret-not-returned\n")
        manifest = directory / "host-settings.json"
        manifest.write_text(json.dumps({"schema": 1, "platform": "linux", "config_file": str(conf),
                                        "host_uniqueid": "server-111"}))
        return conf, manifest

    def test_serverinfo_uses_plain_http_on_exact_base_port(self):
        xml = (b'<root status_code="200"><uniqueid>server-111</uniqueid>'
               b'<state>SUNSHINE_SERVER_FREE</state><currentgame>0</currentgame></root>')
        opener = mock.Mock()
        opener.open.return_value = io.BytesIO(xml)
        with mock.patch.object(host.urllib.request, "build_opener", return_value=opener) as build:
            self.assertEqual(host.serverinfo(TARGET["address"], TARGET["port"]), ("server-111", "unknown"))
        self.assertEqual(opener.open.call_args.args[0], "http://100.100.20.30:47989/serverinfo")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 2)
        self.assertTrue(any(isinstance(arg, host.NoRedirect) for arg in build.call_args.args))
        self.assertTrue(any(isinstance(arg, host.urllib.request.ProxyHandler) and not arg.proxies
                            for arg in build.call_args.args))

    def test_identity_binding_snapshot_and_no_prepare(self):
        with tempfile.TemporaryDirectory() as root:
            conf, manifest = self.config_and_manifest(Path(root))
            state = [kms_record("card0", "eDP-1", crtc=1, mode=(1920, 1080, 60, 1)),
                     kms_record("card1", "DP-1", crtc=2, mode=(3840, 2160, 60000, 1001), gpu_nvidia=True)]
            def inventory(platform, capture, encoder):
                self.assertEqual((platform, capture, encoder), ("linux", "kms", "vaapi"))
                return kms.screens_from_kms(state, capture)
            def endpoint(addr, port):
                self.assertEqual((addr, port), (TARGET["address"], TARGET["port"]))
                return "server-111", "unknown"
            run = lambda raw: json.loads(host.handle(raw, manifest=manifest, platform="linux",
                                                  enumerate_native=inventory, info=endpoint))
            first = run(listed())
            self.assertTrue(first["ok"])
            self.assertFalse(first["selection_ready"])
            self.assertEqual(first["runtime"], "lifecycle_unverified")
            self.assertEqual(first["activity"], "unknown")
            selected = next(s for s in first["screens"] if s["id"] == "kms:card1:DP-1")
            self.assertEqual(selected["requested_fps"], 60)
            self.assertEqual(selected["selector"], "DP-1")
            self.assertEqual(selected["selector_mapping"], "unverified")
            self.assertEqual(selected["mapping_reason"], "effective_encoder_device_unknown")
            self.assertEqual(selected["runtime_capture"], "unverified")
            self.assertFalse(selected["capture_candidate"])
            self.assertFalse(selected["selectable"])
            self.assertEqual(first["configured_capture"], "kms")
            self.assertEqual(first["configured_encoder"], "vaapi")
            # A config value does not attest the effective running encoder.
            self.assertEqual(selected["mapping_reason"], "effective_encoder_device_unknown")
            self.assertNotIn("secret-not-returned", json.dumps(first))
            self.assertEqual(first["snapshot"], run(listed())["snapshot"])
            state[1]["mode"] = (2560, 1440, 120000, 1001)
            self.assertNotEqual(first["snapshot"], run(listed())["snapshot"])
            # A stale or fresh snapshot cannot authorize a switch in this milestone.
            prepared = {"version": 1, "action": "prepare", "target": TARGET,
                        "snapshot": first["snapshot"], "display_id": "kms:card0:DP-1"}
            with self.assertRaisesRegex(protocol.Refusal, "reservation"):
                run(json.dumps(prepared).encode())
            self.assertIn(b"output_name = DP-1", conf.read_bytes())
            with self.assertRaises(protocol.Refusal):
                host.handle(listed(), manifest=manifest, platform="linux", enumerate_native=inventory,
                            info=lambda *_: ("another-host", "unknown"))
            with self.assertRaises(protocol.Refusal):
                host.handle(json.dumps({"version": 1, "action": "list", "target": dict(TARGET, port=47990)}).encode(),
                            manifest=manifest, platform="linux", enumerate_native=inventory, info=endpoint)

    def test_config_parser_native_bracket_and_duplicate_refusal(self):
        text = b"apps = [\noutput_name = WRONG\n]\noutput_name = DP-1#owned comment\n"
        self.assertEqual(config.get_settings(text)["output_name"], "DP-1")
        self.assertEqual(config.select_output(text, "DP-2"), text.replace(b"DP-1", b"DP-2"))
        for invalid in (b"output_name = DP-1 \n", b"output_name = DP-1\noutput_name = DP-2\n",
                        b"output_name = [DP-1]\n", b"apps = [\n"):
            with self.assertRaises(protocol.Refusal):
                config.get_settings(invalid)

    def test_real_cli_is_closed_and_bounded_without_installer_manifest(self):
        child = subprocess.run([sys.executable, str(SOURCE / "host.py")], input=listed(),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=4)
        result = json.loads(child.stdout)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "runtime_unconfigured")
        self.assertEqual(child.stderr, b"")
        self.assertLess(len(child.stdout), 4096)
        invalid = subprocess.run([sys.executable, str(SOURCE / "host.py")], input=b"{" * 2049,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=4)
        self.assertEqual(json.loads(invalid.stdout)["error"]["code"], "invalid_request")
        prepared = json.dumps({"version": 1, "action": "prepare", "target": TARGET,
                               "snapshot": "0" * 64, "display_id": "mac:4"}).encode()
        child = subprocess.run([sys.executable, str(SOURCE / "host.py")], input=prepared,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=4)
        self.assertEqual(json.loads(child.stdout)["error"]["code"], "reservation_unavailable")
        self.assertEqual(child.stderr, b"")


if __name__ == "__main__":
    unittest.main()
