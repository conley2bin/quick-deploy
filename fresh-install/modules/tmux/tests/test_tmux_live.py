#!/usr/bin/python3
"""Opt-in managed runtime tests. Requires narrow installer/pane drop-in first.

Unique private socket services; one64MiB pane OOM. No legacy endpoint or
production managed socket is stopped. Run via tmux/pi-notify or established job.
"""
import concurrent.futures
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

MODULE = Path(__file__).resolve().parents[1]
WRAPPER = MODULE / "runtime/tmux_managed.py"
JOB = MODULE / "runtime/job.py"
PYTHON = sys.executable


def wait_for(function, timeout=6):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = function()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("bounded fixture condition not reached")


def ctl(*args):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=15)


class ManagedTmuxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="resilience-tmux-test-")
        cls.directory = Path(cls.temp.name)
        cls.config = cls.directory / "config.json"
        cls.config.write_text(json.dumps({"executable": "/usr/bin/tmux", "config_file": "/dev/null"}))
        cls.name = "test-" + uuid.uuid4().hex[:12]
        cls.names = [cls.name]
        cls.env = dict(os.environ, XDG_STATE_HOME=str(cls.directory / "state"), RESILIENCE_TMUX_ENV="caller-value")
        cls.jobs = []

    @classmethod
    def tearDownClass(cls):
        for ident in cls.jobs:
            subprocess.run([PYTHON, str(JOB), "cancel", ident], env=cls.env, capture_output=True, timeout=20)
        for name in cls.names:
            path = Path(os.environ["XDG_RUNTIME_DIR"]) / "tmux-managed" / ("managed-" + name + ".owner.json")
            if not path.exists():
                continue
            record = json.loads(path.read_text())
            # Cleanup only this test's recorded service, never a reused name.
            description = ctl("show", record["unit"], "--property=Description", "--value").stdout.strip()
            if description == "tmux-managed:" + record["nonce"]:
                ctl("stop", record["unit"])
                cls.wrapper("clear-stale", name=name, check=False)
        cls.temp.cleanup()

    @classmethod
    def wrapper(cls, *args, name=None, check=True, config=None):
        proc = subprocess.run([PYTHON, str(WRAPPER), "--config", str(config or cls.config), "--socket", name or cls.name, *args],
                              env=cls.env, capture_output=True, text=True, timeout=15)
        if check and proc.returncode:
            raise AssertionError(f"tmux-managed {args}: {proc.returncode}: {proc.stderr}")
        return proc

    def tx(self, *args):
        return self.wrapper("--", *args).stdout.strip()

    def test_01_concurrent_cold_start_without_sessions(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(lambda _: json.loads(self.wrapper("start").stdout), range(4)))
        pids = {row["serverPID"] for row in outcomes}
        self.assertEqual(len(pids), 1, outcomes)
        for value in outcomes:
            self.assertEqual(value["serverPID"], value["peerPID"])
            self.assertIn("/" + value["slice"] + "/", value["cgroup"])
        status = json.loads(self.wrapper("status").stdout)
        self.assertEqual(status["state"], "ready")
        # A no-session foreground server is ready; no anchor pane was created.
        proc = self.wrapper("--", "list-sessions", check=False)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertEqual(json.loads(self.wrapper("status").stdout)["serverPID"], status["serverPID"])
        print("COLD_START: four callers, one MainPID=peerPID, no sessions, slice=" + status["slice"], flush=True)

    def test_02_refuse_unowned_endpoint_and_executable_mismatch(self):
        name = "test-" + uuid.uuid4().hex[:12]
        status = json.loads(self.wrapper("status", name=name).stdout)
        endpoint = Path(status["socketPath"])
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(endpoint))
            listener.listen(2)
            listener.setblocking(False)
            before = endpoint.stat().st_ino
            proc = self.wrapper("start", name=name, check=False)
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(endpoint.stat().st_ino, before)
            with self.assertRaises(BlockingIOError):
                listener.accept()  # wrapper must not even query the foreign peer
            self.assertEqual(ctl("show", status["unit"], "--property=LoadState", "--value").stdout.strip(), "not-found")
        endpoint.unlink()  # this test's raw fixture, never wrapper-owned/adopted
        altered = self.directory / "wrong-exe.json"
        altered.write_text(json.dumps({"executable": "/bin/true", "config_file": "/dev/null"}))
        proc = self.wrapper("start", config=altered, check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("mismatch", proc.stderr)
        print("OWNERSHIP: foreign live endpoint untouched/unqueried; executable mismatch refuses restart", flush=True)

    def test_03_pane_slice_environment_and_oom_secondary_cleanup(self):
        value = self.tx("new-session", "-d", "-P", "-F", "#{pane_id}:#{pane_pid}", "-s", "oom-fixture",
                        "-e", "TMOUT=30", "/bin/bash", "--noprofile", "--norc")
        pane, pid = value.split(":")
        def pane_scope():
            current = Path(f"/proc/{pid}/cgroup").read_text().strip().split("::", 1)[1]
            return current if current.rsplit("/", 1)[1].startswith("tmux-spawn-") else None
        # The distro's scope handoff is asynchronous to the client reply.
        # Measure the actual destination rather than its pre-handoff instant.
        cg = wait_for(pane_scope)
        scope = cg.rsplit("/", 1)[1]
        mapping = json.loads(self.wrapper("status").stdout)
        self.assertTrue(scope.startswith("tmux-spawn-") and scope.endswith(".scope"), scope)
        self.assertEqual(cg.rsplit("/", 1)[0], mapping["cgroup"].rsplit("/", 1)[0])
        policy = ctl("show", scope, "--property=OOMPolicy", "--value").stdout.strip()
        self.assertEqual(policy, "continue", "install targeted tmux-spawn-.scope.d drop-in first")
        limited = ctl("set-property", "--runtime", scope, "MemoryHigh=64M", "MemoryMax=64M", "MemorySwapMax=0")
        self.assertEqual(limited.returncode, 0, limited.stderr)
        allocator = self.directory / "allocate.py"
        allocator.write_text("import time\nopen('/proc/self/oom_score_adj','w').write('1000')\na=[]\nwhile True:\n a.append(bytearray(1024*1024)); time.sleep(.002)\n")
        survived = self.directory / "shell-survived"
        command = shlex.join([PYTHON, str(allocator)]) + "; printf '%s' \"$RESILIENCE_TMUX_ENV\" > " + shlex.quote(str(survived))
        self.tx("send-keys", "-t", pane, "-l", "--", command)
        self.tx("send-keys", "-t", pane, "Enter")
        wait_for(lambda: survived.exists(), timeout=12)
        self.assertEqual(survived.read_text(), "caller-value")
        self.assertTrue(Path(f"/proc/{pid}").exists(), "interactive shell was lost to secondary cleanup")
        events = dict(line.split() for line in Path("/sys/fs/cgroup" + cg + "/memory.events").read_text().splitlines())
        self.assertGreaterEqual(int(events["oom_kill"]), 1)
        self.assertEqual(self.tx("display-message", "-p", "-t", pane, "#{pane_pid}"), pid)
        self.assertEqual(json.loads(self.wrapper("status").stdout)["state"], "ready")
        print(json.dumps({"PANE_OOM": {"scope": scope, "cgroup": cg, "OOMPolicy": policy, "shellPID": pid,
                                      "events": events, "shellSurvived": True}}), flush=True)
        self.tx("kill-pane", "-t", pane)
        # Remove only transient set-property files for this exact test scope.
        ctl("revert", scope)

    def test_04_launcher_pane_teardown_preserves_job_and_other_server(self):
        other = "test-" + uuid.uuid4().hex[:12]
        self.names.append(other)
        runfile = self.directory / "detached-run-id"
        otherfile = self.directory / "other-server.json"
        launcher = self.directory / "launch.sh"
        launcher.write_text("#!/bin/bash\nset -eu\n" + shlex.join([PYTHON, str(WRAPPER), "--config", str(self.config), "--socket", other, "start"]) + " > " + shlex.quote(str(otherfile)) + "\n" +
                            shlex.join([PYTHON, str(JOB), "run", "detached-fixture", "--mem", "64M", "--swap", "0", "--", PYTHON, "-c", "import time; time.sleep(2); print('detached-completed')"]) + " > " + shlex.quote(str(runfile)) + "\nsleep 20\n")
        pane = self.tx("new-session", "-d", "-P", "-F", "#{pane_id}", "-s", "launcher-fixture", "/bin/bash", str(launcher))
        wait_for(lambda: runfile.exists() and runfile.stat().st_size > 0)
        ident = runfile.read_text().strip()
        self.jobs.append(ident)
        submitted = subprocess.run([PYTHON, str(JOB), "status", ident], env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(json.loads(submitted.stdout)["state"], "running")
        other_before = json.loads(otherfile.read_text())
        self.tx("kill-pane", "-t", pane)
        other_after = json.loads(self.wrapper("status", name=other).stdout)
        self.assertEqual(other_after["serverPID"], other_before["serverPID"])
        proc = subprocess.run([PYTHON, str(JOB), "wait", ident, "--timeout", "8"], env=self.env, capture_output=True, text=True, timeout=12)
        self.assertEqual(proc.returncode, 0, (proc.stdout, proc.stderr))
        output = subprocess.run([PYTHON, str(JOB), "log", ident], env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(output.stdout.strip(), "detached-completed")
        print("DETACH: launching pane killed; job completed/recorded; other foreground server same MainPID", flush=True)

    def test_05_owned_sigusr1_rebind_preserves_server_and_session(self):
        pane = self.tx("new-session", "-d", "-P", "-F", "#{pane_id}", "-s", "rebind-fixture", "/bin/sleep", "20")
        before = json.loads(self.wrapper("status").stdout)
        self.assertTrue(self.name.startswith("test-"))
        endpoint = Path(before["socketPath"])
        endpoint.unlink()
        # Signal only this unique test service's verified MainPID. Production
        # sockets/servers are neither removed nor signalled by this fixture.
        os.kill(before["serverPID"], signal.SIGUSR1)
        wait_for(lambda: endpoint.exists() and [endpoint.stat().st_dev, endpoint.stat().st_ino] != before["socketIdentity"])
        after = json.loads(self.wrapper("status").stdout)
        self.assertEqual(after["state"], "ready", after)
        self.assertEqual(after["serverPID"], before["serverPID"])
        self.assertEqual(after["peerPID"], before["serverPID"])
        self.assertNotEqual(after["socketIdentity"], before["socketIdentity"])
        owner = endpoint.with_name(endpoint.name + ".owner.json")
        self.assertEqual(json.loads(owner.read_text())["socketIdentity"], after["socketIdentity"])
        self.tx("has-session", "-t", "rebind-fixture")
        self.assertEqual(json.loads(self.wrapper("start").stdout)["serverPID"], before["serverPID"])
        print(json.dumps({"SOCKET_REBIND": {"serverPID": after["serverPID"], "oldInode": before["socketIdentity"],
                                          "newInode": after["socketIdentity"], "sessionSurvived": True}}), flush=True)
        self.tx("kill-pane", "-t", pane)


if __name__ == "__main__":
    unittest.main(verbosity=2)
