#!/usr/bin/env python3
"""Optional same-origin streaming adapter; upstream and its package stay untouched."""
import argparse
import asyncio
from contextlib import suppress
from html.parser import HTMLParser
import importlib.util
import hashlib
import json
from pathlib import Path
import socket
import stat
import sys
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web
from multidict import CIMultiDict
from yarl import URL

from registry_projection import read_registry

HERE = Path(__file__).resolve().parent
NAMESPACE = '__owner_groups__'
HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
       'te', 'trailer', 'transfer-encoding', 'upgrade'}
MAX_HTML = 2 * 1024 * 1024
MAX_AUTH = 8 * 1024 * 1024
CONFIG = web.AppKey('config', dict)
CLIENT = web.AppKey('client', aiohttp.ClientSession)


def headers_without_hops(headers):
    blocked = HOP | {s.strip().lower() for s in headers.get('Connection', '').split(',')}
    return CIMultiDict((k, v) for k, v in headers.items() if k.lower() not in blocked)


class Shell(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = False
        self.bundle = False
        self.policies = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'div' and attrs.get('id') == 'root':
            self.root = True
        if tag == 'script' and 'dist/app.bundle.js' in attrs.get('src', ''):
            self.bundle = True
        if tag == 'meta' and attrs.get('http-equiv', '').lower() == 'content-security-policy':
            self.policies.append(attrs.get('content', ''))


def allows_injection(policy):
    directives = {}
    for item in policy.split(';'):
        fields = item.strip().split()
        if fields:
            directives[fields[0].lower()] = fields[1:]
    scripts = directives.get('script-src-elem', directives.get('script-src', directives.get('default-src')))
    styles = directives.get('style-src-elem', directives.get('style-src', directives.get('default-src')))
    # Do not weaken upstream CSP or invent nonces. Inline shadow styles need the existing allowance.
    return (scripts is None or "'self'" in scripts) and (styles is None or "'unsafe-inline'" in styles)


def asset():
    body = '\n;\n'.join((HERE / 'web' / path).read_text() for path in ('core.js', 'panel.js', 'bootstrap.js')).encode('utf-8')
    return body, hashlib.sha256(body).hexdigest()[:20]


def inject(body, headers, prefix):
    try:
        text = body.decode('utf-8')
        shell = Shell(); shell.feed(text)
        policies = shell.policies + headers.getall('Content-Security-Policy', [])
        if not (shell.root and shell.bundle and '</body>' in text and all(allows_injection(p) for p in policies)):
            return body, 'unsupported-html-or-csp'
        marker = f'<script defer src="{prefix}/{NAMESPACE}/app.js?v={asset()[1]}"></script>'
        return text.replace('</body>', marker + '\n</body>', 1).encode('utf-8'), 'injected'
    except (ValueError, UnicodeError):
        return body, 'unsupported-html-or-csp'


def json_response(data, status=200):
    return web.json_response(data, status=status, headers={
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
        'Cross-Origin-Resource-Policy': 'same-origin', 'Vary': 'Cookie, Origin'})


def same_origin(request):
    # Browser same-origin policy + upstream authenticated cookie is the private-data boundary.
    # No CORS, no page-supplied identity/path/root. Reject browser cross-site requests explicitly.
    if request.headers.get('Sec-Fetch-Site') == 'cross-site':
        return False
    origin = request.headers.get('Origin')
    if origin:
        scheme = request.headers.get('X-Forwarded-Proto', request.scheme)
        if scheme not in ('http', 'https') or origin != f'{scheme}://{request.host}':
            return False
    return True


async def limited_read(response, limit):
    chunks, size = [], 0
    async for chunk in response.content.iter_chunked(65536):
        size += len(chunk)
        if size > limit:
            raise ValueError('response too large')
        chunks.append(chunk)
    return b''.join(chunks)


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
    # Read-only POST bypasses upstream service-worker GET cache/fallback behavior.
    if request.method != 'POST':
        return json_response({'ok': False, 'error': 'Empty read-only POST required; GET snapshots are not fresh'}, 405)
    if request.content_length not in (None, 0) or await request.content.read(1):
        return json_response({'ok': False, 'error': 'Empty request body required'}, 400)
    if request.query_string or not same_origin(request):
        return json_response({'ok': False, 'error': 'Same-origin fixed request required'}, 403)
    if not request.headers.get('Cookie'):
        return json_response({'ok': False, 'error': 'Codoxear login required'}, 401)
    config = request.app[CONFIG]
    auth_headers = {'Cookie': request.headers['Cookie'], 'Host': request.host, 'Accept': 'application/json',
                    'Accept-Encoding': 'identity'}
    if 'X-Forwarded-Proto' in request.headers:
        auth_headers['X-Forwarded-Proto'] = request.headers['X-Forwarded-Proto']
    try:
        async with request.app[CLIENT].get(config['upstream'] + config['prefix'] + '/api/sessions',
                headers=auth_headers, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=10)) as response:
            if response.status in (401, 403):
                return json_response({'ok': False, 'error': 'Codoxear login required'}, 401)
            if response.status != 200 or response.content_type != 'application/json' or response.headers.get('Content-Encoding'):
                raise ValueError('unsupported authentication response')
            payload = json.loads(await limited_read(response, MAX_AUTH))
            if not isinstance(payload, dict) or not isinstance(payload.get('sessions'), list) or not all(
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
        data = await asyncio.to_thread(config['projector'])
        if not data['ok']:
            return json_response({'ok': False, 'error': data['error']}, 503)
        return json_response({'owners': data, 'sessions': {'sessions': sessions}, 'demo': config.get('demo', False)})
    except (aiohttp.ClientError, TimeoutError, ValueError, KeyError):
        return json_response({'ok': False, 'error': 'Upstream login verification unavailable or incompatible'}, 503)


async def feature(request):
    config = request.app[CONFIG]
    name = request.match_info['name']
    if name == 'snapshot':
        return await snapshot(request)
    if request.method not in ('GET', 'HEAD'):
        return web.Response(status=405)
    if name == 'status':
        return json_response({'adapter': 'quick-deploy owner-groups/v1',
            'html_compatibility': config['html_status'],
            'snapshot': 'Empty read-only POST; requires existing authenticated Codoxear cookie; never public.',
            'native_app': 'Preserved; unsupported HTML/CSP passes through unchanged.'})
    if name == 'app.js':
        return web.Response(body=asset()[0], content_type='application/javascript', headers={
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Cross-Origin-Resource-Policy': 'same-origin'})
    return web.Response(status=404)


async def proxy(request):
    config = request.app[CONFIG]
    if request.headers.get('Upgrade'):
        # Observed Codoxear uses HTTP + SSE, not WebSockets. Never silently break a new protocol.
        return web.Response(status=501, text='Owner-groups adapter does not support this upstream upgrade; use native entry.')
    headers = headers_without_hops(request.headers)
    entry = request.path in (config['prefix'] + '/', config['prefix'] + '/index.html', config['prefix'] + '/static/index.html')
    if entry:
        headers['Accept-Encoding'] = 'identity'
        for key in ('If-None-Match', 'If-Modified-Since', 'Range'):
            headers.popall(key, None)
    target = URL(config['upstream'] + request.raw_path, encoded=True)
    result = None
    try:
        async with request.app[CLIENT].request(request.method, target, headers=headers,
                data=request.content.iter_chunked(65536) if request.can_read_body else None,
                allow_redirects=False) as response:
            outgoing = headers_without_hops(response.headers)
            # Some upstream revisions may emit an absolute loopback redirect.
            location = outgoing.get('Location', '')
            if location == config['upstream'] or location.startswith(config['upstream'] + '/'):
                scheme = request.headers.get('X-Forwarded-Proto', request.scheme)
                if scheme not in ('http', 'https'): scheme = request.scheme
                outgoing['Location'] = f'{scheme}://{request.host}' + location[len(config['upstream']):]
            if entry and request.method == 'GET' and response.status == 200 and response.content_type == 'text/html':
                if response.headers.get('Content-Encoding') or (response.content_length or 0) > MAX_HTML:
                    config['html_status'] = 'unsupported-encoding-or-size'
                    outgoing['X-Owner-Groups'] = config['html_status']
                else:
                    # Only the bounded entry HTML is buffered. All uploads/API/downloads/SSE stream.
                    chunks, size = [], 0
                    async for chunk in response.content.iter_chunked(65536):
                        chunks.append(chunk); size += len(chunk)
                        if size > MAX_HTML:
                            config['html_status'] = 'unsupported-encoding-or-size'
                            outgoing['X-Owner-Groups'] = config['html_status']
                            result = web.StreamResponse(status=response.status, headers=outgoing)
                            await result.prepare(request)
                            for buffered in chunks:
                                await result.write(buffered)
                            async for remainder in response.content.iter_chunked(65536):
                                await result.write(remainder)
                            await result.write_eof(); return result
                    body, status = inject(b''.join(chunks), outgoing, config['prefix'])
                    config['html_status'] = status; outgoing['X-Owner-Groups'] = status
                    if status == 'injected':
                        for key in ('Content-Length', 'Content-Encoding', 'ETag', 'Last-Modified', 'Content-MD5', 'Digest'):
                            outgoing.popall(key, None)
                        outgoing['Cache-Control'] = 'no-store'
                    return web.Response(status=response.status, headers=outgoing, body=body)
            result = web.StreamResponse(status=response.status, headers=outgoing)
            await result.prepare(request)
            async for chunk in response.content.iter_chunked(65536):
                await result.write(chunk)
            await result.write_eof()
            return result
    except (aiohttp.ClientError, TimeoutError):
        if result is not None and result.prepared:
            result.force_close()
            return result
        return web.Response(status=502, text='Codoxear upstream unavailable. Owner-groups adapter did not modify it.')


def create_app(upstream, prefix='', projector=read_registry):
    parsed = urlsplit(upstream)
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', '::1') or not parsed.port or parsed.path not in ('', '/') or parsed.query or parsed.fragment or parsed.username:
        raise ValueError('upstream must be a fixed loopback HTTP origin')
    if prefix and (not prefix.startswith('/') or prefix.endswith('/') or any(c not in '/abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in prefix)):
        raise ValueError('unsupported URL prefix')
    app = web.Application(client_max_size=0, handler_args={'auto_decompress': False})
    app[CONFIG] = {'upstream': upstream.rstrip('/'), 'prefix': prefix, 'projector': projector, 'html_status': 'not-observed'}
    async def client_context(app):
        async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(), auto_decompress=False,
                trust_env=False, timeout=aiohttp.ClientTimeout(total=None, sock_connect=10),
                skip_auto_headers={'User-Agent', 'Accept-Encoding', 'Content-Type'}) as client:
            app[CLIENT] = client
            yield
    app.cleanup_ctx.append(client_context)
    app.router.add_route('*', prefix + '/' + NAMESPACE + '/{name}', feature)
    app.router.add_route('*', '/{tail:.*}', proxy)
    return app


def wrapper():
    spec = importlib.util.spec_from_file_location('codoxear_wrapper', HERE.parent / '.internal/launch.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def managed_app():
    source = wrapper(); source.validate_env(); env = source.runtime_env()
    host = env.get('CODEX_WEB_HOST', '::').strip() or '::'
    port = int(env.get('CODEX_WEB_PORT', '8743').strip() or '8743')
    prefix = source.access_path(env).rstrip('/')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); backend_port = sock.getsockname()[1]
    if backend_port == port:
        raise RuntimeError('Private backend port collided with front port; retry launch.')
    env.update(CODEX_WEB_HOST='127.0.0.1', CODEX_WEB_PORT=str(backend_port))
    app = create_app(f'http://127.0.0.1:{backend_port}', prefix)
    async def backend_context(app):
        process = await asyncio.create_subprocess_exec(str(source.VENV_BIN / 'codoxear-server'),
                    env=env, cwd=source.HELPER_DIR)
        async def watch():
            code = await process.wait()
            print(f'Owner-groups: private upstream exited ({code}); stop this entry and inspect startup.', file=sys.stderr)
            # Stop the front too: do not leave a deceptively healthy proxy after backend failure.
            import signal
            import os
            os.kill(os.getpid(), signal.SIGTERM)
        task = asyncio.create_task(watch())
        print('Owner-groups enabled: same configured front URL; unchanged upstream on private loopback. Ctrl-C stops both.', flush=True)
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 8)
                except TimeoutError:
                    process.kill(); await process.wait()
    app.cleanup_ctx.append(backend_context)
    return app, host, port


def front_socket(host, port):
    """Preserve upstream's IPv6 wildcard dual-stack behavior (asyncio defaults differ)."""
    sock = socket.socket(socket.AF_INET6 if ':' in host else socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if sock.family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        sock.bind((host, port)); sock.listen(128); sock.setblocking(False)
        return sock
    except BaseException:
        sock.close(); raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--managed-upstream', action='store_true', help='Own unchanged backend; retain configured front URL')
    parser.add_argument('--upstream', help='Existing fixed loopback HTTP origin (standalone isolated entry)')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8874)
    parser.add_argument('--prefix', default='')
    args = parser.parse_args()
    if args.managed_upstream:
        if args.upstream or args.prefix or args.host != '127.0.0.1' or args.port != 8874:
            parser.error('managed mode takes front topology only from existing wrapper config')
        app, host, port = managed_app()
    else:
        if not args.upstream:
            parser.error('--upstream or --managed-upstream required')
        app, host, port = create_app(args.upstream, args.prefix), args.host, args.port
    # Disable access logs: credentials, request URLs and transcript paths do not belong in adapter logs.
    # Bind front before starting any backend: an occupied live port fails without touching it.
    with front_socket(host, port) as listener:
        web.run_app(app, sock=listener, access_log=None, print=None, handler_cancellation=True)


if __name__ == '__main__':
    main()
