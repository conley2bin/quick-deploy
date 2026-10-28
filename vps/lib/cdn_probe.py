"""Finite, isolated CDN acceptance around complete native Mihomo node mappings.

No desktop controller, ambient proxy/resolver, custom CA, TUN, or direct fallback.
Only the caller persists the successful public-metadata proof. Temporary native
credentials live in mode0700/0600 files and are removed; process output is drained
privately without retention.
"""
from __future__ import annotations

import contextlib
import copy
from datetime import datetime, timezone
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import select
import signal
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
import time
import uuid

import bootstrap

COMMIT_MARGIN = 45
PROBE_SECONDS = 180
CHECKS = ('edge_tls', 'edge_cloudflare', 'xhttp_https', 'vps_egress',
          'wrong_uuid_rejected', 'valid_again', 'reconnect', 'hysteria2_preserved')
# Cloudflare's published public IPv4 ranges: https://www.cloudflare.com/ips-v4/
CF_NETWORKS = tuple(ipaddress.ip_network(net) for net in (
    '173.245.48.0/20', '103.21.244.0/22', '103.22.200.0/22', '103.31.4.0/22',
    '141.101.64.0/18', '108.162.192.0/18', '190.93.240.0/20', '188.114.96.0/20',
    '197.234.240.0/22', '198.41.128.0/17', '162.158.0.0/15', '104.16.0.0/13',
    '104.24.0.0/14', '172.64.0.0/13', '131.0.72.0/22'))


class Error(ValueError):
    """Probe errors never include native credentials, source YAML, or URIs."""


class Rejected(Error):
    """An established fresh client explicitly denied/closed the inner connection."""


class Budget:
    def __init__(self, deadline):
        if type(deadline) not in (float, int) or not time.time() < deadline <= time.time() + 905:
            raise Error('Missing, expired, or invalid absolute candidate deadline')
        self.end = time.monotonic() + (deadline - time.time())

    def left(self, maximum=12):
        remaining = self.end - time.monotonic()
        if remaining <= 0:
            raise Error('CDN acceptance deadline expired; recover the transaction')
        return min(maximum, remaining)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def public_ipv4(value):
    try:
        ip = ipaddress.IPv4Address(value)
        if type(value) is not str or str(ip) != value or not ip.is_global or ip.is_multicast or ip.is_reserved:
            raise ValueError
        return value
    except ValueError:
        raise Error('CDN DNS and socket endpoints require public unicast IPv4 addresses') from None


def domain_name(value):
    try:
        bootstrap._domain(value)
    except bootstrap.BootstrapError:
        raise Error('CDN domain must be a DNS hostname') from None
    if value != value.lower():
        raise Error('CDN domain must use lowercase DNS labels')
    return value


def bound_socket(interface, kind, budget):
    sock = socket.socket(socket.AF_INET, kind)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode() + b'\0')
        sock.settimeout(budget.left())
        return sock
    except BaseException:
        sock.close()
        raise


