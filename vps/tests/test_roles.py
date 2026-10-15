"""Role boundaries: fake relay host and private dual-host inventory; no contacts."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import vps
import test_remote as fixtures


class RelayRemote(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.RemoteTransaction(methodName='runTest')
        self.f.setUp()
        self.addCleanup(self.f.tmp.cleanup)
        f = self.f
        f.role = 'relay'
        f.prepare_tools()
        params = {'role': 'relay', 'ssh_user': 'ubuntu', 'host': '93.184.216.34', 'relay_endpoint': '93.184.216.34:40000', 'ssh_port': 22, 'without_tailscale': False}
        (f.state / 'incoming/params.json').write_text(json.dumps(params))
        (f.state / 'incoming/config.json').unlink()
        (f.etc / 'sing-box/config.json').unlink()
        (f.etc / 'systemd/system/sing-box.service.d/90-quick-deploy-vps.conf').unlink()
        (f.state / 'incoming/firewall.template').write_text(vps.firewall(role='relay'))
        self.bound = f.root / 'relay-bound'
        self.loaded = f.root / 'nft-loaded'
        self.endpoint = f.root / 'relay-endpoint'
        self.argv_log = f.root / 'tailscale-argv.jsonl'
        f.stub('getent', 'printf "%s:x:1000:1000::%s:/bin/bash\\n" "${2:-ubuntu}" "$QD_ACCOUNT"\n')
        f.stub('tailscale', f'''printf 'tailscale %s\\n' "$*" >>"$QD_LOG"
python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "$@" >>'{self.argv_log}'
if [[ "${{2:-}}" == --help ]]; then
 echo '--advertise-tags --accept-dns --accept-routes --hostname --timeout --auth-key file: --relay-server-port --relay-server-static-endpoints'
elif [[ "${{1:-}}" == set ]]; then
 if [[ "$*" == *'--relay-server-port=40000'* ]]; then touch '{self.bound}'; else rm -f '{self.bound}'; fi
 for arg in "$@"; do
  if [[ $arg == --relay-server-static-endpoints=* ]]; then printf '%s' "${{arg#*=}}" >'{self.endpoint}'; fi
 done
elif [[ "${{1:-}}" == debug ]]; then
 python3 -c 'import pathlib,json; b=pathlib.Path("{self.bound}"); e=pathlib.Path("{self.endpoint}"); s=e.read_text() if e.exists() else ""; print(json.dumps({{"RelayServerPort":40000 if b.exists() else None,"RelayServerStaticEndpoints":[s] if s else []}}))'
elif [[ "${{1:-}}" == status ]]; then
 echo '{{"BackendState":"Running","Self":{{"Tags":["tag:relay"]}}}}'
elif [[ "${{1:-}}" == ip ]]; then echo 100.70.0.90
fi
''')
        f.stub('ss', f'''if [[ "$*" == *40000* && -f '{self.bound}' ]]; then echo listening; fi
''')
        f.stub('nft', f'''printf 'nft %s\\n' "$*" >>"$QD_LOG"
if [[ " $* " == *' list '* ]]; then
 test -f '{self.loaded}' || exit 1
 echo 'table inet qd_vps {{}}'
elif [[ "${{1:-}}" == -f ]]; then
 if grep -q 'table inet qd_vps {{' "$2"; then touch '{self.loaded}'; else rm -f '{self.loaded}'; fi
fi
''')
        # Any accidental proxy command is a hard test failure, not a mock pass.
        f.stub('sing-box', 'echo UNEXPECTED_PROXY >&2; exit 92\n')
        f.stub('apt-mark', 'echo UNEXPECTED_PROXY_HOLD >&2; exit 93\n')
        f.stub('readlink', 'echo UNEXPECTED_PROXY_PID >&2; exit 94\n')

    def deploy(self):
        for action in ('prepare', 'apply', 'harden', 'commit'):
            result = self.f.action(action)
            self.assertEqual(result.returncode, 0, action + ': ' + result.stdout + result.stderr)

    def test_relay_deploy_repeat_and_confirmed_rollback_without_proxy(self):
        self.deploy()
        f = self.f
        first = (f.etc / 'quick-deploy-vps.nft').read_bytes()
        self.assertTrue(self.bound.exists())
        argv = [json.loads(line) for line in self.argv_log.read_text().splitlines()]
        self.assertIn(['set', '--relay-server-port=40000', '--relay-server-static-endpoints=93.184.216.34:40000'], argv)
        self.assertIn('--advertise-tags=tag:relay', f.log.read_text())
        self.assertNotIn('sing-box', f.log.read_text())
        self.assertFalse((f.etc / 'sing-box/config.json').exists())
        self.assertEqual(f.action('health').returncode, 0)
        # Repeated deployment doesn't stack rules or invent a proxy artifact.
        self.deploy()
        self.assertEqual((f.etc / 'quick-deploy-vps.nft').read_bytes(), first)
        self.assertEqual(f.action('rollback').returncode, 0)
        self.assertEqual(f.action('transaction-health').returncode, 0)
        self.assertEqual(f.action('commit').returncode, 0)
        self.assertNotIn('sing-box', f.log.read_text())

    def test_pristine_relay_rollback_disables_relay_port(self):
        self.deploy()
        result = self.f.action('rollback')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('pristine relay rollback', result.stdout)
        self.assertFalse(self.bound.exists())
        self.assertFalse(self.loaded.exists())
        self.assertEqual(self.endpoint.read_text(), '')
        argv = [json.loads(line) for line in self.argv_log.read_text().splitlines()]
        self.assertIn(['set', '--relay-server-port=', '--relay-server-static-endpoints='], argv)
        self.assertEqual(self.f.action('commit').returncode, 0)
        self.assertNotIn('sing-box', self.f.log.read_text())

    def test_ipv6_static_endpoint_and_health_reject_missing_pref(self):
        path = self.f.state / 'incoming/params.json'
        p = json.loads(path.read_text())
        p.update(host='2606:4700:4700::1111', relay_endpoint='[2606:4700:4700::1111]:40000')
        path.write_text(json.dumps(p))
        self.deploy()
        expected = '[2606:4700:4700::1111]:40000'
        argv = [json.loads(line) for line in self.argv_log.read_text().splitlines()]
        self.assertIn(['set', '--relay-server-port=40000', '--relay-server-static-endpoints=' + expected], argv)
        self.assertIn(expected, self.f.action('health').stdout)
        self.assertEqual(self.f.action('relay-endpoint').stdout.strip(), expected)
        self.endpoint.write_text('')
        result = self.f.action('health')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('static endpoint missing', result.stderr)

    def test_legacy_proxy_host_cannot_be_adopted_as_relay(self):
        (self.f.etc / 'sing-box/config.json').write_text('legacy proxy fixture')
        result = self.f.action('prepare')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('existing proxy configuration', result.stderr)
        self.assertFalse(self.f.log.exists())

    def test_role_conflict_refused_without_proxy_service_access(self):
        self.deploy()
        self.f.role = 'proxy'
        result = self.f.action('health')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('other VPS role', result.stderr)

    def test_pkgs_unreachable_has_clear_failure_without_proxy_fallback(self):
        f = self.f
        source = f.script.read_text().replace('if ! command -v tailscale >/dev/null; then\n            curl -fIsS', 'if true; then\n            curl -fIsS', 1)
        f.script.write_text(source)
        (f.state / 'incoming/remote.sh').write_text(source)
        f.stub('curl', 'echo blocked >&2; exit 7\n')
        result = f.action('prepare')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('pkgs.tailscale.com unreachable', result.stderr)
        self.assertNotIn('sing-box', f.log.read_text())

    def test_relay_firewall_has_no_proxy_or_extra_ssh_ports(self):
        fw = vps.firewall(role='relay', ssh_port=2222)
        self.assertIn('tcp dport 2222 accept', fw)
        self.assertNotIn('tcp dport 22 accept', fw)
        self.assertNotIn('443', fw)
        self.assertNotIn('proxy_output', fw)
        self.assertIn('udp dport { 40000, 41641 }', fw)
        self.assertIn('ip6 saddr fe80::/10', fw)
        proxy = vps.firewall('100.70.0.1')
        self.assertIn('udp dport { 443, 41641 }', proxy)
        self.assertNotIn('40000', proxy)


class RoleInventory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='qd-vps-roles-')
        self.addCleanup(self.tmp.cleanup)
        self.state = vps.private_dir(Path(self.tmp.name) / 'state')
        self.proxy = {'host': '93.184.216.33', 'user': 'root', 'port': 22, 'provider': 'proxy', 'role': 'proxy'}
        self.relay = {'host': '93.184.216.34', 'user': 'ubuntu', 'port': 22, 'provider': 'relay', 'role': 'relay'}
        self.inv = vps.updated_inventory({}, self.proxy)
        self.inv = vps.updated_inventory(self.inv, self.relay, 'relay')
        self.inv.update(company_ip='100.70.0.1', home_ip='100.70.0.2')
        vps.write_private(self.state / 'inventory.json', vps.dumps(self.inv))

    def test_global_literal_validation_and_bundle_format_before_contact(self):
        self.assertEqual(vps.relay_endpoint('93.184.216.34'), '93.184.216.34:40000')
        self.assertEqual(vps.relay_endpoint('2606:4700:4700::1111'), '[2606:4700:4700::1111]:40000')
        for host in ('relay.example.net', '10.1.2.3', '192.168.0.2', '100.64.0.2', '127.0.0.1', '203.0.113.2', 'fe80::1', 'fc00::1', '224.0.0.1'):
            with self.subTest(host=host), mock.patch.object(vps, 'run') as run, mock.patch('sys.stderr', new_callable=io.StringIO):
                result = vps.main(['--state-dir', str(self.state / 'absent'), 'deploy', '--role', 'relay', '--host', host, '--dry-run'])
                self.assertEqual(result, 1)
                run.assert_not_called()
                self.assertFalse((self.state / 'absent').exists())
        for host in ('93.184.216.34', '2606:4700:4700::1111'):
            record = dict(self.relay, host=host)
            with tarfile.open(fileobj=io.BytesIO(vps.bundle(None, None, record, []))) as archive:
                self.assertEqual(json.load(archive.extractfile('params.json'))['relay_endpoint'], vps.relay_endpoint(host))

    def test_replacing_one_role_preserves_other_current_and_history(self):
        before = vps.dumps(self.inv['roles']['relay'])
        changed = vps.updated_inventory(self.inv, dict(self.proxy, host='93.184.216.35'))
        self.assertEqual(vps.dumps(changed['roles']['relay']), before)
        self.assertEqual(changed['roles']['proxy']['previous'], [self.proxy])
        self.assertEqual(vps.updated_inventory(changed, dict(self.proxy, host='93.184.216.35')), changed)
        with self.assertRaisesRegex(vps.Error, 'same-host'):
            vps.updated_inventory(self.inv, dict(self.relay, host=self.proxy['host']), 'relay')

    def test_mixed_schema_and_shared_hosts_are_ambiguous(self):
        with self.assertRaisesRegex(vps.Error, 'mixed'):
            vps.inventory_document(dict(self.inv, current=self.proxy))
        broken = json.loads(json.dumps(self.inv))
        broken['roles']['relay']['current']['host'] = self.proxy['host']
        with self.assertRaisesRegex(vps.Error, 'cannot share'):
            vps.inventory_document(broken)

    def test_legacy_host_maps_only_to_proxy_and_missing_role_refuses(self):
        old = {'version': 1, 'current': dict(self.proxy), 'previous': []}
        migrated = vps.inventory_document(old)
        self.assertEqual(migrated['roles']['proxy']['current'], self.proxy)
        self.assertIsNone(migrated['roles']['relay']['current'])
        vps.write_private(self.state / 'inventory.json', vps.dumps(old))
        with mock.patch.object(vps, 'remote') as remote:
            self.assertEqual(vps.main(['--state-dir', str(self.state), 'status', '--role', 'relay']), 1)
        remote.assert_not_called()

    def test_relay_dryrun_needs_no_device_identity_or_contact(self):
        with mock.patch.object(vps, 'run', side_effect=AssertionError('contact')), mock.patch('sys.stdout', new_callable=io.StringIO) as output:
            result = vps.main(['--state-dir', str(self.state / 'absent'), 'deploy', '--role', 'relay', '--host', '93.184.216.36', '--dry-run'])
        self.assertEqual(result, 0)
        self.assertIn('ubuntu@93.184.216.36', output.getvalue())
        self.assertNotIn('sing-box check', output.getvalue())
        self.assertFalse((self.state / 'absent').exists())

    def test_relay_init_and_deploy_never_create_or_read_proxy_identity(self):
        args = vps.parser().parse_args(['init', '--role', 'relay'])
        vps.init(self.state, args)
        upload = []
        commands = []
        def run(argv, **kwargs):
            upload.append(kwargs['input'])
            commands.append(argv[-1])
        with mock.patch.object(vps, 'identity', side_effect=AssertionError('identity read')), \
                mock.patch.object(vps, 'render_outputs', side_effect=AssertionError('client output')), \
                mock.patch.object(vps, 'public_keys', return_value=['one', 'two']), mock.patch.object(vps, 'confirm'), \
                mock.patch.object(vps, 'remote', return_value=subprocess.CompletedProcess([], 0, b'checkpoint')), mock.patch.object(vps, 'run', side_effect=run):
            result = vps.main(['--state-dir', str(self.state), 'deploy', '--role', 'relay', '--host', '93.184.216.36'])
        self.assertEqual(result, 0)
        self.assertFalse((self.state / 'identity.json').exists())
        self.assertFalse((self.state / 'generated').exists())
        self.assertLess(commands[0].index('other host role'), commands[0].index('tar -xf'))
        self.assertLess(commands[0].index('proxy config already exists'), commands[0].index('tar -xf'))
        with tarfile.open(fileobj=io.BytesIO(upload[0])) as archive:
            self.assertNotIn('config.json', archive.getnames())
            self.assertNotIn('443', archive.extractfile('firewall.template').read().decode())
            params = json.load(archive.extractfile('params.json'))
            self.assertEqual(params['ssh_user'], 'ubuntu')
            self.assertEqual(params['role'], 'relay')
            self.assertNotIn('sing_box_version', params)
        self.assertEqual(vps.role_slot(vps.load(self.state / 'inventory.json'))['current'], self.proxy)

    def test_enrollment_requires_policy_merge_confirmation_before_contact(self):
        with mock.patch.object(vps, 'public_keys', return_value=['one', 'two']), \
                mock.patch.object(vps, 'confirm', side_effect=[None, vps.Error('policy not merged')]), \
                mock.patch.object(vps, 'run') as run:
            result = vps.main(['--state-dir', str(self.state), 'deploy', '--role', 'relay', '--host', '93.184.216.36'])
        self.assertEqual(result, 1)
        run.assert_not_called()

    def test_selected_role_controls_status_and_abort_target(self):
        with mock.patch.object(vps, 'remote') as remote:
            self.assertEqual(vps.main(['--state-dir', str(self.state), 'status', '--role', 'relay']), 0)
            self.assertEqual(remote.call_args.args[0], self.relay)
            self.assertEqual(vps.main(['--state-dir', str(self.state), 'abort', '--role', 'relay']), 0)
            self.assertEqual(remote.call_args.args[0], self.relay)
            self.assertEqual(vps.main(['--state-dir', str(self.state), 'abort', '--role', 'relay', '--host', self.proxy['host']]), 1)
            remote.reset_mock()
            with mock.patch.object(vps, 'confirm'):
                self.assertEqual(vps.main(['--state-dir', str(self.state), 'rollback', '--role', 'relay']), 0)
            self.assertTrue(all(call.args[0] == self.relay and call.kwargs['fresh'] for call in remote.call_args_list))

    def test_policy_before_enrollment_requires_no_identity(self):
        result = vps.main(['--state-dir', str(self.state), 'render', '--policy-only'])
        self.assertEqual(result, 0)
        policy = vps.load(self.state / 'tailnet-policy.json')
        self.assertEqual(set(policy['tagOwners']), {'tag:relay', 'tag:egress'})
        self.assertEqual(policy['grants'][1]['src'], ['tag:egress'])
        self.assertEqual(policy['grants'][1]['ip'], ['tcp:8743'])
        self.assertEqual(policy['grants'][2]['src'], ['company', 'home'])
        self.assertEqual(policy['grants'][2]['dst'], ['tag:relay'])
        self.assertFalse((self.state / 'identity.json').exists())

    def test_relay_backup_contains_only_role_state_not_proxy_secrets(self):
        vps.write_private(self.state / 'identity.json', 'proxy-secret-fixture')
        seen = []
        def run(argv):
            if argv[0] == 'rsync':
                stage = Path(argv[-2])
                self.assertFalse((stage / 'identity.json').exists())
                self.assertFalse((stage / 'generated').exists())
                document = vps.load(stage / 'inventory.json')
                self.assertIsNone(document['roles']['proxy']['current'])
                self.assertEqual(document['roles']['relay']['current'], self.relay)
                seen.append(argv[-1])
        with mock.patch.object(vps, 'run', side_effect=run):
            vps.backup(self.state, self.inv, 'peer-fixture', 'relay')
        self.assertTrue(seen[0].endswith('/relay/'))

    def test_relay_check_pings_numeric_host_and_reports_other_pc_path(self):
        document = {'Self': {'TailscaleIPs': ['100.70.0.1']}, 'Peer': {'p': {'TailscaleIPs': ['100.70.0.2'], 'Active': True, 'PeerRelay': '203.0.113.2:40000:vni:1', 'Relay': 'hkg'}}}
        def run(argv, **kwargs):
            return subprocess.CompletedProcess([], 0, json.dumps(document) if 'status' in argv else '')
        with mock.patch.object(vps, 'remote', side_effect=lambda record, action, **kwargs: subprocess.CompletedProcess([], 0, '93.184.216.34:40000\n' if action == 'relay-endpoint' else '100.70.0.90\n')), \
                mock.patch.object(vps, 'run', side_effect=run) as commands, mock.patch('sys.stdout', new_callable=io.StringIO) as output:
            result = vps.main(['--state-dir', str(self.state), 'check', '--role', 'relay'])
        self.assertEqual(result, 0)
        self.assertIn('peer-relay', output.getvalue())
        self.assertIn('Advertised relay endpoint: 93.184.216.34:40000', output.getvalue())
        self.assertEqual(commands.call_args_list[0].args[0], ['tailscale', 'ping', '100.70.0.90'])
        peer = document['Peer']['p']
        peer['CurAddr'] = '198.51.100.3:41641'
        self.assertEqual(vps.relay_path(document, '100.70.0.1', '100.70.0.2')[1], 'direct')
        peer['CurAddr'] = peer['PeerRelay'] = ''
        self.assertEqual(vps.relay_path(document, '100.70.0.1', '100.70.0.2')[1], 'DERP')
        peer['Active'] = False
        self.assertIn('idle', vps.relay_path(document, '100.70.0.1', '100.70.0.2')[1])
        with self.assertRaisesRegex(vps.Error, 'neither'):
            vps.relay_path({'Self': None}, '100.70.0.1', '100.70.0.2')


if __name__ == '__main__':
    unittest.main()
