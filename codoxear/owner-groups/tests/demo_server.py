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
import tempfile
import time

from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import proxy
import fixture


async def serve(static_dir, port):
    data = fixture.demo()
    state = {'variant': 'normal', 'auth': True, 'version': 0, 'activity': '', 'add': False}
    actions = []
    # Additional SYNTHETIC native rows for approved two-frame visual/interaction tests.
    for oid, parent, title in [('task-web', 'owner:other', '发布 web 管理'), ('task-office', 'owner:other', 'office 采集任务发布')]:
        data['owners']['owners'].append({**copy.deepcopy(data['owners']['owners'][0]),
            'id': oid, 'parent': parent, 'name': title, 'pi_session_id': 'pi-synthetic-' + oid,
            'registration_order': len(data['owners']['owners'])})
    for oid in ('ops', 'other', 'task-web', 'task-office'):
        data['sessions']['sessions'].append({'session_id': 'demo-' + oid, 'thread_id': 'pi-synthetic-' + oid,
            'agent_backend': 'pi', 'has_bound_log': True})
    titles = {'ops': 'Dexathlon 场景评测', 'other': 'Dexathlon_task-proposals',
        'office-opaque-123': 'office 场景评测', 'legacy': '前任 office · 已交接', 'web': 'kitchen 场景评测',
        'qa': 'Office 验收', 'task-web': '发布 web 管理', 'task-office': 'office 采集任务发布',
        'unrelated': 'quick-deploy', 'alias-only': 'stage-harness-observer'}
    rows = []
    logs = tempfile.TemporaryDirectory(prefix='owner-demo-public-logs-')
    for index, item in enumerate(data['sessions']['sessions']):
        log = Path(logs.name) / (str(index) + '.jsonl'); log.touch()
        public = {k: v for k, v in item.items() if k != 'has_bound_log'}
        rows.append({**public, 'log_path': str(log),
            'alias': titles.get(item['session_id'].removeprefix('demo-'), '未绑定 · ' + item['session_id']), 'cwd': '/example/Dexathlon',
            'model': 'gpt-6-astra',
            'owned': False, 'transport': 'socket', 'busy': False, 'lost': False,
            'start_ts': time.time() - index * 60, 'updated_ts': time.time() - index * 60, 'transcript_state': 'bound'})
    original_rows = copy.deepcopy(rows)

    def current_rows():
        result = copy.deepcopy(rows)
        if state['variant'] == 'rebind':
            result[0]['thread_id'] = 'pi-synthetic-switched'
        if state['variant'] == 'duplicate-thread':
            result.append({**result[0], 'session_id': 'demo-second-thread'})
        if state['activity']:
            target = next(r for r in result if r['session_id'] == state['activity'])
            target['updated_ts'] = time.time() + state['version']
            target['busy'] = True; target['unread_count'] = 3
        if state['add']:
            result.append({**result[0], 'session_id': 'demo-new', 'thread_id': 'pi-synthetic-new', 'alias': '新成员', 'updated_ts': time.time() + 100})
        if state['variant'] == 'root-missing': result = [r for r in result if r['session_id'] != 'demo-ops']
        if state['variant'] == 'unresolved':
            result.append({**result[0], 'session_id': 'demo-orphan', 'thread_id': 'pi-synthetic-orphan', 'alias': '上级待确认方向'})
        if state.get('new_group'):
            result.append({**result[0], 'session_id': 'demo-third', 'thread_id': 'pi-synthetic-third', 'alias': '新团队'})
        if state['variant'] == 'empty-root':
            absent = {'demo-ops', 'demo-office-opaque-123', 'demo-legacy', 'demo-web', 'demo-qa'}
            result = [r for r in result if r['session_id'] not in absent]
        if state['variant'] == 'all-now-bound':
            result = [r for r in result if r['session_id'] in {
                'demo-ops', 'demo-office-opaque-123', 'demo-legacy', 'demo-web', 'demo-qa',
                'demo-other', 'demo-task-web', 'demo-task-office'}]
        if state.get('sections') == 'mixed':
            for row in result:
                row['blocked'] = row['session_id'] in {'demo-web', 'demo-unrelated'}
                row['snoozed'] = row['session_id'] in {'demo-task-office', 'demo-alias-only'}
        if state['variant'] == 'no-sessions': result = []
        if result: result[0]['alias'] += f" v{state['version']}"
        return result

    def projector():
        if state['variant'] == 'bridge-down':
            return {'schema': 'codoxear-owner-groups/v1', 'ok': False, 'error': 'Synthetic unavailable', 'owners': []}
        result = copy.deepcopy(data['owners'])
        if state['variant'] == 'empty': result['owners'] = []
        if state['variant'] == 'cycle': result['owners'][0]['parent'] = 'owner:' + result['owners'][0]['id']
        if state['variant'] == 'xss':
            result['owners'][0]['name'] = '<img src=x onerror="window.fixtureXSS=true">'
        if state['variant'] == 'handover':
            next(o for o in result['owners'] if o['id'] == 'office')['parent'] = 'owner:other'
        if state.get('new_group'):
            result['owners'].append({**copy.deepcopy(result['owners'][0]), 'id': 'third', 'name': '新团队',
                'parent': 'user', 'pi_session_id': 'pi-synthetic-third', 'registration_order': 100})
        if state['add']:
            result['owners'].append({**copy.deepcopy(result['owners'][0]), 'id': 'new', 'name': '新成员',
                'parent': 'owner:other', 'pi_session_id': 'pi-synthetic-new', 'registration_order': 99})
        return result

    async def backend(request):
        path = request.path.removeprefix('/demo')
        if path == '/__fixture__/state' and request.method == 'POST':
            options = await request.json()
            if options.pop('reset', False):
                rows[:] = copy.deepcopy(original_rows); actions.clear()
                state.update(variant='normal', auth=True, version=0, activity='', add=False, new_group=False, sections='')
            state.update(options); state['version'] += 1
            return web.json_response({'ok': True, 'actions': actions})
        if path == '/api/login':
            # Synthetic fixture access only. Never a production credential.
            result = web.json_response({'ok': True}); result.set_cookie('fixture', 'demo', httponly=True, path='/demo/'); return result
        if path.startswith('/api/'):
            if request.cookies.get('fixture') != 'demo' or not state['auth']:
                return web.json_response({'error': 'Demo login required'}, status=401)
            if request.method == 'POST' and path != '/api/logout':
                body = await request.json(); actions.append({'path': path, 'body': body})
                if path == '/api/sessions':
                    if not any(r['session_id'] == 'demo-created' for r in rows):
                        rows.append({**rows[0], 'session_id': 'demo-created', 'thread_id': '', 'alias': '原生复制 · 未绑定', 'broker_pid': 42001})
                    return web.json_response({'broker_pid': 42001})
                if path.endswith('/edit'):
                    target = next((r for r in rows if r['session_id'] == path.split('/')[3]), None)
                    if target: target['alias'] = body['name']
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
        await front_runner.cleanup(); await runner.cleanup(); logs.cleanup()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-dir', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8875)
    args = parser.parse_args()
    try:
        asyncio.run(serve(args.static_dir.resolve(), args.port))
    except KeyboardInterrupt:
        pass
