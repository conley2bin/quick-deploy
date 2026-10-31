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

    def test_complete_cli_commands_execute_without_stdin(self):
        cases = [('deploy',), ('deploy', 'unchanged'), ('replace',), ('restore',),
                 ('devices', 'add', 'tablet'), ('devices', 'rename', 'home', 'tablet'),
                 ('devices', 'reset', 'home'), ('devices', 'remove', 'home')]
        for index, case in enumerate(cases):
            with self.subTest(command=case):
                state = vps.private_dir(self.state / str(index))
                vps.private_dir(state / 'outputs')
                document = dict(self.doc, current=dict(self.record, endpoint='93.184.216.35'))
                archive = state / 'saved.tar'
                host.atomic(archive, 'isolated backup')
                document['backup'] = str(archive)
                argv = ['--state-dir', str(state), case[0]]
                if case[0] == 'devices':
                    document['current'] = dict(self.record)
                    argv += list(case[1:])
                else:
                    argv += ['--ssh', self.record['target']]
                if case[0] == 'deploy':
                    # First setup: all configuration data supplied explicitly.
                    argv += ['--domain', self.config['domain'], '--reality-target', self.config['reality_target'],
                             '--acme-email', self.config['acme_email']]
                elif case[0] == 'replace':
                    # Existing deployment: saved domain stated explicitly; the config DOMAIN default would require migration.
                    argv += ['--domain', self.config['domain']]
                elif case[0] == 'restore':
                    # Fresh local restore gets its configuration from the archive.
                    argv += ['--backup', str(archive)]
                if case[0] not in ('deploy', 'restore'):
                    host.atomic(state / 'state.json', host.dumps(document))
                events = []
                def remote(record, action, request=None, **kwargs):
                    events.append(action)
                    if action in ('commit', 'devices-commit'):
                        self.assertEqual(request['offnode_sha256'], host.digest(archive))
                    return {'machine_id': self.record['machine_id'], 'unchanged': case == ('deploy', 'unchanged'),
                            'adapter_sha256': host.DEPLOYED_ADAPTER_SHA256,
                            'devices': ['company', 'home', 'phone'], 'rename_target_has_history': False}
                def backup(*args):
                    events.append('backup')
                    return archive
                def validate(*args):
                    events.append('validate')
                def export(state):
                    events.append('export')
                    self.assertFalse((state / 'pending.json').exists())
                with mock.patch('builtins.input', side_effect=AssertionError('Unexpected input')) as no_input, \
                     mock.patch('sys.stdin', io.StringIO('')), mock.patch('sys.stdout', new_callable=io.StringIO) as out, \
                     mock.patch.object(host, 'run', side_effect=AssertionError('No external process')), \
                     mock.patch.object(vps.subprocess, 'run', side_effect=AssertionError('No external process')), \
                     mock.patch.object(vps, 'target_record', return_value=dict(self.record)), \
                     mock.patch.object(vps, 'remote', side_effect=remote), \
                     mock.patch.object(vps, 'inspect_backup', return_value=host.new_manifest(document['owner'], self.config,
                         document['current']['endpoint'] if document['current'] else self.record['endpoint'])), \
                     mock.patch.object(vps, 'artifacts', return_value=state), \
                     mock.patch.object(vps, 'upload_bundle', return_value=io.BytesIO()), \
                     mock.patch.object(vps, 'resolve_mihomo', return_value='/fixture/mihomo'), \
                     mock.patch.object(vps, 'backup_remote', side_effect=backup), \
                     mock.patch.object(vps, 'validate_outputs', side_effect=validate), \
                     mock.patch.object(vps, 'export_after_commit', side_effect=export):
                    self.assertEqual(vps.main(argv), 0)
                no_input.assert_not_called()
                self.assertNotRegex(out.getvalue(), r'Type YES|Proceed\?|Not confirmed|three full|six exact')
                if case[0] == 'devices':
                    expected = ['status', 'backup', 'devices-prepare', 'backup', 'validate', 'devices-commit',
                                'backup', 'validate', 'status', 'export']
                    self.assertIn('ALL devices may briefly reconnect', out.getvalue())
                else:
                    expected = (['preflight', 'prepare', 'backup', 'validate', 'status', 'export']
                                if case == ('deploy', 'unchanged') else
                                ['preflight', 'prepare', 'backup', 'validate', 'commit', 'backup', 'validate', 'status', 'export'])
                self.assertEqual(events, expected)
                self.assertEqual(vps.load(state / 'state.json')['current'], self.record)
                self.assertFalse((state / 'pending.json').exists())

    def test_no_input_deploy_keeps_preflight_backup_and_publication_gates(self):
        for failure in ('owner', 'preflight', 'backup', 'native/TLS'):
            with self.subTest(failure=failure):
                state = vps.private_dir(self.state / failure.replace('/', '-'))
                archive = state / 'backup.tar'
                host.atomic(archive, 'isolated backup')
                argv = ['--state-dir', str(state), 'restore', '--ssh', self.record['target'], '--backup', str(archive)]
                inspected = [{'owner': self.doc['owner'], 'config': self.config},
                             {'owner': 'foreign' if failure == 'owner' else self.doc['owner'], 'config': self.config}]
                with mock.patch('builtins.input', side_effect=AssertionError('Unexpected input')) as no_input, \
                     mock.patch('sys.stdin', io.StringIO('')), mock.patch('sys.stdout', new_callable=io.StringIO), \
                     mock.patch('sys.stderr', new_callable=io.StringIO), \
                     mock.patch.object(vps, 'target_record', return_value=dict(self.record)), \
                     mock.patch.object(vps, 'inspect_backup', side_effect=inspected), \
                     mock.patch.object(vps, 'remote', side_effect=host.Error('owner/machine preflight') if failure == 'preflight'
                                       else lambda *a, **kw: {'machine_id': 'a' * 32, 'unchanged': False}) as remote, \
                     mock.patch.object(vps, 'upload_bundle', return_value=io.BytesIO()) as bundle, \
                     mock.patch.object(vps, 'backup_remote', side_effect=host.Error('off-node receipt failed') if failure == 'backup'
                                       else None, return_value=archive), \
                     mock.patch.object(vps, 'validate_outputs', side_effect=host.Error('native/TLS failed')) as validate, \
                     mock.patch.object(vps, 'export_after_commit') as export:
                    self.assertEqual(vps.main(argv), 1)
                no_input.assert_not_called()
                export.assert_not_called()
                self.assertNotIn('commit', [c.args[1] for c in remote.call_args_list])
                if failure in ('owner', 'preflight'):
                    bundle.assert_not_called()
                    self.assertFalse((state / 'pending.json').exists())
                else:
                    self.assertTrue((state / 'pending.json').exists())
                if failure != 'native/TLS':
                    validate.assert_not_called()

    def test_no_input_device_prechecks_still_refuse_before_backup(self):
        self.doc['current'] = self.record
        host.atomic(self.state / 'state.json', host.dumps(self.doc))
        for failure in ('pending', 'core', 'rename-history'):
            with self.subTest(failure=failure):
                pending = self.state / 'pending.json'
                if failure == 'pending':
                    host.atomic(pending, 'preserved pending intent')
                with mock.patch('builtins.input', side_effect=AssertionError('Unexpected input')) as no_input, \
                     mock.patch('sys.stdin', io.StringIO('')), mock.patch('sys.stderr', new_callable=io.StringIO), \
                     mock.patch.object(vps, 'resolve_mihomo', side_effect=host.Error('Missing native core') if failure == 'core'
                                       else None, return_value='/fixture/mihomo'), \
                     mock.patch.object(vps, 'remote', return_value={'adapter_sha256': host.DEPLOYED_ADAPTER_SHA256,
                                       'devices': ['home'], 'rename_target_has_history': True}) as remote, \
                     mock.patch.object(vps, 'backup_remote') as backup, mock.patch.object(vps, 'upload_bundle') as bundle:
                    self.assertEqual(vps.main(['--state-dir', str(self.state), 'devices', 'rename', 'home', 'tablet']), 1)
                no_input.assert_not_called()
                backup.assert_not_called()
                bundle.assert_not_called()
                if failure == 'pending':
                    self.assertEqual(pending.read_text(), 'preserved pending intent')
                    pending.unlink()
                else:
                    self.assertFalse(pending.exists())
                if failure != 'rename-history':
                    remote.assert_not_called()

    def test_missing_configuration_is_data_not_confirmation(self):
        args = self.args()
        with mock.patch('sys.stdin', io.StringIO('')), \
             mock.patch('builtins.input', side_effect=AssertionError('No input on EOF')) as no_input:
            with self.assertRaisesRegex(host.Error, 'requires --domain and --reality-target'):
                vps.configuration(args)
        no_input.assert_not_called()
        with mock.patch.object(sys.stdin, 'isatty', return_value=True), \
             mock.patch('builtins.input', side_effect=['sub.example.org', 'target.example.org']) as data:
            config = vps.configuration(args)
        self.assertEqual(data.call_count, 2)
        self.assertEqual(config, dict(self.config, acme_email=''))
        self.assertEqual([call.args[0] for call in data.call_args_list],
                         ['Grey DNS subscription hostname: ', 'Verified REALITY TLS1.3+h2 target hostname: '])

    def test_device_unknown_adapter_or_transport_refuses_before_backup_stop(self):
        self.doc['current'] = self.record
        args = vps.parser().parse_args(['devices', 'add', 'device4', 'device5'])
        for status in ({'healthy': True, 'adapter_sha256': 'unknown'}, host.Error('fixture transport unavailable')):
            kwargs = {'side_effect': status} if isinstance(status, Exception) else {'return_value': status}
            with mock.patch.object(vps, 'remote', **kwargs) as remote, mock.patch.object(vps, 'backup_remote') as backup, \
                    mock.patch.object(vps.shutil, 'which', return_value='/fixture/mihomo'), mock.patch('builtins.input', side_effect=AssertionError('Unexpected input')) as no_input:
                with self.assertRaises(host.Error):
                    vps.devices(self.state, self.doc, args)
                remote.assert_called_once_with(self.record, 'status', vps.request_for(self.doc, self.record))
                backup.assert_not_called()
                no_input.assert_not_called()
                self.assertFalse((self.state / 'pending.json').exists())

    def test_recover_retains_custom_core_and_accepts_override_for_legacy_pending(self):
        executable = self.state / 'custom-mihomo'
        host.atomic(executable, '#!/bin/sh\nexit 0\n', 0o700)
        with mock.patch.dict(os.environ, PATH=str(self.state)):
            self.assertEqual(vps.resolve_mihomo('custom-mihomo'), str(executable))
        self.assertEqual(vps.parser().parse_args(['recover','--mihomo',str(executable)]).mihomo,str(executable))
        self.doc['current'] = self.record
        request=dict(vps.request_for(self.doc,self.record),device_action='reset',names=['home'],operation_id='c'*32)
        host.atomic(self.state/'fixture.tar', 'postcommit archive fixture')
        for saved, override in ((str(executable), None), ('/vanished/core',str(executable)), (None,str(executable))):
            pending = {'record':self.record,'mode':'devices','request':request,'mihomo':saved}
            host.atomic(self.state/'pending.json',host.dumps(pending))
            with mock.patch.object(vps,'remote',return_value={'committed':True,'machine_id':'a'*32}), \
                    mock.patch.object(vps,'backup_remote',return_value=self.state/'fixture.tar'), \
                    mock.patch.object(vps,'validate_outputs') as validate, mock.patch.object(vps,'export_after_commit'):
                vps.recover_local(self.state,self.doc,override)
                self.assertEqual(validate.call_args.kwargs['binary'],str(executable))
        host.atomic(self.state/'pending.json',host.dumps({'record':self.record,'mode':'devices','request':request,'mihomo':'/vanished/core'}))
        with mock.patch.object(vps,'remote',return_value={'committed':True,'machine_id':'a'*32}), \
                mock.patch.object(vps,'backup_remote',return_value=self.state/'fixture.tar'), \
                mock.patch.object(vps,'validate_outputs') as validate:
            with self.assertRaisesRegex(host.Error,'Mihomo'):
                vps.recover_local(self.state,self.doc)
            validate.assert_not_called()
            self.assertTrue((self.state/'pending.json').exists())

    def test_rename_refuses_invalid_missing_or_collision_before_backup(self):
        self.doc['current']=self.record
        for names in (['home','home'],['missing','tablet'],['home','phone'],['home','Bad/Name']):
            args=vps.parser().parse_args(['devices','rename',*names])
            status={'adapter_sha256':host.DEPLOYED_ADAPTER_SHA256,'devices':['company','home','phone']}
            with mock.patch.object(vps,'remote',return_value=status),mock.patch.object(vps,'backup_remote') as backup, \
                    mock.patch('builtins.input', side_effect=AssertionError('Unexpected input')) as no_input,mock.patch.object(vps,'resolve_mihomo',return_value='/core'):
                with self.assertRaises(host.Error):vps.devices(self.state,self.doc,args)
                backup.assert_not_called();no_input.assert_not_called()
                self.assertFalse((self.state/'pending.json').exists())

    def test_removed_rotate_refuses_and_old_pending_is_preserved(self):
        args=vps.parser().parse_args(['devices','reset','home'])
        self.assertEqual(args.device_action,'reset')
        with mock.patch('sys.stderr'),mock.patch.object(vps,'remote') as remote,mock.patch.object(vps,'backup_remote') as backup:
            with self.assertRaises(SystemExit) as exit:
                vps.main(['devices','rotate','home'])
            self.assertEqual(exit.exception.code,2);remote.assert_not_called();backup.assert_not_called()
        request=dict(vps.request_for(self.doc,self.record),device_action='rotate',names=['home'],operation_id='a'*32)
        with self.assertRaisesRegex(host.Error,'Unsupported device action'):host.device_request(request)
        self.assertTrue(host.device_request(dict(request,device_action='reset')))
        original=host.dumps({'record':self.record,'mode':'devices','request':request})
        host.atomic(self.state/'pending.json',original)
        with mock.patch.object(vps,'remote') as remote,mock.patch.object(vps,'backup_remote') as backup:
            with self.assertRaisesRegex(host.Error,'Unsupported device action'):
                vps.recover_local(self.state,self.doc)
            remote.assert_not_called();backup.assert_not_called()
        self.assertEqual((self.state/'pending.json').read_text(),original)
        request.update(device_action='rename',names=['home','tablet'])
        self.assertNotEqual(host.device_request(request),host.device_request(dict(request,names=['tablet','home'])))

    def test_committed_rename_cleanup_is_exact_and_recoverable(self):
        sync = mock.patch.object(vps, 'sync_outputs')  # This test isolates the exact legacy rename cleanup hook.
        sync.start(); self.addCleanup(sync.stop)
        self.doc['current']=self.record
        output=vps.private_dir(self.state/'outputs')
        outside=self.state/'outside';host.atomic(outside,'outside-unchanged')
        request=dict(vps.request_for(self.doc,self.record),device_action='rename',names=['home','tablet'],operation_id='b'*32)
        host.atomic(self.state/'pending.json',host.dumps({'record':self.record,'mode':'devices','request':request}))
        (output/'home.yaml').symlink_to(outside)
        host.atomic(output/'home.txt','managed-old');host.atomic(output/'tablet.yaml','new-current')
        host.atomic(output/'unrelated.txt','unrelated')
        backup=self.state/'postcommit.tar'
        host.atomic(backup, 'postcommit archive fixture')
        with self.assertRaisesRegex(host.Error,'already committed'):
            vps.remember_commit(self.state,self.doc,self.record,backup,{'machine_id':'a'*32})
        self.assertTrue((self.state/'pending.json').exists())
        self.assertEqual(outside.read_text(),'outside-unchanged')
        (output/'home.yaml').unlink();host.atomic(output/'home.yaml','managed-old')
        vps.remember_commit(self.state,self.doc,self.record,backup,{'machine_id':'a'*32})
        self.assertFalse((self.state/'pending.json').exists())
        self.assertFalse((output/'home.yaml').exists());self.assertFalse((output/'home.txt').exists())
        self.assertEqual((output/'tablet.yaml').read_text(),'new-current')
        self.assertEqual((output/'unrelated.txt').read_text(),'unrelated')
        self.assertEqual(outside.read_text(),'outside-unchanged')

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
             mock.patch('builtins.input', side_effect=AssertionError('Unexpected input')), mock.patch.object(vps, 'inspect_backup', return_value=manifest), \
             mock.patch.object(vps, 'backup_remote', return_value=Path(self.doc['backup'])), mock.patch.object(vps, 'validate_outputs'), \
             mock.patch.object(vps, 'artifacts', side_effect=AssertionError('release network required')), \
             mock.patch.object(vps, 'export_after_commit') as export:
            vps.deploy(self.state, self.doc, self.args('replace'))
        export.assert_called_once_with(self.state)
        self.assertTrue(all(target != 'old-fixture' for target, _ in calls))
        self.assertEqual([action for _, action in calls], ['preflight', 'prepare', 'commit', 'status'])
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
             mock.patch('builtins.input', side_effect=AssertionError('Unexpected input')), mock.patch.object(vps, 'artifacts', return_value=self.state), \
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
             mock.patch.object(vps, 'validate_outputs'), mock.patch.object(vps, 'export_after_commit') as export:
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
