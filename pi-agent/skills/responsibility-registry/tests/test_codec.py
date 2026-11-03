"""Pure document contract; no Store/filesystem/runtime discovery."""
import copy
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/registry_document.py'
spec = importlib.util.spec_from_file_location('codec_standalone', SCRIPT)
codec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(codec)


class CodecTests(unittest.TestCase):
    def test_v2_blank_is_exact_valid_legacy_document(self):
        # Frozen legacy fixture, independent of the default v3 source template.
        legacy = '''# Responsibility registry

Format: responsibility-registry/v2
Era: template
Revision: 0

Edit the visible rows. Details are additional fields, never duplicate columns.
To start over: delete this live file, then copy RESPONSIBILITIES.template.md here.
Reset changes registered authority; it does not stop running agents.
Parent values: user or owner:ID (bare stable ID). Use the CLI to appoint after a template reset.
Manual deletion loses this file and its history. Prefer agent-managed clear when a complete backup matters.

## Owners
| ID | Name | Role | Parent | Details (JSON) |
| --- | --- | --- | --- | --- |

## Assignments
| ID | Owner | Status | Project ID | Task | Objective | Acceptance | Details (JSON) |
| --- | --- | --- | --- | --- | --- | --- | --- |

## Coordination
| ID | Status | Integration owner | Details (JSON) |
| --- | --- | --- | --- |

## History (audit only; never restores current rows)
```json
[]
```
'''
        state = dict(version=1, revision=0, owners={}, assignments={}, coordination={}, history=[])
        document = codec.serialize_document(state, 'template')
        self.assertEqual(document, legacy)
        self.assertEqual(codec.parse_document(legacy), (state, 'template'))
        self.assertEqual(codec.parse_document(document.replace('\n', '\n\n')), (state, 'template'))
        for bad in [document + 'extra structure', document.replace('Revision: 0', 'Revision: 1'),
                    document.replace('Format: responsibility-registry/v2', 'Format: unsupported')]:
            with self.assertRaises(codec.RegistryError):
                codec.parse_document(bad)

    def test_editor_table_padding_separators_crlf_and_trailing_space(self):
        state = dict(version=1, revision=0, owners={}, assignments={}, coordination={}, history=[])
        content = codec.serialize_document(state, 'template')
        lines = []
        for line in content.splitlines():
            if line.startswith('| ---'):
                line = '|' + '|'.join(':---:' for _ in line[1:-1].split('|')) + '|'
            elif line.startswith('|'):
                line = '|' + '|'.join('    ' + c.strip() + '    ' for c in line[1:-1].split('|')) + '|'
            lines.append(line + '  \t' if line.startswith('|') else line)
        formatted = '\r\n'.join(lines) + '\r\n'
        self.assertEqual(codec.parse_document(formatted), (state, 'template'))

    def test_one_pass_cells_and_json(self):
        # Keep the original Chinese text in the exact Unicode/escaping roundtrip fixture.
        value = '  \u4e2d\u6587 | &#124; & <br> \\| \\n **[\u8bcd](\u8def\u5f84)** `\u5f15\u53f7`\r\n\u4e0b\u4e00\u884c\t  '
        self.assertEqual(codec.decode_cell(codec.encode_cell(value)), value)
        self.assertEqual(codec.decode_cell('&amp;#124;'), '&#124;')
        self.assertEqual(codec.decode_cell('&lt;br&gt;'), '<br>')
        for bad in ['&unknown;', '<script>', '&#0;', '&#xD800;']:
            with self.assertRaises(codec.RegistryError):
                codec.decode_cell(bad)
        for bad in ['{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}']:
            with self.assertRaises(codec.RegistryError):
                codec.strict_json(bad)


class EventValidationTests(unittest.TestCase):
    def legacy_event(self, revision=1):
        return dict(revision=revision, at='Legacy recorded time', actor='user', action='register',
                    reason='Synthetic authorization', before={}, after={'owners': {'legacy': {'raw': True}}})

    def full_state(self, event):
        return dict(version=1, revision=1, owners={}, assignments={}, coordination={}, history=[event])

    def test_legacy_shape_and_optional_fields_keep_original_behavior(self):
        event = self.legacy_event()
        for optional in ({}, dict(occurred='legacy opaque value', authority={'legacy': True}, changes='legacy opaque value')):
            candidate = {**event, **optional}
            self.assertIsNone(codec.validate_event(candidate, 1))
            self.assertIsNone(codec.validate(self.full_state(candidate)))
        later = self.legacy_event(123)
        self.assertIsNone(codec.validate_event(later, 123))
        with self.assertRaises(codec.RegistryError):
            codec.validate(self.full_state(later))

    def test_modern_fields_and_full_validation_share_rejections(self):
        event = {**self.legacy_event(), 'id': 'event-00000001', 'at': '2026-01-01T01:02:03+08:00',
                 'occurred': {'at': None, 'precision': 'unknown'}, 'authority': None, 'changes': []}
        self.assertIsNone(codec.validate_event(event, 1))
        self.assertIsNone(codec.validate(self.full_state(event)))
        for fields in ({'revision': 2}, {'id': 'event-00000002'}, {'at': 'no timezone'},
                       {'occurred': {'at': '2026-01-01', 'precision': 'exact'}}, {'authority': {}},
                       {'changes': {}}, {'changes': [{'summary': 'missing semantic fields'}]},
                       {'before': []}, {'after': []}, {'actor': ''}, {'unexpected': True}):
            candidate = {**copy.deepcopy(event), **fields}
            with self.subTest(fields=fields):
                for validator, value in ((lambda e: codec.validate_event(e, 1), candidate),
                                         (codec.validate, self.full_state(candidate))):
                    with self.assertRaises(codec.RegistryError):
                        validator(value)
        with self.assertRaises(codec.RegistryError):
            codec.validate({**self.full_state(event), 'revision': 2})


if __name__ == '__main__':
    unittest.main()
