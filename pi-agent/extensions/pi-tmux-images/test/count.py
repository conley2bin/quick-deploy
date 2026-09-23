#!/usr/bin/env python3
"""Count kitty commands, placeholder cells and text markers in captured E2E output."""
import sys

ESC_G = b"\x1b_G"
PTMUX = b"Ptmux;"
CELL = "\U0010EEEE".encode("utf-8")


def main() -> int:
    kind, path = sys.argv[1], sys.argv[2]
    data = open(path, "rb").read()
    if kind == "bytes":
        print(data.count(ESC_G) + data.count(PTMUX))
    elif kind == "cells":
        print(data.count(CELL))
    elif kind == "result":
        for line in data.decode("utf-8", "replace").splitlines():
            if "E2E_RESULT" in line:
                print(line.strip())
    else:
        raise SystemExit(f"unknown kind: {kind}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
