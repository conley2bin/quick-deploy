"""Dual topology/closed snapshots and had-active feature transaction boundaries.

No SSH/systemctl/native service is used. Real native interoperability is in the
separate pinned-artifact test_native_dual lane, not inferred from these mocks.
"""
import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import host
import test_remote


class DualSnapshot(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='qd-dual-unit-')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.manifest = host.new_manifest('owner', {'domain': 'sub.example.org', 'reality_target': 'target.example.org',
            'acme_email': '', 'proxy_port': 443}, '93.184.216.34')
        self.manifest['reality'] = {'publicKey': 'x'*43, 'privateKey': 'y'*43, 'shortIds': ['1234567890abcdef']}
        self.manifest['host_group_id'] = 'main-group'
        self.database = self.folder / 'db.sqlite'
        with sqlite3.connect(self.database) as db:
            db.executescript('''create table users(id integer,username text);
                create table inbounds(id integer,user_id integer,remark text,protocol text,port integer);
                create table hosts(group_id text,inbound_id integer,address text,port integer);
                create table clients(id integer,email text,uuid text,sub_id text,enable integer,flow text,auth text);
                create table client_inbounds(client_id integer,inbound_id integer);''')
            db.execute('insert into users values(1,?)', (self.manifest['admin_user'],))
            db.execute("insert into inbounds values(1,1,'SELF-main','vless',443)")
            db.execute('insert into hosts values(?,1,?,443)', ('main-group', self.manifest['endpoint']))
            for identity, (name, client) in enumerate(self.manifest['accounts'].items(), 1):
                db.execute('insert into clients values(?,?,?,?,?,?,?)', (identity,name,client['id'],client['subId'],1,client['flow'],''))
                db.execute('insert into client_inbounds values(?,1)', (identity,))

    def dualize(self):
        self.manifest['management'] = {'ssh_port': 2222}
        self.manifest['hysteria2'] = {'schema':1,'inbound_id':2,'host_group_id':'h2-group',
            'certificate_file':'caddy/data/caddy/certificates/issuer/sub.example.org.crt',
            'key_file':'caddy/data/caddy/certificates/issuer/sub.example.org.key'}
        with sqlite3.connect(self.database) as db:
            db.execute("insert into inbounds values(2,1,'SELF-h2','hysteria',443)")
            db.execute('insert into hosts values(?,2,?,443)', ('h2-group',self.manifest['endpoint']))
            db.execute('insert into client_inbounds select id,2 from clients')
            for name, client in self.manifest['accounts'].items():
                client['auth'] = ('auth-' + name + '-' + 'a'*32)
                db.execute('update clients set auth=? where email=?',(client['auth'],name))

    def test_legacy_five_fields_and_dual_six_fields_exact_membership(self):
        original = copy.deepcopy(self.manifest['accounts'])
        self.assertFalse(host.dual_enabled(self.manifest))
        self.assertEqual(host.snapshot_accounts(self.database,self.manifest),original)
        self.assertEqual(host.snapshot_host_group(self.database,self.manifest),'main-group')
        self.dualize()
        self.assertTrue(host.dual_enabled(self.manifest))
        self.assertEqual(host.snapshot_accounts(self.database,self.manifest),self.manifest['accounts'])
        self.assertEqual(host.snapshot_host_group(self.database,self.manifest),'main-group')
        self.assertEqual({n:{k:v for k,v in c.items() if k!='auth'} for n,c in self.manifest['accounts'].items()},original)
        with sqlite3.connect(self.database) as db: db.execute('delete from client_inbounds where client_id=1 and inbound_id=2')
        with self.assertRaisesRegex(host.Error,'inbound membership'): host.snapshot_accounts(self.database,self.manifest)

    def test_extra_hidden_host_duplicate_join_and_missing_auth_refuse(self):
        self.dualize()
        for sql in ("insert into hosts values('foreign',2,'93.184.216.34',443)",
                    'insert into client_inbounds values(1,2)', "update clients set auth='' where id=1"):
            with self.subTest(sql=sql):
                backup = self.folder/'saved.db'
                with sqlite3.connect(self.database) as db, sqlite3.connect(backup) as target: db.backup(target)
                with sqlite3.connect(self.database) as db: db.execute(sql)
                with self.assertRaises(host.Error):
                    host.snapshot_host_group(self.database,self.manifest)
                    host.snapshot_accounts(self.database,self.manifest)
                self.database.write_bytes(backup.read_bytes()); backup.unlink()

    def test_dual_uri_scope_credentials_tls_endpoints_and_leakage(self):
        self.dualize(); current=self.manifest['accounts']['company']; config=self.manifest['config']
        vless='vless://'+current['id']+'@'+self.manifest['endpoint']+':443?'+urlencode({
            'security':'reality','flow':'xtls-rprx-vision','pbk':self.manifest['reality']['publicKey'],
            'sid':self.manifest['reality']['shortIds'][0],'sni':config['reality_target']})
        h2='hysteria2://'+current['auth']+'@'+self.manifest['endpoint']+':443?'+urlencode({'security':'tls','sni':config['domain'],'alpn':'h3'})
        def check(text): host.validate_feed_scope(self.manifest,'company',base64.b64encode(text.encode()),'sub')
        check(vless+'\n'+h2+'\n')
        for bad in (vless, vless+'\n'+h2+'&insecure=1',vless+'\n'+h2.replace(current['auth'],'wrong-auth'),
                    vless+'\n'+h2.replace(config['domain'],'wrong.example.org'),vless+'\n'+h2+'\n'+h2):
            with self.assertRaises(host.Error): check(bad)
        self.assertEqual(json.loads(host.clash_rules(self.manifest))['proxy-groups'][0]['proxies'],['SELF-h2','SELF-main','DIRECT'])

    def test_feed_groups_precise_tcp8443_scope_and_legacy_bytes(self):
        legacy_rules = [
            'DOMAIN,sub.example.org,DIRECT', 'IP-CIDR,93.184.216.34/32,DIRECT,no-resolve',
            'IP-CIDR,127.0.0.0/8,DIRECT,no-resolve', 'IP-CIDR,10.0.0.0/8,DIRECT,no-resolve',
            'IP-CIDR,172.16.0.0/12,DIRECT,no-resolve', 'IP-CIDR,192.168.0.0/16,DIRECT,no-resolve',
            'IP-CIDR,100.64.0.0/10,DIRECT,no-resolve', 'DOMAIN-SUFFIX,bilibili.com,DIRECT',
            'DOMAIN-SUFFIX,bilivideo.com,DIRECT', 'DOMAIN-SUFFIX,biliapi.net,DIRECT',
            'DOMAIN-SUFFIX,biliapi.com,DIRECT', 'DOMAIN-SUFFIX,hdslb.com,DIRECT',
            'GEOSITE,cn,DIRECT', 'GEOIP,CN,DIRECT', 'MATCH,PROXY']
        legacy_bytes = json.dumps({'geodata-mode': True, 'rules': legacy_rules})
        self.assertEqual(host.clash_rules(self.manifest), legacy_bytes)
        self.dualize()
        inline = json.loads(host.clash_rules(self.manifest))
        self.assertEqual(inline['proxy-groups'], [
            {'name': 'PROXY', 'type': 'select', 'proxies': ['SELF-h2', 'SELF-main', 'DIRECT']},
            {'name': 'FEED', 'type': 'fallback', 'proxies': ['SELF-h2', 'DIRECT'],
             'url': 'https://sub.example.org:8443/', 'expected-status': '404',
             'interval': 300, 'timeout': 5000, 'lazy': False}])
        self.assertEqual(inline['rules'][:2], [
            'AND,((NETWORK,TCP),(DOMAIN,sub.example.org),(DST-PORT,8443)),FEED',
            'AND,((NETWORK,TCP),(IP-CIDR,93.184.216.34/32,no-resolve),(DST-PORT,8443)),FEED'])
        self.assertEqual(inline['rules'][2:], legacy_rules)
        # Each FEED exception conjunctively requires TCP8443. UDP443 and either
        # default/nondefault SSH retain the following endpoint DIRECT rules.
        for rule in inline['rules'][:2]:
            self.assertTrue(rule.startswith('AND,((NETWORK,TCP),'))
            self.assertIn('(DST-PORT,8443)', rule)
            for unrelated in ('(NETWORK,UDP)', '(DST-PORT,443)', '(DST-PORT,22)', '(DST-PORT,2222)'):
                self.assertNotIn(unrelated, rule)
        self.assertNotIn('proxy-groups', json.loads(legacy_bytes))

    def test_feed_groups_and_added_rules_parse_in_installed_mihomo(self):
        binary = shutil.which('verge-mihomo') or shutil.which('mihomo')
        if not binary:
            self.skipTest('Native Mihomo parser unavailable; static rule contract still tested')
        version = subprocess.run([binary, '-v'], capture_output=True, timeout=10)
        if version.returncode or 'v1.19.32' not in version.stdout.decode(errors='replace'):
            self.skipTest('This parser proof requires the selected installed Mihomo1.19.32')
        self.dualize()
        inline = json.loads(host.clash_rules(self.manifest))
        config = {'log-level': 'silent', 'dns': {'enable': False},
            'proxies': [
                {'name': 'SELF-h2', 'type': 'hysteria2', 'server': self.manifest['endpoint'], 'port': 443,
                 'password': self.manifest['accounts']['company']['auth'], 'sni': 'sub.example.org', 'alpn': ['h3']},
                {'name': 'SELF-main', 'type': 'vless', 'server': self.manifest['endpoint'], 'port': 443,
                 'uuid': self.manifest['accounts']['company']['id']}],
            'proxy-groups': inline['proxy-groups'], 'rules': inline['rules'][:2] + ['MATCH,PROXY']}
        # Synthetic node fixture tests the exact new group/rule syntax without
        # geodata downloads. The full unchanged rule tail is checked above;
        # genuine native full-profile parsing belongs to test_native_dual.
        path = self.folder / 'feed-parser.json'
        path.write_text(json.dumps(config))
        result = subprocess.run([binary, '-t', '-d', str(self.folder), '-f', str(path)], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, 'Installed pinned Mihomo rejected native inline FEED fields/rules')

    def test_management_route_exact_authenticated_inbound_destination_ports(self):
        self.dualize()
        template={'outbounds':[{'tag':'direct','protocol':'freedom'}]}
        rule=host.management_rule(template,self.manifest,'SELF-h2')
        self.assertEqual(rule,{'type':'field','inboundTag':['SELF-h2'],'network':'tcp','ip':['93.184.216.34/32'],
            'port':'2222,8443','outboundTag':'direct'})
        with self.assertRaises(host.Error): host.management_rule({'outbounds':[]},self.manifest,'SELF-h2')

    def test_unique_certificate_pair_sni_key_and_link_guards(self):
        storage=self.folder/'caddy/data/caddy/certificates/issuer';storage.mkdir(parents=True)
        cert=storage/'sub.example.org.crt';key=cert.with_suffix('.key')
        subprocess.run(['openssl','req','-x509','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256','-nodes','-days','1',
            '-subj','/CN=sub.example.org','-addext','subjectAltName=DNS:sub.example.org','-keyout',str(key),'-out',str(cert)],
            check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.assertEqual(host.hysteria_certificate_paths(self.folder,self.manifest),(cert,key))
        duplicate=storage.parent/'other';duplicate.mkdir();(duplicate/cert.name).write_bytes(cert.read_bytes())
        with self.assertRaisesRegex(host.Error,'exactly one'):host.hysteria_certificate_paths(self.folder,self.manifest)
        (duplicate/cert.name).unlink();duplicate.rmdir()
        original=key.read_bytes();key.unlink();key.symlink_to(cert)
        with self.assertRaisesRegex(host.Error,'Symlink'):host.hysteria_certificate_paths(self.folder,self.manifest)
        key.unlink();key.write_bytes(original)
        alternate=storage/'alternate.key'
        subprocess.run(['openssl','genpkey','-algorithm','EC','-pkeyopt','ec_paramgen_curve:P-256','-out',str(alternate)],
            check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        key.write_bytes(alternate.read_bytes())
        with self.assertRaisesRegex(host.Error,'mismatch'):host.hysteria_certificate_paths(self.folder,self.manifest)
        key.write_bytes(original);alternate.unlink()
        changed=dict(self.manifest,config=dict(self.manifest['config'],domain='wrong.example.org'))
        # Same filename is deliberately changed as well: selection cannot hide a wrong SAN.
        wrong=storage/'wrong.example.org.crt';wrong.write_bytes(cert.read_bytes());wrong.with_suffix('.key').write_bytes(original)
        with self.assertRaises(host.Error):host.hysteria_certificate_paths(self.folder,changed)


class BaselineAdapterRetention(unittest.TestCase):
    def setUp(self):
        self.f = test_remote.FakeHost('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.h = self.f.h
        self.f.upload()
        self.f.patch(self.h, 'service_state', return_value={'running': False, 'enabled': False})
        self.baseline = subprocess.check_output(['git', 'show', 'ae72036:vps/lib/host.py'])
        self.pin = self.h.BASELINE_ADAPTER_SHA256
        self.previous = self.h.ROOT / ('adapter-before-' + self.pin + '.py')

    def test_exact_reviewed_baseline_migration_exports_all_three_pinned_sources(self):
        self.assertEqual(self.baseline, subprocess.check_output(['git', 'show', '6c02d25:vps/lib/host.py']))
        self.assertEqual(hashlib.sha256(self.baseline).hexdigest(), self.pin)
        self.assertEqual(self.pin, 'b52adbbfb7efc3593c5fa266e8f148ed60deaa2b685d72b926e549b9425f6d7e')
        self.assertEqual(self.h.ADAPTER_PREDECESSORS, {
            self.h.LEGACY_ADAPTER_SHA256: 'previous-host.py',
            self.h.DEPLOYED_ADAPTER_SHA256: 'previous-host-918103e.py',
            self.pin: 'previous-host-ae72036.py'})
        originals = {
            self.h.LEGACY_ADAPTER_SHA256: subprocess.check_output(['git', 'show', '18d1b3b:vps/lib/host.py']),
            self.h.DEPLOYED_ADAPTER_SHA256: subprocess.check_output(['git', 'show', '918103e:vps/lib/host.py']),
            self.pin: self.baseline}
        inodes = {}
        for pin, source in originals.items():
            if pin != self.pin:
                self.h.retain_previous_adapter(source, pin)
                inodes[pin] = (self.h.ROOT / ('adapter-before-' + pin + '.py')).stat().st_ino
        self.h.atomic(self.h.ROOT / 'host.py', self.baseline, 0o700)
        for attempt in range(2):
            with tarfile.open(self.h.export_backup()) as archive:
                for pin, source in originals.items():
                    self.assertEqual(archive.extractfile(self.h.ADAPTER_PREDECESSORS[pin]).read(), source)
                    retained = self.h.ROOT / ('adapter-before-' + pin + '.py')
                    self.assertFalse(retained.is_symlink())
                    self.assertEqual(retained.read_bytes(), source)
                    if pin in inodes:
                        self.assertEqual(retained.stat().st_ino, inodes[pin])
                    inodes[pin] = retained.stat().st_ino
            self.assertEqual(self.h.digest(self.h.ROOT / 'host.py'), host.digest(Path(host.__file__)))
        self.assertTrue(self.h.accepted_adapter_digest(self.pin, host.digest(Path(host.__file__))))

    def test_baseline_retention_unsafe_inputs_refuse_before_migration_or_stop(self):
        outside = self.f.root / 'outside-baseline-sentinel'
        outside.write_bytes(self.baseline)
        for kind in ('link', 'dangling', 'fifo', 'directory', 'wrong-digest'):
            with self.subTest(kind=kind):
                self.h.atomic(self.h.ROOT / 'host.py', self.baseline, 0o700)
                if kind == 'link': self.previous.symlink_to(outside)
                elif kind == 'dangling': self.previous.symlink_to(self.f.root / 'missing-baseline')
                elif kind == 'fifo': os.mkfifo(self.previous)
                elif kind == 'directory': self.previous.mkdir()
                else: self.previous.write_bytes(b'not-reviewed-baseline')
                before = self.previous.lstat()
                try:
                    with mock.patch.object(self.h, 'atomic', wraps=self.h.atomic) as writes, \
                         mock.patch.object(self.h, 'ctl', wraps=self.h.ctl) as control:
                        with self.assertRaises(self.h.Error): self.h.export_backup()
                        writes.assert_not_called()
                        control.assert_not_called()
                    self.assertEqual((self.h.ROOT / 'host.py').read_bytes(), self.baseline)
                    self.assertEqual(self.previous.lstat().st_ino, before.st_ino)
                    self.assertEqual(self.previous.lstat().st_mode, before.st_mode)
                    self.assertFalse((self.h.ROOT / 'backup-stop.json').exists())
                    self.assertEqual(outside.read_bytes(), self.baseline)
                finally:
                    if kind == 'directory': self.previous.rmdir()
                    else: self.previous.unlink()

    def test_unknown_adapter_remains_rejected_without_new_trust_or_writes(self):
        self.h.atomic(self.h.ROOT / 'host.py', b'unreviewed-adapter', 0o700)
        self.assertFalse(self.h.accepted_adapter_digest('f'*64, host.digest(Path(host.__file__))))
        with mock.patch.object(self.h, 'atomic', wraps=self.h.atomic) as writes, \
             mock.patch.object(self.h, 'ctl', wraps=self.h.ctl) as control:
            with self.assertRaisesRegex(self.h.Error, 'Unknown stored backup adapter'): self.h.export_backup()
            writes.assert_not_called()
            control.assert_not_called()
        with self.assertRaisesRegex(self.h.Error, 'Unreviewed'): self.h.read_previous_adapter('f'*64)
        self.assertFalse(self.previous.exists())
        self.assertEqual((self.h.ROOT / 'host.py').read_bytes(), b'unreviewed-adapter')


class FeatureTransaction(unittest.TestCase):
    def setUp(self):
        self.f=test_remote.FakeHost('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.h=self.f.h
        original_run=self.h.run.side_effect
        self.h.run.side_effect=lambda argv,**kwargs: subprocess.CompletedProcess(argv,0,b'') if argv==['ss','-H','-lunp'] else original_run(argv,**kwargs)
        # The fake host tests transaction files, never native protocol behavior.
        self.f.patch(self.h,'enable_hysteria')
        self.f.upload()
        self.h.atomic(self.h.ROOT/'export-sha256','initial-receipt')
        self.h.commit(dict(self.f.request,machine_id='a'*32,offnode_sha256='initial-receipt'))
        self.manifest=self.h.read_json(self.h.ROOT/'active/manifest.json')
        self.manifest['published']=True
        self.h.atomic(self.h.ROOT/'active/manifest.json',self.h.dumps(self.manifest))
        self.f.patch(self.h,'API',return_value=object())
        self.f.patch(self.h,'wait_api',return_value=object())
        self.f.patch(self.h,'owned_host_group',return_value=({'id':1},{}))
        self.f.patch(self.h,'service_state',return_value={'running':True,'enabled':True})
        self.f.patch(self.h,'services_active',return_value=True)
        self.f.patch(self.h.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'',b''))
        self.request=dict(self.f.request,machine_id='a'*32,operation_id='1'*32,ssh_port=22,mode='transport-upgrade')
        self.original=(self.h.ROOT/'active/manifest.json').read_bytes()
        def enable(folder,manifest,ssh_port):
            manifest['management']={'ssh_port':ssh_port}
            manifest['hysteria2']={'schema':1,'inbound_id':2,'host_group_id':'fake-h2',
                'certificate_file':'caddy/data/caddy/certificates/fixture/sub.example.org.crt',
                'key_file':'caddy/data/caddy/certificates/fixture/sub.example.org.key'}
            with sqlite3.connect(folder/'db/x-ui.db') as db:
                db.execute('alter table clients add column auth text')
                db.execute("insert into inbounds values(2,1,'SELF-h2','hysteria',443)")
                db.execute('insert into hosts values(?,2,?,?,0,0)',('fake-h2',manifest['endpoint'],443))
                db.execute('insert into client_inbounds select id,2 from clients')
                for name,account in manifest['accounts'].items():
                    account['auth']='a'*32+name
                    db.execute('update clients set auth=? where email=?',(account['auth'],name))
        self.h.enable_hysteria.side_effect=enable

    def upload(self,request=None):
        stream=io.BytesIO()
        with tarfile.open(fileobj=stream,mode='w') as archive:
            for name,data in [('request.json',host.dumps(request or self.request).encode()),('host.py',Path(host.__file__).read_bytes())]:
                item=tarfile.TarInfo(name);item.size=len(data);archive.addfile(item,io.BytesIO(data))
        stream.seek(0);return stream

    def test_transport_snapshot_phase_rollback_and_request_binding(self):
        self.assertEqual(self.h.transport_prepare(self.upload()),{'prepared':True})
        pending=self.h.read_json(self.h.ROOT/'pending.json')
        self.assertEqual(pending['kind'],'transport-upgrade');self.assertEqual(pending['phase'],'prepared')
        self.assertTrue((self.h.ROOT/'transport-receipts'/('1'*32+'.json')).is_file())
        self.assertIsNone(self.h.transaction_receipt(self.request))
        self.assertTrue(self.h.abort(self.request)['queued'])
        result=self.h.abort(self.request)
        self.assertFalse(result['committed']);self.assertFalse(result['pending'])
        self.assertEqual((self.h.ROOT/'active/manifest.json').read_bytes(),self.original)
        with self.assertRaisesRegex(self.h.Error,'different transport request'):
            self.h.transport_prepare(self.upload(dict(self.request,before_sha256='0'*64)))

    def test_transport_commit_receipt_replay_and_lost_ack(self):
        self.h.transport_prepare(self.upload())
        self.h.atomic(self.h.ROOT/'export-sha256','receipt')
        request=dict(self.request,offnode_sha256='receipt')
        result=self.h.transport_commit(request)
        self.assertTrue(result['committed']);self.assertEqual(result['transports'],['tcp-reality','hysteria2'])
        self.assertEqual(self.h.transport_commit(request),result)
        self.assertEqual(self.h.abort(self.request),result)
        with self.assertRaises(self.h.Error):self.h.transport_commit(dict(request,ssh_port=2222))

    def test_transition_fingerprint_binds_before_archive_and_topology(self):
        base=dict(self.request,before_sha256='a'*64,source_transports=['tcp-reality'],target_transports=['tcp-reality','hysteria2'])
        self.assertNotEqual(self.h.transport_request(base),self.h.transport_request(dict(base,before_sha256='b'*64)))
        with self.assertRaises(self.h.Error):self.h.transport_request(dict(base,target_transports=['tcp-reality']))
        with self.assertRaises(self.h.Error):self.h.transport_request(dict(base,kind='devices'))
        self.assertNotEqual(self.h.receipt_path(base),self.h.ROOT/'device-receipts'/('1'*32+'.json'))

    def test_bad_identity_lease_and_receipt_fail_without_false_commit(self):
        with self.assertRaises(self.h.Error):self.h.transport_prepare(self.upload(dict(self.request,ssh_port=2222)))
        self.h.transport_prepare(self.upload())
        pending=self.h.read_json(self.h.ROOT/'pending.json');pending['deadline']=0
        self.h.atomic(self.h.ROOT/'pending.json',host.dumps(pending))
        with self.assertRaisesRegex(self.h.Error,'expired'):self.h.transport_commit(self.request)
        self.assertIsNone(self.h.transaction_receipt(self.request))


if __name__=='__main__':unittest.main()
