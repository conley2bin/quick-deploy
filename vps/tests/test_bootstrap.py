"""Pure offline management envelope; native -t never starts a proxy or uses feeds."""
import copy
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import bootstrap

try:
    import yaml
except ImportError:
    yaml = None


ENDPOINT = '93.184.216.34'
DOMAIN = 'example.com'
SECRET = 'fake-native-password-DO-NOT-LOG'
MISSING = object()


def native_node():
    return {
        'name': 'Native H2 公司', 'type': 'hysteria2', 'server': ENDPOINT,
        'port': 443, 'password': SECRET, 'sni': DOMAIN, 'skip-cert-verify': False,
        'alpn': ['h3'], 'obfs': 'salamander', 'obfs-password': 'fake-obfs-secret',
        'up': '30 Mbps', 'down': '200 Mbps', 'udp': True,
        'initial-stream-receive-window': 8388608,
        'max-stream-receive-window': 8388608,
        'initial-connection-receive-window': 20971520,
        'max-connection-receive-window': 20971520,
        # Future/native options must survive, including nested scalar types.
        'native-uncommon-options': {
            'tls': {'alpn': ['h3', 'hy2'], 'session': [False, None, 1.5, '001']},
            'flow-control': {'window': 12345678, 'padding': True},
        },
    }


def native_profile():
    return {
        'mixed-port': 7897, 'allow-lan': True, 'bind-address': '*',
        'external-controller': '0.0.0.0:9090', 'secret': 'fake-controller-secret',
        'tun': {'enable': True, 'auto-route': True, 'dns-hijack': ['any:53']},
        'dns': {'enable': True, 'listen': '0.0.0.0:53',
                'nameserver': ['https://8.8.8.8/dns-query#PROXY']},
        'profile': {'store-selected': True},
        'proxy-providers': {'ignored': {'url': 'https://invalid.example/feed'}},
        'proxies': [
            {'name': 'Native TCP', 'type': 'vless', 'server': ENDPOINT, 'port': 443},
            native_node(),
        ],
        'proxy-groups': [{'name': 'PROXY', 'type': 'select', 'proxies': ['DIRECT', 'Native TCP']}],
        'rules': ['DST-PORT,22,DIRECT', 'MATCH,DIRECT'],
    }


def render(document=None, **overrides):
    arguments = dict(endpoint=ENDPOINT, proxy_port=443, ssh_port=2222,
                     domain=DOMAIN, interface='eno1')
    arguments.update(overrides)
    if document is None:
        document = native_profile()
    data = document if type(document) is bytes else yaml.safe_dump(document).encode('utf-8')
    return bootstrap.render_management(data, **arguments)


