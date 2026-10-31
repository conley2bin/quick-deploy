"""Loopback-only carrier and orchestration tests; never contact a VPS."""
import argparse
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import random
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import host
import ssh_proxy
import vps

ENDPOINT = '93.184.216.34'
HELPER = Path(ssh_proxy.__file__)


def exact(conn, size):
    result = b''
    while len(result) < size:
        chunk = conn.recv(size - len(result))
        if not chunk:
            raise AssertionError('unexpected EOF in mock proxy')
        result += chunk
    return result


@contextlib.contextmanager
def server(handler):
    errors = []
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener.settimeout(3)
        port = listener.getsockname()[1]
        def run():
            try:
                with listener.accept()[0] as conn:
                    conn.settimeout(3)
                    handler(conn)
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        try:
            yield port
        finally:
            thread.join(4)
            if thread.is_alive():
                raise AssertionError('mock proxy did not finish')
            if errors:
                raise errors[0]


def negotiate(conn, scheme, banner=b''):
    if scheme == 'socks5':
        if exact(conn, 3) != b'\x05\x01\x00':
            raise AssertionError('wrong no-auth greeting')
        conn.sendall(b'\x05\x00')
        expected = b'\x05\x01\x00\x01' + socket.inet_aton(ENDPOINT) + (2222).to_bytes(2, 'big')
        if exact(conn, 10) != expected:
            raise AssertionError('destination was not pinned numeric IPv4:2222')
        conn.sendall(b'\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x01' + banner)
    else:
        header = b''
        while not header.endswith(b'\r\n\r\n'):
            header += exact(conn, 1)
        expected = f'CONNECT {ENDPOINT}:2222 HTTP/1.1\r\nHost: {ENDPOINT}:2222\r\n\r\n'.encode()
        if header != expected:
            raise AssertionError('unexpected CONNECT request/auth header')
        conn.sendall(b'HTTP/1.1 200 Connection established\r\nX-Test: ok\r\n\r\n' + banner)


