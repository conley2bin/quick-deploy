#!/usr/bin/env python3
"""Real companion/root code, synthetic independently verified package+host facts.

Only dpkg-deb --build/-f/--fsys-tarfile on private synthetic packages and local
Python/bash fixture processes are run. Fixed host subprocesses are injected,
Popen is a tripwire. No actual HOME/NSS lookup, service, SSH, GUI/device,
package install, credential, network or privileged publication is exercised.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import json
import multiprocessing as mp
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
from urllib.parse import quote

MODULE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE / "display-control"))
sys.path.insert(0, str(MODULE / "tests"))
import configuration as cfg
import host
import install_controller as ic
import lifecycle as lc
from protocol import Refusal
import qualification as q
import reservation as rs
import runtime_linux as rt
import display_lifecycle as lf
import display_runtime_linux as rf

VERSION = q.TAG[1:] + "-1+ubuntu24.04"
NAME = "sunshine_" + VERSION + "_amd64.deb"
FIXTURE_POPEN = subprocess.Popen
REFERENCE_BINARY = rf.BINARY  # Independent artifact fixture; never executable.
REFERENCE_UNIT = b"[Service]\nExecStartPre=/bin/sleep 5\nExecStart=/usr/bin/sunshine\n"
REFERENCE_ASSETS = {"apps.json": b'{"env":{},"apps":[{"name":"Desktop"}]}\n',
                    "box.png": b"\x89PNG\r\n\x1a\nsynthetic independent box image",
                    "desktop.png": lf.PNG, "web/index.html": b"synthetic web asset"}


def make_reference(root):
    root = Path(root)
    package_root = root / "synthetic-package"
    (package_root / "DEBIAN").mkdir(parents=True)
    (package_root / "DEBIAN/control").write_text(
        f"Package: sunshine\nVersion: {VERSION}\nArchitecture: amd64\nMaintainer: Fixture <fixture@example.invalid>\nDescription: Synthetic package, never installed or run\n")
    files = {"usr/bin/sunshine": REFERENCE_BINARY,
             "usr/lib/systemd/user/" + rt.UNIT: REFERENCE_UNIT,
             **{"usr/share/sunshine/" + name: data for name, data in REFERENCE_ASSETS.items()}}
    for name, data in files.items():
        target = package_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o755 if name == "usr/bin/sunshine" else 0o644)
    deb = root / "synthetic.deb"
    # Narrow exception to the host-Popen tripwire: ONLY private fixture .deb
    # construction/reading. Never installer/service/package-manager execution.
    with mock.patch.object(subprocess, "Popen", FIXTURE_POPEN):
        subprocess.run(("/usr/bin/dpkg-deb", "--build", str(package_root), str(deb)),
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10)
        responses = {}
        for key in ("Package", "Architecture", "Version"):
            argv = ("/usr/bin/dpkg-deb", "-f", str(deb), key)
            responses[argv] = rt.CommandResult(0, subprocess.check_output(argv, timeout=10))
        argv = ("/usr/bin/dpkg-deb", "--fsys-tarfile", str(deb))
        responses[argv] = rt.CommandResult(0, subprocess.check_output(argv, timeout=10))
    metadata = {"tag_name": q.TAG, "target_commitish": rt.SOURCE_COMMIT, "draft": False, "prerelease": False,
                "assets": [{"name": NAME, "browser_download_url": f"https://github.com/{q.REPO}/releases/download/{q.TAG}/{quote(NAME, safe='')}",
                            "digest": "sha256:" + hashlib.sha256(deb.read_bytes()).hexdigest(), "size": deb.stat().st_size}]}
    return deb, metadata, responses


class Fixture:
    def __init__(self, root):
        self.root = Path(root)
        self.f = lf.Fixture(self.root / "host-fixture")
        self.f.apps.write_bytes(b'{"env":{},"apps":[{"name":"Desktop","cmd":"","image-path":""}]}\n')
        self.deb, self.metadata, responses = make_reference(self.root)
        self.t = self.f.transport
        self.t.responses.update(responses)
        self.t.put(self.deb, self.deb.read_bytes())
        for name, data in REFERENCE_ASSETS.items():
            self.t.put(q.ASSETS / name, data, uid=0)
        for directory in list(self.t.dirs):
            if directory == q.ASSETS or q.ASSETS in directory.parents:
                self.t.ids[directory] = replace(self.t.ids[directory], uid=0)
        self.t.put(rf.FRAGMENT, REFERENCE_UNIT, uid=0)
        self.published = False
        self.publishes = 0
        self.source = MODULE / "display-control"

    @property
    def controller(self):
        return self.f.home / ic.RELATIVE_ROOT

    def publisher(self, transport):
        self.publishes += 1
        self.published = True

    def install(self, **kwargs):
        result = ic.install(json.dumps(self.metadata).encode(), self.deb, "24.04", "amd64", VERSION,
                            source=self.source, home=self.f.home, transport=self.t, info=self.f.endpoint,
                            check_shim=lambda: self.published, publisher=self.publisher, **kwargs)
        self.f.state = self.controller / "state"  # Injected restart observer follows REAL installed state.
        return result

    def manifest(self):
        version, _ = ic.active(self.controller)
        return self.controller / "versions" / version / "host-settings.json"

    def rpc(self, action, **fields):
        installed = host.settings(self.manifest(), "linux")
        runtime = rt.LinuxRuntime(installed.runtime, self.t)
        raw = json.dumps({"version": 1, "action": action, "target": lf.TARGET, **fields}).encode()
        return json.loads(host.handle(raw, manifest=self.manifest(), runtime=runtime, info=self.f.endpoint,
                          enumerate_native=self.f.inventory, authorize_enrollment=lambda: True,
                          sleep=lambda _: None, readiness_attempts=3))


def held_lock(path, ready, finish):
    import fcntl
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        ready.set()
        finish.wait(5)
    finally:
        os.close(fd)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qd-controller-test-")
        self.addCleanup(self.temp.cleanup)
        self.fixture = Fixture(self.temp.name)
        # EVERY actual host fixed-argv subprocess is forbidden, not just apt.
        self.tripwire = mock.patch.object(rt.subprocess, "Popen", side_effect=AssertionError("real host subprocess forbidden"))
        self.tripwire.start()
        self.addCleanup(self.tripwire.stop)
        self.nss = mock.patch.object(ic.pwd, "getpwuid", side_effect=AssertionError("real NSS HOME lookup forbidden"))
        self.nss.start()
        self.addCleanup(self.nss.stop)

    def refused(self, code, **kwargs):
        with self.assertRaises(Refusal) as exc:
            self.fixture.install(**kwargs)
        self.assertEqual(exc.exception.code, code, exc.exception.detail)

    def test_happy_real_deb_decodes_and_real_protocol_is_requestable(self):
        f = self.fixture
        result = f.install(publish=True)
        self.assertEqual((result.rpc_ready, result.status, f.publishes), (True, "UNENROLLED", 1))
        s = host.settings(f.manifest(), "linux")
        self.assertEqual(s.qualification["artifact_sha256"], hashlib.sha256(f.deb.read_bytes()).hexdigest())
        self.assertEqual(s.qualification["kind"], "verified_vendor_package")
        self.assertEqual(s.runtime.build.executable_sha256, hashlib.sha256(REFERENCE_BINARY).hexdigest())
        self.assertEqual(s.apps_file, f.f.apps)
        self.assertEqual(s.assets_directory, Path("/usr/share/sunshine"))
        self.assertEqual(s.state_directory, f.controller / "state")
        self.assertEqual(f.manifest().stat().st_mode & 0o777, 0o600)
        self.assertEqual(s.state_directory.stat().st_mode & 0o777, 0o700)
        listing = f.rpc("list")
        self.assertEqual(listing["uncertainty"], "needs_onboarding")
        self.assertGreater(len(listing["screens"]), 0)
        self.assertFalse((s.state_directory / "start-receipt.json").exists())
        self.assertEqual(f.t.restarts, 0)
        self.assertFalse(any("restart" in argv or "daemon-reload" in argv for argv, _, _ in f.t.calls))

    def test_no_optin_is_truthful_private_install_not_rpc_ready(self):
        result = self.fixture.install()
        self.assertFalse(result.rpc_ready)
        self.assertEqual(self.fixture.publishes, 0)
        self.assertTrue(self.fixture.manifest().is_file())
        self.assertEqual(result.status, "UNENROLLED")

    def test_older_and_newer_versions_are_not_qualified_or_downgraded(self):
        for version in ("2026.906.222525-1", "2099.1.1-1"):
            with self.subTest(version=version), self.assertRaises(Refusal) as exc:
                ic.install(json.dumps(self.fixture.metadata).encode(), self.fixture.deb, "24.04", "amd64", version,
                           home=self.fixture.f.home, transport=self.fixture.t)
            self.assertEqual(exc.exception.code, "build_unqualified")
            self.assertFalse(self.fixture.controller.exists())

    def test_metadata_faults_refuse_before_ownership_or_publication(self):
        f = self.fixture
        for field, value in (("digest", None), ("digest", "sha256:" + "0" * 64),
                             ("browser_download_url", "https://github.com/other/asset.deb"),
                             ("size", True), ("size", f.deb.stat().st_size + 1)):
            old = f.metadata["assets"][0][field]
            f.metadata["assets"][0][field] = value
            with self.subTest(field=field, value=value):
                self.refused("build_unqualified")
                self.assertFalse(f.controller.exists())
            f.metadata["assets"][0][field] = old
        for field, value in (("target_commitish", "master"), ("tag_name", "v2099.1.1"), ("draft", True), ("prerelease", True)):
            old = f.metadata[field]
            f.metadata[field] = value
            with self.subTest(field=field): self.refused("build_unqualified")
            f.metadata[field] = old

    def test_altered_binary_asset_unit_and_extra_asset_refuse(self):
        f = self.fixture
        for path in (rt.EXECUTABLE, rf.FRAGMENT, q.ASSETS / "box.png", q.ASSETS / "web/index.html"):
            previous = f.t.files[path]
            identity = f.t.ids[path]
            f.t.put(path, b"x" * len(previous), uid=0, mode=0o755 if path == rt.EXECUTABLE else 0o644)
            with self.subTest(path=path):
                self.refused("build_unqualified")
                self.assertFalse(f.controller.exists())
            f.t.files[path], f.t.ids[path] = previous, identity
        f.t.put(q.ASSETS / "foreign.png", b"foreign", uid=0)
        self.refused("build_unqualified")

    def test_loaded_unit_overrides_reload_and_generation_refusals(self):
        f = self.fixture
        for field, value in (("ExecStart", rf.command("/usr/bin/sunshine", "/usr/bin/sunshine /custom.conf")),
                             ("FragmentPath", "/home/foreign/sunshine.service"),
                             ("Environment", "XDG_CONFIG_HOME=/custom"),
                             ("ConfigurationDirectory", "custom"), ("NeedDaemonReload", "yes")):
            previous = f.t.props[field]
            f.t.props[field] = value
            with self.subTest(field=field): self.refused("unit_unowned")
            f.t.props[field] = previous
        self.assertFalse(f.controller.exists())
        self.assertEqual(f.t.restarts, 0)
        self.assertFalse(any("daemon-reload" in argv for argv, _, _ in f.t.calls))

    def test_endpoint_error_and_busy_first_install_never_adopt_stream(self):
        f = self.fixture
        with self.assertRaises(Refusal):
            ic.install(json.dumps(f.metadata).encode(), f.deb, "24.04", "amd64", VERSION,
                       home=f.f.home, transport=f.t, info=lambda *args: (_ for _ in ()).throw(Refusal("endpoint_unknown", "synthetic API fault")))
        self.assertFalse(f.controller.exists())
        f.f.app = 123
        self.assertEqual(f.install(publish=True).status, "UNENROLLED")
        with self.assertRaises(Refusal) as exc: f.rpc("enroll", revision=f.rpc("status")["revision"])
        self.assertIn(exc.exception.code, ("host_busy", "runtime_not_idle"))
        self.assertEqual((f.t.restarts, f.f.app), (0, 123))
        self.assertFalse((f.controller / "state/start-receipt.json").exists())

    def test_custom_native_file_apps_and_config_parent_link(self):
        f = self.fixture
        apps = f.f.home / "custom-apps.json"
        apps.write_bytes(f.f.apps.read_bytes()); apps.chmod(0o600)
        f.f.conf.write_bytes(f.f.conf.read_bytes() + f"file_apps = {apps}\n".encode())
        f.install(publish=True)
        self.assertEqual(host.settings(f.manifest(), "linux").apps_file, apps)
        # Config leaf links, unlike ordinary config-root parent links, are refused.
        f2 = Fixture(Path(self.temp.name) / "linked")
        real = f2.f.home / "actual-config-parent"
        f2.f.conf.parent.parent.rename(real)
        f2.f.conf.parent.parent.symlink_to(real, target_is_directory=True)
        # Fixture virtual resolver canonicalizes the same ordinary parent link.
        f2.t.links[f2.f.home / ".config"] = real
        original_realpath = f2.t.realpath
        f2.t.realpath = lambda p: Path(p).resolve(strict=True) if str(p).startswith(str(f2.f.home)) else original_realpath(p)
        original_identity, original_read = f2.t.identity, f2.t.read
        f2.t.identity = lambda p, follow=False: rt.FileIdentity.from_stat(Path(p).stat() if follow else Path(p).lstat()) if str(p).startswith(str(real)) else original_identity(p, follow=follow)
        f2.t.read = lambda p, limit: Path(p).read_bytes() if str(p).startswith(str(real)) else original_read(p, limit=limit)
        f2.install(publish=True)
        self.assertEqual(host.settings(f2.manifest(), "linux").runtime.config_file, real / "sunshine/sunshine.conf")
        leaf = f2.f.conf
        bytes_before = leaf.read_bytes()
        other = leaf.with_name("other.conf"); other.write_bytes(bytes_before); other.chmod(0o600)
        leaf.unlink(); leaf.symlink_to(other)
        with self.assertRaises(Refusal): f2.install()
        self.assertEqual(other.read_bytes(), bytes_before)

    def test_existing_enrolled_active_receipt_backup_and_settings_survive_upgrade(self):
        f = self.fixture
        f.install(publish=True)
        f.rpc("enroll", revision=f.rpc("status")["revision"])
        acquired = f.rpc("acquire", revision=f.rpc("list")["revision"], snapshot=f.rpc("list")["snapshot"])
        prepared = f.rpc("prepare", revision=acquired["revision"], capability=acquired["capability"],
                         snapshot=acquired["snapshot"], display_id=lf.SECOND)
        attempted = f.rpc("launch", revision=prepared["revision"], capability=prepared["capability"])
        f.f.app = attempted["desktop_app_id"]
        f.rpc("observe_active", revision=attempted["revision"], capability=attempted["capability"])
        state = f.controller / "state"
        (state / "backup.bak").write_bytes(b"protected prior backup bytes"); (state / "backup.bak").chmod(0o600)
        settings_before = f.manifest().read_bytes()
        before = {p.name: (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns, p.stat().st_ino) for p in state.iterdir()}
        original_conf = f.f.conf.read_bytes()
        source = Path(self.temp.name) / "source-update"
        shutil.copytree(f.source, source)
        # A coherent new local controller source version, not a changed vendor.
        with (source / "host.py").open("a") as stream: stream.write("\n# synthetic independently published controller revision\n")
        f.source = source
        result = f.install()
        self.assertEqual(result.status, "active")
        self.assertEqual(f.manifest().read_bytes(), settings_before)
        self.assertEqual({p.name: (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns, p.stat().st_ino) for p in state.iterdir()}, before)
        self.assertEqual(ic.active(f.controller)[1], 2)
        self.assertEqual(f.f.conf.read_bytes(), original_conf)
        self.assertEqual(f.publishes, 1)
        self.assertEqual(f.t.restarts, 1)  # Only explicit fixture prepare, NEVER installer.

    def test_same_version_update_preserves_all_bytes_and_no_reenrollment(self):
        f = self.fixture
        f.install(publish=True)
        old = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in f.controller.rglob("*") if p.is_file()}
        f.install()
        self.assertEqual({p: (p.read_bytes(), p.stat().st_mtime_ns) for p in old}, old)
        self.assertEqual(ic.active(f.controller)[1], 1)
        self.assertEqual(f.publishes, 1)

    def test_corrupt_incomplete_symlink_state_and_settings_conflict_preserved(self):
        f = self.fixture
        f.install(publish=True)
        state = f.controller / "state"
        for name, data in (("reservation.json", b"broken"), ("operation.json", b'{"schema":1,"enrollment":{"ceremony":"11111111-1111-1111-1111-111111111111","revision":1},"lease":null}'),
                           ("start-receipt.json", b"{}"), (".reservation-write-incomplete", b"partial")):
            p = state / name; p.write_bytes(data); p.chmod(0o600)
            with self.subTest(name=name), self.assertRaises(Refusal): f.install()
            self.assertEqual(p.read_bytes(), data); p.unlink()
        backup = state / "backup.bak"; backup.write_bytes(b"retained"); backup.chmod(0o600)
        link = state / "operation.json"; link.symlink_to(backup)
        with self.assertRaises(Refusal): f.install()
        self.assertTrue(link.is_symlink()); self.assertEqual(backup.read_bytes(), b"retained"); link.unlink()
        old_manifest = f.manifest().read_bytes()
        f.f.endpoint_id = "11111111-1111-1111-1111-111111111111"
        self.refused("settings_conflict")
        self.assertEqual(f.manifest().read_bytes(), old_manifest)

    def test_foreign_private_publication_and_code_are_not_adopted(self):
        f = self.fixture
        f.controller.parent.mkdir(parents=True)
        f.controller.symlink_to(f.f.state)
        self.refused("publication_unsafe")
        f.controller.unlink(); f.install(publish=True)
        path = f.manifest().parent / "host.py"
        path.write_bytes(b"foreign code"); path.chmod(0o600)
        self.refused("publication_unsafe")
        self.assertEqual(path.read_bytes(), b"foreign code")

    def test_active_and_settings_races_never_overwrite_new_target(self):
        f = self.fixture
        real_link = ic.os.link
        sentinel = b"newly appearing target remains intact"
        def race(src, dst, **kwargs):
            if str(dst) == "host-settings.json":
                fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=kwargs["dst_dir_fd"])
                os.write(fd, sentinel); os.close(fd)
            return real_link(src, dst, **kwargs)
        with mock.patch.object(ic.os, "link", side_effect=race): self.refused("publication_race")
        settings = next((f.controller / "versions").glob("*/host-settings.json"))
        self.assertEqual(settings.read_bytes(), sentinel)
        self.assertFalse(list((f.controller / "active").iterdir()))
        # Separate complete baseline for an UPGRADE-active race.
        f2 = Fixture(Path(self.temp.name) / "active-race"); f2.install(publish=True)
        source = Path(self.temp.name) / "race-source"; shutil.copytree(f2.source, source)
        with (source / "host.py").open("a") as stream: stream.write("\n# upgrade\n")
        f2.source = source
        def active_race(src, dst, **kwargs):
            if str(dst) == "00000002.json":
                fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=kwargs["dst_dir_fd"])
                os.write(fd, sentinel); os.close(fd)
            return real_link(src, dst, **kwargs)
        with mock.patch.object(ic.os, "link", side_effect=active_race), self.assertRaises(Refusal): f2.install()
        self.assertEqual((f2.controller / "active/00000002.json").read_bytes(), sentinel)
        self.assertEqual(len(list((f2.controller / "active").iterdir())), 2)

    def test_real_multiprocess_installer_and_host_operation_exclusion(self):
        f = self.fixture
        f.install(publish=True)
        ctx = mp.get_context("fork")
        for path, code in ((f.controller / ".install.lock", "installation_contended"),
                           (f.controller / "state/operation.lock", "operation_contended")):
            ready, finish = ctx.Event(), ctx.Event()
            process = ctx.Process(target=held_lock, args=(str(path), ready, finish))
            process.start()
            try:
                self.assertTrue(ready.wait(5))
                self.refused(code)
            finally:
                finish.set(); process.join(5)
                if process.is_alive(): process.terminate(); process.join()
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(ic.active(f.controller)[1], 1)

    def test_nonlinux_and_root_refuse_before_any_host_or_home_access(self):
        for platform in ("win32", "darwin"):
            with self.subTest(platform=platform), self.assertRaises(Refusal) as exc:
                ic.install(b"invalid", Path("/forbidden"), "24.04", "amd64", VERSION, platform=platform)
            self.assertEqual(exc.exception.code, "platform_runtime_unavailable")
        with mock.patch.object(ic.os, "geteuid", return_value=0), self.assertRaises(Refusal) as exc:
            ic.install(b"invalid", Path("/forbidden"), "24.04", "amd64", VERSION)
        self.assertEqual(exc.exception.code, "installer_identity")


class RPCAndShimTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qd-shim-test-")
        self.addCleanup(self.temp.cleanup)
        self.f = Fixture(self.temp.name)
        self.popen = mock.patch.object(rt.subprocess, "Popen", side_effect=AssertionError("real subprocess forbidden"))
        self.popen.start(); self.addCleanup(self.popen.stop)
        self.f.install(publish=True)

    def streams(self, data=b"", tty=False):
        def stream(content=b""):
            return types.SimpleNamespace(buffer=io.BytesIO(content), isatty=lambda: tty)
        return stream(data), stream(), stream()

    def test_rpc_enroll_disabled_even_with_a_terminal(self):
        req = ic.document({"version": 1, "action": "enroll", "target": lf.TARGET, "revision": 0})
        for tty in (False, True):
            stdin, stdout, stderr = self.streams(req, tty)
            with mock.patch.object(host, "handle", side_effect=AssertionError("RPC enrollment must not dispatch")):
                rc = ic.rpc_main(stdin=stdin, stdout=stdout, stderr=stderr,
                                 handle=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should never dispatch")))
            self.assertEqual(rc, 0)  # Completed stdin RPC refusal, not enrollment CLI.
            self.assertEqual(json.loads(stdout.buffer.getvalue())["error"]["code"], "operator_authorization_required")

    def injected_host(self):
        f, actual_handle = self.f, host.handle
        def handle(raw, **kwargs):
            installed = host.settings(f.manifest(), "linux")
            return actual_handle(raw, manifest=f.manifest(), runtime=rt.LinuxRuntime(installed.runtime, f.t),
                                 info=f.f.endpoint, enumerate_native=f.f.inventory,
                                 authorize_enrollment=kwargs.get("authorize_enrollment", lambda: False),
                                 sleep=lambda _: None, readiness_attempts=3)
        return handle

    def test_real_installed_exec_twice_then_reinstall_without_bytecode(self):
        f = self.f
        version = f.manifest().parent
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
                  for p in version.iterdir()}
        shim = Path(self.temp.name) / "private-bin/quick-deploy-display-control"
        shim.parent.mkdir()
        shim.write_bytes(ic.SHIM); shim.chmod(0o755)
        # Only NSS HOME is injected. This executes the exact dispatcher with
        # REAL execv into the installed interpreter; no exec argv modification.
        # Invalid input stops before settings/runtime/native/endpoint queries.
        launcher = '''import pwd, runpy, sys, types
pwd.getpwuid = lambda uid, home=sys.argv[2]: types.SimpleNamespace(pw_dir=home)
sys.argv = [sys.argv[1]]
runpy.run_path(sys.argv[0], run_name='__main__')
'''
        env = {"PATH": "/usr/bin:/bin", "HOME": str(Path(self.temp.name) / "ignored-home"),
               "TMPDIR": self.temp.name, "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1"}
        argv = ("/usr/bin/python3", "-I", "-c", launcher, str(shim), str(f.f.home))
        for call in (1, 2):
            with self.subTest(call=call), mock.patch.object(subprocess, "Popen", FIXTURE_POPEN):
                result = subprocess.run(argv, input=b"{}", capture_output=True, timeout=10,
                                        env=env, cwd=self.temp.name)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "invalid_request")
            self.assertEqual(result.stderr, b"")
            self.assertFalse((version / "__pycache__").exists())
            self.assertEqual(list(version.rglob("*.pyc")), [])
            self.assertEqual(result.returncode, 0)
            self.assertEqual({p.name: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
                              for p in version.iterdir()}, before)
        self.assertTrue(f.install().rpc_ready)
        self.assertEqual(f.manifest().parent, version)
        self.assertEqual({p.name: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
                          for p in version.iterdir()}, before)
        self.assertEqual((ic.active(f.controller)[1], f.publishes, f.t.restarts), (1, 1, 0))

    def test_owned_stale_prepare_rpc_exit_matches_host_and_preserves_selecting(self):
        f = self.f
        f.rpc("enroll", revision=f.rpc("status")["revision"])
        menu = f.rpc("list")
        acquired = f.rpc("acquire", revision=menu["revision"], snapshot=menu["snapshot"])
        self.assertEqual(acquired["phase"], "selecting")
        raw = ic.document({"version": 1, "action": "prepare", "target": lf.TARGET,
                           "revision": acquired["revision"], "capability": acquired["capability"],
                           "snapshot": "0" * 64, "display_id": lf.SECOND})
        state = f.controller / "state"
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino)
                  for p in state.iterdir()}
        handle = self.injected_host()
        stdin, stdout, stderr = self.streams(raw)
        rc = ic.rpc_main(stdin=stdin, stdout=stdout, stderr=stderr, handle=handle)
        installed_reply = stdout.buffer.getvalue()
        self.assertEqual((rc, json.loads(installed_reply)["error"]["code"]), (0, "stale_snapshot"))
        stdin, stdout, stderr = self.streams(raw)
        with mock.patch.object(sys, "stdin", stdin), mock.patch.object(sys, "stdout", stdout), \
                mock.patch.object(host, "handle", side_effect=handle):
            self.assertEqual(host.main(), 0)
        self.assertEqual(stdout.buffer.getvalue(), installed_reply)
        self.assertEqual({p.name: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino)
                          for p in state.iterdir()}, before)
        # A successful follow-up RPC still reaches the actual ownership-only
        # coordinator, with the same SELECTING revision and no issued intent.
        stdin, stdout, stderr = self.streams(ic.document({"version": 1, "action": "status", "target": lf.TARGET}))
        self.assertEqual(ic.rpc_main(stdin=stdin, stdout=stdout, stderr=stderr, handle=handle), 0)
        status = json.loads(stdout.buffer.getvalue())
        self.assertTrue(status["ok"])
        self.assertEqual((status["phase"], status["revision"]), ("selecting", acquired["revision"]))
        self.assertEqual({p.name: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino)
                          for p in state.iterdir()}, before)
        self.assertEqual(f.t.restarts, 0)
        self.assertFalse(any("restart" in argv or "daemon-reload" in argv for argv, _, _ in f.t.calls))

    def test_completed_malformed_rpc_errors_exit_zero_without_host_dispatch(self):
        for raw in (b"{}", b"not json", b"\xff", b'{"version":1,"version":1}', b"x" * 2049):
            stdin, stdout, stderr = self.streams(raw)
            handle = mock.Mock(side_effect=AssertionError("malformed RPC must not observe a host"))
            with self.subTest(raw=raw[:32]):
                self.assertEqual(ic.rpc_main(stdin=stdin, stdout=stdout, stderr=stderr, handle=handle), 0)
                self.assertEqual(json.loads(stdout.buffer.getvalue())["error"]["code"], "invalid_request")
                handle.assert_not_called()
                self.assertEqual(stderr.buffer.getvalue(), b"")
        self.assertEqual(self.f.t.restarts, 0)

    def test_completed_rpc_internal_error_retains_zero_exit_and_sanitized_envelope(self):
        stdin, stdout, stderr = self.streams(ic.document({"version": 1, "action": "status", "target": lf.TARGET}))
        handle = mock.Mock(side_effect=RuntimeError("synthetic details must not leak"))
        self.assertEqual(ic.rpc_main(stdin=stdin, stdout=stdout, stderr=stderr, handle=handle), 0)
        reply = json.loads(stdout.buffer.getvalue())
        self.assertFalse(reply["ok"])
        self.assertEqual(reply["error"]["code"], "installation_unavailable")
        self.assertNotIn(b"synthetic details", stdout.buffer.getvalue())

    def test_explicit_enrollment_requires_terminal_and_refuses_busy_without_restart(self):
        stdin, stdout, stderr = self.streams()
        rc = ic.rpc_main(enroll=True, manifest=self.f.manifest(), stdin=stdin, stdout=stdout, stderr=stderr)
        self.assertEqual(rc, 1)
        f = self.f
        def handle(raw, **kwargs):
            s = host.settings(f.manifest(), "linux")
            return host.handle(raw, manifest=f.manifest(), runtime=rt.LinuxRuntime(s.runtime, f.t),
                               info=f.f.endpoint, enumerate_native=f.f.inventory, **{k: v for k, v in kwargs.items() if k != "manifest"})
        stdin, stdout, stderr = self.streams(tty=True)
        f.f.app = 123
        rc = ic.rpc_main(enroll=True, manifest=f.manifest(), stdin=stdin, stdout=stdout, stderr=stderr,
                         handle=handle, interactive=lambda: True)
        self.assertEqual(rc, 1); self.assertEqual(f.t.restarts, 0)
        f.f.app = 0
        stdin, stdout, stderr = self.streams(tty=True)
        self.assertEqual(ic.rpc_main(enroll=True, manifest=f.manifest(), stdin=stdin, stdout=stdout, stderr=stderr,
                                   handle=handle, interactive=lambda: True), 0)
        self.assertTrue(json.loads(stdout.buffer.getvalue())["ok"])
        before = {p.name: p.read_bytes() for p in (f.controller / "state").iterdir()}
        stdin, stdout, stderr = self.streams(tty=True)
        self.assertEqual(ic.rpc_main(enroll=True, manifest=f.manifest(), stdin=stdin, stdout=stdout, stderr=stderr,
                                   handle=handle, interactive=lambda: True), 1)
        self.assertEqual({p.name: p.read_bytes() for p in (f.controller / "state").iterdir()}, before)

    def test_fixed_shim_minimal_path_and_nss_dispatch_ignore_ambient_home(self):
        # Only name resolution is executed by bash. The shim's actual bytes are
        # separately executed below with NSS+exec injected, never the real HOME.
        self.popen.stop()
        bin_dir = Path(self.temp.name) / "isolated-usr/bin"; bin_dir.mkdir(parents=True)
        path = bin_dir / "quick-deploy-display-control"; path.write_bytes(ic.SHIM); path.chmod(0o755)
        result = subprocess.run(("/bin/bash", "--noprofile", "--norc", "-c", "command -v quick-deploy-display-control"),
                                env={"PATH": str(bin_dir) + ":/usr/bin:/bin", "HOME": "/must-not-read"},
                                capture_output=True, check=True, timeout=5)
        self.assertEqual(result.stdout.decode().strip(), str(path))
        self.popen.start()
        with mock.patch.object(pwd_module(), "getpwuid", return_value=types.SimpleNamespace(pw_dir=str(self.f.f.home))), \
                mock.patch.object(ic.os, "execv") as execute, mock.patch.object(sys, "argv", [str(path)]), \
                mock.patch.dict(os.environ, {"HOME": "/must-not-read", "USER": "root", "XDG_DATA_HOME": "/must-not-read"}):
            exec(compile(ic.SHIM, str(path), "exec"), {"__name__": "shim_fixture"})
        self.assertEqual(execute.call_args.args[0], "/usr/bin/python3")
        self.assertEqual(execute.call_args.args[1][-1], "rpc")
        self.assertIn(str(self.f.manifest().parent / "install_controller.py"), execute.call_args.args[1])

    def test_shim_root_uid_mismatch_and_arbitrary_args_refuse_before_nss(self):
        for uid, euid, args in ((0, 0, []), (1000, 1001, []), (1000, 1000, ["/arbitrary/path"]), (1000, 1000, ["--command", "evil"])):
            out = io.StringIO()
            with mock.patch.object(ic.os, "getuid", return_value=uid), mock.patch.object(ic.os, "geteuid", return_value=euid), \
                    mock.patch.object(pwd_module(), "getpwuid", side_effect=AssertionError("NSS forbidden before identity gate")), \
                    mock.patch.object(sys, "argv", ["shim", *args]), mock.patch.object(sys, "stdout", out), self.assertRaises(SystemExit):
                exec(compile(ic.SHIM, "shim", "exec"), {"__name__": "shim_fixture"})
            self.assertEqual(json.loads(out.getvalue())["ok"], False)

    def test_shim_refuses_linked_active_and_altered_code(self):
        f = self.f
        record = f.controller / "active/00000001.json"
        backup = f.controller / "record-backup"; backup.write_bytes(record.read_bytes()); backup.chmod(0o600)
        record.unlink(); record.symlink_to(backup)
        with mock.patch.object(pwd_module(), "getpwuid", return_value=types.SimpleNamespace(pw_dir=str(f.f.home))), \
                mock.patch.object(ic.os, "execv", side_effect=AssertionError("foreign controller executed")), \
                mock.patch.object(sys, "argv", ["shim"]), mock.patch.object(sys, "stdout", io.StringIO()), self.assertRaises(SystemExit):
            exec(compile(ic.SHIM, "shim", "exec"), {"__name__": "shim_fixture"})

    def test_explicit_privileged_publisher_argv_stdin_and_no_rpc_sudo(self):
        with mock.patch.object(subprocess, "run", return_value=types.SimpleNamespace(returncode=0)) as run:
            ic.publish_shim(None)
        argv = run.call_args.args[0]
        self.assertEqual(argv[:5], ("/usr/bin/sudo", "--", "/usr/bin/python3", "-I", "-c"))
        self.assertEqual(run.call_args.kwargs["input"], ic.SHIM)
        self.assertEqual(run.call_args.kwargs["env"], {"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
        self.assertNotIn("sudo", ic.SHIM.decode())


def pwd_module():
    import pwd
    return pwd


class ShellAndRootTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qd-root-install-test-")
        self.addCleanup(self.temp.cleanup)
        self.case = Path(self.temp.name)
        self.module = self.case / "module with spaces"; self.module.mkdir()
        for name in ("commands", "lib", "service", "display-control"):
            shutil.copytree(MODULE / name, self.module / name, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(MODULE / "install.sh", self.module / "install.sh")
        self.bin = self.case / "bin"; self.bin.mkdir()
        self.home = self.case / "home"; self.home.mkdir()
        self.log = self.case / "log"
        self.deb, self.metadata, _ = make_reference(self.case)
        (self.case / "metadata.json").write_text(json.dumps(self.metadata))
        os_release = self.case / "os-release"; os_release.write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
        self.env = {"PATH": str(self.bin) + ":/usr/bin:/bin", "HOME": str(self.home), "CASE": str(self.case),
                    "LOG": str(self.log), "QD_OS_RELEASE_FILE": str(os_release), "TMPDIR": str(self.case),
                    "QD_TEST_SYSTEM_PYTHON": str(self.bin / "system-python"), "INSTALLED_VERSION": VERSION}
        self.script("id", '#!/bin/sh\nprintf "1000\\n"\n')
        self.script("dpkg", '#!/bin/sh\n[ "$1" != --print-architecture ] || { echo amd64; exit; }\nexit 0\n')
        self.script("dpkg-query", '#!/bin/sh\ncase "$*" in *Version*) printf "%s\\n" "$INSTALLED_VERSION";; *) echo installed;; esac\n')
        self.script("system-python", '#!/bin/sh\n[ "$1" != -c ] || exit 0\nexec /usr/bin/python3 "$@"\n')
        # Never allow the fixture to fall through to actual service/network/apt/SSH.
        for command in ("systemctl", "busctl", "journalctl", "sudo", "apt-get", "ssh", "moonlight"):
            self.script(command, '#!/bin/sh\necho FORBIDDEN-' + command + ' >>"$LOG"\nexit 99\n')
        self.script("curl", '''#!/bin/bash
args=("$@")
out=''; headers=''; url=''
for ((i=0;i<${#args[@]};i++)); do
  case "${args[i]}" in -o) ((i+=1)); out="${args[i]}";; -D) ((i+=1)); headers="${args[i]}";; https:*) url="${args[i]}";; esac
done
[ "$1" = --disable ] || { echo FORBIDDEN-curlrc >>"$LOG"; exit 99; }
if [[ "$url" == https://api.github.com/* ]]; then
  config="$(cat)"
  [ -z "${GITHUB_TOKEN:-}${GH_TOKEN:-}" ] || { echo FORBIDDEN-token-env >>"$LOG"; exit 99; }
  printf 'api %s\\n' "$url" >>"$LOG"
  printf 'HTTP/1.1 %s fixture\\r\\n\\r\\n' "${API_STATUS:-200}" >"$headers"
  cp "$CASE/metadata.json" "$out"; printf '%s' "${API_STATUS:-200}"
else
  [ -z "${GITHUB_TOKEN:-}${GH_TOKEN:-}" ] || { echo FORBIDDEN-asset-token >>"$LOG"; exit 99; }
  printf 'asset %s\\n' "$url" >>"$LOG"
  cp "$CASE/synthetic.deb" "$out"
fi
''')
        # Fixture-only CLI interception: asset parser is REAL, install dispatch is
        # recorded. The full installer function is exercised with actual schemas
        # and injected host facts in InstallTests above, never a live-host CLI.
        shim = self.module / "display-control/install_controller.py"
        shim.write_text('''import pathlib, sys, os
sys.path.insert(0, %r)
import install_controller as real
if sys.argv[1] == 'asset':
    raise SystemExit(real.main())
with open(os.environ['LOG'], 'a') as stream: stream.write('controller-install '+repr(sys.argv[1:])+'\\n')
raise SystemExit(int(os.environ.get('COMPANION_FAIL', '0')))
''' % str(MODULE / "display-control"))
        for command, stage in (("install-host.sh", "host"), ("install-client.sh", "client")):
            p = self.module / "commands" / command
            p.write_text(f'#!/bin/sh\necho {stage} >>"$LOG"\nexit "${{{stage.upper()}_FAIL:-0}}"\n'); p.chmod(0o755)

    def script(self, name, text):
        p = self.bin / name; p.write_text(text); p.chmod(0o755)

    def run_command(self, script, *args):
        return subprocess.run(("/bin/bash", str(self.module / script), *args), env=self.env,
                              cwd=str(self.case), capture_output=True, timeout=15)

    def test_real_independent_shell_reuses_metadata_and_sha_boundary(self):
        self.env["GITHUB_TOKEN"] = "synthetic-secret-never-log"
        self.env["GH_TOKEN"] = "synthetic-secret-never-log"
        result = self.run_command("commands/install-display-control.sh", "--publish-shim")
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        log = self.log.read_text()
        self.assertIn("tags/" + q.TAG, log)
        self.assertIn("--publish-shim", log)
        self.assertIn("SHA-256", result.stdout.decode())
        self.assertNotIn("synthetic-secret", log + result.stdout.decode() + result.stderr.decode())
        self.assertNotIn("FORBIDDEN", log)
        for mutation in ("api", "size", "digest", "url"):
            before = self.log.read_text(); old = json.loads(json.dumps(self.metadata))
            if mutation == "api": self.env["API_STATUS"] = "403"
            elif mutation == "size": old["assets"][0]["size"] += 1
            elif mutation == "digest": old["assets"][0]["digest"] = None
            else: old["assets"][0]["browser_download_url"] = "https://other.invalid/reference.deb"
            (self.case / "metadata.json").write_text(json.dumps(old))
            with self.subTest(mutation=mutation):
                failed = self.run_command("commands/install-display-control.sh")
                self.assertNotEqual(failed.returncode, 0)
                self.assertNotIn("controller-install", self.log.read_text()[len(before):])
            self.env.pop("API_STATUS", None)
        self.assertNotIn("FORBIDDEN", self.log.read_text())

    def test_root_all_role_ordering_companion_optin_then_inventory(self):
        for args, expected in (([], ["host", "client"]), (["--host-only"], ["host"]), (["--client-only"], ["client"])):
            self.log.write_text("")
            inventory = self.module / "machines.yaml"
            if inventory.exists(): inventory.unlink()
            with self.subTest(args=args):
                result = self.run_command("install.sh", *args)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                lines = self.log.read_text().splitlines()
                self.assertEqual(lines[:len(expected)], expected)
                self.assertEqual(any("controller-install" in line for line in lines), "--client-only" not in args)
                self.assertTrue(inventory.is_file())
        self.log.write_text("")
        self.assertEqual(self.run_command("install.sh", "--host-only", "--publish-display-control-shim").returncode, 0)
        self.assertIn("--publish-shim", self.log.read_text())
        self.assertNotIn("FORBIDDEN", self.log.read_text())

    def test_root_failed_stage_order_and_personal_inventory_preservation(self):
        inventory = self.module / "machines.yaml"
        inventory.write_bytes(b"personal inventory preserved\n"); inventory.chmod(0o400)
        os.utime(inventory, ns=(1000000000, 1000000000))
        before = (inventory.read_bytes(), inventory.stat().st_mode, inventory.stat().st_mtime_ns)
        legacy = self.module / "machines.local.yaml"; legacy.write_bytes(b"legacy-never-read"); legacy.chmod(0o600)
        for flag in ("HOST_FAIL", "CLIENT_FAIL", "COMPANION_FAIL"):
            self.log.write_text(""); self.env[flag] = "1"
            with self.subTest(stage=flag):
                failed = self.run_command("install.sh")
                self.assertNotEqual(failed.returncode, 0)
                log = self.log.read_text()
                if flag == "HOST_FAIL": self.assertNotIn("client", log)
                if flag in ("HOST_FAIL", "CLIENT_FAIL"): self.assertNotIn("controller-install", log)
                if flag == "COMPANION_FAIL": self.assertIn("vendor", failed.stderr.decode())
                self.assertEqual((inventory.read_bytes(), inventory.stat().st_mode, inventory.stat().st_mtime_ns), before)
                self.assertEqual(legacy.read_bytes(), b"legacy-never-read")
            self.env.pop(flag)
        self.assertEqual(self.run_command("install.sh").returncode, 0)
        self.assertEqual((inventory.read_bytes(), inventory.stat().st_mode, inventory.stat().st_mtime_ns), before)
        inventory.unlink(); self.env["COMPANION_FAIL"] = "1"
        self.assertNotEqual(self.run_command("install.sh").returncode, 0)
        self.assertFalse(inventory.exists())

    def test_help_and_argument_refusals_do_not_reach_any_stage(self):
        for args, ok in ((["--help"], True), (["--host-only", "--client-only"], False),
                         (["--client-only", "--publish-display-control-shim"], False), (["--unknown"], False)):
            result = self.run_command("install.sh", *args)
            self.assertEqual(result.returncode == 0, ok)
            self.assertFalse(self.log.exists())
            self.assertFalse((self.module / "machines.yaml").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
