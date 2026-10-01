"""Byte-preserving Sunshine output_name edit and compare-before-replace transaction.

The top-level scanner follows check-tailnet.py's native parse_config cursor: a
bracketed value consumes lines until its closing bracket; a managed scalar
must be unique, plain and unambiguous. No other key/value bytes are rewritten.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile

from protocol import Refusal

MANAGED = {b"output_name", b"capture", b"encoder", b"port", b"bind_address", b"address_family"}
SELECTOR = re.compile(rb"(?:[A-Za-z0-9][A-Za-z0-9_.-]{0,63}|\{[0-9a-f-]{36}\})\Z")


def entries(data: bytes, *, keys=MANAGED, lists=frozenset()):
    result = {}
    pos, size = 0, len(data)
    while pos < size:
        begin = pos
        while begin < size and data[begin] in b" \t\r\n":
            begin += 1
        end = begin
        while end < size and data[end] not in b"\r\n":
            end += 1
        line = data[begin:end]
        content = line.split(b"#", 1)[0].rstrip(b" \t\r\n")
        eq = content.find(b"=")
        key = content[:eq].rstrip(b" \t") if eq > 0 else b""
        value_begin = begin + eq + 1
        if eq > 0:
            limit = begin + len(content)
            while value_begin < limit and data[value_begin] in b" \t":
                value_begin += 1
            if value_begin < size and data[value_begin] == ord("["):
                depth, end = 1, value_begin + 1
                while end < size and depth:
                    if data[end] == ord("["):
                        depth += 1
                    elif data[end] == ord("]"):
                        depth -= 1
                    end += 1
                if depth:
                    raise Refusal("config_ambiguous", "unclosed Sunshine bracketed value")
                if key in keys and key not in lists:
                    raise Refusal("config_ambiguous", "managed key has bracketed value")
        if key in keys:
            if key in result:
                raise Refusal("config_ambiguous", "duplicate managed Sunshine key")
            comment_start = data.find(b"#", value_begin, end)
            stop = comment_start if comment_start >= 0 else end
            value = data[value_begin:stop]
            if not value or value != value.strip(b" \t\r\n") or b"\0" in value:
                raise Refusal("config_ambiguous", "managed Sunshine scalar has whitespace/empty/invalid value")
            result[key] = (value, value_begin, stop)
        pos = end + (2 if end < size and data[end] == ord("\r") else 1)
    return result


def get_settings(data):
    found = entries(data)
    def value(key, default=b""):
        try:
            return found[key][0] if key in found else default
        except (IndexError, TypeError) as exc:
            raise Refusal("config_ambiguous", "invalid managed scalar") from exc
    try:
        backend = value(b"capture").decode("ascii")
        encoder = value(b"encoder").decode("ascii")
        output = value(b"output_name").decode("ascii")
        address = value(b"bind_address").decode("ascii")
        family = value(b"address_family").decode("ascii")
        port = value(b"port", b"47989").decode("ascii")
    except UnicodeError as exc:
        raise Refusal("config_ambiguous", "non-ASCII managed value") from exc
    if not re.fullmatch(r"[1-9][0-9]{3,4}", port) or not 1029 <= int(port) <= 65514:
        raise Refusal("config_ambiguous", "invalid Sunshine port")
    return {"capture": backend, "encoder": encoder, "output_name": output, "bind_address": address,
            "address_family": family, "port": int(port)}


def select_output(data: bytes, value: str) -> bytes:
    try:
        selector = value.encode("ascii")
    except (UnicodeError, AttributeError) as exc:
        raise Refusal("invalid_selection", "capture selector must be an ASCII scalar") from exc
    if not SELECTOR.fullmatch(selector):
        raise Refusal("invalid_selection", "capture selector must be a plain scalar")
    found = entries(data)
    if b"output_name" in found:
        _, start, end = found[b"output_name"]
        updated = data[:start] + selector + data[end:]
    else:
        separator = b"" if not data or data.endswith(b"\n") else b"\n"
        updated = data + separator + b"output_name = " + selector + b"\n"
    if get_settings(updated)["output_name"] != value:
        raise Refusal("config_ambiguous", "output_name roundtrip failed")
    return updated


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_identity(info: os.stat_result):
    # Reading the file itself can update atime; compare ownership/content state,
    # not read-observation metadata.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def load(path: Path) -> tuple[bytes, os.stat_result]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        try:
            identity = os.fstat(fd)
            if (not stat.S_ISREG(identity.st_mode) or identity.st_nlink != 1 or
                    identity.st_size > 1024 * 1024 or
                    (os.name != "nt" and (identity.st_uid != os.geteuid() or identity.st_mode & 0o022))):
                raise Refusal("config_unowned", "Sunshine config must be bounded, owned and non-writable by others")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                data = stream.read(1024 * 1024 + 1)
            if (len(data) != identity.st_size or
                    stable_identity(path.lstat()) != stable_identity(identity) or
                    stable_identity(os.fstat(fd)) != stable_identity(identity)):
                raise Refusal("config_changed", "Sunshine config changed while read")
            get_settings(data)
            return data, identity
        finally:
            os.close(fd)
    except OSError as exc:
        raise Refusal("config_unowned", "Sunshine config unavailable") from exc


def same(path, before, identity):
    try:
        return not path.is_symlink() and stable_identity(path.stat()) == stable_identity(identity) and path.read_bytes() == before
    except OSError:
        return False


def replace_if_same(path: Path, expected: bytes, identity: os.stat_result, updated: bytes):
    if not same(path, expected, identity):
        raise Refusal("config_changed", "Sunshine config was changed by another process")
    fd, name = tempfile.mkstemp(prefix=".qd-display-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            if hasattr(os, "fchown"):
                os.fchown(stream.fileno(), identity.st_uid, identity.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(identity.st_mode)) if hasattr(os, "fchmod") else None
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        if not same(path, expected, identity):
            raise Refusal("config_changed", "Sunshine config changed before replace")
        os.replace(name, path)
        if os.name != "nt":
            dirfd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


# Cross-process serialization belongs to lifecycle.PrivateFiles.operation.lock,
# held for the entire config/restart/receipt/state operation, never this edit alone.
