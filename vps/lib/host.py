#!/usr/bin/env python3
"""Single-host 3x-ui adapter. SSH sends requests on stdin; never run an installer.

The panel owns accounts, SQLite schema, Xray config and subscription encoders.
This file owns only its private tree and four explicitly named systemd units.
"""
from __future__ import annotations

import base64
import contextlib
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import http.cookiejar
import io
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import socket
import sqlite3
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, parse_qs, unquote
from urllib.request import Request, build_opener, ProxyHandler, HTTPCookieProcessor
import uuid
import zipfile

ROOT = Path('/var/lib/qd-3x-ui')
UNITS = Path('/etc/systemd/system')
PANEL = 'qd-3x-ui.service'
FRONT = 'qd-3x-ui-front.service'
RECOVER = 'qd-3x-ui-recover.service'
TIMER = 'qd-3x-ui-recover.timer'
UNIT_NAMES = (PANEL, FRONT, RECOVER, TIMER)
DEVICES = ('company', 'home', 'phone')
FORMAT = 'quick-deploy-3x-ui-1'
ADMIN_PORT, SUB_PORT, API_PORT = 2053, 2096, 62789
DEFAULT_PROXY_PORT = 443
CDN_PORT, XHTTP_PORT = 443, 2080
XHTTP_PATH, XHTTP_MODE = '/qd-xhttp/', 'packet-up'
MAX_ARCHIVE = 512 * 1024 * 1024


class Error(Exception):
    pass


