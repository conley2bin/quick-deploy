"""Version 1 fixed-stdin protocol. Requests contain data, never commands or paths."""
from __future__ import annotations

import ipaddress
import json
import re

VERSION = 1
MAX_INPUT = 2048
MAX_OUTPUT = 16384
ID = re.compile(r"(?:kms:[A-Za-z0-9_.:-]{1,80}|win:\{[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\}|mac:[1-9][0-9]{0,9})\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")


class Refusal(Exception):
    def __init__(self, code: str, detail: str):
        self.code, self.detail = code, detail
        super().__init__(detail)


def request(data: bytes) -> dict:
    if len(data) > MAX_INPUT:
        raise Refusal("invalid_request", "request exceeds 2048 bytes")
    try:
        def pairs(items):
            obj = {}
            for key, value in items:
                if key in obj:
                    raise ValueError("duplicate JSON key")
                obj[key] = value
            return obj
        value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite number")))
    except (UnicodeError, ValueError) as exc:
        raise Refusal("invalid_request", "request is not unique-key UTF-8 JSON") from exc
    if type(value) is not dict or value.get("version") != VERSION or type(value.get("version")) is not int:
        raise Refusal("invalid_request", "expected protocol version 1 object")
    action = value.get("action")
    keys = {"version", "action", "target"} | ({"snapshot", "display_id"} if action == "prepare" else set())
    if action not in ("list", "prepare") or set(value) != keys:
        raise Refusal("invalid_request", "unknown action or fields")
    target = value["target"]
    if type(target) is not dict or set(target) != {"address", "port"}:
        raise Refusal("invalid_request", "target must contain address and port")
    try:
        ip = ipaddress.IPv4Address(target["address"])
    except (ValueError, KeyError, TypeError) as exc:
        raise Refusal("invalid_request", "target address must be a Tailnet IPv4 literal") from exc
    if str(ip) != target["address"] or ip not in ipaddress.IPv4Network("100.64.0.0/10"):
        raise Refusal("invalid_request", "target address must be canonical Tailnet IPv4")
    if type(target["port"]) is not int or not 1029 <= target["port"] <= 65514:
        raise Refusal("invalid_request", "target port out of range")
    if action == "prepare" and (type(value["snapshot"]) is not str or not HEX.fullmatch(value["snapshot"])
                                or type(value["display_id"]) is not str or not ID.fullmatch(value["display_id"])):
        raise Refusal("invalid_request", "invalid snapshot or display ID")
    return value


def response(ok: bool, **fields) -> bytes:
    data = json.dumps({"version": VERSION, "ok": ok, **fields}, ensure_ascii=True,
                      separators=(",", ":"), sort_keys=True).encode("ascii") + b"\n"
    if len(data) > MAX_OUTPUT:
        raise Refusal("internal_error", "response exceeds bound")
    return data
