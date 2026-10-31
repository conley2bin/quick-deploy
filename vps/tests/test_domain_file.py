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
    def write(self, data):
        path = Path(tempfile.mkdtemp()) / 'domain.txt'
        path.write_bytes(data)
        return mock.patch.object(vps, 'DOMAIN_FILE', path)

    def test_shipped_file_is_single_public_base(self):
        self.assertEqual((vps.MODULE / 'domain.txt').read_bytes(), b'conley2bin.com\n')
        self.assertEqual(vps.default_domain('sub'), 'sub.conley2bin.com')
        self.assertEqual(vps.default_domain('cdn'), 'cdn.conley2bin.com')

    def test_lf_crlf_and_no_newline_accepted_and_cwd_independent(self):
        old = os.getcwd()
        try:
            os.chdir(tempfile.mkdtemp())
            self.assertEqual(vps.base_domain(), 'conley2bin.com')
        finally:
            os.chdir(old)
        for data in (b'Example.org\r\n', b'example.org\n', b'example.org'):
            with self.subTest(data=data), self.write(data):
                self.assertEqual(vps.default_domain('sub'), 'sub.example.org')

    def test_invalid_contents_rejected(self):
        for data in (b'', b'\n', b'\n\n', b'a.example.org\nb.example.org\n', b'example.org\n\n', b'example.org\r\r\n',
                     b' example.org\n', b'example.org \n', b'example.org\t\n', b'# c\nexample.org\n', b'example.org # c\n',
                     b'1.2.3.4\n', b'127.0.0.1\n', b'example.123\n', b'https://example.org\n', b'example.org/x\n',
                     b'example.org:443\n', b'user@example.org\n', b'localhost\n', b'exa mple.org\n', b'-a.example.org\n',
                     b'example..org\n', b'\xc3\xa9.org\n', b'a' * 64 + b'.org\n'):
            with self.subTest(data=data), self.write(data):
                with self.assertRaises(host.Error):
                    vps.base_domain()

    def test_missing_file_clear_error(self):
        with mock.patch.object(vps, 'DOMAIN_FILE', Path('/nonexistent/domain.txt')):
            with self.assertRaisesRegex(host.Error, '--domain'):
                vps.base_domain()

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
        with self.write(b'example.net\n'):
            code, args = self.run_main(base)
            self.assertEqual(args.domain, 'sub.example.net')
            code, args = self.run_main(base + ['--domain', 'sub.other.org'])
            self.assertEqual(args.domain, 'sub.other.org')
        with self.write(b'bad domain\n'):
            code, args = self.run_main(base + ['--domain', 'sub.other.org'])
            self.assertEqual(args.domain, 'sub.other.org')
            self.assertEqual(self.run_main(base)[0], 1)

    def test_deploy_config_uses_default(self):
        with self.write(b'example.net\n'):
            code, args = self.run_main(['preflight', '--ssh', 'x', '--reality-target', 'target.example.org'])
        with mock.patch('sys.stdin.isatty', return_value=False):
            self.assertEqual(vps.configuration(args)['domain'], 'sub.example.net')

    def test_cli_default_keeps_file_error_not_too_long(self):
        for command in (['deploy', '--ssh', 'x', '--reality-target', 't.example.org'],
                        ['preflight', '--ssh', 'x', '--reality-target', 't.example.org'],
                        ['cdn', '--interface', 'eth0', '--dns-server', '1.1.1.1']):
            for data in (None, b'', b'a.example.org\nb\n', b'https://example.org\n'):
                with self.subTest(command=command[0], data=data):
                    path = Path(tempfile.mkdtemp()) / 'domain.txt'
                    if data is not None:
                        path.write_bytes(data)
                    with mock.patch.object(vps, 'DOMAIN_FILE', path), mock.patch('sys.stderr') as err:
                        self.assertEqual(vps.main(['--state-dir', tempfile.mkdtemp(), *command]), 1)
                    message = ''.join(c.args[0] for c in err.write.call_args_list)
                    self.assertNotIn('too long', message)
                    self.assertRegex(message, 'missing or unreadable|exactly one base DNS domain')

    def test_too_long_derived_host_reports_length(self):
        base = '.'.join(['a' * 61] * 3 + ['b' * 60, 'org'])  # 250 chars, valid alone; sub.<base> exceeds 253
        self.assertEqual(len(base), 250)
        with self.write(base.encode() + b'\n'):
            self.assertEqual(vps.base_domain(), base)
            with self.assertRaisesRegex(host.Error, 'too long'):
                vps.default_domain('sub')

    def test_cdn_role_default_and_explicit(self):
        base = ['cdn', '--interface', 'eth0', '--dns-server', '1.1.1.1']
        with self.write(b'example.net\n'):
            self.assertEqual(self.run_main(base)[1].cdn_domain, 'cdn.example.net')
            self.assertEqual(self.run_main(base + ['--domain', 'c.other.org'])[1].cdn_domain, 'c.other.org')
        with self.write(b''):
            self.assertEqual(self.run_main(base + ['--domain', 'c.other.org'])[1].cdn_domain, 'c.other.org')
            self.assertEqual(self.run_main(base)[0], 1)

    def test_non_deploy_commands_never_read_file(self):
        for command in (['status'], ['export'], ['recover'], ['backup'], ['devices', 'list']):
            with self.subTest(command=command), mock.patch.object(vps, 'base_domain', side_effect=AssertionError('read')):
                code, args = self.run_main(command)
                self.assertEqual((code, args.command), (1, command[0]))

    def test_replace_omitted_domain_uses_file_then_mismatch_refuses(self):
        saved = {'domain': 'sub.old.org', 'reality_target': 'target.example.org', 'acme_email': '', 'proxy_port': 443}
        with self.write(b'new.org\n'):
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
