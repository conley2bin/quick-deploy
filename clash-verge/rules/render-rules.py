#!/usr/bin/env python3
"""Validate local Mihomo route sources, registry bindings, and render Verge Script.js."""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as error:
    raise SystemExit(
        "PyYAML is required to read route sources. Install clash-verge/rules/requirements.txt."
    ) from error


class SourceError(Exception):
    pass


@dataclass(frozen=True)
class Rule:
    text: str
    normalized: str
    selector: tuple[str, ...]
    target: str
    source: Path
    line: int
    phase: str


DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*$")
KEYWORD_RE = re.compile(r"^[A-Za-z0-9._-]+$")
COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")
RESERVED_POLICIES = {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "GLOBAL", "MATCH"}
SUPPORTED_TYPES = {
    "DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "DST-PORT", "GEOIP", "IP-CIDR", "IP-CIDR6"
}
NO_RESOLVE_TYPES = {"GEOIP", "IP-CIDR", "IP-CIDR6"}
LEGACY_GITHUB_RULE = "DOMAIN,ssh.github.com,DIRECT"


def fail(path: Path, line: int, message: str) -> None:
    raise SourceError(f"{path}:{line}: {message}")


def normalized_rule(text: str) -> str:
    parts = [part.strip() for part in text.split(",")]
    if parts:
        parts[0] = parts[0].upper()
    return ",".join(parts)


