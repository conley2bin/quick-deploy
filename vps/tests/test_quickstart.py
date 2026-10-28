"""Keep the operator recipes executable against the real CLI parser, offline."""
from pathlib import Path
import re
import shlex
import string
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import vps


class Quickstart(unittest.TestCase):
    def test_documented_shell_recipes_and_nondefault_identity(self):
        module = Path(__file__).resolve().parents[1]
        variables = {'HOME': '/fixture-home'}
        parsed = []
        for filename in ('README.md', 'DESIGN.md'):
            text = (module / filename).read_text()
            for block in re.findall(r'```bash\n(.*?)```', text, re.S):
                for line in block.replace('\\\n', '').splitlines():
                    tokens = shlex.split(string.Template(line).safe_substitute(variables), comments=True)
                    if not tokens:
                        continue
                    if len(tokens) == 1 and '=' in tokens[0]:
                        key, value = tokens[0].split('=', 1)
                        variables[key] = value
                    elif tokens[:2] == ['bash', 'vps/setup.sh']:
                        args = vps.parser().parse_args(tokens[2:])
                        parsed.append(args)
                        if args.command in ('preflight', 'deploy') and args.domain:
                            vps.configuration(args)
            if filename == 'README.md':
                identity_flags = re.search(r'`(--port 2222 --identity .*?)`', text).group(1)
                flags = shlex.split(string.Template(identity_flags).substitute(variables))
                for command in ('preflight', 'deploy'):
                    args = vps.parser().parse_args([command, '--ssh', variables['VPS_SSH'], *flags])
                    with mock.patch.object(vps.host, 'run', return_value=subprocess.CompletedProcess([], 0,
                                           b'hostname 93.184.216.34\nuser root\nport 2222\n')):
                        record = vps.target_record(args)
                    self.assertEqual(record['port'], 2222)
                    self.assertIn('/fixture-home/.ssh/vps_ed25519', record['options'])
        commands = [args.command for args in parsed]
        self.assertEqual(commands[:4], ['preflight', 'deploy', 'cdn', 'replace'])
        cdn = next(args for args in parsed if args.command == 'cdn')
        self.assertEqual(cdn.cdn_domain, 'cdn.example.org')
        self.assertFalse(hasattr(cdn, 'domain'))
        self.assertEqual(cdn.reality_port, 9443)
        explicit = vps.parser().parse_args(['cdn', '--domain', 'cdn.example.org', '--reality-port', '10443',
                                           '--interface', 'eno1', '--dns-server', '203.0.113.2'])
        self.assertEqual(explicit.reality_port, 10443)
        self.assertIs(type(explicit.reality_port), int)
        self.assertEqual((cdn.interface, cdn.dns_server, cdn.mihomo),
                         ('eno1', '203.0.113.2', '/path/to/mihomo'))
        for required in ('status', 'backup', 'recover', 'restore'):
            self.assertIn(required, commands)
        devices = [args for args in parsed if args.command == 'devices']
        self.assertEqual([args.device_action for args in devices], ['list', 'add', 'rename', 'reset', 'remove'])
        self.assertEqual(devices[1].names, ['device4', 'device5'])
        self.assertEqual(devices[2].names, ['device4', 'tablet'])
        self.assertEqual(devices[3].names, ['tablet'])
        self.assertEqual(devices[4].names, ['device5'])
        for action in ('rename', 'reset'):
            with mock.patch('sys.stdout'):
                with self.assertRaises(SystemExit) as exit:
                    vps.parser().parse_args(['devices', action, '--help'])
                self.assertEqual(exit.exception.code, 0)


if __name__ == '__main__':
    unittest.main()
