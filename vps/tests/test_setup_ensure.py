"""`setup.sh` with no command (ensure): fake probe and fake transports only; no SSH, native API, systemd or browser.

The remote is a dispatcher that knows only `ensure-probe`; any other remote action, backup, prepare or commit from a
skip path raises AssertionError, so a healthy repeat that touches the server beyond the read-only probe fails here.
"""
import argparse
import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))

import connect
import host
import setup_ensure
import target_config
import vps

REAL_MODULE = Path(__file__).resolve().parents[1]
CONFIG = {'domain': 'sub.example.net', 'reality_target': 'www.python.org', 'acme_email': '', 'proxy_port': 443}
DUAL = ['tcp-reality', 'hysteria2']
TRIPLE = ['tcp-reality', 'tcp-xhttp-cdn', 'hysteria2']
SINGLE = ['tcp-reality']
IPS = {'root@1.1.1.1': '1.1.1.1', 'root@2.2.2.2': '2.2.2.2'}
MANIFEST = {'published': True, 'node_id': 'n1', 'config': CONFIG, 'owner': 'o1', 'accounts': {'a': {}}}


def fake_digest(accounts):
    return 'rd-' + ','.join(sorted(accounts))


def probe(**changes):
    result = {'kind': 'owned', 'owner': 'o1', 'machine_id': 'm1', 'node_id': 'n1', 'config': dict(CONFIG), 'transports': DUAL,
              'tcp_cdn': None, 'versions': {'panel': '3.10.2'}, 'lock_match': True,
              'adapter_sha256': host.digest(REAL_MODULE / 'lib/host.py'), 'front_drift': False,
              'health': {'ok': True, 'failed_check': None, 'error': None}, 'roster_digest': fake_digest(['a'])}
    result.update(changes)
    return result


def unhealthy(check, drift=False):
    return probe(health={'ok': False, 'failed_check': check, 'error': 'boom'}, front_drift=drift)


