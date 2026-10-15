#!/usr/bin/env python3
"""Private VPS node integration. Never read/write the real Verge profile."""
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
sys.path.insert(0, str(ROOT / "clash-verge/lib"))
sys.path.insert(0, str(ROOT / "vps/lib"))
import rules
import vps


class VPSIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ident = vps.new_identity()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="clash-vps-test-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.output = self.home / ".local/state/quick-deploy-private/vps/generated/clash-nodes.json"
        self.nodes = vps.nodes(self.ident, "203.0.113.1", "company")
        self.sources = {"pre": [], "post": []}

    def publish(self):
        vps.write_private(self.output, vps.dumps({"version": 1, "proxies": self.nodes}))

    def script(self, nodes):
        return rules.render(self.sources, [], nodes)

    def evaluate(self, script, config, twice=False):
        result = subprocess.run(["node", "-e", script + '\nlet c = ' + json.dumps(config) + ';\nc=main(c);\n' +
                                 ('c=main(c);\n' if twice else '') + 'console.log(JSON.stringify(c));'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def base(self):
        return {"proxies": [{"name": "airport", "type": "direct"}],
                "proxy-groups": [{"name": "🚀 节点选择", "type": "select", "proxies": ["airport"]},
                                 {"name": "unrelated", "type": "select", "proxies": ["DIRECT"]}],
                "rules": ["MATCH,DIRECT"]}

    def test_absent_private_file_preserves_historical_bytes(self):
        with mock.patch.object(Path, "home", return_value=self.home):
            self.assertEqual(rules.load_vps_nodes(), [])
        # Golden hashes measured from the pre-integration generator, not from
        # a second invocation of the new renderer.
        self.assertEqual(hashlib.sha256(self.script([]).encode()).hexdigest(),
                         "9786678fe64bf8bdf51d09c96d1a89843c8985b6bda5beb6690aff8a1cfd3a44")
        self.assertEqual(hashlib.sha256(rules.render(self.sources, ["203.0.113.9/32"], []).encode()).hexdigest(),
                         "709dfa3e3eb8f400dc97c5d0df25cd534a6ccafe317d37b90ef948a0e980d63f")

    def test_private_output_loads_only_expected_pinned_nodes(self):
        self.publish()
        with mock.patch.object(Path, "home", return_value=self.home):
            self.assertEqual(rules.load_vps_nodes(), self.nodes)
        self.nodes[1]["skip-cert-verify"] = True
        self.publish()
        with mock.patch.object(Path, "home", return_value=self.home), self.assertRaises(rules.SourceError):
            rules.load_vps_nodes()

    def test_malformed_private_output_and_public_mode_fail_explicitly(self):
        self.publish()
        self.output.chmod(0o644)
        with mock.patch.object(Path, "home", return_value=self.home), self.assertRaisesRegex(rules.SourceError, "0600"):
            rules.load_vps_nodes()
        self.output.chmod(0o600)
        self.output.write_text('{invalid')
        with mock.patch.object(Path, "home", return_value=self.home), self.assertRaises(rules.SourceError):
            rules.load_vps_nodes()

    def test_script_injects_select_choices_without_changing_subscription(self):
        result = self.evaluate(self.script(self.nodes), self.base())
        self.assertEqual(result["proxies"][0]["name"], "airport")
        self.assertEqual([n["name"] for n in result["proxies"][1:]], ["SELF-Reality", "SELF-Hy2"])
        group = next(g for g in result["proxy-groups"] if g["name"] == "🛰️ 自建 VPS")
        self.assertEqual(group, {"name": "🛰️ 自建 VPS", "type": "select", "proxies": ["SELF-Reality", "SELF-Hy2"]})
        self.assertEqual(result["proxy-groups"][0]["proxies"], ["airport", "🛰️ 自建 VPS", "SELF-Reality", "SELF-Hy2"])
        self.assertEqual(result["proxy-groups"][1]["name"], "unrelated")
        self.assertEqual(result["rules"], ["MATCH,DIRECT"])
        self.assertNotIn("proxy-providers", result)

    def test_script_is_idempotent_and_replaces_old_credentials(self):
        base = self.base()
        base["proxies"].append(dict(self.nodes[0], uuid="stale"))
        base["proxy-groups"].append({"name": "🛰️ 自建 VPS", "type": "select", "proxies": ["stale"]})
        result = self.evaluate(self.script(self.nodes), base, twice=True)
        self.assertEqual(len(result["proxies"]), 3)
        self.assertEqual(result["proxies"][1]["uuid"], self.ident["users"]["company"]["uuid"])
        self.assertEqual(len([g for g in result["proxy-groups"] if g["name"] == "🛰️ 自建 VPS"]), 1)
        choices = result["proxy-groups"][0]["proxies"]
        self.assertEqual(len(choices), len(set(choices)))

    def test_missing_or_wrong_entry_group_uses_visible_core_guard(self):
        for groups in ([], [{"name": "🚀 节点选择", "type": "url-test", "proxies": ["DIRECT"]}]):
            base = self.base()
            base["proxy-groups"] = groups
            result = self.evaluate(self.script(self.nodes), base)
            self.assertIn("LOCAL-RULES-ERROR-MISSING-VPS-SELECT", result["rules"][0])

    def test_cli_reads_optional_file_without_touching_verge_runtime(self):
        self.publish()
        sources = self.home / "rules"
        sources.mkdir()
        for filename in ("direct.yaml", "proxy.yaml"):
            (sources / filename).write_text("version: 1\npre: []\npost: []\n")
        sentinel = self.home / ".local/share/io.github.clash-verge-rev.clash-verge-rev/clash-verge.yaml"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_text("runtime sentinel\n")
        result = subprocess.run([sys.executable, str(ROOT / "clash-verge/lib/rules.py"), "--render", "--direct", str(sources / "direct.yaml"),
                                 "--proxy", str(sources / "proxy.yaml")], env=dict(os.environ, HOME=str(self.home), TUN_FIX_WG_ENDPOINTS="off"),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SELF-Reality", result.stdout)
        self.assertEqual(sentinel.read_text(), "runtime sentinel\n")
        self.assertEqual(list(sentinel.parent.iterdir()), [sentinel])

    def test_native_core_validates_full_generated_config_and_missing_group_guard(self):
        binary = shutil.which("verge-mihomo") or shutil.which("mihomo")
        self.assertIsNotNone(binary)
        result = self.evaluate(self.script(self.nodes), self.base())
        # airport 'direct' fixture is a supported mihomo proxy type.
        config = self.home / "config.json"
        config.write_text(json.dumps(result, ensure_ascii=False))
        checked = subprocess.run([binary, "-t", "-d", str(self.home), "-f", str(config)], capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        base = self.base()
        base["proxy-groups"] = []
        config.write_text(json.dumps(self.evaluate(self.script(self.nodes), base), ensure_ascii=False))
        checked = subprocess.run([binary, "-t", "-d", str(self.home), "-f", str(config)], capture_output=True, text=True)
        self.assertNotEqual(checked.returncode, 0)
        self.assertIn("LOCAL-RULES-ERROR", checked.stdout + checked.stderr)


if __name__ == "__main__":
    unittest.main()
