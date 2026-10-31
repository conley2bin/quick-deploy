"""Real native THREE-entry fixture -> `setup.sh connect --ssh` into BLANK local state.

Run this single class serially, with an exclusive API62789 lease. Retained pinned
artifacts and QD_NATIVE_CDN_EVIDENCE are mandatory; no skipped native evidence.

Contract: .memory/tasks/2026-10-31-vps-cross-machine-management/CONNECT-CONTRACT.md.
The only replaced boundary is the SSH carrier (`vps.remote`, signature unchanged).
During the connect phase it is a strict READ-ONLY gate: `connect-info` ->
connect_read.connect_info(host, req); `connect-pull` -> connect_read.connect_pull(host,
req, stdout) (standard full archive, no FRONT stop); plus stock `outputs`/`status`/
`devices-list`. Any other action (stock `backup` stops FRONT, devices-*, abort,
adapter install) fails. host.py stays byte-identical. Native authority is the live
fixture; the stale pre-`test` archive only proves staleness and is never imported.
Protected authority = ROOT + UNITS (minus logs and live DB/WAL/SHM, whose counters
legitimately move); scratch outside ROOT/UNITS is allowed.
Raw credentials and native logs stay under the private evidence directory.
"""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import threading
import unittest
from unittest import mock
import uuid

import yaml

import test_native_three_lifecycle as three
import test_native_web_management as web
import connect_read  # lib/ is on sys.path via the fixtures above

host, vps, native, dual = three.host, three.vps, three.native, three.dual
ROLES = three.ROLES
CONNECT_ACTIONS = ('connect-info', 'connect-pull')
READ_ACTIONS = ('status', 'devices-list', 'outputs')
# Live traffic counters legitimately move while streams are held; every other
# native field, including createdAt/updatedAt/created_at/updated_at, must not.
COUNTERS = frozenset({'up', 'down', 'allTime', 'all_time', 'lastOnline', 'last_online', 'clientStats'})
ROSTER = ('company', 'home', 'phone', 'laptop', 'tablet', 'test')


def stable(value):
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items() if k not in COUNTERS}
    if isinstance(value, list):
        return [stable(v) for v in value]
    return value


def tree(path):
    """(relative path, kind, mode, content hash) of everything under a local state dir."""
    path = Path(path)
    if not path.exists():
        return None
    result = []
    for entry in sorted(path.rglob('*')):
        info = entry.lstat()
        kind = 'dir' if stat.S_ISDIR(info.st_mode) else 'link' if stat.S_ISLNK(info.st_mode) else 'file'
        digest = hashlib.sha256(entry.read_bytes()).hexdigest() if kind == 'file' else None
        result.append((str(entry.relative_to(path)), kind, stat.S_IMODE(info.st_mode), digest))
    return result


