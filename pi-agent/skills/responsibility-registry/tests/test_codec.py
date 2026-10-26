"""Pure document contract; no Store/filesystem/runtime discovery."""
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/registry_document.py'
spec = importlib.util.spec_from_file_location('codec_standalone', SCRIPT)
codec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(codec)


class CodecTests(unittest.TestCase):
    def test_blank_template_is_exact_valid_empty_document(self):
        state = dict(version=1, revision=0, owners={}, assignments={}, coordination={}, history=[])
        document = codec.serialize_document(state, 'template')
        self.assertEqual(codec.parse_document(document), (state, 'template'))
        self.assertEqual(codec.parse_document(document.replace('\n', '\n\n')), (state, 'template'))
        self.assertEqual((SCRIPT.parents[1] / 'RESPONSIBILITIES.template.md').read_text(), document)
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


if __name__ == '__main__':
    unittest.main()
