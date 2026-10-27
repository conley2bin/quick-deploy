#!/usr/bin/env python3
"""Explicit loopback SOCKS5/HTTP CONNECT carrier for OpenSSH (stdlib only)."""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
import re
import select
import shlex
import socket
import sys
import time

HANDSHAKE_TIMEOUT = 15
MAX_HEADER = 16384
BUFFER_SIZE = 262144


class Error(ValueError):
    pass


@dataclass(frozen=True)
class Proxy:
    scheme: str
    port: int

    @property
    def url(self):
        return f'{self.scheme}://127.0.0.1:{self.port}'


def parse_proxy(value):
    match = re.fullmatch(r'(socks5|http)://127\.0\.0\.1:([0-9]{1,5})', value) if isinstance(value, str) else None
    if not match or not 1 <= int(match[2]) <= 65535:
        raise Error('--ssh-proxy requires socks5://127.0.0.1:PORT or http://127.0.0.1:PORT (1..65535), without credentials or other URL parts')
    return Proxy(match[1], int(match[2]))


def destination(address, port):
    try:
        ip = ipaddress.IPv4Address(address)
    except ipaddress.AddressValueError as exc:
        raise Error('SSH proxy destination must be a public IPv4 literal') from exc
    if not ip.is_global or ip.is_multicast or type(port) is not int or not 1 <= port <= 65535:
        raise Error('SSH proxy destination requires a public unicast IPv4 and port1..65535')
    return str(ip), port


def proxy_command(proxy, address, port, executable=None, helper=None):
    # OpenSSH evaluates this via a shell AND expands percent tokens first.
    # Quote every token, escape literal percent signs, and refuse control bytes.
    proxy = parse_proxy(proxy.url)
    address, port = destination(address, port)
    executable = str(Path(executable or sys.executable).absolute())
    helper = str(Path(helper or __file__).resolve())
    if not Path(executable).is_file() or not os.access(executable, os.X_OK) or not Path(helper).is_file():
        raise Error('Bundled SSH proxy helper or Python executable is unavailable')
    tokens = [executable, helper, proxy.url, address, str(port)]
    if any(any(ord(c) < 32 or ord(c) == 127 for c in token) for token in tokens):
        raise Error('Unsupported control character in SSH proxy helper path')
    return ' '.join(shlex.quote(token).replace('%', '%%') for token in tokens)


