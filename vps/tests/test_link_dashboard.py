"""Offline fake credentials only: exact payloads, committed authority and private publication."""
import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import host
import link_dashboard as dashboard
import vps
import test_dual_cli as dual_fixture


ASSETS = {'dashboard.html': b'<link href="dashboard.css"><script id="data" type="application/json">__QD_DATA__</script><script src="dashboard.js"></script>',
          'dashboard.css': b'body{color:black}', 'dashboard.js': b'/* fixture only */'}


class Render(unittest.TestCase):
    def setUp(self):
        self.manifest = {'endpoint': '93.184.216.34', 'accounts': {
            'phone': {'enable': True}, 'disabled': {'enable': False}}}
        self.payloads = ['vless://00000000-0000-4000-8000-000000000001@93.184.216.34:443?x=%2f&x=%2F&v=+%20#公司手机',
                         'hysteria2://FAKE_AUTH@93.184.216.34:443?sni=sub.example.org&alpn=h3#</script><img src=x onerror=alert(1)>']
        self.raw = ('\r\n'.join(self.payloads) + '\r\n').encode()
        self.files = {'phone.txt': base64.b64encode(self.raw), 'phone.yaml': b'fake native YAML\n'}
        self.urls = {'phone': {'mihomo': 'https://sub.example.org:8443/mihomo/FAKE_SUB',
                               'shadowrocket': 'https://sub.example.org:8443/sub/FAKE_SUB'}}

    def render(self, **kwargs):
        return dashboard.render(self.manifest, self.files, self.urls, 'a' * 64, ASSETS, **kwargs)

    def test_exact_utf8_payloads_and_distinct_subscription_urls(self):
        original = dashboard.qr_svg
        with mock.patch.object(dashboard, 'qr_svg', wraps=original) as encoder:
            model, index, assets = self.render()
        self.assertEqual([call.args[0] for call in encoder.call_args_list],
                         [uri.encode() for uri in self.payloads] + [uri.encode() for uri in self.urls['phone'].values()])
        phone = next(d for d in model['devices'] if d['name'] == 'phone')
        self.assertEqual([l['uri'] for l in phone['links'][:2]], self.payloads)
        self.assertEqual([l['kind'] for l in phone['links']], ['node', 'node', 'subscription', 'subscription'])
        self.assertEqual(len({l['qr'] for l in phone['links']}), 4)
        self.assertEqual(phone['links'][0]['label'], '公司手机')
        self.assertEqual(model['snapshot']['backup_sha256'], 'a' * 64)
        self.assertRegex(model['snapshot']['generated_at'], r'^\d{4}-\d\d-\d\dT.*\+00:00$')
        for entry in phone['links'] + phone['downloads']:
            name = entry.get('qr', entry.get('path'))
            self.assertIsNotNone(dashboard.ASSET_NAME.fullmatch(name))
            self.assertIn(name, assets)
        downloads = {item['label']: assets[item['path']] for item in phone['downloads']}
        self.assertEqual(downloads['原生节点 URI'], self.raw)
        self.assertEqual(downloads['原生 base64 订阅'], self.files['phone.txt'])
        self.assertEqual(downloads['原生 Mihomo 配置'], self.files['phone.yaml'])
        self.assertEqual(json.loads(downloads['订阅 URL']), self.urls['phone'])
        self.assertIn(b'qd-', index)

    def test_embedded_download_bytes_equal_only_explicit_model_assets(self):
        model, _, assets = self.render(device='phone')
        expected = {link['qr'] for d in model['devices'] for link in d['links']}
        expected.update(item['path'] for d in model['devices'] for item in d['downloads'])
        self.assertEqual(set(model['files']), expected)
        for name, encoded in model['files'].items():
            self.assertEqual(base64.b64decode(encoded, validate=True), assets[name])
        self.assertFalse(any(name.endswith(('.css', '.js')) for name in model['files']))
        self.assertNotIn('index.html', model['files'])
        self.assertNotIn('metadata.json', model['files'])
        self.assertNotIn('state.json', model['files'])
        self.manifest['accounts']['home'] = {'enable': True}
        self.files.update({'home.txt': base64.b64encode(b'hysteria2://FAKE_HOME@93.184.216.34:443\n'),
                           'home.yaml': b'fake home YAML'})
        self.urls['home'] = {'mihomo': 'https://sub.example.org:8443/mihomo/FAKE_HOME',
                             'shadowrocket': 'https://sub.example.org:8443/sub/FAKE_HOME'}
        home_model, _, home_assets = self.render(device='home')
        self.assertTrue(set(model['files']).isdisjoint(home_model['files']))
        self.assertTrue(all(base64.b64decode(encoded, validate=True) == home_assets[name]
                            for name, encoded in home_model['files'].items()))
        all_model, _, _ = self.render()
        self.assertEqual(set(all_model['files']), set(model['files']) | set(home_model['files']))

    def test_script_end_and_html_metacharacters_cannot_escape_json(self):
        model, index, _ = self.render()
        text = index.decode()
        self.assertNotIn('</script><img', text)
        self.assertNotIn('onerror=alert(1)>', text)
        encoded = re.search(r'type="application/json">(.*?)</script>', text).group(1)
        self.assertNotIn('<', encoded)
        self.assertNotIn('>', encoded)
        self.assertNotIn('&', encoded)
        self.assertEqual(json.loads(encoded), model)
        self.assertEqual(json.loads(encoded)['devices'][1]['links'][1]['uri'], self.payloads[1])

    def test_disabled_has_no_credentials_and_filter_rejects_invalid_names(self):
        model, _, _ = self.render()
        self.assertEqual(model['devices'][0], {'name': 'disabled', 'enabled': False, 'links': [], 'downloads': []})
        self.assertEqual([d['name'] for d in self.render(device='phone')[0]['devices']], ['phone'])
        for name in ('disabled', 'unknown', '../phone', '<script>'):
            with self.assertRaises(dashboard.DashboardError):
                self.render(device=name)
        self.manifest['accounts']['../bad'] = {'enable': False}
        with self.assertRaises(dashboard.DashboardError):
            self.render()

    def test_qr_determinism_border_and_capacity_failure_without_payload_echo(self):
        payload = self.payloads[0].encode()
        svg = dashboard.qr_svg(payload)
        self.assertEqual(svg, dashboard.qr_svg(payload))
        self.assertIn(b'fill="#fff"', svg)
        self.assertIn(b'fill="#000"', svg)
        self.assertIn(b'M4,4h1v1h-1z', svg)
        self.assertNotIn(payload, svg)
        side = int(re.search(rb'viewBox="0 0 (\d+) ', svg).group(1))
        coords = [(int(x), int(y)) for x, y in re.findall(rb'M(\d+),(\d+)', svg)]
        self.assertTrue(all(4 <= x < side - 4 and 4 <= y < side - 4 for x, y in coords))
        for data in (b'FAKE_SECRET' * 400, b'\xff'):
            with self.assertRaises(dashboard.DashboardError) as raised:
                dashboard.qr_svg(data)
            self.assertNotIn('FAKE_SECRET', str(raised.exception))
        with self.assertRaisesRegex(dashboard.DashboardError, 'capacity'):
            dashboard.qr_svg(b'a' * 2500)  # Below byte-mode absolute max; exceeds medium ECC.

    def test_inventory_closed_schema_exact_names_and_complete_bootstrap_groups(self):
        bad_files = ({'../foreign': 'a' * 64}, {'foreign.txt': 'a' * 64},
                     {'phone.yaml': 'a' * 64}, {'qd-' + 'a' * 64 + '.svg': 'b' * 64})
        for files in bad_files:
            with self.assertRaises(dashboard.DashboardError):
                dashboard.inventory(dashboard.inventory_bytes(files))
        for schema in (True, 2, '1'):
            with self.assertRaises(dashboard.DashboardError):
                dashboard.inventory(json.dumps({'schema': schema, 'files': {}}))
        with self.assertRaises(dashboard.DashboardError):
            dashboard.inventory(b' ' * (dashboard.MAX_INVENTORY_BYTES + 1))

    def test_vendor_source_pin_and_template_contract(self):
        root = Path(dashboard.__file__).parent / 'vendor'
        pin = json.loads((root / 'manifest.json').read_bytes())
        self.assertEqual(pin['version'], '1.8.0')
        self.assertEqual(Path(sys.modules[dashboard.QrCode.__module__].__file__).resolve(),
                         (root / 'qrcodegen.py').resolve())
        self.assertEqual(hashlib.sha256((root / 'qrcodegen.py').read_bytes()).hexdigest(), pin['source_sha256'])
        self.assertIn('MIT', (root / 'LICENSE-qrcodegen').read_text())
        with mock.patch.dict(ASSETS, {'dashboard.html': b'no data placeholder'}):
            with self.assertRaisesRegex(dashboard.DashboardError, 'placeholder'):
                self.render()


