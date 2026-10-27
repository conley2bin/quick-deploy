#!/usr/bin/python3
"""Opt-in live user-manager tests: unique IDs, bounded lifetimes, <=128MiB OOM stress.

No production run is stopped. Small sleep fixtures use RLIMIT_AS=64MiB even
when testing the default 8GiB *reservation*. Run via tmux/pi-notify.
"""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

JOB = Path(__file__).resolve().parents[1] / "runtime/job.py"
PYTHON = sys.executable
SLEEP = "import resource,time; resource.setrlimit(resource.RLIMIT_AS,(64*1024**2,64*1024**2)); time.sleep(8)"


class LiveJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="resilience-job-test-")
        cls.home = Path(cls.temp.name)
        cls.env = dict(os.environ, XDG_STATE_HOME=str(cls.home / "state % $ 中文"))
        cls.ids = []

    @classmethod
    def tearDownClass(cls):
        for ident in cls.ids:
            cls.cli("cancel", ident, check=False)
        cls.temp.cleanup()

    @classmethod
    def cli(cls, *args, check=True, env=None):
        proc = subprocess.run([PYTHON, str(JOB), *args], env=env or cls.env, capture_output=True, text=True, timeout=30)
        if check and proc.returncode:
            raise AssertionError(f"job {args}: {proc.returncode}\n{proc.stdout}\n{proc.stderr}")
        return proc

    def submit(self, name, command, opts=(), env=None):
        proc = self.cli("run", name, *opts, "--", *command, env=env)
        ident = proc.stdout.strip()
        self.ids.append(ident)
        return ident

    def completed(self, ident, expected):
        proc = self.cli("wait", ident, "--timeout", "15", check=False)
        self.assertEqual(proc.returncode, expected, (proc.stdout, proc.stderr))
        value = json.loads(proc.stdout)
        status = json.loads(self.cli("status", ident).stdout)
        self.assertEqual(status["manager"]["LoadState"], "not-found", status)
        self.assertEqual(status["result"], value)
        print(json.dumps({"testRun": ident, "result": value}), flush=True)
        return value

    def test_01_argv_environment_and_collected_result(self):
        cwd = self.home / "cwd 中文 % $ space"
        cwd.mkdir()
        data = cwd / "input data"
        data.write_text("versioned input")
        env = dict(self.env, PRIVATE_TEST_TOKEN="secret-should-not-be-in-metadata", PATH="/usr/bin:/bin")
        args = ["a b", "中文", "$HOME; touch SHOULD_NOT_EXIST", "%s", "line\nbreak", "'quotes'", ""]
        code = "import hashlib,json,os,sys; print(json.dumps([os.getcwd(),sys.argv[1:],hashlib.sha256(os.environ['PRIVATE_TEST_TOKEN'].encode()).hexdigest()],ensure_ascii=True))"
        ident = self.submit("名称 ; quoted job", ["python3", "-c", code, *args],
                            ("--cwd", str(cwd), "--mem", "64M", "--swap", "0", "--input", str(data)), env)
        self.completed(ident, 0)
        import hashlib
        self.assertEqual(json.loads(self.cli("log", ident).stdout),
                         [str(cwd), args, hashlib.sha256(env["PRIVATE_TEST_TOKEN"].encode()).hexdigest()])
        directory = Path(json.loads(self.cli("status", ident).stdout)["directory"])
        metadata = (directory / "run.json").read_text()
        self.assertNotIn("secret-should-not-be-in-metadata", metadata)
        record = json.loads(metadata)
        self.assertEqual(len(record["versions"]["inputs"][0]["sha256"]), 64)
        self.assertFalse(Path(record["environmentFile"]).exists())
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual((directory / "result.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual((directory / "runner.py").read_bytes(), JOB.read_bytes())
        self.assertFalse((cwd / "SHOULD_NOT_EXIST").exists())

    def test_02_fast_failure_signal_and_exec_failure(self):
        for name, code, expected in (("exit19", "raise SystemExit(19)", 19),
                                     ("sigterm", "import os,signal; os.kill(os.getpid(),signal.SIGTERM)", 143)):
            ident = self.submit(name, [PYTHON, "-c", code], ("--mem", "64M", "--swap", "0"))
            self.completed(ident, expected)
        ident = self.submit("execfail", ["missing-resilience-test-executable"], ("--mem", "64M", "--swap", "0"))
        result = self.completed(ident, 127)
        self.assertIn("execError", result)

    def test_03_service_start_rejection(self):
        bindir = self.home / "fake-bin"
        bindir.mkdir()
        fake = bindir / "systemd-run"
        fake.write_text("#!/bin/sh\necho 'injected manager launch rejection' >&2\nexit 23\n")
        fake.chmod(0o700)
        env = dict(self.env, PATH=str(bindir) + ":" + os.environ["PATH"])
        proc = self.cli("run", "start-rejected", "--mem", "64M", "--swap", "0", "--", "/bin/true", check=False, env=env)
        self.assertEqual(proc.returncode, 125, proc.stderr)
        ident = proc.stderr.splitlines()[0]
        self.ids.append(ident)
        result = self.completed(ident, 125)
        self.assertEqual(result["serviceResult"], "start-failed")

    def test_04_concurrent_default_reservations(self):
        def attempt(index):
            return self.cli("run", f"admission-{index}", "--high", "64M", "--swap", "0", "--", PYTHON, "-c", SLEEP, check=False)
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            attempts = list(pool.map(attempt, range(3)))
        accepted = [p.stdout.strip() for p in attempts if p.returncode == 0]
        self.ids.extend(accepted)
        self.assertEqual(len(accepted), 2, [(p.returncode, p.stdout, p.stderr) for p in attempts])
        refused = [p for p in attempts if p.returncode != 0]
        self.assertEqual(refused[0].returncode, 75)
        self.assertIn("admission refused", refused[0].stderr)
        alternate = dict(self.env, XDG_STATE_HOME=str(self.home / "another-state-home"))
        other = self.cli("run", "other-state", "--high", "64M", "--swap", "0", "--", PYTHON, "-c", SLEEP, check=False, env=alternate)
        self.assertEqual(other.returncode, 75, other.stderr)
        print("CONCURRENT_ADMISSION: two8GiB accepted, third refused, alternate XDG state also refused; payloads RLIMIT_AS64MiB", flush=True)
        for ident in accepted:
            self.cli("cancel", ident)

    def test_05_oom_containment(self):
        sentinel = self.submit("sentinel", [PYTHON, "-c", "import time; time.sleep(8)"], ("--mem", "64M", "--swap", "0"))
        allocator = "import time; a=[]\nwhile True:\n a.append(bytearray(1024*1024)); time.sleep(.003)"
        victim = self.submit("oom64m", [PYTHON, "-c", allocator], ("--mem", "64M", "--high", "64M", "--swap", "0"))
        result = self.completed(victim, 137)
        self.assertEqual(result["serviceResult"], "oom-kill")
        self.assertGreaterEqual(int(result["memoryEvents"]["oom_kill"]), 1)
        info = json.loads(self.cli("status", sentinel).stdout)
        self.assertEqual(info["state"], "running", info)
        self.cli("cancel", sentinel)
        print("OOM_CONTAINMENT: 64MiB job killed; independent64MiB service alive", flush=True)

    def test_06_interrupted_submission_retains_lock_and_reconciles(self):
        bindir = self.home / "delayed-rejection"
        bindir.mkdir()
        marker = self.home / "launch-entered"
        fake = bindir / "systemd-run"
        fake.write_text("#!/bin/sh\nprintf ready > '" + str(marker) + "'\nsleep .6\nexit 23\n")
        fake.chmod(0o700)
        env = dict(self.env, PATH=str(bindir) + ":" + os.environ["PATH"])
        proc = subprocess.Popen([PYTHON, str(JOB), "run", "interrupted", "--mem", "64M", "--swap", "0", "--", "/bin/true"],
                                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        end = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < end:
            time.sleep(.01)
        self.assertTrue(marker.exists())
        records = list((Path(self.env["XDG_STATE_HOME"]) / "workstation-jobs").glob("interrupted-*/run.json"))
        self.assertEqual(len(records), 1)
        ident = json.loads(records[0].read_text())["id"]
        self.ids.append(ident)
        proc.kill()
        proc.wait(timeout=2)
        before = time.monotonic()
        following = self.submit("after-interruption", ["/bin/true"], ("--mem", "64M", "--swap", "0"))
        self.assertGreater(time.monotonic() - before, .3, "submitting parent's death released the child's admission lock")
        self.completed(following, 0)
        value = json.loads(self.cli("status", ident).stdout)
        self.assertEqual(value["state"], "unknown")
        ledger = Path(f"/run/user/{os.getuid()}/workstation-jobs/{ident}.reservation.json")
        self.assertTrue(ledger.exists(), "uncertain submission must fail closed")
        self.cli("cancel", ident)
        self.assertFalse(ledger.exists())
        self.completed(ident, 125)
        print("INTERRUPTION: child retained admission lock; unknown result held reservation until explicit cancel", flush=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