def _remaining(sock, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise Error('SSH proxy connect/handshake timed out')
    sock.settimeout(remaining)


def _recv(sock, size, deadline):
    _remaining(sock, deadline)
    chunk = sock.recv(size)
    if not chunk:
        raise Error('SSH proxy handshake truncated')
    return chunk


def _exact(sock, size, deadline):
    data = bytearray()
    while len(data) < size:
        data.extend(_recv(sock, size - len(data), deadline))
    return bytes(data)


def _send(sock, data, deadline):
    _remaining(sock, deadline)
    sock.sendall(data)


def connect_tunnel(proxy, address, port, timeout=HANDSHAKE_TIMEOUT):
    proxy = parse_proxy(proxy.url)
    address, port = destination(address, port)
    deadline = time.monotonic() + timeout
    # This is the ONLY outbound connect. No DNS, ambient proxy, or direct retry.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _remaining(sock, deadline)
        sock.connect(('127.0.0.1', proxy.port))
        prefetched = b''
        if proxy.scheme == 'socks5':
            _send(sock, b'\x05\x01\x00', deadline)
            if _exact(sock, 2, deadline) != b'\x05\x00':
                raise Error('SOCKS5 proxy refused no-authentication method')
            _send(sock, b'\x05\x01\x00\x01' + socket.inet_aton(address) + port.to_bytes(2, 'big'), deadline)
            version, reply, reserved, kind = _exact(sock, 4, deadline)
            if version != 5 or reserved != 0:
                raise Error('Malformed SOCKS5 proxy reply')
            if reply != 0:
                raise Error('SOCKS5 proxy denied CONNECT')
            if kind == 1:
                size = 6
            elif kind == 4:
                size = 18
            elif kind == 3:
                size = _exact(sock, 1, deadline)[0]
                if not size:
                    raise Error('Malformed SOCKS5 proxy address')
                size += 2
            else:
                raise Error('Malformed SOCKS5 proxy address type')
            _exact(sock, size, deadline)
        else:
            authority = f'{address}:{port}'
            _send(sock, f'CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n'.encode('ascii'), deadline)
            data = bytearray()
            while b'\r\n\r\n' not in data:
                if len(data) >= MAX_HEADER:
                    raise Error('HTTP CONNECT proxy response headers oversized')
                data.extend(_recv(sock, min(4096, MAX_HEADER - len(data)), deadline))
            end = data.index(b'\r\n\r\n') + 4
            lines = bytes(data[:end - 4]).split(b'\r\n')
            if not re.fullmatch(rb'HTTP/1\.[01] [0-9]{3}(?: [\x20-\x7e]*)?', lines[0]):
                raise Error('Malformed HTTP CONNECT proxy status')
            if lines[0][9:12] != b'200':
                raise Error('HTTP CONNECT proxy denied CONNECT')
            for line in lines[1:]:
                if not re.fullmatch(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+:[\t\x20-\x7e\x80-\xff]*", line):
                    raise Error('Malformed HTTP CONNECT proxy header')
            prefetched = bytes(data[end:])
        sock.settimeout(None)
        return sock, prefetched
    except BaseException:
        sock.close()
        raise


def relay(sock, prefetched=b'', input_fd=0, output_fd=1):
    """Bounded full duplex; stdin EOF half-closes TCP, TCP EOF closes stdout."""
    input_blocking = os.get_blocking(input_fd)
    output_blocking = os.get_blocking(output_fd)
    sock.setblocking(False)
    os.set_blocking(input_fd, False)
    os.set_blocking(output_fd, False)
    to_socket, to_output = bytearray(), bytearray(prefetched)
    input_open = socket_open = output_open = True
    write_closed = False
    try:
        while input_open or socket_open or to_socket or to_output:
            if not input_open and not to_socket and not write_closed:
                sock.shutdown(socket.SHUT_WR)
                write_closed = True
            if not socket_open and not to_output and output_open:
                os.close(output_fd)
                output_open = False
            reads = ([input_fd] if input_open and len(to_socket) < BUFFER_SIZE else [])
            if socket_open and len(to_output) < BUFFER_SIZE:
                reads.append(sock)
            writes = ([sock] if to_socket else []) + ([output_fd] if to_output else [])
            if not reads and not writes:
                break
            ready_read, ready_write, _ = select.select(reads, writes, [])
            for fd in ready_read:
                try:
                    if fd is sock:
                        chunk = sock.recv(min(65536, BUFFER_SIZE - len(to_output)))
                        if chunk:
                            to_output.extend(chunk)
                        else:
                            socket_open = False
                    else:
                        chunk = os.read(input_fd, min(65536, BUFFER_SIZE - len(to_socket)))
                        if chunk:
                            to_socket.extend(chunk)
                        else:
                            input_open = False
                except BlockingIOError:
                    pass
            for fd in ready_write:
                try:
                    if fd is sock:
                        count = sock.send(to_socket)
                        del to_socket[:count]
                    else:
                        count = os.write(output_fd, to_output)
                        del to_output[:count]
                except BlockingIOError:
                    pass
    finally:
        os.set_blocking(input_fd, input_blocking)
        if output_open:
            os.set_blocking(output_fd, output_blocking)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if len(argv) != 3 or not re.fullmatch(r'[0-9]{1,5}', argv[2]):
            raise Error('Usage: ssh_proxy.py PROXY_URL PUBLIC_IPV4 PORT')
        proxy = parse_proxy(argv[0])
        address, port = destination(argv[1], int(argv[2]))
        sock, prefetched = connect_tunnel(proxy, address, port)
        with sock:
            relay(sock, prefetched)
        return 0
    except (Error, OSError) as exc:
        if isinstance(exc, TimeoutError):
            message = 'SSH proxy connect/handshake timed out'
        elif isinstance(exc, Error):
            message = str(exc)
        else:
            message = 'SSH proxy socket/stream failed'
        print('ssh proxy: ' + message, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