def environment(binary, interface, dns_server):
    """Fail before remote intent if explicit physical underlay/tools are unavailable."""
    try:
        bootstrap._interface(interface)
        device = Path('/sys/class/net') / interface
        if not (device / 'device').exists() or (device / 'type').read_text().strip() != '1':
            raise Error('CDN probe requires an existing physical Ethernet/Wi-Fi interface')
        if not int((device / 'flags').read_text().strip(), 16) & 1:
            raise Error('CDN probe physical interface is down')
        public_ipv4(dns_server)
        bootstrap._yaml()
        strict_context()
        with bound_socket(interface, socket.SOCK_STREAM, Budget(time.time() + 5)):
            pass
        result = subprocess.run([binary, '-v'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5,
                                env=client_environment(), check=False)
        if result.returncode or not re.search(rb'\bv1\.19\.32\b', result.stdout):
            raise Error('CDN acceptance requires the supported Mihomo v1.19.32 executable')
    except (OSError, subprocess.TimeoutExpired, bootstrap.BootstrapError):
        raise Error('CDN prerequisites unavailable: PyYAML, Mihomo v1.19.32, physical interface and bind permission required') from None


def exact(sock, count, budget):
    data = bytearray()
    while len(data) < count:
        sock.settimeout(budget.left())
        part = sock.recv(count - len(data))
        if not part:
            raise Error('Truncated DNS/SOCKS handshake; authentication rejection is unproven')
        data.extend(part)
    return bytes(data)


def dns_name(packet, offset):
    labels, visited, end = [], set(), None
    while True:
        if offset >= len(packet) or offset in visited or len(visited) > 128:
            raise Error('Invalid public DNS response name')
        visited.add(offset)
        size = packet[offset]
        if size & 0xc0 == 0xc0:
            if offset + 1 >= len(packet):
                raise Error('Truncated public DNS response')
            end = end or offset + 2
            offset = ((size & 0x3f) << 8) | packet[offset + 1]
        elif size & 0xc0 or size > 63 or offset + 1 + size > len(packet):
            raise Error('Invalid public DNS response label')
        elif not size:
            return '.'.join(labels).lower(), end or offset + 1
        else:
            try:
                labels.append(packet[offset + 1:offset + 1 + size].decode('ascii'))
            except UnicodeError:
                raise Error('Invalid public DNS response encoding') from None
            offset += size + 1


def dns_answers(packet, transaction, domain):
    if len(packet) < 12:
        raise Error('Truncated public DNS response')
    tid, flags, questions, answers, _, _ = struct.unpack('!6H', packet[:12])
    if (tid != transaction or flags & 0x820f != 0x8000 or not flags & 0x0400 or
            flags & 0x7800 or questions != 1 or not 0 < answers <= 128):
        raise Error('DNS response must be fresh authoritative (AA), with matching identity/status')
    qname, offset = dns_name(packet, 12)
    if qname != domain or packet[offset:offset + 4] != b'\x00\x01\x00\x01':
        raise Error('Public DNS response question mismatch')
    offset += 4
    records, aliases = {}, {}
    for _ in range(answers):
        name, offset = dns_name(packet, offset)
        if offset + 10 > len(packet):
            raise Error('Truncated public DNS answer')
        kind, family, _, length = struct.unpack('!HHIH', packet[offset:offset + 10])
        offset += 10
        if offset + length > len(packet) or family != 1:
            raise Error('Invalid public DNS answer')
        if kind == 1:
            if length != 4:
                raise Error('Invalid public DNS IPv4 answer')
            records.setdefault(name, []).append(public_ipv4(socket.inet_ntoa(packet[offset:offset + 4])))
        elif kind == 5:
            alias, end = dns_name(packet, offset)
            if end != offset + length or name in aliases:
                raise Error('Ambiguous public DNS alias')
            aliases[name] = alias
        offset += length
    seen, current = set(), domain
    while current in aliases:
        if current in seen or current in records:
            raise Error('Cyclic/ambiguous public DNS alias')
        seen.add(current)
        current = aliases[current]
    ips = sorted(set(records.get(current, [])))
    if not ips:
        raise Error('Authoritative DNS returned no usable IPv4 answer')
    return ips


def resolve_a(domain, interface, dns_server, budget):
    domain, dns_server = domain_name(domain), public_ipv4(dns_server)
    transaction = secrets.randbelow(65536)
    question = b''.join(bytes([len(label)]) + label.encode('ascii') for label in domain.split('.')) + b'\0\0\x01\0\x01'
    packet = struct.pack('!6H', transaction, 0, 1, 0, 0, 0) + question
    # Explicit zone-authoritative CF NS, RD=0 and mandatory AA=1: a recursive
    # resolver may otherwise cache the preflight gray/origin answer after the
    # orange-cloud click. No discovery, TTL retry loop, system stub or fallback.
    with bound_socket(interface, socket.SOCK_STREAM, budget) as sock:
        sock.connect((dns_server, 53))
        sock.sendall(struct.pack('!H', len(packet)) + packet)
        length = struct.unpack('!H', exact(sock, 2, budget))[0]
        if not 12 <= length <= 32768:
            raise Error('Oversized public DNS response')
        return dns_answers(exact(sock, length, budget), transaction, domain)


def edge_addresses(ips, origin):
    if not ips or origin in ips or any(not any(ipaddress.ip_address(public_ipv4(ip)) in net for net in CF_NETWORKS) for ip in ips):
        raise Error('CDN DNS is gray, fake, private, or outside assigned Cloudflare IPv4 ranges')
    return ips


def strict_context():
    # Use normal OS CA paths, ignoring invocation-only SSL_CERT_FILE/DIR overrides.
    paths = ssl.get_default_verify_paths()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=paths.openssl_cafile, capath=paths.openssl_capath)
    context.set_alpn_protocols(['http/1.1'])
    return context


