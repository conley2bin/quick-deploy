"""App-local caching regressions; canonical synthetic data and temporary HOME only."""
import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from aiohttp import ClientSession, DummyCookieJar, web
from aiohttp.test_utils import TestServer

import fixture

p = fixture.projection
sys.path.insert(0, str(fixture.HERE))
import proxy


class SyntheticRegistry:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix='owner-cache-home-')
        self.home = Path(self.temp.name)
        self.env = patch.dict(os.environ, HOME=str(self.home))
        self.env.start()
        self.root = self.home / 'registry'
        self.root.mkdir()
        self.document = self.root / 'RESPONSIBILITIES.md'
        self.codec_path = self.home / 'scripts/registry_document.py'
        self.codec_path.parent.mkdir()
        self.codec_path.write_bytes(fixture.CODEC.read_bytes())
        self.state = fixture.build(self.root)
        self.era = '00000000-0000-0000-0000-000000000001'
        self.write()

    def close(self):
        self.env.stop()
        self.temp.cleanup()

    def write(self):
        self.document.write_text(fixture.registry_document.serialize_document(self.state, self.era), encoding='utf-8')

    def reader(self):
        return p.RegistryReader(self.root, self.document, self.codec_path)


@contextmanager
def counted_loads():
    """Instrument actual codec parse and document read, not cache implementation methods."""
    counts = {'parse': 0, 'read': 0, 'load': 0}
    real_load = p.load_codec
    original_open = Path.open
    original_fdopen = os.fdopen

    class CountedSource:
        def __init__(self, source):
            self.source = source
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return self.source.__exit__(*args)
        def __getattr__(self, name):
            return getattr(self.source, name)
        def read(self, *args):
            counts['read'] += 1
            return self.source.read(*args)

    def opened(path, *args, **kwargs):
        source = original_open(path, *args, **kwargs)
        return CountedSource(source) if path.name == 'RESPONSIBILITIES.md' and args == ('rb',) else source

    def fdopened(fd, *args, **kwargs):
        source = original_fdopen(fd, *args, **kwargs)
        target = Path(os.readlink('/proc/self/fd/' + str(fd))).name
        return CountedSource(source) if target == 'RESPONSIBILITIES.md' else source

    def loaded(*args, **kwargs):
        counts['load'] += 1
        codec = real_load(*args, **kwargs)
        real_parse = codec.parse_document
        def parsed(*args, **kwargs):
            counts['parse'] += 1
            return real_parse(*args, **kwargs)
        codec.parse_document = parsed
        return codec

    with patch.object(p, 'load_codec', loaded), patch.object(Path, 'open', opened), patch.object(os, 'fdopen', fdopened):
        yield counts


class RegistryCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = SyntheticRegistry()
        cls.initial_state = copy.deepcopy(cls.files.state)
        cls.initial_codec = cls.files.codec_path.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.files.close()

    def setUp(self):
        self.files.state = copy.deepcopy(self.initial_state)
        self.files.era = '00000000-0000-0000-0000-000000000001'
        for path in (self.files.document, self.files.codec_path, self.files.root / 'registry.lock'):
            if path.is_symlink() or (path.exists() and not path.is_file()):
                path.unlink() if not path.is_dir() else path.rmdir()
        (self.files.root / 'registry.lock').touch()
        self.files.codec_path.write_bytes(self.initial_codec)
        self.files.write()

    def test_hot_generation_reads_and_parses_zero_content(self):
        reader = self.files.reader()
        with counted_loads() as counts:
            expected = reader()
            self.assertTrue(expected['ok'])
            self.assertEqual(counts, {'parse': 1, 'read': 1, 'load': 1})
            counts.update(parse=0, read=0, load=0)
            for _ in range(5):
                self.assertEqual(reader(), expected)
            self.assertEqual(counts, {'parse': 0, 'read': 0, 'load': 0})

    def test_stateless_entrypoint_still_loads_each_call(self):
        with counted_loads() as counts:
            first = p.read_registry(self.files.root, self.files.document, self.files.codec_path)
            second = p.read_registry(self.files.root, self.files.document, self.files.codec_path)
        self.assertTrue(first['ok'])
        self.assertEqual(first, second)
        self.assertEqual(counts, {'parse': 2, 'read': 2, 'load': 2})

    def test_cold_concurrent_callers_share_one_parse_and_correct_result(self):
        reader = self.files.reader()
        ready = threading.Barrier(12)
        def call():
            ready.wait(timeout=5)
            return reader()
        with counted_loads() as counts, ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: call(), range(12)))
        self.assertEqual(counts, {'parse': 1, 'read': 1, 'load': 1})
        expected = dict(p.project(self.files.state), era=self.files.era)
        self.assertEqual(results, [expected] * 12)
        results[0]['owners'][0]['scopes'].clear()
        self.assertEqual(results[1], expected)

    def test_waiter_rechecks_generation_after_inflight_source_change(self):
        reader = self.files.reader()
        parsing, release, waiting = threading.Event(), threading.Event(), threading.Event()
        real_load = p.load_codec
        calls = []
        def loaded(*args, **kwargs):
            codec = real_load(*args, **kwargs)
            real_parse = codec.parse_document
            def parse(raw):
                calls.append(True)
                if len(calls) == 1:
                    parsing.set()
                    if not release.wait(timeout=5):
                        raise AssertionError('test did not release cold parse')
                return real_parse(raw)
            codec.parse_document = parse
            return codec
        def waiter():
            waiting.set()
            return reader()
        with patch.object(p, 'load_codec', loaded), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(reader)
            try:
                self.assertTrue(parsing.wait(timeout=5))
                second = pool.submit(waiter)
                self.assertTrue(waiting.wait(timeout=5))
                self.files.state['owners']['ops']['name'] = 'New generation'
                self.files.write()
            finally:
                release.set()
            self.assertFalse(first.result(timeout=5)['ok'])
            self.assertEqual(second.result(timeout=5), dict(p.project(self.files.state), era=self.files.era))
        self.assertEqual(len(calls), 2)

    def test_rename_reset_identity_reparent_and_history_mutations_reload(self):
        reader = self.files.reader()
        with counted_loads() as counts:
            self.assertTrue(reader()['ok'])
            changes = (
                lambda: self.files.state['owners']['ops'].update(name='Renamed owner'),
                lambda: self.files.state['owners']['office'].update(parent='owner:other'),
                lambda: self.files.state['owners']['ops']['identity'].update(pi_session_id='pi-changed-id'),
                lambda: self.files.state['history'][0].update(reason='PRIVATE changed audit reason'),
            )
            for number, change in enumerate(changes, start=2):
                change()
                self.files.write()
                self.assertEqual(reader(), dict(p.project(self.files.state), era=self.files.era))
                self.assertEqual(counts['parse'], number)
            self.files.state = {'version': 1, 'revision': 0, 'owners': {}, 'assignments': {}, 'coordination': {}, 'history': []}
            self.files.era = '00000000-0000-0000-0000-000000000002'
            self.files.write()
            reset = reader()
            self.assertTrue(reset['ok'])
            self.assertEqual(reset['owners'], [])
            self.assertEqual(reset['era'], self.files.era)
            self.assertEqual(counts['parse'], 6)

    def test_same_size_and_restored_mtime_still_reloads_on_ctime(self):
        reader = self.files.reader()
        with counted_loads() as counts:
            before = reader()
            old = self.files.document.stat()
            raw = self.files.document.read_bytes()
            changed = raw.replace('运营总负责人'.encode(), '运行总负责人'.encode())
            self.assertNotEqual(raw, changed)
            self.files.document.write_bytes(changed)
            os.utime(self.files.document, ns=(old.st_atime_ns, old.st_mtime_ns))
            new = self.files.document.stat()
            self.assertEqual((old.st_dev, old.st_ino, old.st_size, old.st_mtime_ns),
                             (new.st_dev, new.st_ino, new.st_size, new.st_mtime_ns))
            self.assertNotEqual(old.st_ctime_ns, new.st_ctime_ns)
            after = reader()
            self.assertNotEqual(before['owners'][0]['name'], after['owners'][0]['name'])
            self.assertEqual(counts['parse'], 2)

    def test_atomic_replace_with_equal_size_and_mtime_reloads(self):
        reader = self.files.reader()
        with counted_loads() as counts:
            self.assertTrue(reader()['ok'])
            old = self.files.document.stat()
            replacement = self.files.root / 'replacement.md'
            replacement.write_bytes(self.files.document.read_bytes())
            os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns))
            replacement.replace(self.files.document)
            self.assertNotEqual(old.st_ino, self.files.document.stat().st_ino)
            self.assertTrue(reader()['ok'])
            self.assertEqual(counts['parse'], 2)

    def test_codec_change_and_same_size_restored_mtime_bypass_pyc(self):
        reader = self.files.reader()
        # Populate an actual same-size/mtime .pyc: a ctime-sensitive key alone is insufficient.
        pyc = Path(py_compile.compile(str(self.files.codec_path), doraise=True))
        self.assertTrue(pyc.is_file())
        with counted_loads() as counts:
            self.assertTrue(reader()['ok'])
            old = self.files.codec_path.stat()
            raw = self.files.codec_path.read_bytes()
            changed = raw.replace(b'responsibility-registry/v2', b'responsibility-registry/v3')
            self.assertNotEqual(raw, changed)
            self.files.codec_path.write_bytes(changed)
            os.utime(self.files.codec_path, ns=(old.st_atime_ns, old.st_mtime_ns))
            self.assertEqual(self.files.codec_path.stat().st_size, old.st_size)
            self.assertEqual(self.files.codec_path.stat().st_mtime_ns, old.st_mtime_ns)
            self.assertNotEqual(self.files.codec_path.stat().st_ctime_ns, old.st_ctime_ns)
            spec = importlib.util.spec_from_file_location('stale_synthetic_codec', self.files.codec_path)
            stale = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(stale)
            self.assertEqual(stale.FORMAT, p.FORMAT)  # Timestamp .pyc still contains the OLD codec.
            result = reader()
            self.assertFalse(result['ok'])
            self.assertEqual(result['owners'], [])
            self.assertEqual(counts['load'], 2)
            self.files.codec_path.write_bytes(raw)
            self.assertTrue(reader()['ok'])
            self.assertEqual(counts['parse'], 2)

    def test_invalid_generation_discards_success_and_can_cache_sanitized_failure(self):
        reader = self.files.reader()
        with counted_loads() as counts:
            self.assertTrue(reader()['ok'])
            self.files.document.write_text('PRIVATE malformed markdown')
            failed = reader()
            self.assertFalse(failed['ok'])
            self.assertNotIn('PRIVATE', json.dumps(failed))
            self.assertEqual(reader(), failed)
            self.assertEqual(counts['parse'], 2)
            self.files.write()
            self.assertTrue(reader()['ok'])
            self.assertEqual(counts['parse'], 3)

    def test_missing_unreadable_link_nonregular_and_lock_failure_clear_success(self):
        for name in ('document', 'codec_path', 'lock'):
            for kind in ('missing', 'unreadable', 'symlink', 'directory'):
                with self.subTest(source=name, kind=kind):
                    self.setUp()
                    reader = self.files.reader()
                    self.assertTrue(reader()['ok'])
                    path = self.files.root / 'registry.lock' if name == 'lock' else getattr(self.files, name)
                    saved = path.read_bytes()
                    if kind == 'unreadable':
                        path.chmod(0)
                    else:
                        path.unlink()
                        if kind == 'symlink':
                            target = self.files.home / 'link-target'
                            target.write_bytes(saved)
                            path.symlink_to(target)
                        elif kind == 'directory':
                            path.mkdir()
                    try:
                        result = reader()
                        self.assertFalse(result['ok'])
                        self.assertEqual(result['owners'], [])
                        self.assertIsNone(reader._generation)
                        self.assertIsNone(reader._projection)
                    finally:
                        if kind == 'directory':
                            path.rmdir()
                        elif kind == 'symlink':
                            path.unlink()
                        elif kind == 'unreadable':
                            path.chmod(0o600)
                        path.write_bytes(saved)
                    self.assertTrue(reader()['ok'])
        reader = self.files.reader()
        self.assertTrue(reader()['ok'])
        with patch.object(p.fcntl, 'flock', side_effect=PermissionError('PRIVATE lock failure')):
            self.assertFalse(reader()['ok'])
            self.assertIsNone(reader._generation)
            self.assertIsNone(reader._projection)

    def test_noncooperating_edits_during_parse_never_publish(self):
        for name, atomic in (('document', False), ('document', True), ('codec_path', False), ('codec_path', True)):
            with self.subTest(source=name, atomic=atomic):
                self.setUp()
                reader = self.files.reader()
                real_load = p.load_codec
                def changing_load(*args, **kwargs):
                    codec = real_load(*args, **kwargs)
                    real_parse = codec.parse_document
                    def parse(raw):
                        state = real_parse(raw)
                        path = getattr(self.files, name)
                        if atomic:
                            replacement = self.files.home / 'replacement'
                            replacement.write_bytes(path.read_bytes())
                            replacement.replace(path)
                        else:
                            with path.open('ab') as source:
                                source.write(b'\n')
                        return state
                    codec.parse_document = parse
                    return codec
                with patch.object(p, 'load_codec', changing_load):
                    result = reader()
                self.assertFalse(result['ok'])
                self.assertIsNone(reader._generation)
                self.assertIsNone(reader._projection)

    def test_returned_results_do_not_mutate_cache_and_retained_state_is_public(self):
        reader = self.files.reader()
        expected = reader()
        changed = reader()
        changed['era'] = 'caller-controlled'
        changed['owners'][0]['scopes'][0]['task'] = 'caller-controlled'
        changed['owners'][0]['name'] = 'caller-controlled'
        changed['owners'].clear()
        self.assertEqual(reader(), expected)
        self.assertEqual(set(vars(reader)), {'root', 'document', 'codec_path', '_mutex', '_generation', '_projection'})
        retained = json.dumps(reader._projection)
        for private in ('PRIVATE', 'history', 'synthetic-contact', 'recovery', 'objective', 'acceptance', 'cookie', 'sessions'):
            self.assertNotIn(private, retained)


class FeatureCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.files = SyntheticRegistry()
        self.cookies = []
        self.rows = []
        async def upstream(request):
            self.cookies.append(request.headers.get('Cookie'))
            if request.headers.get('Cookie') not in ('caller=one', 'caller=two'):
                return web.json_response({'error': 'denied'}, status=401)
            return web.json_response({'sessions': self.rows})
        app = web.Application()
        app.router.add_get('/api/sessions', upstream)
        self.upstream = TestServer(app)
        await self.upstream.start_server()
        self.owner = proxy._owner
        self.reader = self.files.reader()
        with patch.object(self.owner, 'RegistryReader', return_value=self.reader) as constructor:
            self.app = proxy.create_app(str(self.upstream.make_url('')).rstrip('/'))
            constructor.assert_called_once_with()
        self.front = TestServer(self.app)
        await self.front.start_server()
        self.client = ClientSession(cookie_jar=DummyCookieJar())

    async def asyncTearDown(self):
        await self.client.close()
        await self.front.close()
        await self.upstream.close()
        self.files.close()

    async def request(self, cookie, client=None):
        # Fail before HTTP if facade wiring regresses; never read a real HOME registry.
        self.assertIs(self.app[self.owner.STATE]['projector'], self.reader)
        headers = {'Cookie': cookie} if cookie is not None else {}
        async with (client or self.client).post(self.front.make_url('/__owner_groups__/snapshot'), headers=headers) as response:
            return response.status, await response.json()

    async def test_facade_default_shares_projection_with_fresh_auth_and_mapping(self):
        log = self.files.home / 'native.jsonl'
        log.write_text('PRIVATE transcript never read')
        with counted_loads() as counts:
            self.rows = [{'session_id': 'broker-one', 'thread_id': 'pi-synthetic-ops', 'agent_backend': 'pi', 'log_path': str(log)}]
            first_status, first = await self.request('caller=one')
            self.assertEqual(first_status, 200)
            self.assertEqual(first['sessions']['sessions'][0]['session_id'], 'broker-one')
            self.assertTrue(first['sessions']['sessions'][0]['has_bound_log'])
            self.rows[0].update(session_id='broker-two', thread_id='pi-new-binding')
            log.unlink()
            second_status, second = await self.request('caller=two')
            self.assertEqual(second_status, 200)
            self.assertEqual(second['owners'], first['owners'])
            self.assertEqual(second['sessions']['sessions'][0], {'session_id': 'broker-two', 'thread_id': 'pi-new-binding',
                'agent_backend': 'pi', 'has_bound_log': False})
            self.assertEqual(counts, {'parse': 1, 'read': 1, 'load': 1})
            for cookie in ('caller=bad', None):
                status, denied = await self.request(cookie)
                self.assertEqual(status, 401)
                self.assertNotIn('owners', denied)
                self.assertNotIn('sessions', denied)
            self.assertEqual(counts, {'parse': 1, 'read': 1, 'load': 1})
        self.assertEqual(self.cookies, ['caller=one', 'caller=two', 'caller=bad'])
        self.assertNotIn('PRIVATE', json.dumps(second))

    async def test_concurrent_clients_share_cold_parse_and_auth_each_request(self):
        async with ClientSession(cookie_jar=DummyCookieJar()) as second_client:
            with counted_loads() as counts:
                responses = await asyncio.gather(*(self.request(cookie, client)
                    for _ in range(4) for cookie, client in (('caller=one', self.client), ('caller=two', second_client))))
            self.assertEqual(counts, {'parse': 1, 'read': 1, 'load': 1})
            expected = dict(p.project(self.files.state), era=self.files.era)
            for status, payload in responses:
                self.assertEqual(status, 200)
                self.assertEqual(payload['owners'], expected)
            self.assertCountEqual(self.cookies, ['caller=one', 'caller=two'] * 4)

    async def test_facade_invalid_and_missing_current_source_never_returns_old_owners(self):
        status, initial = await self.request('caller=one')
        self.assertEqual(status, 200)
        self.assertTrue(initial['owners']['owners'])
        self.files.document.write_text('PRIVATE invalid document')
        status, invalid = await self.request('caller=one')
        self.assertEqual(status, 503)
        self.assertNotIn('owners', invalid)
        self.assertNotIn('PRIVATE', json.dumps(invalid))
        self.files.write()
        self.assertEqual((await self.request('caller=one'))[0], 200)
        self.files.document.unlink()
        status, missing = await self.request('caller=two')
        self.assertEqual(status, 503)
        self.assertNotIn('owners', missing)
        self.assertIsNone(self.reader._projection)

    async def test_facade_separates_app_readers_and_preserves_explicit_projector(self):
        app = proxy.create_app('http://127.0.0.1:1')
        other_reader = app[self.owner.STATE]['projector']
        self.assertIsNot(other_reader, self.reader)
        self.assertEqual(other_reader.__class__.__name__, 'RegistryReader')
        self.assertIs(self.app[self.owner.STATE]['projector'], self.reader)
        explicit = lambda: {'ok': False}
        overridden = proxy.create_app('http://127.0.0.1:1', projector=explicit)
        self.assertIs(overridden[self.owner.STATE]['projector'], explicit)


if __name__ == '__main__':
    unittest.main()
