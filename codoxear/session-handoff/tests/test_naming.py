"""Name sequencing is display state; native launch/header remain identity."""
import unittest

from test_engine import Engine, HandoffError, engine_module


class SequenceTests(unittest.TestCase):
    def test_exact_terminal_ascii_sequence(self):
        for source, expected in (
            ("plain", "plain_01"), ("中文 空格 '引号' $;\"", "中文 空格 '引号' $;\"_01"),
            ("source_00", "source_01"), ("source_01", "source_02"),
            ("source_09", "source_10"), ("source_98", "source_99"),
            ("source_1", "source_1_01"), ("source_001", "source_001_01"),
            ("source_100", "source_100_01"), ("source_０１", "source_０１_01"),
            ("source_09_tail", "source_09_tail_01"), ("a ", "a _01"),
            ("x" * 77, "x" * 77 + "_01"),
        ):
            with self.subTest(source=source):
                self.assertEqual(engine_module.handoff_names({"display_name": source}), (source, expected))

    def test_native_source_authority_and_exact_legacy_fallback(self):
        self.assertEqual(engine_module.handoff_names({"display_name": "fresh_09", "alias": "stale", "cwd": "/other",
                         "tmux_window": "launch-label", "session_id": "opaque"}), ("fresh_09", "fresh_10"))
        self.assertEqual(engine_module.handoff_names({"alias": " alias ", "cwd": "/other"}), ("alias", "alias_01"))
        self.assertEqual(engine_module.handoff_names({"cwd": "/中文 path/"}), ("中文 path", "中文 path_01"))
        for row in ({"display_name": "", "alias": "not-a-fallback"}, {"cwd": "/", "session_id": "never-name"}):
            with self.assertRaises(HandoffError):
                engine_module.handoff_names(row)

    def test_refuse_cleaner_or_mirror_changes(self):
        for name in ("", "x" * 78, "a  b", " a", "a\tb", "a\nb", "a\x1bb", "a\x7fb", "a\x80b", "a\x00b", "-flag", "a\u00a0b"):
            with self.subTest(name=name), self.assertRaises(HandoffError) as raised:
                engine_module.handoff_names({"display_name": name})
            self.assertEqual(raised.exception.code, "invalid_name")
            self.assertEqual(raised.exception.status, 422)
