"""Hard loss + source-derived systemd ordering; NEVER real systemctl/SSH.

The graph executes queued starts only AFTER the recovery oneshot exits. Readiness
before that prerequisite fails the test. Stored adapter bytes are loaded afresh.
Native format/auth gates have separate pinned-binary integration tests.
"""
import argparse
import base64
import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import types
import unittest
import uuid
from unittest import mock

import test_remote
import host
import vps


class OrderedServices:
    def __init__(self, fixture):
        self.f = fixture
        self.file = fixture.root / 'systemd-model.json'
        self.oneshot = False
        self.probes = []
        self.crash_queue = False
        self.save({'active': {u: True for u in (host.PANEL, host.FRONT)},
                   'enabled': {host.PANEL: False, host.FRONT: True}, 'jobs': []})
        texts = fixture.h.unit_texts()
        for unit in (host.PANEL, host.FRONT):
            assert 'After=network-online.target ' + host.RECOVER + '\n' in texts[unit]
            assert 'Before=' + host.PANEL + ' ' + host.FRONT in texts[host.RECOVER]
        assert 'Type=oneshot' in texts[host.RECOVER]

    def save(self, value):
        host.atomic(self.file, host.dumps(value))

    def state(self, unit):
        value = host.read_json(self.file)
        return {'running': value['active'][unit], 'enabled': value['enabled'][unit]}

    def ctl(self, action, *args):
        unit = args[-1]
        value = host.read_json(self.file)
        if action == 'stop':
            value['active'][unit] = False
            value['jobs'] = [u for u in value['jobs'] if u != unit]
        elif action in ('enable', 'disable'):
            value['enabled'][unit] = action == 'enable'
        elif action == 'start':
            if self.oneshot:
                if '--no-block' not in args:
                    raise subprocess.TimeoutExpired(['systemctl', action, *args], 60)
                if unit not in value['jobs']: value['jobs'].append(unit)
            else:
                value['active'][unit] = True
        else:
            raise AssertionError((action, args))
        self.save(value)
        if self.crash_queue and action == 'start': os._exit(76)

    def exit_oneshot(self, adapter):
        self.oneshot = False
        value = host.read_json(self.file)
        for unit in value['jobs']:
            adapter.guard()  # real stored guard, after predecessor exited
            value['active'][unit] = True
        value['jobs'] = []
        self.save(value)

    def probe(self, unit):
        if not self.state(unit)['running']:
            raise AssertionError('readiness waited before dependent job could run')
        self.probes.append(unit)


