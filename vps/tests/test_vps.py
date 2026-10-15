#!/usr/bin/env python3
"""Offline tests: only temporary state and child processes on loopback for -t."""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit
import io

MODULE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE / "lib"))
import vps


class VPS(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ident = vps.new_identity()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="qd-vps-test-")
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"
        vps.private_dir(self.state)
        self.inv = {"version": 1, "current": None, "previous": [], "company_ip": "100.70.0.1", "home_ip": "100.70.0.2"}

    def test_init_preserves_identity_bytes_inode_and_mtime(self):
        args = argparse.Namespace(hostname=None, reality_target="www.microsoft.com")
        vps.init(self.state, args)
        path = self.state / "identity.json"
        before = path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns
        with mock.patch.object(vps, "new_identity", side_effect=AssertionError("rotated")):
            vps.init(self.state, args)
        self.assertEqual(before, (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
        self.assertEqual(vps.identity(self.state)["users"].keys(), set(vps.DEVICES))

    def test_corrupt_identity_is_preserved_without_regeneration(self):
        path = self.state / "identity.json"
        vps.write_private(path, "{broken")
        with self.assertRaises(vps.Error):
            vps.init(self.state, argparse.Namespace())
        self.assertEqual(path.read_text(), "{broken")

    def test_identity_missing_fields_not_regenerated(self):
        vps.write_private(self.state / "identity.json", '{"version":1}')
        with self.assertRaisesRegex(vps.Error, "No identity was regenerated"):
            vps.identity(self.state)

    def test_private_permissions_and_symlinks_rejected(self):
        path = self.state / "inventory.json"
        vps.write_private(path, "{}")
        path.chmod(0o644)
        with self.assertRaisesRegex(vps.Error, "0600"):
            vps.load(path)
        link = self.state / "link"
        link.symlink_to(path)
        with self.assertRaises(vps.Error):
            vps.write_private(link, "secret")
        with self.assertRaises(vps.Error):
            vps.load(link)

    def test_atomic_private_write_noop_and_failure_preserve_original(self):
        path = self.state / "config"
        vps.write_private(path, "before")
        inode = path.stat().st_ino
        vps.write_private(path, "before")
        self.assertEqual(inode, path.stat().st_ino)
        with mock.patch.object(vps.os, "replace", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                vps.write_private(path, "after")
        self.assertEqual(path.read_text(), "before")
        self.assertFalse(list(self.state.glob(".candidate-*")))

    def test_inventory_host_history_is_unique_and_idempotent(self):
        a = {"host": "203.0.113.1", "user": "root", "port": 22, "provider": "a"}
        b = dict(a, host="203.0.113.2")
        first = vps.updated_inventory(self.inv, a)
        second = vps.updated_inventory(first, b)
        self.assertEqual(vps.role_slot(second)['previous'], [dict(a, role='proxy')])
        self.assertEqual(vps.updated_inventory(second, b), second)
        self.assertIsNone(self.inv["current"])

    def test_identity_device_credentials_independent(self):
        self.assertEqual(len({u["uuid"] for u in self.ident["users"].values()}), 3)
        self.assertEqual(len({u["hy2_password"] for u in self.ident["users"].values()}), 3)
        self.assertEqual(len(vps.fingerprint(self.ident)), 64)

    def test_server_json_and_ordered_phone_only_allow(self):
        config = json.loads(json.dumps(vps.server_config(self.ident, "100.70.0.1", "203.0.113.1")))
        resolve, allow, deny = config["route"]["rules"]
        self.assertEqual(resolve["action"], "resolve")
        self.assertEqual(allow, {"auth_user": ["phone"], "ip_cidr": ["100.70.0.1/32"], "network": "tcp", "port": [8743], "action": "route", "outbound": "direct"})
        self.assertEqual(deny["action"], "reject")
        for network in ("100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "203.0.113.1/32", "::/3"):
            self.assertIn(network, deny["ip_cidr"])
        self.assertTrue(deny["ip_is_private"])
        self.assertEqual([u["name"] for u in config["inbounds"][0]["users"]], list(vps.DEVICES))
        self.assertEqual(config["inbounds"][0]["tls"]["reality"]["short_id"], [self.ident["reality"]["short_id"]])

    def test_firewall_limits_local_and_tailnet_access(self):
        fw = vps.firewall("100.70.0.1", 2222, 998)
        self.assertIn('policy drop;', fw)
        self.assertIn('ip6 saddr fe80::/10 udp sport 547 udp dport 546 accept', fw)
        self.assertIn('meta skuid 998 jump proxy_output', fw)
        self.assertIn('fib daddr type local reject', fw)
        self.assertIn('oifname "tailscale0" ip daddr 100.70.0.1 tcp dport 8743 accept', fw)
        self.assertIn('iifname "tailscale0" drop', fw)
        self.assertIn('22, 443, 2222', fw)
        self.assertNotIn('flush ruleset', fw)
        self.assertLess(fw.index('fib daddr'), fw.index('tcp dport 8743'))

    def test_uris_encode_phone_identity_and_require_official_hy2_pin(self):
        links = vps.uris(self.ident, "2001:db8::1")
        for link in links:
            parsed = urlsplit(link)
            self.assertEqual(parsed.hostname, "2001:db8::1")
            self.assertTrue(parsed.fragment.startswith("SELF-"))
        reality, hy2 = map(urlsplit, links)
        self.assertEqual(reality.username, self.ident["users"]["phone"]["uuid"])
        self.assertEqual(parse_qs(reality.query)["flow"], ["xtls-rprx-vision"])
        self.assertEqual(parse_qs(reality.query)["pbk"], [self.ident["reality"]["public_key"]])
        self.assertEqual(parse_qs(hy2.query)["pinSHA256"], [vps.fingerprint(self.ident).lower()])
        self.assertNotIn("insecure", parse_qs(hy2.query))

    def test_policy_scopes_relay_and_service(self):
        result = vps.policy("100.70.0.1", "100.70.0.2")
        self.assertEqual(result["hosts"], {"company": "100.70.0.1", "home": "100.70.0.2"})
        self.assertEqual(result["grants"][0]["dst"], ["autogroup:self"])
        self.assertEqual(result["grants"][1]["ip"], ["tcp:8743"])
        self.assertEqual(result["grants"][2]["src"], ["company", "home"])
        self.assertEqual(result["grants"][2]["app"], {"tailscale.com/cap/relay": []})
        self.assertNotIn("acls", result)

    def test_peer_discovery_checks_both_self_and_peers(self):
        status = {"Self": {"HostName": "office-fixture", "TailscaleIPs": ["100.70.0.1", "fd7a::1"]},
                  "Peer": {"a": {"HostName": "HOME-fixture", "TailscaleIPs": ["100.70.0.2"]}}}
        with mock.patch.object(vps, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(status))):
            self.assertEqual(vps.peers({"company_name": "office-fixture", "home_name": "home-fixture"}), ("100.70.0.1", "100.70.0.2"))

    def test_deploy_requires_device_and_init_has_no_machine_name_defaults(self):
        with mock.patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
            vps.main(["deploy", "--host", "203.0.113.1", "--dry-run"])
        args = vps.parser().parse_args(["init"])
        vps.init(self.state, args)
        inv = vps.load(self.state / "inventory.json")
        self.assertIsNone(inv["company_name"])
        self.assertIsNone(inv["home_name"])
        with mock.patch.object(vps, "run", side_effect=AssertionError("must refuse before discovery")), self.assertRaisesRegex(vps.Error, "private inventory"):
            vps.peers(inv)

    def test_explicit_peer_ips_never_contact_local_daemon(self):
        with mock.patch.object(vps, "run", side_effect=AssertionError("daemon contact")):
            self.assertEqual(vps.peers(self.inv), ("100.70.0.1", "100.70.0.2"))
        for address in ("192.168.0.1", "127.0.0.1", "2001:db8::1"):
            with self.assertRaises((vps.Error, ValueError)):
                vps.tail_ip(address)
        with self.assertRaises(vps.Error):
            vps.peers({}, "100.70.0.1", "100.70.0.1")

    def test_dryrun_requires_no_identity_and_never_calls_ssh_or_tailscale(self):
        with mock.patch.object(vps, "run", side_effect=AssertionError("network contact")):
            rc = vps.main(["--state-dir", str(self.state / "missing"), "deploy", "--host", "203.0.113.1", "--device", "home", "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertFalse((self.state / "missing").exists())

    def test_dryrun_cli_has_no_side_effects(self):
        child = subprocess.run(["bash", str(MODULE / "setup.sh"), "--state-dir", str(self.state / "missing"),
                                "deploy", "--host", "203.0.113.1", "--device", "home", "--dry-run"], capture_output=True, text=True)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertIn("NO CONTACT / NO WRITES", child.stdout)
        self.assertFalse((self.state / "missing").exists())

    def test_invalid_management_inputs_rejected_before_contact(self):
        for host in ("-oProxyCommand=bad", "localhost", "1.2.3.4;id"):
            with self.assertRaises(ValueError):
                vps.host_record(argparse.Namespace(host=host, user="root", port=22, provider=""))
        for user in ("-bad", "root;id"):
            with self.assertRaises(vps.Error):
                vps.host_record(argparse.Namespace(host="203.0.113.1", user=user, port=22, provider=""))

    def test_bundle_has_only_expected_members_and_private_modes(self):
        record = {"host": "203.0.113.1", "user": "root", "port": 22}
        data = vps.bundle(self.ident, "100.70.0.1", record, ["public fixture"], "secret-auth-fixture")
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            self.assertEqual(set(archive.getnames()), {"remote.sh", "config.json", "params.json", "authorized_keys", "firewall.template", "auth-key"})
            self.assertTrue(all(m.mode == 0o600 for m in archive.getmembers()))
            self.assertEqual(archive.extractfile("auth-key").read(), b"secret-auth-fixture")
            self.assertEqual(json.load(archive.extractfile("params.json"))["sing_box_version"], vps.SING_BOX_VERSION)
            self.assertNotIn(b"secret-auth-fixture", archive.extractfile("remote.sh").read())

    def test_staged_bundles_keep_proxy_bytes_identical_and_skip_auth(self):
        record = {"host": "203.0.113.1", "user": "root", "port": 22}
        configs = []
        for without in (True, False):
            data = vps.bundle(self.ident, "100.70.0.1", record, ["fixture"], "auth-fixture", without)
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                self.assertEqual('auth-key' in archive.getnames(), not without)
                self.assertEqual(json.load(archive.extractfile('params.json'))['without_tailscale'], without)
                configs.append(archive.extractfile('config.json').read())
        self.assertEqual(configs[0], configs[1])

    def test_without_then_with_deploy_preserves_identity_and_client_outputs(self):
        vps.write_private(self.state / 'identity.json', vps.dumps(self.ident))
        vps.write_private(self.state / 'inventory.json', vps.dumps(self.inv))
        original_identity = (self.state / 'identity.json').read_bytes()
        original_inode = (self.state / 'identity.json').stat().st_ino
        uploads = []
        def upload(argv, **kwargs):
            uploads.append(kwargs['input'])
        with mock.patch.object(vps, 'public_keys', return_value=['one', 'two']), mock.patch.object(vps, 'confirm'), \
                mock.patch.object(vps, 'run', side_effect=upload), mock.patch.object(vps, 'remote', return_value=subprocess.CompletedProcess([], 0, b'checkpoint')), \
                mock.patch.object(vps.shutil, 'which', return_value=None), mock.patch.dict(vps.os.environ, TS_AUTHKEY='auth-fixture'), \
                mock.patch.object(vps, 'new_identity', side_effect=AssertionError('rotated')):
            first = vps.parser().parse_args(['deploy', '--host', '203.0.113.1', '--device', 'home', '--without-tailscale', '--provider', 'provider-fixture', '--auth-key-file', str(self.state / 'absent-auth')])
            vps.deploy(self.state, first)
            before = (self.state / 'generated/clash-nodes.json').read_bytes()
            self.assertTrue(vps.role_slot(vps.load(self.state / 'inventory.json'))['without_tailscale'])
            second = vps.parser().parse_args(['deploy', '--host', '203.0.113.1', '--device', 'home'])
            vps.deploy(self.state, second)
        self.assertEqual(original_identity, (self.state / 'identity.json').read_bytes())
        self.assertEqual(original_inode, (self.state / 'identity.json').stat().st_ino)
        self.assertEqual(before, (self.state / 'generated/clash-nodes.json').read_bytes())
        inv = vps.load(self.state / 'inventory.json')
        slot = vps.role_slot(inv)
        self.assertFalse(slot['without_tailscale'])
        self.assertEqual(slot['previous'], [])
        self.assertEqual(slot['current']['provider'], 'provider-fixture')
        for without, data in zip((True, False), uploads):
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                self.assertEqual(json.load(archive.extractfile('params.json'))['without_tailscale'], without)
                self.assertEqual('auth-key' in archive.getnames(), not without)

    def test_without_tailscale_dryrun_is_explicit_and_offline(self):
        with mock.patch('sys.stdout', new_callable=io.StringIO) as output, mock.patch.object(vps, 'run', side_effect=AssertionError('contact')):
            result = vps.main(['--state-dir', str(self.state / 'missing'), 'deploy', '--host', '203.0.113.1', '--device', 'home', '--without-tailscale', '--dry-run'])
        self.assertEqual(result, 0)
        self.assertIn('SKIP Tailscale', output.getvalue())
        self.assertNotIn('tailscale up', output.getvalue())
        self.assertFalse((self.state / 'missing').exists())

    def test_pin_and_upgrade_require_an_additional_explicit_confirmation(self):
        self.assertEqual(vps.SING_BOX_VERSION, '1.14.3')
        vps.write_private(self.state / 'identity.json', vps.dumps(self.ident))
        vps.write_private(self.state / 'inventory.json', vps.dumps(self.inv))
        args = vps.parser().parse_args(['deploy', '--host', '203.0.113.1', '--device', 'home', '--upgrade'])
        with mock.patch.object(vps, 'public_keys', return_value=['one', 'two']), mock.patch.object(vps, 'confirm', side_effect=[None, vps.Error('declined')]) as confirm, mock.patch.object(vps, 'run') as run:
            with self.assertRaisesRegex(vps.Error, 'declined'):
                vps.deploy(self.state, args)
        self.assertEqual(confirm.call_count, 2)
        run.assert_not_called()

    def test_fresh_ssh_forces_publickey_and_disables_multiplex_proxy(self):
        argv = vps.ssh_args({"host": "203.0.113.1", "user": "root", "port": 2222}, fresh=True)
        for option in ("ControlPath=none", "ControlMaster=no", "ProxyCommand=none", "ProxyJump=none", "BatchMode=yes", "PreferredAuthentications=publickey"):
            self.assertIn(option, argv)
        self.assertNotIn("StrictHostKeyChecking=no", argv)

    def test_render_outputs_only_private_paths_and_device_credentials(self):
        with mock.patch.object(vps.shutil, "which", return_value=None):
            vps.render_outputs(self.state, self.ident, self.inv, "home", "203.0.113.1", "100.70.0.1", "100.70.0.2")
        output = self.state / "generated"
        for file in output.iterdir():
            self.assertEqual(file.stat().st_mode & 0o777, 0o600, file.name)
        document = json.loads((output / "clash-nodes.json").read_text())
        self.assertEqual(document["proxies"][0]["uuid"], self.ident["users"]["home"]["uuid"])
        self.assertEqual(document["proxies"][1]["fingerprint"], vps.fingerprint(self.ident))
        text = (output / "手机和策略说明.txt").read_text()
        self.assertIn("IP-CIDR,100.70.0.1/32,SELF,no-resolve", text)
        self.assertIn("insecure=1作为兼容fallback", text)
        self.assertIn("将pin故意改错，确认连接失败", text)
        self.assertIn("pinSHA256=" + vps.fingerprint(self.ident).lower(), text)

    def test_render_hostname_carries_known_ipv4_ipv6_without_dns(self):
        inv = dict(self.inv, current={"host": "203.0.113.1"}, vps_ips=["2001:db8::9"])
        with mock.patch.object(vps.shutil, "which", return_value=None), mock.patch.object(vps.socket, "getaddrinfo", side_effect=AssertionError("DNS")):
            vps.render_outputs(self.state, self.ident, inv, "home", "relay.example.net", "100.70.0.1", "100.70.0.2")
        output = vps.load(self.state / "generated/clash-nodes.json")
        self.assertEqual(output["server_ips"], ["2001:db8::9", "203.0.113.1"])
        self.assertEqual(output["proxies"][0]["server"], "relay.example.net")

    def test_native_mihomo_accepts_both_nodes_offline(self):
        binary = shutil.which("verge-mihomo") or shutil.which("mihomo")
        self.assertIsNotNone(binary, "Repository validation requires installed mihomo")
        for node in vps.nodes(self.ident, "203.0.113.1", "company"):
            config = self.state / (node["name"] + ".json")
            vps.write_private(config, vps.dumps(vps.client_config(node, 17890)))
            result = subprocess.run([binary, "-t", "-d", str(self.state), "-f", str(config)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_backup_uses_existing_alias_preserves_modes_without_delete(self):
        with mock.patch.object(vps, "run") as run, mock.patch.object(vps.socket, "gethostname", return_value="source-fixture"):
            vps.backup(self.state, {"backup_alias": "other-pc"}, None)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][0:2], ["ssh", "other-pc"])
        self.assertIn("--chmod=D700,F600", commands[1])
        self.assertNotIn("--delete", commands[1])
        self.assertEqual(commands[1][-1], "other-pc:.local/state/quick-deploy-private/vps-backups/source-fixture/proxy/")

    def test_backup_does_not_replace_peer_inventory_or_generated_identity(self):
        peer = self.state.parent / "peer"
        live = peer / ".local/state/quick-deploy-private/vps"
        (live / "generated").mkdir(parents=True)
        (live / "inventory.json").write_text('home inventory')
        (live / "generated/clash-nodes.json").write_text('home nodes')
        (self.state / "inventory.json").write_text('company inventory')
        def execute(argv):
            if argv[0] == "rsync":
                target = peer / argv[-1].split(":", 1)[1]
                shutil.copytree(Path(argv[-2]), target, dirs_exist_ok=True)
        with mock.patch.object(vps, "run", side_effect=execute), mock.patch.object(vps.socket, "gethostname", return_value="source-fixture"):
            vps.backup(self.state, {"backup_alias": "other-pc"}, None)
        self.assertEqual((live / "inventory.json").read_text(), 'home inventory')
        self.assertEqual((live / "generated/clash-nodes.json").read_text(), 'home nodes')
        self.assertEqual(json.loads((peer / '.local/state/quick-deploy-private/vps-backups/source-fixture/proxy/inventory.json').read_text())['version'], 2)

    def test_server_transaction_contract_order_and_no_global_flush(self):
        source = (MODULE / "lib/remote.sh").read_text()
        apply = source.split('apply)\n', 1)[1].split('harden)\n', 1)[0]
        self.assertLess(apply.index('arm_transaction'), apply.index('mv -f "$CONF.candidate"'))
        self.assertLess(apply.index('arm_transaction'), apply.index('nft -f'))
        self.assertLess(source.index('sing-box check -c "$ROOT/incoming/config.json"'), source.index('apply)\n'))
        self.assertNotIn('flush ruleset', source)
        self.assertNotIn('/etc/nftables.conf', source)
        self.assertIn('trap', source.split('arm_transaction() {', 1)[1].split('transaction_health()', 1)[0])
        self.assertIn('User=qd-vps-proxy', apply)

    def test_deploy_failure_requests_rollback_without_inventory_update(self):
        vps.write_private(self.state / "identity.json", vps.dumps(self.ident))
        vps.write_private(self.state / "inventory.json", vps.dumps(self.inv))
        args = vps.parser().parse_args(["deploy", "--host", "203.0.113.1", "--device", "home"])
        original = (self.state / "inventory.json").read_bytes()
        def remote(record, action, **kwargs):
            if action == "export-backup":
                return subprocess.CompletedProcess([], 0, b"backup fixture")
            if action == "health":
                raise vps.Error("injected health failure")
        with mock.patch.object(vps, "public_keys", return_value=["one", "two"]), mock.patch.object(vps, "confirm"), \
                mock.patch.object(vps, "run"), mock.patch.object(vps, "remote", side_effect=remote) as rem:
            with self.assertRaisesRegex(vps.Error, "health failure"):
                vps.deploy(self.state, args)
        self.assertIn("abort", [call.args[1] for call in rem.call_args_list])
        self.assertEqual(original, (self.state / "inventory.json").read_bytes())

    def test_check_clients_accepts_unauthenticated_provider_statuses_and_cleans_up(self):
        proc = mock.Mock()
        proc.poll.return_value = None
        results = [subprocess.CompletedProcess([], 0, code) for code in ("401", "403", "400") * 2]
        inv = dict(self.inv, current={"host": "203.0.113.1"})
        with mock.patch.object(vps.subprocess, "Popen", return_value=proc), \
                mock.patch.object(vps.socket, "create_connection"), \
                mock.patch.object(vps.subprocess, "run", side_effect=results) as curl, \
                mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            vps.check_clients(self.ident, inv, "company", "/fixture/mihomo")
        self.assertEqual(output.getvalue().count("PASS"), 6)
        self.assertEqual(proc.terminate.call_count, 2)
        for call in curl.call_args_list:
            argv = call.args[0]
            self.assertIn("--proxy", argv)
            self.assertNotIn("-k", argv)
            self.assertNotIn("--location", argv)
            self.assertFalse(any("Authorization" in item for item in argv))

    def test_check_clients_transport_failure_is_not_a_status_pass(self):
        proc = mock.Mock()
        proc.poll.return_value = None
        results = [subprocess.CompletedProcess([], 7, "000")] + [subprocess.CompletedProcess([], 0, "401")] * 5
        inv = dict(self.inv, current={"host": "203.0.113.1"})
        with mock.patch.object(vps.subprocess, "Popen", return_value=proc), \
                mock.patch.object(vps.socket, "create_connection"), \
                mock.patch.object(vps.subprocess, "run", side_effect=results), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            with self.assertRaisesRegex(vps.Error, "checks failed"):
                vps.check_clients(self.ident, inv, "company", "/fixture/mihomo")
        self.assertEqual(output.getvalue().count("FAIL"), 1)
        self.assertEqual(proc.terminate.call_count, 2)

    def test_deploy_success_hardens_only_after_fresh_login_then_saves_inventory(self):
        vps.write_private(self.state / "identity.json", vps.dumps(self.ident))
        vps.write_private(self.state / "inventory.json", vps.dumps(self.inv))
        args = vps.parser().parse_args(["deploy", "--host", "203.0.113.1", "--device", "home"])
        def remote(record, action, **kwargs):
            return subprocess.CompletedProcess([], 0, b"checkpoint")
        with mock.patch.object(vps, "public_keys", return_value=["one", "two"]), mock.patch.object(vps, "confirm"), \
                mock.patch.object(vps, "run"), mock.patch.object(vps, "render_outputs"), \
                mock.patch.object(vps, "remote", side_effect=remote) as rem:
            vps.deploy(self.state, args)
        actions = [call.args[1] for call in rem.call_args_list]
        self.assertEqual(actions, ["export-backup", "apply", "health", "harden", "health", "commit"])
        self.assertTrue(rem.call_args_list[2].kwargs["fresh"])
        self.assertEqual(vps.role_slot(vps.load(self.state / 'inventory.json'))['device'], 'home')

    def test_rollback_requires_fresh_login_and_second_confirmation(self):
        vps.write_private(self.state / "identity.json", vps.dumps(self.ident))
        inv = dict(self.inv, current={"host": "203.0.113.1", "user": "root", "port": 22})
        vps.write_private(self.state / "inventory.json", vps.dumps(inv))
        with mock.patch.object(vps, "remote") as remote, mock.patch.object(vps, "confirm") as confirm:
            result = vps.main(["--state-dir", str(self.state), "rollback"])
        self.assertEqual(result, 0)
        self.assertEqual([c.args[1] for c in remote.call_args_list], ["rollback", "transaction-health", "commit"])
        self.assertTrue(all(c.kwargs["fresh"] for c in remote.call_args_list))
        self.assertEqual(confirm.call_count, 2)

    def test_recovery_tries_fresh_then_controlmaster_connection(self):
        with mock.patch.object(vps, "remote", side_effect=[vps.Error("new login blocked"), None]) as remote:
            vps.recover_abort({"host": "203.0.113.1"})
        self.assertEqual([c.kwargs["fresh"] for c in remote.call_args_list], [True, False])

    def test_abort_explicit_host_works_before_identity_or_inventory(self):
        with mock.patch.object(vps, "remote") as remote:
            result = vps.main(["--state-dir", str(self.state), "abort", "--host", "203.0.113.1"])
        self.assertEqual(result, 0)
        self.assertEqual(remote.call_args.args[1], 'abort')
        self.assertFalse((self.state / "identity.json").exists())

    def test_ping_targets_numeric_ip_read_from_current_replacement_host(self):
        vps.write_private(self.state / "identity.json", vps.dumps(self.ident))
        inv = dict(self.inv, current={"host": "203.0.113.2", "user": "root", "port": 22}, device="home")
        vps.write_private(self.state / "inventory.json", vps.dumps(inv))
        with mock.patch.object(vps, "remote", return_value=subprocess.CompletedProcess([], 0, "100.70.0.99\n")) as remote, \
                mock.patch.object(vps, "check_clients"), mock.patch.object(vps, "run") as run:
            result = vps.main(["--state-dir", str(self.state), "check", "--ping"])
        self.assertEqual(result, 0)
        self.assertEqual(remote.call_args.args[0]["host"], "203.0.113.2")
        self.assertEqual(remote.call_args.args[1], "tailnet-ip")
        run.assert_called_once_with(["tailscale", "ping", "100.70.0.99"])

    def test_check_and_ping_report_skipped_overlay_without_local_tailscale_call(self):
        vps.write_private(self.state / 'identity.json', vps.dumps(self.ident))
        inv = dict(self.inv, current={'host': '203.0.113.1', 'user': 'root', 'port': 22}, device='home', without_tailscale=True)
        vps.write_private(self.state / 'inventory.json', vps.dumps(inv))
        with mock.patch.object(vps, 'remote', return_value=subprocess.CompletedProcess([], 0, 'skipped\n')), \
                mock.patch.object(vps, 'check_clients') as clients, mock.patch.object(vps, 'run') as run, \
                mock.patch('sys.stdout', new_callable=io.StringIO) as output:
            result = vps.main(['--state-dir', str(self.state), 'check', '--ping'])
        self.assertEqual(result, 0)
        clients.assert_called_once()
        run.assert_not_called()
        self.assertIn('SKIP Tailscale ping', output.getvalue())

    def test_check_report_requires_provider_status_and_never_changes_tun(self):
        config = vps.client_config(vps.nodes(self.ident, "203.0.113.1", "company")[0], 18791)
        self.assertEqual(config["bind-address"], "127.0.0.1")
        self.assertNotIn("tun", config)
        self.assertNotIn("external-controller", config)
        self.assertNotIn("dns", config)
        self.assertFalse(config["allow-lan"])


if __name__ == "__main__":
    unittest.main()
