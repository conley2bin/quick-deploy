#!/usr/bin/env python3
"""Known legacy STOP -> independent START -> normal publish -> zero controls.

All native facts are private transport fixtures. Raw event/SHOW/typed-property
shapes follow the captured producer grammar; actor assertions here are explicitly
synthetic facts, never a claim about the real operator's unanswered account.
"""
from contextlib import ExitStack
from dataclasses import asdict, is_dataclass
import ast
from datetime import datetime, timedelta, timezone
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path[:0] = [str(Path(__file__).resolve().parents[1]/"display-control"), str(Path(__file__).resolve().parent)]
import display_attempt_retirement as ar
import display_install as di
import display_lifecycle as lf
import configuration as cfg
import host
import install_controller as ic
import lifecycle as lc
from protocol import Refusal
import reservation as rs
import runtime_linux as rt

LEGACY = "06e45978a0fb87561fd7e3c6066a5c1dd8841050"
ARCHIVE_COMMIT = "fece0dd9b58bdee2c158e7a6b8a001415fd98672"
ARCHIVE_LATER_COMMIT = "e0f49015677f7e4b9fcdf42108d83d2ee40cc53e"
ARCHIVE_PUBLISHER = "4af7b73adbd39e0768356e5067e5aba9b054551d6ceab58fa8582021ed76a71f"


def utc_ns(value):
    seconds, fraction = divmod(value, 1_000_000_000)
    instant = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=seconds)
    return instant.strftime("%Y-%m-%dT%H:%M:%S") + f".{fraction:09d}+00:00"


