"""Pure current-view/audit boundary tests; only synthetic, in-memory documents."""
import copy
import io
import json
import unittest
from unittest.mock import patch

from test_codec import codec


ERA = '12345678-1234-4234-8234-123456789abc'
CURRENT_KEYS = {'version', 'revision', 'owners', 'assignments', 'coordination'}


def fixture():
    timestamp = '2026-01-01T01:02:03+08:00'
    owners = {}
    for oid, parent in [('alpha', 'user'), ('beta', 'owner:alpha')]:
        owners[oid] = dict(id=oid, name='Example ' + oid, role='Release owner', parent=parent,
                           identity=dict(pi_session_id='synthetic-pi-' + oid, intercom_id='synthetic-contact-' + oid,
                                         source='current-session', unknown_reason=''),
                           created_at=timestamp, updated_at=timestamp)
    assignment = dict(id='release', owner='alpha', status='active',
                      project=dict(id='project', name='Example project', path='/example/project'),
                      repo=dict(id='repo', path='/example/project/.git'),
                      goal_source=dict(issuer='user', source='Synthetic explicit appointment'),
                      task='release', objective='Deliver the agreed scope', acceptance='Explicit user acceptance',
                      depends_on=[], appointment='Synthetic appointment', created_at=timestamp, updated_at=timestamp,
                      end_reason=None, successor=None)
    agreement = dict(id='agreement', repo=assignment['repo'],
                     scopes={'release': dict(owner='alpha', write_scope=['module/**'], assignment_updated_at=timestamp)},
                     shared_interfaces='schema v1', workspace=dict(mode='isolated-worktrees', plan='one branch per writer'),
                     sequencing='serial integration', integration_owner='alpha', acceptance='Peer review',
                     status='proposed', acknowledgements={}, created_at=timestamp, updated_at=timestamp)
    history = [dict(revision=n, id=f'event-{n:08d}', at=timestamp,
                    occurred=dict(at=timestamp, precision='exact'), authority='Synthetic user evidence', changes=[],
                    actor='alpha', action='register', reason='Synthetic appointment', before={}, after={})
               for n in (1, 2)]
    return dict(version=1, revision=2, owners=owners, assignments={'release': assignment},
                coordination={'agreement': agreement}, history=history)


def current(state):
    return {key: value for key, value in state.items() if key != 'history'}