class Recovery(unittest.TestCase):
    def setUp(self):
        self.f = test_remote.FakeHost('runTest'); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        f = self.f; f.upload()
        f.h.atomic(f.h.ROOT/'export-sha256', 'fixture-receipt')
        f.h.commit(dict(f.request, machine_id='a'*32, offnode_sha256='fixture-receipt'))
        self.graph = OrderedServices(f)
        self.state = vps.private_dir(f.root/'operator')
        self.record = {'endpoint':f.request['endpoint'],'machine_id':'a'*32}
        self.document = {'format':host.FORMAT,'owner':f.request['owner'],'config':f.config,
                         'current':self.record,'previous':[],'backup':None}
        host.atomic(self.state/'state.json',host.dumps(self.document))
        for key, value in [('ctl',self.graph.ctl),('service_state',self.graph.state),
                           ('wait_tls',lambda manifest, **kw:self.graph.probe(host.FRONT))]:
            patch = mock.patch.object(f.h,key,side_effect=value); patch.start(); self.addCleanup(patch.stop)

    def stored(self, old=False):
        source = subprocess.check_output(['git','show','82235b2:vps/lib/host.py']) if old else (self.f.h.ROOT/'host.py').read_bytes()
        module = types.ModuleType('stored_recovery')
        exec(compile(source,'stored-host.py','exec'),module.__dict__)
        module.ROOT, module.UNITS = self.f.h.ROOT, self.f.h.UNITS
        module.machine_id, module.boot_id = self.f.h.machine_id, self.f.h.boot_id
        module.check_units = self.f.h.check_units
        module.check_request_identity = self.f.h.check_request_identity
        module.ctl, module.service_state = self.graph.ctl, self.graph.state
        module.wait_api = lambda manifest:self.graph.probe(host.PANEL)
        module.wait_tls = lambda manifest, **kw:self.graph.probe(host.FRONT)
        module.tls_probe = lambda manifest:self.graph.probe(host.FRONT)
        module.backend_health = self.f.h.backend_health
        module.process_health = lambda:[self.graph.probe(u) for u in (host.PANEL,host.FRONT)]
        return module

    def intent(self):
        h = self.f.h; active = h.ROOT/'active'; rollback = h.ROOT/'device-rollback'
        rollback.mkdir(mode=0o700)
        for name in ('manifest.json','Caddyfile'):
            h.atomic(rollback/name,(active/name).read_bytes())
        h.atomic(rollback/'host.py',(h.ROOT/'host.py').read_bytes())
        h.db_snapshot(active/'db/x-ui.db',rollback/'x-ui.db')
        pending = {'boot_id':'old-boot','deadline':0,'had_active':True,'kind':'devices','phase':'ready',
                   'operation_id':'1'*32,'request_sha256':'binding',
                   'services':{u:self.graph.state(u) for u in (host.PANEL,host.FRONT)}}
        h.atomic(rollback/'intent.json',h.dumps(pending)); h.atomic(h.ROOT/'pending.json',h.dumps(pending))
        return pending

    def test_old_ordering_negative_and_queued_restore_exit_crash_boot_positive(self):
        h = self.f.h; self.intent()
        old = self.stored(old=True); self.graph.oneshot = True
        with self.assertRaises(subprocess.TimeoutExpired): old.recover()
        self.assertEqual(h.read_json(h.ROOT/'pending.json')['phase'],'recovering')
        self.graph.oneshot = False
        current = self.stored(); self.graph.crash_queue = True
        pid = os.fork()
        if pid == 0:
            self.graph.oneshot = True; current.recover(); os._exit(99)
        _, status = os.waitpid(pid,0)
        self.assertEqual(os.waitstatus_to_exitcode(status),76)
        self.assertEqual(h.read_json(h.ROOT/'pending.json')['phase'],'restore-queued')
        # Reboot loses queued jobs. Stored intent authorizes ONLY restored state.
        value = host.read_json(self.graph.file); value['jobs'] = []; self.graph.save(value)
        h.atomic(self.f.root/'proc/sys/kernel/random/boot_id','another-boot')
        self.graph.crash_queue = False; self.graph.oneshot = True
        queued = current.recover()
        self.assertTrue(queued['pending']); self.assertTrue(queued['queued'])
        self.assertEqual(self.graph.probes,[])
        self.assertFalse(self.graph.state(host.PANEL)['running'])
        self.graph.exit_oneshot(current)
        self.graph.oneshot = True
        result = current.recover()
        self.assertFalse(result['pending']); self.assertIn('restored',result)
        self.graph.exit_oneshot(current)
        self.assertFalse(self.graph.state(host.PANEL)['enabled'])
        self.assertTrue(self.graph.state(host.FRONT)['enabled'])
        self.assertTrue(all(self.graph.state(u)['running'] for u in (host.PANEL,host.FRONT)))

    def test_readiness_failure_keeps_restoration_fail_closed(self):
        self.intent(); adapter = self.stored(); self.graph.oneshot = True
        self.assertTrue(adapter.recover()['queued']); self.graph.exit_oneshot(adapter)
        with mock.patch.object(adapter,'wait_tls',side_effect=adapter.Error('fixture invalid TLS')):
            with self.assertRaises(adapter.Error): adapter.recover()
        self.assertFalse(self.graph.state(host.FRONT)['running'])
        self.assertFalse(self.graph.state(host.PANEL)['running'])
        self.assertTrue((self.f.h.ROOT/'pending.json').exists())
        self.graph.oneshot = True
        self.assertTrue(adapter.recover()['queued']); self.graph.exit_oneshot(adapter)
        self.assertFalse(adapter.recover()['pending'])

    def test_first_and_committed_backup_hard_exit_stored_recovery_and_local_reconcile(self):
        h = self.f.h
        original = h.read_json(h.ROOT/'active/manifest.json')
        for phase in ('prechange','postcommit','reconciliation'):
            with self.subTest(phase=phase):
                manifest = h.read_json(h.ROOT/'active/manifest.json')
                if phase == 'postcommit':
                    manifest['accounts']['company'].update(id=str(uuid.uuid4()),subId=os.urandom(24).hex())
                    h.atomic(h.ROOT/'active/manifest.json',h.dumps(manifest))
                    with sqlite3.connect(h.ROOT/'active/db/x-ui.db') as db:
                        c=manifest['accounts']['company']; db.execute('update clients set uuid=?,sub_id=? where email=?',(c['id'],c['subId'],'company'))
                expected = copy.deepcopy(manifest['accounts'])
                request=dict(vps.request_for(self.document,self.record),device_action='rotate',names=['company'],
                             operation_id=os.urandom(16).hex(),adapter_sha256=host.digest(vps.MODULE/'lib/host.py'))
                if phase != 'prechange':
                    h.atomic(h.ROOT/'device-receipts'/(request['operation_id']+'.json'),h.dumps({'committed':True,
                        'request_sha256':h.device_request(request),'machine_id':'a'*32,'node_id':manifest['node_id']}))
                    host.atomic(self.state/'pending.json',host.dumps({'record':self.record,'mode':'devices','request':request,'mihomo':'/bin/true'}))
                # First backup migrates the real legacy adapter before stop.
                if phase == 'prechange':
                    h.atomic(h.ROOT/'host.py',subprocess.check_output(['git','show','18d1b3b:vps/lib/host.py']),0o700)
                real_ctl = self.graph.ctl
                def die(action,*args):
                    real_ctl(action,*args)
                    if action == 'stop' and args[-1] == host.FRONT: os._exit(73)
                def remote(record,action,request=None,stdout=None,**kw):
                    self.assertEqual(action,'backup')
                    with h.export_backup(request).open('rb') as source: stdout.write(source.read())
                pid=os.fork()
                if pid == 0:
                    with mock.patch.object(h,'ctl',side_effect=die),mock.patch.object(vps,'remote',side_effect=remote):
                        vps.backup_remote(self.state,self.document,self.record)
                    os._exit(99)
                _,status=os.waitpid(pid,0); self.assertEqual(os.waitstatus_to_exitcode(status),73)
                self.assertTrue((h.ROOT/'backup-stop.json').exists())
                self.assertTrue((self.state/'pending.json').exists())
                self.assertFalse(self.graph.state(host.FRONT)['running'])
                self.assertEqual(host.digest(h.ROOT/'host.py'),host.digest(vps.MODULE/'lib/host.py'))
                adapter=self.stored(); exports=[]
                def recover_remote(record,action,request=None,**kw):
                    if action=='abort':
                        self.graph.oneshot=True
                        result=adapter.abort(request)
                        self.graph.exit_oneshot(adapter)
                        return result
                    if action=='status': return adapter.status(request)
                    raise AssertionError(action)
                def backup(state,document,record):
                    self.assertTrue(self.graph.state(host.FRONT)['running'])
                    self.assertEqual(h.read_json(h.ROOT/'active/manifest.json')['accounts'],expected)
                    return self.state/'reconciled.tar'
                with mock.patch.object(vps,'remote',side_effect=recover_remote),mock.patch.object(vps,'backup_remote',side_effect=backup), \
                        mock.patch.object(vps,'validate_outputs'),mock.patch.object(vps,'export_after_commit',side_effect=lambda state:exports.append(True)):
                    vps.recover_local(self.state,self.document)
                    self.assertTrue((self.state/'pending.json').exists(),'queued response must retain local intent')
                    self.assertEqual(exports,[])
                    vps.recover_local(self.state,self.document)
                self.assertFalse((self.state/'pending.json').exists())
                self.assertEqual(exports,[True]); self.assertEqual(h.read_json(h.ROOT/'active/manifest.json')['accounts'],expected)
                self.assertFalse((h.ROOT/'backup-stop.json').exists())
        self.assertNotEqual(original['accounts']['company']['id'],expected['company']['id'])

    def test_expired_candidate_backup_stop_cannot_revive_candidate(self):
        h=self.f.h; self.intent(); adapter=self.stored()
        original=h.read_json(h.ROOT/'active/manifest.json')
        candidate=copy.deepcopy(original); candidate['published']=False
        candidate['accounts']['company']['id']=str(uuid.uuid4())
        h.atomic(h.ROOT/'active/manifest.json',h.dumps(candidate))
        with sqlite3.connect(h.ROOT/'active/db/x-ui.db') as db:
            db.execute('update clients set uuid=? where email=?',(candidate['accounts']['company']['id'],'company'))
        h.atomic(h.ROOT/'backup-stop.json',h.dumps({'owner':self.document['owner'],'machine_id':'a'*32,
            'node_id':original['node_id'],'phase':'stopping','front':self.graph.state(host.FRONT)}))
        with self.assertRaises(adapter.Error): adapter.guard()
        self.graph.oneshot=True
        result=adapter.recover(); self.assertTrue(result['queued']); self.assertFalse(result['committed'])
        self.assertEqual(h.read_json(h.ROOT/'active/manifest.json'),original)
        self.assertFalse(self.graph.state(host.FRONT)['running'])
        self.graph.exit_oneshot(adapter)
        self.assertFalse(adapter.recover()['pending'])
        self.assertFalse((h.ROOT/'backup-stop.json').exists())
        self.assertEqual(h.snapshot_accounts(h.ROOT/'active/db/x-ui.db',original),original['accounts'])

    def test_previous_adapter_unsafe_inputs_refuse_before_stop_or_migration(self):
        import signal
        import stat
        h = self.f.h
        predecessor = h.ROOT/('adapter-before-'+h.LEGACY_ADAPTER_SHA256+'.py')
        outside = self.f.root/'outside-sentinel'
        h.atomic(outside,'SYNTHETIC-OUTSIDE-ARCHIVE-SENTINEL')
        sources = {'current':(vps.MODULE/'lib/host.py').read_bytes(),
                   'legacy':subprocess.check_output(['git','show','18d1b3b:vps/lib/host.py'])}
        def io_timeout(*args):
            raise AssertionError('FIFO input blocked')
        for source_name, source in sources.items():
            for kind in ('outside-link','dangling-link','directory','fifo','wrong-digest'):
                with self.subTest(source=source_name,kind=kind):
                    h.atomic(h.ROOT/'host.py',source,0o700)
                    if kind == 'outside-link': predecessor.symlink_to(outside)
                    elif kind == 'dangling-link': predecessor.symlink_to(self.f.root/'missing-sentinel')
                    elif kind == 'directory': predecessor.mkdir()
                    elif kind == 'fifo': os.mkfifo(predecessor)
                    else: h.atomic(predecessor,'not-the-pinned-legacy-source')
                    before = predecessor.lstat()
                    old_handler = signal.signal(signal.SIGALRM,io_timeout)
                    try:
                        signal.alarm(2)
                        with mock.patch.object(h,'ctl',wraps=h.ctl) as control, \
                                mock.patch.object(h,'atomic',wraps=h.atomic) as writes, \
                                mock.patch.object(h,'retain_previous_adapter',wraps=h.retain_previous_adapter) as retention:
                            with self.assertRaisesRegex(h.Error,'[Pp]redecessor'):
                                h.export_backup()
                            control.assert_not_called(); writes.assert_not_called(); retention.assert_not_called()
                        self.assertEqual((h.ROOT/'host.py').read_bytes(),source)
                        self.assertEqual(predecessor.lstat().st_ino,before.st_ino)
                        self.assertEqual(predecessor.lstat().st_mode,before.st_mode)
                        self.assertFalse((h.ROOT/'backup-stop.json').exists())
                        self.assertFalse((h.ROOT/'export.tar').exists())
                        self.assertEqual(outside.read_text(),'SYNTHETIC-OUTSIDE-ARCHIVE-SENTINEL')
                    finally:
                        signal.alarm(0); signal.signal(signal.SIGALRM,old_handler)
                        if stat.S_ISDIR(before.st_mode): predecessor.rmdir()
                        else: predecessor.unlink()

    def test_previous_adapter_absent_genuine_migration_and_current_reuse_exports(self):
        import tarfile
        h = self.f.h
        predecessor = h.ROOT/('adapter-before-'+h.LEGACY_ADAPTER_SHA256+'.py')
        legacy = subprocess.check_output(['git','show','18d1b3b:vps/lib/host.py'])
        with tarfile.open(h.export_backup()) as archive:
            self.assertNotIn('previous-host.py',archive.getnames())
        self.assertFalse(predecessor.exists())
        h.atomic(h.ROOT/'host.py',legacy,0o700)
        for state in ('migration','current-reuse'):
            with self.subTest(state=state):
                with tarfile.open(h.export_backup()) as archive:
                    self.assertEqual(archive.extractfile('previous-host.py').read(),legacy)
                self.assertEqual(predecessor.read_bytes(),legacy)
                self.assertEqual(host.digest(h.ROOT/'host.py'),host.digest(vps.MODULE/'lib/host.py'))
                self.assertTrue(self.graph.state(h.FRONT)['running'])
        # Even with an already genuine predecessor, migration must not overwrite it.
        inode = predecessor.stat().st_ino
        h.atomic(h.ROOT/'host.py',legacy,0o700)
        h.export_backup()
        self.assertEqual(predecessor.stat().st_ino,inode)

    def test_previous_adapter_cache_prevents_link_laundering_after_validation(self):
        import tarfile
        h = self.f.h
        predecessor = h.ROOT/('adapter-before-'+h.LEGACY_ADAPTER_SHA256+'.py')
        legacy = subprocess.check_output(['git','show','18d1b3b:vps/lib/host.py'])
        h.atomic(predecessor,legacy)
        outside = self.f.root/'raced-outside-sentinel'; h.atomic(outside,'outside-race-sentinel')
        real_control = h.ctl
        def raced_stop(action,*args):
            if action == 'stop' and args[-1] == h.FRONT:
                predecessor.unlink(); predecessor.symlink_to(outside)
            return real_control(action,*args)
        with mock.patch.object(h,'ctl',side_effect=raced_stop):
            archive_path = h.export_backup()
        with tarfile.open(archive_path) as archive:
            self.assertEqual(archive.extractfile('previous-host.py').read(),legacy)
        self.assertTrue(predecessor.is_symlink(),'do not delete or chmod the raced-in unknown path')
        self.assertEqual(outside.read_text(),'outside-race-sentinel')

    def test_previous_adapter_raced_creation_and_post_install_tamper_refuse(self):
        h = self.f.h
        predecessor = h.ROOT/('adapter-before-'+h.LEGACY_ADAPTER_SHA256+'.py')
        legacy = subprocess.check_output(['git','show','18d1b3b:vps/lib/host.py'])
        h.atomic(h.ROOT/'host.py',legacy,0o700)
        real_link = os.link
        def raced_link(source,target,**kwargs):
            h.atomic(predecessor,'raced-in-unknown-input')
            return real_link(source,target,**kwargs)
        with mock.patch.object(h.os,'link',side_effect=raced_link),mock.patch.object(h,'ctl') as control:
            with self.assertRaisesRegex(h.Error,'appeared'):
                h.export_backup()
            control.assert_not_called()
        self.assertEqual(predecessor.read_text(),'raced-in-unknown-input')
        self.assertEqual((h.ROOT/'host.py').read_bytes(),legacy)
        predecessor.unlink()
        real_atomic = h.atomic
        def tamper_after_install(path,data,mode=0o600):
            real_atomic(path,data,mode)
            if path == h.ROOT/'host.py': predecessor.write_bytes(b'tampered-after-adapter-install')
        with mock.patch.object(h,'atomic',side_effect=tamper_after_install),mock.patch.object(h,'ctl') as control:
            with self.assertRaisesRegex(h.Error,'digest mismatch'):
                h.export_backup()
            control.assert_not_called()
        self.assertEqual(predecessor.read_bytes(),b'tampered-after-adapter-install')
        self.assertFalse((h.ROOT/'backup-stop.json').exists())

    def test_backup_initially_inactive_stays_inactive(self):
        h=self.f.h; value=host.read_json(self.graph.file); value['active'][host.FRONT]=False; self.graph.save(value)
        h.atomic(h.ROOT/'backup-stop.json',h.dumps({'owner':self.document['owner'],'machine_id':'a'*32,
            'node_id':h.read_json(h.ROOT/'active/manifest.json')['node_id'],'phase':'stopping','front':self.graph.state(host.FRONT)}))
        adapter=self.stored(); self.graph.oneshot=True
        self.assertTrue(adapter.recover()['queued']); self.graph.exit_oneshot(adapter)
        result=adapter.recover(); self.assertFalse(result['pending']); self.assertFalse(result['front_running'])
        self.assertFalse(self.graph.state(host.FRONT)['running']); self.assertTrue(self.graph.state(host.FRONT)['enabled'])
        self.assertEqual(self.graph.probes,[])
        # Timer has already disarmed the remote stop intent. A later operator
        # recover must still preserve inactive front, not require public health.
        def remote(record,action,request=None,**kwargs):
            self.assertEqual(action,'abort')
            return adapter.abort(request)
        with mock.patch.object(vps,'remote',side_effect=remote), \
                mock.patch.object(vps,'backup_remote',return_value=self.state/'inactive.tar'), \
                mock.patch.object(vps,'export_after_commit'):
            vps.recover_local(self.state,self.document)
        self.assertFalse((self.state/'pending.json').exists())
        self.assertFalse(self.graph.state(host.FRONT)['running'])


if __name__=='__main__': unittest.main()
