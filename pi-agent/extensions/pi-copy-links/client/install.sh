#!/usr/bin/env bash
# Explicit per-desktop install: no Pi extension, SSH config, service, or host deploy.
set -euo pipefail
exec python3 - "${BASH_SOURCE[0]}" "$@" <<'PY'
import os
from pathlib import Path
import stat
import sys
import tempfile

NAME = "pi-client-link-helper"


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def inspect_link(path):
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISLNK(before.st_mode):
        raise RuntimeError(f"refusing to replace ordinary file/directory: {path}")
    destination = os.readlink(path)
    if identity(path.lstat()) != identity(before):
        raise RuntimeError(f"command symlink changed during inspection: {path}")
    return identity(before), destination


def validate_source(client):
    source = (client / NAME).resolve(strict=True)
    info = source.stat()
    if not stat.S_ISREG(info.st_mode) or not os.access(source, os.X_OK):
        raise RuntimeError(f"source must be an executable regular file: {source}")
    return source, identity(info)


def main():
    args = sys.argv[2:]
    if args not in ([], ["--replace"]):
        print(f"{NAME}: usage: bash {sys.argv[1]} [--replace]", file=sys.stderr)
        return 2

    client = Path(sys.argv[1]).resolve(strict=True).parent
    source, source_identity = validate_source(client)
    home = os.environ.get("HOME", "")
    if not os.path.isabs(home):
        raise RuntimeError("HOME must be an absolute path")
    target = (Path(home) / ".local/bin").resolve() / NAME
    # Physical checkout root, but do not require the deleted old source to exist.
    legacy = client.parents[2] / "client-link-helper" / NAME
    previous = inspect_link(target)
    action = "installed"
    if previous is not None:
        destination = Path(os.path.realpath(target.parent / previous[1]))
        if destination == source:
            if (validate_source(client) != (source, source_identity)
                    or inspect_link(target) != previous):
                raise RuntimeError("source or command changed; rerun after inspecting it")
            print(f"{NAME}: already installed {target} -> {previous[1]}")
            return 0
        if destination == legacy:
            action = "migrated same-checkout legacy symlink"
        elif args == ["--replace"]:
            action = "replaced symlink (--replace)"
        else:
            raise RuntimeError(f"refusing foreign symlink: {target} -> {previous[1]}; "
                               "use --replace only to deliberately repoint this command")

    target.parent.mkdir(parents=True, exist_ok=True)
    relative_source = os.path.relpath(source, target.parent)
    with tempfile.TemporaryDirectory(prefix=f".{NAME}-install-", dir=target.parent) as temporary:
        staged = Path(temporary) / NAME
        staged.symlink_to(relative_source)
        staged_identity, staged_destination = inspect_link(staged)
        # Recheck identities immediately before publishing; never unlink the command.
        if validate_source(client) != (source, source_identity):
            raise RuntimeError("source changed before installation; command was not replaced")
        if inspect_link(target) != previous:
            raise RuntimeError("command symlink changed; refusing to replace its successor")
        if previous is None:
            # Atomic no-clobber creation, including a competing file/dir/symlink.
            os.link(staged, target, follow_symlinks=False)
        else:
            os.replace(staged, target)
        installed = inspect_link(target)
        if (installed is None or installed[0][:2] != staged_identity[:2]
                or installed[1] != staged_destination
                or validate_source(client) != (source, source_identity)
                or inspect_link(target) != installed):
            # Do not remove/restore the command: a successor may already own it.
            raise RuntimeError("source or command changed after publication; inspect before retrying")

    if previous is not None:
        print(f"{NAME}: {action}: {target} -> {previous[1]}")
    print(f"{NAME}: installed {target} -> {relative_source}")
    print("No code was fetched, remote helper run, SSH config, Pi extension, or running session changed.")
    return 0


try:
    sys.exit(main())
except (OSError, RuntimeError) as error:
    print(f"{NAME}: {error}", file=sys.stderr)
    sys.exit(1)
PY