@unittest.skipUnless(os.environ.get('QD_VPS_ARTIFACTS') and os.environ.get('QD_NATIVE_CDN_EVIDENCE'), three.REQUIRED)
class NativeConnect(three.ThreeFixture, unittest.TestCase):
    hashes, assert_protected = web.NativeThreeBridge.hashes, web.NativeThreeBridge.assert_protected
    isolate_module, live_h2, epoch = (web.NativeThreeBridge.isolate_module, web.NativeThreeBridge.live_h2,
                                      web.NativeThreeBridge.epoch)

    @classmethod
    def setUpClass(cls):
        three.ThreeFixture.setUpClass.__func__(cls)

    def setUp(self):
        # Capture actual company authority before any fixture or MODULE redirect.
        self.real_module = vps.MODULE
        company_module = Path(os.environ.get('QD_NATIVE_REAL_MODULE', str(self.real_module)))
        real_state = Path.home() / '.local/state/quick-deploy-private/vps'
        self.protected = [company_module / 'subscription-urls.json', company_module / 'index.html', company_module / 'config',
                          real_state / 'state.json', real_state / 'outputs/subscription-urls.json']
        self.protected_hashes = self.hashes()
        self.addCleanup(self.assert_protected)
        self.sources = self.source_hashes()
        self.addCleanup(self.assert_sources_frozen)
        three.ThreeFixture.setUp(self)
        self.remote_actions, self.native_writes, self.phase_log = [], [], []
        self.phase = 'setup'  # setup -> read (connect gate) -> manage
        self.fail_after_commit, self.backup_count, self.gate = False, 0, False
        self.gate_entered, self.gate_release = threading.Event(), threading.Event()
        inner_ctl, inner_atomic = host.ctl, host.atomic
        def guarded_ctl(action, *units):
            self.assertNotEqual(self.phase, 'read', 'Read-only connect touched a service: ' + action)
            return inner_ctl(action, *units)
        def watched_atomic(path, *arguments, **keywords):
            target = Path(path).absolute()
            roots = (Path(host.ROOT).absolute(), Path(host.UNITS).absolute())
            if self.phase == 'read' and any(root == target or root in target.parents for root in roots):
                self.native_writes.append(str(target))
            return inner_atomic(path, *arguments, **keywords)
        self.enterContext(mock.patch.object(host, 'ctl', side_effect=guarded_ctl))
        self.enterContext(mock.patch.object(host, 'atomic', side_effect=watched_atomic))
        self.enterContext(mock.patch.object(vps, 'remote', side_effect=self.connect_remote))
        # The reader's coordination lock is a private fixture file, never the host's global one.
        self.enterContext(mock.patch.object(connect_read, 'LOCK_PATH', self.fx.fx.root / 'qd-3x-ui.lock'))
        # What sshd exports on a real host: client addr/port, server addr/port.
        self.enterContext(mock.patch.dict(os.environ, {
            'QD_SSH_CONNECTION': '203.0.113.9 40000 %s 22' % self.fx.manifest['endpoint']}))
        self.isolate_module()
        self.newroot = self.evidence / 'blank-operator-state' / 'connect'  # does not exist yet

    def source_hashes(self):
        lib = self.real_module / 'lib'
        names = [lib / n for n in ('host.py', 'vps.py', 'connect.py', 'connect_read.py')]
        return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in names}

    def assert_sources_frozen(self):
        self.assertEqual(self.source_hashes(), self.sources, 'Frozen source drifted during the run')

    # --- hooks bound to the (pending) connect contract -------------------------
    def connect_argv(self, state):
        return ['--state-dir', str(state), 'connect', '--ssh', 'root@' + self.fx.manifest['endpoint'],
                '--mihomo', vps.resolve_mihomo()]

    def connect_remote(self, record, action, request=None, stdin=None, stdout=None):
        self.phase_log.append((self.phase, action))
        if self.phase != 'read':
            return web.NativeThreeBridge.remote(self, record, action, request, stdin, stdout)
        if action in CONNECT_ACTIONS:
            import connect_read  # public reader; host.py gains no methods
            self.assertIsNone(stdin, 'Read-only connect action must not receive an uploaded payload')
            if action == 'connect-info':
                self.assertIsNone(stdout)
                return connect_read.connect_info(host, request or {})
            self.assertTrue(stdout is not None, 'connect-pull streams the archive to stdout')
            connect_read.connect_pull(host, request or {}, stdout)
            return None
        self.assertIn(action, READ_ACTIONS, 'Side-effecting carrier action during read-only connect: ' + action)
        return web.NativeThreeBridge.remote(self, record, action, request, stdin, stdout)

    # --- helpers ------------------------------------------------------------------
    def run_cli(self, *argv, name):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = vps.main(list(argv))
        host.atomic(self.evidence / (name + '.private.log'), json.dumps({'rc': code, 'out': out.getvalue(), 'err': err.getvalue()}))
        secrets_ = {v for r in self.fx.records().values() for v in (r['uuid'], r['auth'], r['subId'])}
        secrets_.add(self.fx.manifest['reality']['privateKey'])
        self.assertFalse(any(s in out.getvalue() + err.getvalue() for s in secrets_), name + ' leaked credentials (values withheld)')
        return code, out.getvalue(), err.getvalue()

    def native_snapshot(self):
        fx, manifest = self.fx, self.fx.manifest
        api = fx.api()
        records = fx.records()
        return {
            'records': stable(records),
            'rows': {t: stable(fx.rows(t)) for t in ('clients', 'client_inbounds', 'hosts', 'inbounds')},
            'inbounds': stable(api.call('/panel/api/inbounds/list')),
            'config': stable(api.call('/panel/api/server/getConfigJson')),
            'feeds': {(n, f): host.get_feed(manifest, n, f) for n in records for f in ('mihomo', 'sub')},
            'h2': self.live_h2(),
            'authority': self.authority_tree(),
            'host_module': host.digest(vps.MODULE / 'lib/host.py'),
        }

    def authority_tree(self):
        """Every file under ROOT and UNITS except logs and the live DB/WAL/SHM (traffic counters)."""
        found = {}
        for root in (Path(host.ROOT), Path(host.UNITS)):
            for path in sorted(root.rglob('*')):
                relative = path.relative_to(root)
                if 'logs' in relative.parts or path.name.startswith('x-ui.db') or path.suffix == '.log' \
                        or path.is_dir() or path.is_socket():
                    continue
                found[str(root.name) + '/' + str(relative)] = (
                    str(path.readlink()) if path.is_symlink() else hashlib.sha256(path.read_bytes()).hexdigest())
        return found

    def assert_native_unchanged(self, before, label):
        after = self.native_snapshot()
        changed = sorted(k for k in before if before[k] != after[k])
        self.assertFalse(changed, label + ': native state changed (values withheld): ' + ','.join(changed))
        self.assertFalse(self.native_writes, label + ': adapter wrote native files: ' + ','.join(self.native_writes))
        self.assertEqual([a for p, a in self.phase_log if p == 'read' and a not in READ_ACTIONS
                          and a not in CONNECT_ACTIONS], [])

    def processes(self):
        panel, front = self.fx.fx.proc, self.front
        self.assertIsNone(panel.poll())
        self.assertIsNone(front.poll())
        return {'panel': panel.pid, 'core': self.epoch(), 'front': front.pid}

    def hold(self, profile, origin):
        with self.reality_coordinate():
            held = {'reality': self.held_stream(profile, 'reality', origin)}
        held.update({role: self.held_stream(profile, role, origin) for role in ('cdn', 'h2')})
        return held

    @contextlib.contextmanager
    def streams_alive_throughout(self, held):
        """Keep each held authenticated stream exercising the native tunnel while the body runs."""
        stop, errors, rounds = threading.Event(), [], {role: 0 for role in held}
        def pump(role, stream):
            try:
                while True:
                    self.assert_stream_alive(stream)
                    rounds[role] += 1
                    if stop.wait(0.15):
                        return
            except BaseException as exc:
                errors.append((role, repr(exc)))
        threads = [threading.Thread(target=pump, args=item, daemon=True) for item in held.items()]
        for t in threads:
            t.start()
        try:
            yield
        finally:
            stop.set()
            for t in threads:
                t.join(10)
        self.assertEqual(errors, [], 'Held stream broke during read-only connect')
        self.assertTrue(all(n >= 1 for n in rounds.values()), 'Held stream never verified: ' + str(rounds))
        self.save('held-throughout.json', {'rounds': rounds, 'streams_never_closed': True})
        for stream in held.values():
            self.assert_stream_alive(stream)

    def build_roster(self):
        """6 native clients. `test` is added AFTER the last archive an old PC could have saved."""
        fx = self.fx
        fx.device('add', 'laptop', 'tablet')
        self.topology()
        stale, _, stale_manifest = fx.archive()
        stale_copy = self.evidence / 'stale-old-pc-backup.private.tar'
        stale_copy.write_bytes(stale.read_bytes())
        stale_copy.chmod(0o600)
        fx.device('add', 'test')
        self.topology()
        for name in ('test', 'phone'):
            self.seed_three_policy(name)
        host.backend_health(fx.folder, fx.manifest)
        self.assertEqual(sorted(fx.records()), sorted(ROSTER))
        self.assertNotIn('test', stale_manifest['accounts'])
        return stale_manifest

    def assert_connected(self, stale_manifest):
        """Local state is learned entirely from the live native host."""
        fx, state = self.fx, self.newroot
        manifest = host.read_json(fx.folder / 'manifest.json')
        self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o700)
        document = vps.load(state / 'state.json')
        self.assertEqual((document['format'], document['owner'], document['config']),
                         (host.FORMAT, manifest['owner'], manifest['config']))
        current = document['current']
        self.assertEqual((current['endpoint'], current['machine_id'], current['port']),
                         (manifest['endpoint'], host.machine_id(), 22))
        self.assertEqual(document['previous'], [])
        if 'node_id' in current:  # new connect may record it; legacy records carry none
            self.assertEqual(current['node_id'], manifest['node_id'])
        self.assertFalse((state / 'pending.json').exists())
        backup = Path(document['backup'])
        learned = vps.checked_backup(document, current, backup)
        self.assertEqual(sorted(learned['accounts']), sorted(ROSTER))
        self.assertIn('test', learned['accounts'], 'Server-only account missing from learned roster')
        self.assertNotIn('test', stale_manifest['accounts'])
        self.assertEqual({n: a['enable'] for n, a in learned['accounts'].items()},
                         {n: r['enable'] for n, r in fx.records().items()})
        self.assertEqual(host.transports(learned), ['tcp-reality', 'tcp-xhttp-cdn', 'hysteria2'])
        self.assertEqual(document['tcp_cdn']['domain'], manifest['tcp_cdn']['domain'])
        digest = host.digest(backup)
        self.assertEqual(document.get('native_generation'), digest)
        # Profiles exist only for ENABLED accounts (vps.profile_names); the fixture
        # seeds `home` disabled. Derive from live native authority, never a count.
        live = fx.records()
        enabled = sorted(n for n, r in live.items() if r['enable'])
        self.assertEqual(sorted(n for n, r in live.items() if not r['enable']), ['home'])
        self.assertEqual(enabled, sorted(set(ROSTER) - {'home'}))
        files = vps.read_generation(state, learned, digest)
        self.assertEqual(set(files), {n + e for n in enabled for e in ('.yaml', '.txt')})
        self.assertFalse(any(n.startswith('home.') for n in files), 'Disabled account got a profile')
        for name in enabled:
            self.assertEqual(files[name + '.yaml'], host.get_feed(manifest, name, 'mihomo'))
            self.assertEqual(files[name + '.txt'], host.get_feed(manifest, name, 'sub'))
            self.assertEqual(set(three.native_nodes(yaml.safe_load(files[name + '.yaml']))), set(ROLES))
        code, out, _ = self.run_cli('--state-dir', str(state), 'devices', 'list', name='devices-list')
        self.assertEqual(code, 0)
        self.assertEqual(sorted(out.split('\n')[:-1]), sorted(n + (' enabled' if n in enabled else ' disabled') for n in ROSTER))
        code, out, _ = self.run_cli('--state-dir', str(state), 'export', name='export')
        self.assertEqual(code, 0)
        exported = json.loads((vps.MODULE / 'subscription-urls.json').read_text())
        self.assertEqual(sorted(exported), enabled)
        records = fx.records()
        for name, urls in exported.items():
            self.assertEqual(sorted(urls), ['mihomo', 'shadowrocket'])
            self.assertTrue(urls['mihomo'].endswith('/mihomo/' + records[name]['subId']))
            self.assertTrue(urls['shadowrocket'].endswith('/sub/' + records[name]['subId']))
        return document

    # --- the case -----------------------------------------------------------------
    def test_connect_blank_state_from_live_native_is_read_only_and_functional(self):
        fx = self.fx
        stale = self.build_roster()
        profile, _ = self.exports('test')
        origin = self.origin()
        origin.RequestHandlerClass.protocol_version = 'HTTP/1.1'
        self.all_three(profile, origin)
        mihomo = vps.resolve_mihomo()
        self.assertFalse(self.newroot.parent.exists(), 'Blank-state precondition: no local root')
        before = self.native_snapshot()
        held = self.hold(profile, origin)
        procs = self.processes()
        self.save('connect-before.json', {'roster': sorted(before['records']), 'processes': procs,
            'host_adapter_sha256': host.digest(host.ROOT / 'host.py')})
        self.native_writes.clear()
        self.phase = 'read'

        # Phase 1: first connect, with every old credential and stream live throughout.
        with self.streams_alive_throughout(held):
            code, _, err = self.run_cli(*self.connect_argv(self.newroot), name='connect')
        self.assertEqual(code, 0, 'connect failed: ' + err[:300])
        self.assertEqual(self.processes(), procs, 'connect restarted panel/core/front')
        self.assertTrue(any(a == 'connect-info' for _, a in self.phase_log) and
                        any(a == 'connect-pull' for _, a in self.phase_log), 'connect did not use the approved actions')
        self.assertNotIn('backup', [a for p, a in self.phase_log if p == 'read'], 'stock backup stops FRONT')
        self.assert_native_unchanged(before, 'connect')
        document = self.assert_connected(stale)
        self.assert_native_unchanged(before, 'devices list/export')
        self.all_three(profile, origin)  # old credentials still valid on fresh sessions
        for stream in held.values():
            self.assert_stream_alive(stream)

        # Phase 1b: reconnecting the SAME owner is a safe refresh.
        with self.streams_alive_throughout(held):
            code, _, err = self.run_cli(*self.connect_argv(self.newroot), name='reconnect')
        self.assertEqual(code, 0, 'same-owner reconnect failed: ' + err[:300])
        again = vps.load(self.newroot / 'state.json')
        self.assertEqual((again['owner'], again['config'], again['current']),
                         (document['owner'], document['config'], document['current']))
        self.assertEqual(self.processes(), procs)
        self.assert_native_unchanged(before, 'reconnect')

        # Phase 1c: refusals write nothing locally and nothing natively.
        self.refusals(document)
        self.assert_native_unchanged(before, 'refusals')
        for stream in held.values():
            self.assert_stream_alive(stream)
        self.assertEqual(self.processes(), procs)
        for stream in held.values():
            stream.close()

        # Phase 2: ordinary management on the connected record (restarts are expected here).
        self.phase = 'manage'
        records, earliest = fx.records(), self.earliest(fx.records())
        joins = fx.rows('client_inbounds')
        code, _, err = self.run_cli('--state-dir', str(self.newroot), 'devices', 'rename', 'test', 'probe',
                                    '--mihomo', mihomo, name='rename-after-connect')
        self.assertEqual(code, 0, 'post-connect rename failed: ' + err[:300])
        after = fx.records()
        self.assertEqual(sorted(after), sorted(set(ROSTER) - {'test'} | {'probe'}))
        self.record_contract(records['test'], after['probe'], earliest['test'], ('email', 'updatedAt'))
        self.assertEqual(after['probe']['id'], records['test']['id'])
        for name in set(ROSTER) - {'test'}:
            self.unchanged(records[name], after[name], ('updatedAt',))
        self.assertEqual(fx.rows('client_inbounds'), joins, 'Rename changed join metadata')
        self.topology()
        renamed, _ = self.exports('probe')
        self.assertTrue(three.native_nodes(profile) == three.native_nodes(renamed),
                        'Rename changed credential maps (values withheld)')
        self.all_three(profile, origin)
        renamed_state = vps.load(self.newroot / 'state.json')
        self.assertNotEqual(renamed_state['native_generation'], document['native_generation'])
        self.assertIn('probe', vps.checked_backup(renamed_state, renamed_state['current'],
                                                  Path(renamed_state['backup']))['accounts'])
        self.assertEqual(renamed_state['owner'], document['owner'])
        self.http2_evidence()
        self.assert_protected()
        self.save('RESULT.json', {'blank_state_connect_from_live_native': True, 'roster': list(ROSTER),
            'server_only_account_seen': True, 'native_unchanged_incl_timestamps': True,
            'panel_core_front_pids_unchanged': True, 'held_streams_alive_throughout': True,
            'old_credentials_valid': True, 'same_owner_reconnect': True,
            'refusals_zero_writes': True, 'post_connect_rename_functional': True,
            'company_authority_unchanged': True, 'host_sha256': host.digest(vps.MODULE / 'lib/host.py'),
            'counters_excluded': sorted(COUNTERS)})

    def refusals(self, document):
        connected = tree(self.newroot)
        base = self.evidence / 'refusal-states'
        base.mkdir(mode=0o700)
        # Foreign occupied local state: a different owner already lives here.
        foreign = base / 'foreign'
        shutil.copytree(self.newroot, foreign)
        occupied = dict(document, owner=str(uuid.uuid4()))
        host.atomic(foreign / 'state.json', host.dumps(occupied))
        pending = base / 'pending'
        shutil.copytree(self.newroot, pending)
        host.atomic(pending / 'pending.json', host.dumps({'record': document['current'], 'mode': 'backup', 'mihomo': None}))
        for label, state in (('foreign-owner', foreign), ('pending-local', pending)):
            digest, calls = tree(state), len(self.phase_log)
            code, _, _ = self.run_cli(*self.connect_argv(state), name='refuse-' + label)
            self.assertNotEqual(code, 0, label + ' was accepted')
            self.assertEqual(tree(state), digest, label + ' changed local state')
            self.assertTrue(all(p == 'read' for p, _ in self.phase_log[calls:]))
        # Pending REMOTE transaction: nothing may be learned or written locally.
        blank = base / 'pending-remote'
        marker = host.ROOT / 'pending.json'
        marker.write_text(host.dumps({'phase': 'prepared'}))  # fixture fault injection, not an adapter write
        try:
            code, _, _ = self.run_cli(*self.connect_argv(blank), name='refuse-pending-remote')
        finally:
            marker.unlink()
        self.assertNotEqual(code, 0, 'pending remote transaction was accepted')
        self.assertEqual([e for e in (tree(blank) or []) if e[1] != 'dir' and e[0] != '.lock'], [],
                         'Refused pending-remote connect left local files')
        self.assertEqual(tree(self.newroot), connected, 'Refusals changed the connected state')


if __name__ == '__main__':
    unittest.main()
