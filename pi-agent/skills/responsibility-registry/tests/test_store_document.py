"""Pure v3 Store document contract; synthetic input, no Store or audit I/O."""
import copy
import io
import unittest

from test_codec import codec
from test_current_view import CURRENT_KEYS, ERA, current, fixture


HEAD = 'abcdef01-2345-4678-9abc-0123456789ab'


class StoreDocumentTests(unittest.TestCase):
    def setUp(self):
        self.state = current(fixture())
        self.document = codec.serialize_current_document(self.state, ERA, HEAD)

    def test_roundtrip_has_exact_current_and_store_metadata(self):
        expected = (self.state, ERA, {'format': codec.SPLIT_FORMAT, 'audit_head': HEAD})
        self.assertEqual(codec.parse_store_document(self.document), expected)
        self.assertEqual(codec.parse_store_document(self.document.replace('\n', '\r\n')), expected)
        self.assertEqual(codec.parse_current_document(io.StringIO(self.document)), (self.state, ERA))
        self.assertEqual(set(expected[0]), CURRENT_KEYS)
        self.assertIn('Audit-Head: ' + HEAD + '\n', self.document)
        self.assertTrue(self.document.endswith(codec.HISTORY + '\n' + codec.EXTERNAL_HISTORY_NOTICE + '\n'))
        self.assertNotIn('```', self.document)

    def test_optional_head_is_normalized_and_serializer_always_emits_it(self):
        without_head = self.document.replace('Audit-Head: ' + HEAD + '\n', '')
        explicit_none = self.document.replace(HEAD, 'none')
        for document in (without_head, explicit_none):
            state, era, metadata = codec.parse_store_document(document)
            self.assertEqual(metadata, {'format': codec.SPLIT_FORMAT, 'audit_head': None})
            self.assertIn('Audit-Head: none\n', codec.serialize_current_document(state, era, metadata['audit_head']))
        self.assertIn('Audit-Head: none\n', codec.serialize_current_document(self.state, ERA))

    def test_formats_remain_separate_without_fabricating_history(self):
        legacy = codec.serialize_document(fixture(), ERA)
        with self.assertRaisesRegex(codec.RegistryError, 'unsupported document format'):
            codec.parse_store_document(legacy)
        with self.assertRaisesRegex(codec.RegistryError, 'unsupported document format'):
            codec.parse_document(self.document)
        with self.assertRaises(codec.RegistryError):
            codec.serialize_current_document(fixture(), ERA)
        with self.assertRaises(codec.RegistryError):
            codec.serialize_document(self.state, ERA)
        self.assertEqual(codec.FORMAT, 'responsibility-registry/v2')
        self.assertEqual(codec.SPLIT_FORMAT, 'responsibility-registry/v3')

    def test_template_is_blank_at_zero_revision_without_audit_head(self):
        empty = dict(version=1, revision=0, owners={}, assignments={}, coordination={})
        document = codec.serialize_current_document(empty, 'template')
        self.assertEqual(codec.parse_store_document(document),
                         (empty, 'template', {'format': codec.SPLIT_FORMAT, 'audit_head': None}))
        for state, era, head in ((self.state, 'template', None), ({**empty, 'revision': 1}, 'template', None),
                                 (empty, 'template', HEAD)):
            with self.subTest(state=state, era=era, head=head), self.assertRaises(codec.RegistryError):
                codec.serialize_current_document(state, era, head)
        for bad in (document.replace('Revision: 0', 'Revision: 1'),
                    document.replace('Audit-Head: none', 'Audit-Head: ' + HEAD)):
            with self.assertRaises(codec.RegistryError):
                codec.parse_store_document(bad)

    def test_head_is_only_none_or_canonical_uuid_not_a_locator(self):
        for head in ('', 'None', ' none', 'none ', HEAD.upper(), HEAD.replace('-', ''),
                     '{' + HEAD + '}', '/private/audit/head.json', '../ops/record', 'https://example/head',
                     'Audit-Head: none', 1, True, {}):
            with self.subTest(head=head):
                with self.assertRaises(codec.RegistryError):
                    codec.serialize_current_document(self.state, ERA, head)
                if isinstance(head, str):
                    bad = self.document.replace('Audit-Head: ' + HEAD, 'Audit-Head: ' + head)
                    for parser, source in ((codec.parse_store_document, bad),
                                           (codec.parse_current_document, io.StringIO(bad))):
                        with self.assertRaises(codec.RegistryError):
                            parser(source)

    def test_store_validates_fixed_notice_and_trailing_structure(self):
        prefix = self.document.split(codec.HISTORY + '\n', 1)[0] + codec.HISTORY + '\n'
        for tail in ('', '```json\n[]\n```\n', codec.EXTERNAL_HISTORY_NOTICE + ' \n',
                     codec.EXTERNAL_HISTORY_NOTICE + '\nextra\n', '/private/audit/history\n',
                     codec.EXTERNAL_HISTORY_NOTICE + '\nAudit-Head: ' + HEAD + '\n'):
            with self.subTest(tail=tail):
                document = prefix + tail
                self.assertEqual(codec.parse_current_document(io.StringIO(document)), (self.state, ERA))
                with self.assertRaises(codec.RegistryError):
                    codec.parse_store_document(document)
        self.assertEqual(codec.parse_store_document(self.document.replace('\n', '\n\n'))[0], self.state)

    def test_revision_is_observed_current_label_not_audit_length(self):
        state = copy.deepcopy(self.state)
        state['revision'] = 10**30
        document = codec.serialize_current_document(state, ERA, HEAD)
        self.assertEqual(codec.parse_store_document(document)[0], state)


if __name__ == '__main__':
    unittest.main()
