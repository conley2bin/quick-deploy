#!/usr/bin/env python3
"""Private NSS/HOME/systemd/proc/endpoint fixtures; no live recovery or host I/O.

Both retained stages originate in the actual coordinator. Real subprocesses are
forbidden except private synthetic deb construction/reading and an exact Git
source-object read to execute the byte-identical baseline dispatcher.
"""
from dataclasses import asdict
import io
import json
import multiprocessing as mp
import os
from pathlib import Path
import shutil
import subprocess
import sys
import types
import unittest
from unittest import mock

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "display-control"), str(Path(__file__).resolve().parent)]
import display_install as di
import display_lifecycle as lf
import display_runtime_linux as rf
import configuration as cfg
import host
import install_controller as ic
import lifecycle as lc
from protocol import Refusal
import reservation as rs
import runtime_linux as rt


def new_boot(f, boot="3" * 32, pid=80):
    t = f.t
    old, new = Path("/proc") / t.props["MainPID"], Path("/proc") / str(pid)
    for suffix in ("status", "cmdline", "cgroup", "environ"):
        t.put(new / suffix, t.files[old / suffix])
    fields = ["S"] + ["0"] * 18 + ["900"] + ["0"] * 30
    t.put(new / "stat", (f"{pid} (sunshine worker)) " + " ".join(fields) + "\n").encode())
    t.files.pop(old / "stat", None); t.ids.pop(old / "stat", None)
    t.put(Path("/proc/sys/kernel/random/boot_id"), (str(ic.uuid.UUID(boot)) + "\n").encode(), uid=0)
    t.props.update(MainPID=str(pid), InvocationID=f"{pid:032x}", ExecMainStartTimestampMonotonic="9000000")
    base = lf.FixtureTransport.current_trace.__get__(t, lf.FixtureTransport)
    def trace():
        base()
        for row in t.entries:
            row["_BOOT_ID"] = boot
    t.current_trace = trace
    trace()


class RecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Source-only Git read, never a host command. Run the exact baseline
        # installed dispatcher function, not a rewritten simulation of it.
        import tempfile
        with tempfile.TemporaryDirectory(prefix="qd-old-source-") as home:
            raw = subprocess.check_output(("/usr/bin/git", "show", "66b94bd83b9c14185a24abc1b3af23502b1d2e21:sunshine-moonlight/display-control/install_controller.py"),
                                          cwd=di.MODULE, env={"PATH": "/usr/bin:/bin", "HOME": home, "GIT_CONFIG_NOSYSTEM": "1"}, timeout=10)
        if cfg.digest(raw) != "3e2adaf9719a524310b4c28f4185d5d254013b7599d23541590422a449e1832b":
            raise AssertionError("baseline dispatcher source differs")
        cls.old_controller = types.ModuleType("qd_baseline_install_controller")
        cls.old_controller.__file__ = str(di.MODULE / "display-control/install_controller.py")
        sys.modules[cls.old_controller.__name__] = cls.old_controller
        exec(compile(raw, cls.old_controller.__file__, "exec"), cls.old_controller.__dict__)

    setUp = di.InstallTests.setUp
    snapshot = di.OwnedReleaseReconciliationTests.snapshot

    def call(self, f, ticket, action, **fields):
        return f.rpc(action, revision=ticket["revision"], capability=ticket["capability"], **fields)

    def acquire(self, f):
        menu = f.rpc("list")
        return f.rpc("acquire", revision=menu["revision"], snapshot=menu["snapshot"])

    def failed(self, f=None, *, shape="restart_intent", receipt="current", reboot=False):
        f = f or self.fixture
        f.install(publish=True)
        f.rpc("enroll", revision=f.rpc("status")["revision"])
        if receipt == "historical":
            owner = self.acquire(f)
            prepared = self.call(f, owner, "prepare", snapshot=owner["snapshot"], display_id=lf.FIRST)
            self.call(f, prepared, "abort")
            new_boot(f)
            with self.assertRaises(Refusal):
                self.acquire(f)
            f.rpc("enroll", revision=f.rpc("status")["revision"])
        owner = self.acquire(f)
        f.failed_owner = owner
        if receipt in ("absent", "historical"):
            with mock.patch.object(lc.HostController, "issue_start", side_effect=Refusal("original_prepare_refusal", "private injected original")):
                with self.assertRaises(Refusal) as exc:
                    self.call(f, owner, "prepare", snapshot=owner["snapshot"],
                              display_id=lf.FIRST if shape == "restart_intent" else lf.SECOND)
            self.assertEqual(exc.exception.code, "original_prepare_refusal")
        else:
            original = lc.HostController.check_selected
            def fail_final(controller, *args):
                if f.t.restarts:
                    raise Refusal("capture_correspondence_unproven", "injected after genuine receipt")
                return original(controller, *args)
            with mock.patch.object(lc.HostController, "check_selected", fail_final), self.assertRaises(Refusal):
                self.call(f, owner, "prepare", snapshot=owner["snapshot"],
                          display_id=lf.FIRST if shape == "restart_intent" else lf.SECOND)
        meta = json.loads((f.controller / "state/operation.json").read_bytes())
        self.assertEqual(meta["lease"]["stage"], shape)
        status = json.loads((f.controller / "state/reservation.json").read_bytes())
        self.assertEqual((status["phase"], status["uncertainty"], status["desktop"], status["attempt_revision"], status["active_observed"]),
                         ("reconciliation_required", "runtime_changed", None, None, False))
        if reboot:
            new_boot(f, "4" * 32, 90)
        return f

    def recover(self, f, authorize=lambda: True):
        def stream():
            return types.SimpleNamespace(buffer=io.BytesIO(), isatty=lambda: True)
        stdin, stdout, stderr = stream(), stream(), stream()
        with mock.patch.object(ic.pwd, "getpwuid", return_value=types.SimpleNamespace(pw_dir=str(f.f.home))), \
                mock.patch.object(rt, "LocalTransport", return_value=f.t), \
                mock.patch.object(host, "collect", side_effect=AssertionError("idle retirement must not depend on native capture/enumeration")), \
                mock.patch.object(host, "serverinfo", side_effect=f.f.endpoint):
            rc = ic.rpc_main(enroll=True, manifest=f.manifest(), stdin=stdin, stdout=stdout, stderr=stderr,
                             interactive=authorize, handle=lambda *a, **k: (_ for _ in ()).throw(AssertionError("exceptional local path must not call nested handle")))
        return rc, json.loads(stdout.buffer.getvalue())

    def positive(self, f):
        state_dir = f.controller / "state"
        before = {name: ic.record_identity(state_dir / name) for name in ic.UNLAUNCHED_RECORDS if (state_dir / name).exists()}
        enrollment = json.loads((state_dir / "operation.json").read_bytes())["enrollment"]
        original = json.loads((state_dir / "reservation.json").read_bytes())
        disk = f.f.conf.read_bytes()
        locks = {p: ic.record_identity(p) for p in (f.controller / ".install.lock", state_dir / "operation.lock", state_dir / "reservation.lock")}
        restarts = f.t.restarts
        rc, reply = self.recover(f)
        self.assertEqual((rc, reply.get("ok"), reply.get("phase"), reply.get("revision")), (0, True, "available", original["revision"] + 1), reply)
        transaction = max((json.loads(p.read_bytes()) for p in state_dir.glob("unlaunched-transition-*.bak")),
                          key=lambda value: value["evidence"]["reservation_revision"])
        for name, identity in before.items():
            archive = state_dir / (name + "." + transaction["id"] + ".bak")
            self.assertEqual(ic.record_identity(archive), identity)
            if name in ("start-receipt.json", "prepare-backup.conf"):
                self.assertEqual((state_dir / name).read_bytes(), archive.read_bytes())
        for p, identity in locks.items():
            self.assertEqual(ic.record_identity(p), identity)
        self.assertEqual(json.loads((state_dir / "operation.json").read_bytes()), {"schema": 1, "enrollment": enrollment, "lease": None})
        state = json.loads((state_dir / "reservation.json").read_bytes())
        current = rt.LinuxRuntime(host.settings(f.manifest(), "linux").runtime, f.t).observe()
        self.assertEqual(state["binding"]["generation"], asdict(lc.service_generation(current.generation)))
        self.assertEqual(state["binding"]["config_digest"], cfg.digest(disk))
        self.assertEqual((state["owner_digest"], state["desktop"], state["attempt_revision"], state["active_observed"], state["uncertainty"]), (None, None, None, False, None))
        self.assertEqual((f.f.conf.read_bytes(), f.t.restarts), (disk, restarts))
        self.assertFalse(any("daemon-reload" in argv or "stop" in argv or "quit" in argv for argv, _, _ in f.t.calls))
        return transaction

    def test_both_actual_stages_receipt_absent_current_historical_and_reboot(self):
        for shape in ("restart_intent", "rolled_back_requires_reconciliation"):
            for receipt in ("absent", "current", "historical"):
                for reboot in (False, True):
                    with self.subTest(shape=shape, receipt=receipt, reboot=reboot):
                        f = self.failed(di.Fixture(Path(self.temp.name) / f"{shape}-{receipt}-{reboot}"), shape=shape, receipt=receipt, reboot=reboot)
                        transaction = self.positive(f)
                        if shape != "restart_intent":
                            self.assertEqual(transaction["evidence"]["config"]["loaded_digest"], None)
                            self.assertEqual(transaction["evidence"]["config"]["file"]["sha256"], transaction["records"]["prepare-backup.conf"]["sha256"])
                        snap = self.snapshot(f)
                        self.assertEqual(self.recover(f, lambda: (_ for _ in ()).throw(AssertionError("no reenrollment")))[0], 0)
                        self.assertEqual(self.snapshot(f), snap)

    def test_historical_structural_parse_does_not_admit_wrong_context(self):
        cases = ("host", "build", "unit", "uid", "root", "path", "environment", "desktop", "config", "fingerprint", "operation", "enrollment", "previous", "birth")
        for case in cases:
            with self.subTest(case=case):
                f = self.failed(di.Fixture(Path(self.temp.name) / case), shape="rolled_back_requires_reconciliation", receipt="historical")
                path = f.controller / "state/start-receipt.json"
                value = json.loads(path.read_bytes())
                receipt, issuance = value["receipt"], value["issuance"]
                lease = json.loads((f.controller / "state/operation.json").read_bytes())["lease"]
                if case == "host": receipt["host_uniqueid"] = "11223344-1234-4321-abcd-0123456789ab"
                elif case == "build": receipt["qualified_build"]["executable_sha256"] = "f" * 64
                elif case == "unit": receipt["unit_digest"] = "f" * 64
                elif case == "uid": receipt["generation"]["uid"] = 1234
                elif case == "root": receipt["config_root"] += "/foreign"
                elif case == "path": receipt["config_path"] += ".foreign"
                elif case == "environment": receipt["manager_environment_digest"] = "f" * 64
                elif case == "desktop": value["desktop_digest"] = "f" * 64
                elif case == "config": receipt["config"]["sha256"] = "f" * 64
                elif case == "fingerprint": receipt["config"]["identity"]["size"] = True
                elif case == "operation": issuance["operation"] = lease["restart_id"]
                elif case == "enrollment": issuance["enrollment_revision"] = 999999
                elif case == "previous": issuance["previous_generation"]["start_monotonic_usec"] = receipt["generation"]["start_monotonic_usec"]
                elif case == "birth": receipt["generation"]["invocation_id"] = "bad"
                path.write_bytes(ic.document(value))
                before = self.snapshot(f)
                rc, reply = self.recover(f)
                self.assertEqual(rc, 1, reply)
                self.assertEqual(self.snapshot(f), before)

    def test_no_launch_intent_owner_stage_and_config_fail_closed(self):
        cases = ("owner", "attempt", "active", "desktop", "stage", "extra", "missing", "restart", "birth", "topology", "before", "after", "backup", "disk", "selection", "enrollment", "busy", "proc_exe", "proc_env", "root", "argv", "unit", "binary", "old_alive", "old_restricted", "same_invocation")
        for case in cases:
            with self.subTest(case=case):
                f = self.failed(di.Fixture(Path(self.temp.name) / case), shape="rolled_back_requires_reconciliation", receipt="absent")
                folder = f.controller / "state"
                state, meta = json.loads((folder / "reservation.json").read_bytes()), json.loads((folder / "operation.json").read_bytes())
                lease = meta["lease"]
                if case == "owner": lease["owner_digest"] = "f" * 64
                elif case == "attempt": state["attempt_revision"] = state["revision"]; state["desktop"] = lease["selection_desktop"]
                elif case == "active": state["attempt_revision"] = state["revision"]; state["desktop"] = lease["selection_desktop"]; state["active_observed"] = True
                elif case == "desktop": state["desktop"] = lease["selection_desktop"]
                elif case == "stage": lease["stage"] = "config_written"
                elif case == "extra": lease["prepared_revision"] = 2
                elif case == "missing": del lease["selection_desktop"]
                elif case == "restart": lease["restart_id"] = "bad"
                elif case == "birth": lease["previous_generation"]["start_ticks"] += 1
                elif case == "topology": lease["topology"] = "f" * 64
                elif case == "before": lease["before_digest"] = "f" * 64
                elif case == "after": lease["after_digest"] = "f" * 64
                elif case == "backup": (folder / "prepare-backup.conf").write_bytes(b"unexplained")
                elif case == "disk": f.f.conf.write_bytes(f.f.conf.read_bytes() + b"# unexplained\n")
                elif case == "selection": lease["selection_desktop"]["app_id"] += 1
                elif case == "enrollment": meta["enrollment"]["revision"] = state["revision"]
                elif case == "busy": f.f.app = 123
                elif case == "proc_exe": f.t.exe_link = "/foreign"
                elif case == "proc_env": f.t.denied.add(Path("/proc") / f.t.props["MainPID"] / "environ")
                elif case == "root": f.t.put(Path("/proc") / f.t.props["MainPID"] / "environ", b"HOME=/foreign\0")
                elif case == "argv": f.t.put(Path("/proc") / f.t.props["MainPID"] / "cmdline", b"/usr/bin/sunshine\0other.conf\0")
                elif case == "unit": f.t.props["NeedDaemonReload"] = "yes"
                elif case == "binary": f.t.put(rt.EXECUTABLE, b"foreign", uid=0)
                elif case in ("old_alive", "old_restricted"):
                    old = lease["previous_generation"]
                    p = Path("/proc") / str(old["pid"]) / "stat"
                    fields = ["S"] + ["0"] * 18 + [str(old["start_ticks"])] + ["0"] * 30
                    f.t.put(p, (str(old["pid"]) + " (sunshine) " + " ".join(fields)).encode())
                    if case == "old_restricted": f.t.denied.add(p)
                elif case == "same_invocation": f.t.props["InvocationID"] = lease["previous_generation"]["invocation_id"]; f.t.current_trace()
                (folder / "reservation.json").write_bytes(ic.document(state)); (folder / "operation.json").write_bytes(ic.document(meta))
                before = self.snapshot(f)
                self.assertEqual(self.recover(f)[0], 1)
                self.assertEqual(self.snapshot(f), before)

    def test_dead_and_reused_exact_birth_and_normalized_prior_boot(self):
        for case in ("dead", "reused", "boot"):
            with self.subTest(case=case):
                f = self.failed(di.Fixture(Path(self.temp.name) / case), receipt="absent")
                folder = f.controller / "state"
                value = json.loads((folder / "operation.json").read_bytes())
                old = value["lease"]["previous_generation"]
                if case == "boot":
                    old["boot_id"] = str(ic.uuid.UUID(old["boot_id"]))
                    (folder / "operation.json").write_bytes(ic.document(value))
                    new_boot(f)
                else:
                    fields = ["Z" if case == "dead" else "S"] + ["0"] * 18 + [str(old["start_ticks"] + (case == "reused"))] + ["0"] * 30
                    f.t.put(Path("/proc") / str(old["pid"]) / "stat", (str(old["pid"]) + " (sunshine) " + " ".join(fields)).encode())
                self.positive(f)

    def test_every_later_endpoint_busy_refuses_before_availability(self):
        baseline = self.failed(receipt="absent")
        observations = []
        endpoint = baseline.f.endpoint
        baseline.f.endpoint = lambda *args: (observations.append(1) or endpoint(*args))
        self.assertEqual(self.recover(baseline)[0], 0)
        for at in range(1, len(observations) + 1):
            with self.subTest(endpoint_observation=at):
                f = self.failed(di.Fixture(Path(self.temp.name) / str(at)), shape="rolled_back_requires_reconciliation", receipt="absent")
                count, endpoint = [0], f.f.endpoint
                def busy(*args):
                    count[0] += 1
                    return lc.Endpoint(lc.canonical_uuid(lf.HOST), "BUSY", 123) if count[0] == at else endpoint(*args)
                f.f.endpoint = busy
                self.assertEqual(self.recover(f)[0], 1)
                live = (f.controller / "state/reservation.json")
                if live.exists() and live.read_bytes() != ic.RELEASE_BLOCKED:
                    self.assertNotEqual(json.loads(live.read_bytes())["phase"], "available")
                self.assertFalse(self.old_reply(f, "list").get("selection_ready"))

    def old_enroll(self, f):
        stdin = types.SimpleNamespace(buffer=io.BytesIO(), isatty=lambda: True)
        stdout = types.SimpleNamespace(buffer=io.BytesIO())
        stderr = types.SimpleNamespace(isatty=lambda: True)
        def handle(raw, **kwargs):
            return host.handle(raw, manifest=f.manifest(), runtime=rt.LinuxRuntime(host.settings(f.manifest(), "linux").runtime, f.t),
                               info=f.f.endpoint, enumerate_native=f.f.inventory, authorize_enrollment=kwargs["authorize_enrollment"])
        rc = self.old_controller.rpc_main(enroll=True, manifest=f.manifest(), handle=handle,
                                          interactive=lambda: True, stdin=stdin, stdout=stdout, stderr=stderr)
        return rc, json.loads(stdout.buffer.getvalue())

    def old_reply(self, f, action, **fields):
        # These host/lifecycle/reservation modules are byte-identical to HEAD,
        # the shim is unchanged, and runtime+metadata are only injected facts.
        for name in ("host.py", "lifecycle.py", "reservation.py", "protocol.py"):
            self.assertEqual((f.manifest().parent / name).read_bytes(), (di.MODULE / "display-control" / name).read_bytes())
        try:
            return f.rpc(action, **fields)
        except Refusal as exc:
            return {"ok": False, "error": {"code": exc.code}}

    def test_precommit_cuts_old_dispatcher_blocking_and_exact_resume(self):
        points = ("journal", "archive-reservation.json", "sentinel", "archive-operation.json", "archive-start-receipt.json", "archive-prepare-backup.conf", "new-operation.json", "blocked", "new-start-receipt.json", "new-prepare-backup.conf", "descriptor", "final-replace", "final-fsync")
        for shape in ("restart_intent", "rolled_back_requires_reconciliation"):
            for point in points:
                with self.subTest(shape=shape, point=point):
                    f = self.failed(di.Fixture(Path(self.temp.name) / shape / point), shape=shape)
                    original_rename, original_create, original_replace, original_fsync = ic.os.rename, ic.create, ic.os.replace, ic.os.fsync
                    original_write = lc.PrivateFiles.write
                    final = [False]
                    def rename(src, dst, **kw):
                        original_rename(src, dst, **kw)
                        if point == "archive-" + src: raise RuntimeError("death after archive rename")
                    def create(path, data, mode=0o600):
                        original_create(path, data, mode)
                        if ((point == "journal" and path.name.startswith("unlaunched-transition-"))
                                or (point == "descriptor" and path.name.startswith("unlaunched-committed-"))
                                or (point == "sentinel" and path.name == "reservation.json")
                                or point == "new-" + path.name):
                            raise RuntimeError("death after durable create")
                    def write(files, name, data, limit=lc.MAX_PRIVATE):
                        if name == "reservation.json" and rs._decode(data, rs.HostIdentity(lc.canonical_uuid(lf.HOST), 47989)).status.phase is rs.Phase.AVAILABLE:
                            final[0] = True
                        original_write(files, name, data, limit=limit)
                        if point == "blocked" and name == "reservation.json" and not final[0]:
                            raise RuntimeError("death after readable blocked state")
                    def replace(src, dst, **kw):
                        original_replace(src, dst, **kw)
                        if point == "final-replace" and final[0]: raise RuntimeError("death after atomic available replace")
                    def fsync(fd):
                        original_fsync(fd)
                        if point == "final-fsync" and final[0] and os.fstat(fd).st_ino == (f.controller / "state").stat().st_ino:
                            raise OSError("lost final directory fsync reply")
                    with mock.patch.object(ic.os, "rename", side_effect=rename), mock.patch.object(ic, "create", side_effect=create), \
                            mock.patch.object(lc.PrivateFiles, "write", write), mock.patch.object(ic.os, "replace", side_effect=replace), \
                            mock.patch.object(ic.os, "fsync", side_effect=fsync):
                        self.assertEqual(self.recover(f)[0], 1)
                    before = self.snapshot(f)
                    menu = self.old_reply(f, "list")
                    if point not in ("final-replace", "final-fsync"):
                        self.assertFalse(menu.get("selection_ready"))
                        self.assertFalse(self.old_reply(f, "acquire", revision=menu.get("revision", 1), snapshot=menu.get("snapshot", "a" * 64)).get("ok"))
                        self.assertFalse(self.old_reply(f, "enroll", revision=menu.get("revision", 1)).get("ok"))
                        self.assertEqual(self.old_enroll(f)[0], 1)
                        for action in ("refresh", "launch", "abort", "resume", "observe_active", "begin_release", "finish_release"):
                            self.assertFalse(self.old_reply(f, action, revision=menu.get("revision", 1), capability=f.failed_owner["capability"]).get("ok"))
                        self.assertEqual(self.snapshot(f), before)
                    else:
                        self.assertTrue(menu.get("selection_ready"))
                    rc, reply = self.recover(f, lambda: (_ for _ in ()).throw(AssertionError("journal confirmed already")))
                    self.assertEqual(rc, 0, reply)

    def test_committed_floor_lost_reply_later_lease_source_and_receipt(self):
        f = self.failed(shape="rolled_back_requires_reconciliation", receipt="historical")
        transaction = self.positive(f)
        folder = f.controller / "state"
        committed = (folder / "reservation.json").read_bytes()
        # A legitimate later preparation MUST create a new receipt because
        # the rollback disk digest does not attest the process's loaded bytes.
        owner = self.acquire(f)
        before = self.snapshot(f)
        self.assertEqual(self.recover(f)[0], 0)
        self.assertEqual(self.snapshot(f), before)
        f.t.failed_probe = False
        prepared = self.call(f, owner, "prepare", snapshot=owner["snapshot"], display_id=lf.FIRST)
        self.assertTrue(prepared["restarted"])
        receipt = (folder / "start-receipt.json").read_bytes()
        self.assertNotEqual(cfg.digest(receipt), transaction["records"]["start-receipt.json"]["sha256"])
        self.call(f, prepared, "abort")
        source = f.root / "later-source"
        shutil.copytree(f.source, source, ignore=shutil.ignore_patterns("__pycache__"))
        with (source / "host.py").open("a") as stream: stream.write("\n# later independent publication\n")
        f.source = source
        f.install()
        before = self.snapshot(f)
        self.assertEqual(self.recover(f)[0], 0)
        self.assertEqual(self.snapshot(f), before)
        # Real rollback below floor refuses, never republishing available.
        saved = (folder / "reservation.json").read_bytes()
        value = json.loads(committed); value["revision"] -= 1
        (folder / "reservation.json").write_bytes(ic.document(value))
        before = self.snapshot(f)
        self.assertEqual(self.recover(f)[0], 1)
        self.assertEqual(self.snapshot(f), before)
        (folder / "reservation.json").write_bytes(saved)

    def test_idle_uses_historical_topology_then_acquire_refreshes_native(self):
        f = self.failed(shape="rolled_back_requires_reconciliation", receipt="absent")
        folder = f.controller / "state"
        old = json.loads((folder / "reservation.json").read_bytes())["binding"]["topology_digest"]
        f.f.rows[0]["mode"] = (2560, 1440, 120, 1)
        self.positive(f)
        self.assertEqual(json.loads((folder / "reservation.json").read_bytes())["binding"]["topology_digest"], old)
        menu = f.rpc("list")
        self.assertNotEqual(menu["topology_digest"], old)
        owner = f.rpc("acquire", revision=menu["revision"], snapshot=menu["snapshot"])
        self.assertEqual(json.loads((folder / "reservation.json").read_bytes())["binding"]["topology_digest"], menu["topology_digest"])
        prepared = self.call(f, owner, "prepare", snapshot=owner["snapshot"], display_id=lf.FIRST)
        self.assertTrue(prepared["restarted"])
        self.assertEqual(prepared["display"]["width"], 2560)
        self.call(f, prepared, "abort")

    def test_after_config_with_real_change_and_false_rollback_stage(self):
        f = self.fixture
        f.install(publish=True)
        f.rpc("enroll", revision=f.rpc("status")["revision"])
        owner = self.acquire(f)
        original = cfg.replace_if_same
        count = [0]
        def no_rollback(*args, **kwargs):
            count[0] += 1
            if count[0] == 2: raise Refusal("rollback_unavailable", "injected")
            return original(*args, **kwargs)
        with mock.patch.object(lc.HostController, "issue_start", side_effect=Refusal("original_refusal", "injected")), \
                mock.patch.object(cfg, "replace_if_same", side_effect=no_rollback), self.assertRaises(Refusal):
            self.call(f, owner, "prepare", snapshot=owner["snapshot"], display_id=lf.SECOND)
        self.assertNotEqual(f.f.conf.read_bytes(), (f.controller / "state/prepare-backup.conf").read_bytes())
        self.positive(f)
        other = self.failed(di.Fixture(Path(self.temp.name) / "false-rollback"), receipt="absent")
        path = other.controller / "state/operation.json"
        meta = json.loads(path.read_bytes()); meta["lease"]["stage"] = "rolled_back_requires_reconciliation"; path.write_bytes(ic.document(meta))
        before = self.snapshot(other)
        self.assertEqual(self.recover(other)[0], 1)
        self.assertEqual(self.snapshot(other), before)

    def test_every_publication_fsync_failure_stays_blocked_or_classifies_commit(self):
        baseline = self.failed(receipt="absent")
        observed = []
        original = ic.os.fsync
        def observe(fd):
            observed.append(1)
            return original(fd)
        with mock.patch.object(ic.os, "fsync", side_effect=observe):
            self.assertEqual(self.recover(baseline)[0], 0)
        for when in ("before", "after"):
            for at in range(1, len(observed) + 1):
                with self.subTest(fsync=at, when=when):
                    f = self.failed(di.Fixture(Path(self.temp.name) / when / str(at)), receipt="absent")
                    count = [0]
                    def fail(fd):
                        count[0] += 1
                        if count[0] == at and when == "before":
                            raise OSError("injected fsync failure before persistence")
                        original(fd)
                        if count[0] == at and when == "after":
                            raise OSError("lost fsync reply")
                    with mock.patch.object(ic.os, "fsync", side_effect=fail):
                        self.assertEqual(self.recover(f)[0], 1)
                    before = self.snapshot(f)
                    live = f.controller / "state/reservation.json"
                    committed = live.exists() and live.read_bytes() != ic.RELEASE_BLOCKED and json.loads(live.read_bytes())["phase"] == "available"
                    if not committed:
                        self.assertFalse(self.old_reply(f, "list").get("selection_ready"))
                        self.assertEqual(self.old_enroll(f)[0], 1)
                        self.assertEqual(self.snapshot(f), before)
                    rc, reply = self.recover(f)
                    self.assertEqual(rc, 0, reply)

    def test_abrupt_child_death_preserves_blocking_or_durable_commit(self):
        points = ("journal", "archive", "sentinel", "operation", "blocked", "receipt", "backup", "descriptor", "file-fsync", "replace", "directory-fsync")
        for shape in ("restart_intent", "rolled_back_requires_reconciliation"):
            for point in points:
                with self.subTest(shape=shape, death=point):
                    f = self.failed(di.Fixture(Path(self.temp.name) / shape / point), shape=shape)
                    rename, create, write, replace, fsync = ic.os.rename, ic.create, lc.PrivateFiles.write, ic.os.replace, ic.os.fsync
                    def child():
                        final = [False]
                        def die_rename(src, dst, **kwargs):
                            rename(src, dst, **kwargs)
                            if point == "archive" and src == "reservation.json": os._exit(71)
                        def die_create(path, data, mode=0o600):
                            create(path, data, mode)
                            if ((point == "journal" and path.name.startswith("unlaunched-transition-"))
                                    or (point == "descriptor" and path.name.startswith("unlaunched-committed-"))
                                    or (point == "sentinel" and path.name == "reservation.json")
                                    or (point == "operation" and path.name == "operation.json")
                                    or (point == "receipt" and path.name == "start-receipt.json")
                                    or (point == "backup" and path.name == "prepare-backup.conf")):
                                os._exit(71)
                        def die_write(files, name, data, limit=lc.MAX_PRIVATE):
                            if name == "reservation.json" and rs._decode(data, rs.HostIdentity(lc.canonical_uuid(lf.HOST), 47989)).status.phase is rs.Phase.AVAILABLE:
                                final[0] = True
                            write(files, name, data, limit=limit)
                            if point == "blocked" and name == "reservation.json" and not final[0]: os._exit(71)
                        def die_replace(src, dst, **kwargs):
                            replace(src, dst, **kwargs)
                            if point == "replace" and final[0]: os._exit(71)
                        def die_fsync(fd):
                            fsync(fd)
                            if final[0] and ((point == "file-fsync" and not ic.stat.S_ISDIR(os.fstat(fd).st_mode))
                                             or (point == "directory-fsync" and ic.stat.S_ISDIR(os.fstat(fd).st_mode))):
                                os._exit(71)
                        with mock.patch.object(ic.os, "rename", side_effect=die_rename), mock.patch.object(ic, "create", side_effect=die_create), \
                                mock.patch.object(lc.PrivateFiles, "write", die_write), mock.patch.object(ic.os, "replace", side_effect=die_replace), \
                                mock.patch.object(ic.os, "fsync", side_effect=die_fsync):
                            self.recover(f)
                        os._exit(72)
                    process = mp.get_context("fork").Process(target=child)
                    process.start(); process.join(15)
                    if process.is_alive(): process.terminate(); process.join(); self.fail("private death fixture hung")
                    self.assertEqual(process.exitcode, 71)
                    before = self.snapshot(f)
                    if point in ("replace", "directory-fsync"):
                        self.assertTrue(self.old_reply(f, "list").get("selection_ready"))
                    else:
                        self.assertFalse(self.old_reply(f, "list").get("selection_ready"))
                        self.assertEqual(self.old_enroll(f)[0], 1)
                    self.assertEqual(self.snapshot(f), before)
                    rc, reply = self.recover(f, lambda: (_ for _ in ()).throw(AssertionError("already confirmed")))
                    if point == "file-fsync":
                        # A real death leaves a private unpublished temp file.
                        # It is evidence, not something a fallback may erase.
                        self.assertEqual(rc, 1, reply)
                        self.assertEqual(self.snapshot(f), before)
                        self.assertTrue(list((f.controller / "state").glob(".operation-*")))
                    else:
                        self.assertEqual(rc, 0, reply)

    def test_matching_current_receipt_only_can_bridge_restricted_proc(self):
        for receipt in ("current", "absent", "historical"):
            for field in ("exe", "environ"):
                with self.subTest(receipt=receipt, field=field):
                    f = self.failed(di.Fixture(Path(self.temp.name) / receipt / field), receipt=receipt)
                    method = "identity" if field == "exe" else "read"
                    original = getattr(f.t, method)
                    def limited(path, *args, **kwargs):
                        if str(path).startswith("/proc/") and Path(path).name == field:
                            raise PermissionError("synthetic restricted current proc")
                        return original(path, *args, **kwargs)
                    before = self.snapshot(f)
                    with mock.patch.object(f.t, method, side_effect=limited):
                        rc, reply = self.recover(f)
                        self.assertEqual(rc, 0 if receipt == "current" else 1, reply)
                    if receipt != "current": self.assertEqual(self.snapshot(f), before)

    def test_committed_replay_contextualizes_later_receipt_and_second_failed_lease(self):
        f = self.failed(shape="rolled_back_requires_reconciliation", receipt="historical")
        self.positive(f)
        owner = self.acquire(f)
        prepared = self.call(f, owner, "prepare", snapshot=owner["snapshot"], display_id=lf.FIRST)
        self.call(f, prepared, "abort")
        folder = f.controller / "state"
        receipt_path = folder / "start-receipt.json"
        later = receipt_path.read_bytes()
        bad = json.loads(later); bad["receipt"]["host_uniqueid"] = "11223344-1234-4321-abcd-0123456789ab"
        receipt_path.write_bytes(ic.document(bad))
        before = self.snapshot(f)
        self.assertEqual(self.recover(f)[0], 1)
        self.assertEqual(self.snapshot(f), before)
        receipt_path.write_bytes(later)
        self.assertEqual(self.recover(f)[0], 0)
        owner = self.acquire(f)
        with mock.patch.object(lc.HostController, "issue_start", side_effect=Refusal("later_failed_start", "injected")), self.assertRaises(Refusal):
            self.call(f, owner, "prepare", snapshot=owner["snapshot"], display_id=lf.SECOND)
        # Same-enrollment earlier receipt and issued==reserved generation are
        # legitimate; validate their own previous->issued relationship.
        self.positive(f)
        self.assertEqual(len(list(folder.glob("unlaunched-transition-*.bak"))), 2)
        before = self.snapshot(f)
        self.assertEqual(self.recover(f)[0], 0)
        self.assertEqual(self.snapshot(f), before)

    def test_precommit_descriptor_drift_cannot_expose_availability(self):
        for case in ("unit", "manifest", "lock_inode", "history"):
            with self.subTest(case=case):
                f = self.failed(di.Fixture(Path(self.temp.name) / case), receipt="absent")
                original = ic.create
                def drift(path, data, mode=0o600):
                    original(path, data, mode)
                    if path.name.startswith("unlaunched-committed-"):
                        if case == "unit": f.t.props["NeedDaemonReload"] = "yes"
                        if case == "manifest":
                            p = f.manifest().parent / "host.py"; p.write_bytes(p.read_bytes() + b"# drift\n")
                        if case == "lock_inode":
                            p = f.controller / "state/reservation.lock"; data = p.read_bytes(); p.rename(f.root / "old-lock"); ic.create(p, data)
                        if case == "history":
                            p = f.controller / "state/extra.bak"; ic.create(p, b"late unexplained history")
                with mock.patch.object(ic, "create", side_effect=drift):
                    self.assertEqual(self.recover(f)[0], 1)
                self.assertEqual(json.loads((f.controller / "state/reservation.json").read_bytes())["phase"], "selecting")

    def test_original_committed_archive_history_prefix_and_locks_stay_sealed(self):
        for case in ("archive", "history", "prefix", "lock", "journal", "descriptor"):
            with self.subTest(case=case):
                f = self.failed(di.Fixture(Path(self.temp.name) / case), receipt="absent")
                history = f.controller / "state/previous.bak"
                ic.create(history, b"original earlier history")
                transaction = self.positive(f)
                folder = f.controller / "state"
                if case == "archive": path = folder / ("operation.json." + transaction["id"] + ".bak")
                elif case == "history": path = history
                elif case == "prefix": path = f.controller / "active/00000001.json"
                elif case == "lock": path = folder / "reservation.lock"
                elif case == "journal": path = folder / ("unlaunched-transition-" + transaction["id"] + ".bak")
                else: path = folder / ("unlaunched-committed-" + transaction["id"] + ".bak")
                if case in ("prefix", "lock"):
                    data = path.read_bytes(); path.rename(f.root / "saved-original"); ic.create(path, data)
                else:
                    path.write_bytes(path.read_bytes() + b" ")
                before = self.snapshot(f)
                self.assertEqual(self.recover(f)[0], 1)
                self.assertEqual(self.snapshot(f), before)

    def test_pending_journal_never_accepts_a_later_active_source(self):
        f = self.failed(receipt="absent")
        original = ic.create
        def cut(path, data, mode=0o600):
            original(path, data, mode)
            if path.name.startswith("unlaunched-transition-"): raise RuntimeError("durable journal")
        with mock.patch.object(ic, "create", side_effect=cut):
            self.assertEqual(self.recover(f)[0], 1)
        source = f.root / "changed-pending-source"
        shutil.copytree(f.source, source, ignore=shutil.ignore_patterns("__pycache__"))
        with (source / "qualification.py").open("a") as stream: stream.write("\n# other active source before retirement commit\n")
        f.source = source
        f.install()
        before = self.snapshot(f)
        self.assertEqual(self.recover(f)[0], 1)
        self.assertEqual(self.snapshot(f), before)
        self.assertFalse(self.old_reply(f, "list").get("selection_ready"))

    def test_confirmation_and_lock_or_history_drift_preserve_blocking(self):
        for case in ("deny", "busy", "operation.lock", "reservation.lock", ".install.lock", "history", "prefix", "manifest"):
            with self.subTest(case=case):
                f = self.failed(di.Fixture(Path(self.temp.name) / case), receipt="absent")
                folder = f.controller / "state"
                if case in ("operation.lock", "reservation.lock", ".install.lock"):
                    (f.controller / case if case == ".install.lock" else folder / case).write_bytes(b"x")
                def authorize():
                    if case == "busy": f.f.app = 123
                    if case == "history":
                        p = folder / "foreign.bak"; p.write_bytes(b"changed history"); p.chmod(0o600)
                    if case == "prefix":
                        p = f.controller / "active/00000001.json"; data = p.read_bytes(); p.unlink(); ic.create(p, data)
                    if case == "manifest":
                        p = f.manifest().parent / "host.py"; p.write_bytes(p.read_bytes() + b"# drift\n")
                    return case != "deny"
                self.assertEqual(self.recover(f, authorize)[0], 1)
                self.assertFalse(list(folder.glob("unlaunched-transition-*.bak")))
                self.assertNotEqual(json.loads((folder / "reservation.json").read_bytes())["phase"], "available")

    def test_all_original_archives_revalidated_after_last_endpoint(self):
        for shape in ("restart_intent", "rolled_back_requires_reconciliation"):
            for name in ic.UNLAUNCHED_RECORDS:
                for fault in ("missing", "bytes", "inode"):
                    with self.subTest(shape=shape, original=name, fault=fault):
                        f = self.failed(di.Fixture(Path(self.temp.name) / shape / name / fault), shape=shape)
                        folder = f.controller / "state"
                        originals = {n: ic.record_identity(folder / n) for n in ic.UNLAUNCHED_RECORDS}
                        create, endpoint, write = ic.create, f.f.endpoint, lc.PrivateFiles.write
                        sealed, injected, available_writes = [], [], []
                        post_descriptor = [0]
                        live_baseline = {}
                        def remember(path, data, mode=0o600):
                            create(path, data, mode)
                            if path.name.startswith("unlaunched-committed-"): sealed.append(path)
                        def last_endpoint(*args):
                            if sealed:
                                post_descriptor[0] += 1
                                if post_descriptor[0] == 2:
                                    journal = json.loads(next(folder.glob("unlaunched-transition-*.bak")).read_bytes())
                                    archive = folder / (name + "." + journal["id"] + ".bak")
                                    self.assertEqual(ic.record_identity(archive), originals[name])
                                    live_baseline.update({n: ic.record_identity(folder / n) for n in ic.UNLAUNCHED_RECORDS})
                                    self.assertNotEqual(live_baseline[name]["inode"], originals[name]["inode"])
                                    if fault == "missing": archive.unlink()
                                    elif fault == "bytes": archive.write_bytes(archive.read_bytes() + b" ")
                                    else:
                                        data = archive.read_bytes(); archive.rename(f.root / "saved-original-archive"); create(archive, data)
                                    injected.append(archive)
                            return endpoint(*args)
                        def observe_write(files, record_name, data, limit=lc.MAX_PRIVATE):
                            if record_name == "reservation.json" and json.loads(data)["phase"] == "available":
                                available_writes.append(1)
                            return write(files, record_name, data, limit=limit)
                        f.f.endpoint = last_endpoint
                        with mock.patch.object(ic, "create", side_effect=remember), mock.patch.object(lc.PrivateFiles, "write", observe_write):
                            rc, reply = self.recover(f)
                        self.assertEqual((len(injected), post_descriptor[0]), (1, 2))
                        self.assertEqual(rc, 1, reply)
                        self.assertEqual(reply["error"]["code"], "unlaunched_retirement_refused")
                        self.assertIn("original archives changed after final external observation", reply["error"]["detail"])
                        self.assertEqual(available_writes, [], "must refuse before the sole availability publication")
                        self.assertEqual(json.loads((folder / "reservation.json").read_bytes())["phase"], "selecting")
                        self.assertEqual({n: ic.record_identity(folder / n) for n in ic.UNLAUNCHED_RECORDS}, live_baseline)
                        f.f.endpoint = endpoint
                        before = self.snapshot(f)
                        self.assertFalse(self.old_reply(f, "list").get("selection_ready"))
                        self.assertEqual(self.old_enroll(f)[0], 1)
                        self.assertEqual(self.snapshot(f), before)
                        self.assertEqual(self.recover(f)[0], 1)
                        self.assertEqual(self.snapshot(f), before)

    def test_original_receipt_archive_absence_stays_bound_after_last_endpoint(self):
        for shape in ("restart_intent", "rolled_back_requires_reconciliation"):
            with self.subTest(shape=shape):
                f = self.failed(di.Fixture(Path(self.temp.name) / shape), shape=shape, receipt="absent")
                folder = f.controller / "state"
                create, endpoint, write = ic.create, f.f.endpoint, lc.PrivateFiles.write
                sealed, injected, available_writes = [], [], []
                post_descriptor = [0]
                def remember(path, data, mode=0o600):
                    create(path, data, mode)
                    if path.name.startswith("unlaunched-committed-"): sealed.append(path)
                def last_endpoint(*args):
                    if sealed:
                        post_descriptor[0] += 1
                        if post_descriptor[0] == 2:
                            journal = json.loads(next(folder.glob("unlaunched-transition-*.bak")).read_bytes())
                            self.assertNotIn("start-receipt.json", journal["records"])
                            archive = folder / ("start-receipt.json." + journal["id"] + ".bak")
                            self.assertFalse(archive.exists())
                            create(archive, b"unexplained late archived receipt\n"); injected.append(archive)
                    return endpoint(*args)
                def observe_write(files, name, data, limit=lc.MAX_PRIVATE):
                    if name == "reservation.json" and json.loads(data)["phase"] == "available": available_writes.append(1)
                    return write(files, name, data, limit=limit)
                f.f.endpoint = last_endpoint
                with mock.patch.object(ic, "create", side_effect=remember), mock.patch.object(lc.PrivateFiles, "write", observe_write):
                    rc, reply = self.recover(f)
                self.assertEqual((len(injected), post_descriptor[0]), (1, 2))
                self.assertEqual(rc, 1, reply)
                self.assertEqual(available_writes, [])
                self.assertEqual(json.loads((folder / "reservation.json").read_bytes())["phase"], "selecting")
                self.assertFalse((folder / "start-receipt.json").exists())

    def test_last_runtime_observation_cannot_replace_the_intended_commit_records(self):
        baseline = self.failed(receipt="absent")
        create, endpoint = ic.create, baseline.f.endpoint
        sealed, observations = [], []
        def remember(path, data, mode=0o600):
            create(path, data, mode)
            if path.name.startswith("unlaunched-committed-"): sealed.append(path)
        def observe(*args):
            if sealed: observations.append(1)
            return endpoint(*args)
        baseline.f.endpoint = observe
        with mock.patch.object(ic, "create", side_effect=remember):
            self.assertEqual(self.recover(baseline)[0], 0)
        self.assertTrue(observations)
        for target in ("journal", "descriptor", "operation.json", "reservation.json"):
            for fault in ("missing", "changed", "same_bytes_new_inode"):
                with self.subTest(target=target, fault=fault):
                    f = self.failed(di.Fixture(Path(self.temp.name) / target / fault), receipt="absent")
                    armed, count, injected = [], [0], []
                    endpoint = f.f.endpoint
                    def remember(path, data, mode=0o600):
                        create(path, data, mode)
                        if path.name.startswith("unlaunched-committed-"): armed.append(path)
                    def drift(*args):
                        if armed:
                            count[0] += 1
                            if count[0] == len(observations):
                                folder = f.controller / "state"
                                path = next(folder.glob("unlaunched-transition-*.bak")) if target == "journal" else (
                                    armed[0] if target == "descriptor" else folder / target)
                                if fault == "missing": path.unlink()
                                elif fault == "changed": path.write_bytes(path.read_bytes() + b" ")
                                else:
                                    data = path.read_bytes(); path.rename(f.root / "last-original"); create(path, data)
                                injected.append(1)
                        return endpoint(*args)
                    f.f.endpoint = drift
                    with mock.patch.object(ic, "create", side_effect=remember):
                        rc, reply = self.recover(f)
                    self.assertEqual(injected, [1])
                    self.assertEqual(rc, 1, reply)
                    live = f.controller / "state/reservation.json"
                    if live.exists(): self.assertNotEqual(json.loads(live.read_bytes())["phase"], "available")

    def test_intended_journal_is_pinned_through_final_readiness(self):
        for shape in ("restart_intent", "rolled_back_requires_reconciliation"):
            for fault in ("lower_floor", "higher_floor", "delete", "same_bytes_new_inode", "changed_promise"):
                with self.subTest(shape=shape, fault=fault):
                    f = self.failed(di.Fixture(Path(self.temp.name) / shape / fault), shape=shape, receipt="absent")
                    folder = f.controller / "state"
                    rename, create = ic.os.rename, ic.create
                    journal_path, injected = [], []
                    def remember(path, data, mode=0o600):
                        create(path, data, mode)
                        if path.name.startswith("unlaunched-transition-"): journal_path.append(path)
                    def drift(src, dst, **kwargs):
                        rename(src, dst, **kwargs)
                        if src == "reservation.json":
                            path = journal_path[0]; data = path.read_bytes(); value = json.loads(data)
                            if fault == "delete": path.unlink()
                            elif fault == "same_bytes_new_inode":
                                path.rename(f.root / "original-journal"); create(path, data)
                            else:
                                if fault in ("lower_floor", "higher_floor"):
                                    delta = -1 if fault == "lower_floor" else 1
                                    value["evidence"]["reservation_revision"] += delta
                                    promised = json.loads(bytes.fromhex(value["replacement"]["reservation.json"]))
                                    promised["revision"] += delta
                                    value["replacement"]["reservation.json"] = ic.document(promised).hex()
                                else:
                                    promised = json.loads(bytes.fromhex(value["replacement"]["reservation.json"]))
                                    promised["binding"]["config_digest"] = "f" * 64
                                    value["replacement"]["reservation.json"] = ic.document(promised).hex()
                                path.write_bytes(ic.document(value))
                            injected.append(1)
                    with mock.patch.object(ic, "create", side_effect=remember), mock.patch.object(ic.os, "rename", side_effect=drift):
                        rc, reply = self.recover(f)
                    self.assertEqual(injected, [1])
                    self.assertEqual(rc, 1, reply)
                    self.assertEqual(json.loads((folder / "reservation.json").read_bytes())["phase"], "selecting")
                    self.assertFalse(list(folder.glob("unlaunched-committed-*.bak")))
                    self.assertFalse(self.old_reply(f, "list").get("selection_ready"))

    def test_even_self_resealed_promises_cannot_redefine_original_floor_or_enrollment(self):
        for fault in ("lower_floor", "higher_floor", "enrollment", "blocked_owner", "receipt_absence"):
            with self.subTest(fault=fault):
                f = self.failed(di.Fixture(Path(self.temp.name) / fault))
                transaction = self.positive(f)
                folder = f.controller / "state"
                path = folder / ("unlaunched-transition-" + transaction["id"] + ".bak")
                descriptor = folder / ("unlaunched-committed-" + transaction["id"] + ".bak")
                journal, seal = json.loads(path.read_bytes()), json.loads(descriptor.read_bytes())
                if fault in ("lower_floor", "higher_floor"):
                    delta = -1 if fault == "lower_floor" else 1
                    journal["evidence"]["reservation_revision"] += delta
                    promised = json.loads(bytes.fromhex(journal["replacement"]["reservation.json"]))
                    promised["revision"] += delta
                    journal["replacement"]["reservation.json"] = ic.document(promised).hex()
                    (folder / "reservation.json").write_bytes(ic.document(promised))
                elif fault == "enrollment":
                    value = json.loads(bytes.fromhex(journal["replacement"]["operation.json"]))
                    value["enrollment"]["revision"] += 1
                    journal["replacement"]["operation.json"] = ic.document(value).hex()
                elif fault == "blocked_owner":
                    value = json.loads(bytes.fromhex(journal["blocked"]))
                    value["owner_digest"] = "f" * 64
                    journal["blocked"] = ic.document(value).hex()
                else:
                    del journal["replacement"]["start-receipt.json"]
                    del journal["records"]["start-receipt.json"]
                    del seal["live_records"]["start-receipt.json"]
                path.write_bytes(ic.document(journal))
                seal["journal_sha256"] = cfg.digest(path.read_bytes())
                seal["journal_identity"] = ic.record_identity(path)
                descriptor.write_bytes(ic.document(seal))
                before = self.snapshot(f)
                self.assertEqual(self.recover(f)[0], 1)
                self.assertEqual(self.snapshot(f), before)

    def test_descriptor_missing_changed_or_replaced_before_last_check_refuses(self):
        for fault in ("missing_at_create", "changed_at_create", "missing_late", "changed_late", "same_bytes_inode_late"):
            with self.subTest(fault=fault):
                f = self.failed(di.Fixture(Path(self.temp.name) / fault), receipt="absent")
                folder = f.controller / "state"
                create, endpoint = ic.create, f.f.endpoint
                descriptor, injected = [], []
                def change(path):
                    if "missing" in fault: path.unlink()
                    elif fault == "same_bytes_inode_late":
                        data = path.read_bytes(); path.rename(f.root / "old-descriptor"); create(path, data)
                    else: path.write_bytes(path.read_bytes() + b" ")
                    injected.append(1)
                def observe(*args):
                    if descriptor and not injected and fault.endswith("late"):
                        change(descriptor[0])
                    return endpoint(*args)
                def publish(path, data, mode=0o600):
                    create(path, data, mode)
                    if path.name.startswith("unlaunched-committed-"):
                        descriptor.append(path)
                        if fault.endswith("create"): change(path)
                f.f.endpoint = observe
                with mock.patch.object(ic, "create", side_effect=publish):
                    rc, reply = self.recover(f)
                self.assertEqual(injected, [1])
                self.assertEqual(rc, 1, reply)
                self.assertEqual(json.loads((folder / "reservation.json").read_bytes())["phase"], "selecting")
                self.assertEqual(self.old_enroll(f)[0], 1)
                if fault == "same_bytes_inode_late":
                    # Approved boundary: a new invocation validates exact derived
                    # content+semantics, not original descriptor self-inode.
                    self.assertEqual(self.recover(f)[0], 0)

    def test_complete_actual_live_baseline_required_after_descriptor(self):
        for shape in ("restart_intent", "rolled_back_requires_reconciliation"):
            for name in ic.UNLAUNCHED_RECORDS:
                for fault in ("missing", "changed", "same_bytes_new_inode"):
                    with self.subTest(shape=shape, name=name, fault=fault):
                        f = self.failed(di.Fixture(Path(self.temp.name) / shape / name / fault), shape=shape)
                        folder = f.controller / "state"
                        create = ic.create
                        injected = []
                        def drift(path, data, mode=0o600):
                            create(path, data, mode)
                            if path.name.startswith("unlaunched-committed-"):
                                live = folder / name; original = live.read_bytes()
                                if fault == "missing": live.unlink()
                                elif fault == "same_bytes_new_inode":
                                    live.rename(f.root / ("saved-" + name)); create(live, original)
                                elif name == "reservation.json":
                                    value = json.loads(original); value["owner_digest"] = "f" * 64; live.write_bytes(ic.document(value))
                                elif name == "operation.json":
                                    value = json.loads(original); value["enrollment"]["revision"] += 1; live.write_bytes(ic.document(value))
                                else: live.write_bytes(original + b"\n")
                                injected.append(1)
                        with mock.patch.object(ic, "create", side_effect=drift):
                            rc, reply = self.recover(f)
                        self.assertEqual(injected, [1])
                        self.assertEqual(rc, 1, reply)
                        live = folder / "reservation.json"
                        if live.exists(): self.assertEqual(json.loads(live.read_bytes())["phase"], "selecting")
                        before = self.snapshot(f)
                        # A sealed baseline has no legitimate missing-copy cut;
                        # replay must not silently restore the corrupted record.
                        self.assertEqual(self.recover(f)[0], 1)
                        self.assertEqual(self.snapshot(f), before)

    def test_original_receipt_absence_cannot_be_replaced_by_an_unexplained_live_file(self):
        f = self.failed(receipt="absent")
        create = ic.create
        injected = []
        def drift(path, data, mode=0o600):
            create(path, data, mode)
            if path.name.startswith("unlaunched-committed-"):
                create(f.controller / "state/start-receipt.json", b"{}\n"); injected.append(1)
        with mock.patch.object(ic, "create", side_effect=drift):
            self.assertEqual(self.recover(f)[0], 1)
        self.assertEqual(injected, [1])
        before = self.snapshot(f)
        self.assertEqual(self.recover(f)[0], 1)
        self.assertEqual(self.snapshot(f), before)
        self.assertEqual(json.loads((f.controller / "state/reservation.json").read_bytes())["phase"], "selecting")

    def unrelated_current_receipt(self, f, *, earlier=False):
        path = f.controller / "state/start-receipt.json"
        value = json.loads(path.read_bytes())
        current = rt.LinuxRuntime(host.settings(f.manifest(), "linux").runtime, f.t).observe()
        generation = asdict(current.generation)
        if earlier:
            generation.update(pid=generation["pid"] - 5, start_ticks=generation["start_ticks"] - 2,
                              start_monotonic_usec=generation["start_monotonic_usec"] - 10, invocation_id="8" * 32)
        value["receipt"]["generation"] = generation
        value["receipt"]["config"] = asdict(current.config.file)
        value["receipt"]["executable"] = asdict(current.binary.file)
        previous = dict(generation)
        previous.update(pid=previous["pid"] - 1, start_ticks=previous["start_ticks"] - 1,
                        start_monotonic_usec=previous["start_monotonic_usec"] - 1, invocation_id="9" * 32)
        value["issuance"]["previous_generation"] = previous
        value["issuance"]["operation"] = "99999999-9999-4999-9999-999999999999"
        path.write_bytes(ic.document(value))

    def test_pending_unrelated_post_reboot_receipt_never_becomes_prior_authority(self):
        for shape in ("restart_intent", "rolled_back_requires_reconciliation"):
            for earlier in (False, True):
                for limited_field in (None, "exe", "environ"):
                    with self.subTest(shape=shape, earlier=earlier, limited=limited_field):
                        f = self.failed(di.Fixture(Path(self.temp.name) / shape / str(earlier) / str(limited_field)), shape=shape, reboot=True)
                        self.unrelated_current_receipt(f, earlier=earlier)
                        before = self.snapshot(f)
                        method = "identity" if limited_field == "exe" else "read"
                        original = getattr(f.t, method)
                        def limited(path, *args, **kwargs):
                            if limited_field and str(path).startswith("/proc/") and Path(path).name == limited_field:
                                raise PermissionError("synthetic inaccessible current proc")
                            return original(path, *args, **kwargs)
                        with mock.patch.object(f.t, method, side_effect=limited):
                            rc, reply = self.recover(f)
                        self.assertEqual(rc, 1, reply)
                        self.assertEqual(self.snapshot(f), before)
                        self.assertFalse(list((f.controller / "state").glob("unlaunched-transition-*.bak")))

    def test_noncurrent_receipt_retirement_does_not_invent_cross_boot_order(self):
        f = self.failed(shape="rolled_back_requires_reconciliation", receipt="historical", reboot=True)
        transaction = self.positive(f)
        context = transaction["evidence"]["receipt_context"]
        self.assertEqual((context["kind"], context["current_match"]), ("retired_history", False))
        self.assertEqual(context["retirement"]["kind"], "previous_boot")

    def test_committed_replay_preserves_a_genuine_independent_later_current_receipt(self):
        f = self.failed(reboot=True)
        original = self.positive(f)
        owner = self.acquire(f)
        prepared = self.call(f, owner, "prepare", snapshot=owner["snapshot"], display_id=lf.FIRST)
        self.assertTrue(prepared["restarted"])
        self.call(f, prepared, "abort")
        path = f.controller / "state/start-receipt.json"
        receipt = json.loads(path.read_bytes())
        self.assertNotEqual(receipt["issuance"]["operation"], original["evidence"]["restart_id"])
        before = self.snapshot(f)
        identity = f.t.identity
        def limited(p, *args, **kwargs):
            if str(p).startswith("/proc/") and Path(p).name == "exe":
                raise PermissionError("synthetic restricted proc on already committed replay")
            return identity(p, *args, **kwargs)
        with mock.patch.object(f.t, "identity", side_effect=limited):
            rc, reply = self.recover(f)
        self.assertEqual(rc, 0, reply)
        self.assertEqual(self.snapshot(f), before)



if __name__ == "__main__":
    unittest.main()
