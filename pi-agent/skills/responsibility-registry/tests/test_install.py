"""Run the selected-skill installer against temporary sources/targets, never HOME."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from test_registry import SCRIPT

SKILL = SCRIPT.parents[1]


class InstallTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='registry-install-fixture-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.source = self.base / 'source'
        self.source.mkdir()
        shutil.copyfile(SKILL.parent / 'install-skills.sh', self.source / 'install-skills.sh')
        self.source_skill = self.source / SKILL.name
        shutil.copytree(SKILL, self.source_skill, ignore=shutil.ignore_patterns('__pycache__'))
        self.target = self.base / 'installed'
        self.dest = self.target / SKILL.name
        self.live = self.dest / 'RESPONSIBILITIES.md'
        self.private = self.dest / '.registry-private'
        self.home = self.base / 'home'
        self.home.mkdir()

    def install(self):
        env = dict(os.environ, HOME=str(self.home), PI_AGENT_SKILLS_DIR=str(self.target),
                   PI_AGENT_SKILLS_ZSHRC=str(self.home / '.zshrc'), PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run(['bash', str(self.source / 'install-skills.sh'), '--target', str(self.target)],
                                input='1\n', text=True, capture_output=True, timeout=20, env=env,
                                cwd=self.base)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertFalse((self.home / '.pi').exists(), 'alternate target must not touch HOME skills/runtime')
        self.assertFalse((self.home / '.zshrc').exists(), 'selected skill has no API-key configuration')
        return result.stdout

    @staticmethod
    def snapshot(paths):
        result = {}
        for path in paths:
            content = os.readlink(path) if path.is_symlink() else path.read_bytes() if path.is_file() else None
            stat = path.lstat()
            result[path] = (content, stat.st_dev, stat.st_ino, stat.st_mode, stat.st_uid, stat.st_gid,
                            stat.st_nlink, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        return result

    def assert_code_updated(self):
        for name in ('SKILL.md', 'scripts/registry.py', 'scripts/registry_document.py', 'RESPONSIBILITIES.template.md'):
            self.assertEqual((self.dest / name).read_bytes(), (self.source_skill / name).read_bytes())

    def test_reinstall_byte_inode_preservation_missing_marker_and_legacy_block(self):
        # Accidentally shipped runtime must neither overwrite nor initialize active history.
        for name in ('RESPONSIBILITIES.md', 'BACKUPS.md', 'backups/x.md',
                     '.registry-private/document-control.json', '.registry-private/registry.lock',
                     '.registry-private/audit/legacy/source.md', 'audit/legacy/source.md'):
            path = self.source_skill / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('FORBIDDEN synthetic source runtime')
        first = self.install()
        self.assertIn('"initialized": true', first, 'fresh install must call the installed registry init')
        self.assertEqual(self.live.read_bytes(), (SKILL / 'RESPONSIBILITIES.template.md').read_bytes())
        self.assertFalse((self.dest / 'BACKUPS.md').exists())
        self.assertFalse((self.dest / 'backups').exists())
        self.assertFalse((self.dest / 'audit').exists())
        self.assertFalse((self.private / 'audit/legacy/source.md').exists())
        index = self.dest / 'BACKUPS.md'
        index.write_text('PRIVATE synthetic index remains byte exact')
        snapshots = self.dest / 'backups'
        snapshots.mkdir(mode=0o700)
        snapshot = snapshots / 'x.md'
        snapshot.write_text('PRIVATE synthetic immutable snapshot')
        self.assertTrue((self.private / 'document-control.json').exists())
        # Preserve original multibyte unfinished-edit bytes, not only valid documents.
        real_like = self.live.read_bytes().replace(b'Revision: 0', b'Revision: 999') + '\u4eba\u5de5\u672a\u5b8c\u6210\u7f16\u8f91\n'.encode()
        self.live.write_bytes(real_like)
        paths = [self.live, index, snapshots, snapshot, self.private, *self.private.rglob('*')]
        protected = self.snapshot(paths)
        self.install()
        self.assertEqual(self.snapshot(paths), protected)
        self.live.unlink()
        self.install()
        self.assertFalse(self.live.exists(), 'reinstall must preserve deliberate missing-file state')
        # Legacy-only alternate install cannot silently initialize blank Markdown.
        shutil.rmtree(self.private)
        self.private.mkdir()
        legacy = self.private / 'registry.json'
        legacy.write_bytes(b'{"synthetic":"legacy data remains unchanged"}')
        protected = self.snapshot([self.private, legacy])
        self.install()
        self.assertFalse(self.live.exists())
        self.assertEqual(self.snapshot([self.private, legacy]), protected)
        self.assert_code_updated()

    def test_audit_only_evidence_never_invokes_registry_or_recreates_markdown(self):
        # An unexpected CLI call fails even if a future init learns to preserve audit.
        (self.source_skill / 'scripts/registry.py').write_text('raise AssertionError("installer must only copy code")\n')
        self.private.mkdir(parents=True, mode=0o700)
        audit = self.private / 'audit'
        for shape in ('empty-directory', 'head.json', 'ops/attempt.prepare.json', 'legacy/raw.md',
                      'pending.json', 'regular-file', 'dangling-symlink'):
            with self.subTest(audit_evidence=shape):
                if audit.is_symlink() or audit.is_file():
                    audit.unlink()
                elif audit.exists():
                    shutil.rmtree(audit)
                if shape == 'regular-file':
                    audit.write_bytes(b'PRIVATE synthetic damaged audit evidence')
                elif shape == 'dangling-symlink':
                    audit.symlink_to('missing-synthetic-audit-target')
                else:
                    audit.mkdir(mode=0o700)
                    if shape != 'empty-directory':
                        evidence = audit / shape
                        evidence.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        evidence.write_bytes(b'PRIVATE synthetic audit evidence')
                        evidence.chmod(0o600)
                paths = [self.private, audit, *([] if audit.is_symlink() or not audit.is_dir() else audit.rglob('*'))]
                protected = self.snapshot(paths)
                self.assertIn('preserving runtime state', self.install())
                self.assertEqual(self.snapshot(paths), protected)
                self.assertFalse(self.live.exists())
                self.assertEqual(sorted(self.private.iterdir()), [audit], 'must not create lock/control or other runtime')
                self.assert_code_updated()

    def test_split_runtime_and_live_evidence_survive_polluted_source(self):
        (self.source_skill / 'scripts/registry.py').write_text('raise AssertionError("installer must only copy code")\n')
        names = ('RESPONSIBILITIES.md', 'BACKUPS.md', 'backups/snapshot.md',
                 '.registry-private/registry.lock', '.registry-private/document-control.json',
                 '.registry-private/switch-pending.json', '.registry-private/audit/head.json',
                 '.registry-private/audit/pending.json', '.registry-private/audit/ops/attempt.prepare.json',
                 '.registry-private/audit/ops/attempt.outcome.json',
                 '.registry-private/audit/legacy/raw.md', '.registry-private/audit/legacy/manifest.json')
        for name in names:
            path = self.dest / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_bytes(('PRIVATE synthetic target ' + name + '\n').encode())
            path.chmod(0o600)
            polluted = self.source_skill / name
            polluted.parent.mkdir(parents=True, exist_ok=True)
            polluted.write_bytes(b'FORBIDDEN synthetic source runtime')
        for name in ('.registry-private/audit/ops/source-only.prepare.json', 'audit/ops/source-only.prepare.json'):
            polluted = self.source_skill / name
            polluted.parent.mkdir(parents=True, exist_ok=True)
            polluted.write_bytes(b'FORBIDDEN extra source audit')
        (self.dest / 'RESPONSIBILITIES.template.md').write_text('obsolete synthetic code template')
        paths = [self.live, self.dest / 'BACKUPS.md', self.dest / 'backups', self.dest / 'backups/snapshot.md',
                 self.private, *self.private.rglob('*')]
        protected = self.snapshot(paths)
        runtime_names = sorted(str(p.relative_to(self.private)) for p in self.private.rglob('*'))
        self.assertIn('preserving runtime state', self.install())
        self.assertEqual(self.snapshot(paths), protected)
        self.assertEqual(sorted(str(p.relative_to(self.private)) for p in self.private.rglob('*')), runtime_names)
        self.assertFalse((self.dest / 'audit').exists(), 'source audit must not become active history')
        self.assert_code_updated()


if __name__ == '__main__':
    unittest.main()