@contextlib.contextmanager
def operation_deadline(budget):
    """A wall-clock bound also covers TLS/header/body trickle reads."""
    current, expired = [], threading.Event()
    def interrupt():
        expired.set()
        if current:
            with contextlib.suppress(OSError):
                current[0].shutdown(socket.SHUT_RDWR)
    def watch(sock):
        current[:] = [sock]
        if expired.is_set():
            raise Error('HTTPS operation exceeded its finite deadline')
    timer = threading.Timer(budget.left(), interrupt)
    timer.daemon = True
    timer.start()
    try:
        yield watch
        if expired.is_set():
            raise Error('HTTPS operation exceeded its finite deadline')
    except BaseException:
        if expired.is_set():
            raise Error('HTTPS operation timed out; authentication rejection is unproven') from None
        raise
    finally:
        timer.cancel()
        timer.join()


def http_get(sock, name, path, budget, limit=262144):
    sock.settimeout(budget.left())
    sock.sendall(('GET ' + path + ' HTTP/1.1\r\nHost: ' + name +
                  '\r\nUser-Agent: quick-deploy-cdn-acceptance/1\r\nAccept: */*\r\n\r\n').encode('ascii'))
    response = http.client.HTTPResponse(sock)
    response.begin()
    headers = dict(response.getheaders())
    data = response.read(limit + 1)
    if len(data) > limit:
        raise Error('Public HTTPS probe response exceeds its size limit')
    return response.status, {key.lower(): value for key, value in headers.items()}, data


def edge_tls(domain, edge_ip, interface, budget):
    with operation_deadline(budget) as watch, bound_socket(interface, socket.SOCK_STREAM, budget) as sock:
        watch(sock)
        sock.connect((edge_ip, 443))
        with strict_context().wrap_socket(sock, server_hostname=domain, do_handshake_on_connect=False) as tls:
            watch(tls)
            tls.do_handshake()
            if tls.getpeername() != (edge_ip, 443):
                raise Error('Outer TLS socket endpoint differs from fresh pinned Cloudflare answer')
            status, headers, data = http_get(tls, domain, '/cdn-cgi/trace', budget, 16384)
            trace = dict(line.split('=', 1) for line in data.decode('ascii').splitlines() if '=' in line)
            if (status != 200 or headers.get('server', '').lower() != 'cloudflare' or
                    not re.fullmatch(r'[0-9a-fA-F]+-[A-Za-z0-9]+', headers.get('cf-ray', '')) or
                    trace.get('h') != domain or not trace.get('tls', '').startswith('TLSv1.') or
                    trace.get('http') != 'http/1.1'):
                raise Error('Strict outer TLS response lacks matching Cloudflare trace evidence')
            return {'peer': edge_ip, 'port': 443, 'tls': tls.version(), 'http': trace['http']}


