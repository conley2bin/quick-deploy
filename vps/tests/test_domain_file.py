import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))

import host
import vps


class DomainFile(unittest.TestCase):
    def write(self, domain, ssh=b'root@136.0.5.229'):
        """Temp module whose config.txt carries this DOMAIN (raw bytes, no newline added)."""
        module = Path(tempfile.mkdtemp())
        (module / 'config.txt').write_bytes(b'VPS_SSH=' + ssh + b'\nDOMAIN=' + domain)
        return mock.patch.object(vps, 'MODULE', module)

    def write_raw(self, data):
        module = Path(tempfile.mkdtemp())
        if data is not None:
            (module / 'config.txt').write_bytes(data)
        return mock.patch.object(vps, 'MODULE', module)

    def test_shipped_file_is_single_public_base(self):
        self.assertEqual((vps.MODULE / 'config.txt').read_bytes(), b'VPS_SSH=root@136.0.5.229\nDOMAIN=conley2bin.com\n')
        self.assertFalse((vps.MODULE / 'domain.txt').exists())
        self.assertEqual(vps.default_ssh(), 'root@136.0.5.229')
        self.assertEqual(vps.default_domain('sub'), 'sub.conley2bin.com')
        self.assertEqual(vps.default_domain('cdn'), 'cdn.conley2bin.com')

    def test_lf_crlf_and_no_newline_accepted_and_cwd_independent(self):
        old = os.getcwd()
        try:
            os.chdir(tempfile.mkdtemp())
            self.assertEqual(vps.base_domain(), 'conley2bin.com')
        finally:
            os.chdir(old)
        for data in (b'VPS_SSH=h\r\nDOMAIN=Example.org\r\n', b'VPS_SSH=h\nDOMAIN=example.org\n', b'VPS_SSH=h\nDOMAIN=example.org',
                     b'# note\n\nVPS_SSH=h\nDOMAIN=example.org\n', b'DOMAIN=example.org\nVPS_SSH=h\n'):
            with self.subTest(data=data), self.write_raw(data):
                self.assertEqual(vps.default_domain('sub'), 'sub.example.org')

    def test_invalid_domain_rejected(self):
        for data in (b'', b'\n', b' example.org', b'example.org ', b'example.org\t', b'1.2.3.4', b'127.0.0.1', b'example.123',
                     b'https://example.org', b'example.org/x', b'example.org:443', b'user@example.org', b'localhost',
                     b'exa mple.org', b'-a.example.org', b'example..org', b'\xc3\xa9.org', b'a' * 64 + b'.org', b'$HOME', b'`id`.org'):
            with self.subTest(data=data), self.write(data):
                with self.assertRaises(host.Error):
                    vps.base_domain()

    def test_invalid_ssh_rejected(self):
        for ssh in (b'', b'root@', b'root@h:22', b'root@h -p 22', b'-oProxyCommand=x', b'ssh://root@h', b'root@h;id', b'$(id)',
                    b'a@b@c', b'root@h/x', b'"root@h"', b'root@\xc3\xa9'):
            with self.subTest(ssh=ssh), self.write(b'example.org', ssh):
                with self.assertRaises(host.Error):
                    vps.default_ssh()

    def test_malformed_structure_rejected(self):
        good = b'VPS_SSH=h\nDOMAIN=example.org\n'
        for data in (b'', b'VPS_SSH=h\n', b'DOMAIN=example.org\n', good + b'DOMAIN=example.org\n', good + b'VPS_SSH=h\n',
                     good + b'EXTRA=1\n', good + b'vps_ssh=h\n', b'VPS_SSH = h\nDOMAIN=example.org\n', b'VPS_SSH=h\nDOMAIN= example.org\n',
                     b'VPS_SSH=h # c\nDOMAIN=example.org\n', good + b'junk\n', b'VPS_SSH=h\rDOMAIN=example.org\n',
                     b'VPS_SSH=h\nDOMAIN=example.org\r\r\n', b'export VPS_SSH=h\nDOMAIN=example.org\n',
                     b'VPS_SSH=h\nDOMAIN=example.org\n' + b'#' * 5000, b'VPS_SSH=h\nDOMAIN=\xc3\xa9.org\n', b'  # c\n' + good):
            with self.subTest(data=data), self.write_raw(data):
                with self.assertRaises(host.Error):
                    vps.default_ssh()
                with self.assertRaises(host.Error):
                    vps.base_domain()

    def test_no_interpolation_or_eval(self):
        with self.write_raw(b'VPS_SSH=h\nDOMAIN=$HOME.org\n'), self.assertRaises(host.Error):
            vps.base_domain()

    def test_missing_file_clear_error(self):
        with self.write_raw(None):
            with self.assertRaisesRegex(host.Error, 'explicit option'):
                vps.base_domain()
            with self.assertRaises(host.Error):
                vps.default_ssh()

    def test_default_ssh_ignores_domain_value_but_domain_ignores_ssh_value(self):
        import target_config
        module = Path(tempfile.mkdtemp())
        (module / 'config.txt').write_bytes(b'VPS_SSH=root@1.2.3.4\nDOMAIN=https://bad\n')
        self.assertEqual(target_config.default_ssh(module), 'root@1.2.3.4')
        self.assertEqual(vps.default_ssh(module), 'root@1.2.3.4')
        with self.assertRaises(host.Error):
            target_config.base_domain(module)
        with self.assertRaises(host.Error):
            target_config.read_config(module)
        (module / 'config.txt').write_bytes(b'VPS_SSH=root@h:22\nDOMAIN=example.org\n')
        self.assertEqual(vps.base_domain(module), 'example.org')
        # a missing key or malformed line still fails either accessor
        (module / 'config.txt').write_bytes(b'VPS_SSH=h\n')
        with self.assertRaises(host.Error):
            target_config.default_ssh(module)

    def test_target_config_contract(self):
        import target_config
        module = Path(tempfile.mkdtemp())
        (module / 'config.txt').write_bytes(b'VPS_SSH=alias\nDOMAIN=Example.org\n')
        self.assertEqual(target_config.read_config(module), {'VPS_SSH': 'alias', 'DOMAIN': 'example.org'})
        self.assertEqual(target_config.default_ssh(module), 'alias')
        self.assertEqual(target_config.base_domain(module), 'example.org')

    def run_main(self, argv, state=None):
        state = state or Path(tempfile.mkdtemp())
        seen = {}
        def capture(st, document, args):
            seen['args'] = args
            raise host.Error('stop')
        def document(st, args, create=False):
            seen['args'] = args
            raise host.Error('stop')
        with mock.patch.object(vps, 'state_document', document), mock.patch.object(vps, 'cdn', capture), \
             mock.patch('sys.stderr'):
            code = vps.main(['--state-dir', str(state), *argv])
        return code, seen.get('args')

    def test_deploy_default_and_explicit_override_without_file(self):
        base = ['deploy', '--ssh', 'x', '--reality-target', 'target.example.org']
        with self.write(b'example.net'):
            code, args = self.run_main(base)
            self.assertEqual(args.domain, 'sub.example.net')
            code, args = self.run_main(base + ['--domain', 'sub.other.org'])
            self.assertEqual(args.domain, 'sub.other.org')
        with self.write(b'bad domain'):
            code, args = self.run_main(base + ['--domain', 'sub.other.org'])
            self.assertEqual(args.domain, 'sub.other.org')
            self.assertEqual(self.run_main(base)[0], 1)

    def test_deploy_config_uses_default(self):
        with self.write(b'example.net'):
            code, args = self.run_main(['preflight', '--ssh', 'x', '--reality-target', 'target.example.org'])
        with mock.patch('sys.stdin.isatty', return_value=False):
            self.assertEqual(vps.configuration(args)['domain'], 'sub.example.net')

    def test_cli_default_keeps_file_error_not_too_long(self):
        for command in (['deploy', '--ssh', 'x', '--reality-target', 't.example.org'],
                        ['preflight', '--ssh', 'x', '--reality-target', 't.example.org'],
                        ['cdn', '--interface', 'eth0', '--dns-server', '1.1.1.1']):
            for data in (None, b'', b'VPS_SSH=h\nDOMAIN=a.example.org\nDOMAIN=b\n', b'VPS_SSH=h\nDOMAIN=https://example.org\n'):
                with self.subTest(command=command[0], data=data), self.write_raw(data), mock.patch('sys.stderr') as err:
                    self.assertEqual(vps.main(['--state-dir', tempfile.mkdtemp(), *command]), 1)
                    message = ''.join(c.args[0] for c in err.write.call_args_list)
                    self.assertNotIn('too long', message)
                    self.assertIn('config.txt', message)

    def test_too_long_derived_host_reports_length(self):
        base = '.'.join(['a' * 61] * 3 + ['b' * 60, 'org'])  # 250 chars, valid alone; sub.<base> exceeds 253
        self.assertEqual(len(base), 250)
        with self.write(base.encode()):
            self.assertEqual(vps.base_domain(), base)
            with self.assertRaisesRegex(host.Error, 'too long'):
                vps.default_domain('sub')

    def test_cdn_role_default_and_explicit(self):
        base = ['cdn', '--interface', 'eth0', '--dns-server', '1.1.1.1']
        with self.write(b'example.net'):
            self.assertEqual(self.run_main(base)[1].cdn_domain, 'cdn.example.net')
            self.assertEqual(self.run_main(base + ['--domain', 'c.other.org'])[1].cdn_domain, 'c.other.org')
        with self.write(b''):
            self.assertEqual(self.run_main(base + ['--domain', 'c.other.org'])[1].cdn_domain, 'c.other.org')
            self.assertEqual(self.run_main(base)[0], 1)

    def test_ssh_default_and_explicit_independent(self):
        base = ['--reality-target', 'target.example.org']
        with self.write(b'example.net', b'ops@host.example'):
            for command in ('deploy', 'preflight', 'replace'):
                with self.subTest(command=command):
                    self.assertEqual(self.run_main([command, *base])[1].ssh, 'ops@host.example')
                    self.assertEqual(self.run_main([command, '--ssh', 'x', *base])[1].ssh, 'x')
                    self.assertEqual(self.run_main([command, '--domain', 'sub.o.org', *base])[1].domain, 'sub.o.org')
        with self.write(b'bad domain', b'bad ssh'):  # explicit values bypass an invalid file
            _, args = self.run_main(['deploy', '--ssh', 'x', '--domain', 'sub.o.org', *base])
            self.assertEqual((args.ssh, args.domain), ('x', 'sub.o.org'))
        with self.write_raw(None):
            self.assertEqual(self.run_main(['deploy', '--ssh', 'x', '--domain', 'sub.o.org', *base])[1].ssh, 'x')
            self.assertEqual(self.run_main(['deploy', '--domain', 'sub.o.org', *base])[0], 1)

    def test_omitted_ssh_always_uses_config_and_restore_is_explicit(self):
        state = Path(tempfile.mkdtemp())
        (state / 'state.json').write_text('{}')
        with self.write(b'example.net', b'ops@host.example'):
            for command in ('deploy', 'preflight', 'replace'):
                self.assertEqual(self.run_main([command, '--reality-target', 't.example.org'], state)[1].ssh, 'ops@host.example')
            self.assertEqual(self.run_main(['preflight', '--ssh', 'x', '--reality-target', 't.example.org'], state)[1].ssh, 'x')
            with mock.patch('sys.stderr'), self.assertRaises(SystemExit):
                vps.main(['--state-dir', str(state), 'restore', '--backup', 'b'])

    def foreign_run(self, command, ssh_args):
        """Existing state deployed at 1.1.1.1; the target resolves to 2.2.2.2. Returns (code, stderr, remote calls)."""
        state = Path(tempfile.mkdtemp())
        current = {'target': 'root@1.1.1.1', 'endpoint': '1.1.1.1', 'user': 'root', 'port': 22, 'options': [], 'host_key_alias': None}
        document = {'format': host.FORMAT, 'owner': 'o', 'current': current, 'previous': [], 'backup': None,
                    'config': {'domain': 'sub.example.net', 'reality_target': 't.example.org', 'acme_email': '', 'proxy_port': 443}}
        target = {'target': 'root@2.2.2.2', 'endpoint': '2.2.2.2', 'user': 'root', 'port': 22, 'options': [], 'host_key_alias': None}
        with self.write(b'example.net', b'root@2.2.2.2'), mock.patch.object(vps, 'state_document', return_value=document), \
             mock.patch.object(vps, 'target_record', return_value=target), mock.patch.object(vps, 'remote', return_value={}) as remote, \
             mock.patch.object(vps, 'release_lock', return_value={}), mock.patch('sys.stderr') as err:
            code = vps.main(['--state-dir', str(state), command, *ssh_args, '--reality-target', 't.example.org'])
        return code, ''.join(c.args[0] for c in err.write.call_args_list), remote.call_count

    def test_configured_foreign_vps_refused_before_remote(self):
        for command in ('deploy', 'preflight'):
            with self.subTest(command=command):
                code, message, calls = self.foreign_run(command, [])
                self.assertEqual((code, calls), (1, 0))
                self.assertIn('VPS_SSH', message)
                self.assertIn('--state-dir', message)

    def test_explicit_deploy_to_foreign_host_still_refused(self):
        code, message, calls = self.foreign_run('deploy', ['--ssh', 'root@2.2.2.2'])
        self.assertEqual((code, calls), (1, 0))

    def test_explicit_preflight_of_candidate_host_allowed(self):
        code, message, calls = self.foreign_run('preflight', ['--ssh', 'root@2.2.2.2'])
        self.assertEqual((code, calls), (0, 1))

    def test_deployment_only_mode(self):
        for command in ('connect', 'devices', 'export', 'dashboard'):
            for argv in ([command], ['--state-dir', 'S', command, '--ssh', 'x'], ['--state-dir=S', command]):
                with self.subTest(command=command, argv=argv), mock.patch('sys.stderr') as err:
                    self.assertEqual(vps.main(['--deployment-only', *argv]), 2)
                    self.assertIn('manage.sh ' + command, ''.join(c.args[0] for c in err.write.call_args_list))
        with mock.patch('sys.stderr'), self.assertRaises(SystemExit):
            vps.main(['--deployment-only', '--deployment-only', 'status'])
        with mock.patch('sys.stderr'), self.assertRaises(SystemExit):
            vps.main(['--state-dir', 'S', '--deployment-only', 'status'])
        out = io.StringIO()
        with mock.patch('sys.stdout', out), self.assertRaises(SystemExit):
            vps.main(['--deployment-only', '--help'])
        for name in ('deploy', 'cdn', 'status', 'recover', 'restore', 'replace', 'backup', 'preflight'):
            self.assertIn(name, out.getvalue())
        for name in ('connect', 'devices', 'export', 'dashboard'):
            self.assertNotIn(name, out.getvalue())
        self.assertEqual(vps.parser(True).parse_args(['status']).command, 'status')
        self.assertEqual(vps.parser().parse_args(['devices', 'list']).command, 'devices')
        self.assertEqual(vps.parser().parse_args(['connect', '--ssh', 'x']).command, 'connect')

    def test_setup_script_passes_deployment_only(self):
        self.assertIn('lib/vps.py" --deployment-only "$@"', (vps.MODULE / 'setup.sh').read_text())

    def test_non_deploy_commands_never_read_file(self):
        for command in (['status'], ['export'], ['recover'], ['backup'], ['devices', 'list']):
            with self.subTest(command=command), mock.patch.object(vps, 'base_domain', side_effect=AssertionError('read')):
                code, args = self.run_main(command)
                self.assertEqual((code, args.command), (1, command[0]))

    def test_replace_omitted_domain_uses_file_then_mismatch_refuses(self):
        saved = {'domain': 'sub.old.org', 'reality_target': 'target.example.org', 'acme_email': '', 'proxy_port': 443}
        with self.write(b'new.org'):
            code, args = self.run_main(['replace', '--ssh', 'x'])
            self.assertEqual(args.domain, 'sub.new.org')
            with self.assertRaisesRegex(host.Error, 'current sub.old.org, requested sub.new.org'):
                vps.configuration(args, saved)
            _, args = self.run_main(['replace', '--ssh', 'x', '--domain', 'sub.old.org'])
            self.assertEqual(vps.configuration(args, saved), saved)

    def test_mismatch_with_saved_deployment_is_refused_not_migrated(self):
        saved = {'domain': 'sub.old.org', 'reality_target': 'target.example.org', 'acme_email': '', 'proxy_port': 443}
        args = mock.Mock(domain='sub.new.org', reality_target=None, acme_email=None, proxy_port=None)
        with self.assertRaisesRegex(host.Error, 'current sub.old.org, requested sub.new.org.*migration is needed'):
            vps.configuration(args, saved)
        args.domain = None
        self.assertEqual(vps.configuration(args, saved), saved)


if __name__ == '__main__':
    unittest.main()