class ExternalTests(unittest.TestCase):
    setUp = di.InstallTests.setUp
    retained = ar.RecoveryTests.retained
    snapshot = ar.RecoveryTests.snapshot
    assert_blocked = ar.RecoveryTests.assert_blocked

    def ordinary_retire(self, f, authorize=lambda: True, *, original_certificates=None):
        # These paths are the fixture operator's retained inputs, not discovered
        # from controller state/cache. Explicit () exercises missing-root refusal.
        selected = tuple(getattr(f,"certificate_originals",{}).values()) if original_certificates is None else original_certificates
        with mock.patch.object(ic.pwd,"getpwuid",return_value=types.SimpleNamespace(pw_dir=str(f.f.home))), \
             mock.patch.object(ic,"__file__",str(f.source/"install_controller.py")), \
             mock.patch.object(host,"collect",side_effect=AssertionError("no native capture inventory")):
            return json.loads(ic.local_attempt_retirement(authorize=authorize,transport=f.t,info=f.f.endpoint,original_certificates=selected))

    @classmethod
    def setUpClass(cls):
        ar.RecoveryTests.setUpClass()
        cls.base_dispatcher = ar.RecoveryTests.base_dispatcher
        cls.legacy_bytes = {}
        with tempfile.TemporaryDirectory(prefix="qd-legacy-source-") as home:
            for name in ic.SOURCES:
                raw = subprocess.check_output(("/usr/bin/git", "show", LEGACY+":sunshine-moonlight/display-control/"+name),
                    cwd=di.MODULE, env={"PATH":"/usr/bin:/bin", "HOME":home, "GIT_CONFIG_NOSYSTEM":"1"}, timeout=10)
                if cfg.digest(raw) != ic.EXTERNAL_LEGACY_SOURCE[name]:
                    raise AssertionError("legacy source differs from audited ef471 bytes: "+name)
                cls.legacy_bytes[name] = raw
            cls.archive_bytes = {name:subprocess.check_output(("/usr/bin/git","show",ARCHIVE_COMMIT+":sunshine-moonlight/display-control/"+name),
                cwd=di.MODULE,env={"PATH":"/usr/bin:/bin","HOME":home,"GIT_CONFIG_NOSYSTEM":"1"},timeout=10) for name in ic.SOURCES}
            cls.archive_later_bytes = {name:subprocess.check_output(("/usr/bin/git","show",ARCHIVE_LATER_COMMIT+":sunshine-moonlight/display-control/"+name),
                cwd=di.MODULE,env={"PATH":"/usr/bin:/bin","HOME":home,"GIT_CONFIG_NOSYSTEM":"1"},timeout=10) for name in ic.SOURCES}
        public=cls.archive_bytes["install_controller.py"]
        if cfg.digest(public)!=ARCHIVE_PUBLISHER or cls.archive_later_bytes["install_controller.py"]!=public:
            raise AssertionError("wrong public archival publisher")
        tree=ast.parse(public);document=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="document")
        install=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="install")
        # Execute exactly the old producer's four pure statements in source
        # order: source hash/name, THEN settings insertion, THEN full marker.
        statements=sorted((n for n in ast.walk(install) if isinstance(n,ast.Assign) and
            ((isinstance(n.targets[0],ast.Name) and n.targets[0].id in ("version_hash","version","marker")) or
             (isinstance(n.targets[0],ast.Subscript) and isinstance(n.targets[0].value,ast.Name) and n.targets[0].value.id=="payload"))),key=lambda n:n.lineno)
        if len(statements)!=4:raise AssertionError("old pure publication block changed")
        cls.archive_algorithm=compile(ast.fix_missing_locations(ast.Module(body=[copy.deepcopy(document),*copy.deepcopy(statements)],type_ignores=[])),"<audited-public4af-pure-publication>","exec")

    def archive_version(self, f, variant="", settings=None):
        payload=dict(self.archive_later_bytes if variant else self.archive_bytes)
        if settings is None:
            obj=json.loads(f.manifest().read_bytes());obj["host_uniqueid"]="33333333-3333-4333-8333-333333333333";settings=ic.document(obj)
        ns={"json":json,"cfg":cfg,"RELEASE":1,"payload":payload,"data":settings}
        exec(self.archive_algorithm,ns)
        folder=f.controller/"versions"/ns["version"];ic.make_directory(folder)
        for name,data in ns["payload"].items():ic.create(folder/name,data)
        ic.create(folder/".owned.json",ic.document(ns["marker"]))
        self.assertEqual(ic.validate_version(folder),ns["marker"])
        self.assertEqual(ns["version"],"v1-"+cfg.digest(ic.document({n:cfg.digest(b) for n,b in ns["payload"].items() if n!="host-settings.json"})))
        self.assertNotEqual(ns["version"],"v1-"+cfg.digest(ic.document(ns["marker"]["files"])))
        return folder

    def materialize_legacy(self, f):
        source = f.root/"legacy-source"
        source.mkdir()
        for name, data in self.legacy_bytes.items():
            (source/name).write_bytes(data)
        runtime = types.ModuleType("qd_private_legacy_runtime_"+str(id(f)))
        runtime.__file__ = str(source/"runtime_linux.py")
        sys.modules[runtime.__name__] = runtime
        exec(compile(self.legacy_bytes["runtime_linux.py"], runtime.__file__, "exec"), runtime.__dict__)
        # Identical dataclass ABI, shared with the independently loaded fixture
        # producer. The legacy algorithms and entire legacy source remain exact.
        for name, value in vars(rt).items():
            if isinstance(value, type) and is_dataclass(value):
                setattr(runtime, name, value)
        module = types.ModuleType("qd_private_legacy_installer_"+str(id(f)))
        module.__file__ = str(source/"install_controller.py")
        sys.modules[module.__name__] = module
        exec(compile(self.legacy_bytes["install_controller.py"], module.__file__, "exec"), module.__dict__)
        module.rt = runtime
        f.source = source
        return module

    def paused(self, f=None, *, size=155, legacy=True, native_shape=False):
        f = f or self.fixture
        previous = self.materialize_legacy(f) if legacy else ic
        f.f.conf.write_bytes(ar.RecoveryTests.bounded_config(size))
        self.assertEqual(len(f.f.conf.read_bytes()), size)
        if native_shape:
            original_source=f.source
            f.install(publish=True)
            for n in range(4):
                source=f.root/("prior-source-"+str(n));shutil.copytree(original_source,source)
                (source/"host.py").write_bytes((source/"host.py").read_bytes()+("# historical publication"+str(n)+"\n").encode())
                f.source=source;f.install()
            f.source=original_source;f.install()
            self.assertEqual(ic.active(f.controller)[1],6)
            for n in range(16):ic.create(f.controller/"state"/("preserved-history-"+str(n)+".bak"),("private prior record"+str(n)+"\n").encode())
        self.retained(f)
        k = f.kernel
        def remove(action):
            if action != "stop":
                return
            for path in list(f.t.files):
                if k.path in path.parents:
                    f.t.files.pop(path); f.t.ids.pop(path, None)
            f.t.dirs.remove(k.path); f.t.ids.pop(k.path)
            k.removed = True
            for n in range(558):
                f.t.mkdir(k.path.parent/("unrelated-"+str(n)))
        k.after_control = remove
        listing = f.t.listdir
        def bounded_listing(path):
            result = listing(path)
            if len(result) > 128:
                raise ValueError("directory entry limit exceeded")
            return result
        f.t.listdir = bounded_listing
        with mock.patch.object(ic.pwd, "getpwuid", return_value=types.SimpleNamespace(pw_dir=str(f.f.home))), \
             mock.patch.object(previous, "__file__", str(f.source/"install_controller.py")):
            if legacy:
                with self.assertRaisesRegex(ValueError, "directory entry limit exceeded"):
                    previous.local_attempt_retirement(authorize=lambda: True, transport=f.t, info=f.f.endpoint)
            else:
                # Authentic current authority cut: STOP completion persisted;
                # no STOPPED before independent external action.
                with mock.patch.object(rt.AttemptRetirement, "stopped", side_effect=RuntimeError("private STOP-completed cut")), \
                     self.assertRaises(RuntimeError):
                    ic.local_attempt_retirement(authorize=lambda: True, transport=f.t, info=f.f.endpoint)
        f.t.listdir = listing
        f.old = json.loads(next((f.controller/"state").glob("attempt-transition-*.bak")).read_bytes())
        f.identifier = f.old["id"]
        self.assertEqual(k.controls, ["stop"])
        self.assertEqual(sorted(p.name.split("-"+f.identifier)[0] for p in (f.controller/"state").glob("attempt-*.bak")),
                         ["attempt-stop-completed", "attempt-stop-issued", "attempt-transition"])
        f.native_before = self.snapshot(f)
        return f

    def external_start(self, f):
        """Independent manager lifecycle, not the assistant control adapter."""
        k = f.kernel
        controls = list(k.controls)
        pid = int(k.oldpid)+100
        f.t.restarts += 1
        for suffix, content in k.oldfiles.items():
            f.t.put(Path("/proc")/str(pid)/suffix, content)
        fields = ["S"]+["0"]*18+[str(500+f.t.restarts)]+["0"]*30
        f.t.put(Path("/proc")/str(pid)/"stat", (f"{pid} (sunshine worker)) "+" ".join(fields)+"\n").encode())
        f.t.props.update(MainPID=str(pid), ActiveState="active", SubState="running", InvocationID=f"{pid:032x}",
                        ExecMainStartTimestampMonotonic=str(int(f.t.props["ExecMainStartTimestampMonotonic"])+1000000))
        k.removed = False
        f.t.mkdir(k.path)
        f.t.put(k.path/"cgroup.events", b"populated 1\nfrozen 0\n")
        f.t.put(k.path/"cgroup.procs", (str(pid)+"\n").encode())
        f.t.current_trace()
        self.assertEqual(k.controls, controls)
        self.assertEqual(self.snapshot(f), f.native_before)

    def publish_and_inputs(self, f, *, source=None):
        f.source = source or di.MODULE/"display-control"
        state = f.controller/"state"
        before = {p.name:ic.record_identity(p) for p in state.iterdir()}
        old_source = {p.name:ic.record_identity(p) for p in (f.controller/"versions"/f.old["version"]).iterdir()}
        f.install()  # The ordinary ACTIVE resolver, not a new publisher.
        self.assertEqual({p.name:ic.record_identity(p) for p in state.iterdir()}, before)
        self.assertEqual({p.name:ic.record_identity(p) for p in (f.controller/"versions"/f.old["version"]).iterdir()},old_source)
        installed = host.settings(f.manifest(), "linux")
        with lc.PrivateFiles(f.controller/"state").locked() as files:
            old, records, original_set = ic.external_predecessor(f.controller, installed, files, f.identifier)
        f.original_set = original_set
        evidence, boundary = ic.attempt_sample(installed, records, f.t, f.f.endpoint, old["admission"])
        gen, oldgen = evidence["generation"], old["admission"]["boundary"]["generation"]
        old_boundary = old["admission"]["boundary"]
        # Explicit synthetic wall-clock points consistent with the genuine
        # private original record mtimes, never invented native observations.
        completed_ns = original_set["attempt-stop-completed-"+old["id"]+".bak"]["mtime_ns"]
        kernel_ns = completed_ns + 1000
        anchored_ns = kernel_ns + 1_000_000
        wall = (anchored_ns + 1_000_000 + 999) // 1000  # Manager producer uses integer microseconds.
        kernel_time, anchored_time, context_time = utc_ns(kernel_ns), utc_ns(anchored_ns), utc_ns(wall*1000+2_000_000_000)
        kernel = {"utc":kernel_time,"purpose":"PRIVATE synthetic native-grammar component",
            "components":{"boot":{"value":oldgen["boot_id"]},"old_birth":{"value":"absent"},
                "cgroup_path":{"value":str(f.kernel.path)},"parent_identity":{"value":old_boundary["parent_identity"]},
                "old_scope_identity":{"exception":"FileNotFoundError","detail":"metadata only, NOT authority"}},
            "raw_parent_cardinality":{"count":558,"old_scope_name_present":False}}
        anchored = {"observed_utc":anchored_time,"purpose":"PRIVATE anchored component, not StopWitness",
            "source_snapshot_sha256":"1"*64,"runtime_sha256":ic.EXTERNAL_COMPONENT_RUNTIME,"transaction":old["id"],
            "target":str(f.kernel.path),"expected_parent":old_boundary["parent_identity"],"targeted_scope_removed":True,
            "native_unit_properties":{"MainPID":"0","ControlPID":"0","ControlGroup":"","ActiveState":"inactive","SubState":"dead",
                "Job":"","InvocationID":oldgen["invocation_id"]},"native_unit_query_exit":0,"start_issued_record_exists":False}
        current_scope = asdict(f.t.identity(f.kernel.path))
        context = {"observed_utc":context_time,"purpose":"PRIVATE independently started successor",
            "old_generation":oldgen,"current_boot_id":gen["boot_id"],"same_boot":True,
            "native_unit":{"MainPID":str(gen["pid"]),"ControlPID":"0","NRestarts":"0","InvocationID":gen["invocation_id"],
                "Id":rt.UNIT,"ActiveState":"active","SubState":"running","Job":"","ControlGroup":boundary.cgroup,
                "ExecMainStartTimestampMonotonic":str(gen["start_monotonic_usec"])},
            "current_proc":{"pid":str(gen["pid"]),"birth":gen["start_ticks"],"exe":str(rt.EXECUTABLE),"cgroup":"0::"+boundary.cgroup+"\n"},
            "current_old_path_identity":current_scope,"old_scope_identity":old_boundary["cgroup_identity"]}
        base = {"_BOOT_ID":gen["boot_id"],"_UID":str(gen["uid"]),"_COMM":"systemd","USER_UNIT":rt.UNIT,
                "USER_INVOCATION_ID":gen["invocation_id"],"JOB_TYPE":"start","JOB_ID":"77"}
        lifecycle = {"observed_utc":context_time,"purpose":"PRIVATE scoped manager grammar","journal_exit":0,"journal_stderr":"",
            "manager_events":[{**base,"MESSAGE_ID":"7d4958e842da4a758f6c1cdc7b36dcc5","__CURSOR":"private-start",
                "__MONOTONIC_TIMESTAMP":str(gen["start_monotonic_usec"]-100),"__REALTIME_TIMESTAMP":str(wall)},
                {**base,"MESSAGE_ID":"39f53479d3a045ac8e11786248231fbf","__CURSOR":"private-completed",
                 "__MONOTONIC_TIMESTAMP":str(gen["start_monotonic_usec"]+100),"__REALTIME_TIMESTAMP":str(wall+1000000)}]}
        version, serial = ic.active(f.controller)
        marker = ic.validate_version(f.manifest().parent)
        raw = {"kernel":ic.document(kernel),"anchored":ic.document(anchored),"context":ic.document(context),"lifecycle":ic.document(lifecycle)}
        cert = {"schema":1,"kind":ic.EXTERNAL_KIND,
            "predecessor":{"id":old["id"],"intent_sha256":cfg.digest(ic.document(old)),"records_sha256":cfg.digest(ic.document(original_set)),
                "source_manifest_sha256":cfg.digest(ic.document(old["manifest"])),"settings_sha256":old["manifest"]["files"]["host-settings.json"]},
            "approved_source":{"version":version,"serial":serial,"manifest_sha256":cfg.digest(ic.document(marker)),
                "settings_sha256":marker["files"]["host-settings.json"]},
            "external":{"generation":gen,"job_id":"77","start_cursor":"private-start","completed_cursor":"private-completed"},
            "account":{"actor":"PRIVATE fixture operator","action":"one explicit canonical user-unit Start, fixture action77",
                "issuer":"fixture interactive terminal action77","issuer_completion":"operator observed action77 returned and all scheduled work finished",
                "issuer_completion_brand":"operator_attested_action_finished_not_child_reaping","helpers":[],
                **{k:"none" for k in ("specific_outstanding_work","outside_request_holders","request_fd_handoff","process_migration",
                    "authenticated_replay","other_deviations","policy_config_changes")},"exclusive_window":"renewed_until_commit"},
            "ordering":{"brand":"operator_attested_causal_order","old_invocation":oldgen["invocation_id"],"external_invocation":gen["invocation_id"],
                "components_after_old_admission":True,"old_admission_intent_identity":original_set["attempt-transition-"+old["id"]+".bak"],
                "clock_basis":"same_host_realtime_utc_and_sealed_mtime_ns",
                "scope_absence_before_external_start":True,"unaccounted_clock_changes":False},"unknown":ic.EXTERNAL_UNKNOWN,
            "source_version_prefix":ic.external_version_prefix(f.controller),
            "attachments":{k:{"sha256":cfg.digest(v),"observer":"PRIVATE fixture component observer",
                "clock":{"basis":"UTC","value":json.loads(v)["utc" if k == "kernel" else "observed_utc"]}} for k,v in raw.items()}}
        raw["certificate"] = ic.document(cert)
        folder = f.root/("input-"+f.identifier)
        folder.mkdir()
        f.inputs = {}
        for role, data in raw.items():
            path = folder/(role+".json"); path.write_bytes(data);path.chmod(0o600);f.inputs[role] = path
        if not hasattr(f,"certificate_originals"):f.certificate_originals={}
        f.certificate_originals[old["id"]]=f.inputs["certificate"]
        return f

    def ready(self, f=None, **kw):
        f = self.paused(f, **kw)
        self.external_start(f)
        return self.publish_and_inputs(f)

    def reconcile(self, f, authorize=lambda _: True, *, original_certificates=None, read_only=False):
        current=json.loads(f.inputs["certificate"].read_bytes())["predecessor"]["id"]
        selected=tuple(path for identifier,path in getattr(f,"certificate_originals",{}).items() if identifier!=current) if original_certificates is None else original_certificates
        def reject_control(*args, **kwargs):
            raise AssertionError("ZERO-CONTROL authority attempted service mutation")
        base_run = f.t.run
        def run(argv, **kwargs):
            if argv[0] in ("/usr/bin/systemctl","/bin/systemctl") and any(v in argv for v in ("start","stop","restart","reload","kill","daemon-reload")):
                reject_control()
            return base_run(argv, **kwargs)
        with mock.patch.object(ic.pwd,"getpwuid",return_value=types.SimpleNamespace(pw_dir=str(f.f.home))), \
             mock.patch.object(ic,"__file__",str(f.source/"install_controller.py")), \
             mock.patch.object(rt.AttemptRetirement,"control",side_effect=reject_control), \
             mock.patch.object(rt.LinuxRuntime,"guarded_restart",side_effect=reject_control), \
             mock.patch.object(f.t,"run",side_effect=run), \
             mock.patch.object(host,"collect",side_effect=AssertionError("no native display I/O")):
            args=(f.inputs["certificate"],) if read_only else tuple(f.inputs[k] for k in ("certificate",*ic.EXTERNAL_ROLES))
            return json.loads(ic.local_external_successor(*args,authorize=authorize,transport=f.t,info=f.f.endpoint,original_certificates=selected))

    def change(self, f, role, mutate):
        path = f.inputs[role]
        obj = json.loads(path.read_bytes()); mutate(obj); path.write_bytes(ic.document(obj))
        if role != "certificate":
            self.change(f,"certificate",lambda cert:cert["attachments"][role].update(sha256=cfg.digest(path.read_bytes())))

    def test_authentic_legacy_558_then_independent_start_and_zero_control_commit(self):
        f = self.ready()
        state = f.controller/"state"
        old = (state/"reservation.json").read_bytes()
        writes = []
        write = lc.PrivateFiles.write
        def observe(files,name,data,**kw):
            if name == "reservation.json" and data != ic.RELEASE_BLOCKED and json.loads(data)["phase"] == "available":
                writes.append(data)
            return write(files,name,data,**kw)
        with mock.patch.object(lc.PrivateFiles,"write",observe):
            result = self.reconcile(f)
        self.assertTrue(result["ok"])
        self.assertEqual(result["revision"],json.loads(old)["revision"]+1)
        self.assertEqual(len(writes),1)
        self.assertEqual(f.kernel.controls,["stop"])
        for name in ic.RELEASE_RECORDS:
            self.assertEqual(ic.record_identity(state/(name+"."+f.identifier+".bak")),f.old["records"][name])
        for phase in ic.ATTEMPT_PHASES[2:]:
            self.assertFalse((state/("attempt-"+phase+"-"+f.identifier+".bak")).exists())
        self.assertEqual((state/"start-receipt.json").read_bytes(),(state/("start-receipt.json."+f.identifier+".bak")).read_bytes())
        snap = self.snapshot(f)
        self.assertTrue(self.reconcile(f,lambda _:(_ for _ in ()).throw(AssertionError("replay read-only")))["ok"])
        self.assertEqual(self.snapshot(f),snap)
        self.assertTrue(self.ordinary_retire(f)["ok"])
        self.assertEqual(self.snapshot(f),snap)

    def test_native_sized_25_record_six_reference_155_byte_context(self):
        f=self.ready(native_shape=True)
        self.assertEqual((len(f.original_set),f.old["serial"]),(25,6))
        self.assertEqual(ic.active(f.controller)[1],7)
        self.assertEqual(len((f.controller/"state/prepare-backup.conf").read_bytes()),155)
        baseline=list(f.kernel.controls);writes=[];write=lc.PrivateFiles.write
        def count(files,name,data,**kw):
            if name=="reservation.json" and data!=ic.RELEASE_BLOCKED and json.loads(data)["phase"]=="available":writes.append(data)
            return write(files,name,data,**kw)
        with mock.patch.object(lc.PrivateFiles,"write",count):result=self.reconcile(f)
        self.assertTrue(result["ok"]);self.assertEqual(f.kernel.controls,baseline);self.assertEqual(len(writes),1)
        folder=f.controller/"state"
        for name,identity in f.original_set.items():
            path=folder/(name+"."+f.identifier+".bak") if name in ic.RELEASE_RECORDS else folder/name
            self.assertEqual(ic.record_identity(path),identity)
        intent=json.loads((folder/ic.external_name(f.identifier,"transition")).read_bytes())
        self.assertEqual((intent["serial"],len(intent["original_set"])),(7,25))
        print("NATIVE_SHAPED_PRIVATE_155="+json.dumps({"original_records":25,"old_serial":6,"approved_serial":7,
            "config_bytes":155,"additional_controls":0,"availability_writes":len(writes),"exact_physical_originals":True,
            "intent_bytes":(folder/ic.external_name(f.identifier,"transition")).stat().st_size,
            "successor_bytes":(folder/ic.external_name(f.identifier,"successor")).stat().st_size},sort_keys=True))

    def test_unknown_account_and_unfinished_specific_work_refuse_before_any_write(self):
        f = self.ready()
        original = f.inputs["certificate"].read_bytes()
        changes = [lambda c:c["account"].update(actor="unknown"),lambda c:c["account"].update(action="unaccounted"),
            lambda c:c["account"].update(issuer_completion="pending"),lambda c:c["account"].update(specific_outstanding_work="queued retry"),
            lambda c:c["account"].update(outside_request_holders="unaccounted"),lambda c:c["account"].update(request_fd_handoff="yes"),
            lambda c:c["account"].update(process_migration="yes"),lambda c:c["account"].update(authenticated_replay="unknown"),
            lambda c:c["account"].update(other_deviations="unknown"),lambda c:c["account"].update(policy_config_changes="yes"),
            lambda c:c["account"].update(helpers=[{"identity":"timer17","work":"queued start","completion":"unknown","state":"unfinished"}]),
            lambda c:c["unknown"].update(historical_global_jobs="empty"),lambda c:c["ordering"].update(scope_absence_before_external_start=1)]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                f.inputs["certificate"].write_bytes(original);self.change(f,"certificate",change)
                snap = self.snapshot(f)
                with mock.patch.object(ic,"create",side_effect=AssertionError("pre-write refusal")),self.assertRaises(Refusal):
                    self.reconcile(f)
                self.assertEqual(self.snapshot(f),snap)
        self.assertEqual(f.kernel.controls,["stop"])

    def test_component_and_generation_links_are_not_permission_or_filename_authority(self):
        f = self.ready()
        originals = {k:p.read_bytes() for k,p in f.inputs.items()}
        cases = [("kernel",lambda x:x["components"].update(old_birth={"value":"unknown"})),
            ("anchored",lambda x:x.update(targeted_scope_removed=False)),
            ("anchored",lambda x:x.update(runtime_sha256="f"*64)),
            ("anchored",lambda x:x["expected_parent"].__setitem__(1,999)),
            ("anchored",lambda x:x["native_unit_properties"].update(ActiveState="active",MainPID="42")),
            ("context",lambda x:x["current_proc"].update(birth=999)),
            ("lifecycle",lambda x:x["manager_events"][1].update(JOB_ID="78")),
            ("lifecycle",lambda x:x["manager_events"].append(x["manager_events"][0]))]
        for role,change in cases:
            for key,data in originals.items():f.inputs[key].write_bytes(data)
            self.change(f,role,change); snap=self.snapshot(f)
            with self.subTest(role=role),mock.patch.object(ic,"create",side_effect=AssertionError("pre-write refusal")),self.assertRaises(Refusal):
                self.reconcile(f)
            self.assertEqual(self.snapshot(f),snap)

    def test_fresh_global_jobs_typed_policy_config_and_busy_refuse_without_controls(self):
        for kind in ("jobs","typed","config","busy","old_birth"):
            f = self.ready(di.Fixture(Path(self.temp.name)/kind))
            if kind == "jobs": f.kernel.jobs={"type":"a(usssoo)","data":[[[7,"other.service","start","running","/job/7","/unit/other"]]]}
            elif kind == "typed": f.kernel.typed["NFileDescriptorStore"]={"type":"u","data":1}
            elif kind == "config": f.f.conf.write_bytes(f.f.conf.read_bytes()+b"# drift\n")
            elif kind == "busy": f.f.app = 1
            else:
                old=f.old["admission"]["boundary"]["generation"]
                fields=["S"]+["0"]*18+[str(old["start_ticks"])]+["0"]*30
                f.t.put(Path("/proc")/str(old["pid"])/"stat",(str(old["pid"])+" (old) "+" ".join(fields)+"\n").encode())
            snap=self.snapshot(f)
            with self.subTest(kind=kind),mock.patch.object(ic,"create",side_effect=AssertionError("pre-write refusal")),self.assertRaises(Refusal):
                self.reconcile(f)
            self.assertEqual(self.snapshot(f),snap)

    def test_every_publication_archival_and_final_commit_cut_resumes_zero_controls(self):
        points = ("transition","certificate",*ic.EXTERNAL_ROLES,"successor","committed",
            *("archive-"+n for n in ic.RELEASE_RECORDS),"sentinel","new-operation.json","blocked",
            "new-start-receipt.json","new-prepare-backup.conf","final-replace","final-fsync")
        for point in points:
            with self.subTest(point=point):
                f=self.ready(di.Fixture(Path(self.temp.name)/point))
                create,rename,write,replace_os,fsync=ic.create,ic.os.rename,lc.PrivateFiles.write,ic.os.replace,ic.os.fsync
                final,cut=[False],[False]
                def stop():cut[0]=True;raise RuntimeError("private external crash cut")
                def publish(path,data,mode=0o600):
                    create(path,data,mode)
                    if path.name==ic.external_name(f.identifier,point) or (point=="sentinel" and path.name=="reservation.json") or point=="new-"+path.name:
                        stop()
                def move(src,dst,**kw):
                    rename(src,dst,**kw)
                    if point=="archive-"+src:stop()
                def record(files,name,data,**kw):
                    isfinal=name=="reservation.json" and data!=ic.RELEASE_BLOCKED and json.loads(data)["phase"]=="available"
                    if isfinal:final[0]=True
                    write(files,name,data,**kw)
                    if point=="blocked" and name=="reservation.json" and not isfinal:stop()
                def replace_call(src,dst,**kw):
                    replace_os(src,dst,**kw)
                    if point=="final-replace" and final[0]:stop()
                def sync(fd):
                    fsync(fd)
                    if point=="final-fsync" and final[0] and os.fstat(fd).st_ino==(f.controller/"state").stat().st_ino:stop()
                with mock.patch.object(ic,"create",side_effect=publish),mock.patch.object(ic.os,"rename",side_effect=move), \
                     mock.patch.object(lc.PrivateFiles,"write",record),mock.patch.object(ic.os,"replace",side_effect=replace_call), \
                     mock.patch.object(ic.os,"fsync",side_effect=sync),self.assertRaises(RuntimeError):
                    self.reconcile(f)
                self.assertTrue(cut[0])
                before=self.snapshot(f)
                if not final[0]:
                    self.assert_blocked(f)
                    with self.assertRaises((Refusal,OSError,ValueError)):
                        self.ordinary_retire(f)
                    self.assertEqual(self.snapshot(f),before)
                self.assertTrue(self.reconcile(f,lambda _:(_ for _ in ()).throw(AssertionError("no duplicate ceremony")))["ok"])
                self.assertEqual(f.kernel.controls,["stop"])

    def test_final_after_external_fence_covers_every_old_new_and_input_artifact(self):
        targets=["old-intent","old-stop-issued","old-stop-completed","old-archive","source-old","source-new","reference-old","reference-new",
            "install-lock","operation-lock","reservation-lock","unknown-history","unknown-source","input-certificate",
            *("authority-"+r for r in ("transition","certificate",*ic.EXTERNAL_ROLES,"successor","committed")),
            *("live-"+n for n in ic.RELEASE_RECORDS)]
        for target in targets:
            with self.subTest(target=target):
                f=self.ready(di.Fixture(Path(self.temp.name)/target));folder=f.controller/"state"
                create,realpath,write=ic.create,f.t.realpath,lc.PrivateFiles.write
                sealed,injected,writes=[],[],[]
                def publish(path,data,mode=0o600):
                    create(path,data,mode)
                    if path.name==ic.external_name(f.identifier,"committed"):sealed.append(path)
                def last_call(observed):
                    result=realpath(observed)
                    if sealed and not injected and Path(observed)==f.kernel.path.parent:
                        if target=="old-intent":path=folder/("attempt-transition-"+f.identifier+".bak")
                        elif target.startswith("old-stop-"):path=folder/("attempt-"+target[4:]+"-"+f.identifier+".bak")
                        elif target=="old-archive":path=folder/("reservation.json."+f.identifier+".bak")
                        elif target=="source-old":path=f.controller/"versions"/f.old["version"]/"runtime_linux.py"
                        elif target=="source-new":path=f.manifest().parent/"runtime_linux.py"
                        elif target=="reference-old":path=sorted((f.controller/"active").iterdir())[0]
                        elif target=="reference-new":path=sorted((f.controller/"active").iterdir())[-1]
                        elif target=="install-lock":path=f.controller/".install.lock"
                        elif target in ("operation-lock","reservation-lock"):path=folder/target.replace("-lock",".lock")
                        elif target=="input-certificate":path=f.inputs["certificate"]
                        elif target=="unknown-history":
                            path=folder/"foreign-after-observation.bak";create(path,b"foreign\n");injected.append(path);return result
                        elif target=="unknown-source":
                            path=f.controller/"versions"/"foreign-after-observation";path.mkdir(mode=0o700);injected.append(path);return result
                        elif target.startswith("authority-"):path=folder/ic.external_name(f.identifier,target[10:])
                        else:path=folder/target[5:]
                        injected.append(path)
                        data=path.read_bytes();path.rename(f.root/"moved-original");create(path,data)
                    return result
                def count(files,name,data,**kw):
                    if name=="reservation.json" and data!=ic.RELEASE_BLOCKED and json.loads(data)["phase"]=="available":writes.append(data)
                    return write(files,name,data,**kw)
                f.t.realpath=last_call
                with mock.patch.object(ic,"create",side_effect=publish),mock.patch.object(lc.PrivateFiles,"write",count),self.assertRaises((Refusal,OSError,ValueError)):
                    self.reconcile(f)
                self.assertEqual(len(injected),1);self.assertEqual(writes,[]);self.assertEqual(f.kernel.controls,["stop"])

    def test_missing_evidence_old_phase_and_orphan_authority_remain_blocked(self):
        for kind in ("stop-issued","stop-completed","extra-stopped","orphan","unknown-private","unknown-source","certificate-link","certificate-duplicate","input-mode"):
            f=self.ready(di.Fixture(Path(self.temp.name)/kind));folder=f.controller/"state"
            if kind in ("stop-issued","stop-completed"):(folder/("attempt-"+kind+"-"+f.identifier+".bak")).unlink()
            elif kind=="extra-stopped":ic.create(folder/("attempt-stopped-"+f.identifier+".bak"),b"{}\n")
            elif kind=="orphan":ic.create(folder/ic.external_name("f"*32,"successor"),b"{}\n")
            elif kind=="unknown-private":ic.create(folder/".operation-orphan",b"interrupted\n")
            elif kind=="unknown-source":(f.controller/"versions"/"unknown").mkdir(mode=0o700)
            elif kind=="certificate-link":
                f.inputs["certificate"].rename(f.root/"original-cert");f.inputs["certificate"].symlink_to(f.root/"original-cert")
            elif kind=="certificate-duplicate":
                p=f.inputs["certificate"];p.write_bytes(p.read_bytes().replace(b'"schema":1',b'"schema":1,"schema":1'))
            else:f.inputs["kernel"].chmod(0o640)
            before=self.snapshot(f)
            with self.subTest(kind=kind),self.assertRaises((Refusal,OSError,ValueError)):
                self.reconcile(f)
            self.assertEqual(self.snapshot(f),before);self.assertEqual(f.kernel.controls,["stop"])

    def test_record_ceiling_preflights_future_complete_authority_and_attachments(self):
        f=self.ready()
        certificate=f.inputs["certificate"].read_bytes()
        def inflate(c):
            c["account"]["helpers"]=[{"identity":"helper"+str(n),"work":"w"*512,"completion":"c"*512,"state":"finished_no_committed_work"} for n in range(16)]
            c["account"].update(actor="a"*512,action="a"*512,issuer="i"*512,issuer_completion="c"*512)
        self.change(f,"certificate",inflate)
        self.assertLessEqual(f.inputs["certificate"].stat().st_size,lc.MAX_PRIVATE)
        before=self.snapshot(f)
        with mock.patch.object(ic,"create",side_effect=AssertionError("complete future ceiling must refuse before intent")),self.assertRaisesRegex(Refusal,"complete serialization ceiling"):
            self.reconcile(f)
        self.assertEqual(self.snapshot(f),before)
        f.inputs["certificate"].write_bytes(certificate)
        f.inputs["kernel"].write_bytes(b" "*(lc.MAX_PRIVATE+1))
        with self.assertRaises(Refusal):self.reconcile(f)
        self.assertEqual(self.snapshot(f),before);self.assertEqual((lc.MAX_PRIVATE,rs.MAX_STATE),(32768,16384))

    def test_known_source_bridge_rejects_forks_serial_hops_and_settings_changes(self):
        for kind in ("source-fork","serial-hop","settings"):
            f=self.ready(di.Fixture(Path(self.temp.name)/kind))
            if kind=="source-fork":
                path=f.controller/"versions"/f.old["version"]/"runtime_linux.py";path.write_bytes(path.read_bytes()+b"# unknown fork\n")
            elif kind=="serial-hop":
                source=f.root/"third-source";shutil.copytree(f.source,source)
                (source/"host.py").write_bytes((source/"host.py").read_bytes()+b"# third normal source\n")
                f.source=source;f.install()
            else:
                path=f.manifest();obj=json.loads(path.read_bytes());obj["base_port"]+=1;path.write_bytes(ic.document(obj))
            before=self.snapshot(f)
            with self.subTest(kind=kind),self.assertRaises((Refusal,OSError,ValueError)):
                self.reconcile(f)
            self.assertEqual(self.snapshot(f),before);self.assertEqual(f.kernel.controls,["stop"])

    def test_declined_ceremony_and_source_nss_permanent_locks_do_not_write(self):
        f=self.ready();before=self.snapshot(f)
        with self.assertRaises(Refusal):self.reconcile(f,lambda _:False)
        self.assertEqual(self.snapshot(f),before)
        for lock in (ic.installation_lock(f.controller),lc.PrivateFiles(f.controller/"state").locked()):
            with lock,self.assertRaises(Refusal):self.reconcile(f)
            self.assertEqual(self.snapshot(f),before)
        with mock.patch.object(ic,"source_read",return_value=b"unpublished"),self.assertRaises(Refusal):self.reconcile(f)
        self.assertEqual(self.snapshot(f),before);self.assertEqual(f.kernel.controls,["stop"])

    def test_committed_floor_replay_future_lease_launch_release_and_source_evolution(self):
        f=self.ready();original=(f.controller/"state/reservation.json").read_bytes()
        self.assertTrue(self.reconcile(f)["ok"])
        menu=f.rpc("list");owner=f.rpc("acquire",revision=menu["revision"],snapshot=menu["snapshot"])
        self.assertTrue(self.reconcile(f)["ok"]);self.assertTrue(self.ordinary_retire(f)["ok"])
        prepared=f.rpc("prepare",revision=owner["revision"],capability=owner["capability"],snapshot=owner["snapshot"],display_id=lf.FIRST)
        self.assertTrue(prepared["launch_authorized"]);self.assertTrue(self.reconcile(f)["ok"])
        launch=f.rpc("launch",revision=prepared["revision"],capability=prepared["capability"])
        f.f.app=launch["desktop_app_id"]
        active=f.rpc("observe_active",revision=launch["revision"],capability=launch["capability"])
        resumed=f.rpc("resume",revision=active["revision"],capability=active["capability"])
        active=f.rpc("observe_active",revision=resumed["revision"],capability=resumed["capability"])
        self.assertTrue(self.reconcile(f)["ok"])
        closing=f.rpc("begin_release",revision=active["revision"],capability=active["capability"])
        f.f.app=0
        # Official quit is a CLIENT operation outside this ceremony. Host
        # transition acknowledges current idle only through its ordinary ticket.
        closing=f.rpc("finish_release",revision=closing["revision"],capability=closing["capability"])
        self.assertTrue(closing["ok"])
        source=f.root/"later-source";shutil.copytree(f.source,source)
        (source/"host.py").write_bytes((source/"host.py").read_bytes()+b"# legitimate later source publication\n")
        f.source=source;f.install()
        self.assertTrue(self.reconcile(f)["ok"]);self.assertTrue(self.ordinary_retire(f)["ok"])
        (f.controller/"state/reservation.json").write_bytes(original)
        before=self.snapshot(f)
        with self.assertRaises(Refusal):self.reconcile(f)
        self.assertEqual(self.snapshot(f),before)

    def test_second_distinct_authority_and_cut_preserve_both_sealed_histories(self):
        f=self.ready();self.assertTrue(self.reconcile(f)["ok"])
        first=f.identifier;first_inputs=dict(f.inputs)
        folder=f.controller/"state"
        sealed={p.name:ic.record_identity(p) for p in folder.iterdir() if p.name not in (*ic.RELEASE_RECORDS,"operation.lock","reservation.lock")}
        menu=f.rpc("list");owner=f.rpc("acquire",revision=menu["revision"],snapshot=menu["snapshot"])
        prepared=f.rpc("prepare",revision=owner["revision"],capability=owner["capability"],snapshot=owner["snapshot"],display_id=lf.FIRST)
        attempted=f.rpc("launch",revision=prepared["revision"],capability=prepared["capability"])
        f.rpc("control_lost",revision=attempted["revision"],capability=attempted["capability"])
        with mock.patch.object(rt.AttemptRetirement,"stopped",side_effect=RuntimeError("second authentic STOP-completed cut")),self.assertRaises(RuntimeError):
            self.ordinary_retire(f)
        f.old=next(json.loads(p.read_bytes()) for p in folder.glob("attempt-transition-*.bak") if not p.name.endswith(first+".bak"))
        f.identifier=f.old["id"];f.native_before=self.snapshot(f)
        self.external_start(f)
        source=f.root/"second-approved-source";shutil.copytree(f.source,source)
        (source/"host.py").write_bytes((source/"host.py").read_bytes()+b"# second qualified source evolution\n")
        self.publish_and_inputs(f,source=source)
        create=ic.create
        def cut(path,data,mode=0o600):
            create(path,data,mode)
            if path.name==ic.external_name(f.identifier,"committed"):raise RuntimeError("second precommit descriptor cut")
        with mock.patch.object(ic,"create",side_effect=cut),self.assertRaises(RuntimeError):self.reconcile(f)
        self.assert_blocked(f)
        self.assertTrue(self.reconcile(f,lambda _:(_ for _ in ()).throw(AssertionError("durable second authority")))["ok"])
        self.assertEqual(f.kernel.controls,["stop","stop"])
        for name,ident in sealed.items():self.assertEqual(ic.record_identity(folder/name),ident)
        second_inputs=f.inputs
        f.inputs=first_inputs;self.assertTrue(self.reconcile(f)["ok"])
        f.inputs=second_inputs;self.assertTrue(self.ordinary_retire(f)["ok"])
        self.assertEqual(len(list(folder.glob("external-transition-*.bak"))),2)
        for identifier in (first,f.identifier):
            for phase in ic.ATTEMPT_PHASES[2:]:self.assertFalse((folder/("attempt-"+phase+"-"+identifier+".bak")).exists())
        selected=tuple(f.certificate_originals.values());self.assertEqual(len(selected),2)
        with self.assertRaisesRegex(Refusal,"explicit original certificate"):
            self.ordinary_retire(f,original_certificates=selected[:1])
        with self.assertRaisesRegex(Refusal,"explicit original certificate"):
            self.reconcile(f,original_certificates=())
        lost=self.future_lost_attempt(f);f.kernel.after_control=None
        normal=self.ordinary_retire(f,original_certificates=selected)
        self.assertTrue(normal["ok"]);self.assertEqual(normal["revision"],lost["revision"]+1)
        self.assertEqual(f.kernel.controls,["stop","stop","stop","start"])
        self.assertTrue(self.reconcile(f)["ok"])

    def test_deleted_or_resealed_authorities_cannot_republish_or_redefine_floor(self):
        for kind in ("all-authority-missing","descriptor-missing","raised-floor","enrollment","receipt"):
            f=self.ready(di.Fixture(Path(self.temp.name)/kind));self.assertTrue(self.reconcile(f)["ok"])
            folder=f.controller/"state"
            if kind=="all-authority-missing":
                for p in folder.glob("external-*.bak"):p.unlink()
            elif kind=="descriptor-missing":(folder/ic.external_name(f.identifier,"committed")).unlink()
            else:
                intent_path=folder/ic.external_name(f.identifier,"transition")
                successor_path=folder/ic.external_name(f.identifier,"successor")
                descriptor_path=folder/ic.external_name(f.identifier,"committed")
                intent=json.loads(intent_path.read_bytes());successor=json.loads(successor_path.read_bytes());descriptor=json.loads(descriptor_path.read_bytes())
                promise=successor["promise"]
                if kind=="raised-floor":
                    state=json.loads(bytes.fromhex(promise["replacement"]["reservation.json"]));state["revision"]+=17
                    promise["replacement"]["reservation.json"]=ic.document(state).hex()
                elif kind=="enrollment":
                    meta=json.loads(bytes.fromhex(promise["replacement"]["operation.json"]));meta["enrollment"]["revision"]+=1
                    promise["replacement"]["operation.json"]=ic.document(meta).hex()
                else:promise["replacement"]["start-receipt.json"]=b"new loaded authority forbidden\n".hex()
                intent["promise_sha256"]=cfg.digest(ic.document(promise));intent_path.write_bytes(ic.document(intent))
                successor["intent_sha256"]=cfg.digest(ic.document(intent));successor["intent_identity"]=ic.record_identity(intent_path)
                successor_path.write_bytes(ic.document(successor))
                descriptor["intent_identity"]=ic.record_identity(intent_path);descriptor["successor_identity"]=ic.record_identity(successor_path)
                descriptor_path.write_bytes(ic.document(descriptor))
            before=self.snapshot(f)
            with self.subTest(kind=kind),mock.patch.object(ic,"create",side_effect=AssertionError("missing/resealed authority must not append")), \
                 mock.patch.object(lc.PrivateFiles,"write",side_effect=AssertionError("no second availability")),self.assertRaises((Refusal,OSError,ValueError)):
                self.reconcile(f)
            self.assertEqual(self.snapshot(f),before);self.assertEqual(f.kernel.controls,["stop"])

    def pure_certificate(self, f, certificate=None, raw=None, originals=None):
        installed=host.settings(f.manifest(),"linux")
        with lc.PrivateFiles(f.controller/"state").locked() as files:
            old,records,original_set=ic.external_predecessor(f.controller,installed,files,f.identifier)
        _,boundary=ic.attempt_sample(installed,records,f.t,f.f.endpoint,old["admission"])
        return ic.external_certificate(certificate or json.loads(f.inputs["certificate"].read_bytes()),
            raw or {k:f.inputs[k].read_bytes() for k in ic.EXTERNAL_ROLES},old,originals or original_set,
            ic.validate_version(f.manifest().parent),ic.active(f.controller)[1],boundary,ic.external_version_prefix(f.controller))

    def component_times(self, f, kernel, anchored):
        for role,value in (("kernel",kernel),("anchored",anchored)):
            self.change(f,role,lambda obj,role=role,value=value:obj.update({"utc" if role=="kernel" else "observed_utc":utc_ns(value)}))
            self.change(f,"certificate",lambda cert,role=role,value=value:cert["attachments"][role]["clock"].update(value=utc_ns(value)))

    def test_historical_interval_neighbors_and_exact_nanosecond_precision(self):
        f=self.ready(native_shape=True)
        original={k:p.read_bytes() for k,p in f.inputs.items()}
        admission=f.original_set["attempt-transition-"+f.identifier+".bak"]["mtime_ns"]
        events=json.loads(original["lifecycle"])["manager_events"]
        start=int(events[0]["__REALTIME_TIMESTAMP"])*1000
        cases=(("before-admission-anchor",admission-1,False),("at-admission-anchor",admission,True),
            ("after-admission-anchor",admission+1,True),("before-external-start",start-1,True),
            ("at-external-start",start,False),("after-external-start",start+1,False))
        for name,value,allowed in cases:
            for role,data in original.items():f.inputs[role].write_bytes(data)
            self.component_times(f,value,value)
            before=self.snapshot(f)
            with self.subTest(point=name),mock.patch.object(ic,"create",side_effect=AssertionError("pure qualifier must not write")):
                if allowed:self.pure_certificate(f)
                else:
                    with self.assertRaisesRegex(Refusal,"component realtime interval"):self.pure_certificate(f)
            self.assertEqual(self.snapshot(f),before)
        for role,data in original.items():f.inputs[role].write_bytes(data)
        self.component_times(f,start-1,start-2)
        with self.assertRaisesRegex(Refusal,"component realtime interval"):self.pure_certificate(f)
        self.assertEqual(ic.external_utc("2000-01-01T00:00:00.000000001Z")-ic.external_utc("2000-01-01T00:00:00+00:00"),1)
        self.assertEqual(ic.external_utc("2000-01-01T00:00:00.123456+00:00"),ic.external_utc("2000-01-01T00:00:00.123456000Z"))
        for malformed in ("2000-01-01T00:00:00.0000000001Z","2000-01-01T00:00:00", "2000-01-01T00:00:00+01:00",
                          "2000-02-30T00:00:00Z","2000-01-01T00:00:60Z"):
            with self.subTest(malformed=malformed),self.assertRaises(Refusal):ic.external_utc(malformed)
        self.assertEqual(f.kernel.controls,["stop"])

    def test_predating_components_and_clock_account_refuse_before_any_authority(self):
        for case in ("year2000","reversed","after-start","clock-basis","after-admission","admission-identity","clock-change","stopped-after-start"):
            f=self.ready(di.Fixture(Path(self.temp.name)/case),native_shape=True)
            events=json.loads(f.inputs["lifecycle"].read_bytes())["manager_events"];start=int(events[0]["__REALTIME_TIMESTAMP"])*1000
            if case=="year2000":
                past=ic.external_utc("2000-01-01T00:00:00Z");self.component_times(f,past,past)
            elif case=="reversed":self.component_times(f,start-1,start-2)
            elif case=="after-start":self.component_times(f,start+1,start+1)
            elif case=="clock-basis":self.change(f,"certificate",lambda c:c["ordering"].update(clock_basis="unknown"))
            elif case=="after-admission":self.change(f,"certificate",lambda c:c["ordering"].update(components_after_old_admission=False))
            elif case=="admission-identity":self.change(f,"certificate",lambda c:c["ordering"]["old_admission_intent_identity"].update(inode=1))
            elif case=="clock-change":self.change(f,"certificate",lambda c:c["ordering"].update(unaccounted_clock_changes=True))
            else:
                completed=f.original_set["attempt-stop-completed-"+f.identifier+".bak"]["mtime_ns"]
                bad_start=completed//1000-1
                self.change(f,"lifecycle",lambda obj:obj["manager_events"][0].update(__REALTIME_TIMESTAMP=str(bad_start)))
            before=self.snapshot(f)
            with self.subTest(case=case),mock.patch.object(ic,"create",side_effect=AssertionError("clock/account refusal precedes durable authority")), \
                 mock.patch.object(lc.PrivateFiles,"write",side_effect=AssertionError("zero availability")),self.assertRaises(Refusal):self.reconcile(f)
            self.assertEqual(self.snapshot(f),before);self.assertEqual(f.kernel.controls,["stop"])
        # Sealed control-clock contradictions are checked directly without
        # changing real predecessor records or pretending they were observed.
        f=self.ready(di.Fixture(Path(self.temp.name)/"hypothetical-control-clocks"))
        cert=json.loads(f.inputs["certificate"].read_bytes());hypothetical=copy.deepcopy(f.original_set)
        key="attempt-stop-issued-"+f.identifier+".bak"
        hypothetical[key]["mtime_ns"]=hypothetical["attempt-stop-completed-"+f.identifier+".bak"]["mtime_ns"]+1
        cert["predecessor"]["records_sha256"]=cfg.digest(ic.document(hypothetical))
        with self.assertRaisesRegex(Refusal,"sealed original admission/control realtime anchors"):
            self.pure_certificate(f,certificate=cert,originals=hypothetical)

    def add_unreferenced_source(self, f, name):
        original_source=f.source;before=ic.active(f.controller)
        source=f.root/(name+"-source");shutil.copytree(original_source,source)
        (source/"host.py").write_bytes((source/"host.py").read_bytes()+("# "+name+"\n").encode())
        versions={p.name for p in (f.controller/"versions").iterdir()};f.source=source
        create=ic.create
        def stop(path,data,mode=0o600):
            if path.parent==f.controller/"active":raise RuntimeError("private qualified publisher cut before reference")
            return create(path,data,mode)
        try:
            with mock.patch.object(ic,"create",side_effect=stop),self.assertRaisesRegex(RuntimeError,"before reference"):f.install()
        finally:f.source=original_source
        self.assertEqual(ic.active(f.controller),before)
        added={p.name for p in (f.controller/"versions").iterdir()}-versions
        self.assertEqual(len(added),1)
        return added.pop()

    def reseal_external_links(self, f, mutate):
        folder=f.controller/"state"
        ip=folder/ic.external_name(f.identifier,"transition");sp=folder/ic.external_name(f.identifier,"successor");dp=folder/ic.external_name(f.identifier,"committed")
        item=json.loads(ip.read_bytes());successor=json.loads(sp.read_bytes());descriptor=json.loads(dp.read_bytes())
        mutate(item)
        ip.write_bytes(ic.document(item));successor["intent_sha256"]=cfg.digest(ic.document(item));successor["intent_identity"]=ic.record_identity(ip)
        sp.write_bytes(ic.document(successor));descriptor["intent_identity"]=ic.record_identity(ip);descriptor["successor_identity"]=ic.record_identity(sp)
        dp.write_bytes(ic.document(descriptor))

    def test_resealed_referenced_and_unreferenced_prefix_promises_cannot_shrink(self):
        for kind in ("referenced-omit","unreferenced-omit","full-map-resealed","certificate-field-resealed","unreferenced-missing","referenced-bytes","unreferenced-fork"):
            with self.subTest(kind=kind):
                f=self.ready(di.Fixture(Path(self.temp.name)/kind),native_shape=True)
                unreferenced=self.add_unreferenced_source(f,"retained-unreferenced")
                self.change(f,"certificate",lambda cert:cert.update(source_version_prefix=ic.external_version_prefix(f.controller)))
                original_inputs={k:ic.record_identity(p) for k,p in f.inputs.items()}
                self.reconcile(f)
                folder=f.controller/"state";item=json.loads((folder/ic.external_name(f.identifier,"transition")).read_bytes())
                victim=unreferenced if kind.startswith("unreferenced") else next(n for n in item["version_prefix"] if n not in (f.old["version"],item["version"],unreferenced))
                version=f.controller/"versions"/victim;path=version/"host.py"
                if kind.endswith("-omit") or kind in ("full-map-resealed","certificate-field-resealed"):
                    raw=path.read_bytes();path.rename(f.root/"replaced-host");ic.create(path,raw)
                    def mutate(value):
                        if kind=="full-map-resealed":value["version_prefix"]=ic.external_version_prefix(f.controller)
                        else:
                            del value["version_prefix"][victim]
                            if kind=="certificate-field-resealed":del value["certificate"]["source_version_prefix"][victim]
                    self.reseal_external_links(f,mutate)
                elif kind=="unreferenced-missing":version.rename(f.root/"missing-unreferenced-version")
                elif kind=="referenced-bytes":path.write_bytes(path.read_bytes()+b"# changed immutable old bytes\n")
                else:
                    path.write_bytes(path.read_bytes()+b"# coherent fork\n")
                    marker=json.loads((version/".owned.json").read_bytes());marker["files"]["host.py"]=cfg.digest(path.read_bytes())
                    marker["version"]="v1-"+cfg.digest(ic.document(marker["files"]))
                    (version/".owned.json").write_bytes(ic.document(marker));version.rename(version.parent/marker["version"])
                    self.reseal_external_links(f,lambda value:value["version_prefix"].pop(victim))
                before=self.snapshot(f)
                with mock.patch.object(ic,"create",side_effect=AssertionError("no authority rewrite")), \
                     mock.patch.object(lc.PrivateFiles,"write",side_effect=AssertionError("replay has zero writes")),self.assertRaises((Refusal,OSError,ValueError)):
                    self.reconcile(f)
                self.assertEqual(self.snapshot(f),before)
                with mock.patch.object(rt.AttemptRetirement,"control",side_effect=AssertionError("ordinary replay must not control")), \
                     mock.patch.object(rt.LinuxRuntime,"guarded_restart",side_effect=AssertionError("ordinary replay must not restart")), \
                     mock.patch.object(lc.PrivateFiles,"write",side_effect=AssertionError("ordinary replay must not write")),self.assertRaises((Refusal,OSError,ValueError)):
                    self.ordinary_retire(f)
                self.assertEqual(self.snapshot(f),before)
                self.assertEqual({k:ic.record_identity(p) for k,p in f.inputs.items()},original_inputs)
                self.assertEqual(f.kernel.controls,["stop"])

    def test_complete_certificate_prefix_initial_partial_and_future_evolution(self):
        f=self.ready(native_shape=True)
        orphan=self.add_unreferenced_source(f,"initial-unreferenced")
        before=self.snapshot(f)
        # A caller cannot promise only referenced/current versions at creation.
        with mock.patch.object(ic,"create",side_effect=AssertionError("complete prefix first")),self.assertRaisesRegex(Refusal,"source-prefix commitment"):
            self.reconcile(f)
        self.assertEqual(self.snapshot(f),before)
        self.change(f,"certificate",lambda c:c.update(source_version_prefix=ic.external_version_prefix(f.controller)))
        original_inputs={k:ic.record_identity(p) for k,p in f.inputs.items()}
        self.reconcile(f)
        committed={p.name:ic.record_identity(p) for p in (f.controller/"state").glob("external-*.bak")}
        self.add_unreferenced_source(f,"later-unreferenced")
        self.assertTrue(self.reconcile(f)["ok"])
        source=f.root/"legitimate-reference-append";shutil.copytree(f.source,source)
        (source/"host.py").write_bytes((source/"host.py").read_bytes()+b"# legitimate future reference append\n")
        f.source=source;f.install();self.assertTrue(self.reconcile(f)["ok"]);self.assertTrue(self.ordinary_retire(f)["ok"])
        self.assertEqual({k:ic.record_identity(p) for k,p in f.inputs.items()},original_inputs)
        self.assertEqual({p.name:ic.record_identity(p) for p in (f.controller/"state").glob("external-*.bak")},committed)
        self.assertIn(orphan,json.loads(f.inputs["certificate"].read_bytes())["source_version_prefix"])
        for point in ("transition","certificate","successor","committed"):
            g=self.ready(di.Fixture(Path(self.temp.name)/("partial-"+point)),native_shape=True);create=ic.create
            def stop(path,data,mode=0o600):
                create(path,data,mode)
                if path.name==ic.external_name(g.identifier,point):raise RuntimeError("partial prefix cut")
            with mock.patch.object(ic,"create",side_effect=stop),self.assertRaises(RuntimeError):self.reconcile(g)
            ip=g.controller/"state"/ic.external_name(g.identifier,"transition");item=json.loads(ip.read_bytes())
            victim=next(n for n in item["version_prefix"] if n not in (g.old["version"],item["version"]))
            del item["version_prefix"][victim];ip.write_bytes(ic.document(item))
            before=self.snapshot(g)
            with mock.patch.object(lc.PrivateFiles,"write",side_effect=AssertionError("no partial availability")),self.assertRaisesRegex(Refusal,"non-shrinkable"):
                self.reconcile(g)
            self.assertEqual(self.snapshot(g),before);self.assertEqual(g.kernel.controls,["stop"])
        pending=self.ready(di.Fixture(Path(self.temp.name)/"pending-new-version"))
        create=ic.create
        def stop(path,data,mode=0o600):
            create(path,data,mode)
            if path.name==ic.external_name(pending.identifier,"transition"):raise RuntimeError("pending source-set cut")
        with mock.patch.object(ic,"create",side_effect=stop),self.assertRaises(RuntimeError):self.reconcile(pending)
        self.add_unreferenced_source(pending,"unexpected-pending-source-add")
        before=self.snapshot(pending)
        with self.assertRaisesRegex(Refusal,"pending authority source set"):self.reconcile(pending)
        self.assertEqual(self.snapshot(pending),before);self.assertEqual(pending.kernel.controls,["stop"])

    def coordinated_certificate_reseal(self, f, kind):
        folder=f.controller/"state";paths={role:folder/ic.external_name(f.identifier,role) for role in ("transition","certificate","successor","committed")}
        item=json.loads(paths["transition"].read_bytes());certificate=json.loads(paths["certificate"].read_bytes())
        successor=json.loads(paths["successor"].read_bytes());descriptor=json.loads(paths["committed"].read_bytes())
        certificate["source_version_prefix"]=ic.external_version_prefix(f.controller)
        if kind.endswith("omit"):del certificate["source_version_prefix"][f.root_victim]
        paths["certificate"].write_bytes(ic.document(certificate))
        item["certificate"]=certificate;item["version_prefix"]=certificate["source_version_prefix"]
        item["input_provenance"]["certificate"].update(sha256=cfg.digest(ic.document(certificate)),size=len(ic.document(certificate)))
        if kind=="redirected-provenance":
            fake=f.root/"redirected-original.json";fake.write_bytes(ic.document(certificate));fake.chmod(0o600)
            item["input_provenance"]["certificate"]=ic.record_identity(fake)
        paths["transition"].write_bytes(ic.document(item))
        successor["intent_sha256"]=cfg.digest(ic.document(item));successor["intent_identity"]=ic.record_identity(paths["transition"])
        successor["attachments"]["certificate"]=ic.record_identity(paths["certificate"])
        paths["successor"].write_bytes(ic.document(successor))
        descriptor["intent_identity"]=ic.record_identity(paths["transition"]);descriptor["successor_identity"]=ic.record_identity(paths["successor"])
        paths["committed"].write_bytes(ic.document(descriptor))

    def test_coordinated_cache_reseal_rejects_with_explicit_true_originals(self):
        for kind in ("referenced-hash","unreferenced-hash","unreferenced-omit","redirected-provenance"):
            with self.subTest(kind=kind):
                f=self.ready(di.Fixture(Path(self.temp.name)/kind),native_shape=True)
                unreferenced=self.add_unreferenced_source(f,"original-root-unreferenced")
                self.change(f,"certificate",lambda c:c.update(source_version_prefix=ic.external_version_prefix(f.controller)))
                originals={k:(p.read_bytes(),ic.record_identity(p)) for k,p in f.inputs.items()}
                self.reconcile(f);folder=f.controller/"state"
                item=json.loads((folder/ic.external_name(f.identifier,"transition")).read_bytes())
                f.root_victim=unreferenced if kind.startswith("unreferenced") else next(n for n in item["version_prefix"] if n not in (f.old["version"],item["version"],unreferenced))
                victim=f.controller/"versions"/f.root_victim/"host.py";data=victim.read_bytes();victim.rename(f.root/"moved-source");ic.create(victim,data)
                self.coordinated_certificate_reseal(f,kind)
                before=self.snapshot(f)
                with mock.patch.object(rt.AttemptRetirement,"control",side_effect=AssertionError("no control")), \
                     mock.patch.object(rt.LinuxRuntime,"guarded_restart",side_effect=AssertionError("no restart")), \
                     mock.patch.object(lc.PrivateFiles,"write",side_effect=AssertionError("no writes")):
                    with self.assertRaisesRegex(Refusal,"explicitly selected original"):
                        self.ordinary_retire(f,original_certificates=(f.inputs["certificate"],))
                    with self.assertRaisesRegex(Refusal,"explicitly selected original"):self.reconcile(f)
                self.assertEqual(self.snapshot(f),before)
                self.assertEqual({k:(p.read_bytes(),ic.record_identity(p)) for k,p in f.inputs.items()},originals)
                for name,identity in f.original_set.items():
                    path=folder/(name+"."+f.identifier+".bak") if name in ic.RELEASE_RECORDS else folder/name
                    self.assertEqual(ic.record_identity(path),identity)
                self.assertEqual(f.kernel.controls,["stop"])

    def test_every_consumer_requires_local_original_and_rejects_false_roots(self):
        f=self.ready(native_shape=True);self.reconcile(f);folder=f.controller/"state";before=self.snapshot(f)
        installed=host.settings(f.manifest(),"linux")
        with mock.patch.object(rt.AttemptRetirement,"control",side_effect=AssertionError("no control")), \
             mock.patch.object(rt.LinuxRuntime,"observe",side_effect=AssertionError("missing root must precede native qualification")), \
             mock.patch.object(lc.PrivateFiles,"write",side_effect=AssertionError("no writes")):
            with self.assertRaisesRegex(Refusal,"explicit original certificate"):
                self.ordinary_retire(f,original_certificates=())
            with lc.PrivateFiles(folder).locked() as files:
                with self.assertRaisesRegex(Refusal,"explicit original certificate"):ic.external_authorities(f.controller,installed,files)
                with self.assertRaisesRegex(Refusal,"explicit original certificate"):ic.external_replay(installed,files,f.t,f.f.endpoint)
                with self.assertRaisesRegex(Refusal,"explicit original certificate"):ic.external_descendant_floor(f.controller,installed,files,f.identifier,1,{})
        self.assertEqual(self.snapshot(f),before)
        copied=f.root/"byte-identical-copy.json";copied.write_bytes(f.inputs["certificate"].read_bytes());copied.chmod(0o600)
        forged=f.root/"forged-root.json";obj=json.loads(copied.read_bytes());obj["source_version_prefix"][next(iter(obj["source_version_prefix"]))]="f"*64
        forged.write_bytes(ic.document(obj));forged.chmod(0o600)
        unknown=f.root/"unknown-root.json";obj=json.loads(copied.read_bytes());obj["predecessor"]["id"]="f"*32
        unknown.write_bytes(ic.document(obj));unknown.chmod(0o600)
        for roots in ((copied,),(forged,),(unknown,),(f.inputs["certificate"],unknown),(f.inputs["certificate"],f.inputs["certificate"]),
                      (folder/ic.external_name(f.identifier,"certificate"),)):
            with self.subTest(roots=[p.name for p in roots]),mock.patch.object(lc.PrivateFiles,"write",side_effect=AssertionError("read-only refusal")),self.assertRaises(Refusal):
                self.ordinary_retire(f,original_certificates=roots)
            self.assertEqual(self.snapshot(f),before)
        with self.assertRaisesRegex(Refusal,"at most512"):ic.external_originals([copied]*513,f.controller)
        self.assertEqual(f.kernel.controls,["stop"])

    def future_lost_attempt(self, f):
        menu=f.rpc("list");owner=f.rpc("acquire",revision=menu["revision"],snapshot=menu["snapshot"])
        prepared=f.rpc("prepare",revision=owner["revision"],capability=owner["capability"],snapshot=owner["snapshot"],display_id=lf.FIRST)
        launch=f.rpc("launch",revision=prepared["revision"],capability=prepared["capability"])
        return f.rpc("control_lost",revision=launch["revision"],capability=launch["capability"])

    def test_true_original_allows_future_normal_retirement_controls(self):
        f=self.ready(native_shape=True);self.reconcile(f);folder=f.controller/"state"
        retained={p.name:ic.record_identity(p) for p in folder.iterdir() if p.name not in (*ic.RELEASE_RECORDS,"operation.lock","reservation.lock")}
        lost=self.future_lost_attempt(f);f.kernel.after_control=None
        result=self.ordinary_retire(f,original_certificates=(f.inputs["certificate"],))
        self.assertTrue(result["ok"]);self.assertEqual(result["revision"],lost["revision"]+1)
        self.assertEqual(f.kernel.controls,["stop","stop","start"])
        for name,identity in retained.items():self.assertEqual(ic.record_identity(folder/name),identity)
        self.assertTrue(self.reconcile(f)["ok"])

    def test_selected_original_and_new_records_fenced_after_last_replay_observation(self):
        for consumer in ("ordinary","external","external-original-only"):
            for target in ("original","certificate-cache","transition","descriptor"):
                with self.subTest(consumer=consumer,target=target):
                    f=self.ready(di.Fixture(Path(self.temp.name)/(consumer+"-"+target)));self.reconcile(f)
                    run=lambda:self.ordinary_retire(f) if consumer=="ordinary" else self.reconcile(f,read_only=consumer=="external-original-only")
                    trace=[];names=("run","read","identity","realpath","readlink")
                    def wrapper(name,operation,inject=None):
                        def observe(*args,**kwargs):
                            value=operation(*args,**kwargs);trace.append(name)
                            if inject is not None and len(trace)==inject:
                                if target=="original":path=f.inputs["certificate"]
                                else:path=f.controller/"state"/ic.external_name(f.identifier,{"certificate-cache":"certificate","transition":"transition","descriptor":"committed"}[target])
                                data=path.read_bytes();path.rename(f.root/"moved-after-last-observation");ic.create(path,data)
                            return value
                        return observe
                    with ExitStack() as stack:
                        for name in names:stack.enter_context(mock.patch.object(f.t,name,side_effect=wrapper(name,getattr(f.t,name))))
                        self.assertTrue(run()["ok"])
                    count=len(trace);self.assertGreater(count,0);trace.clear()
                    with ExitStack() as stack:
                        for name in names:stack.enter_context(mock.patch.object(f.t,name,side_effect=wrapper(name,getattr(f.t,name),count)))
                        stack.enter_context(mock.patch.object(lc.PrivateFiles,"write",side_effect=AssertionError("replay never writes")))
                        with self.assertRaises((Refusal,OSError,ValueError)):run()
                    self.assertEqual(len(trace),count);self.assertEqual(f.kernel.controls,["stop"])

    def test_normal_commit_fences_original_after_its_last_external_sample(self):
        f=self.ready();self.reconcile(f);self.future_lost_attempt(f)
        create,realpath,write=ic.create,f.t.realpath,lc.PrivateFiles.write;sealed,injected,writes=[],[],[]
        def publish(path,data,mode=0o600):
            create(path,data,mode)
            if path.name.startswith("attempt-committed-"):sealed.append(path)
        def observe(path):
            result=realpath(path)
            if sealed and not injected and Path(path)==f.kernel.path.parent:
                original=f.inputs["certificate"];data=original.read_bytes();original.rename(f.root/"moved-original-at-final-sample");create(original,data);injected.append(original)
            return result
        def count(files,name,data,**kw):
            if name=="reservation.json" and data!=ic.RELEASE_BLOCKED and json.loads(data)["phase"]=="available":writes.append(data)
            return write(files,name,data,**kw)
        f.t.realpath=observe
        with mock.patch.object(ic,"create",side_effect=publish),mock.patch.object(lc.PrivateFiles,"write",count),self.assertRaises((Refusal,OSError,ValueError)):
            self.ordinary_retire(f,original_certificates=(f.inputs["certificate"],))
        self.assertEqual(len(injected),1);self.assertEqual(writes,[]);self.assertEqual(f.kernel.controls,["stop","stop","start"])

    def test_local_cli_originals_are_readonly_inputs_not_rpc_or_dispatch_authority(self):
        sink=types.SimpleNamespace(buffer=io.BytesIO())
        with mock.patch.object(ic.sys,"argv",["installer","reconcile-attempted-launch","--original-certificate","a","--original-certificate","b"]), \
             mock.patch.object(ic.sys,"stdout",sink),mock.patch.object(ic,"local_attempt_retirement",return_value=b"{}\n") as enter:
            self.assertEqual(ic.main(),0);self.assertEqual(enter.call_args.kwargs,{"original_certificates":[Path("a"),Path("b")]})
        argv=["installer","reconcile-external-successor"]
        for role in ("certificate",*ic.EXTERNAL_ROLES):argv.extend(["--"+role,role+".json"])
        argv.extend(["--original-certificate","earlier.json"])
        with mock.patch.object(ic.sys,"argv",argv),mock.patch.object(ic.sys,"stdout",sink),mock.patch.object(ic,"local_external_successor",return_value=b"{}\n") as enter:
            self.assertEqual(ic.main(),0);self.assertEqual(enter.call_args.kwargs,{"original_certificates":[Path("earlier.json")]})
        with mock.patch.object(ic.sys,"argv",["installer","reconcile-external-successor","--certificate","original.json"]), \
             mock.patch.object(ic.sys,"stdout",sink),mock.patch.object(ic,"local_external_successor",return_value=b"{}\n") as enter:
            self.assertEqual(ic.main(),0);self.assertEqual(enter.call_args.args,(Path("original.json"),None,None,None,None))
        from protocol import request
        with self.assertRaises(Refusal):request(ic.document({"version":1,"action":"status","target":lf.TARGET,"original_certificate":"untrusted.json"}))
        self.assertNotIn(b"original-certificate",ic.SHIM)

    def test_original_only_external_replay_needs_no_historical_components(self):
        f=self.ready();before=self.snapshot(f)
        with mock.patch.object(ic,"create",side_effect=AssertionError("no authority from certificate-only input")),self.assertRaises(Refusal):
            self.reconcile(f,read_only=True)
        self.assertEqual(self.snapshot(f),before)
        self.reconcile(f)
        for role in ic.EXTERNAL_ROLES:f.inputs[role].unlink()
        before=self.snapshot(f)
        with mock.patch.object(ic,"create",side_effect=AssertionError("no replay publication")), \
             mock.patch.object(lc.PrivateFiles,"write",side_effect=AssertionError("zero replay writes")):
            self.assertTrue(self.reconcile(f,read_only=True,authorize=lambda _:(_ for _ in ()).throw(AssertionError("no replay ceremony")))["ok"])
            self.assertTrue(self.ordinary_retire(f,original_certificates=(f.inputs["certificate"],))["ok"])
        self.assertEqual(self.snapshot(f),before);self.assertEqual(f.kernel.controls,["stop"])
        g=self.ready(di.Fixture(Path(self.temp.name)/"pending-original-only"));create=ic.create
        def cut(path,data,mode=0o600):
            create(path,data,mode)
            if path.name==ic.external_name(g.identifier,"transition"):raise RuntimeError("pending cannot use original-only continuation")
        with mock.patch.object(ic,"create",side_effect=cut),self.assertRaises(RuntimeError):self.reconcile(g)
        before=self.snapshot(g)
        with mock.patch.object(ic,"create",side_effect=AssertionError("no partial completion")),self.assertRaises(Refusal):self.reconcile(g,read_only=True)
        self.assertEqual(self.snapshot(g),before);self.assertEqual(g.kernel.controls,["stop"])

    def test_audited_archival_producer_names_keep_full_settings_and_references(self):
        f=self.fixture;f.install(publish=True);current_settings=f.manifest().read_bytes()
        first=self.archive_version(f);second=self.archive_version(f,variant="later")
        self.assertEqual(first.name,"v1-33968ff1677a0c11c6500979f91a4454a6997bedc71e135893ce4d9cd37d1df8")
        self.assertEqual(second.name,"v1-d0708408f21a6aba9ed129fc1bdaec39d20fdf9f66ada2d252997a37c97a00d6")
        self.assertNotEqual((first/"host-settings.json").read_bytes(),current_settings)
        # Referenced and unreferenced archives are preservation objects only;
        # this private historical reference is never executed/adopted.
        ic.create(f.controller/"active/00000002.json",ic.document({"schema":1,"version":first.name}))
        before=self.snapshot(f);prefix=ic.external_version_prefix(f.controller)
        for folder in (first,second):
            expected=cfg.digest(ic.document({p.name:ic.record_identity(p) for p in folder.iterdir()}))
            self.assertEqual(prefix[folder.name],expected)
            self.assertIn("host-settings.json",{p.name for p in folder.iterdir()});self.assertIn(".owned.json",{p.name for p in folder.iterdir()})
        self.assertEqual(self.snapshot(f),before)
        other=di.Fixture(Path(self.temp.name)/"different-settings");other.install(publish=True)
        alternate=self.archive_version(other)
        self.assertEqual(alternate.name,first.name)
        self.assertNotEqual(ic.validate_version(alternate)["files"]["host-settings.json"],ic.validate_version(first)["files"]["host-settings.json"])
        self.assertIn(ic.active(other.controller)[0],ic.external_version_prefix(other.controller))  # Modern name unaffected.

    def test_archival_name_exception_rejects_unknown_or_damaged_objects(self):
        for kind in ("wrong-name","unknown-publisher","source","settings","marker","file-owner","symlink","hardlink","directory-alias","prefix-key"):
            with self.subTest(kind=kind):
                f=di.Fixture(Path(self.temp.name)/kind);f.install(publish=True);folder=self.archive_version(f)
                marker=json.loads((folder/".owned.json").read_bytes());host_file=folder/"host.py"
                if kind in ("wrong-name","prefix-key"):
                    renamed=folder.parent/("v1-"+"f"*64);folder.rename(renamed);folder=renamed
                    if kind=="wrong-name":marker["version"]=folder.name;(folder/".owned.json").write_bytes(ic.document(marker));ic.validate_version(folder)
                elif kind=="unknown-publisher":
                    p=folder/"install_controller.py";p.write_bytes(p.read_bytes()+b"# unaudited publisher\n");marker["files"][p.name]=cfg.digest(p.read_bytes())
                    marker["version"]="v1-"+cfg.digest(ic.document({n:h for n,h in marker["files"].items() if n!="host-settings.json"}))
                    renamed=folder.parent/marker["version"];folder.rename(renamed);folder=renamed
                    (folder/".owned.json").write_bytes(ic.document(marker));ic.validate_version(folder)
                elif kind=="source":host_file.write_bytes(host_file.read_bytes()+b"# changed source bytes\n")
                elif kind=="settings":
                    p=folder/"host-settings.json";p.write_bytes(p.read_bytes()+b" ")
                elif kind=="marker":
                    marker["files"]["host-settings.json"]="f"*64;(folder/".owned.json").write_bytes(ic.document(marker))
                elif kind=="symlink":host_file.rename(f.root/"moved-host");host_file.symlink_to(f.root/"moved-host")
                elif kind=="hardlink":os.link(host_file,f.root/"second-host-name")
                elif kind=="directory-alias":(folder.parent/("v1-"+"f"*64)).symlink_to(folder,target_is_directory=True)
                before=self.snapshot(f)
                with ExitStack() as patches:
                    if kind=="file-owner":
                        target=host_file.stat();real=lc.os.fstat
                        class ForeignOwner:
                            def __init__(self,info):self.info=info;self.st_uid=0
                            def __getattr__(self,key):return getattr(self.info,key)
                        def foreign(fd):
                            info=real(fd)
                            return ForeignOwner(info) if (info.st_dev,info.st_ino)==(target.st_dev,target.st_ino) else info
                        patches.enter_context(mock.patch.object(lc.os,"fstat",side_effect=foreign))
                    with self.assertRaises((Refusal,OSError,ValueError)):ic.external_version_prefix(f.controller)
                self.assertEqual(self.snapshot(f),before)

    def test_archival_compatibility_never_promotes_code_and_original_map_stays_sealed(self):
        for fault in ("settings-marker","clone","missing","shrink-reseal"):
            with self.subTest(fault=fault):
                f=self.ready(di.Fixture(Path(self.temp.name)/fault),native_shape=True)
                # Equal settings eliminate that veto: archival-only code must
                # still fail the existing source bridge, even if a test-only
                # trusted-code hint gets as far as its strict modern-name check.
                folder=self.archive_version(f,settings=f.manifest().read_bytes());marker=ic.validate_version(folder)
                candidate=copy.deepcopy(f.old);candidate.update(version=folder.name,manifest=marker)
                with self.assertRaises(Refusal):ic.external_source_bridge(f.controller,candidate,*ic.active(f.controller),ic.validate_version(f.manifest().parent))
                with self.assertRaisesRegex(Refusal,"source/settings manifest"):
                    ic.external_source_bridge(f.controller,candidate,*ic.active(f.controller),ic.validate_version(f.manifest().parent),trusted=(marker,))
                self.assertNotEqual(ic.EXTERNAL_LEGACY_SOURCE["install_controller.py"],ARCHIVE_PUBLISHER)
                self.change(f,"certificate",lambda c:c.update(source_version_prefix=ic.external_version_prefix(f.controller)))
                self.reconcile(f);original=ic.record_identity(f.inputs["certificate"])
                if fault=="settings-marker":
                    p=folder/"host-settings.json";obj=json.loads(p.read_bytes());obj["host_uniqueid"]="44444444-4444-4444-8444-444444444444";p.write_bytes(ic.document(obj))
                    marker["files"][p.name]=cfg.digest(p.read_bytes());(folder/".owned.json").write_bytes(ic.document(marker))
                    self.assertIn(folder.name,ic.external_version_prefix(f.controller))  # Name is valid; immutable promise detects change.
                elif fault=="clone":
                    p=folder/"host.py";data=p.read_bytes();p.rename(f.root/"moved-host-after-commit");ic.create(p,data)
                elif fault=="missing":folder.rename(f.root/"removed-archive")
                else:
                    p=folder/"host.py";data=p.read_bytes();p.rename(f.root/"moved-host-for-reseal");ic.create(p,data)
                    f.root_victim=folder.name;self.coordinated_certificate_reseal(f,"unreferenced-omit")
                before=self.snapshot(f)
                with mock.patch.object(lc.PrivateFiles,"write",side_effect=AssertionError("no replay availability")), \
                     mock.patch.object(rt.AttemptRetirement,"control",side_effect=AssertionError("no replay control")),self.assertRaises((Refusal,OSError,ValueError)):
                    self.ordinary_retire(f,original_certificates=(f.inputs["certificate"],))
                self.assertEqual(self.snapshot(f),before);self.assertEqual(ic.record_identity(f.inputs["certificate"]),original);self.assertEqual(f.kernel.controls,["stop"])

    def test_two_actual_legacy_archives_survive_native_shaped_publication_and_recovery(self):
        f=self.paused(native_shape=True);state=f.controller/"state"
        first=self.archive_version(f);settings=json.loads(f.manifest().read_bytes());settings["host_uniqueid"]="44444444-4444-4444-8444-444444444444"
        second=self.archive_version(f,variant="later",settings=ic.document(settings))
        archives={p.name:{child.name:ic.record_identity(child) for child in p.iterdir()} for p in (first,second)}
        records={p.name:ic.record_identity(p) for p in state.iterdir()};refs=ic.active_prefix(f.controller,6)
        self.assertEqual((len(records),ic.active(f.controller)[1]),(25,6));self.assertTrue(set(archives)<=set(ic.external_version_prefix(f.controller)))
        f.native_before=self.snapshot(f)
        self.external_start(f);self.publish_and_inputs(f)  # Ordinary ACTIVE publication, not a legacy publisher/adoption path.
        self.assertEqual(ic.active(f.controller)[1],7);self.assertEqual(ic.active_prefix(f.controller,6),refs)
        self.assertEqual({p.name:ic.record_identity(p) for p in state.iterdir()},records)
        self.change(f,"certificate",lambda c:c["account"].update(actor="PRIVATE HYPOTHETICAL operator; NOT the real09:49 startup account"))
        writes=[];write=lc.PrivateFiles.write
        def count(files,name,data,**kw):
            if name=="reservation.json" and data!=ic.RELEASE_BLOCKED and json.loads(data)["phase"]=="available":writes.append(data)
            return write(files,name,data,**kw)
        with mock.patch.object(lc.PrivateFiles,"write",count):result=self.reconcile(f)
        self.assertTrue(result["ok"]);self.assertEqual(len(writes),1);self.assertEqual(f.kernel.controls,["stop"])
        for name,identity in records.items():
            path=state/(name+"."+f.identifier+".bak") if name in ic.RELEASE_RECORDS else state/name
            self.assertEqual(ic.record_identity(path),identity)
        for name,preserved in archives.items():self.assertEqual({p.name:ic.record_identity(p) for p in (f.controller/"versions"/name).iterdir()},preserved)
        for phase in ic.ATTEMPT_PHASES[2:]:self.assertFalse((state/("attempt-"+phase+"-"+f.identifier+".bak")).exists())
        print("LEGACY_ARCHIVES_PRIVATE_FLOW="+json.dumps({"legacy_names":sorted(archives),"original_records":25,"old_serial":6,"new_serial":7,
            "additional_controls":0,"availability_writes":1,"archive_settings_and_markers_physically_preserved":True,"account":"explicitly PRIVATE HYPOTHETICAL"},sort_keys=True))

    def test_reconcile_token_is_exact_tty_and_rpc_source_set_remain_unchanged(self):
        cert={"predecessor":{"id":"synthetic"},"approved_source":{"version":"synthetic"},"external":{"job_id":"77"},"source_version_prefix":{},
            "account":{k:"synthetic concrete fact" for k in ("actor","action","issuer","issuer_completion","helpers")}}
        for data,tty,expected in ((b"RECONCILE\n",True,True),(b"RETIRE\n",True,False),(b"RECONCILE now\n",True,False),(b"RECONCILE\n",False,False)):
            with mock.patch.object(ic.os,"open",return_value=123),mock.patch.object(ic.os,"isatty",return_value=tty), \
                 mock.patch.object(ic.os,"read",return_value=data),mock.patch.object(ic.os,"write") as write,mock.patch.object(ic.os,"close"):
                self.assertEqual(ic.interactive_external(cert),expected)
                if tty:
                    message=write.call_args.args[1]
                    self.assertIn(b"ZERO service controls",message);self.assertIn(b"NOT a machine StopWitness",message)
                    self.assertIn(b"outstanding/queued control",message);self.assertIn(b"unused START",message)
        self.assertNotIn(b"reconcile-external-successor",ic.SHIM)
        self.assertEqual(ic.SOURCES,ar.RecoveryTests.base_dispatcher.SOURCES)
        with mock.patch.object(ic.sys,"argv",["installer","reconcile-external-successor","--certificate","a","--kernel","k"]), \
             mock.patch.object(ic,"local_external_successor",side_effect=AssertionError("incomplete CLI must not enter authority")), \
             mock.patch.object(ic.sys,"stderr"),self.assertRaises(SystemExit):ic.main()


if __name__ == "__main__":
    unittest.main()
