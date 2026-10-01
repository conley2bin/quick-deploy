#!/usr/bin/env python3
"""Actual shell/pipefail probe against owned fake systemctl; no manager I/O.

--common selects a private exact-old-helper copy for the red causal control.
No test changes regex semantics or bypasses producer errors. Fake output is
retained only in private fixtures and must never reach the probe's stdout.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

COMMON = Path(__file__).resolve().parents[1] / "lib/common.sh"
FAKE_SYSTEMCTL = '''#!/usr/bin/python3 -B
import json, os, signal, sys, time
from pathlib import Path
assert sys.argv[1:] == ['--user', 'show-environment'], 'unexpected fixture command'
signal.signal(signal.SIGPIPE, signal.SIG_DFL)
settings = json.loads(Path(os.environ['PROBE_INPUT']).read_text())
prefix, tail = settings['prefix'].encode(), settings['tail'].encode()
os.write(1, prefix)
time.sleep(settings['delay'])
for start in range(0, len(tail), 4096):
    os.write(1, tail[start:start+4096])
Path(os.environ['PROBE_EOF']).write_text(str(len(prefix) + len(tail)))
raise SystemExit(settings['exit'])
'''


class EnvironmentProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qd-environment-probe-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        program = self.bin / "systemctl"
        program.write_text(FAKE_SYSTEMCTL)
        program.chmod(0o700)
        self.input = self.root / "input.json"
        self.eof = self.root / "eof-written"

    def probe(self, prefix, session="", *, tail="UNUSED=value\n", delay=0.02, exit=0):
        self.input.write_text(json.dumps({"prefix": prefix, "tail": tail, "delay": delay, "exit": exit}))
        if self.eof.exists():
            self.eof.unlink()
        # No actual HOME/NSS/environment/service/query. Source is the real
        # helper; the only systemctl resolvable here is this owned executable.
        env = {"PATH": str(self.bin) + ":/usr/bin:/bin", "HOME": str(self.home),
               "TMPDIR": str(self.root), "LC_ALL": "C", "PROBE_INPUT": str(self.input),
               "PROBE_EOF": str(self.eof)}
        result = subprocess.run(("/bin/bash", "--noprofile", "--norc", "-c",
                                 'set -euo pipefail; . "$1"; qd_check_display_environment "$2"',
                                 "owned-fixture", str(COMMON), session),
                                env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=5)
        self.assertEqual(result.stdout, b"")
        return result

    def assert_eof(self, prefix, tail):
        self.assertTrue(self.eof.exists(), "producer was cut off before completing its stream")
        self.assertEqual(int(self.eof.read_text()), len((prefix + tail).encode()))

    def test_valid_matching_prefix_delayed_tail_consumed(self):
        tail = "XDG_CONFIG_HOME=/owned/fixture-config\nUNUSED=complete\n"
        for prefix, session in (("DISPLAY=:1\n", "2 (x11)"),
                                ("WAYLAND_DISPLAY=wayland-0\n", "2 (wayland)")):
            with self.subTest(session=session):
                result = self.probe(prefix, session, tail=tail, delay=0.08)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, b"")
                self.assert_eof(prefix, tail)

    def test_valid_matching_prefix_large_tail_consumed(self):
        prefix = "DISPLAY=:1\n"
        tail = "UNUSED=owned-fixture-data\n" * 20000
        result = self.probe(prefix, tail=tail, delay=0.03)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_eof(prefix, tail)

    def test_real_producer_nonzero_after_match_still_refused(self):
        prefix, tail = "DISPLAY=:1\n", "UNUSED=producer-finished-but-failed\n"
        result = self.probe(prefix, "2 (x11)", tail=tail, exit=7)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"DISPLAY/WAYLAND_DISPLAY", result.stderr)
        self.assert_eof(prefix, tail)

    def test_default_x11_wayland_keep_original_regex_semantics(self):
        for prefix, session, allowed in (
            ("DISPLAY=:1\n", "", True),
            ("WAYLAND_DISPLAY=wayland-0\n", "", True),
            ("DISPLAY=:1\n", "unclassified", True),
            ("DISPLAY=:1\n", "2 (x11)", True),
            ("WAYLAND_DISPLAY=wayland-0\n", "2 (x11)", False),
            ("WAYLAND_DISPLAY=wayland-0\n", "2 (wayland)", True),
            ("DISPLAY=:1\n", "2 (wayland)", False),
            ("DISPLAY= \n", "2 (x11)", True),  # Existing .+ semantics, not a new policy.
        ):
            with self.subTest(prefix=prefix, session=session):
                result = self.probe(prefix, session)
                self.assertEqual(result.returncode == 0, allowed, result.stderr)
                self.assert_eof(prefix, "UNUSED=value\n")

    def test_missing_empty_wrong_keys_remain_refused(self):
        for prefix in ("", "DISPLAY=\n", "WAYLAND_DISPLAY=\n", " display=:1\n",
                       " DISPLAY=:1\n", "NOT_DISPLAY=:1\n"):
            with self.subTest(prefix=prefix):
                result = self.probe(prefix)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b"DISPLAY/WAYLAND_DISPLAY", result.stderr)
                self.assert_eof(prefix, "UNUSED=value\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--common", type=Path)
    options, remaining = parser.parse_known_args()
    if options.common is not None:
        COMMON = options.common.resolve(strict=True)
    unittest.main(argv=[sys.argv[0], *remaining], verbosity=2)
