#!/usr/bin/env python3
"""Read-only, evidence-backed discovery of five native Linux desktop apps.

This module deliberately resolves only package-manifest paths for known packages.
Desktop ``Exec`` values are parsed as data for corroboration and never evaluated.
Process observations read only ``/proc/<pid>/comm`` and ``exe`` links, never command
lines.  Results are suitable for a later rule renderer, but this milestone does
not alter routing policy or generate Mihomo configuration.

Public API:
  * ``resolve_app(app_id, evidence, inspector=...) -> DiscoveryResult``
  * ``discover_host(app_ids=None) -> tuple[DiscoveryResult, ...]``
  * ``DiscoveryResult.as_dict()`` for a stable JSON-shaped report.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class AppSpec:
    app_id: str
    name: str
    packages: tuple[str, ...]
    desktop_files: tuple[str, ...]
    process_names: tuple[str, ...]
    # role, path relative to the dynamically discovered install root, required
    programs: tuple[tuple[str, str, bool], ...]
    # number of parent directories from the main executable to its install root
    main_root_parents: int


SPECS: dict[str, AppSpec] = {
    "baidunetdisk": AppSpec(
        "baidunetdisk", "Baidu Netdisk", ("baidunetdisk",),
        ("baidunetdisk.desktop",), ("baidunetdisk", "netdisk_service"),
        (("main", "baidunetdisk", True), ("helper", "netdisk_service", True)), 1,
    ),
    "wemeet": AppSpec(
        "wemeet", "Tencent Meeting", ("wemeet",), ("wemeetapp.desktop",),
        ("wemeetapp",), (("main", "bin/wemeetapp", True),), 2,
    ),
    "feishu": AppSpec(
        "feishu", "Feishu", ("bytedance-feishu-stable",),
        ("bytedance-feishu.desktop",), ("feishu",),
        (("main", "feishu/feishu", True),), 2,
    ),
    "wechat": AppSpec(
        "wechat", "WeChat", ("wechat",), ("wechat.desktop",), ("wechat",),
        (("main", "wechat", True),), 1,
    ),
    "spark-store": AppSpec(
        "spark-store", "Spark Store", ("spark-store",), ("spark-store.desktop",),
        ("spark-store",), (("main", "bin/spark-store", True),), 2,
    ),
}
GENERIC_RUNTIME_BASENAMES = {
    "aria2c", "bash", "dash", "node", "nodejs", "python", "python3", "sh", "wine", "wineserver",
}


@dataclass(frozen=True)
class DesktopEntry:
    path: str
    exec_path: str | None
    error: str | None = None


@dataclass(frozen=True)
class ProcessEntry:
    pid: int
    comm: str
    executable: str | None


@dataclass(frozen=True)
class Evidence:
    platform: str
    package_files: Mapping[str, Sequence[str]]
    desktops: Sequence[DesktopEntry] = ()
    processes: Sequence[ProcessEntry] = ()


@dataclass(frozen=True)
class FileIdentity:
    exists: bool
    executable: bool
    resolved_path: str | None
    elf: bool


@dataclass(frozen=True)
class DiscoveredExecutable:
    path: str
    role: str
    evidence: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {"path": self.path, "role": self.role, "evidence": list(self.evidence)}


@dataclass(frozen=True)
class DiscoveryResult:
    app_id: str
    name: str
    status: str  # resolved, missing, ambiguous, unsupported
    executables: tuple[DiscoveredExecutable, ...]
    evidence: tuple[str, ...]
    reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "id": self.app_id,
            "name": self.name,
            "status": self.status,
            "executables": [executable.as_dict() for executable in self.executables],
            "evidence": list(self.evidence),
        }
        if self.reason:
            result["reason"] = self.reason
        return result


def supported_ids() -> tuple[str, ...]:
    """Return the complete, fixed canonical-ID vocabulary."""
    return tuple(SPECS)


def parse_desktop_entry(path: str, text: str) -> DesktopEntry:
    """Read a desktop entry's first Exec token without ever invoking it."""
    in_entry = False
    exec_value: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            in_entry = line == "[Desktop Entry]"
            continue
        if in_entry and line.startswith("Exec="):
            exec_value = line[5:]
            break
    if not exec_value:
        return DesktopEntry(path, None, "missing Exec in Desktop Entry")
    try:
        tokens = shlex.split(exec_value, posix=True)
    except ValueError as error:
        return DesktopEntry(path, None, f"malformed Exec: {error}")
    if not tokens:
        return DesktopEntry(path, None, "empty Exec")
    return DesktopEntry(path, tokens[0])


def local_identity(path: str) -> FileIdentity:
    """Identify one candidate path without inspecting any app data."""
    try:
        mode = os.stat(path).st_mode
        resolved = os.path.realpath(path)
        with open(resolved, "rb") as binary:
            elf = binary.read(4) == b"\x7fELF"
    except OSError:
        return FileIdentity(False, False, None, False)
    return FileIdentity(True, bool(mode & stat.S_IXUSR), resolved, elf)


