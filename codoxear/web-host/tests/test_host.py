"""Deterministic HTTP tests: synthetic features and loopback services only."""
import asyncio
import gzip
import hashlib
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
from multidict import CIMultiDict

HOST_PATH = Path(__file__).resolve().parents[1] / 'host.py'
if 'codoxear_web_host' not in sys.modules:
    spec = importlib.util.spec_from_file_location('codoxear_web_host', HOST_PATH)
    host = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = host
    spec.loader.exec_module(host)
else:
    host = sys.modules['codoxear_web_host']

HTML = b'<html><body><div id="root"></div><script src="/phone/dist/app.bundle.js"></script></body></html>'


class FakeFeature:
    def __init__(self, namespace):
        self.namespace = namespace
        self.body = ('window.' + namespace + ' = document.currentScript.src;').encode()
        self.events = []

    def install(self, app, context):
        self.host = context
        context.add_script(self.namespace, self.body)
        async def status(request):
            return context.json_response({'html': context.html_status})
        async def lifecycle(app):
            self.events.append('start')
            yield
            self.events.append('stop')
        app.router.add_post(context.prefix + '/' + self.namespace + '/status', status)
        app.cleanup_ctx.append(lifecycle)


class HostTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.entry = HTML
        self.entry_headers = {'ETag': 'native'}
        self.release = asyncio.Event()
        self.first_upload = asyncio.Event()
        self.features = (FakeFeature('__one__'), FakeFeature('__two__'))
        async def upstream(request):
            self.calls.append((request.method, request.raw_path, request.headers.get('Cookie')))
            if request.path == '/phone/':
                return web.Response(body=self.entry, content_type='text/html', headers=self.entry_headers)
            if request.path == '/phone/api/redirect':
                return web.json_response({'redirect': 'not followed'}, status=307,
                                         headers={'Location': '/phone/api/escaped'})
            if request.path == '/phone/api/redirect-html':
                raise web.HTTPFound('/phone/api/escaped')
            if request.path == '/phone/api/large':
                return web.json_response({'large': 'x' * 1000})
            if request.path == '/phone/api/encoded':
                return web.Response(body=gzip.compress(b'{}'), content_type='application/json',
                                    headers={'Content-Encoding': 'gzip'})
            if request.path == '/phone/api/timeout':
                await self.release.wait()
                return web.json_response({})
            if request.path == '/phone/api/malformed':
                return web.Response(body=b'{bad', content_type='application/json')
            if request.path == '/phone/api/events':
                result = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
                await result.prepare(request)
                await result.write(b'data: first\n\n')
                await self.release.wait()
                await result.write(b'data: second\n\n')
                return result
            if request.path == '/phone/api/upload':
                first = await request.content.readexactly(5)
                self.first_upload.set()
                return web.Response(body=first + await request.read())
            if request.path == '/phone/api/echo':
                result = web.json_response({'cookie': request.headers.get('Cookie'),
                    'body': await request.json() if request.can_read_body else None,
                    'headers': {k: v for k, v in request.headers.items()}}, status=201)
                result.headers.add('Set-Cookie', 'injected=must-not-be-retained')
                return result
            return web.json_response({'native': request.raw_path}, status=404)
        app = web.Application(); app.router.add_route('*', '/{path:.*}', upstream)
        self.upstream = TestServer(app)
        await self.upstream.start_server()
        self.app = host.create_app(str(self.upstream.make_url('')).rstrip('/'), '/phone', features=self.features)
        self.front = TestServer(self.app)
        await self.front.start_server()
        self.context = self.app[host.HOST]
        self.client = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar(), auto_decompress=False)

    async def asyncTearDown(self):
        self.release.set()
        await self.client.close()
        await self.front.close()
        await self.upstream.close()

    async def test_separate_defer_scripts_frozen_bytes_hashes_and_lifecycle(self):
        original = [feature.body for feature in self.features]
        for feature in self.features:
            feature.body = b'changed after installation'
        async with self.client.get(self.front.make_url('/phone/')) as response:
            text = await response.text()
            self.assertEqual(response.headers['X-Codoxear-Extensions'], 'injected')
            self.assertNotIn('ETag', response.headers)
        self.assertEqual(text.count('<script defer src='), 2)
        for feature, body in zip(self.features, original):
            self.assertEqual(feature.events, ['start'])
            digest = hashlib.sha256(body).hexdigest()[:20]
            asset_path = '/phone/' + feature.namespace + '/app.js?v=' + digest
            self.assertIn('<script defer src="' + asset_path + '"></script>', text)
            async with self.client.get(self.front.make_url(asset_path)) as response:
                self.assertEqual(await response.read(), body)
                self.assertIn(b'document.currentScript.src', body)
                self.assertIn('immutable', response.headers['Cache-Control'])
            async with self.client.get(self.front.make_url(asset_path + 'wrong')) as response:
                self.assertEqual(response.status, 404)
            async with self.client.post(self.front.make_url('/phone/' + feature.namespace + '/status')) as response:
                self.assertEqual(await response.json(), {'html': 'injected'})
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                self.assertEqual(response.headers['Cross-Origin-Resource-Policy'], 'same-origin')
                self.assertNotIn('Access-Control-Allow-Origin', response.headers)
        with self.assertRaises(RuntimeError):
            self.context.add_script('__late__', b'late')
        with self.assertRaises(TypeError):
            self.context.scripts['__one__'] = (b'changed', 'bad')
        self.assertFalse(any('/__one__/' in path or '/__two__/' in path for _, path, _ in self.calls))
        await self.front.close()
        for feature in self.features:
            self.assertEqual(feature.events, ['start', 'stop'])
        self.assertTrue(self.app[host.CLIENT].closed)

    async def test_json_transport_fixed_prefix_caller_cookie_status_body_and_no_cookie_jar(self):
        async def call(cookie):
            return await self.context.upstream_json('POST', '/api/echo', cookie=cookie, body={'hello': cookie})
        results = await asyncio.gather(call('caller=a'), call('caller=b'))
        for cookie, (status, payload) in zip(('caller=a', 'caller=b'), results):
            self.assertEqual(status, 201)
            self.assertEqual(payload['cookie'], cookie)
            self.assertEqual(payload['body'], {'hello': cookie})
            self.assertEqual(payload['headers']['Accept-Encoding'], 'identity')
        status, payload = await self.context.upstream_json('GET', '/api/echo', cookie='')
        self.assertEqual(payload['cookie'], '')
        self.assertEqual(len(self.app[host.CLIENT].cookie_jar), 0)
        self.assertTrue(all(path == '/phone/api/echo' for _, path, _ in self.calls))
        status, payload = await self.context.upstream_json('GET', '/api/missing', cookie='caller=c')
        self.assertEqual((status, payload), (404, {'native': '/phone/api/missing'}))
        opaque = '/api/sessions/pi%2Fopaque%3Akey/send?value=a%2Fb'
        status, payload = await self.context.upstream_json('POST', opaque, cookie='', body={'text': 'synthetic'})
        self.assertEqual(payload['native'], '/phone' + opaque)

    async def test_json_transport_rejects_targets_redirects_and_invalid_responses(self):
        for path in ('https://evil.invalid/api/x', '//127.0.0.1/api/x', '/elsewhere',
                     '/api/../escape', '/api/%2e%2e/escape', '/api/ok#fragment', '/api/x\\evil', '/api/x\r\n'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                await self.context.upstream_json('GET', path, cookie='private')
        self.assertEqual(self.calls, [])
        status, payload = await self.context.upstream_json('GET', '/api/redirect', cookie='private')
        self.assertEqual(status, 307)
        self.assertEqual(payload, {'redirect': 'not followed'})
        for path in ('/api/redirect-html', '/api/encoded', '/api/malformed'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                await self.context.upstream_json('GET', path, cookie='private')
        with self.assertRaises(ValueError):
            await self.context.upstream_json('GET', '/api/large', cookie='', limit=100)
        with self.assertRaises(TimeoutError):
            await self.context.upstream_json('GET', '/api/timeout', cookie='', timeout=0.01)
        self.assertFalse(any(path == '/phone/api/escaped' for _, path, _ in self.calls))
        self.release.set()
        await self.upstream.close()
        with self.assertRaises(aiohttp.ClientError):
            await self.context.upstream_json('GET', '/api/echo', cookie='private')

    async def test_csp_unknown_html_and_oversize_pass_through_unchanged(self):
        for body, headers in ((b'<html><body>unknown</body></html>', {'ETag': 'native'}),
                              (HTML, {'Content-Security-Policy': "script-src 'none'", 'ETag': 'native'}),
                              (HTML.replace(b'<body>', b'<body><meta http-equiv="Content-Security-Policy" content="style-src \'self\'">'), {'ETag': 'native'})):
            self.entry, self.entry_headers = body, headers
            async with self.client.get(self.front.make_url('/phone/')) as response:
                self.assertEqual(await response.read(), body)
                self.assertEqual(response.headers['ETag'], 'native')
                self.assertEqual(response.headers['X-Codoxear-Extensions'], 'unsupported-html-or-csp')
        self.entry, self.entry_headers = HTML, {'ETag': 'native'}
        with patch.object(host, 'MAX_HTML', 10):
            async with self.client.get(self.front.make_url('/phone/')) as response:
                self.assertEqual(await response.read(), HTML)
                self.assertEqual(response.headers['X-Codoxear-Extensions'], 'unsupported-encoding-or-size')

    async def test_sse_and_upload_forward_before_input_completion(self):
        async with self.client.get(self.front.make_url('/phone/api/events')) as response:
            self.assertEqual(await asyncio.wait_for(response.content.readuntil(b'\n\n'), 1), b'data: first\n\n')
            self.assertFalse(self.release.is_set())
            self.release.set()
            self.assertEqual(await response.content.readuntil(b'\n\n'), b'data: second\n\n')
        async def chunks():
            yield b'first'
            await asyncio.wait_for(self.first_upload.wait(), 1)
            yield b'second'
        async with self.client.post(self.front.make_url('/phone/api/upload'), data=chunks()) as response:
            self.assertEqual(await response.read(), b'firstsecond')


class ConstructionTests(unittest.TestCase):
    def test_explicit_defaults_and_missing_feature_is_not_omitted(self):
        fake = SimpleNamespace(install=lambda app, context: None)
        with patch.object(host, 'load_feature', return_value=fake) as load:
            host.create_app('http://127.0.0.1:1234')
            self.assertEqual([call.args for call in load.call_args_list], [('session-handoff',)])
        with patch.object(host, 'load_feature', side_effect=FileNotFoundError('missing handoff')):
            with self.assertRaises(FileNotFoundError):
                host.create_app('http://127.0.0.1:1234')

    def test_loopback_prefix_and_script_namespace_validation(self):
        for origin in ('http://evil.invalid:1234', 'https://127.0.0.1:1234', 'http://127.0.0.1:1234/path',
                       'http://user@127.0.0.1:1234', 'http://:password@127.0.0.1:1234',
                       'http://127.0.0.1', 'http://127.0.0.1:1234?x=1'):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                host.create_app(origin, features=())
        host.create_app('http://[::1]:1234', '/phone', features=())
        for prefix in ('/x/../y', '//evil', '/bad/', 'no-leading-slash'):
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                host.create_app('http://127.0.0.1:1234', prefix, features=())
        app = web.Application(); app[host.CONFIG] = {'prefix': ''}
        context = host.HostContext(app)
        for namespace in ('../bad', 'bad"', '__bad__/x'):
            with self.assertRaises(ValueError): context.add_script(namespace, b'')
        context.add_script('__good__', b'')
        with self.assertRaises(ValueError): context.add_script('__good__', b'')
        with self.assertRaises(TypeError): context.add_script('__other__', 'not bytes')

    def test_csp_all_policies_must_allow_both_script_and_style(self):
        headers = CIMultiDict([('Content-Security-Policy', "script-src 'self'; style-src 'unsafe-inline'"),
                               ('Content-Security-Policy', "script-src 'none'")])
        self.assertEqual(host.inject(HTML, headers, '', {'__one__': (b'', 'hash')}),
                         (HTML, 'unsupported-html-or-csp'))
        for policy in ("script-src 'none'; script-src 'self'", "script-src 'self' 'strict-dynamic' 'nonce-abc'",
                       "style-src 'unsafe-inline' 'nonce-abc'", "style-src 'unsafe-inline' 'sha256-abc'", 'sandbox'):
            with self.subTest(policy=policy):
                self.assertFalse(host.allows_injection(policy))


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_unexpected_private_backend_exit_stops_front_without_signalling_other_processes(self):
        import os
        import signal
        exit_code = asyncio.get_running_loop().create_future()
        signalled = asyncio.Event()
        class Child:
            returncode = None
            async def wait(self):
                self.returncode = await asyncio.shield(exit_code)
                return self.returncode
            def terminate(self):
                raise AssertionError('Already exited child must not be terminated')
        child = Child()
        async def spawn(*args, **kwargs):
            return child
        signals = []
        def kill(pid, sig):
            signals.append((pid, sig)); signalled.set()
        source = SimpleNamespace(validate_env=lambda: None,
            runtime_env=lambda: {'CODEX_WEB_HOST': '127.0.0.1', 'CODEX_WEB_PORT': '8743'},
            access_path=lambda env: '/', VENV_BIN=Path('/synthetic/bin'), HELPER_DIR=Path('/synthetic'))
        with patch.object(host.asyncio, 'create_subprocess_exec', spawn), patch.object(os, 'kill', kill):
            app, _, _ = host.managed_app(features=(), source=source)
            context = app.cleanup_ctx[-1](app)
            await context.__anext__()
            exit_code.set_result(17)
            await asyncio.wait_for(signalled.wait(), 1)
            self.assertEqual(signals, [(os.getpid(), signal.SIGTERM)])
            with self.assertRaises(StopAsyncIteration):
                await context.__anext__()

    async def test_feature_install_failure_never_spawns_private_backend(self):
        from unittest.mock import AsyncMock
        def fail(app, context):
            raise FileNotFoundError('Missing required feature asset')
        source = SimpleNamespace(validate_env=lambda: None,
            runtime_env=lambda: {}, access_path=lambda env: '/',
            VENV_BIN=Path('/synthetic/bin'), HELPER_DIR=Path('/synthetic'))
        with patch.object(host.asyncio, 'create_subprocess_exec', new_callable=AsyncMock) as spawn:
            with self.assertRaises(FileNotFoundError):
                host.managed_app(features=(SimpleNamespace(install=fail),), source=source)
            spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