class Ensure(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state = self.tmp / 'state'
        self.module = self.tmp / 'module'
        self.module.mkdir()
        (self.module / 'lib').symlink_to(REAL_MODULE / 'lib')
        (self.module / 'release-lock.json').symlink_to(REAL_MODULE / 'release-lock.json')
        self.calls = []
        self.probe_value = probe()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(vps, 'MODULE', self.module))
        self.stack.enter_context(mock.patch.object(vps, 'target_record', self.target_record))
        self.stack.enter_context(mock.patch.object(vps, 'remote', self.remote))
        self.stack.enter_context(mock.patch.object(vps, 'checked_backup', lambda document, record, backup: dict(MANIFEST)))
        self.stack.enter_context(mock.patch.object(vps, 'read_generation', lambda *a: {}))
        self.stack.enter_context(mock.patch.object(vps, 'resolve_mihomo', self.resolve))
        self.stack.enter_context(mock.patch.object(setup_ensure, 'roster_digest', fake_digest))
        self.transports = self.stack.enter_context(mock.patch.object(host, 'transports', return_value=DUAL))
        self.mihomo = '/bin/mihomo'
        self.recover_effect = lambda state, document, mihomo: (state / 'pending.json').unlink()
        self.mutators = {name: self.stack.enter_context(mock.patch.object(vps, name, side_effect=self.record_call(name)))
                         for name in ('deploy', 'deploy_existing', 'backup_remote', 'remember_commit')}
        self.mutators['recover_local'] = self.stack.enter_context(
            mock.patch.object(vps, 'recover_local', side_effect=lambda *a: self.recover_effect(*a)))
        self.mutators['attach'] = self.stack.enter_context(mock.patch.object(connect, 'run', side_effect=self.attach))
        self.write_config(b'VPS_SSH=root@1.1.1.1\nDOMAIN=example.net\nREALITY_TARGET=www.python.org\n')

    # fakes -----------------------------------------------------------------------------------------------------
    def record_call(self, name):
        return lambda *a, **k: self.calls.append(name)

    def target_record(self, args, current=None):
        endpoint = IPS[args.ssh]
        record = {'target': args.ssh, 'options': [], 'endpoint': endpoint, 'user': 'root', 'port': 22, 'host_key_alias': None}
        if current and current['endpoint'] == endpoint and 'machine_id' in current:
            record['machine_id'] = current['machine_id']
        return record

    def remote(self, record, action, request=None, **kw):
        self.calls.append(action)
        if action != 'ensure-probe':
            raise AssertionError('unexpected server action ' + action)
        self.request = request
        value = self.probe_value
        if isinstance(value, Exception):
            raise value
        return value

    def resolve(self, binary=None):
        if binary is None and self.mihomo is None:
            raise host.Error('Install native Mihomo')
        if binary is not None and not Path(binary).exists() and binary != '/bin/mihomo':
            raise host.Error('Install native Mihomo')
        return binary or self.mihomo

    def attach(self, vps_module, state, args):
        self.calls.append('attach')
        self.attached_with = args.mihomo
        if self.attach_error:
            raise self.attach_error
        self.save(self.document())

    attach_error = None

    # state helpers ----------------------------------------------------------------------------------------------
    def write_config(self, data):
        (self.module / 'config').write_bytes(data)

    def document(self, **changes):
        backup = self.tmp / 'backup.tar'
        backup.write_bytes(b'archive')
        current = {'target': 'root@1.1.1.1', 'endpoint': '1.1.1.1', 'user': 'root', 'port': 22, 'options': [],
                   'host_key_alias': None, 'machine_id': 'm1', 'node_id': 'n1'}
        result = {'format': host.FORMAT, 'owner': 'o1', 'config': dict(CONFIG), 'current': current, 'previous': [],
                  'backup': str(backup), 'native_generation': host.digest(backup)}
        result.update(changes)
        return result

    def save(self, document):
        self.state.mkdir(mode=0o700, exist_ok=True)
        host.atomic(self.state / 'state.json', host.dumps(document))

    def pend(self, endpoint='1.1.1.1'):
        self.state.mkdir(mode=0o700, exist_ok=True)
        record = {'target': 'root@' + endpoint, 'endpoint': endpoint, 'user': 'root', 'port': 22, 'options': [],
                  'host_key_alias': None, 'machine_id': 'm1'}
        host.atomic(self.state / 'pending.json', host.dumps({'record': record, 'mode': 'backup'}))

    def run_ensure(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch('sys.stdout', out), mock.patch('sys.stderr', err):
            code = vps.main(['--deployment-only', '--state-dir', str(self.state), *extra])
        return code, out.getvalue(), err.getvalue()

    def assertNoMutation(self):
        for name in ('deploy', 'deploy_existing', 'backup_remote', 'remember_commit', 'recover_local', 'attach'):
            self.assertEqual(self.mutators[name].call_count, 0, name)

    # healthy / skip ---------------------------------------------------------------------------------------------
    def test_healthy_current_cache_skips_everything_but_the_probe(self):
        self.save(self.document())
        code, out, _ = self.run_ensure()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.calls, ['ensure-probe'])
        self.assertNoMutation()
        self.assertIn('[跳过]', out)
        self.assertEqual(sorted(self.request), ['adapter_sha256', 'endpoint', 'lock', 'machine_id', 'owner'])
        self.assertEqual(self.request['endpoint'], '1.1.1.1')
        self.assertNotIn('config', json.dumps(self.request))

    def test_triple_topology_is_kept_and_skipped(self):
        self.save(self.document())
        self.probe_value = probe(transports=TRIPLE, endpoint='1.1.1.1', tcp_cdn={'schema': 1, 'domain': 'cdn.example.net'})
        self.transports.return_value = TRIPLE
        code, out, _ = self.run_ensure()
        self.assertEqual((code, self.calls), (0, ['ensure-probe']))
        self.assertNoMutation()

    def test_triple_cdn_domain_change_makes_cache_stale(self):
        self.save(self.document())
        self.probe_value = probe(transports=TRIPLE, endpoint='1.1.1.1', tcp_cdn={'schema': 1, 'domain': 'cdn.example.net'})
        self.transports.return_value = TRIPLE
        with mock.patch.object(vps, 'checked_backup', lambda *a: dict(MANIFEST, tcp_cdn={'domain': 'cdn.old.net'})):
            code, _, _ = self.run_ensure()
        self.assertEqual((code, self.calls), (0, ['ensure-probe', 'attach']))

    def test_stale_cache_variants_refresh_through_readonly_connect(self):
        variants = {
            'roster': lambda: setattr(self, 'probe_value', probe(roster_digest='rd-other')),
            'topology': lambda: setattr(self.transports, 'return_value', TRIPLE),
            'no_archive': lambda: self.save(self.document(backup=None)),
            'generation_missing': lambda: self.save(self.document(native_generation=None)),
            'generation_other': lambda: self.save(self.document(native_generation='0' * 64)),
            'machine_unrecorded': lambda: self.save(self.document(current=dict(self.document()['current'], machine_id=None))),
        }
        for name, change in variants.items():
            with self.subTest(name):
                self.setUp()
                self.save(self.document())
                change()
                code, out, _ = self.run_ensure()
                self.assertEqual((code, self.calls), (0, ['ensure-probe', 'attach']), out)
                self.assertEqual(self.mutators['deploy'].call_count + self.mutators['deploy_existing'].call_count, 0)

    def test_archive_unreadable_is_stale_not_error(self):
        self.save(self.document())
        with mock.patch.object(vps, 'checked_backup', side_effect=host.Error('bad archive')):
            code, _, _ = self.run_ensure()
        self.assertEqual((code, self.calls), (0, ['ensure-probe', 'attach']))

    def test_no_local_state_attaches_readonly(self):
        code, out, _ = self.run_ensure()
        self.assertEqual((code, self.calls), (0, ['ensure-probe', 'attach']))
        self.assertEqual(self.attached_with, '/bin/mihomo')

    def test_missing_default_mihomo_only_reports_unattached_success(self):
        self.mihomo = None
        code, out, _ = self.run_ensure()
        self.assertEqual((code, self.calls), (0, ['ensure-probe']))
        self.assertIn('未附着', out)
        self.assertNoMutation()

    def test_explicit_missing_mihomo_is_an_error(self):
        code, _, err = self.run_ensure('--mihomo', '/nonexistent/mihomo')
        self.assertEqual(code, 1)
        self.assertNotIn('attach', self.calls)

    def test_attach_failures_are_not_swallowed_as_success(self):
        for error in (host.Error('SSH auth failed'), host.Error('Server changed or outputs disagree'), OSError('disk')):
            with self.subTest(error=str(error)):
                self.setUp()
                self.attach_error = error
                code, out, err = self.run_ensure()
                self.assertEqual(code, 1, out)
                self.assertNotIn('已刷新', out)

    # repair ----------------------------------------------------------------------------------------------------------
    def test_front_drift_uses_existing_transaction_with_local_document(self):
        self.save(self.document())
        self.probe_value = unhealthy('front', drift=True)
        code, out, _ = self.run_ensure()
        self.assertEqual((code, self.calls), (0, ['ensure-probe', 'deploy_existing']))
        document, record, args = self.mutators['deploy_existing'].call_args.args[1:]
        self.assertEqual((document['owner'], record['endpoint'], args.command), ('o1', '1.1.1.1', 'deploy'))

    def test_front_drift_without_local_state_repairs_from_probe_document_without_attach(self):
        self.probe_value = probe(front_drift=True, transports=TRIPLE)
        code, _, _ = self.run_ensure()
        self.assertEqual((code, self.calls), (0, ['ensure-probe', 'deploy_existing']))
        document, record, args = self.mutators['deploy_existing'].call_args.args[1:]
        self.assertEqual((document['owner'], document['config'], document['previous'], document['backup']),
                         ('o1', CONFIG, [], None))
        self.assertEqual(document['current'], dict(record, machine_id='m1', node_id='n1'))
        self.assertEqual(vps.request_for(document, record)['machine_id'], 'm1')

    def test_front_drift_without_local_state_needs_a_real_core_before_any_repair(self):
        self.mihomo = None
        self.probe_value = probe(front_drift=True)
        self.assertEqual(self.run_ensure()[0], 1)
        self.assertEqual(self.calls, ['ensure-probe'])

    def test_legacy_single_without_state_still_attaches_first(self):
        self.probe_value = probe(transports=SINGLE)
        code, _, _ = self.run_ensure()
        self.assertEqual((code, self.calls), (0, ['ensure-probe', 'attach', 'deploy_existing']))

    def test_legacy_single_transport_goes_through_deploy_existing(self):
        self.save(self.document())
        self.probe_value = probe(transports=SINGLE)
        code, _, _ = self.run_ensure()
        self.assertEqual((code, self.calls), (0, ['ensure-probe', 'deploy_existing']))

    def test_drift_with_another_failed_check_is_reported_not_repaired(self):
        self.save(self.document())
        self.probe_value = unhealthy('tls', drift=True)
        code, _, err = self.run_ensure()
        self.assertEqual(code, 1)
        self.assertIn('tls', err)
        self.assertNoMutation()

    def test_unhealthy_runtime_is_never_repaired(self):
        for check in ('ownership', 'binary', 'native', 'tls', 'process', 'adapter', 'artifacts'):
            with self.subTest(check):
                self.setUp()
                self.save(self.document())
                self.probe_value = unhealthy(check)
                code, _, err = self.run_ensure()
                self.assertEqual((code, self.calls), (1, ['ensure-probe']))
                self.assertIn(check, err)
                self.assertNoMutation()

    def test_lock_or_adapter_or_topology_mismatch_refuses(self):
        for change in ({'lock_match': False}, {'adapter_sha256': 'f' * 64}, {'transports': ['hysteria2']}):
            with self.subTest(change):
                self.setUp()
                self.save(self.document())
                self.probe_value = probe(**change)
                self.assertEqual(self.run_ensure()[0], 1)
                self.assertEqual(self.calls, ['ensure-probe'])
                self.assertNoMutation()

    # fresh install ---------------------------------------------------------------------------------------------------
    def test_absent_server_runs_existing_deploy_with_config_defaults(self):
        self.probe_value = probe(kind='absent', owner=None, machine_id=None, node_id=None, config=None, transports=[])
        code, out, _ = self.run_ensure()
        self.assertEqual((code, self.calls), (0, ['ensure-probe', 'deploy']))
        document, args = self.mutators['deploy'].call_args.args[1:]
        self.assertEqual(document['config'], CONFIG)
        self.assertEqual((args.command, args.ssh, args.domain, args.reality_target),
                         ('deploy', 'root@1.1.1.1', 'sub.example.net', 'www.python.org'))
        self.assertIn('CDN', out)

    def test_absent_requires_reality_target_before_any_mutation(self):
        self.write_config(b'VPS_SSH=root@1.1.1.1\nDOMAIN=example.net\n')
        self.probe_value = probe(kind='absent')
        code, _, err = self.run_ensure()
        self.assertEqual((code, self.calls), (1, ['ensure-probe']))
        self.assertIn('REALITY_TARGET', err)
        self.assertNoMutation()
        self.assertFalse((self.state / 'state.json').exists())

    def test_absent_without_core_fails_before_deploy(self):
        self.mihomo = None
        self.probe_value = probe(kind='absent')
        self.assertEqual(self.run_ensure()[0], 1)
        self.assertNoMutation()

    def test_absent_with_old_committed_binding_means_restore_guidance(self):
        self.save(self.document())
        self.probe_value = probe(kind='absent')
        code, _, err = self.run_ensure()
        self.assertEqual((code, self.calls), (1, ['ensure-probe']))
        self.assertIn('restore --backup', err)
        self.assertNoMutation()

    def test_resumable_owned_empty_needs_matching_local_owner(self):
        self.save(self.document(current=None, backup=None))
        self.probe_value = probe(kind='owned_empty', node_id=None, config=None, transports=[])
        code, _, _ = self.run_ensure()
        self.assertEqual((code, self.calls), (0, ['ensure-probe', 'deploy']))
        self.assertEqual(self.mutators['deploy'].call_args.args[1]['owner'], 'o1')

    def test_owned_empty_foreign_or_unbound_refuses(self):
        for name, setup in {'no_state': lambda: None, 'other_owner': lambda: self.save(self.document(current=None, owner='zz')),
                            'old_binding': lambda: self.save(self.document())}.items():
            with self.subTest(name):
                self.setUp()
                setup()
                self.probe_value = probe(kind='owned_empty', node_id=None, config=None, transports=[])
                self.assertEqual(self.run_ensure()[0], 1)
                self.assertEqual(self.calls, ['ensure-probe'])
                self.assertNoMutation()

    # conflicts -------------------------------------------------------------------------------------------------------
    def test_server_config_conflicts_refuse(self):
        self.save(self.document())
        for config in (dict(CONFIG, domain='sub.other.org'), dict(CONFIG, reality_target='other.example.org')):
            with self.subTest(config):
                self.calls.clear()
                self.probe_value = probe(config=config)
                self.assertEqual(self.run_ensure()[0], 1)
                self.assertNoMutation()

    def test_existing_server_target_is_authority_when_config_omits_it(self):
        self.write_config(b'VPS_SSH=root@1.1.1.1\nDOMAIN=example.net\n')
        self.save(self.document())
        self.assertEqual(self.run_ensure()[0], 0)
        self.assertEqual(self.calls, ['ensure-probe'])

    def test_local_config_conflict_refuses_before_remote(self):
        self.save(self.document(config=dict(CONFIG, reality_target='old.example.org')))
        self.assertEqual(self.run_ensure()[0], 1)
        self.save(self.document(config=dict(CONFIG, domain='sub.old.org')))
        self.assertEqual(self.run_ensure()[0], 1)
        self.assertEqual(self.calls, [])

    def test_foreign_owner_machine_node_refuse_without_mutation(self):
        self.save(self.document())
        for change in ({'owner': 'other'}, {'machine_id': 'm2'}, {'node_id': 'n2'}):
            with self.subTest(change):
                self.calls.clear()
                self.probe_value = probe(**change)
                code, _, err = self.run_ensure()
                self.assertEqual(code, 1)
                self.assertEqual(self.calls, ['ensure-probe'])
                self.assertNoMutation()

    def test_foreign_probe_refuses(self):
        self.probe_value = probe(kind='foreign', health={'ok': False, 'failed_check': 'ownership', 'error': 'unknown directory'})
        code, _, err = self.run_ensure()
        self.assertEqual((code, self.calls), (1, ['ensure-probe']))
        self.assertIn('unknown directory', err)
        self.assertNoMutation()

    def test_different_configured_vps_refuses_before_remote_or_recovery(self):
        self.save(self.document())
        self.write_config(b'VPS_SSH=root@2.2.2.2\nDOMAIN=example.net\nREALITY_TARGET=www.python.org\n')
        code, _, err = self.run_ensure()
        self.assertEqual((code, self.calls), (1, []))
        self.assertIn('VPS_SSH', err)
        self.pend()
        self.assertEqual(self.run_ensure()[0], 1)
        self.assertEqual(self.calls, [])
        self.assertNoMutation()

    def test_pending_for_a_different_endpoint_is_not_recovered(self):
        self.save(self.document())
        self.pend('2.2.2.2')
        self.assertEqual(self.run_ensure()[0], 1)
        self.assertEqual(self.calls, [])
        self.assertNoMutation()

    # pending / busy ---------------------------------------------------------------------------------------------------
    def test_matching_local_pending_recovers_then_reprobes_once(self):
        self.save(self.document())
        self.pend()
        order = []
        self.recover_effect = lambda state, document, mihomo: (order.append('recover'), (state / 'pending.json').unlink())
        code, out, _ = self.run_ensure()
        self.assertEqual(code, 0, out)
        self.assertEqual(order, ['recover'])
        self.assertEqual(self.calls, ['ensure-probe'])
        self.assertEqual(self.mutators['recover_local'].call_count, 1)

    def test_unpublished_after_local_recovery_is_also_a_refusal(self):
        self.save(self.document())
        self.pend()
        self.recover_effect = lambda state, document, mihomo: (state / 'pending.json').unlink()
        self.probe_value = probe(kind='pending', health={'ok': False, 'failed_check': 'unpublished', 'error': None})
        code, _, err = self.run_ensure()
        self.assertEqual((code, self.calls, self.mutators['recover_local'].call_count), (1, ['ensure-probe'], 1))
        self.assertIn('未发布但缺事务记录', err)
        self.probe_value = probe(kind='pending', health={'ok': False, 'failed_check': 'busy', 'error': None})
        self.pend()
        self.assertEqual(self.run_ensure()[0], 75)

    def test_queued_local_recovery_returns_75_without_probe(self):
        self.save(self.document())
        self.pend()
        self.recover_effect = lambda *a: None
        code, _, err = self.run_ensure()
        self.assertEqual((code, self.calls), (75, []))
        self.assertTrue((self.state / 'pending.json').exists())

    def test_remote_still_pending_after_recovery_returns_75(self):
        self.save(self.document())
        self.pend()
        self.probe_value = probe(kind='pending', health={'ok': False, 'failed_check': 'pending', 'error': 'device-rollback'})
        self.assertEqual(self.run_ensure()[0], 75)
        self.assertEqual(self.calls, ['ensure-probe'])

    def test_remote_pending_or_busy_without_local_pending_returns_75_and_never_aborts(self):
        self.save(self.document())
        for check in ('pending', 'busy', 'backup-stop'):
            with self.subTest(check):
                self.calls.clear()
                self.probe_value = probe(kind='pending', health={'ok': False, 'failed_check': check, 'error': None})
                code, _, err = self.run_ensure()
                self.assertEqual((code, self.calls), (75, ['ensure-probe']))
                self.assertIn(check, err)
                self.assertNoMutation()

    def test_unpublished_without_intent_is_a_refusal_not_a_retry(self):
        for local in (False, True):
            with self.subTest(local_state=local):
                self.calls.clear()
                if local:
                    self.save(self.document())
                self.probe_value = probe(kind='pending', health={'ok': False, 'failed_check': 'unpublished', 'error': None})
                code, _, err = self.run_ensure()
                self.assertEqual((code, self.calls), (1, ['ensure-probe']))
                self.assertIn('未发布但缺事务记录，无法自动恢复，需检查原部署/备份', err)
                self.assertNoMutation()

    def test_pending_without_state_document_is_refused(self):
        self.pend()
        self.assertEqual(self.run_ensure()[0], 1)
        self.assertEqual(self.calls, [])
        self.assertNoMutation()

    # locks / probe shape ------------------------------------------------------------------------------------------------
    def test_held_foreground_manager_session_refuses_without_touching_anything(self):
        jobs = self.state / 'manage-jobs'
        jobs.mkdir(parents=True, mode=0o700)
        fd = os.open(jobs / '.session.lock', os.O_CREAT | os.O_RDWR, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        code, _, err = self.run_ensure()
        self.assertEqual((code, self.calls), (1, []))
        self.assertIn('manage.sh', err)
        self.assertTrue((jobs / '.session.lock').exists())

    def test_session_lease_is_held_for_the_whole_run_and_released_after(self):
        self.save(self.document())
        held = []

        def check(record, action, request=None, **kw):
            fd = os.open(self.state / 'manage-jobs/.session.lock', os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held.append(False)
            except BlockingIOError:
                held.append(True)
            finally:
                os.close(fd)
            return probe()
        with mock.patch.object(vps, 'remote', check):
            self.assertEqual(self.run_ensure()[0], 0)
        self.assertEqual(held, [True])
        fd = os.open(self.state / 'manage-jobs/.session.lock', os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # released
        os.close(fd)

    def test_malformed_probe_and_transport_errors_fail_closed(self):
        self.save(self.document())
        for value in ({}, probe(kind='mystery'), {k: v for k, v in probe().items() if k != 'health'},
                      probe(health={'ok': 'yes', 'failed_check': None, 'error': None}),
                      host.Error('SSH operation failed')):
            with self.subTest(value=str(value)[:40]):
                self.calls.clear()
                self.probe_value = value
                self.assertEqual(self.run_ensure()[0], 1)
                self.assertNoMutation()

    def test_unexpected_exception_is_not_converted_to_success(self):
        self.save(self.document())
        self.probe_value = RuntimeError('bug')
        with self.assertRaises(RuntimeError):
            self.run_ensure()


class RealRepair(unittest.TestCase):
    """Fresh computer + pure front drift through the REAL deploy_existing / backup_remote / remember_commit.
    Fakes: the SSH `remote`, artifact upload and the archive/native-output validators (no download, no Mihomo)."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state = self.tmp / 'state'
        self.module = self.tmp / 'module'
        self.module.mkdir()
        (self.module / 'lib').symlink_to(REAL_MODULE / 'lib')
        (self.module / 'release-lock.json').symlink_to(REAL_MODULE / 'release-lock.json')
        (self.module / 'config').write_bytes(b'VPS_SSH=root@1.1.1.1\nDOMAIN=example.net\nREALITY_TARGET=www.python.org\n')
        self.calls, self.repaired, self.fail_at, self.artifacts_dirs = [], False, None, []
        self.probe_value = probe(front_drift=True, transports=TRIPLE, tcp_cdn={'domain': 'cdn.example.net'},
                                 health={'ok': False, 'failed_check': 'front', 'error': 'Caddyfile drift'})
        self.manifest = dict(MANIFEST, endpoint='1.1.1.1', tcp_cdn={'schema': 1, 'domain': 'cdn.example.net', 'reality_port': 8443})
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for name, value in (('MODULE', self.module), ('remote', self.remote), ('resolve_mihomo', lambda b=None: b or '/bin/mihomo'),
                            ('artifacts', self.artifacts), ('upload_bundle', self.upload),
                            ('inspect_backup', self.inspect), ('checked_backup', lambda *a: self.manifest),
                            ('validate_outputs', self.validate), ('read_generation', lambda *a: {}), ('sync_outputs', lambda *a: None), ('export_after_commit', lambda state: None),
                            ('target_record', Ensure.target_record.__get__(self))):
            stack.enter_context(mock.patch.object(vps, name, value))
        stack.enter_context(mock.patch.object(host, 'cdn_enabled', return_value=True))
        stack.enter_context(mock.patch.object(setup_ensure, 'roster_digest', fake_digest))
        stack.enter_context(mock.patch.object(connect, 'run', side_effect=lambda *a: self.calls.append('attach')))

    def artifacts(self, state, lock, directory=None):
        self.artifacts_dirs.append(directory)
        return self.tmp

    @contextlib.contextmanager
    def upload(self, request, folder=None, backup=None):
        yield io.BytesIO(b'bundle')

    def inspect(self, path, lock):
        """The pre-repair archive carries the Caddyfile drift and is rejected, exactly like verify_backup."""
        if not self.repaired:
            raise host.Error('Caddyfile drift')
        return self.manifest

    def validate(self, state, document, record, binary=None, backup=None):
        self.calls.append('outputs')
        (self.state / 'generations' / host.digest(backup)).mkdir(parents=True)  # what store_generation leaves

    def remote(self, record, action, request=None, stdin=None, stdout=None, **kw):
        self.calls.append(action)
        if action == 'ensure-probe':
            return self.probe_value
        if action == 'connect-pull':
            raise AssertionError('connect-pull of the drifted archive')
        if action == self.fail_at:
            raise host.Error('boom ' + action)
        if action == 'status':
            return {'adapter_sha256': host.digest(REAL_MODULE / 'lib/host.py'), 'needs_transport_upgrade': False,
                    'transports': TRIPLE, 'machine_id': 'm1', 'node_id': 'n1'}
        if action == 'prepare':
            self.repaired = True
            return {'unchanged': True}
        if action == 'backup':
            stdout.write(b'archive')
            return None
        raise AssertionError('unexpected action ' + action)

    def ensure(self, artifacts_dir=None):
        args = argparse.Namespace(ssh='root@1.1.1.1', domain='sub.example.net', reality_target='www.python.org',
                                  mihomo=None, ssh_proxy=None, command='ensure', artifacts_dir=artifacts_dir, acme_email='',
                                  proxy_port=443)
        with mock.patch('sys.stdout', io.StringIO()), mock.patch('sys.stderr', io.StringIO()):
            return setup_ensure.run(vps, self.state, args)

    def test_pure_front_drift_repairs_directly_and_publishes_the_post_repair_archive(self):
        self.assertEqual(self.ensure(), 0)
        self.assertEqual(self.calls, ['ensure-probe', 'status', 'prepare', 'backup', 'outputs', 'status'])
        document = json.loads((self.state / 'state.json').read_text())
        self.assertEqual((document['owner'], document['current']['machine_id'], document['tcp_cdn']['domain']),
                         ('o1', 'm1', 'cdn.example.net'))
        self.assertTrue(Path(document['backup']).exists())
        self.assertFalse((self.state / 'pending.json').exists())

    def test_artifacts_dir_reaches_the_pinned_file_verification_of_the_repair(self):
        directory = self.tmp / 'releases'
        self.assertEqual(self.ensure(directory), 0)
        self.assertEqual(self.artifacts_dirs, [directory])

    def test_backup_failure_before_state_clears_only_its_own_tracking_and_reprobes_as_pending(self):
        self.fail_at = 'backup'
        with self.assertRaises(host.Error):
            self.ensure()
        self.assertFalse((self.state / 'state.json').exists())
        self.assertFalse((self.state / 'pending.json').exists())
        self.assertTrue(list((self.state / 'backups').glob('*.incomplete')))
        self.calls.clear()
        self.fail_at = None
        self.probe_value = probe(kind='pending', health={'ok': False, 'failed_check': 'backup-stop', 'error': 'x'})
        with mock.patch.object(vps, 'recover_local', side_effect=AssertionError('recover(None)')):
            self.assertEqual(self.ensure(), setup_ensure.WAITING)
        self.assertEqual(self.calls, ['ensure-probe'])

    def test_foreign_or_committed_pending_is_never_removed(self):
        record = Ensure.target_record(self, argparse.Namespace(ssh='root@1.1.1.1'))
        other = dict(record, endpoint='9.9.9.9')
        for pending, committed in (({'record': other, 'mode': 'backup'}, False),
                                   ({'record': record, 'mode': 'backup', 'request': {'x': 1}}, False),
                                   ({'record': record, 'mode': 'transport-upgrade'}, False),
                                   ({'record': record, 'mode': 'backup'}, True)):
            state = Path(tempfile.mkdtemp())
            host.atomic(state / 'pending.json', host.dumps(pending))
            if committed:
                host.atomic(state / 'state.json', b'{}')
            setup_ensure.discard_untracked_backup_pending(vps, state, record)
            self.assertTrue((state / 'pending.json').exists(), pending)
        state = Path(tempfile.mkdtemp())
        host.atomic(state / 'pending.json', host.dumps({'record': record, 'mode': 'backup'}))
        setup_ensure.discard_untracked_backup_pending(vps, state, record)
        self.assertFalse((state / 'pending.json').exists())


class SetupSurface(unittest.TestCase):
    def module(self, data):
        module = Path(tempfile.mkdtemp())
        (module / 'config').write_bytes(data)
        return module

    def test_read_setup_target_optional_and_validated(self):
        base = b'VPS_SSH=root@1.2.3.4\nDOMAIN=Example.org\n'
        self.assertEqual(target_config.read_setup(self.module(base)),
                         {'VPS_SSH': 'root@1.2.3.4', 'DOMAIN': 'example.org', 'REALITY_TARGET': None})
        self.assertEqual(target_config.read_setup(self.module(base + b'REALITY_TARGET=WWW.Python.org\n'))['REALITY_TARGET'], 'www.python.org')
        for bad in (b'REALITY_TARGET=1.2.3.4\n', b'REALITY_TARGET=localhost\n', b'REALITY_TARGET=a/b.org\n',
                    b'REALITY_TARGET=x.org\nREALITY_TARGET=y.org\n'):
            with self.subTest(bad), self.assertRaises(host.Error):
                target_config.read_setup(self.module(base + bad))

    def test_ensure_reads_the_config_exactly_once(self):
        module = self.module(b'VPS_SSH=root@h.example\nDOMAIN=example.org\nREALITY_TARGET=www.python.org\n')
        with mock.patch.object(vps, 'MODULE', module), mock.patch.object(setup_ensure, 'run', lambda *a: 0), \
             mock.patch.object(target_config, '_parse', wraps=target_config._parse) as parse, \
             mock.patch('sys.stdout', io.StringIO()):
            self.assertEqual(vps.main(['--deployment-only']), 0)
        parse.assert_called_once()

    def test_existing_accessors_ignore_the_unused_target_value_but_not_structure(self):
        data = b'VPS_SSH=root@1.2.3.4\nDOMAIN=example.org\nREALITY_TARGET=not a host\n'
        module = self.module(data.replace(b'not a host', b'1.2.3.4'))
        self.assertEqual(target_config.default_ssh(module), 'root@1.2.3.4')
        self.assertEqual(target_config.base_domain(module), 'example.org')
        self.assertEqual(target_config.read_config(module), {'VPS_SSH': 'root@1.2.3.4', 'DOMAIN': 'example.org'})
        with self.assertRaises(host.Error):
            target_config.read_setup(module)
        for broken in (b'VPS_SSH=h\nDOMAIN=example.org\nREALITY_TARGET=\n', b'VPS_SSH=h\nDOMAIN=example.org\nREALITY_TARGET=a b\n',
                       b'VPS_SSH=h\nDOMAIN=example.org\nEXTRA=1\n'):
            with self.subTest(broken), self.assertRaises(host.Error):
                target_config.default_ssh(self.module(broken))

    def capture(self, argv):
        seen = {}

        def fake_run(module, state, args):
            seen.update(state=state, args=args)
            return 0
        module = self.module(b'VPS_SSH=root@h.example\nDOMAIN=example.org\nREALITY_TARGET=www.python.org\n')
        with mock.patch.object(vps, 'MODULE', module), mock.patch.object(setup_ensure, 'run', fake_run), \
             mock.patch('sys.stdout', io.StringIO()):
            code = vps.main(['--deployment-only', *argv])
        return code, seen

    def test_bare_and_option_first_forms_map_to_ensure_with_state_dir_either_place(self):
        state = str(Path(tempfile.mkdtemp()) / 's')
        cases = [([], None), (['--mihomo', 'M'], 'M'), (['--state-dir', state], None), (['--state-dir=' + state, '--mihomo', 'M'], 'M'),
                 (['--mihomo', 'M', '--state-dir', state], 'M'), (['--state-dir', state, 'ensure', '--mihomo', 'M'], 'M'),
                 (['ensure', '--state-dir', state, '--mihomo', 'M'], 'M')]
        for argv, mihomo in cases:
            with self.subTest(argv=argv):
                code, seen = self.capture(argv)
                self.assertEqual(code, 0)
                args = seen['args']
                self.assertEqual((args.command, args.mihomo, args.ssh, args.domain, args.reality_target),
                                 ('ensure', mihomo, 'root@h.example', 'sub.example.org', 'www.python.org'))
                if any('state' in token for token in argv):
                    self.assertEqual(seen['state'], Path(state))
        code, seen = self.capture(['--ssh-proxy', 'socks5://127.0.0.1:1080'])
        self.assertIsNotNone(seen['args'].ssh_proxy)

    def test_artifacts_dir_is_forwarded_and_bare_usage_is_unchanged(self):
        code, seen = self.capture([])
        self.assertIsNone(seen['args'].artifacts_dir)
        for argv in (['--artifacts-dir', '/r/rel'], ['--mihomo', 'M', '--artifacts-dir=/r/rel'], ['ensure', '--artifacts-dir', '/r/rel']):
            with self.subTest(argv):
                code, seen = self.capture(argv)
                self.assertEqual((code, seen['args'].command, seen['args'].artifacts_dir), (0, 'ensure', Path('/r/rel')))
        self.assertEqual(setup_ensure.deploy_args(seen['args']).artifacts_dir, Path('/r/rel'))
        with mock.patch('sys.stdout', io.StringIO()) as out:
            vps.main(['--deployment-only', '--help'])
        self.assertEqual(out.getvalue().count('--artifacts-dir'), 1)

    def test_explicit_old_verbs_keep_their_parsers(self):
        for verb in ('deploy', 'replace', 'restore', 'preflight', 'status', 'backup', 'recover', 'cdn'):
            with self.subTest(verb), mock.patch('sys.stdout', io.StringIO()) as out, self.assertRaises(SystemExit) as stop:
                vps.main(['--deployment-only', verb, '--help'])
            self.assertEqual(stop.exception.code, 0)
            self.assertIn('usage', out.getvalue())
        with mock.patch('sys.stdout', io.StringIO()) as out, self.assertRaises(SystemExit):
            vps.main(['--deployment-only', '--state-dir', 'S', 'deploy', '--help'])
        self.assertIn('--reality-target', out.getvalue())
        parsed = vps.parser(True).parse_args(['--state-dir', 'S', 'deploy', '--ssh', 'x'])
        self.assertEqual((parsed.command, parsed.state_dir), ('deploy', Path('S')))

    def test_public_help_is_the_one_command_with_an_advanced_hint(self):
        for argv in (['--help'], ['-h'], ['--state-dir', 'S', '--help']):
            with self.subTest(argv), mock.patch('sys.stdout', io.StringIO()) as out:
                self.assertEqual(vps.main(['--deployment-only', *argv]), 0)
            text = out.getvalue()
            self.assertIn('bash vps/setup.sh [--mihomo PATH] [--ssh-proxy URL]', text)
            self.assertIn('Advanced maintenance', text)
            self.assertNotIn('positional arguments', text)
            self.assertNotIn('{deploy,replace', text)

    def test_ensure_config_errors_fail_before_state_or_locks(self):
        state = Path(tempfile.mkdtemp()) / 'never'
        for data in (None, b'VPS_SSH=h\n', b'VPS_SSH=h\nDOMAIN=example.org\nREALITY_TARGET=1.2.3.4\n'):
            module = Path(tempfile.mkdtemp())
            if data is not None:
                (module / 'config').write_bytes(data)
            with self.subTest(data=data), mock.patch.object(vps, 'MODULE', module), mock.patch('sys.stderr', io.StringIO()) as err:
                self.assertEqual(vps.main(['--deployment-only', '--state-dir', str(state)]), 1)
                self.assertIn('config', err.getvalue())
            self.assertFalse(state.exists())

    def test_ensure_probe_uses_the_public_source_bootstrap_unmodified_remote_pinning(self):
        self.assertIn('ensure-probe', vps.CONNECT_ACTIONS)
        record = {'user': 'root'}
        command = vps.remote_command(record, 'ensure-probe')
        self.assertIn('qd_connect_read', command)
        self.assertIn('ensure-probe', command)
        self.assertNotIn('exec(compile(zlib.decompress(base64.b64decode(\'', vps.remote_command(record, 'status').split(' python3 -c ')[0])
        self.assertNotIn('qd_connect_read', vps.remote_command(record, 'status'))

    def test_local_roster_digest_is_the_shared_helper(self):
        with mock.patch.object(setup_ensure, 'roster_digest', return_value='x') as shared:
            self.assertEqual(vps.local_roster_digest({'accounts': {'a': 1}}), 'x')
        shared.assert_called_once_with({'a': 1})

    def test_setup_script_still_runs_vps_in_deployment_only_mode(self):
        self.assertIn('lib/vps.py" --deployment-only "$@"', (REAL_MODULE / 'setup.sh').read_text())


if __name__ == '__main__':
    unittest.main()
