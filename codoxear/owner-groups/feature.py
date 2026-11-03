"""Owner grouping feature installed into the shared HTTP host."""
import asyncio
import hashlib
from pathlib import Path
import stat

import aiohttp
from aiohttp import web

from .registry_projection import RegistryReader, read_registry

HERE = Path(__file__).resolve().parent
NAMESPACE = '__owner_groups__'
STATE = web.AppKey('owner-groups', dict)


def asset():
    body = '\n;\n'.join((HERE / 'web' / path).read_text() for path in ('core.js', 'panel.js', 'bootstrap.js')).encode('utf-8')
    return body, hashlib.sha256(body).hexdigest()[:20]


def session_linkage(rows):
    """Public upstream rows only; stat trusted log paths without reading content."""
    def bound(value):
        if not isinstance(value, str) or not value.strip():
            return False
        path = Path(value)
        try:
            return path.is_absolute() and stat.S_ISREG(path.stat().st_mode)
        except (OSError, ValueError):
            return False
    return [{'session_id': row['session_id'], 'thread_id': row.get('thread_id'),
             'agent_backend': row['agent_backend'], 'has_bound_log': bound(row.get('log_path'))}
            for row in rows]


async def snapshot(request):
    state = request.app[STATE]
    host = state['host']
    json_response = host.json_response
    # Read-only POST bypasses upstream service-worker GET cache/fallback behavior.
    if request.method != 'POST':
        return json_response({'ok': False, 'error': 'Empty read-only POST required; GET snapshots are not fresh'}, 405)
    if request.content_length not in (None, 0) or await request.content.read(1):
        return json_response({'ok': False, 'error': 'Empty request body required'}, 400)
    if request.query_string or not host.same_origin(request):
        return json_response({'ok': False, 'error': 'Same-origin fixed request required'}, 403)
    if not request.headers.get('Cookie'):
        return json_response({'ok': False, 'error': 'Codoxear login required'}, 401)
    try:
        status, payload = await host.upstream_json('GET', '/api/sessions',
            cookie=request.headers['Cookie'], timeout=10)
        if status in (401, 403):
            return json_response({'ok': False, 'error': 'Codoxear login required'}, 401)
        if status != 200 or not isinstance(payload, dict) or not isinstance(payload.get('sessions'), list) or not all(
                isinstance(row, dict) and isinstance(row.get('session_id'), str) and row['session_id'] and
                isinstance(row.get('agent_backend'), str) and
                (row.get('thread_id') is None or isinstance(row['thread_id'], str)) and
                (row.get('log_path') is None or isinstance(row['log_path'], str))
                for row in payload['sessions']):
            raise ValueError('unsupported authentication schema')
        # log_exists is an INTERNAL upstream field, stripped from public API rows.
        # Filesystem metadata work must not block aiohttp's event loop.
        sessions = await asyncio.to_thread(session_linkage, payload['sessions'])
        # Never cache credentials or export paths/transcripts through the bridge.
        data = await asyncio.to_thread(state['projector'])
        if not data['ok']:
            return json_response({'ok': False, 'error': data['error']}, 503)
        return json_response({'owners': data, 'sessions': {'sessions': sessions}, 'demo': state['demo']()})
    except (aiohttp.ClientError, TimeoutError, ValueError, KeyError):
        return json_response({'ok': False, 'error': 'Upstream login verification unavailable or incompatible'}, 503)


async def feature(request):
    host = request.app[STATE]['host']
    name = request.match_info['name']
    if name == 'snapshot':
        return await snapshot(request)
    if request.method not in ('GET', 'HEAD'):
        return web.Response(status=405)
    if name == 'status':
        return host.json_response({'adapter': 'quick-deploy owner-groups/v1',
            'html_compatibility': host.html_status,
            'snapshot': 'Empty read-only POST; requires existing authenticated Codoxear cookie; never public.',
            'native_app': 'Preserved; unsupported HTML/CSP passes through unchanged.'})
    return web.Response(status=404)


def install(app, host):
    app[STATE] = {'host': host, 'projector': RegistryReader(), 'demo': lambda: False}
    host.add_script(NAMESPACE, asset()[0])
    app.router.add_route('*', host.prefix + '/' + NAMESPACE + '/{name}', feature)
