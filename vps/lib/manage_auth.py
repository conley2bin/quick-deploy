"""Private auth state and signed-protocol v1 primitives for the persistent manager."""
from __future__ import annotations

import collections
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import time

VERSION = 1
DEFAULT_PORT = 47831
SESSION_IDLE = 600
MAX_SESSIONS = 128
MAX_NONCES = 1024
HEX32 = re.compile(r'[0-9a-f]{32}\Z')
HEX64 = re.compile(r'[0-9a-f]{64}\Z')
LF = '\n'


class AuthError(Exception):
    pass


def service_dir(state):
    return Path(state) / 'manage-service'


def _check(path, mode, directory=False):
    info = os.lstat(path)
    kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not kind or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != mode:
        raise AuthError('Unsafe manage-service path (symlink, owner, or mode): %s' % path.name)


def _read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise AuthError('Unsafe manage-service file: %s' % path.name)
        return source.read(65536)


def _write_new(directory, name, data):
    """Atomic fsynced create that never replaces an existing file."""
    fd, temporary = tempfile.mkstemp(prefix='.new-', dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.link(temporary, directory / name)
    finally:
        os.unlink(temporary)
    dfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _config(raw):
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise AuthError('Invalid manage-service config') from exc
    if not isinstance(value, dict):
        raise AuthError('Invalid manage-service config')
    port = value.get('port')
    if (value.get('schema') != 1 or set(value) != {'schema', 'port', 'repo'} or type(port) is not int
            or not 0 < port < 65536 or not isinstance(value['repo'], str) or not value['repo']):
        raise AuthError('Invalid manage-service config')
    return value


def ensure_state(state, repo_dir, port=DEFAULT_PORT):
    """Create master.key and config.json once; existing values are preserved."""
    state = Path(state)
    directory = service_dir(state)
    if directory.is_symlink():
        raise AuthError('Refusing symlink manage-service directory')
    directory.mkdir(mode=0o700, exist_ok=True)
    _check(directory, 0o700, True)
    for name, data in (('master.key', None), ('config.json', json.dumps(
            {'schema': 1, 'port': port, 'repo': str(Path(repo_dir).absolute())}, separators=(',', ':')).encode())):
        try:
            os.lstat(directory / name)
        except FileNotFoundError:
            _write_new(directory, name, secrets.token_bytes(32) if data is None else data)
    return load_state(state)[0]


def load_state(state):
    directory = service_dir(state)
    try:
        _check(directory, 0o700, True)
        config = _config(_read(directory / 'config.json'))
        master = _read(directory / 'master.key')
    except OSError as exc:
        raise AuthError('Missing or unreadable manage-service state') from exc
    if len(master) != 32:
        raise AuthError('Invalid manage-service master key')
    return config, master


def _mac(key, *parts):
    return hmac.new(key, LF.join(parts).encode(), hashlib.sha256)


def bootstrap_key(master):
    return hmac.new(master, b'qd-manager-bootstrap-v1', hashlib.sha256).digest()


def context(origin, epoch, session_id, client_nonce, server_nonce):
    return LF.join([str(VERSION), origin, epoch, session_id, client_nonce, server_nonce])


def server_proof(key, ctx):
    return _mac(key, 'qd-server-v1', ctx).hexdigest()


def session_key(key, ctx):
    return _mac(key, 'qd-session-v1', ctx).digest()


def request_mac(ks, epoch, nonce, method, path, body):
    return _mac(ks, 'qd-request-v1', epoch, nonce, method, path, hashlib.sha256(body).hexdigest()).hexdigest()


def response_mac(ks, epoch, nonce, status, body):
    return _mac(ks, 'qd-response-v1', epoch, nonce, str(status), hashlib.sha256(body).hexdigest()).hexdigest()


class Sessions:
    """Bounded LRU of per-hello keys with idle expiry and per-session replay sets."""
    def __init__(self, clock=time.monotonic):
        self.clock, self.items = clock, collections.OrderedDict()

    def add(self, session_id, ks):
        self.items[session_id] = {'ks': ks, 'seen': set(), 'last': self.clock()}
        while len(self.items) > MAX_SESSIONS:
            self.items.popitem(last=False)

    def get(self, session_id):
        record = self.items.get(session_id)
        if record is None:
            return None
        if self.clock() - record['last'] > SESSION_IDLE:
            del self.items[session_id]
            return None
        return record

    def consume(self, session_id, nonce):
        """Return 'ok' or 'replay'. A full replay set drops the session (client re-hellos)."""
        record = self.items[session_id]
        if nonce in record['seen']:
            return 'replay'
        if len(record['seen']) >= MAX_NONCES:
            del self.items[session_id]
            return 'replay'
        record['seen'].add(nonce)
        record['last'] = self.clock()
        self.items.move_to_end(session_id)
        return 'ok'
