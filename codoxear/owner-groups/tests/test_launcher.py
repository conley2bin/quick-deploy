import asyncio
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import proxy


class WrapperTests(unittest.TestCase):
    def test_optional_mode_keeps_default_update_flow_and_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); internal = root / '.internal'; internal.mkdir()
            shutil.copyfile(proxy.HERE.parent / 'run_server.sh', root / 'run_server.sh')
            (root / '.env').touch()
            log = root / 'calls'
            (internal / 'common.sh').write_text(f'''
ENV_FILE={root}/.env
validate_env() {{ echo validate >> {log}; }}
sync_remote_main() {{ echo sync >> {log}; echo fixture-revision; }}
install_remote_main_if_needed() {{ echo install:$1 >> {log}; }}
python_launcher() {{ echo {sys.executable}; }}
''')
            (internal / 'launch.py').write_text(f'import sys\nopen({str(log)!r},"a").write("native:"+" ".join(sys.argv[1:])+"\\n")\n')
            result = subprocess.run(['bash', str(root / 'run_server.sh')], capture_output=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(log.read_text().splitlines(), ['validate', 'sync', 'install:fixture-revision', 'native:describe fixture-revision', 'native:server'])
            log.unlink()
            result = subprocess.run(['bash', str(root / 'run_server.sh'), '--owner-groups'], capture_output=True)
            self.assertNotEqual(result.returncode, 0); self.assertFalse(log.exists())
            python = root / 'owner-groups/.venv/bin/python'; python.parent.mkdir(parents=True)
            python.write_text(f'#!/bin/sh\nif [ "$1" != "-c" ]; then echo "addon:$*" >> {log}; fi\n'); python.chmod(0o755)
            result = subprocess.run(['bash', str(root / 'run_server.sh'), '--owner-groups'], capture_output=True)
            self.assertEqual(result.returncode, 0)
            calls = log.read_text().splitlines()
            self.assertEqual(calls[:4], ['validate', 'sync', 'install:fixture-revision', 'native:describe fixture-revision'])
            self.assertEqual(calls[-1], f'addon:{root}/owner-groups/proxy.py --managed-upstream')
            self.assertNotIn('native:server', calls)

    def test_fixed_upstream_and_prefix_guard(self):
        for origin in ('http://evil.invalid:1234', 'https://127.0.0.1:1234', 'http://127.0.0.1:1234/path', 'http://user@127.0.0.1:1234'):
            with self.assertRaises(ValueError): proxy.create_app(origin)
        with self.assertRaises(ValueError): proxy.create_app('http://127.0.0.1:1234', '/bad/../prefix')
        with proxy.front_socket('127.0.0.1', 0) as listener:
            self.assertEqual(listener.getsockname()[0], '127.0.0.1')
            self.assertGreater(listener.getsockname()[1], 0)
        # Assert wildcard socket options without opening an external test listener.
        from unittest.mock import MagicMock
        listener = MagicMock(); listener.family = proxy.socket.AF_INET6
        with patch.object(proxy.socket, 'socket', return_value=listener):
            proxy.front_socket('::', 8743)
        listener.setsockopt.assert_any_call(proxy.socket.IPPROTO_IPV6, proxy.socket.IPV6_V6ONLY, 0)
        listener.bind.assert_called_once_with(('::', 8743))


class ManagedLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_backend_private_front_topology_unchanged_and_cleanup_owned_child(self):
        future = asyncio.get_running_loop().create_future()
        class Child:
            returncode = None
            async def wait(self): return await asyncio.shield(future)
            def terminate(self): self.returncode = 0; future.set_result(0)
        child = Child()
        calls = []
        async def spawn(*args, **kwargs): calls.append((args, kwargs)); return child
        source = SimpleNamespace(validate_env=lambda: None,
            runtime_env=lambda: {'CODEX_WEB_HOST': '::', 'CODEX_WEB_PORT': '8743',
                'CODEX_WEB_URL_PREFIX': '/phone', 'CODEX_WEB_COOKIE_SECURE': '1'},
            access_path=lambda env: '/phone/', VENV_BIN=Path('/synthetic/bin'), HELPER_DIR=Path('/synthetic'))
        with patch.object(proxy, 'wrapper', lambda: source), patch.object(proxy.asyncio, 'create_subprocess_exec', spawn):
            app, host, port = proxy.managed_app()
            self.assertEqual((host, port), ('::', 8743)); self.assertEqual(app[proxy.CONFIG]['prefix'], '/phone')
            context = app.cleanup_ctx[-1](app); await context.__anext__()
            env = calls[0][1]['env']
            self.assertEqual(env['CODEX_WEB_HOST'], '127.0.0.1'); self.assertNotEqual(env['CODEX_WEB_PORT'], '8743')
            self.assertEqual(env['CODEX_WEB_URL_PREFIX'], '/phone'); self.assertEqual(env['CODEX_WEB_COOKIE_SECURE'], '1')
            self.assertEqual(calls[0][0], ('/synthetic/bin/codoxear-server',))
            with self.assertRaises(StopAsyncIteration): await context.__anext__()
            self.assertEqual(child.returncode, 0)


if __name__ == '__main__': unittest.main()
