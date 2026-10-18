"""Local orchestration contracts. No SSH, private real state or live clients."""
import argparse
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import host
import vps


class Local(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='qd-local-')
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / 'state'
        vps.private_dir(self.state)
        self.config = {'domain': 'sub.example.org', 'reality_target': 'target.example.org', 'acme_email': 'operator@example.org', 'proxy_port': 443}
        self.doc = {'format': host.FORMAT, 'owner': 'owner-fixture', 'config': self.config, 'current': None, 'previous': [], 'backup': None}
        self.record = {'target': 'root@93.184.216.34', 'endpoint': '93.184.216.34', 'user': 'root', 'port': 22,
                       'options': ['-o', 'StrictHostKeyChecking=yes'], 'machine_id': 'a' * 32}

    def args(self, command='deploy', extra=()):
        return vps.parser().parse_args([command, '--ssh', 'root@93.184.216.34', *extra])

    def test_private_atomic_noop_and_failed_write_preserves_old(self):
        path = self.state / 'state.json'
        host.atomic(path, 'original')
        before = path.stat().st_ino, path.stat().st_mtime_ns
        host.atomic(path, 'original')
        self.assertEqual(before, (path.stat().st_ino, path.stat().st_mtime_ns))
        with mock.patch.object(host.os, 'replace', side_effect=OSError('injected')):
            with self.assertRaises(OSError):
                host.atomic(path, 'replacement')
        self.assertEqual(path.read_bytes(), b'original')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
        executable = self.state / 'restored-binary'
        host.atomic(executable, 'same artifact', 0o600)
        host.atomic(executable, 'same artifact', 0o700)
        self.assertEqual(executable.stat().st_mode & 0o777, 0o700)
        path.chmod(0o644)
        with self.assertRaises(host.Error):
            vps.read_private(path)
        link = self.state / 'link'
        link.symlink_to(path)
        with self.assertRaises(host.Error):
            host.atomic(link, 'secret')

    def test_legacy_state_refuses_without_modification_or_contact(self):
        for name in ('identity.json', 'inventory.json', 'generated'):
            path = self.state / name
            path.write_bytes(b'legacy secret fixture')
            with mock.patch.object(vps, 'remote', side_effect=AssertionError('contact')):
                with self.assertRaisesRegex(host.Error, 'Legacy'):
                    with vps.locked(self.state):
                        self.fail('legacy accepted')
            self.assertEqual(path.read_bytes(), b'legacy secret fixture')
            path.unlink()

    def test_corrupt_state_never_regenerated(self):
        host.atomic(self.state / 'state.json', '{bad')
        with self.assertRaisesRegex(host.Error, 'preserved'):
            vps.state_document(self.state, self.args(), True)
        self.assertEqual((self.state / 'state.json').read_text(), '{bad')

    def test_obsolete_flags_are_errors_not_silent_fallbacks(self):
        with mock.patch('sys.stderr', new_callable=io.StringIO):
            for argv in (['init'], ['deploy', '--role', 'relay', '--host', '93.184.216.34'],
                         ['deploy', '--ssh', 'root@93.184.216.34', '--without-tailscale']):
                with self.assertRaises(SystemExit):
                    vps.parser().parse_args(argv)

    def test_target_alias_keeps_trust_and_no_fixed_identity(self):
        output = b'hostname 93.184.216.34\nuser operator\nport 2222\n'
        with mock.patch.object(host, 'run', return_value=subprocess.CompletedProcess([], 0, output)) as run:
            record = vps.target_record(self.args(extra=['--identity', '/fixture-key']))
        argv = run.call_args.args[0]
        for option in ('StrictHostKeyChecking=yes', 'ControlMaster=no', 'ControlPath=none', 'ClearAllForwardings=yes', 'ProxyJump=none', 'ProxyCommand=none'):
            self.assertIn(option, argv)
        self.assertIn('/fixture-key', argv)
        self.assertEqual(record['port'], 2222)
        self.assertEqual(record['user'], 'operator')
        self.assertNotIn('StrictHostKeyChecking=no', argv)
        for target in ('-oProxyCommand=bad', 'root@host;id', 'root@127.0.0.1 extra'):
            args = argparse.Namespace(ssh=target, port=None, identity=None)
            with self.assertRaises(host.Error):
                vps.target_record(args)

    def test_private_payload_is_stdin_not_command_argv(self):
        secret = 'fixture-secret-do-not-log'
        with mock.patch.object(vps.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'{"ok":true}', b'')) as run:
            vps.remote(self.record, 'commit', {'password': secret})
        self.assertNotIn(secret, ' '.join(run.call_args.args[0]))
        self.assertIn(secret.encode(), run.call_args.kwargs['input'])
        self.assertIn('QD_SSH_CONNECTION', run.call_args.args[0][-1])

    def test_different_target_and_wrong_host_fail_before_credentials(self):
        self.doc['current'] = dict(self.record, endpoint='93.184.216.35')
        with mock.patch.object(vps, 'target_record', return_value=self.record), mock.patch.object(vps, 'remote') as remote:
            with self.assertRaisesRegex(host.Error, 'use replace'):
                vps.deploy(self.state, self.doc, self.args())
            remote.assert_not_called()
        self.doc['current'] = None
        with mock.patch.object(vps, 'target_record', return_value=self.record), mock.patch.object(vps, 'remote', side_effect=host.Error('wrong machine')) as remote, mock.patch.object(vps, 'upload_bundle') as bundle:
            with self.assertRaisesRegex(host.Error, 'wrong machine'):
                vps.deploy(self.state, self.doc, self.args())
            self.assertEqual(remote.call_args.args[1], 'preflight')
            bundle.assert_not_called()

    def test_replace_old_unreachable_uses_only_local_backup_new_target(self):
        self.doc['current'] = dict(self.record, target='old-fixture', endpoint='93.184.216.35')
        self.doc['backup'] = str(self.state / 'offnode.tar')
        host.atomic(Path(self.doc['backup']), 'fixture')
        calls = []
        manifest = {'owner': self.doc['owner'], 'config': self.config}
        def remote(record, action, *args, **kwargs):
            calls.append((record['target'], action))
            if action == 'preflight':
                return {'machine_id': self.record['machine_id']}
            if action == 'prepare':
                return {'unchanged': False}
            return {'machine_id': self.record['machine_id'], 'node_id': 'fixture-node'}
        with mock.patch.object(vps, 'target_record', return_value=self.record), mock.patch.object(vps, 'remote', side_effect=remote), \
             mock.patch.object(vps, 'confirm'), mock.patch.object(vps, 'inspect_backup', return_value=manifest), \
             mock.patch.object(vps, 'backup_remote', return_value=Path(self.doc['backup'])), mock.patch.object(vps, 'validate_outputs'), \
             mock.patch.object(vps, 'artifacts', side_effect=AssertionError('release network required')), \
             mock.patch.object(vps, 'export_after_commit') as export:
            vps.deploy(self.state, self.doc, self.args('replace'))
        export.assert_called_once_with(self.state)
        self.assertTrue(all(target != 'old-fixture' for target, _ in calls))
        self.assertEqual([action for _, action in calls], ['preflight', 'prepare', 'commit'])
        saved = vps.load(self.state / 'state.json')
        self.assertEqual(saved['current']['endpoint'], self.record['endpoint'])
        self.assertEqual(saved['previous'][0]['target'], 'old-fixture')
        self.assertFalse((self.state / 'pending.json').exists())

    def test_failed_format_gate_retains_pending_without_publication(self):
        def remote(record, action, *args, **kwargs):
            return {'machine_id': self.record['machine_id']} if action == 'preflight' else {'unchanged': False}
        path = self.state / 'backup.tar'
        host.atomic(path, 'fixture')
        with mock.patch.object(vps, 'target_record', return_value=self.record), mock.patch.object(vps, 'remote', side_effect=remote) as rem, \
             mock.patch.object(vps, 'confirm'), mock.patch.object(vps, 'artifacts', return_value=self.state), \
             mock.patch.object(vps, 'upload_bundle', return_value=io.BytesIO()), mock.patch.object(vps, 'backup_remote', return_value=path), \
             mock.patch.object(vps, 'validate_outputs', side_effect=host.Error('native format failed')):
            with self.assertRaisesRegex(host.Error, 'native format failed'):
                vps.deploy(self.state, self.doc, self.args())
        self.assertNotIn('commit', [c.args[1] for c in rem.call_args_list])
        self.assertTrue((self.state / 'pending.json').exists())
        self.assertIsNone(vps.load(self.state / 'state.json')['current'])

    def test_lost_commit_reply_reconciles_without_second_rotation(self):
        host.atomic(self.state / 'pending.json', host.dumps({'record': self.record}))
        offnode = self.state / 'backup.tar'
        host.atomic(offnode, 'fixture')
        def remote(record, action, *args):
            return {'committed': True} if action == 'abort' else {'machine_id': self.record['machine_id'], 'healthy': True}
        with mock.patch.object(vps, 'remote', side_effect=remote) as rem, mock.patch.object(vps, 'backup_remote', return_value=offnode), \
             mock.patch.object(vps, 'export_after_commit') as export:
            vps.recover_local(self.state, self.doc)
        export.assert_called_once_with(self.state)
        self.assertEqual([c.args[1] for c in rem.call_args_list], ['abort', 'status'])
        self.assertFalse((self.state / 'pending.json').exists())
        self.assertEqual(self.doc['current']['machine_id'], 'a' * 32)

    def test_one_proxy_port_setting_defaults443_and_ssh_port_is_separate(self):
        args = self.args(extra=['--domain', 'sub.example.org', '--reality-target', 'target.example.org',
                                '--acme-email', 'operator@example.org', '--port', '2222'])
        config = vps.configuration(args)
        self.assertEqual(config['proxy_port'], 443)
        self.assertEqual(args.port, 2222)
        custom = dict(config, proxy_port=4443)
        host.validate_config(custom)
        self.assertIn(4443, host.required_ports(custom))
        self.assertNotIn(443, host.required_ports(custom))
        for value in (0, 65536, True, 80, 8443, host.ADMIN_PORT, host.SUB_PORT, host.API_PORT):
            with self.assertRaises(host.Error):
                host.validate_config(dict(config, proxy_port=value))
        with self.assertRaisesRegex(host.Error, 'no implicit listener migration'):
            vps.configuration(self.args(extra=['--proxy-port', '4443']), config)

    def test_native_rules_underlay_domestic_and_no_api_whitelist(self):
        manifest = host.new_manifest('fixture', self.config, self.record['endpoint'])
        rules = json.loads(host.clash_rules(manifest))
        self.assertTrue(rules['geodata-mode'])
        self.assertEqual(rules['rules'][:2], ['DOMAIN,sub.example.org,DIRECT', 'IP-CIDR,93.184.216.34/32,DIRECT,no-resolve'])
        self.assertIn('DOMAIN-SUFFIX,bilibili.com,DIRECT', rules['rules'])
        self.assertEqual(rules['rules'][-1], 'MATCH,PROXY')
        self.assertEqual(len({a['id'] for a in manifest['accounts'].values()}), 3)
        self.assertEqual(len({a['subId'] for a in manifest['accounts'].values()}), 3)
        self.assertEqual(len(host.feed_paths(manifest)), 6)


if __name__ == '__main__':
    unittest.main()
