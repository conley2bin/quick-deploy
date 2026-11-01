"""Real native THREE-entry fixture -> bare `setup.sh` (default ensure) on a healthy node.

Run this single class serially, with an exclusive API62789 lease, ONLY on the
integrated frozen source. Retained pinned artifacts and QD_NATIVE_CDN_EVIDENCE are
mandatory; no skipped native evidence.

Contract: .memory/tasks/2026-10-31-vps-simple-setup/{OPUS-PLAN,IMPLEMENTATION-CONTRACT}.md.
Public behavior under test: `vps.main(['--deployment-only', <bare flags>])` on a private
FULL-history clone whose `config` carries the fixture's public fake IP/DOMAIN/REALITY_TARGET.
The only replaced boundary is the SSH carrier (`vps.remote`, signature unchanged). During
ensure it accepts exactly: `ensure-probe` -> connect_read.ensure_probe(host, request);
`connect-info`/`connect-pull` -> the read-only reader; `outputs` -> the stock native
backend. Anything else (backup stops FRONT, prepare/commit/abort/devices-*, adapter
install, status) fails, as do host.ctl and every host mutation entry point.

Sequence on the live native fixture (5 accounts, `home` disabled):
  E1 fresh newcomer (no local state, no old backup): server healthy -> attach cache only.
  E2 repeat with current cache: ONLY `ensure-probe` crosses the carrier; local tree unchanged.
  late server-only `test` account (fixture mutation, restarts expected) -> 6 accounts.
  E3 stale cache: probe sees a different roster digest -> read-only refresh learns `test`.
  E4 repeat again: ONLY `ensure-probe`.
Throughout each ensure: ROOT/UNITS (minus logs/live DB counters) and every native account,
policy, timestamp, REALITY key, H2 pair and host projection are exact; panel/core/front PIDs
are unchanged and the three held authenticated streams keep passing traffic.
The probe itself runs backend_health (reads feeds), so the cache-refresh signal is the remote
`connect-pull`, never "no feed was read".
Health tls/process/unit stages run on host functions the existing native fixtures already
neutralise (public-CA/systemd boundaries); ownership/release/artifacts/binary/native are real.
New runtime symbols (ensure-probe, vps.local_roster_digest) are touched lazily inside the case.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time
import unittest
from unittest import mock

import yaml

import test_native_connect as nc
import test_native_three_lifecycle as three
import test_native_web_management as web
import connect_read  # lib/ is on sys.path via the fixtures above

host, vps = three.host, three.vps
CONNECT_ACTIONS = ('connect-info', 'connect-pull')
ENSURE_ACTIONS = ('ensure-probe', 'outputs') + CONNECT_ACTIONS
HOST_MUTATORS = ('prepare', 'commit', 'abort', 'export_backup', 'install_backup_adapter',
                 'devices_prepare', 'devices_commit', 'preflight')
VPS_MUTATORS = ('deploy', 'deploy_existing', 'backup_remote', 'recover_local')
INITIAL = ('company', 'home', 'laptop', 'phone', 'tablet')
LATE = 'test'
BOUND_SECONDS = 120  # one ensure: probe (xray -test, feeds) plus at most a read-only pull


def lock_free(tree):
    """Local state tree without the nonblocking coordination lock files."""
    return [e for e in (tree or []) if not e[0].endswith('.lock')]


@unittest.skipUnless(os.environ.get('QD_VPS_ARTIFACTS') and os.environ.get('QD_NATIVE_CDN_EVIDENCE'), three.REQUIRED)
class NativeSetupEnsure(three.ThreeFixture, unittest.TestCase):
    hashes, assert_protected = nc.NativeConnect.hashes, nc.NativeConnect.assert_protected
    live_h2 = web.NativeThreeBridge.live_h2
    run_cli, native_snapshot, authority_tree = (nc.NativeConnect.run_cli, nc.NativeConnect.native_snapshot,
                                                nc.NativeConnect.authority_tree)
    hold, streams_alive_throughout = nc.NativeConnect.hold, nc.NativeConnect.streams_alive_throughout

    @classmethod
    def setUpClass(cls):
        three.ThreeFixture.setUpClass.__func__(cls)

    def setUp(self):
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
        self.phase, self.phase_log, self.probes, self.probe_requests = 'setup', [], [], []
        self.native_writes, self.backup_count, self.remote_actions = [], 0, []
        self.fail_after_commit, self.gate = False, False
        inner_ctl, inner_atomic = host.ctl, host.atomic
        def guarded_ctl(action, *units):
            self.assertNotEqual(self.phase, 'ensure', 'ensure touched a service: ' + action)
            return inner_ctl(action, *units)
        def watched_atomic(path, *arguments, **keywords):
            target = Path(path).absolute()
            roots = (Path(host.ROOT).absolute(), Path(host.UNITS).absolute())
            if self.phase == 'ensure' and any(root == target or root in target.parents for root in roots):
                self.native_writes.append(str(target))
            return inner_atomic(path, *arguments, **keywords)
        self.enterContext(mock.patch.object(host, 'ctl', side_effect=guarded_ctl))
        self.enterContext(mock.patch.object(host, 'atomic', side_effect=watched_atomic))
        for owner, names in ((host, HOST_MUTATORS), (vps, VPS_MUTATORS)):
            for name in names:
                self.forbid_during_ensure(owner, name)
        self.enterContext(mock.patch.object(vps, 'remote', side_effect=self.ensure_remote))
        self.enterContext(mock.patch.object(connect_read, 'LOCK_PATH', self.fx.fx.root / 'qd-3x-ui.lock'))
        self.enterContext(mock.patch.dict(os.environ, {
            'QD_SSH_CONNECTION': '203.0.113.9 40000 %s 22' % self.fx.manifest['endpoint']}))
        self.bridge = web.NativeThreeBridge
        self.isolate_clone()
        self.state = self.evidence / 'blank-operator-state' / 'ensure'  # does not exist yet

    # --- isolation: a fresh copy of the ENTIRE Git history, frozen sources, 0700 dirs --------------
    def isolate_clone(self):
        top = Path(subprocess.run(['git', '-C', str(self.real_module), 'rev-parse', '--show-toplevel'],
                                  capture_output=True, timeout=10, check=True).stdout.decode().strip())
        root = self.evidence / 'full-worktree'
        cloned = subprocess.run(['git', 'clone', '-q', '--no-hardlinks', str(top), str(root)],
                                capture_output=True, timeout=120)
        self.assertEqual(cloned.returncode, 0, 'Private full-history clone failed: ' + cloned.stderr.decode()[:200])
        subprocess.run(['git', '-C', str(root), 'remote', 'remove', 'origin'], capture_output=True, timeout=10, check=True)
        for path in [root, *root.rglob('*')]:
            if path.is_dir() and not path.is_symlink():
                path.chmod(0o700)
        frozen = {n: hashlib.sha256((self.real_module / n).read_bytes()).hexdigest() for n in self.source_names()}
        cloned_hashes = {n: hashlib.sha256((root / 'vps' / n).read_bytes()).hexdigest() for n in self.source_names()}
        self.assertEqual(cloned_hashes, frozen, 'Frozen source is not committed: clone differs from the integrated tree')
        self.enterContext(mock.patch.object(vps, 'MODULE', root / 'vps'))
        manifest = self.fx.manifest
        domain = manifest['config']['domain']
        self.assertTrue(domain.startswith('sub.'), 'Fixture subscription host must be sub.<DOMAIN>')
        self.config_text = 'VPS_SSH=root@%s\nDOMAIN=%s\nREALITY_TARGET=%s\n' % (
            manifest['endpoint'], domain[len('sub.'):], manifest['config']['reality_target'])
        (vps.MODULE / 'config').write_text(self.config_text)
        self.config_hash = hashlib.sha256(self.config_text.encode()).hexdigest()
        self.save('isolation.json', {'real_authority_hashes_before': self.protected_hashes,
            'host_sha256': host.digest(vps.MODULE / 'lib/host.py'), 'private_git_root': str(root),
            'config': 'fixture public fake IP/DOMAIN/REALITY_TARGET; company config untouched'})

    def source_names(self):
        return ['lib/' + n for n in ('host.py', 'vps.py', 'connect.py', 'connect_read.py', 'target_config.py')] + ['setup.sh']

    def source_hashes(self):
        return {n: hashlib.sha256((self.real_module / n).read_bytes()).hexdigest() for n in self.source_names()}

    def assert_sources_frozen(self):
        self.assertEqual(self.source_hashes(), self.sources, 'Frozen source drifted during the run')

    def forbid_during_ensure(self, owner, name):
        inner = getattr(owner, name)
        def guarded(*args, **kwargs):
            self.assertNotEqual(self.phase, 'ensure', 'ensure called mutating entry point ' + owner.__name__ + '.' + name)
            return inner(*args, **kwargs)
        self.enterContext(mock.patch.object(owner, name, side_effect=guarded))

    # --- the carrier ---------------------------------------------------------------------------
    def ensure_remote(self, record, action, request=None, stdin=None, stdout=None):
        self.phase_log.append((self.phase, action))
        if self.phase != 'ensure':
            return self.bridge.remote(self, record, action, request, stdin, stdout)
        self.assertIn(action, ENSURE_ACTIONS, 'Side-effecting/unapproved carrier action during ensure: ' + action)
        if action == 'ensure-probe':
            self.assertIsNone(stdin, 'Probe must not upload a payload')
            self.assertIsNone(stdout)
            self.assertNotIn('adapter_b64', request or {}, 'Probe request carries adapter bytes')
            self.assertEqual((request or {}).get('endpoint'), self.fx.manifest['endpoint'])
            self.probe_requests.append(copy.deepcopy(request))
            result = connect_read.ensure_probe(host, request or {})  # public reader; host.py gains nothing
            self.probes.append(copy.deepcopy(result))
            return result
        if action in CONNECT_ACTIONS:
            self.assertIsNone(stdin, 'Read-only action must not receive an uploaded payload')
            if action == 'connect-info':
                self.assertIsNone(stdout)
                return connect_read.connect_info(host, request or {})
            self.assertTrue(stdout is not None, 'connect-pull streams the archive to stdout')
            connect_read.connect_pull(host, request or {}, stdout)
            return None
        return self.bridge.remote(self, record, action, request, stdin, stdout)  # 'outputs' only

    # --- harness helpers -----------------------------------------------------------------------
    def epoch(self):
        """Owned Xray child (pid, start ticks); a /proc entry may vanish mid-scan."""
        found = []
        for path in (Path('/proc') / str(self.fx.fx.proc.pid) / 'task').glob('*/children'):
            try:
                children = path.read_text().split()
            except (ProcessLookupError, FileNotFoundError):
                continue
            for pid in children:
                try:
                    if 'xray' in (Path('/proc') / pid / 'comm').read_text():
                        found.append((int(pid), (Path('/proc') / pid / 'stat').read_text().rsplit(')', 1)[1].split()[19]))
                except (ProcessLookupError, FileNotFoundError):
                    continue
        self.assertEqual(len(set(found)), 1, 'Exactly one owned native Xray child required')
        return found[0]

    def processes(self):
        panel, front = self.fx.fx.proc, self.front
        self.assertIsNone(panel.poll())
        self.assertIsNone(front.poll())
        return {'panel': panel.pid, 'core': self.epoch(), 'front': front.pid}

    def argv(self, *extra):
        # What setup.sh execs for a bare invocation: the hidden front flag plus ordinary flags only.
        return ['--deployment-only', '--state-dir', str(self.state), '--mihomo', vps.resolve_mihomo(), *extra]

    def ensure(self, label):
        """One bare ensure; returns (rc, out, err, carrier actions, probes seen, seconds)."""
        mark, probes, self.native_writes = len(self.phase_log), len(self.probes), []
        started = time.monotonic()
        code, out, err = self.run_cli(*self.argv(), name=label)
        seconds = time.monotonic() - started
        actions = [a for p, a in self.phase_log[mark:] if p == 'ensure']
        self.assertTrue(all(p == 'ensure' for p, _ in self.phase_log[mark:]), 'ensure leaked outside its phase')
        self.assertLess(seconds, BOUND_SECONDS, label + ' exceeded the latency bound')
        self.assertTrue(out.strip(), label + ' printed nothing')
        self.save(label + '.timing.json', {'seconds': round(seconds, 2), 'actions': actions, 'rc': code})
        return code, out, err, actions, self.probes[probes:], seconds

    def assert_native_unchanged(self, before, label):
        after = self.native_snapshot()
        changed = sorted(k for k in before if before[k] != after[k])
        self.assertFalse(changed, label + ': native state changed (values withheld): ' + ','.join(changed))
        self.assertFalse(self.native_writes, label + ': adapter wrote native files: ' + ','.join(self.native_writes))

    def secrets(self):
        values = {v for r in self.fx.records().values() for v in (r['uuid'], r['auth'], r['subId'])}
        values.add(self.fx.manifest['reality']['privateKey'])
        return values

    def assert_healthy_probe(self, probe, label):
        manifest = host.read_json(self.fx.folder / 'manifest.json')
        wire = json.dumps(probe, sort_keys=True)
        self.assertFalse(any(s in wire for s in self.secrets()), label + ': probe leaked credentials (values withheld)')
        self.assertEqual(probe['kind'], 'owned', label)
        self.assertEqual((probe['owner'], probe['machine_id'], probe['node_id']),
                         (manifest['owner'], host.machine_id(), manifest['node_id']))
        self.assertEqual(probe['transports'], ['tcp-reality', 'tcp-xhttp-cdn', 'hysteria2'])
        self.assertEqual(probe['tcp_cdn']['domain'], manifest['tcp_cdn']['domain'])
        self.assertTrue(probe['lock_match'])
        self.assertEqual(probe['adapter_sha256'], host.digest(host.ROOT / 'host.py'))
        self.assertTrue(probe['health']['ok'], label + ' health: %r' % (probe['health'],))
        self.assertFalse(probe['health']['failed_check'] or probe['health']['error'])
        self.assertIs(probe['front_drift'], False)
        self.assertRegex(probe['roster_digest'], r'^[0-9a-f]{64}$')

    def assert_local(self, expected, label):
        """Local cache learned entirely from the live native host; profiles only for ENABLED accounts."""
        fx, state = self.fx, self.state
        manifest = host.read_json(fx.folder / 'manifest.json')
        self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o700)
        document = vps.load(state / 'state.json')
        self.assertEqual((document['format'], document['owner'], document['config']),
                         (host.FORMAT, manifest['owner'], manifest['config']), label)
        current = document['current']
        self.assertEqual((current['endpoint'], current['machine_id'], current['port']),
                         (manifest['endpoint'], host.machine_id(), 22))
        if 'node_id' in current:
            self.assertEqual(current['node_id'], manifest['node_id'])
        self.assertEqual(document['previous'], [])
        self.assertFalse((state / 'pending.json').exists())
        backup = Path(document['backup'])
        learned = vps.checked_backup(document, current, backup)
        self.assertEqual(sorted(learned['accounts']), sorted(expected), label)
        live = fx.records()
        self.assertEqual({n: a['enable'] for n, a in learned['accounts'].items()}, {n: r['enable'] for n, r in live.items()})
        self.assertEqual(host.transports(learned), ['tcp-reality', 'tcp-xhttp-cdn', 'hysteria2'])
        digest = host.digest(backup)
        self.assertEqual(document.get('native_generation'), digest)
        enabled = sorted(n for n, r in live.items() if r['enable'])
        self.assertEqual(sorted(n for n, r in live.items() if not r['enable']), ['home'])
        files = vps.read_generation(state, learned, digest)
        self.assertEqual(set(files), {n + e for n in enabled for e in ('.yaml', '.txt')})
        self.assertFalse(any(n.startswith('home.') for n in files), 'Disabled account got a profile')
        for name in enabled:
            self.assertEqual(files[name + '.yaml'], host.get_feed(manifest, name, 'mihomo'))
            self.assertEqual(files[name + '.txt'], host.get_feed(manifest, name, 'sub'))
            self.assertEqual(set(three.native_nodes(yaml.safe_load(files[name + '.yaml']))), set(three.ROLES))
        return document, learned

    def assert_probe_matches_local(self, probe, learned):
        # ONE shared digest helper on both sides, never a guessed native DB row count.
        self.assertEqual(probe['roster_digest'], vps.local_roster_digest(learned))
        self.assertEqual(probe['roster_digest'], connect_read.roster_digest(learned['accounts']))

    def build_initial(self):
        fx = self.fx
        fx.device('add', 'laptop', 'tablet')
        self.topology()
        self.seed_three_policy('phone')
        host.backend_health(fx.folder, fx.manifest)
        self.assertEqual(sorted(fx.records()), sorted(INITIAL))

    def late_server_only_add(self):
        fx = self.fx
        fx.device('add', LATE)
        self.topology()
        self.seed_three_policy(LATE)
        host.backend_health(fx.folder, fx.manifest)
        self.assertEqual(sorted(fx.records()), sorted(INITIAL + (LATE,)))

    # --- the case ------------------------------------------------------------------------------
    def test_bare_ensure_healthy_native_repeat_and_stale_cache_refresh(self):
        fx = self.fx
        self.build_initial()
        profile, _ = self.exports('company')
        origin = self.origin()
        origin.RequestHandlerClass.protocol_version = 'HTTP/1.1'
        self.all_three(profile, origin)
        self.assertFalse(self.state.parent.exists(), 'Blank-state precondition: no local root, no old backup')
        before = self.native_snapshot()
        records_before = fx.records()
        reality_before = copy.deepcopy(fx.manifest['reality'])
        held = self.hold(profile, origin)
        procs = self.processes()
        self.save('ensure-before.json', {'roster': sorted(before['records']), 'processes': procs,
            'host_adapter_sha256': host.digest(host.ROOT / 'host.py')})
        self.phase = 'ensure'

        # E1: newcomer. Probe first, then ONLY the read-only attach; nothing is deployed.
        with self.streams_alive_throughout(held):
            code, _, err, actions, probes, _ = self.ensure('ensure-1-blank')
        (self.evidence / 'held-throughout.json').replace(self.evidence / 'held-throughout-E1-E2-pre.json')
        self.assertEqual(code, 0, 'fresh-root ensure failed: ' + err[:300])
        self.assertEqual(actions[0], 'ensure-probe')
        self.assertIn('connect-info', actions)
        self.assertIn('connect-pull', actions)
        self.assertEqual(self.processes(), procs, 'ensure restarted panel/core/front')
        self.assert_native_unchanged(before, 'ensure-1')
        self.assertTrue(probes, 'no probe observed')
        for probe in probes:
            self.assert_healthy_probe(probe, 'E1')
        document, learned = self.assert_local(INITIAL, 'E1')
        self.assert_probe_matches_local(probes[-1], learned)
        first_digest = probes[-1]['roster_digest']
        self.assertEqual(self.probe_requests[0].get('lock'), vps.release_lock())

        # E2: current cache. The ONLY remote action is the probe; no new backup/state path.
        local_before = lock_free(nc.tree(self.state))
        backup_digest = host.digest(Path(document['backup']))
        with self.streams_alive_throughout(held):
            code, _, err, actions, probes, _ = self.ensure('ensure-2-current')
        (self.evidence / 'held-throughout.json').replace(self.evidence / 'held-throughout-E2.json')
        self.assertEqual(code, 0, 'current-cache ensure failed: ' + err[:300])
        self.assertTrue(actions and set(actions) == {'ensure-probe'}, 'Current repeat used extra remote actions: %r' % actions)
        self.assertEqual(lock_free(nc.tree(self.state)), local_before, 'Current repeat changed local state/backups')
        self.assertEqual(host.digest(Path(vps.load(self.state / 'state.json')['backup'])), backup_digest)
        self.assertEqual(vps.load(self.state / 'state.json'), document)
        self.assertEqual(self.processes(), procs)
        self.assert_native_unchanged(before, 'ensure-2')
        self.assertEqual({p['roster_digest'] for p in probes}, {first_digest}, 'Roster digest moved with no roster change')
        self.assert_healthy_probe(probes[-1], 'E2')
        for stream in held.values():
            self.assert_stream_alive(stream)
        for stream in held.values():
            stream.close()
        stale_digest = vps.local_roster_digest(learned)

        # Server-only change (restarts expected): the cache is now stale relative to live native.
        self.phase = 'setup'
        self.late_server_only_add()
        after_add = fx.records()
        for name in INITIAL:
            self.unchanged(records_before[name], after_add[name], ('updatedAt',))
        self.assertEqual(fx.manifest['reality'], reality_before, 'REALITY identity changed by the add')
        self.all_three(profile, origin)
        before = self.native_snapshot()
        held = self.hold(profile, origin)
        procs = self.processes()
        self.phase = 'ensure'

        # E3: stale cache. Probe sees a different digest; read-only refresh learns `test`.
        with self.streams_alive_throughout(held):
            code, _, err, actions, probes, _ = self.ensure('ensure-3-stale')
        (self.evidence / 'held-throughout.json').replace(self.evidence / 'held-throughout-E3.json')
        self.assertEqual(code, 0, 'stale-cache ensure failed: ' + err[:300])
        self.assertEqual(actions[0], 'ensure-probe')
        self.assertIn('connect-pull', actions, 'stale cache was not refreshed through the read-only pull')
        self.assertEqual(self.processes(), procs, 'refresh restarted panel/core/front')
        self.assert_native_unchanged(before, 'ensure-3')
        for probe in probes:
            self.assert_healthy_probe(probe, 'E3')
        self.assertNotEqual(probes[0]['roster_digest'], stale_digest, 'probe did not see the server-only account')
        refreshed, learned = self.assert_local(INITIAL + (LATE,), 'E3')
        self.assert_probe_matches_local(probes[-1], learned)
        self.assertEqual((refreshed['owner'], refreshed['config'], refreshed['current']),
                         (document['owner'], document['config'], document['current']))
        self.assertNotEqual(refreshed['native_generation'], document['native_generation'])
        self.phase = 'setup'  # stock devices-list carrier action is not an ensure action
        code, out, _ = self.run_cli('--state-dir', str(self.state), 'devices', 'list', name='devices-list')
        self.assertEqual(code, 0)
        self.assertEqual(sorted(out.split('\n')[:-1]), sorted(
            n + (' disabled' if n == 'home' else ' enabled') for n in INITIAL + (LATE,)))
        self.assert_native_unchanged(before, 'devices list')
        self.phase = 'ensure'

        # E4: cache current again -> probe only.
        local_before = lock_free(nc.tree(self.state))
        with self.streams_alive_throughout(held):
            code, _, err, actions, _, _ = self.ensure('ensure-4-current')
        (self.evidence / 'held-throughout.json').replace(self.evidence / 'held-throughout-E4.json')
        self.assertEqual(code, 0, err[:300])
        self.assertTrue(actions and set(actions) == {'ensure-probe'}, 'Current repeat used extra remote actions: %r' % actions)
        self.assertEqual(lock_free(nc.tree(self.state)), local_before)
        self.assertEqual(self.processes(), procs)
        self.assert_native_unchanged(before, 'ensure-4')

        # Whole run: old credentials still valid, every held stream alive, config/company untouched.
        self.all_three(profile, origin)
        for stream in held.values():
            self.assert_stream_alive(stream)
            stream.close()
        self.assertEqual(hashlib.sha256((vps.MODULE / 'config').read_bytes()).hexdigest(), self.config_hash)
        self.assertEqual([a for p, a in self.phase_log if p == 'ensure' and a not in ENSURE_ACTIONS], [])
        self.assert_protected()
        self.save('RESULT.json', {'fresh_root_ensure_attach_only': True, 'repeat_probe_only': True,
            'stale_cache_read_only_refresh': True, 'roster': list(INITIAL + (LATE,)), 'home_disabled_no_profile': True,
            'native_unchanged_incl_timestamps': True, 'panel_core_front_pids_unchanged': True,
            'held_streams_alive_throughout': True, 'probe_leaks_no_credentials': True,
            'company_authority_unchanged': True, 'host_sha256': host.digest(vps.MODULE / 'lib/host.py'),
            'counters_excluded': sorted(nc.COUNTERS)})


if __name__ == '__main__':
    unittest.main()
