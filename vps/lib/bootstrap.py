"""Offline, isolated Mihomo management envelope around a native Hysteria2 node.

PyYAML is an OPTIONAL local bootstrap dependency, imported only when rendering.
No deployment, URL-export, network, filesystem or authority operations live here.
The caller must supply an already device-scoped, committed/hash-verified native
profile and trusted expected endpoint/ports/domain. Parsing is not attestation.
The caller also verifies that ``interface`` is the physical, non-TUN interface,
starts a separate core, and owns private directory/file permissions for the bytes
returned here (which contain the native credentials). Never log those bytes.
"""
import copy
import ipaddress
import re


MAX_PROFILE_BYTES = 1024 * 1024
_MAX_VALUES = 50000
_MAX_DEPTH = 64


class BootstrapError(ValueError):
    """Invalid native profile or management envelope; messages omit input values."""


def _yaml():
    try:
        import yaml
    except ImportError:
        raise BootstrapError('Local bootstrap requires the optional PyYAML dependency') from None
    return yaml


def _port(value):
    if type(value) is not int or not 1 <= value <= 65535:
        raise BootstrapError('Management and proxy ports must be integers in 1..65535')
    return value


def _endpoint(value):
    try:
        if type(value) is not str:
            raise ValueError
        address = ipaddress.IPv4Address(value)
        if (not address.is_global or address.is_multicast or address.is_reserved
                or str(address) != value):
            raise ValueError
    except ValueError:
        raise BootstrapError('Expected endpoint must be a numeric public unicast IPv4 address') from None
    return value


def _domain(value):
    if (type(value) is not str or len(value) > 253 or '.' not in value
            or any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                   for label in value.split('.'))):
        raise BootstrapError('Expected certificate domain must be a DNS hostname')
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value
    raise BootstrapError('Expected certificate domain must be a DNS hostname')


def _interface(value):
    # Conservative Linux IFNAMSIZ (16 including NUL) syntax, not device discovery.
    if (type(value) is not str or not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', value)
            or value in {'.', '..', 'lo'}):
        raise BootstrapError('A physical Linux interface name is required')
    return value


def _check_tree(value, active=None, budget=None, depth=0):
    """Bound parsed graphs, rejecting recursion and non-native YAML value types."""
    active = set() if active is None else active
    budget = [_MAX_VALUES] if budget is None else budget
    budget[0] -= 1
    if budget[0] < 0 or depth > _MAX_DEPTH:
        raise BootstrapError('Native profile structure exceeds bootstrap limits')
    if type(value) in (dict, list):
        identity = id(value)
        if identity in active:
            raise BootstrapError('Cyclic YAML is not a native profile')
        active.add(identity)
        if type(value) is dict:
            if any(type(key) is not str for key in value):
                raise BootstrapError('Native profile mapping keys must be strings')
            values = value.values()
        else:
            values = value
        for child in values:
            _check_tree(child, active, budget, depth + 1)
        active.remove(identity)
    elif type(value) not in (str, bool, int, float, type(None)):
        raise BootstrapError('Native profile contains unsupported YAML values')


def _load_profile(profile_bytes, yaml):
    if type(profile_bytes) is not bytes or not 0 < len(profile_bytes) <= MAX_PROFILE_BYTES:
        raise BootstrapError('Native profile must be nonempty YAML bytes within the size limit')

    class UniqueSafeLoader(yaml.SafeLoader):
        # SafeLoader otherwise silently overwrites duplicate keys. Keep its safe
        # constructors; reject ambiguous duplicate/merge mappings, not parse YAML
        # with a second ad-hoc node parser.
        def construct_mapping(self, node, deep=False):
            seen = set()
            for key_node, _ in node.value:
                key = self.construct_object(key_node, deep=deep)
                if type(key) is not str or key in seen:
                    raise BootstrapError('Native profile has ambiguous mapping keys')
                seen.add(key)
            return super().construct_mapping(node, deep=deep)

    try:
        profile = yaml.load(profile_bytes, Loader=UniqueSafeLoader)
    except (yaml.YAMLError, UnicodeError, RecursionError, ValueError, TypeError):
        # Parser diagnostics can quote a password or URI from the source line.
        raise BootstrapError('Native profile is invalid or ambiguous safe YAML') from None
    _check_tree(profile)
    if type(profile) is not dict or type(profile.get('proxies')) is not list:
        raise BootstrapError('Native profile must contain a proxies list')
    return profile


