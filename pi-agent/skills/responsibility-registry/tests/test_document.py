"""Authority/reset/migration regressions. Every byte of state is synthetic and temporary."""
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_registry import SCRIPT, appointment, registry
import registry_document as codec

SKILL = SCRIPT.parents[1]
REPO = SKILL.parents[2]
PROJECTION = REPO / 'codoxear/owner-groups/registry_projection.py'
spec = importlib.util.spec_from_file_location('md_projection_test', PROJECTION)
projection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(projection)


def args(command='register', actor='alpha', **kwargs):
    return argparse.Namespace(command=command, actor=actor, occurred_at=None, occurrence_precision=None,
                              authority=None, **kwargs)


class DocumentFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='md-authority-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'private'
        self.document = self.base / 'skill/RESPONSIBILITIES.md'
        self.store = registry.Store(self.root, self.document)
        self.store.initialize()

    def read(self):
        return self.store.run(lambda state: copy.deepcopy(state))

    def domain(self):
        return codec.parse_document(self.document.read_text())[0]

    def token(self):
        self.read()
        return self.store.envelope['document_token']

    def register(self, payload=None, token=None):
        payload = payload or appointment()
        return self.store.run(lambda s: registry.mutate(s, args(actor=payload['owner']['id']), payload),
                              expected_document=token or self.token())

    def project(self):
        return projection.read_registry(self.root, self.document, SCRIPT.parent / 'registry_document.py')

    def migrate_fixture(self, state):
        self.document.unlink()
        (self.root / 'document-control.json').unlink()
        raw = json.dumps(state, ensure_ascii=False, indent=2).encode()
        (self.root / 'registry.json').write_bytes(raw)
        result = self.store.migrate()
        self.assertEqual(Path(result['backup']).read_bytes(), raw)
        return result


