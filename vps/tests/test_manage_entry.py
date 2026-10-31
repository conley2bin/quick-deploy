"""The private entry is self-contained and its CSP hashes match the embedded bytes."""
import base64
import hashlib
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from manage_entry import render_entry  # noqa: E402

ORIGIN = 'http://127.0.0.1:47831'
FAKE_KEY = 'ab' * 32


def assets():
    return {name: (ROOT / 'assets' / name).read_text() for name in ('manage.html', 'manage.js', 'manage.css', 'dashboard.css')}


def sha(text):
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


class ManageEntry(unittest.TestCase):
    def setUp(self):
        self.page = render_entry(assets(), ORIGIN, FAKE_KEY).decode()

    def test_csp_hashes_match_embedded_bytes_without_unsafe_inline(self):
        csp = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', self.page).group(1)
        directives = dict(item.strip().split(' ', 1) for item in csp.split(';'))
        scripts = re.findall(r'<script>(.*?)</script>', self.page, re.S)
        styles = re.findall(r'<style>(.*?)</style>', self.page, re.S)
        self.assertEqual((len(scripts), len(styles)), (1, 1))
        self.assertEqual(directives['script-src'], sha(scripts[0]))
        self.assertEqual(directives['style-src'], sha(styles[0]))
        self.assertEqual(directives['connect-src'], ORIGIN)
        self.assertEqual(directives['img-src'], 'data: blob:')
        self.assertEqual((directives['base-uri'], directives['object-src'], directives['default-src']), ("'none'",) * 3)
        self.assertNotIn('unsafe', csp)
        self.assertEqual(scripts[0], assets()['manage.js'])

    def test_no_external_code_placeholder_or_redirect(self):
        self.assertNotRegex(self.page, r'<script[^>]+src=|<link[^>]+href=|@import|http-equiv="refresh"|\{\{')
        self.assertNotIn('http://', re.sub(r'http://127\.0\.0\.1:47831|http://www\.w3\.org/\d+/[a-z]+', '', self.page))

    def test_bootstrap_json_and_only_the_key(self):
        raw = re.search(r'<script type="application/json" id="manage-bootstrap">(.*?)</script>', self.page, re.S).group(1)
        self.assertEqual(json.loads(raw), {'version': 1, 'origin': ORIGIN, 'bootstrap_key': FAKE_KEY})
        self.assertEqual(self.page.count(FAKE_KEY), 1)
        self.assertNotRegex(self.page, r'uuid|[a-z0-9]{8}-[a-z0-9]{4}-', )

    def test_rejects_invalid_inputs_and_unsafe_assets(self):
        for origin in ('http://localhost:1', 'https://127.0.0.1:1', 'http://127.0.0.1:99999', ORIGIN + '/x', 'null'):
            with self.assertRaises(ValueError):
                render_entry(assets(), origin, FAKE_KEY)
        for key in ('AB' * 32, 'ab' * 31, ''):
            with self.assertRaises(ValueError):
                render_entry(assets(), ORIGIN, key)
        hostile = assets()
        hostile['manage.js'] += '\n"</script><script>x</script>"'
        with self.assertRaises(ValueError):
            render_entry(hostile, ORIGIN, FAKE_KEY)

    def test_deterministic_for_same_inputs(self):
        self.assertEqual(self.page.encode(), render_entry(assets(), ORIGIN, FAKE_KEY))


if __name__ == '__main__':
    unittest.main()
