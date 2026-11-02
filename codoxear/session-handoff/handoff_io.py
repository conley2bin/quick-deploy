"""Submit machine-correlated handoff reports; drafts stay inside the operation.

Run this installed file directly with Python; no environment-specific import path
or third-party dependencies are needed. Exit 2 means the report was NOT accepted.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import sys

if __package__:
    from . import context
else:
    import context


def submit(manifest_path: Path, kind: str, body_file: Path, *, verdict=None,
           unreviewed=False) -> dict:
    manifest_path = Path(manifest_path)
    if not manifest_path.is_absolute() or manifest_path.name != "manifest.json":
        raise context.ContextError("--manifest must be the absolute operation manifest.json")
    operation_dir = manifest_path.parent
    if kind not in context.KINDS:
        raise context.ContextError("unknown artifact kind")
    if unreviewed and kind != "resolution":
        raise context.ContextError("--unreviewed is only valid for resolution")
    body_file = Path(body_file).absolute()
    if (body_file.parent != operation_dir or body_file.name in
            {"manifest.json", "source-context.md", *(f"{k}.json" for k in context.KINDS)}):
        raise context.ContextError("body draft must be a separate file directly inside operation directory")
    with context._operation_dir(operation_dir) as directory_fd:
        manifest = context._json(context._read_at(directory_fd, "manifest.json", context.MAX_MANIFEST_BYTES))
        if not isinstance(manifest, dict) or manifest.get("version") != 1:
            raise context.ContextError("invalid manifest")
        expected_paths = {"operation_dir": str(operation_dir),
                          "source_context": str(operation_dir / "source-context.md"),
                          **{k: str(operation_dir / f"{k}.json") for k in context.KINDS}}
        if manifest.get("paths") != expected_paths:
            raise context.ContextError("manifest path mismatch")
        operation_id = manifest.get("operation_id")
        source_sha256 = manifest.get("source_sha256")
        context._identity(operation_id, source_sha256)
        source = manifest.get("source", {})
        if not isinstance(source, dict) or not isinstance(source.get("snapshot"), dict):
            raise context.ContextError("invalid manifest source/snapshot")
        snapshot = source["snapshot"]
        if (snapshot.get("sha256") != source_sha256
                or snapshot.get("session_id") != source.get("pi_session_id")):
            raise context.ContextError("manifest source identity/hash mismatch")
        # Drafts may have the agent's default umask; make them private before reading.
        try:
            fd = os.open(body_file.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=directory_fd)
            try:
                info = context._regular(fd)
                if info.st_uid != os.getuid() or info.st_nlink != 1:
                    raise context.ContextError("unsafe draft owner/hardlink")
                os.fchmod(fd, 0o600)
                if info.st_size > context.MAX_BODY_BYTES:
                    raise context.ContextError("body draft exceeds byte limit")
                body = context._read_fd(fd, info.st_size).decode("utf-8")
            finally:
                os.close(fd)
        except (OSError, UnicodeError) as exc:
            raise context.ContextError(f"cannot read body draft: {exc}") from exc
        envelope = {"version": 1, "operation_id": operation_id,
                    "source_sha256": source_sha256, "body": body}
        previous = {}
        identity = {"operation_id": operation_id, "source_sha256": source_sha256}
        if kind != "understanding":
            understanding = context.read_artifact(operation_dir, "understanding", **identity)
            if understanding is None:
                raise context.ContextError("understanding has not been submitted")
            previous["understanding_sha256"] = understanding["_sha256"]
            envelope["verdict"] = verdict
        elif verdict is not None:
            raise context.ContextError("understanding does not take --verdict")
        if kind == "resolution":
            review = context.read_artifact(operation_dir, "review", **identity, **previous)
            if unreviewed:
                if review is not None:
                    raise context.ContextError("cannot bypass an already submitted review")
                previous["review_sha256"] = "unreviewed"
            else:
                if review is None:
                    raise context.ContextError("review missing; explicit bypass requires --unreviewed")
                previous["review_sha256"] = review["_sha256"]
        envelope.update(previous)
        context._validate_artifact(envelope, kind, **identity, **previous)
        raw = context._encode(envelope)
        if len(raw) > context.MAX_ARTIFACT_BYTES:
            raise context.ContextError("encoded artifact exceeds byte limit")
        context._publish_at(directory_fd, f"{kind}.json", raw)
    return {**envelope, "_sha256": hashlib.sha256(raw).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("submit")
    command.add_argument("--manifest", required=True, type=Path)
    command.add_argument("--kind", required=True, choices=context.KINDS)
    command.add_argument("--body-file", required=True, type=Path)
    command.add_argument("--verdict", choices=("clear", "corrections", "conflict", "ready"))
    command.add_argument("--unreviewed", action="store_true")
    args = parser.parse_args(argv)
    try:
        artifact = submit(args.manifest, args.kind, args.body_file,
                          verdict=args.verdict, unreviewed=args.unreviewed)
    except (context.ContextError, OSError) as exc:
        print(context._encode({"error": "artifact_rejected", "message": str(exc)}).decode(),
              file=sys.stderr, end="")
        return 2
    print(context._encode({"kind": args.kind, "artifact": artifact}).decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
