#!/usr/bin/python3
"""Two-key native semantics; optionally compile the actual tagged parser offline.

python3 tests/binding.py --native-source /path/to/Sunshine-2026.906.222525/src/config.cpp
Without that option the same frozen native-result corpus still runs; no download,
Sunshine daemon, sockets or live systemd are needed.
"""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1]
HELPER = MODULE / 'service/check-tailnet.py'
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('binding_guard', HELPER)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
KEYS = guard.BINDING_KEYS
VALID = b'address_family = ipv4\nbind_address = 100.64.0.2\n'
# Expected RAW values, recorded from tagged parse_config, not normalized scalars.
CORPUS = {
    'plain': (VALID, (b'ipv4', b'100.64.0.2')),
    'inline-space': (VALID.replace(b'0.2\n', b'0.2 # retain\n'), (b'ipv4', b'100.64.0.2 ')),
    'inline-no-space': (VALID.replace(b'0.2\n', b'0.2#retain\n'), (b'ipv4', b'100.64.0.2')),
    'family-comment': (VALID.replace(b'ipv4\n', b'ipv4 # family\n'), (b'ipv4 ', b'100.64.0.2')),
    'trailing-space': (VALID.replace(b'0.2\n', b'0.2 \t\n'), (b'ipv4', b'100.64.0.2 \t')),
    'crlf': (VALID.replace(b'\n', b'\r\n'), (b'ipv4', b'100.64.0.2')),
    'lone-cr': (VALID.replace(b'\n', b'\r'), (b'ipv4', None)),
    'tabs': (b'\taddress_family\t=\tipv4\n bind_address=\t100.64.0.2', (b'ipv4', b'100.64.0.2')),
    'unknown-list': (b'unknown = [\n' + VALID + b']\n', (None, None)),
    'nested-list': (b'unknown = [\n[\n' + VALID + b']\n]\n', (None, None)),
    'list-then-top': (b'unknown=[\n' + VALID + b']\n' + VALID, (b'ipv4', b'100.64.0.2')),
    'bracket-in-comment': (b'unknown=[\n# [\n]\n' + VALID + b']\n', (None, None)),
    'close-in-comment': (b'unknown=[#]\n' + VALID, (b'ipv4', b'100.64.0.2')),
    'post-list-space': (b'unknown=[] ' + VALID, (b'ipv4', b'100.64.0.2')),
    'post-list-no-space': (b'unknown=[]' + VALID, (None, b'100.64.0.2')),
    'non-utf8-unrelated': (b'custom = \xff # retained\n' + VALID, (b'ipv4', b'100.64.0.2')),
}
REJECT = {
    'duplicate': (VALID + b'bind_address = 100.64.0.3\n', (b'ipv4', b'100.64.0.2')),
    'same-duplicate': (VALID + b'address_family = ipv4\n', (b'ipv4', b'100.64.0.2')),
    'malformed': (VALID + b'address_family # no equals\n', (b'ipv4', b'100.64.0.2')),
    'empty': (b'address_family =\nbind_address = 100.64.0.2\n', (None, b'100.64.0.2')),
    'relevant-list': (b'address_family = [ipv4]\nbind_address = 100.64.0.2\n', (b'[ipv4]', b'100.64.0.2')),
    'unclosed-unknown': (b'unknown = [\n' + VALID, (None, None)),
}
NATIVE_SOURCE = None
if '--native-source' in sys.argv:
    pos = sys.argv.index('--native-source')
    NATIVE_SOURCE = Path(sys.argv[pos + 1])
    del sys.argv[pos:pos + 2]


class BindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native = None
        if NATIVE_SOURCE is None:
            return
        cls.build = tempfile.TemporaryDirectory(prefix='qd-binding-native-')
        cls.addClassCleanup(cls.build.cleanup)
        source = NATIVE_SOURCE.read_text()
        # Unmodified function block; only logging and project loop macros stubbed.
        body = source[source.index('  bool endline(char ch)'):source.index('  void string_f(')]
        header = '''#include <arpa/inet.h>
#include <algorithm>
#include <functional>
#include <iostream>
#include <fstream>
#include <iterator>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#define KITTY_WHILE_LOOP(init, condition, body) for(init; condition;) body
#define TUPLE_2D(a,b,expr) auto [a,b] = expr
#define BOOST_LOG(level) std::cerr
'''
        main = '''int main(int argc, char **argv) {
 std::ifstream f(argv[1]); std::string s((std::istreambuf_iterator<char>(f)), {});
 auto vars = parse_config(s);
 for (const auto *key : {"address_family", "bind_address"}) {
  auto it = vars.find(key);
  if (it == vars.end()) std::cout << "null";
  else { std::cout << "["; bool first = true;
   for (unsigned char c : it->second) { if (!first) std::cout << ","; first=false; std::cout << unsigned(c); }
   std::cout << "]";
  }
  std::cout << "\\n";
 }
 in_addr address;
 auto it = vars.find("bind_address");
 std::cout << (it != vars.end() && inet_pton(AF_INET, it->second.c_str(), &address) == 1) << "\\n";
}
'''
        cpp = Path(cls.build.name) / 'parser.cpp'
        cpp.write_text(header + body + main)
        cls.native = Path(cls.build.name) / 'parser'
        subprocess.run(['/usr/bin/c++', '-std=c++17', str(cpp), '-o', str(cls.native)],
                       check=True, capture_output=True, text=True, timeout=60)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='qd-binding-')
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.home = root / 'home'
        self.directory = self.home / '.config/sunshine'
        self.directory.mkdir(parents=True)
        self.conf = self.directory / 'sunshine.conf'
        bin_dir = root / 'bin'
        bin_dir.mkdir()
        ip = bin_dir / 'ip'
        ip.write_text('#!/bin/sh\necho "7: tailscale0 inet 100.64.0.2/32 scope global tailscale0"\n')
        ip.chmod(0o755)
        self.env = dict(os.environ, HOME=str(self.home), PATH=f'{bin_dir}:/usr/bin:/bin')
        for key in ('CONFIGURATION_DIRECTORY', 'XDG_CONFIG_HOME', 'QD_SUNSHINE_CONFIG_DIR'):
            self.env.pop(key, None)

    def assert_native(self, data, expected):
        if self.native is None:
            return
        self.conf.write_bytes(data)
        lines = subprocess.check_output([self.native, self.conf], text=True, timeout=5).splitlines()
        actual = tuple(None if line == 'null' else bytes(json.loads(line)) for line in lines[:2])
        self.assertEqual(actual, expected)
        # Native numeric-address acceptance must not strip whitespace either.
        valid_ip = expected[1] == b'100.64.0.2'
        self.assertEqual(lines[2], str(int(valid_ip)))

    def shell(self, script, *args):
        return subprocess.run(['/bin/bash', '-c', '. "$1/lib/common.sh"; ' + script,
                               'fixture', str(MODULE), str(self.conf), *args],
                              env=self.env, capture_output=True, timeout=10)

    def test_native_scalar_and_top_level_corpus(self):
        for name, (data, expected) in CORPUS.items():
            with self.subTest(case=name):
                self.assert_native(data, expected)
                entries = guard.binding_entries(data)
                self.assertEqual(tuple(entries.get(key, (None,))[0] for key in KEYS), expected)
                self.conf.write_bytes(data)
                for key, value in zip(KEYS, expected):
                    got = self.shell('qd_conf_get "$2" "$3"', key.decode())
                    self.assertEqual(got.returncode == 0, value is not None)
                    self.assertEqual(got.stdout, b'' if value is None else value + b'\n')
                checked = subprocess.run(['/usr/bin/python3', str(HELPER), str(self.directory)],
                                         env=self.env, capture_output=True, timeout=10)
                self.assertEqual(checked.returncode == 0, expected == (b'ipv4', b'100.64.0.2'))
                self.assertEqual(self.conf.read_bytes(), data)

    def test_rewrite_then_native_readback_preserves_unknown_bytes(self):
        for name, (data, _) in CORPUS.items():
            with self.subTest(case=name):
                self.conf.write_bytes(data)
                self.conf.chmod(0o640)
                for key, value in zip(KEYS, (b'ipv4', b'100.64.0.2')):
                    old = self.conf.read_bytes()
                    entry = guard.binding_entries(old).get(key)
                    result = self.shell('qd_conf_set "$2" "$3" "$4"', key.decode(), value.decode())
                    self.assertEqual(result.returncode, 0, result.stderr)
                    updated = self.conf.read_bytes()
                    if entry:
                        _, begin, end = entry
                        self.assertTrue(updated.startswith(old[:begin]))
                        self.assertTrue(updated.endswith(old[end:]))
                        if b'#' in old[begin:end]:
                            comment = b'#' + old[begin:end].split(b'#', 1)[1]
                            self.assertIn(comment, updated.splitlines())
                    else:
                        self.assertTrue(updated.startswith(old))
                    self.assertEqual(self.conf.stat().st_mode & 0o777, 0o640)
                final = self.conf.read_bytes()
                self.assert_native(final, (b'ipv4', b'100.64.0.2'))
                self.assertEqual(tuple(guard.binding_entries(final)[k][0] for k in KEYS),
                                 (b'ipv4', b'100.64.0.2'))
                for key, value in zip(KEYS, ('ipv4', '100.64.0.2')):
                    self.assertEqual(self.shell('qd_conf_set "$2" "$3" "$4"', key.decode(), value).returncode, 0)
                self.assertEqual(self.conf.read_bytes(), final)  # repeat is byte-idempotent

    def test_isolated_installer_emits_native_exact_binding(self):
        harness = (MODULE / 'tests/run.sh').read_text().split('# Packaging: real apt suffix classification')[0]
        harness = '\n'.join('TESTS_DIR=' + shlex.quote(str(MODULE / 'tests'))
                            if line.startswith('TESTS_DIR=') else line
                            for line in harness.splitlines())
        script = harness + '''
new_case; installed
mkdir -p "$QD_SUNSHINE_CONFIG_DIR"
cp "$BINDING_INPUT" "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
run install-host.sh
[ "$RC" -eq 0 ] || { cat "$CASE/out"; exit 1; }
cp "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf" "$BINDING_OUTPUT"
python3 "$(qd_retry_dir)/check-tailnet.py" "$(qd_host_config_dir)"
end_case
'''
        fixtures = {
            'comments': (b'address_family = ipv4 # retain family\nbind_address = 100.64.0.2 # retain address\n',
                         (b'ipv4 ', b'100.64.0.2 ')),
            'list': (b'unknown = [\n' + VALID + b']\n', (None, None)),
        }
        for name, (data, native_values) in fixtures.items():
            with self.subTest(case=name):
                data += b'custom_key = custom value # untouched\n'
                self.assert_native(data, native_values)
                source = Path(self.tmp.name) / 'input.conf'
                output = Path(self.tmp.name) / 'emitted.conf'
                source.write_bytes(data)
                installed = subprocess.run(['/bin/bash', '-c', script],
                                           env=dict(self.env, BINDING_INPUT=str(source),
                                                    BINDING_OUTPUT=str(output)),
                                           capture_output=True, text=True, timeout=60)
                self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
                emitted = output.read_bytes()
                self.assert_native(emitted, (b'ipv4', b'100.64.0.2'))
                self.assertEqual(tuple(guard.binding_entries(emitted)[k][0] for k in KEYS),
                                 (b'ipv4', b'100.64.0.2'))
                self.assertIn(b'custom_key = custom value # untouched\n', emitted)
                if name == 'list':
                    self.assertTrue(emitted.startswith(data))
                else:
                    self.assertIn(b'# retain family\n', emitted)
                    self.assertIn(b'# retain address\n', emitted)

    def test_duplicate_malformed_and_ambiguous_structure_refused(self):
        for name, (data, native_values) in REJECT.items():
            with self.subTest(case=name):
                self.assert_native(data, native_values)
                self.conf.write_bytes(data)
                for script, args in [('qd_conf_get "$2" bind_address', ()),
                                     ('qd_conf_set "$2" bind_address 100.64.0.2', ())]:
                    self.assertNotEqual(self.shell(script, *args).returncode, 0)
                    self.assertEqual(self.conf.read_bytes(), data)
                checked = subprocess.run(['/usr/bin/python3', str(HELPER), str(self.directory)],
                                         env=self.env, capture_output=True, timeout=10)
                self.assertNotEqual(checked.returncode, 0)


if __name__ == '__main__':
    print('Native compiled differential: ' + (str(NATIVE_SOURCE) if NATIVE_SOURCE else
          'not requested; frozen tagged-parser corpus only'), flush=True)
    unittest.main(verbosity=2)