class DocumentTests(DocumentFixture):
    def test_literal_rm_cp_old_json_untouched_new_appointment_only_and_manual_relations(self):
        self.register()
        child = appointment('beta', 'child', parent='owner:alpha', path='/example/beta')
        self.register(child)
        before = self.domain()
        self.migrate_fixture(before)
        # Keep both tombstone and complete old JSON backup across the literal reset.
        legacy = (self.root / 'registry.json').read_bytes()
        old_token = self.token()
        old_era = self.store.envelope['era']
        self.assertEqual([o['id'] for o in self.project()['owners']], ['alpha', 'beta'])
        self.document.unlink()  # user's literal rm; no CLI reset command
        with self.assertRaisesRegex(registry.RegistryError, 'missing'):
            self.read()
        self.assertFalse(self.project()['ok'])
        with self.assertRaisesRegex(registry.RegistryError, 'prior initialization'):
            self.store.initialize()
        shutil.copyfile(SKILL / 'RESPONSIBILITIES.template.md', self.document)  # user's literal cp
        blank = self.read()
        for key in ('owners', 'assignments', 'coordination', 'history'):
            self.assertFalse(blank[key])
        self.assertEqual(blank['revision'], 0)
        self.assertEqual(blank['era'], 'template')
        self.assertEqual(self.project()['owners'], [])
        self.assertEqual((self.root / 'registry.json').read_bytes(), legacy)
        new = appointment('gamma', 'new-direction', path='/example/new')
        with self.assertRaisesRegex(registry.RegistryError, 'stale'):
            self.register(new, token=old_token)
        self.register(new)
        current = self.domain()
        self.assertEqual(list(current['owners']), ['gamma'])
        self.assertEqual(list(current['assignments']), ['new-direction'])
        self.assertEqual(len(current['history']), 1)
        self.assertNotEqual(self.store.envelope['era'], old_era)
        token = self.token()
        text = self.document.read_text().replace('| gamma | Example gamma | Release owner | user |',
                                                '| gamma | Manually edited name | New role | owner:unregistered |')
        self.document.write_text(text)
        edited = self.read()
        self.assertEqual(edited['owners']['gamma']['parent'], 'owner:unregistered')
        web_owner = self.project()['owners'][0]
        self.assertEqual((web_owner['name'], web_owner['role'], web_owner['parent']),
                         ('Manually edited name', 'New role', 'owner:unregistered'))
        self.assertEqual(edited['revision'], 1)  # no fabricated manual-edit attribution
        with self.assertRaisesRegex(registry.RegistryError, 'stale'):
            self.register(new, token=token)
        self.assertEqual((self.root / 'registry.json').read_bytes(), legacy)

    def test_cell_and_complete_document_roundtrip_exact_once(self):
        # Keep the original Chinese text in the exact Unicode/escaping roundtrip fixture.
        value = '  \u4e2d\u6587 | &#124; &amp; <br> \\| \\n **[\u8bcd](\u8def\u5f84)** `\u5f15\u53f7`\r\n\u4e0b\u4e00\u884c\t  '
        self.assertEqual(codec.decode_cell(codec.encode_cell(value)), value)
        self.assertIn('&#124;', codec.encode_cell(value))
        self.assertEqual(codec.decode_cell('&amp;#124;'), '&#124;')
        self.assertEqual(codec.decode_cell('&lt;br&gt;'), '<br>')
        self.assertEqual(codec.decode_cell('<br>'), '\n')
        self.assertEqual(codec.decode_cell('&#13;'), '\r')
        for invalid in ['&unknown;', '&amp', '<script>', '&', '&#0;', '&#xD800;']:
            with self.assertRaises(registry.RegistryError):
                codec.decode_cell(invalid)
        p = appointment()
        p['owner']['name'] = value
        p['assignment']['objective'] = value
        p['assignment']['acceptance'] = value
        self.register(p)
        self.assertEqual(self.domain()['owners']['alpha']['name'], value)
        self.assertEqual(self.domain()['assignments']['release']['objective'], value)
        self.assertTrue(self.project()['ok'])

    def test_strict_structure_shadows_duplicates_and_invalid_bytes_preserved(self):
        self.register()
        good = self.document.read_text()
        details = codec.encode_cell(json.dumps({'id': 'shadow'}, separators=(',', ':')))
        variants = [good.replace('Revision: 1', 'Revision: 9'), good.replace('Format: responsibility-registry/v2', 'Format: v99'),
                    good.replace('Era: ' + self.store.envelope['era'], 'Era: template'),
                    good + 'ignored table\n| stale | ghost |\n', good.replace('## Owners', '## Owners\n## Owners')]
        line = next(line for line in good.splitlines() if line.startswith('| alpha |'))
        variants += [good.replace(line, line + '\n' + line), good.replace(line, '| alpha | n | r | user | ' + details + ' |'),
                     good.replace('identity&quot;:', 'identity&quot;:null,&quot;identity&quot;:'),
                     good.replace('identity&quot;:', 'unknown&quot;:true,&quot;identity&quot;:'),
                     good.replace('identity&quot;:', 'identity&quot;:NaN,&quot;x&quot;:'),
                     good.replace('| Example alpha |', '| literal\\|error |')]
        original = self.domain()
        with self.assertRaisesRegex(registry.RegistryError, 'duplicate JSON'):
            codec.parse_json('{"same":1,"same":2}')
        with self.assertRaisesRegex(registry.RegistryError, 'nonfinite'):
            codec.parse_json('{"number":1e999}')
        for broken in variants:
            with self.subTest(broken=broken[-100:]):
                self.document.write_text(broken)
                with self.assertRaises(registry.RegistryError):
                    self.read()
                self.assertEqual(self.document.read_text(), broken)
                self.assertFalse(self.project()['ok'])
        self.document.write_text(good)
        # Deleting a referenced owner yields a diagnostic, never restores from audit.
        self.document.write_text(good.replace(line + '\n', ''))
        with self.assertRaisesRegex(registry.RegistryError, 'assignment owner'):
            self.read()
        self.assertNotIn(line, self.document.read_text())
        self.assertTrue(original['history'])

    def test_manual_scope_edit_invalidates_ack_without_updated_timestamp_change(self):
        self.register()
        proposal = dict(id='agreement', scopes={'release': ['module/**']}, shared_interfaces='schema v1',
                        workspace=dict(mode='shared-cwd', plan='one writer'), sequencing='serial',
                        integration_owner='alpha', acceptance='explicit review')
        self.store.run(lambda s: registry.mutate(s, args('coord-propose', expected_revision=1, reason='proposal'), proposal), expected_document=self.token())
        self.store.run(lambda s: registry.mutate(s, args('coord-ack', coordination='agreement', expected_revision=2, reason='explicit agreement'), None), expected_document=self.token())
        state = self.domain()
        self.assertTrue(codec.agreement_view(state, state['coordination']['agreement'])['agreement_ready'])
        self.document.write_text(self.document.read_text().replace('| Organizational goals and acceptance |', '| Manually expanded scope |'))
        state = self.domain()
        self.assertEqual(state['assignments']['release']['updated_at'], state['coordination']['agreement']['scopes']['release']['assignment_updated_at'])
        self.assertFalse(codec.agreement_view(state, state['coordination']['agreement'])['agreement_ready'])

    def test_manual_agreement_terms_changes_invalidate_acknowledgements(self):
        self.register()
        p = appointment('beta', 'frontend')
        p['assignment']['task'] = 'front'
        self.register(p)
        proposal = dict(id='terms', scopes={'release': ['x/**'], 'frontend': ['y/**']}, shared_interfaces='schema v1',
                        workspace=dict(mode='shared-cwd', plan='one writer'), sequencing='serial',
                        integration_owner='alpha', acceptance='explicit review')
        self.store.run(lambda s: registry.mutate(s, args('coord-propose', expected_revision=2, reason='proposal'), proposal), expected_document=self.token())
        for actor, revision in [('alpha', 3), ('beta', 4)]:
            self.store.run(lambda s: registry.mutate(s, args('coord-ack', actor=actor, coordination='terms', expected_revision=revision, reason='explicit consent'), None), expected_document=self.token())
        state, era = codec.parse_document(self.document.read_text())
        self.assertTrue(codec.agreement_view(state, state['coordination']['terms'])['agreement_ready'])
        for field in ['integration_owner', 'write_scope', 'shared_interfaces']:
            changed = copy.deepcopy(state)
            agreement = changed['coordination']['terms']
            if field == 'write_scope':
                agreement['scopes']['release']['write_scope'] = ['EVERYTHING/**']
            else:
                agreement[field] = 'beta' if field == 'integration_owner' else 'unacknowledged new interface'
            self.document.write_text(codec.serialize_document(changed, era))
            current = self.domain()
            self.assertIn('terms changed', ' '.join(codec.agreement_view(current, current['coordination']['terms'])['not_ready_reasons']))

    def test_manual_parent_changes_direct_superior_authorization(self):
        for oid in ['old-superior', 'new-superior', 'successor']:
            p = appointment(oid)
            p.pop('assignment')
            self.register(p)
        self.register(appointment(parent='owner:old-superior'))
        self.document.write_text(self.document.read_text().replace('| owner:old-superior |', '| owner:new-superior |'))
        def transfer(actor):
            return self.store.run(lambda state: registry.mutate(state, args('handover', actor=actor,
                assignment='release', to_owner='successor', new_assignment='next', expected_revision=4,
                reason='explicit authorized transfer', pending_work='verified none', coordination_plan=None,
                reporting_change='authorized different superior'), None), expected_document=self.token())
        with self.assertRaisesRegex(registry.RegistryError, 'actor must'):
            transfer('old-superior')
        transfer('new-superior')
        self.assertEqual(self.domain()['assignments']['next']['owner'], 'successor')

    def test_same_bytes_replacement_and_manual_edit_during_operation_refuse_stale_commit(self):
        token = self.token()
        replacement = self.document.with_name('copy.md')
        shutil.copyfile(self.document, replacement)
        replacement.replace(self.document)
        with self.assertRaisesRegex(registry.RegistryError, 'stale'):
            self.register(token=token)
        token = self.token()
        raw = self.document.read_bytes()
        def edit_during_operation(state):
            registry.mutate(state, args(), appointment())
            replacement.write_bytes(raw)
            replacement.replace(self.document)
            return {}
        with self.assertRaisesRegex(registry.RegistryError, 'before commit'):
            self.store.run(edit_during_operation, expected_document=token)
        self.assertEqual(self.document.read_bytes(), raw)
        self.assertFalse(self.domain()['owners'])

    def test_final_noncooperating_check_to_rename_race_is_an_explicit_boundary(self):
        token = self.token()
        real_replace = os.replace
        fired = []
        def noncooperating_reset(source, destination):
            if destination == self.document:
                fired.append(True)
                # This happens AFTER the final recheck, immediately before rename.
                self.document.unlink()
                shutil.copyfile(SKILL / 'RESPONSIBILITIES.template.md', self.document)
            return real_replace(source, destination)
        with patch.object(registry.os, 'replace', noncooperating_reset):
            self.register(token=token)
        self.assertEqual(fired, [True])
        self.assertEqual(list(self.domain()['owners']), ['alpha'])
        # POSIX rename cannot detect that final non-locking copy. The documented
        # operational mitigation is quiescent registry writers during literal reset.

    def test_codec_missing_or_incompatible_never_uses_json(self):
        self.register()
        (self.root / 'registry.json').write_text(json.dumps(self.domain()))
        incompatible = self.base / 'wrong.py'
        incompatible.write_text("FORMAT='unknown'\n")
        for path in [incompatible, self.base / 'missing.py']:
            result = projection.read_registry(self.root, self.document, path)
            self.assertFalse(result['ok'])
            self.assertEqual(result['owners'], [])

    def test_corrupt_control_marker_cannot_be_treated_as_fresh_or_allow_writes(self):
        raw = self.document.read_bytes()
        (self.root / 'document-control.json').write_text('null')
        with self.assertRaises(registry.RegistryError):
            self.register()
        self.document.unlink()
        with self.assertRaises(registry.RegistryError):
            self.store.initialize()
        self.assertFalse(self.document.exists())
        self.assertEqual((self.root / 'document-control.json').read_text(), 'null')
        self.assertTrue(raw)

    def test_initializer_never_refills_deliberate_delete_and_pending_recovery_is_explicit(self):
        inode = self.document.stat().st_ino
        self.store.initialize()
        self.assertEqual(self.document.stat().st_ino, inode)
        self.document.unlink()
        with self.assertRaisesRegex(registry.RegistryError, 'prior initialization'):
            self.store.initialize()
        control = self.store.control()
        control['status'] = 'pending'
        (self.root / 'document-control.json').write_text(json.dumps(control))
        with self.assertRaisesRegex(registry.RegistryError, 'missing'):
            self.read()
        self.store.recover_initialization()
        self.assertEqual(self.read()['era'], 'template')
        self.assertEqual(self.store.control()['status'], 'completed')


