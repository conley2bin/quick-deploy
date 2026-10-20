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
        host.atomic(owned / 'owner.json', host.dumps({'format':host.FORMAT,'owner':self.manifest['owner'],'machine_id':host.machine_id()}))
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
                self.assertIn(argv, (['systemctl','is-active','--quiet',host.FRONT], ['systemctl','is-enabled','--quiet',host.FRONT]))
                return subprocess.CompletedProcess(argv, 3, b'', b'')
            return original_run(argv, **kwargs)
        with mock.patch.object(subprocess, 'run', side_effect=no_systemctl), mock.patch.object(host, 'ctl'), mock.patch.object(host, 'check_units'):
            return host.export_backup()

    def device_fixture(self, legacy=False):
        self.prepare_export_fixture()
        self.manifest['published'] = True
        host.atomic(self.work / 'manifest.json', host.dumps(self.manifest))
        host.atomic(self.work / 'Caddyfile', host.caddyfile(self.manifest, True))
        host.atomic(host.ROOT / 'owner.json', host.dumps({'format': host.FORMAT, 'owner': self.manifest['owner'], 'machine_id': host.machine_id()}))
        if legacy:
            source = subprocess.check_output(['git', 'show', '18d1b3b:vps/lib/host.py'])
            self.assertEqual(__import__('hashlib').sha256(source).hexdigest(), host.LEGACY_ADAPTER_SHA256)
            host.atomic(host.ROOT / 'host.py', source, 0o700)
        def ctl(action, *arguments):
            unit = arguments[-1]
            if unit == host.PANEL:
                if action == 'stop': self.stop()
                elif action == 'start': self.start()
                elif action not in ('enable', 'disable'): raise AssertionError(action)
            elif unit != host.FRONT:
                raise AssertionError(unit)
        mock.patch.object(host, 'ctl', side_effect=ctl).start()
        for function in ('check_units', 'process_health', 'tls_probe', 'wait_tls', 'check_request_identity'):
            mock.patch.object(host, function).start()
        real_run = subprocess.run
        def systemctl(argv, **kwargs):
            if argv[0] == 'systemctl':
                self.assertIn(argv[1], ('is-active', 'is-enabled'))
                return subprocess.CompletedProcess(argv, 0, b'', b'')
            return real_run(argv, **kwargs)
        mock.patch.object(subprocess, 'run', side_effect=systemctl).start()

    def device_request(self, action, *names):
        return dict(owner=self.manifest['owner'], config=self.manifest['config'], endpoint=self.manifest['endpoint'],
                    machine_id=host.machine_id(), device_action=action, names=list(names), operation_id=os.urandom(16).hex(),
                    adapter_sha256=host.digest(vps.MODULE / 'lib/host.py'))

    def device_prepare(self, request):
        with vps.upload_bundle(request) as stream:
            try:
                return host.devices_prepare(stream)
            except Exception:
                if (host.ROOT/'pending.json').exists() and host.read_json(host.ROOT/'pending.json').get('phase') == 'restore-queued':
                    host.recover(force=True)
                raise

    def device_finish(self, request):
        archive = host.export_backup()
        stage = self.root / ('verify-' + str(time.monotonic_ns()))
        stage.mkdir()
        with archive.open('rb') as stream:
            host.safe_unpack(stream, stage)
        manifest = host.verify_backup(stage, self.lock)
        result = host.devices_commit(dict(request, offnode_sha256=host.digest(archive)))
        self.manifest = host.read_json(self.work / 'manifest.json')
        return manifest, result

    def test_devices_native_add_rotate_remove_archives_and_idempotence(self):
        self.device_fixture(legacy=True)
        original = copy.deepcopy(self.native_accounts())
        original_reality = copy.deepcopy(self.manifest['reality'])
        old_paths = host.feed_paths(self.manifest)
        request = self.device_request('add', 'device4', 'device5')
        self.assertEqual(self.device_prepare(request), {'prepared': True})
        # New capability is private until commit, while the old exact paths stay.
        self.assertEqual((self.work / 'Caddyfile').read_text(), host.caddyfile(self.manifest, True))
        self.assertEqual(self.device_prepare(request), {'prepared': True})
        candidate, result = self.device_finish(request)
        self.assertEqual(len(candidate['accounts']), 5)
        self.assertEqual(len(host.feed_paths(self.manifest)), 10)
        self.assertTrue(set(old_paths) <= set(host.feed_paths(self.manifest)))
        self.assertEqual(self.manifest['reality'], original_reality)
        self.assertEqual({d: c for d, c in self.native_accounts().items() if d in original}, original)
        self.assertEqual(self.device_prepare(request), result, 'lost commit reply must not apply twice')
        replay = dict(request, names=['different'])
        with self.assertRaisesRegex(host.Error, 'different request'):
            self.device_prepare(replay)
        unchanged = self.device_request('add', 'device4', 'device5')
        self.assertTrue(self.device_prepare(unchanged)['unchanged'])
        with self.assertRaisesRegex(host.Error, 'different request'):
            self.device_prepare(dict(unchanged, names=['other']))
        with self.assertRaisesRegex(host.Error, 'Mixed'):
            self.device_prepare(self.device_request('add', 'device4', 'different'))
        before = copy.deepcopy(self.native_accounts())
        target = before['device4']
        target.update(totalGB=1234567890, expiryTime=int(time.time()*1000)+86400000, limitIp=2,
                      trafficReset='monthly', trafficResetDay=6, reset=7, resetMax=9, comment='policy-fixture', limitHwid=3,
                      enable=False)
        host.API(self.manifest).call('/panel/api/clients/update/device4', target)
        before = copy.deepcopy(self.native_accounts())
        details = host.API(self.manifest).call('/panel/api/clients/get/device4')['client']
        request = self.device_request('rotate', 'device4')
        self.device_prepare(request)
        self.device_finish(request)
        after = self.native_accounts()
        self.assertNotEqual(after['device4']['id'], before['device4']['id'])
        self.assertNotEqual(after['device4']['subId'], before['device4']['subId'])
        self.assertFalse(after['device4']['enable'])
        for name in before:
            excluded = ('id', 'subId', 'updated_at') if name == 'device4' else ()
            self.assertEqual({k:v for k,v in after[name].items() if k not in excluded},
                             {k:v for k,v in before[name].items() if k not in excluded})
        new_detail = host.API(self.manifest).call('/panel/api/clients/get/device4')['client']
        self.assertEqual({k:v for k,v in new_detail.items() if k not in ('uuid','subId','updatedAt')},
                         {k:v for k,v in details.items() if k not in ('uuid','subId','updatedAt')})
        self.assertEqual(self.manifest['reality'], original_reality)
        for name in ('device4', 'device5', 'company', 'home', 'phone'):
            request = self.device_request('remove', name)
            self.device_prepare(request)
            archive_manifest, _ = self.device_finish(request)
            self.assertNotIn(name, archive_manifest['accounts'])
            self.assertTrue(self.device_prepare(self.device_request('remove', name))['unchanged'])
        self.assertEqual(self.manifest['accounts'], {}, 'native empty roster must remain valid')
        self.assertEqual(host.backend_health(self.work, self.manifest), {})

    def test_devices_native_partial_api_failure_restores_old_db_adapter(self):
        self.device_fixture(legacy=True)
        original = self.native_accounts()
        old_adapter = (host.ROOT / 'host.py').read_bytes()
        old_front = (self.work / 'Caddyfile').read_bytes()
        request = self.device_request('add', 'device4', 'device5')
        real_call = host.API.call
        def fail_second(api, path, data=None):
            if path == '/panel/api/clients/add' and data['client']['email'] == 'device5':
                # The FIRST native API operation already committed device4.
                self.assertIn('device4', self.native_accounts())
                raise host.Error('fixture second API failure')
            return real_call(api, path, data)
        with mock.patch.object(host.API, 'call', new=fail_second):
            with self.assertRaisesRegex(host.Error, 'second API failure'):
                self.device_prepare(request)
        self.assertEqual(self.native_accounts(), original)
        self.assertEqual((host.ROOT / 'host.py').read_bytes(), old_adapter)
        self.assertEqual((self.work / 'Caddyfile').read_bytes(), old_front)
        self.assertFalse((host.ROOT / 'pending.json').exists())
        self.assertIsNone(host.device_receipt(request), 'bound intent must not be mistaken for commit')
        with self.assertRaisesRegex(host.Error, 'different request'):
            self.device_prepare(dict(request, names=['other']))
        self.assertFalse(host.abort(request)['committed'], 'old active tree is not a device commit receipt')
        # Same uncommitted request is retryable; no orphan native account remains.
        self.device_prepare(request)
        self.device_finish(request)
        self.assertEqual(len(self.native_accounts()), 5)

    def test_devices_native_unknown_global_account_and_adapter_refuse(self):
        self.device_fixture()
        original = self.native_accounts()
        host.atomic(host.ROOT / 'host.py', b'foreign-adapter')
        with self.assertRaisesRegex(host.Error, 'adapter digest'):
            self.device_prepare(self.device_request('add', 'device4'))
        self.assertEqual(self.native_accounts(), original)
        self.assertFalse((host.ROOT / 'pending.json').exists())
        rejected = self.device_request('add', 'device4')
        self.assertFalse(host.abort(rejected)['committed'], 'rejected preparation must not claim old roster was a commit')
        host.atomic(host.ROOT / 'host.py', (vps.MODULE / 'lib/host.py').read_bytes())
        api = host.API(self.manifest)
        extra = dict(id=__import__('uuid').uuid4().hex, subId=os.urandom(24).hex(), email='foreign', enable=True, flow='xtls-rprx-vision')
        inbound_id = api.call('/panel/api/inbounds/list')[0]['id']
        api.call('/panel/api/clients/add', {'client':extra, 'inboundIds':[inbound_id]})
        api.call('/panel/api/clients/foreign/detach', {'inboundIds':[inbound_id]})
        with self.assertRaisesRegex(host.Error, 'snapshot account membership'):
            self.device_prepare(self.device_request('add', 'device4'))
        with self.assertRaisesRegex(host.Error, 'snapshot account membership'):
            host.backend_health(self.work, self.manifest)
        with self.assertRaisesRegex(host.Error, 'snapshot account membership'):
            host.export_backup()

    def test_device_credentials_reject_uuid_alias_old_rotation_keys_and_batch_duplicates(self):
        import uuid
        self.device_fixture()
        client = self.native_accounts()['company']
        client['id'] = client['id'].upper()
        client['limitHwid'] = host.API(self.manifest).call('/panel/api/clients/get/company')['client']['limitHwid']
        host.API(self.manifest).call('/panel/api/clients/update/company', client)
        before = copy.deepcopy(self.native_accounts())
        original_id, original_sub = before['company']['id'], before['company']['subId']
        cases = [('alias-add','add',['alias-device'],uuid.UUID(original_id),None,0),
                 ('old-uuid-rotate','rotate',['company'],uuid.UUID(original_id),None,0),
                 ('old-sub-rotate','rotate',['company'],None,original_sub,0),
                 ('batch-duplicate','add',['device4','device5'],uuid.uuid4(),None,1)]
        real_call = host.API.call
        for label, action, names, forced_id, forced_sub, expected_writes in cases:
            with self.subTest(label=label):
                writes = []
                def watched(api, path, data=None):
                    if path == '/panel/api/clients/add' or path.startswith('/panel/api/clients/update/'):
                        writes.append(path)
                    return real_call(api,path,data)
                request = self.device_request(action,*names)
                with __import__('contextlib').ExitStack() as stack:
                    stack.enter_context(mock.patch.object(host.API,'call',new=watched))
                    if forced_id is not None: stack.enter_context(mock.patch.object(host.uuid,'uuid4',return_value=forced_id))
                    if forced_sub is not None: stack.enter_context(mock.patch.object(host.secrets,'token_hex',return_value=forced_sub))
                    with self.assertRaisesRegex(host.Error,'credential collision'):
                        self.device_prepare(request)
                self.assertEqual(len(writes),expected_writes)
                self.assertEqual(self.native_accounts(),before)
                self.assertEqual(self.native_accounts()['company']['id'],original_id,'native spelling must be preserved')
        alias = copy.deepcopy(self.manifest)
        alias['accounts']['other'] = dict(alias['accounts']['company'], email='other', id=original_id.lower(), subId=os.urandom(24).hex())
        with self.assertRaisesRegex(host.Error,'Duplicate'):
            host.device_names(alias)

    def test_rename_native_policy_usage_hwid_and_918_rollback(self):
        import sqlite3
        self.device_fixture()
        deployed = subprocess.check_output(['git','show','918103e:vps/lib/host.py'])
        host.atomic(host.ROOT/'host.py',deployed,0o700)
        client=self.native_accounts()['company']
        client.update(enable=False,totalGB=1234567890,expiryTime=int(time.time()*1000)+30*86400000,
            limitIp=2,trafficReset='monthly',trafficResetDay=6,reset=7,resetMax=9,limitHwid=3,
            comment='rename-policy',group='fixture-group',adTag='fixture-ad')
        api=host.API(self.manifest);api.call('/panel/api/clients/update/company',client)
        api.call('/panel/api/clients/updateTraffic/company',{'upload':123456,'download':789012})
        before=copy.deepcopy(self.native_accounts());record=api.call('/panel/api/clients/get/company')['client']
        def rows(table):
            with sqlite3.connect(self.work/'db/x-ui.db') as db:
                db.row_factory=sqlite3.Row
                return [dict(row) for row in db.execute('select * from '+table)]
        traffic=next(r for r in rows('client_traffics') if r['email']=='company');joins=rows('client_inbounds')
        request=self.device_request('rename','company','tablet');real_call=host.API.call
        def after_write(api,path,data=None):
            value=real_call(api,path,data)
            if path=='/panel/api/clients/update/company' and data['email']=='tablet':
                self.assertIn('tablet',self.native_accounts())
                raise host.Error('fixture loss after native rename')
            return value
        with mock.patch.object(host.API,'call',new=after_write):
            with self.assertRaisesRegex(host.Error,'after native rename'):self.device_prepare(request)
        self.assertEqual(self.native_accounts(),before)
        self.assertEqual((host.ROOT/'host.py').read_bytes(),deployed)
        self.assertEqual(rows('client_inbounds'),joins)
        self.assertEqual(next(r for r in rows('client_traffics') if r['email']=='company'),traffic)
        with self.assertRaisesRegex(host.Error,'different request'):
            self.device_prepare(dict(request,names=['company','different']))
        self.device_prepare(request);self.device_finish(request)
        after=self.native_accounts();updated=host.API(self.manifest).call('/panel/api/clients/get/tablet')['client']
        self.assertNotIn('company',after);self.assertIn('tablet',after)
        self.assertEqual({k:v for k,v in updated.items() if k not in ('email','updatedAt')},
                         {k:v for k,v in record.items() if k not in ('email','updatedAt')})
        self.assertEqual(after['tablet']['id'],before['company']['id'])
        self.assertEqual(after['tablet']['subId'],before['company']['subId'])
        self.assertFalse(after['tablet']['enable'])
        self.assertEqual(rows('client_inbounds'),joins)
        newtraffic=next(r for r in rows('client_traffics') if r['email']=='tablet')
        self.assertEqual({k:v for k,v in newtraffic.items() if k!='email'},
                         {k:v for k,v in traffic.items() if k!='email'})
        self.assertEqual((newtraffic['up'],newtraffic['down']),(123456,789012))
        self.assertEqual({n:after[n] for n in ('home','phone')},{n:before[n] for n in ('home','phone')})
        with self.assertRaises(host.Error):host.API(self.manifest).call('/panel/api/clients/get/company')
        receipt=self.device_prepare(request);self.assertTrue(receipt['committed'])
        self.assertEqual(self.native_accounts(),after)

    def test_rename_five_native_urls_outputs_and_closed_archives(self):
        self.device_fixture()
        add=self.device_request('add','device4','device5');self.device_prepare(add);self.device_finish(add)
        original=copy.deepcopy(self.manifest);before=self.native_accounts();paths=host.feed_paths(original)
        request=self.device_request('rename','home','home-laptop')
        self.device_prepare(request);self.device_finish(request)
        self.assertCountEqual(host.feed_paths(self.manifest),paths)
        self.assertEqual(len(set(host.feed_paths(self.manifest))),10)
        self.assertEqual(len(paths),10)
        self.assertEqual(vps.subscription_urls(self.manifest)['home-laptop'],vps.subscription_urls(original)['home'])
        self.assertNotIn('home',self.manifest['accounts'])
        self.assertEqual(self.manifest['reality'],original['reality'])
        for name in self.manifest['accounts']:
            for fmt in ('sub','mihomo'):
                host.validate_feed_scope(self.manifest,name,host.get_feed(self.manifest,name,fmt),fmt)
        archive=host.export_backup();stage=self.root/'renamed-archive';stage.mkdir()
        with archive.open('rb') as stream:host.safe_unpack(stream,stage)
        renamed=host.verify_backup(stage,self.lock)
        self.assertIn('home-laptop',renamed['accounts']);self.assertNotIn('home',renamed['accounts'])
        self.stop();self.work=stage/'deployment';self.manifest=renamed;host.install_runtime(self.work,self.lock);self.start()
        host.configure_native(self.work,self.manifest,False)
        self.assertEqual(len(host.backend_health(self.work,self.manifest)),10)
        controlled=self.native_accounts()
        self.assertEqual(controlled['home-laptop']['id'],before['home']['id'])
        self.assertEqual(controlled['home-laptop']['subId'],before['home']['subId'])
        records={name:host.API(self.manifest).call('/panel/api/clients/get/'+name)['client'] for name in controlled}
        self.manifest['reality']=host.native_keys(self.work)
        host.configure_native(self.work,self.manifest,False,rotate=True)
        self.assertEqual(len(host.backend_health(self.work,self.manifest)),10)
        for name,client in self.native_accounts().items():
            self.assertNotEqual(client['id'],controlled[name]['id'])
            self.assertEqual(client['subId'],controlled[name]['subId'])
            record=host.API(self.manifest).call('/panel/api/clients/get/'+name)['client']
            self.assertEqual({k:v for k,v in record.items() if k not in ('uuid','updatedAt')},
                             {k:v for k,v in records[name].items() if k not in ('uuid','updatedAt')})

    def test_rename_native_refusals_and_reset_rotate_receipt_alias(self):
        self.device_fixture();before=self.native_accounts()
        for old,new in [('home','home'),('missing','tablet'),('home','phone'),('home','Bad/Name')]:
            with self.subTest(old=old,new=new),mock.patch.object(host,'ctl',wraps=host.ctl) as control, \
                    mock.patch.object(host,'atomic',wraps=host.atomic) as writes:
                with self.assertRaises(host.Error):self.device_prepare(self.device_request('rename',old,new))
                control.assert_not_called();writes.assert_not_called()
                self.assertEqual(self.native_accounts(),before)
        request=self.device_request('reset','home');alias=dict(request,device_action='rotate')
        self.assertEqual(host.device_request(request),host.device_request(alias))
        self.device_prepare(request);self.device_finish(alias)
        after=self.native_accounts()
        self.assertNotEqual(after['home']['id'],before['home']['id'])
        self.assertNotEqual(after['home']['subId'],before['home']['subId'])
        self.assertTrue(self.device_prepare(alias)['committed'])
        self.assertTrue(self.device_prepare(request)['committed'])
        self.assertEqual(self.native_accounts(),after)
        self.assertEqual(after['company'],before['company']);self.assertEqual(after['phone'],before['phone'])

    def stored_adapter(self):
        import types
        module = types.ModuleType('stored_device_adapter')
        path = host.ROOT / 'host.py'
        exec(compile(path.read_bytes(), str(path), 'exec'), module.__dict__)
        module.ROOT, module.UNITS = host.ROOT, host.UNITS
        module.ADMIN_PORT, module.SUB_PORT, module.API_PORT = host.ADMIN_PORT, host.SUB_PORT, host.API_PORT
        module.ctl = host.ctl
        module.check_units = host.check_units
        module.wait_tls = host.wait_tls
        module.process_health = host.process_health
        return module

    def test_devices_stored_adapter_expiry_boot_and_recovery_crash(self):
        self.device_fixture(legacy=True)
        original = copy.deepcopy(self.native_accounts())
        original_adapter = (host.ROOT / 'host.py').read_bytes()
        for expiry in ('deadline', 'boot'):
            with self.subTest(expiry=expiry):
                request = self.device_request('add', 'device4', 'device5')
                self.device_prepare(request)
                self.assertEqual(host.digest(host.ROOT / 'host.py'), host.digest(vps.MODULE / 'lib/host.py'))
                stored = self.stored_adapter()
                pending = stored.read_json(host.ROOT / 'pending.json')
                if expiry == 'deadline': pending['deadline'] = 0
                else: pending['boot_id'] = 'different-boot'
                stored.atomic(host.ROOT / 'pending.json', stored.dumps(pending))
                with self.assertRaises(stored.Error): stored.guard()
                # Crash during restoration, after native DB replacement. The
                # on-disk adapter must resume despite the expired boot/lease.
                real_atomic = stored.atomic
                def fail_front(path, data, mode=0o600):
                    if Path(path) == self.work / 'Caddyfile':
                        raise OSError('fixture interrupted rollback front write')
                    return real_atomic(path, data, mode)
                with mock.patch.object(stored, 'atomic', side_effect=fail_front):
                    with self.assertRaises(OSError): stored.recover()
                self.assertEqual(stored.read_json(host.ROOT / 'pending.json')['phase'], 'recovering')
                with self.assertRaises(stored.Error): stored.guard()  # incomplete restore stays fail-closed
                self.assertTrue(stored.recover()['queued'])
                self.assertIn('restored', stored.recover())
                self.assertEqual(self.native_accounts(), original)
                self.assertEqual((host.ROOT / 'host.py').read_bytes(), original_adapter)
                self.assertFalse((host.ROOT / 'pending.json').exists())
                self.manifest = host.read_json(self.work / 'manifest.json')

    def test_devices_snapshot_and_adapter_install_crash_order(self):
        self.device_fixture(legacy=True)
        original = self.native_accounts()
        before = (self.work / 'manifest.json').read_bytes()
        request = self.device_request('add', 'device4')
        with mock.patch.object(host, 'db_snapshot', side_effect=OSError('fixture incomplete snapshot')):
            with self.assertRaises(OSError): self.device_prepare(request)
        self.assertEqual(self.native_accounts(), original)
        self.assertEqual((self.work / 'manifest.json').read_bytes(), before)
        real_atomic = host.atomic
        def fail_journal(path, data, mode=0o600):
            if Path(path) == host.ROOT / 'pending.json':
                raise OSError('fixture crash after adapter install before journal')
            return real_atomic(path, data, mode)
        with mock.patch.object(host, 'atomic', side_effect=fail_journal):
            with self.assertRaises(OSError): self.device_prepare(request)
        self.assertFalse((host.ROOT / 'pending.json').exists())
        self.assertTrue((host.ROOT / 'device-rollback/intent.json').is_file())
        self.assertTrue(self.stored_adapter().recover(force=True)['queued'])
        self.assertIn('restored', self.stored_adapter().recover(force=True))
        self.assertEqual(self.native_accounts(), original)

    def test_devices_failed_publication_and_lost_commit_ack(self):
        self.device_fixture()
        original = self.native_accounts()
        original_front = (self.work / 'Caddyfile').read_bytes()
        request = self.device_request('add', 'device4')
        self.device_prepare(request)
        archive = host.export_backup()
        receipt = dict(request, offnode_sha256=host.digest(archive))
        with mock.patch.object(host, 'wait_tls', side_effect=host.Error('fixture TLS certificate failure')):
            with self.assertRaisesRegex(host.Error, 'TLS certificate'):
                host.devices_commit(receipt)
        self.assertTrue(host.recover(force=True)['queued'])
        self.assertIn('restored', host.recover(force=True))
        self.assertEqual(self.native_accounts(), original)
        self.assertEqual((self.work / 'Caddyfile').read_bytes(), original_front)
        self.manifest = host.read_json(self.work / 'manifest.json')
        self.device_prepare(request)
        archive = host.export_backup()
        receipt['offnode_sha256'] = host.digest(archive)
        real_replace = os.replace
        def lost_reply(source, target):
            if Path(source) == host.ROOT / 'device-rollback':
                raise OSError('fixture lost acknowledgment after durable receipt')
            return real_replace(source, target)
        with mock.patch.object(os, 'replace', side_effect=lost_reply):
            with self.assertRaises(OSError): host.devices_commit(receipt)
        self.assertTrue(host.device_receipt(request)['committed'])
        self.assertTrue(self.stored_adapter().recover(force=True)['committed'])
        after = copy.deepcopy(self.native_accounts())
        self.assertEqual(len(after), 4)
        self.assertTrue(host.devices_commit(receipt)['committed'])
        self.assertEqual(self.native_accounts(), after)

    def test_devices_dynamic_archive_restore_replace_full_native_outputs(self):
        self.device_fixture()
        # Original complete native policy must survive ADD, not just default DTOs.
        account = self.native_accounts()['company']
        account.update(totalGB=1234567890, expiryTime=int(time.time()*1000)+86400000,
                       reset=3, resetMax=7, trafficReset='monthly', trafficResetDay=4, comment='original-policy')
        host.API(self.manifest).call('/panel/api/clients/update/company', account)
        for count in (3, 5, 4, 0):
            if count == 5:
                request = self.device_request('add', 'device4', 'device5')
                self.device_prepare(request); self.device_finish(request)
            elif count == 4:
                request = self.device_request('remove', 'device5')
                self.device_prepare(request); self.device_finish(request)
            elif count == 0:
                for name in list(self.manifest['accounts']):
                    request = self.device_request('remove', name)
                    self.device_prepare(request); self.device_finish(request)
            archive = host.export_backup()
            stage = self.root / ('closed-' + str(count))
            stage.mkdir()
            with archive.open('rb') as stream: host.safe_unpack(stream, stage)
            manifest = host.verify_backup(stage, self.lock)
            self.assertEqual(len(manifest['accounts']), count)
            original_work, original_manifest = self.work, self.manifest
            expected = self.native_accounts()
            self.stop()
            self.work, self.manifest = stage / 'deployment', manifest
            host.install_runtime(self.work, self.lock)
            self.start()
            host.configure_native(self.work, self.manifest, False)
            self.assertEqual(len(host.backend_health(self.work, self.manifest)), 2*count)
            controlled = self.native_accounts()
            self.assertEqual({n:{k:v for k,v in c.items() if k != 'updated_at'} for n,c in controlled.items()},
                             {n:{k:v for k,v in c.items() if k != 'updated_at'} for n,c in expected.items()})
            records = {n:host.API(self.manifest).call('/panel/api/clients/get/' + n)['client'] for n in controlled}
            subids = {n:c['subId'] for n,c in controlled.items()}
            self.manifest['reality'] = host.native_keys(self.work)
            host.configure_native(self.work, self.manifest, False, rotate=True)
            self.assertEqual(len(host.backend_health(self.work, self.manifest)), 2*count)
            for name, after in self.native_accounts().items():
                self.assertNotEqual(after['id'], controlled[name]['id'])
                self.assertEqual(after['subId'], subids[name])
                record = host.API(self.manifest).call('/panel/api/clients/get/' + name)['client']
                self.assertEqual({k:v for k,v in record.items() if k not in ('uuid','updatedAt')},
                                 {k:v for k,v in records[name].items() if k not in ('uuid','updatedAt')})
            self.stop()
            self.work, self.manifest = original_work, original_manifest
            self.start()

    def test_devices_real_reality_auth_stream_revocation_and_tls_capabilities(self):
        import socketserver
        import threading
        import yaml
        self.device_fixture()
        cert, key = self.work / 'fixture.crt', self.work / 'fixture.key'
        host.run(['openssl','req','-x509','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256','-nodes',
                  '-days','1','-subj','/CN=target.example.org','-addext','subjectAltName=DNS:sub.example.org,DNS:target.example.org',
                  '-keyout',key,'-out',cert], timeout=15)
        storage = self.work / 'caddy/data/caddy/certificates/fixture'
        host.atomic(storage / 'sub.example.org.crt', cert.read_bytes())
        host.atomic(storage / 'sub.example.org.key', key.read_bytes())
        target_port = free_port()
        target_config = self.work / 'target.Caddyfile'
        host.atomic(target_config, '{\n admin off\n auto_https off\n}\nhttps://target.example.org:' + str(target_port) +
                    ' {\n bind 127.0.0.1\n tls ' + str(cert) + ' ' + str(key) + '\n respond "target"\n}\n')
        target = subprocess.Popen([str(self.work/'bin/caddy'),'run','--config',str(target_config),'--adapter','caddyfile'],
                                  env=dict(host.front_env(self.work),XDG_DATA_HOME=str(self.work/'target-fixture-data')),
                                  stdout=self.log,stderr=self.log)
        self.addCleanup(lambda: (target.terminate(), target.wait(timeout=10)))
        class Echo(socketserver.StreamRequestHandler):
            def handle(self):
                while line := self.rfile.readline():
                    self.wfile.write(line); self.wfile.flush()
        origin = socketserver.ThreadingTCPServer(('127.0.0.1',0), Echo)
        origin.daemon_threads = True
        worker = threading.Thread(target=origin.serve_forever, daemon=True); worker.start()
        self.addCleanup(origin.server_close); self.addCleanup(origin.shutdown)
        self.stop()
        # Fixture-only routing exception permits a local echo origin; production
        # private blocking is unchanged. All listeners/traffic remain loopback.
        with __import__('sqlite3').connect(self.work / 'db/x-ui.db') as db:
            template = json.loads(db.execute("select value from settings where key='xrayTemplateConfig'").fetchone()[0])
        for outbound in template['outbounds']:
            if outbound['tag'] == 'direct': outbound['settings'] = {'finalRules':[{'action':'allow','ip':['127.0.0.1/32']},{'action':'block'}]}
        template['routing']['rules'].insert(0, {'type':'field','ip':['127.0.0.1/32'],'port':str(origin.server_address[1]),'outboundTag':'direct'})
        host.settings_write(self.work / 'db/x-ui.db', {'xrayTemplateConfig':json.dumps(template)})
        self.start()
        api = host.wait_api(self.manifest)
        inbound = api.call('/panel/api/inbounds/list')[0]
        stream = inbound['streamSettings']
        stream = json.loads(stream) if isinstance(stream,str) else stream
        stream['realitySettings']['dest'] = '127.0.0.1:'+str(target_port)
        inbound['streamSettings'] = json.dumps(stream)
        api.call('/panel/api/inbounds/update/'+str(inbound['id']), inbound)
        self.stop(); self.start(); host.wait_api(self.manifest)
        def node(name):
            return yaml.safe_load(host.get_feed(self.manifest, name, 'mihomo'))['proxies'][0]
        def client(proxy):
            directory = self.work / ('auth-'+str(time.monotonic_ns())); directory.mkdir()
            proxy = copy.deepcopy(proxy); proxy['server'] = '127.0.0.1'
            proxy['port'] = self.manifest['config']['proxy_port']
            port = free_port()
            config = {'socks-port':port,'bind-address':'127.0.0.1','allow-lan':False,'log-level':'silent',
                      'dns':{'enable':False},'tun':{'enable':False},'proxies':[proxy],
                      'proxy-groups':[{'name':'PROXY','type':'select','proxies':[proxy['name']]}],'rules':['MATCH,PROXY']}
            host.atomic(directory / 'config.yaml', yaml.safe_dump(config))
            process = subprocess.Popen([shutil.which('verge-mihomo') or shutil.which('mihomo'),'-d',str(directory),
                                        '-f',str(directory/'config.yaml')], stdout=self.log, stderr=self.log)
            self.addCleanup(lambda: (process.terminate(), process.wait(timeout=10)))
            for _ in range(100):
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.1): break
                except OSError: time.sleep(.05)
            return port
        def echo(port):
            stream = socket.create_connection(('127.0.0.1',port),timeout=3)
            stream.settimeout(3)
            try:
                stream.sendall(b'\x05\x01\x00'); self.assertEqual(stream.recv(2),b'\x05\x00')
                stream.sendall(b'\x05\x01\x00\x01\x7f\x00\x00\x01'+origin.server_address[1].to_bytes(2,'big'))
                self.assertEqual(stream.recv(10)[1],0)
                stream.sendall(b'credential-proof\n')
                if stream.recv(1024) != b'credential-proof\n': raise OSError('no authenticated echo')
                return stream
            except BaseException:
                stream.close(); raise
        # Real Caddy reloads the adapter's exact allowlist on fresh loopback
        # TLS/HTTP ports, using a test CA explicitly trusted by this test only.
        https, http = free_port(), free_port()
        front = [None]
        def stop_front():
            if front[0] is not None:
                front[0].terminate(); front[0].wait(timeout=10); front[0] = None
        self.addCleanup(stop_front)
        panel_ctl = host.ctl.side_effect
        def ctl(action, *arguments):
            unit = arguments[-1]
            if unit == host.PANEL: return panel_ctl(action,unit)
            if action in ('stop','restart'): stop_front()
            if action in ('start','restart'):
                if front[0] is not None: return
                text = (self.work/'Caddyfile').read_text().replace('http_port 80',f'http_port {http}').replace('https_port 8443',f'https_port {https}')
                text = text.replace(':8443 {',f':{https} {{\n    bind 127.0.0.1').replace(':80 {',f':{http} {{\n    bind 127.0.0.1')
                start = text.index('    tls {'); end = text.index('    @feed',start) if '    @feed' in text else text.index('    respond 404',start)
                text = text[:start]+f'    tls {cert} {key}\n'+text[end:]
                config = self.work/'tls-fixture.Caddyfile'; host.atomic(config,text)
                front[0] = subprocess.Popen([str(self.work/'bin/caddy'),'run','--config',str(config),'--adapter','caddyfile'],
                                           env=dict(host.front_env(self.work), XDG_DATA_HOME=str(self.work/'front-fixture-data')),stdout=self.log,stderr=self.log)
                for _ in range(100):
                    try:
                        with socket.create_connection(('127.0.0.1',https),timeout=.1): break
                    except OSError: time.sleep(.05)
        mock.patch.object(host,'ctl',side_effect=ctl).start()
        ctl('start',host.FRONT)
        context = ssl.create_default_context(cafile=str(cert))
        def public_get(path):
            with socket.create_connection(('127.0.0.1',https),timeout=3) as raw:
                with context.wrap_socket(raw,server_hostname='sub.example.org') as connection:
                    connection.sendall(('GET '+path+' HTTP/1.1\r\nHost: sub.example.org\r\nConnection: close\r\n\r\n').encode())
                    result = b''
                    while chunk := connection.recv(65536): result += chunk
                    return result
        old_paths = host.feed_paths(self.manifest)
        request = self.device_request('add','device4','device5')
        self.device_prepare(request)
        candidate = host.read_json(self.work/'manifest.json')
        new_path = '/sub/'+candidate['accounts']['device4']['subId']
        self.assertTrue(public_get(new_path).startswith(b'HTTP/1.1 404'))
        for path in old_paths: self.assertTrue(public_get(path).startswith(b'HTTP/1.1 200'))
        self.device_finish(request)
        for name in self.manifest['accounts']:
            for fmt in ('sub','mihomo'):
                path = '/'+fmt+'/'+self.manifest['accounts'][name]['subId']
                response = public_get(path)
                self.assertTrue(response.startswith(b'HTTP/1.1 200'))
                host.validate_feed_scope(self.manifest,name,response.split(b'\r\n\r\n',1)[1],fmt)
        old_node, home_node = node('device4'), node('home')
        old_client, home_client = client(old_node), client(home_node)
        existing = echo(old_client)
        echo(home_client).close()
        request = self.device_request('reset','device4')
        self.device_prepare(request); self.device_finish(request)
        try:
            existing.sendall(b'after-rotation\n')
            self.assertEqual(existing.recv(1024),b'','established old UUID stream must close')
        except (ConnectionResetError,BrokenPipeError): pass
        finally: existing.close()
        self.assertTrue(public_get(new_path).startswith(b'HTTP/1.1 404'))
        with self.assertRaises((OSError,IndexError)): echo(old_client)
        new_client = client(node('device4'))
        echo(new_client).close(); echo(home_client).close()
        rotated_path = '/sub/'+self.manifest['accounts']['device4']['subId']
        self.assertTrue(public_get(rotated_path).startswith(b'HTTP/1.1 200'))
        request = self.device_request('remove','device4')
        open_rotated = echo(new_client)
        self.device_prepare(request); self.device_finish(request)
        self.assertTrue(public_get(rotated_path).startswith(b'HTTP/1.1 404'))
        with self.assertRaises((OSError,IndexError)): echo(new_client)
        try:
            open_rotated.sendall(b'after-remove\n'); self.assertEqual(open_rotated.recv(1024),b'')
        except (ConnectionResetError,BrokenPipeError): pass
        finally: open_rotated.close()
        echo(home_client).close()
        for path in ('/', '/sub/', '/sub/unknown', self.manifest['admin_path']+'panel/api/inbounds/list'):
            self.assertTrue(public_get(path).startswith(b'HTTP/1.1 404'))

    def test_devices_cli_native_postcommit_backup_failure_reconciles_without_rotation(self):
        import argparse
        self.device_fixture()
        state = self.root / 'operator-state'; state.mkdir(mode=0o700)
        record = {'endpoint':self.manifest['endpoint'], 'machine_id':host.machine_id()}
        document = {'format':host.FORMAT,'owner':self.manifest['owner'],'config':self.manifest['config'],
                    'current':record,'previous':[],'backup':None}
        custom_core = state/'custom-core'
        custom_core.symlink_to(shutil.which('verge-mihomo') or shutil.which('mihomo'))
        counts = {'backup':0}; fail_after = [False]; exports = []
        def remote(record, action, request=None, stdin=None, stdout=None):
            if action == 'devices-prepare': return host.devices_prepare(stdin)
            if action == 'devices-commit': return host.devices_commit(request)
            if action == 'abort': return host.abort(request)
            if action == 'backup':
                counts['backup'] += 1
                if fail_after[0] and counts['backup'] == 3:
                    raise host.Error('fixture transport lost after committed rotation')
                with host.export_backup(request).open('rb') as source: shutil.copyfileobj(source, stdout)
                return
            if action == 'outputs':
                manifest = host.read_json(self.work/'manifest.json')
                return {n:base64.b64encode(data).decode() for n,data in host.backend_health(self.work,manifest).items()}
            if action == 'devices-list':
                manifest = host.read_json(self.work/'manifest.json')
                return {name:{'enabled':account['enable']} for name,account in host.snapshot_accounts(self.work/'db/x-ui.db',manifest).items()}
            if action == 'status':
                return {'machine_id':host.machine_id(),'node_id':self.manifest['node_id'],'healthy':True,'adapter_sha256':host.digest(host.ROOT/'host.py'),'devices':list(host.read_json(self.work/'manifest.json')['accounts'])}
            raise AssertionError(action)
        def exported(state):
            committed = vps.load(state/'state.json')
            manifest = vps.inspect_backup(Path(committed['backup']),self.lock)
            self.assertTrue(manifest['published'])
            self.assertFalse((state/'pending.json').exists())
            exports.append(copy.deepcopy(manifest))
        with mock.patch.object(vps,'remote',side_effect=remote), mock.patch.object(vps,'confirm'), \
                mock.patch.object(vps,'export_after_commit',side_effect=exported):
            vps.devices(state,document,argparse.Namespace(device_action='add',names=['device4','device5'],mihomo=str(custom_core)))
            self.assertEqual(len(exports[-1]['accounts']),5)
            with mock.patch('sys.stdout',new_callable=__import__('io').StringIO) as output:
                self.assertEqual(vps.main(['--state-dir',str(state),'devices','list']),0)
            self.assertEqual(len(output.getvalue().splitlines()),5)
            for account in exports[-1]['accounts'].values():
                self.assertNotIn(account['id'],output.getvalue())
                self.assertNotIn(account['subId'],output.getvalue())
            self.manifest = host.read_json(self.work/'manifest.json')
            old_url=vps.subscription_urls(self.manifest)['device4']
            host.atomic(state/'outputs/operator-notes.txt','unrelated-output')
            vps.devices(state,document,argparse.Namespace(device_action='rename',names=['device4','tablet'],mihomo=str(custom_core)))
            self.manifest = host.read_json(self.work/'manifest.json')
            self.assertEqual(vps.subscription_urls(self.manifest)['tablet'],old_url)
            self.assertNotIn('device4',self.manifest['accounts'])
            self.assertFalse((state/'outputs/device4.yaml').exists());self.assertFalse((state/'outputs/device4.txt').exists())
            self.assertTrue((state/'outputs/tablet.yaml').is_file())
            self.assertEqual((state/'outputs/operator-notes.txt').read_text(),'unrelated-output')
            prior = self.native_accounts()['tablet']['id']
            counts['backup'] = 0; fail_after[0] = True
            with self.assertRaisesRegex(host.Error,'transport lost'):
                vps.devices(state,document,argparse.Namespace(device_action='reset',names=['tablet'],mihomo=str(custom_core)))
            current = self.native_accounts()['tablet']['id']
            self.assertNotEqual(prior,current)
            self.assertTrue((state/'pending.json').exists())
            self.assertEqual(vps.load(state/'pending.json')['request']['device_action'],'rotate')
            self.assertEqual(len(exports),2,'failed postcommit backup must not publish stale export')
            with self.assertRaisesRegex(host.Error,'pending'):
                vps.devices(state,document,argparse.Namespace(device_action='reset',names=['tablet'],mihomo=str(custom_core)))
            fail_after[0] = False
            self.assertEqual(vps.load(state/'pending.json')['mihomo'],str(custom_core))
            real_which = shutil.which
            with mock.patch.object(vps.shutil,'which',side_effect=lambda name:None if name in ('verge-mihomo','mihomo') else real_which(name)):
                vps.recover_local(state,document)
            self.assertEqual(self.native_accounts()['tablet']['id'],current)
            self.assertEqual(exports[-1]['accounts']['tablet']['id'],current)
            self.assertFalse((state/'pending.json').exists())

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
        host.atomic(self.root / 'owner.json', host.dumps({'format':host.FORMAT,'owner':self.manifest['owner'],'machine_id':host.machine_id()}))
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
                self.assertIn(argv, (['systemctl','is-active','--quiet',host.FRONT], ['systemctl','is-enabled','--quiet',host.FRONT]))
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
