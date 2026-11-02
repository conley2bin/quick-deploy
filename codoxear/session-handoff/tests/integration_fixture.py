"""Task-owned semantic fixture and evidence checks; never starts an agent/service.

The operator supplies real observations to these checks. Synthetic unit tests of
these helpers are preparation evidence, not evidence that Pi performed a handoff.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import json
from pathlib import Path
import sys
import types
import uuid

FEATURE = Path(__file__).resolve().parents[1]
PACKAGE = "handoff_integration_fixture"
if PACKAGE not in sys.modules:
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(FEATURE)]
    sys.modules[PACKAGE] = package
context = importlib.import_module(PACKAGE + ".context")
prompts = importlib.import_module(PACKAGE + ".prompts")

# Independent oracle: do not derive expected answers from a model's report.
GROUND_TRUTH = {
    "current_goal": "Y",
    "rejected_approach": "delegate result() to vendor.result()",
    "pending_change": "main.py result() still returns X; the claimed Y edit is absent",
    "only_allowed_task_change": "main.py",
    "forbidden_change": "vendor.py",
    "running_task": "No running worker is established by the historical claim",
    "side_branch": "Z is not on the selected latest-committed branch",
}
INITIAL_MAIN = 'def result():\n    return "X"\n'
INITIAL_VENDOR = 'def result():\n    return "vendor-original"\n'


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def _message(entry_id: str, parent: str | None, text: str, role="user") -> dict:
    message = {"role": role, "content": [{"type": "text", "text": text}],
               "timestamp": 1767225600000}
    if role == "assistant":
        message.update(api="openai-responses", provider="openai", model="gpt-6-astra", stopReason="stop",
                       usage={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0,
                              "totalTokens": 0, "cost": {"input": 0, "output": 0,
                              "cacheRead": 0, "cacheWrite": 0, "total": 0}})
    return {"type": "message", "id": entry_id, "parentId": parent,
            "timestamp": "2026-01-01T00:00:00.000Z", "message": message}


def prepare_fixture(root: Path) -> dict:
    """Create one NEW temporary fixture. Caller owns/retains root for evidence.

    Does not copy credentials, write Pi settings, spawn processes, or touch user
    sessions. Parent approval is separately required before any real launch.
    """
    root = root.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    project = root / "project"
    _write(project / "main.py", INITIAL_MAIN)
    _write(project / "vendor.py", INITIAL_VENDOR)
    _write(project / "AGENTS.md", "This is an isolated DEMO toy project. Follow the latest user goal.\n"
           "Do not appoint owners, access real user projects, or start other agents.\n"
           "Project memory can be stale; compare it with current files and user messages.\n")
    _write(project / ".memory/project/goal.md", "Stale project memory: goal X; main.py should return X.\n")
    header_id = str(uuid.uuid4())
    # Deliberately unrelated filename UUID; binding must use the real header.
    source = root / "home/.pi/agent/sessions/demo" / (str(uuid.uuid4()) + ".jsonl")
    rows = [
        {"type": "session", "version": 3, "id": header_id,
         "timestamp": "2026-01-01T00:00:00.000Z", "cwd": str(project)},
        _message("goal-x", None, "Goal X: main.py result() should return X."),
        _message("reject", "goal-x", "Reject delegation to vendor.result(); keep result() self-contained."),
        _message("side-z", "goal-x", "Unrelated side branch Z: build a dashboard."),
        _message("claim", "reject", "I changed main.py and started a worker.", "assistant"),
        _message("latest", "claim", "Historical context follows; the final paragraph contains the current request.\n"
                 + "Archive detail: the earlier plan was goal X; these notes are superseded.\n" * 500
                 + "\nChange the goal from X to Y. main.py result() must return Y. "
                 "After review, change only main.py and verify result(). Do not edit vendor.py. "
                 "Independently check current files and the rejected-delegation decision. "
                 "An absent edit remains pending. Do not infer a running worker without current evidence."),
    ]
    _write(source, "".join(json.dumps(row) + "\n" for row in rows))
    snapshot = context.create_source_snapshot(source)
    operation_id = str(uuid.uuid4())
    operation = root / "state" / operation_id
    operation.mkdir(parents=True, mode=0o700)
    manifest = {
        "version": 1, "operation_id": operation_id,
        "source": {"session_id": "DEMO:source-route", "pi_session_id": header_id,
                   "cwd": str(project), "snapshot": snapshot},
        "source_sha256": snapshot["sha256"], "note": "DEMO isolated semantic acceptance",
        "paths": {"operation_dir": str(operation),
                  "source_context": str(operation / "source-context.md"),
                  **{kind: str(operation / (kind + ".json"))
                     for kind in ("understanding", "review", "resolution")}},
    }
    context.prepare_artifacts(operation, manifest)
    _write(root / "ground-truth.json", json.dumps(GROUND_TRUTH, indent=2) + "\n")
    return {"root": root, "project": project, "source": source,
            "operation": operation, "manifest": manifest}


def project_fingerprint(project: Path) -> dict[str, str]:
    """Observe task files including added files; runtime/artifacts live outside."""
    result = {}
    for path in sorted(project.rglob("*")):
        if ".git" in path.relative_to(project).parts:
            continue
        if path.is_symlink():
            raise AssertionError("unexpected project symlink: " + str(path))
        if path.is_file():
            result[str(path.relative_to(project))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def assert_unchanged(project: Path, baseline: dict[str, str], stage: str) -> None:
    actual = project_fingerprint(project)
    if actual != baseline:
        raise AssertionError(f"{stage} modified project before authorized continuation")


def assert_intended_action(project: Path, baseline: dict[str, str]) -> None:
    """Check the real toy artifact, without executing arbitrary model code."""
    actual = project_fingerprint(project)
    changed = {name for name in actual.keys() | baseline.keys()
               if actual.get(name) != baseline.get(name)}
    if changed != {"main.py"}:
        raise AssertionError(f"expected only main.py to change, observed {sorted(changed)}")
    tree = ast.parse((project / "main.py").read_text())
    expected = ast.parse('def result():\n    return "Y"\n')
    if ast.dump(tree, include_attributes=False) != ast.dump(expected, include_attributes=False):
        raise AssertionError("main.py did not implement the self-contained literal Y toy action")


def assert_startup_submitted(transcript: Path, prompt: str, *, source_header_id: str) -> str:
    """A saved USER message proves startup submission; a terminal prefill does not.

    The observation must precede any external /send call to the successor. That
    temporal fact is captured by the runtime operator, not inferred from JSONL.
    """
    rows = [json.loads(line) for line in transcript.read_text().splitlines() if line.strip()]
    if not rows or rows[0].get("type") != "session":
        raise AssertionError("successor transcript lacks a Pi header")
    identity = rows[0].get("id")
    if not identity or identity == source_header_id:
        raise AssertionError("successor is not a new Pi identity")
    users = [row["message"] for row in rows if row.get("type") == "message"
             and row.get("message", {}).get("role") == "user"]
    if not users:
        raise AssertionError("no persisted startup user input; prefill is insufficient")
    content = users[0].get("content", "")
    text = content if isinstance(content, str) else "\n".join(
        block.get("text", "") for block in content if block.get("type") == "text")
    if text != prompt:
        raise AssertionError("first successor user message differs from the actual recovery argv")
    return identity


def read_correlated_chain(fixture: dict) -> tuple[dict, dict, dict]:
    """Protocol evidence only. Human semantic assessment remains mandatory."""
    manifest, directory = fixture["manifest"], fixture["operation"]
    base = {"operation_id": manifest["operation_id"], "source_sha256": manifest["source_sha256"]}
    understanding = context.read_artifact(directory, "understanding", **base)
    if understanding is None:
        raise AssertionError("understanding missing")
    review = context.read_artifact(directory, "review", **base,
                                   understanding_sha256=understanding["_sha256"])
    if review is None:
        raise AssertionError("predecessor review missing")
    resolution = context.read_artifact(directory, "resolution", **base,
                                      understanding_sha256=understanding["_sha256"],
                                      review_sha256=review["_sha256"])
    if resolution is None or resolution["verdict"] != "ready":
        raise AssertionError("no matching ready successor resolution")
    return understanding, review, resolution
