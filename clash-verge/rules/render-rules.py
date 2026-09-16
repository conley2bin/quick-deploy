#!/usr/bin/env python3
"""Validate local Mihomo route sources and render Verge's global Script.js."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

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
    source: str
    line: int
    phase: str


def fail(path: Path, line: int, message: str) -> None:
    raise SourceError(f"{path}:{line}: {message}")


def normalized_rule(text: str) -> str:
    parts = [part.strip() for part in text.split(",")]
    if parts:
        parts[0] = parts[0].upper()
    return ",".join(parts)


def split_rule(path: Path, line: int, text: str, source: str, phase: str) -> Rule:
    if not text or "\n" in text:
        fail(path, line, "rule must be a non-empty one-line string")
    parts = [part.strip() for part in text.split(",")]
    if len(parts) < 3 or any(not part for part in parts):
        fail(path, line, "rule must contain a type, selector, and explicit policy target")
    if not parts[0].replace("-", "").isalpha():
        fail(path, line, f"invalid rule type {parts[0]!r}")
    option_count = 1 if parts[-1].lower() == "no-resolve" else 0
    target_index = len(parts) - 1 - option_count
    if target_index < 2:
        fail(path, line, "rule has no explicit policy target")
    target = parts[target_index]
    selector = tuple([parts[0].upper(), *parts[1:target_index]])
    return Rule(text=text, normalized=normalized_rule(text), selector=selector,
                target=target, source=source, line=line, phase=phase)


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
    unknown = set(fields) - required
    missing = required - set(fields)
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
            rule = split_rule(path, entry.start_mark.line + 1, entry.value, path.name, phase)
            if expected_target == "DIRECT":
                if rule.target != "DIRECT":
                    fail(path, rule.line, f"direct rules must target DIRECT, got {rule.target!r}")
            elif rule.target == "DIRECT":
                fail(path, rule.line, "proxy rules must target a subscription proxy group, not DIRECT")
            result[phase].append(rule)
    return result


def validate(rules: dict[str, list[Rule]]) -> None:
    all_rules = [rule for phase in ("pre", "post") for rule in rules[phase]]
    by_normalized: dict[str, Rule] = {}
    by_selector: dict[tuple[str, ...], Rule] = {}
    for rule in all_rules:
        previous = by_normalized.get(rule.normalized)
        if previous:
            fail(Path(rule.source), rule.line,
                 f"duplicate local rule; first declared at {previous.source}:{previous.line}")
        by_normalized[rule.normalized] = rule
        previous = by_selector.get(rule.selector)
        if previous and previous.target != rule.target:
            fail(Path(rule.source), rule.line,
                 f"selector conflicts with {previous.source}:{previous.line} ({previous.target!r} vs {rule.target!r})")
        by_selector[rule.selector] = rule

    for phase in ("pre", "post"):
        phase_rules = rules[phase]
        for index, left in enumerate(phase_rules):
            for right in phase_rules[index + 1:]:
                if left.target == right.target:
                    continue
                left_domain = domain_selector(left)
                right_domain = domain_selector(right)
                if left_domain and right_domain and domains_overlap(*left_domain, *right_domain):
                    fail(Path(right.source), right.line,
                         f"same-phase domain overlap with {left.source}:{left.line}; Mihomo uses first match, not specificity")


def domain_selector(rule: Rule) -> tuple[str, str] | None:
    if len(rule.selector) != 2:
        return None
    kind, value = rule.selector
    if kind not in {"DOMAIN", "DOMAIN-SUFFIX"}:
        return None
    return kind, value.lower().rstrip(".")


def domains_overlap(left_kind: str, left: str, right_kind: str, right: str) -> bool:
    if left_kind == "DOMAIN" and right_kind == "DOMAIN":
        return left == right
    if left_kind == "DOMAIN":
        return left == right or left.endswith("." + right)
    if right_kind == "DOMAIN":
        return right == left or right.endswith("." + left)
    return left == right or left.endswith("." + right) or right.endswith("." + left)


def render(rules: dict[str, list[Rule]]) -> str:
    packed = {phase: [rule.text for rule in rules[phase]] for phase in ("pre", "post")}
    proxy_targets = sorted({rule.target for phase in ("pre", "post") for rule in rules[phase]
                            if rule.target != "DIRECT"})
    return """// Generated by tun-fix.sh from rules/direct.yaml and rules/proxy.yaml. Do not edit.\n\nconst localRules = %s;\nconst requiredProxyGroups = %s;\n\nfunction main(config) {\n  const groups = Array.isArray(config[\"proxy-groups\"]) ? config[\"proxy-groups\"] : [];\n  const groupNames = new Set(groups.map(group => group && group.name).filter(Boolean));\n  const missingGroups = requiredProxyGroups.filter(name => !groupNames.has(name));\n  if (missingGroups.length) {\n    throw new Error(\"local proxy rule references missing group(s): \" + missingGroups.join(\", \"));\n  }\n\n  const norm = (rule) => {\n    const parts = String(rule).split(\",\").map(part => part.trim());\n    if (parts.length) parts[0] = parts[0].toUpperCase();\n    return parts.join(\",\");\n  };\n  const base = Array.isArray(config.rules) ? config.rules.slice() : [];\n  const pre = [...localRules.pre];\n  const post = [...localRules.post];\n  const preSet = new Set(pre.map(norm));\n  const postSet = new Set(post.map(norm));\n  const withoutPre = base.filter(rule => !preSet.has(norm(rule)));\n  const matchIndex = withoutPre.findIndex(rule => norm(rule).split(\",\")[0] === \"MATCH\");\n  if (matchIndex < 0) {\n    throw new Error(\"incoming rules have no MATCH; refusing to append unreachable local post rules\");\n  }\n  const beforeMatch = withoutPre.slice(0, matchIndex);\n  const fromMatch = withoutPre.slice(matchIndex);\n  const existing = new Set(withoutPre.map(norm));\n  const missingPost = post.filter(rule => !existing.has(norm(rule)));\n  config.rules = [...pre, ...beforeMatch, ...missingPost, ...fromMatch];\n  return config;\n}\n""" % (json.dumps(packed, ensure_ascii=False, indent=2), json.dumps(proxy_targets, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--direct", type=Path, required=True)
    parser.add_argument("--proxy", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    if args.check == args.render:
        parser.error("choose exactly one of --check or --render")
    try:
        direct = load_source(args.direct, "DIRECT")
        proxy = load_source(args.proxy, "PROXY")
        rules = {phase: direct[phase] + proxy[phase] for phase in ("pre", "post")}
        validate(rules)
    except SourceError as error:
        print(f"route source error: {error}", file=sys.stderr)
        return 1
    if args.render:
        print(render(rules), end="")
    else:
        print(f"route sources valid: {sum(len(v) for v in rules.values())} rule(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
