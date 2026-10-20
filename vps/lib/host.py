#!/usr/bin/env python3
"""Single-host 3x-ui adapter. SSH sends requests on stdin; never run an installer.

The panel owns accounts, SQLite schema, Xray config and subscription encoders.
This file owns only its private tree and four explicitly named systemd units.
"""
from __future__ import annotations

import base64
import contextlib
import copy
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
from urllib.parse import urlencode, urlsplit, parse_qs
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


def required_ports(config):
    return (80, config['proxy_port'], 8443, ADMIN_PORT, SUB_PORT, API_PORT)


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


def device_names(manifest):
    """The manifest owns membership; native rows can never extend it implicitly."""
    accounts = manifest.get('accounts')
    if not isinstance(accounts, dict) or any(not isinstance(d, str) or
            not re.fullmatch(r'[a-z][a-z0-9-]{0,31}', d) for d in accounts):
        raise Error('Invalid owned device roster; use lowercase names up to32 characters')
    identities, subscriptions = [], []
    for name, account in accounts.items():
        if account.get('email') != name or not isinstance(account.get('enable'), bool):
            raise Error('Invalid owned device identity')
        try:
            uuid.UUID(account['id'])
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise Error('Invalid owned device UUID') from exc
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', account.get('subId', '')):
            raise Error('Invalid owned subscription identity')
        identities.append(uuid.UUID(account['id']))
        subscriptions.append(account['subId'])
    if len(set(identities)) != len(identities) or len(set(subscriptions)) != len(subscriptions):
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
    return json.dumps({'geodata-mode': True, 'rules': rules})


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
    """Attest the single owned native Host group before any endpoint write."""
    inbounds = api.call('/panel/api/inbounds/list') or []
    if len(inbounds) != 1 or inbounds[0].get('remark') != 'SELF-main':
        raise Error('Unexpected native Host inbound membership')
    inbound = inbounds[0]
    stream = inbound['streamSettings']
    stream = json.loads(stream) if isinstance(stream, str) else stream
    external = stream.get('externalProxy', [])
    if (len(external) != 1 or inbound['port'] != manifest['config']['proxy_port'] or
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
    if len(groups) != 1 or groups[0].get('groupId') != group_id:
        raise Error('Unexpected native Host group membership')
    group = api.call('/panel/api/hosts/get/' + group_id)
    expected = [old_endpoint + ':' + str(manifest['config']['proxy_port'])]
    if (group.get('groupId') != group_id or group.get('inboundIds') != [inbound['id']] or
            group.get('hosts') != expected or group.get('port') != manifest['config']['proxy_port']):
        raise Error('Unexpected native Host group endpoint/inbound membership')
    return inbound, group


def configure_native(folder, manifest, fresh, rotate=False):
    if fresh:
        # admin/admin exists only in the loopback candidate; change via native API,
        # never place a password in process argv or systemd Environment.
        bootstrap = dict(manifest, admin_user='admin')
        api = wait_api(bootstrap, 'admin')
        api.call('/panel/api/setting/updateUser', {'oldUsername': 'admin', 'oldPassword': 'admin',
                 'newUsername': manifest['admin_user'], 'newPassword': manifest['admin_password']})
    api = wait_api(manifest)
    if not fresh:
        owned_host_group(folder, api, manifest)  # before settings, inbound or client writes
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
        if len(external) != 1 or inbound['port'] != manifest['config']['proxy_port']:
            raise Error('Unexpected restored native endpoint configuration')
        external[0]['dest'] = manifest['endpoint']
        if rotate:
            reality = stream['realitySettings']
            reality.update(manifest['reality'])
            reality.setdefault('settings', {})['publicKey'] = manifest['reality']['publicKey']
        inbound['streamSettings'] = json.dumps(stream)
        desired_group = copy.deepcopy(group)
        desired_group['hosts'] = [manifest['endpoint'] + ':' + str(manifest['config']['proxy_port'])]
        if desired_group['hosts'] != group['hosts']:
            # Hosts, not externalProxy, is authoritative for native feeds. Send
            # the complete object and change the address only, never its policy.
            api.call('/panel/api/hosts/update/' + group['groupId'], desired_group)
        api.call('/panel/api/inbounds/update/' + str(inbound['id']), inbound)
        if owned_host_group(folder, api, manifest)[1] != desired_group:
            raise Error('Native Host update changed unexpected fields')
        if rotate:
            # Each complete fresh native object carries quota/expiry/reset and
            # disabled state. Change only UUID, never reconstruct a Client DTO.
            for device in device_names(manifest):
                current = api.call('/panel/api/inbounds/list')[0]['settings']
                current = json.loads(current) if isinstance(current, str) else current
                account = next(c for c in current['clients'] if c['email'] == device)
                # The native update endpoint has one policy outside Client DTO:
                # LimitHwid. Preserve it from its own complete native record.
                detail = api.call('/panel/api/clients/get/' + device)['client']
                account['limitHwid'] = detail['limitHwid']
                account['id'] = str(uuid.uuid4())
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


def caddyfile(manifest, published):
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
'''


def front_config(folder, manifest, published):
    candidate = folder / 'Caddyfile.candidate'
    atomic(candidate, caddyfile(manifest, published))
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
    others = [manifest['accounts'][d]['id'] for d in device_names(manifest) if d != device]
    if fmt == 'sub':
        try:
            text = base64.b64decode(data, validate=True).decode()
            links = text.strip().splitlines()
            if len(links) != 1:
                raise ValueError()
            parsed = urlsplit(links[0])
            query = parse_qs(parsed.query)
            valid = (parsed.scheme == 'vless' and uuid.UUID(parsed.username) == uuid.UUID(current['id']) and
                     parsed.hostname == manifest['endpoint'] and parsed.port == manifest['config']['proxy_port'] and
                     query.get('security') == ['reality'] and query.get('flow') == ['xtls-rprx-vision'] and
                     query.get('pbk') == [manifest['reality']['publicKey']] and
                     query.get('sid') == [manifest['reality']['shortIds'][0]] and
                     query.get('sni') == [manifest['config']['reality_target']])
            if not valid:
                raise ValueError('URI identity mismatch')
        except (ValueError, KeyError, UnicodeError) as exc:
            raise Error('Native URI subscription does not match device/node identity') from exc
    else:
        text = data.decode()
        if not all(s in text for s in ('proxies:', 'proxy-groups:', 'rules:', current['id'], manifest['endpoint'], 'MATCH,PROXY', 'bilibili.com,DIRECT')):
            raise Error('Native Mihomo output is not the expected full routed profile')
    if any(other in text for other in others):
        raise Error('Cross-device credentials present in native subscription')


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
    # SQLite/API is the source of truth. Supported native per-device credential,
    # SubID and enabled changes must survive backup/replacement, not be undone by
    # an old wrapper copy. Only explicit manifest membership is wrapper-owned.
    for client in clients:
        uuid.UUID(client['id'])
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', client['subId']) or not isinstance(client['enable'], bool):
            raise Error('Invalid native account identity')
        manifest['accounts'][client['email']] = {k: client[k] for k in ('id', 'subId', 'email', 'enable', 'flow')}
    # Count global accounts AND joins, including unattached/foreign native rows.
    snapshot_accounts(folder / 'db/x-ui.db', manifest)
    device_names(manifest)
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
    ctx = ssl.create_default_context()
    with socket.create_connection(('127.0.0.1', 8443), timeout=10) as raw:
        with ctx.wrap_socket(raw, server_hostname=manifest['config']['domain']) as stream:
            stream.sendall(('GET / HTTP/1.1\r\nHost: ' + manifest['config']['domain'] + '\r\nConnection: close\r\n\r\n').encode())
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
    config = read_json(active / 'manifest.json')['config']
    ports = required_ports(config)
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
        if int(port) in (ADMIN_PORT, SUB_PORT, API_PORT) and not fields[3].startswith('127.0.0.1:'):
            raise Error('Backend/admin escaped loopback')
        pids = re.findall(r'pid=(\d+)', line)
        if not pids:
            raise Error('Cannot attest listener process ownership')
        unit = FRONT if int(port) in (80, 8443) else PANEL
        binary = 'caddy' if unit == FRONT else ('xray-linux-amd64' if int(port) in (config['proxy_port'], API_PORT) else 'x-ui')
        for pid in pids:
            proc = Path('/proc') / pid
            if unit not in (proc / 'cgroup').read_text() or os.readlink(proc / 'exe') != str(active / 'bin' / binary):
                raise Error('Foreign/stale loaded executable on owned listener')
    if found != set(ports):
        raise Error('One or more declared listeners are missing')


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
    if config['proxy_port'] == int(connection[3]):
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
    check_units(require_all=(ROOT / 'active/manifest.json').exists())
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
        if not owned or not pids:
            raise Error('Required listener port is already in use; no port stealing')
        for pid in pids:
            cgroup = Path('/proc') / pid / 'cgroup'
            group = cgroup.read_text() if cgroup.exists() else ''
            service = FRONT if port in ('80', '8443') else PANEL
            if service not in group:
                raise Error('Foreign process on an owned listener port')
        if port in (str(ADMIN_PORT), str(SUB_PORT), str(API_PORT)) and not columns[3].startswith('127.0.0.1:'):
            raise Error('Management/backend listener escaped loopback')
    if shutil.disk_usage(ROOT if ROOT.exists() else ROOT.parent).free < 2 * 1024**3:
        raise Error('Require at least2GiB free for verified artifacts, staging and complete off-node export')
    if (ROOT / 'pending.json').exists():
        raise Error('Persistent transaction pending; run recover, never overwrite it')
    return {'machine_id': mid, 'owned': owned, 'endpoint': expected,
            'platform': 'ubuntu-24.04-amd64', 'firewall': 'empty; unchanged', 'ports': list(ports)}


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


def snapshot_accounts(database, manifest=None):
    """Read only publication metadata from the same authoritative closed DB."""
    with contextlib.closing(sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True)) as db:
        rows = db.execute('select email,uuid,sub_id,enable,flow from clients order by email').fetchall()
    expected = set(DEVICES if manifest is None else device_names(manifest))
    if len(rows) != len(expected) or {row[0] for row in rows} != expected:
        raise Error('Unexpected snapshot account membership')
    if manifest is not None:
        with contextlib.closing(sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True)) as db:
            inbounds = db.execute('select id from inbounds').fetchall()
            joins = db.execute('select c.email,ci.inbound_id from client_inbounds ci left join clients c on c.id=ci.client_id').fetchall()
        if len(inbounds) != 1 or len(joins) != len(expected) or set(joins) != {(name, inbounds[0][0]) for name in expected}:
            raise Error('Unexpected snapshot account inbound membership')
    return {email: {'email': email, 'id': identity, 'subId': sub_id, 'enable': bool(enabled), 'flow': flow}
            for email, identity, sub_id, enabled, flow in rows}


def snapshot_host_group(database, manifest):
    """Verify Host identity from an owned DB; old metadata upgrades are read-only."""
    with contextlib.closing(sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True)) as db:
        inbounds = db.execute('select id,user_id,remark,protocol,port from inbounds').fetchall()
        admins = db.execute('select id from users where username=?', (manifest['admin_user'],)).fetchall()
        # No enabled/hidden filter: foreign inactive rows and duplicates count.
        hosts = db.execute('select group_id,inbound_id,address,port from hosts').fetchall()
    if (len(inbounds) != 1 or len(admins) != 1 or
            inbounds[0][1:] != (admins[0][0], 'SELF-main', 'vless', manifest['config']['proxy_port'])):
        raise Error('Unexpected native Host owned-inbound identity')
    if len(hosts) != 1:
        raise Error('Unexpected native Host snapshot membership')
    group_id, inbound_id, address, port = hosts[0]
    if (not isinstance(group_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', group_id) or
            inbound_id != inbounds[0][0] or address != manifest['endpoint'] or port != manifest['config']['proxy_port']):
        raise Error('Unexpected native Host snapshot endpoint/membership')
    if 'host_group_id' in manifest and manifest['host_group_id'] != group_id:
        raise Error('Changed persisted native Host group identity')
    return group_id


def reject_symlinks(root):
    if root.is_symlink():
        raise Error('Symlink in owned export source')
    for directory, names, files in os.walk(root, followlinks=False):
        if any((Path(directory) / name).is_symlink() for name in names + files):
            raise Error('Symlink in owned export source')


def adapter_source():
    return Path(__file__).read_bytes()


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
    if stored.is_symlink():
        raise Error('Unknown stored backup adapter; no service stop')
    stored_sha = digest(stored)
    if not accepted_adapter_digest(stored_sha, sha):
        raise Error('Unknown stored backup adapter; no service stop')
    # Every explicitly reviewed slot is optional, but any existing input must
    # satisfy the no-link/pinned-byte contract before ANY migration write.
    previous = {pin: read_previous_adapter(pin) for pin in ADAPTER_PREDECESSORS}
    check_units()
    if stored_sha != sha:
        if previous[stored_sha] is None:
            retain_previous_adapter(stored.read_bytes(), stored_sha)
            previous[stored_sha] = read_previous_adapter(stored_sha)
        atomic(stored, source, 0o700)
    verified = {pin: read_previous_adapter(pin) for pin in ADAPTER_PREDECESSORS}
    if any(previous[pin] is not None and verified[pin] is None for pin in previous):
        raise Error('Retained predecessor disappeared during migration')
    return {ADAPTER_PREDECESSORS[pin]: data for pin, data in verified.items() if data is not None}


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
        if pending.get('kind') == 'devices' and pending.get('phase') != 'prepared':
            raise Error('Device transaction not ready for backup')
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
            return {'unchanged': True, **info}
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
            atomic(active / 'manifest.json', dumps(manifest))
            front_config(active, manifest, False)
            ctl('start', PANEL)
            configure_native(active, manifest, fresh, rotate=(request['mode'] == 'replace'))
            backend_health(active, manifest)
            atomic(active / 'manifest.json', dumps(manifest))
            ctl('start', FRONT)
            wait_tls(manifest)
            process_health()
            return {'unchanged': False, **info}
        except BaseException:
            # Failures remain recoverable even if this handler itself is killed.
            recover(force=True)
            raise


# Explicit reviewed public-source predecessors; never infer trust from a remote hash.
LEGACY_ADAPTER_SHA256 = '30430e73199f175099d509f056b31c38b17f3eb6ec7ff07972a75003409a000e'
DEPLOYED_ADAPTER_SHA256 = '8777a3d47b07b91bc85de2ce88016efb6de9c3b0568f5688f5bd8d929ed701f1'
ADAPTER_PREDECESSORS = {LEGACY_ADAPTER_SHA256: 'previous-host.py',
                        DEPLOYED_ADAPTER_SHA256: 'previous-host-918103e.py'}


def accepted_adapter_digest(stored, current):
    return stored == current or stored in ADAPTER_PREDECESSORS


def canonical_device_action(action):
    return 'rotate' if action == 'reset' else action


def validate_rename_names(names, roster):
    if names[0] not in roster:
        raise Error('Unknown source device for rename')
    if names[1] in roster:
        raise Error('Rename target already exists')


def device_request(request):
    names = request.get('names')
    action = canonical_device_action(request.get('device_action'))
    if action == 'rename' and isinstance(names, list) and len(names) == 2 and names[0] == names[1]:
        raise Error('Rename requires two distinct names; no changes made')
    if (action not in ('add', 'remove', 'rotate', 'rename') or not isinstance(names, list) or not names or
            len(set(names)) != len(names) or any(not isinstance(n, str) or
                not re.fullmatch(r'[a-z][a-z0-9-]{0,31}', n) for n in names) or
            (action == 'rename' and len(names) != 2) or
            (action not in ('add', 'rename') and len(names) != 1) or
            not re.fullmatch(r'[0-9a-f]{32}', request.get('operation_id', ''))):
        raise Error('Invalid device operation/names/operation ID')
    bound = {k: request[k] for k in ('owner', 'machine_id', 'endpoint', 'config',
                                    'device_action', 'names', 'operation_id')}
    bound['device_action'] = action  # reset aliases the existing rotate protocol before hashing.
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
    return manifest


def devices_prepare(upload):
    with tempfile.TemporaryDirectory(prefix='qd-device-upload-') as tmp:
        stage = Path(tmp)
        safe_unpack(upload, stage)
        request = read_json(stage / 'request.json')
        request['device_action'] = canonical_device_action(request.get('device_action'))
        check_request_identity(request)
        manifest = device_owner(request)
        fingerprint = device_request(request)
        receipt = device_receipt(request)
        if receipt:
            return receipt
        if (ROOT / 'pending.json').exists():
            pending = read_json(ROOT / 'pending.json')
            if pending.get('request_sha256') != fingerprint:
                raise Error('Different pending operation; recover first')
            check_lease()
            if pending.get('phase') == 'prepared':
                return {'prepared': True}
            raise Error('Interrupted device preparation; recover before retry')
        rollback = ROOT / 'device-rollback'
        if rollback.exists():
            raise Error('Retained device recovery intent; run recover first')
        reject_symlinks(ROOT / 'active')
        if (ROOT / 'host.py').is_symlink():
            raise Error('Symlink recovery adapter')
        adapter = (stage / 'host.py').read_bytes()
        current_sha = hashlib.sha256(adapter).hexdigest()
        if current_sha != request['adapter_sha256'] or not accepted_adapter_digest(digest(ROOT / 'host.py'), current_sha):
            raise Error('Unknown stored/uploaded adapter digest; no device writes')
        check_units()
        binary_health(ROOT / 'active')
        # Publication probes are not an ownership test: restricted feeds may deny.
        api = API(manifest)
        inbound, _ = owned_host_group(ROOT / 'active', api, manifest)
        snapshot_accounts(ROOT / 'active/db/x-ui.db', manifest)
        names, action = request['names'], request['device_action']
        existing = set(device_names(manifest))
        if (action == 'add' and set(names) <= existing) or (action == 'remove' and names[0] not in existing):
            result = {'unchanged': True, 'committed': True, 'request_sha256': fingerprint,
                      'machine_id': request['machine_id'], 'node_id': manifest['node_id']}
            atomic(ROOT / 'device-receipts' / (request['operation_id'] + '.json'), dumps(result))
            return result
        if action == 'add' and set(names) & existing:
            raise Error('Mixed existing/new names; no partial add')
        if action == 'rotate' and names[0] not in existing:
            raise Error('Unknown device for reset')
        if action == 'rename':
            validate_rename_names(names, existing)
        process_health()
        tls_probe(manifest)
        # All rollback inputs and durable intent precede adapter/service mutation.
        preparation = Path(tempfile.mkdtemp(prefix='device-snapshot-', dir=ROOT))
        for name, source in [('manifest.json', ROOT / 'active/manifest.json'),
                             ('Caddyfile', ROOT / 'active/Caddyfile'), ('host.py', ROOT / 'host.py')]:
            atomic(preparation / name, source.read_bytes())
        services = {}
        for unit in (PANEL, FRONT):
            services[unit] = {key: subprocess.run(['systemctl', command, '--quiet', unit], capture_output=True).returncode == 0
                              for key, command in [('running', 'is-active'), ('enabled', 'is-enabled')]}
        intent = {'boot_id': boot_id(), 'deadline': time.time() + 900, 'had_active': True,
                  'kind': 'devices', 'phase': 'snapshot', 'operation_id': request['operation_id'],
                  'request_sha256': fingerprint, 'services': services}
        atomic(preparation / 'intent.json', dumps(intent))
        os.replace(preparation, rollback)
        directory = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        # Crash before replacement leaves the old adapter and services unchanged;
        # crash after replacement is recognized by recover's staged-intent branch.
        atomic(ROOT / 'device-receipts' / (request['operation_id'] + '.json'),
               dumps({'request_sha256': fingerprint, 'committed': False}))
        atomic(ROOT / 'host.py', adapter, 0o700)
        atomic(ROOT / 'pending.json', dumps(intent))
        active = ROOT / 'active'
        try:
            ctl('stop', FRONT)
            ctl('stop', PANEL)
            db_snapshot(active / 'db/x-ui.db', rollback / 'x-ui.db')
            manifest['accounts'] = snapshot_accounts(rollback / 'x-ui.db', manifest)
            intent['phase'] = 'ready'
            atomic(ROOT / 'pending.json', dumps(intent))
            ctl('start', PANEL)
            api = wait_api(manifest)
            if action == 'add':
                for name in names:
                    check_lease()
                    account = {'id': str(uuid.uuid4()), 'subId': secrets.token_hex(24), 'email': name,
                               'enable': True, 'flow': 'xtls-rprx-vision'}
                    # Collision checking covers ALL global native accounts above.
                    if any(uuid.UUID(account['id']) == uuid.UUID(c['id']) or account['subId'] == c['subId']
                           for c in manifest['accounts'].values()):
                        raise Error('Generated credential collision; recover and retry')
                    api.call('/panel/api/clients/add', {'client': account, 'inboundIds': [inbound['id']]})
                    manifest['accounts'][name] = account
            elif action == 'remove':
                check_lease()
                api.call('/panel/api/clients/del/' + names[0], {})
                del manifest['accounts'][names[0]]
            else:
                name = names[0]
                settings = api.call('/panel/api/inbounds/list')[0]['settings']
                settings = json.loads(settings) if isinstance(settings, str) else settings
                account = next(c for c in settings['clients'] if c['email'] == name)
                account['limitHwid'] = api.call('/panel/api/clients/get/' + name)['client']['limitHwid']
                if action == 'rename':
                    account['email'] = names[1]
                else:
                    account.update(id=str(uuid.uuid4()), subId=secrets.token_hex(24))
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
            ctl('start', FRONT)
            wait_tls(manifest, pending=True)
            process_health()
            check_lease()
            intent['phase'] = 'prepared'
            atomic(ROOT / 'pending.json', dumps(intent))
            return {'prepared': True}
        except BaseException:
            recover(force=True)
            raise


def recover_devices(pending):
    rollback = ROOT / 'device-rollback'
    receipt = ROOT / 'device-receipts' / (pending['operation_id'] + '.json')
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
            wait_api(restored)
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


def devices_commit(request):
    manifest = device_owner(request)
    fingerprint = device_request(request)
    receipt = device_receipt(request)
    if receipt:
        return receipt
    pending = read_json(ROOT / 'pending.json')
    if pending.get('kind') != 'devices' or pending.get('phase') != 'prepared' or pending['request_sha256'] != fingerprint:
        raise Error('Device commit does not match prepared intent')
    check_lease()
    if (ROOT / 'export-sha256').read_text() != request.get('offnode_sha256'):
        raise Error('Require verified off-node device backup receipt')
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
    atomic(ROOT / 'device-receipts' / (request['operation_id'] + '.json'), dumps(result))
    (ROOT / 'pending.json').unlink()
    os.replace(ROOT / 'device-rollback', ROOT / ('completed-' + request['operation_id']))
    return result


def recover_transaction(force=False):
    pending_path = ROOT / 'pending.json'
    if not pending_path.exists() and (ROOT / 'device-rollback/intent.json').exists():
        staged = read_json(ROOT / 'device-rollback/intent.json')
        # A completed journal was disarmed before old-adapter restoration.
        if (ROOT / 'device-receipts' / (staged['operation_id'] + '.json')).exists():
            return recover_devices(staged)
        atomic(pending_path, dumps(staged))
    if not pending_path.exists():
        return {'pending': False, 'committed': (ROOT / 'active/manifest.json').exists()}
    pending = read_json(pending_path)
    if not force and pending.get('phase') not in ('recovering', 'restore-queued') and pending['boot_id'] == boot_id() and time.time() < pending['deadline']:
        return {'pending': True, 'waiting': True}
    if pending.get('kind') == 'devices':
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
        receipt = ROOT / 'device-receipts' / (pending.get('operation_id', 'none') + '.json')
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
    backend_health(active, manifest)
    tls_probe(manifest)
    process_health()
    if (ROOT / 'host.py').is_symlink():
        raise Error('Symlink recovery adapter')
    return {'healthy': True, 'published': manifest['published'], 'node_id': manifest['node_id'],
            'adapter_sha256': digest(ROOT / 'host.py'), 'devices': list(device_names(manifest)),
            'machine_id': marker['machine_id'], 'versions': {k: read_json(active / 'release-lock.json')[k]['version'] for k in ('panel', 'core', 'front')}}


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


def abort(request):
    check_request_identity(request)
    if not ROOT.exists():
        return {'pending': False, 'committed': False, 'pristine': True}
    marker = read_json(ROOT / 'owner.json')
    if marker['owner'] != request['owner'] or marker['machine_id'] != request['machine_id']:
        raise Error('Wrong host/owner for recovery')
    if request.get('device_action'):
        fingerprint = device_request(request)
        receipt = device_receipt(request)
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
            if action == 'devices-prepare':
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
                elif action == 'devices-list':
                    manifest = device_owner(request)
                    if (ROOT / 'pending.json').exists():
                        raise Error('Pending transaction; recover first')
                    snapshot_accounts(ROOT / 'active/db/x-ui.db', manifest)
                    result = {name: {'enabled': account['enable']} for name, account in snapshot_accounts(ROOT / 'active/db/x-ui.db', manifest).items()}
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
                        if pending.get('kind') == 'devices' and pending.get('phase') != 'prepared':
                            raise Error('Device transaction not ready for output export')
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
