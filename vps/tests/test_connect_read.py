"""connect_read on a synthetic owned host: read-only, standard archive, no stream on refusal.

Real files, SQLite, openssl/ssl and the real archive/unpack code; only system
and artifact-health boundaries of the UNCHANGED host.py are mocked. Native
backup acceptance is exercised later by the native connect validator.
"""
import contextlib
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import connect_read
import host

DOMAIN = 'sub.example.org'
ENDPOINT = '93.184.216.34'


def tree(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob('*')) if p.is_file()} | \
           {str(p.relative_to(root)) + '/': '' for p in Path(root).rglob('*') if p.is_dir()}


class Stream(io.BytesIO):
    pass


class ConnectRead(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='qd-connect-read-')
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root, self.units, self.scratch = base / 'qd', base / 'units', base / 'scratch'
        for path in (self.root, self.units, self.scratch):
            path.mkdir(mode=0o700)
        self.lockfile = base / 'qd.lock'  # absent: first use creates it (fresh boot)
        active = self.active = self.root / 'active'
        config = {'domain': DOMAIN, 'reality_target': 'target.example.org', 'acme_email': '', 'proxy_port': 443}
        self.manifest = host.new_manifest('fixture-owner', config, ENDPOINT)
        self.manifest['published'] = True
        self.lock = {'panel': {'version': '9'}}
        patches = [mock.patch.object(host, 'ROOT', self.root), mock.patch.object(host, 'UNITS', self.units),
                   mock.patch.object(connect_read, 'LOCK_PATH', self.lockfile),
                   mock.patch.object(tempfile, 'tempdir', str(self.scratch)),
                   mock.patch.object(host, 'machine_id', return_value='m' * 32),
                   mock.patch.dict(os.environ, {'QD_SSH_CONNECTION': '1.2.3.4 5555 %s 22' % ENDPOINT}),
                   mock.patch.object(host, 'verify_artifacts'), mock.patch.object(host, 'binary_health'),
                   mock.patch.object(host, 'snapshot_accounts', side_effect=self.accounts),
                   mock.patch.object(host, 'snapshot_host_group', return_value='group-1')]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.verified = []
        mock.patch.object(host, 'verify_backup', side_effect=self.verify).start()
        for name in ('ctl', 'export_backup', 'install_backup_adapter', 'adapter_source', 'recover', 'abort'):
            mock.patch.object(host, name, side_effect=AssertionError('forbidden ' + name)).start()
        real_atomic = host.atomic
        def atomic(path, *args, **kwargs):
            resolved = Path(path).resolve()
            for owned in (self.root.resolve(), self.units.resolve()):
                assert owned not in resolved.parents, 'write under owned storage: ' + str(path)
            return real_atomic(path, *args, **kwargs)
        mock.patch.object(host, 'atomic', side_effect=atomic).start()
        self.addCleanup(mock.patch.stopall)
        (self.root / 'owner.json').write_text(host.dumps({'format': host.FORMAT, 'owner': 'fixture-owner', 'machine_id': 'm' * 32}))
        (self.root / 'host.py').write_bytes(b'installed adapter bytes\n')
        for name, text in host.unit_texts().items():
            (self.units / name).write_text(text)
        (active / 'db').mkdir(parents=True)
        for name, data in (('release-lock.json', json.dumps(self.lock)), ('binary-hashes.json', '{}'),
                           ('Caddyfile', 'front\n'), ('logs/x.log', 'secret log'), ('db/x-ui.db-wal', 'wal'), ('db/x-ui.db-shm', 'shm')):
            (active / name).parent.mkdir(parents=True, exist_ok=True)
            (active / name).write_text(data)
        self.pair = active / 'caddy/data/caddy/certificates/fixture'
        self.pair.mkdir(parents=True)
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:prime256v1', '-nodes',
                        '-keyout', str(self.pair / (DOMAIN + '.key')), '-out', str(self.pair / (DOMAIN + '.crt')),
                        '-subj', '/CN=' + DOMAIN, '-addext', 'subjectAltName=DNS:' + DOMAIN, '-days', '2'],
                       check=True, capture_output=True)
        (active / 'caddy/data/caddy/acme.json').write_text('{}')
        with contextlib.closing(sqlite3.connect(active / 'db/x-ui.db')) as db:
            db.execute('create table clients(id integer primary key, email text, enable int, up int)')
            db.executemany('insert into clients(email,enable,up) values(?,1,0)', [(n,) for n in host.device_names(self.manifest)])
            db.commit()
        self.write_manifest()
        self.request = {'endpoint': ENDPOINT}
        self.pull_request = {'endpoint': ENDPOINT, 'owner': 'fixture-owner', 'machine_id': 'm' * 32, 'node_id': self.manifest['node_id'],
                             'config': self.manifest['config'], 'lock': self.lock}

    def accounts(self, database, manifest=None, validate_metadata=True):
        with contextlib.closing(sqlite3.connect('file:%s?mode=ro' % database, uri=True)) as db:
            return {r[0]: {'email': r[0], 'enable': bool(r[1])} for r in db.execute('select email,enable from clients order by email')}

    def write_manifest(self, **change):
        self.manifest.update(change)
        (self.active / 'manifest.json').write_text(host.dumps(self.manifest))

    def verify(self, folder, lock):
        self.verified.append((self.out.tell(), lock))
        for name in ('backup.json', 'database-schema.sql', 'host.py', 'deployment/db/x-ui.db', 'deployment/manifest.json'):
            self.assertTrue((folder / name).is_file(), name)
        for name in host.UNIT_NAMES:
            self.assertTrue((folder / 'units' / name).is_file())
        if getattr(self, 'reject_archive', False):
            raise host.Error('archive rejected')

    out = Stream()

    def pull(self):
        self.out = Stream()
        connect_read.main(host, 'connect-pull', io.BytesIO(json.dumps(self.pull_request).encode()), self.out)
        return self.out.getvalue()

    def refuses(self, action, message=None, injected=False):
        self.out = Stream()
        request = self.pull_request if action == 'connect-pull' else self.request
        before = tree(self.root), tree(self.units)
        with self.assertRaises(host.Error) as caught:
            connect_read.main(host, action, io.BytesIO(json.dumps(request).encode()), self.out)
        if message:
            self.assertIn(message, str(caught.exception))
        self.assertEqual(self.out.getvalue(), b'', 'refusal must not stream')
        if not injected:  # an injected concurrent change legitimately differs
            self.assertEqual((tree(self.root), tree(self.units)), before)
        self.assertEqual(os.listdir(self.scratch), [], 'scratch must be removed')

    # --- success -------------------------------------------------------------
    def test_info_fields_from_live_authority(self):
        self.out = Stream()
        connect_read.main(host, 'connect-info', io.BytesIO(json.dumps(self.request).encode()), self.out)
        self.assertEqual(json.loads(self.out.getvalue()), {
            'format': host.FORMAT, 'owner': 'fixture-owner', 'machine_id': 'm' * 32, 'node_id': self.manifest['node_id'],
            'endpoint': ENDPOINT, 'config': self.manifest['config'], 'published': True})

    def test_pull_streams_standard_verified_archive_without_touching_owned_state(self):
        (self.root / ('adapter-before-' + host.LEGACY_ADAPTER_SHA256 + '.py')).write_bytes(b'x')  # wrong digest: refused
        self.refuses('connect-pull', 'digest mismatch')
        os.unlink(self.root / ('adapter-before-' + host.LEGACY_ADAPTER_SHA256 + '.py'))
        before = tree(self.root), tree(self.units)
        data = self.pull()
        self.assertEqual((tree(self.root), tree(self.units)), before)
        self.assertEqual(os.listdir(self.scratch), [])
        self.assertEqual(self.verified, [(0, self.lock)], 'accepted before the first stdout byte')
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            names = set(archive.getnames())
            self.assertTrue(all(m.isfile() and m.mode == 0o600 for m in archive))
            self.assertEqual(archive.extractfile('host.py').read(), b'installed adapter bytes\n')
            live = json.loads(archive.extractfile('deployment/manifest.json').read())
        self.assertLessEqual({'backup.json', 'database-schema.sql', 'host.py', 'deployment/db/x-ui.db', 'deployment/Caddyfile',
                              'deployment/release-lock.json', 'deployment/caddy/data/caddy/certificates/fixture/%s.crt' % DOMAIN,
                              'deployment/caddy/data/caddy/certificates/fixture/%s.key' % DOMAIN} | {'units/' + n for n in host.UNIT_NAMES}, names)
        self.assertFalse([n for n in names if 'logs' in n or n.endswith(('-wal', '-shm'))])
        self.assertEqual(live['accounts'], self.accounts(self.active / 'db/x-ui.db'))
        self.assertEqual(live['host_group_id'], 'group-1')

    def test_counter_drift_is_tolerated_but_policy_drift_refused(self):
        real = host.db_snapshot
        def counters(source, target):
            with contextlib.closing(sqlite3.connect(self.active / 'db/x-ui.db')) as db:
                db.execute('update clients set up=up+5'); db.commit()
            return real(source, target)
        with mock.patch.object(host, 'db_snapshot', side_effect=counters):
            self.pull()
        def disable(source, target):
            with contextlib.closing(sqlite3.connect(self.active / 'db/x-ui.db')) as db:
                db.execute("update clients set enable=0 where email='phone'"); db.commit()
            return real(source, target)
        with mock.patch.object(host, 'db_snapshot', side_effect=disable):
            self.refuses('connect-pull', 'changed during collection', injected=True)

    def test_live_db_names_override_stale_manifest_cache(self):
        with contextlib.closing(sqlite3.connect(self.active / 'db/x-ui.db')) as db:
            db.execute("update clients set enable=0 where email='phone'"); db.commit()
        with tarfile.open(fileobj=io.BytesIO(self.pull())) as archive:
            live = json.loads(archive.extractfile('deployment/manifest.json').read())
        self.assertFalse(live['accounts']['phone']['enable'])

    # --- refusals: nothing streamed, nothing written, scratch gone -----------
    def test_busy_missing_lock_pending_backup_stop_unpublished(self):
        fd = os.open(self.lockfile, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            self.refuses('connect-pull', 'busy')
            self.refuses('connect-info', 'busy')
        finally:
            os.close(fd)
        for name in ('pending.json', 'backup-stop.json'):
            (self.root / name).write_text('{}')
            self.refuses('connect-pull', 'recover')
            self.refuses('connect-info', 'recover')
            os.unlink(self.root / name)
        self.write_manifest(published=False)
        self.refuses('connect-pull', 'not published')
        self.write_manifest(published=True)

    def test_missing_lock_is_created_privately_and_unsafe_lock_refused(self):
        self.assertFalse(self.lockfile.exists())
        self.pull()
        self.assertEqual(stat.S_IMODE(self.lockfile.stat().st_mode), 0o600)
        self.lockfile.chmod(0o666)
        self.refuses('connect-info', 'Unsafe')
        self.lockfile.chmod(0o600)
        self.lockfile.unlink()
        self.lockfile.symlink_to(self.active / 'manifest.json')
        self.refuses('connect-info', 'lock')
        self.lockfile.unlink()
        (self.root / 'owner.json').unlink()  # actual state, not the lock, decides "not deployed"
        self.refuses('connect-info')

    def test_identity_mismatches(self):
        for key, value, text in (('owner', 'other', 'owner'), ('machine_id', 'z' * 32, 'machine_id'), ('node_id', 'n', 'node_id'),
                                 ('config', {'domain': 'x.example.org'}, 'config'), ('lock', {'panel': {'version': '8'}}, 'lock'),
                                 ('endpoint', '93.184.216.35', 'SSH endpoint')):
            good = self.pull_request[key]
            self.pull_request[key] = value
            self.refuses('connect-pull', text)
            self.pull_request[key] = good
        (self.root / 'owner.json').write_text(host.dumps({'format': host.FORMAT, 'owner': 'other', 'machine_id': 'm' * 32}))
        self.refuses('connect-info', 'disagrees')
        with mock.patch.dict(os.environ, {'QD_SSH_CONNECTION': '1.2.3.4 5555 93.184.216.35 22'}):
            self.refuses('connect-info', 'SSH endpoint')
        self.pull_request['adapter_sha256'] = 'f' * 64
        (self.root / 'owner.json').write_text(host.dumps({'format': host.FORMAT, 'owner': 'fixture-owner', 'machine_id': 'm' * 32}))
        self.refuses('connect-pull', 'adapter')

    def test_modified_unit_and_symlink_refused(self):
        (self.units / host.UNIT_NAMES[0]).write_text('foreign')
        self.refuses('connect-pull', 'unit')
        (self.units / host.UNIT_NAMES[0]).write_text(host.unit_texts()[host.UNIT_NAMES[0]])
        os.symlink('/etc/passwd', self.active / 'caddy/link')
        self.refuses('connect-pull', 'Symlink')

    def test_tls_renewal_during_copy_or_bad_pair_refuses_without_stop(self):
        real = host.db_snapshot
        def renew(source, target):
            (self.pair / (DOMAIN + '.crt')).write_bytes((self.pair / (DOMAIN + '.crt')).read_bytes() + b'\n')
            return real(source, target)
        with mock.patch.object(host, 'db_snapshot', side_effect=renew):
            self.refuses('connect-pull', 'changed', injected=True)
        (self.pair / (DOMAIN + '.key')).write_text('not a key')
        self.refuses('connect-pull', 'openssl')

    def test_unloadable_copied_pair_refuses(self):
        with mock.patch.object(connect_read.ssl.SSLContext, 'load_cert_chain', side_effect=OSError):
            self.refuses('connect-pull', 'unusable')

    def test_failed_standard_verification_streams_nothing(self):
        self.reject_archive = True
        self.refuses('connect-pull', 'rejected')

    def test_unknown_action(self):
        self.refuses('connect-backup', 'Unknown')


if __name__ == '__main__':
    unittest.main()