def native_nodes(profile_bytes, manifest, device, interface):
    try:
        yaml = bootstrap._yaml()
        profile = bootstrap._load_profile(profile_bytes, yaml)
        nodes = profile['proxies']
        if len(nodes) != 2 or {node.get('type') for node in nodes} != {'vless', 'hysteria2'}:
            raise Error('CDN candidate must contain exactly the two native protocol mappings')
        h2 = bootstrap._native_node(profile, manifest['endpoint'], 443, manifest['config']['domain'], interface)
        cdn = copy.deepcopy(next(node for node in nodes if node['type'] == 'vless'))
        account, target = manifest['accounts'][device], manifest['tcp_cdn']
        if (type(target.get('schema')) is not int or target['schema'] != 1 or
                target.get('path') != '/qd-xhttp/' or target.get('mode') != 'packet-up' or h2.get('alpn') != ['h3'] or
                not account['enable'] or cdn.get('name') != 'SELF-cdn' or h2['name'] != 'SELF-h2' or
                cdn.get('uuid') != account['id'] or h2['password'] != account['auth'] or
                cdn.get('server') != target['domain'] or cdn.get('port') != 443 or cdn.get('tls') is not True or
                cdn.get('network') != 'xhttp' or cdn.get('servername') != target['domain'] or
                cdn.get('alpn') != ['h2'] or cdn.get('flow') not in (None, '') or
                cdn.get('xhttp-opts') != {'path': target['path'], 'host': target['domain'], 'mode': target['mode']}):
            raise Error('Native CDN/H2 identity, TLS, or XHTTP fields disagree with candidate archive')
        for key in ('skip-cert-verify', 'insecure', 'disable-sni'):
            if key in cdn and cdn[key] is not False:
                raise Error('Native CDN requires normal CA and hostname verification')
        for key in ('ca', 'ca-str', 'fingerprint', 'dialer-proxy', 'ip-version', 'reality-opts', 'ws-opts', 'http-opts'):
            if key in cdn and cdn[key] not in (None, ''):
                raise Error('Native CDN has an unsupported trust/outer-dial override')
        for key in ('sni', 'name-cert-verify'):
            if key in cdn and cdn[key] != target['domain']:
                raise Error('Native CDN has conflicting TLS names')
        for node in (cdn, h2):
            if ('interface-name' in node and node['interface-name'] != interface) or node.get('routing-mark'):
                raise Error('Native node conflicts with required physical interface')
        identity = hashlib.sha256(canonical({'device': device, 'id': account['id'], 'auth': account['auth']})).hexdigest()
        return cdn, h2, identity
    except (bootstrap.BootstrapError, KeyError, TypeError, StopIteration, AttributeError):
        raise Error('Invalid complete native CDN/H2 profile or candidate identity') from None


def client_environment():
    removed = {'ssl_cert_file', 'ssl_cert_dir', 'https_proxy', 'http_proxy', 'all_proxy', 'no_proxy'}
    return {key: value for key, value in os.environ.items() if key.lower() not in removed}


def private_ports():
    with socket.socket() as first, socket.socket() as second:
        first.bind(('127.0.0.1', 0))
        second.bind(('127.0.0.1', 0))
        return first.getsockname()[1], second.getsockname()[1]


def runtime(node, domain, edge_ip, interface, dns_server, mixed, controller, secret):
    return {'mixed-port': mixed, 'bind-address': '127.0.0.1', 'allow-lan': False,
            'external-controller': '127.0.0.1:' + str(controller), 'secret': secret,
            'mode': 'rule', 'log-level': 'info', 'ipv6': False, 'interface-name': interface,
            'tun': {'enable': False}, 'profile': {'store-selected': False, 'store-fake-ip': False},
            'hosts': {domain: edge_ip},
            'dns': {'enable': True, 'ipv6': False, 'enhanced-mode': 'redir-host', 'use-hosts': True,
                    'use-system-hosts': False, 'nameserver': [dns_server], 'default-nameserver': [dns_server],
                    'proxy-server-nameserver': [dns_server], 'respect-rules': False},
            'proxies': [copy.deepcopy(node)],
            'proxy-groups': [{'name': 'PROXY', 'type': 'select', 'proxies': [node['name']]}],
            'rules': ['MATCH,PROXY']}


def socket_peers(pid):
    """Actual TCP endpoints owned by this temporary process, not controller claims."""
    inodes = set()
    for fd in Path('/proc', str(pid), 'fd').iterdir():
        try:
            match = re.fullmatch(r'socket:\[([0-9]+)\]', os.readlink(fd))
            if match:
                inodes.add(match[1])
        except FileNotFoundError:
            pass
    peers = set()
    for line in Path('/proc', str(pid), 'net/tcp').read_text().splitlines()[1:]:
        fields = line.split()
        if fields[9] in inodes and fields[3] == '01':
            address, port = fields[2].split(':')
            peers.add((socket.inet_ntoa(bytes.fromhex(address)[::-1]), int(port, 16)))
    return peers