@unittest.skipUnless(yaml, 'Optional local bootstrap dependency PyYAML is not installed')
class Bootstrap(unittest.TestCase):
    def assert_invalid(self, document=None, **overrides):
        with self.assertRaises(bootstrap.BootstrapError) as raised:
            render(document, **overrides)
        self.assertNotIn(SECRET, str(raised.exception))
        self.assertNotIn('fake-obfs-secret', str(raised.exception))
        self.assertNotIn(SECRET, ''.join(traceback.format_exception(raised.exception)))
        return raised.exception

    def test_entire_native_mapping_preserved_without_mutating_profile(self):
        source = native_profile()
        before = copy.deepcopy(source)
        result = render(source)
        self.assertIs(type(result), bytes)
        runtime = yaml.safe_load(result)
        self.assertEqual(runtime['proxies'], [source['proxies'][1]])
        self.assertEqual(source, before)
        self.assertEqual(runtime['proxies'][0]['native-uncommon-options'],
                         before['proxies'][1]['native-uncommon-options'])
        self.assertEqual(result, render(source))

    def test_three_native_roles_keep_only_full_unchanged_h2_mapping(self):
        source = native_profile()
        shared_uuid = '00000000-0000-4000-8000-000000000001'
        reality = {'name': 'SELF-main', 'type': 'vless', 'server': ENDPOINT,
                   'port': 9443, 'uuid': shared_uuid, 'network': 'tcp', 'tls': True,
                   'flow': 'xtls-rprx-vision', 'reality-opts': {'public-key': 'y' * 43}}
        cdn = {'name': 'SELF-cdn', 'type': 'vless', 'server': 'cdn.example.com',
               'port': 443, 'uuid': shared_uuid, 'network': 'xhttp', 'tls': True,
               'servername': 'cdn.example.com', 'alpn': ['h2'],
               'xhttp-opts': {'host': 'cdn.example.com', 'path': '/qd-xhttp/', 'mode': 'packet-up'}}
        h2 = dict(native_node(), name='SELF-h2')
        source['proxy-groups'] = [{'name': 'PROXY', 'type': 'select',
                                   'proxies': ['SELF-h2', 'SELF-cdn', 'SELF-main', 'DIRECT']}]
        # Selection must depend on the sole native H2 type, not list position or
        # the UUID shared by both VLESS transports. All H2 options survive.
        for nodes in ([reality, cdn, h2], [h2, reality, cdn], [cdn, h2, reality]):
            with self.subTest(order=[node['name'] for node in nodes]):
                source['proxies'] = nodes
                before = copy.deepcopy(source)
                runtime = yaml.safe_load(render(source))
                self.assertEqual(runtime['proxies'], [h2])
                self.assertEqual(runtime['proxies'][0]['port'], 443)
                self.assertEqual(runtime['tun'], {'enable': False})
                self.assertEqual(runtime['interface-name'], 'eno1')
                self.assertNotIn('proxy-groups', runtime)
                self.assertTrue(all(rule.endswith(',SELF-h2') for rule in runtime['rules'][:-1]))
                self.assertEqual(runtime['rules'][-1], 'MATCH,REJECT')
                self.assertEqual(source, before)
        source['proxies'] = [reality, cdn]
        self.assert_invalid(source)  # No REALITY/CDN management fallback.

    def test_original_envelope_is_not_inherited(self):
        runtime = yaml.safe_load(render())
        self.assertEqual(runtime['bind-address'], '127.0.0.1')
        self.assertEqual(runtime['mixed-port'], 10808)
        self.assertIs(runtime['allow-lan'], False)
        self.assertEqual(runtime['interface-name'], 'eno1')
        self.assertEqual(runtime['mode'], 'rule')
        self.assertEqual(runtime['log-level'], 'silent')
        self.assertEqual(runtime['tun'], {'enable': False})
        self.assertEqual(runtime['profile'], {'store-selected': False, 'store-fake-ip': False})
        for key in ('external-controller', 'external-controller-tls', 'external-controller-unix',
                    'secret', 'listeners', 'proxy-groups', 'proxy-providers', 'rule-providers',
                    'port', 'socks-port', 'redir-port', 'tproxy-port', 'sniffer', 'hosts'):
            self.assertNotIn(key, runtime)
        dns = runtime['dns']
        self.assertNotIn('listen', dns)
        self.assertNotIn('dns-hijack', dns)
        self.assertEqual(dns['enhanced-mode'], 'redir-host')
        self.assertEqual(dns['nameserver'], ['https://223.5.5.5/dns-query#DIRECT'])
        self.assertEqual(dns['proxy-server-nameserver'], dns['nameserver'])
        self.assertNotIn('#PROXY', yaml.safe_dump(dns))
        self.assertNotIn('fake-controller-secret', render().decode())

    def test_only_exact_tcp_self_management_is_allowed(self):
        runtime = yaml.safe_load(render())
        name = runtime['proxies'][0]['name']
        expected = [
            f'AND,((NETWORK,TCP),(IP-CIDR,{ENDPOINT}/32,no-resolve),(DST-PORT,2222)),{name}',
            f'AND,((NETWORK,TCP),(IP-CIDR,{ENDPOINT}/32,no-resolve),(DST-PORT,8443)),{name}',
            'MATCH,REJECT',
        ]
        self.assertEqual(runtime['rules'], expected)
        # Assert each allow rule has all three conjuncts; exercise the resulting
        # allow set, not a broad IP/port/network rule or DIRECT escape hatch.
        pattern = r'AND,\(\(NETWORK,(TCP)\),\(IP-CIDR,([0-9.]+)/32,no-resolve\),\(DST-PORT,(\d+)\)\),(.+)'
        allowed = set()
        for rule in runtime['rules'][:-1]:
            match = re.fullmatch(pattern, rule)
            self.assertIsNotNone(match)
            network, address, port, target = match.groups()
            self.assertEqual(target, name)
            allowed.add((network, address, int(port)))
        for network, address, port, permitted in [
            ('TCP', ENDPOINT, 2222, True), ('TCP', ENDPOINT, 8443, True),
            ('TCP', ENDPOINT, 22, False), ('TCP', ENDPOINT, 443, False),
            ('TCP', ENDPOINT, 2053, False), ('TCP', ENDPOINT, 2096, False),
            ('TCP', ENDPOINT, 62789, False), ('UDP', ENDPOINT, 2222, False),
            ('UDP', ENDPOINT, 8443, False), ('TCP', '127.0.0.1', 2222, False),
            ('TCP', '192.168.1.1', 2222, False), ('TCP', '8.8.8.8', 8443, False),
        ]:
            with self.subTest(network=network, address=address, port=port):
                self.assertEqual((network, address, port) in allowed, permitted)
        self.assertFalse(any('DIRECT' in rule for rule in runtime['rules']))

    def test_custom_listen_and_duplicate_ssh_feed_port(self):
        runtime = yaml.safe_load(render(ssh_port=8443, listen_port=12080))
        self.assertEqual(runtime['mixed-port'], 12080)
        self.assertEqual(len(runtime['rules']), 2)
        self.assertIn('(DST-PORT,8443)', runtime['rules'][0])

    def test_json_is_accepted_as_native_yaml(self):
        runtime = yaml.safe_load(render(json.dumps(native_profile()).encode()))
        self.assertEqual(runtime['proxies'], [native_node()])

    def test_absent_skip_verify_retains_secure_default(self):
        source = native_profile()
        del source['proxies'][1]['skip-cert-verify']
        self.assertEqual(yaml.safe_load(render(source))['proxies'][0], source['proxies'][1])

    def test_missing_or_extra_hysteria2_is_rejected(self):
        source = native_profile()
        source['proxies'].pop()
        self.assert_invalid(source)
        source = native_profile()
        second = native_node()
        second['name'] = 'second H2'
        source['proxies'].append(second)
        self.assert_invalid(source)

    def test_invalid_native_fields_are_rejected_without_echo(self):
        cases = {
            'server': [MISSING, '8.8.8.8', 'server.example.com', '127.0.0.1', 123, None],
            'port': [MISSING, 8443, 0, 65536, '443', 443.0, True, None],
            'password': [MISSING, '', ' \t', 123, True, None, ['secret'], {'secret': SECRET}],
            'sni': [MISSING, 'wrong.example.com', '', ENDPOINT, False, None],
            'type': [MISSING, '', 'hy2', 'vless', 123, None],
            'name': [MISSING, '', ' ', 'DIRECT', 'REJECT', 'PASS', 'bad,name',
                     'bad(name)', 'bad\nname', ['bad'], None],
            'skip-cert-verify': [True, 'true', 'false', 0, 1, None],
            'insecure': [True, 'false', None],
            'disable-sni': [True],
            'servername': ['wrong.example.com'],
            'name-cert-verify': ['wrong.example.com'],
            'ca': ['private-ca.pem'], 'ca-str': ['fake-private-ca'],
            'fingerprint': ['ab' * 32], 'dialer-proxy': ['DIRECT', 'Native TCP'],
            'ports': ['443-8443', [443, 8443], 8443],
            'realm-opts': [{'enable': True, 'server-url': 'https://invalid.example'}],
            'interface-name': ['tun0', 'lo'],
        }
        for field, values in cases.items():
            for value in values:
                with self.subTest(field=field, kind=type(value).__name__):
                    source = native_profile()
                    if value is MISSING:
                        source['proxies'][1].pop(field, None)
                    else:
                        source['proxies'][1][field] = value
                    self.assert_invalid(source)

    def test_invalid_expected_identity_and_ports_are_rejected(self):
        for endpoint in ['127.0.0.1', '10.0.0.1', '100.64.0.1', '192.0.2.1', '224.0.0.1',
                         '240.0.0.1', '0.0.0.0', '2001:4860::8888', 'example.com',
                         '093.184.216.34', '', None, 123]:
            with self.subTest(endpoint=endpoint):
                self.assert_invalid(endpoint=endpoint)
        for field in ['proxy_port', 'ssh_port', 'listen_port']:
            for port in [0, -1, 65536, '443', True, 443.0, None]:
                with self.subTest(field=field, port=port):
                    self.assert_invalid(**{field: port})
        for domain in ['', 'localhost', '127.0.0.1', 'https://example.com', 'bad_name.example',
                       '-bad.example', 'example.com.', 'a' * 64 + '.com', None]:
            with self.subTest(domain=domain):
                self.assert_invalid(domain=domain)

    def test_physical_interface_argument_is_required_and_validated(self):
        for interface in ['', ' ', 'lo', '.', '..', 'eth0:1', 'a/b', 'eth0\n',
                          'a' * 16, 'eth0;id', None, False]:
            with self.subTest(interface=interface):
                self.assert_invalid(interface=interface)
        with self.assertRaises(TypeError):
            bootstrap.render_management(b'{}', endpoint=ENDPOINT, proxy_port=443,
                                        ssh_port=22, domain=DOMAIN)
        source = native_profile()
        source['proxies'][1]['interface-name'] = 'eno1'
        self.assertEqual(yaml.safe_load(render(source))['proxies'][0], source['proxies'][1])

    def test_invalid_yaml_and_structure_have_no_source_diagnostics(self):
        for data in [b'', b'\xff', b'[' + SECRET.encode(),
                     ('proxies: !!python/object/apply:os.system ["' + SECRET + '"]').encode(),
                     b'{}\n---\n{}', b'proxies: []\nproxies: []',
                     b'proxies: [{name: H2, type: hysteria2, password: one, password: two}]',
                     b'proxies: &cycle [*cycle]', b'cycle: &a {child: *a}\nproxies: []',
                     b'!!set {proxies: null}', b'[]', b'proxies: {}', b'proxies: [null]',
                     b'proxies: []\n1: wrong', b'proxies: []\n<<: {proxies: []}',
                     b'#' * (bootstrap.MAX_PROFILE_BYTES + 1)]:
            with self.subTest(size=len(data)):
                self.assert_invalid(data)
        for data in [None, '{}', bytearray(b'{}')]:
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap.render_management(data, endpoint=ENDPOINT, proxy_port=443,
                                            ssh_port=22, domain=DOMAIN, interface='eno1')

    def test_excessive_depth_and_alias_expansion_are_bounded(self):
        self.assert_invalid(b'proxies: []\ndeep: ' + b'[' * 100 + b'0' + b']' * 100)
        data = b'proxies: []\na0: &a0 [0, 0]\n'
        for level in range(1, 18):
            data += f'a{level}: &a{level} [*a{level-1}, *a{level-1}]\n'.encode()
        self.assert_invalid(data)

    def test_duplicate_name_even_across_protocols_is_rejected(self):
        source = native_profile()
        source['proxies'][0]['name'] = source['proxies'][1]['name']
        self.assert_invalid(source)

    def test_native_mihomo_config_test_when_installed(self):
        binary = shutil.which('mihomo') or shutil.which('verge-mihomo')
        if not binary:
            self.skipTest('Installed Mihomo unavailable; native -t is not unit-fixture evidence')
        source = native_profile()
        # This gate uses documented native options, not the future-option fixture.
        del source['proxies'][1]['native-uncommon-options']
        source['proxies'][1]['password'] = '14f984f5-78bb-4d59-ab63-226a164a568e'
        for ssh_port in (2222, 8443):
            with self.subTest(ssh_port=ssh_port), tempfile.TemporaryDirectory(prefix='qd-management-test-') as root:
                config = Path(root) / 'management.yaml'
                config.write_bytes(render(source, ssh_port=ssh_port))
                config.chmod(0o600)
                result = subprocess.run([binary, '-t', '-d', root, '-f', str(config)],
                                        capture_output=True, timeout=10)
                # No config/auth/log output is attached to assertion diagnostics.
                self.assertEqual(result.returncode, 0, 'Installed Mihomo rejected management runtime')


class OptionalDependency(unittest.TestCase):
    def test_module_import_does_not_require_yaml_but_render_fails_visibly(self):
        real_import = __import__
        def without_yaml(name, *args, **kwargs):
            if name == 'yaml':
                raise ModuleNotFoundError('PyYAML intentionally absent')
            return real_import(name, *args, **kwargs)
        path = Path(bootstrap.__file__)
        spec = importlib.util.spec_from_file_location('bootstrap_without_yaml', path)
        module = importlib.util.module_from_spec(spec)
        with mock.patch('builtins.__import__', side_effect=without_yaml):
            spec.loader.exec_module(module)
            with self.assertRaisesRegex(module.BootstrapError, 'optional PyYAML'):
                module.render_management(b'{}', endpoint=ENDPOINT, proxy_port=443,
                                         ssh_port=22, domain=DOMAIN, interface='eno1')


if __name__ == '__main__':
    unittest.main()
