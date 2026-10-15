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
            self.assertEqual(rules.load_vps_output(), {})
        # Golden hashes measured from the pre-integration generator, not from
        # a second invocation of the new renderer.
        self.assertEqual(hashlib.sha256(self.script([]).encode()).hexdigest(),
                         "9786678fe64bf8bdf51d09c96d1a89843c8985b6bda5beb6690aff8a1cfd3a44")
        self.assertEqual(hashlib.sha256(rules.render(self.sources, ["203.0.113.9/32"], []).encode()).hexdigest(),
                         "709dfa3e3eb8f400dc97c5d0df25cd534a6ccafe317d37b90ef948a0e980d63f")

    def test_private_output_loads_only_expected_pinned_nodes(self):
        self.publish()
        with mock.patch.object(Path, "home", return_value=self.home):
            self.assertEqual(rules.load_vps_output()["proxies"], self.nodes)
        self.nodes[1]["skip-cert-verify"] = True
        self.publish()
        with mock.patch.object(Path, "home", return_value=self.home), self.assertRaises(rules.SourceError):
            rules.load_vps_output()

    def test_malformed_private_output_and_public_mode_fail_explicitly(self):
        self.publish()
        self.output.chmod(0o644)
        with mock.patch.object(Path, "home", return_value=self.home), self.assertRaisesRegex(rules.SourceError, "0600"):
            rules.load_vps_output()
        self.output.chmod(0o600)
        self.output.write_text('{invalid')
        with mock.patch.object(Path, "home", return_value=self.home), self.assertRaises(rules.SourceError):
            rules.load_vps_output()

    def test_script_injects_select_choices_without_changing_subscription(self):
        result = self.evaluate(self.script(self.nodes), self.base())
        self.assertEqual(result["proxies"][0]["name"], "airport")
        self.assertEqual([n["name"] for n in result["proxies"][1:]], ["SELF-Reality", "SELF-Hy2"])
        group = next(g for g in result["proxy-groups"] if g["name"] == "🛰️ 自建 VPS")
        self.assertEqual(group, {"name": "🛰️ 自建 VPS", "type": "select", "proxies": ["SELF-Reality", "SELF-Hy2", "🚀 节点选择"]})
        self.assertEqual(result["proxy-groups"][0]["proxies"], ["airport", "SELF-Reality", "SELF-Hy2"])
        self.assertEqual(result["proxy-groups"][1]["name"], "unrelated")
        self.assertEqual(result["rules"][-1], "MATCH,DIRECT")
        self.assertEqual(result["rules"][0], "IP-CIDR,203.0.113.1/32,DIRECT,no-resolve")
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

    def test_missing_entry_and_null_entries_preserve_rules_and_tun_exclusions(self):
        self.sources["pre"] = [rules.parse_rule(Path("fixture"), 1, "DST-PORT,22,DIRECT", Path("fixture"), "pre")]
        for groups in ([], [None, {"name": "unrelated", "type": "select", "proxies": ["DIRECT"]}]):
            base = self.base()
            base["proxy-groups"] = groups
            base["proxies"].append(None)
            result = self.evaluate(self.script(self.nodes), base)
            self.assertIn("DST-PORT,22,DIRECT", result["rules"])
            self.assertIn("100.64.0.0/10", result["tun"]["route-exclude-address"])
            self.assertFalse(any("LOCAL-RULES-ERROR" in rule for rule in result["rules"]))
            group = next(g for g in result["proxy-groups"] if g["name"] == rules.VPS_GROUP)
            self.assertEqual(group["proxies"], ["SELF-Reality", "SELF-Hy2"])

    def test_model_domains_precede_subscription_and_endpoint_rules_cover_ipv6(self):
        base = self.base()
        base["rules"] = ["DOMAIN-SUFFIX,openai.com,unrelated", "MATCH,DIRECT"]
        result = self.evaluate(rules.render(self.sources, [], self.nodes, ["2001:db8::9"]), base, twice=True)
        prefix = result["rules"][:10]
        self.assertEqual(prefix[:2], ["IP-CIDR6,2001:db8::9/128,DIRECT,no-resolve", "IP-CIDR,203.0.113.1/32,DIRECT,no-resolve"])
        self.assertEqual(len(prefix), 10)
        for kind, names in rules.VPS_MODEL_DOMAINS.items():
            for name in names:
                self.assertIn(f"{kind},{name},{rules.VPS_GROUP}", prefix)
        self.assertFalse(any(',google.com,' in rule or ',googleapis.com,' in rule for rule in prefix))
        self.assertEqual(result["rules"].count("DOMAIN-SUFFIX,openai.com," + rules.VPS_GROUP), 1)
        self.assertIn("DOMAIN,cloudcode-pa.googleapis.com," + rules.VPS_GROUP, prefix)
        self.assertIn("DOMAIN,aistudio.google.com," + rules.VPS_GROUP, prefix)

    def test_hostname_output_requires_explicit_known_endpoint_ips(self):
        self.nodes = vps.nodes(self.ident, "relay.example.net", "company")
        self.publish()
        with mock.patch.object(Path, "home", return_value=self.home), self.assertRaises(rules.SourceError):
            rules.load_vps_output()
        vps.write_private(self.output, vps.dumps({"version": 1, "proxies": self.nodes, "server_ips": ["203.0.113.1", "2001:db8::9"]}))
        with mock.patch.object(Path, "home", return_value=self.home):
            self.assertEqual(len(rules.load_vps_output()["server_ips"]), 2)

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

    def test_native_core_validates_full_config_with_and_without_entry_group(self):
        binary = shutil.which("verge-mihomo") or shutil.which("mihomo")
        self.assertIsNotNone(binary)
        for present in (True, False):
            base = self.base()
            if not present:
                base["proxy-groups"] = [None]
                base["proxies"].append(None)
            else:
                # Remove stale reverse edge from an earlier generated profile.
                base["proxy-groups"][0]["proxies"].append(rules.VPS_GROUP)
            result = self.evaluate(self.script(self.nodes), base, twice=True)
            config = self.home / "config.json"
            config.write_text(json.dumps(result, ensure_ascii=False))
            checked = subprocess.run([binary, "-t", "-d", str(self.home), "-f", str(config)], capture_output=True, text=True)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)


if __name__ == "__main__":
    unittest.main()
