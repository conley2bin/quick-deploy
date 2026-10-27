"""Local controller authority: real closed archives/hashes/SQLite, no SSH/native services."""
import base64
import contextlib
import copy
import hashlib
import io
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import bootstrap
import host
import vps


class DualCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='qd-dual-cli-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = vps.private_dir(self.root / 'private')
        self.config = {'domain': 'sub.example.org', 'reality_target': 'target.example.org', 'acme_email': '', 'proxy_port': 443}
        self.record = {'target': 'verified-alias', 'endpoint': '93.184.216.34', 'user': 'root', 'port': 2222,
                       'machine_id': 'a' * 32, 'options': list(vps.SSH_OPTIONS) + ['-p', '2222']}
        self.legacy = host.new_manifest('fixture-owner', self.config, self.record['endpoint'])
        self.legacy.update(published=True, host_group_id='main-group',
                           reality={'publicKey': 'y' * 43, 'privateKey': 'x' * 43, 'shortIds': ['0123456789abcdef']})
        self.dual = copy.deepcopy(self.legacy)
        self.dual['management'] = {'ssh_port': 2222}
        self.dual['hysteria2'] = {'schema': 1, 'inbound_id': 2, 'host_group_id': 'h2-group',
            'certificate_file': 'caddy/data/caddy/certificates/fixture/sub.example.org.crt',
            'key_file': 'caddy/data/caddy/certificates/fixture/sub.example.org.key'}
        for name, account in self.dual['accounts'].items():
            account['auth'] = 'test-auth-' + name + '-' + 'a' * 32
        self.lock = copy.deepcopy(vps.release_lock())
        self.public = self.root / 'public-artifacts'
        self.public.mkdir()
        for component in ('panel', 'core', 'front'):
            data = ('public-test-' + component).encode()
            pin = self.lock[component]
            pin['sha256'] = hashlib.sha256(data).hexdigest()
            (self.public / pin['file']).write_bytes(data)
            (self.public / pin['file']).chmod(0o644)
        patch = mock.patch.object(vps, 'release_lock', return_value=self.lock)
        patch.start(); self.addCleanup(patch.stop)
        self.cert, self.key = self.root / 'cert.pem', self.root / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes',
                        '-days', '1', '-subj', '/CN=sub.example.org', '-addext', 'subjectAltName=DNS:sub.example.org',
                        '-keyout', str(self.key), '-out', str(self.cert)], check=True, capture_output=True, timeout=5)
        self.before = self.archive(self.legacy, 'before')
        self.candidate = self.archive(dict(self.dual, published=False), 'candidate')
        self.after = self.archive(self.dual, 'after')
        self.doc = {'format': host.FORMAT, 'owner': self.legacy['owner'], 'config': self.config,
                    'current': copy.deepcopy(self.record), 'previous': [], 'backup': str(self.before)}
        host.atomic(self.state / 'state.json', host.dumps(self.doc))
        self.events = []
        self.current_outputs = self.feeds(self.dual)
        original = host.run
        def run(argv, **kwargs):
            if str(argv[0]) == '/fixture/mihomo':
                self.events.append('mihomo-t')
                return subprocess.CompletedProcess(argv, 0, b'', b'')
            return original(argv, **kwargs)
        patch = mock.patch.object(host, 'run', side_effect=run)
        patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(vps, 'resolve_mihomo', return_value='/fixture/mihomo')
        patch.start(); self.addCleanup(patch.stop)

    def archive(self, manifest, name):
        folder = self.root / ('tree-' + name)
        active = folder / 'deployment'
        (active / 'db').mkdir(parents=True)
        (active / 'artifacts').mkdir()
        (active / 'bin').mkdir()
        for component in ('panel', 'core', 'front'):
            pin = self.lock[component]
            (active / 'artifacts' / pin['file']).write_bytes((self.public / pin['file']).read_bytes())
        for file in ('geoip.dat', 'geosite.dat'):
            (active / 'bin' / file).write_bytes(b'local-format-gate-fixture')
        certificate = active / 'caddy/data/caddy/certificates/fixture/sub.example.org.crt'
        certificate.parent.mkdir(parents=True)
        certificate.write_bytes(self.cert.read_bytes()); certificate.with_suffix('.key').write_bytes(self.key.read_bytes())
        (active / 'release-lock.json').write_text(host.dumps(self.lock))
        (active / 'manifest.json').write_text(host.dumps(manifest))
        (active / 'binary-hashes.json').write_text('{}')
        (active / 'Caddyfile').write_text(host.caddyfile(manifest, manifest['published']))
        (folder / 'host.py').write_bytes(Path(host.__file__).read_bytes())
        (folder / 'units').mkdir()
        for unit, text in host.unit_texts().items():
            (folder / 'units' / unit).write_text(text)
        dual = host.dual_enabled(manifest)
        with sqlite3.connect(active / 'db/x-ui.db') as db:
            db.executescript('''create table users(id integer,username text);
                create table settings(key text,value text);
                create table inbounds(id integer,user_id integer,remark text,protocol text,port integer,settings text,stream_settings text,enable integer,tag text);
                create table hosts(group_id text,inbound_id integer,address text,port integer);
                create table clients(id integer,email text,uuid text,sub_id text,enable integer,flow text,auth text);
                create table client_inbounds(client_id integer,inbound_id integer);''')
            db.execute('insert into users values(1,?)', (manifest['admin_user'],))
            db.execute("insert into inbounds values(1,1,'SELF-main','vless',443,'{}','{}',1,'SELF-main')")
            db.execute('insert into hosts values(?,1,?,443)', ('main-group', manifest['endpoint']))
            for index, (device, account) in enumerate(manifest['accounts'].items(), 1):
                db.execute('insert into clients values(?,?,?,?,?,?,?)',
                    (index, device, account['id'], account['subId'], account['enable'], account['flow'], account.get('auth', '')))
                db.execute('insert into client_inbounds values(?,1)', (index,))
                if dual:
                    db.execute('insert into client_inbounds values(?,2)', (index,))
            if dual:
                inbound = host.hysteria_inbound(manifest, '/owned/' + manifest['hysteria2']['certificate_file'], '/owned/' + manifest['hysteria2']['key_file'])
                settings = {'version': 2, 'clients': [{'email': n, 'auth': c['auth']} for n, c in manifest['accounts'].items()]}
                db.execute("insert into inbounds values(2,1,'SELF-h2','hysteria',443,?,?,1,'SELF-h2')", (json.dumps(settings), inbound['streamSettings']))
                db.execute('insert into hosts values(?,2,?,443)', ('h2-group', manifest['endpoint']))
                template = {'outbounds': [{'tag': 'direct', 'protocol': 'freedom'}]}
                template['routing'] = {'rules': [host.management_rule(template, manifest, 'SELF-h2')]}
                db.execute('insert into settings values(?,?)', ('xrayTemplateConfig', json.dumps(template)))
            schema = '\n'.join(db.iterdump()).encode()
        (folder / 'database-schema.sql').write_bytes(schema)
        (folder / 'backup.json').write_text(host.dumps({'format': host.FORMAT, 'encryption': 'none',
            'schema_sha256': hashlib.sha256(schema).hexdigest()}))
        path = self.state / (name + '.tar')
        with tarfile.open(path, 'w') as archive:
            for file in folder.rglob('*'):
                if file.is_file():
                    archive.add(file, arcname=str(file.relative_to(folder)), recursive=False)
        path.chmod(0o600)
        # This is a real complete verifier, including SQLite, membership,
        # artifact hashes, owned certificates and the exact management rule.
        self.assertEqual(vps.inspect_backup(path, self.lock)['accounts'], manifest['accounts'])
        return path

    def feeds(self, manifest):
        outputs = {}
        for name, account in manifest['accounts'].items():
            if not account['enable']:
                continue
            uri = 'vless://' + account['id'] + '@' + manifest['endpoint'] + ':443?' + urlencode({
                'security': 'reality', 'flow': 'xtls-rprx-vision', 'pbk': manifest['reality']['publicKey'],
                'sid': manifest['reality']['shortIds'][0], 'sni': self.config['reality_target']})
            h2 = 'hysteria2://' + account['auth'] + '@' + manifest['endpoint'] + ':443?' + urlencode({
                'security': 'tls', 'sni': self.config['domain'], 'alpn': 'h3'})
            yaml = f'''proxies:
  - name: SELF-main
    type: vless
    server: {manifest['endpoint']}
    port: 443
    uuid: {account['id']}
  - name: SELF-h2
    type: hysteria2
    server: {manifest['endpoint']}
    port: 443
    password: {account['auth']}
    sni: {self.config['domain']}
    alpn: [h3]
    skip-cert-verify: false
proxy-groups:
  - name: PROXY
    type: select
    proxies: [SELF-h2, SELF-main]
rules:
  - DOMAIN-SUFFIX,bilibili.com,DIRECT
  - MATCH,PROXY
'''.encode()
            outputs[name + '.yaml'] = yaml
            outputs[name + '.txt'] = base64.b64encode((uri + '\n' + h2 + '\n').encode())
        return outputs

    def remote(self, record, action, request=None, stdin=None, **kwargs):
        self.events.append(action)
        self.assertEqual(record['endpoint'], self.record['endpoint'])
        if action == 'outputs':
            return {name: base64.b64encode(data).decode() for name, data in self.current_outputs.items()}
        if action == 'status':
            return {'machine_id': self.record['machine_id'], 'node_id': self.legacy['node_id'],
                    'adapter_sha256': host.digest(Path(host.__file__)), 'needs_transport_upgrade': not self.upgraded,
                    'transports': ['tcp-reality', 'hysteria2'] if self.upgraded else ['tcp-reality']}
        if action == 'transport-prepare':
            with tarfile.open(fileobj=stdin) as archive:
                self.assertEqual(set(archive.getnames()), {'request.json', 'host.py'})
                sent = json.load(archive.extractfile('request.json'))
            self.assertEqual(sent['ssh_port'], 2222)
            self.assertEqual(sent['source_transports'], ['tcp-reality'])
            self.assertEqual(sent['target_transports'], ['tcp-reality', 'hysteria2'])
            self.assertEqual(sent['before_sha256'], host.digest(self.before))
            self.bound_request = sent
            return {'prepared': True}
        if action == 'transport-commit':
            self.assertEqual({k: v for k, v in request.items() if k != 'offnode_sha256'}, self.bound_request)
            self.assertEqual(request['offnode_sha256'], host.digest(self.candidate))
            self.assertNotIn('native_generation', vps.load(self.state / 'state.json'))
            self.upgraded = True
            return {'committed': True, 'machine_id': record['machine_id']}
        raise AssertionError('Unexpected remote action: ' + action)

    def capture(self, backup=None):
        with mock.patch.object(vps, 'remote', side_effect=self.remote):
            return vps.validate_outputs(self.state, self.doc, self.record, '/fixture/mihomo', backup or self.after)

    def publish(self):
        self.capture()
        vps.remember_commit(self.state, self.doc, self.record, self.after,
                            {'machine_id': self.record['machine_id'], 'node_id': self.dual['node_id']})

    def export_args(self, *extra):
        return vps.parser().parse_args(['export', '--device', 'company', '--bootstrap-dir', str(self.root / 'bootstrap'), *extra])

    def test_real_archive_candidate_never_publishes_and_native_bytes_export_exact(self):
        self.capture(self.candidate)
        self.assertNotIn('native_generation', vps.load(self.state / 'state.json'))
        with self.assertRaisesRegex(host.Error, 'No committed native generation'):
            vps.export_bootstrap(self.state, self.export_args())
        self.publish()
        # Candidate convenience outputs are not bootstrap authority.
        host.atomic(self.state / 'outputs/company.yaml', b'wrong candidate')
        with mock.patch.object(vps, 'remote', side_effect=AssertionError('offline only')), mock.patch('sys.stdout', new_callable=io.StringIO) as out:
            folder = vps.export_bootstrap(self.state, self.export_args())
        for ext in ('.yaml', '.txt'):
            self.assertEqual((folder / ('company' + ext)).read_bytes(), self.current_outputs['company' + ext])
        self.assertEqual((folder / 'company.uris.txt').read_bytes(), base64.b64decode(self.current_outputs['company.txt']))
        self.assertNotIn(self.dual['accounts']['company']['auth'], out.getvalue())
        self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in folder.iterdir()))
        self.assertFalse((folder / 'management.yaml').exists())

    def test_hash_tamper_cross_device_and_immutable_generation_refuse(self):
        self.publish()
        digest = self.doc['native_generation']
        path = self.state / 'generations' / digest / 'company.yaml'
        original = path.read_bytes()
        host.atomic(path, original + b'# tampered\n')
        with self.assertRaisesRegex(host.Error, 'hash changed'):
            vps.export_bootstrap(self.state, self.export_args())
        host.atomic(path, original)
        self.current_outputs['company.yaml'] = original + b'# changed native bytes\n'
        with self.assertRaisesRegex(host.Error, 'different bytes'):
            self.capture()
        self.assertEqual(path.read_bytes(), original)
        self.current_outputs['company.yaml'] = original + self.dual['accounts']['home']['auth'].encode()
        with self.assertRaisesRegex(host.Error, 'Cross-device'):
            self.capture(self.candidate)

    def test_pending_disabled_wrong_backup_and_symlink_refuse(self):
        self.publish()
        host.atomic(self.state / 'pending.json', '{}')
        with self.assertRaisesRegex(host.Error, 'Pending'):
            vps.export_bootstrap(self.state, self.export_args())
        (self.state / 'pending.json').unlink()
        self.doc['backup'] = str(self.before)
        host.atomic(self.state / 'state.json', host.dumps(self.doc))
        with self.assertRaisesRegex(host.Error, 'No committed native generation'):
            vps.export_bootstrap(self.state, self.export_args())
        self.doc['backup'] = str(self.after); host.atomic(self.state / 'state.json', host.dumps(self.doc))
        outside = self.root / 'outside'; outside.mkdir(mode=0o700)
        args = self.export_args(); args.bootstrap_dir.symlink_to(outside, target_is_directory=True)
        with self.assertRaises((host.Error, OSError)):
            vps.export_bootstrap(self.state, args)
        self.assertEqual(list(outside.iterdir()), [])
        args.bootstrap_dir.unlink()
        generation = self.state / 'generations' / self.doc['native_generation']
        file = generation / 'company.yaml'; data = file.read_bytes(); file.unlink(); file.symlink_to(self.cert)
        with self.assertRaises((host.Error, OSError)):
            vps.export_bootstrap(self.state, args)
        file.unlink(); host.atomic(file, data)
        disabled = copy.deepcopy(self.dual); disabled['accounts']['company']['enable'] = False
        archive = self.archive(disabled, 'disabled')
        self.current_outputs = self.feeds(disabled)
        self.capture(archive)
        vps.remember_commit(self.state, self.doc, self.record, archive, {'machine_id': self.record['machine_id']})
        with self.assertRaisesRegex(host.Error, 'disabled'):
            vps.export_bootstrap(self.state, args)

    @unittest.skipUnless(importlib.util.find_spec('yaml'), 'Optional PyYAML is needed only for management rendering')
    def test_management_is_optional_copies_native_node_and_prints_only_commands_paths(self):
        self.publish()
        args = self.export_args('--management-interface', 'eno1', '--management-port', '10809')
        with mock.patch('sys.stdout', new_callable=io.StringIO) as out:
            folder = vps.export_bootstrap(self.state, args)
        import yaml
        runtime = yaml.safe_load((folder / 'management.yaml').read_bytes())
        self.assertEqual(runtime['proxies'][0]['password'], self.dual['accounts']['company']['auth'])
        self.assertEqual(runtime['mixed-port'], 10809)
        self.assertIn('DST-PORT,2222', runtime['rules'][0])
        self.assertIn('--ssh-proxy socks5://127.0.0.1:10809', out.getvalue())
        self.assertNotIn(self.dual['accounts']['company']['auth'], out.getvalue())
        self.assertNotIn('https://', out.getvalue())

    def test_feature_action_sequence_verified_before_candidate_commit_and_after_snapshot(self):
        self.upgraded = False
        backups = iter((self.before, self.candidate, self.after))
        def backup(*args):
            self.events.append('backup')
            return next(backups)
        with mock.patch.object(vps, 'remote', side_effect=self.remote), mock.patch.object(vps, 'backup_remote', side_effect=backup), \
             mock.patch.object(vps, 'export_after_commit'), mock.patch('sys.stdout', new_callable=io.StringIO):
            vps.deploy(self.state, self.doc, vps.parser().parse_args(['deploy', '--mihomo', '/fixture/mihomo']))
        self.assertEqual([x for x in self.events if x != 'mihomo-t'],
                         ['status', 'backup', 'transport-prepare', 'backup', 'outputs', 'transport-commit', 'backup', 'outputs', 'status'])
        self.assertFalse((self.state / 'pending.json').exists())
        saved = vps.load(self.state / 'state.json')
        self.assertEqual(saved['native_generation'], host.digest(self.after))
        self.assertEqual(saved['backup'], str(self.after))
        self.assertEqual({n: {k: v for k, v in a.items() if k != 'auth'} for n, a in self.dual['accounts'].items()}, self.legacy['accounts'])

    def test_feature_failure_retains_request_and_recover_never_prepares_again(self):
        self.upgraded = False
        with mock.patch.object(vps, 'remote', side_effect=self.remote), \
             mock.patch.object(vps, 'backup_remote', side_effect=[self.before, self.candidate]), \
             mock.patch.object(vps, 'validate_outputs', side_effect=host.Error('candidate gate failed')), \
             mock.patch('sys.stderr', new_callable=io.StringIO):
            with self.assertRaisesRegex(host.Error, 'candidate gate failed'):
                vps.deploy(self.state, self.doc, vps.parser().parse_args(['deploy']))
        original = (self.state / 'pending.json').read_bytes()
        request = vps.load(self.state / 'pending.json')['request']
        with mock.patch.object(vps, 'remote', return_value={'pending': True, 'queued': True}) as remote, mock.patch('sys.stdout', new_callable=io.StringIO):
            vps.recover_local(self.state, self.doc)
        self.assertEqual(remote.call_args.args, (self.record, 'abort', request))
        self.assertEqual((self.state / 'pending.json').read_bytes(), original)
        self.events.clear()
        self.upgraded = True
        def recover(record, action, *args, **kwargs):
            if action == 'abort':
                self.events.append(action)
                return {'committed': True}
            return self.remote(record, action, *args, **kwargs)
        with mock.patch.object(vps, 'remote', side_effect=recover), mock.patch.object(vps, 'backup_remote', return_value=self.after), \
             mock.patch.object(vps, 'export_after_commit'), mock.patch('sys.stdout', new_callable=io.StringIO):
            vps.recover_local(self.state, self.doc)
        self.assertEqual([e for e in self.events if e != 'mihomo-t'], ['abort', 'status', 'outputs'])
        self.assertFalse((self.state / 'pending.json').exists())
        self.assertEqual(self.doc['native_generation'], host.digest(self.after))

    def test_all_committed_recover_modes_capture_native_generation_without_replay(self):
        for mode in ('deploy', 'replace', 'restore', 'backup', 'devices', 'transport-upgrade'):
            with self.subTest(mode=mode):
                document = copy.deepcopy(self.doc)
                pending = {'mode': mode, 'record': self.record, 'mihomo': '/fixture/mihomo'}
                if mode == 'devices':
                    pending['request'] = dict(vps.request_for(document, self.record), device_action='reset', names=['home'],
                                              operation_id='1' * 32)
                elif mode == 'transport-upgrade':
                    pending['request'] = dict(vps.request_for(document, self.record), mode=mode, operation_id='2' * 32,
                        adapter_sha256=host.digest(Path(host.__file__)), before_sha256=host.digest(self.before),
                        source_transports=['tcp-reality'], target_transports=['tcp-reality', 'hysteria2'])
                host.atomic(self.state / 'state.json', host.dumps(document))
                host.atomic(self.state / 'pending.json', host.dumps(pending))
                self.events.clear(); self.upgraded = True
                def remote(record, action, request=None, **kwargs):
                    if action == 'abort':
                        self.events.append(action)
                        self.assertEqual(request, pending.get('request', vps.request_for(document, record)))
                        return {'committed': True}
                    return self.remote(record, action, request, **kwargs)
                with mock.patch.object(vps, 'remote', side_effect=remote), \
                     mock.patch.object(vps, 'backup_remote', return_value=self.after), \
                     mock.patch.object(vps, 'export_after_commit'), mock.patch('sys.stdout', new_callable=io.StringIO):
                    vps.recover_local(self.state, document)
                self.assertEqual([x for x in self.events if x != 'mihomo-t'],
                                 ['abort', 'outputs'] if mode == 'backup' else ['abort', 'status', 'outputs'])
                self.assertEqual(vps.load(self.state / 'state.json')['native_generation'], host.digest(self.after))
                self.assertFalse((self.state / 'pending.json').exists())

    def test_missing_optional_yaml_is_explicit_and_does_not_write_output(self):
        self.publish()
        args = self.export_args('--management-interface', 'eno1')
        with mock.patch.object(bootstrap, '_yaml', side_effect=bootstrap.BootstrapError('Local bootstrap requires the optional PyYAML dependency')):
            with self.assertRaisesRegex(host.Error, 'optional PyYAML'):
                vps.export_bootstrap(self.state, args)
        self.assertFalse(args.bootstrap_dir.exists())

    def test_artifact_directory_all_sha_before_import_and_no_silent_fallback(self):
        (self.public / 'unrelated-private-backup.tar').write_text('not read')
        folder = vps.artifacts(self.state, self.lock, self.public)
        self.assertEqual({p.name for p in folder.iterdir()}, {self.lock[c]['file'] for c in ('panel', 'core', 'front')})
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in folder.iterdir()))
        original = {p.name: p.read_bytes() for p in folder.iterdir()}
        inodes = {p.name: p.stat().st_ino for p in folder.iterdir()}
        core = self.public / self.lock['core']['file']; good = core.read_bytes(); core.write_bytes(b'wrong')
        with self.assertRaisesRegex(host.Error, 'SHA256 mismatch'):
            vps.artifacts(self.state, self.lock, self.public)
        self.assertEqual({p.name: p.read_bytes() for p in folder.iterdir()}, original)
        self.assertEqual({p.name: p.stat().st_ino for p in folder.iterdir()}, inodes)
        core.unlink(); core.symlink_to(self.cert)
        with self.assertRaises((host.Error, OSError)):
            vps.artifacts(self.state, self.lock, self.public)
        core.unlink(); core.write_bytes(good)
        opener = mock.Mock(); opener.open.side_effect = AssertionError('valid cache downloaded')
        with mock.patch.object(vps, 'build_opener', return_value=opener):
            vps.artifacts(self.state, self.lock)
        for path in folder.iterdir(): path.unlink()
        opener.open.side_effect = HTTPError(self.lock['panel']['url'], 404, 'Not Found', {}, None)
        with mock.patch.object(vps, 'build_opener', return_value=opener):
            with self.assertRaisesRegex(host.Error, 'HTTP404.*--artifacts-dir'):
                vps.artifacts(self.state, self.lock)
        self.assertEqual(list(folder.iterdir()), [])

    def test_fresh_deploy_replace_restore_postpublication_snapshot_and_repeat_dual(self):
        for command in ('deploy', 'replace', 'restore'):
            self.events.clear()
            document = copy.deepcopy(self.doc)
            document['current'] = None if command == 'deploy' else dict(self.record, endpoint='93.184.216.35')
            host.atomic(self.state / 'state.json', host.dumps(document))
            def remote(record, action, request=None, stdin=None, **kwargs):
                if action in ('preflight', 'prepare', 'commit'):
                    self.events.append(action)
                    if action == 'commit':
                        self.assertEqual(request['offnode_sha256'], host.digest(self.candidate))
                        self.assertNotIn('native_generation', vps.load(self.state / 'state.json'))
                    return {'machine_id': self.record['machine_id'], 'unchanged': False}
                return self.remote(record, action, request, stdin, **kwargs)
            flags = [command, '--ssh', self.record['target']]
            if command == 'deploy': flags += ['--artifacts-dir', str(self.public)]
            if command == 'restore': flags += ['--backup', str(self.before)]
            backups = iter((self.candidate, self.after))
            def backup(*args):
                self.events.append('backup'); return next(backups)
            self.upgraded = True
            with mock.patch.object(vps, 'target_record', return_value=copy.deepcopy(self.record)), \
                 mock.patch.object(vps, 'remote', side_effect=remote), mock.patch.object(vps, 'backup_remote', side_effect=backup), \
                 mock.patch.object(vps, 'export_after_commit'), mock.patch('sys.stdout', new_callable=io.StringIO):
                vps.deploy(self.state, document, vps.parser().parse_args(flags))
            self.assertEqual([x for x in self.events if x != 'mihomo-t'],
                             ['preflight', 'prepare', 'backup', 'outputs', 'commit', 'backup', 'outputs', 'status'])
            self.assertEqual(document['native_generation'], host.digest(self.after))
        self.events.clear()
        with mock.patch.object(vps, 'remote', side_effect=self.remote), mock.patch.object(vps, 'backup_remote', return_value=self.after), \
             mock.patch.object(vps, 'export_after_commit'), mock.patch('sys.stdout', new_callable=io.StringIO):
            vps.deploy(self.state, document, vps.parser().parse_args(['deploy']))
        self.assertEqual([x for x in self.events if x != 'mihomo-t'], ['status', 'outputs'])

    def test_reset_rename_remove_generation_binding_and_missing_generation_clear(self):
        self.publish()
        initial = self.doc['native_generation']
        for operation in ('reset', 'rename', 'remove'):
            changed = copy.deepcopy(self.dual)
            if operation == 'reset':
                changed['accounts']['company'].update(id='11111111-1111-4111-8111-111111111111', auth='new-auth-' + 'b' * 32, subId='c' * 32)
            elif operation == 'rename':
                changed['accounts']['tablet'] = dict(changed['accounts'].pop('company'), email='tablet')
            else:
                del changed['accounts']['company']
            archive = self.archive(changed, 'committed-' + operation)
            self.current_outputs = self.feeds(changed)
            self.capture(archive)
            vps.remember_commit(self.state, self.doc, self.record, archive, {'machine_id': self.record['machine_id']})
            self.assertNotEqual(self.doc['native_generation'], initial)
            if operation != 'reset':
                with self.assertRaisesRegex(host.Error, 'removed'):
                    vps.export_bootstrap(self.state, self.export_args())
            else:
                with mock.patch('sys.stdout', new_callable=io.StringIO):
                    folder = vps.export_bootstrap(self.state, self.export_args())
                self.assertIn(changed['accounts']['company']['auth'].encode(), (folder / 'company.yaml').read_bytes())
                self.assertNotIn(self.dual['accounts']['company']['auth'].encode(), (folder / 'company.yaml').read_bytes())
        vps.remember_commit(self.state, self.doc, self.record, self.before, {'machine_id': self.record['machine_id']})
        self.assertNotIn('native_generation', self.doc)

    def test_legacy_url_export_remains_offline_without_generation(self):
        repo = self.root / 'repo'; module = repo / 'vps'; module.mkdir(parents=True)
        subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True)
        (repo / '.gitignore').write_text('/vps/subscription-urls.json\n/vps/.subscription-urls-*.tmp\n')
        with mock.patch.object(vps, 'MODULE', module), mock.patch.object(vps, 'remote', side_effect=AssertionError('offline only')):
            path = vps.export_urls(self.state)
        self.assertEqual(json.loads(path.read_bytes()), vps.subscription_urls(self.legacy))
        self.assertNotIn('native_generation', vps.load(self.state / 'state.json'))

    def test_unknown_topology_or_adapter_refuses_before_backup_and_pair_flags(self):
        self.upgraded = False
        for patch in ({'adapter_sha256': '0' * 64}, {'transports': ['unknown']}):
            status = self.remote(self.record, 'status') | patch
            with mock.patch.object(vps, 'remote', return_value=status), mock.patch.object(vps, 'backup_remote') as backup:
                with self.assertRaises(host.Error):
                    vps.deploy(self.state, self.doc, vps.parser().parse_args(['deploy']))
                backup.assert_not_called()
        with mock.patch('sys.stderr', new_callable=io.StringIO):
            for flags in (['--device', 'company'], ['--bootstrap-dir', str(self.root / 'out')],
                          ['--management-interface', 'eno1'], ['--management-interface', '']):
                self.assertEqual(vps.main(['--state-dir', str(self.state), 'export', *flags]), 1)
        args = vps.parser().parse_args(['deploy', '--artifacts-dir', str(self.public)])
        self.assertEqual(args.artifacts_dir, self.public)


if __name__ == '__main__':
    unittest.main()