def safe_process_path(path: str) -> bool:
    """Mihomo PROCESS-PATH payloads are comma-delimited and must be one line."""
    return (
        path.startswith("/")
        and "," not in path
        and not any(ord(character) < 32 or ord(character) == 127 for character in path)
    )


def _candidate_roots(spec: AppSpec, package_files: Mapping[str, Sequence[str]]) -> dict[str, dict[str, set[str]]]:
    """Map dynamically observed package-manifest roots to their declared roles."""
    roots: dict[str, dict[str, set[str]]] = {}
    main_relative = dict((role, relative) for role, relative, _required in spec.programs)["main"]
    main_name = Path(main_relative).name
    for package in spec.packages:
        for raw_path in package_files.get(package, ()):
            candidate = Path(raw_path)
            if not isinstance(raw_path, str) or not raw_path.startswith("/") or candidate.name != main_name:
                continue
            try:
                root = candidate.parents[spec.main_root_parents - 1]
            except IndexError:
                continue
            if candidate != root / main_relative:
                continue
            manifest_paths = set(package_files.get(package, ()))
            roles: dict[str, set[str]] = {}
            for role, relative, _required in spec.programs:
                expected = str(root / relative)
                if expected in manifest_paths:
                    roles[role] = {expected}
            roots[str(root)] = roles
    return roots


def _desktop_evidence(desktops: Sequence[DesktopEntry], paths: set[str]) -> list[str]:
    evidence: list[str] = []
    for desktop in desktops:
        if desktop.error:
            evidence.append(f"desktop:{desktop.path}: {desktop.error}")
            continue
        if desktop.exec_path is None:
            continue
        if desktop.exec_path in paths:
            evidence.append(f"desktop:{desktop.path}: Exec matches manifest path")
        elif not desktop.exec_path.startswith("/"):
            evidence.append(f"desktop:{desktop.path}: non-absolute Exec cannot identify an installation")
        else:
            evidence.append(f"desktop:{desktop.path}: Exec does not match manifest executable")
    return evidence


