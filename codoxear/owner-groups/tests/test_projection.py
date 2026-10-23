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

    def test_read_only_including_dashboard_lock_and_modes(self):
        def snapshot():
            return {f.name: (f.read_bytes(), f.stat().st_mtime_ns, f.stat().st_mode, f.stat().st_ino)
                    for f in self.root.iterdir() if f.is_file()}
        before = snapshot()
        self.assertTrue(p.read_registry(self.root)['ok'])
        self.assertEqual(snapshot(), before)

    def test_lock_precedes_snapshot_read(self):
        original = Path.open
        locked = []
        def opened(path, *args, **kwargs):
            if path.name == 'registry.json':
                self.assertEqual(locked, [True])
            return original(path, *args, **kwargs)
        real_lock = p.fcntl.flock
        def lock(fd, operation):
            self.assertEqual(operation, p.fcntl.LOCK_SH)
            locked.append(True)
            return real_lock(fd, operation)
        with patch.object(Path, 'open', opened), patch.object(p.fcntl, 'flock', lock):
            self.assertTrue(p.read_registry(self.root)['ok'])

    def test_invalid_snapshots_fail_closed(self):
        variants = []
        for field, value in [('version', 2), ('owners', []), ('revision', 999), ('assignments', [])]:
            state = copy.deepcopy(self.state); state[field] = value; variants.append(state)
        state = copy.deepcopy(self.state); state['owners']['ops']['parent'] = 'owner:qa'; variants.append(state)
        state = copy.deepcopy(self.state); state['owners']['office']['id'] = 'another'; variants.append(state)
        state = copy.deepcopy(self.state); state['owners']['office']['identity']['source'] = 'intercom-status'; variants.append(state)
        state = copy.deepcopy(self.state); state['owners']['office']['parent'] = 'owner: ops'; variants.append(state)
        state = copy.deepcopy(self.state); state['assignments']['office-scope']['owner'] = 'no-such-owner'; variants.append(state)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'registry.lock').touch()
            for state in variants:
                (root / 'registry.json').write_text(json.dumps(state))
                result = p.read_registry(root)
                self.assertFalse(result['ok']); self.assertEqual(result['owners'], [])
            for raw in ('{', '{"version":1,"version":1}', '{"version":NaN}'):
                (root / 'registry.json').write_text(raw)
                self.assertFalse(p.read_registry(root)['ok'])

    def test_missing_store_does_not_create_anything(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'absent'
            self.assertFalse(p.read_registry(root)['ok']); self.assertFalse(root.exists())

    def test_display_controls_clipped_and_text_retained(self):
        state = copy.deepcopy(self.state)
        state['owners']['office']['name'] = '<script>not HTML</script>\r\n|' + 'x' * 200
        result = next(o for o in p.project(state)['owners'] if o['id'] == 'office')
        self.assertLessEqual(len(result['name']), 160)
        self.assertNotIn('\n', result['name'])
        self.assertIn('<script>', result['name'])  # Browser uses textContent, never HTML.


if __name__ == '__main__':
    unittest.main()
