#!/usr/bin/env python3
"""Linux authority fixtures only: no installed services, proc, HOME, APIs or devices.

The fixed-argv adapter sees an entirely synthetic filesystem/systemd/journal.
LocalTransport pipe mechanics use a fake Popen/selector; only the leaf-file
no-follow/bound test uses a private TemporaryDirectory. Journal traces follow
pinned main.cpp461, nvhttp.cpp1390/1503, video.cpp1593/3072/3275/3448 and Linux
misc.cpp1279/1298. These fixtures do not attest a deployed binary or capture.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import FrozenInstanceError, asdict, replace
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "display-control"))
import runtime_linux as rt
from protocol import Refusal

UID = 1000
HOST = "fixture-host-0001"
HOME = Path("/fixture/home")
CONF = HOME / ".config/sunshine/sunshine.conf"
FRAGMENT = Path("/usr/lib/systemd/user/app-dev.lizardbyte.app.Sunshine.service")
BOOT = "11111111111111111111111111111111"
INVOCATION = "22222222222222222222222222222222"
START = 8000000
PROC = Path("/proc/42")
BINARY = b"qualified-binary-fixture-not-an-executable"
GUARD = b"fixture guard bytes enrolled independently"
CONFIG = (b"# retained private settings are not echoed\naddress_family = ipv4\n"
          b"bind_address = 100.100.20.30\ncapture = kms\nencoder = nvenc\noutput_name = DP-3\n")
EXPECTED_PROPERTIES = ("Id,LoadState,ActiveState,SubState,MainPID,InvocationID,ExecMainStartTimestampMonotonic,"
                       "FragmentPath,DropInPaths,ExecStart,ExecStartPre,Environment,EnvironmentFiles,ConfigurationDirectory,"
                       "Restart,RestartUSec,StartLimitIntervalUSec,PartOf,NeedDaemonReload")
SHOW = ("/usr/bin/systemctl", "--user", "show", "--no-pager", rt.UNIT,
        "--property=" + EXPECTED_PROPERTIES)
ENV = ("/usr/bin/systemctl", "--user", "show-environment")
RESTART = ("/usr/bin/systemctl", "--user", "--no-block", "--no-ask-password", "restart", rt.UNIT)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def command(path, argv=None, *, ignore="no"):
    # Native systemctl255 textual structured command; dynamic suffix is not argv.
    return ("{ path=" + path + " ; argv[]=" + (argv or path) + " ; ignore_errors=" + ignore
            + " ; start_time=[Wed 2026-09-30 19:00:00 CST] ; stop_time=[n/a] ; pid=42 ; code=(null) ; status=0/0 }")


def prefixed(message, level="Info"):
    return f"[2026-09-30 19:00:00.123]: {level}: {message}"


def startup(encoder="nvenc", output="DP-3", capture="kms", port=None):
    result = ["Sunshine version: 2026.914.233613 commit: 63d35f702ee9e362e43263742981836ec0710384",
              "config: 'address_family' = ipv4", "config: 'bind_address' = 100.100.20.30"]
    for key, value in (("capture", capture), ("encoder", encoder), ("output_name", output), ("port", port)):
        if value is not None and value != "":
            result.append(f"config: '{key}' = {value}")
    return result


def completed(encoder="vaapi", backend="KMS", connector=507):
    codec = {"nvenc": "h264_nvenc", "vaapi": "h264_vaapi", "software": "libx264"}[encoder]
    return [f"Trying encoder [{encoder}]", f"Screencasting with {backend}",
            "Found monitor for DRM screencasting", f"Found connector ID [{connector}]",
            f"Creating encoder [{codec}]", "// Ignore any errors mentioned above, they are not relevant. //",
            f"Found H.264 encoder: {codec} [{encoder}]"]


class FakeTransport:
    def __init__(self):
        self.files, self.ids, self.dirs, self.links = {}, {}, set(), {}
        self.calls, self.reads = [], []
        self.denied, self.on_run, self.on_read, self.responses = set(), None, None, {}
        self.show_count = 0
        self.manager = f"HOME={HOME}\n"
        self.exe_link = "/usr/bin/sunshine"
        self.exe_identity = None
        self.props = {key: "" for key in EXPECTED_PROPERTIES.split(",")}
        self.props.update(Id=rt.UNIT, LoadState="loaded", ActiveState="active", SubState="running",
                          MainPID="42", InvocationID=INVOCATION, ExecMainStartTimestampMonotonic=str(START),
                          FragmentPath=str(FRAGMENT), ExecStart=command("/usr/bin/sunshine"),
                          ExecStartPre=command("/bin/sleep", "/bin/sleep 5"), Restart="on-failure",
                          RestartUSec="5s", StartLimitIntervalUSec="0", PartOf="graphical-session.target",
                          NeedDaemonReload="no")
        self.put(CONF, CONFIG)
        self.put(rt.EXECUTABLE, BINARY, uid=0, mode=0o755)
        self.put(FRAGMENT, b"[Service]\nExecStartPre=/bin/sleep 5\nExecStart=/usr/bin/sunshine\n", uid=0)
        self.put(Path("/proc/sys/kernel/random/boot_id"), b"11111111-1111-1111-1111-111111111111\n", uid=0)
        # /proc/stat field22 is starttime; field3 is state. The comm contains ')' to
        # ensure the parser does not split at the first close-parenthesis.
        fields = ["S"] + ["0"] * 18 + ["500"] + ["0"] * 30
        self.put(PROC / "stat", ("42 (sunshine worker)) " + " ".join(fields) + "\n").encode())
        self.put(PROC / "status", b"Name:\tsunshine\nUid:\t1000\t1000\t1000\t1000\n")
        self.put(PROC / "cmdline", b"/usr/bin/sunshine\0")
        self.put(PROC / "cgroup", ("0::/user.slice/user-1000.slice/user@1000.service/app.slice/" + rt.UNIT + "\n").encode())
        self.put(PROC / "environ", f"HOME={HOME}\0OTHER_SECRET=fixture-secret-not-output\0".encode())
        self.trace(startup() + completed())

    def canonical(self, path):
        path = Path(path)
        for old, new in sorted(self.links.items(), key=lambda item: -len(str(item[0]))):
            if path == old or old in path.parents:
                return new / path.relative_to(old)
        return path

    def mkdir(self, path):
        path = Path(path)
        for parent in (path, *path.parents):
            self.dirs.add(parent)
            self.ids.setdefault(parent, rt.FileIdentity(1, len(self.ids) + 100, stat.S_IFDIR | 0o755,
                                                       UID, UID, 0, 10, 10))

    def put(self, path, data, *, uid=UID, mode=0o644, inode=None):
        path = self.canonical(path)
        self.mkdir(path.parent)
        previous = self.ids.get(path)
        identity = rt.FileIdentity(1, inode or (previous.inode if previous else len(self.ids) + 100),
                                   stat.S_IFREG | mode, uid, uid, len(data),
                                   previous.mtime_ns + 1 if previous else 20,
                                   previous.ctime_ns + 1 if previous else 20)
        self.files[path], self.ids[path] = data, identity

    def trace(self, messages):
        self.entries = []
        for index, message in enumerate(messages):
            self.entries.append({"MESSAGE": prefixed(message), "__CURSOR": f"fixture-cursor-{index}",
                                 "__MONOTONIC_TIMESTAMP": str(START + index + 1), "_BOOT_ID": BOOT,
                                 "_PID": "42", "_UID": "1000", "_SYSTEMD_USER_UNIT": rt.UNIT,
                                 "_SYSTEMD_INVOCATION_ID": INVOCATION, "_EXE": "/usr/bin/sunshine",
                                 "_TRANSPORT": "stdout"})
        self.raw_journal = None

    def run(self, argv, *, timeout, max_output):
        self.calls.append((argv, timeout, max_output))
        if argv[0] == "/usr/bin/systemctl" and "show" in argv:
            self.show_count += 1
        if self.on_run:
            self.on_run(self, argv)
        if argv in self.responses:
            response = self.responses[argv]
            if isinstance(response, Exception):
                raise response
            return response
        if argv == SHOW or (argv[:4] == SHOW[:4] and argv[4] == rt.ALIAS and argv[5:] == SHOW[5:]):
            return rt.CommandResult(0, "".join(f"{key}={value}\n" for key, value in reversed(list(self.props.items()))).encode())
        if argv == ENV:
            return rt.CommandResult(0, self.manager.encode())
        if argv[:5] == ("/usr/bin/journalctl", "--user", "--no-pager", "--output=json", "--all"):
            data = self.raw_journal if self.raw_journal is not None else b"\n".join(json.dumps(record).encode() for record in self.entries)
            return rt.CommandResult(0, data)
        if argv == RESTART or argv == (*RESTART[:-1], rt.ALIAS):
            return rt.CommandResult(0)
        raise AssertionError(f"unexpected fixture command: {argv}")

    def read(self, path, *, limit):
        path = self.canonical(path)
        self.reads.append((path, limit))
        if path in self.denied:
            raise PermissionError("fixture capability restriction")
        if self.on_read:
            self.on_read(self, path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]  # Adapter must enforce even a misbehaving transport's bound.

    def identity(self, path, *, follow=False):
        path = self.canonical(path)
        if path in self.denied:
            raise PermissionError("fixture capability restriction")
        if path == PROC / "exe" and follow:
            return self.exe_identity or self.ids[rt.EXECUTABLE]
        if path not in self.ids:
            raise FileNotFoundError(path)
        return self.ids[path]

    def realpath(self, path):
        result = self.canonical(path)
        if result not in self.ids:
            raise FileNotFoundError(result)
        return result

    def readlink(self, path):
        if path in self.denied:
            raise PermissionError("fixture capability restriction")
        if path != PROC / "exe":
            raise AssertionError(path)
        return self.exe_link

    def listdir(self, path):
        path = self.canonical(path)
        return tuple(sorted(file.name for file in self.ids if file.parent == path))

    def retry_policy(self):
        directory = HOME / ".config/systemd/user" / (rt.UNIT + ".d")
        guard, drop = directory / "check-tailnet.py", directory / "quick-deploy-retry.conf"
        content = ("# quick-deploy/sunshine-moonlight: managed retry policy\n[Unit]\nStartLimitIntervalSec=0\n"
                   "PartOf=graphical-session.target\n\n[Service]\nRestart=on-failure\nRestartSec=5s\n"
                   f'ExecStartPre=:/usr/bin/python3 "{guard}" "{self.canonical(CONF.parent)}"\n').encode()
        self.put(drop, content)
        self.put(guard, GUARD)
        self.props["DropInPaths"] = str(self.canonical(drop))
        self.props["ExecStartPre"] += " " + command("/usr/bin/python3", f"/usr/bin/python3 {guard} {self.canonical(CONF.parent)}")
        return directory, drop, guard


class AuthorityFixture(unittest.TestCase):
    def setUp(self):
        # Tripwire: no accidental real subprocess regardless of fixture contents.
        self.popen = mock.patch.object(rt.subprocess, "Popen", side_effect=AssertionError("real subprocess forbidden"))
        self.popen.start()
        self.addCleanup(self.popen.stop)
        self.transport = FakeTransport()
        self.settings = rt.Settings(HOST, UID, HOME, CONF, rt.SupportedBuild(sha(BINARY)))
        self.runtime = rt.LinuxRuntime(self.settings, self.transport)

    def receipt(self, observation=None):
        obs = observation or self.runtime.observe()
        return rt.StartReceipt(obs.host_uniqueid, obs.generation, obs.unit.ownership_digest,
                               obs.config.path, obs.config.root, obs.config.file,
                               obs.config.manager_environment_digest, obs.binary.file, self.settings.build)

    def assert_refuses(self, action, code):
        with self.assertRaises(Refusal) as raised:
            action()
        self.assertEqual(raised.exception.code, code)
        return raised.exception


class AuthorityTests(AuthorityFixture):
    def test_final_not_preference_and_exact_fixed_argv(self):
        obs = self.runtime.observe(receipt=self.receipt())
        self.assertEqual(obs.config.configured_encoder, "nvenc")
        self.assertEqual((obs.capture.status, obs.capture.effective_backend, obs.capture.effective_encoder,
                          obs.capture.effective_mem_type), ("verified", "kms", "vaapi", "vaapi"))
        self.assertEqual(obs.config.loaded_digest, sha(CONFIG))
        self.assertEqual(obs.generation, rt.Generation(BOOT, rt.UNIT, UID, INVOCATION, 42, 500, START))
        self.assertEqual(obs.capture.probe.connector_ids, (507,))
        self.assertTrue(obs.capture.probe.evidence[-1].event == "chosen_encoder_final")
        commands = [call[0] for call in self.transport.calls]
        self.assertEqual(commands[:5], [SHOW, ENV, commands[2], SHOW, ENV])
        journal = commands[2]
        self.assertEqual(journal, ("/usr/bin/journalctl", "--user", "--no-pager", "--output=json", "--all",
                                   "--lines=2049", "--output-fields=MESSAGE,__CURSOR,__MONOTONIC_TIMESTAMP,_BOOT_ID,_PID,_UID,"
                                   "_SYSTEMD_USER_UNIT,_SYSTEMD_INVOCATION_ID,_EXE,_TRANSPORT",
                                   "_BOOT_ID=" + BOOT, "_SYSTEMD_USER_UNIT=" + rt.UNIT, "_UID=1000", "_PID=42",
                                   "_SYSTEMD_INVOCATION_ID=" + INVOCATION))
        self.assertTrue(all(timeout == 5 and bound == 1048576 for _, timeout, bound in self.transport.calls))
        self.assertFalse(any("restart" in argv for argv in commands))
        self.assertNotIn("fixture-secret", repr(asdict(obs)))
        with self.assertRaises(FrozenInstanceError):
            obs.generation.pid = 88

    def test_receipt_is_required_but_bootstrap_observation_works(self):
        obs = self.runtime.observe()
        self.assertEqual(obs.capture.reason, "start_receipt_required")
        self.assertIsNotNone(obs.capture.probe)
        self.assertIsNone(obs.config.loaded_digest)
        self.assertIsNone(obs.capture.effective_mem_type)
        self.assertTrue(any("loaded_config_digest_unattested" in item for item in obs.limitations))
        for encoder, memtype in (("nvenc", "cuda"), ("vaapi", "vaapi"), ("software", "system")):
            with self.subTest(encoder=encoder):
                self.transport.trace(startup() + completed(encoder))
                result = self.runtime.observe(receipt=self.receipt())
                self.assertEqual(result.capture.effective_mem_type, memtype)

    def test_capability_limited_proc_requires_attested_not_fabricated_identity(self):
        self.transport.denied.update((PROC / "exe", PROC / "environ"))
        partial = self.runtime.observe()
        self.assertEqual(partial.binary.process_exe_status, "capability_limited")
        self.assertEqual(partial.config.process_environment_status, "capability_limited")
        self.assertEqual(partial.capture.status, "unverified")
        verified = self.runtime.observe(receipt=self.receipt(partial))
        self.assertEqual(verified.capture.status, "verified")
        self.assertEqual(verified.binary.process_exe_status, "capability_limited")
        self.assertTrue(any("proc_exe_inaccessible" in item for item in verified.limitations))
        self.transport.denied.add(PROC / "stat")
        self.assert_refuses(self.runtime.observe, "observation_unavailable")

    def test_old_deleted_executable_and_changed_loaded_root_refuse(self):
        for mutate, code in (
            (lambda: setattr(self.transport, "exe_link", "/usr/bin/sunshine (deleted)"), "binary_changed"),
            (lambda: setattr(self.transport, "exe_identity", replace(self.transport.ids[rt.EXECUTABLE], inode=999)), "binary_changed"),
            (lambda: self.transport.put(PROC / "environ", b"CONFIGURATION_DIRECTORY=/fixture/other\0"), "observation_unavailable"),
        ):
            with self.subTest(code=code):
                self.transport = FakeTransport()
                self.runtime = rt.LinuxRuntime(self.settings, self.transport)
                mutate()
                self.assert_refuses(self.runtime.observe, code)

    def test_custom_units_execstart_pre_and_environment_refuse(self):
        alterations = {"FragmentPath": "/fixture/foreign.service", "ExecStart": command("/usr/bin/sunshine", "/usr/bin/sunshine /fixture/custom.conf"),
                       "ExecStartPre": command("/bin/sleep", "/bin/sleep 2"), "Environment": "HOME=/foreign",
                       "EnvironmentFiles": "/fixture/env", "ConfigurationDirectory": "other", "NeedDaemonReload": "yes",
                       "LoadState": "masked", "Id": "other.service"}
        for key, value in alterations.items():
            with self.subTest(key=key):
                original = self.transport.props[key]
                self.transport.props[key] = value
                self.assert_refuses(self.runtime.observe, "unit_unowned")
                self.transport.props[key] = original
        self.transport.props["ExecStart"] += " " + command("/usr/bin/foreign")
        self.assert_refuses(self.runtime.observe, "unit_unowned")

    def test_effective_properties_are_complete_unique_and_service_running(self):
        for data in (b"Id=x\nId=y\n", b"Id=x\n", b"Unknown=foo\n", b"x\xff"):
            with self.subTest(data=data):
                self.transport.responses[SHOW] = rt.CommandResult(0, data)
                self.assert_refuses(self.runtime.observe, "observation_unavailable" if b"\xff" in data else "unit_ambiguous")
        self.transport.responses.clear()
        self.transport.props["ActiveState"] = "inactive"
        self.assert_refuses(self.runtime.observe, "service_not_running")

    def test_pid_boot_process_user_cgroup_and_argv_fail_closed(self):
        changes = (("MainPID", "0", "generation_unknown"), ("InvocationID", "", "generation_unknown"),
                   ("ExecMainStartTimestampMonotonic", "0", "generation_unknown"))
        for key, value, code in changes:
            with self.subTest(key=key):
                old = self.transport.props[key]
                self.transport.props[key] = value
                self.assert_refuses(self.runtime.observe, code)
                self.transport.props[key] = old
        for path, value, code in ((PROC / "status", b"Uid:\t1001\t1001\t1001\t1001\n", "process_unowned"),
                                  (PROC / "cgroup", b"0::/foreign.service\n", "process_unowned"),
                                  (PROC / "cmdline", b"/usr/bin/sunshine\0/foreign.conf\0", "process_unowned"),
                                  (PROC / "stat", b"42 (sunshine) Z\n", "generation_unknown"),
                                  (Path("/proc/sys/kernel/random/boot_id"), b"invalid\n", "generation_unknown")):
            with self.subTest(path=path):
                old = self.transport.files[path]
                self.transport.put(path, value)
                self.assert_refuses(self.runtime.observe, code)
                self.transport.put(path, old)

    def test_config_native_cursor_rejects_ambiguity_and_keeps_nested_keys_nested(self):
        self.transport.put(CONF, CONFIG + b"prep = [\noutput_name = wrong\n]\n")
        self.assertEqual(self.runtime.observe().config.configured_output, "DP-3")
        for data, code in ((CONFIG + b"output_name = DP-1\n", "config_ambiguous"),
                           (CONFIG.replace(b"capture = kms", b"capture = kms # comment"), "config_ambiguous"),
                           (CONFIG + b"key = [\n", "config_ambiguous"),
                           (CONFIG.replace(b"capture = kms", b"capture = auto"), "config_ambiguous"),
                           (CONFIG.replace(b"ipv4", b"both"), "config_binding_unowned"),
                           (CONFIG.replace(b"100.100.20.30", b"127.0.0.1"), "config_binding_unowned")):
            with self.subTest(data=data):
                self.transport.put(CONF, data)
                self.assert_refuses(self.runtime.observe, code)

    def test_config_ownership_leaf_symlink_mode_and_bound(self):
        original = self.transport.ids[CONF]
        for identity in (replace(original, uid=1001), replace(original, mode=stat.S_IFLNK | 0o777),
                         replace(original, mode=stat.S_IFREG | 0o666), replace(original, size=1048577)):
            self.transport.ids[CONF] = identity
            self.assert_refuses(self.runtime.observe, "file_unowned")
        self.transport.ids[CONF] = original
        self.transport.files[CONF] = b"x" * 1048577
        self.assert_refuses(self.runtime.observe, "observation_overflow")

    def test_config_directory_precedence_and_process_match(self):
        for env, confroot in (("CONFIGURATION_DIRECTORY=/fixture/config\nXDG_CONFIG_HOME=/fixture/unused\n", Path("/fixture/config/sunshine")),
                              ("XDG_CONFIG_HOME=/fixture/xdg\n", Path("/fixture/xdg/sunshine")),
                              ("", CONF.parent)):
            with self.subTest(env=env):
                self.transport = FakeTransport()
                self.transport.manager = env
                self.transport.put(confroot / "sunshine.conf", CONFIG)
                self.transport.put(PROC / "environ", env.replace("\n", "\0").encode())
                settings = replace(self.settings, config_file=confroot / "sunshine.conf")
                self.runtime = rt.LinuxRuntime(settings, self.transport)
                self.assertEqual(self.runtime.observe().config.root, str(confroot))
        for env in ("CONFIGURATION_DIRECTORY=/fixture/a:/fixture/b\n", "XDG_CONFIG_HOME=relative\n", "HOME=/fixture\nHOME=/other\n"):
            self.transport.manager = env
            self.assert_refuses(self.runtime.observe, "config_resolution_ambiguous")

    def test_parent_symlinks_and_canonical_dropins_follow_existing_policy(self):
        tr = self.transport
        target = Path("/fixture/xdg-real")
        # Move the ordinary XDG parent to a new canonical path. The owned retry
        # dir/files themselves stay non-symlinks; systemd emits resolved paths.
        for path in tuple(tr.ids):
            old = HOME / ".config"
            if path == old or old in path.parents:
                new = target / path.relative_to(old)
                tr.ids[new] = tr.ids.pop(path)
                if path in tr.files:
                    tr.files[new] = tr.files.pop(path)
        tr.links[HOME / ".config"] = target
        directory, drop, guard = tr.retry_policy()
        settings = replace(self.settings, retry_guard_sha256=sha(GUARD))
        self.runtime = rt.LinuxRuntime(settings, tr)
        observed = self.runtime.observe()
        self.assertEqual(observed.config.path, str(target / "sunshine/sunshine.conf"))
        self.assertTrue(observed.unit.ownership_digest)
        # Exact .conf leaf hardlink at another canonical pathname remains foreign.
        foreign = Path("/fixture/foreign/quick-deploy-retry.conf")
        tr.put(foreign, tr.files[tr.canonical(drop)])
        tr.ids[foreign] = tr.ids[tr.canonical(drop)]
        tr.props["DropInPaths"] = str(foreign)
        self.assert_refuses(self.runtime.observe, "unit_unowned")

    def test_managed_retry_bytes_guard_sleep_and_unloaded_extra_overrides(self):
        directory, drop, guard = self.transport.retry_policy()
        self.runtime = rt.LinuxRuntime(replace(self.settings, retry_guard_sha256=sha(GUARD)), self.transport)
        self.assertIsNotNone(self.runtime.observe())
        for key, value in (("RestartUSec", "1s"), ("ExecStartPre", command("/usr/bin/python3", f"/usr/bin/python3 {guard} {CONF.parent}"))):
            old = self.transport.props[key]
            self.transport.props[key] = value
            self.assert_refuses(self.runtime.observe, "unit_unowned")
            self.transport.props[key] = old
        old_guard = self.transport.files[guard]
        self.transport.put(guard, b"foreign guard")
        self.assert_refuses(self.runtime.observe, "unit_unowned")
        self.transport.put(guard, old_guard)
        self.transport.put(directory / "extra.conf", b"[Service]\n")
        self.assert_refuses(self.runtime.observe, "unit_unowned")

    def test_receipt_rejects_each_mismatched_attested_fact(self):
        good = self.receipt()
        mutations = {"host_uniqueid": "other-host-0002", "generation": replace(good.generation, pid=43),
                     "unit_digest": "0" * 64, "config_path": "/foreign/sunshine.conf", "config_root": "/foreign",
                     "config": replace(good.config, sha256="0" * 64), "manager_environment_digest": "0" * 64,
                     "executable": replace(good.executable, sha256="0" * 64),
                     "qualified_build": rt.SupportedBuild("0" * 64)}
        for field, value in mutations.items():
            with self.subTest(field=field):
                self.assert_refuses(lambda: self.runtime.observe(receipt=replace(good, **{field: value})), "receipt_mismatch")
        self.assert_refuses(lambda: self.runtime.observe(receipt={"verified": True}), "receipt_mismatch")

    def test_same_bytes_replacement_is_not_the_start_receipt_file(self):
        good = self.receipt()
        self.transport.put(CONF, CONFIG, inode=99999)
        self.assert_refuses(lambda: self.runtime.observe(receipt=good), "receipt_mismatch")
        good = self.receipt()
        self.transport.put(rt.EXECUTABLE, BINARY, uid=0, mode=0o755, inode=88888)
        self.assert_refuses(lambda: self.runtime.observe(receipt=good), "receipt_mismatch")

    def test_matching_source_text_does_not_qualify_arbitrary_binary_bytes(self):
        self.transport.put(rt.EXECUTABLE, b"unqualified", uid=0, mode=0o755)
        obs = self.runtime.observe()
        self.assertFalse(obs.binary.supported_build)
        self.assertEqual(obs.capture.status, "unverified")
        self.assert_refuses(lambda: self.runtime.observe(receipt=self.receipt(obs)), "receipt_mismatch")

    def test_revalidate_generation_config_binary_manager_around_journal(self):
        mutations = [lambda tr: tr.props.update(InvocationID="3" * 32),
                     lambda tr: tr.put(CONF, CONFIG + b"# operator edit\n"),
                     lambda tr: tr.put(rt.EXECUTABLE, BINARY + b"changed", uid=0, mode=0o755),
                     lambda tr: setattr(tr, "manager", f"HOME={HOME}\nXDG_CONFIG_HOME={HOME}/.config\n"),
                     lambda tr: tr.put(FRAGMENT, tr.files[FRAGMENT], uid=0, inode=3000)]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.transport = FakeTransport()
                self.runtime = rt.LinuxRuntime(self.settings, self.transport)
                self.transport.on_run = lambda tr, argv: mutation(tr) if argv[0] == "/usr/bin/journalctl" else None
                self.assert_refuses(self.runtime.observe, "observation_changed")

    def test_read_race_cannot_create_a_file_fingerprint(self):
        def change(tr, path):
            if path == CONF:
                tr.on_read = None
                tr.put(CONF, CONFIG + b"# raced\n")
        self.transport.on_read = change
        self.assert_refuses(self.runtime.observe, "file_changed")

    def test_journal_generation_metadata_all_must_match(self):
        for key, value in (("_PID", "99"), ("_BOOT_ID", "9" * 32), ("_UID", "1001"),
                           ("_SYSTEMD_USER_UNIT", rt.ALIAS), ("_SYSTEMD_INVOCATION_ID", "9" * 32),
                           ("_EXE", "/old/sunshine"), ("_TRANSPORT", "syslog")):
            with self.subTest(key=key):
                self.transport.trace(startup() + completed())
                self.transport.entries[-1][key] = value
                self.assertEqual(self.runtime.observe().capture.reason, "journal_generation_mismatch")

    def test_journal_unique_scalar_fields_order_window_and_formatter(self):
        mutations = [(lambda entries: entries[-1].update(__CURSOR=entries[0]["__CURSOR"]), "journal_order_ambiguous"),
                     (lambda entries: entries[-1].update(__MONOTONIC_TIMESTAMP=str(START - 1)), "journal_order_ambiguous"),
                     (lambda entries: entries[-1].update(_PID=["42", "99"]), "journal_fields_ambiguous"),
                     (lambda entries: entries[-1].update(MESSAGE="prefix " + entries[-1]["MESSAGE"]), "journal_formatter_unsupported"),
                     (lambda entries: entries[-1].update(MESSAGE=entries[-1]["MESSAGE"] + "\nsecond line"), "journal_formatter_unsupported")]
        for mutate, reason in mutations:
            with self.subTest(reason=reason):
                self.transport.trace(startup() + completed())
                mutate(self.transport.entries)
                self.assertEqual(self.runtime.observe().capture.reason, reason)
        self.transport.raw_journal = b'{"_PID":"42","_PID":"42"}'
        self.assertEqual(self.runtime.observe().capture.reason, "journal_json_invalid")
        self.transport.raw_journal = b"{}\n" * 2049
        self.assertEqual(self.runtime.observe().capture.reason, "journal_window_overflow")

    def test_attempted_or_creating_encoder_without_final_is_not_authority(self):
        for messages in (startup(), startup() + completed()[:-1], startup() + ["Creating encoder [h264_nvenc]"],
                         startup() + ["Found H.264 encoder: h264_vaapi [vaapi]"]):
            with self.subTest(messages=messages):
                self.transport.trace(messages)
                obs = self.runtime.observe(receipt=self.receipt())
                self.assertEqual(obs.capture.status, "unverified")
                self.assertIsNone(obs.capture.effective_encoder)

    def test_failed_preference_then_final_fallback_and_real_reprobe(self):
        messages = startup() + ["Trying encoder [nvenc]", "Screencasting with KMS", "Creating encoder [h264_nvenc]",
                                "Encoder [nvenc] failed", "Couldn't find any working encoder matching [nvenc]"] + completed("software")
        self.transport.trace(messages)
        receipt = self.receipt()
        self.assertEqual(self.runtime.observe(receipt=receipt).capture.effective_encoder, "software")
        self.transport.trace(messages + ["Trying encoder [vaapi]", "Screencasting with KMS"])
        self.assertEqual(self.runtime.observe(receipt=receipt).capture.status, "unverified")
        self.transport.trace(messages + completed("vaapi"))
        obs = self.runtime.observe(receipt=receipt)
        self.assertEqual(obs.capture.effective_encoder, "vaapi")
        refs = obs.capture.probe.evidence
        self.assertEqual(sum(ref.event == "chosen_encoder_final" for ref in refs), 1)
        self.assertEqual(refs[-1].cursor, self.transport.entries[-1]["__CURSOR"])

    def test_error_missing_preference_without_trial_supersedes_and_recovers(self):
        # video.cpp clears chosen_encoder before searching an absent preference;
        # this Error precedes both the testing banner and any fallback trial.
        self.transport.put(CONF, CONFIG.replace(b"encoder = nvenc", b"encoder = missing"))
        messages = startup(encoder="missing") + completed("software")
        self.transport.trace(messages)
        receipt = self.receipt()
        before = self.runtime.observe(receipt=receipt)
        self.assertEqual(before.capture.effective_encoder, "software")
        error = "Couldn't find any working encoder matching [missing]"
        self.transport.trace(messages + [error])
        self.transport.entries[-1]["MESSAGE"] = prefixed(error, "Error")
        pending = self.runtime.observe(receipt=receipt)
        self.assertEqual(pending.capture.status, "unverified")
        self.assertEqual(pending.capture.reason, "incomplete_or_superseded_probe")
        self.assertIsNone(pending.capture.probe)
        self.assertIsNone(pending.capture.effective_encoder)
        self.assertEqual(pending.generation, before.generation)
        banner = "// Testing for available encoders, this may generate errors. You can safely ignore those errors. //"
        self.transport.trace(messages + [error, banner] + completed("vaapi"))
        self.transport.entries[len(messages)]["MESSAGE"] = prefixed(error, "Error")
        recovered = self.runtime.observe(receipt=receipt)
        self.assertEqual(recovered.capture.status, "verified")
        self.assertEqual(recovered.capture.effective_encoder, "vaapi")
        self.assertEqual(recovered.generation, before.generation)
        self.assertEqual(recovered.capture.probe.evidence[-1].cursor, self.transport.entries[-1]["__CURSOR"])
        self.assertEqual(sum(ref.event == "chosen_encoder_final" for ref in recovered.capture.probe.evidence), 1)
        self.assertFalse(any("restart" in argv for argv, _, _ in self.transport.calls))

    def test_error_gpu_rejection_without_trial_supersedes_and_recovers(self):
        # Explicit GPU rejection contradicts the previous choice even when this
        # journal window contains no new Trying header for the rejected probe.
        messages = startup() + completed("vaapi")
        self.transport.trace(messages)
        receipt = self.receipt()
        before = self.runtime.observe(receipt=receipt)
        self.assertEqual(before.capture.effective_encoder, "vaapi")
        error = "Encoder [vaapi] is not supported on this GPU"
        self.transport.trace(messages + [error])
        self.transport.entries[-1]["MESSAGE"] = prefixed(error, "Error")
        pending = self.runtime.observe(receipt=receipt)
        self.assertEqual(pending.capture.status, "unverified")
        self.assertEqual(pending.capture.reason, "incomplete_or_superseded_probe")
        self.assertIsNone(pending.capture.probe)
        self.assertIsNone(pending.capture.effective_encoder)
        self.assertEqual(pending.generation, before.generation)
        self.transport.trace(messages + [error] + completed("software"))
        self.transport.entries[len(messages)]["MESSAGE"] = prefixed(error, "Error")
        recovered = self.runtime.observe(receipt=receipt)
        self.assertEqual(recovered.capture.status, "verified")
        self.assertEqual(recovered.capture.effective_encoder, "software")
        self.assertEqual(recovered.generation, before.generation)
        self.assertEqual(recovered.capture.probe.evidence[-1].cursor, self.transport.entries[-1]["__CURSOR"])
        self.assertEqual(sum(ref.event == "chosen_encoder_final" for ref in recovered.capture.probe.evidence), 1)
        self.assertFalse(any("restart" in argv for argv, _, _ in self.transport.calls))

    def test_unrelated_error_and_same_kms_capture_preserve_final(self):
        messages = startup() + completed("vaapi")
        error = "An unrelated subsystem reported a recoverable error"
        self.transport.trace(messages + [error, "Screencasting with KMS"])
        self.transport.entries[len(messages)]["MESSAGE"] = prefixed(error, "Error")
        result = self.runtime.observe(receipt=self.receipt())
        self.assertEqual(result.capture.status, "verified")
        self.assertEqual(result.capture.effective_encoder, "vaapi")
        self.assertEqual(result.capture.probe.evidence[-1].event, "ordinary_capture_attempt")

    def test_reprobe_banner_supersedes_even_before_first_new_trial(self):
        banner = "// Testing for available encoders, this may generate errors. You can safely ignore those errors. //"
        self.transport.trace(startup() + completed() + [banner])
        receipt = self.receipt()
        self.assertEqual(self.runtime.observe(receipt=receipt).capture.status, "unverified")
        self.transport.trace(startup() + completed() + [banner] + completed("nvenc"))
        self.assertEqual(self.runtime.observe(receipt=receipt).capture.effective_encoder, "nvenc")
        # With a successful explicit preference the same banner is printed
        # AFTER validation but before final selection. It must not erase trial.
        self.transport.trace(startup() + completed()[:-1] + [banner] + completed()[-1:])
        self.assertEqual(self.runtime.observe(receipt=receipt).capture.effective_encoder, "vaapi")

    def test_final_codec_block_and_gpu_rejection_are_consistent(self):
        for messages in (startup() + completed() + ["Found HEVC encoder: hevc_nvenc [nvenc]"],
                         startup() + completed() + ["Found AV1 encoder: av1_nvenc [nvenc]"],
                         startup() + completed()[:-1] + ["Encoder [vaapi] is not supported on this GPU"] + completed()[-1:]):
            with self.subTest(messages=messages):
                self.transport.trace(messages)
                self.assertEqual(self.runtime.observe(receipt=self.receipt()).capture.status, "unverified")
        self.transport.trace(startup() + completed() + ["Found HEVC encoder: hevc_vaapi [vaapi]",
                                                        "Found AV1 encoder: av1_vaapi [vaapi]"])
        self.assertEqual(self.runtime.observe(receipt=self.receipt()).capture.status, "verified")

    def test_ordinary_stream_disconnect_and_noop_reconnect_keep_choice(self):
        # KMS factory calls below come from captureThread using chosen_encoder,
        # not validate_encoder. After the first render-device stabilization probe,
        # launch/resume can return early without another Trying/Found block.
        messages = startup() + completed("vaapi")
        self.transport.trace(messages)
        receipt = self.receipt()
        baseline = self.runtime.observe(receipt=receipt)
        # Include an initial same-choice reprobe (needs_encoder_reenumeration's
        # first cache population), normal capture, disconnect and next no-op.
        messages += completed("vaapi") + ["Screencasting with KMS", "Found monitor for DRM screencasting",
                                         "Found connector ID [507]", "CLIENT DISCONNECTED",
                                         "Screencasting with KMS", "Found monitor for DRM screencasting",
                                         "Found connector ID [507]"]
        self.transport.trace(messages)
        after = self.runtime.observe(receipt=receipt)
        self.assertEqual(after.capture.status, "verified")
        self.assertEqual(after.capture.effective_encoder, baseline.capture.effective_encoder)
        self.assertEqual(after.capture.effective_mem_type, "vaapi")
        self.assertTrue(any(ref.event == "ordinary_capture_attempt" for ref in after.capture.probe.evidence))
        # A genuinely new unfinished trial invalidates even the retained choice.
        self.transport.trace(messages + ["Trying encoder [nvenc]", "Screencasting with KMS"])
        self.assertEqual(self.runtime.observe(receipt=receipt).capture.status, "unverified")

    def test_malformed_contradictory_and_unsupported_probe_refuse(self):
        cases = [(startup() + completed(backend="X11"), "final_probe_ambiguous_or_backend_unsupported"),
                 (startup() + completed() + ["Screencasting with X11"], "ordinary_capture_backend_changed"),
                 (startup() + completed() + ["Screencasting with Other"], "journal_backend_unsupported"),
                 (startup() + completed() + ["Found H.264 encoder: h264_vaapi [vaapi]"], "final_probe_ambiguous_or_backend_unsupported"),
                 (startup() + completed()[:-1] + ["Found H.264 encoder: libx264 [vaapi]"], "final_codec_unsupported"),
                 ([startup()[0].replace(rt.SOURCE_COMMIT, "f" * 40)] + startup()[1:] + completed(), "journal_source_mismatch"),
                 (startup() + completed() + ["Couldn't map 'DP-3' to a monitor index. Falling back to first monitor in list (index=0)."], "capture_output_fallback")]
        for messages, reason in cases:
            with self.subTest(reason=reason):
                self.transport.trace(messages)
                obs = self.runtime.observe(receipt=self.receipt())
                self.assertEqual(obs.capture.status, "unverified")
                self.assertEqual(obs.capture.reason, reason)

    def test_startup_configuration_provenance_cannot_be_stale(self):
        for messages, reason in ((startup(output="DP-1") + completed(), "journal_config_mismatch"),
                                 ([message for message in startup() if "'capture'" not in message] + completed(), "journal_config_incomplete"),
                                 (completed(), "journal_startup_missing"),
                                 (startup() + completed() + ["config: 'capture' = kms"], "journal_config_ambiguous")):
            self.transport.trace(messages)
            self.assertEqual(self.runtime.observe().capture.reason, reason)

    def test_transport_failures_and_oversize_are_explicit_not_truncated_proof(self):
        self.transport.responses[SHOW] = rt.CommandResult(0, b"x" * (1048576 + 1))
        self.assert_refuses(self.runtime.observe, "transport_unavailable")
        self.transport.responses[SHOW] = rt.CommandResult(1, b"", b"fixture failure")
        self.assert_refuses(self.runtime.observe, "command_failed")
        self.transport.responses[SHOW] = subprocess.TimeoutExpired(SHOW, 5)
        self.assert_refuses(self.runtime.observe, "transport_unavailable")
        self.transport.responses.clear()
        def fail_journal(tr, argv):
            if argv[0] == "/usr/bin/journalctl":
                raise PermissionError("fixture journal unavailable")
        self.transport.on_run = fail_journal
        obs = self.runtime.observe()
        self.assertEqual(obs.capture.reason, "journal_unavailable:transport_unavailable")


class FakeOwner:
    def __init__(self, observation):
        self.events = []
        self.idle = rt.OwnerIdleEvidence(observation.host_uniqueid, observation.generation, observation.config.file.sha256,
                                         2, 1, "preparing", 958645192, "FREE", 0, "enrolled_no_launch", False)
        self.after_idle = None
        self.after_mark = None
        self.holding = False

    @contextmanager
    def restart_window(self, expected):
        self.events.append("owner_enter")
        self.holding = True
        try:
            yield self
        finally:
            self.holding = False
            self.events.append("owner_exit")

    def revalidate_idle(self, current):
        assert self.holding
        self.events.append("idle_revalidated")
        if self.after_idle:
            self.after_idle()
        return self.idle

    def mark_restart_attempted(self, current):
        assert self.holding
        self.events.append("attempt_durably_marked")
        if self.after_mark:
            self.after_mark()


class RestartTests(AuthorityFixture):
    def test_first_restart_needs_owner_idle_not_prior_capture_receipt(self):
        before = self.runtime.observe()
        owner = FakeOwner(before)
        def observe_restart(tr, argv):
            if argv == RESTART:
                self.assertTrue(owner.holding)
                self.assertEqual(owner.events[-1], "attempt_durably_marked")
        self.transport.on_run = observe_restart
        result = self.runtime.guarded_restart(before, owner=owner)
        self.assertEqual(result.status, "restart_queued")
        self.assertEqual(result.previous_generation, before.generation)
        self.assertIn("new_verified_generation", result.required_followup)
        self.assertEqual(owner.events, ["owner_enter", "idle_revalidated", "attempt_durably_marked", "owner_exit"])
        self.assertEqual([argv for argv, _, _ in self.transport.calls if "restart" in argv], [RESTART])

    def test_busy_unknown_free_without_enrollment_launch_and_wrong_owner_refuse(self):
        before = self.runtime.observe()
        self.assert_refuses(lambda: self.runtime.guarded_restart(before, owner=None), "restart_owner_required")
        mutations = ({"application_state": "BUSY"}, {"application_state": "unknown"}, {"currentgame": 42},
                     {"enrollment_revision": 0}, {"idle_basis": "raw_FREE"}, {"phase": "active"},
                     {"positive_desktop_app_id": 0}, {"launch_attempted": True}, {"host_uniqueid": "other-host-002"},
                     {"generation": replace(before.generation, pid=43)}, {"config_digest": "0" * 64},
                     {"reservation_revision": True})
        for changes in mutations:
            with self.subTest(changes=changes):
                owner = FakeOwner(before)
                owner.idle = replace(owner.idle, **changes)
                self.assert_refuses(lambda: self.runtime.guarded_restart(before, owner=owner), "restart_idle_unproven")
                self.assertNotIn("attempt_durably_marked", owner.events)
        self.assertFalse(any("restart" in argv for argv, _, _ in self.transport.calls))

    def test_owned_positive_desktop_closed_can_restart_after_launch(self):
        before = self.runtime.observe()
        owner = FakeOwner(before)
        owner.idle = replace(owner.idle, idle_basis="owned_desktop_closed", launch_attempted=True)
        self.assertEqual(self.runtime.guarded_restart(before, owner=owner).status, "restart_queued")

    def test_expected_generation_or_config_change_refuses_before_dispatch(self):
        before = self.runtime.observe()
        owner = FakeOwner(before)
        self.transport.put(CONF, CONFIG + b"# outside edit\n")
        self.assert_refuses(lambda: self.runtime.guarded_restart(before, owner=owner), "restart_snapshot_changed")
        self.assertNotIn("attempt_durably_marked", owner.events)
        self.assertFalse(any("restart" in argv for argv, _, _ in self.transport.calls))

    def test_revalidate_local_snapshot_after_owner_idle_and_durable_mark(self):
        for hook in ("after_idle", "after_mark"):
            with self.subTest(hook=hook):
                before = self.runtime.observe()
                owner = FakeOwner(before)
                setattr(owner, hook, lambda: self.transport.put(CONF, self.transport.files[CONF] + b"# raced\n"))
                self.assert_refuses(lambda: self.runtime.guarded_restart(before, owner=owner), "restart_snapshot_changed")
        self.assertFalse(any("restart" in argv for argv, _, _ in self.transport.calls))

    def test_restart_command_failure_timeout_retains_owner_uncertainty(self):
        before = self.runtime.observe()
        for result in (rt.CommandResult(1, stderr=b"failed"), subprocess.TimeoutExpired(RESTART, 5)):
            with self.subTest(result=result):
                owner = FakeOwner(before)
                self.transport.responses[RESTART] = result
                self.assert_refuses(lambda: self.runtime.guarded_restart(before, owner=owner), "restart_uncertain")
                self.assertIn("attempt_durably_marked", owner.events)


class TransportTests(unittest.TestCase):
    def test_real_private_temp_leaf_read_bounds_and_no_symlink_follow(self):
        with tempfile.TemporaryDirectory(prefix="display-runtime-fixture-") as directory:
            file = Path(directory) / "fixture"
            file.write_bytes(b"bounded fixture")
            transport = rt.LocalTransport()
            self.assertEqual(transport.read(file, limit=15), b"bounded fixture")
            with self.assertRaises(ValueError):
                transport.read(file, limit=2)
            link = Path(directory) / "linked"
            link.symlink_to(file)
            with self.assertRaises(OSError):
                transport.read(link, limit=100)

    def test_bounded_popen_has_no_shell_stdin_or_ambient_credentials(self):
        class Stream:
            def __init__(self, fd):
                self.fd, self.closed = fd, False
            def fileno(self):
                return self.fd
            def close(self):
                self.closed = True
        class Process:
            def __init__(self):
                self.stdout, self.stderr = Stream(10), Stream(11)
                self.returncode, self.killed = None, False
            def poll(self):
                return self.returncode
            def kill(self):
                self.killed = True
            def wait(self, timeout=None):
                self.returncode = 0
                return 0
        class Selector:
            def __init__(self):
                self.keys = {}
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def register(self, stream, events, index):
                self.keys[stream.fd] = types.SimpleNamespace(fd=stream.fd, fileobj=stream, data=index)
            def unregister(self, stream):
                del self.keys[stream.fd]
            def get_map(self):
                return self.keys
            def select(self, timeout):
                return [(key, 1) for key in list(self.keys.values())]
        for limit, should_overflow in ((10, False), (3, True)):
            process = Process()
            chunks = {10: [b"abcd", b""], 11: [b"e", b""]}
            with mock.patch.object(rt.subprocess, "Popen", return_value=process) as popen, \
                 mock.patch.object(rt.selectors, "DefaultSelector", side_effect=Selector), \
                 mock.patch.object(rt.os, "set_blocking"), \
                 mock.patch.object(rt.os, "read", side_effect=lambda fd, n: chunks[fd].pop(0)), \
                 mock.patch.dict(rt.os.environ, {"HOME": "/fixture/home", "SECRET": "not-forwarded"}, clear=True):
                if should_overflow:
                    with self.assertRaises(ValueError):
                        rt.LocalTransport().run(SHOW, timeout=5, max_output=limit)
                    self.assertTrue(process.killed)
                else:
                    result = rt.LocalTransport().run(SHOW, timeout=5, max_output=limit)
                    self.assertEqual((result.stdout, result.stderr), (b"abcd", b"e"))
                argv, = popen.call_args.args
                kwargs = popen.call_args.kwargs
                self.assertEqual(argv, SHOW)
                self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
                self.assertNotIn("shell", kwargs)
                self.assertNotIn("SECRET", kwargs["env"])
                self.assertEqual(kwargs["env"]["LC_ALL"], "C")
                self.assertTrue(process.stdout.closed and process.stderr.closed)


if __name__ == "__main__":
    unittest.main()
