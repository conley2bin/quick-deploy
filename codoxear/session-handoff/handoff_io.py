"""Submit machine-correlated handoff reports; drafts stay inside the operation.

Run this installed file directly with Python; no environment-specific import path
or third-party dependencies are needed. Exit 2 means the report was NOT accepted.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
import sys

if __package__:
    from . import context, succession
else:
    import context
    import succession


def submit(manifest_path: Path, kind: str, body_file: Path, *, verdict=None,
           unreviewed=False, service=None) -> dict:
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
    with ExitStack() as stack:
        directory_fd = stack.enter_context(context._operation_dir(operation_dir))
        if kind == 'resolution':
            stack.enter_context(succession.operation_lock(directory_fd))
            if context._read_at(directory_fd, 'resolution.json', context.MAX_ARTIFACT_BYTES, missing=True) is not None:
                raise context.ContextError('refusing to overwrite resolution.json')
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
            if unreviewed:
                # The authenticated workflow selected this route explicitly. A late
                # review is retained for audit, but must not change that selection.
                previous["review_sha256"] = "unreviewed"
            else:
                review = context.read_artifact(operation_dir, "review", **identity, **previous)
                if review is None:
                    raise context.ContextError("review missing; explicit bypass requires --unreviewed")
                previous["review_sha256"] = review["_sha256"]
        envelope.update(previous)
        context._validate_artifact(envelope, kind, **identity, **previous)
        raw = context._encode(envelope)
        if len(raw) > context.MAX_ARTIFACT_BYTES:
            raise context.ContextError("encoded artifact exceeds byte limit")
        if 'succession' in manifest and kind == 'understanding':
            succession.check_target(directory_fd, manifest)
        if 'succession' in manifest and kind == 'review':
            custody = succession.read(directory_fd, 'succession-custody.json')
            succession.require(custody['operation_id'] == operation_id and
                               custody['understanding_sha256'] == previous['understanding_sha256'])
            succession.validate_custody(custody['custody'], source['pi_session_id'])
        if 'succession' in manifest and kind == 'resolution' and verdict == 'ready':
            succession.gate(directory_fd, manifest, previous['understanding_sha256'],
                            previous['review_sha256'], succession.Adapter(service))
        context._publish_at(directory_fd, f"{kind}.json", raw)
    return {**envelope, "_sha256": hashlib.sha256(raw).hexdigest()}


def attest(manifest_path, kind, body_file):
    """Private cooperative runtime self-report; never accepts browser identity."""
    manifest_path, body_file = Path(manifest_path), Path(body_file)
    if (not manifest_path.is_absolute() or manifest_path.name != 'manifest.json'
            or body_file.parent != manifest_path.parent or not body_file.name.endswith('-draft.json')):
        raise context.ContextError('attestation requires a local *-draft.json')
    with context._operation_dir(manifest_path.parent) as fd:
        manifest = succession.read(fd, 'manifest.json')
        succession.check_source(fd, manifest)
        # Draft is caller-authored data, not a path/owner/authority override.
        draft_fd = os.open(body_file.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            info = context._regular(draft_fd)
            succession.require(info.st_uid == os.getuid() and info.st_nlink == 1 and info.st_size <= context.MAX_BODY_BYTES)
            os.fchmod(draft_fd, 0o600)
            value = context._json(context._read_fd(draft_fd, info.st_size))
        finally:
            os.close(draft_fd)
        if kind == 'identity':
            succession.check_target(fd, manifest, report=value)
        elif kind == 'custody':
            succession.keys(value, ('operation_id', 'understanding_sha256', 'custody'))
            succession.require(value['operation_id'] == manifest['operation_id'])
            understanding = context.read_artifact(manifest_path.parent, 'understanding',
                operation_id=manifest['operation_id'], source_sha256=manifest['source_sha256'])
            succession.require(understanding is not None and value['understanding_sha256'] == understanding['_sha256'])
            succession.validate_custody(value['custody'], manifest['source']['pi_session_id'])
        else:
            raise context.ContextError('invalid attestation kind')
        succession.publish(fd, 'succession-' + kind + '.json', value)
    return {'kind': kind, 'accepted': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("submit")
    command.add_argument("--manifest", required=True, type=Path)
    command.add_argument("--kind", required=True, choices=context.KINDS)
    command.add_argument("--body-file", required=True, type=Path)
    command.add_argument("--verdict", choices=("clear", "corrections", "conflict", "ready"))
    command.add_argument("--unreviewed", action="store_true")
    proof = commands.add_parser('attest')
    proof.add_argument('--manifest', required=True, type=Path)
    proof.add_argument('--kind', required=True, choices=('identity', 'custody'))
    proof.add_argument('--body-file', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'attest':
            artifact = attest(args.manifest, args.kind, args.body_file)
        else:
            artifact = submit(args.manifest, args.kind, args.body_file,
                              verdict=args.verdict, unreviewed=args.unreviewed)
    except (context.ContextError, OSError) as exc:
        print(context._encode({"error": "artifact_rejected", "message": str(exc) if isinstance(exc, succession.GateError) else '私有报告校验或发布失败'}).decode(),
              file=sys.stderr, end="")
        return 2
    print(context._encode({"kind": args.kind, "artifact": artifact}).decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