def _native_node(profile, endpoint, proxy_port, domain, interface):
    proxies = profile['proxies']
    if any(type(node) is not dict or type(node.get('type')) is not str
           or not node['type'] or type(node.get('name')) is not str or not node['name']
           for node in proxies):
        raise BootstrapError('Native proxies require a type and name')
    names = [node['name'] for node in proxies]
    if len(set(names)) != len(names):
        raise BootstrapError('Native proxy names must be unambiguous')
    nodes = [node for node in proxies if node['type'] == 'hysteria2']
    if len(nodes) != 1:
        raise BootstrapError('Native profile must contain exactly one hysteria2 proxy')
    node = nodes[0]
    name = node['name']
    if (not name.strip() or name != name.strip() or name in {'DIRECT', 'REJECT', 'GLOBAL', 'PASS', 'COMPATIBLE', 'REJECT-DROP'}
            or any(char in name for char in ',()\r\n') or any(ord(char) < 32 for char in name)):
        raise BootstrapError('Native Hysteria2 name cannot be used as a fixed rule target')
    if node.get('server') != endpoint:
        raise BootstrapError('Native Hysteria2 server does not match the expected public endpoint')
    if _port(node.get('port')) != proxy_port:
        raise BootstrapError('Native Hysteria2 port does not match the expected proxy port')
    if type(node.get('password')) is not str or not node['password'].strip():
        raise BootstrapError('Native Hysteria2 requires a nonempty string password')
    if node.get('sni') != domain:
        raise BootstrapError('Native Hysteria2 SNI does not match the expected certificate domain')
    for key in ('servername', 'name-cert-verify'):
        if key in node and node[key] != domain:
            raise BootstrapError('Native Hysteria2 certificate name does not match the expected domain')
    for key in ('skip-cert-verify', 'insecure', 'disable-sni'):
        if key in node and node[key] is not False:
            raise BootstrapError('Native Hysteria2 must retain strict certificate verification and SNI')
    # Pins/custom CAs can replace normal public-CA/hostname verification. An
    # alternate dialer, realm or port set can replace the expected outer socket.
    for key in ('ca', 'ca-str', 'fingerprint', 'dialer-proxy', 'ports'):
        if key in node and node[key] not in (None, ''):
            raise BootstrapError('Native Hysteria2 has an unsupported trust or outer-dial override')
    if 'realm-opts' in node and (type(node['realm-opts']) is not dict
                               or node['realm-opts'].get('enable', False) is not False):
        raise BootstrapError('Native Hysteria2 cannot use alternate realm discovery')
    if 'interface-name' in node and node['interface-name'] != interface:
        raise BootstrapError('Native Hysteria2 interface conflicts with the physical interface')
    return copy.deepcopy(node)


def render_management(profile_bytes, *, endpoint, proxy_port, ssh_port, domain,
                      interface, listen_port=10808):
    """Return UTF-8 YAML bytes, copying one native H2 mapping without re-encoding.

    Arguments bind the native node to the caller's trusted committed identity.
    Ports are integers in 1..65535; ``endpoint`` is a public numeric IPv4 and
    ``domain`` is its expected public-CA certificate hostname, exactly matching
    native ``sni``. ``interface`` is required (Linux name, e.g. ``eno1``).
    This helper validates shape/trust/routing, not account scope, commit hashes,
    native protocol interoperability, physical interface existence, or reachability.

    Only TCP to endpoint:{ssh_port,8443} is allowed. DNS is internal-only numeric
    AliDNS over DIRECT, never through H2; no DNS socket, TUN, controller, provider,
    selection group, desktop settings or direct management fallback is inherited.
    Invalid input raises BootstrapError without including source/secret values.
    """
    endpoint = _endpoint(endpoint)
    proxy_port, ssh_port, listen_port = map(_port, (proxy_port, ssh_port, listen_port))
    domain, interface = _domain(domain), _interface(interface)
    yaml = _yaml()
    node = _native_node(_load_profile(profile_bytes, yaml), endpoint, proxy_port, domain, interface)
    rules = [f'AND,((NETWORK,TCP),(IP-CIDR,{endpoint}/32,no-resolve),(DST-PORT,{port})),{node["name"]}'
             for port in dict.fromkeys((ssh_port, 8443))]
    runtime = {
        'mixed-port': listen_port,
        'bind-address': '127.0.0.1',
        'allow-lan': False,
        'mode': 'rule',
        'log-level': 'silent',
        'ipv6': False,
        'interface-name': interface,
        'profile': {'store-selected': False, 'store-fake-ip': False},
        'tun': {'enable': False},
        'dns': {
            'enable': True,
            'ipv6': False,
            'enhanced-mode': 'redir-host',
            'use-hosts': False,
            'use-system-hosts': False,
            'nameserver': ['https://223.5.5.5/dns-query#DIRECT'],
            'proxy-server-nameserver': ['https://223.5.5.5/dns-query#DIRECT'],
        },
        'proxies': [node],
        'rules': rules + ['MATCH,REJECT'],
    }
    try:
        return yaml.safe_dump(runtime, allow_unicode=True, sort_keys=False).encode('utf-8')
    except (yaml.YAMLError, UnicodeError, RecursionError, ValueError, TypeError):
        raise BootstrapError('Native profile cannot be serialized as a management runtime') from None