class Helper(unittest.TestCase):
    def test_strict_uri_grammar_and_injections(self):
        for scheme in ('http', 'socks5'):
            for port in (1, 10808, 65535):
                parsed = ssh_proxy.parse_proxy(f'{scheme}://127.0.0.1:{port}')
                self.assertEqual((parsed.scheme, parsed.port), (scheme, port))
        bad = ['http://127.0.0.1:0', 'http://127.0.0.1:65536', 'http://127.0.0.1:-1',
               'http://127.0.0.1:+80', 'http://127.0.0.1:8.0', 'http://127.0.0.1:８０',
               'https://127.0.0.1:80', 'socks5h://127.0.0.1:80', 'HTTP://127.0.0.1:80',
               'http://localhost:80', 'http://127.1:80', 'http://[::1]:80', 'http://127.0.0.2:80',
               'http://93.184.216.34:80', 'http://user:secret@127.0.0.1:80', 'http://127.0.0.1:80/',
               'http://127.0.0.1:80?', 'http://127.0.0.1:80#', 'http://127.0.0.1:80%20',
               'http://127.0.0.1:80;id', 'http://127.0.0.1:80$(id)', 'http://127.0.0.1:80\n',
               ' http://127.0.0.1:80', 'http://127.0.0.1:80 ', 'http://127.0.0.1:80\t',
               'http://127.0.0.1:80\x00', 'http://127.0.0.1:80/../x', 'http://%31%32%37.0.0.1:80']
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ssh_proxy.Error):
                ssh_proxy.parse_proxy(value)
        for address, port in [('127.0.0.1', 22), ('localhost', 22), ('::1', 22), (ENDPOINT, 0), (ENDPOINT, True)]:
            with self.assertRaises(ssh_proxy.Error):
                ssh_proxy.destination(address, port)

    def test_actual_socks_http_full_duplex_stdin_halfclose(self):
        payload = bytes(range(256)) * 4096
        banner = b'SSH-2.0-prefetched-test\r\n'
        for scheme in ('socks5', 'http'):
            received = bytearray()
            def handle(conn):
                negotiate(conn, scheme, banner)
                # Echo concurrently, larger than the relay buffers/pipe capacity.
                while chunk := conn.recv(65536):
                    received.extend(chunk)
                    conn.sendall(chunk)
                conn.sendall(b'AFTER-STDIN-EOF')
                conn.shutdown(socket.SHUT_WR)
            with self.subTest(scheme=scheme), server(handle) as port:
                result = subprocess.run([sys.executable, str(HELPER), f'{scheme}://127.0.0.1:{port}', ENDPOINT, '2222'],
                                        input=payload, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, banner + payload + b'AFTER-STDIN-EOF')
                self.assertEqual(bytes(received), payload)
                self.assertEqual(result.stderr, b'')

    def test_remote_halfclose_exposes_stdout_eof_but_still_sends_stdin(self):
        for scheme in ('socks5', 'http'):
            received = bytearray()
            def handle(conn):
                negotiate(conn, scheme, b'SSH-2.0-half-close\r\n')
                conn.shutdown(socket.SHUT_WR)
                while chunk := conn.recv(4096):
                    received.extend(chunk)
            with self.subTest(scheme=scheme), server(handle) as port:
                with subprocess.Popen([sys.executable, str(HELPER), f'{scheme}://127.0.0.1:{port}', ENDPOINT, '2222'],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
                    # EOF must arrive before closing our stdin (no deadlock).
                    output = []
                    reader = threading.Thread(target=lambda: output.append(proc.stdout.read()), daemon=True)
                    reader.start()
                    reader.join(2)
                    self.assertFalse(reader.is_alive(), 'network half-close did not close stdout')
                    self.assertEqual(output, [b'SSH-2.0-half-close\r\n'])
                    proc.stdin.write(b'AFTER-REMOTE-EOF')
                    proc.stdin.close()
                    self.assertEqual(proc.wait(timeout=3), 0, proc.stderr.read())
            self.assertEqual(received, b'AFTER-REMOTE-EOF')

    def test_http_prefetched_banner_retained_by_handshake(self):
        banner = b'SSH-2.0-immediate\r\n'
        with server(lambda conn: negotiate(conn, 'http', banner)) as port:
            sock, prefetched = ssh_proxy.connect_tunnel(ssh_proxy.parse_proxy(f'http://127.0.0.1:{port}'), ENDPOINT, 2222)
            with sock:
                # The header and banner were emitted by one sendall; bytes
                # received with headers must not be discarded.
                remainder = prefetched
                while len(remainder) < len(banner):
                    remainder += sock.recv(4096)
                self.assertEqual(remainder, banner)

    def test_malformed_denied_truncated_oversized_are_visible(self):
        cases = [('socks5', b'\x05\xff', 'no-authentication'),
                 ('socks5', b'\x04\x00', 'no-authentication'),
                 ('socks5', b'\x05', 'truncated'),
                 ('http', b'HTTP/1.1 403 Forbidden\r\n\r\n', 'denied'),
                 ('http', b'garbage\r\n\r\n', 'Malformed'),
                 ('http', b'HTTP/1.1 200 OK\r\nBad Header\r\n\r\n', 'Malformed'),
                 ('http', b'HTTP/1.1 200 OK\r\nX:' + b'x' * ssh_proxy.MAX_HEADER, 'oversized'),
                 ('http', b'HTTP/1.1 200 OK\r\n', 'truncated')]
        for scheme, response, error in cases:
            def handle(conn):
                conn.recv(4096)
                conn.sendall(response)
            with self.subTest(scheme=scheme, error=error), server(handle) as port:
                result = subprocess.run([sys.executable, str(HELPER), f'{scheme}://127.0.0.1:{port}', ENDPOINT, '2222'],
                                        input=b'PRIVATE-SENTINEL', capture_output=True, timeout=3)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, b'')
                self.assertIn(error.encode(), result.stderr)
                self.assertNotIn(b'PRIVATE-SENTINEL', result.stderr)

    def test_socks_connect_reply_validation(self):
        replies = [b'\x05\x02\x00\x01', b'\x04\x00\x00\x01', b'\x05\x00\x01\x01',
                   b'\x05\x00\x00\x09', b'\x05\x00\x00\x03\x00', b'\x05\x00\x00\x01\x00']
        for reply in replies:
            def handle(conn):
                exact(conn, 3)
                conn.sendall(b'\x05\x00')
                exact(conn, 10)
                conn.sendall(reply)
            with self.subTest(reply=reply), server(handle) as port:
                with self.assertRaises(ssh_proxy.Error):
                    ssh_proxy.connect_tunnel(ssh_proxy.parse_proxy(f'socks5://127.0.0.1:{port}'), ENDPOINT, 2222)

    def test_timeout_and_refusal_never_connect_direct(self):
        for scheme in ('socks5', 'http'):
            with server(lambda conn: (conn.recv(4096), time.sleep(.15))) as port:
                with self.assertRaises(TimeoutError):
                    ssh_proxy.connect_tunnel(ssh_proxy.parse_proxy(f'{scheme}://127.0.0.1:{port}'), ENDPOINT, 2222, timeout=.04)
        fake = mock.MagicMock()
        fake.connect.side_effect = ConnectionRefusedError('mock refusal')
        with mock.patch.object(ssh_proxy.socket, 'socket', return_value=fake) as factory:
            with self.assertRaises(ConnectionRefusedError):
                ssh_proxy.connect_tunnel(ssh_proxy.parse_proxy('socks5://127.0.0.1:10808'), ENDPOINT, 2222)
        factory.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM)
        fake.connect.assert_called_once_with(('127.0.0.1', 10808))
        fake.close.assert_called_once()
        with mock.patch.object(ssh_proxy, 'connect_tunnel', side_effect=TimeoutError), mock.patch('sys.stderr', new_callable=io.StringIO) as err:
            self.assertEqual(ssh_proxy.main(['http://127.0.0.1:10808', ENDPOINT, '2222']), 1)
        self.assertIn('timed out', err.getvalue())

    def test_actual_openssh_percent_expansion_with_quoted_helper_paths(self):
        seen = []
        def handle(conn):
            negotiate(conn, 'socks5', b'SSH-2.0-local-mock\r\n')
            banner = b''
            while not banner.endswith(b'\n'):
                banner += exact(conn, 1)
            seen.append(banner)
        with tempfile.TemporaryDirectory(prefix='qd helper %h ') as tmp, server(handle) as port:
            root = Path(tmp)
            helper = root / "quoted '%p helper.py"
            helper.write_bytes(HELPER.read_bytes())
            python = root / 'python %h executable'
            python.symlink_to(sys.executable)
            command = ssh_proxy.proxy_command(ssh_proxy.parse_proxy(f'socks5://127.0.0.1:{port}'), ENDPOINT, 2222,
                                              executable=python, helper=helper)
            # The real OpenSSH process reaches only the local mock through our
            # helper. The mock closes after banner, before key exchange/auth.
            result = subprocess.run(['ssh', '-F', '/dev/null', '-T', '-p', '2222', '-o', 'BatchMode=yes',
                                     '-o', 'StrictHostKeyChecking=yes', '-o', 'ProxyJump=none',
                                     '-o', 'ConnectTimeout=2', '-o', 'ProxyCommand=' + command, 'fixture@' + ENDPOINT],
                                    input=b'', capture_output=True, timeout=4)
            self.assertEqual(result.returncode, 255)
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0].startswith(b'SSH-2.0-OpenSSH'), seen)

    def test_crafted_shell_quotes_spaces_quotes_percent_and_no_missing_helper_fallback(self):
        with tempfile.TemporaryDirectory(prefix='qd proxy % ') as tmp:
            root = Path(tmp)
            helper = root / "helper '%h ; echo BAD.py"
            helper.write_text('import json,sys;print(json.dumps(sys.argv[1:]))')
            python = root / 'python %p executable'
            python.symlink_to(sys.executable)
            command = ssh_proxy.proxy_command(ssh_proxy.parse_proxy('http://127.0.0.1:1234'), ENDPOINT, 2222,
                                              executable=python, helper=helper)
            # OpenSSH expands %% before shell evaluation; no user %-tokens exist.
            self.assertNotIn('%h', command.replace('%%', ''))
            result = subprocess.run(['/bin/sh', '-c', command.replace('%%', '%')], capture_output=True, check=True)
            self.assertEqual(json.loads(result.stdout), ['http://127.0.0.1:1234', ENDPOINT, '2222'])
            self.assertNotIn(b'BAD\n', result.stdout)
            with self.assertRaisesRegex(ssh_proxy.Error, 'unavailable'):
                ssh_proxy.proxy_command(ssh_proxy.parse_proxy('http://127.0.0.1:1234'), ENDPOINT, 22, helper=root / 'missing')