class Publication(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='qd-dashboard-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / 'dashboard'

    def asset(self, content=b'fake QR bytes', ext='svg'):
        return {'qd-' + hashlib.sha256(content).hexdigest() + '.' + ext: content}

    def publish(self, content=b'fake QR bytes', index=b'old index', selected=None):
        return vps.publish_dashboard(self.output, index, self.asset(content), selected)

    def test_modes_cleanup_only_previous_inventory_and_stable_bootstrap_files(self):
        old = self.asset()
        selected = {f'phone{ext}': b'old fixture' for ext in ('.yaml', '.txt', '.uris.txt', '.urls.json')}
        self.publish(selected=selected)
        (self.output / 'foreign.txt').write_text('leave me')
        self.publish(b'new QR bytes', b'new index')
        self.assertEqual((self.output / 'index.html').read_bytes(), b'new index')
        self.assertEqual((self.output / 'foreign.txt').read_text(), 'leave me')
        self.assertFalse(any((self.output / name).exists() for name in old.keys() | selected.keys()))
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in self.output.iterdir() if p.name != 'foreign.txt'))
        self.publish(b'new QR bytes', b'repeated index')

    def test_index_failure_keeps_old_references_and_retry_cleans_partial_assets(self):
        self.publish()
        real_write = vps.write_at
        def fail(directory, name, data):
            if name == 'index.html':
                raise OSError('fixture interrupted index publication')
            return real_write(directory, name, data)
        with mock.patch.object(vps, 'write_at', side_effect=fail):
            with self.assertRaises(OSError):
                self.publish(b'new QR bytes', b'new index')
        self.assertEqual((self.output / 'index.html').read_bytes(), b'old index')
        self.assertTrue((self.output / next(iter(self.asset()))).exists())
        self.assertEqual(len(dashboard.inventory((self.output / dashboard.INVENTORY).read_bytes())), 2)
        self.publish(b'new QR bytes', b'new index')
        self.assertFalse((self.output / next(iter(self.asset()))).exists())

    def test_interrupted_stable_bootstrap_update_retries_without_cached_hash_refusal(self):
        extensions = ('.yaml', '.txt', '.uris.txt', '.urls.json')
        old = {f'phone{ext}': b'old fixture' for ext in extensions}
        new = {f'phone{ext}': b'new fixture' for ext in extensions}
        self.publish(selected=old)
        real_write = vps.write_at
        for failure in ('phone.yaml', 'phone.txt', 'index.html'):
            def fail(directory, name, data):
                if name == failure:
                    raise OSError('fixture interrupted stable output')
                return real_write(directory, name, data)
            with mock.patch.object(vps, 'write_at', side_effect=fail), self.assertRaises(OSError):
                self.publish(b'new QR bytes', b'new index', new)
            self.assertEqual((self.output / 'index.html').read_bytes(), b'old index')
        self.publish(b'new QR bytes', b'new index', new)
        self.assertTrue(all((self.output / name).read_bytes() == data for name, data in new.items()))

    def test_symlink_bad_modes_wrong_owner_and_invalid_inventory_fail_before_writes(self):
        for kind in ('symlink', 'mode', 'owner', 'inventory'):
            with self.subTest(kind=kind):
                self.output = self.root / kind
                self.publish()
                name = next(iter(self.asset()))
                path = self.output / name
                before = (self.output / 'index.html').read_bytes()
                if kind == 'symlink':
                    path.unlink(); path.symlink_to(self.root / 'elsewhere')
                elif kind == 'mode':
                    path.chmod(0o644)
                elif kind == 'inventory':
                    (self.output / dashboard.INVENTORY).write_text('{"schema":1,"files":{"../foreign":"' + 'a' * 64 + '"}}')
                if kind == 'owner':
                    original = os.stat
                    def stat_owner(*args, **kwargs):
                        value = original(*args, **kwargs)
                        if args[0] == name:
                            fields = list(value); fields[4] = os.getuid() + 1
                            return os.stat_result(fields)
                        return value
                    patch = mock.patch.object(os, 'stat', side_effect=stat_owner)
                else:
                    patch = contextlib.nullcontext()
                with patch, self.assertRaises((host.Error, dashboard.DashboardError, OSError)):
                    self.publish(b'new QR bytes', b'new index')
                self.assertEqual((self.output / 'index.html').read_bytes(), before)
                self.assertFalse((self.output / next(iter(self.asset(b'new QR bytes')))).exists())

    def test_dashboard_cannot_prune_or_keep_stale_management_runtime(self):
        selected = {f'phone{ext}': b'fake fixture' for ext in
                    ('.yaml', '.txt', '.uris.txt', '.urls.json', '.management.yaml')}
        self.publish(selected=selected)
        with self.assertRaisesRegex(host.Error, 'explicit refresh'):
            self.publish(b'new QR bytes', b'new index')
        self.assertEqual((self.output / 'index.html').read_bytes(), b'old index')
        self.assertEqual((self.output / 'phone.management.yaml').read_bytes(), b'fake fixture')

    def test_git_and_ancestor_symlinks_and_unowned_index_refuse(self):
        (self.root / '.git').mkdir()
        with self.assertRaisesRegex(host.Error, 'outside Git'):
            self.publish()
        (self.root / '.git').rmdir()
        real = self.root / 'real'; real.mkdir(mode=0o700)
        self.output.symlink_to(real, target_is_directory=True)
        with self.assertRaises(OSError):
            self.publish()
        self.assertEqual(list(real.iterdir()), [])
        self.output.unlink(); self.output.mkdir(mode=0o700)
        host.atomic(self.output / 'index.html', b'foreign index')
        with self.assertRaisesRegex(host.Error, 'unowned'):
            self.publish()


