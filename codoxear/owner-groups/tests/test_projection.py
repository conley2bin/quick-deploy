import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import fixture

p = fixture.projection


class ProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='owner-groups-test-')
        cls.root = Path(cls.temp.name)
        cls.state = fixture.build(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_canonical_fixture_whitelist(self):
        result = p.project(self.state)
        self.assertEqual(len(result['owners']), 11)
        owners = {o['id']: o for o in result['owners']}
        self.assertEqual(owners['office']['parent'], 'owner:ops')
        self.assertEqual(owners['qa']['parent'], 'owner:office')
        self.assertEqual(owners['orphan']['parent_status'], 'unresolved')
        self.assertIsNone(owners['kitchen']['pi_session_id'])
        self.assertEqual(owners['legacy']['state'], 'handed-over')
        self.assertEqual(owners['unknown']['state'], 'no-scope')
        self.assertTrue(owners['collision-a']['duplicate_identity'])
        self.assertFalse(owners['office']['duplicate_identity'])
        encoded = json.dumps(result)
        for forbidden in ('PRIVATE', '/example/', 'synthetic-contact', 'history', 'recovery', 'objective', 'acceptance', 'predecessor_context'):
            self.assertNotIn(forbidden, encoded)
        # Current identities only; no traversal of private historical snapshots.
        changed = copy.deepcopy(self.state)
        changed['history'][0]['after'] = {'pi_session_id': 'HISTORY-ONLY-ID'}
        self.assertNotIn('HISTORY-ONLY-ID', json.dumps(p.project(changed)))

    def test_registration_ordinal_is_stable_and_append_only(self):
        before = {o['id']: o['registration_order'] for o in p.project(self.state)['owners']}
        changed = copy.deepcopy(self.state)
        changed['owners']['ops']['name'] = 'ZZZ'
        changed['owners']['other']['name'] = 'AAA'
        changed['owners']['new'] = {**copy.deepcopy(changed['owners']['ops']), 'id': 'new'}
        after = {o['id']: o['registration_order'] for o in p.project(changed)['owners']}
        self.assertEqual({oid: after[oid] for oid in before}, before)
        self.assertEqual(after['new'], len(before))
        self.assertLess(before['ops'], before['other'])

    def test_read_only_including_dashboard_lock_and_modes(self):
        def snapshot():
            return {f.name: (f.read_bytes(), f.stat().st_mtime_ns, f.stat().st_mode, f.stat().st_ino)
                    for f in self.root.iterdir() if f.is_file()}
        before = snapshot()
        self.assertTrue(p.read_registry(self.root, self.root / 'RESPONSIBILITIES.md', fixture.CODEC)['ok'])
        self.assertEqual(snapshot(), before)

    def test_lock_precedes_snapshot_read(self):
        original = Path.open
        locked = []
        def opened(path, *args, **kwargs):
            if path.name == 'RESPONSIBILITIES.md':
                self.assertEqual(locked, [True])
            return original(path, *args, **kwargs)
        real_lock = p.fcntl.flock
        def lock(fd, operation):
            self.assertEqual(operation, p.fcntl.LOCK_SH)
            locked.append(True)
            return real_lock(fd, operation)
        with patch.object(Path, 'open', opened), patch.object(p.fcntl, 'flock', lock):
            self.assertTrue(p.read_registry(self.root, self.root / 'RESPONSIBILITIES.md', fixture.CODEC)['ok'])

    def test_invalid_snapshots_fail_closed(self):
        variants = []
        for field, value in [('version', 2), ('owners', []), ('revision', 999), ('assignments', [])]:
            state = copy.deepcopy(self.state); state[field] = value; variants.append(state)
        state = copy.deepcopy(self.state); state['owners']['ops']['parent'] = 'owner:qa'; variants.append(state)
        state = copy.deepcopy(self.state); state['owners']['office']['id'] = 'another'; variants.append(state)
        state = copy.deepcopy(self.state); state['owners']['office']['identity']['source'] = 'intercom-status'; variants.append(state)
        state = copy.deepcopy(self.state); state['owners']['office']['parent'] = 'owner: ops'; variants.append(state)
        state = copy.deepcopy(self.state); state['assignments']['office-scope']['owner'] = 'no-such-owner'; variants.append(state)
        state = copy.deepcopy(self.state); state['history'][0]['revision'] = 99; variants.append(state)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'registry.lock').touch()
            for state in variants:
                if state.get('version') != 1 or not isinstance(state.get('owners'), dict) or not isinstance(state.get('assignments'), dict):
                    with self.assertRaises(ValueError):
                        p.project(state)
                    continue
                with patch.object(fixture.registry_document, 'validate_envelope'):
                    raw = fixture.registry_document.serialize_document(state, '00000000-0000-0000-0000-000000000001')
                (root / 'RESPONSIBILITIES.md').write_text(raw)
                result = p.read_registry(root, root / 'RESPONSIBILITIES.md', fixture.CODEC)
                self.assertFalse(result['ok']); self.assertEqual(result['owners'], [])
            for raw in ('{', '{"version":1,"version":1}', '{"version":NaN}'):
                (root / 'RESPONSIBILITIES.md').write_text(raw)
                self.assertFalse(p.read_registry(root, root / 'RESPONSIBILITIES.md', fixture.CODEC)['ok'])

    def test_valid_history_above_old_document_limit_stays_private(self):
        state = copy.deepcopy(self.state)
        snapshot = {kind: copy.deepcopy(state[kind]) for kind in ('owners', 'assignments', 'coordination')}
        state['history'] = []
        # Legal full-state audit snapshots model history growth, not whitespace padding.
        for revision in range(1, 257):
            after = copy.deepcopy(snapshot)
            after['owners']['ops']['role'] = f'PRIVATE archived coordination role {revision}'
            after['owners']['ops']['identity']['pi_session_id'] = f'HISTORY-ONLY-ID-{revision}'
            state['history'].append({'revision': revision, 'at': '2026-01-01T00:00:00+00:00',
                'actor': 'ops', 'action': 'update-owner', 'reason': f'Archived role review {revision}',
                'before': snapshot, 'after': after})
            snapshot = after
        state['revision'] = len(state['history'])
        raw = fixture.registry_document.serialize_document(state, '00000000-0000-0000-0000-000000000001').encode('utf-8')
        self.assertGreater(len(raw), 8 * 1024 * 1024)
        self.assertLess(len(raw), 64 * 1024 * 1024)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'registry.lock').touch()
            document = root / 'RESPONSIBILITIES.md'; document.write_bytes(raw)
            result = p.read_registry(root, document, fixture.CODEC)
        self.assertTrue(result['ok'], result.get('error'))
        self.assertEqual(result['revision'], 256)
        self.assertEqual(result['owners'], p.project(self.state)['owners'])
        self.assertLess(len(json.dumps(result).encode('utf-8')), 16 * 1024)
        for forbidden in ('PRIVATE', 'HISTORY-ONLY-ID', 'history', 'recovery'):
            self.assertNotIn(forbidden, json.dumps(result))

    def test_document_byte_boundary_is_bounded_before_codec_parse(self):
        self.assertEqual(p.MAX_DOCUMENT_BYTES, 64 * 1024 * 1024)
        raw = (self.root / 'RESPONSIBILITIES.md').read_bytes()
        self.assertGreater(len(raw), len(raw.decode('utf-8')))
        codec = fixture.registry_document
        real_parse = codec.parse_document
        reads = []
        original_open = Path.open
        class BoundedRead:
            def __init__(self, source): self.source = source
            def __enter__(self): return self
            def __exit__(self, *args): self.source.close()
            def read(self, size):
                reads.append(size)
                return self.source.read(size)
        def opened(path, *args, **kwargs):
            source = original_open(path, *args, **kwargs)
            return BoundedRead(source) if path.name == 'RESPONSIBILITIES.md' and args == ('rb',) else source
        with patch.object(p, 'load_codec', return_value=codec), patch.object(Path, 'open', opened):
            for limit in (len(raw) + 1, len(raw)):
                with self.subTest(limit=limit), patch.object(p, 'MAX_DOCUMENT_BYTES', limit), \
                        patch.object(codec, 'parse_document', wraps=real_parse) as parse:
                    self.assertTrue(p.read_registry(self.root, self.root / 'RESPONSIBILITIES.md', fixture.CODEC)['ok'])
                    parse.assert_called_once_with(raw.decode('utf-8'))
                    self.assertEqual(reads[-1], limit + 1)
            limit = len(raw) - 1
            with patch.object(p, 'MAX_DOCUMENT_BYTES', limit), patch.object(codec, 'parse_document') as parse:
                result = p.read_registry(self.root, self.root / 'RESPONSIBILITIES.md', fixture.CODEC)
                self.assertEqual(result, p.failure('document-too-large'))
                parse.assert_not_called()
                self.assertEqual(reads[-1], limit + 1)

    def test_owner_count_boundary_is_independent_of_document_size(self):
        state = copy.deepcopy(self.state)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'registry.lock').touch()
            document = root / 'RESPONSIBILITIES.md'
            for count in (500, 501):
                state['owners'] = {f'owner-{i}': {**self.state['owners']['ops'], 'id': f'owner-{i}', 'parent': 'user'}
                                   for i in range(count)}
                state['assignments'] = {}
                raw = fixture.registry_document.serialize_document(state, '00000000-0000-0000-0000-000000000001').encode('utf-8')
                self.assertLess(len(raw), p.MAX_DOCUMENT_BYTES)
                document.write_bytes(raw)
                result = p.read_registry(root, document, fixture.CODEC)
                with self.subTest(count=count):
                    self.assertEqual(result['ok'], count == 500)
                    self.assertEqual(len(result['owners']), 500 if count == 500 else 0)
                    if count > 500:
                        self.assertEqual(result, p.failure('invalid'))

    def test_depth_boundary_is_independent_of_document_size(self):
        state = copy.deepcopy(self.state)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'registry.lock').touch()
            document = root / 'RESPONSIBILITIES.md'
            for depth in (64, 65):
                state['owners'] = {f'owner-{i}': {**self.state['owners']['ops'], 'id': f'owner-{i}',
                    'parent': 'user' if i == 0 else f'owner:owner-{i - 1}'} for i in range(depth)}
                state['assignments'] = {}
                raw = fixture.registry_document.serialize_document(state, '00000000-0000-0000-0000-000000000001').encode('utf-8')
                self.assertLess(len(raw), p.MAX_DOCUMENT_BYTES)
                document.write_bytes(raw)
                result = p.read_registry(root, document, fixture.CODEC)
                with self.subTest(depth=depth):
                    self.assertEqual(result['ok'], depth == 64)
                    self.assertEqual(len(result['owners']), 64 if depth == 64 else 0)
                    if depth > 64:
                        self.assertEqual(result, p.failure('invalid'))

    def test_missing_store_does_not_create_anything(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'absent'
            self.assertFalse(p.read_registry(root, root / 'RESPONSIBILITIES.md', fixture.CODEC)['ok']); self.assertFalse(root.exists())

    def test_display_controls_clipped_and_text_retained(self):
        state = copy.deepcopy(self.state)
        state['owners']['office']['name'] = '<script>not HTML</script>\r\n|' + 'x' * 200
        result = next(o for o in p.project(state)['owners'] if o['id'] == 'office')
        self.assertLessEqual(len(result['name']), 160)
        self.assertNotIn('\n', result['name'])
        self.assertIn('<script>', result['name'])  # Browser uses textContent, never HTML.


if __name__ == '__main__':
    unittest.main()
