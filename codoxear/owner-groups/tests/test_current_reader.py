"""Real binary reader/current-codec boundary; synthetic files and loopback HTTP only."""
from contextlib import contextmanager
import copy
import io
import json
import os
import unittest
from unittest.mock import patch

from aiohttp import ClientSession, DummyCookieJar, web
from aiohttp.test_utils import TestServer

import fixture
from test_registry_cache import SyntheticRegistry, counted_loads, proxy

p = fixture.projection


@contextmanager
def measured_document(document):
    """Count actual raw readinto bytes plus logical binary lines, including prefetch."""
    counts = {'bytes': 0, 'raw_reads': [], 'lines': 0, 'line_bytes': 0}
    original = os.fdopen

    class Raw(io.RawIOBase):
        def __init__(self, fd):
            super().__init__()
            self.source = io.FileIO(fd, 'rb', closefd=True)
        def readable(self):
            return True
        def fileno(self):
            return self.source.fileno()
        def readinto(self, target):
            size = self.source.readinto(target)
            counts['raw_reads'].append((len(target), size))
            counts['bytes'] += size
            return size
        def close(self):
            self.source.close()
            super().close()

    class Lines:
        def __init__(self, source):
            self.source = source
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.source.close()
        def fileno(self):
            return self.source.fileno()
        def __iter__(self):
            return self
        def __next__(self):
            line = self.source.readline()
            if not line:
                raise StopIteration
            counts['lines'] += 1
            counts['line_bytes'] += len(line)
            return line
        def read(self, size=-1):
            raise AssertionError('current reader must not read the whole document')

    def opened(fd, *args, **kwargs):
        if os.path.realpath('/proc/self/fd/' + str(fd)) == str(document):
            return Lines(io.BufferedReader(Raw(fd), buffer_size=io.DEFAULT_BUFFER_SIZE))
        return original(fd, *args, **kwargs)

    with patch.object(os, 'fdopen', opened):
        yield counts


class CurrentReaderTests(unittest.TestCase):
    def setUp(self):
        self.files = SyntheticRegistry()
        self.addCleanup(self.files.close)
        text = self.files.document.read_text(encoding='utf-8')
        self.prefix = (text.split(fixture.registry_document.HISTORY + '\n', 1)[0]
                       + fixture.registry_document.HISTORY + '\n').encode('utf-8')
        self.expected = dict(p.project(self.files.state), era=self.files.era)

    def test_binary_bad_utf8_audit_succeeds_and_current_or_boundary_failure_clears_success(self):
        reader = self.files.reader()
        good = self.prefix + b'```json\n[\xff]\n```\n'
        self.files.document.write_bytes(good)
        self.assertEqual(reader(), self.expected)
        self.assertEqual(p.read_registry(self.files.root, self.files.document, self.files.codec_path), self.expected)
        with self.assertRaises(UnicodeDecodeError):
            fixture.registry_document.parse_document(good.decode('utf-8'))
        for invalid in (self.prefix.replace(b'## Owners', b'## Own\xffers'),
                        self.prefix.replace(fixture.registry_document.HISTORY.encode(), b'## History'),
                        self.prefix.split(fixture.registry_document.HISTORY.encode(), 1)[0]):
            self.files.document.write_bytes(invalid)
            failed = reader()
            self.assertFalse(failed['ok'])
            self.assertEqual(failed['owners'], [])
            self.assertFalse(reader._projection['ok'])
            self.files.document.write_bytes(good)
            self.assertEqual(reader(), self.expected)

    def test_large_sparse_audit_has_same_bounded_raw_reads_and_hot_zero(self):
        # Both tails exceed a buffer. Only the long one crosses the retired64MiB gate.
        short = self.prefix + b'```json\n[\xff]\n```\n' + b'.' * (2 * io.DEFAULT_BUFFER_SIZE)
        self.files.document.write_bytes(short)
        with measured_document(self.files.document) as small:
            self.assertEqual(self.files.reader()(), self.expected)
        with self.files.document.open('wb') as source:
            source.write(short)
            source.seek(128 * 1024 * 1024)
            source.write(b'\xff')
        self.assertGreater(self.files.document.stat().st_size, 64 * 1024 * 1024)
        reader = self.files.reader()
        with measured_document(self.files.document) as large, counted_loads() as loads:
            self.assertEqual(reader(), self.expected)
            self.assertEqual(loads['parse'], 1)
        self.assertEqual(large, small)
        self.assertEqual(large['line_bytes'], len(self.prefix))
        self.assertLessEqual(large['bytes'], len(self.prefix) + io.DEFAULT_BUFFER_SIZE)
        self.assertTrue(large['raw_reads'])
        self.assertTrue(all(request <= io.DEFAULT_BUFFER_SIZE for request, _ in large['raw_reads']))
        with measured_document(self.files.document) as hot, counted_loads() as loads:
            for _ in range(5):
                self.assertEqual(reader(), self.expected)
            self.assertEqual(loads, {'parse': 0, 'read': 0, 'load': 0})
        self.assertEqual(hot, {'bytes': 0, 'raw_reads': [], 'lines': 0, 'line_bytes': 0})

    def test_corrupt_audit_is_current_valid_but_full_invalid(self):
        self.files.document.write_bytes(self.prefix + b'```json\n[]\n```\n')
        self.assertEqual(self.files.reader()(), self.expected)
        with self.assertRaises(fixture.registry_document.RegistryError):
            fixture.registry_document.parse_document(self.files.document.read_text())

    def test_project_current_really_validates_five_fields_and_preserves_full_project_contract(self):
        codec = fixture.registry_document
        current = {key: copy.deepcopy(value) for key, value in self.files.state.items() if key != 'history'}
        self.assertEqual(p.project_current(current, codec), p.project(self.files.state))
        with self.assertRaises(ValueError):
            p.project(current)
        invalid_full = copy.deepcopy(self.files.state)
        invalid_full['history'] = []
        with self.assertRaises(ValueError):
            p.project(invalid_full)
        mutations = [dict(current, history=[]), {key: value for key, value in current.items() if key != 'coordination'}]
        bad_coordination = copy.deepcopy(current)
        bad_coordination['coordination'] = {'bad': {'id': 'bad'}}
        mutations.append(bad_coordination)
        bad_assignment = copy.deepcopy(current)
        next(iter(bad_assignment['assignments'].values()))['start_time'] = dict(at='2026-01-01', precision='exact')
        mutations.append(bad_assignment)
        for state in mutations:
            with self.subTest(fields=state.keys()), self.assertRaises(codec.RegistryError):
                p.project_current(state, codec)
        # The new API checks current revision type/domain, not unread audit length.
        current['revision'] = 10**30
        self.assertEqual(p.project_current(current, codec)['revision'], 10**30)

    def test_missing_current_codec_api_explicitly_unavailable_without_old_success_or_full_parse(self):
        reader = self.files.reader()
        self.assertEqual(reader(), self.expected)
        original = self.files.codec_path.read_bytes()
        for name in ('parse_current_document', 'validate_current'):
            # A valid v2 source with one missing new API is incompatible, not a fallback.
            self.files.codec_path.write_bytes(original + ('\n' + name + ' = None\n'
                'def parse_document(*args):\n    raise AssertionError("full parse fallback")\n').encode())
            result = reader()
            self.assertEqual(result, p.failure('incompatible-codec'))
            self.assertEqual(reader._projection, result)
            self.assertNotEqual(result, self.expected)
        self.files.codec_path.write_bytes(original)
        self.assertEqual(reader(), self.expected)


class CurrentReaderHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_host_and_facade_use_real_current_reader_with_fresh_auth_and_binding(self):
        files = SyntheticRegistry()
        self.addCleanup(files.close)
        rows, cookies = [], []
        async def native(request):
            cookie = request.headers.get('Cookie')
            cookies.append(cookie)
            if cookie not in ('caller=one', 'caller=two'):
                return web.json_response({'error': 'denied'}, status=401)
            return web.json_response({'sessions': rows})
        upstream_app = web.Application()
        upstream_app.router.add_get('/api/sessions', native)
        async with TestServer(upstream_app) as upstream:
            for facade in (False, True):
                reader = files.reader()
                owner = proxy._owner
                with patch.object(owner, 'RegistryReader', return_value=reader):
                    url = str(upstream.make_url('')).rstrip('/')
                    app = proxy.create_app(url) if facade else proxy._host.create_app(url, features=(owner,))
                self.assertIs(app[owner.STATE]['projector'], reader)
                # Same-buffer invalid audit exercises HTTP -> feature -> binary reader.
                text = files.document.read_text()
                prefix = text.split(fixture.registry_document.HISTORY, 1)[0] + fixture.registry_document.HISTORY + '\n'
                files.document.write_bytes(prefix.encode() + b'\xff')
                log = files.home / 'native.jsonl'
                log.write_text('PRIVATE unread transcript')
                rows[:] = [dict(session_id='broker-one', thread_id='pi-synthetic-ops', agent_backend='pi', log_path=str(log))]
                async with TestServer(app) as front, ClientSession(cookie_jar=DummyCookieJar()) as one, \
                        ClientSession(cookie_jar=DummyCookieJar()) as two:
                    async def request(client, cookie):
                        async with client.post(front.make_url('/__owner_groups__/snapshot'), headers={'Cookie': cookie}) as response:
                            return response.status, await response.json()
                    with counted_loads() as counts:
                        first_status, first = await request(one, 'caller=one')
                        self.assertEqual(first_status, 200)
                        self.assertTrue(first['sessions']['sessions'][0]['has_bound_log'])
                        rows[0].update(session_id='broker-two', thread_id='pi-new-binding')
                        log.unlink()
                        second_status, second = await request(two, 'caller=two')
                        self.assertEqual(second_status, 200)
                        self.assertEqual(first['owners'], second['owners'])
                        self.assertEqual(second['sessions']['sessions'][0], dict(session_id='broker-two',
                            thread_id='pi-new-binding', agent_backend='pi', has_bound_log=False))
                        denied_status, denied = await request(two, 'caller=bad')
                        self.assertEqual(denied_status, 401)
                        self.assertNotIn('owners', denied)
                        self.assertEqual(counts, {'parse': 1, 'read': 1, 'load': 1})
                    files.document.write_text('PRIVATE invalid current')
                    status, invalid = await request(one, 'caller=one')
                    self.assertEqual(status, 503)
                    self.assertNotIn('owners', invalid)
                    self.assertNotIn('PRIVATE', json.dumps(invalid))
                    files.write()
                    self.assertEqual((await request(two, 'caller=two'))[0], 200)
        self.assertEqual(cookies, ['caller=one', 'caller=two', 'caller=bad', 'caller=one', 'caller=two'] * 2)


if __name__ == '__main__':
    unittest.main()
