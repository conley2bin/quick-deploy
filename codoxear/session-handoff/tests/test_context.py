import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if "handoff_test" not in sys.modules:
    package = types.ModuleType("handoff_test")
    package.__path__ = [str(ROOT)]
    sys.modules["handoff_test"] = package
context = importlib.import_module("handoff_test.context")


def message(id, parent, text, role="user"):
    return {"type": "message", "id": id, "parentId": parent,
            "message": {"role": role, "content": text}}


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "misleading-filename.jsonl"
        self.header = {"type": "session", "version": 3, "id": "real-header-id", "cwd": "/project"}

    def log(self, entries, tail=b""):
        raw = b"".join((json.dumps(item) + "\n").encode() for item in [self.header, *entries])
        self.path.write_bytes(raw + tail)
        return raw

    def test_goal_change_side_branch_rejected_approach_and_unwritten_edit(self):
        self.log([message("a", None, "Goal X"),
                  message("b", "a", "Reject approach A: it destroys history"),
                  message("z", "a", "Unrelated goal Z"),
                  message("y", "b", "Change goal X to Y, preserve records"),
                  message("p", "y", "I plan to edit file, not yet written", "assistant")])
        snapshot = context.create_source_snapshot(self.path)
        rendered = context.render_source_context(snapshot)
        self.assertEqual(snapshot["session_id"], "real-header-id")
        self.assertEqual(snapshot["projection_resolution"], "latest_committed")
        self.assertEqual(snapshot["projection_leaf_id"], "p")
        self.assertIn("Change goal X to Y", rendered)
        self.assertIn("Reject approach A", rendered)
        self.assertIn("not yet written", rendered)
        self.assertNotIn("Unrelated goal Z", rendered)
        self.assertIn("unpersisted active cursor is UNKNOWN", rendered)
        self.assertIn("Current files/evidence outrank stale memory", rendered)

    def test_partial_tail_and_post_snapshot_appends_are_excluded(self):
        raw = self.log([message("a", None, "committed")], b'{"type":"message"')
        before = self.path.read_bytes()
        snapshot = context.create_source_snapshot(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(snapshot["cutoff_bytes"], len(raw))
        self.assertEqual(snapshot["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertGreater(snapshot["excluded_tail_bytes"], 0)
        rendered = context.render_source_context(snapshot)
        self.path.write_bytes(raw + (json.dumps(message("b", "a", "later")) + "\n").encode())
        self.assertEqual(context.render_source_context(snapshot), rendered)
        self.assertNotIn("later", rendered)

    def test_append_during_capture_stays_outside_initial_size(self):
        raw = self.log([message("a", None, "first")])
        original = context._read_fd
        def append_then_read(fd, size):
            with self.path.open("ab") as stream:
                stream.write((json.dumps(message("b", "a", "racing append")) + "\n").encode())
            return original(fd, size)
        with patch.object(context, "_read_fd", append_then_read):
            snapshot = context.create_source_snapshot(self.path)
        self.assertEqual(snapshot["cutoff_bytes"], len(raw))
        self.assertEqual(snapshot["boundary_entry_id"], "a")

    def test_compaction_kept_range_and_context_edits(self):
        self.log([message("a", None, "old obsolete detail"),
                  message("b", "a", "wrong retained text"),
                  {"type": "compaction", "id": "c", "parentId": "b", "summary": "Goal Y; approach A rejected",
                   "firstKeptEntryId": "b"},
                  {"type": "context_edit", "id": "e", "parentId": "c", "targetId": "b",
                   "replacement": {"content": "corrected retained constraint"}},
                  message("d", "e", "drop this"),
                  {"type": "context_edit", "id": "f", "parentId": "d", "targetId": "d", "replacement": None}])
        rendered = context.render_source_context(context.create_source_snapshot(self.path))
        self.assertIn("Goal Y; approach A rejected", rendered)
        self.assertIn("corrected retained constraint", rendered)
        self.assertNotIn("old obsolete detail", rendered)
        self.assertNotIn("wrong retained text", rendered)
        self.assertNotIn("drop this", rendered)

    def test_context_edits_are_branch_relative_last_wins(self):
        self.log([message("a", None, "original"),
                  {"type": "context_edit", "id": "z", "parentId": "a", "targetId": "a",
                   "replacement": {"content": "side branch"}},
                  {"type": "context_edit", "id": "b", "parentId": "a", "targetId": "a",
                   "replacement": {"content": "first edit"}},
                  {"type": "context_edit", "id": "c", "parentId": "b", "targetId": "a",
                   "replacement": {"content": "last edit"}}])
        rendered = context.render_source_context(context.create_source_snapshot(self.path))
        self.assertIn("last edit", rendered)
        self.assertNotIn("side branch", rendered)
        self.assertNotIn("first edit", rendered)
        self.assertNotIn("original", rendered)

    def test_summarized_edits_do_not_apply_to_kept_entries(self):
        self.log([message("a", None, "original kept text"),
                  {"type": "context_edit", "id": "e", "parentId": "a", "targetId": "a", "replacement": None},
                  {"type": "compaction", "id": "c", "parentId": "e", "summary": "retain none", "firstKeptEntryId": "c"},
                  message("b", "c", "new work")])
        rendered = context.render_source_context(context.create_source_snapshot(self.path))
        self.assertIn("retain none", rendered)
        self.assertIn("new work", rendered)
        self.assertNotIn("original kept text", rendered)

    def test_nested_compaction_does_not_replay_previous_summary(self):
        self.log([message("a", None, "old"),
                  {"type": "compaction", "id": "b", "parentId": "a", "summary": "obsolete summary", "firstKeptEntryId": "b"},
                  message("c", "b", "kept"),
                  {"type": "compaction", "id": "d", "parentId": "c", "summary": "current summary", "firstKeptEntryId": "b"}])
        rendered = context.render_source_context(context.create_source_snapshot(self.path))
        self.assertIn("current summary", rendered)
        self.assertNotIn("obsolete summary", rendered)
        self.assertIn("kept", rendered)

    def test_context_is_bounded_and_no_bulk_tool_output(self):
        entries = [message("a", None, "old " * 5000)]
        entries += [message("b", "a", "SECRET_TOOL_OUTPUT" * 10000, "toolResult"),
                    message("c", "b", "most recent goal")]
        self.log(entries)
        rendered = context.render_source_context(context.create_source_snapshot(self.path), max_chars=1600)
        self.assertLessEqual(len(rendered), 1600)
        self.assertIn("most recent goal", rendered)
        self.assertNotIn("SECRET_TOOL_OUTPUT", rendered)
        self.assertIn("omitted", rendered)

    def test_malformed_complete_records_and_missing_parents_fail(self):
        for suffix in (b"{broken}\n", b"\n", b'{"type":"message","id":"a","parentId":"missing"}\n',
                       b'{"type":"x","id":"a","id":"b","parentId":null}\n'):
            with self.subTest(suffix=suffix):
                self.log([], suffix)
                with self.assertRaises(context.ContextError):
                    context.create_source_snapshot(self.path)

    def test_legacy_headers_bad_compaction_and_invalid_edit_fail(self):
        self.header["version"] = 1
        self.log([])
        with self.assertRaises(context.ContextError):
            context.create_source_snapshot(self.path)
        self.header["version"] = 3
        for item in ({"type": "compaction", "id": "b", "parentId": "a", "summary": "s", "firstKeptEntryId": "missing"},
                     {"type": "context_edit", "id": "b", "parentId": "a", "targetId": "a", "replacement": "bad"}):
            self.log([message("a", None, "x"), item])
            with self.assertRaises(context.ContextError):
                context.create_source_snapshot(self.path)

    def test_regular_source_only_and_size_limit(self):
        self.log([])
        link = self.path.with_name("link")
        link.symlink_to(self.path)
        with self.assertRaises(context.ContextError):
            context.create_source_snapshot(link)
        fifo = self.path.with_name("fifo")
        os.mkfifo(fifo)
        with self.assertRaises(context.ContextError):
            context.create_source_snapshot(fifo)
        with self.assertRaises(context.ContextError):
            context.create_source_snapshot(self.path, max_bytes=1)


if __name__ == "__main__":
    unittest.main()
