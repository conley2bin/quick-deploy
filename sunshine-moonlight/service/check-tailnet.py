#!/usr/bin/python3
"""One-shot Sunshine start prerequisite; never creates config or opens sockets."""
import ipaddress
import os
from pathlib import Path
import pwd
import subprocess
import sys


BINDING_KEYS = (b"address_family", b"bind_address")


def binding_entries(data):
    """Only the two binding keys, with Sunshine v2026.906.222525 cursor semantics.

    config.cpp parse_option/parse_config scan bracketed values across lines (even
    brackets in comments count). Scalars retain whitespace before # or newline.
    Keep byte spans so setting a binding never edits an unknown/list value.
    Duplicate/empty/non-scalar managed entries and unclosed lists are refused,
    rather than relying on native first-wins/default behavior for broken input.
    """
    entries = {}
    pos, size = 0, len(data)
    while pos < size:
        begin = pos
        while begin < size and data[begin] in b" \t\r\n":
            begin += 1
        end = begin
        while end < size and data[end] not in b"\r\n":
            end += 1
        content = data[begin:end].split(b"#", 1)[0].rstrip(b" \t\r\n")
        eq = content.find(b"=")
        key = content[:eq].rstrip(b" \t") if eq >= 0 else content
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
                    raise ValueError("unclosed bracketed configuration value")
                if key in BINDING_KEYS:
                    raise ValueError(f"non-scalar {key.decode()} in configuration")
        if key in BINDING_KEYS:
            value = data[value_begin:end].split(b"#", 1)[0] if eq > 0 else b""
            if key in entries or not value.strip(b" \t\r\n"):
                raise ValueError(f"malformed or duplicate {key.decode()} in configuration")
            entries[key] = (value, begin, end)
        # Native parse_config advances once after parse_option, including after
        # a closing list bracket; CR consumes two bytes, assuming CRLF.
        pos = end + (2 if end < size and data[end] == ord("\r") else 1)
    return entries


def rewrite_binding(data, key, value):
    entries = binding_entries(data)
    if key not in BINDING_KEYS or not value or any(c in value for c in b" \t\r\n#[]"):
        raise ValueError("expected a plain managed binding scalar")
    replacement = key + b" = " + value
    if key in entries:
        _, begin, end = entries[key]
        old = data[begin:end]
        if b"#" in old:
            newline = b"\r\n" if data[end:end + 2] == b"\r\n" else b"\n"
            replacement = b"#" + old.split(b"#", 1)[1] + newline + replacement
        result = data[:begin] + replacement + data[end:]
    else:
        # LF also completes a terminal lone CR before appending a top-level key.
        separator = b"" if not data or data.endswith(b"\n") else b"\n"
        result = data + separator + replacement + b"\n"
    if binding_entries(result).get(key, (None,))[0] != value:
        raise ValueError("rewritten binding is not a native top-level scalar")
    return result


def binding_command(args):
    """Repository read/staged-write path; the installed prestart uses check()."""
    action, filename, *rest = args
    path = Path(filename)
    data = path.read_bytes() if path.exists() else b""
    entries = binding_entries(data)
    if action == "--binding-validate" and not rest:
        return
    if action == "--binding-get" and len(rest) == 1:
        key = rest[0].encode("ascii")
        if key not in BINDING_KEYS or key not in entries:
            raise ValueError("missing managed binding key")
        sys.stdout.buffer.write(entries[key][0] + b"\n")
        return
    if action == "--binding-set" and len(rest) == 2:
        sys.stdout.buffer.write(rewrite_binding(data, *(s.encode("ascii") for s in rest)))
        return
    raise ValueError("invalid binding command")


def check(expected_directory):
    root = os.environ.get("CONFIGURATION_DIRECTORY", "")
    if ":" in root:
        raise ValueError("multiple CONFIGURATION_DIRECTORY paths are unsupported")
    root = root or os.environ.get("XDG_CONFIG_HOME", "")
    if not root:
        home = os.environ.get("HOME") or pwd.getpwuid(os.geteuid()).pw_dir
        root = str(Path(home) / ".config")
    if not Path(root).is_absolute():
        raise ValueError("configuration root must be absolute")
    directory = (Path(root) / "sunshine").resolve()
    if str(directory) != expected_directory:
        raise ValueError(f"configuration directory changed: {directory}; expected {expected_directory}")
    conf = directory / "sunshine.conf"
    if conf.is_symlink() or not conf.is_file():
        raise ValueError(f"configuration must be a regular, non-symlink file: {conf}")
    settings = {key.decode(): entry[0].decode("ascii")
                for key, entry in binding_entries(conf.read_bytes()).items()}
    if settings.get("address_family") != "ipv4":
        raise ValueError("address_family must remain ipv4")
    address = ipaddress.IPv4Address(settings.get("bind_address", ""))
    if address not in ipaddress.IPv4Network("100.64.0.0/10"):
        raise ValueError(f"bind_address must remain a Tailnet IPv4: {address}")
    result = subprocess.run(
        ["ip", "-o", "-4", "address", "show", "dev", "tailscale0"],
        check=True, capture_output=True, text=True, timeout=5,
    )
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[2] == "inet" and fields[3].split("/")[0] == str(address):
            return
    raise ValueError(f"configured Tailnet IPv4 {address} is not assigned to tailscale0")


if __name__ == "__main__":
    try:
        if len(sys.argv) >= 3 and sys.argv[1].startswith("--binding-"):
            binding_command(sys.argv[1:])
        elif len(sys.argv) == 2:
            check(sys.argv[1])
        else:
            raise ValueError("expected one pinned configuration directory argument")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Sunshine start prerequisite: {error}; service will retry (stop the unit to cancel)", file=sys.stderr)
        sys.exit(1)
