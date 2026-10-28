"""Fake native links only: execute dashboard rendering without rewriting payloads."""
import base64
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest import mock
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import link_dashboard as dashboard

ROOT = Path(__file__).resolve().parents[1]
MALICIOUS_LABEL = '</script><img src=x onerror=alert(1)> 原生标签'
AUTHORITY = '00000000-0000-4000-8000-000000000001@node.example.invalid:443'
CASES = [
    ('vless://' + AUTHORITY + '?type=tcp&security=reality#Original-REALITY', 'VLESS / REALITY · vless://', 'R'),
    ('vless://' + AUTHORITY + '?type=xhttp&security=tls&path=%2fMiXeD%2F&x=%2f&x=%2F&v=+%20#' + quote(MALICIOUS_LABEL), 'VLESS / XHTTP / TLS · vless://', 'X'),
    ('vless://' + AUTHORITY + '?type=xhttp&path=%2Fdirect#Direct-XHTTP', 'VLESS / XHTTP · vless://', 'X'),
    ('vless://' + AUTHORITY + '?type=tcp&security=tls#Ordinary-TLS', 'VLESS / TLS · vless://', 'V'),
    ('vless://' + AUTHORITY + '?type=grpc&security=none#Unknown', 'VLESS · vless://', 'V'),
    ('vless://' + AUTHORITY + '#REALITY&type=xhttp&security=tls', 'VLESS · vless://', 'V'),
    ('vless://' + AUTHORITY + '?security=future#Future-security', 'VLESS · vless://', 'V'),
    ('hysteria2://FAKE_AUTH@node.example.invalid:443?sni=sub.example.invalid&alpn=h3#Original-H2', 'Hysteria2 · hysteria2://', 'H2'),
    ('hy2://FAKE_AUTH@node.example.invalid:443#Original-Hy2', 'Hysteria2 · hy2://', 'H2'),
]


def fixture():
    manifest = {'endpoint': 'node.example.invalid', 'accounts': {
        'alpha': {'enable': True}, 'beta': {'enable': True}}}
    payloads = [case[0] for case in CASES]
    raw = ('\r\n'.join(payloads) + '\r\n').encode('utf-8')
    beta_raw = b'hysteria2://FAKE_BETA@node.example.invalid:443#Beta\n'
    files = {'alpha.txt': base64.b64encode(raw), 'alpha.yaml': b'# FAKE native YAML\nraw: +%2f%2F\r\n',
             'beta.txt': base64.b64encode(beta_raw), 'beta.yaml': b'# FAKE beta YAML\n'}
    urls = {device: {'mihomo': f'https://sub.example.invalid:8443/mihomo/FAKE_{device}',
                     'shadowrocket': f'https://sub.example.invalid:8443/sub/FAKE_{device}'}
            for device in manifest['accounts']}
    assets = {name: (ROOT / 'assets' / name).read_bytes()
              for name in ('dashboard.html', 'dashboard.js', 'dashboard.css')}
    model, html, artifacts = dashboard.render(manifest, files, urls, 'a' * 64, assets)
    # Old schema1 models carry only scheme, URI and label; no new metadata is needed.
    model['devices'][0]['links'][5]['protocol'] = 'reality'
    return {'model': model, 'expected': [{'tag': tag, 'icon': icon} for _, tag, icon in CASES],
            'maliciousIndex': 1, 'maliciousLabel': MALICIOUS_LABEL}, html, artifacts, raw


class DashboardLabels(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is required for executed dashboard UI semantics')
    def test_rendered_labels_copy_qr_downloads_and_device_scope(self):
        data, _, _, _ = fixture()
        result = subprocess.run(['node', str(ROOT / 'tests' / 'dashboard_labels.mjs')],
                                input=json.dumps(data), text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'passed')
        self.assertGreaterEqual(len(report['checks']), 8)

    def test_native_uri_fragment_and_qr_payloads_are_unchanged(self):
        original = dashboard.qr_svg
        with mock.patch.object(dashboard, 'qr_svg', wraps=original) as encoder:
            data, html, artifacts, raw = fixture()
        links = data['model']['devices'][0]['links']
        self.assertEqual([link['uri'] for link in links[:len(CASES)]], [case[0] for case in CASES])
        self.assertEqual([call.args[0] for call in encoder.call_args_list[:len(CASES)]],
                         [case[0].encode('utf-8') for case in CASES])
        self.assertEqual(links[1]['label'], MALICIOUS_LABEL)
        self.assertNotIn(b'</script><img', html)
        self.assertNotIn(b'<img src=x', html)
        for link in links:
            self.assertEqual(artifacts[link['qr']], original(link['uri'].encode('utf-8')))
        downloads = {item['label']: artifacts[item['path']] for item in data['model']['devices'][0]['downloads']}
        self.assertEqual(downloads['原生节点 URI'], raw)
        self.assertEqual(base64.b64decode(downloads['原生 base64 订阅'], validate=True), raw)


if __name__ == '__main__':
    unittest.main()
