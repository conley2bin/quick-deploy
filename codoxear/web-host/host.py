#!/usr/bin/env python3
"""Shared same-origin extension host; upstream and its package stay untouched."""
import argparse
import asyncio
from contextlib import suppress
from html.parser import HTMLParser
from types import MappingProxyType
import re
import importlib.util
import hashlib
import json
from pathlib import Path
import socket
import sys
from urllib.parse import unquote, urlsplit

import aiohttp
from aiohttp import web
from multidict import CIMultiDict
from yarl import URL

HERE = Path(__file__).resolve().parent
HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
       'te', 'trailer', 'transfer-encoding', 'upgrade'}
MAX_HTML = 2 * 1024 * 1024
CONFIG = web.AppKey('config', dict)
CLIENT = web.AppKey('client', aiohttp.ClientSession)


def load_feature(directory):
    """Load a named sibling as a package, so feature-local imports stay local."""
    name = 'codoxear_' + directory.replace('-', '_')
    module_name = name + '.feature'
    if module_name not in sys.modules:
        package = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader=None, is_package=True))
        package.__path__ = [str(HERE.parent / directory)]
        sys.modules[name] = package
        spec = importlib.util.spec_from_file_location(module_name, HERE.parent / directory / 'feature.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(module_name, None)
            raise
    return sys.modules[module_name]


def default_features():
    # Explicit composition, never discovery or silent omission of a missing feature.
    return (load_feature('owner-groups'), load_feature('session-handoff'))


class HostContext:
    """One fixed upstream, caller-scoped auth, and startup-frozen feature assets."""
    def __init__(self, app):
        self._app = app
        self.prefix = app[CONFIG]['prefix']
        self.scripts = {}
        self._frozen = False

    @property
    def html_status(self):
        return self._app[CONFIG]['html_status']

    def same_origin(self, request):
        return same_origin(request)

    def json_response(self, data, status=200):
        return json_response(data, status)

    def add_script(self, namespace, body):
        if self._frozen:
            raise RuntimeError('Feature assets are frozen; restart to install changes')
        if not isinstance(namespace, str) or not re.fullmatch(r'__[a-z][a-z0-9_]*__', namespace):
            raise ValueError('Unsupported feature namespace')
        if namespace in self.scripts:
            raise ValueError('Duplicate feature namespace')
        if not isinstance(body, bytes):
            raise TypeError('Feature script must be bytes')
        digest = hashlib.sha256(body).hexdigest()[:20]
        self.scripts[namespace] = (body, digest)

        async def script(request):
            version = request.query.get('v')
            if (version is not None and version != digest) or set(request.query) - {'v'}:
                raise web.HTTPNotFound()
            return web.Response(body=body, content_type='application/javascript', headers={
                'Cache-Control': 'public, max-age=31536000, immutable' if version else 'no-store',
                'X-Content-Type-Options': 'nosniff', 'Cross-Origin-Resource-Policy': 'same-origin'})
        self._app.router.add_get(self.prefix + '/' + namespace + '/app.js', script)

    def freeze(self):
        self.scripts = MappingProxyType(dict(self.scripts))
        self._frozen = True

    def compatibility_headers(self, headers):
        headers['X-Codoxear-Extensions'] = self.html_status
        if '__owner_groups__' in self.scripts:
            headers['X-Owner-Groups'] = self.html_status

    async def upstream_json(self, method, path, *, cookie, body=None, timeout=30.0, limit=8*1024*1024):
        # A feature supplies API paths, never a browser-selected URL or redirect target.
        if not isinstance(path, str) or not path.startswith('/api/') or any(ord(c) < 32 or c == '\\' for c in path):
            raise ValueError('Expected a fixed relative API path')
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or parsed.fragment or any(
                part in ('.', '..') for part in unquote(parsed.path).split('/')):
            raise ValueError('Expected a fixed relative API path')
        headers = {'Cookie': cookie, 'Accept': 'application/json', 'Accept-Encoding': 'identity'}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        config = self._app[CONFIG]
        target = URL(config['upstream'] + self.prefix + path, encoded=True)
        async with self._app[CLIENT].request(method, target, headers=headers, json=body,
                allow_redirects=False, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
            if response.content_type != 'application/json' or response.headers.get('Content-Encoding'):
                raise ValueError('Unsupported upstream JSON response')
            payload = json.loads(await limited_read(response, limit))
            return response.status, payload


HOST = web.AppKey('host', HostContext)


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
            # Browsers honor the first instance of each directive.
            directives.setdefault(fields[0].lower(), fields[1:])
    scripts = directives.get('script-src-elem', directives.get('script-src', directives.get('default-src')))
    styles = directives.get('style-src-elem', directives.get('style-src', directives.get('default-src')))
    # Do not weaken upstream CSP or invent nonces. Inline shadow styles need the existing allowance.
    scripts_ok = scripts is None or ("'self'" in scripts and "'strict-dynamic'" not in scripts)
    # A nonce/hash disables unsafe-inline in modern CSP; our styles carry neither.
    styles_ok = styles is None or ("'unsafe-inline'" in styles and not any(
        token.startswith(("'nonce-", "'sha256-", "'sha384-", "'sha512-")) for token in styles))
    sandbox = directives.get('sandbox')
    return scripts_ok and styles_ok and (sandbox is None or 'allow-scripts' in sandbox)


def inject(body, headers, prefix, scripts):
    try:
        text = body.decode('utf-8')
        shell = Shell(); shell.feed(text)
        policies = shell.policies + headers.getall('Content-Security-Policy', [])
        if not (shell.root and shell.bundle and '</body>' in text and all(allows_injection(p) for p in policies)):
            return body, 'unsupported-html-or-csp'
        marker = "\n".join(f'<script defer src="{prefix}/{namespace}/app.js?v={digest}"></script>'
                           for namespace, (_, digest) in scripts.items())
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


async def proxy(request):
    config = request.app[CONFIG]
    host = request.app[HOST]
    if request.headers.get('Upgrade'):
        # Observed Codoxear uses HTTP + SSE, not WebSockets. Never silently break a new protocol.
        return web.Response(status=501, text='Codoxear web host does not support this upstream upgrade; use native entry.')
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
                    host.compatibility_headers(outgoing)
                else:
                    # Only the bounded entry HTML is buffered. All uploads/API/downloads/SSE stream.
                    chunks, size = [], 0
                    async for chunk in response.content.iter_chunked(65536):
                        chunks.append(chunk); size += len(chunk)
                        if size > MAX_HTML:
                            config['html_status'] = 'unsupported-encoding-or-size'
                            host.compatibility_headers(outgoing)
                            result = web.StreamResponse(status=response.status, headers=outgoing)
                            await result.prepare(request)
                            for buffered in chunks:
                                await result.write(buffered)
                            async for remainder in response.content.iter_chunked(65536):
                                await result.write(remainder)
                            await result.write_eof(); return result
                    body, status = inject(b''.join(chunks), outgoing, config['prefix'], host.scripts)
                    config['html_status'] = status; host.compatibility_headers(outgoing)
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
        return web.Response(status=502, text='Codoxear upstream unavailable. Codoxear web host did not modify it.')


def create_app(upstream, prefix='', *, features=None):
    parsed = urlsplit(upstream)
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', '::1') or not parsed.port or parsed.path not in ('', '/') or parsed.query or parsed.fragment or parsed.username is not None:
        raise ValueError('upstream must be a fixed loopback HTTP origin')
    if prefix and (not prefix.startswith('/') or prefix.endswith('/') or '//' in prefix or any(c not in '/abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in prefix)):
        raise ValueError('unsupported URL prefix')
    app = web.Application(client_max_size=0, handler_args={'auto_decompress': False})
    app[CONFIG] = {'upstream': upstream.rstrip('/'), 'prefix': prefix, 'html_status': 'not-observed'}
    async def client_context(app):
        async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(), auto_decompress=False,
                trust_env=False, timeout=aiohttp.ClientTimeout(total=None, sock_connect=10),
                skip_auto_headers={'User-Agent', 'Accept-Encoding', 'Content-Type'}) as client:
            app[CLIENT] = client
            yield
    app.cleanup_ctx.append(client_context)
    host = HostContext(app)
    app[HOST] = host
    for feature in default_features() if features is None else features:
        feature.install(app, host)
    host.freeze()
    app.router.add_route('*', '/{tail:.*}', proxy)
    return app


def wrapper():
    spec = importlib.util.spec_from_file_location('codoxear_wrapper', HERE.parent / '.internal/launch.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def managed_app(*, features=None, source=None):
    source = source if source is not None else wrapper(); source.validate_env(); env = source.runtime_env()
    host = env.get('CODEX_WEB_HOST', '::').strip() or '::'
    port = int(env.get('CODEX_WEB_PORT', '8743').strip() or '8743')
    prefix = source.access_path(env).rstrip('/')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); backend_port = sock.getsockname()[1]
    if backend_port == port:
        raise RuntimeError('Private backend port collided with front port; retry launch.')
    env.update(CODEX_WEB_HOST='127.0.0.1', CODEX_WEB_PORT=str(backend_port))
    app = create_app(f'http://127.0.0.1:{backend_port}', prefix, features=features)
    async def backend_context(app):
        process = await asyncio.create_subprocess_exec(str(source.VENV_BIN / 'codoxear-server'),
                    env=env, cwd=source.HELPER_DIR)
        async def watch():
            code = await process.wait()
            print(f'Codoxear web host: private upstream exited ({code}); stop this entry and inspect startup.', file=sys.stderr)
            # Stop the front too: do not leave a deceptively healthy proxy after backend failure.
            import signal
            import os
            os.kill(os.getpid(), signal.SIGTERM)
        task = asyncio.create_task(watch())
        print('Codoxear extensions enabled: same configured front URL; unchanged upstream on private loopback. Ctrl-C stops both.', flush=True)
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
