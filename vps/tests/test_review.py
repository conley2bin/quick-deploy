"""Discriminating R1/R3/R4/R6/R7/R8 regressions; no real SSH/systemd."""
import argparse
import base64
import fcntl
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import host
import vps
import test_remote as fixtures


class ReviewGates(unittest.TestCase):
    def fixture(self):
        fixture = fixtures.FakeHost()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_periodic_recovery_dispatch_during_prepare_backup_commit(self):
        for operation in ('prepare', 'backup', 'commit'):
            with self.subTest(operation=operation):
                f = fixtures.FakeHost()
                f.setUp()
                child = None
                try:
                    if operation != 'prepare':
                        f.upload()
                    if operation == 'commit':
                        f.h.atomic(f.h.ROOT / 'export-sha256', 'receipt')
                    lock = f.root / 'run/lock/qd-3x-ui.lock'
                    lock.parent.mkdir(parents=True, exist_ok=True)
                    saved_ctl = f.ctl
                    def dispatch():
                        return subprocess.Popen([sys.executable, '-B', str(f.root / 'host_fixture.py'), 'recover'],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    def ordered_ctl(*args):
                        nonlocal child
                        trigger = {'prepare': ('start', f.h.TIMER), 'backup': ('start', f.h.FRONT),
                                   'commit': ('restart', f.h.FRONT)}[operation]
                        if args == trigger and child is None:
                            child = dispatch()
                        if child is not None and args in (('start', f.h.PANEL), ('start', f.h.FRONT), ('restart', f.h.FRONT)):
                            try:
                                output, error = child.communicate(timeout=1)
                            except subprocess.TimeoutExpired:
                                self.fail('periodic recovery blocked on caller flock while service ordering waits')
                            self.assertEqual(child.returncode, 0, error.decode())
                            self.assertTrue(json.loads(output)['busy'])
                        return saved_ctl(*args)
                    original_run = subprocess.run
                    def fake_systemctl(argv, **kwargs):
                        if argv == ['systemctl', 'is-active', '--quiet', f.h.FRONT]:
                            return subprocess.CompletedProcess(argv, 0, b'', b'')
                        return original_run(argv, **kwargs)
                    with lock.open('w') as holder, mock.patch.object(f.h, 'ctl', side_effect=ordered_ctl), \
                         mock.patch.object(subprocess, 'run', side_effect=fake_systemctl):
                        fcntl.flock(holder, fcntl.LOCK_EX)
                        if operation == 'prepare':
                            f.upload()
                        elif operation == 'backup':
                            f.h.export_backup()
                        else:
                            f.h.commit(dict(f.request, machine_id='a' * 32, offnode_sha256='receipt'))
                    self.assertIsNotNone(child)
                    self.assertEqual(child.returncode, 0)
                finally:
                    if child is not None:
                        if child.poll() is None:
                            child.kill()
                            child.wait()
                        child.stdout.close()
                        child.stderr.close()
                    f.doCleanups()

    def test_effective_foreign_units_and_dropins_refuse_before_execution(self):
        for kind in ('exact-dropin', 'vendor', 'runtime', 'type-dropin', 'loaded-dropin', 'loaded-fragment', 'runtime-source'):
            with self.subTest(kind=kind):
                f = fixtures.FakeHost()
                f.setUp()
                try:
                    if kind == 'exact-dropin':
                        path = f.h.UNITS / (f.h.PANEL + '.d/override.conf')
                    elif kind == 'vendor':
                        path = f.root / 'usr/lib/systemd/system' / f.h.PANEL
                    elif kind == 'runtime':
                        path = f.root / 'run/systemd/system' / f.h.PANEL
                    elif kind == 'type-dropin':
                        path = f.root / 'run/systemd/system/service.d/hook.conf'
                    else:
                        f.upload()
                        props = {'LoadState': 'loaded'}
                        props.update({'DropInPaths': '/synthetic/foreign.conf'} if kind == 'loaded-dropin' else
                                     {'FragmentPath': '/synthetic/foreign.service'} if kind == 'loaded-fragment' else
                                     {'SourcePath': '/synthetic/runtime-config'})
                        f.unit_overrides[f.h.PANEL] = props
                        path = None
                    if path:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text('[Service]\nExecStartPre=/synthetic/foreign-hook\n')
                    f.events.clear()
                    with self.assertRaisesRegex(f.h.Error, '[Ff]oreign|[Dd]rop.in|[Ee]ffective|[Rr]untime'):
                        f.h.preflight(f.request)
                    self.assertFalse(any(event[0] in ('start', 'restart') for event in f.events))
                    if path:
                        self.assertIn('foreign-hook', path.read_text())
                    else:
                        with self.assertRaises(f.h.Error):
                            f.control_service('start', f.h.PANEL)
                finally:
                    f.doCleanups()

    def test_commit_rejects_expired_changed_boot_and_midcheck_expiry(self):
        for kind in ('expired', 'reboot', 'during-health'):
            with self.subTest(kind=kind):
                f = fixtures.FakeHost()
                f.setUp()
                try:
                    f.upload()
                    path = f.h.ROOT / 'pending.json'
                    pending = f.h.read_json(path)
                    if kind == 'expired':
                        pending['deadline'] = 0
                    elif kind == 'reboot':
                        pending['boot_id'] = 'other-boot'
                    else:
                        def expire(*args):
                            current = f.h.read_json(path)
                            current['deadline'] = 0
                            f.h.atomic(path, f.h.dumps(current))
                            return {}
                        f.h.backend_health.side_effect = expire
                    f.h.atomic(path, f.h.dumps(pending))
                    f.h.atomic(f.h.ROOT / 'export-sha256', 'receipt')
                    if kind != 'during-health':
                        with self.assertRaises(f.h.Error):
                            f.h.guard()
                    with self.assertRaisesRegex(f.h.Error, '[Ll]ease|[Ee]xpired|[Bb]oot'):
                        f.h.commit(dict(f.request, machine_id='a' * 32, offnode_sha256='receipt'))
                    self.assertNotIn('@feed', (f.h.ROOT / 'active/Caddyfile').read_text())
                    self.assertTrue(path.exists())
                finally:
                    f.doCleanups()

    def test_same_target_preserves_nondefault_identity_and_ssh_port(self):
        args = argparse.Namespace(ssh='verified-alias', identity=Path('/synthetic/nondefault-key'), port=2222)
        resolved = subprocess.CompletedProcess([], 0, b'hostname 93.184.216.34\nuser root\nport 2222\n')
        with mock.patch.object(host, 'run', return_value=resolved):
            first = vps.target_record(args)
            first['machine_id'] = 'a' * 32
            repeat = vps.target_record(argparse.Namespace(ssh=args.ssh, identity=None, port=None), first)
        self.assertIn('/synthetic/nondefault-key', repeat['options'])
        self.assertIn('IdentitiesOnly=yes', repeat['options'])
        self.assertEqual(repeat['options'][repeat['options'].index('-p') + 1], '2222')
        self.assertEqual(repeat['machine_id'], first['machine_id'])

    def test_uri_identity_gate_survives_python_optimization(self):
        code = '''import base64,host
m=host.new_manifest('fixture',{'domain':'sub.example.org','reality_target':'target.example.org','acme_email':'operator@example.org','proxy_port':443},'93.184.216.34')
m['reality']={'publicKey':'y'*43,'shortIds':['0123456789abcdef']}
bad=base64.b64encode(b'https://wrong.invalid/not-a-vless-subscription')
try: host.validate_feed_scope(m,'company',bad,'sub')
except host.Error: print('REJECTED')
else: print('ACCEPTED')
'''
        for optimization in ('0', '1'):
            result = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, timeout=5,
                env=dict(os.environ, PYTHONOPTIMIZE=optimization, PYTHONPATH=str(vps.MODULE / 'lib')))
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(result.stdout.strip(), b'REJECTED')

    def test_alias_change_cannot_receive_private_archive_bytes(self):
        with tempfile.TemporaryDirectory(prefix='qd-fake-ssh-') as tmp:
            root = Path(tmp)
            alias = root / 'alias.json'
            alias.write_text(json.dumps({'host': '93.184.216.34', 'user': 'root', 'port': '2222'}))
            script = root / 'ssh'
            script.write_text('#!' + sys.executable + '\n' + '''import json,os,sys,pathlib
args=sys.argv[1:]; root=pathlib.Path(os.environ['SSH_FIXTURE']); selected=json.loads((root/'alias.json').read_text())
opts={}; i=0
while i < len(args):
 if args[i]=='-o':
  k,_,v=args[i+1].partition('='); opts.setdefault(k.lower(),v); i+=2
 elif args[i] in ('-p','-l'):
  opts.setdefault('port' if args[i]=='-p' else 'user',args[i+1]); i+=2
 else: i+=1
if '-G' in args:
 print('hostname '+selected['host']+'\\nuser '+selected['user']+'\\nport '+selected['port']); sys.exit(0)
if opts.get('stricthostkeychecking')!='yes': sys.exit(90)
# Both synthetic hosts are already trusted. The changed alias is not an unknown-key test.
host=opts.get('hostname',selected['host']); user=opts.get('user',selected['user']); port=opts.get('port',selected['port'])
data=sys.stdin.buffer.read(); (root/(host+'.received')).write_bytes(data)
(root/'last-transport.json').write_text(json.dumps([host,user,port]))
print('{"ok":true}')
''')
            script.chmod(0o700)
            with mock.patch.dict(os.environ, PATH=str(root) + os.pathsep + os.defpath, SSH_FIXTURE=str(root)):
                args = argparse.Namespace(ssh='verified-alias', identity=None, port=None)
                record = vps.target_record(args)
                vps.remote(record, 'preflight', {'public': 'inventory-only'})
                alias.write_text(json.dumps({'host': '93.184.216.35', 'user': 'alternate', 'port': '2200'}))
                with tempfile.TemporaryFile() as archive:
                    archive.write(b'PRIVATE-ARCHIVE-SENTINEL')
                    archive.seek(0)
                    vps.remote(record, 'prepare', stdin=archive)
            alternate = root / '93.184.216.35.received'
            self.assertFalse(alternate.exists(), 'already-known alternate host received private bytes')
            self.assertEqual(json.loads((root / 'last-transport.json').read_text()), ['93.184.216.34', 'root', '2222'])
            self.assertEqual((root / '93.184.216.34.received').read_bytes(), b'PRIVATE-ARCHIVE-SENTINEL')


if __name__ == '__main__':
    unittest.main()
