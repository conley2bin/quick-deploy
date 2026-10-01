"""Bounded version-1 fixed-stdin host protocol; data never supplies authority.

Owner refresh(capability, revision) returns a new SELECTING revision/menu;
abort may cancel untouched selection after topology drift. status observes
ownership only, without a fresh screen snapshot. list remains native inventory.
Launch's Desktop ID is an expected value: the supervised stock client must
freshly resolve its case-insensitive title and match that exact inspected ID
BEFORE dispatch. No caller-supplied ID or completion flag is internal proof.
"""
from __future__ import annotations

import ipaddress
import json
import re

VERSION = 1
MAX_INPUT = 2048
MAX_OUTPUT = 32768
ID = re.compile(r"(?:kms:[A-Za-z0-9_.:-]{1,80}|win:\{[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\}|mac:[1-9][0-9]{0,9})\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
OWNER_ACTIONS = {"refresh", "prepare", "launch", "resume", "observe_active", "begin_release",
                 "finish_release", "abort", "control_lost"}


class Refusal(Exception):
    def __init__(self, code: str, detail: str):
        self.code, self.detail = code, detail
        super().__init__(detail)


def unique_json(data: bytes):
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise ValueError("duplicate JSON key")
            obj[key] = value
        return obj
    return json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite number")))


def request(data: bytes) -> dict:
    if type(data) is not bytes or len(data) > MAX_INPUT:
        raise Refusal("invalid_request", "request exceeds 2048 bytes")
    try:
        value = unique_json(data)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise Refusal("invalid_request", "request is not unique-key UTF-8 JSON") from exc
    if type(value) is not dict or type(value.get("version")) is not int or value["version"] != VERSION:
        raise Refusal("invalid_request", "expected protocol version 1 object")
    action = value.get("action")
    if type(action) is not str or action not in OWNER_ACTIONS | {"list", "status", "enroll", "acquire"}:
        raise Refusal("invalid_request", "unknown action")
    keys = {"version", "action", "target"}
    if action not in {"list", "status"}:
        keys.add("revision")
    if action in OWNER_ACTIONS:
        keys.add("capability")
    if action in {"acquire", "prepare"}:
        keys.add("snapshot")
    if action == "prepare":
        keys.add("display_id")
    if set(value) != keys:
        raise Refusal("invalid_request", "unknown or missing action fields")
    target = value["target"]
    if type(target) is not dict or set(target) != {"address", "port"}:
        raise Refusal("invalid_request", "target must contain address and port")
    try:
        if type(target["address"]) is not str:
            raise ValueError("non-string")
        ip = ipaddress.IPv4Address(target["address"])
    except (ValueError, TypeError) as exc:
        raise Refusal("invalid_request", "target address must be a Tailnet IPv4 literal") from exc
    if str(ip) != target["address"] or ip not in ipaddress.IPv4Network("100.64.0.0/10"):
        raise Refusal("invalid_request", "target address must be canonical Tailnet IPv4")
    if type(target["port"]) is not int or not 1029 <= target["port"] <= 65514:
        raise Refusal("invalid_request", "target port out of range")
    if "revision" in keys and (type(value["revision"]) is not int or not 0 <= value["revision"] < (1 << 63) - 1):
        raise Refusal("invalid_request", "invalid revision")
    for key in ("snapshot", "capability"):
        if key in keys and (type(value[key]) is not str or not HEX.fullmatch(value[key])):
            raise Refusal("invalid_request", "invalid " + key)
    if action == "prepare" and (type(value["display_id"]) is not str or not ID.fullmatch(value["display_id"])):
        raise Refusal("invalid_request", "invalid display ID")
    return value


def response(ok: bool, **fields) -> bytes:
    data = json.dumps({"version": VERSION, "ok": ok, **fields}, ensure_ascii=True,
                      separators=(",", ":"), sort_keys=True).encode("ascii") + b"\n"
    if len(data) > MAX_OUTPUT:
        raise Refusal("internal_error", "response exceeds bound")
    return data