class MigrationTests(DocumentFixture):
    def legacy_state(self):
        # Rich synthetic current-format fixture, including recovery, omissions, handover,
        # active/end times, dependency graph, all acknowledgement outcomes and snapshots.
        for n in range(12):
            p = appointment(f'fixture-{n}', f'scope-{n}', parent='user' if n == 0 else 'owner:fixture-0', path=f'/example/project-{n}')
            p['assignment']['repo'] = {'path': '/example/shared/.git'}
            if n == 0:
                p['owner']['identity'].update(pi_session_id='synthetic-actual-pi', intercom_id='synthetic-distinct-alias',
                                            source='current-session', unknown_reason='')
                p['owner']['recovery'] = dict(session_file='/example/session.jsonl', cwd='/example', repo='/example/.git',
                    worktree='/example', summary='Handover summary', decisions='Interface decision', pending_issues='Acceptance pending', context_refs=['doc-ref'],
                    source='synthetic verified fixture', unknown_reason='')
            if n == 1:
                p['owner']['identity'].update(intercom_id='synthetic-contact-only', source='intercom-status')
            self.register(p)
        def action(command, **kwargs):
            state = self.domain()
            return self.store.run(lambda s: registry.mutate(s, args(command, actor=kwargs.pop('actor', 'fixture-0'),
                expected_revision=state['revision'], reason='synthetic semantic evidence', **kwargs), None), expected_document=self.token())
        action('handover', assignment='scope-0', to_owner='fixture-1', new_assignment='successor', pending_work='active child remains with predecessor',
               coordination_plan='renegotiate shared boundary', reporting_change='authorized new superior')
        action('close', actor='fixture-10', assignment='scope-10')
        state = self.domain()
        repo = state['assignments']['scope-2']['repo']
        for cid, stale, status in [('ready', False, 'confirmed'), ('stale', True, 'confirmed'), ('proposed', False, 'proposed'), ('released', False, 'released')]:
            # Preserve the original multilingual write-boundary data during migration.
            state['coordination'][cid] = dict(id=cid, repo=repo, scopes={'scope-2': dict(owner='fixture-2', write_scope=['\u5171\u4eab/**'],
                assignment_updated_at='old-time' if stale else state['assignments']['scope-2']['updated_at'])},
                shared_interfaces='schema 1', workspace=dict(mode='isolated-worktrees', plan='branches'), sequencing='serial integration',
                integration_owner='fixture-2', acceptance='peer review', status=status,
                acknowledgements={} if status == 'proposed' else {'fixture-2': dict(at='2026-01-01T01:02:03+08:00', evidence='explicit consent')},
                created_at='2026-01-01T01:02:03+08:00', updated_at='2026-01-01T01:02:03+08:00')
        state['revision'] += 1
        state['history'].append(dict(revision=state['revision'], id=f'event-{state["revision"]:08d}',
            at='2026-01-01T01:02:03+08:00', occurred=dict(at='2026-01-01T01:02:03+08:00', precision='exact'),
            actor='fixture-2', action='coord-propose', reason='synthetic coordination audit evidence',
            authority='synthetic explicit participant evidence', changes=[], before=dict(coordination={}),
            after=dict(coordination=copy.deepcopy(state['coordination']))))
        # Historical optional omissions must remain absent, not fabricated during migration.
        state['assignments']['scope-11'].pop('repo')
        state['assignments']['scope-11'].pop('goal_source')
        state['owners']['fixture-11'].pop('appointment_source')
        state['history'][0]['occurred'] = {'at': None, 'precision': 'unknown'}
        # Keep a free-form Chinese approximate date in full-field preservation coverage.
        state['history'][1]['occurred'] = {'at': '\u7ea62025\u5e74\u521d', 'precision': 'approximate'}
        codec.validate(state)
        return state

    def setup_legacy(self, state):
        self.document.unlink()
        (self.root / 'document-control.json').unlink()
        raw = json.dumps(state, ensure_ascii=False, indent=2).encode()
        (self.root / 'registry.json').write_bytes(raw)
        return raw

    def test_full_field_migration_and_ready_stale_preservation(self):
        state = self.legacy_state()
        expected = copy.deepcopy(state)
        for a in expected['coordination'].values():
            aid, b = next(iter(a['scopes'].items()))
            b['assignment_digest'] = None if a['id'] == 'stale' else codec.assignment_digest(state['assignments'][aid])
        for agreement in expected['coordination'].values():
            for ack in agreement['acknowledgements'].values():
                ack['terms_digest'] = codec.terms_digest(agreement)
        result = self.migrate_fixture(state)
        self.assertEqual(self.domain(), expected)  # ALL fields, history IDs/order/snapshots, not counts
        self.assertEqual(len(self.domain()['owners']), 12)
        self.assertEqual(Path(result['backup']).stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.document.stat().st_mode & 0o777, 0o600)
        self.assertEqual({cid: codec.agreement_view(self.domain(), a)['agreement_ready'] for cid, a in self.domain()['coordination'].items()},
                         {cid: codec.agreement_view(state, a, legacy=True)['agreement_ready'] for cid, a in state['coordination'].items()})
        tombstone = json.loads((self.root / 'registry.json').read_text())
        with self.assertRaises(registry.RegistryError):
            codec.validate(tombstone)  # preloaded old validator cannot write after cutover
        with self.assertRaisesRegex(registry.RegistryError, 'prior initialization'):
            self.store.migrate()

    def test_crash_resume_prepared_fenced_published_and_divergence(self):
        state = self.legacy_state()
        for phase in ['prepared', 'fenced', 'published']:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                store = registry.Store(base / 'private', base / 'skill/RESPONSIBILITIES.md')
                store.root.mkdir()
                (store.root / 'registry.json').write_text(json.dumps(state))
                def crash(at):
                    if at == phase:
                        raise OSError('synthetic power loss at ' + phase)
                with patch.object(store, 'migration_checkpoint', crash):
                    with self.assertRaisesRegex(OSError, 'power loss'):
                        store.migrate()
                self.assertEqual(store.control()['status'], 'pending')
                if phase == 'prepared':
                    # Old code advanced JSON after crash. Resume must snapshot that, not old staging.
                    advanced = copy.deepcopy(state)
                    advanced['owners']['fixture-0']['name'] = 'advanced while legacy still writable'
                    (store.root / 'registry.json').write_text(json.dumps(advanced))
                if phase == 'published':
                    with self.assertRaisesRegex(registry.RegistryError, 'pending'):
                        token = store.run(lambda s: {})['document_token']
                        store.run(lambda s: {}, expected_document=token)
                store.migrate(resume=True)
                self.assertEqual(store.control()['status'], 'completed')
                if phase == 'prepared':
                    self.assertEqual(codec.parse_document(store.document.read_text())[0]['owners']['fixture-0']['name'], advanced['owners']['fixture-0']['name'])
                    self.assertEqual(len(list(store.root.glob('legacy-*.json'))), 2)
        # A published human edit must never be overwritten by explicit resume.
        self.setup_legacy(state)
        with patch.object(self.store, 'migration_checkpoint', side_effect=lambda p: (_ for _ in ()).throw(OSError()) if p == 'published' else None):
            with self.assertRaises(OSError):
                self.store.migrate()
        edited = self.document.read_text().replace('| fixture-0 |', '| edited-id |', 1)
        self.document.write_text(edited)
        with self.assertRaisesRegex(registry.RegistryError, 'diverged'):
            self.store.migrate(resume=True)
        self.assertEqual(self.document.read_text(), edited)

    def test_legacy_dashboard_is_backed_up_and_permanently_retired(self):
        self.register()
        original = b'# old canonical report\nalpha owns release\n'
        (self.root / 'dashboard.md').write_bytes(original)
        migrated = self.migrate_fixture(self.domain())
        control = self.store.control()
        self.assertEqual(Path(control['dashboard_backup']).read_bytes(), original)
        tombstone = (self.root / 'dashboard.md').read_bytes()
        self.assertNotIn(b'alpha', tombstone)
        self.assertIn(str(self.document).encode(), tombstone)
        self.document.unlink()
        shutil.copyfile(SKILL / 'RESPONSIBILITIES.template.md', self.document)
        self.assertEqual((self.root / 'dashboard.md').read_bytes(), tombstone)
        result = subprocess.run([sys.executable, str(SCRIPT), '--root', str(self.root), '--document', str(self.document), 'rebuild'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Non-authoritative', result.stdout)
        self.assertIn('template', result.stdout)
        self.assertEqual((self.root / 'dashboard.md').read_bytes(), tombstone)

    def test_crash_before_pending_leaves_legacy_usable_and_no_auto_resume(self):
        self.register()
        raw = self.setup_legacy(self.domain())
        real_write = registry.atomic_write
        def crash_before_pending(path, content, **kwargs):
            if path.name == 'document-control.json':
                raise OSError('synthetic pre-pending crash')
            return real_write(path, content, **kwargs)
        with patch.object(registry, 'atomic_write', crash_before_pending):
            with self.assertRaisesRegex(OSError, 'pre-pending'):
                self.store.migrate()
        self.assertEqual((self.root / 'registry.json').read_bytes(), raw)
        self.assertIsNone(self.store.control())
        with self.assertRaisesRegex(registry.RegistryError, 'missing'):
            self.read()
        with self.assertRaisesRegex(registry.RegistryError, 'no pending'):
            self.store.migrate(resume=True)
        self.store.migrate()
        self.assertEqual(list(self.domain()['owners']), ['alpha'])

    def test_unexpected_live_file_after_fence_is_preserved_and_not_published_over(self):
        self.register()
        self.setup_legacy(self.domain())
        unexpected = b'human created this file during migration\n'
        def human_copy(phase):
            if phase == 'fenced':
                self.document.write_bytes(unexpected)
        with patch.object(self.store, 'migration_checkpoint', human_copy):
            with self.assertRaisesRegex(registry.RegistryError, 'unexpected document'):
                self.store.migrate()
        self.assertEqual(self.document.read_bytes(), unexpected)
        self.assertEqual(self.store.control()['status'], 'pending')
        with self.assertRaises(registry.RegistryError):
            self.store.migrate(resume=True)
        self.assertEqual(self.document.read_bytes(), unexpected)

    def test_loaded_old_writer_holds_lock_included_then_queued_old_writer_fails(self):
        self.register()
        self.setup_legacy(self.domain())
        script = '''import sys,fcntl,json
from pathlib import Path
sys.path.insert(0,sys.argv[2])
from registry_document import validate
root=Path(sys.argv[1])
print('LOADED',flush=True)
sys.stdin.readline()
with (root/'registry.lock').open('a') as lock:
 print('WAITING',flush=True)
 fcntl.flock(lock,fcntl.LOCK_EX)
 print('LOCKED',flush=True)
 state=json.loads((root/'registry.json').read_text()); validate(state)
 if sys.argv[3]=='hold':
  sys.stdin.readline()
  state['owners']['alpha']['name']='old lockholder completed'
  (root/'registry.json').write_text(json.dumps(state))
 else:
  state['owners']['alpha']['name']='DIVERGENT OLD WRITER'
  (root/'registry.json').write_text(json.dumps(state))
'''
        def old(mode):
            p = subprocess.Popen([sys.executable, '-c', script, str(self.root), str(SCRIPT.parent), mode],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(p.stdout.readline().strip(), 'LOADED')
            self.addCleanup(lambda: p.kill() if p.poll() is None else None)
            return p
        holder = old('hold')
        holder.stdin.write('go\n'); holder.stdin.flush()
        self.assertEqual(holder.stdout.readline().strip(), 'WAITING')
        self.assertEqual(holder.stdout.readline().strip(), 'LOCKED')
        queued = old('queue')
        # Migration blocks on holder; release is event driven, no timing/poll assumptions.
        import threading
        result = []
        def queued_at_fence(phase):
            if phase == 'fenced':
                queued.stdin.write('go\n'); queued.stdin.flush()
                self.assertEqual(queued.stdout.readline().strip(), 'WAITING')
        def migrate_with_queue():
            with patch.object(self.store, 'migration_checkpoint', queued_at_fence):
                result.append(self.store.migrate())
        thread = threading.Thread(target=migrate_with_queue)
        thread.start()
        holder.stdin.write('release\n'); holder.stdin.flush()
        out, err = holder.communicate(timeout=10)
        self.assertEqual(holder.returncode, 0, err)
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertTrue(result)
        out, err = queued.communicate(timeout=10)
        self.assertNotEqual(queued.returncode, 0)
        self.assertIn('RegistryError', err)
        self.assertEqual(self.domain()['owners']['alpha']['name'], 'old lockholder completed')


if __name__ == '__main__':
    unittest.main()
