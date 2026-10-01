"""Exact official Ubuntu package qualification, not a vendor updater.

Release/source mapping: official v2026.914.233613 target_commitish is
63d35f702ee9e362e43263742981836ec0710384. That source's scripts/linux_build.sh
sets CMAKE_INSTALL_PREFIX=/usr and SUNSHINE_ASSETS_DIR=share/sunshine;
compile_definitions/unix.cmake makes the compiled path /usr/share/sunshine.
No installed executable is run. dpkg-deb only reads an independently verified
reference package; maintainer scripts and non-authority members are not extracted.
Metadata fetching and download SHA256 verification belong to common.sh.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
from pathlib import Path, PurePosixPath
import re
import tarfile
from urllib.parse import quote

from protocol import Refusal, unique_json
import runtime_linux as rt

TAG = "v2026.914.233613"
REPO = "LizardByte/Sunshine"
ASSETS = Path("/usr/share/sunshine")
MAX_PACKAGE = 128 * 1024 * 1024
MAX_ARCHIVE = 384 * 1024 * 1024
MAX_MEMBERS = 4096


def fail(detail):
    raise Refusal("build_unqualified", detail)


def upstream(version):
    if type(version) is not str:
        fail("installed package version is unavailable")
    if ":" in version:
        epoch, version = version.split(":", 1)
        if not epoch.isdigit():
            fail("package epoch is invalid")
    version = version.rsplit("-", 1)[0]
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", version):
        fail("package upstream version is invalid")
    return version


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    sha256: str
    size: int


def asset(metadata: bytes, ubuntu: str, architecture: str) -> Asset:
    if architecture not in ("amd64", "arm64") or not re.fullmatch(r"[0-9]{2}\.[0-9]{2}", ubuntu):
        fail("official Ubuntu package platform is unsupported")
    try:
        obj = unique_json(metadata)
        if (type(obj) is not dict or obj.get("tag_name") != TAG
                or obj.get("target_commitish") != rt.SOURCE_COMMIT
                or obj.get("draft") is not False or obj.get("prerelease") is not False
                or type(obj.get("assets")) is not list):
            fail("release must match the exact supported stable tag AND source commit")
        pattern = re.compile(r"sunshine_" + re.escape(TAG[1:]) + r"-1\+ubuntu"
                             + re.escape(ubuntu) + "_" + architecture + r"\.deb\Z")
        matches = [a for a in obj["assets"] if type(a) is dict
                   and type(a.get("name")) is str and pattern.fullmatch(a["name"])]
        if len(matches) != 1:
            fail("exact supported Ubuntu/architecture asset is missing or ambiguous")
        selected = matches[0]
        name, digest, size = selected["name"], selected.get("digest"), selected.get("size")
        url = f"https://github.com/{REPO}/releases/download/{TAG}/{quote(name, safe='')}"
        if selected.get("browser_download_url") != url:
            fail("asset URL is not the canonical exact release/name URL")
        if type(digest) is not str or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
            fail("exact reference asset requires an independent API SHA256 digest")
        if type(size) is not int or not 0 < size <= MAX_PACKAGE:
            fail("reference asset size is missing or outside the package bound")
        return Asset(name, url, digest[7:].lower(), size)
    except (ValueError, UnicodeError, TypeError, RecursionError) as exc:
        fail("release metadata is invalid or has duplicate keys")


@dataclass(frozen=True)
class Reference:
    asset: Asset
    executable_sha256: str
    files: dict[Path, bytes]
    directories: frozenset[Path]


def reference(metadata, package, ubuntu, architecture, installed_version, transport):
    selected = asset(metadata, ubuntu, architecture)
    # Recheck the common.sh download boundary before dpkg-deb sees the bytes.
    package = Path(package)
    before = transport.identity(package)
    if before.size != selected.size:
        fail("verified reference package size differs from release metadata")
    data = transport.read(package, limit=MAX_PACKAGE)
    if (len(data) != selected.size or transport.identity(package) != before
            or hashlib.sha256(data).hexdigest() != selected.sha256):
        fail("reference package changed or differs from independent release digest")
    if upstream(installed_version) != TAG[1:]:
        fail(f"only Sunshine {TAG[1:]} is compatible; installed version is unsupported (no downgrade)")
    fields = []
    for key in ("Package", "Architecture", "Version"):
        result = transport.run(("/usr/bin/dpkg-deb", "-f", str(package), key), timeout=10, max_output=4096)
        if result.returncode != 0:
            fail("verified package control metadata could not be read")
        fields.append(result.stdout.decode("ascii", "strict").strip())
    if fields != ["sunshine", architecture, installed_version]:
        fail("reference Package/Architecture/exact Version differs from the installation")
    result = transport.run(("/usr/bin/dpkg-deb", "--fsys-tarfile", str(package)),
                           timeout=30, max_output=MAX_ARCHIVE)
    if result.returncode != 0 or len(result.stdout) > MAX_ARCHIVE:
        fail("verified reference data archive could not be read within bounds")
    files, directories, seen, total = {}, set(), set(), 0
    fragments = {Path(prefix) / name for prefix in ("/usr/lib/systemd/user", "/lib/systemd/user")
                 for name in (rt.UNIT, rt.ALIAS)}
    try:
        with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
            for member in archive:
                name = member.name.removeprefix("./").rstrip("/")
                if not name:  # Conventional root directory entry.
                    if not member.isdir():
                        fail("invalid reference root member")
                    continue
                if (member.name.startswith("/") or ".." in PurePosixPath(name).parts
                        or any(ord(c) < 32 for c in name) or name in seen or len(seen) >= MAX_MEMBERS):
                    fail("reference archive has unsafe, duplicate or excessive member names")
                seen.add(name)
                path = Path("/") / name
                relevant = path == rt.EXECUTABLE or path in fragments or path == ASSETS or ASSETS in path.parents
                if not relevant:
                    continue  # Never materialize maintainer scripts/other package files.
                if member.isdir():
                    directories.add(path)
                elif member.isfile():
                    total += member.size
                    if member.size < 0 or total > MAX_ARCHIVE:
                        fail("reference authority files exceed extraction bounds")
                    stream = archive.extractfile(member)
                    content = stream.read(member.size + 1)
                    if len(content) != member.size:
                        fail("reference archive member is truncated")
                    if member.mode & 0o022 or (path == rt.EXECUTABLE and not member.mode & 0o111):
                        fail("reference authority permissions are incompatible")
                    files[path] = content
                    directories.update(parent for parent in path.parents if parent == ASSETS or ASSETS in parent.parents)
                else:
                    fail("reference authority members must be regular files/directories, never links")
    except (tarfile.TarError, OSError, ValueError, EOFError) as exc:
        fail("verified reference archive is malformed")
    if (rt.EXECUTABLE not in files or not fragments.intersection(files)
            or ASSETS / "apps.json" not in files or ASSETS / "box.png" not in files):
        fail("reference package lacks the pinned executable/unit/compiled assets layout")
    return Reference(selected, hashlib.sha256(files[rt.EXECUTABLE]).hexdigest(), files, frozenset(directories))


def qualify(ref: Reference, current, transport):
    """Compare independently extracted bytes to owned installed authority files."""
    import stat
    paths = {rt.EXECUTABLE, Path(current.unit.fragment_path)}
    fragment = Path(current.unit.fragment_path)
    candidates = [path for path in ref.files if path.name == fragment.name
                  and path.parent in (Path("/usr/lib/systemd/user"), Path("/lib/systemd/user"))]
    if len(candidates) != 1:
        fail("loaded vendor unit has no unique verified package counterpart")
    expected = {rt.EXECUTABLE: ref.files[rt.EXECUTABLE], fragment: ref.files[candidates[0]]}
    paths.update(path for path in ref.files if ASSETS in path.parents)
    expected.update({path: ref.files[path] for path in paths if ASSETS in path.parents})
    installed = set()

    def tree(path):
        info = transport.identity(path)
        if not stat.S_ISDIR(info.mode) or info.uid != 0 or info.mode & 0o022:
            fail("installed compiled assets directory is linked, writable or not vendor-owned")
        for name in transport.listdir(path):
            if not name or name in (".", "..") or "/" in name or any(ord(c) < 32 for c in name):
                fail("installed assets have an invalid member name")
            leaf = path / name
            identity = transport.identity(leaf)
            if stat.S_ISDIR(identity.mode):
                tree(leaf)
            else:
                installed.add(leaf)
            if len(installed) > MAX_MEMBERS:
                fail("installed assets exceed qualification bounds")
        if transport.identity(path) != info:
            fail("installed asset directory changed during qualification")

    tree(ASSETS)
    if installed != {path for path in paths if ASSETS in path.parents}:
        fail("installed asset file set differs from the verified exact package")
    fingerprints = {}
    for path, content in sorted(expected.items()):
        before = transport.identity(path)
        if (not stat.S_ISREG(before.mode) or before.uid != 0 or before.mode & 0o022
                or before.size != len(content) or (path == rt.EXECUTABLE and not before.mode & 0o111)):
            fail("installed binary/assets/unit type, ownership or size differs from reference")
        actual = transport.read(path, limit=max(len(content), 1))
        if actual != content or transport.identity(path) != before:
            fail("installed binary/assets/unit bytes changed or differ from verified reference")
        fingerprints[path] = before
    if not current.binary.supported_build or current.binary.file.sha256 != ref.executable_sha256:
        fail("runtime executable differs from the independently qualified package")
    return {"kind": "verified_vendor_package", "reference": ref.asset.url,
            "artifact_sha256": ref.asset.sha256}
