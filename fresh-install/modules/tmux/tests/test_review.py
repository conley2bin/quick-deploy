#!/usr/bin/python3
"""Focused offline regressions for reviewed submission contracts; no manager calls."""
import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / "runtime/job.py"
spec = importlib.util.spec_from_file_location("reviewed_job", SOURCE)
job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(job)


class SubmissionReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="resilience-review-test-")
        self.directory = Path(self.temp.name)
        self.root = job.private_dir(self.directory / "records")
        self.runtime = job.private_dir(self.directory / "runtime")
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(mock.patch.object(job, "admission_root", return_value=self.runtime))
        self.stack.enter_context(mock.patch.object(job, "reservation_total", return_value=0))
        self.stack.enter_context(mock.patch.object(job, "versions", return_value={"git": None, "inputs": []}))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    def args(self, mem=job.DEFAULT_MAX, high=None):
        return argparse.Namespace(name="review-fixture", mem=mem, high=high, swap=0,
                                  cwd=str(self.directory), input=[], command=["/bin/true"])

    def record(self):
        path = next(self.root.glob("*/run.json"))
        return path.parent, json.loads(path.read_text())

    def test_default_high_tracks_declared_max_and_stop_timeout(self):
        for size in (8, 16):
            with self.subTest(gib=size), mock.patch.object(job.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as launch:
                self.assertEqual(job.submit(self.args(size * 1024**3), self.root), 0)
                command = launch.call_args.args[0]
                self.assertIn("--property=MemoryHigh=" + str(size * 1024**3 * 3 // 4), command)
                self.assertIn("--property=TimeoutStopSec=30s", command)

    def test_explicit_high_preserved(self):
        with mock.patch.object(job.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as launch:
            self.assertEqual(job.submit(self.args(16 * 1024**3, 10 * 1024**3), self.root), 0)
        self.assertIn("--property=MemoryHigh=" + str(10 * 1024**3), launch.call_args.args[0])

    def test_minimum32m_rejects_before_launch(self):
        with mock.patch.object(job.subprocess, "run") as launch:
            for size in (1024, 8 * 1024**2, 32 * 1024**2 - 1):
                with self.subTest(bytes=size), self.assertRaisesRegex(RuntimeError, "32M <= mem"):
                    job.submit(self.args(size), self.root)
            launch.assert_not_called()
        self.assertEqual(list(self.root.glob("*/run.json")), [])

    def test_detached_environment_discards_only_inherited_tmux_identity(self):
        with mock.patch.dict(job.os.environ, {"TMUX": "foreign-socket,999,0", "TMUX_PANE": "%999", "KEEP_TEST_VALUE": "required"}), \
             mock.patch.object(job.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")):
            self.assertEqual(job.submit(self.args(32 * 1024**2), self.root), 0)
        _, record = self.record()
        environment = json.loads(Path(record["environmentFile"]).read_text())
        self.assertNotIn("TMUX", environment)
        self.assertNotIn("TMUX_PANE", environment)
        self.assertEqual(environment["KEEP_TEST_VALUE"], "required")

    def test_nonzero_ack_existing_or_queued_unit_is_uncertain(self):
        for active in ("active", "activating", "inactive", "failed"):
            with self.subTest(active=active), \
                 mock.patch.object(job.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "ack timed out")), \
                 mock.patch.object(job, "unit_info", return_value={"LoadState": "loaded", "ActiveState": active}):
                self.assertEqual(job.submit(self.args(32 * 1024**2), self.root), 70)
        for path in self.root.glob("*/run.json"):
            record = json.loads(path.read_text())
            self.assertEqual(record["submission"], "pending")
            self.assertFalse((path.parent / "result.json").exists())
            reservation = json.loads((self.runtime / (record["id"] + ".reservation.json")).read_text())
            self.assertEqual(reservation["submission"], "pending")

    def test_nonzero_ack_completed_run_is_not_rejection(self):
        def completed(*args, **kwargs):
            directory, _ = self.record()
            job.local_result(directory, "success", 0)
            return subprocess.CompletedProcess([], 1, "", "ack lost after completion")
        with mock.patch.object(job.subprocess, "run", side_effect=completed), \
             mock.patch.object(job, "unit_info", return_value={"LoadState": "not-found", "ActiveState": "inactive"}):
            self.assertEqual(job.submit(self.args(32 * 1024**2), self.root), 70)
        directory, record = self.record()
        self.assertEqual(record["submission"], "pending")
        self.assertEqual(json.loads((directory / "result.json").read_text())["exitCode"], 0)

    def test_nonzero_ack_missing_unit_is_explicit_rejection(self):
        with mock.patch.object(job.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "injected start rejection")), \
             mock.patch.object(job, "unit_info", return_value={"LoadState": "not-found", "ActiveState": "inactive"}):
            self.assertEqual(job.submit(self.args(32 * 1024**2), self.root), 125)
        directory, record = self.record()
        self.assertEqual(record["submission"], "rejected")
        self.assertFalse(Path(record["environmentFile"]).exists())
        self.assertEqual(json.loads((directory / "result.json").read_text())["serviceResult"], "start-failed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
