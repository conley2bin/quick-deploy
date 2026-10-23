"""Isolated HTTP auth/streaming tests; no real cookies, paths or assignments."""
import asyncio
import gzip
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from aiohttp import web, ClientSession, DummyCookieJar, ClientTimeout
from aiohttp.test_utils import TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import proxy

HTML = b'''<!doctype html><html><head><title>Codoxear</title>
<meta http-equiv="Content-Security-Policy" content="script-src 'self'; style-src 'self' 'unsafe-inline'">
</head><body><div id="root"></div><script type="module" src="/demo/dist/app.bundle.js?v=abc"></script></body></html>'''
EMPTY = {'schema': 'codoxear-owner-groups/v1', 'ok': True, 'revision': 0, 'owners': []}


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.auth_mode = 'normal'; self.cookies_seen = []; self.project_calls = 0; self.session_rows = []
        self.release_sse = asyncio.Event()
        async def upstream(request):
            path = request.path
            if path == '/demo/api/sessions':
                self.cookies_seen.append(request.headers.get('Cookie'))
                if self.auth_mode == 'redirect':
                    raise web.HTTPFound('/demo/')
                if self.auth_mode == 'html':
                    return web.Response(text='login', content_type='text/html')
                if request.headers.get('Cookie') != 'synthetic=valid':
                    return web.json_response({'error': 'login'}, status=401)
                return web.json_response({'sessions': self.session_rows, 'private_extra': 'never exported'})
            if path == '/demo/':
                return web.Response(body=HTML, content_type='text/html', headers={'ETag': 'upstream-tag'})
            if path == '/demo/unknown':
                return web.Response(body=b'unchanged', headers={'ETag': 'keep-me'})
            if path == '/demo/cookies':
                result = web.Response(text='ok'); result.headers.add('Set-Cookie', 'a=1'); result.headers.add('Set-Cookie', 'b=2')
                return result
            if path == '/demo/redirect':
                raise web.HTTPFound('/demo/?next=one')
            if path == '/demo/upload':
                body = await request.read()
                return web.Response(body=body, headers={'X-Seen-Length': request.headers.get('Content-Length', 'chunked')})
            if path == '/demo/compressed':
                return web.Response(body=gzip.compress(b'compressed opaque bytes'), headers={'Content-Encoding': 'gzip'})
            if path == '/demo/events':
                result = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
                await result.prepare(request); await result.write(b'data: first\n\n')
                await self.release_sse.wait(); await result.write(b'data: second\n\n'); return result
            return web.Response(status=404)
        app = web.Application(); app.router.add_route('*', '/{path:.*}', upstream)
        self.upstream = TestServer(app); await self.upstream.start_server()
        def projection():
            self.project_calls += 1; return EMPTY
        self.front = TestServer(proxy.create_app(str(self.upstream.make_url('')).rstrip('/'), '/demo', projection))
        await self.front.start_server()
        self.client = ClientSession(cookie_jar=DummyCookieJar(), auto_decompress=False)

    async def asyncTearDown(self):
        self.release_sse.set()
        await self.client.close(); await self.front.close(); await self.upstream.close()

    def url(self, path):
        return self.front.make_url('/demo/' + path)

    async def test_registry_auth_origin_method_query_and_no_credential_cache(self):
        url = self.url('__owner_groups__/snapshot')
        for headers, status in [({}, 401), ({'Cookie': 'synthetic=bad'}, 401),
            ({'Cookie': 'synthetic=valid', 'Origin': 'https://evil.invalid'}, 403),
            ({'Cookie': 'synthetic=valid', 'Sec-Fetch-Site': 'cross-site'}, 403)]:
            async with self.client.post(url, headers=headers) as response:
                self.assertEqual(response.status, status); self.assertNotIn('Access-Control-Allow-Origin', response.headers)
        self.assertEqual(self.project_calls, 0)
        async with self.client.post(url, headers={'Cookie': 'synthetic=valid', 'Origin': str(self.front.make_url('')).rstrip('/')}) as response:
            self.assertEqual(response.status, 200); self.assertEqual(await response.json(), {'owners': EMPTY, 'sessions': {'sessions': []}, 'demo': False})
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(self.project_calls, 1)
        async with self.client.post(url) as response:
            self.assertEqual(response.status, 401)
        async with self.client.post(str(url) + '?root=/private', headers={'Cookie': 'synthetic=valid'}) as response:
            self.assertEqual(response.status, 403)
        async with self.client.get(url, headers={'Cookie': 'synthetic=valid'}) as response:
            self.assertEqual(response.status, 405)
        async with self.client.post(url, headers={'Cookie': 'synthetic=valid'}, data=b'not-empty') as response:
            self.assertEqual(response.status, 400)
        for mode in ('redirect', 'html'):
            self.auth_mode = mode
            async with self.client.post(url, headers={'Cookie': 'synthetic=valid'}) as response:
                self.assertEqual(response.status, 503)
        self.assertEqual(self.project_calls, 1)

    async def test_injection_only_known_html_and_changed_entity_headers(self):
        async with self.client.get(self.url(''), headers={'If-None-Match': 'upstream-tag'}) as response:
            body = await response.read()
            self.assertIn(b'/demo/__owner_groups__/app.js', body)
            self.assertEqual(response.headers['X-Owner-Groups'], 'injected')
            self.assertNotIn('ETag', response.headers)
            self.assertEqual(int(response.headers['Content-Length']), len(body))
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        for html in (b'<html><body>unknown</body></html>', HTML.replace(b"script-src 'self'", b"script-src 'none'")):
            body, status = proxy.inject(html, proxy.CIMultiDict(), '/demo')
            self.assertEqual(body, html); self.assertEqual(status, 'unsupported-html-or-csp')
        async with self.client.get(self.url('unknown')) as response:
            self.assertEqual(await response.read(), b'unchanged'); self.assertEqual(response.headers['ETag'], 'keep-me')
        async with self.client.get(self.url('__owner_groups__/app.js')) as response:
            self.assertEqual(response.status, 200); self.assertIn('javascript', response.content_type)

    async def test_upload_redirect_duplicate_cookies_and_compression(self):
        data = b'\x00binary synthetic upload' * 1000
        async with self.client.post(self.url('upload'), data=data) as response:
            self.assertEqual(await response.read(), data)
            self.assertEqual(response.headers['X-Seen-Length'], str(len(data)))
        async def chunks():
            yield b'chunk1'; yield b'chunk2'
        async with self.client.post(self.url('upload'), data=chunks()) as response:
            self.assertEqual(await response.read(), b'chunk1chunk2')
        async with self.client.get(self.url('redirect'), allow_redirects=False) as response:
            self.assertEqual(response.status, 302); self.assertEqual(response.headers['Location'], '/demo/?next=one')
        async with self.client.get(self.url('cookies')) as response:
            self.assertEqual(response.headers.getall('Set-Cookie'), ['a=1', 'b=2'])
        async with self.client.get(self.url('compressed')) as response:
            self.assertEqual(gzip.decompress(await response.read()), b'compressed opaque bytes')
            self.assertEqual(response.headers['Content-Encoding'], 'gzip')

    async def test_snapshot_linkage_whitelist_and_auth_schema(self):
        temp = tempfile.TemporaryDirectory(prefix='owner-public-schema-'); self.addCleanup(temp.cleanup)
        log = Path(temp.name) / 'native-log.jsonl'; log.touch()
        self.session_rows = [{'session_id': 'opaque-broker-123', 'thread_id': 'pi-synthetic-id',
            'agent_backend': 'pi', 'log_path': str(log),
            'title': 'PRIVATE-TITLE', 'cwd': '/private/repo', 'transcript': 'PRIVATE-CONTENT'}]
        async with self.client.post(self.url('__owner_groups__/snapshot'), headers={'Cookie': 'synthetic=valid'}) as response:
            text = await response.text(); data = json.loads(text)
            self.assertEqual(data['sessions']['sessions'], [{'session_id': 'opaque-broker-123',
                'thread_id': 'pi-synthetic-id', 'agent_backend': 'pi', 'has_bound_log': True}])
            for forbidden in (str(log), '/private', 'PRIVATE', 'log_path', 'title', 'transcript', 'private_extra'):
                self.assertNotIn(forbidden, text)
        for row in ({'session_id': 'missing-contract'}, {'session_id': 'bad', 'agent_backend': 'pi', 'log_path': 42}):
            self.session_rows = [row]
            async with self.client.post(self.url('__owner_groups__/snapshot'), headers={'Cookie': 'synthetic=valid'}) as response:
                self.assertEqual(response.status, 503)

    async def test_public_nonempty_rows_stat_only_regular_absolute_logs(self):
        with tempfile.TemporaryDirectory(prefix='owner-public-logs-') as root:
            log = Path(root) / 'regular.jsonl'; log.write_bytes(b'PRIVATE-CONTENT-NOT-READ')
            variants = [str(log), str(Path(root) / 'missing'), None, '', 'relative.jsonl', root, '/bad\x00path']
            self.session_rows = [{'session_id': 'row-' + str(i), 'agent_backend': 'pi', 'thread_id': 'pi-' + str(i), 'log_path': value}
                                 for i, value in enumerate(variants)]
            # Internal optional fields do not decide binding, including contradictory values.
            self.session_rows[0]['log_exists'] = False
            self.session_rows[1]['log_exists'] = True
            async with self.client.post(self.url('__owner_groups__/snapshot'), headers={'Cookie': 'synthetic=valid'}) as response:
                self.assertEqual(response.status, 200)
                text = await response.text(); data = json.loads(text)
                self.assertEqual([r['has_bound_log'] for r in data['sessions']['sessions']], [True, False, False, False, False, False, False])
                self.assertNotIn(root, text); self.assertNotIn('PRIVATE-CONTENT', text)
            with patch.object(proxy.Path, 'stat', side_effect=PermissionError('private path must not leak')):
                self.assertFalse(proxy.session_linkage(self.session_rows[:1])[0]['has_bound_log'])

    async def test_sse_first_event_arrives_before_upstream_finishes(self):
        async with self.client.get(self.url('events'), timeout=ClientTimeout(total=3)) as response:
            first = await asyncio.wait_for(response.content.readuntil(b'\n\n'), 1)
            self.assertEqual(first, b'data: first\n\n')
            self.assertFalse(self.release_sse.is_set())
            self.release_sse.set()
            self.assertEqual(await response.content.readuntil(b'\n\n'), b'data: second\n\n')

    async def test_unavailable_upstream_never_exports_registry(self):
        await self.upstream.close()
        async with self.client.post(self.url('__owner_groups__/snapshot'), headers={'Cookie': 'synthetic=valid'}) as response:
            self.assertEqual(response.status, 503)
        self.assertEqual(self.project_calls, 0)


if __name__ == '__main__':
    unittest.main()
