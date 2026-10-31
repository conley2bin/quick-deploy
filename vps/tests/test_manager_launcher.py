import argparse
import contextlib
import http.client
import io
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import manage_cli

SETUP = Path(__file__).resolve().parents[1]


class ManageScript(unittest.TestCase):
    def test_manage_sh_is_executable_and_parses_from_any_cwd(self):
        self.assertTrue(os.access(SETUP / 'manage.sh', os.X_OK))
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run(['bash', str(SETUP / 'manage.sh'), '--help'], cwd=cwd,
                                    capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        for flag in ('--state-dir', '--ssh-proxy', '--independent-carrier', '--no-open'):
            self.assertIn(flag, result.stdout)
        for command in ('connect', 'devices', 'recover', 'backup'):
            self.assertIn(command, result.stdout)
        for gone in ('install', 'rotate', 'systemd'):
            self.assertNotIn(gone, result.stdout)
        self.assertNotIn('--job', result.stdout)
        self.assertIn('--identity', result.stdout)

    def test_manage_sh_targets_foreground_cli_with_private_umask(self):
        text = (SETUP / 'manage.sh').read_text()
        self.assertIn('umask 077', text)
        self.assertIn('lib/manage_cli.py', text)
        self.assertNotIn('manage_service', text)
        self.assertIn('SCRIPT_DIR', text)

    def test_setup_has_no_manage_and_points_to_manage_sh(self):
        helped = subprocess.run(['bash', str(SETUP / 'setup.sh'), '--help'], capture_output=True, text=True, timeout=20)
        self.assertNotIn('manage-job', helped.stdout)
        self.assertNotRegex(helped.stdout, r'[{,]manage[,}]')
        for command in ('manage', 'manage-job'):
            result = subprocess.run(['bash', str(SETUP / 'setup.sh'), command], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 2)
            self.assertIn('manage.sh', result.stderr)

    def test_parser_defaults_to_connect_and_ignores_no_open(self):
        self.assertEqual(manage_cli.parser().parse_args([]).words, [])
        self.assertTrue(manage_cli.parser().parse_args(['--no-open']).no_open)


if __name__ == '__main__':
    unittest.main()
