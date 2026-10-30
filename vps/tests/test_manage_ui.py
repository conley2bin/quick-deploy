"""Execute the real manager JavaScript with fake native material and HTTP only."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from test_dashboard_labels import fixture, THREE_CASES, MALICIOUS_LABEL

ROOT = Path(__file__).resolve().parents[1]


class ManageUI(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js required for executed UI semantics')
    def test_session_credentials_mutations_recovery_and_expiry(self):
        data, _, _, _ = fixture(THREE_CASES)
        data['model']['devices'][0]['links'][1]['label'] = MALICIOUS_LABEL
        result = subprocess.run(['node', str(ROOT / 'tests' / 'manage_ui.mjs')],
                                input=json.dumps(data), capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'passed')
        self.assertGreaterEqual(len(report['checks']), 10)


if __name__ == '__main__':
    unittest.main()
