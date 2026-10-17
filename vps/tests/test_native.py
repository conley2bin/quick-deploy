"""Native release/API evidence in temporary directories, loopback listeners only.

Set QD_VPS_ARTIFACTS to a directory containing the three pinned official assets.
No SSH, installer, systemctl, DNS/ACME request or live client settings are used.
"""
import base64
import copy
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import host
import vps


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@unittest.skipUnless(os.environ.get('QD_VPS_ARTIFACTS'), 'Native release test requires explicit QD_VPS_ARTIFACTS; unit fixtures are not native evidence')
class Native(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='qd-native-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.base = cls.root / 'base'
        cls.base.mkdir(mode=0o700)
        cls.lock = vps.release_lock()
        artifacts = Path(os.environ['QD_VPS_ARTIFACTS'])
        (cls.base / 'artifacts').mkdir(mode=0o700)
        for key in ('panel', 'core', 'front'):
            shutil.copy2(artifacts / cls.lock[key]['file'], cls.base / 'artifacts' / cls.lock[key]['file'])
        host.install_runtime(cls.base, cls.lock)

    def setUp(self):
        self.work = self.root / ('candidate-' + str(time.monotonic_ns()))
        shutil.copytree(self.base, self.work)
        self.proc = None
        self.addCleanup(self.stop)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(host, 'ADMIN_PORT', free_port()).start()
        mock.patch.object(host, 'SUB_PORT', free_port()).start()
        self.manifest = host.new_manifest('owner-fixture', {'domain': 'sub.example.org', 'reality_target': 'target.example.org', 'acme_email': 'operator@example.org', 'proxy_port': 443}, '93.184.216.34')
        self.manifest['config']['proxy_port'] = free_port()
        self.manifest['reality'] = host.native_keys(self.work)
        host.initialize_db(self.work, self.manifest)
        self.log = (self.work / 'native.log').open('wb')
        self.addCleanup(self.log.close)
        self.real_inbound = host.inbound_for
        def loopback_inbound(manifest):
            inbound = self.real_inbound(manifest)
            inbound['listen'] = '127.0.0.1'
            return inbound
        mock.patch.object(host, 'inbound_for', side_effect=loopback_inbound).start()
        def ctl(action, unit):
            self.assertEqual(unit, host.PANEL)
            if action == 'stop':
                self.stop()
            elif action == 'start':
                self.start()
            else:
                raise AssertionError('Unexpected systemctl operation')
        mock.patch.object(host, 'ctl', side_effect=ctl).start()
        self.start()
        host.configure_native(self.work, self.manifest, True)

    def start(self):
        if self.proc is not None:
            raise AssertionError('Fixture panel already running')
        self.proc = subprocess.Popen([str(self.work / 'bin/x-ui'), 'run'], env=host.panel_env(self.work),
            cwd=self.work, stdout=self.log, stderr=self.log, start_new_session=True)

    def stop(self):
        if self.proc is not None:
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait()
            self.proc = None

    def native_accounts(self):
        settings = host.wait_api(self.manifest).call('/panel/api/inbounds/list')[0]['settings']
        settings = json.loads(settings) if isinstance(settings, str) else settings
        return {client['email']: client for client in settings['clients']}

    def prepare_export_fixture(self):
        self.stop()
        owned = self.root / ('export-' + str(time.monotonic_ns()))
        owned.mkdir()
        self.work.rename(owned / 'active')
        self.work = owned / 'active'
        self.start()
        mock.patch.object(host, 'ROOT', owned).start()
        units = owned / 'units'
        units.mkdir()
        mock.patch.object(host, 'UNITS', units).start()
        for name, text in host.unit_texts().items():
            host.atomic(units / name, text)
        host.atomic(owned / 'host.py', (vps.MODULE / 'lib/host.py').read_bytes())
        host.atomic(self.work / 'manifest.json', host.dumps(self.manifest))
        host.atomic(self.work / 'Caddyfile', host.caddyfile(self.manifest, False))
        certs = self.work / 'caddy/data/caddy/certificates/fixture'
        host.atomic(certs / 'sub.example.org.crt', 'fixture cert')
        host.atomic(certs / 'sub.example.org.key', 'fixture key')
        host.atomic(self.work / 'caddy/data/caddy/acme/fixture/account.json', '{}')

    def export_without_systemctl(self):
        original_run = subprocess.run
        def no_systemctl(argv, **kwargs):
            if argv[0] == 'systemctl':
                self.assertEqual(argv, ['systemctl', 'is-active', '--quiet', host.FRONT])
                return subprocess.CompletedProcess(argv, 3, b'', b'')
            return original_run(argv, **kwargs)
        with mock.patch.object(subprocess, 'run', side_effect=no_systemctl), mock.patch.object(host, 'ctl'), mock.patch.object(host, 'check_units'):
            return host.export_backup()

    def test_review_snapshot_revocation_and_full_policy_restore(self):
        self.prepare_export_fixture()
        for device, client in self.native_accounts().items():
            client.update(trafficReset='monthly', trafficResetDay=6, limitHwid=3)
            if device == 'company':
                client.update(totalGB=1234567890, expiryTime=int(time.time() * 1000) + 30 * 86400000,
                              limitIp=2, reset=7, resetMax=9, comment='retained-policy-fixture')
            host.API(self.manifest).call('/panel/api/clients/update/' + device, client)
        real_snapshot = host.db_snapshot
        def revoke_before_snapshot(source, target):
            current = self.native_accounts()['company']
            current['limitHwid'] = host.API(self.manifest).call('/panel/api/clients/get/company')['client']['limitHwid']
            current['enable'] = False
            host.API(self.manifest).call('/panel/api/clients/update/company', current)
            return real_snapshot(source, target)
        with mock.patch.object(host, 'db_snapshot', side_effect=revoke_before_snapshot):
            archive = self.export_without_systemctl()
        expected = self.native_accounts()
        expected_records = {d: host.API(self.manifest).call('/panel/api/clients/get/' + d)['client'] for d in host.DEVICES}
        self.assertFalse(expected['company']['enable'])
        self.stop()
        stage = self.root / 'interleaved-restore'
        stage.mkdir()
        with archive.open('rb') as stream:
            host.safe_unpack(stream, stage)
        recovered = host.verify_backup(stage, self.lock)
        self.assertFalse(recovered['accounts']['company']['enable'], 'metadata must describe the same revoked snapshot')
        stored_manifest = stage / 'deployment/manifest.json'
        wrong = copy.deepcopy(recovered)
        wrong['accounts']['company']['enable'] = True
        host.atomic(stored_manifest, host.dumps(wrong))
        with self.assertRaisesRegex(host.Error, 'snapshot account'):
            host.verify_backup(stage, self.lock)
        host.atomic(stored_manifest, host.dumps(recovered))
        self.work = stage / 'deployment'
        self.manifest = recovered
        host.install_runtime(self.work, self.lock)
        self.start()
        host.configure_native(self.work, self.manifest, False)
        controlled = self.native_accounts()
        for device in host.DEVICES:
            self.assertEqual({k: v for k, v in controlled[device].items() if k != 'updated_at'},
                             {k: v for k, v in expected[device].items() if k != 'updated_at'})
        self.manifest['reality'] = host.native_keys(self.work)
        host.configure_native(self.work, self.manifest, False, rotate=True)
        replaced = self.native_accounts()
        for device in host.DEVICES:
            self.assertNotEqual(replaced[device]['id'], controlled[device]['id'])
            self.assertEqual({k: v for k, v in replaced[device].items() if k not in ('id', 'updated_at')},
                             {k: v for k, v in controlled[device].items() if k not in ('id', 'updated_at')})
        self.assertFalse(replaced['company']['enable'])
        self.assertEqual(replaced['company']['totalGB'], 1234567890)
        for device in host.DEVICES:
            record = host.API(self.manifest).call('/panel/api/clients/get/' + device)['client']
            self.assertNotEqual(record['uuid'], expected_records[device]['uuid'])
            self.assertEqual({k: v for k, v in record.items() if k not in ('uuid', 'updatedAt')},
                             {k: v for k, v in expected_records[device].items() if k not in ('uuid', 'updatedAt')})
            self.assertEqual(record['limitHwid'], 3)

    def test_review_export_rejects_outside_file_and_directory_links(self):
        self.prepare_export_fixture()
        outside = self.root / 'outside-private-fixture'
        host.atomic(outside / 'sentinel', 'outside-owned-secret-fixture')
        for target in (outside / 'sentinel', outside):
            with self.subTest(target=target.name):
                link = self.work / 'unexpected-link'
                link.symlink_to(target, target_is_directory=target.is_dir())
                try:
                    with self.assertRaisesRegex(host.Error, '[Ss]ymlink'):
                        self.export_without_systemctl()
                    self.assertFalse((host.ROOT / 'export.tar').exists())
                    self.assertEqual((outside / 'sentinel').read_text(), 'outside-owned-secret-fixture')
                finally:
                    link.unlink()
        root_alias = self.root / 'owned-root-link'
        root_alias.symlink_to(host.ROOT, target_is_directory=True)
        with mock.patch.object(host, 'ROOT', root_alias):
            with self.assertRaisesRegex(host.Error, '[Ss]ymlink'):
                self.export_without_systemctl()
        # Decoupling backup from restricted feed access must retain artifact
        # integrity checks; a drifted native binary cannot define a valid set.
        host.atomic(self.work / 'bin/caddy', b'drifted-fixture-binary', 0o700)
        with self.assertRaisesRegex(host.Error, 'binary/geodata drift'):
            self.export_without_systemctl()

    def test_review_closed_archive_new_ip_restore_and_replace(self):
        import re
        import sqlite3
        self.prepare_export_fixture()
        api = host.wait_api(self.manifest)
        for device, client in self.native_accounts().items():
            client.update(trafficReset='monthly', trafficResetDay=11, reset=4, resetMax=8, limitHwid=0,
                          totalGB=987654321, expiryTime=int(time.time() * 1000) + 20 * 86400000,
                          limitIp=2, comment='new-IP-preserved-policy')
            if device == 'company':
                client['enable'] = False
            api.call('/panel/api/clients/update/' + device, client)
        group = api.call('/panel/api/hosts/get/' + api.call('/panel/api/hosts/list')[0]['groupId'])
        group.update(remark='owned-host-fixture', serverDescription='preserve native Host policy',
                     tags=['fixture-owned'], sortOrder=9)
        api.call('/panel/api/hosts/update/' + group['groupId'], group)
        original_group = api.call('/panel/api/hosts/get/' + group['groupId'])
        original_records = {d: api.call('/panel/api/clients/get/' + d)['client'] for d in host.DEVICES}
        original_keys = copy.deepcopy(self.manifest['reality'])
        archive = self.export_without_systemctl()
        self.stop()  # the old process is gone before either new-target restoration
        old_stage = self.root / 'old-source-archive'
        old_stage.mkdir()
        with archive.open('rb') as stream:
            host.safe_unpack(stream, old_stage)
        # Exercise a source-era archive without the new Host ownership metadata.
        archived_manifest = old_stage / 'deployment/manifest.json'
        legacy = host.read_json(archived_manifest)
        legacy.pop('host_group_id', None)
        host.atomic(archived_manifest, host.dumps(legacy))
        old_archive = self.root / 'old-source-native.tar'
        host.archive_tree(old_stage, old_archive)
        original_archive_hash = host.digest(old_archive)
        for mode, endpoint in (('restore', '93.184.216.35'), ('replace', '93.184.216.36')):
            with self.subTest(mode=mode):
                stage = self.root / ('different-IP-' + mode)
                stage.mkdir()
                with old_archive.open('rb') as stream:
                    host.safe_unpack(stream, stage)
                untouched = (stage / 'deployment/manifest.json').read_bytes()
                recovered = host.verify_backup(stage, self.lock)
                self.assertEqual((stage / 'deployment/manifest.json').read_bytes(), untouched)
                self.work = stage / 'deployment'
                self.manifest = recovered
                self.manifest['endpoint'] = endpoint
                host.install_runtime(self.work, self.lock)
                if mode == 'replace':
                    self.manifest['reality'] = host.native_keys(self.work)
                self.start()
                try:
                    calls = []
                    native_call = host.API.call
                    def watched(session, path, data=None):
                        if '/hosts/update/' in path or '/clients/update/' in path:
                            calls.append(path)
                        return native_call(session, path, data)
                    with mock.patch.object(host.API, 'call', new=watched):
                        host.configure_native(self.work, self.manifest, False, rotate=(mode == 'replace'))
                    with sqlite3.connect(self.work / 'db/x-ui.db') as db:
                        rows = db.execute('select group_id,inbound_id,address,port from hosts').fetchall()
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0][2], endpoint, 'authoritative native Host must move, not just externalProxy')
                    self.assertEqual(rows[0][0], original_group['groupId'])
                    self.assertEqual(rows[0][3], self.manifest['config']['proxy_port'])
                    self.assertEqual(self.manifest['host_group_id'], original_group['groupId'])
                    outputs = host.backend_health(self.work, self.manifest)  # strict scope rejection stays active
                    self.assertNotIn('company.txt', outputs)
                    for device in ('home', 'phone'):
                        host.validate_feed_scope(self.manifest, device, outputs[device + '.txt'], 'sub')
                        servers = re.findall(r'(?m)^\s*server:\s*([^\n]+)$', outputs[device + '.yaml'].decode())
                        self.assertEqual([s.strip().strip('\"\'') for s in servers], [endpoint])
                    current_api = host.API(self.manifest)
                    migrated = current_api.call('/panel/api/hosts/get/' + original_group['groupId'])
                    self.assertEqual(migrated['hosts'], [endpoint + ':' + str(self.manifest['config']['proxy_port'])])
                    self.assertEqual({k: v for k, v in migrated.items() if k != 'hosts'},
                                     {k: v for k, v in original_group.items() if k != 'hosts'})
                    for device in host.DEVICES:
                        current = current_api.call('/panel/api/clients/get/' + device)['client']
                        before = original_records[device]
                        self.assertEqual(current['uuid'] == before['uuid'], mode == 'restore')
                        self.assertEqual({k: v for k, v in current.items() if k not in ('uuid', 'updatedAt')},
                                         {k: v for k, v in before.items() if k not in ('uuid', 'updatedAt')})
                    self.assertFalse(self.native_accounts()['company']['enable'])
                    config = json.loads((self.work / 'bin/config.json').read_text())
                    core_ids = {c['id'] for i in config['inbounds'] if i['protocol'] == 'vless' for c in i['settings']['clients']}
                    self.assertNotIn(self.native_accounts()['company']['id'], core_ids)
                    self.assertEqual(self.manifest['reality'] == original_keys, mode == 'restore')
                    self.assertEqual(sum('/hosts/update/' in c for c in calls), 1)
                    self.assertEqual(sum('/clients/update/' in c for c in calls), 0 if mode == 'restore' else 3)
                    host.atomic(self.work / 'manifest.json', host.dumps(self.manifest))
                    self.assertEqual(host.read_json(self.work / 'manifest.json')['host_group_id'], original_group['groupId'])
                    self.assertEqual(host.digest(old_archive), original_archive_hash)
                finally:
                    self.stop()

    def test_review_foreign_native_hosts_refuse_before_mutation(self):
        api = host.API(self.manifest)
        original = api.call('/panel/api/hosts/get/' + api.call('/panel/api/hosts/list')[0]['groupId'])
        for kind in ('extra-address', 'extra-disabled-hidden-group'):
            with self.subTest(kind=kind):
                extra = copy.deepcopy(original)
                if kind == 'extra-address':
                    extra['hosts'].append('93.184.216.99:' + str(self.manifest['config']['proxy_port']))
                    api.call('/panel/api/hosts/update/' + original['groupId'], extra)
                else:
                    extra.update(groupId='foreign-hidden-fixture', hosts=['93.184.216.99:' + str(self.manifest['config']['proxy_port'])],
                                 isDisabled=True, isHidden=True)
                    api.call('/panel/api/hosts/add', extra)
                try:
                    before = api.call('/panel/api/hosts/list')
                    pid = self.proc.pid
                    mutations = []
                    native_call = host.API.call
                    def watched(session, path, data=None):
                        if any(part in path for part in ('/hosts/update/', '/clients/update/', '/inbounds/update/')):
                            mutations.append(path)
                        return native_call(session, path, data)
                    with mock.patch.object(host.API, 'call', new=watched):
                        with self.assertRaisesRegex(host.Error, '[Hh]ost'):
                            host.configure_native(self.work, self.manifest, False, rotate=True)
                    self.assertEqual(mutations, [])
                    self.assertEqual(self.proc.pid, pid)
                    self.assertEqual(api.call('/panel/api/hosts/list'), before)
                finally:
                    if kind == 'extra-address':
                        api.call('/panel/api/hosts/update/' + original['groupId'], original)
                    else:
                        api.call('/panel/api/hosts/del/foreign-hidden-fixture', {})

    def test_native_api_scope_core_mihomo_and_no_rotation(self):
        before = copy.deepcopy(self.manifest)
        outputs = host.backend_health(self.work, self.manifest)
        host.backend_health(self.work, self.manifest)
        self.assertEqual(self.manifest, before)
        self.assertEqual(len(outputs), 6)
        for device in host.DEVICES:
            for ext, fmt in (('.txt', 'sub'), ('.yaml', 'mihomo')):
                host.validate_feed_scope(self.manifest, device, outputs[device + ext], fmt)
            for name in ('geoip.dat', 'geosite.dat'):
                shutil.copy2(self.work / 'bin' / name, self.work / name)
            path = self.work / (device + '.yaml')
            path.write_bytes(outputs[device + '.yaml'])
            binary = shutil.which('verge-mihomo') or shutil.which('mihomo')
            self.assertIsNotNone(binary)
            result = subprocess.run([binary, '-t', '-d', str(self.work), '-f', str(path)], capture_output=True, timeout=45)
            self.assertEqual(result.returncode, 0, result.stdout.decode() + result.stderr.decode())
        # Wrong administrative credentials must fail, not return an empty list.
        with self.assertRaises(host.Error):
            host.API(dict(self.manifest, admin_password='incorrect-fixture'))
        opener = build_opener(ProxyHandler({}))
        for suffix in ('/sub/unknown-sub-id', '/mihomo/unknown-sub-id'):
            try:
                with opener.open(f'http://127.0.0.1:{host.SUB_PORT}' + suffix) as response:
                    self.assertEqual(response.read(), b'')
            except HTTPError as exc:
                self.assertIn(exc.code, (400, 404))
        # Native device disable must remove its usable feed without changing
        # another device's credentials or claiming a revoked path stayed live.
        api = host.API(self.manifest)
        inbound = api.call('/panel/api/inbounds/list')[0]
        native = inbound['settings']
        native = json.loads(native) if isinstance(native, str) else native
        account = next(c for c in native['clients'] if c['email'] == 'company')
        account['enable'] = False
        api.call('/panel/api/clients/update/company', account)
        updated = api.call('/panel/api/inbounds/list')[0]['settings']
        updated = json.loads(updated) if isinstance(updated, str) else updated
        self.assertFalse(next(c for c in updated['clients'] if c['email'] == 'company')['enable'])
        api.call('/panel/api/server/restartXrayService', {})
        for _ in range(60):
            config = json.loads((self.work / 'bin/config.json').read_text())
            native_users = [c['id'] for inbound in config['inbounds'] if inbound['protocol'] == 'vless' for c in inbound['settings']['clients']]
            if self.manifest['accounts']['company']['id'] not in native_users:
                break
            time.sleep(0.1)
        else:
            self.fail('Disabled native device remained in the core authorization config')
        # Upstream intentionally can retain the disabled URI. Our public front
        # removes that path when explicitly re-deployed; no silent reactivation.
        outputs = host.backend_health(self.work, self.manifest)
        self.assertFalse(self.manifest['accounts']['company']['enable'])
        self.assertNotIn('company.txt', outputs)
        self.assertEqual(len(host.feed_paths(self.manifest)), 4)
        host.validate_feed_scope(self.manifest, 'home', host.get_feed(self.manifest, 'home', 'sub'), 'sub')

    def test_complete_native_backup_archive_and_schema_restore(self):
        # Re-home this disposable fixture under the adapter's exact active layout.
        self.stop()
        destination = self.root / 'active'
        self.work.rename(destination)
        self.work = destination
        self.start()
        mock.patch.object(host, 'ROOT', self.root).start()
        units = self.root / 'fixture-units'
        units.mkdir()
        mock.patch.object(host, 'UNITS', units).start()
        for name, text in host.unit_texts().items():
            host.atomic(units / name, text)
        host.atomic(self.root / 'host.py', (vps.MODULE / 'lib/host.py').read_bytes())
        host.atomic(self.work / 'manifest.json', host.dumps(self.manifest))
        host.atomic(self.work / 'Caddyfile', host.caddyfile(self.manifest, False))
        # Certificate content belongs to the separate actual TLS-front test;
        # here assert that both external files and renewal state survive export.
        certs = self.work / 'caddy/data/caddy/certificates/fixture'
        host.atomic(certs / 'sub.example.org.crt', 'fixture external certificate')
        host.atomic(certs / 'sub.example.org.key', 'fixture external private key')
        host.atomic(self.work / 'caddy/data/caddy/acme/fixture/account.json', '{"account":"fixture"}')
        original_run = subprocess.run
        def no_real_systemctl(argv, **kwargs):
            if argv[0] == 'systemctl':
                self.assertEqual(argv, ['systemctl', 'is-active', '--quiet', host.FRONT])
                return subprocess.CompletedProcess(argv, 3, b'', b'')
            return original_run(argv, **kwargs)
        with mock.patch.object(subprocess, 'run', side_effect=no_real_systemctl), mock.patch.object(host, 'ctl') as ctl, mock.patch.object(host, 'check_units'):
            archive = host.export_backup()
        ctl.assert_called_once_with('stop', host.FRONT)
        offnode = self.root / 'offnode.tar'
        shutil.copy2(archive, offnode)
        self.assertEqual(offnode.stat().st_mode & 0o777, 0o600)
        stage = self.root / 'restore-proof'
        stage.mkdir()
        with offnode.open('rb') as stream:
            names = host.safe_unpack(stream, stage)
        recovered = host.verify_backup(stage, self.lock)
        self.assertEqual(recovered['accounts'], self.manifest['accounts'])
        self.assertEqual(json.loads((stage / 'backup.json').read_text())['encryption'], 'none')
        for name in host.UNIT_NAMES:
            self.assertIn('units/' + name, names)
        for component in ('panel', 'core', 'front'):
            self.assertIn('deployment/artifacts/' + self.lock[component]['file'], names)
        self.assertIn('deployment/caddy/data/caddy/acme/fixture/account.json', names)
        self.assertIn('deployment/db/x-ui.db', names)
        self.assertFalse(any(name.endswith(('.db-wal', '.db-shm')) for name in names))
        # Restore from the archive's closed database alone. The live source
        # process is stopped and no source WAL/SHM is copied into the new tree.
        self.stop()
        self.work = stage / 'deployment'
        host.install_runtime(self.work, self.lock)
        self.assertEqual(set(host.read_json(self.work / 'binary-hashes.json')),
                         {'x-ui', 'xray-linux-amd64', 'caddy', 'geoip.dat', 'geosite.dat'})
        self.manifest = recovered
        expected_accounts = copy.deepcopy(recovered['accounts'])
        self.start()
        restored_outputs = host.backend_health(self.work, self.manifest)
        self.assertEqual(len(restored_outputs), 6)
        self.assertEqual(self.manifest['accounts'], expected_accounts)
        self.stop()
        (stage / 'deployment/caddy/data/caddy/certificates/fixture/sub.example.org.key').unlink()
        with self.assertRaisesRegex(host.Error, 'missing external TLS'):
            host.verify_backup(stage, self.lock)

    def test_native_restore_rotates_proxy_identity_preserves_subids(self):
        import uuid
        before = copy.deepcopy(self.manifest)
        self.stop()
        schema = host.db_snapshot(self.work / 'db/x-ui.db', self.work / 'snapshot.db')
        self.assertIn('CREATE TABLE `clients`', schema['schema'])
        # Controlled restore preserves the exact native database and identities.
        self.start()
        host.configure_native(self.work, self.manifest, False)
        host.backend_health(self.work, self.manifest)
        self.assertEqual(self.manifest['accounts'], before['accounts'])
        # Cold replacement rotates using the native CLIENT endpoint, not a stale
        # inbound form. The old process need not exist for these writes.
        self.stop()
        self.manifest['reality'] = host.native_keys(self.work)
        self.start()
        host.configure_native(self.work, self.manifest, False, rotate=True)
        host.backend_health(self.work, self.manifest)
        for device in host.DEVICES:
            self.assertNotEqual(self.manifest['accounts'][device]['id'], before['accounts'][device]['id'])
            self.assertEqual(self.manifest['accounts'][device]['subId'], before['accounts'][device]['subId'])
        self.assertNotEqual(self.manifest['reality']['publicKey'], before['reality']['publicKey'])

    def test_real_tls_front_scopes_http_and_admin_paths(self):
        cert, key = self.work / 'fixture.crt', self.work / 'fixture.key'
        host.run(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes',
                  '-days', '1', '-subj', '/CN=sub.example.org', '-addext', 'subjectAltName=DNS:sub.example.org',
                  '-keyout', key, '-out', cert], timeout=15)
        http, https = free_port(), free_port()
        text = host.caddyfile(self.manifest, True).replace('http_port 80', f'http_port {http}').replace('https_port 8443', f'https_port {https}')
        text = text.replace(':8443 {', f':{https} {{\n    bind 127.0.0.1').replace(':80 {', f':{http} {{\n    bind 127.0.0.1')
        start = text.index('    tls {')
        end = text.index('    @feed', start)
        text = text[:start] + f'    tls {cert} {key}\n' + text[end:]
        config = self.work / 'front.Caddyfile'
        host.atomic(config, text)
        front = subprocess.Popen([str(self.work / 'bin/caddy'), 'run', '--config', str(config), '--adapter', 'caddyfile'],
                                 env=host.front_env(self.work), stdout=self.log, stderr=self.log, start_new_session=True)
        def stop_front():
            if front.poll() is None:
                os.killpg(front.pid, signal.SIGTERM)
                front.wait(timeout=10)
        self.addCleanup(stop_front)
        for _ in range(60):
            try:
                with socket.create_connection(('127.0.0.1', https), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            self.fail('Native TLS front did not start')
        context = ssl.create_default_context(cafile=str(cert))
        def get(path, secure=True):
            with socket.create_connection(('127.0.0.1', https if secure else http), timeout=5) as raw:
                stream = context.wrap_socket(raw, server_hostname='sub.example.org') if secure else raw
                stream.sendall(('GET ' + path + ' HTTP/1.1\r\nHost: sub.example.org\r\nConnection: close\r\n\r\n').encode())
                result = b''
                while chunk := stream.recv(65536):
                    result += chunk
                if secure:
                    stream.close()
                return result
        token_path = '/sub/' + self.manifest['accounts']['phone']['subId']
        payload = get(token_path)
        self.assertTrue(payload.startswith(b'HTTP/1.1 200'))
        self.assertIn(b'Cache-Control: no-store', payload)
        for path in ('/', '/sub/', '/sub/unknown', self.manifest['admin_path'] + 'panel/api/inbounds/list'):
            self.assertTrue(get(path).startswith(b'HTTP/1.1 404'), path)
        plain = get(token_path, secure=False)
        self.assertTrue(plain.startswith(b'HTTP/1.1 404'))
        self.assertNotIn(self.manifest['accounts']['phone']['id'].encode(), plain)
        with self.assertRaises(ssl.SSLCertVerificationError):
            with socket.create_connection(('127.0.0.1', https), timeout=5) as raw:
                ssl.create_default_context().wrap_socket(raw, server_hostname='sub.example.org')

    def test_native_caddy_adaptation_and_bad_certificate_fail_closed(self):
        config = self.work / 'Caddyfile'
        host.atomic(config, host.caddyfile(self.manifest, True))
        command = [self.work / 'bin/caddy', 'adapt', '--config', config, '--adapter', 'caddyfile']
        adapted = json.loads(host.run(command).stdout)
        servers = adapted['apps']['http']['servers']
        listen = {addr for server in servers.values() for addr in server['listen']}
        self.assertEqual(listen, {':80', ':8443'})
        self.assertNotIn(':443', listen)
        policy = adapted['apps']['tls']['automation']['policies'][0]
        self.assertTrue(policy['issuers'][0]['challenges']['tls-alpn']['disabled'])
        for server in servers.values():
            if ':80' in server['listen']:
                self.assertNotIn('reverse_proxy', json.dumps(server))
        # Exercise real Caddy with explicit invalid cert paths, only high
        # loopback ports. Startup must fail; never open a plaintext feed.
        http, https = free_port(), free_port()
        bad = host.caddyfile(self.manifest, True).replace('http_port 80', f'http_port {http}').replace('https_port 8443', f'https_port {https}')
        bad = bad.replace(':8443 {', f':{https} {{').replace(':80 {', f':{http} {{')
        bad = bad.replace('    tls {', '    bind 127.0.0.1\n    tls /missing-cert-fixture /missing-key-fixture\n    # tls {', 1)
        # Replace the whole automation block rather than generate malformed syntax.
        start = bad.index('    # tls {')
        end = bad.index('    @feed', start)
        bad = bad[:start] + bad[end:]
        host.atomic(config, bad)
        result = subprocess.run([str(self.work / 'bin/caddy'), 'run', '--config', str(config), '--adapter', 'caddyfile'],
                                env=host.front_env(self.work), capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        for port in (http, https):
            with self.assertRaises(OSError):
                socket.create_connection(('127.0.0.1', port), timeout=0.2)


if __name__ == '__main__':
    unittest.main()