class CommittedAuthority(unittest.TestCase):
    def setUp(self):
        self.fixture = dual_fixture.DualCLI()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        # Controller tests use the same model renderer with a tiny local UI fixture.
        def render(manifest, digest, files, device=None, extra_downloads=None):
            return dashboard.render(manifest, files, vps.subscription_urls(manifest), digest,
                                    ASSETS, device, extra_downloads)
        patch = mock.patch.object(vps, 'dashboard_render', side_effect=render)
        patch.start(); self.addCleanup(patch.stop)

    def test_dashboard_cli_all_enabled_entries_and_no_network(self):
        f = self.fixture
        f.publish()
        args = vps.parser().parse_args(['dashboard'])
        with mock.patch.object(vps, 'remote', side_effect=AssertionError('offline only')), mock.patch('sys.stdout', new_callable=io.StringIO) as output:
            folder = vps.export_dashboard(f.state, args)
        self.assertEqual(folder, f.state / 'dashboard')
        text = (folder / 'index.html').read_text()
        model = json.loads(re.search(r'type="application/json">(.*?)</script>', text).group(1))
        self.assertEqual(model['snapshot']['backup_sha256'], f.doc['native_generation'])
        self.assertEqual(len(model['devices']), len(f.dual['accounts']))
        self.assertTrue(all(len(d['links']) == 4 for d in model['devices']))
        for account in f.dual['accounts'].values():
            self.assertNotIn(account['auth'], output.getvalue())
        host.atomic(f.state / 'pending.json', '{}')
        with self.assertRaisesRegex(host.Error, 'Pending'):
            vps.export_dashboard(f.state, args)

    def test_bootstrap_preserves_four_files_and_management_recheck_every_time(self):
        f = self.fixture
        f.publish()
        args = f.export_args('--management-interface', 'eno1')
        with mock.patch('sys.stdout', new_callable=io.StringIO):
            folder = vps.export_bootstrap(f.state, args)
        for filename, data in dashboard.native_material('company', f.current_outputs, vps.subscription_urls(f.dual)).items():
            self.assertEqual((folder / filename).read_bytes(), data)
        self.assertTrue((folder / 'index.html').exists())
        count = f.events.count('mihomo-t')
        with mock.patch('sys.stdout', new_callable=io.StringIO):
            vps.export_bootstrap(f.state, args)
        self.assertEqual(f.events.count('mihomo-t'), count + 1)
        index = (folder / 'index.html').read_bytes()
        with self.assertRaisesRegex(host.Error, 'explicit refresh'):
            vps.export_bootstrap(f.state, f.export_args())
        self.assertEqual((folder / 'index.html').read_bytes(), index)

    def test_missing_or_legacy_generation_refuses_not_silently_ignored(self):
        f = self.fixture
        args = vps.parser().parse_args(['dashboard'])
        with self.assertRaisesRegex(host.Error, 'No committed native generation'):
            vps.export_dashboard(f.state, args)
        f.doc['native_generation'] = host.digest(f.before)
        host.atomic(f.state / 'state.json', host.dumps(f.doc))
        with self.assertRaisesRegex(host.Error, 'dual'):
            vps.export_dashboard(f.state, args)


if __name__ == '__main__':
    unittest.main()
