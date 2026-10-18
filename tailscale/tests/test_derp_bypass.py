#!/usr/bin/env python3
"""Non-root offline tests; --live-parser exercises the installed CLI's real parser."""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
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

    def test_apply_never_prompts_or_elevates(self):
        with mock.patch.object(m.os, 'geteuid', return_value=1000), mock.patch.object(m, 'run') as call:
            with self.assertRaisesRegex(RuntimeError, 'requires the user'):
                m.apply()
            call.assert_not_called()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.state = self.root/'state'
        self.state.mkdir()
        self.target = self.root/'policy.conf'
        self.lock = self.root/'lock'
        self.new = m.dropin({})
        self.plan = {'previous_exists': False, 'previous_mode': 0o600,
                     'candidate_hash': m.sha(self.new), 'other_dropins': {'foreign.conf': 'unchanged'}}
        self.patches = [mock.patch.object(m, 'TARGET', self.target), mock.patch.object(m, 'LOCK', self.lock),
                        mock.patch.object(m, 'load_plan', return_value=self.plan),
                        mock.patch.object(m, 'other_dropins', return_value=self.plan['other_dropins']),
                        mock.patch.object(m, 'node_identity', return_value={'id': 'same-node'})]
        for p in self.patches:
            p.start()
        self.target.write_bytes(self.new)
        self.lock.write_text(str(self.state)+'\n')

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_absent_original_removed_and_only_tailscaled_restarted(self):
        with mock.patch.object(m, 'run', return_value='') as command:
            m.rollback(self.state)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.lock.exists())
        self.assertEqual(command.call_args_list, [mock.call(['systemctl', 'daemon-reload']),
                         mock.call(['systemctl', 'restart', 'tailscaled.service'], 60),
                         mock.call(['systemctl', 'is-active', '--quiet', 'tailscaled.service'])])

    def test_old_file_and_permissions_restored(self):
        old = (m.MARKER+'\n[Service]\nEnvironment="NO_PROXY=original"\n').encode()
        (self.state/'previous.conf').write_bytes(old)
        self.plan['previous_exists'] = True
        self.plan['previous_hash'] = m.sha(old)
        with mock.patch.object(m, 'run', return_value=''):
            m.rollback(self.state)
        self.assertEqual(self.target.read_bytes(), old)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)

    def test_corrupt_backup_refused_before_any_service_command(self):
        self.plan['previous_exists'] = True
        self.plan['previous_hash'] = m.sha(b'original')
        (self.state/'previous.conf').write_bytes(b'corrupt')
        with mock.patch.object(m, 'run') as command:
            with self.assertRaisesRegex(RuntimeError, 'backup hash mismatch'):
                m.rollback(self.state)
        command.assert_not_called()
        self.assertEqual(self.target.read_bytes(), self.new)

    def test_duplicate_recovery_does_not_restart_again(self):
        self.target.unlink()
        with mock.patch.object(m, 'run') as command:
            m.rollback(self.state)
        command.assert_not_called()

    def test_foreign_edit_refused_without_rewrite_or_restart(self):
        self.target.write_text('new user content')
        with mock.patch.object(m, 'run') as command:
            with self.assertRaisesRegex(RuntimeError, 'outside this job'):
                m.rollback(self.state)
        self.assertEqual(self.target.read_text(), 'new user content')
        command.assert_not_called()

    def test_other_unit_source_change_refused(self):
        with mock.patch.object(m, 'other_dropins', return_value={'foreign.conf': 'different'}), mock.patch.object(m, 'run') as command:
            with self.assertRaisesRegex(RuntimeError, 'another service drop-in'):
                m.rollback(self.state)
        self.assertEqual(self.target.read_bytes(), self.new)
        command.assert_not_called()

    def test_unconfirmed_deadline_rolls_back(self):
        with mock.patch.object(m, 'TIMEOUT', 0), mock.patch.object(m, 'rollback') as recovery:
            m.guard(self.state)
        recovery.assert_called_once_with(self.state)
        self.assertTrue((self.state/'guard-ready').exists())

    def test_confirmation_failure_still_rolls_back(self):
        (self.state/'ack').mkdir()
        (self.state/'ack/confirm').write_text('confirmed')
        m.record(self.state, 'verified-awaiting-confirmation')
        with mock.patch.object(m, 'validate_runtime', side_effect=RuntimeError('path changed')), mock.patch.object(m, 'rollback') as recovery:
            m.guard(self.state)
        recovery.assert_called_once_with(self.state)
        self.assertEqual(json.loads((self.state/'status.json').read_text())['phase'], 'confirmation-failed-rolling-back')

    def test_successful_confirmation_rechecks_and_keeps(self):
        (self.state/'ack').mkdir()
        (self.state/'ack/confirm').write_text('confirmed')
        m.record(self.state, 'verified-awaiting-confirmation')
        with mock.patch.object(m, 'validate_runtime', return_value={'direct': True}) as check, mock.patch.object(m, 'rollback') as recovery:
            m.guard(self.state)
        check.assert_called_once()
        recovery.assert_not_called()
        self.assertFalse(self.lock.exists())
        self.assertEqual(json.loads((self.state/'status.json').read_text())['phase'], 'confirmed')

    def test_worker_requires_independent_guard_before_any_write(self):
        with mock.patch.object(m, 'run') as command:
            with self.assertRaisesRegex(RuntimeError, 'guard not ready'):
                m.worker(self.state)
        command.assert_not_called()
        self.assertEqual(self.target.read_bytes(), self.new)
        self.assertTrue((self.state/'rollback-now').exists())


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