def safe_file(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise SourceError(f"{context}: file must be a non-empty string")
    if value in {".", ".."} or "/" in value or "\\" in value or value.startswith(".") or ".." in value:
        raise SourceError(f"{context}: unsafe profile file name {value!r}")
    return value


def valid_domain(value: str) -> bool:
    return bool(DOMAIN_RE.fullmatch(value.rstrip(".")))


def valid_port(value: str) -> bool:
    try:
        if "-" in value:
            if value.count("-") != 1:
                return False
            start, end = (int(part) for part in value.split("-"))
            return 1 <= start <= end <= 65535
        return 1 <= int(value) <= 65535
    except ValueError:
        return False


def valid_cidr(value: str, expected_version: int | None) -> bool:
    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError:
        return False
    return expected_version is None or network.version == expected_version


def parse_rule(path: Path, line: int, text: str, source: Path, phase: str) -> Rule:
    if not text or "\n" in text:
        fail(path, line, "rule must be a non-empty one-line string")
    parts = [part.strip() for part in text.split(",")]
    if any(not part for part in parts):
        fail(path, line, "rule has an empty field")
    rule_type = parts[0].upper()
    if rule_type not in SUPPORTED_TYPES:
        fail(path, line, f"unsupported matcher {parts[0]!r}; supported: {', '.join(sorted(SUPPORTED_TYPES))}")
    parts[0] = rule_type
    has_no_resolve = parts[-1].lower() == "no-resolve"
    if any(part.lower() == "no-resolve" for part in parts[:-1]):
        fail(path, line, "no-resolve is allowed only once as the final option")
    option_count = 1 if has_no_resolve else 0
    if has_no_resolve:
        parts[-1] = "no-resolve"
        if rule_type not in NO_RESOLVE_TYPES:
            fail(path, line, f"no-resolve is not supported for {rule_type}")
    if len(parts) != 3 + option_count:
        fail(path, line, f"{rule_type} requires one payload, one policy target, and optional final no-resolve")
    payload, target = parts[1], parts[2]
    if rule_type in {"DOMAIN", "DOMAIN-SUFFIX"} and not valid_domain(payload):
        fail(path, line, f"{rule_type} payload must be a domain name")
    if rule_type == "DOMAIN-KEYWORD" and not KEYWORD_RE.fullmatch(payload):
        fail(path, line, "DOMAIN-KEYWORD payload may contain only letters, digits, dot, underscore, and hyphen")
    if rule_type == "DST-PORT" and not valid_port(payload):
        fail(path, line, "DST-PORT payload must be 1-65535 or an ascending range")
    if rule_type == "GEOIP" and not COUNTRY_RE.fullmatch(payload):
        fail(path, line, "GEOIP payload must be a two-letter country code")
    if rule_type == "IP-CIDR" and not valid_cidr(payload, None):
        fail(path, line, "IP-CIDR payload must be a valid IPv4 or IPv6 CIDR")
    if rule_type == "IP-CIDR6" and not valid_cidr(payload, 6):
        fail(path, line, "IP-CIDR6 payload must be a valid IPv6 CIDR")
    canonical = ",".join(parts)
    return Rule(canonical, canonical, (rule_type, payload), target, source, line, phase)


def scalar_key(node: yaml.nodes.Node, path: Path) -> str:
    if not isinstance(node, yaml.nodes.ScalarNode):
        fail(path, node.start_mark.line + 1, "mapping keys must be scalars")
    return str(node.value)


def load_source(path: Path, expected_target: str) -> dict[str, list[Rule]]:
    try:
        root = yaml.compose(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise SourceError(f"{path}: cannot read source: {error}") from error
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        where = f":{mark.line + 1}" if mark else ""
        raise SourceError(f"{path}{where}: invalid YAML: {error.problem or error}") from error
    if not isinstance(root, yaml.nodes.MappingNode):
        fail(path, 1, "root must be a mapping")
    fields: dict[str, yaml.nodes.Node] = {}
    for key_node, value_node in root.value:
        key = scalar_key(key_node, path)
        if key in fields:
            fail(path, key_node.start_mark.line + 1, f"duplicate field {key!r}")
        fields[key] = value_node
    required = {"version", "pre", "post"}
    unknown, missing = set(fields) - required, required - set(fields)
    if unknown:
        fail(path, 1, f"unknown field(s): {', '.join(sorted(unknown))}")
    if missing:
        fail(path, 1, f"missing field(s): {', '.join(sorted(missing))}")
    version = fields["version"]
    if not isinstance(version, yaml.nodes.ScalarNode) or str(version.value) != "1":
        fail(path, version.start_mark.line + 1, "version must be 1")
    result: dict[str, list[Rule]] = {"pre": [], "post": []}
    for phase in ("pre", "post"):
        entries = fields[phase]
        if not isinstance(entries, yaml.nodes.SequenceNode):
            fail(path, entries.start_mark.line + 1, f"{phase} must be a YAML list")
        for entry in entries.value:
            if not isinstance(entry, yaml.nodes.ScalarNode) or entry.tag != "tag:yaml.org,2002:str":
                fail(path, entry.start_mark.line + 1, f"{phase} entries must be quoted rule strings")
            rule = parse_rule(path, entry.start_mark.line + 1, entry.value, path, phase)
            if expected_target == "DIRECT":
                if rule.target.upper() != "DIRECT":
                    fail(path, rule.line, f"direct rules must target DIRECT, got {rule.target!r}")
                canonical_parts = rule.text.split(",")
                canonical_parts[2] = "DIRECT"
                canonical = ",".join(canonical_parts)
                rule = Rule(canonical, canonical, rule.selector, "DIRECT", rule.source, rule.line, rule.phase)
            elif rule.target.upper() in RESERVED_POLICIES:
                fail(path, rule.line, f"proxy rules must name a subscription proxy group, not {rule.target!r}")
            result[phase].append(rule)
    return result


def domain_selector(rule: Rule) -> tuple[str, str] | None:
    kind, value = rule.selector
    if kind not in {"DOMAIN", "DOMAIN-SUFFIX"}:
        return None
    return kind, value.lower().rstrip(".")


def domains_overlap(left_kind: str, left: str, right_kind: str, right: str) -> bool:
    if left_kind == right_kind == "DOMAIN":
        return left == right
    if left_kind == "DOMAIN":
        return left == right or left.endswith("." + right)
    if right_kind == "DOMAIN":
        return right == left or right.endswith("." + left)
    return left == right or left.endswith("." + right) or right.endswith("." + left)


def validate(rules: dict[str, list[Rule]]) -> None:
    all_rules = [rule for phase in ("pre", "post") for rule in rules[phase]]
    by_normalized: dict[str, Rule] = {}
    by_selector: dict[tuple[str, ...], Rule] = {}
    for rule in all_rules:
        if rule.normalized in by_normalized:
            previous = by_normalized[rule.normalized]
            fail(rule.source, rule.line, f"duplicate local rule; first declared at {previous.source}:{previous.line}")
        by_normalized[rule.normalized] = rule
        if rule.selector in by_selector and by_selector[rule.selector].target != rule.target:
            previous = by_selector[rule.selector]
            fail(rule.source, rule.line,
                 f"selector conflicts with {previous.source}:{previous.line} ({previous.target!r} vs {rule.target!r})")
        by_selector[rule.selector] = rule
    for phase in ("pre", "post"):
        for index, left in enumerate(rules[phase]):
            for right in rules[phase][index + 1:]:
                if left.target == right.target:
                    continue
                left_domain, right_domain = domain_selector(left), domain_selector(right)
                if left_domain and right_domain and domains_overlap(*left_domain, *right_domain):
                    fail(right.source, right.line,
                         f"same-phase domain overlap with {left.source}:{left.line}; Mihomo uses first match, not specificity")


def load_registry(path: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise SourceError(f"{path}: cannot read registry: {error}") from error
    except yaml.YAMLError as error:
        raise SourceError(f"{path}: invalid registry YAML: {error}") from error
    if not isinstance(document, dict) or not isinstance(document.get("items"), list):
        raise SourceError(f"{path}: registry must contain an items list")
    items = document["items"]
    if not all(isinstance(item, dict) for item in items):
        raise SourceError(f"{path}: each registry item must be a mapping")
    by_uid: dict[str, dict[str, Any]] = {}
    for item in items:
        uid = item.get("uid")
        if isinstance(uid, str) and uid:
            if uid in by_uid:
                raise SourceError(f"{path}: duplicate registry uid {uid!r}")
            by_uid[uid] = item
    return items, by_uid


def script_target(registry: Path) -> str:
    items, _ = load_registry(registry)
    scripts = [item for item in items if item.get("uid") == "Script"]
    if len(scripts) != 1:
        raise SourceError(f"{registry}: require exactly one uid Script entry, found {len(scripts)}")
    script = scripts[0]
    if script.get("type") != "script":
        raise SourceError(f"{registry}: uid Script must have type script")
    return safe_file(script.get("file"), f"{registry}: uid Script")


def extension_rules(path: Path) -> list[tuple[int, str]]:
    try:
        root = yaml.compose(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise SourceError(f"{path}: cannot inspect bound Rules extension: {error}") from error
    except yaml.YAMLError as error:
        raise SourceError(f"{path}: invalid bound Rules extension YAML: {error}") from error
    if root is None:
        return []
    if not isinstance(root, yaml.nodes.MappingNode):
        raise SourceError(f"{path}: bound Rules extension root must be a mapping")
    found: list[tuple[int, str]] = []
    for key, value in root.value:
        if not isinstance(key, yaml.nodes.ScalarNode) or key.value not in {"prepend", "append"}:
            continue
        if not isinstance(value, yaml.nodes.SequenceNode):
            raise SourceError(f"{path}:{value.start_mark.line + 1}: {key.value} must be a list")
        for entry in value.value:
            if not isinstance(entry, yaml.nodes.ScalarNode) or entry.tag != "tag:yaml.org,2002:str":
                raise SourceError(f"{path}:{entry.start_mark.line + 1}: extension rules must be strings")
            found.append((entry.start_mark.line + 1, normalized_rule(entry.value)))
    return found


def migration_check(registry: Path, local_rules: dict[str, list[Rule]]) -> None:
    items, by_uid = load_registry(registry)
    tracked = {rule.normalized for phase in ("pre", "post") for rule in local_rules[phase]}
    tracked.add(LEGACY_GITHUB_RULE)
    failures: list[str] = []
    for profile in items:
        if profile.get("type") != "remote":
            continue
        option = profile.get("option")
        if not isinstance(option, dict) or not isinstance(option.get("rules"), str):
            continue
        rules_uid = option["rules"]
        rules_item = by_uid.get(rules_uid)
        if not rules_item or rules_item.get("type") != "rules":
            raise SourceError(f"{registry}: remote profile references invalid Rules uid {rules_uid!r}")
        filename = safe_file(rules_item.get("file"), f"{registry}: Rules uid {rules_uid!r}")
        path = registry.parent / "profiles" / filename
        for line, rule in extension_rules(path):
            if rule in tracked:
                failures.append(f"{path}:{line}: {rule}")
    if failures:
        listed = "\n  ".join(failures)
        raise SourceError(
            "legacy per-profile Rules duplicates must be removed manually before global rules apply:\n"
            f"  {listed}\nRemove only those local extension entries, then run rules apply again."
        )


def render(rules: dict[str, list[Rule]]) -> str:
    packed = {phase: [rule.text for rule in rules[phase]] for phase in ("pre", "post")}
    proxy_targets = sorted({rule.target for phase in ("pre", "post") for rule in rules[phase]
                            if rule.target != "DIRECT"})
    return """// Generated by tun-fix.sh from rules/direct.yaml and rules/proxy.yaml. Do not edit.\n\nconst localRules = %s;\nconst requiredProxyGroups = %s;\n\nfunction localRulesFailure(config, code, detail) {\n  // Clash Verge Rev 2.5.2 swallows thrown Script errors and returns the original\n  // config. Return a JSON object with an unsupported Mihomo matcher instead so\n  // the following core validation fails visibly instead of dropping overrides.\n  const marker = \"LOCAL-RULES-ERROR-\" + code + \"-\" + String(detail).replace(/[^A-Za-z0-9-]/g, \"-\");\n  config.rules = [marker + \",local-route-guard,DIRECT\"];\n  return config;\n}\n\nfunction main(config) {\n  const groups = Array.isArray(config[\"proxy-groups\"]) ? config[\"proxy-groups\"] : [];\n  const groupNames = new Set(groups.map(group => group && group.name).filter(Boolean));\n  const missingGroups = requiredProxyGroups.filter(name => !groupNames.has(name));\n  if (missingGroups.length) {\n    return localRulesFailure(config, \"MISSING-PROXY-GROUP\", missingGroups.join(\"-\"));\n  }\n\n  const norm = (rule) => {\n    const parts = String(rule).split(\",\").map(part => part.trim());\n    if (parts.length) parts[0] = parts[0].toUpperCase();\n    return parts.join(\",\");\n  };\n  const base = Array.isArray(config.rules) ? config.rules.slice() : [];\n  const pre = [...localRules.pre];\n  const post = [...localRules.post];\n  const preSet = new Set(pre.map(norm));\n  const withoutPre = base.filter(rule => !preSet.has(norm(rule)));\n  const matchIndex = withoutPre.findIndex(rule => norm(rule).split(\",\")[0] === \"MATCH\");\n  if (matchIndex < 0) {\n    return localRulesFailure(config, \"MISSING-MATCH\", \"incoming-rules\");\n  }\n  const beforeMatch = withoutPre.slice(0, matchIndex);\n  const fromMatch = withoutPre.slice(matchIndex);\n  const existing = new Set(withoutPre.map(norm));\n  const missingPost = post.filter(rule => !existing.has(norm(rule)));\n  config.rules = [...pre, ...beforeMatch, ...missingPost, ...fromMatch];\n  return config;\n}\n""" % (json.dumps(packed, ensure_ascii=False, indent=2), json.dumps(proxy_targets, ensure_ascii=False))


def parsed_sources(direct: Path, proxy: Path) -> dict[str, list[Rule]]:
    direct_rules, proxy_rules = load_source(direct, "DIRECT"), load_source(proxy, "PROXY")
    rules = {phase: direct_rules[phase] + proxy_rules[phase] for phase in ("pre", "post")}
    validate(rules)
    return rules


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--direct", type=Path)
    parser.add_argument("--proxy", type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--script-target", action="store_true")
    parser.add_argument("--migration-check", action="store_true")
    args = parser.parse_args()
    actions = sum((args.check, args.render, args.script_target, args.migration_check))
    if actions != 1:
        parser.error("choose exactly one action")
    try:
        if args.script_target:
            if not args.registry:
                parser.error("--script-target requires --registry")
            print(script_target(args.registry))
            return 0
        if not args.direct or not args.proxy:
            parser.error("source actions require --direct and --proxy")
        rules = parsed_sources(args.direct, args.proxy)
        if args.migration_check:
            if not args.registry:
                parser.error("--migration-check requires --registry")
            migration_check(args.registry, rules)
            print("no bound local Rules duplicates")
        elif args.render:
            print(render(rules), end="")
        else:
            print(f"route sources valid: {sum(len(value) for value in rules.values())} rule(s)")
    except SourceError as error:
        print(f"route source error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
