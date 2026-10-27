#!/usr/bin/python3
"""Fast standard-library checks. Live systemd behavior is in test_job_live.py."""
import argparse
import importlib.util
import pathlib
import tempfile
import unittest

MODULE = pathlib.Path(__file__).resolve().parents[1] / "runtime/job.py"
spec = importlib.util.spec_from_file_location("job", MODULE)
job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(job)


class JobTests(unittest.TestCase):
    def test_binary_amount(self):
        self.assertEqual(job.amount("8G"), 8 * 1024**3)
        self.assertEqual(job.amount("64MiB"), 64 * 1024**2)
        self.assertEqual(job.amount("0"), 0)
        for invalid in ("-1G", "1.5G", "infinity", "8G; echo bad"):
            with self.assertRaises(argparse.ArgumentTypeError):
                job.amount(invalid)

    def test_systemd_quote(self):
        self.assertEqual(job.systemd_word('a b$%"\\'), '"a b$%\\"\\\\"')

    def test_durable_private_json(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = job.private_dir(pathlib.Path(temp) / "runs")
            job.atomic(directory / "test.json", {"argv": ["中文", "a b", "$HOME;\n%"]})
            self.assertEqual(job.read(directory / "test.json")["argv"][0], "中文")
            self.assertEqual((directory / "test.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