class CurrentViewTests(unittest.TestCase):
    def setUp(self):
        self.state = fixture()
        self.document = codec.serialize_document(self.state, ERA)
        self.prefix = self.document.split(codec.HISTORY + '\n', 1)[0] + codec.HISTORY + '\n'

    def test_exact_heading_is_consumed_but_next_is_never_requested(self):
        consumed = []
        def guarded_lines():
            for line in self.prefix.splitlines(keepends=True):
                consumed.append(line)
                yield line
            raise AssertionError('History body was requested')
        result = codec.parse_current_document(guarded_lines())
        self.assertEqual(result, (current(self.state), ERA))
        self.assertEqual(consumed[-1], codec.HISTORY + '\n')
        self.assertEqual(len(consumed), len(self.prefix.splitlines()))
        self.assertEqual(set(result[0]), CURRENT_KEYS)
        self.assertNotIn('history', result[0])
        # A current view must not be mistaken for a valid full-state envelope.
        with self.assertRaises(codec.RegistryError):
            codec.validate(result[0])
        with self.assertRaises(codec.RegistryError):
            codec.serialize_document(*result)

    def test_enormous_generated_audit_is_neither_allocated_nor_consumed(self):
        audit_reads = []
        def enormous_document():
            yield from self.prefix.splitlines(keepends=True)
            for _ in range(10**12):
                audit_reads.append(True)
                yield 'x' * 4096 + '\n'
        self.assertEqual(codec.parse_current_document(enormous_document()), (current(self.state), ERA))
        self.assertEqual(audit_reads, [])

    def test_binary_per_line_decoding_does_not_decode_invalid_utf8_audit(self):
        raw = self.prefix.encode('utf-8') + b'```json\n[\xff]\n```\n'
        binary = io.BytesIO(raw)
        decoded = []
        def decoded_lines(source):
            for line in source:
                decoded.append(line)
                yield line.decode('utf-8')
        self.assertEqual(codec.parse_current_document(decoded_lines(binary)), (current(self.state), ERA))
        self.assertEqual(decoded[-1], (codec.HISTORY + '\n').encode('utf-8'))
        self.assertEqual(binary.tell(), len(self.prefix.encode('utf-8')))
        # The full text load still rejects invalid UTF-8 before invoking its codec.
        with self.assertRaises(UnicodeDecodeError):
            codec.parse_document(raw.decode('utf-8'))
        # TextIOWrapper can decode the bad audit while returning its first line.
        with io.TextIOWrapper(io.BytesIO(raw), encoding='utf-8') as text_stream:
            with self.assertRaises(UnicodeDecodeError):
                next(text_stream)
        bad_prefix = raw.replace(b'| Example alpha |', b'| \xff |')
        with self.assertRaises(UnicodeDecodeError):
            codec.parse_current_document(decoded_lines(io.BytesIO(bad_prefix)))

    def test_normal_full_and_current_fields_match_for_line_sources(self):
        full, era = codec.parse_document(self.document)
        without_instructions = self.document
        for instruction in codec.INSTRUCTIONS:
            without_instructions = without_instructions.replace(instruction + '\n', '')
        padded = []
        for line in self.document.splitlines():
            if line.startswith('| ---'):
                line = '|' + '|'.join(':---:' for _ in line[1:-1].split('|')) + '|'
            elif line.startswith('|'):
                line = '|' + '|'.join('    ' + cell.strip() + '    ' for cell in line[1:-1].split('|')) + '|'
            padded.append(line + '  \t' if line.startswith('|') else line)
        for source in (self.document.split('\n'), self.document.splitlines(keepends=True),
                       io.StringIO(self.document), io.StringIO(self.document.replace('\n', '\r\n')),
                       io.StringIO(self.document.replace('\n', '\n\n')), io.StringIO(without_instructions),
                       io.StringIO('\r\n'.join(padded))):
            with self.subTest(source=type(source).__name__):
                view, view_era = codec.parse_current_document(source)
                self.assertEqual((view, view_era), (current(full), era))
                self.assertIsNone(codec.validate_current(view))
                self.assertIsNone(codec.validate_current_envelope(view, era))

    def test_template_is_wholly_empty_at_zero_revision(self):
        state = dict(version=1, revision=0, owners={}, assignments={}, coordination={}, history=[])
        document = codec.serialize_document(state, 'template')
        self.assertEqual(codec.parse_current_document(iter(document.splitlines())), (current(state), 'template'))
        # The audit may be absent after its heading even for the template view.
        prefix = document.split(codec.HISTORY, 1)[0] + codec.HISTORY
        self.assertEqual(codec.parse_current_document(iter(prefix.splitlines())), (current(state), 'template'))
        for bad in (document.replace('Revision: 0', 'Revision: 1'), self.document.replace(ERA, 'template')):
            with self.subTest(bad=bad[:100]), self.assertRaises(codec.RegistryError):
                codec.parse_current_document(iter(bad.splitlines()))

    def test_whole_text_bytes_and_non_line_items_are_rejected(self):
        for source in (self.document, self.document.encode(), [b'# Responsibility registry\n'], [None],
                       [self.prefix], [1], None, 1):
            with self.subTest(source=type(source).__name__), self.assertRaises(codec.RegistryError):
                codec.parse_current_document(source)

    def test_valid_prefix_allows_absent_corrupt_and_invalid_audit_only_in_current_view(self):
        bad_events = copy.deepcopy(self.state['history'])
        bad_events[0]['id'] = 'event-00000002'
        bad_time = copy.deepcopy(self.state['history'])
        bad_time[0]['occurred'] = dict(at='no timezone', precision='exact')
        bad_changes = copy.deepcopy(self.state['history'])
        bad_changes[0]['changes'] = [dict(summary='missing semantic fields')]
        tails = ['', '```json\n[', '```json\n[]\n', '```json\n{}\n```\n',
                 '```json\n[]\n```\n', '```json\n[{"x":1,"x":2}]\n```\n',
                 '```json\n[NaN]\n```\n', '```json\n[1e999]\n```\n',
                 '```json\n' + json.dumps(self.state['history'][::-1]) + '\n```\n',
                 '```json\n' + json.dumps(bad_events) + '\n```\n',
                 '```json\n' + json.dumps(bad_time) + '\n```\n',
                 '```json\n' + json.dumps(bad_changes) + '\n```\n',
                 self.document[len(self.prefix):] + 'unexpected trailing structure\n']
        for tail in tails:
            with self.subTest(tail=tail[:80]):
                document = self.prefix + tail
                self.assertEqual(codec.parse_current_document(io.StringIO(document)), (current(self.state), ERA))
                with self.assertRaises(codec.RegistryError):
                    codec.parse_document(document)

    def test_invalid_current_structure_and_strict_json_fail_in_both_views(self):
        row = next(line for line in self.document.splitlines() if line.startswith('| alpha |'))
        variants = [self.document.replace(codec.HISTORY, '## History'),
                    self.document.replace(codec.HISTORY, codec.HISTORY + ' '),
                    self.document.split(codec.HISTORY, 1)[0],
                    self.document.replace('## Owners', 'Unknown prose\n## Owners'),
                    self.document.replace(codec.HISTORY, 'Unknown prose\n' + codec.HISTORY),
                    self.document.replace('## Owners', '## Owners\n## Owners'),
                    self.document.replace('## Assignments', '## Coordination'),
                    self.document.replace('| ID | Name |', '| ID | Alias |'),
                    self.document.replace('| --- | --- | --- | --- | --- |', '| -- | --- | --- | --- | --- |', 1),
                    self.document.replace('Revision: 2', 'Revision: 02'),
                    self.document.replace('Format: ' + codec.FORMAT, 'Format: unsupported'),
                    self.document.replace(ERA, ERA.upper()),
                    self.document.replace(row, row + '\n' + row),
                    self.document.replace('| Example alpha |', '| broken\\|pipe |'),
                    self.document.replace('| Example alpha |', '| &unknown; |'),
                    self.document.replace('| Example alpha |', '| &#0; |'),
                    self.document.replace('| Example alpha |', '| &#xD800; |'),
                    self.document.replace('| Example alpha |', '| <script> |'),
                    self.document.replace('identity&quot;:', 'identity&quot;:null,&quot;identity&quot;:'),
                    self.document.replace('identity&quot;:', 'unknown&quot;:true,&quot;identity&quot;:'),
                    self.document.replace('identity&quot;:', 'identity&quot;:NaN,&quot;x&quot;:'),
                    self.document.replace('identity&quot;:', 'identity&quot;:1e999,&quot;x&quot;:'),
                    self.document.replace('identity&quot;:', 'id&quot;:&quot;shadow&quot;,&quot;identity&quot;:'),
                    self.document.replace('project&quot;:{', 'project&quot;:{&quot;id&quot;:&quot;shadow&quot;,')]
        for document in variants:
            with self.subTest(document=document[:80]):
                for parser, source in ((codec.parse_document, document),
                                       (codec.parse_current_document, io.StringIO(document))):
                    with self.assertRaises(codec.RegistryError):
                        parser(source)

    def test_current_domain_invariants_are_shared_with_full_validation(self):
        def invalid_identity(state):
            state['owners']['alpha']['identity']['source'] = 'unavailable'
        def cycle(state):
            state['owners']['alpha']['parent'] = 'owner:beta'
        def duplicate_lead(state):
            extra = copy.deepcopy(state['assignments']['release'])
            extra['id'] = 'conflicting'
            state['assignments']['conflicting'] = extra
        def lifecycle(state):
            state['assignments']['release']['status'] = 'ended'
        def successor(state):
            state['assignments']['release']['successor'] = 'missing'
        def orphan(state):
            del state['owners']['alpha']
        def time(state):
            state['assignments']['release']['start_time'] = dict(at='2026-01-01', precision='exact')
        def coordination(state):
            state['coordination']['agreement']['status'] = 'confirmed'
        def integration_owner(state):
            state['coordination']['agreement']['integration_owner'] = 'beta'
        def recovery(state):
            state['owners']['alpha']['recovery'] = {'session_file': 'relative'}
        for mutate in (invalid_identity, cycle, duplicate_lead, lifecycle, successor, orphan,
                       time, coordination, integration_owner, recovery):
            state = copy.deepcopy(self.state)
            mutate(state)
            # Produce invalid input without weakening either parser under test.
            with patch.object(codec, 'validate_envelope'):
                document = codec.serialize_document(state, ERA)
            with self.subTest(invariant=mutate.__name__):
                for validator, value in ((codec.validate_current, current(state)), (codec.validate, state)):
                    with self.assertRaises(codec.RegistryError):
                        validator(value)
                with self.assertRaises(codec.RegistryError):
                    codec.parse_current_document(io.StringIO(document))
                with self.assertRaises(codec.RegistryError):
                    codec.parse_document(document)

    def test_current_schema_has_exact_fields_and_integer_revision_without_audit_claim(self):
        state = current(self.state)
        state['revision'] = 10**30  # Deliberately unrelated to unexamined audit length.
        self.assertIsNone(codec.validate_current(state))
        for mutation in ({**state, 'history': []}, {k: v for k, v in state.items() if k != 'coordination'},
                         {**state, 'revision': True}, {**state, 'revision': -1}, {**state, 'version': True}):
            with self.subTest(fields=mutation.keys()), self.assertRaises(codec.RegistryError):
                codec.validate_current(mutation)

    def test_unicode_escaped_metadata_and_structural_crlf_are_preserved_once(self):
        value = '  \u4e2d\u6587 | &#124; & <br> \\| \\n **[\u8bcd](\u8def\u5f84)** `\u5f15\u53f7`\r\n\u4e0b\u4e00\u884c\t  '
        state = copy.deepcopy(self.state)
        state['owners']['alpha']['name'] = value
        state['assignments']['release']['objective'] = value
        state['coordination']['agreement']['shared_interfaces'] = value
        document = codec.serialize_document(state, ERA).replace('\n', '\r\n')
        view, era = codec.parse_current_document(io.StringIO(document))
        self.assertEqual((view, era), (current(state), ERA))
        self.assertEqual(current(codec.parse_document(document)[0]), view)


if __name__ == '__main__':
    unittest.main()
