#!/usr/bin/python3
"""Scaled aggregate OOM: private64MiB slice, two48MiB workloads,16MiB control.

Only unique fixture IDs are stopped. Allocators each touch32MiB then sleep;
individual caps exceed each working set, while their aggregate exceeds64MiB.
No assertion selects which workload must be killed.
"""
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
import uuid


def ctl(*args):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=15)


def info(unit):
    proc = ctl("show", unit, "--property=MainPID,Result,ControlGroup,MemoryMax")
    return dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)


class AggregateTests(unittest.TestCase):
    def test_only_private_workload_subtree_takes_aggregate_oom(self):
        token = "resilience-aggregate-" + uuid.uuid4().hex[:12]
        slice_name = "resilienceaggregate" + uuid.uuid4().hex[:12] + ".slice"
        control, anchor, first, second = [token + "-" + name + ".service" for name in ("control", "anchor", "a", "b")]
        units = [control, anchor, first, second]

        def start(unit, mem, command, slice_arg=()):
            proc = subprocess.run(["systemd-run", "--user", "--no-ask-password", "--quiet", "--service-type=exec",
                                   "--unit=" + unit, *slice_arg, "--property=Restart=no", "--property=OOMPolicy=kill",
                                   "--property=RuntimeMaxSec=15s", "--property=TimeoutStopSec=2s",
                                   "--property=MemoryHigh=" + mem, "--property=MemoryMax=" + mem,
                                   "--property=MemorySwapMax=0", "--", *command], capture_output=True, text=True, timeout=15)
            self.assertEqual(proc.returncode, 0, proc.stderr)

        try:
            start(control, "16M", ["/bin/sleep", "12"])
            control_pid = info(control)["MainPID"]
            start(anchor, "16M", ["/bin/sleep", "12"], ("--slice=" + slice_name,))
            limited = ctl("set-property", "--runtime", slice_name, "MemoryHigh=64M", "MemoryMax=64M", "MemorySwapMax=0")
            self.assertEqual(limited.returncode, 0, limited.stderr)
            cg = info(slice_name)["ControlGroup"]
            events_path = Path("/sys/fs/cgroup" + cg + "/memory.events")
            allocator = "import time; a=[bytearray(1024*1024) for _ in range(32)]; time.sleep(10)"
            for unit in (first, second):
                start(unit, "48M", [sys.executable, "-c", allocator], ("--slice=" + slice_name,))
            deadline = time.monotonic() + 6
            while True:
                events = dict(line.split() for line in events_path.read_text().splitlines())
                if int(events["oom_kill"]) > 0:
                    break
                if time.monotonic() >= deadline:
                    self.fail("scaled aggregate cap did not produce the predicted OOM")
                time.sleep(.05)
            local_events = dict(line.split() for line in events_path.with_name("memory.events.local").read_text().splitlines())
            self.assertGreater(int(local_events["max"]), 0, "must hit the parent slice's own cap, not just a child's cap")
            self.assertGreater(int(local_events["oom"]), 0)
            self.assertEqual(info(control)["MainPID"], control_pid)
            self.assertTrue(Path("/proc/" + control_pid).exists())
            self.assertEqual(info(control)["Result"], "success")
            workloads = {unit: info(unit) for unit in (first, second)}
            self.assertEqual(info(slice_name)["MemoryMax"], str(64 * 1024**2))
            print(json.dumps({"AGGREGATE_OOM": {"slice": slice_name, "aggregateMax": 64 * 1024**2,
                                             "perWorkloadMax": 48 * 1024**2, "events": events, "localEvents": local_events,
                                             "workloads": workloads, "independentControlAlive": True}}), flush=True)
        finally:
            ctl("stop", *units)
            ctl("reset-failed", *units)
            ctl("revert", slice_name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
