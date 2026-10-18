#!/usr/bin/env python3
"""Private installed-source/systemd/cgroup-v2 fixtures; no real service/network.

Synthetic reference .deb construction is inherited from display_install. Native
service/process/endpoint reads and commands are injected; Popen is a tripwire.
"""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

sys.path[:0] = [str(Path(__file__).resolve().parents[1]/"display-control"), str(Path(__file__).resolve().parent)]
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

SHOW = ("/usr/bin/systemctl", "--user", "show", "--no-pager", rt.UNIT,
        "--property="+",".join(rt.RETIREMENT_PROPERTIES))
JOBS = ("/usr/bin/busctl", "--user", "--json=short", "--auto-start=no", "--allow-interactive-authorization=no",
        "call", "org.freedesktop.systemd1", "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager", "ListJobs")
GET_UNIT = (*rf.BUS_PREFIX,"call","org.freedesktop.systemd1","/org/freedesktop/systemd1",
            "org.freedesktop.systemd1.Manager","GetUnit","s",rt.UNIT)
PROPERTY_COMMANDS = {
    (*rf.BUS_PREFIX,"get-property","org.freedesktop.systemd1",rf.BUS_OBJECT,
     "org.freedesktop.systemd1."+interface,name):name
    for interface,name in (("Service","ExecStop"),("Service","ExecStopPost"),("Unit","Triggers"),
                           ("Unit","TriggeredBy"),("Service","FileDescriptorStoreMax"),("Service","NFileDescriptorStore"))
}


class Kernel:
    """Kernel-like hierarchical events, PID births and fixed unit controls."""
    def __init__(self, f):
        self.f, self.t = f, f.t
        self.cg = f"/user.slice/user-{os.geteuid()}.slice/user@{os.geteuid()}.service/app.slice/{rt.UNIT}"
        self.path = rt.CGROUP_ROOT/self.cg.lstrip("/")
        # Actual systemctl255 producer omits these special empty arrays even
        # with --all; Sockets is not a native Service property at all.
        self.props = {key: "" for key in rt.RETIREMENT_PROPERTIES if key not in ("ExecStop","ExecStopPost")}
        self.props.update({k:v for k,v in rt.RETIREMENT_POLICY.items() if k not in ("ExecStop","ExecStopPost")})
        self.get_unit = {"type":"o","data":[rf.BUS_OBJECT]}
        self.typed = {"ExecStop":{"type":"a(sasbttttuii)","data":[]},
                      "ExecStopPost":{"type":"a(sasbttttuii)","data":[]},
                      "Triggers":{"type":"as","data":[]}, "TriggeredBy":{"type":"as","data":[]},
                      "FileDescriptorStoreMax":{"type":"u","data":0}, "NFileDescriptorStore":{"type":"u","data":0}}
        self.jobs = {"type": "a(usssoo)", "data": [[]]}
        self.controls, self.events = [], []
        self.fault = None
        self.removed = False
        self.after_control = None
        self.t.put(rt.CGROUP_ROOT/"cgroup.controllers", b"cpu memory pids\n", uid=0)
        self.t.put(self.path/"cgroup.events", b"populated 1\nfrozen 0\n")
        self.t.put(self.path/"cgroup.procs", (self.t.props["MainPID"]+"\n").encode())
        self.t.put(Path("/proc")/self.t.props["MainPID"]/"cgroup", ("0::"+self.cg+"\n").encode())
        self.base_run = self.t.run
        self.t.run = self.run

    def run(self, argv, *, timeout, max_output):
        if argv == SHOW:
            props = {**self.props, **{key: self.t.props[key] for key in ("Id", "ActiveState", "SubState", "MainPID", "InvocationID")},
                     "ControlGroup": "" if self.removed else self.cg}
            self.t.calls.append((argv, timeout, max_output))
            if self.t.on_run:
                self.t.on_run(self.t, argv)
            return rt.CommandResult(0, "".join(k+"="+v+"\n" for k,v in props.items()).encode())
        if argv == GET_UNIT or argv in PROPERTY_COMMANDS:
            self.t.calls.append((argv,timeout,max_output))
            if self.t.on_run: self.t.on_run(self.t,argv)
            value = self.get_unit if argv == GET_UNIT else self.typed[PROPERTY_COMMANDS[argv]]
            if isinstance(value,Exception): raise value
            if type(value) is rt.CommandResult: return value
            return rt.CommandResult(0,value if type(value) is bytes else ic.document(value))
        if argv == JOBS:
            self.t.calls.append((argv, timeout, max_output))
            if self.t.on_run:
                self.t.on_run(self.t, argv)
            return rt.CommandResult(0, ic.document(self.jobs))
        if argv[:3] == ("/usr/bin/systemctl", "--user", "--no-ask-password") and argv[3:] in (("stop",rt.UNIT),("start",rt.UNIT)):
            action = argv[3]
            self.controls.append(action)
            self.events.append(action)
            state = self.f.controller/"state"
            self.assert_intent(state, action)
            if self.fault == action+"-before":
                raise TimeoutError("synthetic uncertain issued control")
            if action == "stop":
                self.oldpid = self.t.props["MainPID"]
                self.oldfiles = {s: self.t.files[Path("/proc")/self.oldpid/s] for s in ("status","cmdline","cgroup","environ")}
                self.t.files.pop(Path("/proc")/self.oldpid/"stat", None)
                self.t.ids.pop(Path("/proc")/self.oldpid/"stat", None)
                self.t.props.update(MainPID="0", ActiveState="inactive", SubState="dead")
                self.t.put(self.path/"cgroup.events", b"populated 0\nfrozen 0\n")
                self.t.put(self.path/"cgroup.procs", b"")
            else:
                pid = int(self.oldpid)+100
                self.t.restarts += 1  # Keep the inherited monotonic fixture clock honest.
                for suffix, content in self.oldfiles.items():
                    self.t.put(Path("/proc")/str(pid)/suffix, content)
                fields = ["S"]+["0"]*18+[str(500+self.t.restarts)]+["0"]*30
                self.t.put(Path("/proc")/str(pid)/"stat", (f"{pid} (sunshine worker)) "+" ".join(fields)+"\n").encode())
                self.t.props.update(MainPID=str(pid), ActiveState="active", SubState="running", InvocationID=f"{pid:032x}",
                                    ExecMainStartTimestampMonotonic=str(int(self.t.props["ExecMainStartTimestampMonotonic"])+1000000))
                if self.removed:
                    self.removed = False
                    self.t.mkdir(self.path)
                self.t.put(self.path/"cgroup.events", b"populated 1\nfrozen 0\n")
                self.t.current_trace()
            if self.after_control:
                self.after_control(action)
            if self.fault == action+"-after":
                raise TimeoutError("synthetic completed-but-unacknowledged control")
            return rt.CommandResult(0)
        return self.base_run(argv, timeout=timeout, max_output=max_output)

    def assert_intent(self, state, action):
        assert list(state.glob("attempt-transition-*.bak"))
        assert list(state.glob("attempt-"+action+"-issued-*.bak"))
        if action == "start":
            witness = json.loads(next(state.glob("attempt-stopped-*.bak")).read_bytes())
            assert witness["witness"]["terminal"]["MainPID"] == "0"
            assert witness["witness"]["cgroup_retirement"] in ("removed", "hierarchical_empty")
            self.events.append("witness-before-start")


class RecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import hashlib
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory(prefix="qd-base-dispatcher-") as home:
            raw = subprocess.check_output(("/usr/bin/git", "show", "65416b7384ac5f9468c7547eb1f8b8fd6c770ddc:sunshine-moonlight/display-control/install_controller.py"),
                                          cwd=di.MODULE,env={"PATH":"/usr/bin:/bin","HOME":home,"GIT_CONFIG_NOSYSTEM":"1"},timeout=10)
        cls.base_dispatcher = types.ModuleType("qd_attempt_base_dispatcher")
        cls.base_dispatcher.__file__ = str(di.MODULE/"display-control/install_controller.py")
        sys.modules[cls.base_dispatcher.__name__] = cls.base_dispatcher
        exec(compile(raw,cls.base_dispatcher.__file__,"exec"),cls.base_dispatcher.__dict__)
        if cls.base_dispatcher.SHIM != ic.SHIM or cls.base_dispatcher.SOURCES != ic.SOURCES:
            raise AssertionError("fixed shim/source-file manifest set changed")
        cls.base_digest = hashlib.sha256(raw).hexdigest()

    setUp = di.InstallTests.setUp
    snapshot = di.OwnedReleaseReconciliationTests.snapshot

    def retained(self, f=None, *, noop=True):
        f = f or self.fixture
        f.install(publish=True)
        f.rpc("enroll", revision=f.rpc("status")["revision"])
        if noop:
            menu = f.rpc("list")
            owner = f.rpc("acquire", revision=menu["revision"], snapshot=menu["snapshot"])
            prepared = f.rpc("prepare", revision=owner["revision"], capability=owner["capability"], snapshot=owner["snapshot"], display_id=lf.FIRST)
            f.rpc("abort", revision=prepared["revision"], capability=prepared["capability"])
        menu = f.rpc("list")
        owner = f.rpc("acquire", revision=menu["revision"], snapshot=menu["snapshot"])
        prepared = f.rpc("prepare", revision=owner["revision"], capability=owner["capability"], snapshot=owner["snapshot"], display_id=lf.FIRST)
        attempted = f.rpc("launch", revision=prepared["revision"], capability=prepared["capability"])
        f.rpc("control_lost", revision=attempted["revision"], capability=attempted["capability"])
        status = json.loads((f.controller/"state/reservation.json").read_bytes())
        self.assertEqual((status["phase"],status["uncertainty"],status["active_observed"]),("reconciliation_required","owner_lost",False))
        lease = json.loads((f.controller/"state/operation.json").read_bytes())["lease"]
        self.assertEqual("restart_id" not in lease,noop)
        f.kernel = Kernel(f)
        return f

    def recover(self, f, authorize=lambda: True):
        with mock.patch.object(ic.pwd, "getpwuid", return_value=types.SimpleNamespace(pw_dir=str(f.f.home))), \
             mock.patch.object(ic, "__file__", str(f.source/"install_controller.py")), \
             mock.patch.object(host, "collect", side_effect=AssertionError("no native capture inventory")):
            return json.loads(ic.local_attempt_retirement(authorize=authorize, transport=f.t, info=lambda *args: f.f.endpoint(*args)))

    def test_positive_preserves_exact_originals_one_stop_witness_start_one_commit(self):
        f = self.retained()
        folder = f.controller/"state"
        identities = {name: ic.record_identity(folder/name) for name in ic.RELEASE_RECORDS}
        old = (folder/"reservation.json").read_bytes()
        disk = f.f.conf.read_bytes()
        writes = []
        write = lc.PrivateFiles.write
        def observe(files, name, data, **kw):
            if name == "reservation.json" and data != ic.RELEASE_BLOCKED and json.loads(data)["phase"] == "available":
                writes.append(data)
            return write(files,name,data,**kw)
        with mock.patch.object(lc.PrivateFiles,"write",observe):
            reply = self.recover(f)
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["revision"], json.loads(old)["revision"]+1)
        self.assertEqual(f.kernel.controls,["stop","start"])
        self.assertEqual(len(writes),1)
        journal = json.loads(next(folder.glob("attempt-transition-*.bak")).read_bytes())
        for name, identity in identities.items():
            self.assertEqual(ic.record_identity(folder/(name+"."+journal["id"]+".bak")), identity)
        self.assertEqual((folder/"start-receipt.json").read_bytes(), (folder/("start-receipt.json."+journal["id"]+".bak")).read_bytes())
        self.assertEqual(f.f.conf.read_bytes(),disk)
        snap = self.snapshot(f)
        with mock.patch.object(lc.PrivateFiles,"write",observe):
            self.assertTrue(self.recover(f, lambda: (_ for _ in ()).throw(AssertionError("no second confirmation")))["ok"])
        self.assertEqual(self.snapshot(f),snap)
        self.assertEqual(len(writes),1)
        self.assertEqual(f.kernel.controls,["stop","start"])

    def test_independent_native_present_empty_job_and_cgroup_grammar(self):
        f = self.retained()
        adapter = rt.AttemptRetirement(host.settings(f.manifest(),"linux").runtime,f.t)
        # Sanitized native grammar, NOT generated from RETIREMENT_POLICY. The
        # captured rows establish present-empty Job/no JobType and these
        # cgroup.events bytes, not retirement. Other mandatory policy rows here
        # are independent synthetic facts, not a native qualification claim.
        raw = (b"MainPID=42\nControlPID=0\nFileDescriptorStoreMax=0\nNFileDescriptorStore=0\n"
               b"ControlGroup=/fixture/owned-unit\nDelegate=no\nKillMode=control-group\nFinalKillSignal=9\n"
               b"SendSIGKILL=yes\nId=app-dev.lizardbyte.app.Sunshine.service\nTriggers=\nTriggeredBy=\n"
               b"ActiveState=active\nSubState=running\nJob=\nInvocationID=22222222222222222222222222222222\n"
               b"LoadState=loaded\nKillSignal=15\nTimeoutStopUSec=1min 30s\nRestart=on-failure\n"
               b"BusName=\nType=simple\nNotifyAccess=none\nRemainAfterExit=no\nNeedDaemonReload=no\n")
        # Native method-call codec: outer return arguments, then the job array.
        # The separate empty ListUnitsByNames capture proves this codec shape,
        # not real ListJobs quiescence. All values here are private synthetic.
        jobs = b'{"type":"a(usssoo)","data":[[]]}\n'
        def observe(argv):
            if argv == SHOW: return raw
            if argv == JOBS: return jobs
            if argv == GET_UNIT: return ic.document(f.kernel.get_unit)
            if argv in PROPERTY_COMMANDS: return ic.document(f.kernel.typed[PROPERTY_COMMANDS[argv]])
            raise AssertionError("unexpected external observation")
        with mock.patch.object(adapter.runtime,"_run",side_effect=observe):
            self.assertEqual(adapter.properties()["Job"],"")
            self.assertNotIn("JobType",adapter.properties())
            valid = raw
            for replacement in (b"",b"Job=0\n",b"Job=17 /org/freedesktop/systemd1/job/17\n",b"Job=\nJob=\n"):
                raw = valid.replace(b"Job=\n",replacement)
                with self.assertRaises(Refusal): adapter.properties()
            raw = valid
            for unknown in (b'{}', b'{"type":"as","data":[[]]}', b'{"type":"a(usssoo)","data":[]}',
                            b'{"type":"a(usssoo)","data":[[],[]]}', b'{"type":"a(usssoo)","data":[null]}',
                            b'{"type":"a(usssoo)","data":[[1]]}', b'{"type":"a(usssoo)","data":[[[]]]}',
                            b'{"type":"a(usssoo)","data":[[]],"extra":[]}',
                            b'{"type":"a(usssoo)","data":[[]],"data":[[]]}'):
                jobs = unknown
                with self.assertRaisesRegex(Refusal,"invalid typed ListJobs method reply"): adapter.properties()
            row = [7,"fixture-job.service","start","running","/org/freedesktop/systemd1/job/7",
                   "/org/freedesktop/systemd1/unit/fixture_2djob_2eservice"]
            jobs = ic.document({"type":"a(usssoo)","data":[[row]]})
            with self.assertRaisesRegex(Refusal,"user manager has pending jobs"): adapter.properties()
            for invalid in (row[:-1],[True,*row[1:]],[-1,*row[1:]],[(1<<32),*row[1:]],
                            [row[0],None,*row[2:]],[*row[:4],"not/object/path",row[5]],
                            [row[0],"bad\0name",*row[2:]],[row[0],chr(0xD800),*row[2:]]):
                jobs = ic.document({"type":"a(usssoo)","data":[[invalid]]})
                with self.assertRaisesRegex(Refusal,"invalid typed ListJobs method reply"): adapter.properties()
        f.t.put(f.kernel.path/"cgroup.events",b"populated 1\nfrozen 0\n")
        self.assertTrue(adapter.populated(f.kernel.path))
        for unknown in (b"",b"frozen 0\n",b"populated 0\nfrozen 1\n",b"populated 0\npopulated 1\nfrozen 0\n"):
            f.t.put(f.kernel.path/"cgroup.events",unknown)
            with self.assertRaises(Refusal): adapter.populated(f.kernel.path)
        self.assertEqual(f.kernel.controls,[])
        self.assertFalse(list((f.controller/"state").glob("attempt-*.bak")))

    def test_typed_native_properties_are_mandatory_and_fail_before_intent(self):
        f = self.retained()
        before = self.snapshot(f)
        for name in ("ExecStop","ExecStopPost","Triggers","TriggeredBy","FileDescriptorStoreMax","NFileDescriptorStore"):
            original = f.kernel.typed[name]
            signature = original["type"]
            nonempty = (1 if signature == "u" else ["fixture.socket"] if signature == "as" else
                        [["/fixture/hook",["/fixture/hook"],False,0,0,0,0,0,0,0]])
            cases = (rt.CommandResult(1,stderr=b"Unknown property"),rt.CommandResult(0,b""),
                     {"type":"t" if signature == "u" else "u","data":0},
                     {**original,"extra":[]},
                     ('{"type":'+json.dumps(signature)+',"data":[],"data":[]}').encode(),
                     {"type":signature,"data":nonempty},
                     {"type":signature,"data":[0] if signature == "u" else [[]]},
                     {"type":signature,"data":False if signature == "u" else None})
            for value in cases:
                with self.subTest(property=name,value=repr(value)):
                    f.kernel.typed[name] = value
                    with mock.patch.object(ic,"create",side_effect=AssertionError("unknown proof must not publish")), self.assertRaises(Refusal):
                        self.recover(f)
                    self.assertEqual(self.snapshot(f),before)
                    self.assertEqual(f.kernel.controls,[])
            f.kernel.typed[name] = original
        original = f.kernel.get_unit
        for value in ({"type":"o","data":["/org/freedesktop/systemd1/unit/sunshine_2eservice"]},
                      {"type":"o","data":rf.BUS_OBJECT},{"type":"o","data":[]},
                      {"type":"o","data":[rf.BUS_OBJECT,rf.BUS_OBJECT]},
                      {"type":"s","data":[rf.BUS_OBJECT]}, {**original,"extra":0},
                      b'{"type":"o","data":[],"data":[]}',rt.CommandResult(1,stderr=b"unknown unit")):
            with self.subTest(GetUnit=repr(value)):
                f.kernel.get_unit = value
                with mock.patch.object(ic,"create",side_effect=AssertionError("wrong object must not publish")), self.assertRaises(Refusal):
                    self.recover(f)
                self.assertEqual(self.snapshot(f),before)
                self.assertEqual(f.kernel.controls,[])
        self.assertNotIn("Sockets",rt.RETIREMENT_PROPERTIES)
        self.assertNotIn("Sockets",rt.RETIREMENT_POLICY)
        self.assertFalse(any(argv[-1] == "Sockets" for argv,_,_ in f.t.calls))

    def test_hostile_socket_routes_cannot_bypass_real_boundary(self):
        for case in ("fragment","foreign_dropin","foreign_loaded_dropin","managed_retry","activation"):
            with self.subTest(case=case):
                f = di.Fixture(Path(self.temp.name)/case)
                drop = None
                if case == "managed_retry":
                    directory = f.f.home/".config/systemd/user"/(rt.UNIT+".d")
                    guard,drop = directory/"check-tailnet.py",directory/"quick-deploy-retry.conf"
                    f.t.put(guard,(di.MODULE/"service/check-tailnet.py").read_bytes())
                    f.t.put(drop,rt._retry_bytes(guard,f.f.conf.parent))
                    f.t.props["DropInPaths"] = str(drop)
                    f.t.props["ExecStartPre"] += " "+rf.command("/usr/bin/python3",f"/usr/bin/python3 {guard} {f.f.conf.parent}")
                self.retained(f)
                before = self.snapshot(f)
                if case == "fragment":
                    f.t.put(rf.FRAGMENT,di.REFERENCE_UNIT+b"Sockets=fixture.socket\n",uid=0)
                elif case == "foreign_dropin":
                    path = f.f.home/".config/systemd/user"/(rt.UNIT+".d")/"foreign-sockets.conf"
                    f.t.put(path,b"[Service]\nSockets=fixture.socket\n")
                    f.t.props["DropInPaths"] = str(path)
                elif case == "foreign_loaded_dropin":
                    f.t.props["DropInPaths"] = "/fixture/runtime-unit.d/foreign-sockets.conf"
                elif case == "managed_retry":
                    f.t.put(drop,f.t.files[drop]+b"Sockets=fixture.socket\n")
                else:
                    # A loaded socket association is independently visible as
                    # TriggeredBy even if the human SHOW text is falsely empty.
                    f.kernel.typed["TriggeredBy"] = {"type":"as","data":["fixture.socket"]}
                with mock.patch.object(ic,"create",side_effect=AssertionError("hostile socket policy must not publish")), self.assertRaises(Refusal):
                    self.recover(f)
                self.assertEqual(self.snapshot(f),before)
                self.assertEqual(f.kernel.controls,[])

    def test_restarted_attested_attempt_is_also_admitted(self):
        f = self.retained(noop=False)
        self.assertTrue(self.recover(f)["ok"])
        self.assertEqual(f.kernel.controls,["stop","start"])

    def test_admission_refusals_have_no_side_effects(self):
        cases = ("owner", "no_attempt", "active", "no_desktop", "stage", "prepared", "enrollment", "binding", "backup", "disk",
                 "foreign_receipt", "foreign_issuer", "busy", "binary", "exe", "proc_root", "missing_proc", "hooks", "global_hooks", "not_confirmed", "replay_sender")
        for case in cases:
            with self.subTest(case=case):
                f = self.retained(di.Fixture(Path(self.temp.name)/case))
                folder = f.controller/"state"
                state = json.loads((folder/"reservation.json").read_bytes())
                meta = json.loads((folder/"operation.json").read_bytes())
                receipt = json.loads((folder/"start-receipt.json").read_bytes())
                proc = Path("/proc")/f.t.props["MainPID"]
                if case == "owner": meta["lease"]["owner_digest"] = "f"*64
                elif case == "no_attempt": state["attempt_revision"] = None
                elif case == "active": state["active_observed"] = True
                elif case == "no_desktop": state["desktop"] = None
                elif case == "stage": meta["lease"]["stage"] = "releasing"
                elif case == "prepared": meta["lease"]["prepared_revision"] = state["attempt_revision"]
                elif case == "enrollment": meta["enrollment"]["revision"] = state["attempt_revision"]
                elif case == "binding": state["binding"]["generation"]["pid"] += 1
                elif case == "backup": (folder/"prepare-backup.conf").write_bytes(b"unexplained")
                elif case == "disk": f.f.conf.write_bytes(f.f.conf.read_bytes()+b"# unexplained\n")
                elif case == "foreign_receipt": receipt["receipt"]["host_uniqueid"] = "11223344-1234-4321-abcd-0123456789ab"
                elif case == "foreign_issuer": receipt["issuance"]["enrollment_revision"] += 1
                elif case == "busy": f.f.app = 123
                elif case == "binary": f.t.put(rt.EXECUTABLE,b"foreign",uid=0)
                elif case == "exe": f.t.exe_link = "/foreign"
                elif case == "proc_root": f.t.denied.add(proc/"environ")
                elif case == "missing_proc": f.t.files.pop(proc/"stat")
                elif case in ("hooks","global_hooks"):
                    apps = json.loads(f.f.apps.read_bytes())
                    if case == "hooks": apps["apps"][0]["prep-cmd"] = [{"do":"outside-command"}]
                    else: apps["env"] = {}; f.f.conf.write_bytes(f.f.conf.read_bytes()+b'global_prep_cmd = [{"do":"outside-command"}]\n')
                    f.f.apps.write_bytes(ic.document(apps))
                (folder/"reservation.json").write_bytes(ic.document(state))
                (folder/"operation.json").write_bytes(ic.document(meta))
                (folder/"start-receipt.json").write_bytes(ic.document(receipt))
                before = self.snapshot(f)
                with self.assertRaises((Refusal, ValueError, KeyError)):
                    self.recover(f, lambda: case not in ("not_confirmed","replay_sender"))
                self.assertEqual(self.snapshot(f),before)
                self.assertEqual(f.kernel.controls,[])

    def test_each_additional_policy_or_unknown_fact_refuses_before_stop(self):
        cases = [(key,"foreign" if value != "foreign" else "") for key,value in rt.RETIREMENT_POLICY.items()]
        cases += [("missing",None),("jobs",None),("cgroup-v1",None),("cgroup-outside",None)]
        for key,value in cases:
            with self.subTest(key=key):
                f = self.retained(di.Fixture(Path(self.temp.name)/key))
                if key == "missing": del f.kernel.props["KillMode"]
                elif key == "jobs": f.kernel.jobs["data"] = [[[1,rt.UNIT,"start","waiting","/job/1","/unit/x"]]]
                elif key == "cgroup-v1": f.t.files.pop(rt.CGROUP_ROOT/"cgroup.controllers")
                elif key == "cgroup-outside": f.t.put(Path("/proc")/f.t.props["MainPID"]/"cgroup",b"0::/outside\n")
                elif key == "Id": f.t.props[key] = value
                elif key in ("ExecStop","ExecStopPost"):
                    f.kernel.typed[key]["data"] = [["/fixture/hook",["/fixture/hook"],False,0,0,0,0,0,0,0]]
                else: f.kernel.props[key] = value
                before = self.snapshot(f)
                with self.assertRaises((Refusal,FileNotFoundError)):
                    self.recover(f)
                self.assertEqual(self.snapshot(f),before)
                self.assertEqual(f.kernel.controls,[])

    def test_stop_ack_pid_change_or_empty_top_level_is_not_extinction(self):
        cases = ("stop_ack", "main_changed_child_alive", "descendant", "path_reused", "old_alive", "old_denied", "job", "fdstore", "activation", "controlpid", "restart_transition", "boot")
        for case in cases:
            with self.subTest(case=case):
                f = self.retained(di.Fixture(Path(self.temp.name)/case))
                kernel = f.kernel
                oldpid = f.t.props["MainPID"]
                oldstat = f.t.files[Path("/proc")/oldpid/"stat"]
                def defeat(action):
                    if action != "stop": return
                    if case in ("stop_ack","main_changed_child_alive"):
                        f.t.props.update(ActiveState="active",SubState="running",MainPID="999",InvocationID="f"*32)
                    if case in ("main_changed_child_alive","descendant"):
                        # Root membership is empty, but a child scope still owns
                        # accepted request work. Hierarchical events stay 1.
                        f.t.put(kernel.path/"cgroup.procs",b"")
                        f.t.put(kernel.path/"worker/cgroup.procs",b"999\n")
                        f.t.put(kernel.path/"cgroup.events",b"populated 1\nfrozen 0\n")
                    elif case == "path_reused": f.t.ids[kernel.path] = replace(f.t.ids[kernel.path],inode=99999)
                    elif case in ("old_alive","old_denied"):
                        f.t.put(Path("/proc")/oldpid/"stat",oldstat)
                        if case == "old_denied": f.t.denied.add(Path("/proc")/oldpid/"stat")
                    elif case == "job": kernel.jobs["data"] = [[[1,rt.UNIT,"start","waiting","/job/1","/unit/x"]]]
                    elif case == "fdstore": kernel.props["NFileDescriptorStore"] = "1"
                    elif case == "activation": kernel.props["TriggeredBy"] = "sunshine.socket"
                    elif case == "controlpid": kernel.props["ControlPID"] = "999"
                    elif case == "restart_transition": f.t.props["SubState"] = "auto-restart"
                    elif case == "boot": f.t.put(Path("/proc/sys/kernel/random/boot_id"),b"33333333-3333-3333-3333-333333333333\n",uid=0)
                kernel.after_control = defeat
                with self.assertRaises((Refusal,PermissionError)):
                    self.recover(f)
                self.assertEqual(kernel.controls,["stop"])
                self.assertFalse(list((f.controller/"state").glob("attempt-stopped-*.bak")))
                self.assertFalse(list((f.controller/"state").glob("attempt-start-issued-*.bak")))
                self.assertEqual(json.loads((f.controller/"state/reservation.json").read_bytes())["phase"],"reconciliation_required")

    def test_positive_removed_scope_is_witnessed_before_path_reuse(self):
        f = self.retained()
        k = f.kernel
        def remove(action):
            if action != "stop": return
            for path in list(f.t.files):
                if k.path in path.parents:
                    f.t.files.pop(path); f.t.ids.pop(path,None)
            f.t.dirs.remove(k.path); f.t.ids.pop(k.path)
            k.removed = True
        k.after_control = remove
        self.assertTrue(self.recover(f)["ok"])
        record = json.loads(next((f.controller/"state").glob("attempt-stopped-*.bak")).read_bytes())
        self.assertEqual(record["witness"]["cgroup_retirement"],"removed")
        self.assertEqual(k.controls,["stop","start"])

    def test_uncertain_control_never_repeated_even_if_terminal_or_successor(self):
        for fault in ("stop-before","stop-after","start-before","start-after"):
            with self.subTest(fault=fault):
                f = self.retained(di.Fixture(Path(self.temp.name)/fault))
                f.kernel.fault = fault
                with self.assertRaises(Refusal): self.recover(f)
                controls = list(f.kernel.controls)
                f.kernel.fault = None
                before = self.snapshot(f)
                with self.assertRaisesRegex(Refusal,"sender completion is unknown"):
                    self.recover(f,lambda: (_ for _ in ()).throw(AssertionError("no reauthorization/reissue")))
                self.assertEqual(f.kernel.controls,controls)
                self.assertEqual(self.snapshot(f),before)

    def test_surviving_delayed_control_sender_is_not_fenced_by_stopped_snapshot(self):
        f = self.retained()
        # Simulate an orphan issuer that has not yet sent its final STOP. It
        # can send after a terminal/no-jobs snapshot. Only a completed/reaped
        # issuer record permits proceeding; a killed parent leaves none.
        f.kernel.fault = "stop-after"
        with self.assertRaises(Refusal): self.recover(f)
        self.assertEqual(f.t.props["MainPID"],"0")
        self.assertEqual(f.kernel.jobs["data"],[[]])
        f.kernel.fault = None
        # A naive terminal-snapshot continuation would start a successor that
        # this surviving sender can still stop. Show the concrete mock effect.
        sender = {"alive":True,"held_stop":True}
        naive_successor = {"running":True}
        if sender["alive"] and sender["held_stop"]:
            naive_successor["running"] = False
        self.assertFalse(naive_successor["running"])
        with self.assertRaisesRegex(Refusal,"sender completion is unknown"): self.recover(f)
        self.assertEqual(f.kernel.controls,["stop"])
        self.assertFalse(list((f.controller/"state").glob("attempt-start-issued-*.bak")))

    def test_cuts_resume_only_with_durable_control_completion(self):
        points = ("transition","stop-issued","stop-completed","stopped","start-issued","start-completed","successor","committed",
                  "archive-reservation.json","archive-operation.json","archive-start-receipt.json","archive-prepare-backup.conf",
                  "sentinel","new-operation.json","blocked","new-start-receipt.json","new-prepare-backup.conf","final-replace","final-fsync")
        for point in points:
            with self.subTest(point=point):
                f = self.retained(di.Fixture(Path(self.temp.name)/point))
                create,rename,write,replace_os,fsync = ic.create,ic.os.rename,lc.PrivateFiles.write,ic.os.replace,ic.os.fsync
                final,cut = [False],[False]
                def stop(): cut[0] = True; raise RuntimeError("private crash cut")
                def publish(path,data,mode=0o600):
                    create(path,data,mode)
                    if ((path.name.startswith("attempt-"+point+"-")) or (point == "sentinel" and path.name == "reservation.json")
                            or point == "new-"+path.name): stop()
                def move(src,dst,**kw):
                    rename(src,dst,**kw)
                    if point == "archive-"+src: stop()
                def record(files,name,data,**kw):
                    isfinal = name == "reservation.json" and json.loads(data)["phase"] == "available"
                    if isfinal: final[0] = True
                    write(files,name,data,**kw)
                    if point == "blocked" and name == "reservation.json" and not isfinal: stop()
                def replace_call(src,dst,**kw):
                    replace_os(src,dst,**kw)
                    if point == "final-replace" and final[0]: stop()
                def sync(fd):
                    fsync(fd)
                    if point == "final-fsync" and final[0] and os.fstat(fd).st_ino == (f.controller/"state").stat().st_ino: stop()
                with mock.patch.object(ic,"create",side_effect=publish), mock.patch.object(ic.os,"rename",side_effect=move), \
                     mock.patch.object(lc.PrivateFiles,"write",record), mock.patch.object(ic.os,"replace",side_effect=replace_call), \
                     mock.patch.object(ic.os,"fsync",side_effect=sync):
                    with self.assertRaises(RuntimeError): self.recover(f)
                self.assertTrue(cut[0])
                before = self.snapshot(f)
                if not final[0]:
                    self.assert_blocked(f)
                    self.assertEqual(self.snapshot(f),before)
                if point in ("stop-issued","start-issued"):
                    controls = list(f.kernel.controls)
                    with self.assertRaisesRegex(Refusal,"sender completion is unknown"): self.recover(f)
                    self.assertEqual(f.kernel.controls,controls)
                    self.assertEqual(self.snapshot(f),before)
                else:
                    self.assertTrue(self.recover(f,lambda: (_ for _ in ()).throw(AssertionError("no second confirmation")))["ok"])
                    self.assertEqual(f.kernel.controls,["stop","start"])

    def assert_blocked(self,f):
        # The actual accepted base installer must not ENROLL a compound cut.
        import io
        stream = lambda: types.SimpleNamespace(buffer=io.BytesIO(),isatty=lambda: True)
        stdin,stdout,stderr = stream(),stream(),stream()
        with mock.patch.object(ic.pwd,"getpwuid",return_value=types.SimpleNamespace(pw_dir=str(f.f.home))), \
             mock.patch.object(rt,"LocalTransport",return_value=f.t), \
             mock.patch.object(host,"serverinfo",side_effect=lambda *a: f.f.endpoint(*a)):
            rc = self.base_dispatcher.rpc_main(enroll=True,manifest=f.manifest(),stdin=stdin,stdout=stdout,stderr=stderr,
                                                interactive=lambda: True)
        self.assertEqual(rc,1,stdout.buffer.getvalue())
        # host/lifecycle ordinary functions are byte-unchanged by this feature.
        for action in ("list","acquire","enroll"):
            try:
                if action == "list": reply = f.rpc(action)
                elif action == "enroll": reply = f.rpc(action,revision=1)
                else: reply = f.rpc(action,revision=1,snapshot="f"*64)
            except Refusal: continue
            self.assertFalse(reply.get("selection_ready") if action == "list" else reply.get("ok"),reply)

    def test_last_external_call_mutations_refuse_zero_availability(self):
        targets = [("archive-"+name,fault) for name in ic.RELEASE_RECORDS for fault in ("missing","bytes","inode","mode","mtime")]
        targets += [("live-"+name,fault) for name in ic.RELEASE_RECORDS for fault in ("missing","bytes","inode")]
        targets += [(name,fault) for name in ("journal","descriptor","stopped","stop-completed","start-completed","history","install-lock","operation-lock","reservation-lock","reference") for fault in ("missing","bytes","inode")]
        for target,fault in targets:
            with self.subTest(target=target,fault=fault):
                f = self.retained(di.Fixture(Path(self.temp.name)/(target+"-"+fault)))
                folder = f.controller/"state"
                ic.create(folder/"older-history.bak",b"preserved older recovery\n")
                create,realpath,write = ic.create,f.t.realpath,lc.PrivateFiles.write
                sealed,injected,writes = [],[],[]
                def publish(path,data,mode=0o600):
                    create(path,data,mode)
                    if path.name.startswith("attempt-committed-"): sealed.append(path)
                def change(path):
                    injected.append(path)
                    if fault == "missing": path.unlink()
                    elif fault == "bytes": path.write_bytes(path.read_bytes()+b" ")
                    elif fault == "inode":
                        data = path.read_bytes(); path.rename(f.root/"moved-original"); create(path,data)
                    elif fault == "mode": path.chmod(0o400)
                    else:
                        s = path.stat(); os.utime(path,ns=(s.st_atime_ns,s.st_mtime_ns+1))
                def last_call(observed):
                    result = realpath(observed)
                    # directory(parent) ends bind(): this is the LAST external
                    # transport operation, including kernel reads, after the
                    # descriptor and final endpoint/runtime qualification.
                    if sealed and not injected and Path(observed) == f.kernel.path.parent:
                        journal = json.loads(next(folder.glob("attempt-transition-*.bak")).read_bytes())
                        if target.startswith("archive-"): path = folder/(target[8:]+"."+journal["id"]+".bak")
                        elif target.startswith("live-"): path = folder/target[5:]
                        elif target == "journal": path = next(folder.glob("attempt-transition-*.bak"))
                        elif target == "descriptor": path = sealed[0]
                        elif target in ("stopped","stop-completed","start-completed"): path = next(folder.glob("attempt-"+target+"-*.bak"))
                        elif target == "history": path = folder/"older-history.bak"
                        elif target == "reference": path = sorted((f.controller/"active").iterdir())[-1]
                        elif target == "install-lock": path = f.controller/".install.lock"
                        elif target == "operation-lock": path = folder/"operation.lock"
                        else: path = folder/"reservation.lock"
                        change(path)
                    return result
                def count(files,name,data,**kw):
                    if name == "reservation.json" and json.loads(data)["phase"] == "available": writes.append(data)
                    return write(files,name,data,**kw)
                f.t.realpath = last_call
                with mock.patch.object(ic,"create",side_effect=publish),mock.patch.object(lc.PrivateFiles,"write",count):
                    with self.assertRaises((Refusal,OSError,ValueError)):
                        self.recover(f)
                self.assertEqual(len(injected),1)
                self.assertEqual(writes,[])

    def test_busy_or_identity_drift_after_stop_does_not_publish_or_repeat(self):
        for case in ("busy","uuid","disk","root","binary"):
            with self.subTest(case=case):
                f = self.retained(di.Fixture(Path(self.temp.name)/case))
                endpoint = f.f.endpoint
                def drift(action):
                    if action != "start": return
                    if case == "busy": f.f.app = 123
                    elif case == "uuid":
                        f.f.endpoint = lambda *a: lc.Endpoint("11223344-1234-4321-abcd-0123456789ab","FREE",0)
                    elif case == "disk": f.f.conf.write_bytes(f.f.conf.read_bytes()+b"# drift\n")
                    elif case == "root": f.t.put(Path("/proc")/f.t.props["MainPID"]/"environ",b"HOME=/foreign\0")
                    else: f.t.put(rt.EXECUTABLE,b"foreign",uid=0)
                f.kernel.after_control = drift
                with self.assertRaises(Refusal): self.recover(f)
                self.assertEqual(f.kernel.controls,["stop","start"])
                self.assert_blocked(f)
                with self.assertRaises(Refusal): self.recover(f)
                self.assertEqual(f.kernel.controls,["stop","start"])

    def test_queued_old_work_caller_death_and_free_remain_blocked_until_extinction(self):
        for surviving in (False,True):
            with self.subTest(surviving=surviving):
                f = self.retained(di.Fixture(Path(self.temp.name)/str(surviving)))
                work = {"accepted":True,"caller_alive":False,"executor_alive":True,"dispatched":False}
                self.assertEqual(f.f.endpoint(**lf.TARGET).state,"FREE")
                self.assert_blocked(f)
                def retire(action):
                    if action == "stop":
                        if surviving:
                            f.t.put(f.kernel.path/"cgroup.events",b"populated 1\nfrozen 0\n")
                        else: work["executor_alive"] = False
                f.kernel.after_control = retire
                if surviving:
                    with self.assertRaises(Refusal): self.recover(f)
                    self.assertEqual(f.kernel.controls,["stop"])
                    self.assert_blocked(f)
                else:
                    self.assertTrue(self.recover(f)["ok"])
                    self.assertEqual(f.kernel.controls,["stop","start"])
                # Only an executor could deliver the held accepted work; an
                # encrypted byte queue/dead caller has no new authenticated sender.
                if work["executor_alive"]: work["dispatched"] = True
                self.assertEqual(work["dispatched"],surviving)

    def test_committed_replay_allows_future_lease_receipt_source_and_refuses_rollback(self):
        import shutil
        f = self.retained()
        original = (f.controller/"state/reservation.json").read_bytes()
        self.assertTrue(self.recover(f)["ok"])
        menu = f.rpc("list")
        owner = f.rpc("acquire",revision=menu["revision"],snapshot=menu["snapshot"])
        self.assertTrue(self.recover(f)["ok"])
        prepared = f.rpc("prepare",revision=owner["revision"],capability=owner["capability"],snapshot=owner["snapshot"],display_id=lf.FIRST)
        self.assertTrue(prepared["launch_authorized"])
        self.assertTrue(self.recover(f)["ok"])
        f.rpc("abort",revision=prepared["revision"],capability=prepared["capability"])
        source = f.root/"later-source"
        shutil.copytree(f.source,source)
        with (source/"host.py").open("a") as stream: stream.write("\n# legitimate later private publication\n")
        f.source = source
        f.install()
        self.assertTrue(self.recover(f)["ok"])
        before = self.snapshot(f)
        (f.controller/"state/reservation.json").write_bytes(original)
        with self.assertRaises(Refusal): self.recover(f)
        self.assertEqual(f.kernel.controls,["stop","start"])

    def test_resealed_successor_cannot_redefine_original_floor_or_enrollment(self):
        for fault in ("lower_floor","higher_floor","enrollment","blocked_owner","missing_receipt","same_generation"):
            with self.subTest(fault=fault):
                f = self.retained(di.Fixture(Path(self.temp.name)/fault))
                self.recover(f)
                folder = f.controller/"state"
                path = next(folder.glob("attempt-successor-*.bak"))
                obj = json.loads(path.read_bytes())
                if fault in ("lower_floor","higher_floor"):
                    state = json.loads(bytes.fromhex(obj["replacement"]["reservation.json"]))
                    state["revision"] += -1 if fault == "lower_floor" else 1
                    obj["replacement"]["reservation.json"] = ic.document(state).hex()
                    (folder/"reservation.json").write_bytes(ic.document(state))
                elif fault == "enrollment":
                    operation = json.loads(bytes.fromhex(obj["replacement"]["operation.json"]))
                    operation["enrollment"]["revision"] += 1
                    obj["replacement"]["operation.json"] = ic.document(operation).hex()
                elif fault == "blocked_owner":
                    blocked = json.loads(bytes.fromhex(obj["blocked"])); blocked["owner_digest"] = "f"*64
                    obj["blocked"] = ic.document(blocked).hex()
                elif fault == "missing_receipt": del obj["replacement"]["start-receipt.json"]
                else:
                    intent = json.loads(next(folder.glob("attempt-transition-*.bak")).read_bytes())
                    obj["evidence"]["generation"] = intent["admission"]["evidence"]["generation"]
                path.write_bytes(ic.document(obj))
                descriptor = next(folder.glob("attempt-committed-*.bak"))
                seal = json.loads(descriptor.read_bytes()); seal["proofs"]["successor"] = ic.record_identity(path)
                descriptor.write_bytes(ic.document(seal))
                before = self.snapshot(f)
                with self.assertRaises(Refusal): self.recover(f)
                self.assertEqual(self.snapshot(f),before)
                self.assertEqual(f.kernel.controls,["stop","start"])

    @staticmethod
    def bounded_config(size):
        data = (b"address_family = ipv4\nbind_address = 100.100.20.30\ncapture = kms\n"
                b"encoder = nvenc\noutput_name = eDP-1\n")
        assert size >= len(data)+2
        return data+b"#"+b"x"*(size-len(data)-2)+b"\n"

    def resize_private_context(self, f, size):
        # Rebuild a coherent synthetic original context for size probing only;
        # no production receipt is reconstructed and no service is invoked.
        data = self.bounded_config(size)
        f.f.conf.write_bytes(data)
        folder = f.controller/"state"
        (folder/"prepare-backup.conf").write_bytes(data)
        value = json.loads((folder/"start-receipt.json").read_bytes())
        value["receipt"]["config"] = {"identity":asdict(rt.FileIdentity.from_stat(f.f.conf.stat())),"sha256":cfg.digest(data)}
        (folder/"start-receipt.json").write_bytes(ic.document(value))
        value = json.loads((folder/"reservation.json").read_bytes())
        value["binding"]["config_digest"] = cfg.digest(data)
        (folder/"reservation.json").write_bytes(ic.document(value))
        f.t.current_trace()

    def test_serialization_ceiling_threshold_normal155_and_all_phase_bounds(self):
        f = self.retained()
        model = ic.attempt_record_bounds
        captured = []
        class Probe(Exception): pass
        def probe(installed, records, journal):
            captured.append(model(installed,records,journal))
            raise Probe()
        def bounds(size):
            self.resize_private_context(f,size)
            with mock.patch.object(ic,"attempt_record_bounds",side_effect=probe),self.assertRaises(Probe):
                self.recover(f)
            self.assertEqual(f.kernel.controls,[])
            self.assertFalse(list((f.controller/"state").glob("attempt-*.bak")))
            return captured[-1]
        low, high = 155, 16753
        self.assertLess(bounds(low)["successor"],lc.MAX_PRIVATE)
        self.assertGreater(bounds(high)["successor"],lc.MAX_PRIVATE)
        while high-low > 1:
            middle = (low+high)//2
            if bounds(middle)["successor"] <= lc.MAX_PRIVATE: low = middle
            else: high = middle
        below, above = bounds(low), bounds(high)
        self.assertIn(below["successor"],(lc.MAX_PRIVATE-1,lc.MAX_PRIVATE))
        self.assertGreater(above["successor"],lc.MAX_PRIVATE)
        before = self.snapshot(f)
        with mock.patch.object(ic,"create",side_effect=AssertionError("budget refusal must not publish")), \
             self.assertRaisesRegex(Refusal,"successor serialization ceiling"):
            self.recover(f)
        self.assertEqual(self.snapshot(f),before)
        self.assertEqual(f.kernel.controls,[])
        self.assertFalse(list((f.controller/"state").glob("attempt-*.bak")))
        self.resize_private_context(f,low)
        actual_bounds = []
        def remember(*args):
            result = model(*args); actual_bounds.append(result); return result
        with mock.patch.object(ic,"attempt_record_bounds",side_effect=remember):
            self.assertTrue(self.recover(f)["ok"])
        self.assertEqual(f.kernel.controls,["stop","start"])
        self.assertEqual(set(actual_bounds[0]),{"intent","reservation",*ic.ATTEMPT_PHASES})
        folder = f.controller/"state"
        for key in ("transition",*ic.ATTEMPT_PHASES):
            path = next(folder.glob("attempt-"+key+"-*.bak"))
            budget_key = "intent" if key == "transition" else key
            self.assertLessEqual(path.stat().st_size,actual_bounds[0][budget_key])
            self.assertLessEqual(path.stat().st_size,lc.MAX_PRIVATE)
            self.assertEqual(ic.private_read(path,lc.MAX_PRIVATE),ic.document(json.loads(path.read_bytes())))
        # Normal155-byte input still completes the same one-control ceremony.
        normal = di.Fixture(Path(self.temp.name)/"normal155")
        normal.f.conf.write_bytes(self.bounded_config(155))
        self.retained(normal)
        self.assertTrue(self.recover(normal)["ok"])
        self.assertEqual(normal.kernel.controls,["stop","start"])

    def test_large_config_to_ordinary_one_mib_refuses_without_intent_or_controls(self):
        for size in (16753,rt.MAX_CONFIG):
            with self.subTest(size=size):
                f = di.Fixture(Path(self.temp.name)/str(size))
                f.f.conf.write_bytes(self.bounded_config(size))
                self.retained(f)  # Genuine ordinary install/prepare/attempt accepts it.
                self.assertEqual((f.controller/"state/prepare-backup.conf").stat().st_size,size)
                before = self.snapshot(f)
                originals = {name:ic.record_identity(f.controller/"state"/name) for name in ic.RELEASE_RECORDS}
                for _ in range(2):
                    with mock.patch.object(ic,"create",side_effect=AssertionError("budget refusal must not publish")), \
                         self.assertRaisesRegex(Refusal,"successor serialization ceiling"):
                        self.recover(f)
                    self.assertEqual(self.snapshot(f),before)
                    self.assertEqual({name:ic.record_identity(f.controller/"state"/name) for name in ic.RELEASE_RECORDS},originals)
                    self.assertEqual(f.kernel.controls,[])
                    self.assertFalse(list((f.controller/"state").glob("attempt-*.bak")))

    def test_full_receipt_and_maximum_reference_metadata_are_budgeted(self):
        for case in ("receipt", "references"):
            with self.subTest(case=case):
                f = self.retained(di.Fixture(Path(self.temp.name)/case))
                if case == "receipt":
                    path = f.controller/"state/start-receipt.json"
                    raw = path.read_bytes(); path.write_bytes(raw+b" "*(lc.MAX_PRIVATE-len(raw)))
                    self.assertEqual(path.stat().st_size,lc.MAX_PRIVATE)
                else:
                    version,_ = ic.active(f.controller)
                    for serial in range(2,513):
                        ic.create(f.controller/"active"/f"{serial:08d}.json",ic.document({"schema":1,"version":version}))
                before = self.snapshot(f)
                with self.assertRaisesRegex(Refusal,"serialization ceiling"):
                    self.recover(f)
                self.assertEqual(self.snapshot(f),before)
                self.assertEqual(f.kernel.controls,[])
                self.assertFalse(list((f.controller/"state").glob("attempt-*.bak")))
        # The largest admitted original revision also projects a readable R+1;
        # known metadata widths are serialized, never covered by spare padding.
        f = self.retained(di.Fixture(Path(self.temp.name)/"max-revision"))
        folder = f.controller/"state"
        state = json.loads((folder/"reservation.json").read_bytes())
        meta = json.loads((folder/"operation.json").read_bytes())
        receipt = json.loads((folder/"start-receipt.json").read_bytes())
        state.update(revision=rs.MAX_REVISION-1,attempt_revision=rs.MAX_REVISION-2)
        meta["lease"]["prepared_revision"] = rs.MAX_REVISION-3
        meta["enrollment"]["revision"] = rs.MAX_REVISION-4
        receipt["issuance"]["enrollment_revision"] = rs.MAX_REVISION-4
        for name,value in (("reservation.json",state),("operation.json",meta),("start-receipt.json",receipt)):
            (folder/name).write_bytes(ic.document(value))
        self.assertEqual(self.recover(f)["revision"],rs.MAX_REVISION)
        self.assertEqual(f.kernel.controls,["stop","start"])
        identity = ic.attempt_identity_ceiling(lc.MAX_PRIVATE,os.geteuid())
        ic.attempt_identity_domain(identity,lc.MAX_PRIVATE,os.geteuid())
        for key in ("device","inode","mtime_ns"):
            invalid = {**identity,key:identity[key]+1}
            with self.assertRaises(Refusal): ic.attempt_identity_domain(invalid,lc.MAX_PRIVATE,os.geteuid())

    def test_permanent_installation_and_operation_locks_serialize_authority(self):
        f = self.retained()
        before = self.snapshot(f)
        with ic.installation_lock(f.controller):
            with self.assertRaises(Refusal): self.recover(f)
        self.assertEqual(self.snapshot(f),before)
        with lc.PrivateFiles(f.controller/"state").locked():
            with self.assertRaises(Refusal): self.recover(f)
        self.assertEqual(self.snapshot(f),before)
        self.assertEqual(f.kernel.controls,[])

    def test_shim_source_manifest_cli_and_fixed_environment_contract(self):
        self.assertNotIn("attempt_retirement_linux.py",ic.SOURCES)
        self.assertNotIn(b"reconcile-attempted-launch",ic.SHIM)
        env = rt.RetirementTransport.environment(types.SimpleNamespace(home=Path("/nss/home"),uid=1234))
        self.assertEqual(env["HOME"],"/nss/home")
        self.assertEqual(env["XDG_RUNTIME_DIR"],"/run/user/1234")
        self.assertEqual(env["DBUS_SESSION_BUS_ADDRESS"],"unix:path=/run/user/1234/bus")
        self.assertNotIn("XDG_CONFIG_HOME",env)
        self.assertNotIn("XDG_DATA_HOME",env)
        self.assertNotIn("XDG_CACHE_HOME",env)
        f = self.retained()
        self.assertEqual(set(json.loads((f.manifest().parent/".owned.json").read_bytes())["files"]),set(ic.SOURCES)|{"host-settings.json"})
        # Source-only code differing from the installed immutable authority must
        # refuse, without service controls or repair records.
        with mock.patch.object(ic,"source_read",return_value=b"unpublished"):
            with self.assertRaisesRegex(Refusal,"publish this exact source"): self.recover(f)
        self.assertEqual(f.kernel.controls,[])


if __name__ == "__main__":
    unittest.main()