def _process_evidence(processes: Sequence[ProcessEntry], paths: set[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {path: [] for path in paths}
    for process in processes:
        if process.executable and process.executable in found:
            found[process.executable].append(f"proc:{process.pid}: comm={process.comm}")
    return found


def resolve_app(
    app_id: str,
    evidence: Evidence,
    inspector: Callable[[str], FileIdentity] = local_identity,
) -> DiscoveryResult:
    """Resolve one canonical app ID from package metadata, safely and deterministically.

    A package must identify every required app-owned executable in one root.  Two
    valid roots are an ambiguity, even if the package happens to own both.  A
    shell wrapper, shared interpreter, broken symlink, non-executable, or unsafe
    delimiter path never becomes a candidate process rule.
    """
    if app_id not in SPECS:
        raise ValueError(f"unsupported app id {app_id!r}; supported: {', '.join(supported_ids())}")
    spec = SPECS[app_id]
    if evidence.platform != "linux":
        return DiscoveryResult(spec.app_id, spec.name, "unsupported", (), (), "Linux native package discovery only")

    roots = _candidate_roots(spec, evidence.package_files)
    if not roots:
        return DiscoveryResult(spec.app_id, spec.name, "missing", (), (), "no known installed package manifest entry")

    valid_roots: list[tuple[str, tuple[DiscoveredExecutable, ...]]] = []
    invalid_reasons: list[str] = []
    for root in sorted(roots):
        role_paths = roots[root]
        manifest_paths = {
            path for package in spec.packages for path in evidence.package_files.get(package, ())
        }
        discovered: list[DiscoveredExecutable] = []
        failure: str | None = None
        for role, _relative, required in spec.programs:
            raw_candidates = sorted(role_paths.get(role, ()))
            if not raw_candidates:
                if required:
                    failure = f"{root}: required {role} executable absent from package manifest"
                continue
            accepted: list[tuple[str, str]] = []
            for raw_path in raw_candidates:
                identity = inspector(raw_path)
                resolved = identity.resolved_path
                if not identity.exists or not identity.executable or not resolved:
                    failure = f"{root}: {role} is missing or not executable"
                    continue
                if not identity.elf:
                    failure = f"{root}: {role} is a wrapper or non-native executable"
                    continue
                if resolved not in manifest_paths:
                    failure = f"{root}: {role} target lacks package-manifest identity evidence"
                    continue
                if Path(resolved).name in GENERIC_RUNTIME_BASENAMES:
                    failure = f"{root}: {role} resolves to a shared runtime"
                    continue
                if not (resolved == root or resolved.startswith(root + "/")):
                    failure = f"{root}: {role} resolves outside its package installation"
                    continue
                if not safe_process_path(resolved):
                    failure = f"{root}: {role} path cannot be represented as PROCESS-PATH"
                    continue
                accepted.append((resolved, raw_path))
            accepted = sorted(set(accepted))
            if len(accepted) != 1:
                if not failure:
                    failure = f"{root}: {role} has no unique native executable"
                continue
            resolved, raw_path = accepted[0]
            discovered.append(
                DiscoveredExecutable(
                    resolved, role, (f"package-manifest:{spec.packages[0]}:{raw_path}",)
                )
            )
        if failure:
            invalid_reasons.append(failure)
        elif len(discovered) == len(spec.programs):
            valid_roots.append((root, tuple(discovered)))

    all_manifest_paths = {path for roles in roots.values() for candidates in roles.values() for path in candidates}
    matching_desktops = tuple(
        desktop for desktop in evidence.desktops if Path(desktop.path).name in spec.desktop_files
    )
    desktop_evidence = _desktop_evidence(matching_desktops, all_manifest_paths)
    if not valid_roots:
        reason = "; ".join(sorted(set(invalid_reasons))) or "no complete native installation"
        unsupported = any(
            marker in reason
            for marker in (
                "wrapper or non-native", "resolves outside", "cannot be represented",
                "target lacks package-manifest", "shared runtime",
            )
        )
        return DiscoveryResult(
            spec.app_id, spec.name, "unsupported" if unsupported else "missing", (),
            tuple(desktop_evidence), reason,
        )
    if len(valid_roots) > 1:
        names = ", ".join(root for root, _programs in valid_roots)
        return DiscoveryResult(
            spec.app_id, spec.name, "ambiguous", (), tuple(desktop_evidence),
            f"multiple complete installations: {names}",
        )

    _root, executables = valid_roots[0]
    observed_processes = _process_evidence(evidence.processes, {item.path for item in executables})
    enriched = tuple(
        DiscoveredExecutable(
            item.path, item.role,
            item.evidence + tuple(sorted(observed_processes[item.path])),
        )
        for item in executables
    )
    return DiscoveryResult(spec.app_id, spec.name, "resolved", enriched, tuple(desktop_evidence))


def _run(*command: str) -> str | None:
    try:
        return subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None


def _installed_manifest(package: str) -> tuple[str, ...]:
    status = _run("dpkg-query", "-W", "-f=${db:Status-Status}", package)
    if status is None or status.strip() != "installed":
        return ()
    files = _run("dpkg-query", "-L", package)
    return tuple(sorted(line for line in (files or "").splitlines() if line.startswith("/")))


def _read_desktops() -> tuple[DesktopEntry, ...]:
    names = {name for spec in SPECS.values() for name in spec.desktop_files}
    directories = (Path("/usr/share/applications"), Path("/usr/local/share/applications"),
                   Path.home() / ".local/share/applications")
    entries: list[DesktopEntry] = []
    for directory in directories:
        for name in sorted(names):
            candidate = directory / name
            try:
                entries.append(parse_desktop_entry(str(candidate), candidate.read_text(encoding="utf-8")))
            except OSError:
                continue
    return tuple(entries)


def _read_processes() -> tuple[ProcessEntry, ...]:
    names = {name for spec in SPECS.values() for name in spec.process_names}
    entries: list[ProcessEntry] = []
    try:
        proc_entries = os.scandir("/proc")
    except OSError:
        return ()
    with proc_entries:
        for item in proc_entries:
            if not item.name.isdigit():
                continue
            try:
                comm = (Path(item.path) / "comm").read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if comm not in names:
                continue
            try:
                executable = os.readlink(Path(item.path) / "exe")
            except OSError:
                executable = None
            entries.append(ProcessEntry(int(item.name), comm, executable))
    return tuple(sorted(entries, key=lambda item: item.pid))


def collect_host_evidence() -> Evidence:
    """Bounded Linux-only host reads: known dpkg IDs, named launchers, relevant /proc."""
    packages = {package for spec in SPECS.values() for package in spec.packages}
    return Evidence(
        "linux" if sys.platform.startswith("linux") else sys.platform,
        {package: _installed_manifest(package) for package in sorted(packages)},
        _read_desktops(), _read_processes(),
    )


def discover_host(app_ids: Iterable[str] | None = None) -> tuple[DiscoveryResult, ...]:
    """Collect a single read-only host snapshot and resolve requested IDs."""
    selected = tuple(app_ids) if app_ids is not None else supported_ids()
    host = collect_host_evidence()
    return tuple(resolve_app(app_id, host) for app_id in selected)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="read-only discovery of supported native Linux apps")
    parser.add_argument("ids", nargs="*", metavar="ID", help="canonical IDs (default: all)")
    args = parser.parse_args(argv)
    try:
        results = discover_host(args.ids or None)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps([result.as_dict() for result in results], ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if all(result.status == "resolved" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
