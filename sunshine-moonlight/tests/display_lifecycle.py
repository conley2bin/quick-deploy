#!/usr/bin/env python3
"""Actual JSON/coordinator/store/config path; injected systemd/proc/HTTP/DRM.

All files live in private TemporaryDirectory fixtures. No installed HOME,
service, device, GUI, credential, SSH or network operation is permitted.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import io
import json
import multiprocessing as mp
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "display-control"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import configuration as config
import host
import lifecycle as lc
import linux_kms as kms
import protocol
import reservation as rs
import runtime_linux as rt
import display_runtime_linux as runtime_fixture

HOST = "AABBCCDD-1234-4321-ABCD-0123456789AB"
TARGET = {"address": "100.100.20.30", "port": 47989}
FIRST = "kms:card0:eDP-1"
SECOND = "kms:card1:HDMI-A-2"
INITIAL = (b"# retain private/unrelated bytes\r\naddress_family = ipv4\r\n"
           b"bind_address = 100.100.20.30\r\ncapture = kms\r\nencoder = nvenc\r\n"
           b"output_name = eDP-1#keep comment\r\ncredentials = never-return-this-secret\r\n")
PNG = b"\x89PNG\r\n\x1a\nprivate fixture image bytes"


def must_contend(directory, result):
    try:
        with lc.PrivateFiles(Path(directory)).locked():
            result.put("acquired")
    except protocol.Refusal as exc:
        result.put(exc.code)


class FixtureTransport(runtime_fixture.FakeTransport):
    def __init__(self, fixture):
        super().__init__()
        self.fixture = fixture
        self.links[runtime_fixture.HOME] = fixture.home
        self.manager = f"HOME={fixture.home}\n"
        self.put(runtime_fixture.PROC / "environ", f"HOME={fixture.home}\0".encode())
        self.restarts = 0
        self.restart_fail = False
        self.failed_probe = False
        self.keep_old = False
        self.on_restart = None
        self.probe_connector = None
        self.current_trace()

    def current_trace(self):
        cfg = config.get_settings(self.fixture.conf.read_bytes())
        matches = [row for row in self.fixture.rows if row["connector"] == cfg["output_name"] and row["connected"]]
        connector = (self.probe_connector if self.probe_connector is not None else
                     matches[0]["connector_id"] if len(matches) == 1 else 507)
        messages = runtime_fixture.startup(encoder=cfg["encoder"], output=cfg["output_name"])
        messages += (runtime_fixture.completed(encoder="nvenc", connector=connector) if not self.failed_probe else
                     ["Trying encoder [nvenc]", "Screencasting with KMS", "Encoder [nvenc] failed"])
        self.trace(messages)
        for row in self.entries:
            row["_PID"] = self.props["MainPID"]
            row["_SYSTEMD_INVOCATION_ID"] = self.props["InvocationID"]
            row["__MONOTONIC_TIMESTAMP"] = str(int(self.props["ExecMainStartTimestampMonotonic"]) +
                                                int(row["__MONOTONIC_TIMESTAMP"]) - runtime_fixture.START)

    def read(self, path, *, limit):
        path = self.canonical(path)
        if path == self.fixture.conf:
            return self.fixture.conf.read_bytes()
        return super().read(path, limit=limit)

    def identity(self, path, *, follow=False):
        path = self.canonical(path)
        if path == self.fixture.conf or path == self.fixture.home or path == self.fixture.conf.parent:
            return rt.FileIdentity.from_stat(path.stat() if follow else path.lstat())
        return super().identity(path, follow=follow)

    def realpath(self, path):
        path = self.canonical(path)
        if path in (self.fixture.conf, self.fixture.home, self.fixture.conf.parent):
            return path
        return super().realpath(path)

    def run(self, argv, *, timeout, max_output):
        if argv == runtime_fixture.RESTART:
            self.calls.append((argv, timeout, max_output))
            self.restarts += 1
            # Inspect genuine on-disk operation intent BEFORE dispatch effects.
            meta = json.loads((self.fixture.state / "operation.json").read_bytes())
            persisted = rs.ReservationStore(self.fixture.state, rs.HostIdentity(lc.canonical_uuid(HOST), 47989)).status()
            assert meta["lease"]["stage"] == "restart_intent"
            assert persisted.phase is rs.Phase.PREPARING and not persisted.launch_attempted
            assert not (self.fixture.state / "start-receipt.json").exists() or self.restarts > 1
            if self.on_restart:
                self.on_restart(self)
            if self.restart_fail:
                return rt.CommandResult(1, stderr=b"synthetic queued/failure uncertainty")
            old = Path("/proc") / self.props["MainPID"]
            pid = int(self.props["MainPID"]) + 1
            new = Path("/proc") / str(pid)
            for suffix in ("status", "cmdline", "cgroup", "environ"):
                self.put(new / suffix, self.files[old / suffix])
            fields = ["S"] + ["0"] * 18 + [str(500 + self.restarts)] + ["0"] * 30
            self.put(new / "stat", (f"{pid} (sunshine worker)) " + " ".join(fields) + "\n").encode())
            if not self.keep_old:
                self.files.pop(old / "stat", None)
                self.ids.pop(old / "stat", None)
            self.props.update(MainPID=str(pid), InvocationID=f"{pid:032x}",
                              ExecMainStartTimestampMonotonic=str(runtime_fixture.START + 1000000 * self.restarts))
            self.current_trace()
            return rt.CommandResult(0)
        return super().run(argv, timeout=timeout, max_output=max_output)

    def readlink(self, path):
        if Path(path).name != "exe":
            raise AssertionError(path)
        return self.exe_link



class Fixture:
    def __init__(self, root):
        self.root = Path(root)
        self.home = self.root / "runtime-home"
        self.conf = self.home / ".config/sunshine/sunshine.conf"
        self.conf.parent.mkdir(parents=True)
        self.conf.write_bytes(INITIAL)
        self.conf.chmod(0o600)
        self.state = self.root / "controller-state"
        self.state.mkdir(mode=0o700)
        self.assets = self.root / "installed-assets"
        self.assets.mkdir()
        (self.assets / "desktop.png").write_bytes(PNG)
        (self.assets / "desktop.png").chmod(0o644)
        self.apps = self.conf.with_name("apps.json")
        self.apps.write_text(json.dumps({"env": {}, "apps": [
            {"name": "Desktop", "cmd": "", "image-path": "desktop.png"}]}))
        self.apps.chmod(0o600)
        self.manifest = self.root / "host-settings.json"
        self.document = {"schema": 2, "platform": "linux", "config_file": str(self.conf),
                         "apps_file": str(self.apps), "assets_directory": str(self.assets),
                         "host_uniqueid": HOST, "base_port": 47989, "state_directory": str(self.state),
                         "runtime_home": str(self.home), "runtime_uid": os.geteuid(), "unit": rt.UNIT,
                         "retry_guard_sha256": None,
                         "build": {"executable_sha256": hashlib.sha256(runtime_fixture.BINARY).hexdigest(),
                                   "source_commit": rt.SOURCE_COMMIT,
                                   "provenance": {"kind": "verified_exact_source_build", "reference": "synthetic qualification fixture",
                                                  "artifact_sha256": "a" * 64}}}
        self.write_manifest()
        self.app = 0
        self.endpoint_id = HOST
        self.rows = [dict(card="card0", connector="eDP-1", connector_id=507, crtc=11,
                          connected=True, mode=(1920, 1080, 60, 1), planes=[101], rotation=0, gpu_nvidia=False),
                     dict(card="card1", connector="HDMI-A-2", connector_id=601, crtc=22,
                          connected=True, mode=(3840, 2160, 60000, 1001), planes=[201], rotation=0, gpu_nvidia=True)]
        self.transport = FixtureTransport(self)
        # FakeTransport's /proc/exe seam was originally fixed to PID42; this
        # fixture models the same public transport for every observed generation.
        original_identity = self.transport.identity
        self.transport.identity = lambda path, follow=False: (
            self.transport.ids[rt.EXECUTABLE] if str(path).startswith("/proc/") and Path(path).name == "exe" and follow
            else original_identity(path, follow=follow))
        self.runtime = rt.LinuxRuntime(host.settings(self.manifest, "linux").runtime, self.transport)
        self.authorization = True
        self.inventory_hook = None

    def write_manifest(self):
        self.manifest.write_text(json.dumps(self.document))
        self.manifest.chmod(0o600)

    def endpoint(self, address, port):
        assert (address, port) == (TARGET["address"], TARGET["port"])
        return lc.Endpoint(self.endpoint_id, "BUSY" if self.app else "FREE", self.app)

    def inventory(self, platform, capture, encoder, *, device_type=None):
        assert platform == "linux"
        if self.inventory_hook:
            self.inventory_hook(device_type)
        return kms.screens_from_kms(self.rows, capture, device_type=device_type, configured_encoder=encoder)

    def rpc(self, action, **fields):
        req = {"version": 1, "action": action, "target": TARGET, **fields}
        return json.loads(host.handle(json.dumps(req).encode(), manifest=self.manifest, platform="linux",
                          enumerate_native=self.inventory, info=self.endpoint, runtime=self.runtime,
                          authorize_enrollment=lambda: self.authorization, sleep=lambda _: None, readiness_attempts=3))

    def status(self):
        return self.rpc("status")

    def enroll(self):
        return self.rpc("enroll", revision=self.status()["revision"])

    def acquire(self):
        menu = self.rpc("list")
        return self.rpc("acquire", revision=menu["revision"], snapshot=menu["snapshot"])

    def owner(self, ticket, action, **fields):
        return self.rpc(action, revision=ticket["revision"], capability=ticket["capability"], **fields)

    def prepare(self, ticket=None, display=SECOND):
        ticket = ticket or self.acquire()
        return self.owner(ticket, "prepare", snapshot=ticket["snapshot"], display_id=display)

    def desktop_id(self):
        input_bytes = b"Desktop" + hashlib.sha256(PNG).hexdigest().encode("ascii")
        crc = zlib.crc32(input_bytes)
        return abs(int.from_bytes(crc.to_bytes(4, "little"), "little", signed=True))

    def active(self):
        prepared = self.prepare()
        attempted = self.owner(prepared, "launch")
        self.app = attempted["desktop_app_id"]
        return self.owner(attempted, "observe_active")


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = Fixture(self.temp.name)
        self.tripwire = mock.patch.object(rt.subprocess, "Popen", side_effect=AssertionError("real subprocess forbidden"))
        self.tripwire.start()
        self.addCleanup(self.tripwire.stop)

    def test_success_nonfirst_new_generation_real_receipt_and_rebind(self):
        f = self.fixture
        before = f.rpc("list")
        self.assertEqual(before["phase"], "reconciliation_required")
        self.assertTrue(next(s for s in before["screens"] if s["id"] == SECOND)["selectable"])
        self.assertFalse(next(s for s in before["screens"] if s["id"] == SECOND)["capture_candidate"])
        self.assertFalse(before["selection_ready"])
        with self.assertRaises(protocol.Refusal):
            f.acquire()  # raw FREE did not enroll
        enrolled = f.enroll()
        ticket = f.acquire()
        prepared = f.prepare(ticket)
        self.assertTrue(prepared["ok"])
        self.assertTrue(prepared["restarted"])
        self.assertTrue(prepared["launch_authorized"])
        self.assertEqual(prepared["capability"], ticket["capability"])
        self.assertEqual(prepared["display"]["id"], SECOND)
        self.assertEqual((prepared["display"]["width"], prepared["display"]["height"], prepared["display"]["requested_fps"]), (3840, 2160, 60))
        self.assertEqual(f.conf.read_bytes(), INITIAL.replace(b"eDP-1", b"HDMI-A-2"))
        self.assertEqual(stat.S_IMODE(f.conf.stat().st_mode), 0o600)
        self.assertEqual(f.transport.restarts, 1)
        self.assertEqual(before["topology_digest"], prepared["topology_digest"])
        self.assertNotEqual(before["snapshot"], prepared["snapshot"])
        receipt = json.loads((f.state / "start-receipt.json").read_bytes())
        self.assertEqual(receipt["receipt"]["generation"]["pid"], 43)
        self.assertEqual(receipt["receipt"]["config"]["sha256"], config.digest(f.conf.read_bytes()))
        self.assertEqual(receipt["issuance"]["previous_generation"]["pid"], 42)
        self.assertEqual(stat.S_IMODE((f.state / "start-receipt.json").stat().st_mode), 0o600)
        store = rs.ReservationStore(f.state, rs.HostIdentity(lc.canonical_uuid(HOST), 47989))
        self.assertEqual(store.status().binding.generation.pid, 43)
        self.assertEqual((f.state / "prepare-backup.conf").read_bytes(), INITIAL)
        self.assertNotIn("never-return-this-secret", json.dumps(prepared))
        self.assertNotIn(ticket["capability"], json.dumps(f.status()))

    def test_noop_and_normal_three_reconnects_zero_extra_restart(self):
        f = self.fixture
        f.enroll()
        prepared = f.prepare()
        status = f.owner(prepared, "abort")
        self.assertEqual(status["phase"], "available")
        inode = f.conf.stat().st_ino
        noop = f.prepare()
        self.assertFalse(noop["restarted"])
        self.assertEqual(f.transport.restarts, 1)
        self.assertEqual(f.conf.stat().st_ino, inode)
        attempted = f.owner(noop, "launch")
        self.assertEqual(attempted["desktop_app_id"], f.desktop_id())
        f.app = f.desktop_id()
        active = f.owner(attempted, "observe_active")
        for _ in range(3):
            resumed = f.owner(active, "resume")
            self.assertEqual(resumed["phase"], "reconnecting")
            active = f.owner(resumed, "observe_active")
        releasing = f.owner(active, "begin_release")
        self.assertEqual(releasing["phase"], "releasing")
        self.assertEqual(f.transport.restarts, 1)
        f.app = 0  # actual injected /cancel transition, not RPC evidence
        closed = f.owner(releasing, "finish_release")
        self.assertEqual(closed["phase"], "available")
        self.assertEqual(closed["closure_basis"], "same_generation_owned_desktop_cancelled")
        again = f.prepare()
        self.assertFalse(again["restarted"])
        self.assertEqual(f.transport.restarts, 1)

    def test_busy_onboarding_and_prepare_do_not_touch_existing_session(self):
        f = self.fixture
        f.app = 958645192  # excluded unmanaged current stream, never our app identity
        before = f.conf.read_bytes()
        with self.assertRaisesRegex(protocol.Refusal, "untouched"):
            f.enroll()
        self.assertEqual(f.transport.restarts, 0)
        self.assertEqual(f.conf.read_bytes(), before)
        f.app = 0
        f.enroll()
        ticket = f.acquire()
        f.app = 958645192
        with self.assertRaises(protocol.Refusal):
            f.prepare(ticket)
        self.assertEqual(f.transport.restarts, 0)
        self.assertEqual(f.conf.read_bytes(), before)
        self.assertEqual(f.status()["phase"], "selecting")

    def test_explicit_authorization_and_qualified_build_required(self):
        f = self.fixture
        f.authorization = False
        with self.assertRaisesRegex(protocol.Refusal, "ceremony"):
            f.enroll()
        self.assertFalse((f.state / "reservation.json").exists())
        f.document["build"].pop("provenance")
        f.write_manifest()
        with self.assertRaisesRegex(protocol.Refusal, "provenance"):
            f.status()
        with tempfile.TemporaryDirectory() as root:
            other = Fixture(root)
            other.document["build"]["executable_sha256"] = "0" * 64
            other.write_manifest()
            other.runtime = rt.LinuxRuntime(host.settings(other.manifest).runtime, other.transport)
            with self.assertRaisesRegex(protocol.Refusal, "qualification"):
                other.enroll()
            self.assertFalse((other.state / "reservation.json").exists())
            self.assertEqual(other.transport.restarts, 0)

    def test_exact_snapshot_stale_hotplug_before_write(self):
        f = self.fixture
        f.enroll()
        ticket = f.acquire()
        f.rows[1]["mode"] = (2560, 1440, 120, 1)
        with self.assertRaisesRegex(protocol.Refusal, "snapshot"):
            f.prepare(ticket)
        self.assertEqual(f.transport.restarts, 0)
        self.assertEqual(f.conf.read_bytes(), INITIAL)
        self.assertEqual(f.status()["phase"], "selecting")

    def test_target_uuid_and_port_mismatch_no_config_change(self):
        f = self.fixture
        f.endpoint_id = "11223344-1234-4321-abcd-0123456789ab"
        with self.assertRaises(protocol.Refusal):
            f.enroll()
        f.endpoint_id = HOST
        with self.assertRaises(protocol.Refusal):
            host.handle(json.dumps({"version": 1, "action": "list", "target": dict(TARGET, port=47990)}).encode(),
                        manifest=f.manifest, runtime=f.runtime, enumerate_native=f.inventory, info=f.endpoint)
        self.assertEqual(f.transport.restarts, 0)
        self.assertEqual(f.conf.read_bytes(), INITIAL)

    def test_second_caller_wrong_capability_stale_replay_refused(self):
        f = self.fixture
        f.enroll()
        ticket = f.acquire()
        with self.assertRaises(protocol.Refusal):
            f.acquire()
        bad = dict(ticket, capability="0" * 64)
        with self.assertRaises(protocol.Refusal):
            f.prepare(bad)
        prepared = f.prepare(ticket)
        with self.assertRaises(protocol.Refusal):
            f.owner(ticket, "abort")
        self.assertEqual(f.status()["revision"], prepared["revision"])

    def test_cross_process_operation_lock_and_concurrent_abort(self):
        f = self.fixture
        f.enroll()
        ticket = f.acquire()
        def during_restart(transport):
            with self.assertRaises(protocol.Refusal) as error:
                f.owner(ticket, "abort")
            self.assertEqual(error.exception.code, "operation_contended")
            context = mp.get_context("spawn")
            queue = context.Queue()
            child = context.Process(target=must_contend, args=(str(f.state), queue))
            child.start()
            self.assertEqual(queue.get(timeout=5), "operation_contended")
            child.join(5)
            self.assertEqual(child.exitcode, 0)
            queue.close()
        f.transport.on_restart = during_restart
        prepared = f.prepare(ticket)
        self.assertEqual(prepared["phase"], "preparing")
        self.assertTrue(prepared["launch_authorized"])

    def test_hotplug_after_owned_restart_retains_lease(self):
        f = self.fixture
        f.enroll()
        ticket = f.acquire()
        f.transport.on_restart = lambda _: f.rows[1].update(mode=(2560, 1440, 60, 1))
        with self.assertRaisesRegex(protocol.Refusal, "topology"):
            f.prepare(ticket)
        self.assertEqual(f.status()["phase"], "reconciliation_required")
        self.assertEqual(f.conf.read_bytes(), INITIAL)  # owned idle unchanged transaction rolled back
        with self.assertRaises(protocol.Refusal):
            f.acquire()

    def test_wrong_connector_capture_correspondence_refuses(self):
        f = self.fixture
        f.enroll()
        f.rows[1]["connector_id"] = 888
        f.transport.probe_connector = 601  # independent wrong-output observation, not a verified flag
        with self.assertRaisesRegex(protocol.Refusal, "connector"):
            f.prepare()
        self.assertEqual(f.status()["phase"], "reconciliation_required")

    def test_failed_readiness_safe_rollback_and_conflict_preservation(self):
        f = self.fixture
        f.enroll()
        f.transport.failed_probe = True
        with self.assertRaisesRegex(protocol.Refusal, "readiness"):
            f.prepare()
        self.assertEqual(f.conf.read_bytes(), INITIAL)
        self.assertEqual(f.status()["phase"], "reconciliation_required")
        with tempfile.TemporaryDirectory() as root:
            other = Fixture(root)
            other.enroll()
            changed = INITIAL.replace(b"eDP-1", b"HDMI-A-2") + b"operator_edit = preserve\n"
            other.transport.on_restart = lambda _: other.conf.write_bytes(changed)
            with self.assertRaises(protocol.Refusal):
                other.prepare()
            self.assertEqual(other.conf.read_bytes(), changed)
            self.assertEqual(other.status()["phase"], "reconciliation_required")

    def test_old_process_alive_or_dispatch_uncertainty_does_not_launch(self):
        f = self.fixture
        f.enroll()
        f.transport.keep_old = True
        with self.assertRaisesRegex(protocol.Refusal, "readiness"):
            f.prepare()
        self.assertEqual(f.status()["phase"], "reconciliation_required")
        self.assertFalse((f.state / "start-receipt.json").exists())
        with tempfile.TemporaryDirectory() as root:
            other = Fixture(root)
            other.enroll()
            other.transport.restart_fail = True
            with self.assertRaisesRegex(protocol.Refusal, "restart"):
                other.prepare()
            self.assertEqual(other.status()["phase"], "reconciliation_required")

    def test_launch_attempt_persisted_unconfirmed_cannot_abort_or_release(self):
        f = self.fixture
        f.enroll()
        prepared = f.prepare()
        attempted = f.owner(prepared, "launch")
        status = rs.ReservationStore(f.state, rs.HostIdentity(lc.canonical_uuid(HOST), 47989)).status()
        self.assertEqual(status.phase, rs.Phase.CONNECTING)
        self.assertTrue(status.launch_attempted)
        with self.assertRaises(protocol.Refusal):
            f.owner(attempted, "observe_active")
        self.assertEqual(f.status()["phase"], "connecting")
        with self.assertRaises(protocol.Refusal):
            f.owner(attempted, "abort")
        self.assertEqual(f.status()["phase"], "reconciliation_required")

    def test_release_requires_owned_positive_before_cancel(self):
        f = self.fixture
        f.enroll()
        active = f.active()
        with self.assertRaises(protocol.Refusal):
            f.owner(active, "finish_release")
        self.assertEqual(f.status()["phase"], "active")
        releasing = f.owner(active, "begin_release")
        with self.assertRaises(protocol.Refusal):
            f.owner(releasing, "finish_release")
        self.assertEqual(f.status()["phase"], "releasing")
        f.app = 0
        self.assertEqual(f.owner(releasing, "finish_release")["phase"], "available")

    def test_owner_loss_is_retained_even_when_free(self):
        f = self.fixture
        f.enroll()
        prepared = f.prepare()
        lost = f.owner(prepared, "control_lost")
        self.assertEqual(lost["phase"], "reconciliation_required")
        self.assertEqual(lost["uncertainty"], "owner_lost")
        with self.assertRaises(protocol.Refusal):
            f.acquire()
        with self.assertRaises(protocol.Refusal):
            f.enroll()  # explicit ceremony cannot replace an existing owner

    def test_incomplete_preparing_is_not_an_automatic_clean_abort(self):
        f = self.fixture
        f.enroll()
        ticket = f.acquire()
        original_save = lc.PrivateFiles.save
        def interrupted_save(files, name, value):
            if name == "operation.json" and value["lease"] and value["lease"]["stage"] == "preparing":
                raise SystemExit("injected process interruption after durable PREPARING")
            return original_save(files, name, value)
        with mock.patch.object(lc.PrivateFiles, "save", interrupted_save), self.assertRaises(SystemExit):
            f.prepare(ticket)
        interrupted = dict(ticket, revision=f.status()["revision"])
        with self.assertRaisesRegex(protocol.Refusal, "incomplete"):
            f.owner(interrupted, "abort")
        self.assertEqual(f.status()["phase"], "reconciliation_required")

    def test_topology_excludes_config_mapping_and_transient_plane_ids(self):
        f = self.fixture
        before = lc.topology_digest(f.inventory("linux", "kms", "nvenc"))
        f.rows[1]["planes"] = [9999]
        after = lc.topology_digest(f.inventory("linux", "kms", "nvenc", device_type="cuda"))
        self.assertEqual(before, after)
        f.rows.append(dict(card="card0", connector="HDMI-A-2", connector_id=610, crtc=0,
                           connected=False, mode=None, planes=[], rotation=0, gpu_nvidia=False))
        self.assertNotEqual(before, lc.topology_digest(f.inventory("linux", "kms", "nvenc")))
        cards = f.inventory("linux", "kms", "nvenc", device_type="cuda")
        self.assertEqual(cards[1]["mapping_reason"], "ambiguous_capture_name")

    def test_desktop_derivation_hooks_ambiguity_and_receipt_protection(self):
        f = self.fixture
        content = f.conf.read_bytes()
        desktop = lc.inspected_desktop(host.settings(f.manifest), content, f.conf.parent)
        self.assertEqual(desktop.app_id, f.desktop_id())
        for app in ({"name": "Desktop", "cmd": "touch /tmp/not-executed"},
                    {"name": "Desktop", "prep-cmd": [{"do": "xrandr"}]},
                    {"name": "Desktop", "detached": ["anything"]}):
            f.apps.write_text(json.dumps({"env": {}, "apps": [app]}))
            with self.assertRaises(protocol.Refusal):
                f.enroll()
        f.apps.write_text(json.dumps({"env": {}, "apps": [{"name": "Desktop"}]}))
        f.enroll()
        f.prepare()
        receipt = f.state / "start-receipt.json"
        receipt.chmod(0o644)
        with self.assertRaises(protocol.Refusal):
            f.status()

    def test_ordinary_capture_markers_preserve_owned_noop_proof(self):
        f = self.fixture
        f.enroll()
        active = f.active()
        row = dict(f.transport.entries[-1])
        row["MESSAGE"] = runtime_fixture.prefixed("Screencasting with KMS")
        row["__CURSOR"] = "ordinary-capture-after-launch"
        row["__MONOTONIC_TIMESTAMP"] = str(int(row["__MONOTONIC_TIMESTAMP"]) + 1)
        f.transport.entries.append(row)
        resumed = f.owner(active, "resume")
        self.assertEqual(resumed["phase"], "reconnecting")
        self.assertEqual(f.owner(resumed, "observe_active")["phase"], "active")
        self.assertEqual(f.transport.restarts, 1)

    def test_early_client_quit_and_generation_change_cannot_release(self):
        f = self.fixture
        f.enroll()
        active = f.active()
        f.app = 0  # A premature --quit-after cleared Desktop BEFORE our intent.
        with self.assertRaises(protocol.Refusal):
            f.owner(active, "begin_release")
        self.assertEqual(f.status()["phase"], "reconciliation_required")
        with tempfile.TemporaryDirectory() as root:
            other = Fixture(root)
            other.enroll()
            active = other.active()
            releasing = other.owner(active, "begin_release")
            other.app = 0
            other.transport.props["InvocationID"] = "9" * 32  # unexpected service generation
            other.transport.current_trace()
            with self.assertRaises(protocol.Refusal):
                other.owner(releasing, "finish_release")
            self.assertEqual(other.status()["phase"], "reconciliation_required")

    def test_target_changes_during_readiness_and_duplicate_native_ids_refuse(self):
        f = self.fixture
        f.enroll()
        f.transport.on_restart = lambda _: setattr(f, "endpoint_id", "11223344-1234-4321-abcd-0123456789ab")
        with self.assertRaises(protocol.Refusal):
            f.prepare()
        status = rs.ReservationStore(f.state, rs.HostIdentity(lc.canonical_uuid(HOST), 47989)).status()
        self.assertEqual(status.phase, rs.Phase.RECONCILIATION_REQUIRED)
        self.assertFalse((f.state / "start-receipt.json").exists())
        with tempfile.TemporaryDirectory() as root:
            other = Fixture(root)
            other.enroll()
            other.rows[0]["connector_id"] = 601
            with self.assertRaisesRegex(protocol.Refusal, "connector"):
                other.prepare()
            self.assertEqual(other.status()["phase"], "reconciliation_required")

    def test_default_output_without_existing_receipt_requires_a_real_restart(self):
        f = self.fixture
        f.enroll()
        inode = f.conf.stat().st_ino
        prepared = f.prepare(display=FIRST)
        self.assertTrue(prepared["restarted"])
        self.assertEqual(f.transport.restarts, 1)
        self.assertEqual(f.conf.stat().st_ino, inode)  # config no-op; startup proof was absent
        self.assertEqual(f.conf.read_bytes(), INITIAL)
        self.assertTrue((f.state / "start-receipt.json").exists())

    def test_stock_title_aliases_refuse_before_authority_in_both_orders(self):
        f = self.fixture
        for alias in ("desktop", "DESKTOP", "des\u212atop"):
            for first in (True, False):
                with self.subTest(alias=alias, alias_first=first), tempfile.TemporaryDirectory() as root:
                    case = Fixture(root)
                    desktop = {"name": "Desktop", "cmd": "", "image-path": "desktop.png"}
                    other = {"name": alias, "cmd": "marker-command-never-executed", "image-path": "desktop.png"}
                    apps = [other, desktop] if first else [desktop, other]
                    self.assertEqual([a["name"].lower() for a in apps], ["desktop", "desktop"])
                    case.apps.write_text(json.dumps({"env": {}, "apps": apps}))
                    with self.assertRaises(protocol.Refusal) as error:
                        case.enroll()
                    self.assertEqual(error.exception.code, "desktop_unqualified")
                    self.assertEqual(case.transport.restarts, 0)
                    self.assertFalse((case.state / "reservation.json").exists())
        # QString::toLower is not case folding: long-s stays long-s, unlike
        # Python casefold. It must not spuriously alias the ASCII CLI target.
        f.apps.write_text(json.dumps({"env": {}, "apps": [
            {"name": "De\u017fktop", "cmd": "not-executed"}, {"name": "Desktop", "cmd": ""}]}))
        self.assertNotEqual("De\u017fktop".lower(), "desktop")
        self.assertEqual(f.enroll()["phase"], "available")

    def test_selecting_stale_menu_owner_refresh_keeps_capability(self):
        f = self.fixture
        f.enroll()
        ticket = f.acquire()
        f.rows[1]["mode"] = (2560, 1440, 120, 1)
        with self.assertRaises(protocol.Refusal) as error:
            f.prepare(ticket)
        self.assertEqual(error.exception.code, "stale_snapshot")
        refreshed = f.owner(ticket, "refresh")
        self.assertEqual(refreshed["phase"], "selecting")
        self.assertEqual(refreshed["capability"], ticket["capability"])
        self.assertNotEqual(refreshed["snapshot"], ticket["snapshot"])
        with self.assertRaises(protocol.Refusal):
            f.owner(ticket, "abort")  # old revision cannot cancel the refreshed owner
        prepared = f.prepare(refreshed)
        self.assertEqual(prepared["display"]["width"], 2560)
        self.assertEqual(f.transport.restarts, 1)

    def test_selecting_escape_after_hotplug_is_clean_without_external_work(self):
        f = self.fixture
        f.enroll()
        ticket = f.acquire()
        f.rows[1]["connected"] = False
        cancelled = f.owner(ticket, "abort")
        self.assertEqual(cancelled["phase"], "available")
        self.assertEqual(f.transport.restarts, 0)
        self.assertEqual(f.conf.read_bytes(), INITIAL)
        stored = rs.ReservationStore(f.state, rs.HostIdentity(lc.canonical_uuid(HOST), 47989)).status()
        self.assertEqual(cancelled["sampling"], "ownership")
        self.assertEqual(stored.binding.config_digest, config.digest(INITIAL))
        # No fresh topology claim: availability keeps the historical selection;
        # next acquire observes the actual new topology before use.
        self.assertNotEqual(stored.binding.topology_digest, lc.topology_digest(f.inventory("linux", "kms", "nvenc")))
        next_ticket = f.acquire()
        def unavailable(_):
            raise protocol.Refusal("backend_unavailable", "lost native access during untouched selection")
        f.inventory_hook = unavailable
        self.assertEqual(f.owner(next_ticket, "abort")["phase"], "available")

    def test_owned_release_ignores_unrelated_topology_before_and_after_cancel(self):
        f = self.fixture
        f.enroll()
        active = f.active()
        f.rows[0]["mode"] = (2560, 1440, 120, 1)
        releasing = f.owner(active, "begin_release")
        self.assertEqual(releasing["phase"], "releasing")
        f.app = 0  # source-producible successful authenticated /cancel effect
        f.rows[0]["mode"] = (1920, 1080, 50, 1)
        closed = f.owner(releasing, "finish_release")
        self.assertEqual(closed["phase"], "available")
        self.assertEqual(f.transport.restarts, 1)

    def test_owned_close_and_status_work_without_native_or_capture_proof(self):
        f = self.fixture
        f.enroll()
        active = f.active()
        def unavailable(_):
            raise protocol.Refusal("backend_unavailable", "injected lost native display access")
        f.inventory_hook = unavailable
        f.transport.entries = []  # journal/probe unavailable; generation/unit/config remain known
        status = f.status()
        self.assertEqual(status["phase"], "active")
        self.assertNotIn("screens", status)  # status is ownership, not a fabricated fresh menu
        releasing = f.owner(active, "begin_release")
        self.assertEqual(releasing["phase"], "releasing")
        f.app = 0
        closed = f.owner(releasing, "finish_release")
        self.assertEqual(closed["phase"], "available")
        self.assertEqual(f.status()["phase"], "available")

    def test_selected_capture_loss_can_close_but_cannot_authorize_resume(self):
        f = self.fixture
        f.enroll()
        active = f.active()
        f.rows[1].update(connected=False, mode=None, planes=[])
        self.assertEqual(f.status()["phase"], "active")
        releasing = f.owner(active, "begin_release")
        f.app = 0
        self.assertEqual(f.owner(releasing, "finish_release")["phase"], "available")
        with tempfile.TemporaryDirectory() as root:
            other = Fixture(root)
            other.enroll()
            active = other.active()
            other.rows[1].update(connected=False, mode=None, planes=[])
            with self.assertRaises(protocol.Refusal):
                other.owner(active, "resume")
            self.assertEqual(other.status()["phase"], "active")  # rejected before new resume intent
            releasing = other.owner(active, "begin_release")
            other.app = 0
            self.assertEqual(other.owner(releasing, "finish_release")["phase"], "available")

    def test_selecting_refresh_and_abort_still_refuse_changed_configuration(self):
        for action in ("refresh", "abort"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as root:
                f = Fixture(root)
                f.enroll()
                ticket = f.acquire()
                f.conf.write_bytes(INITIAL + b"# changed by operator\n")
                with self.assertRaises(protocol.Refusal):
                    f.owner(ticket, action)
                self.assertEqual(f.status()["phase"], "reconciliation_required")
                self.assertEqual(f.transport.restarts, 0)
                self.assertEqual(f.conf.read_bytes(), INITIAL + b"# changed by operator\n")

    def test_owned_cleanup_keeps_actual_runtime_policy_and_definition_guards(self):
        for change in ("NeedDaemonReload", "definition"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as root:
                f = Fixture(root)
                f.enroll()
                active = f.active()
                if change == "NeedDaemonReload":
                    f.transport.props["NeedDaemonReload"] = "yes"
                else:
                    f.apps.write_text(json.dumps({"env": {}, "apps": [{"name": "Desktop", "cmd": "new-command"}]}))
                with self.assertRaises(protocol.Refusal) as error:
                    f.owner(active, "begin_release")
                self.assertEqual(error.exception.code, "unit_unowned" if change == "NeedDaemonReload" else "desktop_unqualified")
                status = rs.ReservationStore(f.state, rs.HostIdentity(lc.canonical_uuid(HOST), 47989)).status()
                self.assertEqual(status.phase, rs.Phase.RECONCILIATION_REQUIRED)
                self.assertEqual(f.transport.restarts, 1)
                self.assertEqual(f.app, f.desktop_id())
                with self.assertRaises(protocol.Refusal):
                    f.status()  # ownership sampling does not bypass either guard

    def test_connector_recreation_uses_owned_reprobe_instead_of_false_noop(self):
        f = self.fixture
        f.enroll()
        prepared = f.prepare()
        f.owner(prepared, "abort")
        f.rows[1]["connector_id"] = 888
        ticket = f.acquire()
        self.assertTrue((f.state / "start-receipt.json").exists())
        prepared = f.prepare(ticket)
        self.assertTrue(prepared["restarted"])
        self.assertEqual(prepared["capability"], ticket["capability"])
        self.assertEqual(f.transport.restarts, 2)
        receipt = json.loads((f.state / "start-receipt.json").read_bytes())
        proof = f.runtime.observe(receipt=lc.receipt_from_json(receipt["receipt"]))
        self.assertEqual(proof.capture.probe.connector_ids, (888,))

    def test_filter_dependent_inactive_alias_is_unverified_preparation_intent(self):
        f = self.fixture
        f.conf.write_bytes(INITIAL.replace(b"encoder = nvenc\r\n", b"").replace(b"eDP-1", b"DP-1"))
        f.rows = [dict(card="card0", connector="DP-1", connector_id=507, crtc=0, connected=False,
                       mode=None, planes=[], rotation=0, gpu_nvidia=False),
                  dict(card="card1", connector="DP-1", connector_id=601, crtc=22, connected=True,
                       mode=(3840, 2160, 60, 1), planes=[201], rotation=0, gpu_nvidia=True)]
        f.transport.current_trace()
        menu = f.rpc("list")
        self.assertEqual(len(menu["screens"]), 1)
        self.assertTrue(menu["screens"][0]["selectable"])
        self.assertFalse(menu["screens"][0]["capture_candidate"])
        self.assertEqual(menu["screens"][0]["selector_mapping"], "unverified")
        f.enroll()
        ticket = f.acquire()
        prepared = f.prepare(ticket, display="kms:card1:DP-1")
        self.assertTrue(prepared["launch_authorized"])
        self.assertEqual(prepared["capability"], ticket["capability"])
        self.assertEqual(prepared["display"]["selector_mapping"], "mapped")
        self.assertEqual(f.transport.restarts, 1)
        self.assertEqual(len(f.inventory("linux", "kms", "", device_type="cuda").topology_facts), 2)

    def test_failed_native_refresh_composes_with_escape_without_phase_poisoning(self):
        for recover_native in (False, True):
            with self.subTest(recovered=recover_native), tempfile.TemporaryDirectory() as root:
                f = Fixture(root)
                f.enroll()
                ticket = f.acquire()
                before = (f.state / "reservation.json").read_bytes()
                operation = (f.state / "operation.json").read_bytes()
                def unavailable(_):
                    raise protocol.Refusal("backend_unavailable", "injected native-only refresh failure")
                f.inventory_hook = unavailable
                with self.assertRaises(protocol.Refusal) as error:
                    f.owner(ticket, "refresh")
                self.assertEqual(error.exception.code, "backend_unavailable")
                self.assertEqual((f.state / "reservation.json").read_bytes(), before)
                self.assertEqual((f.state / "operation.json").read_bytes(), operation)
                if recover_native:
                    f.inventory_hook = None
                self.assertEqual(f.owner(ticket, "abort")["phase"], "available")
                self.assertEqual(f.transport.restarts, 0)
                self.assertEqual(f.conf.read_bytes(), INITIAL)

    def test_failed_resume_preflight_composes_with_owned_quit(self):
        for failure in ("native", "selected_output"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as root:
                f = Fixture(root)
                f.enroll()
                active = f.active()
                before = (f.state / "reservation.json").read_bytes()
                operation = (f.state / "operation.json").read_bytes()
                if failure == "native":
                    def unavailable(_):
                        raise protocol.Refusal("backend_unavailable", "injected native-only resume failure")
                    f.inventory_hook = unavailable
                else:
                    f.rows[1].update(connected=False, mode=None, planes=[])
                with self.assertRaises(protocol.Refusal):
                    f.owner(active, "resume")
                self.assertEqual((f.state / "reservation.json").read_bytes(), before)
                self.assertEqual((f.state / "operation.json").read_bytes(), operation)
                releasing = f.owner(active, "begin_release")
                f.app = 0
                self.assertEqual(f.owner(releasing, "finish_release")["phase"], "available")
                self.assertEqual(f.transport.restarts, 1)

    def test_completed_preparation_failed_launch_preflight_can_cleanly_abort(self):
        for failure in ("native", "topology"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as root:
                f = Fixture(root)
                f.enroll()
                prepared = f.prepare()
                before = (f.state / "reservation.json").read_bytes()
                if failure == "native":
                    def unavailable(_):
                        raise protocol.Refusal("backend_unavailable", "injected prelaunch native failure")
                    f.inventory_hook = unavailable
                else:
                    f.rows[1].update(connected=False, mode=None, planes=[])
                with self.assertRaises(protocol.Refusal):
                    f.owner(prepared, "launch")
                self.assertEqual((f.state / "reservation.json").read_bytes(), before)
                self.assertEqual(f.owner(prepared, "abort")["phase"], "available")
                self.assertEqual(f.app, 0)
                self.assertEqual(f.transport.restarts, 1)

    def test_probe_only_change_during_preflight_preserves_known_active_owner(self):
        f = self.fixture
        f.enroll()
        active = f.active()
        before = (f.state / "reservation.json").read_bytes()
        def lose_probe(_):
            f.transport.entries = []  # journal window no longer contains completed capture proof
        f.inventory_hook = lose_probe
        with self.assertRaises(protocol.Refusal) as error:
            f.owner(active, "resume")
        self.assertEqual(error.exception.code, "capture_changed")
        self.assertEqual((f.state / "reservation.json").read_bytes(), before)
        releasing = f.owner(active, "begin_release")
        f.app = 0
        self.assertEqual(f.owner(releasing, "finish_release")["phase"], "available")
        self.assertEqual(f.transport.restarts, 1)

    def test_capture_failure_rechecks_ownership_after_native_side_effects(self):
        for phase in ("selecting", "active"):
            for change in ("generation", "config", "definition", "endpoint", "app", "policy", "owner_lost"):
                with self.subTest(phase=phase, change=change), tempfile.TemporaryDirectory() as root:
                    f = Fixture(root)
                    f.enroll()
                    ticket = f.acquire() if phase == "selecting" else f.active()
                    before = rs.ReservationStore(f.state, rs.HostIdentity(lc.canonical_uuid(HOST), 47989)).status()
                    def change_then_refuse(_):
                        if change == "generation":
                            f.transport.props["InvocationID"] = "9" * 32
                            f.transport.current_trace()
                        elif change == "config":
                            f.conf.write_bytes(f.conf.read_bytes() + b"# intervening config edit\n")
                        elif change == "definition":
                            f.assets.joinpath("desktop.png").write_bytes(PNG + b"changed identity")
                        elif change == "endpoint":
                            f.endpoint_id = "11223344-1234-4321-abcd-0123456789ab"
                        elif change == "app":
                            f.app = 12345
                        elif change == "policy":
                            f.transport.props["NeedDaemonReload"] = "yes"
                        else:
                            rs.ReservationStore(f.state, before.host).require_reconciliation(before.revision, rs.Uncertainty.OWNER_LOST)
                        raise protocol.Refusal("backend_unavailable", "native refused after ownership changed")
                    f.inventory_hook = change_then_refuse
                    with self.assertRaises(protocol.Refusal):
                        f.owner(ticket, "refresh" if phase == "selecting" else "resume")
                    stored = rs.ReservationStore(f.state, before.host).status()
                    self.assertEqual(stored.phase, rs.Phase.RECONCILIATION_REQUIRED)
                    self.assertIsNotNone(json.loads((f.state / "reservation.json").read_bytes())["owner_digest"])
                    self.assertEqual(f.transport.restarts, 0 if phase == "selecting" else 1)
                    f.inventory_hook = None
                    latest = dict(ticket, revision=stored.revision)
                    with self.assertRaises(protocol.Refusal):
                        f.owner(latest, "abort" if phase == "selecting" else "begin_release")
                    self.assertEqual(rs.ReservationStore(f.state, before.host).status().phase, rs.Phase.RECONCILIATION_REQUIRED)

    def test_arbitrary_refusal_is_not_classified_as_capture_only(self):
        f = self.fixture
        f.enroll()
        ticket = f.acquire()
        def unrelated(_):
            raise protocol.Refusal("unclassified_failure", "not a native capture contract refusal")
        f.inventory_hook = unrelated
        with self.assertRaises(protocol.Refusal):
            f.owner(ticket, "refresh")
        self.assertEqual(f.status()["phase"], "reconciliation_required")

    def test_incomplete_preparation_and_unobserved_attempts_stay_owned(self):
        for pending in ("preparing", "resume"):
            with self.subTest(pending=pending), tempfile.TemporaryDirectory() as root:
                f = Fixture(root)
                f.enroll()
                if pending == "preparing":
                    ticket = f.acquire()
                    original_save = lc.PrivateFiles.save
                    def interrupt(files, name, value):
                        if name == "operation.json" and value["lease"] and value["lease"]["stage"] == "preparing":
                            raise SystemExit("interrupt before completed preparation proof")
                        return original_save(files, name, value)
                    with mock.patch.object(lc.PrivateFiles, "save", interrupt), self.assertRaises(SystemExit):
                        f.prepare(ticket)
                    ticket = dict(ticket, revision=f.status()["revision"])
                    action = "launch"
                else:
                    ticket = f.owner(f.active(), "resume")
                    action = "observe_active"
                def unavailable(_):
                    raise protocol.Refusal("backend_unavailable", "native unavailable with unresolved external intent")
                f.inventory_hook = unavailable
                with self.assertRaises(protocol.Refusal):
                    f.owner(ticket, action)
                stored = rs.ReservationStore(f.state, rs.HostIdentity(lc.canonical_uuid(HOST), 47989)).status()
                self.assertEqual(stored.phase, rs.Phase.RECONCILIATION_REQUIRED)
                self.assertIsNotNone(json.loads((f.state / "reservation.json").read_bytes())["owner_digest"])
                latest = dict(ticket, revision=stored.revision)
                f.inventory_hook = None
                with self.assertRaises(protocol.Refusal):
                    f.owner(latest, "abort")

    def test_connecting_capture_settling_is_retryable_without_poisoning(self):
        # A controlled launch makes the vendor re-run its capture/encoder probe;
        # the bounded journal window can read that probe mid-write. While the same
        # owner attempt is fresh and unconfirmed, that read-only transient is a
        # retryable launch_unconfirmed, never an ownership block. Non-capture
        # refusals in the same window still block and retain.
        with tempfile.TemporaryDirectory() as root:
            f = Fixture(root)
            f.enroll()
            attempted = f.owner(f.prepare(), "launch")
            before = (f.state / "reservation.json").read_bytes()
            operation = (f.state / "operation.json").read_bytes()
            def settling(_):
                raise protocol.Refusal("backend_unavailable", "injected capture probe mid-write")
            f.inventory_hook = settling
            with self.assertRaises(protocol.Refusal) as error:
                f.owner(attempted, "observe_active")
            self.assertEqual(error.exception.code, "launch_unconfirmed")
            self.assertEqual((f.state / "reservation.json").read_bytes(), before)
            self.assertEqual((f.state / "operation.json").read_bytes(), operation)
            self.assertEqual(f.status()["phase"], "connecting")
            f.inventory_hook = None
            f.app = attempted["desktop_app_id"]
            active = f.owner(attempted, "observe_active")
            self.assertEqual(active["phase"], "active")
            releasing = f.owner(active, "begin_release")
            f.app = 0
            self.assertEqual(f.owner(releasing, "finish_release")["phase"], "available")
        with tempfile.TemporaryDirectory() as root:
            f = Fixture(root)
            f.enroll()
            attempted = f.owner(f.prepare(), "launch")
            def unrelated(_):
                raise protocol.Refusal("unclassified_failure", "not a capture contract refusal")
            f.inventory_hook = unrelated
            with self.assertRaises(protocol.Refusal) as blocked:
                f.owner(attempted, "observe_active")
            self.assertNotEqual(blocked.exception.code, "launch_unconfirmed")
            self.assertEqual(f.status()["phase"], "reconciliation_required")

    def test_completed_preparation_cleanup_requires_actual_completion_record(self):
        f = self.fixture
        f.enroll()
        prepared = f.prepare()
        meta = json.loads((f.state / "operation.json").read_bytes())
        self.assertEqual(meta["lease"]["prepared_revision"], prepared["revision"])
        meta["lease"].pop("prepared_revision")
        (f.state / "operation.json").write_text(json.dumps(meta))
        with self.assertRaises(protocol.Refusal):
            f.owner(prepared, "abort")
        self.assertEqual(f.status()["phase"], "reconciliation_required")

    def test_unchecked_authority_flags_and_types_are_not_protocol_fields(self):
        f = self.fixture
        for fields in ({"force": True}, {"verified": True}, {"cancelled": True}, {"command": "restart"}, {"path": "/etc"}):
            with self.assertRaises(protocol.Refusal) as error:
                f.rpc("list", **fields)
            self.assertEqual(error.exception.code, "invalid_request")
        for action in ("acquire", "enroll", "prepare", "launch", "finish_release"):
            with self.assertRaises(protocol.Refusal):
                f.rpc(action, revision=True)
        self.assertEqual(f.transport.restarts, 0)


if __name__ == "__main__":
    unittest.main()
