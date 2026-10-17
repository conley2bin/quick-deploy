"""Optional-contact configuration and offline Caddy adaptation; no CA/network."""
import copy
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import host
import vps


class ContactConfig(unittest.TestCase):
    def args(self, *extra):
        return vps.parser().parse_args(['deploy', '--ssh', 'fixture-alias', '--domain', 'sub.example.org',
                                      '--reality-target', 'target.example.org', *extra])

    def test_fresh_cli_defaults_no_contact_without_any_email_prompt(self):
        with mock.patch('builtins.input', side_effect=AssertionError('unexpected contact prompt')), \
             mock.patch.object(sys.stdin, 'isatty', return_value=True):
            config = vps.configuration(self.args())
        self.assertEqual(config['acme_email'], '')
        self.assertEqual(config['proxy_port'], 443)
        self.assertEqual(set(config), {'domain', 'reality_target', 'acme_email', 'proxy_port'})
        host.validate_config(config)
        self.assertEqual(json.loads(host.dumps(config)), config)

    def test_explicit_and_saved_contacts_preserve_config_compatibility(self):
        config = vps.configuration(self.args('--acme-email', 'explicit-fixture@example.org'))
        self.assertEqual(config['acme_email'], 'explicit-fixture@example.org')
        old = copy.deepcopy(config)
        with mock.patch('builtins.input', side_effect=AssertionError('saved configuration prompted')):
            self.assertEqual(vps.configuration(self.args(), config), old)
        self.assertEqual(config, old)
        with self.assertRaises(host.Error):
            vps.configuration(self.args('--acme-email', ''), config)
        no_contact = dict(config, acme_email='')
        host.validate_config(no_contact)
        self.assertEqual(vps.configuration(self.args(), no_contact), no_contact)

    def test_malformed_contacts_reject_without_subprocess_or_network(self):
        config = {'domain': 'sub.example.org', 'reality_target': 'target.example.org',
                  'acme_email': '', 'proxy_port': 443}
        for email in ('not-email', 'x@', 'x@example', 'x@@example.org', 'x@example..org',
                      ' x@example.org', 'x@example.org\n', 'x@example.org {', None, False):
            with self.subTest(email=email), mock.patch.object(host, 'run', side_effect=AssertionError('subprocess')):
                with self.assertRaises(host.Error):
                    host.validate_config(dict(config, acme_email=email))


@unittest.skipUnless(os.environ.get('QD_VPS_ARTIFACTS'), 'Cached official Caddy artifact required for offline adaptation')
class CaddyContactAdaptation(unittest.TestCase):
    def test_no_contact_and_explicit_contact_use_only_pinned_letsencrypt(self):
        pin = vps.release_lock()['front']
        archive = Path(os.environ['QD_VPS_ARTIFACTS']) / pin['file']
        self.assertEqual(host.digest(archive), pin['sha256'])
        with tempfile.TemporaryDirectory(prefix='qd-acme-adapt-') as directory:
            root = Path(directory)
            binary = root / 'caddy'
            with tarfile.open(archive) as source:
                member = source.getmember('caddy')
                self.assertTrue(member.isfile())
                host.atomic(binary, source.extractfile(member).read(), 0o700)
            for contact in ('', 'explicit-fixture@example.org'):
                with self.subTest(contact=contact):
                    config = {'domain': 'sub.example.org', 'reality_target': 'target.example.org',
                              'acme_email': contact, 'proxy_port': 443}
                    manifest = host.new_manifest('fixture', config, '93.184.216.34')
                    path = root / 'Caddyfile'
                    host.atomic(path, host.caddyfile(manifest, True))
                    adapted = json.loads(host.run([binary, 'adapt', '--config', path, '--adapter', 'caddyfile']).stdout)
                    policies = adapted['apps']['tls']['automation']['policies']
                    issuers = [issuer for policy in policies for issuer in policy['issuers']]
                    self.assertEqual(len(issuers), 1, 'no automatic other-CA fallback')
                    issuer = issuers[0]
                    self.assertEqual(issuer['module'], 'acme')
                    self.assertEqual(issuer['ca'], 'https://acme-v02.api.letsencrypt.org/directory')
                    self.assertTrue(issuer['challenges']['tls-alpn']['disabled'])
                    self.assertFalse(issuer['challenges'].get('http', {}).get('disabled', False))
                    if contact:
                        self.assertEqual(issuer['email'], contact)
                    else:
                        self.assertNotIn('email', issuer)
                        self.assertNotIn('email', json.dumps(adapted))
                    servers = adapted['apps']['http']['servers']
                    self.assertEqual({addr for server in servers.values() for addr in server['listen']}, {':80', ':8443'})
                    for server in servers.values():
                        self.assertTrue(server['automatic_https']['disable_redirects'])


if __name__ == '__main__':
    unittest.main()