def dumps(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + '\n'


def run(argv, **kwargs):
    # Do not echo arguments or subprocess output: native APIs can contain secrets.
    result = subprocess.run([str(a) for a in argv], capture_output=True, check=False, **kwargs)
    if result.returncode:
        raise Error(f'{Path(str(argv[0])).name} failed (exit {result.returncode}); private candidate retained')
    return result


def atomic(path, data, mode=0o600):
    path = Path(path)
    if path.is_symlink():
        raise Error('Refusing symlink output')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = data.encode() if isinstance(data, str) else data
    if path.exists() and path.read_bytes() == data:
        if path.stat().st_mode & 0o777 != mode:
            path.chmod(mode)
        return
    fd, name = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise Error('Missing or invalid state; preserve it and restore a verified backup') from exc


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def hostname(value):
    if not isinstance(value, str) or len(value) > 253 or '.' not in value:
        raise Error('Require a literal fully qualified DNS hostname')
    labels = value.split('.')
    if any(not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', label) for label in labels):
        raise Error('Invalid DNS hostname')
    return value.lower()


def public_ip(value):
    try:
        addr = ipaddress.ip_address(value)
    except ValueError as exc:
        raise Error('SSH HostName must resolve to a public IPv4 literal') from exc
    if addr.version != 4 or not addr.is_global or addr.is_multicast:
        raise Error('Initial support requires a global unicast IPv4')
    return str(addr)


def validate_config(config):
    if set(config) != {'domain', 'reality_target', 'acme_email', 'proxy_port'}:
        raise Error('Invalid deployment configuration keys')
    port = config['proxy_port']
    if type(port) is not int or not 1 <= port <= 65535 or port in (80, 8443, ADMIN_PORT, SUB_PORT, API_PORT):
        raise Error('Invalid proxy_port or conflict with certificate/subscription/management listener')
    hostname(config['domain'])
    hostname(config['reality_target'])
    email = config['acme_email']
    if not isinstance(email, str) or (email and not re.fullmatch(r'[^\s@{}]+@[^\s@{}]+\.[^\s@{}]+', email)):
        raise Error('ACME contact must be empty or a valid email')
    if email:
        hostname(email.rsplit('@', 1)[1])
    if config['domain'] == config['reality_target']:
        raise Error('The subscription hostname cannot be its own REALITY target')


def required_ports(config, manifest=None):
    ports = (80, config['proxy_port'], 8443, ADMIN_PORT, SUB_PORT, API_PORT)
    return (*ports, XHTTP_PORT) if manifest is not None and cdn_enabled(manifest) else ports


def tcp_owner(manifest, port):
    """Ownership follows attested active state, never a proposed transition."""
    if port in (80, 8443) or (cdn_enabled(manifest) and port == CDN_PORT):
        return FRONT, 'caddy'
    return PANEL, 'xray-linux-amd64' if port in (manifest['config']['proxy_port'], API_PORT, XHTTP_PORT) else 'x-ui'


def verify_artifacts(folder, lock):
    if lock.get('platform') != 'ubuntu-24.04-amd64':
        raise Error('Unsupported release platform')
    for key in ('panel', 'core', 'front'):
        pin = lock[key]
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', pin['file']) or not re.fullmatch(r'[0-9a-f]{64}', pin['sha256']):
            raise Error('Invalid release lock')
        path = folder / pin['file']
        if path.is_symlink() or digest(path) != pin['sha256']:
            raise Error(f'{key} artifact checksum mismatch; no installation')


def install_runtime(folder, lock):
    """Select only executables/geodata. Never extract upstream scripts/units."""
    artifacts = folder / 'artifacts'
    verify_artifacts(artifacts, lock)
    runtime = folder / 'bin'
    runtime.mkdir(mode=0o700, exist_ok=True)
    with tarfile.open(artifacts / lock['panel']['file']) as archive:
        member = archive.getmember('x-ui/x-ui')
        if not member.isfile() or member.size > MAX_ARCHIVE:
            raise Error('Invalid panel executable member')
        atomic(runtime / 'x-ui', archive.extractfile(member).read(), 0o700)
    with zipfile.ZipFile(artifacts / lock['core']['file']) as archive:
        for name, dest in [('xray', 'xray-linux-amd64'), ('geoip.dat', 'geoip.dat'), ('geosite.dat', 'geosite.dat')]:
            if archive.getinfo(name).file_size > MAX_ARCHIVE:
                raise Error('Oversized core member')
            atomic(runtime / dest, archive.read(name), 0o700 if name == 'xray' else 0o600)
    with tarfile.open(artifacts / lock['front']['file']) as archive:
        member = archive.getmember('caddy')
        if not member.isfile() or member.size > MAX_ARCHIVE:
            raise Error('Invalid front executable member')
        atomic(runtime / 'caddy', archive.extractfile(member).read(), 0o700)
    versions = [('x-ui', ['-v'], lock['panel']['version']),
                ('xray-linux-amd64', ['version'], 'Xray ' + lock['core']['version']),
                ('caddy', ['version'], 'v' + lock['front']['version'])]
    for name, args, expected in versions:
        result = run([runtime / name, *args], timeout=20).stdout.decode().splitlines()[0]
        if result != expected and not result.startswith(expected + ' '):
            raise Error(f'{name} version does not match the verified release')
    atomic(folder / 'release-lock.json', dumps(lock))
    immutable = ('x-ui', 'xray-linux-amd64', 'caddy', 'geoip.dat', 'geosite.dat')
    atomic(folder / 'binary-hashes.json', dumps({name: digest(runtime / name) for name in immutable}))


def panel_env(folder):
    return dict(os.environ, XUI_DB_FOLDER=str(folder / 'db'), XUI_BIN_FOLDER=str(folder / 'bin'),
                XUI_LOG_FOLDER=str(folder / 'logs'), XUI_DB_TYPE='sqlite', XUI_LOG_LEVEL='error',
                XUI_DEBUG='false', XUI_ENABLE_FAIL2BAN='false')


def settings_write(db, values):
    """Offline native settings only; account tables are written by the panel API."""
    with contextlib.closing(sqlite3.connect(db)) as conn, conn:
        if [r[1] for r in conn.execute('pragma table_info(settings)')] != ['id', 'key', 'value']:
            raise Error('Unexpected upstream settings schema')
        for key, value in values.items():
            text = str(value).lower() if isinstance(value, bool) else str(value)
            count = conn.execute('select count(*) from settings where key=?', (key,)).fetchone()[0]
            if count > 1:
                raise Error('Ambiguous native setting key; refuse duplicate rows')
            if count:
                conn.execute('update settings set value=? where key=?', (text, key))
            else:
                conn.execute('insert into settings(key,value) values(?,?)', (key, text))


class API:
    def __init__(self, manifest, password=None):
        self.base = f'http://127.0.0.1:{ADMIN_PORT}' + manifest['admin_path'].rstrip('/')
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf = self.call('/csrf-token')
        self.call('/login', {'username': manifest['admin_user'], 'password': password or manifest['admin_password']})

    def call(self, path, data=None):
        raw = None if data is None else json.dumps(data).encode()
        headers = {'Content-Type': 'application/json'}
        if data is not None:
            headers['X-CSRF-Token'] = self.csrf
        request = Request(self.base + path, data=raw, headers=headers)
        try:
            with self.opener.open(request, timeout=20) as response:
                value = json.load(response)
        except (HTTPError, URLError, ValueError, TimeoutError) as exc:
            raise Error('Native panel API request failed (credentials/paths withheld)') from exc
        if not isinstance(value, dict) or not value.get('success'):
            raise Error('Native panel API rejected request (response withheld)')
        return value.get('obj')


def wait_api(manifest, password=None):
    for _ in range(60):
        try:
            return API(manifest, password)
        except Error:
            time.sleep(0.5)
    raise Error('Loopback panel did not become ready')


def new_manifest(owner, config, endpoint):
    return {'format': FORMAT, 'owner': owner, 'node_id': str(uuid.uuid4()), 'config': config,
            'endpoint': public_ip(endpoint), 'admin_path': '/control-' + secrets.token_hex(16) + '/',
            'admin_user': 'operator-' + secrets.token_hex(6), 'admin_password': secrets.token_urlsafe(36),
            'accounts': {d: {'id': str(uuid.uuid4()), 'subId': secrets.token_hex(24), 'email': d,
                             'enable': True, 'flow': 'xtls-rprx-vision'} for d in DEVICES}, 'published': False}


def certificate_metadata(meta, label):
    for key in ('certificate_file', 'key_file'):
        path = Path(meta[key]) if isinstance(meta.get(key), str) else Path('/')
        if path.is_absolute() or '..' in path.parts or (len(path.parts) < 5 or path.parts[:4] != ('caddy', 'data', 'caddy', 'certificates')):
            raise Error('Invalid owned ' + label + ' certificate path')
    if Path(meta['certificate_file']).suffix != '.crt' or str(Path(meta['certificate_file']).with_suffix('.key')) != meta['key_file']:
        raise Error('Invalid owned ' + label + ' certificate pair')


def cdn_metadata(manifest):
    meta = manifest['tcp_cdn']
    if (not isinstance(meta, dict) or set(meta) != {'schema', 'domain', 'inbound_id', 'path', 'mode', 'certificate_file', 'key_file'} or
            type(meta['schema']) is not int or meta['schema'] != 1 or type(meta['inbound_id']) is not int or meta['inbound_id'] <= 0 or
            hostname(meta['domain']) != meta['domain'] or meta['domain'] in (manifest['config']['domain'], manifest['config']['reality_target']) or
            manifest['config']['proxy_port'] != CDN_PORT or meta['path'] != XHTTP_PATH or meta['mode'] != XHTTP_MODE or
            meta['inbound_id'] == manifest['hysteria2']['inbound_id']):
        raise Error('Invalid owned TCP CDN capability metadata')
    certificate_metadata(meta, 'CDN')


def cdn_enabled(manifest):
    if 'tcp_cdn' not in manifest:
        return False
    dual_enabled(manifest)  # validates both discriminators, including exact fields
    return True


def main_tag(manifest):
    return 'SELF-cdn' if cdn_enabled(manifest) else 'SELF-main'


def transports(manifest):
    return ['tcp-xhttp-cdn', 'hysteria2'] if cdn_enabled(manifest) else (['tcp-reality', 'hysteria2'] if dual_enabled(manifest) else ['tcp-reality'])


def dual_enabled(manifest):
    if 'hysteria2' not in manifest:
        if 'tcp_cdn' in manifest:
            raise Error('TCP CDN requires owned Hysteria2')
        return False
    meta = manifest['hysteria2']
    if (not isinstance(meta, dict) or set(meta) != {'schema', 'inbound_id', 'host_group_id', 'certificate_file', 'key_file'} or
            meta['schema'] != 1 or type(meta['inbound_id']) is not int or meta['inbound_id'] <= 0 or
            not isinstance(meta['host_group_id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', meta['host_group_id'])):
        raise Error('Invalid owned Hysteria2 capability metadata')
    for key in ('certificate_file', 'key_file'):
        path = Path(meta[key]) if isinstance(meta[key], str) else Path('/')
        if path.is_absolute() or '..' in path.parts or (len(path.parts) < 5 or path.parts[:4] != ('caddy', 'data', 'caddy', 'certificates')):
            raise Error('Invalid owned Hysteria2 certificate path')
    if not isinstance(manifest.get('management'), dict) or type(manifest['management'].get('ssh_port')) is not int or not 1 <= manifest['management']['ssh_port'] <= 65535:
        raise Error('Missing verified management port for Hysteria2')
    if 'tcp_cdn' in manifest:
        cdn_metadata(manifest)
    return True


def certificate_paths(folder, domain, metadata=None):
    """Select one hostname's owned pair, independently of other Caddy sites."""
    folder = Path(folder)
    storage = folder / 'caddy/data/caddy/certificates'
    reject_symlinks(folder)
    certs = list(storage.rglob(domain + '.crt')) if storage.is_dir() else []
    if len(certs) != 1:
        raise Error('Require exactly one owned Caddy certificate pair')
    cert = certs[0]
    key = cert.with_suffix('.key')
    if not cert.is_file() or not key.is_file() or not cert.stat().st_size or not key.stat().st_size:
        raise Error('Missing owned Caddy certificate/private key')
    if not cert.stat().st_mode & stat.S_IRUSR or not key.stat().st_mode & stat.S_IRUSR or key.stat().st_mode & 0o077:
        raise Error('Owned Caddy certificate/key must be readable and private')
    if metadata is not None and (str(cert.relative_to(folder)) != metadata['certificate_file'] or
                                 str(key.relative_to(folder)) != metadata['key_file']):
        raise Error('Changed owned certificate identity')
    checked = run(['openssl', 'x509', '-in', cert, '-noout', '-checkhost', domain], timeout=10).stdout
    if b' does match certificate' not in checked:
        raise Error('Owned Caddy certificate does not match deployment hostname')
    public = run(['openssl', 'x509', '-in', cert, '-pubkey', '-noout'], timeout=10).stdout
    private = run(['openssl', 'pkey', '-in', key, '-pubout'], timeout=10).stdout
    if public != private:
        raise Error('Owned Caddy certificate/private key mismatch')
    return cert.absolute(), key.absolute()


def hysteria_certificate_paths(folder, manifest):
    return certificate_paths(folder, manifest['config']['domain'], manifest['hysteria2'] if dual_enabled(manifest) else None)


def cdn_certificate_paths(folder, manifest):
    if not cdn_enabled(manifest):
        raise Error('Missing owned TCP CDN capability')
    return certificate_paths(folder, manifest['tcp_cdn']['domain'], manifest['tcp_cdn'])


def device_names(manifest):
    """The manifest owns membership; native rows can never extend it implicitly."""
    accounts = manifest.get('accounts')
    if not isinstance(accounts, dict) or any(not isinstance(d, str) or
            not re.fullmatch(r'[a-z][a-z0-9-]{0,31}', d) for d in accounts):
        raise Error('Invalid owned device roster; use lowercase names up to32 characters')
    identities, subscriptions, authorizations = [], [], []
    for name, account in accounts.items():
        if account.get('email') != name or not isinstance(account.get('enable'), bool):
            raise Error('Invalid owned device identity')
        try:
            uuid.UUID(account['id'])
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise Error('Invalid owned device UUID') from exc
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', account.get('subId', '')):
            raise Error('Invalid owned subscription identity')
        if dual_enabled(manifest):
            auth = account.get('auth')
            if not isinstance(auth, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', auth):
                raise Error('Invalid owned Hysteria2 authorization')
            authorizations.append(auth)
        identities.append(uuid.UUID(account['id']))
        subscriptions.append(account['subId'])
    if len(set(identities)) != len(identities) or len(set(subscriptions)) != len(subscriptions) or len(set(authorizations)) != len(authorizations):
        raise Error('Duplicate owned device credential')
    return tuple(accounts)


def native_keys(folder):
    text = run([folder / 'bin/xray-linux-amd64', 'x25519'], timeout=10).stdout.decode()
    private = re.search(r'^PrivateKey:\s*([\w-]{43})$', text, re.M)
    public = re.search(r'^(?:Password \(PublicKey\)|PublicKey):\s*([\w-]{43})$', text, re.M)
    if not private or not public:
        raise Error('Unrecognized native X25519 output')
    return {'privateKey': private[1], 'publicKey': public[1], 'shortIds': [secrets.token_hex(8)]}


def inbound_for(manifest):
    keys = manifest['reality']
    return {'remark': 'SELF-main', 'enable': True, 'listen': '0.0.0.0', 'port': manifest['config']['proxy_port'],
            'protocol': 'vless', 'settings': json.dumps({'clients': list(manifest['accounts'].values()), 'decryption': 'none'}),
            'streamSettings': json.dumps({'network': 'tcp', 'security': 'reality', 'tcpSettings': {'header': {'type': 'none'}},
                'realitySettings': {'show': False, 'dest': manifest['config']['reality_target'] + ':443',
                    'xver': 0, 'serverNames': [manifest['config']['reality_target']], **keys,
                    'settings': {'publicKey': keys['publicKey'], 'fingerprint': 'chrome', 'spiderX': '/'}},
                'externalProxy': [{'forceTls': 'same', 'dest': manifest['endpoint'], 'port': manifest['config']['proxy_port'], 'remark': ''}]}),
            'sniffing': json.dumps({'enabled': True, 'destOverride': ['http', 'tls', 'quic'], 'routeOnly': True})}


def clash_rules(manifest):
    # An upstream-supported routing setting, not a subscription encoder.
    rules = ['DOMAIN,' + manifest['config']['domain'] + ',DIRECT',
        'IP-CIDR,' + manifest['endpoint'] + '/32,DIRECT,no-resolve',
        'IP-CIDR,127.0.0.0/8,DIRECT,no-resolve', 'IP-CIDR,10.0.0.0/8,DIRECT,no-resolve',
        'IP-CIDR,172.16.0.0/12,DIRECT,no-resolve', 'IP-CIDR,192.168.0.0/16,DIRECT,no-resolve',
        'IP-CIDR,100.64.0.0/10,DIRECT,no-resolve',
        'DOMAIN-SUFFIX,bilibili.com,DIRECT', 'DOMAIN-SUFFIX,bilivideo.com,DIRECT',
        'DOMAIN-SUFFIX,biliapi.net,DIRECT', 'DOMAIN-SUFFIX,biliapi.com,DIRECT',
        'DOMAIN-SUFFIX,hdslb.com,DIRECT', 'GEOSITE,cn,DIRECT', 'GEOIP,CN,DIRECT', 'MATCH,PROXY']
    # JSON is valid YAML and the panel's supported inline template merges these
    # top-level settings. Native dat assets avoid an implicit MMDB dependency.
    if cdn_enabled(manifest):
        rules.insert(0, 'DOMAIN,' + manifest['tcp_cdn']['domain'] + ',DIRECT')
    settings = {'geodata-mode': True, 'rules': rules}
    if dual_enabled(manifest):
        domain = manifest['config']['domain']
        settings['proxy-groups'] = [
            {'name': 'PROXY', 'type': 'select', 'proxies': ['SELF-h2', main_tag(manifest), 'DIRECT']},
            {'name': 'FEED', 'type': 'fallback', 'proxies': ['SELF-h2', 'DIRECT'],
             'url': f'https://{domain}:8443/', 'expected-status': '404', 'interval': 300, 'timeout': 5000, 'lazy': False}]
        settings['rules'] = [
            f'AND,((NETWORK,TCP),(DOMAIN,{domain}),(DST-PORT,8443)),FEED',
            f"AND,((NETWORK,TCP),(IP-CIDR,{manifest['endpoint']}/32,no-resolve),(DST-PORT,8443)),FEED",
            *rules]
    return json.dumps(settings)


def backend_settings(manifest):
    domain = manifest['config']['domain']
    return {'webListen': '127.0.0.1', 'webPort': ADMIN_PORT, 'webBasePath': manifest['admin_path'],
            'subListen': '127.0.0.1', 'subPort': SUB_PORT, 'subEnable': True, 'subPath': '/sub/',
            'subClashEnable': True, 'subClashPath': '/mihomo/', 'subJsonEnable': False,
            'subClashAutoDetect': False, 'subJsonAutoDetect': False,
            'subDomain': '', 'webDomain': '', 'webCertFile': '', 'webKeyFile': '', 'subCertFile': '', 'subKeyFile': '',
            'subURI': f'https://{domain}:8443/sub/', 'subClashURI': f'https://{domain}:8443/mihomo/',
            'subClashEnableRouting': True, 'subClashRules': clash_rules(manifest),
            'subEncrypt': True, 'subProfileMode': 'none', 'remarkTemplate': '{{INBOUND}}',
            'tgBotEnable': False, 'externalTrafficInformEnable': False}


def initialize_db(folder, manifest):
    run([folder / 'bin/x-ui', 'setting', '-listenIP', '127.0.0.1', '-port', str(ADMIN_PORT),
         '-webBasePath', manifest['admin_path']], env=panel_env(folder), timeout=60)
    settings_write(folder / 'db/x-ui.db', backend_settings(manifest))


def owned_host_group(folder, api, manifest):
    """Attest every native row and the exact advertised/main-listener split."""
    inbounds = api.call('/panel/api/inbounds/list') or []
    tag = main_tag(manifest)
    expected = {tag: 'vless', 'SELF-h2': 'hysteria'} if dual_enabled(manifest) else {tag: 'vless'}
    if len(inbounds) != len(expected) or {i.get('remark'): i.get('protocol') for i in inbounds} != expected:
        raise Error('Unexpected native Host inbound membership')
    inbound = next(i for i in inbounds if i['remark'] == tag)
    stream = native_object(inbound['streamSettings'])
    if cdn_enabled(manifest):
        cdn_inbound_health(inbound, manifest)
        h2 = next(i for i in inbounds if i['remark'] == 'SELF-h2')
        external = native_object(h2['streamSettings']).get('externalProxy', [])
    else:
        external = stream.get('externalProxy', [])
    if (len(external) != 1 or (not cdn_enabled(manifest) and inbound['port'] != manifest['config']['proxy_port']) or
            external[0].get('port') != manifest['config']['proxy_port']):
        raise Error('Unexpected native Host endpoint membership')
    old_endpoint = public_ip(external[0]['dest'])
    group_id = manifest.get('host_group_id')
    if not group_id:
        raise Error('Missing native Host ownership identity; verify the closed backup first')
    # Count all DB rows, including hidden/disabled duplicates that the grouped
    # API can deduplicate. A changed saved ID is never silently adopted.
    snapshot_host_group(folder / 'db/x-ui.db', dict(manifest, endpoint=old_endpoint))
    groups = api.call('/panel/api/hosts/list') or []
    expected_groups = {group_id}
    if dual_enabled(manifest):
        expected_groups.add(manifest['hysteria2']['host_group_id'])
    if len(groups) != len(expected_groups) or {g.get('groupId') for g in groups} != expected_groups:
        raise Error('Unexpected native Host group membership')
    group = api.call('/panel/api/hosts/get/' + group_id)
    expected = [old_endpoint + ':' + str(manifest['config']['proxy_port'])]
    advertised = [manifest['tcp_cdn']['domain'] + ':' + str(CDN_PORT)] if cdn_enabled(manifest) else expected
    if (group.get('groupId') != group_id or group.get('inboundIds') != [inbound['id']] or
            group.get('hosts') != advertised or group.get('port') != manifest['config']['proxy_port']):
        raise Error('Unexpected native Host group endpoint/inbound membership')
    if cdn_enabled(manifest):
        cdn_host_health(group, manifest)
    if dual_enabled(manifest):
        h2 = next(i for i in inbounds if i['remark'] == 'SELF-h2')
        h2stream = h2['streamSettings']
        h2stream = json.loads(h2stream) if isinstance(h2stream, str) else h2stream
        if (h2['id'] != manifest['hysteria2']['inbound_id'] or h2.get('tag') != 'SELF-h2' or
                h2stream.get('externalProxy') != [{'forceTls': 'same', 'dest': old_endpoint, 'port': manifest['config']['proxy_port'], 'remark': ''}]):
            raise Error('Changed owned Hysteria2 inbound identity')
        h2_group = api.call('/panel/api/hosts/get/' + manifest['hysteria2']['host_group_id'])
        if (h2_group.get('inboundIds') != [h2['id']] or h2_group.get('hosts') != expected or
                h2_group.get('port') != manifest['config']['proxy_port']):
            raise Error('Unexpected native Hysteria2 Host group')
    return inbound, group


def native_object(value):
    return json.loads(value) if isinstance(value, str) else value


def cdn_inbound_health(inbound, manifest):
    meta = manifest['tcp_cdn']
    stream, settings = native_object(inbound['streamSettings']), native_object(inbound['settings'])
    if (inbound.get('id') != meta['inbound_id'] or inbound.get('tag') != 'SELF-cdn' or inbound.get('remark') != 'SELF-cdn' or
            inbound.get('protocol') != 'vless' or inbound.get('listen') != '127.0.0.1' or inbound.get('port') != XHTTP_PORT or
            inbound.get('enable') is not True or settings.get('decryption') != 'none' or
            inbound.get('disableFlow') is not True or any(c.get('flow') not in ('', None) for c in settings.get('clients', [])) or
            stream.get('network') != 'xhttp' or stream.get('security') != 'none' or
            stream.get('xhttpSettings') != {'host': meta['domain'], 'path': meta['path'], 'mode': meta['mode']} or
            stream.get('externalProxy', []) != []):
        raise Error('Unexpected owned CDN native transport/listener')


def cdn_host_health(group, manifest):
    meta = manifest['tcp_cdn']
    expected = {'groupId': manifest['host_group_id'], 'inboundIds': [meta['inbound_id']], 'hosts': [meta['domain'] + ':' + str(CDN_PORT)],
                'port': CDN_PORT, 'security': 'tls', 'sni': meta['domain'], 'hostHeader': meta['domain'],
                'path': meta['path'], 'alpn': ['h2'], 'allowInsecure': False}
    forbidden = ('pinnedPeerCertSha256', 'verifyPeerCertByName', 'echConfigList', 'muxParams', 'sockoptParams', 'finalMask',
                 'vlessRoute', 'excludeFromSubTypes', 'keepSniBlank', 'overrideSniFromAddress')
    if (any(group.get(key) != value or (key == 'allowInsecure' and group.get(key) is not False) for key, value in expected.items()) or
            any(group.get(key) not in (None, '', False, []) for key in forbidden)):
        raise Error('Unexpected owned CDN native Host TLS advertisement')


def reality_source_health(inbound, manifest):
    stream, settings = native_object(inbound['streamSettings']), native_object(inbound['settings'])
    reality = stream.get('realitySettings', {})
    # The existing native API generates this tag because inbound_for supplies
    # a remark but no tag. SELF-main is the exported role, not the native tag.
    if (inbound.get('tag') != 'in-' + str(CDN_PORT) + '-tcp' or inbound.get('remark') != 'SELF-main' or inbound.get('enable') is not True or
            inbound.get('listen') != '0.0.0.0' or inbound.get('port') != CDN_PORT or inbound.get('protocol') != 'vless' or
            stream.get('network') != 'tcp' or stream.get('security') != 'reality' or settings.get('decryption') != 'none' or
            reality.get('dest') != manifest['config']['reality_target'] + ':443' or
            reality.get('serverNames') != [manifest['config']['reality_target']] or
            any(reality.get(k) != v for k, v in manifest['reality'].items())):
        raise Error('CDN source is not the exact owned REALITY listener')


def enable_cdn(folder, manifest, domain):
    """Convert the existing VLESS/Host via full native objects; never reattach clients."""
    if cdn_enabled(manifest) or not dual_enabled(manifest) or manifest['config']['proxy_port'] != CDN_PORT:
        raise Error('CDN migration requires the exact REALITY+Hysteria2 source')
    domain = hostname(domain)
    api = wait_api(manifest)
    original, group = owned_host_group(folder, api, manifest)
    before = snapshot_accounts(Path(folder) / 'db/x-ui.db', manifest)
    h2_group = api.call('/panel/api/hosts/get/' + manifest['hysteria2']['host_group_id'])
    records = {n: api.call('/panel/api/clients/get/' + n)['client'] for n in device_names(manifest)}
    earliest = {n: r.get('createdAt') for n, r in records.items()}
    for item in api.call('/panel/api/inbounds/list'):
        for client in native_object(item['settings']).get('clients', []):
            value = client.get('created_at', 0)
            if client['email'] in earliest and type(value) is int and value > 0 and type(earliest[client['email']]) is int:
                earliest[client['email']] = min(earliest[client['email']], value)
    cert, key = certificate_paths(folder, domain)
    meta = {'schema': 1, 'domain': domain, 'inbound_id': original['id'], 'path': XHTTP_PATH, 'mode': XHTTP_MODE,
            'certificate_file': str(cert.relative_to(Path(folder).absolute())), 'key_file': str(key.relative_to(Path(folder).absolute()))}
    candidate = dict(manifest, tcp_cdn=meta)
    cdn_enabled(candidate)
    inbound = copy.deepcopy(original)
    settings = native_object(inbound['settings'])
    inbound.update(remark='SELF-cdn', tag='SELF-cdn', listen='127.0.0.1', port=XHTTP_PORT, disableFlow=True, settings=json.dumps(settings),
        streamSettings=json.dumps({'network': 'xhttp', 'security': 'none',
            'xhttpSettings': {'host': domain, 'path': XHTTP_PATH, 'mode': XHTTP_MODE}, 'externalProxy': []}))
    desired = copy.deepcopy(group)
    desired.update(hosts=[domain + ':' + str(CDN_PORT)], port=CDN_PORT, security='tls', sni=domain,
                   hostHeader=domain, path=XHTTP_PATH, alpn=['h2'], allowInsecure=False)
    check_lease()
    api.call('/panel/api/inbounds/update/' + str(inbound['id']), inbound)
    api.call('/panel/api/hosts/update/' + group['groupId'], desired)
    manifest['tcp_cdn'] = meta
    after = snapshot_accounts(Path(folder) / 'db/x-ui.db', manifest)
    if any({k: v for k, v in after[n].items() if k != 'flow'} != {k: v for k, v in c.items() if k != 'flow'} or after[n]['flow'] != ''
           for n, c in before.items()):
        raise Error('Native CDN conversion changed credential authority')
    manifest['accounts'] = after
    for name, record in records.items():
        current = api.call('/panel/api/clients/get/' + name)['client']
        if ({k: v for k, v in current.items() if k not in ('flow', 'createdAt', 'updatedAt')} !=
                {k: v for k, v in record.items() if k not in ('flow', 'createdAt', 'updatedAt')} or current.get('flow') != ''):
            raise Error('Native CDN conversion changed full client identity/policy')
        if current.get('createdAt') != record.get('createdAt'):
            value, original_time = current.get('createdAt'), record.get('createdAt')
            if (type(value) is not int or type(original_time) is not int or value != earliest[name] or
                    value // 1000 != original_time // 1000 or not 0 <= original_time - value < 1000):
                raise Error('Native CDN conversion changed creation metadata beyond known normalization')
        # Only the known earliest stored millisecond normalization is allowed.
    if (api.call('/panel/api/hosts/get/' + group['groupId']) != desired or
            api.call('/panel/api/hosts/get/' + manifest['hysteria2']['host_group_id']) != h2_group):
        raise Error('Native CDN Host update changed unrelated policy')
    owned_host_group(folder, api, manifest)
    # The full non-flow native policies stay in the preserved inbound/DB; do not
    # reconstruct global Client DTOs from the reduced manifest projection.
    ctl('stop', PANEL)
    settings_write(Path(folder) / 'db/x-ui.db', backend_settings(manifest))
    ctl('start', PANEL)
    wait_api(manifest)
    core_check(folder)


def hysteria_inbound(manifest, cert, key):
    return {'remark': 'SELF-h2', 'tag': 'SELF-h2', 'enable': True, 'listen': '0.0.0.0', 'port': manifest['config']['proxy_port'],
            'protocol': 'hysteria', 'settings': json.dumps({'version': 2, 'clients': []}),
            'streamSettings': json.dumps({'network': 'hysteria', 'security': 'tls',
                'hysteriaSettings': {'version': 2}, 'tlsSettings': {'serverName': manifest['config']['domain'],
                    'alpn': ['h3'], 'certificates': [{'certificateFile': str(cert), 'keyFile': str(key), 'oneTimeLoading': False}],
                    'settings': {'allowInsecure': False}},
                'externalProxy': [{'forceTls': 'same', 'dest': manifest['endpoint'], 'port': manifest['config']['proxy_port'], 'remark': ''}]}),
            'sniffing': json.dumps({'enabled': True, 'destOverride': ['http', 'tls', 'quic'], 'routeOnly': True})}


def management_rule(template, manifest, inbound_tag):
    direct = [o for o in template.get('outbounds', []) if o.get('tag') == 'direct' and o.get('protocol') == 'freedom']
    if len(direct) != 1:
        raise Error('Missing unique native freedom management outbound')
    return {'type': 'field', 'inboundTag': [inbound_tag], 'network': 'tcp',
            'ip': [manifest['endpoint'] + '/32'], 'port': ','.join(map(str, sorted({manifest['management']['ssh_port'], 8443}))),
            'outboundTag': direct[0]['tag']}


def hysteria_health(folder, manifest, api, check_paths=True):
    if not dual_enabled(manifest):
        return
    inbound = next(i for i in api.call('/panel/api/inbounds/list') if i['id'] == manifest['hysteria2']['inbound_id'])
    stream = inbound['streamSettings']
    stream = json.loads(stream) if isinstance(stream, str) else stream
    settings = inbound['settings']
    settings = json.loads(settings) if isinstance(settings, str) else settings
    clients = settings.get('clients', [])
    if (len(clients) != len(device_names(manifest)) or {c.get('email') for c in clients} != set(device_names(manifest)) or
            any(c.get('auth') != manifest['accounts'][c['email']]['auth'] for c in clients)):
        raise Error('Unexpected native Hysteria2 account authorization')
    tls = stream.get('tlsSettings', {})
    cert = Path(folder) / manifest['hysteria2']['certificate_file']
    key = Path(folder) / manifest['hysteria2']['key_file']
    if (inbound.get('protocol') != 'hysteria' or inbound.get('enable') is not True or inbound.get('port') != manifest['config']['proxy_port'] or
            stream.get('network') != 'hysteria' or stream.get('security') != 'tls' or
            stream.get('hysteriaSettings', {}).get('version') != 2 or settings.get('version') != 2 or
            tls.get('serverName') != manifest['config']['domain'] or tls.get('alpn') != ['h3'] or
            tls.get('settings', {}).get('allowInsecure') is not False or
            (check_paths and tls.get('certificates') != [{'certificateFile': str(cert.absolute()), 'keyFile': str(key.absolute()), 'oneTimeLoading': False}])):
        raise Error('Unexpected native Hysteria2 transport/TLS configuration')
    if not check_paths:
        certificates = tls.get('certificates', [])
        if (len(certificates) != 1 or certificates[0].get('oneTimeLoading') is not False or
                any(not isinstance(certificates[0].get(field), str) or not Path(certificates[0][field]).is_absolute() or
                    not certificates[0][field].endswith('/' + manifest['hysteria2'][meta])
                    for field, meta in (('certificateFile', 'certificate_file'), ('keyFile', 'key_file')))):
            raise Error('Invalid archived Hysteria2 certificate paths')
    hysteria_certificate_paths(folder, manifest)
    with contextlib.closing(sqlite3.connect('file:' + str(Path(folder) / 'db/x-ui.db') + '?mode=ro', uri=True)) as db:
        row = db.execute("select value from settings where key='xrayTemplateConfig'").fetchone()
    template = json.loads(row[0]) if row else {}
    if inbound.get('tag') != 'SELF-h2':
        raise Error('Changed owned Hysteria2 inbound tag')
    rule = management_rule(template, manifest, inbound['tag'])
    rules = template.get('routing', {}).get('rules', [])
    if not rules or rules[0] != rule or sum(r.get('inboundTag') == [inbound['tag']] for r in rules) != 1:
        raise Error('Missing or changed exact Hysteria2 management route')


def enable_hysteria(folder, manifest, ssh_port):
    """Native-only mutation. Caller owns the complete DB rollback transaction."""
    if type(ssh_port) is not int or not 1 <= ssh_port <= 65535 or ssh_port in required_ports(manifest['config']):
        raise Error('Invalid verified management SSH port')
    if 'management' in manifest and manifest['management'] != {'ssh_port': ssh_port}:
        raise Error('Changed verified management SSH port')
    api = wait_api(manifest)
    owned_host_group(folder, api, manifest)
    manifest['accounts'] = snapshot_accounts(Path(folder) / 'db/x-ui.db', manifest)
    cert, key = hysteria_certificate_paths(folder, manifest)
    was_dual = dual_enabled(manifest)
    manifest['management'] = {'ssh_port': ssh_port}
    if not was_dual:
        old_groups = {g['groupId'] for g in api.call('/panel/api/hosts/list')}
        api.call('/panel/api/inbounds/add', hysteria_inbound(manifest, cert, key))
        inbounds = api.call('/panel/api/inbounds/list')
        found = [i for i in inbounds if i.get('remark') == 'SELF-h2' and i.get('protocol') == 'hysteria']
        groups = [g for g in api.call('/panel/api/hosts/list') if g['groupId'] not in old_groups]
        if len(found) != 1 or len(groups) != 1:
            raise Error('Unexpected native Hysteria2 creation membership')
        inbound = found[0]
        # Attach native global identities; native code generates missing Auth.
        for name in device_names(manifest):
            api.call('/panel/api/clients/' + name + '/attach', {'inboundIds': [inbound['id']]})
        manifest['hysteria2'] = {'schema': 1, 'inbound_id': inbound['id'], 'host_group_id': groups[0]['groupId'],
            'certificate_file': str(cert.relative_to(Path(folder).absolute())), 'key_file': str(key.relative_to(Path(folder).absolute()))}
        # Metadata is not yet populated with Auth: derive it directly from DB.
        manifest['accounts'] = snapshot_accounts(Path(folder) / 'db/x-ui.db', manifest, validate_metadata=False)
    else:
        inbound = next(i for i in api.call('/panel/api/inbounds/list') if i['id'] == manifest['hysteria2']['inbound_id'])
        stream = inbound['streamSettings']
        stream = json.loads(stream) if isinstance(stream, str) else stream
        stream['tlsSettings']['certificates'] = [{'certificateFile': str(cert), 'keyFile': str(key), 'oneTimeLoading': False}]
        inbound['streamSettings'] = json.dumps(stream)
        api.call('/panel/api/inbounds/update/' + str(inbound['id']), inbound)
    with contextlib.closing(sqlite3.connect(Path(folder) / 'db/x-ui.db')) as db:
        template = json.loads(db.execute("select value from settings where key='xrayTemplateConfig'").fetchone()[0])
    rules = template.setdefault('routing', {}).setdefault('rules', [])
    if inbound.get('tag') != 'SELF-h2':
        raise Error('Unexpected native Hysteria2 inbound tag')
    expected = management_rule(template, manifest, inbound['tag'])
    # Replace only our exact previous management rule, never another exception.
    rules[:] = [r for r in rules if not (r.get('inboundTag') == expected['inboundTag'] and r.get('outboundTag') == 'direct' and
        r.get('ip') == [manifest['endpoint'] + '/32'] and r.get('network') == 'tcp' and r.get('port') == expected['port'])]
    rules.insert(0, expected)
    ctl('stop', PANEL)
    settings_write(Path(folder) / 'db/x-ui.db', dict(backend_settings(manifest), xrayTemplateConfig=json.dumps(template)))
    ctl('start', PANEL)
    api = wait_api(manifest)
    owned_host_group(folder, api, manifest)
    hysteria_health(folder, manifest, api)
    core_check(folder)


def configure_native(folder, manifest, fresh, rotate=False):
    if fresh:
        # admin/admin exists only in the loopback candidate; change via native API,
        # never place a password in process argv or systemd Environment.
        bootstrap = dict(manifest, admin_user='admin')
        api = wait_api(bootstrap, 'admin')
        api.call('/panel/api/setting/updateUser', {'oldUsername': 'admin', 'oldPassword': 'admin',
                 'newUsername': manifest['admin_user'], 'newPassword': manifest['admin_password']})
    api = wait_api(manifest)
    previous_endpoint = manifest['endpoint']
    if not fresh:
        old_inbound, _ = owned_host_group(folder, api, manifest)  # before any write
        old_stream = old_inbound['streamSettings']
        old_stream = json.loads(old_stream) if isinstance(old_stream, str) else old_stream
        if cdn_enabled(manifest):
            h2 = next(i for i in api.call('/panel/api/inbounds/list') if i['id'] == manifest['hysteria2']['inbound_id'])
            previous_endpoint = native_object(h2['streamSettings'])['externalProxy'][0]['dest']
        else:
            previous_endpoint = old_stream['externalProxy'][0]['dest']
    if fresh:
        template = api.call('/panel/api/setting/getDefaultJsonConfig')
        template = json.loads(template) if isinstance(template, str) else template
    else:
        with contextlib.closing(sqlite3.connect(folder / 'db/x-ui.db')) as db:
            row = db.execute("select value from settings where key='xrayTemplateConfig'").fetchone()
        if not row:
            raise Error('Missing native Xray template in restored database')
        template = json.loads(row[0])
    template['log'] = {'access': 'none', 'error': 'none', 'loglevel': 'none'}
    routing = template.setdefault('routing', {})
    routing['domainStrategy'] = 'IPIfNonMatch'
    routing.setdefault('rules', []).insert(0, {'type': 'field', 'ip': ['geoip:private', manifest['endpoint'] + '/32'], 'outboundTag': 'blocked'})
    if dual_enabled(manifest):
        # A restored target may have a different verified SSH port. The closed
        # archive already attested the old rule; remove only its exact shape.
        old_rules = [r for r in routing['rules'] if r.get('inboundTag') == ['SELF-h2']]
        if len(old_rules) != 1 or old_rules[0].get('ip') != [previous_endpoint + '/32'] or old_rules[0].get('network') != 'tcp' or old_rules[0].get('outboundTag') != 'direct':
            raise Error('Unexpected restored Hysteria2 management rule')
        routing['rules'] = [r for r in routing['rules'] if r != old_rules[0]]
        routing['rules'].insert(0, management_rule(template, manifest, 'SELF-h2'))
    if not any(o.get('tag') == 'blocked' and o.get('protocol') == 'blackhole' for o in template.get('outbounds', [])):
        raise Error('Unexpected native template: missing blocked outbound')
    # Stop before changing persistent template/settings; cached panel state is
    # refreshed by restart. Inbounds/accounts still use the native API.
    ctl('stop', PANEL)
    settings_write(folder / 'db/x-ui.db', dict(backend_settings(manifest), xrayTemplateConfig=json.dumps(template)))
    ctl('start', PANEL)
    api = wait_api(manifest)
    if fresh:
        if api.call('/panel/api/inbounds/list') or api.call('/panel/api/hosts/list'):
            raise Error('Fresh database unexpectedly contains inbound/Host state')
        api.call('/panel/api/inbounds/add', inbound_for(manifest))
        groups = api.call('/panel/api/hosts/list') or []
        if len(groups) != 1:
            raise Error('Fresh native Host creation has unexpected membership')
        manifest['host_group_id'] = groups[0]['groupId']
        owned_host_group(folder, api, manifest)
    else:
        original, group = owned_host_group(folder, api, manifest)
        # Preserve the full restored inbound and all its policies. The DB is
        # authoritative; wrapper account metadata is never a write-back input.
        inbound = copy.deepcopy(original)
        stream = inbound['streamSettings']
        stream = json.loads(stream) if isinstance(stream, str) else stream
        external = stream.get('externalProxy', [])
        if not cdn_enabled(manifest) and (len(external) != 1 or inbound['port'] != manifest['config']['proxy_port']):
            raise Error('Unexpected restored native endpoint configuration')
        if not cdn_enabled(manifest):
            external[0]['dest'] = manifest['endpoint']
        if rotate and not cdn_enabled(manifest):
            reality = stream['realitySettings']
            reality.update(manifest['reality'])
            reality.setdefault('settings', {})['publicKey'] = manifest['reality']['publicKey']
        inbound['streamSettings'] = json.dumps(stream)
        desired_group = copy.deepcopy(group)
        if not cdn_enabled(manifest):
            desired_group['hosts'] = [manifest['endpoint'] + ':' + str(manifest['config']['proxy_port'])]
        if desired_group['hosts'] != group['hosts']:
            # Hosts, not externalProxy, is authoritative for native feeds. Send
            # the complete object and change the address only, never its policy.
            api.call('/panel/api/hosts/update/' + group['groupId'], desired_group)
        api.call('/panel/api/inbounds/update/' + str(inbound['id']), inbound)
        if api.call('/panel/api/hosts/get/' + group['groupId']) != desired_group:
            raise Error('Native Host update changed unexpected fields')
        if dual_enabled(manifest):
            h2 = next(i for i in api.call('/panel/api/inbounds/list') if i['id'] == manifest['hysteria2']['inbound_id'])
            h2group = api.call('/panel/api/hosts/get/' + manifest['hysteria2']['host_group_id'])
            desired_h2 = copy.deepcopy(h2group)
            desired_h2['hosts'] = [manifest['endpoint'] + ':' + str(manifest['config']['proxy_port'])]
            if desired_h2 != h2group:
                api.call('/panel/api/hosts/update/' + h2group['groupId'], desired_h2)
            h2stream = h2['streamSettings']
            h2stream = json.loads(h2stream) if isinstance(h2stream, str) else h2stream
            h2stream['externalProxy'][0]['dest'] = manifest['endpoint']
            cert, key = hysteria_certificate_paths(folder, manifest)
            h2stream['tlsSettings']['certificates'] = [{'certificateFile': str(cert), 'keyFile': str(key), 'oneTimeLoading': False}]
            h2['streamSettings'] = json.dumps(h2stream)
            api.call('/panel/api/inbounds/update/' + str(h2['id']), h2)
        owned_host_group(folder, api, manifest)
        if rotate:
            # Each complete fresh native object carries quota/expiry/reset and
            # disabled state. Change only UUID, never reconstruct a Client DTO.
            for device in device_names(manifest):
                current = next(i for i in api.call('/panel/api/inbounds/list') if i['remark'] == main_tag(manifest))['settings']
                current = json.loads(current) if isinstance(current, str) else current
                account = next(c for c in current['clients'] if c['email'] == device)
                # The native update endpoint has one policy outside Client DTO:
                # LimitHwid. Preserve it from its own complete native record.
                detail = api.call('/panel/api/clients/get/' + device)['client']
                account['limitHwid'] = detail['limitHwid']
                if dual_enabled(manifest):
                    account['auth'] = detail['auth']
                account['id'] = str(uuid.uuid4())
                if dual_enabled(manifest):
                    account['auth'] = secrets.token_hex(24)
                api.call('/panel/api/clients/update/' + device, account)
    api.call('/panel/api/server/restartXrayService', {})
    for _ in range(60):
        config = folder / 'bin/config.json'
        if config.exists():
            try:
                core_check(folder)
                return
            except Error:
                pass
        time.sleep(0.5)
    raise Error('Generated native Xray configuration failed core validation')


def core_check(folder):
    run([folder / 'bin/xray-linux-amd64', 'run', '-test', '-config', folder / 'bin/config.json'],
        env=dict(os.environ, XRAY_LOCATION_ASSET=str(folder / 'bin')), timeout=30)


def feed_paths(manifest):
    return ['/' + fmt + '/' + manifest['accounts'][device]['subId']
            for device in device_names(manifest) if manifest['accounts'][device]['enable'] for fmt in ('sub', 'mihomo')]


def caddyfile(manifest, published, bootstrap_domain=None):
    domain = manifest['config']['domain']
    paths = ' '.join(feed_paths(manifest))
    routes = f'''    @feed {{
        method GET HEAD
        path {paths}
    }}
    handle @feed {{
        header Cache-Control "no-store"
        header Pragma "no-cache"
        reverse_proxy 127.0.0.1:{SUB_PORT} {{
            header_down -Server
            header_down -ETag
        }}
    }}
''' if published and paths else ''
    contact = manifest['config']['acme_email']
    email_option = '    email ' + json.dumps(contact) + '\n' if contact else ''
    extra = ''
    if cdn_enabled(manifest) or bootstrap_domain is not None:
        cdn = manifest['tcp_cdn']['domain'] if cdn_enabled(manifest) else hostname(bootstrap_domain)
        port = CDN_PORT if cdn_enabled(manifest) else 8443
        proxy = f'''    @xhttp path {XHTTP_PATH} {XHTTP_PATH}*
    handle @xhttp {{
        reverse_proxy 127.0.0.1:{XHTTP_PORT} {{
            flush_interval -1
        }}
    }}
''' if cdn_enabled(manifest) else ''
        extra = f'''https://{cdn}:{port} {{
    tls {{
        issuer acme {{
            dir https://acme-v02.api.letsencrypt.org/directory
            disable_tlsalpn_challenge
        }}
    }}
{proxy}    respond 404
}}
http://{cdn}:80 {{
    respond 404
}}
'''
    return f'''{{
    admin off
    auto_https disable_redirects
    http_port 80
    https_port 8443
{email_option}    log default {{
        output discard
    }}
    servers {{
        protocols h1 h2
    }}
}}
https://{domain}:8443 {{
    tls {{
        issuer acme {{
            dir https://acme-v02.api.letsencrypt.org/directory
            disable_tlsalpn_challenge
        }}
    }}
{routes}    respond 404
}}
http://{domain}:80 {{
    respond 404
}}
{extra}'''


def front_config(folder, manifest, published, bootstrap_domain=None):
    candidate = folder / 'Caddyfile.candidate'
    atomic(candidate, caddyfile(manifest, published, bootstrap_domain))
    run([folder / 'bin/caddy', 'validate', '--config', candidate, '--adapter', 'caddyfile'],
        env=front_env(folder), timeout=30)
    os.replace(candidate, folder / 'Caddyfile')


def front_env(folder):
    return dict(os.environ, HOME=str(folder / 'caddy'), XDG_DATA_HOME=str(folder / 'caddy/data'),
                XDG_CONFIG_HOME=str(folder / 'caddy/config'))


def get_feed(manifest, device, fmt):
    path = '/' + fmt + '/' + manifest['accounts'][device]['subId']
    request = Request(f'http://127.0.0.1:{SUB_PORT}' + path, headers={'Host': manifest['config']['domain']})
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=15) as response:
            data = response.read(4 * 1024 * 1024 + 1)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise Error('Native subscription retrieval failed (path withheld)') from exc
    if not data or len(data) > 4 * 1024 * 1024:
        raise Error('Empty or oversized native subscription')
    return data


def validate_feed_scope(manifest, device, data, fmt):
    current = manifest['accounts'][device]
    others = [manifest['accounts'][d][key] for d in device_names(manifest) if d != device
              for key in (('id', 'auth') if dual_enabled(manifest) else ('id',))]
    if fmt == 'sub':
        try:
            text = base64.b64decode(data, validate=True).decode()
            links = text.strip().splitlines()
            if len(links) != (2 if dual_enabled(manifest) else 1):
                raise ValueError()
            parsed_links = [urlsplit(link) for link in links]
            vless = [link for link in parsed_links if link.scheme == 'vless']
            if len(vless) != 1:
                raise ValueError()
            parsed = vless[0]
            query = parse_qs(parsed.query, keep_blank_values=True)
            valid = (parsed.scheme == 'vless' and uuid.UUID(parsed.username) == uuid.UUID(current['id']) and
                     parsed.hostname == manifest['endpoint'] and parsed.port == manifest['config']['proxy_port'] and
                     query.get('security') == ['reality'] and query.get('flow') == ['xtls-rprx-vision'] and
                     query.get('pbk') == [manifest['reality']['publicKey']] and
                     query.get('sid') == [manifest['reality']['shortIds'][0]] and
                     query.get('sni') == [manifest['config']['reality_target']])
            if cdn_enabled(manifest):
                meta = manifest['tcp_cdn']
                allowed = {'security', 'type', 'encryption', 'sni', 'host', 'path', 'mode', 'alpn', 'fp', 'flow', 'extra'}
                valid = (uuid.UUID(parsed.username) == uuid.UUID(current['id']) and parsed.password is None and
                    parsed.hostname == meta['domain'] and parsed.port == CDN_PORT and unquote(parsed.fragment) == 'SELF-cdn' and
                    set(query) <= allowed and all(len(v) == 1 for v in query.values()) and
                    all(query.get(k) == [v] for k, v in {'security': 'tls', 'type': 'xhttp', 'sni': meta['domain'],
                        'host': meta['domain'], 'path': meta['path'], 'mode': meta['mode'], 'alpn': 'h2'}.items()) and
                    query.get('extra') == [json.dumps({'mode': meta['mode']}, separators=(',', ':'))] and
                    query.get('flow', ['']) == [''] and query.get('encryption', ['none']) == ['none'])
            if dual_enabled(manifest):
                h2 = [link for link in parsed_links if link.scheme == 'hysteria2']
                if len(h2) != 1:
                    raise ValueError('Missing Hysteria2 URI')
                link = h2[0]
                params = parse_qs(link.query, keep_blank_values=True)
                valid = valid and (unquote(link.username or '') == current['auth'] and link.password is None and
                    link.hostname == manifest['endpoint'] and link.port == manifest['config']['proxy_port'] and
                    params.get('sni') == [manifest['config']['domain']] and params.get('alpn') == ['h3'] and
                    params.get('security') == ['tls'] and not any(params.get(k, ['0']) != ['0'] for k in ('insecure', 'allowInsecure', 'skip-cert-verify')))
                if cdn_enabled(manifest):
                    valid = valid and (unquote(link.fragment) == 'SELF-h2' and all(len(v) == 1 for v in params.values()) and
                        set(params) <= {'security', 'sni', 'alpn', 'fp', 'insecure', 'allowInsecure', 'skip-cert-verify'})
            if not valid:
                raise ValueError('URI identity mismatch')
        except (ValueError, KeyError, TypeError, AttributeError, UnicodeError) as exc:
            raise Error('Native URI subscription does not match device/node identity') from exc
    else:
        text = data.decode()
        if not all(s in text for s in ('proxies:', 'proxy-groups:', 'rules:', current['id'], manifest['endpoint'], 'MATCH,PROXY', 'bilibili.com,DIRECT')):
            raise Error('Native Mihomo output is not the expected full routed profile')
        if cdn_enabled(manifest):
            validate_cdn_yaml(manifest, current, text)
        elif dual_enabled(manifest):
            section = re.search(r'(?ms)^proxies:\s*\n(.*?)(?=^[A-Za-z][\w-]*:|\Z)', text)
            if not section:
                raise Error('Missing native dual Mihomo proxies')
            lines = section[1].splitlines()
            starts = [(n, len(line) - len(line.lstrip())) for n, line in enumerate(lines) if re.match(r'^\s*-\s+[\w-]+:', line)]
            indent = min((i for _, i in starts), default=-1)
            indices = [n for n, i in starts if i == indent] + [len(lines)]
            nodes = []
            for start, end in zip(indices, indices[1:]):
                fields = {}
                for line in lines[start:end]:
                    match = re.match(r'^\s*(?:-\s+)?([\w-]+):\s*(.*?)\s*$', line)
                    if match and match[2]:
                        value = match[2]
                        try:
                            value = json.loads(value)
                        except ValueError:
                            value = value.strip("\"'")
                        fields[match[1]] = value
                nodes.append(fields)
            if (len(nodes) != 2 or {n.get('type') for n in nodes} != {'vless', 'hysteria2'} or
                    any(n.get('server') != manifest['endpoint'] or n.get('port') != manifest['config']['proxy_port'] for n in nodes)):
                raise Error('Unexpected native dual Mihomo endpoints/types')
            reality = next(n for n in nodes if n['type'] == 'vless')
            h2 = next(n for n in nodes if n['type'] == 'hysteria2')
            if (reality.get('uuid') != current['id'] or h2.get('password') != current['auth'] or
                    h2.get('sni') != manifest['config']['domain'] or h2.get('skip-cert-verify') not in (None, False)):
                raise Error('Unexpected native dual Mihomo authentication/TLS')
    if any(other in text for other in others):
        raise Error('Cross-device credentials present in native subscription')


def native_yaml_scalar(value):
    """The pinned emitter's scalar subset; aliases/tags/complex YAML refuse."""
    if value.startswith('[') and value.endswith(']'):
        return [native_yaml_scalar(v.strip()) for v in value[1:-1].split(',')] if value[1:-1].strip() else []
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    if value.startswith('"'):
        return json.loads(value)
    if value in ('true', 'false', 'null'):
        return json.loads(value)
    if re.fullmatch(r'-?\d+', value):
        return int(value)
    if not value or value[0] in '&*!{|>' or ' #' in value:
        raise ValueError('Unsupported native YAML scalar')
    return value


def native_yaml_nodes(text):
    """Read emitted proxy fields only, without a second node encoder/dependency."""
    section = re.search(r'(?ms)^proxies:\s*\n(.*?)(?=^[A-Za-z][\w-]*:|\Z)', text)
    if not section:
        raise Error('Missing native CDN Mihomo proxies')
    lines = [line for line in section[1].splitlines() if line.strip()]
    starts = [(n, len(s) - len(s.lstrip())) for n, s in enumerate(lines) if re.match(r'^\s*-\s+[\w-]+:', s)]
    indent = min((i for _, i in starts), default=-1)
    indices = [n for n, i in starts if i == indent] + [len(lines)]
    nodes = []
    try:
        for start, end in zip(indices, indices[1:]):
            node, stack = {}, []
            for index, line in enumerate(lines[start:end]):
                field_indent = len(line) - len(line.lstrip()) + (2 if index == 0 else 0)
                raw = line.strip()[2:] if index == 0 else line.strip()
                # Pinned native YAML uses indentless block sequences: the
                # '- h2' item aligns with its 'alpn:' key, not one level deeper.
                while stack and (field_indent < stack[-1][0] or
                        (field_indent == stack[-1][0] and not (raw.startswith('- ') and isinstance(stack[-1][1], list)))):
                    stack.pop()
                parent = stack[-1][1] if stack else node
                if raw.startswith('- '):
                    if not isinstance(parent, list):
                        raise ValueError()
                    parent.append(native_yaml_scalar(raw[2:].strip()))
                    continue
                match = re.fullmatch(r'([\w-]+):(?:\s+(.*))?', raw)
                if not match or not isinstance(parent, dict) or match[1] in parent:
                    raise ValueError()
                key, value = match[1], match[2]
                if value is None or value == '':
                    following = lines[start + index + 1].strip() if start + index + 1 < end else ''
                    child = [] if following.startswith('- ') else {}
                    parent[key] = child
                    stack.append((field_indent, child))
                else:
                    parent[key] = native_yaml_scalar(value)
            nodes.append(node)
    except (ValueError, TypeError) as exc:
        raise Error('Unsupported or ambiguous native CDN Mihomo proxy fields') from exc
    return nodes


def validate_cdn_yaml(manifest, current, text):
    nodes = native_yaml_nodes(text)
    if len(nodes) != 2 or {n.get('name') for n in nodes} != {'SELF-cdn', 'SELF-h2'}:
        raise Error('Unexpected native CDN Mihomo role membership')
    cdn = next(n for n in nodes if n['name'] == 'SELF-cdn')
    h2 = next(n for n in nodes if n['name'] == 'SELF-h2')
    meta = manifest['tcp_cdn']
    allowed = {'name', 'type', 'server', 'port', 'uuid', 'udp', 'network', 'tls', 'servername', 'alpn',
               'xhttp-opts', 'client-fingerprint', 'flow', 'skip-cert-verify'}
    expected = {'type': 'vless', 'server': meta['domain'], 'port': CDN_PORT, 'uuid': current['id'], 'network': 'xhttp',
                'tls': True, 'servername': meta['domain'], 'alpn': ['h2'],
                'xhttp-opts': {'host': meta['domain'], 'path': meta['path'], 'mode': meta['mode']}}
    if (not set(cdn) <= allowed or any(cdn.get(k) != v for k, v in expected.items()) or cdn.get('tls') is not True or
            cdn.get('flow', '') != '' or cdn.get('skip-cert-verify', False) is not False):
        raise Error('Unexpected native CDN Mihomo authentication/transport/TLS')
    expected_h2 = {'type': 'hysteria2', 'server': manifest['endpoint'], 'port': manifest['config']['proxy_port'],
                   'password': current['auth'], 'sni': manifest['config']['domain'], 'alpn': ['h3']}
    if (not set(h2) <= {'name', 'type', 'server', 'port', 'password', 'udp', 'sni', 'alpn', 'skip-cert-verify', 'client-fingerprint'} or
            any(h2.get(k) != v for k, v in expected_h2.items()) or h2.get('skip-cert-verify', False) is not False):
        raise Error('Unexpected preserved Hysteria2 Mihomo authentication/TLS')


def binary_health(folder):
    stored = read_json(folder / 'binary-hashes.json')
    if any(digest(folder / 'bin' / name) != sha for name, sha in stored.items()):
        raise Error('Installed binary/geodata drift; no automatic upgrade or repair')


def backend_health(folder, manifest):
    binary_health(folder)
    api = API(manifest)
    expected_core = read_json(folder / 'release-lock.json')['core']['version']
    for attempt in range(20):
        core = (api.call('/panel/api/server/status') or {}).get('xray', {})
        if core.get('state') == 'running' and str(core.get('version', '')).lstrip('v') == expected_core:
            break
        time.sleep(0.5)
    else:
        raise Error('Native core is not running the pinned version')
    inbound, _ = owned_host_group(folder, api, manifest)
    settings = inbound['settings']
    clients = (json.loads(settings) if isinstance(settings, str) else settings)['clients']
    if len(clients) != len(device_names(manifest)) or {c['email'] for c in clients} != set(device_names(manifest)):
        raise Error('Unexpected native account membership; manual ownership review required')
    # Inbound DTOs are protocol projections: VLESS does not carry native Auth.
    # Read credential authority from global records and cross-check the VLESS
    # projection, then H2's projection and both genuine native feeds below.
    authoritative = snapshot_accounts(folder / 'db/x-ui.db', manifest)
    for client in clients:
        uuid.UUID(client['id'])
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', client['subId']) or not isinstance(client['enable'], bool):
            raise Error('Invalid native account identity')
        if any(client[k] != authoritative[client['email']][k] for k in ('id', 'subId', 'email', 'enable', 'flow')):
            raise Error('Native VLESS projection disagrees with global account authority')
    manifest['accounts'] = authoritative
    device_names(manifest)
    hysteria_health(folder, manifest, api)
    if cdn_enabled(manifest):
        cdn_certificate_paths(folder, manifest)
    core_check(folder)
    outputs = {}
    for device in device_names(manifest):
        if not manifest['accounts'][device]['enable']:
            continue
        for fmt in ('sub', 'mihomo'):
            raw = get_feed(manifest, device, fmt)
            validate_feed_scope(manifest, device, raw, fmt)
            outputs[device + ('.txt' if fmt == 'sub' else '.yaml')] = raw
    return outputs


def tls_probe(manifest):
    # Default public CA validation, hostname verification, no insecure fallback.
    tls_hostname_probe(manifest['config']['domain'], 8443)
    if cdn_enabled(manifest):
        tls_hostname_probe(manifest['tcp_cdn']['domain'], CDN_PORT)


def tls_hostname_probe(domain, port):
    ctx = ssl.create_default_context()
    with socket.create_connection(('127.0.0.1', port), timeout=10) as raw:
        with ctx.wrap_socket(raw, server_hostname=domain) as stream:
            stream.sendall(('GET / HTTP/1.1\r\nHost: ' + domain + '\r\nConnection: close\r\n\r\n').encode())
            if not stream.recv(1024).startswith(b'HTTP/1.1 404'):
                raise Error('Unexpected TLS front root response')


def wait_tls(manifest, pending=False):
    for _ in range(120):
        if pending:
            check_lease()
        try:
            tls_probe(manifest)
            return
        except ssl.SSLCertVerificationError:
            raise  # An invalid certificate is not a listener-readiness race.
        except (OSError, ssl.SSLError):
            time.sleep(2)
    raise Error('No publicly trusted HTTPS certificate; feeds remain unpublished; check grey DNS/HTTP01:80')


def bootstrap_cdn_certificate(folder, manifest, domain):
    """Transient issuance belongs only to this transaction's existing deadline."""
    check_lease()
    front_config(folder, manifest, False, bootstrap_domain=domain)
    ctl('start', FRONT)
    try:
        for _ in range(120):
            check_lease()
            try:
                tls_hostname_probe(domain, 8443)
                certificate_paths(folder, domain)
                return
            except ssl.SSLCertVerificationError:
                raise
            except (OSError, ssl.SSLError):
                time.sleep(2)
        raise Error('No publicly trusted CDN certificate within rollback lease; check grey DNS/HTTP01:80')
    finally:
        ctl('stop', FRONT)


def ctl(*args):
    if args[0] in ('start', 'restart', 'stop', 'enable', 'disable'):
        check_units(require_all=False)
    return run(['systemctl', *args], timeout=60)


def unit_texts():
    active = ROOT / 'active'
    common = '[Unit]\nAfter=network-online.target qd-3x-ui-recover.service\nWants=network-online.target\n'
    common += '[Service]\nType=simple\nUMask=0077\nRestart=on-failure\nRestartSec=3\nStandardOutput=null\nStandardError=null\n'
    common += f'ExecStartPre=/usr/bin/python3 {ROOT}/host.py guard\nWorkingDirectory={active}\n'
    panel = common + f'Environment=XUI_DB_FOLDER={active}/db XUI_BIN_FOLDER={active}/bin XUI_LOG_FOLDER={active}/logs XUI_DB_TYPE=sqlite XUI_LOG_LEVEL=error XUI_DEBUG=false XUI_ENABLE_FAIL2BAN=false\nExecStart={active}/bin/x-ui run\n[Install]\nWantedBy=multi-user.target\n'
    front = common + f'Environment=HOME={active}/caddy XDG_DATA_HOME={active}/caddy/data XDG_CONFIG_HOME={active}/caddy/config\nExecStart={active}/bin/caddy run --config {active}/Caddyfile --adapter caddyfile\n[Install]\nWantedBy=multi-user.target\n'
    recovery = f'[Unit]\nDescription=Recover interrupted owned 3x-ui transaction\nBefore={PANEL} {FRONT}\n[Service]\nType=oneshot\nExecStart=/usr/bin/python3 {ROOT}/host.py recover\n[Install]\nWantedBy=multi-user.target\n'
    timer = f'[Unit]\nDescription=Check persistent 3x-ui transaction deadline\n[Timer]\nOnBootSec=30s\nOnUnitActiveSec=30s\nUnit={RECOVER}\n[Install]\nWantedBy=timers.target\n'
    return dict(zip(UNIT_NAMES, (panel, front, recovery, timer)))


def check_units(require_all=True):
    paths = run(['systemd-analyze', 'unit-paths'], timeout=15).stdout.decode().splitlines()
    if not paths or any(not Path(path).is_absolute() for path in paths):
        raise Error('Cannot attest systemd unit load paths')
    for name, contents in unit_texts().items():
        path = UNITS / name
        if path.is_symlink() or (path.exists() and (not path.is_file() or path.read_text() != contents)):
            raise Error('Foreign or modified owned unit; manual review required')
        if require_all and not path.is_file():
            raise Error('Missing owned service unit')
        stem, kind = name.rsplit('.', 1)
        dropins = [name + '.d', kind + '.d']
        dropins += [stem[:index + 1] + '.' + kind + '.d' for index, char in enumerate(stem) if char == '-']
        for directory in map(Path, paths):
            candidate = directory / name
            if directory.resolve() != UNITS.resolve() and (candidate.exists() or candidate.is_symlink()):
                raise Error('Foreign unit in systemd load path')
            if any((directory / dropin).exists() or (directory / dropin).is_symlink() for dropin in dropins):
                raise Error('Foreign systemd drop-in configuration; no takeover')
            # Our outgoing dependencies are declared in the exact owned
            # fragments. enable creates reverse target.wants links, not edges
            # FROM these four names. Any injected outgoing directory edge is
            # foreign; never follow even a dangling directory/entry symlink.
            for relation in ('wants', 'requires'):
                dependencies = directory / (name + '.' + relation)
                if dependencies.is_symlink() or (dependencies.exists() and
                        (not dependencies.is_dir() or any(dependencies.iterdir()))):
                    raise Error('Foreign systemd dependency edge configuration; no takeover')
        output = run(['systemctl', 'show', name,
            '--property=LoadState,FragmentPath,DropInPaths,SourcePath,NeedDaemonReload,Transient', '--no-pager'], timeout=15).stdout.decode()
        props = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
        if props.get('DropInPaths') or props.get('SourcePath') or props.get('Transient') == 'yes':
            raise Error('Foreign effective/runtime systemd configuration')
        if props.get('LoadState') == 'not-found' and not path.exists() and not require_all:
            continue
        if (props.get('LoadState') != 'loaded' or props.get('FragmentPath') != str(path) or
                props.get('NeedDaemonReload') != 'no'):
            raise Error('Foreign or stale effective systemd fragment')


def process_health():
    """Attest loaded executables, unit ownership and the actual socket boundary."""
    active = ROOT / 'active'
    manifest = read_json(active / 'manifest.json')
    config = manifest['config']
    ports = required_ports(config, manifest)
    check_units()
    for service in (PANEL, FRONT):
        ctl('is-active', '--quiet', service)
    found = set()
    for line in run(['ss', '-H', '-ltnp'], timeout=10).stdout.decode().splitlines():
        fields = line.split()
        if len(fields) < 4:
            raise Error('Cannot parse runtime listeners')
        port = fields[3].rsplit(':', 1)[-1]
        if port not in {str(p) for p in ports}:
            continue
        found.add(int(port))
        if int(port) in (ADMIN_PORT, SUB_PORT, API_PORT, XHTTP_PORT) and (int(port) != XHTTP_PORT or cdn_enabled(manifest)) and not fields[3].startswith('127.0.0.1:'):
            raise Error('Backend/admin escaped loopback')
        pids = re.findall(r'pid=(\d+)', line)
        if not pids:
            raise Error('Cannot attest listener process ownership')
        unit, binary = tcp_owner(manifest, int(port))
        for pid in pids:
            proc = Path('/proc') / pid
            if unit not in (proc / 'cgroup').read_text() or os.readlink(proc / 'exe') != str(active / 'bin' / binary):
                raise Error('Foreign/stale loaded executable on owned listener')
    if found != set(ports):
        raise Error('One or more declared listeners are missing')
    if dual_enabled(manifest):
        found_udp = False
        for line in run(['ss', '-H', '-lunp'], timeout=10).stdout.decode().splitlines():
            fields = line.split()
            if len(fields) < 4:
                raise Error('Cannot parse runtime UDP listeners')
            if fields[3].rsplit(':', 1)[-1] != str(config['proxy_port']):
                continue
            pids = re.findall(r'pid=(\d+)', line)
            if not pids:
                raise Error('Cannot attest Hysteria2 UDP listener ownership')
            if fields[3] not in ('0.0.0.0:' + str(config['proxy_port']), '*:' + str(config['proxy_port']), '[::]:' + str(config['proxy_port'])):
                raise Error('Hysteria2 UDP listener is not publicly bound')
            for pid in pids:
                proc = Path('/proc') / pid
                if PANEL not in (proc / 'cgroup').read_text() or os.readlink(proc / 'exe') != str(active / 'bin/xray-linux-amd64'):
                    raise Error('Foreign/stale executable on Hysteria2 UDP listener')
            found_udp = True
        if not found_udp:
            raise Error('Declared Hysteria2 UDP listener is missing')


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def check_lease():
    journal = read_json(ROOT / 'pending.json')
    if journal['boot_id'] != boot_id():
        raise Error('Pending lease changed across reboot; recovery required')
    if time.time() >= journal['deadline']:
        raise Error('Pending lease expired; recovery required')


def guard():
    if (ROOT / 'pending.json').exists():
        phase = read_json(ROOT / 'pending.json').get('phase')
        if phase == 'recovering':
            raise Error('Restoration incomplete; dependent services must remain stopped')
        if phase == 'restore-queued':
            return  # Only the fully restored database/front may restart.
        check_lease()


def machine_id():
    return Path('/etc/machine-id').read_text().strip()


def preflight(request):
    config = request['config']
    validate_config(config)
    expected = public_ip(request['endpoint'])
    ports = required_ports(config)
    os_release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if os_release.get('ID', '').strip('"') != 'ubuntu' or os_release.get('VERSION_ID', '').strip('"') != '24.04' or platform.machine() != 'x86_64':
        raise Error('Only dedicated Ubuntu24.04 amd64 is supported')
    if os.geteuid() != 0:
        raise Error('Root or passwordless sudo is required')
    for name in ('python3', 'openssl', 'systemctl', 'systemd-analyze', 'ss', 'nft'):
        if not shutil.which(name):
            raise Error(f'Missing prerequisite {name}; install explicitly before deployment')
    connection = os.environ.get('QD_SSH_CONNECTION', os.environ.get('SSH_CONNECTION', '')).split()
    if len(connection) != 4 or connection[2] != expected:
        raise Error('SSH server address differs from expected endpoint; wrong host/NAT is not supported')
    ssh_port = int(connection[3])
    if ('ssh_port' in request and type(request['ssh_port']) is not int) or request.get('ssh_port', ssh_port) != ssh_port or not 1 <= ssh_port <= 65535:
        raise Error('SSH management port differs from authenticated connection')
    if config['proxy_port'] == ssh_port:
        raise Error('Proxy listener conflicts with the actual SSH port')
    mid = machine_id()
    if not re.fullmatch(r'[0-9a-f]{32}', mid) or request.get('machine_id', mid) != mid:
        raise Error('SSH machine identity changed; refusing wrong host')
    if ROOT.is_symlink():
        raise Error('Refusing symlink owned root')
    owned = (ROOT / 'owner.json').exists()
    if owned:
        marker = read_json(ROOT / 'owner.json')
        if marker != {'format': FORMAT, 'owner': request['owner'], 'machine_id': mid}:
            raise Error('Foreign owned state; no takeover')
        if ROOT.stat().st_uid != 0 or ROOT.stat().st_mode & 0o077:
            raise Error('Owned root must be root:0700')
    elif ROOT.exists():
        raise Error('Unmarked state tree; preserve and inspect, never adopt implicitly')
    for path in ('/var/lib/quick-deploy-vps', '/etc/x-ui', '/usr/local/x-ui', '/opt/3x-ui', '/etc/sing-box/config.json'):
        if Path(path).exists() or Path(path).is_symlink():
            raise Error('Legacy/foreign proxy state detected; explicit migration required, nothing deleted')
    if not owned and any((UNITS / name).exists() or (UNITS / name).is_symlink() for name in UNIT_NAMES):
        raise Error('Foreign unit name conflict')
    current = read_json(ROOT / 'active/manifest.json') if (ROOT / 'active/manifest.json').is_file() else None
    ports = required_ports(config, current)
    if request.get('feature') == 'tcp-cdn' and XHTTP_PORT not in ports:
        ports = (*ports, XHTTP_PORT)
    check_units(require_all=current is not None)
    # We never change the firewall. Unknown rules might drop HTTP01/feed/Reality
    # or protect services the operator forgot about. Refuse rather than flush.
    ruleset = json.loads(run(['nft', '-j', 'list', 'ruleset'], timeout=15).stdout)
    if any('metainfo' not in item for item in ruleset.get('nftables', [])):
        raise Error('Existing firewall requires explicit review; wrapper never flushes or opens it')
    listeners = run(['ss', '-H', '-ltnp'], timeout=10).stdout.decode()
    for line in listeners.splitlines():
        columns = line.split()
        if len(columns) < 4:
            raise Error('Cannot parse listener inventory')
        port = columns[3].rsplit(':', 1)[-1]
        if port not in {str(p) for p in ports}:
            continue
        pids = re.findall(r'pid=(\d+)', line)
        if (int(port) == XHTTP_PORT and request.get('feature') == 'tcp-cdn' and (current is None or not cdn_enabled(current))):
            raise Error('XHTTP loopback2080 is already in use; no port stealing')
        if not owned or not pids:
            raise Error('Required listener port is already in use; no port stealing')
        for pid in pids:
            cgroup = Path('/proc') / pid / 'cgroup'
            group = cgroup.read_text() if cgroup.exists() else ''
            service, binary = tcp_owner(current or {'config': config}, int(port))
            if service not in group or (current is not None and os.readlink(Path('/proc') / pid / 'exe') != str(ROOT / 'active/bin' / binary)):
                raise Error('Foreign process on an owned listener port')
        if (int(port) in (ADMIN_PORT, SUB_PORT, API_PORT) or (int(port) == XHTTP_PORT and current is not None and cdn_enabled(current))) and not columns[3].startswith('127.0.0.1:'):
            raise Error('Management/backend listener escaped loopback')
    # UDP ownership is independent of the identically numbered TCP listener.
    for line in run(['ss', '-H', '-lunp'], timeout=10).stdout.decode().splitlines():
        columns = line.split()
        if len(columns) < 4:
            raise Error('Cannot parse UDP listener inventory')
        if columns[3].rsplit(':', 1)[-1] != str(config['proxy_port']):
            continue
        pids = re.findall(r'pid=(\d+)', line)
        if not owned or not pids or not (ROOT / 'active/manifest.json').is_file() or not dual_enabled(read_json(ROOT / 'active/manifest.json')):
            raise Error('Required UDP listener port is already in use; no port stealing')
        if any(PANEL not in (Path('/proc') / pid / 'cgroup').read_text() for pid in pids):
            raise Error('Foreign process on owned Hysteria2 UDP port')
    if shutil.disk_usage(ROOT if ROOT.exists() else ROOT.parent).free < 2 * 1024**3:
        raise Error('Require at least2GiB free for verified artifacts, staging and complete off-node export')
    if (ROOT / 'pending.json').exists():
        raise Error('Persistent transaction pending; run recover, never overwrite it')
    return {'machine_id': mid, 'owned': owned, 'endpoint': expected,
            'platform': 'ubuntu-24.04-amd64', 'firewall': 'empty; unchanged', 'ports': list(ports), 'ssh_port': ssh_port}


def check_xhttp_port():
    for line in run(['ss', '-H', '-ltnp'], timeout=10).stdout.decode().splitlines():
        fields = line.split()
        if len(fields) < 4:
            raise Error('Cannot parse listener inventory')
        if fields[3].rsplit(':', 1)[-1] == str(XHTTP_PORT):
            raise Error('XHTTP loopback2080 is already in use; no port stealing')


def check_cdn_dns(domain, endpoint):
    addresses = socket.getaddrinfo(domain, CDN_PORT, type=socket.SOCK_STREAM)
    if not addresses or any(item[0] != socket.AF_INET or item[4][0] != endpoint for item in addresses):
        raise Error('CDN bootstrap requires grey DNS A pointing only to this origin, with no AAAA')


def check_target(config):
    target = hostname(config['reality_target'])
    addresses = {item[4][0] for item in socket.getaddrinfo(target, 443, type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise Error('REALITY target resolves to a private/reserved address')
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.set_alpn_protocols(['h2'])
    with socket.create_connection((sorted(addresses)[0], 443), timeout=15) as raw:
        with context.wrap_socket(raw, server_hostname=target) as stream:
            if stream.version() != 'TLSv1.3' or stream.selected_alpn_protocol() != 'h2':
                raise Error('REALITY target must support verified TLS1.3+h2 from this host')


def safe_unpack(stream, destination):
    """Bounded regular-file archive extraction with no traversal or links."""
    seen, total = set(), 0
    with tarfile.open(fileobj=stream, mode='r|*') as archive:
        for member in archive:
            path = Path(member.name)
            total += member.size
            if (not member.isfile() or path.is_absolute() or '..' in path.parts or
                    not path.parts or member.name in seen or total > MAX_ARCHIVE or len(seen) > 2000):
                raise Error('Unsafe/oversized backup or upload; nothing extracted outside staging')
            seen.add(member.name)
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if target.is_symlink():
                raise Error('Symlink in extraction target')
            with archive.extractfile(member) as source, target.open('xb') as out:
                os.chmod(target, 0o600)
                shutil.copyfileobj(source, out)
    return seen


def archive_tree(source, output):
    with tarfile.open(output, 'w') as archive:
        for path in sorted(source.rglob('*')):
            if path.is_symlink():
                raise Error('Symlink in owned backup tree')
            if path.is_file():
                item = archive.gettarinfo(str(path), str(path.relative_to(source)))
                item.uid = item.gid = 0
                item.uname = item.gname = ''
                item.mode = 0o600
                with path.open('rb') as stream:
                    archive.addfile(item, stream)
    os.chmod(output, 0o600)


def db_snapshot(source, target):
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.closing(sqlite3.connect('file:' + str(source) + '?mode=ro', uri=True)) as db, contextlib.closing(sqlite3.connect(target)) as dest:
        db.backup(dest)
        # The native source uses WAL. Produce one closed standalone database,
        # not an archive that accidentally depends on live -wal/-shm sidecars.
        dest.execute('pragma journal_mode=DELETE')
        if dest.execute('pragma integrity_check').fetchone()[0] != 'ok':
            raise Error('Native SQLite backup integrity failure')
        schema = '\n'.join(row[0] + ';' for row in dest.execute("select sql from sqlite_master where sql is not null order by name"))
        return {'sqlite_version': sqlite3.sqlite_version, 'user_version': dest.execute('pragma user_version').fetchone()[0],
                'schema_sha256': hashlib.sha256(schema.encode()).hexdigest(), 'schema': schema}


def snapshot_accounts(database, manifest=None, validate_metadata=True):
    """Read publication metadata from native authority; legacy archives stay five-field."""
    dual = manifest is not None and dual_enabled(manifest)
    with contextlib.closing(sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True)) as db:
        rows = db.execute('select email,uuid,sub_id,enable,flow' + (',auth' if dual else '') + ' from clients order by email').fetchall()
    source = manifest if validate_metadata else {k: v for k, v in manifest.items() if k not in ('hysteria2', 'tcp_cdn')}
    expected = set(DEVICES if manifest is None else device_names(source))
    if len(rows) != len(expected) or {row[0] for row in rows} != expected:
        raise Error('Unexpected snapshot account membership')
    if manifest is not None:
        with contextlib.closing(sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True)) as db:
            inbounds = db.execute('select id from inbounds').fetchall()
            joins = db.execute('select c.email,ci.inbound_id from client_inbounds ci left join clients c on c.id=ci.client_id').fetchall()
        ids = {row[0] for row in inbounds}
        wanted = {(name, identity) for name in expected for identity in ids}
        if (len(inbounds) != (2 if dual else 1) or len(joins) != len(wanted) or set(joins) != wanted or
                (dual and manifest['hysteria2']['inbound_id'] not in ids)):
            raise Error('Unexpected snapshot account inbound membership')
    if manifest is not None and cdn_enabled(manifest):
        snapshot_host_group(database, manifest)
    result = {r[0]: dict(zip(('email', 'id', 'subId', 'enable', 'flow') + (('auth',) if dual else ()),
                           (r[0], r[1], r[2], bool(r[3]), r[4]) + ((r[5],) if dual else ()))) for r in rows}
    if manifest is not None:
        device_names(dict(manifest, accounts=result))
        if cdn_enabled(manifest) and any(c['flow'] != '' for c in result.values()):
            raise Error('Unexpected Vision flow in CDN global authority')
    return result


def snapshot_host_group(database, manifest):
    """Verify Host identity from an owned DB; old metadata upgrades are read-only."""
    with contextlib.closing(sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True)) as db:
        inbounds = db.execute('select id,user_id,remark,protocol,port from inbounds').fetchall()
        admins = db.execute('select id from users where username=?', (manifest['admin_user'],)).fetchall()
        # No enabled/hidden filter: foreign inactive rows and duplicates count.
        hosts = db.execute('select group_id,inbound_id,address,port from hosts').fetchall()
    dual = dual_enabled(manifest)
    tag = main_tag(manifest)
    wanted = {tag: 'vless', 'SELF-h2': 'hysteria'} if dual else {tag: 'vless'}
    if (len(inbounds) != len(wanted) or len(admins) != 1 or
            {r[2]: r[3] for r in inbounds} != wanted or any(r[1] != admins[0][0] or
                r[4] != (XHTTP_PORT if cdn_enabled(manifest) and r[2] == tag else manifest['config']['proxy_port']) for r in inbounds)):
        raise Error('Unexpected native Host owned-inbound identity')
    if len(hosts) != len(wanted):
        raise Error('Unexpected native Host snapshot membership')
    identities = {}
    for identity, _, remark, _, _ in inbounds:
        matching = [row for row in hosts if row[1] == identity]
        if len(matching) != 1:
            raise Error('Unexpected native Host snapshot membership')
        group_id, inbound_id, address, port = matching[0]
        if (not isinstance(group_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', group_id) or
                address != (manifest['tcp_cdn']['domain'] if cdn_enabled(manifest) and remark == tag else manifest['endpoint']) or port != manifest['config']['proxy_port']):
            raise Error('Unexpected native Host snapshot endpoint/membership')
        identities[remark] = group_id
    if len(set(identities.values())) != len(wanted) or ('host_group_id' in manifest and manifest['host_group_id'] != identities[tag]):
        raise Error('Changed persisted native Host group identity')
    if dual and (identities['SELF-h2'] != manifest['hysteria2']['host_group_id'] or
                 next(r[0] for r in inbounds if r[2] == 'SELF-h2') != manifest['hysteria2']['inbound_id']):
        raise Error('Changed persisted Hysteria2 Host/inbound identity')
    if cdn_enabled(manifest):
        if next(r[0] for r in inbounds if r[2] == tag) != manifest['tcp_cdn']['inbound_id']:
            raise Error('Changed persisted CDN inbound identity')
        # CDN archives require positive transport/Host field evidence, not just
        # two familiar row names. Old DB schemas retain their original reader.
        with contextlib.closing(sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            row = dict(db.execute('select * from inbounds where id=?', (manifest['tcp_cdn']['inbound_id'],)).fetchone())
            host = dict(db.execute('select * from hosts where group_id=?', (manifest['host_group_id'],)).fetchone())
            clients = {c['email']: {'email': c['email'], 'id': c['uuid'], 'subId': c['sub_id'], 'enable': bool(c['enable']), 'flow': c['flow']}
                       for c in db.execute('select email,uuid,sub_id,enable,flow from clients')}
        try:
            projected = native_object(row['settings']).get('clients', [])
            if (len(projected) != len(clients) or {c['email'] for c in projected} != set(clients) or
                    any(any(c.get(k, '' if k == 'flow' else None) != v for k, v in clients[c['email']].items()) for c in projected)):
                raise Error('CDN snapshot projection disagrees with global account authority')
            cdn_inbound_health(dict(row, streamSettings=row['stream_settings'], enable=bool(row['enable']), disableFlow=bool(row['disable_flow'])), manifest)
            cdn_host_health({'groupId': host['group_id'], 'inboundIds': [host['inbound_id']],
                'hosts': [host['address'] + ':' + str(host['port'])], 'port': host['port'], 'security': host['security'],
                'sni': host['sni'], 'hostHeader': host['host_header'], 'path': host['path'],
                'alpn': native_object(host['alpn']), 'allowInsecure': bool(host['allow_insecure']),
                **{key: host.get(column) for key, column in (('verifyPeerCertByName', 'verify_peer_cert_by_name'), ('echConfigList', 'ech_config_list'),
                    ('muxParams', 'mux_params'), ('sockoptParams', 'sockopt_params'), ('finalMask', 'final_mask'),
                    ('vlessRoute', 'vless_route'), ('keepSniBlank', 'keep_sni_blank'),
                    ('overrideSniFromAddress', 'override_sni_from_address'))},
                'pinnedPeerCertSha256': native_object(host.get('pinned_peer_cert_sha256') or 'null'),
                'excludeFromSubTypes': native_object(host.get('exclude_from_sub_types') or 'null')}, manifest)
        except (KeyError, ValueError, TypeError) as exc:
            raise Error('Incomplete native CDN snapshot fields') from exc
    return identities[tag]


def reject_symlinks(root):
    if root.is_symlink():
        raise Error('Symlink in owned export source')
    for directory, names, files in os.walk(root, followlinks=False):
        if any((Path(directory) / name).is_symlink() for name in names + files):
            raise Error('Symlink in owned export source')


def adapter_source():
    return Path(__file__).read_bytes()


def regular_adapter_bytes(path):
    """Read bounded public source without following links or waiting on a FIFO."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Error('Recovery adapter source must be a regular file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise Error('Oversized recovery adapter source')
        return data
    finally:
        os.close(fd)


def read_previous_adapter(expected_sha=None):
    """Optional, pinned public source; never follow a link or block on a FIFO."""
    expected_sha = LEGACY_ADAPTER_SHA256 if expected_sha is None else expected_sha
    if expected_sha not in ADAPTER_PREDECESSORS:
        raise Error('Unreviewed predecessor adapter digest')
    path = ROOT / ('adapter-before-' + expected_sha + '.py')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise Error('Invalid retained predecessor adapter; no service stop') from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Error('Retained predecessor adapter must be a regular file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            # The fixed historical script is small. Bound corrupt/untrusted input.
            data = stream.read(1024 * 1024 + 1)
    finally:
        os.close(fd)
    if len(data) > 1024 * 1024 or hashlib.sha256(data).hexdigest() != expected_sha:
        raise Error('Retained predecessor adapter digest mismatch')
    return data


def retain_previous_adapter(data, expected_sha=None):
    expected_sha = LEGACY_ADAPTER_SHA256 if expected_sha is None else expected_sha
    if expected_sha not in ADAPTER_PREDECESSORS or hashlib.sha256(data).hexdigest() != expected_sha:
        raise Error('Unverified predecessor adapter bytes')
    path = ROOT / ('adapter-before-' + expected_sha + '.py')
    # Publish a complete saved copy without replacing a raced-in unknown input.
    with tempfile.NamedTemporaryFile(prefix='.adapter-before-', dir=ROOT) as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
        try:
            os.link(stream.name, path, follow_symlinks=False)
        except FileExistsError as exc:
            raise Error('Retained predecessor appeared during migration; preserve and inspect') from exc
        directory = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def install_backup_adapter(request=None):
    """Install capable recovery BEFORE publishing any backup stop intent."""
    source = adapter_source() if request is None else base64.b64decode(request['adapter_b64'], validate=True)
    sha = hashlib.sha256(source).hexdigest()
    if request is not None and sha != request.get('adapter_sha256'):
        raise Error('Backup adapter upload digest mismatch')
    stored = ROOT / 'host.py'
    try:
        stored_source = regular_adapter_bytes(stored)
    except (OSError, Error) as exc:
        raise Error('Unknown stored backup adapter; no service stop') from exc
    stored_sha = hashlib.sha256(stored_source).hexdigest()
    if not accepted_adapter_digest(stored_sha, sha):
        raise Error('Unknown stored backup adapter; no service stop')
    # Every explicitly reviewed slot is optional, but any existing input must
    # satisfy the no-link/pinned-byte contract before ANY migration write.
    previous = {pin: read_previous_adapter(pin) for pin in ADAPTER_PREDECESSORS}
    check_units()
    if stored_sha != sha:
        if previous[stored_sha] is None:
            retain_previous_adapter(stored_source, stored_sha)
            previous[stored_sha] = read_previous_adapter(stored_sha)
        atomic(stored, source, 0o700)
    verified = {pin: read_previous_adapter(pin) for pin in ADAPTER_PREDECESSORS}
    if any(previous[pin] is not None and verified[pin] is None for pin in previous):
        raise Error('Retained predecessor disappeared during migration')
    return {ADAPTER_PREDECESSORS[pin]: data for pin, data in verified.items() if data is not None}


def copy_caddy_tree(source, target):
    """Closed, private regular-file tree; never copy a link or special file."""
    source, target = Path(source), Path(target)
    if source.is_symlink() or (source.exists() and not source.is_dir()):
        raise Error('Invalid closed Caddy storage input')
    target.mkdir(mode=0o700)
    # os.walk over a previously unused store yields an exact empty snapshot.
    for directory, names, files in os.walk(source, followlinks=False):
        relative = Path(directory).relative_to(source)
        for name in names:
            path = Path(directory) / name
            if not stat.S_ISDIR(path.lstat().st_mode):
                raise Error('Link/special directory in closed Caddy storage')
            (target / relative / name).mkdir(mode=0o700)
        for name in files:
            path = Path(directory) / name
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise Error('Link/special file in closed Caddy storage')
                with os.fdopen(fd, 'rb', closefd=False) as stream:
                    atomic(target / relative / name, stream.read())
            finally:
                os.close(fd)
    for directory in [*(d for d, _, _ in os.walk(target, topdown=False)), target.parent]:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def snapshot_caddy(active, rollback):
    copy_caddy_tree(active / 'caddy', rollback / 'caddy')


def restore_caddy(active, rollback):
    # Copy before replacing. Hard loss at any point leaves immutable rollback
    # input and recovering guard; the next invocation repeats with consumers off.
    if not (rollback / 'caddy').is_dir() or (rollback / 'caddy').is_symlink():
        raise Error('Missing complete closed Caddy rollback input')
    target = ROOT / ('caddy-restore-' + secrets.token_hex(8))
    copy_caddy_tree(rollback / 'caddy', target)
    current = active / 'caddy'
    if current.is_symlink():
        raise Error('Symlink active Caddy storage cannot be restored')
    if current.exists():
        os.replace(current, ROOT / ('caddy-failed-' + secrets.token_hex(8)))
    os.replace(target, current)
    for path in (active, ROOT):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def service_state(unit):
    return {key: subprocess.run(['systemctl', command, '--quiet', unit], capture_output=True).returncode == 0
            for key, command in [('running', 'is-active'), ('enabled', 'is-enabled')]}


def disarm(path):
    path.unlink(missing_ok=True)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def queue_services(services):
    for unit, state in services.items():
        ctl('enable' if state['enabled'] else 'disable', unit)
        if state['running']:
            ctl('start', '--no-block', unit)
        else:
            ctl('stop', unit)


def services_active(services):
    return all(service_state(unit)['running'] == state['running'] for unit, state in services.items())


def recover_backup():
    path = ROOT / 'backup-stop.json'
    intent = read_json(path)
    manifest = read_json(ROOT / 'active/manifest.json')
    marker = read_json(ROOT / 'owner.json')
    if (intent['owner'] != marker['owner'] or intent['machine_id'] != marker['machine_id'] or
            intent['node_id'] != manifest['node_id']):
        raise Error('Backup stop intent owner/machine/node mismatch')
    check_units()
    services = {FRONT: intent['front']}
    if intent['phase'] != 'queued' or not services_active(services):
        intent['phase'] = 'queued'
        atomic(path, dumps(intent))
        queue_services(services)
        return {'pending': True, 'queued': True, 'backup': True, 'committed': manifest['published']}
    # This is a LATER recovery invocation. The dependent start job has already
    # completed; never wait for it from the oneshot that queued that job.
    try:
        if intent['front']['running']:
            wait_tls(manifest)
    except BaseException:
        ctl('stop', FRONT)
        raise
    disarm(path)
    return {'pending': (ROOT / 'pending.json').exists(), 'backup_restored': True,
            'committed': manifest['published'], 'front_running': intent['front']['running'],
            'machine_id': marker['machine_id'], 'node_id': manifest['node_id']}


def export_backup(request=None):
    if (ROOT / 'pending.json').exists():
        pending = read_json(ROOT / 'pending.json')
        if pending.get('kind') in ('devices', 'transport-upgrade') and pending.get('phase') != 'prepared':
            raise Error('Owned transaction not ready for backup')
        if pending.get('cdn_auth', {}).get('phase') not in (None, 'restored'):
            raise Error('Active auth diagnostic cannot enter an archive')
    if ROOT.is_symlink():
        raise Error('Symlink owned root cannot be exported')
    active = ROOT / 'active'
    reject_symlinks(active)
    if (ROOT / 'host.py').is_symlink():
        raise Error('Symlink recovery adapter cannot be exported')
    manifest = read_json(active / 'manifest.json')
    snapshot_accounts(active / 'db/x-ui.db', manifest)
    binary_health(active)
    verify_artifacts(active / 'artifacts', read_json(active / 'release-lock.json'))
    # Backups must also work for expired/quota/HWID-restricted accounts. Their
    # authoritative snapshot, not successful feed access, defines this export.
    # Caddy storage uses atomic file writes, but its cert/key pair must be taken
    # together. Briefly stop ONLY our subscription front, not Xray or SSH.
    if (ROOT / 'backup-stop.json').exists():
        raise Error('Interrupted backup stop; run recover first')
    previous_adapters = install_backup_adapter(request)
    marker = read_json(ROOT / 'owner.json')
    front = service_state(FRONT)
    atomic(ROOT / 'backup-stop.json', dumps({'owner': marker['owner'], 'machine_id': marker['machine_id'],
           'node_id': manifest['node_id'], 'phase': 'stopping', 'front': front}))
    ctl('stop', FRONT)
    try:
        with tempfile.TemporaryDirectory(prefix='export-', dir=ROOT) as tmp:
            stage = Path(tmp)
            shutil.copytree(active, stage / 'deployment', symlinks=True,
                            ignore=shutil.ignore_patterns('logs', '*.db-wal', '*.db-shm', 'x-ui.db'))
            reject_symlinks(stage / 'deployment')
            metadata = db_snapshot(active / 'db/x-ui.db', stage / 'deployment/db/x-ui.db')
            manifest['accounts'] = snapshot_accounts(stage / 'deployment/db/x-ui.db', manifest)
            manifest['host_group_id'] = snapshot_host_group(stage / 'deployment/db/x-ui.db', manifest)
            atomic(stage / 'deployment/manifest.json', dumps(manifest))
            metadata.update(format=FORMAT, encryption='none', panel_version=read_json(active / 'release-lock.json')['panel']['version'])
            atomic(stage / 'database-schema.sql', metadata.pop('schema'))
            atomic(stage / 'backup.json', dumps(metadata))
            for name in UNIT_NAMES:
                atomic(stage / 'units' / name, (UNITS / name).read_bytes())
            atomic(stage / 'host.py', (ROOT / 'host.py').read_bytes())
            for name, data in previous_adapters.items():
                atomic(stage / name, data)
            archive_tree(stage, ROOT / 'export.tar')
        sha = digest(ROOT / 'export.tar')
        atomic(ROOT / 'export-sha256', sha)
        return ROOT / 'export.tar'
    finally:
        if front['running']:
            ctl('start', FRONT)
            wait_tls(manifest)
        disarm(ROOT / 'backup-stop.json')


def verify_backup(folder, lock):
    meta = read_json(folder / 'backup.json')
    active = folder / 'deployment'
    if meta.get('format') != FORMAT or meta.get('encryption') != 'none' or read_json(active / 'release-lock.json') != lock:
        raise Error('Unknown backup format/release; no implicit schema migration')
    verify_artifacts(active / 'artifacts', lock)
    if hashlib.sha256((folder / 'database-schema.sql').read_bytes()).hexdigest() != meta['schema_sha256']:
        raise Error('Backup schema digest mismatch')
    for name in UNIT_NAMES:
        if not (folder / 'units' / name).is_file():
            raise Error('Backup missing unit configuration')
    manifest = read_json(active / 'manifest.json')
    if manifest.get('format') != FORMAT:
        raise Error('Backup missing account mapping')
    validate_config(manifest['config'])
    public_ip(manifest['endpoint'])
    certs = list((active / 'caddy/data/caddy/certificates').rglob(manifest['config']['domain'] + '.crt'))
    if not certs or any(not cert.with_suffix('.key').is_file() or not cert.with_suffix('.key').stat().st_size for cert in certs):
        raise Error('Backup missing external TLS certificate/private key and renewal storage')
    if not (folder / 'host.py').is_file() or not (active / 'Caddyfile').is_file() or not (active / 'binary-hashes.json').is_file():
        raise Error('Backup missing adapter, environment/front config or binary manifest')
    with contextlib.closing(sqlite3.connect('file:' + str(active / 'db/x-ui.db') + '?mode=ro', uri=True)) as db:
        if db.execute('pragma integrity_check').fetchone()[0] != 'ok':
            raise Error('Corrupt backup database')
    if manifest['accounts'] != snapshot_accounts(active / 'db/x-ui.db', manifest):
        raise Error('Backup metadata disagrees with native snapshot accounts')
    # This is the only source-era metadata migration: derive from the verified
    # closed OLD-endpoint snapshot; never alter the archive or adopt a conflict.
    manifest['host_group_id'] = snapshot_host_group(active / 'db/x-ui.db', manifest)
    if cdn_enabled(manifest):
        cdn_certificate_paths(active, manifest)
        if (active / 'Caddyfile').read_text() != caddyfile(manifest, manifest['published']):
            raise Error('Backup CDN front configuration disagrees with typed manifest')
    if dual_enabled(manifest):
        class SnapshotAPI:
            def call(self, path):
                if path != '/panel/api/inbounds/list':
                    raise Error('Unsupported closed snapshot query')
                with contextlib.closing(sqlite3.connect('file:' + str(active / 'db/x-ui.db') + '?mode=ro', uri=True)) as db:
                    db.row_factory = sqlite3.Row
                    return [dict(row, streamSettings=row['stream_settings'], enable=bool(row['enable'])) for row in db.execute('select * from inbounds')]
        hysteria_health(active, manifest, SnapshotAPI(), check_paths=False)
    return manifest


def prepare(upload):
    # Public-data staging is outside the owned tree until ownership/preflight
    # has succeeded. Request secrets are never echoed.
    with tempfile.TemporaryDirectory(prefix='qd-3x-ui-upload-') as tmp:
        stage = Path(tmp)
        safe_unpack(upload, stage)
        request = read_json(stage / 'request.json')
        info = preflight(request)
        config, lock = request['config'], request['lock']
        restored = None
        if (stage / 'restore.tar').exists():
            restored = stage / 'restore'
            restored.mkdir(mode=0o700)
            with (stage / 'restore.tar').open('rb') as stream:
                safe_unpack(stream, restored)
            old = verify_backup(restored, lock)
            if old['owner'] != request['owner'] or old['config'] != config:
                raise Error('Backup owner/config mismatch')
        else:
            verify_artifacts(stage / 'artifacts', lock)
        if info['owned'] and (ROOT / 'active/manifest.json').exists():
            old = read_json(ROOT / 'active/manifest.json')
            if restored is not None or old['config'] != config or old['endpoint'] != request['endpoint']:
                raise Error('Existing owned host differs; restore/replace only onto a fresh target')
            if read_json(ROOT / 'active/release-lock.json') != lock:
                raise Error('Version drift: explicit backed-up upgrade implementation is required')
            backend_health(ROOT / 'active', old)
            tls_probe(old)
            process_health()
            desired_front = caddyfile(old, old['published'])
            if (ROOT / 'active/Caddyfile').read_text() != desired_front:
                # Explicit re-deploy reconciles supported native device changes.
                # Disabling/rotating a SubID removes its old public path.
                front_config(ROOT / 'active', old, old['published'])
                ctl('restart', FRONT)
                tls_probe(old)
            atomic(ROOT / 'active/manifest.json', dumps(old))
            return {'unchanged': True, 'needs_transport_upgrade': not dual_enabled(old), **info}
        if restored is not None and cdn_enabled(old):
            check_xhttp_port()
        else:
            check_target(config)
        adapter = (stage / 'host.py').read_bytes()
        if hashlib.sha256(adapter).hexdigest() != request['adapter_sha256']:
            raise Error('Uploaded recovery adapter digest mismatch')
        ROOT.mkdir(mode=0o700, exist_ok=True)
        atomic(ROOT / 'owner.json', dumps({'format': FORMAT, 'owner': request['owner'], 'machine_id': info['machine_id']}))
        atomic(ROOT / 'host.py', adapter, 0o700)
        # There was no active owned deployment on this fresh target. Journal
        # before unit writes as well as activation; even bootstrap is resumable.
        atomic(ROOT / 'pending.json', dumps({'boot_id': boot_id(), 'deadline': time.time() + 900, 'had_active': False}))
        active = ROOT / 'active'
        try:
            for name, text in unit_texts().items():
                atomic(UNITS / name, text, 0o644)
            ctl('daemon-reload')
            ctl('enable', RECOVER, TIMER)
            ctl('start', TIMER)
            if active.exists():
                raise Error('Unrecognized incomplete active tree; use recover first')
            if restored is not None:
                shutil.copytree(restored / 'deployment', active)
                install_runtime(active, lock)  # archive entries intentionally have mode0600
                manifest = old
                # The upstream schema is retained; only a new node lease rotates
                # keys and proxy UUIDs. SubIDs/admin credentials stay stable.
                manifest.update(endpoint=request['endpoint'], node_id=str(uuid.uuid4()), published=False)
                if request['mode'] == 'replace':
                    manifest['reality'] = native_keys(active)
                settings_write(active / 'db/x-ui.db', backend_settings(manifest))
                fresh = False
            else:
                active.mkdir(mode=0o700)
                shutil.copytree(stage / 'artifacts', active / 'artifacts')
                install_runtime(active, lock)
                manifest = new_manifest(request['owner'], config, request['endpoint'])
                manifest['reality'] = native_keys(active)
                initialize_db(active, manifest)
                fresh = True
            manifest['management'] = {'ssh_port': info['ssh_port']}
            atomic(active / 'manifest.json', dumps(manifest))
            front_config(active, manifest, False)
            ctl('start', PANEL)
            configure_native(active, manifest, fresh, rotate=(request['mode'] == 'replace'))
            # H2 TLS paths may only reference an already-issued owned pair.
            ctl('start', FRONT)
            wait_tls(manifest)
            if fresh or dual_enabled(manifest):
                enable_hysteria(active, manifest, info['ssh_port'])
            backend_health(active, manifest)
            atomic(active / 'manifest.json', dumps(manifest))
            process_health()
            return {'unchanged': False, **info}
        except BaseException:
            # Failures remain recoverable even if this handler itself is killed.
            recover(force=True)
            raise


# Explicit reviewed public-source predecessors; never infer trust from a remote hash.
LEGACY_ADAPTER_SHA256 = '30430e73199f175099d509f056b31c38b17f3eb6ec7ff07972a75003409a000e'
DEPLOYED_ADAPTER_SHA256 = '8777a3d47b07b91bc85de2ce88016efb6de9c3b0568f5688f5bd8d929ed701f1'
# Reviewed reset-only ae72036; identical public host.py at baseline6c02d25.
BASELINE_ADAPTER_SHA256 = 'b52adbbfb7efc3593c5fa266e8f148ed60deaa2b685d72b926e549b9425f6d7e'
# Independently verified git-show51f71ce AND e754605 public host.py bytes.
DUAL_ADAPTER_SHA256 = 'ecdb5af7d782bb41b9aa2e8794b5659724bfd468a2c7d82c67da330df81e8df7'
ADAPTER_PREDECESSORS = {LEGACY_ADAPTER_SHA256: 'previous-host.py',
                        DEPLOYED_ADAPTER_SHA256: 'previous-host-918103e.py',
                        BASELINE_ADAPTER_SHA256: 'previous-host-ae72036.py',
                        DUAL_ADAPTER_SHA256: 'previous-host-51f71ce.py'}


def accepted_adapter_digest(stored, current):
    return stored == current or stored in ADAPTER_PREDECESSORS


def validate_rename_names(names, roster):
    if names[0] not in roster:
        raise Error('Unknown source device for rename')
    if names[1] in roster:
        raise Error('Rename target already exists')


def rename_target_has_history(database, target):
    if not isinstance(target, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,31}', target):
        raise Error('Invalid rename target')
    # Match the pinned native equality predicates: its rename can reuse target
    # traffic and delete target IP tracking even without a target ClientRecord.
    with contextlib.closing(sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True)) as db:
        return any(db.execute('select 1 from ' + table + ' where ' + column + '=? limit 1',
                              (target,)).fetchone() for table, column in
                   (('client_traffics', 'email'), ('inbound_client_ips', 'client_email')))


def device_request(request):
    names = request.get('names')
    action = request.get('device_action')
    if action not in ('add', 'remove', 'reset', 'rename'):
        raise Error('Unsupported device action; no conversion or replay; preserve pending for manual review')
    if action == 'rename' and isinstance(names, list) and len(names) == 2 and names[0] == names[1]:
        raise Error('Rename requires two distinct names; no changes made')
    if (action not in ('add', 'remove', 'reset', 'rename') or not isinstance(names, list) or not names or
            len(set(names)) != len(names) or any(not isinstance(n, str) or
                not re.fullmatch(r'[a-z][a-z0-9-]{0,31}', n) for n in names) or
            (action == 'rename' and len(names) != 2) or
            (action not in ('add', 'rename') and len(names) != 1) or
            not re.fullmatch(r'[0-9a-f]{32}', request.get('operation_id', ''))):
        raise Error('Invalid device operation/names/operation ID')
    bound = {k: request[k] for k in ('owner', 'machine_id', 'endpoint', 'config',
                                    'device_action', 'names', 'operation_id')}
    return hashlib.sha256(dumps(bound).encode()).hexdigest()


def device_receipt(request):
    fingerprint = device_request(request)
    path = ROOT / 'device-receipts' / (request['operation_id'] + '.json')
    if path.exists():
        receipt = read_json(path)
        if receipt['request_sha256'] != fingerprint:
            raise Error('Operation ID already bound to a different request')
        return receipt if receipt.get('committed') else None
    return None


def device_owner(request):
    marker = read_json(ROOT / 'owner.json')
    if marker != {'format': FORMAT, 'owner': request['owner'], 'machine_id': machine_id()} or request['machine_id'] != machine_id():
        raise Error('Wrong host/owner for device operation')
    manifest = read_json(ROOT / 'active/manifest.json')
    if manifest['owner'] != request['owner'] or manifest['config'] != request['config'] or manifest['endpoint'] != request['endpoint']:
        raise Error('Device operation differs from owned deployment')
    if 'ssh_port' in request and 'management' in manifest and manifest['management'] != {'ssh_port': request['ssh_port']}:
        raise Error('Management SSH port differs from owned deployment')
    return manifest


def transport_request(request):
    required = ('owner', 'machine_id', 'endpoint', 'config', 'operation_id', 'adapter_sha256', 'ssh_port', 'mode', 'before_sha256')
    if (request.get('mode') != 'transport-upgrade' or request.get('kind', 'transport-upgrade') != 'transport-upgrade' or request.get('device_action') is not None or
            not re.fullmatch(r'[0-9a-f]{32}', request.get('operation_id', '')) or
            not re.fullmatch(r'[0-9a-f]{64}', request.get('adapter_sha256', '')) or
            type(request.get('ssh_port')) is not int or not 1 <= request['ssh_port'] <= 65535 or
            any(key not in request for key in required)):
        raise Error('Invalid transport feature request')
    validate_config(request['config'])
    bound = {key: request[key] for key in required}
    if not isinstance(request['before_sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', request['before_sha256']):
        raise Error('Invalid pre-change archive digest')
    if 'feature' in request:
        proposal = request.get('tcp_cdn')
        if (request['feature'] != 'tcp-cdn' or not isinstance(proposal, dict) or set(proposal) != {'schema', 'domain'} or
                type(proposal['schema']) is not int or proposal['schema'] != 1 or hostname(proposal['domain']) != proposal['domain'] or
                proposal['domain'] in (request['config']['domain'], request['config']['reality_target']) or request['config']['proxy_port'] != CDN_PORT or
                request.get('source_transports') != ['tcp-reality', 'hysteria2'] or
                request.get('target_transports') != ['tcp-xhttp-cdn', 'hysteria2']):
            raise Error('Unsupported TCP CDN feature proposal/transition')
        bound.update({key: request[key] for key in ('feature', 'tcp_cdn', 'source_transports', 'target_transports')})
    else:
        # Historical featureless fingerprints are immutable, including the
        # optional presence/absence of the old transport lists.
        for key, expected in (('source_transports', ['tcp-reality']), ('target_transports', ['tcp-reality', 'hysteria2'])):
            if key in request and request[key] != expected:
                raise Error('Unsupported transport feature transition')
        for key in ('source_transports', 'target_transports'):
            if key in request:
                bound[key] = request[key]
        if 'tcp_cdn' in request:
            raise Error('CDN proposal requires the explicit feature discriminator')
    return hashlib.sha256(dumps(bound).encode()).hexdigest()


def cdn_proof_digest(proof):
    return hashlib.sha256(json.dumps(proof, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()


def validate_cdn_proof(request, fingerprint):
    proof = request.get('cdn_proof')
    keys = {'schema', 'operation_id', 'request_fingerprint', 'candidate_sha256', 'domain', 'device_identity_sha256',
            'observed_at', 'interface', 'edge_ips', 'checks'}
    checks = {'edge_tls', 'edge_cloudflare', 'xhttp_https', 'vps_egress', 'wrong_uuid_rejected', 'valid_again', 'reconnect', 'hysteria2_preserved'}
    if (not isinstance(proof, dict) or set(proof) != keys or type(proof['schema']) is not int or proof['schema'] != 1 or
            proof['operation_id'] != request['operation_id'] or proof['request_fingerprint'] != fingerprint or
            not isinstance(proof['candidate_sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', proof['candidate_sha256']) or
            proof['candidate_sha256'] != request.get('offnode_sha256') or proof['domain'] != request['tcp_cdn']['domain'] or
            not isinstance(proof['device_identity_sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', proof['device_identity_sha256']) or
            not isinstance(proof['checks'], dict) or set(proof['checks']) != checks or any(v is not True for v in proof['checks'].values()) or
            not isinstance(proof['interface'], str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', proof['interface']) or
            not isinstance(proof['edge_ips'], list) or not proof['edge_ips'] or
            any(not isinstance(ip, str) for ip in proof['edge_ips']) or len(set(proof['edge_ips'])) != len(proof['edge_ips'])):
        raise Error('Invalid CDN proof schema or operation/request/candidate/domain binding')
    try:
        for ip in proof['edge_ips']:
            public_ip(ip)
        if not isinstance(proof['observed_at'], str):
            raise ValueError()
        observed = datetime.fromisoformat(proof['observed_at'].replace('Z', '+00:00'))
        if observed.tzinfo is None or observed.utcoffset() != timezone.utc.utcoffset(observed):
            raise ValueError()
    except (ValueError, TypeError) as exc:
        raise Error('Invalid CDN proof UTC observation/network') from exc
    sha = request.get('cdn_proof_sha256')
    if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha) or cdn_proof_digest(proof) != sha:
        raise Error('CDN proof canonical digest mismatch')
    return proof


def transaction_request(request):
    return transport_request(request) if request.get('mode') == 'transport-upgrade' else device_request(request)


def receipt_path(value):
    transport = value.get('kind') == 'transport-upgrade' or value.get('mode') == 'transport-upgrade'
    return ROOT / ('transport-receipts' if transport else 'device-receipts') / (value['operation_id'] + '.json')


def transaction_receipt(request):
    if request.get('mode') != 'transport-upgrade':
        return device_receipt(request)
    fingerprint = transport_request(request)
    path = receipt_path(request)
    if path.exists():
        receipt = read_json(path)
        if receipt['request_sha256'] != fingerprint:
            raise Error('Operation ID already bound to a different transport request')
        if receipt.get('committed') and request.get('feature') == 'tcp-cdn' and not receipt.get('unchanged') and any(
                key in request for key in ('offnode_sha256', 'cdn_proof', 'cdn_proof_sha256')):
            validate_cdn_proof(request, fingerprint)
            if any(request.get(k) != receipt.get(k) for k in ('offnode_sha256', 'cdn_proof', 'cdn_proof_sha256')):
                raise Error('Committed CDN receipt differs from supplied archive/proof')
        return receipt if receipt.get('committed') else None
    return None


def prepared_result(request, pending, manifest):
    if request.get('feature') == 'tcp-cdn':
        return {'prepared': True, 'deadline': pending['deadline'], 'tcp_cdn': manifest['tcp_cdn'], 'transports': transports(manifest)}
    return {'prepared': True}


def transport_prepare(upload):
    return devices_prepare(upload, transport=True)


def devices_prepare(upload, transport=False):
    with tempfile.TemporaryDirectory(prefix='qd-device-upload-') as tmp:
        stage = Path(tmp)
        safe_unpack(upload, stage)
        request = read_json(stage / 'request.json')
        check_request_identity(request)
        manifest = device_owner(request)
        if transport != (request.get('mode') == 'transport-upgrade'):
            raise Error('Wrong transaction preparation action')
        fingerprint = transaction_request(request)
        receipt = transaction_receipt(request)
        if receipt:
            return receipt
        if (ROOT / 'pending.json').exists():
            pending = read_json(ROOT / 'pending.json')
            if pending.get('request_sha256') != fingerprint:
                raise Error('Different pending operation; recover first')
            check_lease()
            if pending.get('phase') == 'prepared':
                return prepared_result(request, pending, manifest)
            raise Error('Interrupted device preparation; recover before retry')
        rollback = ROOT / 'device-rollback'
        if rollback.exists():
            raise Error('Retained device recovery intent; run recover first')
        if transport and (not (ROOT / 'export-sha256').is_file() or
                (ROOT / 'export-sha256').read_text() != request['before_sha256']):
            raise Error('Transport migration requires its verified pre-change archive receipt')
        reject_symlinks(ROOT / 'active')
        if (ROOT / 'host.py').is_symlink():
            raise Error('Symlink recovery adapter')
        adapter = (stage / 'host.py').read_bytes()
        current_sha = hashlib.sha256(adapter).hexdigest()
        try:
            stored_source = regular_adapter_bytes(ROOT / 'host.py')
        except (OSError, Error) as exc:
            raise Error('Unknown stored recovery adapter; no device writes') from exc
        stored_sha = hashlib.sha256(stored_source).hexdigest()
        if current_sha != request['adapter_sha256'] or not accepted_adapter_digest(stored_sha, current_sha):
            raise Error('Unknown stored/uploaded adapter digest; no device writes')
        # Old reviewed adapters cannot recover a transport journal or a dual
        # snapshot. The verified pre-change backup must hand off recovery first.
        if (transport or dual_enabled(manifest)) and stored_sha != current_sha:
            raise Error('Current recovery adapter required; run verified pre-change backup before this operation')
        check_units()
        binary_health(ROOT / 'active')
        # Publication probes are not an ownership test: restricted feeds may deny.
        api = API(manifest)
        inbound, _ = owned_host_group(ROOT / 'active', api, manifest)
        snapshot_accounts(ROOT / 'active/db/x-ui.db', manifest)
        names, action = ([], 'transport-upgrade') if transport else (request['names'], request['device_action'])
        existing = set(device_names(manifest))
        cdn = transport and request.get('feature') == 'tcp-cdn'
        if transport and not cdn and cdn_enabled(manifest):
            raise Error('Owned CDN mode cannot be reinterpreted as a REALITY transport upgrade')
        if cdn:
            if manifest.get('published') is not True:
                raise Error('CDN migration requires a committed source; active files alone are not acceptance')
            if cdn_enabled(manifest):
                if manifest['tcp_cdn']['domain'] != request['tcp_cdn']['domain']:
                    raise Error('CDN domain differs from the owned target')
                backend_health(ROOT / 'active', manifest)
                tls_probe(manifest)
                process_health()
            elif not dual_enabled(manifest):
                raise Error('CDN migration requires the exact REALITY+Hysteria2 source')
            else:
                check_xhttp_port()
                check_cdn_dns(request['tcp_cdn']['domain'], manifest['endpoint'])
                reality_source_health(inbound, manifest)
                hysteria_health(ROOT / 'active', dict(manifest, accounts=snapshot_accounts(ROOT / 'active/db/x-ui.db', manifest)), api)
        if (transport and ((cdn and cdn_enabled(manifest)) or (not cdn and dual_enabled(manifest)))) or (action == 'add' and set(names) <= existing) or (action == 'remove' and names[0] not in existing):
            result = {'unchanged': True, 'committed': True, 'request_sha256': fingerprint,
                      'machine_id': request['machine_id'], 'node_id': manifest['node_id']}
            if transport:
                result['transports'] = transports(manifest)
            atomic(receipt_path(request), dumps(result))
            return result
        if action == 'add' and set(names) & existing:
            raise Error('Mixed existing/new names; no partial add')
        if action == 'reset' and names[0] not in existing:
            raise Error('Unknown device for reset')
        if action == 'rename':
            validate_rename_names(names, existing)
            if rename_target_has_history(ROOT / 'active/db/x-ui.db', names[1]):
                raise Error('Rename target has retained native history; no changes made')
        process_health()
        tls_probe(manifest)
        # All rollback inputs and durable intent precede adapter/service mutation.
        preparation = Path(tempfile.mkdtemp(prefix='device-snapshot-', dir=ROOT))
        for name, source in [('manifest.json', ROOT / 'active/manifest.json'),
                             ('Caddyfile', ROOT / 'active/Caddyfile'), ('host.py', ROOT / 'host.py')]:
            atomic(preparation / name, stored_source if name == 'host.py' else source.read_bytes())
        services = {}
        for unit in (PANEL, FRONT):
            services[unit] = {key: subprocess.run(['systemctl', command, '--quiet', unit], capture_output=True).returncode == 0
                              for key, command in [('running', 'is-active'), ('enabled', 'is-enabled')]}
        intent = {'boot_id': boot_id(), 'deadline': time.time() + 900, 'had_active': True,
                  'kind': 'transport-upgrade' if transport else 'devices', 'phase': 'snapshot', 'operation_id': request['operation_id'],
                  'request_sha256': fingerprint, 'services': services}
        if cdn:
            intent['feature'] = 'tcp-cdn'
        atomic(preparation / 'intent.json', dumps(intent))
        os.replace(preparation, rollback)
        directory = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        # Crash before replacement leaves the old adapter and services unchanged;
        # crash after replacement is recognized by recover's staged-intent branch.
        atomic(receipt_path(request), dumps({'request_sha256': fingerprint, 'committed': False}))
        atomic(ROOT / 'host.py', adapter, 0o700)
        atomic(ROOT / 'pending.json', dumps(intent))
        active = ROOT / 'active'
        try:
            ctl('stop', FRONT)
            ctl('stop', PANEL)
            db_snapshot(active / 'db/x-ui.db', rollback / 'x-ui.db')
            manifest['accounts'] = snapshot_accounts(rollback / 'x-ui.db', manifest)
            snapshot_caddy(active, rollback)
            intent['caddy_snapshot'] = True
            intent['phase'] = 'ready'
            atomic(ROOT / 'pending.json', dumps(intent))
            ctl('start', PANEL)
            api = wait_api(manifest)
            if cdn:
                bootstrap_cdn_certificate(active, manifest, request['tcp_cdn']['domain'])
                enable_cdn(active, manifest, request['tcp_cdn']['domain'])
            elif transport:
                enable_hysteria(active, manifest, request['ssh_port'])
            elif action == 'add':
                for name in names:
                    check_lease()
                    account = {'id': str(uuid.uuid4()), 'subId': secrets.token_hex(24), 'email': name,
                               'enable': True, 'flow': '' if cdn_enabled(manifest) else 'xtls-rprx-vision'}
                    # Collision checking covers ALL global native accounts above.
                    if any(uuid.UUID(account['id']) == uuid.UUID(c['id']) or account['subId'] == c['subId']
                           for c in manifest['accounts'].values()):
                        raise Error('Generated credential collision; recover and retry')
                    inbound_ids = [inbound['id']]
                    if dual_enabled(manifest):
                        account['auth'] = secrets.token_hex(24)
                        if any(account['auth'] == c['auth'] for c in manifest['accounts'].values()):
                            raise Error('Generated Hysteria2 credential collision')
                        inbound_ids.append(manifest['hysteria2']['inbound_id'])
                    api.call('/panel/api/clients/add', {'client': account, 'inboundIds': inbound_ids})
                    manifest['accounts'][name] = account
            elif action == 'remove':
                check_lease()
                api.call('/panel/api/clients/del/' + names[0], {})
                del manifest['accounts'][names[0]]
            else:
                name = names[0]
                settings = next(i for i in api.call('/panel/api/inbounds/list') if i['remark'] == main_tag(manifest))['settings']
                settings = json.loads(settings) if isinstance(settings, str) else settings
                account = next(c for c in settings['clients'] if c['email'] == name)
                detail = api.call('/panel/api/clients/get/' + name)['client']
                account['limitHwid'] = detail['limitHwid']
                if dual_enabled(manifest):
                    account['auth'] = detail['auth']
                if action == 'rename':
                    if rename_target_has_history(active / 'db/x-ui.db', names[1]):
                        raise Error('Rename target has retained native history; recover before retry')
                    account['email'] = names[1]
                else:
                    account.update(id=str(uuid.uuid4()), subId=secrets.token_hex(24))
                    if dual_enabled(manifest):
                        account['auth'] = secrets.token_hex(24)
                        if any(account['auth'] == c['auth'] for c in manifest['accounts'].values()):
                            raise Error('Generated Hysteria2 credential collision')
                    if any(uuid.UUID(account['id']) == uuid.UUID(c['id']) or account['subId'] == c['subId']
                           for c in manifest['accounts'].values()):
                        raise Error('Generated credential collision; recover and retry')
                check_lease()
                api.call('/panel/api/clients/update/' + name, account)
                if action == 'rename':
                    # Preserve ordering/capability paths; only the roster key and
                    # native name change. Full policy/usage stays with native ID.
                    manifest['accounts'] = {names[1] if key == name else key:
                        dict(value, email=names[1]) if key == name else value
                        for key, value in manifest['accounts'].items()}
            # Restart the owned panel/core process, not only Xray's live user API:
            # this closes existing streams as well as rejecting new old UUIDs.
            ctl('stop', PANEL)
            ctl('start', PANEL)
            wait_api(manifest)
            backend_health(active, manifest)
            manifest['published'] = False
            atomic(active / 'manifest.json', dumps(manifest))
            # Keep the OLD exact front paths until backup receipt/commit. New
            # capabilities are never exposed before off-node verification.
            if transport:
                front_config(active, manifest, False)
            ctl('start', FRONT)
            wait_tls(manifest, pending=True)
            process_health()
            check_lease()
            intent['phase'] = 'prepared'
            atomic(ROOT / 'pending.json', dumps(intent))
            return prepared_result(request, intent, manifest)
        except BaseException:
            recover(force=True)
            raise


def recover_devices(pending):
    rollback = ROOT / 'device-rollback'
    receipt = receipt_path(pending)
    if receipt.exists() and read_json(receipt)['request_sha256'] != pending['request_sha256']:
        raise Error('Recovery receipt request mismatch')
    if receipt.exists() and read_json(receipt).get('committed'):
        (ROOT / 'pending.json').unlink(missing_ok=True)
        if rollback.exists():
            os.replace(rollback, ROOT / ('completed-' + pending['operation_id']))
        return {'committed': True, 'pending': False}
    if not receipt.exists():
        atomic(receipt, dumps({'request_sha256': pending['request_sha256'], 'committed': False}))
    services = pending['services']
    if pending['phase'] != 'restore-queued':
        restore_db = pending.get('restore_db', pending['phase'] != 'snapshot')
        pending.update(phase='recovering', restore_db=restore_db)
        atomic(ROOT / 'pending.json', dumps(pending))
        check_units()
        ctl('stop', FRONT)
        ctl('stop', PANEL)
        active = ROOT / 'active'
        if restore_db:
            atomic(active / 'db/x-ui.db', (rollback / 'x-ui.db').read_bytes())
            for suffix in ('-wal', '-shm'):
                (active / ('db/x-ui.db' + suffix)).unlink(missing_ok=True)
        for name in ('manifest.json', 'Caddyfile'):
            atomic(active / name, (rollback / name).read_bytes())
        if pending.get('caddy_snapshot'):
            restore_caddy(active, rollback)
        auth_cleanup(pending)
        # Snapshot-phase crashes never ran candidate FRONT, so an incomplete
        # initial subtree is deliberately ignored rather than restored.
        # Persist restored-state authorization BEFORE queueing dependent jobs.
        pending['phase'] = 'restore-queued'
        atomic(ROOT / 'pending.json', dumps(pending))
        queue_services(services)
        return {'pending': True, 'queued': True, 'committed': False}
    check_units()
    if not services_active(services):
        queue_services(services)
        return {'pending': True, 'queued': True, 'committed': False}
    restored = read_json(ROOT / 'active/manifest.json')
    try:
        if services[PANEL]['running']:
            api = wait_api(restored)
            if dual_enabled(restored):
                hysteria_health(ROOT / 'active', restored, api)
        if services[FRONT]['running']:
            wait_tls(restored)
        if all(state['running'] for state in services.values()):
            process_health()
    except BaseException:
        ctl('stop', FRONT)
        ctl('stop', PANEL)
        raise
    # Both journals must be disarmed before restoring a legacy adapter.
    disarm(ROOT / 'backup-stop.json')
    disarm(ROOT / 'pending.json')
    atomic(ROOT / 'host.py', (rollback / 'host.py').read_bytes(), 0o700)
    os.replace(rollback, ROOT / ('rolled-back-' + pending['operation_id'] + '-' + secrets.token_hex(4)))
    return {'pending': False, 'restored': 'original device database/front/adapter and service state'}


def auth_template(folder):
    with contextlib.closing(sqlite3.connect('file:' + str(Path(folder) / 'db/x-ui.db') + '?mode=ro', uri=True)) as db:
        rows = db.execute("select value from settings where key='xrayTemplateConfig'").fetchall()
    if len(rows) != 1 or not isinstance(rows[0][0], str):
        raise Error('Missing or ambiguous native Xray template')
    raw = rows[0][0]
    try:
        template = json.loads(raw)
    except ValueError as exc:
        raise Error('Invalid native Xray template') from exc
    if not isinstance(template, dict) or 'xraySetting' in template or not isinstance(template.get('log'), dict):
        raise Error('Unexpected native Xray template shape')
    return raw, template


def auth_core_epoch(folder):
    """Bind evidence to the attested native API listener, not a PID alone."""
    pids = set()
    for line in run(['ss', '-H', '-ltnp'], timeout=10).stdout.decode().splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[3] == '127.0.0.1:' + str(API_PORT):
            pids.update(re.findall(r'pid=(\d+)', line))
    if len(pids) != 1:
        raise Error('Cannot pin unique native core epoch')
    pid = pids.pop()
    proc = Path('/proc') / pid
    if (os.readlink(proc / 'exe') != str(Path(folder) / 'bin/xray-linux-amd64') or
            PANEL not in (proc / 'cgroup').read_text()):
        raise Error('Foreign native core epoch')
    fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
    return {'boot_id': boot_id(), 'pid': int(pid), 'start_ticks': int(fields[19])}


def auth_live_users(folder, tag):
    """Pinned Xray HandlerService read; never the panel's cached projection."""
    result = run([Path(folder) / 'bin/xray-linux-amd64', 'api', 'inbounduser',
                  '--server=127.0.0.1:' + str(API_PORT), '-tag=' + tag, '-timeout=6'], timeout=8)
    try:
        users = json.loads(result.stdout)['users']
        if not isinstance(users, list):
            raise ValueError()
        for user in users:
            if (not isinstance(user.get('email'), str) or
                    user['account'].get('_TypedMessage_') != 'xray.proxy.vless.Account'):
                raise ValueError()
            uuid.UUID(user['account']['id'])
        return {user['email']: user['account']['id'] for user in users} if len({u['email'] for u in users}) == len(users) else None
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise Error('Cannot attest native VLESS live registry') from exc


def auth_uuid_key(value):
    raw = bytearray(uuid.UUID(value).bytes)
    raw[6:8] = b'\0\0'  # Pinned Xray ProcessUUID lookup semantics.
    return bytes(raw)


def auth_native_state(folder, manifest):
    check_lease()
    binary_health(Path(folder))
    api = wait_api(manifest)
    live = auth_live_users(folder, 'SELF-cdn')  # Blocking native gRPC readiness after restart.
    process_health()
    owned_host_group(folder, api, manifest)
    hysteria_health(folder, manifest, api)
    accounts = snapshot_accounts(Path(folder) / 'db/x-ui.db', manifest)
    if accounts != manifest['accounts']:
        raise Error('Native credentials changed during auth diagnostic')
    config = read_json(Path(folder) / 'bin/config.json')
    config.pop('log', None)
    vless = [i for i in config.get('inbounds', []) if i.get('protocol') == 'vless']
    if len(vless) != 1 or vless[0].get('tag') != 'SELF-cdn':
        raise Error('Auth diagnostic requires the single owned native VLESS ingress')
    generated = {c['email']: c['id'] for c in vless[0]['settings']['clients']}
    if not generated or live != generated:
        raise Error('Native VLESS registry disagrees with generated configuration')
    authority = {c['email']: c['id'] for c in accounts.values()}
    if any(authority.get(name) != identity for name, identity in live.items()):
        raise Error('Native VLESS registry differs from owned identities')
    records = {name: api.call('/panel/api/clients/get/' + name)['client'] for name in device_names(manifest)}
    # ClientRecord contains identity and policy, not mutable traffic counters.
    # Routing rules/outbounds and all other generated lists retain their order.
    state = {'config': config, 'clients': records,
             'hosts': {g['groupId']: g for g in api.call('/panel/api/hosts/list')}}
    check_lease()
    return state, list(authority.values())


def auth_bound(request):
    fingerprint = transport_request(request)
    manifest = device_owner(request)
    pending = read_json(ROOT / 'pending.json')
    if (request.get('feature') != 'tcp-cdn' or not cdn_enabled(manifest) or
            pending.get('kind') != 'transport-upgrade' or pending.get('phase') != 'prepared' or
            pending.get('feature') != 'tcp-cdn' or pending.get('operation_id') != request['operation_id'] or
            pending.get('request_sha256') != fingerprint or manifest['tcp_cdn']['domain'] != request['tcp_cdn']['domain']):
        raise Error('Auth diagnostic requires its owned prepared CDN operation')
    return manifest, pending, fingerprint


def auth_directory(pending):
    # No caller-selected path and no arbitrary recursive cleanup.
    return ROOT / ('auth-witness-' + pending['operation_id'])


def auth_file(pending, record):
    directory = auth_directory(pending)
    ds = directory.lstat()
    if (not stat.S_ISDIR(ds.st_mode) or ds.st_uid != os.getuid() or stat.S_IMODE(ds.st_mode) != 0o700 or
            [ds.st_dev, ds.st_ino] != record['directory_identity']):
        raise Error('Auth witness directory identity changed')
    fd = os.open(directory / 'error.log', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or
                info.st_nlink != 1 or [info.st_dev, info.st_ino] != record['file_identity']):
            raise Error('Auth witness file identity changed')
        return os.fdopen(fd, 'rb')
    except BaseException:
        os.close(fd)
        raise


def auth_cleanup(pending):
    record = pending.get('cdn_auth')
    if not record:
        return
    directory = auth_directory(pending)
    if not directory.exists() and not directory.is_symlink():
        return
    ds = directory.lstat()
    if (not stat.S_ISDIR(ds.st_mode) or ds.st_uid != os.getuid() or stat.S_IMODE(ds.st_mode) != 0o700 or
            ('directory_identity' in record and [ds.st_dev, ds.st_ino] != record['directory_identity'])):
        raise Error('Refusing foreign auth diagnostic directory cleanup')
    path = directory / 'error.log'
    if path.exists() or path.is_symlink():
        if 'file_identity' in record:
            with auth_file(pending, record):
                pass
        else:
            info = path.lstat()  # Crash between exclusive creation and journal pin.
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                    stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                raise Error('Refusing foreign unpinned auth witness cleanup')
        path.unlink()
    directory.rmdir()  # Refuse unexpected entries; never delete a foreign file.


def auth_begin_result(request, fingerprint, record):
    return {'schema': 1, 'operation_id': request['operation_id'], 'request_fingerprint': fingerprint,
            'nonce': record['nonce'], 'native_identity_sha256': record['native_identity_sha256']}


def transport_auth_begin(request):
    manifest, pending, fingerprint = auth_bound(request)
    active = ROOT / 'active'
    try:
        check_lease()
        record = pending.get('cdn_auth')
        if record:
            if record.get('phase') != 'active':
                raise Error('Auth diagnostic already finished or interrupted; recover operation')
            state, _ = auth_native_state(active, manifest)
            raw, _ = auth_template(active)
            with auth_file(pending, record) as stream:
                if os.fstat(stream.fileno()).st_size < record['offset']:
                    raise Error('Auth witness truncated')
            if (auth_core_epoch(active) != record['epoch'] or state != record['native_state'] or
                    raw != record['temporary_template']):
                raise Error('Active auth diagnostic changed; recovery required')
            check_lease()
            return auth_begin_result(request, fingerprint, record)
        state, registered = auth_native_state(active, manifest)
        raw, template = auth_template(active)
        if template['log'] != {'access': 'none', 'error': 'none', 'loglevel': 'none'}:
            raise Error('Auth diagnostic requires original disabled native logging')
        excluded = {auth_uuid_key(value) for value in registered}
        for _ in range(32):
            nonce = str(uuid.uuid4())
            if auth_uuid_key(nonce) not in excluded:
                break
        else:
            raise Error('Cannot generate excluded native auth nonce')
        record = {'phase': 'arming', 'original_template': raw, 'native_state': state, 'nonce': nonce,
                  'original_epoch': auth_core_epoch(active), 'native_identity_sha256': cdn_proof_digest(state)}
        pending['cdn_auth'] = record
        atomic(ROOT / 'pending.json', dumps(pending))  # Save exact original BEFORE any mutation.
        directory = auth_directory(pending)
        directory.mkdir(mode=0o700)
        ds = directory.lstat()
        record['directory_identity'] = [ds.st_dev, ds.st_ino]
        atomic(ROOT / 'pending.json', dumps(pending))
        fd = os.open(directory / 'error.log', os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.flush(); os.fsync(stream.fileno())
            info = os.fstat(stream.fileno())
        record['file_identity'] = [info.st_dev, info.st_ino]
        record['offset'] = info.st_size
        template['log'] = {'access': 'none', 'error': str(directory / 'error.log'), 'loglevel': 'info'}
        record['temporary_template'] = json.dumps(template)
        record['started_at'] = time.time()
        atomic(ROOT / 'pending.json', dumps(pending))
        check_lease()
        ctl('stop', PANEL)
        check_lease()
        settings_write(active / 'db/x-ui.db', {'xrayTemplateConfig': record['temporary_template']})
        ctl('start', PANEL)
        wait_api(manifest)
        current, _ = auth_native_state(active, manifest)
        if current != state or auth_template(active)[0] != record['temporary_template']:
            raise Error('Temporary logging changed native identity/configuration')
        with auth_file(pending, record) as stream:
            record['offset'] = os.fstat(stream.fileno()).st_size
        epoch = auth_core_epoch(active)
        if epoch == record['original_epoch']:
            raise Error('Native logger did not enter a fresh core epoch')
        record.update(phase='active', epoch=epoch)
        check_lease()
        atomic(ROOT / 'pending.json', dumps(pending))
        return auth_begin_result(request, fingerprint, record)
    except BaseException:
        recover(force=True)
        raise


def auth_read_verdict(pending, record):
    with auth_file(pending, record) as stream:
        size = os.fstat(stream.fileno()).st_size
        if size < record['offset'] or size - record['offset'] > 4 * 1024 * 1024:
            raise Error('Auth witness truncated or exceeds bounded read')
        stream.seek(record['offset'])
        data = stream.read(4 * 1024 * 1024 + 1)
    nonce = re.escape(record['nonce'])
    pattern = (rb'^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}\.\d{6}) \[Info\] \[\d+\] '
               rb'app/proxyman/inbound: connection ends > proxy/vless/inbound: invalid request from '
               rb'[^\r\n]+ > proxy/vless/encoding: invalid request user id: ' + nonce.encode() + rb'$')
    for line in data.splitlines(keepends=True):
        if not line.endswith(b'\n'):
            continue
        match = re.fullmatch(pattern, line.rstrip(b'\r\n'))
        if match:
            observed = datetime.strptime(match[1].decode(), '%Y/%m/%d %H:%M:%S.%f').timestamp()
            if record['started_at'] <= observed <= time.time():
                return True
    raise Error('No fresh exact native invalid-user nonce verdict')


def transport_auth_finish(request):
    manifest, pending, fingerprint = auth_bound(request)
    record = pending.get('cdn_auth')
    if not record:
        raise Error('No owned native auth diagnostic to finish')
    try:
        check_lease()
    except Error:
        recover(force=True)
        raise
    supplied = request.get('cdn_auth_nonce')
    nonce_matches = (isinstance(supplied, str) and hashlib.sha256(supplied.encode()).hexdigest() ==
                     (record.get('nonce_sha256') or hashlib.sha256(record['nonce'].encode()).hexdigest()))
    if record.get('phase') == 'restored':
        if not nonce_matches or not record.get('verdict'):
            raise Error('No matching restored native auth verdict')
        auth_commit_verdict(request, pending, fingerprint)
        return record['verdict']
    active = ROOT / 'active'
    failure = None
    try:
        if record.get('phase') != 'active' or not nonce_matches:
            raise Error('Auth nonce or diagnostic phase mismatch')
        if auth_core_epoch(active) != record['epoch']:
            raise Error('Native auth witness process epoch changed')
    except Exception as exc:
        failure = exc
    # Close writer before reading; restoration runs even on absent/invalid evidence.
    ctl('stop', PANEL)
    try:
        if failure is None:
            auth_read_verdict(pending, record)
    except Exception as exc:
        failure = exc
    record['phase'] = 'restoring'
    atomic(ROOT / 'pending.json', dumps(pending))
    settings_write(active / 'db/x-ui.db', {'xrayTemplateConfig': record['original_template']})
    cleanup_failed = False
    try:
        auth_cleanup(pending)
    except Exception as exc:
        failure, cleanup_failed = exc, True
    check_lease()
    ctl('start', PANEL)
    wait_api(manifest)
    current, _ = auth_native_state(active, manifest)
    if (current != record['native_state'] or auth_template(active)[0] != record['original_template'] or
            read_json(active / 'bin/config.json').get('log') != json.loads(record['original_template'])['log']):
        raise Error('Auth diagnostic restoration changed native identity/configuration/logging')
    check_lease()
    identity = record['native_identity_sha256']
    verdict = {'schema': 1, 'operation_id': request['operation_id'], 'request_fingerprint': fingerprint,
               'nonce_sha256': hashlib.sha256(record['nonce'].encode()).hexdigest(), 'rejected': True,
               'native_identity_sha256': identity}
    # Only sanitized bound result persists after restoration; no template/log/nonce archive.
    if not cleanup_failed:
        pending['cdn_auth'] = {'phase': 'restored', 'nonce_sha256': verdict['nonce_sha256'],
                               'restored_template_sha256': hashlib.sha256(record['original_template'].encode()).hexdigest(),
                               'native_identity_sha256': identity, 'verdict': verdict if failure is None else None}
    (ROOT / 'export-sha256').unlink(missing_ok=True)  # Candidate A is never final B.
    atomic(ROOT / 'pending.json', dumps(pending))
    if failure is not None:
        raise Error('Native auth verdict unavailable; original logging restored') from failure
    return verdict


def auth_commit_verdict(request, pending, fingerprint):
    record = pending.get('cdn_auth', {})
    verdict = record.get('verdict', {})
    if (record.get('phase') != 'restored' or not isinstance(verdict, dict) or
            set(verdict) != {'schema', 'operation_id', 'request_fingerprint', 'nonce_sha256', 'rejected', 'native_identity_sha256'} or
            type(verdict.get('schema')) is not int or verdict.get('schema') != 1 or verdict.get('rejected') is not True or
            verdict.get('operation_id') != request['operation_id'] or verdict.get('request_fingerprint') != fingerprint or
            verdict.get('nonce_sha256') != record.get('nonce_sha256') or
            not re.fullmatch(r'[0-9a-f]{64}', verdict.get('nonce_sha256', '')) or
            auth_directory(pending).exists() or auth_directory(pending).is_symlink()):
        raise Error('CDN commit requires its bound restored native auth verdict and no active diagnostic')
    active = ROOT / 'active'
    raw, template = auth_template(active)
    if (hashlib.sha256(raw.encode()).hexdigest() != record.get('restored_template_sha256') or
            template['log'] != {'access': 'none', 'error': 'none', 'loglevel': 'none'} or
            read_json(active / 'bin/config.json').get('log') != template['log']):
        raise Error('Native logging/template changed after auth restoration')
    state, _ = auth_native_state(active, device_owner(request))
    if cdn_proof_digest(state) != verdict['native_identity_sha256']:
        raise Error('Native identity/configuration changed after auth restoration')
    return verdict


def transport_commit(request):
    return devices_commit(request, transport=True)


def devices_commit(request, transport=False):
    manifest = device_owner(request)
    if transport != (request.get('mode') == 'transport-upgrade'):
        raise Error('Wrong transaction commit action')
    fingerprint = transaction_request(request)
    receipt = transaction_receipt(request)
    if receipt:
        return receipt
    pending = read_json(ROOT / 'pending.json')
    if pending.get('kind') != ('transport-upgrade' if transport else 'devices') or pending.get('phase') != 'prepared' or pending['request_sha256'] != fingerprint:
        raise Error('Owned commit does not match prepared intent')
    cdn = transport and request.get('feature') == 'tcp-cdn'
    if transport and (transports(manifest) != (request['target_transports'] if cdn else ['tcp-reality', 'hysteria2']) or
            (cdn and manifest['tcp_cdn']['domain'] != request['tcp_cdn']['domain'])):
        raise Error('Transport candidate does not contain the exact requested target mode')
    if transport and request.get('offnode_sha256') == request['before_sha256']:
        raise Error('Transport commit requires a new dual-state backup, not the pre-change archive')
    check_lease()
    if (ROOT / 'export-sha256').read_text() != request.get('offnode_sha256'):
        raise Error('Require verified off-node device backup receipt')
    if cdn:
        validate_cdn_proof(request, fingerprint)
        auth_commit_verdict(request, pending, fingerprint)
    backend_health(ROOT / 'active', manifest)
    check_lease()
    front_config(ROOT / 'active', manifest, True)
    ctl('restart', FRONT)
    wait_tls(manifest, pending=True)
    process_health()
    check_lease()
    manifest['published'] = True
    atomic(ROOT / 'active/manifest.json', dumps(manifest))
    result = {'committed': True, 'published': True, 'request_sha256': fingerprint,
              'machine_id': request['machine_id'], 'node_id': manifest['node_id']}
    if transport:
        result['transports'] = transports(manifest)
        if cdn:
            result.update(offnode_sha256=request['offnode_sha256'], cdn_proof=request['cdn_proof'], cdn_proof_sha256=request['cdn_proof_sha256'],
                          cdn_auth_verdict=pending['cdn_auth']['verdict'])
    atomic(receipt_path(request), dumps(result))
    (ROOT / 'pending.json').unlink()
    os.replace(ROOT / 'device-rollback', ROOT / ('completed-' + request['operation_id']))
    return result


def recover_transaction(force=False):
    pending_path = ROOT / 'pending.json'
    if not pending_path.exists() and (ROOT / 'device-rollback/intent.json').exists():
        staged = read_json(ROOT / 'device-rollback/intent.json')
        # A completed journal was disarmed before old-adapter restoration.
        if receipt_path(staged).exists():
            return recover_devices(staged)
        atomic(pending_path, dumps(staged))
    if not pending_path.exists():
        return {'pending': False, 'committed': (ROOT / 'active/manifest.json').exists()}
    pending = read_json(pending_path)
    if not force and pending.get('phase') not in ('recovering', 'restore-queued') and pending['boot_id'] == boot_id() and time.time() < pending['deadline']:
        return {'pending': True, 'waiting': True}
    if pending.get('kind') in ('devices', 'transport-upgrade'):
        return recover_devices(pending)
    check_units(require_all=False)
    for service in (FRONT, PANEL):
        if (UNITS / service).is_file():
            ctl('disable', '--now', service)
    active = ROOT / 'active'
    if active.exists():
        # Preserve all failed private data, never delete someone else's files.
        os.replace(active, ROOT / ('failed-' + secrets.token_hex(8)))
    if pending['had_active']:
        raise Error('Unexpected recovery state; preserve files and inspect from console')
    pending_path.unlink()
    return {'pending': False, 'restored': 'pristine; proxy/front stopped and disabled; failed private candidate retained'}


def recover(force=False):
    backup = ROOT / 'backup-stop.json'
    if not backup.exists():
        return recover_transaction(force)
    pending_path = ROOT / 'pending.json'
    if pending_path.exists():
        pending = read_json(pending_path)
        receipt = receipt_path(dict(pending, operation_id=pending.get('operation_id', 'none')))
        committed = receipt.exists() and read_json(receipt).get('committed')
        due = (force or pending['boot_id'] != boot_id() or time.time() >= pending['deadline'] or
               pending.get('phase') in ('recovering', 'restore-queued'))
        if committed or due:
            result = recover_transaction(force=True)
            if result.get('pending') or not backup.exists():
                return result
            if not result.get('committed'):
                disarm(backup)  # pristine candidate recovery never revives its front
                return result
    return recover_backup()


def status(request):
    marker = read_json(ROOT / 'owner.json')
    if marker.get('owner') != request['owner'] or marker.get('machine_id') != machine_id() or request.get('machine_id', machine_id()) != machine_id():
        raise Error('Wrong host/owner for status')
    if (ROOT / 'pending.json').exists():
        raise Error('Pending transaction: recover before normal operations')
    active = ROOT / 'active'
    manifest = read_json(active / 'manifest.json')
    if 'ssh_port' in request and 'management' in manifest and manifest['management'] != {'ssh_port': request['ssh_port']}:
        raise Error('Management SSH port differs from owned deployment')
    backend_health(active, manifest)
    tls_probe(manifest)
    process_health()
    if (ROOT / 'host.py').is_symlink():
        raise Error('Symlink recovery adapter')
    result = {'healthy': True, 'published': manifest['published'], 'node_id': manifest['node_id'],
            'transports': transports(manifest),
            'needs_transport_upgrade': not dual_enabled(manifest),
            'adapter_sha256': hashlib.sha256(regular_adapter_bytes(ROOT / 'host.py')).hexdigest(), 'devices': list(device_names(manifest)),
            'machine_id': marker['machine_id'], 'versions': {k: read_json(active / 'release-lock.json')[k]['version'] for k in ('panel', 'core', 'front')}}
    if cdn_enabled(manifest):
        result['tcp_cdn'] = {'schema': 1, 'domain': manifest['tcp_cdn']['domain']}
    if 'rename_target' in request:
        result['rename_target_has_history'] = bool(rename_target_has_history(active / 'db/x-ui.db', request['rename_target']))
    return result


def commit(request):
    if not (ROOT / 'pending.json').exists():
        return status(request)  # Lost SSH reply after commit is not an abort.
    check_lease()
    marker = read_json(ROOT / 'owner.json')
    if marker['owner'] != request['owner'] or marker['machine_id'] != request['machine_id'] or marker['machine_id'] != machine_id():
        raise Error('Wrong owner/machine at publication')
    if not (ROOT / 'export-sha256').is_file() or (ROOT / 'export-sha256').read_text() != request.get('offnode_sha256'):
        raise Error('Require a verified off-node backup receipt before publication')
    active = ROOT / 'active'
    manifest = read_json(active / 'manifest.json')
    backend_health(active, manifest)
    tls_probe(manifest)
    process_health()
    check_lease()  # caller holds the operation lock; timer timing is irrelevant
    front_config(active, manifest, True)
    check_lease()  # validation may have taken time; do not start an expired front
    ctl('restart', FRONT)
    wait_tls(manifest, pending=True)
    process_health()
    check_lease()  # readiness/ownership checks must not outlive the lease
    manifest['published'] = True
    atomic(active / 'manifest.json', dumps(manifest))
    ctl('enable', PANEL, FRONT)
    (ROOT / 'pending.json').unlink()
    return {'published': True, 'machine_id': marker['machine_id'], 'node_id': manifest['node_id']}


def check_request_identity(request):
    connection = os.environ.get('QD_SSH_CONNECTION', os.environ.get('SSH_CONNECTION', '')).split()
    if len(connection) != 4 or connection[2] != public_ip(request['endpoint']):
        raise Error('Wrong SSH endpoint for private operation')
    if request.get('machine_id') != machine_id():
        raise Error('Wrong machine identity for private operation')
    if 'ssh_port' in request and (type(request['ssh_port']) is not int or request['ssh_port'] != int(connection[3])):
        raise Error('Wrong verified SSH management port')


def abort(request):
    check_request_identity(request)
    if not ROOT.exists():
        return {'pending': False, 'committed': False, 'pristine': True}
    marker = read_json(ROOT / 'owner.json')
    if marker['owner'] != request['owner'] or marker['machine_id'] != request['machine_id']:
        raise Error('Wrong host/owner for recovery')
    if request.get('device_action') or request.get('mode') == 'transport-upgrade':
        fingerprint = transaction_request(request)
        receipt = transaction_receipt(request)
        if (ROOT / 'pending.json').exists() and read_json(ROOT / 'pending.json').get('request_sha256') != fingerprint:
            raise Error('Recovery request differs from pending device operation')
        if (ROOT / 'device-rollback/intent.json').exists() and read_json(ROOT / 'device-rollback/intent.json')['request_sha256'] != fingerprint:
            raise Error('Recovery request differs from staged device operation')
        result = recover(force=True)
        if result.get('pending'):
            return result
        if receipt:
            return receipt
        # An old active deployment is not proof that THIS device operation
        # committed. No durable receipt means unchanged or rolled-back state.
        return dict(result, committed=False)
    return recover(force=True)


def main():
    os.umask(0o077)
    action = sys.argv[1] if len(sys.argv) == 2 else ''
    try:
        if action == 'guard':
            guard()
            return
        if action == 'preflight':
            print(dumps(preflight(json.load(sys.stdin))), end='')
            return
        # The lock is outside the owned tree so read-only preflight does not
        # create/claim it. flock is released by kernel even on interrupted SSH.
        fd = os.open('/run/lock/qd-3x-ui.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as lock:
            try:
                flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if action == 'recover' else 0)
                fcntl.flock(lock, flags)
            except BlockingIOError:
                # Periodic recovery must finish, not wait behind a caller whose
                # service start is itself ordered after this recovery oneshot.
                print(dumps({'pending': True, 'busy': True}), end='')
                return
            if action == 'transport-prepare':
                result = transport_prepare(sys.stdin.buffer)
            elif action == 'devices-prepare':
                result = devices_prepare(sys.stdin.buffer)
            elif action == 'prepare':
                result = prepare(sys.stdin.buffer)
            elif action == 'recover':
                result = recover()
            else:
                request = json.load(sys.stdin)
                check_request_identity(request)
                if action == 'preflight':
                    result = preflight(request)
                elif action == 'status':
                    result = status(request)
                elif action == 'devices-commit':
                    result = devices_commit(request)
                elif action == 'transport-commit':
                    result = transport_commit(request)
                elif action == 'transport-auth-begin':
                    result = transport_auth_begin(request)
                elif action == 'transport-auth-finish':
                    result = transport_auth_finish(request)
                elif action == 'devices-list':
                    manifest = device_owner(request)
                    if (ROOT / 'pending.json').exists():
                        raise Error('Pending transaction; recover first')
                    accounts = snapshot_accounts(ROOT / 'active/db/x-ui.db', manifest)
                    snapshot_host_group(ROOT / 'active/db/x-ui.db', manifest)
                    result = {name: {'enabled': account['enable']} for name, account in accounts.items()}
                elif action == 'commit':
                    result = commit(request)
                elif action == 'abort':
                    result = abort(request)
                elif action in ('backup', 'outputs'):
                    marker = read_json(ROOT / 'owner.json')
                    if marker['owner'] != request['owner'] or marker['machine_id'] != request['machine_id'] or marker['machine_id'] != machine_id():
                        raise Error('Wrong host/owner for private export')
                    if action == 'backup':
                        backup = export_backup(request)
                        with backup.open('rb') as stream:
                            shutil.copyfileobj(stream, sys.stdout.buffer)
                        return
                    if (ROOT / 'pending.json').exists():
                        pending = read_json(ROOT / 'pending.json')
                        if pending.get('kind') in ('devices', 'transport-upgrade') and pending.get('phase') != 'prepared':
                            raise Error('Owned transaction not ready for output export')
                        if pending.get('cdn_auth', {}).get('phase') not in (None, 'restored'):
                            raise Error('Active auth diagnostic cannot enter output export')
                    active = ROOT / 'active'
                    result = {name: base64.b64encode(data).decode() for name, data in backend_health(active, read_json(active / 'manifest.json')).items()}
                else:
                    raise Error('Unknown remote operation')
            print(dumps(result), end='')
    except Exception as exc:
        # No traceback, URLs or native response bodies in operator logs.
        print('vps-host: ' + (str(exc) if isinstance(exc, Error) else type(exc).__name__ + '; private candidate retained'), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
