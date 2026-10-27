#!/usr/bin/python3
"""Actual entrypoint removal/reinstall while private job/server stay alive.

Uses an isolated install HOME, one64MiB job, a90second test socket service, and
exact owned cleanup. The real user's installed entrypoints/units are untouched.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

MODULE = Path(__file__).resolve().parents[1]
JOB = MODULE / "runtime/job.py"
WRAPPER = MODULE / "runtime/tmux_managed.py"
INSTALL = MODULE / "install-runtime.py"


class RollbackTests(unittest.TestCase):
    def test_live_lifetime_independent_of_entrypoints(self):
        with tempfile.TemporaryDirectory(prefix="resilience-rollback-test-") as temp:
            home = Path(temp)
            env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"), XDG_STATE_HOME=str(home / "state"))
            name = "test-" + uuid.uuid4().hex[:12]
            config = home / "test-config.json"
            config.write_text('{"executable":"/usr/bin/tmux","config_file":"/dev/null"}')
            ident = None

            def run(*args, check=True):
                proc = subprocess.run([sys.executable, *map(str, args)], env=env, capture_output=True, text=True, timeout=15)
                if check:
                    self.assertEqual(proc.returncode, 0, (args, proc.stdout, proc.stderr))
                return proc

            def tx(*args):
                return run(WRAPPER, "--config", config, "--socket", name, *args)

            try:
                run(INSTALL, "--no-reload", "--tmux", "/usr/bin/tmux")
                before = json.loads(tx("start").stdout)
                ident = run(home / ".local/bin/job", "run", "rollback-fixture", "--mem", "64M", "--swap", "0", "--",
                            sys.executable, "-c", "import time; time.sleep(2); print('survived-rollback')").stdout.strip()
                removed = run(INSTALL, "--no-reload", "--uninstall")
                self.assertIn("retained aggregate policy", removed.stdout)
                self.assertTrue((home / ".config/systemd/user/jobs.slice").is_symlink())
                self.assertTrue((home / ".config/workstation-resilience/install.json").exists())
                self.assertFalse((home / ".local/bin/job").exists())
                self.assertFalse((home / ".local/bin/tmux-managed").exists())
                self.assertEqual(json.loads(tx("status").stdout)["serverPID"], before["serverPID"])
                result = run(JOB, "wait", ident, "--timeout", "8")
                self.assertEqual(json.loads(result.stdout)["exitCode"], 0)
                self.assertEqual(run(JOB, "log", ident).stdout.strip(), "survived-rollback")
                state = json.loads(run(JOB, "status", ident).stdout)
                self.assertEqual(state["manager"]["LoadState"], "not-found")
                run(INSTALL, "--no-reload", "--tmux", "/usr/bin/tmux")
                self.assertEqual(json.loads(tx("start").stdout)["serverPID"], before["serverPID"])
                print("LIVE_ROLLBACK: entrypoints removed/reinstalled, aggregate policy retained, job finished with persisted result, server same MainPID", flush=True)
            finally:
                if ident:
                    run(JOB, "cancel", ident, check=False)
                owner = Path(os.environ["XDG_RUNTIME_DIR"]) / "tmux-managed" / ("managed-" + name + ".owner.json")
                if owner.exists():
                    record = json.loads(owner.read_text())
                    query = subprocess.run(["systemctl", "--user", "show", record["unit"], "--property=Description", "--value"],
                                           capture_output=True, text=True, timeout=10)
                    if query.stdout.strip() == "tmux-managed:" + record["nonce"]:
                        subprocess.run(["systemctl", "--user", "stop", record["unit"]], capture_output=True, timeout=10)
                        run(WRAPPER, "--config", config, "--socket", name, "clear-stale", check=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
