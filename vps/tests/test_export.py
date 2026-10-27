"""Committed local URL copy: real Git exclusion, no production state or contact."""
import copy
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
import host
import vps


class Export(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='qd-url-export-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.module = self.repo / 'vps'
        self.module.mkdir(parents=True)
        self.git('init', '-q')
        source = Path(__file__).resolve().parents[2]
        (self.repo / '.gitignore').write_bytes((source / '.gitignore').read_bytes())
        (self.module / 'release-lock.json').write_bytes((source / 'vps/release-lock.json').read_bytes())
        (self.module / 'lib').mkdir()
        (self.module / 'lib/host.py').write_bytes((source / 'vps/lib/host.py').read_bytes())
        self.state = vps.private_dir(self.root / 'authority')
        self.record = {'target': 'fixture', 'endpoint': '93.184.216.34', 'machine_id': 'a' * 32, 'port': 22}
        config = {'domain': 'sub.example.org', 'reality_target': 'target.example.org', 'acme_email': '', 'proxy_port': 443}
        self.manifest = host.new_manifest('fixture-owner', config, self.record['endpoint'])
        self.manifest['published'] = True
        self.doc = {'format': host.FORMAT, 'owner': self.manifest['owner'], 'config': config,
                    'current': self.record, 'previous': [], 'backup': str(self.state / 'backup.tar')}
        host.atomic(self.state / 'state.json', host.dumps(self.doc))
        host.atomic(Path(self.doc['backup']), 'verified-backup-fixture')
        self.destination = self.module / 'subscription-urls.json'
        for patch in (mock.patch.object(vps, 'MODULE', self.module),
                      mock.patch.object(vps, 'inspect_backup', return_value=self.manifest),
                      mock.patch.object(vps, 'remote', side_effect=AssertionError('remote contact forbidden'))):
            patch.start()
            self.addCleanup(patch.stop)

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True, capture_output=True)

    def test_offline_cli_repeated_export_matches_committed_enabled_accounts(self):
        self.manifest['accounts']['phone']['enable'] = False
        # Candidate output is deliberately wrong and must never be a source.
        vps.private_dir(self.state / 'outputs')
        host.atomic(self.state / 'outputs/subscription-urls.json', '{"candidate": "not-authority"}')
        original = (self.state / 'state.json').read_bytes()
        directory_mode = self.module.stat().st_mode
        expected = vps.subscription_urls(self.manifest)
        with mock.patch('sys.stdout', new_callable=io.StringIO) as out:
            self.assertEqual(vps.main(['--state-dir', str(self.state), 'export']), 0)
            first = self.destination.read_bytes()
            self.assertEqual(vps.main(['--state-dir', str(self.state), 'export']), 0)
        self.assertEqual(self.destination.read_bytes(), first)
        self.assertEqual(json.loads(first), expected)
        self.assertNotIn('phone', json.loads(first))
        self.assertNotIn('https://', out.getvalue())
        self.assertEqual((self.state / 'state.json').read_bytes(), original)
        self.assertEqual(self.module.stat().st_mode, directory_mode)
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o600)
        self.git('check-ignore', '-q', '--', 'vps/subscription-urls.json')
        self.assertFalse(self.git('ls-files', '--', 'vps/subscription-urls.json').stdout)
        self.assertFalse(self.git('status', '--short', '--untracked-files=all', '--', 'vps/subscription-urls.json').stdout)

    def test_dynamic_roster_export_add_rotate_remove_and_empty(self):
        import uuid
        original = vps.subscription_urls(self.manifest)
        for name in ('device4', 'device5'):
            self.manifest['accounts'][name] = {'email': name, 'id': str(uuid.uuid4()),
                'subId': uuid.uuid4().hex, 'enable': True, 'flow': 'xtls-rprx-vision'}
        vps.export_urls(self.state)
        expanded = json.loads(self.destination.read_bytes())
        self.assertEqual(len(expanded), 5)
        self.assertEqual({name: expanded[name] for name in original}, original)
        self.manifest['accounts']['device4'].update(id=str(uuid.uuid4()), subId=uuid.uuid4().hex)
        vps.export_urls(self.state)
        rotated = json.loads(self.destination.read_bytes())
        self.assertNotEqual(rotated['device4'], expanded['device4'])
        self.assertEqual({name: rotated[name] for name in original}, original)
        del self.manifest['accounts']['device5']
        vps.export_urls(self.state)
        self.assertNotIn('device5', json.loads(self.destination.read_bytes()))
        self.manifest['accounts'] = {}
        vps.export_urls(self.state)
        self.assertEqual(json.loads(self.destination.read_bytes()), {})
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.git('ls-files', '--', 'vps/subscription-urls.json').stdout)
        self.git('check-ignore', '-q', '--', 'vps/subscription-urls.json')

    def test_no_uncommitted_or_mismatched_snapshot_can_replace_export(self):
        vps.export_urls(self.state)
        original = self.destination.read_bytes()
        for field, value in [('owner', 'foreign'), ('config', {}), ('endpoint', '93.184.216.35')]:
            with mock.patch.dict(self.manifest, {field: value}):
                with self.assertRaisesRegex(host.Error, 'does not match'):
                    vps.export_urls(self.state)
            self.assertEqual(self.destination.read_bytes(), original)
        for key in ('current', 'backup'):
            invalid = dict(self.doc, **{key: None})
            host.atomic(self.state / 'state.json', host.dumps(invalid))
            with self.assertRaisesRegex(host.Error, 'committed'):
                vps.export_urls(self.state)
        host.atomic(self.state / 'state.json', host.dumps(self.doc))
        host.atomic(self.state / 'pending.json', '{}')
        with self.assertRaisesRegex(host.Error, 'Pending'):
            vps.export_urls(self.state)
        self.assertEqual(self.destination.read_bytes(), original)

    def test_git_missing_ignore_or_tracked_destination_refuses_before_write(self):
        (self.repo / '.gitignore').write_text('')
        with self.assertRaises(host.Error):
            vps.export_urls(self.state)
        self.assertFalse(self.destination.exists())
        (self.repo / '.gitignore').write_text('/vps/subscription-urls.json\n')
        with self.assertRaisesRegex(host.Error, 'temporary files must be Git-ignored'):
            vps.export_urls(self.state)
        self.assertFalse(self.destination.exists())
        (self.repo / '.gitignore').write_text('/vps/subscription-urls.json\n/vps/.subscription-urls-*.tmp\n')
        host.atomic(self.destination, 'old fixture')
        self.git('add', '-f', '--', 'vps/subscription-urls.json')
        with self.assertRaisesRegex(host.Error, 'tracked'):
            vps.export_urls(self.state)
        self.assertEqual(self.destination.read_text(), 'old fixture')

    def test_tracked_temporary_path_also_refuses_without_untracking(self):
        temporary = self.module / '.subscription-urls-fixture.tmp'
        host.atomic(temporary, 'preexisting fixture')
        self.git('add', '-f', '--', 'vps/' + temporary.name)
        with self.assertRaisesRegex(host.Error, 'tracked'):
            vps.export_urls(self.state)
        self.assertFalse(self.destination.exists())
        self.assertEqual(temporary.read_text(), 'preexisting fixture')
        self.assertTrue(self.git('ls-files', '--', 'vps/' + temporary.name).stdout)

    def test_existing_temporary_collision_is_not_deleted(self):
        temporary = self.module / '.subscription-urls-fixture.tmp'
        host.atomic(temporary, 'preexisting fixture')
        with mock.patch.object(vps.secrets, 'token_hex', return_value='fixture'):
            with self.assertRaises(FileExistsError):
                vps.export_urls(self.state)
        self.assertEqual(temporary.read_text(), 'preexisting fixture')
        self.assertFalse(self.destination.exists())

    def test_symlink_and_nonprivate_destination_refuse_without_external_write(self):
        outside = vps.private_dir(self.root / 'outside')
        target = outside / self.destination.name
        host.atomic(target, 'outside unchanged')
        link = self.repo / 'linked-module'
        link.symlink_to(self.module, target_is_directory=True)
        with mock.patch.object(vps, 'MODULE', link):
            with self.assertRaisesRegex(host.Error, 'symlink'):
                vps.export_urls(self.state)
        self.destination.symlink_to(target)
        with self.assertRaisesRegex(host.Error, 'symlink'):
            vps.export_urls(self.state)
        self.destination.unlink()
        host.atomic(self.destination, 'old fixture')
        self.destination.chmod(0o644)
        with self.assertRaisesRegex(host.Error, 'mode0600'):
            vps.export_urls(self.state)
        self.assertEqual(target.read_text(), 'outside unchanged')
        self.assertEqual(self.destination.read_text(), 'old fixture')

    def test_atomic_failure_preserves_export_and_cleans_temporary_file(self):
        vps.export_urls(self.state)
        original = self.destination.read_bytes()
        def failed_replace(*args, **kwargs):
            temporary, = self.module.glob('.subscription-urls-*.tmp')
            self.assertEqual(temporary.stat().st_mode & 0o777, 0o600)
            self.git('check-ignore', '-q', '--', 'vps/' + temporary.name)
            self.assertFalse(self.git('status', '--short', '--untracked-files=all', '--', 'vps/' + temporary.name).stdout)
            raise OSError('injected atomic failure')
        with mock.patch.object(vps.os, 'replace', side_effect=failed_replace):
            with self.assertRaises(OSError):
                vps.export_urls(self.state)
        self.assertEqual(self.destination.read_bytes(), original)
        self.assertFalse(list(self.module.glob('.subscription-urls-*.tmp')))

    def test_successful_deploy_replace_restore_and_unchanged_use_committed_snapshot(self):
        for command, unchanged in [('deploy', False), ('deploy', True), ('replace', False), ('restore', False)]:
            with self.subTest(command=command, unchanged=unchanged):
                document = copy.deepcopy(self.doc)
                if command in ('replace', 'restore'):
                    document['current']['endpoint'] = '93.184.216.35'
                elif not unchanged:
                    document['current'] = None  # Fresh deploy; repeated dual uses status only.
                before = copy.deepcopy(self.manifest)
                args = vps.parser().parse_args([command, '--ssh', 'fixture'])
                def remote(record, action, *args, **kwargs):
                    if action == 'prepare':
                        return {'unchanged': unchanged}
                    return {'machine_id': self.record['machine_id'], 'adapter_sha256': host.digest(self.module / 'lib/host.py'),
                            'needs_transport_upgrade': False, 'transports': ['tcp-reality', 'hysteria2']}
                def check_at_export(state):
                    self.assertFalse((state / 'pending.json').exists())
                    self.assertEqual(vps.load(state / 'state.json')['current'], self.record)
                    return original_export(state)
                original_export = vps.export_urls
                with mock.patch.object(vps, 'target_record', return_value=self.record), \
                     mock.patch.object(vps, 'remote', side_effect=remote) as rem, \
                     mock.patch('builtins.input', side_effect=AssertionError('Unexpected input')), mock.patch.object(vps, 'artifacts'), \
                     mock.patch.object(vps, 'upload_bundle', return_value=io.BytesIO()), \
                     mock.patch.object(vps, 'backup_remote', return_value=Path(self.doc['backup'])), \
                     mock.patch.object(vps, 'validate_outputs'), \
                     mock.patch.object(vps, 'export_urls', side_effect=check_at_export) as export, \
                     mock.patch('sys.stdout', new_callable=io.StringIO):
                    vps.deploy(self.state, document, args)
                export.assert_called_once_with(self.state)
                self.assertEqual(self.manifest, before)
                self.assertEqual(json.loads(self.destination.read_bytes()), vps.subscription_urls(before))
                self.assertEqual([c.args[1] for c in rem.call_args_list][-1], 'status')
                self.assertEqual([c.args[1] for c in rem.call_args_list].count('commit'), 0 if unchanged else 1)

    def test_postcommit_export_failure_preserves_commit_not_false_pending(self):
        for recovering in (False, True):
            document = copy.deepcopy(self.doc)
            if recovering:
                host.atomic(self.state / 'pending.json', host.dumps({'record': self.record}))
            def remote(record, action, *args, **kwargs):
                if action == 'prepare':
                    return {'unchanged': True}
                return {'machine_id': self.record['machine_id'], 'committed': True,
                        'adapter_sha256': host.digest(self.module / 'lib/host.py'),
                        'needs_transport_upgrade': False, 'transports': ['tcp-reality', 'hysteria2']}
            with mock.patch.object(vps, 'target_record', return_value=self.record), \
                 mock.patch.object(vps, 'remote', side_effect=remote), mock.patch('builtins.input', side_effect=AssertionError('Unexpected input')), \
                 mock.patch.object(vps, 'artifacts'), mock.patch.object(vps, 'upload_bundle', return_value=io.BytesIO()), \
                 mock.patch.object(vps, 'backup_remote', return_value=Path(self.doc['backup'])), \
                 mock.patch.object(vps, 'validate_outputs'), \
                 mock.patch.object(vps, 'export_urls', side_effect=host.Error('injected export error')), \
                 mock.patch('sys.stderr', new_callable=io.StringIO) as err, \
                 mock.patch('sys.stdout', new_callable=io.StringIO):
                with self.assertRaisesRegex(host.Error, 'already committed.*export failed'):
                    if recovering:
                        vps.recover_local(self.state, document)
                    else:
                        vps.deploy(self.state, document, vps.parser().parse_args(['deploy', '--ssh', 'fixture']))
            self.assertNotIn('Transaction incomplete', err.getvalue())
            self.assertFalse((self.state / 'pending.json').exists())
            self.assertEqual(vps.load(self.state / 'state.json')['current'], self.record)

    def test_failed_candidate_never_replaces_last_accepted_export(self):
        vps.export_urls(self.state)
        original = self.destination.read_bytes()
        with mock.patch.object(vps, 'target_record', return_value=self.record), \
             mock.patch.object(vps, 'remote', return_value={'machine_id': self.record['machine_id'], 'unchanged': False,
                 'adapter_sha256': host.digest(self.module / 'lib/host.py'), 'node_id': self.manifest['node_id'],
                 'needs_transport_upgrade': True, 'transports': ['tcp-reality']}) as rem, \
             mock.patch('builtins.input', side_effect=AssertionError('Unexpected input')), mock.patch.object(vps, 'artifacts'), \
             mock.patch.object(vps, 'upload_bundle', return_value=io.BytesIO()), \
             mock.patch.object(vps, 'backup_remote', return_value=Path(self.doc['backup'])), \
             mock.patch.object(vps, 'validate_outputs', side_effect=host.Error('candidate failed')), \
             mock.patch.object(vps, 'export_urls') as export, mock.patch('sys.stderr', new_callable=io.StringIO):
            with self.assertRaisesRegex(host.Error, 'candidate failed'):
                vps.deploy(self.state, self.doc, vps.parser().parse_args(['deploy', '--ssh', 'fixture']))
        export.assert_not_called()
        self.assertNotIn('commit', [c.args[1] for c in rem.call_args_list])
        self.assertNotIn('transport-commit', [c.args[1] for c in rem.call_args_list])
        self.assertTrue((self.state / 'pending.json').exists())
        self.assertEqual(self.destination.read_bytes(), original)

    def test_committed_recovery_exports_fresh_verified_snapshot(self):
        host.atomic(self.state / 'pending.json', host.dumps({'record': self.record}))
        self.manifest['accounts']['home']['enable'] = False
        before = copy.deepcopy(self.manifest)
        with mock.patch.object(vps, 'remote', return_value={'committed': True, 'machine_id': self.record['machine_id']}) as rem, \
             mock.patch.object(vps, 'backup_remote', return_value=Path(self.doc['backup'])), \
             mock.patch.object(vps, 'validate_outputs'), \
             mock.patch('sys.stdout', new_callable=io.StringIO):
            vps.recover_local(self.state, self.doc)
        self.assertEqual([c.args[1] for c in rem.call_args_list], ['abort'])
        self.assertFalse((self.state / 'pending.json').exists())
        self.assertEqual(json.loads(self.destination.read_bytes()), vps.subscription_urls(before))
        self.assertEqual(self.manifest, before)

    def test_aborted_recovery_does_not_export_candidate(self):
        host.atomic(self.state / 'pending.json', host.dumps({'record': self.record}))
        with mock.patch.object(vps, 'remote', return_value={'committed': False}), \
             mock.patch.object(vps, 'export_urls') as export, mock.patch('sys.stdout', new_callable=io.StringIO):
            vps.recover_local(self.state, self.doc)
        export.assert_not_called()
        self.assertFalse(self.destination.exists())


if __name__ == '__main__':
    unittest.main()
