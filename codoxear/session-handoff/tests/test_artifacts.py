import concurrent.futures
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
if "handoff_test" not in sys.modules:
    package = types.ModuleType("handoff_test")
    package.__path__ = [str(ROOT)]
    sys.modules["handoff_test"] = package
context = importlib.import_module("handoff_test.context")
io = importlib.import_module("handoff_test.handoff_io")


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "private op"
        self.operation_id = str(uuid.uuid4())
        source = Path(self.temp.name) / "source.jsonl"
        source.write_text(json.dumps({"type": "session", "version": 3, "id": "real-id", "cwd": "/old-cwd"}) + "\n")
        snapshot = context.create_source_snapshot(source)
        self.manifest = {
            "version": 1, "operation_id": self.operation_id, "source_sha256": snapshot["sha256"],
            "source": {"session_id": "opaque-native-route", "pi_session_id": "real-id",
                       "cwd": "/current-cwd", "snapshot": snapshot}, "note": "",
            "paths": {"operation_dir": str(self.directory),
                      "source_context": str(self.directory / "source-context.md"),
                      **{k: str(self.directory / f"{k}.json") for k in context.KINDS}},
        }
        self.identity = {"operation_id": self.operation_id, "source_sha256": snapshot["sha256"]}
        context.prepare_artifacts(self.directory, self.manifest)
        self.draft = self.directory / "body.md"
        self.draft.write_text("Current goal Y; approach A remains rejected. Actual file has not been edited.")
        self.manifest_path = self.directory / "manifest.json"

    def submit(self, kind, **kwargs):
        return io.submit(self.manifest_path, kind, self.draft, **kwargs)

    def put(self, kind, value):
        path = self.directory / f"{kind}.json"
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def test_private_manifest_has_bounded_snapshot_no_raw_entries(self):
        manifest = json.loads(self.manifest_path.read_text())
        self.assertNotIn("entries", manifest["source"]["snapshot"])
        self.assertIn("entries", self.manifest["source"]["snapshot"])
        self.assertEqual(manifest["source"]["cwd"], "/current-cwd")
        self.assertEqual(os.stat(self.directory).st_mode & 0o777, 0o700)
        for path in self.directory.iterdir():
            if path != self.draft:
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(context.ContextError):
            context.prepare_artifacts(self.directory, self.manifest)

    def test_full_chain_hashes_and_corrected_resolution(self):
        understanding = self.submit("understanding")
        self.draft.write_text("Omitted constraint: preserve parent tree. See current contract.")
        review = self.submit("review", verdict="corrections")
        self.draft.write_text("Accepted constraint after reading current contract; preserve parent tree, then proceed.")
        resolution = self.submit("resolution", verdict="ready")
        self.assertEqual(review["understanding_sha256"], understanding["_sha256"])
        self.assertEqual(resolution["review_sha256"], review["_sha256"])
        result = context.read_artifact(self.directory, "resolution", **self.identity,
                                      understanding_sha256=understanding["_sha256"], review_sha256=review["_sha256"])
        self.assertEqual(result, resolution)
        self.assertIn("Accepted constraint", result["body"])
        self.assertEqual(understanding, context.read_artifact(self.directory, "understanding", **self.identity))
        self.assertEqual(understanding["_sha256"], hashlib.sha256((self.directory / "understanding.json").read_bytes()).hexdigest())

    def test_missing_is_none_and_unknown_kind_rejected(self):
        self.assertIsNone(context.read_artifact(self.directory, "understanding", **self.identity))
        with self.assertRaises(context.ContextError):
            context.read_artifact(self.directory, "../other", **self.identity)

    def test_overwrite_refused_and_original_bytes_unchanged(self):
        self.submit("understanding")
        path = self.directory / "understanding.json"
        before = path.read_bytes()
        self.draft.write_text("replacement")
        with self.assertRaisesRegex(context.ContextError, "overwrite"):
            self.submit("understanding")
        self.assertEqual(path.read_bytes(), before)

    def test_concurrent_submissions_publish_only_one_complete_file(self):
        def attempt(_):
            try:
                return self.submit("understanding")
            except context.ContextError:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(attempt, range(6)))
        self.assertEqual(sum(item is not None for item in results), 1)
        self.assertIsNotNone(context.read_artifact(self.directory, "understanding", **self.identity))
        self.assertFalse(list(self.directory.glob(".pending-*")))

    def test_wrong_operation_source_or_previous_hashes_rejected(self):
        u = self.submit("understanding")
        r = self.submit("review", verdict="clear")
        self.submit("resolution", verdict="ready")
        checks = [("understanding", {**self.identity, "operation_id": str(uuid.uuid4())}),
                  ("understanding", {**self.identity, "source_sha256": "0" * 64}),
                  ("review", {**self.identity, "understanding_sha256": "0" * 64}),
                  ("resolution", {**self.identity, "understanding_sha256": u["_sha256"], "review_sha256": "0" * 64}),
                  ("resolution", {**self.identity, "understanding_sha256": u["_sha256"], "review_sha256": "unreviewed"})]
        for kind, kwargs in checks:
            with self.subTest(kind=kind, kwargs=kwargs), self.assertRaises(context.ContextError):
                context.read_artifact(self.directory, kind, **kwargs)
        self.assertNotEqual(r["_sha256"], "0" * 64)

    def test_unreviewed_is_explicit_not_missing_review_fallback(self):
        u = self.submit("understanding")
        with self.assertRaisesRegex(context.ContextError, "review missing"):
            self.submit("resolution", verdict="ready")
        with self.assertRaises(context.ContextError):
            self.submit("review", verdict="clear", unreviewed=True)
        resolution = self.submit("resolution", verdict="ready", unreviewed=True)
        self.assertEqual(resolution["review_sha256"], "unreviewed")
        with self.assertRaises(context.ContextError):
            context.read_artifact(self.directory, "resolution", **self.identity,
                                  understanding_sha256=u["_sha256"])
        self.assertIsNotNone(context.read_artifact(self.directory, "resolution", **self.identity,
                                                 understanding_sha256=u["_sha256"], review_sha256="unreviewed"))

    def test_late_review_before_bypass_does_not_change_explicit_route(self):
        understanding = self.submit("understanding")
        self.submit("review", verdict="conflict")
        review_path = self.directory / "review.json"
        original_review = review_path.read_bytes()
        resolution = self.submit("resolution", verdict="ready", unreviewed=True)
        self.assertEqual(resolution["review_sha256"], "unreviewed")
        self.assertEqual(review_path.read_bytes(), original_review)
        self.assertIsNotNone(context.read_artifact(self.directory, "resolution", **self.identity,
                                                 understanding_sha256=understanding["_sha256"],
                                                 review_sha256="unreviewed"))

    def test_late_review_after_bypass_does_not_rewrite_resolution(self):
        understanding = self.submit("understanding")
        resolution = self.submit("resolution", verdict="ready", unreviewed=True)
        original_resolution = (self.directory / "resolution.json").read_bytes()
        self.submit("review", verdict="corrections")
        self.assertEqual((self.directory / "resolution.json").read_bytes(), original_resolution)
        result = context.read_artifact(self.directory, "resolution", **self.identity,
                                       understanding_sha256=understanding["_sha256"],
                                       review_sha256="unreviewed")
        self.assertEqual(result, resolution)

    def test_invalid_verdict_and_missing_understanding(self):
        with self.assertRaises(context.ContextError):
            self.submit("review", verdict="clear")
        with self.assertRaises(context.ContextError):
            self.submit("understanding", verdict="ready")
        self.submit("understanding")
        with self.assertRaises(context.ContextError):
            self.submit("review", verdict="ready")

    def test_malformed_empty_oversized_and_unsafe_artifact(self):
        path = self.directory / "understanding.json"
        for raw in (b"{", b"x" * (context.MAX_ARTIFACT_BYTES + 1)):
            path.write_bytes(raw)
            path.chmod(0o600)
            with self.assertRaises(context.ContextError):
                context.read_artifact(self.directory, "understanding", **self.identity)
        self.put("understanding", {"version": 1, **self.identity, "body": ""})
        with self.assertRaises(context.ContextError):
            context.read_artifact(self.directory, "understanding", **self.identity)
        path.unlink()
        path.symlink_to(self.draft)
        with self.assertRaises(context.ContextError):
            context.read_artifact(self.directory, "understanding", **self.identity)
        path.unlink()
        os.mkfifo(path, 0o600)
        with self.assertRaises(context.ContextError):
            context.read_artifact(self.directory, "understanding", **self.identity)

    def test_nonprivate_artifact_or_directory_rejected(self):
        self.submit("understanding")
        (self.directory / "understanding.json").chmod(0o644)
        with self.assertRaises(context.ContextError):
            context.read_artifact(self.directory, "understanding", **self.identity)
        (self.directory / "understanding.json").chmod(0o600)
        self.directory.chmod(0o755)
        with self.assertRaises(context.ContextError):
            context.read_artifact(self.directory, "understanding", **self.identity)

    def test_draft_must_be_local_regular_bounded_and_private(self):
        outside = Path(self.temp.name) / "outside.md"
        outside.write_text("outside")
        with self.assertRaises(context.ContextError):
            io.submit(self.manifest_path, "understanding", outside)
        self.draft.unlink()
        self.draft.symlink_to(outside)
        with self.assertRaises(context.ContextError):
            self.submit("understanding")
        self.draft.unlink()
        os.link(outside, self.draft)
        with self.assertRaises(context.ContextError):
            self.submit("understanding")
        self.draft.unlink()
        self.draft.write_text("x" * (context.MAX_BODY_BYTES + 1))
        with self.assertRaises(context.ContextError):
            self.submit("understanding")
        self.draft.write_text("valid")
        self.submit("understanding")
        self.assertEqual(self.draft.stat().st_mode & 0o777, 0o600)

    def test_wrong_manifest_hash_id_paths_rejected(self):
        for key in ("hash", "id", "path"):
            manifest = json.loads(self.manifest_path.read_text())
            if key == "hash":
                manifest["source"]["snapshot"]["sha256"] = "0" * 64
            elif key == "id":
                manifest["source"]["pi_session_id"] = "wrong"
            else:
                manifest["paths"]["understanding"] = "/elsewhere"
            self.manifest_path.write_text(json.dumps(manifest))
            with self.assertRaises(context.ContextError):
                self.submit("understanding")
            self.manifest_path.write_text(json.dumps({**self.manifest, "source": {
                **self.manifest["source"], "snapshot": {k: v for k, v in self.manifest["source"]["snapshot"].items() if k != "entries"}}}))

    def test_cli_returns_machine_envelope_and_rejects_second_submit(self):
        command = [sys.executable, str(ROOT / "handoff_io.py"), "submit", "--manifest", str(self.manifest_path),
                   "--kind", "understanding", "--body-file", str(self.draft)]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["artifact"]["operation_id"], self.operation_id)
        self.assertEqual(data["kind"], "understanding")
        second = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(second.returncode, 2)
        self.assertEqual(json.loads(second.stderr)["error"], "artifact_rejected")


if __name__ == "__main__":
    unittest.main()
