#!/usr/bin/env python3
"""Non-root offline tests; --live-parser exercises the installed CLI's real parser."""
import concurrent.futures
import importlib.util
import json
import os
import pathlib
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

SOURCE = pathlib.Path(__file__).resolve().parents[1]/'derp-bypass.py'
spec = importlib.util.spec_from_file_location('derp_bypass', SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class PolicyTests(unittest.TestCase):
    def test_union_preserves_both_spellings_and_is_idempotent(self):
        env = {'NO_PROXY': 'localhost,corp.example,127.0.0.1',
               'no_proxy': '::1,other.example,corp.example', 'HTTP_PROXY': 'http://secret.invalid:1'}
        merged = m.merged_no_proxy(env)
        self.assertEqual(merged.split(','), ['localhost', 'corp.example', '127.0.0.1', '::1',
                                            'other.example', *m.DERP])
        self.assertEqual(m.merged_no_proxy({'NO_PROXY': merged, 'no_proxy': merged}), merged)
        self.assertNotIn('secret.invalid', m.environment_lines(env))

    def test_both_cases_required_and_port_limited(self):
        value = m.merged_no_proxy({})
        self.assertTrue(m.policy_present({'NO_PROXY': value, 'no_proxy': value}))
        self.assertFalse(m.policy_present({'NO_PROXY': value}))
        self.assertFalse(m.policy_present({'NO_PROXY': value, 'no_proxy': value.replace(':1443', ':443')}))
        self.assertNotIn(',dex-gem.ai,', ','+value+',')
        self.assertNotIn('controlplane.tailscale.com', value)

    def test_systemd_escaping_and_control_rejection(self):
        self.assertIn('%%', m.environment_lines({'NO_PROXY': 'percent%host'}))
        self.assertIn('\\"', m.environment_lines({'NO_PROXY': 'quote"host'}))
        for value in ['a\nb', 'a\rb', 'a\0b']:
            with self.assertRaises(ValueError):
                m.merged_no_proxy({'NO_PROXY': value})

    def test_shlex_environment_and_generated_fields(self):
        env = m.environment('HTTP_PROXY=http://127.0.0.1:7897 "NO_PROXY=localhost,a.example" no_proxy=b.example')
        text = m.dropin(env).decode()
        self.assertEqual(text.splitlines()[:2], [m.MARKER, '[Service]'])
        self.assertEqual(len(text.splitlines()), 4)
        self.assertNotIn('HTTP_PROXY=', text)
        self.assertNotIn('HTTPS_PROXY=', text)
        self.assertIn('a.example,b.example', text)

    def test_installer_match_consumes_shared_policy(self):
        installer = SOURCE.parent/'install.sh'
        prefix = installer.read_text().split('\nrun_clash_check=true\n', 1)[0]
        value = m.merged_no_proxy({})
        for no_proxy, expected in [(value, 0), ('localhost,127.0.0.1,::1', 1)]:
            raw = 'HTTP_PROXY=http://127.0.0.1:7897 HTTPS_PROXY=http://127.0.0.1:7897 NO_PROXY='+no_proxy+' no_proxy='+no_proxy
            script = prefix+'\nDERP_POLICY="$1"\neffective_environment() { printf "%s\\n" "$TEST_ENV"; }\neffective_proxy_matches http://127.0.0.1:7897\n'
            p = subprocess.run(['bash', '-c', script, '--', str(SOURCE)], env={**os.environ, 'TEST_ENV': raw}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(p.returncode, expected, p.stderr.decode())

    def test_systemd_parser_accepts_candidate(self):
        with tempfile.TemporaryDirectory() as work:
            p = pathlib.Path(work)/'tailscale-derp-policy-test.service'
            p.write_text('[Unit]\nDescription=Offline parser test\n[Service]\nExecStart=/bin/true\n'+m.environment_lines({'NO_PROXY': 'corp.example,percent%host'}))
            r = subprocess.run(['systemd-analyze', '--user', 'verify', str(p)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(r.returncode, 0, r.stderr.decode())

    def test_rule_telemetry_does_not_change_policy_fingerprint(self):
        a = {'rules': [{'type': 'Domain', 'payload': 'derp.dex-gem.ai', 'proxy': 'DIRECT',
                        'extra': {'disabled': False, 'hitCount': 1, 'hitAt': 'before', 'missCount': 10, 'missAt': 'before'}}]}
        b = json.loads(json.dumps(a))
        b['rules'][0]['extra'].update(hitCount=2, hitAt='after', missCount=20, missAt='after')
        self.assertEqual(m.stable_rules(a), m.stable_rules(b))
        b['rules'][0]['extra']['disabled'] = True
        self.assertNotEqual(m.stable_rules(a), m.stable_rules(b))
        b['rules'][0]['extra']['disabled'] = False
        b['rules'][0]['extra']['unknownSemantics'] = True
        self.assertNotEqual(m.stable_rules(a), m.stable_rules(b))

    def test_public_recovery_does_not_relax_root_requirement(self):
        for action in ['rollback', '_guard']:
            with self.subTest(action=action), mock.patch.object(m.os, 'geteuid', return_value=1000), \
                    mock.patch.object(sys, 'argv', [str(SOURCE), action, '/unopened-fixture']), \
                    mock.patch.object(m, 'load_plan') as load:
                with self.assertRaisesRegex(RuntimeError, 'root required'):
                    m.main()
                load.assert_not_called()

    def test_apply_never_prompts_or_elevates(self):
        with mock.patch.object(m.os, 'geteuid', return_value=1000), mock.patch.object(m, 'run') as call:
            with self.assertRaisesRegex(RuntimeError, 'requires the user'):
                m.apply()
            call.assert_not_called()


class FakeService:
    """Independent disk, manager and process state; no real service operations."""
    def __init__(self, original, node):
        self.original = original
        self.original_node = node
        self.manager = {**original, 'NO_PROXY': 'candidate', 'no_proxy': 'candidate'}
        self.process = dict(self.manager)
        self.node = dict(node)
        self.active = True
        self.fail_reload = 0
        self.fail_restart = 0
        self.bad_manager_after_reload = False
        self.bad_node_after_restart = False
        self.bad_env_after_restart = False
        self.calls = []
        self.process_reads = 0
        self.node_reads = 0

    def run(self, args, timeout=20):
        self.calls.append(args)
        if args[:4] == ['systemctl', 'show', m.SERVICE, '-p'] and args[4] == 'Environment':
            return ' '.join(shlex.quote(k+'='+v) for k, v in self.manager.items())
        if args == ['systemctl', 'daemon-reload']:
            if self.fail_reload:
                self.fail_reload -= 1
                raise RuntimeError('injected reload failure')
            self.manager = dict(self.original)
            if self.bad_manager_after_reload:
                self.manager['HTTPS_PROXY'] = 'http://wrong.invalid:7897'
        elif args == ['systemctl', 'restart', m.SERVICE]:
            if self.fail_restart:
                self.fail_restart -= 1
                self.active = False
                raise RuntimeError('injected restart failure')
            self.active = True
            self.process = dict(self.manager)
            if self.bad_env_after_restart:
                self.process['no_proxy'] = 'wrong'
            self.node = dict(self.original_node)
            if self.bad_node_after_restart:
                self.node['id'] = 'wrong-node'
        elif args == ['systemctl', 'is-active', '--quiet', m.SERVICE] and not self.active:
            raise RuntimeError('injected stopped daemon')
        return ''

    def process_env(self):
        self.process_reads += 1
        if not self.active:
            raise RuntimeError('no running process')
        return dict(self.process)

    def identity(self):
        self.node_reads += 1
        return dict(self.node)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.state = self.root/'job'
        self.state.mkdir()
        (self.state/'ack').mkdir()
        (self.state/'entry.py').write_bytes(b'frozen recovery code')
        self.target = self.root/'policy.conf'
        self.lock = self.root/'global-lock'
        self.new = m.dropin({})
        self.original = {'HTTP_PROXY': 'http://127.0.0.1:7897',
                         'HTTPS_PROXY': 'http://127.0.0.1:7897', 'NO_PROXY': 'localhost'}
        self.node = {'id': 'original-node', 'ips': ['100.64.1.2'], 'host': 'fixture', 'state': 'Running'}
        keys = m.PROXY_KEYS+('NO_PROXY', 'no_proxy')
        hashes = {k: m.sha(json.dumps(self.original.get(k)).encode()) for k in keys}
        self.plan = {'previous_exists': False, 'previous_mode': 0o600, 'previous_hash': None,
                     'candidate_hash': m.sha(self.new), 'other_dropins': {'foreign.conf': 'unchanged'},
                     'original_manager_hashes': hashes, 'original_process_hashes': hashes,
                     'node': self.node, 'repo': str(self.root), 'entry_hash': m.sha((self.state/'entry.py').read_bytes())}
        self.service = FakeService(self.original, self.node)
        self.actual_guard_alive = m.guard_alive
        self.patches = [mock.patch.object(m, 'TARGET', self.target), mock.patch.object(m, 'LOCK', self.lock),
                        mock.patch.object(m, 'load_plan', return_value=self.plan),
                        mock.patch.object(m, 'other_dropins', return_value=self.plan['other_dropins']),
                        mock.patch.object(m, 'run', side_effect=self.service.run),
                        mock.patch.object(m, 'process_environment', side_effect=self.service.process_env),
                        mock.patch.object(m, 'node_identity', side_effect=self.service.identity),
                        mock.patch.object(m, 'register_guard', create=True),
                        mock.patch.object(m, 'guard_alive', return_value=False, create=True),
                        mock.patch.object(m, 'frozen_entry', return_value=self.state/'entry.py', create=True),
                        mock.patch.object(m.os, 'geteuid', return_value=0)]
        for p in self.patches:
            p.start()
        self.target.write_bytes(self.new)
        self.lock.write_text(str(self.state)+'\n')

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def command(self, action):
        with mock.patch.object(sys, 'argv', [str(SOURCE), action, str(self.state)]):
            return m.main()

    def request(self):
        with mock.patch.object(m, 'tmux_window', return_value={'window_pane': '@7:%8'}) as launch:
            self.assertEqual(self.command('rollback'), 0)
        launch.assert_called_once_with(self.root, 'tailscale-derp-rollback', self.state.name,
                    [sys.executable, str(self.state/'entry.py'), '_guard', str(self.state)])

    def run_guard(self):
        # Actual public internal action, not direct invocation of rollback().
        self.assertEqual(self.command('_guard'), 0)

    def status(self):
        return json.loads((self.state/'status.json').read_text())

    def restarts(self):
        return self.service.calls.count(['systemctl', 'restart', m.SERVICE])

    def reloads(self):
        return self.service.calls.count(['systemctl', 'daemon-reload'])

    def test_reload_failure_then_explicit_public_retry_restores_runtime(self):
        self.service.fail_reload = 1
        self.request()
        with self.assertRaisesRegex(RuntimeError, 'injected reload failure'):
            self.command('_guard')
        self.assertFalse(self.target.exists())
        self.assertTrue(self.lock.exists())
        self.assertEqual((self.reloads(), self.restarts()), (1, 0))
        self.assertEqual(self.status()['phase'], 'recovery-needs-operator')
        self.assertTrue(self.status()['recovery']['file_restored'])
        self.assertFalse(self.status()['recovery']['manager_verified'])
        self.assertNotEqual(self.service.manager, self.original)
        self.request()  # Dead guard plus retained global lock must launch frozen continuation.
        self.run_guard()
        self.assertEqual((self.reloads(), self.restarts()), (2, 1))
        self.assertEqual(self.service.manager, self.original)
        self.assertEqual(self.service.process, self.original)
        self.assertFalse(self.lock.exists())
        self.assertEqual(self.status()['phase'], 'rolled-back')

    def test_restart_failure_after_file_restore_then_explicit_public_retry(self):
        self.service.fail_restart = 1
        self.request()
        with self.assertRaisesRegex(RuntimeError, 'injected restart failure'):
            self.command('_guard')
        self.assertFalse(self.target.exists())
        self.assertTrue(self.lock.exists())
        self.assertFalse(self.service.active)
        self.assertEqual((self.reloads(), self.restarts()), (1, 1))
        self.assertTrue(self.status()['recovery']['manager_verified'])
        self.assertFalse(self.status()['recovery']['process_verified'])
        self.request()
        self.run_guard()
        self.assertEqual((self.reloads(), self.restarts()), (2, 2))
        self.assertTrue(self.service.active)
        self.assertEqual(self.service.process, self.original)
        self.assertEqual(self.status()['evidence']['node'], self.node)
        self.assertFalse(self.lock.exists())

    def test_original_disk_with_wrong_or_stopped_daemon_is_not_a_noop(self):
        for stopped in [False, True]:
            with self.subTest(stopped=stopped):
                self.target.unlink(missing_ok=True)
                self.lock.write_text(str(self.state)+'\n')
                self.service.manager = dict(self.original)
                self.service.process = {**self.original, 'HTTPS_PROXY': 'http://wrong.invalid:7897'}
                self.service.active = not stopped
                self.service.node = {**self.node, 'id': 'wrong-node'}
                before = self.restarts()
                self.request()
                self.run_guard()
                self.assertEqual(self.restarts()-before, 1)
                self.assertEqual(self.service.process, self.original)
                self.assertEqual(self.status()['evidence']['node'], self.node)
                self.assertFalse(self.lock.exists())

    def test_successful_reload_command_is_not_original_manager_proof(self):
        self.service.bad_manager_after_reload = True
        self.request()
        with self.assertRaisesRegex(RuntimeError, 'original manager'):
            self.command('_guard')
        self.assertEqual((self.reloads(), self.restarts()), (1, 0))
        self.assertTrue(self.lock.exists())
        self.assertFalse(self.status()['recovery']['manager_verified'])

    def test_wrong_node_after_restart_never_releases_lock_or_reports_success(self):
        self.target.unlink()
        self.service.bad_node_after_restart = True
        self.request()
        with self.assertRaisesRegex(RuntimeError, 'original node'):
            self.command('_guard')
        self.assertEqual((self.reloads(), self.restarts()), (1, 1))
        self.assertTrue(self.lock.exists())
        self.assertEqual(self.status()['phase'], 'recovery-needs-operator')
        self.assertFalse(self.status()['recovery']['process_verified'])

    def test_wrong_original_process_environment_after_restart_is_not_success(self):
        self.service.bad_env_after_restart = True
        self.request()
        with self.assertRaisesRegex(RuntimeError, 'original process'):
            self.command('_guard')
        self.assertTrue(self.lock.exists())
        self.assertEqual(self.restarts(), 1)
        self.assertEqual(self.status()['phase'], 'recovery-needs-operator')

    def test_original_disk_does_not_hide_stopped_candidate_process(self):
        self.target.unlink()
        self.service.active = False
        m.rollback(self.state)
        self.assertTrue(self.service.active)
        self.assertEqual(self.service.manager, self.original)
        self.assertEqual(self.service.process, self.original)
        self.assertGreater(self.service.process_reads, 0)
        self.assertGreater(self.service.node_reads, 0)
        self.assertEqual(self.restarts(), 1)
        self.assertFalse(self.lock.exists())

    def test_healthy_repeated_public_rollback_verifies_without_restart(self):
        self.target.unlink()
        self.service.manager = dict(self.original)
        self.service.process = dict(self.original)
        for _ in range(2):
            self.request()
            self.run_guard()
            self.assertFalse(self.lock.exists())
            self.assertEqual(self.status()['phase'], 'rolled-back-no-change')
            self.assertTrue(all(self.status()['recovery'].values()))
        self.assertEqual((self.reloads(), self.restarts()), (0, 0))
        self.assertGreaterEqual(self.service.process_reads, 2)
        self.assertGreaterEqual(self.service.node_reads, 2)
        self.assertGreaterEqual(self.service.calls.count(['systemctl', 'is-active', '--quiet', m.SERVICE]), 2)

    def test_public_existing_lock_with_missing_guard_process_resumes_frozen_task(self):
        (self.state/'guard-ready').write_text('stale readiness')
        with mock.patch.object(m, 'guard_alive', side_effect=self.actual_guard_alive), \
                mock.patch.object(m, 'guard_owner', return_value={'pid': 2147483647, 'start': 'old', 'pane': '%8'}):
            self.request()  # Actual /proc lookup proves the recorded process is absent.
        self.assertTrue(self.lock.exists())
        self.assertTrue((self.state/'rollback-now').exists())

    def test_alive_owned_guard_is_signalled_without_duplicate_task(self):
        m.record(self.state, 'verified-awaiting-confirmation')
        with mock.patch.object(m, 'guard_alive', return_value=True), mock.patch.object(m, 'tmux_window') as launch:
            self.assertEqual(self.command('rollback'), 0)
        launch.assert_not_called()
        self.assertTrue((self.state/'rollback-now').exists())
        self.assertTrue(self.lock.exists())

    def test_terminal_guard_is_not_treated_as_a_serving_owner(self):
        m.record(self.state, 'recovery-needs-operator')
        with mock.patch.object(m, 'guard_alive', return_value=True):
            self.request()
        self.assertTrue(self.lock.exists())

    def test_other_job_lock_is_never_taken_over(self):
        self.lock.write_text('/another-job\n')
        with mock.patch.object(m, 'tmux_window') as launch:
            with self.assertRaisesRegex(RuntimeError, 'different job'):
                self.command('rollback')
        launch.assert_not_called()
        self.assertEqual(self.lock.read_text(), '/another-job\n')

    def test_public_concurrent_requests_do_not_duplicate_recovery(self):
        entered, leave = threading.Event(), threading.Event()
        def launch(*args):
            entered.set()
            if not leave.wait(3):
                raise RuntimeError('test handoff timeout')
            return {'window_pane': '@7:%8'}
        with mock.patch.object(sys, 'argv', [str(SOURCE), 'rollback', str(self.state)]), \
                mock.patch.object(m, 'tmux_window', side_effect=launch) as started, \
                concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(m.main)
            self.assertTrue(entered.wait(2))
            try:
                with self.assertRaisesRegex(RuntimeError, 'already owns this job'):
                    m.main()
            finally:
                leave.set()
            self.assertEqual(first.result(timeout=3), 0)
            self.assertEqual(started.call_count, 1)
        self.assertEqual(self.restarts(), 0)

    def test_old_file_and_permissions_are_restored_before_runtime_verified(self):
        old = (m.MARKER+'\n[Service]\nEnvironment="NO_PROXY=original"\n').encode()
        (self.state/'previous.conf').write_bytes(old)
        self.plan.update(previous_exists=True, previous_hash=m.sha(old))
        self.request()
        self.run_guard()
        self.assertEqual(self.target.read_bytes(), old)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)
        self.assertTrue(all(self.status()['recovery'].values()))

    def test_corrupt_backup_refused_before_service_mutations(self):
        self.plan.update(previous_exists=True, previous_hash=m.sha(b'original'))
        (self.state/'previous.conf').write_bytes(b'corrupt')
        with self.assertRaisesRegex(RuntimeError, 'backup hash mismatch'):
            m.rollback(self.state)
        self.assertEqual(self.service.calls, [])
        self.assertEqual(self.target.read_bytes(), self.new)
        self.assertTrue(self.lock.exists())

    def test_concurrent_file_or_source_edit_is_not_overwritten(self):
        self.target.write_text('user content')
        with self.assertRaisesRegex(RuntimeError, 'outside this job'):
            m.rollback(self.state)
        self.assertEqual(self.target.read_text(), 'user content')
        self.target.unlink()
        with mock.patch.object(m, 'other_dropins', return_value={'foreign.conf': 'changed'}):
            with self.assertRaisesRegex(RuntimeError, 'another service'):
                m.rollback(self.state)
        self.assertEqual(self.service.calls, [])
        self.assertTrue(self.lock.exists())

    def test_unconfirmed_deadline_performs_one_recovery_attempt(self):
        with mock.patch.object(m, 'TIMEOUT', 0):
            self.run_guard()
        self.assertEqual((self.reloads(), self.restarts()), (1, 1))
        self.assertFalse(self.lock.exists())

    def test_confirmation_failure_still_enters_single_recovery(self):
        (self.state/'ack/confirm').write_text('confirmed')
        m.record(self.state, 'verified-awaiting-confirmation')
        with mock.patch.object(m, 'validate_runtime', side_effect=RuntimeError('path changed')):
            self.run_guard()
        self.assertEqual(self.restarts(), 1)
        self.assertEqual(self.status()['phase'], 'rolled-back')

    def test_successful_confirmation_keeps_bypass_without_recovery(self):
        (self.state/'ack/confirm').write_text('confirmed')
        m.record(self.state, 'verified-awaiting-confirmation')
        with mock.patch.object(m, 'validate_runtime', return_value={'direct': True}):
            self.run_guard()
        self.assertEqual(self.restarts(), 0)
        self.assertFalse(self.lock.exists())
        self.assertEqual(self.target.read_bytes(), self.new)
        self.assertEqual(self.status()['phase'], 'confirmed')

    def test_worker_requires_independent_guard_before_any_write(self):
        with self.assertRaisesRegex(RuntimeError, 'guard not ready'):
            m.worker(self.state)
        self.assertEqual(self.service.calls, [])
        self.assertEqual(self.target.read_bytes(), self.new)
        self.assertTrue((self.state/'rollback-now').exists())


class GuardOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = pathlib.Path(self.tmp.name)
        self.entry = self.state/'entry.py'
        self.entry.write_bytes(b'frozen guard')
        self.plan = {'repo': '/project', 'entry_hash': m.sha(self.entry.read_bytes())}
        self.owner = {'pid': 42, 'start': '12345', 'pane': '%8'}
        (self.state/'guard-owner.json').write_text(json.dumps(self.owner))
        self.proc = {'pid': 42, 'start': '12345', 'state': 'S', 'uid': os.geteuid(),
                     'argv': [sys.executable, str(self.entry), '_guard', str(self.state)]}
        self.pane = '/project\t%8\t42\t0\n'
        self.patches = [mock.patch.object(m, 'process_identity', side_effect=lambda pid: dict(self.proc)),
                        mock.patch.object(m, 'run', side_effect=lambda cmd, timeout=20: self.pane)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_owned_frozen_process_and_tmux_project_are_jointly_required(self):
        self.assertTrue(m.guard_alive(self.state, self.plan))
        cases = [('start', 'reused-pid'), ('state', 'Z'), ('uid', os.geteuid()+1),
                 ('argv', [sys.executable, '/unrelated.py', '_guard', str(self.state)])]
        for key, value in cases:
            with self.subTest(key=key):
                old = self.proc[key]
                self.proc[key] = value
                self.assertFalse(m.guard_alive(self.state, self.plan))
                self.proc[key] = old
        for pane in ['/other-project\t%8\t42\t0\n', '/project\t%9\t42\t0\n',
                     '/project\t%8\t999\t0\n', '/project\t%8\t42\t1\n']:
            with self.subTest(pane=pane):
                self.pane = pane
                self.assertFalse(m.guard_alive(self.state, self.plan))

    def test_ready_file_or_pid_record_alone_does_not_establish_a_guard(self):
        (self.state/'guard-ready').write_text('stale ready')
        (self.state/'guard-owner.json').write_text(json.dumps({'pid': 42}))
        self.assertFalse(m.guard_alive(self.state, self.plan))
        (self.state/'guard-owner.json').unlink()
        self.assertFalse(m.guard_alive(self.state, self.plan))

    def test_frozen_entry_tampering_and_symlink_are_refused(self):
        self.entry.write_bytes(b'changed')
        self.assertFalse(m.guard_alive(self.state, self.plan))
        with self.assertRaisesRegex(RuntimeError, 'ownership/hash'):
            m.frozen_entry(self.state, self.plan)
        self.entry.unlink()
        replacement = self.state/'replacement.py'
        replacement.write_bytes(b'frozen guard')
        self.entry.symlink_to(replacement)
        with self.assertRaisesRegex(RuntimeError, 'ownership/hash'):
            m.frozen_entry(self.state, self.plan)

    def test_registration_checks_real_process_tuple_before_readiness(self):
        with mock.patch.dict(os.environ, {'TMUX_PANE': '%8'}):
            m.register_guard(self.state, self.plan)
        self.assertEqual(json.loads((self.state/'guard-owner.json').read_text()), self.owner)
        self.pane = '/project\t%8\t999\t0\n'
        with mock.patch.object(m, 'load_plan', return_value=self.plan), \
                mock.patch.dict(os.environ, {'TMUX_PANE': '%8'}):
            with self.assertRaisesRegex(RuntimeError, 'ownership not established'):
                m.guard(self.state)
        self.assertFalse((self.state/'guard-ready').exists())

    def test_proc_identity_reader_handles_current_test_process_without_mutation(self):
        # Test the actual /proc reader separately from the fake root-guard tuple.
        for p in reversed(self.patches):
            p.stop()
        try:
            proc = m.process_identity(os.getpid())
            self.assertEqual(proc['pid'], os.getpid())
            self.assertEqual(proc['uid'], os.getuid())
            self.assertTrue(proc['start'])
            self.assertTrue(proc['argv'])
        finally:
            for p in self.patches:
                p.start()


class RuntimePathTests(unittest.TestCase):
    def setUp(self):
        self.env = {'NO_PROXY': m.merged_no_proxy({}), 'no_proxy': m.merged_no_proxy({})}
        self.node = {'id': 'same', 'ips': ['100.125.138.103'], 'state': 'Running'}
        self.clash = {'fingerprints': {'mode': 'unchanged'}, 'derp_connections': []}
        self.plan = {'proxy_hashes': {k: m.sha(b'') for k in m.PROXY_KEYS}, 'no_proxy': m.merged_no_proxy({}),
                     'node': self.node, 'uid': 1000, 'clash': self.clash}
        self.ss = 'ESTAB 0 0 192.168.12.175:55555 39.184.215.16:1443 cgroup:/system.slice/tailscaled.service\n'
        self.route = [{'dev': 'eno1', 'prefsrc': '192.168.12.175'}]
        self.patches = [mock.patch.object(m, 'process_environment', return_value=self.env),
                        mock.patch.object(m, 'node_identity', return_value=self.node),
                        mock.patch.object(m, 'clash_state', side_effect=lambda uid: self.clash),
                        mock.patch.object(m, 'run', side_effect=lambda cmd, timeout=20: self.ss if cmd[0] == 'ss' else json.dumps(self.route))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def test_actual_owned_socket_route_and_clash_absence_required(self):
        self.assertTrue(m.validate_runtime(self.plan)['direct_derp_socket'])

    def test_direct_rule_still_entering_tun_is_rejected(self):
        self.clash['derp_connections'] = [{'sourcePort': '55555', 'chains': ['DIRECT'], 'type': 'Tun'}]
        with self.assertRaisesRegex(RuntimeError, 'explicit proxy or TUN'):
            m.validate_runtime(self.plan)

    def test_loopback_derp_connection_is_rejected(self):
        self.ss += 'ESTAB 0 0 127.0.0.1:44444 127.0.0.1:7897 cgroup:/system.slice/tailscaled.service\n'
        self.clash['derp_connections'] = [{'sourcePort': '44444', 'chains': ['DIRECT']}]
        with self.assertRaisesRegex(RuntimeError, 'explicit proxy or TUN'):
            m.validate_runtime(self.plan)

    def test_physical_route_must_not_be_meta(self):
        self.route = [{'dev': 'Meta'}]
        with self.assertRaisesRegex(RuntimeError, 'not a physical'):
            m.validate_runtime(self.plan)

    def test_real_direct_socket_not_just_policy_presence(self):
        self.ss = 'ESTAB 0 0 127.0.0.1:44444 127.0.0.1:7897 cgroup:/system.slice/tailscaled.service\n'
        with self.assertRaisesRegex(RuntimeError, 'no actual tailscaled-owned'):
            m.validate_runtime(self.plan)


class TmuxTests(unittest.TestCase):
    def test_new_owned_window_vacates_on_exit_and_execs_frozen_process(self):
        calls = []
        def fake(args, timeout=20):
            calls.append(args)
            if 'display-message' in args or 'list-panes' in args:
                return '/project\n'
            if 'new-window' in args:
                return '@7:%8\n'
            return ''
        with mock.patch.object(m.subprocess, 'run', return_value=mock.Mock(returncode=0)), mock.patch.object(m, 'run', side_effect=fake):
            m.tmux_window(pathlib.Path('/project'), 'tailscale-derp-rollback', 'same-job', ['/usr/bin/python3', '/frozen/entry.py', '_guard', '/job'])
        command = next(x[-1] for x in calls if 'new-window' in x)
        self.assertTrue(command.startswith('tmux -L pi-agent set-option -w -t "$TMUX_PANE" remain-on-exit off && exec '))
        self.assertIn('/frozen/entry.py _guard /job', command)

    def test_existing_project_identity_mismatch_stops(self):
        with mock.patch.object(m.subprocess, 'run', return_value=mock.Mock(returncode=0)), mock.patch.object(m, 'run', return_value='/unrelated\n') as command:
            with self.assertRaisesRegex(RuntimeError, 'session path mismatch'):
                m.tmux_window(pathlib.Path('/project'), 'task', 'run', ['true'])
        self.assertEqual(command.call_count, 1)

    def test_occupied_slot_refuses_without_input_or_kill(self):
        calls = []
        def fake(args, timeout=20):
            calls.append(args)
            if 'display-message' in args or 'list-panes' in args:
                return '/project\n'
            if 'new-window' in args:
                raise RuntimeError('slot occupied')
            return ''
        with mock.patch.object(m.subprocess, 'run', return_value=mock.Mock(returncode=0)), mock.patch.object(m, 'run', side_effect=fake):
            with self.assertRaisesRegex(RuntimeError, 'slot occupied'):
                m.tmux_window(pathlib.Path('/project'), 'task', 'run', ['true'])
        self.assertEqual(sum('new-window' in x for x in calls), 1)
        self.assertFalse(any(any(y in x for y in ['kill-window', 'kill-pane', 'send-keys']) for x in calls))
        self.assertTrue(all(x[:3] == ['tmux', '-L', 'pi-agent'] for x in calls))


if __name__ == '__main__':
    if sys.argv[1:] == ['--live-parser']:
        print(json.dumps(m.parser_test(), indent=2))
    else:
        unittest.main()
