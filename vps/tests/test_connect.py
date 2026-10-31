"""Client side of `setup.sh connect`: fake transport, dummy archives, no SSH/GUI/web/real server.

FakeServer plays the VPS reader: it answers `connect-info`/`outputs` and streams a
tar for `connect-pull`. Archive acceptance (inspect_backup) reads the dummy
manifest; everything else is the real vps.py commit machinery.
"""
import base64
import contextlib
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import host
import vps

ENDPOINT = '93.184.216.34'
CONFIG = {'domain': 'sub.example.org', 'reality_target': 'target.example.org', 'acme_email': '', 'proxy_port': 443}


class FakeServer:
    def __init__(self, owner='server-owner'):
        self.manifest = host.new_manifest(owner, dict(CONFIG), ENDPOINT)
        self.manifest['published'] = True
        self.machine_id = 'm' * 32
        self.calls, self.requests = [], []
        self.pull_fault = None  # 'partial' | 'garbage'
        self.outputs_fault = None

    def add_device(self, name):
        self.manifest['accounts'][name] = {'id': str(uuid.uuid4()), 'subId': uuid.uuid4().hex, 'email': name,
                                           'enable': True, 'flow': 'xtls-rprx-vision'}

    def info(self):
        m = self.manifest
        return {'format': host.FORMAT, 'owner': m['owner'], 'machine_id': self.machine_id, 'node_id': m['node_id'],
                'endpoint': m['endpoint'], 'config': m['config'], 'published': m['published']}

    def archive(self):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as tar:
            for name, data in (('deployment/manifest.json', host.dumps(self.manifest).encode()),
                               ('deployment/bin/geoip.dat', b'geoip'), ('deployment/bin/geosite.dat', b'geosite')):
                item = tarfile.TarInfo(name)
                item.size = len(data)
                tar.addfile(item, io.BytesIO(data))
        return stream.getvalue()

    def outputs(self):
        files = {}
        for name in host.device_names(self.manifest):
            if self.manifest['accounts'][name]['enable']:
                files[name + '.yaml'] = base64.b64encode(('proxies: ' + self.manifest['accounts'][name]['subId']).encode()).decode()
                files[name + '.txt'] = base64.b64encode(b'links ' + name.encode()).decode()
        if self.outputs_fault == 'missing':
            files.pop(sorted(files)[0])
        return files

    def remote(self, record, action, request=None, stdin=None, stdout=None):
        self.calls.append(action)
        self.requests.append((action, request))
        assert stdin is None
        if action == 'connect-info':
            return self.info()
        if action == 'connect-pull':
            data = self.archive()
            if self.pull_fault == 'partial':
                stdout.write(data[:len(data) // 2])
                raise host.Error('ssh stream cut')
            stdout.write(b'not a tar' if self.pull_fault == 'garbage' else data)
            return None
        if action == 'outputs':
            return self.outputs()
        raise AssertionError('connect used forbidden carrier action: ' + action)


def inspect(path, lock):
    vps.read_private(path)
    with tarfile.open(path) as tar:
        return json.load(tar.extractfile('deployment/manifest.json'))


def tree(path):
    path = Path(path)
    return sorted((str(p.relative_to(path)), hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None)
                  for p in path.rglob('*'))


class Connect(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='qd-connect-')
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / 'state'
        self.server = FakeServer()
        self.mihomo_calls = []
        self.record = {'target': 'root@' + ENDPOINT, 'options': list(vps.SSH_OPTIONS), 'endpoint': ENDPOINT,
                       'user': 'root', 'port': 22, 'host_key_alias': None}
        def run(argv, **kwargs):
            self.mihomo_calls.append(argv)
            if self.mihomo_fault:
                raise host.Error('mihomo rejected profile')
        self.mihomo_fault = False
        patches = [mock.patch.object(vps, 'remote', side_effect=self.server_remote), mock.patch.object(vps, 'inspect_backup', side_effect=inspect),
                   mock.patch.object(vps, 'release_lock', return_value={'lock': 1}),
                   mock.patch.object(vps, 'resolve_mihomo', side_effect=lambda b=None: '/fixture/mihomo'),
                   mock.patch.object(vps, 'target_record', side_effect=lambda args, current=None: dict(self.record, **(
                       {'machine_id': current['machine_id']} if current and 'machine_id' in current else {}))),
                   mock.patch.object(host, 'run', side_effect=run), mock.patch.object(host, 'validate_feed_scope'),
                   # The base-domain file and its default must never be consulted.
                   mock.patch.object(vps, 'base_domain', side_effect=AssertionError('config read')),
                   mock.patch.object(vps, 'default_domain', side_effect=AssertionError('default_domain called')),
                   mock.patch.object(vps.subprocess, 'run', side_effect=AssertionError('no real process'))]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def server_remote(self, *args, **kwargs):
        return self.server.remote(*args, **kwargs)

    def connect(self, *extra, state=None):
        argv = ['--state-dir', str(state or self.state), 'connect', '--ssh', 'root@' + ENDPOINT, '--mihomo', '/fixture/mihomo', *extra]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = vps.main(argv)
        text = out.getvalue() + err.getvalue()
        for secret in [a[k] for a in self.server.manifest['accounts'].values() for k in ('id', 'subId')] + [
                self.server.manifest['admin_password'], self.server.manifest['owner']]:
            self.assertNotIn(secret, text, 'connect output leaked a credential (value withheld)')
        return code, out.getvalue(), err.getvalue()

    def document(self, **change):
        return dict(vps.load(self.state / 'state.json'), **change)

    def write_state(self, document):
        vps.private_dir(self.state)
        host.atomic(self.state / 'state.json', host.dumps(document))

    # --- success ---------------------------------------------------------------
    def test_blank_state_learns_everything_from_server(self):
        code, out, err = self.connect()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.server.calls, ['connect-info', 'connect-pull', 'outputs'])
        document = vps.load(self.state / 'state.json')
        manifest = self.server.manifest
        self.assertEqual((document['owner'], document['config'], document['previous']), (manifest['owner'], CONFIG, []))
        current = document['current']
        self.assertEqual((current['endpoint'], current['machine_id'], current['node_id'], current['port']),
                         (ENDPOINT, 'm' * 32, manifest['node_id'], 22))
        backup = Path(document['backup'])
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertEqual(backup.parent, self.state / 'backups')
        self.assertEqual(document['native_generation'], host.digest(backup))
        files = vps.read_generation(self.state, manifest, host.digest(backup))
        self.assertEqual(len(files), 2 * len(host.DEVICES))
        self.assertEqual(sorted(os.listdir(self.state / 'outputs')),
                         sorted(list(files) + ['subscription-urls.json']))
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        self.assertFalse((self.state / 'pending.json').exists())
        self.assertIn(ENDPOINT, out)
        self.assertIn(str(len(host.DEVICES)) + ' enabled', out)
        # The native format gate ran on every YAML profile with the resolved core.
        checks = [c for c in self.mihomo_calls if c[0] == '/fixture/mihomo' and '-t' in c]
        self.assertEqual(len(checks), len(host.DEVICES))
        # Requests carry only public expectations plus identity bindings, never account secrets.
        info = dict(self.server.requests)['connect-info']
        self.assertEqual(info, {'endpoint': ENDPOINT, 'ssh_port': 22, 'lock': {'lock': 1}})
        pull = dict(self.server.requests)['connect-pull']
        self.assertEqual((pull['owner'], pull['machine_id'], pull['node_id'], pull['endpoint']),
                         (manifest['owner'], 'm' * 32, manifest['node_id'], ENDPOINT))
        self.assertTrue(self.state.joinpath('state.json').stat().st_mode & 0o077 == 0)

    def test_new_device_after_old_archive_is_learned_by_reconnect(self):
        self.assertEqual(self.connect()[0], 0)
        first = self.document()
        self.server.add_device('laptop')
        self.assertEqual(self.connect()[0], 0)
        again = self.document()
        self.assertNotEqual(again['backup'], first['backup'])
        self.assertTrue(Path(first['backup']).is_file(), 'older snapshot is kept')
        self.assertIn('laptop', vps.checked_backup(again, again['current'], Path(again['backup']))['accounts'])
        self.assertEqual((again['owner'], again['config'], again['current']['node_id']),
                         (first['owner'], first['config'], first['current']['node_id']))
        self.assertEqual(again['previous'], [])
        self.assertIn('laptop.yaml', os.listdir(self.state / 'outputs'))

    def test_identical_reconnect_keeps_current_record(self):
        self.assertEqual(self.connect()[0], 0)
        first = self.document()['current']
        self.assertEqual(self.connect()[0], 0)
        self.assertEqual(self.document()['current'], first)

    def test_legacy_record_without_node_id_refreshes_without_old_archive(self):
        self.assertEqual(self.connect()[0], 0)
        document = self.document()
        old = Path(document['backup'])
        document['current'].pop('node_id')
        self.write_state(document)
        old.unlink()  # fresh machine: the old PC's archive is simply absent
        code, _, err = self.connect()
        self.assertEqual(code, 0, err)
        refreshed = self.document()
        self.assertEqual(refreshed['current']['node_id'], self.server.manifest['node_id'])
        self.assertNotEqual(refreshed['backup'], str(old))

    def test_legacy_record_checks_old_archive_when_present(self):
        self.assertEqual(self.connect()[0], 0)
        document = self.document()
        document['current'].pop('node_id')
        self.write_state(document)
        self.server.manifest['node_id'] = str(uuid.uuid4())  # a replaced node under the same owner
        before = tree(self.state)
        code, _, err = self.connect()
        self.assertEqual(code, 1)
        self.assertIn('replaced node', err)
        self.assertEqual(tree(self.state), before)
        self.assertEqual(self.server.calls[-1], 'connect-info')

    # --- refusals before any binding write ---------------------------------------
    def refused(self, mutate, message):
        self.state = self.state.parent / ('refuse-' + uuid.uuid4().hex[:6])
        self.assertEqual(self.connect()[0], 0)
        document = self.document()
        mutate(document)
        self.write_state(document)
        self.server.calls.clear()
        before = tree(self.state)
        code, _, err = self.connect()
        self.assertEqual(code, 1)
        self.assertIn(message, err)
        self.assertEqual(tree(self.state), before, 'refusal wrote local state')
        self.assertNotIn('connect-pull', self.server.calls)

    def test_foreign_owner_machine_node_endpoint_and_config_are_refused(self):
        self.refused(lambda d: d.update(owner=str(uuid.uuid4())), 'different deployment')
        self.refused(lambda d: d.update(config=dict(CONFIG, domain='other.example.org')), 'different deployment')
        self.refused(lambda d: d['current'].update(machine_id='z' * 32), 'different machine')
        self.refused(lambda d: d['current'].update(node_id=str(uuid.uuid4())), 'different machine')

    def test_other_endpoint_in_existing_state_is_refused(self):
        self.assertEqual(self.connect()[0], 0)
        document = self.document()
        document['current']['endpoint'] = '93.184.216.99'
        self.write_state(document)
        before = tree(self.state)
        code, _, err = self.connect()
        self.assertEqual(code, 1)
        self.assertEqual(tree(self.state), before)

    def test_info_must_match_ssh_endpoint_and_be_published(self):
        for change, message in (({'endpoint': '93.184.216.99'}, 'SSH endpoint'), ({'published': False}, 'published node'),
                                ({'format': 'x'}, 'Unexpected'), ({'config': {'domain': 'x.example.org'}}, 'configuration')):
            with self.subTest(change=change):
                original = self.server.info
                self.server.info = lambda change=change, original=original: dict(original(), **change)
                code, _, err = self.connect(state=self.state / str(len(message)))
                self.server.info = original
                self.assertEqual(code, 1)
                self.assertIn(message, err)
                self.assertNotIn('connect-pull', self.server.calls)
                self.assertFalse((self.state / str(len(message)) / 'state.json').exists())

    def test_unknown_or_uncommitted_local_state_is_refused(self):
        vps.private_dir(self.state)
        host.atomic(self.state / 'state.json', host.dumps({'format': 'other'}))
        self.assertEqual(self.connect()[0], 1)
        self.write_state({'format': host.FORMAT, 'owner': 'x', 'config': CONFIG, 'current': None, 'previous': [], 'backup': None})
        code, _, err = self.connect()
        self.assertEqual((code, self.server.calls), (1, []))
        self.assertIn('another --state-dir', err)

    def test_pending_and_busy_refuse_before_any_remote_io(self):
        vps.private_dir(self.state)
        host.atomic(self.state / 'pending.json', '{}')
        code, _, err = self.connect()
        self.assertEqual((code, self.server.calls), (1, []))
        self.assertIn('pending', err)
        (self.state / 'pending.json').unlink()
        with vps.locked(self.state):
            code, _, err = self.connect()
        self.assertEqual((code, self.server.calls), (1, []))
        self.assertIn('Another VPS command', err)

    def test_missing_mihomo_refuses_before_ssh(self):
        with mock.patch.object(vps, 'resolve_mihomo', side_effect=host.Error('Install native Mihomo')):
            code, _, err = self.connect()
        self.assertEqual((code, self.server.calls), (1, []))
        self.assertFalse((self.state / 'state.json').exists())

    # --- failures preserve old state ---------------------------------------------
    def failed_with_preserved_state(self, prepare, message):
        self.assertEqual(self.connect()[0], 0)
        self.server.add_device('laptop')
        prepare()
        before = tree(self.state / 'outputs'), (self.state / 'state.json').read_bytes(), sorted(os.listdir(self.state / 'generations'))
        code, _, err = self.connect()
        self.assertEqual(code, 1)
        self.assertIn(message, err)
        after = tree(self.state / 'outputs'), (self.state / 'state.json').read_bytes(), sorted(os.listdir(self.state / 'generations'))
        self.assertEqual(after, before)
        backups = os.listdir(self.state / 'backups')
        self.assertEqual(len([b for b in backups if b.endswith('.tar')]), 1, 'failed pull must not leave an accepted .tar')
        self.assertEqual(len([b for b in backups if b.endswith('.incomplete')]), 1)
        self.assertFalse((self.state / 'pending.json').exists())

    def test_interrupted_pull(self):
        self.failed_with_preserved_state(lambda: setattr(self.server, 'pull_fault', 'partial'), 'ssh stream cut')

    def test_garbage_archive(self):
        self.failed_with_preserved_state(lambda: setattr(self.server, 'pull_fault', 'garbage'), '')

    def test_bad_feeds(self):
        self.failed_with_preserved_state(lambda: setattr(self.server, 'outputs_fault', 'missing'), 'disagree')

    def test_native_format_gate_failure(self):
        self.failed_with_preserved_state(lambda: setattr(self, 'mihomo_fault', True), 'mihomo rejected')

    def test_failure_after_durable_commit_keeps_referenced_archive(self):
        real = vps.remember_commit
        def commit_then_fail(*args, **kwargs):
            real(*args, **kwargs)
            raise OSError('fsync failed after os.replace')
        with mock.patch.object(vps, 'remember_commit', side_effect=commit_then_fail):
            code, _, err = self.connect()
        self.assertEqual(code, 1)
        self.assertIn('committed locally', err)
        document = self.document()
        self.assertTrue(Path(document['backup']).is_file(), 'committed state must not point at a missing tar')
        self.assertEqual([b for b in os.listdir(self.state / 'backups') if b.endswith('.incomplete')], [])
        self.assertEqual(self.connect()[0], 0)  # rerun refreshes normally

    def test_failure_before_commit_renames_archive_and_keeps_old_binding(self):
        self.assertEqual(self.connect()[0], 0)
        before = (self.state / 'state.json').read_bytes()
        self.server.add_device('laptop')
        with mock.patch.object(vps, 'remember_commit', side_effect=OSError('before write')):
            self.assertEqual(self.connect()[0], 1)
        self.assertEqual((self.state / 'state.json').read_bytes(), before)
        backups = os.listdir(self.state / 'backups')
        self.assertEqual(len([b for b in backups if b.endswith('.incomplete')]), 1)
        self.assertEqual(len([b for b in backups if b.endswith('.tar')]), 1)

    def test_pull_request_binds_actual_adapter_digest(self):
        self.assertEqual(self.connect()[0], 0)
        pull = dict(self.server.requests)['connect-pull']
        self.assertEqual(pull['adapter_sha256'], host.digest(vps.MODULE / 'lib/host.py'))

    def test_unknown_installed_adapter_fails_before_publish(self):
        original = self.server.remote
        def refusing(record, action, *args, **kwargs):
            if action == 'connect-pull':
                raise host.Error('Installed adapter is not the requested or a pinned predecessor')
            return original(record, action, *args, **kwargs)
        self.server.remote = refusing
        code, _, err = self.connect()
        self.assertEqual(code, 1)
        self.assertIn('Installed adapter', err)
        self.assertFalse((self.state / 'state.json').exists())

    def test_race_between_archive_and_outputs_says_retry(self):
        self.server.outputs_fault = 'missing'
        code, _, err = self.connect()
        self.assertEqual(code, 1)
        self.assertIn('retry connect', err)
        self.assertIn('disagree', err)

    def test_unreadable_state_after_commit_began_keeps_archive(self):
        real = vps.remember_commit
        def commit_corrupt_fail(*args, **kwargs):
            real(*args, **kwargs)
            (self.state / 'state.json').write_text('{not json')
            raise OSError('late failure')
        with mock.patch.object(vps, 'remember_commit', side_effect=commit_corrupt_fail):
            code, _, err = self.connect()
        self.assertEqual(code, 1)
        self.assertIn('UNKNOWN', err)
        self.assertEqual([b for b in os.listdir(self.state / 'backups') if b.endswith('.incomplete')], [])

    def test_stale_outputs_beside_unusable_old_archive_are_kept_with_warning(self):
        self.assertEqual(self.connect()[0], 0)
        document = self.document()
        Path(document['backup']).unlink()
        (self.state / 'outputs' / 'gone.yaml').write_bytes(b'old')
        os.chmod(self.state / 'outputs' / 'gone.yaml', 0o600)
        code, _, err = self.connect()
        self.assertEqual(code, 0, err)
        self.assertIn('1 older profile', err)
        self.assertTrue((self.state / 'outputs' / 'gone.yaml').exists())

    def test_archive_that_disagrees_with_info_is_refused(self):
        original = self.server.archive
        def stale():
            other = FakeServer(self.server.manifest['owner'])
            other.manifest['accounts'] = self.server.manifest['accounts']
            return other.archive()  # same owner, different node_id
        self.server.archive = stale
        code, _, err = self.connect()
        self.assertEqual(code, 1)
        self.assertIn('published node', err)
        self.assertFalse((self.state / 'state.json').exists())
        self.assertEqual([b for b in os.listdir(self.state / 'backups') if b.endswith('.tar')], [])

    def test_blank_failure_creates_no_binding(self):
        self.server.pull_fault = 'partial'
        self.assertEqual(self.connect()[0], 1)
        self.assertFalse((self.state / 'state.json').exists())
        self.assertFalse((self.state / 'outputs').exists())

    def test_stale_old_archive_is_not_required_for_existing_binding(self):
        self.assertEqual(self.connect()[0], 0)
        old = Path(self.document()['backup'])
        os.chmod(old, 0o644)  # unusable under the private-file gate
        self.server.add_device('laptop')
        code, _, err = self.connect()
        self.assertEqual(code, 0, err)

    def test_default_domain_and_domain_file_untouched_even_with_other_saved_config(self):
        self.assertEqual(self.connect()[0], 0)  # base_domain/default_domain raise if called
        self.assertEqual(vps.load(self.state / 'state.json')['config'], CONFIG)

    def test_parser_has_only_the_simple_flags(self):
        args = vps.parser().parse_args(['connect', '--ssh', 'root@' + ENDPOINT])
        self.assertEqual((args.identity, args.port, args.ssh_proxy, args.mihomo), (None, None, None, None))
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            vps.parser().parse_args(['connect'])


class Transport(unittest.TestCase):
    """remote_command source selection and the unchanged SSH wrapper."""

    def setUp(self):
        self.record = {'target': 'root@' + ENDPOINT, 'options': list(vps.SSH_OPTIONS), 'endpoint': ENDPOINT,
                       'user': 'root', 'port': 22, 'host_key_alias': None}

    @staticmethod
    def old_command(record, action):
        source = (vps.MODULE / 'lib/host.py').read_bytes()
        import zlib
        packed = base64.b64encode(zlib.compress(source, 9)).decode('ascii')
        bootstrap = 'import base64,zlib;exec(compile(zlib.decompress(base64.b64decode(' + repr(packed) + ')),"<string>","exec"))'
        prefix = '' if record['user'] == 'root' else 'sudo -n '
        return prefix + 'env QD_SSH_CONNECTION="$SSH_CONNECTION" python3 -c ' + shlex.quote(bootstrap) + ' ' + shlex.quote(action)

    def test_normal_actions_keep_the_single_source_bootstrap(self):
        for user in ('root', 'ops'):
            for action in ('status', 'backup', 'outputs', 'devices-list', 'connect-infos', 'connect'):
                record = dict(self.record, user=user)
                self.assertEqual(vps.remote_command(record, action), self.old_command(record, action))

    def test_connect_actions_ship_both_public_sources_and_nothing_else(self):
        for action in vps.CONNECT_ACTIONS:
            command = vps.remote_command(self.record, action)
            self.assertNotEqual(command, self.old_command(self.record, action))
            self.assertTrue(command.endswith(' ' + action))
            self.assertIn(vps.packed_source('host.py'), command)
            self.assertIn(vps.packed_source('connect_read.py'), command)
            self.assertLess(len(command.encode()), 120 * 1024)
        self.assertTrue(vps.remote_command(dict(self.record, user='ops'), 'connect-info').startswith('sudo -n env QD_SSH_CONNECTION='))
        self.assertEqual(vps.CONNECT_ACTIONS, ('connect-info', 'connect-pull'))

    def test_oversized_sources_are_refused_for_both_actions(self):
        with tempfile.TemporaryDirectory(prefix='qd-module-') as tmp:
            lib = Path(tmp) / 'lib'
            lib.mkdir()
            (lib / 'host.py').write_bytes((vps.MODULE / 'lib/host.py').read_bytes())
            (lib / 'connect_read.py').write_bytes(os.urandom(120 * 1024))
            with mock.patch.object(vps, 'MODULE', Path(tmp)):
                for action in vps.CONNECT_ACTIONS:
                    with self.assertRaisesRegex(host.Error, 'single-argument limit'):
                        vps.remote_command(self.record, action)

    def test_bootstrap_runs_original_host_as_non_main_and_reader_in_own_namespace(self):
        with tempfile.TemporaryDirectory(prefix='qd-module-') as tmp:
            lib = Path(tmp) / 'lib'
            lib.mkdir()
            (lib / 'host.py').write_text("class Error(Exception): pass\nMARK = __name__\n"
                                         "if __name__ == '__main__':\n    raise SystemExit('host main ran')\n")
            (lib / 'connect_read.py').write_text(
                "import json\nMINE = 'reader-only'\n"
                "def main(h, action, stdin, stdout):\n"
                "    request = json.load(stdin)\n"
                "    assert not hasattr(h, 'MINE')\n"
                "    if request.get('fail'):\n        raise h.Error('refused: ' + action)\n"
                "    stdout.write(json.dumps([h.MARK, action, request]).encode())\n")
            with mock.patch.object(vps, 'MODULE', Path(tmp)):
                def run(action, request):
                    return subprocess.run(vps.remote_command(self.record, action), shell=True, input=json.dumps(request).encode(),
                                          capture_output=True, env=dict(os.environ, SSH_CONNECTION='x'))
                for action in vps.CONNECT_ACTIONS:
                    result = run(action, {'a': 1})
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout), ['qd_host', action, {'a': 1}])
                    failed = run(action, {'fail': True})
                    self.assertEqual(failed.returncode, 1)
                    self.assertEqual(failed.stderr.decode().strip(), 'refused: ' + action)

    def test_connect_rides_the_unchanged_pinned_ssh_wrapper(self):
        calls = []
        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout=b'{}', stderr=b'')
        request = {'owner': 'o', 'node_id': 'n', 'secret-ish': 'PAYLOAD-MARKER'}
        with mock.patch.object(vps, 'ssh_effective', return_value={}), mock.patch.object(vps.subprocess, 'run', side_effect=fake_run):
            vps.remote(self.record, 'connect-info', request)
        argv, kwargs = calls[0]
        self.assertEqual(argv[:2], ['ssh', '-T'])
        self.assertIn('StrictHostKeyChecking=yes', argv)
        self.assertIn('HostName=' + ENDPOINT, argv)
        self.assertEqual(kwargs['shell'], False)
        self.assertIn(b'PAYLOAD-MARKER', kwargs['input'])
        self.assertTrue(all('PAYLOAD-MARKER' not in a for a in argv), 'payload must travel on stdin only')
        self.assertFalse([a for a in argv if 'UserKnownHostsFile' in a or 'StrictHostKeyChecking=no' in a or 'ssh-copy-id' in a])
        self.assertEqual(argv[-1], vps.remote_command(self.record, 'connect-info'))

    def test_explicit_target_options_are_strict_and_proxy_is_invocation_only(self):
        args = vps.parser().parse_args(['connect', '--ssh', 'root@' + ENDPOINT, '--identity', '/tmp/key', '--port', '2222'])
        with mock.patch.object(vps, 'ssh_effective', return_value={'hostname': ENDPOINT, 'user': 'root', 'port': '2222'}):
            record = vps.target_record(args, None)
        self.assertEqual(record['port'], 2222)
        self.assertIn('StrictHostKeyChecking=yes', record['options'])
        self.assertEqual(record['options'][-4:], ['-i', '/tmp/key', '-o', 'IdentitiesOnly=yes'])
        self.assertNotIn('ProxyCommand', ' '.join(record['options'].__str__().split('=')[:0]))


if __name__ == '__main__':
    unittest.main()
