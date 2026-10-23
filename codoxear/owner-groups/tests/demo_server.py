#!/usr/bin/env python3
"""Loopback-only DEMO: real installed Codoxear frontend, synthetic API/registry.

No agents, real cookies or registry writes. --static-dir is read-only test input.
"""
import argparse
import asyncio
import copy
import mimetypes
from pathlib import Path
import sys
import time

from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import proxy
import fixture


async def serve(static_dir, port):
    data = fixture.demo()
    state = {'variant': 'normal', 'auth': True, 'version': 0}
    rows = []
    for index, item in enumerate(data['sessions']['sessions']):
        rows.append({**item, 'log_path': '/example/synthetic-' + str(index) + '.jsonl', 'log_exists': True,
            'alias': 'DEMO ' + item['session_id'].removeprefix('demo-'), 'cwd': '/example/shared',
            'owned': False, 'transport': 'socket', 'busy': False, 'lost': False,
            'start_ts': time.time() - index * 60, 'updated_ts': time.time() - index * 60, 'transcript_state': 'bound'})
    def current_rows():
        result = copy.deepcopy(rows)
        if state['variant'] == 'rebind':
            result[0]['thread_id'] = 'pi-synthetic-switched'
        if state['variant'] == 'duplicate-thread':
            result.append({**result[0], 'session_id': 'demo-second-thread'})
        result[0]['alias'] += f" v{state['version']}"
        return result

    def projector():
        if state['variant'] == 'bridge-down':
            return {'schema': 'codoxear-owner-groups/v1', 'ok': False, 'error': 'Synthetic unavailable', 'owners': []}
        result = copy.deepcopy(data['owners'])
        if state['variant'] == 'empty': result['owners'] = []
        if state['variant'] == 'cycle': result['owners'][0]['parent'] = 'owner:' + result['owners'][0]['id']
        if state['variant'] == 'xss':
            result['owners'][0]['name'] = '<img src=x onerror="window.fixtureXSS=true">'
        return result

    async def backend(request):
        path = request.path.removeprefix('/demo')
        if path == '/__fixture__/state' and request.method == 'POST':
            options = await request.json(); state.update(options); state['version'] += 1
            return web.json_response({'ok': True})
        if path == '/api/login':
            # Synthetic fixture access only. Never a production credential.
            result = web.json_response({'ok': True}); result.set_cookie('fixture', 'demo', httponly=True, path='/demo/'); return result
        if path.startswith('/api/'):
            if request.cookies.get('fixture') != 'demo' or not state['auth']:
                return web.json_response({'error': 'Demo login required'}, status=401)
            if path == '/api/me': return web.json_response({'ok': True})
            if path == '/api/sessions':
                return web.json_response({'sessions': current_rows(), 'recent_cwds': [], 'new_session_defaults': {}, 'tmux_available': False})
            if path.endswith('/live'):
                # Finite SSE is sufficient to exercise real upstream event client/reconnect.
                return web.Response(text=': demo\n\n', content_type='text/event-stream')
            if '/messages/' in path:
                session_id = path.split('/')[3]
                row = next((r for r in current_rows() if r['session_id'] == session_id), rows[0])
                return web.json_response({'events': [], 'transcript_state': 'bound', 'thread_id': row['thread_id'],
                    'log_path': row['log_path'], 'live_cursor': None, 'history_cursor': None, 'has_older': False,
                    'busy': False, 'queue_len': 0})
            if path == '/api/logout':
                result = web.json_response({'ok': True}); result.del_cookie('fixture', path='/demo/'); return result
            return web.json_response({'ok': True, 'items': [], 'models': [], 'queue': []})
        if path in ('/', '/index.html', '/static/index.html'):
            html = (static_dir / 'index.html').read_text().replace('__CODOXEAR_ASSET_VERSION__', 'owner-demo').replace('__CODOXEAR_ATTACH_MAX_BYTES__', '10485760')
            html = html.replace('<title>Codoxear</title>', '<title>Codoxear · DEMO 合成数据</title>')
            return web.Response(text=html, content_type='text/html')
        # Serve the ACTUAL upstream service worker and assets unchanged, read-only.
        relative = path.removeprefix('/static/').lstrip('/')
        candidate = (static_dir / relative).resolve()
        if static_dir not in candidate.parents or not candidate.is_file(): return web.Response(status=404)
        return web.FileResponse(candidate, headers={'Content-Type': mimetypes.guess_type(candidate.name)[0] or 'application/octet-stream'})

    backend_app = web.Application(); backend_app.router.add_route('*', '/{path:.*}', backend)
    runner = web.AppRunner(backend_app, access_log=None); await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0); await site.start()
    backend_port = site._server.sockets[0].getsockname()[1]
    front_app = proxy.create_app(f'http://127.0.0.1:{backend_port}', '/demo', projector)
    front_app[proxy.CONFIG]['demo'] = True
    front_runner = web.AppRunner(front_app, access_log=None); await front_runner.setup()
    front_site = web.TCPSite(front_runner, '127.0.0.1', port); await front_site.start()
    chosen = front_site._server.sockets[0].getsockname()[1]
    print(f'DEMO_READY http://127.0.0.1:{chosen}/demo/ (synthetic API; real read-only upstream frontend)', flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await front_runner.cleanup(); await runner.cleanup()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-dir', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8875)
    args = parser.parse_args()
    try:
        asyncio.run(serve(args.static_dir.resolve(), args.port))
    except KeyboardInterrupt:
        pass