class Client:
    def __init__(self, binary, node, domain, edge_ip, interface, dns_server, budget):
        self.binary, self.node, self.budget = binary, node, budget
        self.mixed, self.controller = private_ports()
        self.secret = secrets.token_hex(32)
        self.config = runtime(node, domain, edge_ip, interface, dns_server, self.mixed, self.controller, self.secret)
        self.process = None

    def control(self, method, path, payload=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.controller, timeout=self.budget.left(10))
        try:
            connection.request(method, path, body=json.dumps(payload).encode() if payload is not None else None,
                               headers={'Authorization': 'Bearer ' + self.secret, 'Content-Type': 'application/json'})
            response = connection.getresponse()
            data = response.read(65537)
            if len(data) > 65536:
                raise Error('Owned Mihomo controller response exceeds limit')
            return response.status, data
        finally:
            connection.close()

    def __enter__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='qd-cdn-client-')
        folder = Path(self.temporary.name)
        folder.chmod(0o700)
        config = folder / 'config.json'
        with config.open('xb') as out:
            os.fchmod(out.fileno(), 0o600)
            out.write(canonical(self.config))
        ready = threading.Event()
        try:
            self.process = subprocess.Popen([self.binary, '-d', str(folder), '-f', str(config)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                env=client_environment(), start_new_session=True)
            def drain():
                markers = set()
                while chunk := self.process.stdout.readline(65536):
                    if ('RESTful API listening at: 127.0.0.1:' + str(self.controller)).encode() in chunk:
                        markers.add('controller')
                    if ('proxy listening at: 127.0.0.1:' + str(self.mixed)).encode() in chunk:
                        markers.add('mixed')
                    if len(markers) == 2:
                        ready.set()
            self.reader = threading.Thread(target=drain, daemon=True)
            self.reader.start()
            if not ready.wait(self.budget.left(10)) or self.process.poll() is not None:
                raise Error('Owned Mihomo failed to open its private listeners within startup budget')
            # Listener/selector state precedes tunnel.OnRunning in v1.19.32.
            # Identical config PUT is serialized by executor.ApplyConfig; 204 is
            # emitted only after OnRunning. Never target the desktop controller.
            status, _ = self.control('PUT', '/configs', {'path': str(config)})
            if status != 204:
                raise Error('Owned Mihomo synchronous ApplyConfig readiness barrier failed')
            status, data = self.control('GET', '/proxies/PROXY')
            group = json.loads(data)
            if status != 200 or group.get('now') != self.node['name'] or group.get('all') != [self.node['name']]:
                raise Error('Owned Mihomo selected/all native node does not match acceptance target')
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.process is not None:
            if self.process.poll() is None:
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                    self.process.wait(timeout=3)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=3)
            if hasattr(self, 'reader'):
                self.reader.join(timeout=3)
            self.process.stdout.close()
        self.temporary.cleanup()

    def https(self, name, path='/', edge_ip=None):
        start = time.monotonic()
        socks_accepted = False
        try:
            with operation_deadline(self.budget) as watch, socket.create_connection(('127.0.0.1', self.mixed), timeout=self.budget.left()) as sock:
                watch(sock)
                sock.sendall(b'\x05\x01\x00')
                if exact(sock, 2, self.budget) != b'\x05\x00':
                    raise Error('Owned SOCKS listener did not accept no-auth handshake')
                address = name.encode('ascii')
                sock.sendall(b'\x05\x01\x00\x03' + bytes([len(address)]) + address + b'\x01\xbb')
                header = exact(sock, 4, self.budget)
                if header[0] != 5 or header[2] != 0:
                    raise Error('Owned SOCKS listener returned malformed CONNECT')
                if header[1] != 0:
                    raise Rejected('Fresh proxy denied inner HTTPS CONNECT')
                length = {1: 4, 4: 16}.get(header[3])
                if header[3] == 3:
                    length = exact(sock, 1, self.budget)[0]
                if not length:
                    raise Error('Owned SOCKS listener returned invalid address type')
                exact(sock, length + 2, self.budget)
                socks_accepted = True
                with strict_context().wrap_socket(sock, server_hostname=name, do_handshake_on_connect=False) as tls:
                    watch(tls)
                    tls.do_handshake()
                    status, _, data = http_get(tls, name, path, self.budget)
                    if status != 200 or not data:
                        raise Error('Authenticated inner public HTTPS did not return a successful body')
                    if edge_ip is not None and (edge_ip, 443) not in socket_peers(self.process.pid):
                        raise Error('Native XHTTP process lacks an actual pinned Cloudflare TCP443 socket')
                    return data, round(time.monotonic() - start, 6)
        except ssl.SSLCertVerificationError:
            raise Error('Inner public HTTPS CA/hostname verification failed') from None
        except (ssl.SSLEOFError, ConnectionResetError, BrokenPipeError):
            if not socks_accepted:
                raise Error('Owned SOCKS handshake closed; authentication rejection is unproven') from None
            raise Rejected('Fresh proxy closed inner HTTPS before authenticated completion') from None
        except (TimeoutError, socket.timeout):
            raise Error('Inner HTTPS timed out; authentication rejection is unproven') from None


