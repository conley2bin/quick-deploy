"""Owned fake-host lifecycle: actual file transitions, fake system interfaces.

Only an isolated copy of host.py sees rewritten /etc,/proc,/var paths. Every
service/kernel/network operation is a hard mock; unknown operations fail.
"""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import host
import vps


class FakeHost(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='qd-host-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        source = (vps.MODULE / 'lib/host.py').read_text()
        for prefix in ('/var/lib', '/etc', '/proc', '/run'):
            source = source.replace(prefix, str(self.root) + prefix)
        source = source.replace('ROOT.stat().st_uid != 0', 'ROOT.stat().st_uid != os.getuid()')
        script = self.root / 'host_fixture.py'
        script.write_text(source)
        spec = importlib.util.spec_from_file_location('host_fixture', script)
        self.h = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.h)
        self.h.UNITS.mkdir(parents=True)
        (self.root / 'var/lib').mkdir(parents=True)
        (self.root / 'proc/sys/kernel/random').mkdir(parents=True)
        (self.root / 'etc/os-release').write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
        (self.root / 'etc/machine-id').write_text('a' * 32)
        (self.root / 'proc/sys/kernel/random/boot_id').write_text('fixture-boot-1')
        self.config = {'domain': 'sub.example.org', 'reality_target': 'target.example.org', 'acme_email': 'operator@example.org', 'proxy_port': 443}
        self.request = {'owner': 'owner-fixture', 'endpoint': '93.184.216.34', 'config': self.config, 'mode': 'deploy',
                        'lock': vps.release_lock(), 'adapter_sha256': host.digest(vps.MODULE / 'lib/host.py')}
        self.events, self.services = [], set()
        self.unit_overrides = {}
        self.listeners = b''
        self.ruleset = b'{"nftables":[{"metainfo":{}}]}'
        self.patch(self.h.os, 'geteuid', return_value=0)
        self.patch(self.h.platform, 'machine', return_value='x86_64')
        self.patch(self.h.shutil, 'which', side_effect=lambda name: '/fake/' + name)
        self.patch(self.h.shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(10 * 1024**3, 1, 9 * 1024**3))
        self.patch(self.h, 'run', side_effect=self.run_command)
        self.patch(self.h, 'adapter_source', return_value=(vps.MODULE / 'lib/host.py').read_bytes())
        self.control_service = self.h.ctl
        self.patch(self.h, 'ctl', side_effect=self.ctl)
        self.patch(self.h, 'check_target')
        self.patch(self.h, 'verify_artifacts')
        self.patch(self.h, 'install_runtime', side_effect=self.install)
        self.patch(self.h, 'initialize_db', side_effect=self.initialize)
        self.patch(self.h, 'native_keys', return_value={'privateKey': 'x' * 43, 'publicKey': 'y' * 43, 'shortIds': ['0123456789abcdef']})
        self.patch(self.h, 'front_config', side_effect=lambda folder, manifest, published: self.h.atomic(folder / 'Caddyfile', self.h.caddyfile(manifest, published)))
        self.patch(self.h, 'configure_native', side_effect=self.configure)
        # This fake API/certificate fixture retains the legacy DB shape. It
        # proves transaction control; real dual activation is tested separately.
        self.patch(self.h, 'enable_hysteria')
        self.patch(self.h, 'backend_health', return_value={})
        self.patch(self.h, 'wait_tls')
        self.patch(self.h, 'tls_probe')
        self.process_health = self.h.process_health
        self.patch(self.h, 'process_health')
        self.env = mock.patch.dict(os.environ, QD_SSH_CONNECTION='198.51.100.1 12345 93.184.216.34 22')
        self.env.start()
        self.addCleanup(self.env.stop)

    def patch(self, obj, key, **kwargs):
        p = mock.patch.object(obj, key, **kwargs)
        result = p.start()
        self.addCleanup(p.stop)
        return result

    def run_command(self, argv, **kwargs):
        load_paths = [self.h.UNITS, self.root / 'run/systemd/system', self.root / 'usr/lib/systemd/system']
        if argv == ['systemd-analyze', 'unit-paths']:
            return subprocess.CompletedProcess(argv, 0, ('\n'.join(map(str, load_paths)) + '\n').encode())
        if argv[:2] == ['systemctl', 'show']:
            name = argv[2]
            if name not in self.h.UNIT_NAMES:
                raise AssertionError('Unknown unit query')
            fragment = next((str(path / name) for path in load_paths if (path / name).is_file()), '')
            props = {'LoadState': 'loaded' if fragment else 'not-found', 'FragmentPath': fragment,
                     'DropInPaths': '', 'SourcePath': '', 'NeedDaemonReload': 'no'}
            props.update(self.unit_overrides.get(name, {}))
            return subprocess.CompletedProcess(argv, 0, ('\n'.join(k + '=' + v for k, v in props.items()) + '\n').encode())
        if argv == ['nft', '-j', 'list', 'ruleset']:
            return subprocess.CompletedProcess(argv, 0, self.ruleset)
        if argv == ['ss', '-H', '-ltnp']:
            return subprocess.CompletedProcess(argv, 0, self.listeners)
        if argv == ['ss', '-H', '-lunp']:
            return subprocess.CompletedProcess(argv, 0, b'')
        raise AssertionError('Unmocked host command: ' + str(argv))

    def ctl(self, *args):
        self.events.append(args)
        if args[0] in ('start', 'restart'):
            self.services.update(args[1:])
        if args[0] in ('stop', 'disable'):
            self.services.difference_update(arg for arg in args[1:] if not arg.startswith('-'))

    def install(self, folder, lock):
        (folder / 'bin').mkdir(exist_ok=True)
        self.h.atomic(folder / 'release-lock.json', host.dumps(lock))
        self.h.atomic(folder / 'binary-hashes.json', '{}')

    def initialize(self, folder, manifest):
        import sqlite3
        (folder / 'db').mkdir()
        with sqlite3.connect(folder / 'db/x-ui.db') as db:
            db.execute('create table settings(id integer primary key, key text, value text)')
            db.execute('create table users(id integer primary key, username text, password text)')
            db.execute('insert into users values(1,?,?)', (manifest['admin_user'], 'fixture-only'))
            db.execute('create table inbounds(id integer primary key,user_id integer,remark text,protocol text,port integer)')
            db.execute('insert into inbounds values(1,1,?,?,?)', ('SELF-main', 'vless', manifest['config']['proxy_port']))
            db.execute('create table hosts(group_id text,inbound_id integer,address text,port integer,is_disabled integer,is_hidden integer)')
            db.execute('insert into hosts values(?,1,?,?,0,0)', ('owned-fixture-host', manifest['endpoint'], manifest['config']['proxy_port']))
            db.execute('create table clients(email text, uuid text, sub_id text, enable integer, flow text, id integer primary key)')
            db.execute('create table client_inbounds(client_id integer, inbound_id integer)')
            db.executemany('insert into clients(email,uuid,sub_id,enable,flow) values(?,?,?,?,?)',
                [(d, c['id'], c['subId'], c['enable'], c['flow']) for d, c in manifest['accounts'].items()])
            db.execute('insert into client_inbounds select id,1 from clients')
        self.h.settings_write(folder / 'db/x-ui.db', self.h.backend_settings(manifest))

    def configure(self, folder, manifest, fresh, rotate=False):
        import sqlite3
        import uuid
        with sqlite3.connect(folder / 'db/x-ui.db') as db:
            db.execute('update hosts set address=?', (manifest['endpoint'],))
            if rotate:
                for device in self.h.DEVICES:
                    db.execute('update clients set uuid=? where email=?', (str(uuid.uuid4()), device))
        manifest['accounts'] = self.h.snapshot_accounts(folder / 'db/x-ui.db')
        manifest['host_group_id'] = self.h.snapshot_host_group(folder / 'db/x-ui.db', manifest)

    def upload(self):
        with tempfile.TemporaryDirectory(dir=self.root) as tmp:
            folder = Path(tmp)
            for component in ('panel', 'core', 'front'):
                (folder / self.request['lock'][component]['file']).write_bytes(b'artifact fixture')
            with vps.upload_bundle(self.request, folder=folder) as stream:
                return self.h.prepare(stream)

    def test_process_health_attests_executables_and_loopback_not_just_syntax(self):
        self.config['proxy_port'] = 4443
        for name, text in self.h.unit_texts().items():
            self.h.atomic(self.h.UNITS / name, text)
        self.h.atomic(self.h.ROOT / 'active/manifest.json', host.dumps({'config': self.config}))
        lines = []
        for index, port in enumerate(self.h.required_ports(self.config), 1):
            unit = self.h.FRONT if port in (80, 8443) else self.h.PANEL
            binary = 'caddy' if unit == self.h.FRONT else ('xray-linux-amd64' if port in (self.config['proxy_port'], self.h.API_PORT) else 'x-ui')
            proc = self.root / 'proc' / str(index)
            proc.mkdir()
            (proc / 'cgroup').write_text('0::/system.slice/' + unit)
            (proc / 'exe').symlink_to(self.h.ROOT / 'active/bin' / binary)
            address = '127.0.0.1' if port in (self.h.ADMIN_PORT, self.h.SUB_PORT, self.h.API_PORT) else '0.0.0.0'
            lines.append(f'LISTEN 0 100 {address}:{port} 0.0.0.0:* users:(("native",pid={index},fd=1))')
        self.listeners = ('\n'.join(lines) + '\n').encode()
        self.process_health()
        self.listeners = self.listeners.replace(b'127.0.0.1:2053', b'0.0.0.0:2053')
        with self.assertRaisesRegex(self.h.Error, 'escaped loopback'):
            self.process_health()
        self.listeners = ('\n'.join(lines[:-1]) + '\n').encode()
        with self.assertRaisesRegex(self.h.Error, 'missing'):
            self.process_health()

    def test_preflight_os_wrong_address_machine_and_conflicts_before_ownership(self):
        self.assertFalse(self.h.preflight(self.request)['owned'])
        self.assertFalse(self.h.ROOT.exists())
        for change, match in [({'endpoint': '93.184.216.35'}, 'address'), ({'machine_id': 'b' * 32}, 'identity')]:
            with self.assertRaisesRegex(self.h.Error, match):
                self.h.preflight(dict(self.request, **change))
        (self.root / 'etc/os-release').write_text('ID=debian\nVERSION_ID=12\n')
        with self.assertRaisesRegex(self.h.Error, 'Ubuntu24.04'):
            self.h.preflight(self.request)
        self.assertEqual(self.events, [])
        self.assertFalse(self.h.ROOT.exists())

    def test_abort_before_bootstrap_and_partial_unit_recovery(self):
        request = dict(self.request, machine_id='a' * 32)
        self.assertTrue(self.h.abort(request)['pristine'])
        self.assertFalse(self.h.ROOT.exists())
        with self.assertRaisesRegex(self.h.Error, 'Wrong machine'):
            self.h.abort(dict(request, machine_id='b' * 32))
        self.h.ROOT.mkdir(mode=0o700)
        self.h.atomic(self.h.ROOT / 'owner.json', host.dumps({'format': self.h.FORMAT, 'owner': request['owner'], 'machine_id': request['machine_id']}))
        self.h.atomic(self.h.ROOT / 'pending.json', host.dumps({'boot_id': 'fixture-boot-1', 'deadline': 0, 'had_active': False}))
        self.assertFalse(self.h.abort(request)['pending'])
        self.assertFalse(self.h.preflight(request)['ports'] == [])
        with self.assertRaisesRegex(self.h.Error, 'actual SSH port'):
            self.h.preflight(dict(request, config=dict(self.config, proxy_port=22)))

    def test_foreign_resources_firewall_and_port_are_never_changed(self):
        legacy = self.root / 'var/lib/quick-deploy-vps'
        legacy.mkdir()
        sentinel = legacy / 'identity'
        sentinel.write_text('foreign secret')
        with self.assertRaisesRegex(self.h.Error, 'Legacy/foreign'):
            self.h.preflight(self.request)
        self.assertEqual(sentinel.read_text(), 'foreign secret')
        sentinel.unlink()
        legacy.rmdir()
        self.ruleset = b'{"nftables":[{"table":{"name":"foreign"}}]}'
        with self.assertRaisesRegex(self.h.Error, 'firewall'):
            self.h.preflight(self.request)
        self.ruleset = b'{"nftables":[]}'
        self.listeners = b'LISTEN 0 100 0.0.0.0:443 0.0.0.0:* users:(("foreign",pid=999,fd=1))\n'
        with self.assertRaisesRegex(self.h.Error, 'already in use'):
            self.h.preflight(self.request)
        self.assertFalse(self.h.ROOT.exists())
        self.assertEqual(self.events, [])

    def test_fresh_prepare_publish_and_repeat_no_rotate_no_restart(self):
        self.assertFalse(self.upload()['unchanged'])
        active = self.h.ROOT / 'active'
        first = (active / 'manifest.json').read_bytes()
        before = self.h.read_json(active / 'manifest.json')
        self.assertNotIn('@feed', (active / 'Caddyfile').read_text())
        self.assertTrue((self.h.ROOT / 'pending.json').exists())
        with self.assertRaisesRegex(self.h.Error, 'off-node backup'):
            self.h.commit(dict(self.request, machine_id='a' * 32, offnode_sha256='bad'))
        self.h.atomic(self.h.ROOT / 'export-sha256', 'verified-offnode')
        result = self.h.commit(dict(self.request, machine_id='a' * 32, offnode_sha256='verified-offnode'))
        self.assertTrue(result['published'])
        published = (active / 'Caddyfile').read_text()
        self.assertIn('@feed', published)
        self.assertNotIn('file_server', published)
        self.assertNotIn('http://0.0.0.0', published)
        self.events.clear()
        current = (active / 'manifest.json').read_bytes()
        inode = (active / 'manifest.json').stat().st_ino
        self.assertTrue(self.upload()['unchanged'])
        self.assertEqual((active / 'manifest.json').read_bytes(), current)
        self.assertEqual((active / 'manifest.json').stat().st_ino, inode)
        self.assertEqual(self.h.read_json(active / 'manifest.json')['accounts'], before['accounts'])
        self.assertEqual(self.events, [])

    def test_commit_waits_for_restarted_listener_and_rechecks_lease(self):
        import types
        for outcome in ('ready', 'expired', 'foreign', 'certificate'):
            with self.subTest(outcome=outcome):
                f = FakeHost()
                f.setUp()
                try:
                    f.upload()
                    pending = f.h.ROOT / 'pending.json'
                    f.h.atomic(f.h.ROOT / 'export-sha256', 'receipt')
                    # Run the real helper with this isolated host's globals.
                    wait = types.FunctionType(host.wait_tls.__code__, f.h.__dict__)
                    wait.__defaults__ = host.wait_tls.__defaults__
                    f.h.wait_tls.side_effect = wait
                    calls = []
                    def probe(manifest):
                        calls.append('probe')
                        if len(calls) == 2:
                            if outcome == 'certificate':
                                raise f.h.ssl.SSLCertVerificationError('invalid certificate')
                            raise ConnectionRefusedError('listener starting')
                    f.h.tls_probe.side_effect = probe
                    def sleep(seconds):
                        if outcome == 'expired':
                            journal = f.h.read_json(pending)
                            journal['deadline'] = 0
                            f.h.atomic(pending, f.h.dumps(journal))
                    with mock.patch.object(f.h.time, 'sleep', side_effect=sleep):
                        if outcome == 'foreign':
                            f.h.process_health.side_effect = [None, f.h.Error('Foreign listener')]
                        request = dict(f.request, machine_id='a' * 32, offnode_sha256='receipt')
                        if outcome == 'ready':
                            self.assertTrue(f.h.commit(request)['published'])
                            self.assertEqual(len(calls), 3)
                            self.assertFalse(pending.exists())
                        else:
                            with self.assertRaises((f.h.Error, f.h.ssl.SSLCertVerificationError)):
                                f.h.commit(request)
                            self.assertTrue(pending.exists())
                            self.assertFalse(f.h.read_json(f.h.ROOT / 'active/manifest.json')['published'])
                finally:
                    f.doCleanups()

    def test_bad_cert_cannot_publish_http_and_failure_keeps_ssh_sentinel(self):
        ssh = self.root / 'etc/ssh/sshd_config'
        ssh.parent.mkdir()
        ssh.write_text('foreign SSH unchanged')
        self.h.wait_tls.side_effect = self.h.Error('no trusted certificate')
        with self.assertRaisesRegex(self.h.Error, 'no trusted certificate'):
            self.upload()
        self.assertNotIn(self.h.PANEL, self.services)
        self.assertNotIn(self.h.FRONT, self.services)
        self.assertFalse((self.h.ROOT / 'pending.json').exists())
        failed = list(self.h.ROOT.glob('failed-*'))
        self.assertEqual(len(failed), 1)
        self.assertNotIn('@feed', (failed[0] / 'Caddyfile').read_text())
        self.assertEqual(ssh.read_text(), 'foreign SSH unchanged')
        self.assertTrue(all('ssh' not in str(event) and 'nft' not in str(event) for event in self.events))

    def test_persistent_pending_survives_ssh_loss_reboot_and_expiry(self):
        self.upload()
        result = self.h.recover()
        self.assertTrue(result['waiting'])
        self.h.guard()
        (self.root / 'proc/sys/kernel/random/boot_id').write_text('fixture-boot-2')
        with self.assertRaisesRegex(self.h.Error, 'across reboot'):
            self.h.guard()
        self.assertFalse(self.h.recover()['pending'])
        self.assertFalse((self.h.ROOT / 'active').exists())
        self.assertFalse(self.h.recover()['committed'])
        self.assertFalse(self.services.intersection({self.h.PANEL, self.h.FRONT}))
        self.upload()
        pending = self.h.read_json(self.h.ROOT / 'pending.json')
        pending['deadline'] = 0
        self.h.atomic(self.h.ROOT / 'pending.json', host.dumps(pending))
        self.assertFalse(self.h.recover()['pending'])

    def test_owned_marker_and_modified_unit_refuse_takeover(self):
        self.upload()
        self.h.recover(force=True)
        with self.assertRaisesRegex(self.h.Error, 'Foreign owned'):
            self.h.preflight(dict(self.request, owner='different-owner'))
        unit = self.h.UNITS / self.h.PANEL
        unit.write_text('foreign replacement unit')
        with self.assertRaisesRegex(self.h.Error, 'modified owned unit'):
            self.h.preflight(self.request)
        self.assertEqual(unit.read_text(), 'foreign replacement unit')

    def test_offnode_backup_replace_and_controlled_restore_preserve_owned_state(self):
        self.upload()
        old_root, old_units = self.h.ROOT, self.h.UNITS
        old_active = old_root / 'active'
        old = self.h.read_json(old_active / 'manifest.json')
        old['accounts']['company']['enable'] = False
        import sqlite3
        with sqlite3.connect(old_active / 'db/x-ui.db') as db:
            db.execute("update clients set enable=0 where email='company'")
        self.h.atomic(old_active / 'manifest.json', host.dumps(old))
        original = (old_active / 'manifest.json').read_bytes()
        stage = self.root / 'offnode-export'
        stage.mkdir()
        shutil.copytree(old_active, stage / 'deployment')
        schema = self.h.db_snapshot(old_active / 'db/x-ui.db', stage / 'snapshot.db')
        (stage / 'snapshot.db').unlink()
        self.h.atomic(stage / 'database-schema.sql', schema.pop('schema'))
        schema.update(format=self.h.FORMAT, encryption='none')
        self.h.atomic(stage / 'backup.json', host.dumps(schema))
        self.h.atomic(stage / 'host.py', (old_root / 'host.py').read_bytes())
        cert = stage / 'deployment/caddy/data/caddy/certificates/fixture'
        self.h.atomic(cert / 'sub.example.org.crt', 'certificate fixture')
        self.h.atomic(cert / 'sub.example.org.key', 'private key fixture')
        for name in self.h.UNIT_NAMES:
            self.h.atomic(stage / 'units' / name, (old_units / name).read_bytes())
        archive = self.root / 'offnode.tar'
        self.h.archive_tree(stage, archive)
        for mode in ('replace', 'restore'):
            with self.subTest(mode=mode):
                newroot = self.root / 'var/lib' / ('new-' + mode)
                newunits = self.root / ('units-' + mode)
                newunits.mkdir()
                with mock.patch.object(self.h, 'ROOT', newroot), mock.patch.object(self.h, 'UNITS', newunits), \
                     mock.patch.dict(os.environ, QD_SSH_CONNECTION='198.51.100.1 12345 93.184.216.35 22'):
                    request = dict(self.request, endpoint='93.184.216.35', mode=mode)
                    self.h.native_keys.return_value = {'privateKey': 'z' * 43, 'publicKey': 'w' * 43, 'shortIds': ['fedcba9876543210']}
                    with vps.upload_bundle(request, backup=archive) as stream:
                        self.assertFalse(self.h.prepare(stream)['unchanged'])
                    current = self.h.read_json(newroot / 'active/manifest.json')
                    self.assertFalse(current['accounts']['company']['enable'])
                    for device in self.h.DEVICES:
                        self.assertEqual(current['accounts'][device]['subId'], old['accounts'][device]['subId'])
                        self.assertEqual(current['accounts'][device]['id'] == old['accounts'][device]['id'], mode == 'restore')
                    self.assertEqual(current['reality'] == old['reality'], mode == 'restore')
                    self.assertEqual(current['endpoint'], '93.184.216.35')
                    self.assertEqual((old_active / 'manifest.json').read_bytes(), original)

    def test_safe_archive_rejects_traversal_symlink_and_duplicates(self):
        for name, kind in [('../escape', tarfile.REGTYPE), ('/absolute', tarfile.REGTYPE), ('link', tarfile.SYMTYPE)]:
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode='w') as archive:
                item = tarfile.TarInfo(name)
                item.type, item.size, item.linkname = kind, 0, '/etc/ssh'
                archive.addfile(item, io.BytesIO())
            stream.seek(0)
            with self.assertRaisesRegex(self.h.Error, 'Unsafe'):
                self.h.safe_unpack(stream, self.root / 'extract')
        self.assertFalse((self.root / 'escape').exists())

    def test_native_sqlite_snapshot_integrity_schema_and_plaintext_truth(self):
        self.upload()
        active = self.h.ROOT / 'active'
        schema = self.h.db_snapshot(active / 'db/x-ui.db', self.root / 'snapshot.db')
        self.assertIn('CREATE TABLE settings', schema['schema'])
        self.assertEqual(schema['schema_sha256'], hashlib.sha256(schema['schema'].encode()).hexdigest())
        with tempfile.TemporaryDirectory(dir=self.root) as temp:
            stage = Path(temp)
            (stage / 'backup.json').write_text('{"encryption":"none"}')
            (stage / 'secret').write_text('unencrypted fixture')
            path = self.root / 'backup.tar'
            self.h.archive_tree(stage, path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with tarfile.open(path) as archive:
                self.assertEqual(archive.extractfile('secret').read(), b'unencrypted fixture')
                self.assertEqual(json.load(archive.extractfile('backup.json'))['encryption'], 'none')


if __name__ == '__main__':
    unittest.main()
