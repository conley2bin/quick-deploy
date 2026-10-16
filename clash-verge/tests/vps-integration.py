#!/usr/bin/env python3
"""Offline private nodes: manual select choices, no forced routing or live writes."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'clash-verge/lib'))
sys.path.insert(0, str(ROOT / 'vps/lib'))
import rules
import vps


class VPSIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ident = vps.new_identity()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='clash-vps-test-')
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.output = self.home / '.local/state/quick-deploy-private/vps/generated/clash-nodes.json'
        self.nodes = vps.nodes(self.ident, '203.0.113.1', 'company')
        self.sources = {'pre': [], 'post': []}

    def publish(self):
        vps.write_private(self.output, vps.dumps({'version': 1, 'proxies': self.nodes}))

    def script(self, nodes):
        return rules.render(self.sources, [], nodes)

    def evaluate(self, script, config, twice=False):
        result = subprocess.run(['node', '-e', script + '\nlet c = ' + json.dumps(config) + ';\nc=main(c);\n' +
                                 ('c=main(c);\n' if twice else '') + 'console.log(JSON.stringify(c));'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def base(self):
        return {'proxies': [
                    {'name': 'airport-a', 'type': 'ss', 'server': '203.0.113.8', 'port': 8443, 'cipher': 'aes-128-gcm', 'password': 'fixture'},
                    {'name': 'airport-b', 'type': 'http', 'server': '203.0.113.9', 'port': 8080}],
                'proxy-groups': [
                    {'name': 'manual', 'type': 'select', 'proxies': ['auto', 'airport-b', 'airport-a'], 'selected': 'airport-b'},
                    {'name': 'another-list', 'type': 'select', 'proxies': ['DIRECT', 'airport-a']},
                    {'name': 'auto', 'type': 'url-test', 'proxies': ['airport-a', 'airport-b'], 'url': 'https://example.com', 'interval': 300},
                    {'name': 'fallback', 'type': 'fallback', 'proxies': ['airport-a'], 'url': 'https://example.com', 'interval': 300},
                    {'name': 'balance', 'type': 'load-balance', 'proxies': ['airport-b'], 'url': 'https://example.com', 'interval': 300},
                    {'name': 'policy-only', 'type': 'select', 'proxies': ['DIRECT', 'REJECT', 'auto']}],
                'rules': ['DOMAIN-SUFFIX,openai.com,manual', 'MATCH,manual']}

    def test_absent_private_file_preserves_historical_bytes(self):
        with mock.patch.object(Path, 'home', return_value=self.home):
            self.assertEqual(rules.load_vps_output(), {})
        self.assertEqual(hashlib.sha256(self.script([]).encode()).hexdigest(),
                         '9786678fe64bf8bdf51d09c96d1a89843c8985b6bda5beb6690aff8a1cfd3a44')
        self.assertEqual(hashlib.sha256(rules.render(self.sources, ['203.0.113.9/32'], []).encode()).hexdigest(),
                         '709dfa3e3eb8f400dc97c5d0df25cd534a6ccafe317d37b90ef948a0e980d63f')

    def test_private_output_requires_valid_pinned_nodes_and_permissions(self):
        self.publish()
        with mock.patch.object(Path, 'home', return_value=self.home):
            self.assertEqual(rules.load_vps_output()['proxies'], self.nodes)
        self.output.chmod(0o644)
        with mock.patch.object(Path, 'home', return_value=self.home), self.assertRaises(rules.SourceError):
            rules.load_vps_output()
        self.output.chmod(0o600)
        self.nodes[1]['skip-cert-verify'] = True
        self.publish()
        with mock.patch.object(Path, 'home', return_value=self.home), self.assertRaises(rules.SourceError):
            rules.load_vps_output()

    def test_only_selects_offering_individual_nodes_get_self_leaves(self):
        original = self.base()
        result = self.evaluate(self.script(self.nodes), original, twice=True)
        self.assertEqual(len(result['proxy-groups']), len(original['proxy-groups']))
        for before, after in zip(original['proxy-groups'], result['proxy-groups']):
            if before['name'] in ('manual', 'another-list'):
                self.assertEqual(after['proxies'], before['proxies'] + ['SELF-Reality', 'SELF-Hy2'])
            else:
                self.assertEqual(after, before)
        self.assertEqual(result['proxy-groups'][0]['selected'], 'airport-b')
        self.assertEqual(result['proxy-groups'][0]['proxies'][0], 'auto')
        self.assertEqual(result['rules'][1:], original['rules'])
        self.assertFalse(any('自建 VPS' in g['name'] for g in result['proxy-groups']))
        self.assertNotIn('VPS_MODEL_DOMAINS', self.script(self.nodes))

    def test_nonselect_named_entry_is_not_modified(self):
        base = self.base()
        base['proxy-groups'][2]['name'] = '🚀 节点选择'
        base['proxy-groups'][0]['proxies'][0] = '🚀 节点选择'
        base['proxy-groups'][-1]['proxies'][-1] = '🚀 节点选择'
        result = self.evaluate(self.script(self.nodes), base)
        self.assertEqual(result['proxy-groups'][2], base['proxy-groups'][2])

    def test_idempotency_preserves_existing_self_selection_and_replaces_credentials(self):
        base = self.base()
        base['proxies'].append(dict(self.nodes[0], uuid='stale'))
        base['proxy-groups'][0]['proxies'].insert(0, 'SELF-Reality')
        base['proxy-groups'][0]['selected'] = 'SELF-Reality'
        result = self.evaluate(self.script(self.nodes), base, twice=True)
        self.assertEqual(len(result['proxies']), 4)
        self.assertEqual(result['proxies'][2]['uuid'], self.ident['users']['company']['uuid'])
        self.assertEqual(result['proxy-groups'][0]['proxies'][0], 'SELF-Reality')
        self.assertEqual(result['proxy-groups'][0]['selected'], 'SELF-Reality')
        self.assertEqual(result['proxy-groups'][0]['proxies'].count('SELF-Reality'), 1)

    def test_missing_groups_and_nulls_preserve_local_rules_and_exclusions(self):
        self.sources['pre'] = [rules.parse_rule(Path('fixture'), 1, 'DST-PORT,22,DIRECT', Path('fixture'), 'pre')]
        base = {'proxies': [None], 'proxy-groups': [None], 'rules': ['MATCH,DIRECT']}
        result = self.evaluate(self.script(self.nodes), base)
        self.assertEqual(result['proxy-groups'], [])
        self.assertIn('DST-PORT,22,DIRECT', result['rules'])
        self.assertIn('100.64.0.0/10', result['tun']['route-exclude-address'])

    def test_direct_endpoint_rules_only_no_model_domain_routing(self):
        base = self.base()
        result = self.evaluate(rules.render(self.sources, [], self.nodes, ['2001:db8::9']), base, twice=True)
        self.assertEqual(result['rules'][:2], ['IP-CIDR6,2001:db8::9/128,DIRECT,no-resolve', 'IP-CIDR,203.0.113.1/32,DIRECT,no-resolve'])
        self.assertEqual(result['rules'][2:], base['rules'])

    def test_hostname_requires_known_endpoint_ips(self):
        self.nodes = vps.nodes(self.ident, 'relay.example.net', 'company')
        self.publish()
        with mock.patch.object(Path, 'home', return_value=self.home), self.assertRaises(rules.SourceError):
            rules.load_vps_output()
        vps.write_private(self.output, vps.dumps({'version': 1, 'proxies': self.nodes, 'server_ips': ['203.0.113.1', '2001:db8::9']}))
        with mock.patch.object(Path, 'home', return_value=self.home):
            self.assertEqual(len(rules.load_vps_output()['server_ips']), 2)

    def test_cli_reads_private_output_without_writing_verge(self):
        self.publish()
        sources = self.home / 'rules'
        sources.mkdir()
        for filename in ('direct.yaml', 'proxy.yaml'):
            (sources / filename).write_text('version: 1\npre: []\npost: []\n')
        result = subprocess.run([sys.executable, str(ROOT / 'clash-verge/lib/rules.py'), '--render', '--direct', str(sources / 'direct.yaml'),
                                 '--proxy', str(sources / 'proxy.yaml')], env=dict(os.environ, HOME=str(self.home), TUN_FIX_WG_ENDPOINTS='off'), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('SELF-Reality', result.stdout)
        self.assertFalse((self.home / '.local/share').exists())

    def test_native_mihomo_accepts_realistic_subscription_and_missing_groups(self):
        binary = shutil.which('verge-mihomo') or shutil.which('mihomo')
        self.assertIsNotNone(binary)
        for base in (self.base(), {'proxies': [None], 'proxy-groups': [None], 'rules': ['MATCH,DIRECT']}):
            config = self.home / 'config.json'
            config.write_text(json.dumps(self.evaluate(self.script(self.nodes), base, twice=True), ensure_ascii=False))
            checked = subprocess.run([binary, '-t', '-d', str(self.home), '-f', str(config)], capture_output=True, text=True)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)


if __name__ == '__main__':
    unittest.main()