def validate_cdn_candidate(*, profile_bytes, manifest, before_manifest, device, request,
                           request_fingerprint, candidate_sha256, interface, dns_server, binary, deadline, observations=None):
    """Return exact schema1 successful attestation, never partial proof or raw nodes."""
    try:
        budget = Budget(min(deadline - COMMIT_MARGIN, time.time() + PROBE_SECONDS))
        domain = domain_name(manifest['tcp_cdn']['domain'])
        if (request.get('feature') != 'tcp-cdn' or request.get('tcp_cdn') != {'schema': 1, 'domain': domain} or
                not re.fullmatch(r'[0-9a-f]{32}', request.get('operation_id', '')) or
                not re.fullmatch(r'[0-9a-f]{64}', request_fingerprint) or
                not re.fullmatch(r'[0-9a-f]{64}', candidate_sha256)):
            raise Error('CDN proof request/operation/candidate binding is missing or malformed')
        cdn, h2, identity = native_nodes(profile_bytes, manifest, device, interface)
        if (manifest['endpoint'] != before_manifest['endpoint'] or manifest['hysteria2'] != before_manifest['hysteria2'] or
                any(manifest['accounts'][device][key] != before_manifest['accounts'][device][key] for key in ('id', 'auth', 'subId'))):
            raise Error('Candidate changed original Hysteria2/account identity')
        ips = edge_addresses(resolve_a(domain, interface, dns_server, budget), manifest['endpoint'])
        edge_ip = ips[0]
        edge = edge_tls(domain, edge_ip, interface, budget)
        samples = [] if observations is None else observations
        samples.append({'check': 'outer_cloudflare', **edge})
        def client(node):
            return Client(binary, node, domain, edge_ip, interface, dns_server, budget)
        def https(active, name, edge=None):
            body, elapsed = active.https(name, '/', edge)
            samples.append({'check': 'inner_https', 'transport': active.node['type'], 'site': name, 'seconds': elapsed})
            return body
        def egress(active, edge=None):
            body = https(active, 'api.ipify.org', edge)
            if body.decode('ascii').strip() != manifest['endpoint']:
                raise Error('Authenticated proxy egress differs from expected VPS public endpoint')
        with client(cdn) as active:
            https(active, 'www.python.org', edge_ip)
            egress(active, edge_ip)
        wrong = copy.deepcopy(cdn)
        wrong['uuid'] = str(uuid.uuid4())
        if wrong['uuid'] == cdn['uuid']:
            raise Error('Negative authentication identity was not changed')
        with client(wrong) as active:
            try:
                active.https('www.python.org', '/', edge_ip)
            except Rejected:
                pass
            else:
                raise Error('Wrong UUID was accepted; CDN authentication proof failed')
        with client(cdn) as active:
            egress(active, edge_ip)
        # Deliberate short idle measurement, not a startup sleep or until-pass retry.
        idle = 2
        if budget.left(idle + 1) <= idle:
            raise Error('Insufficient acceptance time for idle/fresh-client check')
        select.select([], [], [], idle)
        with client(cdn) as active:
            https(active, 'www.python.org', edge_ip)
            egress(active, edge_ip)
        with client(h2) as active:
            https(active, 'www.python.org')
            egress(active)
        budget.left()
        return {'schema': 1, 'operation_id': request['operation_id'], 'request_fingerprint': request_fingerprint,
                'candidate_sha256': candidate_sha256, 'domain': domain, 'device_identity_sha256': identity,
                'observed_at': datetime.now(timezone.utc).isoformat(), 'interface': interface,
                'edge_ips': [edge_ip], 'checks': dict.fromkeys(CHECKS, True)}
    except Error:
        raise
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException, subprocess.SubprocessError):
        raise Error('CDN acceptance failed at DNS/TLS/native-client boundary; no successful proof recorded') from None
