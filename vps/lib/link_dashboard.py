"""Private offline link view. Native feeds own payload bytes; this module only draws them."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import unquote, urlsplit

from vendor.qrcodegen import DataTooLongError, QrCode, QrSegment


class DashboardError(ValueError):
    pass


INVENTORY = '.qd-dashboard.json'
ASSET_NAME = re.compile(r'qd-([0-9a-f]{64})\.(svg|css|js|yaml|txt|json)')
BOOTSTRAP_NAME = re.compile(r'([a-z][a-z0-9-]{0,31})(\.yaml|\.txt|\.uris\.txt|\.urls\.json|\.management\.yaml)')
MAX_INVENTORY_BYTES = 4 * 1024 * 1024


def qr_svg(payload: bytes) -> bytes:
    """Encode exact UTF-8 bytes, with ECI 26, medium ECC and a four-module border."""
    if len(payload) > 2953:
        raise DashboardError('Native link exceeds QR capacity; no dashboard written')
    try:
        payload.decode('utf-8')
    except UnicodeError:
        raise DashboardError('Native link is not valid UTF-8; no dashboard written') from None
    # ECI makes non-ASCII native labels unambiguous to standards-compliant readers.
    try:
        qr = QrCode.encode_segments([QrSegment.make_eci(26), QrSegment.make_bytes(payload)],
                                    QrCode.Ecc.MEDIUM, boostecl=False)
    except DataTooLongError:
        raise DashboardError('Native link exceeds QR capacity; no dashboard written') from None
    size = qr.get_size()
    cells = ''.join(f'M{x + 4},{y + 4}h1v1h-1z' for y in range(size) for x in range(size)
                    if qr.get_module(x, y))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size + 8} {size + 8}" '
            f'shape-rendering="crispEdges"><rect width="100%" height="100%" fill="#fff"/>'
            f'<path d="{cells}" fill="#000"/></svg>\n').encode('ascii')


def native_material(device, files, urls):
    """Keep the four existing bootstrap files byte-for-byte compatible."""
    return {device + '.yaml': files[device + '.yaml'], device + '.txt': files[device + '.txt'],
            device + '.uris.txt': base64.b64decode(files[device + '.txt'], validate=True),
            device + '.urls.json': (json.dumps(urls[device], ensure_ascii=False, indent=2) + '\n').encode()}


def safe_json(value):
    text = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    for old, new in (('&', '\\u0026'), ('<', '\\u003c'), ('>', '\\u003e'),
                     ('\u2028', '\\u2028'), ('\u2029', '\\u2029')):
        text = text.replace(old, new)
    return text


def render(manifest, files, urls, digest, assets, device=None, extra_downloads=None):
    """Return complete in-memory page and immutable local assets before publication."""
    names = sorted(manifest['accounts'])
    if any(not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,31}', name) for name in names):
        raise DashboardError('Invalid dashboard device roster')
    if device is not None:
        if device not in names or not manifest['accounts'][device]['enable']:
            raise DashboardError('Dashboard device is removed, disabled or unknown')
        names = [device]
    artifacts = {}

    def asset(data, extension):
        name = 'qd-' + hashlib.sha256(data).hexdigest() + '.' + extension
        artifacts[name] = data
        return name

    model = {'schema': 1, 'snapshot': {
        'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'backup_sha256': digest, 'endpoint': manifest['endpoint']}, 'devices': []}
    for name in names:
        enabled = manifest['accounts'][name]['enable']
        entry = {'name': name, 'enabled': enabled, 'links': [], 'downloads': []}
        model['devices'].append(entry)
        if not enabled:
            continue
        material = native_material(name, files, urls)
        material.update((extra_downloads or {}).get(name, {}))
        decoded = material[name + '.uris.txt']
        payloads = [line.removesuffix(b'\r') for line in decoded.split(b'\n') if line not in (b'', b'\r')]
        for index, payload in enumerate(payloads):
            try:
                uri = payload.decode('utf-8')
                parsed = urlsplit(uri)
            except (UnicodeError, ValueError):
                raise DashboardError('Invalid native link; no dashboard written') from None
            entry['links'].append({'id': f'{name}-node-{index + 1}', 'kind': 'node',
                'label': unquote(parsed.fragment) or parsed.scheme.upper(), 'protocol': parsed.scheme,
                'uri': uri, 'qr': asset(qr_svg(payload), 'svg')})
        for key, label in (('mihomo', 'Mihomo'), ('shadowrocket', 'Shadowrocket')):
            uri = urls[name][key]
            entry['links'].append({'id': f'{name}-{key}', 'kind': 'subscription', 'label': label,
                'protocol': key, 'uri': uri, 'qr': asset(qr_svg(uri.encode('utf-8')), 'svg')})
        labels = {'.yaml': '原生 Mihomo 配置', '.txt': '原生 base64 订阅',
                  '.uris.txt': '原生节点 URI', '.urls.json': '订阅 URL'}
        for filename, data in material.items():
            extension = filename.rsplit('.', 1)[1]
            label = labels.get(filename[len(name):], filename)
            entry['downloads'].append({'label': label, 'path': asset(data, extension)})
    # file:// browsers can block fetch(local file) and ignore <a download>.
    # Embed only explicit model downloads/QRs for Blob downloads without any IO.
    downloadable = {link['qr'] for entry in model['devices'] for link in entry['links']}
    downloadable.update(item['path'] for entry in model['devices'] for item in entry['downloads'])
    model['files'] = {name: base64.b64encode(artifacts[name]).decode('ascii') for name in sorted(downloadable)}
    template = assets['dashboard.html'].decode('utf-8')
    if template.count('__QD_DATA__') != 1:
        raise DashboardError('Dashboard template requires exactly one data placeholder')
    for name in ('dashboard.css', 'dashboard.js'):
        if name not in template:
            raise DashboardError('Dashboard template is missing its local assets')
        template = template.replace(name, asset(assets[name], name.rsplit('.', 1)[1]))
    return model, template.replace('__QD_DATA__', safe_json(model)).encode('utf-8'), artifacts


def inventory(data):
    """A managed name is content-addressed and bounded; no arbitrary deletion paths."""
    try:
        if len(data) > MAX_INVENTORY_BYTES:
            raise ValueError()
        document = json.loads(data)
        if (not isinstance(document, dict) or set(document) != {'schema', 'files'} or
                type(document['schema']) is not int or document['schema'] != 1 or
                not isinstance(document['files'], dict)):
            raise ValueError()
        roots = {}
        for name, digest in document['files'].items():
            if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
                raise ValueError()
            match = ASSET_NAME.fullmatch(name)
            if match is not None:
                if match[1] != digest:
                    raise ValueError()
                continue
            match = BOOTSTRAP_NAME.fullmatch(name)
            if match is None:
                raise ValueError()
            roots.setdefault(match[1], set()).add(match[2])
        required = {'.yaml', '.txt', '.uris.txt', '.urls.json'}
        if any(extensions not in (required, required | {'.management.yaml'}) for extensions in roots.values()):
            raise ValueError()
        return document['files']
    except (ValueError, TypeError, RecursionError):
        raise DashboardError('Invalid dashboard managed inventory; no files changed') from None


def inventory_bytes(files):
    return (json.dumps({'schema': 1, 'files': files}, sort_keys=True, indent=2) + '\n').encode()
