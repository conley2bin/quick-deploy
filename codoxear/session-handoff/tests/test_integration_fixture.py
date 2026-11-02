"""Preparation checks only: these tests never run Pi or claim model acceptance."""
import importlib
import json
from pathlib import Path
import tempfile
import unittest

from integration_fixture import (
    INITIAL_MAIN, assert_intended_action, assert_startup_submitted,
    assert_unchanged, context, prepare_fixture, project_fingerprint, prompts,
    read_correlated_chain,
)


class IntegrationFixtureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="handoff-fixture-test-")
        self.addCleanup(self.tmp.cleanup)
        self.fixture = prepare_fixture(Path(self.tmp.name) / "case")
        self.project = self.fixture["project"]
        self.baseline = project_fingerprint(self.project)

    def test_truth_disagrees_with_memory_and_claim_but_matches_current_branch(self):
        manifest = self.fixture["manifest"]
        snapshot = manifest["source"]["snapshot"]
        text = context.render_source_context(snapshot)
        self.assertIn("Change the goal from X to Y", text)
        self.assertIn("Do not edit vendor.py", text)
        self.assertIn("Reject delegation", text)
        self.assertNotIn("build a dashboard", text)
        self.assertEqual((self.project / "main.py").read_text(), INITIAL_MAIN)
        self.assertIn("goal X", (self.project / ".memory/project/goal.md").read_text())
        self.assertNotEqual(self.fixture["source"].stem, snapshot["session_id"])
        disk_manifest = json.loads((self.fixture["operation"] / "manifest.json").read_text())
        self.assertNotIn("entries", disk_manifest["source"]["snapshot"])
        self.assertEqual((self.fixture["operation"].stat().st_mode & 0o777), 0o700)

    def test_no_project_action_is_allowed_during_recovery_or_review(self):
        assert_unchanged(self.project, self.baseline, "understanding")
        assert_unchanged(self.project, self.baseline, "old review")
        (self.project / "main.py").write_text('def result():\n    return "Y"\n')
        with self.assertRaisesRegex(AssertionError, "before authorized"):
            assert_unchanged(self.project, self.baseline, "old review")
        assert_intended_action(self.project, self.baseline)
        (self.project / "vendor.py").write_text("# forbidden edit\n")
        with self.assertRaisesRegex(AssertionError, "only main.py"):
            assert_intended_action(self.project, self.baseline)

    def test_rejected_delegation_fails_even_when_main_file_changed(self):
        (self.project / "main.py").write_text("import vendor\ndef result():\n    return vendor.result()\n")
        with self.assertRaisesRegex(AssertionError, "self-contained"):
            assert_intended_action(self.project, self.baseline)

    def test_added_project_artifact_is_not_invisible(self):
        (self.project / "extra.py").write_text("# not the authorized next action\n")
        with self.assertRaises(AssertionError):
            assert_unchanged(self.project, self.baseline, "recovery")

    def test_initial_prompt_requires_real_user_record_and_new_identity(self):
        manifest = self.fixture["manifest"]
        prompt = prompts.build_recovery_prompt(manifest, self.fixture["operation"])
        self.assertFalse(prompt.startswith(("-", "@")))
        log = self.fixture["root"] / "successor.jsonl"
        header = {"type": "session", "id": "synthetic-new-header"}
        log.write_text(json.dumps(header) + "\n")
        kwargs = {"source_header_id": manifest["source"]["pi_session_id"]}
        with self.assertRaisesRegex(AssertionError, "prefill"):
            assert_startup_submitted(log, prompt, **kwargs)
        entry = {"type": "message", "message": {"role": "user", "content": [
            {"type": "text", "text": prompt}]}}
        log.write_text(json.dumps(header) + "\n" + json.dumps(entry) + "\n")
        self.assertEqual(assert_startup_submitted(log, prompt, **kwargs), "synthetic-new-header")
        with self.assertRaisesRegex(AssertionError, "differs"):
            assert_startup_submitted(log, "a different prompt", **kwargs)
        with self.assertRaisesRegex(AssertionError, "new Pi identity"):
            assert_startup_submitted(log, prompt, source_header_id="synthetic-new-header")

    def test_chain_check_requires_matching_ready_not_just_understanding(self):
        io = importlib.import_module(context.__package__ + ".handoff_io")
        directory = self.fixture["operation"]
        draft = directory / "actor-draft.md"
        draft.write_text("SYNTHETIC helper test, not a real model report")
        with self.assertRaisesRegex(AssertionError, "understanding missing"):
            read_correlated_chain(self.fixture)
        io.submit(directory / "manifest.json", "understanding", draft)
        with self.assertRaisesRegex(AssertionError, "review missing"):
            read_correlated_chain(self.fixture)
        io.submit(directory / "manifest.json", "review", draft, verdict="corrections")
        with self.assertRaisesRegex(AssertionError, "no matching ready"):
            read_correlated_chain(self.fixture)
        io.submit(directory / "manifest.json", "resolution", draft, verdict="ready")
        understanding, review, resolution = read_correlated_chain(self.fixture)
        self.assertEqual(review["understanding_sha256"], understanding["_sha256"])
        self.assertEqual(resolution["review_sha256"], review["_sha256"])

    def test_existing_fixture_root_is_never_reused(self):
        with self.assertRaises(FileExistsError):
            prepare_fixture(self.fixture["root"])


if __name__ == "__main__":
    unittest.main()