class Transport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='qd-proxy-cli-')
        self.addCleanup(self.temp.cleanup)
        self.state = vps.private_dir(Path(self.temp.name) / 'state')
        self.record = {'target': 'verified-alias', 'endpoint': ENDPOINT, 'user': 'root', 'port': 2222,
                       'options': list(vps.SSH_OPTIONS) + ['-p', '2222', '-i', '/fixture/nondefault key', '-o', 'IdentitiesOnly=yes'],
                       'machine_id': 'a' * 32}
        self.config = {'domain': 'sub.example.org', 'reality_target': 'target.example.org', 'acme_email': '', 'proxy_port': 443}
        self.doc = {'format': host.FORMAT, 'owner': 'fixture-owner', 'config': self.config,
                    'current': copy.deepcopy(self.record), 'previous': [], 'backup': None}
        self.archive = self.state / 'offnode.tar'
        host.atomic(self.archive, 'local fixture archive')
        self.doc['backup'] = str(self.archive)
        self.calls = []

    def process(self, argv, **kwargs):
        if '-G' in argv:
            return subprocess.CompletedProcess(argv, 0, f'hostname {ENDPOINT}\nuser root\nport 2222\n'.encode(), b'')
        self.calls.append((argv, kwargs))
        action = shlex.split(argv[-1])[-1]
        if action == 'devices-list':
            response = {'home': {'enabled': True}}
        elif action in ('prepare', 'devices-prepare'):
            response = {'unchanged': action == 'prepare'}
        elif action == 'abort':
            response = {'committed': False}
        else:
            response = {'machine_id': 'a' * 32, 'adapter_sha256': host.digest(vps.MODULE / 'lib/host.py'),
                        'transports': ['tcp-reality', 'hysteria2'], 'needs_transport_upgrade': False,
                        'devices': ['home'], 'rename_target_has_history': False}
        return subprocess.CompletedProcess(argv, 0, host.dumps(response).encode(), b'')

    def assert_carrier(self, url):
        self.assertTrue(self.calls)
        for argv, kwargs in self.calls:
            values = [argv[i + 1] for i, value in enumerate(argv[:-1]) if value == '-o']
            proxy = [value for value in values if value.startswith('ProxyCommand=')]
            self.assertEqual(len(proxy), 1)
            if url is None:
                self.assertEqual(proxy, ['ProxyCommand=none'])
            else:
                self.assertIn(url, proxy[0])
                self.assertNotIn('ProxyCommand=none', values)
                self.assertEqual(shlex.split(proxy[0].split('=', 1)[1])[-2:], [ENDPOINT, '2222'])
                self.assertLess(argv.index(proxy[0]), argv.index('StrictHostKeyChecking=yes'))
            for value in ('HostName=' + ENDPOINT, 'User=root', 'Port=2222', 'StrictHostKeyChecking=yes',
                          'ControlMaster=no', 'ControlPath=none', 'ClearAllForwardings=yes', 'ProxyJump=none', 'IdentitiesOnly=yes'):
                self.assertIn(value, values)
            self.assertEqual(argv[argv.index('-p') + 1], '2222')
            self.assertIn('/fixture/nondefault key', argv)
            self.assertFalse(kwargs['shell'])

    def test_large_public_source_packs_below_single_argv_and_executes_with_private_stdin(self):
        with tempfile.TemporaryDirectory(prefix='qd-public-source-') as tmp:
            root = Path(tmp)
            (root / 'lib').mkdir()
            source = (b'# public adapter padding with quotes and percent %h ' * 10000 + b'\n' +
                      b'import json,os,sys\nprint(json.dumps({"argv":sys.argv,"connection":os.environ["QD_SSH_CONNECTION"],"stdin":sys.stdin.read(),"name":__name__}))\n')
            path = root / 'lib/host.py'
            path.write_bytes(source)
            digest = host.digest(path)
            with mock.patch.object(vps, 'MODULE', root):
                command = vps.remote_command(self.record, 'prepare')
            self.assertGreater(len(source), 128 * 1024)
            self.assertLess(len(command.encode()), 4096)
            self.assertNotIn('PRIVATE-STDIN-SENTINEL', command)
            tokens = shlex.split(command)
            bootstrap = tokens[tokens.index('-c') + 1]
            # Inspect exact packed public bytes, then exercise the actual shell,
            # Python -c/action argv, inherited SSH_CONNECTION and private stdin.
            encoded = bootstrap.split('base64.b64decode(', 1)[1].split(')', 1)[0]
            import ast
            self.assertEqual(vps.zlib.decompress(vps.base64.b64decode(ast.literal_eval(encoded))), source)
            connection = '198.51.100.1 45678 ' + ENDPOINT + ' 2222'
            result = subprocess.run(['/bin/sh', '-c', command], input=b'PRIVATE-STDIN-SENTINEL', capture_output=True,
                                    env=dict(os.environ, SSH_CONNECTION=connection), timeout=3, check=True)
            output = json.loads(result.stdout)
            self.assertEqual(output, {'argv': ['-c', 'prepare'], 'connection': connection,
                                      'stdin': 'PRIVATE-STDIN-SENTINEL', 'name': '__main__'})
            self.assertEqual(host.digest(path), digest, 'packing changed uncompressed adapter SHA')
        actual = vps.remote_command(self.record, 'status')
        self.assertLess(len(actual.encode()), 120 * 1024)

    def test_uncompressible_public_source_refuses_before_private_process(self):
        with tempfile.TemporaryDirectory(prefix='qd-public-source-limit-') as tmp:
            root = Path(tmp)
            (root / 'lib').mkdir()
            (root / 'lib/host.py').write_text('# ' + random.Random(0).randbytes(160000).hex())
            with mock.patch.object(vps, 'MODULE', root), mock.patch.object(vps.subprocess, 'run') as process:
                with self.assertRaisesRegex(host.Error, 'single-argument limit'):
                    vps.remote(self.record, 'prepare', {'private': 'NEVER-SENT'})
                process.assert_not_called()

    def test_every_remote_parser_and_local_export_rejection(self):
        commands = [['deploy'], ['preflight'], ['replace', '--ssh', 'new-alias'], ['restore', '--ssh', 'new-alias'],
                    ['status'], ['backup'], ['recover'], ['devices', 'list'], ['devices', 'add', 'tablet'],
                    ['devices', 'remove', 'home'], ['devices', 'rename', 'home', 'tablet'], ['devices', 'reset', 'home']]
        for args in commands:
            parsed = vps.parser().parse_args(args + ['--ssh-proxy', 'socks5://127.0.0.1:10808'])
            self.assertEqual(parsed.ssh_proxy.port, 10808)
        with mock.patch('sys.stderr', new_callable=io.StringIO):
            for args in (['export', '--ssh-proxy', 'http://127.0.0.1:10808'], ['status', '--password', 'secret']):
                with self.assertRaises(SystemExit):
                    vps.parser().parse_args(args)
        with mock.patch('sys.stderr', new_callable=io.StringIO) as error:
            with self.assertRaises(SystemExit):
                vps.parser().parse_args(['status', '--ssh-proxy', 'http://user:PRIVATE-PASSWORD@127.0.0.1:10808'])
        self.assertNotIn('PRIVATE-PASSWORD', error.getvalue())

    def test_all_saved_current_cli_paths_capture_argv_and_reset_invocation(self):
        cases = [['preflight'], ['deploy'], ['status'], ['backup'], ['devices', 'list'], ['devices', 'add', 'tablet'],
                 ['devices', 'remove', 'home'], ['devices', 'rename', 'home', 'tablet'], ['devices', 'reset', 'home']]
        for index, command in enumerate(cases):
            for scheme in ('socks5', 'http'):
                with self.subTest(command=command, scheme=scheme):
                    host.atomic(self.state / 'state.json', host.dumps(self.doc))
                    self.calls.clear()
                    url = f'{scheme}://127.0.0.1:{10808 + index}'
                    def backup(state, document, record):
                        vps.remote(record, 'backup', vps.request_for(document, record))
                        return self.archive
                    def validate(state, document, record, *args, **kwargs):
                        vps.remote(record, 'feed', {'private': 'PAYLOAD-SENTINEL'})
                    with mock.patch.object(vps.subprocess, 'run', side_effect=self.process), \
                         mock.patch.object(vps, 'backup_remote', side_effect=backup), \
                         mock.patch.object(vps, 'checked_backup', return_value={'node_id': 'fixture-node', 'published': True}), \
                         mock.patch.object(vps, 'validate_outputs', side_effect=validate), \
                         mock.patch.object(vps, 'resolve_mihomo', return_value='/fixture/mihomo'), \
                         mock.patch.object(vps, 'artifacts', return_value=self.state), \
                         mock.patch.object(vps, 'upload_bundle', return_value=io.BytesIO(b'ARCHIVE-SENTINEL')), \
                         mock.patch.object(vps, 'export_after_commit'), mock.patch.object(vps, 'retire_renamed_outputs'), \
                         mock.patch('sys.stdout', new_callable=io.StringIO):
                        # Deployed identity is explicit; the config.txt DOMAIN default would require migration.
                        explicit = ['--domain', self.config['domain'], '--ssh', self.record['target']] if command[0] in ('preflight', 'deploy') else []
                        result = vps.main(['--state-dir', str(self.state), *command, *explicit, '--ssh-proxy', url])
                    self.assertEqual(result, 0)
                    self.assert_carrier(url)
                    self.assertIsNone(vps.SSH_PROXY.get())
                    for argv, _ in self.calls:
                        self.assertNotIn('PAYLOAD-SENTINEL', ' '.join(argv))
                        self.assertNotIn('ARCHIVE-SENTINEL', ' '.join(argv))
                    saved = (self.state / 'state.json').read_text()
                    self.assertNotIn('127.0.0.1', saved)
                    self.assertNotIn('ssh_proxy.py', saved)
                    self.assertEqual(vps.load(self.state / 'state.json')['current']['options'], self.record['options'])
        # Same main() in the same interpreter, no flag: direct again.
        self.calls.clear()
        with mock.patch.object(vps.subprocess, 'run', side_effect=self.process), mock.patch('sys.stdout', new_callable=io.StringIO), \
             mock.patch.dict(os.environ, ALL_PROXY='http://untrusted.invalid:1', HTTP_PROXY='http://untrusted.invalid:2'):
            self.assertEqual(vps.main(['--state-dir', str(self.state), 'status']), 0)
        self.assert_carrier(None)

    def test_explicit_new_target_and_replacement_restore_use_same_carrier(self):
        for command in ('preflight', 'deploy', 'replace', 'restore'):
            with self.subTest(command=command):
                document = copy.deepcopy(self.doc)
                if command in ('replace', 'restore'):
                    document['current']['endpoint'] = '93.184.216.35'
                host.atomic(self.state / 'state.json', host.dumps(document))
                self.calls.clear()
                def backup(state, document, record):
                    vps.remote(record, 'backup', vps.request_for(document, record))
                    return self.archive
                args = ['--state-dir', str(self.state), command, '--ssh', 'verified-alias',
                        '--port', '2222', '--identity', '/fixture/nondefault key', '--ssh-proxy', 'http://127.0.0.1:10808']
                if command != 'restore':
                    # Deployed identity is explicit; the config.txt DOMAIN default would require migration.
                    args += ['--domain', self.config['domain']]
                if command == 'restore':
                    args += ['--backup', str(self.archive)]
                with mock.patch.object(vps.subprocess, 'run', side_effect=self.process), \
                     mock.patch.object(vps, 'inspect_backup', return_value={'owner': document['owner'], 'config': self.config}), \
                     mock.patch.object(vps, 'backup_remote', side_effect=backup), \
                     mock.patch.object(vps, 'validate_outputs'), mock.patch.object(vps, 'artifacts', return_value=self.state), \
                     mock.patch.object(vps, 'upload_bundle', return_value=io.BytesIO(b'archive')), \
                     mock.patch.object(vps, 'export_after_commit'), mock.patch('sys.stdout', new_callable=io.StringIO):
                    self.assertEqual(vps.main(args), 0)
                self.assert_carrier('http://127.0.0.1:10808')
                self.assertNotIn('127.0.0.1', (self.state / 'state.json').read_text())

    def test_pending_recovery_changes_carrier_not_bound_operation_or_request(self):
        request = dict(vps.request_for(self.doc, self.record), device_action='reset', names=['home'],
                       operation_id='b' * 32, adapter_sha256=host.digest(vps.MODULE / 'lib/host.py'))
        pending = {'mode': 'devices', 'record': self.record, 'request': request, 'before': str(self.archive), 'backup': None}
        host.atomic(self.state / 'state.json', host.dumps(self.doc))
        host.atomic(self.state / 'pending.json', host.dumps(pending))
        original = (self.state / 'pending.json').read_bytes()
        for url in ('http://127.0.0.1:10808', 'socks5://127.0.0.1:10809'):
            self.calls.clear()
            def waiting(argv, **kwargs):
                response = self.process(argv, **kwargs)
                if '-G' not in argv:
                    response.stdout = b'{"pending":true}'
                return response
            with mock.patch.object(vps.subprocess, 'run', side_effect=waiting), mock.patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(vps.main(['--state-dir', str(self.state), 'recover', '--ssh-proxy', url]), 0)
            self.assert_carrier(url)
            self.assertEqual((self.state / 'pending.json').read_bytes(), original)
            self.assertEqual(json.loads(self.calls[0][1]['input']), request)
        # Transport failure also resets the invocation and retains pending bytes.
        with mock.patch.object(vps.subprocess, 'run', side_effect=OSError('mock transport failure')), mock.patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(vps.main(['--state-dir', str(self.state), 'recover', '--ssh-proxy', url]), 1)
        self.assertIsNone(vps.SSH_PROXY.get())
        self.assertEqual((self.state / 'pending.json').read_bytes(), original)

    def test_nondevice_pending_records_work_without_current_and_do_not_persist_carrier(self):
        for mode in ('deploy', 'backup'):
            pending = {'mode': mode, 'record': self.record, 'backup': None}
            document = dict(self.doc, current=None)
            host.atomic(self.state / 'state.json', host.dumps(document))
            host.atomic(self.state / 'pending.json', host.dumps(pending))
            original = (self.state / 'pending.json').read_bytes()
            self.calls.clear()
            def waiting(argv, **kwargs):
                result = self.process(argv, **kwargs)
                if '-G' not in argv:
                    result.stdout = b'{"pending":true}'
                return result
            with mock.patch.object(vps.subprocess, 'run', side_effect=waiting), mock.patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(vps.main(['--state-dir', str(self.state), 'recover', '--ssh-proxy', 'http://127.0.0.1:10808']), 0)
            self.assert_carrier('http://127.0.0.1:10808')
            self.assertEqual((self.state / 'pending.json').read_bytes(), original)
            self.assertEqual(json.loads(self.calls[0][1]['input']), vps.request_for(document, self.record))

    def test_persisted_commands_rejected_before_any_process_or_private_stdin(self):
        for option in ('ProxyCommand=echo arbitrary', 'ProxyJump=untrusted', 'StrictHostKeyChecking=no', 'UserKnownHostsFile=/dev/null'):
            record = dict(self.record, options=['-o', option])
            with self.subTest(option=option), mock.patch.object(vps.subprocess, 'run') as process:
                with self.assertRaises(host.Error):
                    vps.remote(record, 'prepare', {'secret': 'SENTINEL'})
                process.assert_not_called()

    def test_alias_and_hostkeyalias_pinned_before_private_stdin(self):
        with tempfile.TemporaryDirectory(prefix='qd-fake-ssh-proxy-') as tmp:
            root = Path(tmp)
            alias = root / 'alias.json'
            alias.write_text(json.dumps({'host': ENDPOINT, 'user': 'root', 'port': '2222', 'key': 'original-key-name'}))
            script = root / 'ssh'
            script.write_text('#!' + sys.executable + '\n' + '''import json,os,pathlib,sys
root=pathlib.Path(os.environ['SSH_FIXTURE']); selected=json.loads((root/'alias.json').read_text()); args=sys.argv[1:]; opts={}; i=0
while i<len(args):
 if args[i]=='-o':
  k,_,v=args[i+1].partition('='); opts.setdefault(k.lower(),v); i+=2
 elif args[i] in ('-p','-l'):
  opts.setdefault('port' if args[i]=='-p' else 'user',args[i+1]); i+=2
 else: i+=1
h=opts.get('hostname',selected['host']); u=opts.get('user',selected['user']); p=opts.get('port',selected['port']); k=opts.get('hostkeyalias',selected['key'])
if '-G' in args:
 print('hostname '+h+'\\nuser '+u+'\\nport '+p+'\\nhostkeyalias '+k); sys.exit(0)
if opts.get('stricthostkeychecking')!='yes': sys.exit(90)
if (root/'wrong-key').exists():
 print('Host key verification failed',file=sys.stderr); sys.exit(255)
(root/'received').write_bytes(sys.stdin.buffer.read()); (root/'identity').write_text(json.dumps([h,u,p,k,opts.get('proxycommand')]))
print('{"ok":true}')
''')
            script.chmod(0o700)
            token = vps.SSH_PROXY.set(ssh_proxy.parse_proxy('http://127.0.0.1:10808'))
            try:
                with mock.patch.dict(os.environ, PATH=str(root) + os.pathsep + os.defpath, SSH_FIXTURE=str(root)):
                    args = argparse.Namespace(ssh='verified-alias', port=None, identity=None)
                    record = vps.target_record(args)
                    self.assertEqual(record['host_key_alias'], 'original-key-name')
                    alias.write_text(json.dumps({'host': '93.184.216.35', 'user': 'alternate', 'port': '2200', 'key': 'alternate-trusted-key'}))
                    with tempfile.TemporaryFile() as archive:
                        archive.write(b'PRIVATE-ARCHIVE-SENTINEL'); archive.seek(0)
                        vps.remote(record, 'prepare', stdin=archive)
                    identity = json.loads((root / 'identity').read_text())
                    self.assertEqual(identity[:4], [ENDPOINT, 'root', '2222', 'original-key-name'])
                    self.assertIn('http://127.0.0.1:10808', identity[4])
                    self.assertEqual((root / 'received').read_bytes(), b'PRIVATE-ARCHIVE-SENTINEL')
                    (root / 'received').unlink()
                    (root / 'wrong-key').touch()
                    with self.assertRaisesRegex(host.Error, 'Host key verification failed'):
                        vps.remote(record, 'prepare', {'secret': 'NEVER-SENT'})
                    self.assertFalse((root / 'received').exists())
            finally:
                vps.SSH_PROXY.reset(token)

    def test_legacy_no_alias_normal_port_lookup_and_new_alias_refusal(self):
        # A saved target also honors explicitly supplied key/port without --ssh.
        with mock.patch.object(vps.subprocess, 'run', side_effect=self.process):
            resolved = vps.target_record(argparse.Namespace(ssh=None, port=2222, identity=Path('/fixture/new key')), self.record)
        self.assertEqual(resolved['target'], self.record['target'])
        self.assertEqual(resolved['port'], 2222)
        self.assertIn('/fixture/new key', resolved['options'])
        self.calls.clear()
        with mock.patch.object(vps.subprocess, 'run', side_effect=self.process):
            vps.remote(self.record, 'status')
        self.assertFalse(any('HostKeyAlias=' in part for part in self.calls[0][0]))
        with mock.patch.object(vps, 'ssh_effective', return_value={'hostkeyalias': 'new-trusted-alias'}), \
             mock.patch.object(vps.subprocess, 'run') as process:
            with self.assertRaisesRegex(host.Error, 'HostKeyAlias changed'):
                vps.remote(self.record, 'prepare', {'secret': 'NEVER-SENT'})
            process.assert_not_called()
        for alias in ('new alias', '$(id)', '%h', 'x\n', ''):
            with self.assertRaises(host.Error):
                vps.host_key_alias(alias)


if __name__ == '__main__':
    unittest.main()
