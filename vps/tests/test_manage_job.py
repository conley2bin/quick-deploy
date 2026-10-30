"""Isolated machine-adapter tests; never start a native fixture or contact SSH."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import manage
import vps

A, B, OP = 'a' * 64, 'b' * 64, 'c' * 32


class MachineJobs(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name)
        os.chmod(self.state, 0o700)
        self.document = {'native_generation': A, 'current': {'endpoint': 'unit'}, 'config': {}}
        self.manifest = {'accounts': {'company': {}, 'tablet': {}}}
        self.model = {'schema': 1, 'devices': [{'name': 'company'}, {'name': 'tablet'}], 'files': {}}
        self.vps = mock.Mock(wraps=vps)
        self.vps.SSH_PROXY = vps.SSH_PROXY
        self.vps.ssh_proxy = vps.ssh_proxy
        self.vps.state_document.side_effect = lambda *_: dict(self.document)
        self.vps.committed_dashboard_source.side_effect = lambda *_: (
            self.manifest, self.document['native_generation'], {})
        self.vps.dashboard_render.side_effect = lambda *_: (self.model, b'', {})
        self.vps.devices = mock.Mock()
        self.vps.recover_local = mock.Mock()
        self.vps.remote = mock.Mock(side_effect=AssertionError('No remote in units'))

    def tearDown(self):
        self.temp.cleanup()

    def envelope(self, **changes):
        return dict({'action': 'add', 'names': ['reader'], 'expected_generation': A}, **changes)

    def execute(self, envelope=None):
        with contextlib.redirect_stderr(io.StringIO()):
            return manage.machine_execute(self.vps, self.state, envelope or self.envelope())

    def pending(self):
        vps.host.atomic(self.state / 'pending.json', json.dumps({
            'mode': 'devices', 'request': {'operation_id': OP}}))

    def test_committed_reloads_verified_model(self):
        def commit(*_):
            self.document['native_generation'] = B
        self.vps.devices.side_effect = commit
        result = self.execute()
        self.assertEqual(result['outcome'], 'committed')
        self.assertEqual(result['generation_after'], B)
        self.assertEqual(result['model'], self.model)
        self.vps.locked.assert_called_once_with(self.state)

    def test_export_error_after_commit_is_not_rollback(self):
        def commit(*_):
            self.document['native_generation'] = B
            raise RuntimeError('https://secret.invalid/credential')
        self.vps.devices.side_effect = commit
        result = self.execute()
        self.assertEqual(result['outcome'], 'committed_export_failed')
        self.assertEqual(result['model'], self.model)
        self.assertNotIn('secret.invalid', json.dumps(result))

    def test_pending_failure_suppresses_material(self):
        def uncertain(*_):
            self.pending()
            raise RuntimeError('lost ACK')
        self.vps.devices.side_effect = uncertain
        result = self.execute()
        self.assertEqual(result['outcome'], 'recovery_required')
        self.assertIsNone(result['model'])

    def test_recover_exit_zero_but_queued_still_required(self):
        self.pending()
        result = self.execute({'action': 'recover', 'expected_operation_id': OP})
        self.assertEqual(result['outcome'], 'recovery_required')
        self.vps.recover_local.assert_called_once()

    def test_recover_rolled_back_only_when_pending_cleared(self):
        self.pending()
        self.vps.recover_local.side_effect = lambda *_: (self.state / 'pending.json').unlink()
        result = self.execute({'action': 'recover', 'expected_operation_id': OP})
        self.assertEqual(result['outcome'], 'rolled_back')

    def backup_pending(self):
        vps.host.atomic(self.state / 'pending.json', json.dumps({'mode': 'backup'}))
        return manage.pending_info(vps, self.state)

    def recover_backup(self, new_digest=B, export_error=False):
        info = self.backup_pending()
        def refresh(*_):
            (self.state / 'pending.json').unlink()
            self.document['native_generation'] = new_digest
            if export_error:
                raise RuntimeError('https://secret.invalid/export')
        self.vps.recover_local.side_effect = refresh
        return info, self.execute({'action': 'recover', 'expected_operation_id': info['operation_id']})

    def test_backup_pending_without_operation_id_has_derived_recovery_id(self):
        info = self.backup_pending()
        self.assertEqual(info['mode'], 'backup')
        self.assertRegex(info['operation_id'], '^[0-9a-f]{32}$')
        self.assertEqual(info, manage.pending_info(vps, self.state))

    def test_backup_recover_stale_id_refuses_without_recovery(self):
        info = self.backup_pending()
        stale = '0' * 32 if info['operation_id'] != '0' * 32 else '1' * 32
        result = self.execute({'action': 'recover', 'expected_operation_id': stale})
        self.assertEqual(result['error']['code'], 'pending_mismatch')
        self.vps.recover_local.assert_not_called()
        self.assertTrue((self.state / 'pending.json').exists())

    def test_backup_recover_refreshed_snapshot_is_never_committed(self):
        _, result = self.recover_backup()
        self.assertEqual(result['recovered_mode'], 'backup')
        self.assertEqual(result['outcome'], 'unchanged')
        self.assertEqual(result['recovery_effect'], 'snapshot_refreshed')
        self.assertEqual(result['generation_after'], B)
        self.assertFalse(result['pending']['present'])

    def test_backup_recover_export_error_after_refresh_is_not_committed(self):
        _, result = self.recover_backup(export_error=True)
        self.assertEqual(result['recovered_mode'], 'backup')
        self.assertNotIn(result['outcome'], {'committed', 'committed_export_failed'})
        self.assertEqual(result['outcome'], 'unchanged')
        self.assertEqual(result['error']['code'], 'operation_failed')
        self.assertNotIn('secret.invalid', json.dumps(result))

    def test_backup_recover_presentation_failure_is_explicit_not_committed(self):
        self.vps.dashboard_render.side_effect = RuntimeError('render')
        _, result = self.recover_backup()
        self.assertEqual(result['outcome'], 'unchanged')
        self.assertEqual(result['error']['code'], 'presentation_failed')
        self.assertIsNone(result['model'])

    def test_pending_stale_last_device_and_bad_envelope_refuse(self):
        for envelope, code in [(self.envelope(expected_generation=B), 'stale_generation'),
                               (self.envelope(extra='not allowed'), 'invalid_envelope')]:
            self.assertEqual(self.execute(envelope)['error']['code'], code)
        self.pending()
        self.assertEqual(self.execute()['error']['code'], 'pending')
        (self.state / 'pending.json').unlink()
        self.manifest['accounts'] = {'company': {}}
        self.assertEqual(self.execute(self.envelope(action='remove', names=['company']))['error']['code'],
                         'last_device_scope')
        self.vps.devices.assert_not_called()
        self.vps.remote.assert_not_called()

    def test_generation_checked_after_lock_acquisition(self):
        @contextlib.contextmanager
        def changed_under_lock(_):
            self.document['native_generation'] = B
            yield
        self.vps.locked.side_effect = changed_under_lock
        self.assertEqual(self.execute()['error']['code'], 'stale_generation')
        self.vps.devices.assert_not_called()

    def test_real_flock_busy_refuses_without_native_call(self):
        with vps.locked(self.state):
            self.assertEqual(self.execute()['error']['code'], 'busy')
        self.vps.devices.assert_not_called()

    def test_cli_bounded_stdin_and_single_json_stdout(self):
        for body in [b'x' * (manage.MAX_BODY + 1), b'{"action":"state","action":"add"}']:
            result = subprocess.run([sys.executable, '-B', str(vps.MODULE / 'lib/vps.py'),
                                     '--state-dir', str(self.state), 'manage-job'],
                                    input=body, capture_output=True, timeout=10)
            self.assertEqual(json.loads(result.stdout)['error']['code'], 'invalid_envelope')


if __name__ == '__main__':
    unittest.main()
