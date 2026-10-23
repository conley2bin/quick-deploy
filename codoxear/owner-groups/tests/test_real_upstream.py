"""Optional real installed upstream auth integration, always isolated HOME/cwd.
Set CODOXEAR_TEST_PYTHON to the existing installed runtime Python (read-only).
"""
import asyncio
import os
from pathlib import Path
import secrets
import sys
import tempfile
import unittest

from aiohttp import ClientSession, DummyCookieJar
from aiohttp.test_utils import TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import proxy

RUNTIME = os.environ.get('CODOXEAR_TEST_PYTHON')


@unittest.skipUnless(RUNTIME, 'CODOXEAR_TEST_PYTHON not set; optional installed-upstream integration')
class InstalledAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_signed_cookie_login_prefix_and_isolated_empty_store(self):
        with tempfile.TemporaryDirectory(prefix='owner-groups-native-auth-') as temp:
            root = Path(temp)
            password = secrets.token_urlsafe(24)
            env = {**os.environ, 'HOME': temp, 'CODEX_HOME': temp + '/codex', 'PI_HOME': temp + '/pi',
                'CODEX_WEB_PASSWORD': password, 'CODEX_WEB_URL_PREFIX': '/demo',
                'CODEX_WEB_HOST': '127.0.0.1', 'CODEX_WEB_PORT': '0', 'CODEX_WEB_COOKIE_SECURE': '0', 'PYTHONDONTWRITEBYTECODE': '1'}
            script = '''from codoxear import server
server._init_runtime()
server._require_password()
httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
print(httpd.server_address[1], flush=True)
httpd.serve_forever()
'''
            child = await asyncio.create_subprocess_exec(RUNTIME, '-c', script, cwd=temp, env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                port = int(await asyncio.wait_for(child.stdout.readline(), 15))
                empty = {'schema': 'codoxear-owner-groups/v1', 'ok': True, 'revision': 0, 'owners': []}
                front = TestServer(proxy.create_app(f'http://127.0.0.1:{port}', '/demo', lambda: empty))
                await front.start_server()
                try:
                    async with ClientSession(cookie_jar=DummyCookieJar()) as client:
                        snapshot = front.make_url('/demo/__owner_groups__/snapshot')
                        async with client.post(snapshot) as response: self.assertEqual(response.status, 401)
                        async with client.get(front.make_url('/demo/')) as response:
                            self.assertEqual(response.status, 200, (await response.text())[:150])
                            self.assertEqual(response.headers.get('X-Owner-Groups'), 'injected')
                        async with client.post(front.make_url('/demo/api/login'), json={'password': password}) as response:
                            self.assertEqual(response.status, 200)
                            cookie = '; '.join(f'{k}={v.value}' for k, v in response.cookies.items())
                            self.assertTrue(cookie)
                            self.assertIn('Path=/demo/', response.headers['Set-Cookie'])
                        async with client.post(snapshot, headers={'Cookie': cookie}) as response:
                            self.assertEqual(response.status, 200)
                            data = await response.json()
                            self.assertEqual(data['owners']['owners'], [])
                            self.assertEqual(data['sessions']['sessions'], [])
                        async with client.post(snapshot, headers={'Cookie': 'codoxear_auth=invalid'}) as response:
                            self.assertEqual(response.status, 401)
                finally:
                    await front.close()
            finally:
                if child.returncode is None: child.terminate()
                await asyncio.wait_for(child.wait(), 10)
                diagnostic = await child.stderr.read()
                if b'Traceback' in diagnostic: print(diagnostic.decode()[:4000])


if __name__ == '__main__': unittest.main()
