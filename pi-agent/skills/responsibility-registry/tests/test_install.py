"""Run the selected-skill installer against temporary sources/targets, never HOME."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from test_registry import SCRIPT

SKILL = SCRIPT.parents[1]


class InstallTests(unittest.TestCase):
    def test_reinstall_byte_inode_preservation_missing_marker_and_legacy_block(self):
        with tempfile.TemporaryDirectory(prefix='registry-install-fixture-') as temp:
            base = Path(temp)
            source = base / 'source'
            source.mkdir()
            shutil.copyfile(SKILL.parent / 'install-skills.sh', source / 'install-skills.sh')
            shutil.copytree(SKILL, source / SKILL.name, ignore=shutil.ignore_patterns('__pycache__'))
            # Even an accidentally shipped live filename must never reach the target.
            (source / SKILL.name / 'RESPONSIBILITIES.md').write_text('FORBIDDEN synthetic source authority')
            (source / SKILL.name / 'BACKUPS.md').write_text('FORBIDDEN source backup index')
            (source / SKILL.name / 'backups').mkdir()
            (source / SKILL.name / 'backups/x.md').write_text('FORBIDDEN source snapshot')
            target = base / 'installed'
            def install():
                result = subprocess.run(['bash', str(source / 'install-skills.sh'), '--target', str(target)],
                                        input='1\n', text=True, capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            install()
            live = target / SKILL.name / 'RESPONSIBILITIES.md'
            self.assertEqual(live.read_bytes(), (SKILL / 'RESPONSIBILITIES.template.md').read_bytes())
            self.assertFalse((live.parent / 'BACKUPS.md').exists())
            self.assertFalse((live.parent / 'backups').exists())
            index = live.parent / 'BACKUPS.md'
            index.write_text('PRIVATE synthetic index remains byte exact')
            snapshots = live.parent / 'backups'
            snapshots.mkdir(mode=0o700)
            snapshot = snapshots / 'x.md'
            snapshot.write_text('PRIVATE synthetic immutable snapshot')
            protected = {p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns, p.stat().st_ctime_ns) for p in [index, snapshot]}
            private = live.parent / '.registry-private'
            self.assertTrue((private / 'document-control.json').exists())
            real_like = live.read_bytes().replace(b'Revision: 0', b'Revision: 999') + '人工未完成编辑\n'.encode()
            live.write_bytes(real_like)
            before = live.stat()
            install()
            self.assertEqual(live.read_bytes(), real_like)
            self.assertEqual({p: (p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns, p.stat().st_ctime_ns) for p in protected}, protected)
            after = live.stat()
            self.assertEqual((after.st_ino, after.st_mtime_ns, after.st_ctime_ns),
                             (before.st_ino, before.st_mtime_ns, before.st_ctime_ns))
            live.unlink()
            install()
            self.assertFalse(live.exists(), 'reinstall must preserve deliberate missing-file state')
            # Legacy-only alternate install cannot silently initialize blank Markdown.
            shutil.rmtree(private)
            private.mkdir()
            legacy = private / 'registry.json'
            legacy.write_bytes(b'{"synthetic":"legacy data remains unchanged"}')
            install()
            self.assertFalse(live.exists())
            self.assertEqual(legacy.read_bytes(), b'{"synthetic":"legacy data remains unchanged"}')
            self.assertTrue((live.parent / 'scripts/registry_document.py').exists())
            self.assertEqual((live.parent / 'RESPONSIBILITIES.template.md').read_bytes(), (SKILL / 'RESPONSIBILITIES.template.md').read_bytes())


if __name__ == '__main__':
    unittest.main()
