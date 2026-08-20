#!/usr/bin/env bash
# Install the latest stable official Zellij release into the current user's home.
set -euo pipefail

REPO_API='https://api.github.com/repos/zellij-org/zellij/releases/latest'
RELEASE_DOWNLOAD_BASE='https://github.com/zellij-org/zellij/releases/download'
CURL_CMD="${CURL_CMD:-curl}"
UNAME_CMD="${UNAME_CMD:-uname}"
ID_CMD="${ID_CMD:-id}"
PYTHON_CMD="${PYTHON_CMD:-python3}"
SHA256SUM_CMD="${SHA256SUM_CMD:-sha256sum}"
TAR_CMD="${TAR_CMD:-tar}"
INSTALL_CMD="${INSTALL_CMD:-install}"
AWK_CMD="${AWK_CMD:-awk}"
STAT_CMD="${STAT_CMD:-stat}"
READLINK_CMD="${READLINK_CMD:-readlink}"
MKTEMP_CMD="${MKTEMP_CMD:-mktemp}"
FLOCK_CMD="${FLOCK_CMD:-flock}"
OS_RELEASE_FILE="${OS_RELEASE_FILE:-/etc/os-release}"
CHECK_ONLY=false

usage() {
    cat <<'USAGE'
Usage: ./install.sh [--check] [--help]

Installs the newest stable Zellij release for the current user. Every normal
run and --check queries GitHub's latest-release endpoint; pinned versions are
intentionally unsupported.

Options:
  --check  Read-only status and plan (GitHub API access is allowed)
  --help   Show this help
USAGE
}

die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
warn() { printf 'Warning: %s\n' "$*" >&2; }

parse_args() {
    while (($#)); do
        case "$1" in
            --check) CHECK_ONLY=true ;;
            --help|-h) usage; exit 0 ;;
            *) die "unknown option: $1 (use --help)" ;;
        esac
        shift
    done
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

preflight_dependencies() {
    local command_name
    for command_name in "$CURL_CMD" "$UNAME_CMD" "$ID_CMD" "$PYTHON_CMD" "$SHA256SUM_CMD" "$TAR_CMD" "$INSTALL_CMD" "$AWK_CMD" "$STAT_CMD" "$READLINK_CMD" "$MKTEMP_CMD" "$FLOCK_CMD" mkdir mv rm ln; do
        require_command "$command_name"
    done
}

require_ordinary_user() {
    INVOKING_UID="$("$ID_CMD" -u)" || die 'could not determine user identity'
    [[ $INVOKING_UID =~ ^[0-9]+$ ]] || die "invalid user identity returned by id: $INVOKING_UID"
    [[ $INVOKING_UID != 0 ]] || die 'do not run this installer as root or through sudo; run it as your ordinary user'
    [[ -z ${SUDO_USER:-} ]] || die 'do not run this installer through sudo; run it as your ordinary user'
}

require_supported_platform() {
    [[ -r $OS_RELEASE_FILE ]] || die "cannot read OS release data: $OS_RELEASE_FILE"
    # shellcheck disable=SC1090
    . "$OS_RELEASE_FILE"
    [[ ${ID:-} == ubuntu ]] || die "unsupported system: expected Ubuntu 24.04+, found ${PRETTY_NAME:-${ID:-unknown}}"
    [[ ${VERSION_ID:-} =~ ^([0-9]+)\.([0-9]+)$ ]] || die "invalid Ubuntu VERSION_ID: ${VERSION_ID:-missing}"
    UBUNTU_MAJOR=${BASH_REMATCH[1]}
    UBUNTU_MINOR=${BASH_REMATCH[2]}
    ((10#$UBUNTU_MAJOR > 24 || (10#$UBUNTU_MAJOR == 24 && 10#$UBUNTU_MINOR >= 4))) \
        || die "unsupported Ubuntu version: $VERSION_ID (need 24.04+)"

    ARCH="$("$UNAME_CMD" -m)" || die 'could not determine CPU architecture'
    case "$ARCH" in
        x86_64|amd64) ARCH_LABEL='amd64'; TARGET_TRIPLE='x86_64-unknown-linux-musl' ;;
        aarch64|arm64) ARCH_LABEL='arm64'; TARGET_TRIPLE='aarch64-unknown-linux-musl' ;;
        *) die "unsupported CPU architecture: $ARCH (supported: amd64, arm64)" ;;
    esac
    ARCHIVE_STEM="zellij-$TARGET_TRIPLE"
    ARCHIVE_NAME="$ARCHIVE_STEM.tar.gz"
    CHECKSUM_NAME="$ARCHIVE_STEM.sha256sum"
    UPSTREAM_BINARY_PATH="target/$TARGET_TRIPLE/release/zellij"
}

# Existing paths must be private to this uid in the ownership sense and cannot
# be symlinks. Missing descendants are valid for --check and normal setup.
validate_existing_directory() {
    local path=$1
    if [[ -e $path || -L $path ]]; then
        [[ -d $path && ! -L $path ]] || die "refusing unsafe directory path: $path"
        [[ $("$STAT_CMD" -c '%u' "$path") == "$INVOKING_UID" ]] || die "refusing directory not owned by invoking user: $path"
    fi
}

validate_path_ancestors() {
    validate_existing_directory "$HOME"
    [[ -e $HOME || -L $HOME ]] || die "HOME must be an existing directory: $HOME"
    validate_existing_directory "$HOME/.local"
    validate_existing_directory "$HOME/.local/bin"
    validate_existing_directory "$HOME/.local/opt"
    validate_existing_directory "$HOME/.local/opt/zellij"
}

prepare_managed_roots() {
    # Validation precedes and follows mkdir so an existing or newly created
    # path cannot silently redirect installation through a symlink.
    validate_path_ancestors
    mkdir -p "$HOME/.local" "$LOCAL_BIN" "$OPT_DIR"
    validate_path_ancestors
}

safe_regular_owned_file() {
    local path=$1
    [[ -f $path && ! -L $path ]] && [[ $("$STAT_CMD" -c '%u' "$path") == "$INVOKING_UID" ]]
}

api_request() {
    local token=${GITHUB_TOKEN:-${GH_TOKEN:-}}
    local -a args=(--fail --location --silent --show-error)
    [[ -n $token ]] && args+=(-H "Authorization: Bearer $token")
    "$CURL_CMD" "${args[@]}" "$REPO_API"
}

# Emits tag, archive URL, checksum URL, archive SHA-256. Python is part of
# supported Ubuntu and avoids fragile JSON parsing or a jq dependency.
fetch_release() {
    local release_json
    release_json="$(api_request)" || die "GitHub API request failed: $REPO_API"

    RELEASE_INFO="$(RELEASE_JSON="$release_json" "$PYTHON_CMD" - "$ARCHIVE_NAME" "$CHECKSUM_NAME" "$RELEASE_DOWNLOAD_BASE" <<'PY'
import json
import os
import re
import sys

archive_name, checksum_name, download_base = sys.argv[1:]
try:
    release = json.loads(os.environ["RELEASE_JSON"])
except (json.JSONDecodeError, TypeError) as error:
    raise SystemExit(f"malformed GitHub release metadata: {error}")
if not isinstance(release, dict):
    raise SystemExit("malformed GitHub release metadata: expected an object")
tag = release.get("tag_name")
if release.get("draft") is not False or release.get("prerelease") is not False:
    raise SystemExit("invalid GitHub release metadata: latest release must be non-draft and non-prerelease")
if not isinstance(tag, str) or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
    raise SystemExit("invalid GitHub release metadata: tag_name must be vMAJOR.MINOR.PATCH")
assets = release.get("assets")
if not isinstance(assets, list):
    raise SystemExit("malformed GitHub release metadata: assets must be an array")
found = {}
for asset in assets:
    if not isinstance(asset, dict):
        continue
    name = asset.get("name")
    if name not in (archive_name, checksum_name):
        continue
    if name in found:
        raise SystemExit(f"invalid GitHub release metadata: duplicate asset {name}")
    expected_url = f"{download_base}/{tag}/{name}"
    if asset.get("browser_download_url") != expected_url:
        raise SystemExit(f"invalid GitHub release metadata: asset {name} has an unexpected download URL")
    found[name] = asset
missing = [name for name in (archive_name, checksum_name) if name not in found]
if missing:
    raise SystemExit("invalid GitHub release metadata: missing required asset(s): " + ", ".join(missing))
digest = found[archive_name].get("digest")
if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9A-Fa-f]{64}", digest):
    raise SystemExit(f"invalid GitHub release metadata: archive {archive_name} needs a sha256:<64 hex> digest")
print(tag)
print(found[archive_name]["browser_download_url"])
print(found[checksum_name]["browser_download_url"])
print(digest.split(":", 1)[1].lower())
PY
 2>&1
)" || die "$RELEASE_INFO"

    mapfile -t release_fields <<<"$RELEASE_INFO"
    ((${#release_fields[@]} == 4)) || die 'invalid GitHub release metadata: incomplete selected asset data'
    LATEST_TAG=${release_fields[0]}
    ARCHIVE_URL=${release_fields[1]}
    CHECKSUM_URL=${release_fields[2]}
    ARCHIVE_SHA256=${release_fields[3]}
}

version_matches_tag_for() {
    local binary=$1 tag=$2 output expected_without_v
    output="$("$binary" --version 2>/dev/null)" || return 1
    expected_without_v=${tag#v}
    [[ $output == "zellij $expected_without_v" ]]
}

version_matches_tag() { version_matches_tag_for "$1" "$LATEST_TAG"; }

managed_target() {
    local target=$1 relative
    relative=${target#"$OPT_DIR"/}
    [[ $relative != "$target" && $relative =~ ^v[0-9]+\.[0-9]+\.[0-9]+/zellij$ ]]
}

managed_target_tag() {
    local target=$1 relative
    relative=${target#"$OPT_DIR"/}
    printf '%s\n' "${relative%%/zellij}"
}

binary_sha256() {
    "$SHA256SUM_CMD" "$1" | "$AWK_CMD" '{print tolower($1)}'
}

sidecar_digest() {
    local sidecar=$1
    mapfile -t SIDECAR_LINES < "$sidecar" || return 1
    ((${#SIDECAR_LINES[@]} == 1)) || return 1
    [[ ${SIDECAR_LINES[0]} =~ ^[0-9A-Fa-f]{64}$ ]] || return 1
    printf '%s\n' "${SIDECAR_LINES[0],,}"
}

safe_managed_binary() {
    local binary=$1 version_dir
    managed_target "$binary" || return 1
    version_dir=${binary%/zellij}
    [[ -d $version_dir && ! -L $version_dir ]] || return 1
    safe_regular_owned_file "$binary"
}

locally_verified_target() {
    local binary=$1 version_dir sidecar expected actual tag
    safe_managed_binary "$binary" || return 1
    version_dir=${binary%/zellij}
    sidecar="$version_dir/zellij.sha256"
    safe_regular_owned_file "$sidecar" || return 1
    expected="$(sidecar_digest "$sidecar")" || return 1
    actual="$(binary_sha256 "$binary")" || return 1
    [[ $actual == "$expected" ]] || return 1
    tag="$(managed_target_tag "$binary")"
    version_matches_tag_for "$binary" "$tag"
}

locally_verified_latest() { locally_verified_target "$OPT_DIR/$LATEST_TAG/zellij"; }

link_state() {
    if [[ -L $BIN_LINK ]]; then
        LINK_TARGET="$("$READLINK_CMD" "$BIN_LINK")"
        if managed_target "$LINK_TARGET"; then LINK_OWNERSHIP='managed'; else LINK_OWNERSHIP='foreign symlink'; fi
    elif [[ -e $BIN_LINK ]]; then
        LINK_TARGET=''; LINK_OWNERSHIP='foreign file'
    else
        LINK_TARGET=''; LINK_OWNERSHIP='absent'
    fi
}

current_version() {
    # Current state must never execute a foreign, unsafe, or unverified target.
    if [[ $LINK_OWNERSHIP == managed ]] && locally_verified_target "$LINK_TARGET"; then
        "$BIN_LINK" --version 2>/dev/null || printf 'unreadable'
    elif [[ $LINK_OWNERSHIP == managed ]]; then
        printf 'unverified managed target (not executed)'
    elif [[ $LINK_OWNERSHIP == foreign* ]]; then
        printf 'foreign (not executed)'
    else
        printf 'not installed'
    fi
}

path_state() {
    case ":${PATH:-}:" in *":$LOCAL_BIN:"*) printf 'present' ;; *) printf 'missing' ;; esac
}

latest_target_state() {
    local dir="$OPT_DIR/$LATEST_TAG"
    if [[ -e $dir || -L $dir ]]; then
        locally_verified_latest && printf 'verified' || printf 'blocked'
    else
        printf 'absent'
    fi
}

show_check() {
    local target_binary planned current target_state
    target_binary="$OPT_DIR/$LATEST_TAG/zellij"
    target_state="$(latest_target_state)"
    current="$(current_version)"
    printf 'Platform: Ubuntu %s, %s (%s)\n' "$VERSION_ID" "$ARCH_LABEL" "$ARCH"
    printf 'Current link: %s (%s)\n' "$BIN_LINK" "$LINK_OWNERSHIP"
    [[ -n $LINK_TARGET ]] && printf 'Current target: %s\n' "$LINK_TARGET"
    printf 'Current version: %s\n' "$current"
    printf 'Latest stable tag: %s\nSelected asset: %s\n' "$LATEST_TAG" "$ARCHIVE_NAME"
    printf 'PATH (%s): %s\n' "$LOCAL_BIN" "$(path_state)"
    [[ $(path_state) == present ]] || printf 'PATH warning: add %s to your shell PATH to invoke zellij by name.\n' "$LOCAL_BIN"

    if [[ $LINK_OWNERSHIP == foreign* ]]; then planned='blocked: ~/.local/bin/zellij belongs to another installation'
    elif [[ $target_state == blocked ]]; then planned="blocked: $OPT_DIR/$LATEST_TAG is unsafe or lacks a matching zellij.sha256 sidecar"
    elif [[ $target_state == verified && $LINK_TARGET == "$target_binary" ]]; then planned='none: latest version is installed and active; archive download will be skipped'
    elif [[ $target_state == verified ]]; then planned='atomically activate existing verified latest binary; archive download will be skipped'
    else planned="download $ARCHIVE_NAME and $CHECKSUM_NAME, verify, then atomically activate $LATEST_TAG"; fi
    printf 'Planned action: %s\n' "$planned"
}

prepare_lock() {
    local lock="$OPT_DIR/.install.lock"
    if [[ -e $lock || -L $lock ]]; then
        safe_regular_owned_file "$lock" || die "refusing unsafe installer lock: $lock"
    else
        : > "$lock"
        safe_regular_owned_file "$lock" || die "could not create a safe installer lock: $lock"
    fi
    exec 9>>"$lock"
    "$FLOCK_CMD" -n 9 || die "another Zellij installer is already running for this user"
}

verify_sha256() {
    local expected=$1 file=$2 description=$3
    printf '%s  %s\n' "$expected" "$file" | "$SHA256SUM_CMD" --check --status - || die "SHA-256 verification failed for $description"
}

checksum_for_binary() {
    local checksum_file=$1 count
    mapfile -t CHECKSUM_LINES < <("$AWK_CMD" -v filename="$UPSTREAM_BINARY_PATH" '
        ($2 == filename || $2 == "*" filename) && length($1) == 64 && $1 ~ /^[0-9A-Fa-f]+$/ { print tolower($1) }
    ' "$checksum_file")
    count=${#CHECKSUM_LINES[@]}
    ((count == 1)) || die "checksum file must contain exactly one SHA-256 entry for $UPSTREAM_BINARY_PATH"
    printf '%s\n' "${CHECKSUM_LINES[0]}"
}

validate_tar_manifest() {
    "$PYTHON_CMD" - "$1" <<'PY'
import tarfile
import sys
try:
    with tarfile.open(sys.argv[1], "r:gz") as tar: members = tar.getmembers()
except (tarfile.TarError, OSError) as error: raise SystemExit(f"could not read archive manifest: {error}")
if len(members) != 1: raise SystemExit("archive manifest must contain exactly one entry")
member = members[0]
if member.name != "zellij": raise SystemExit("archive manifest entry must be exactly top-level zellij")
if not member.isreg(): raise SystemExit("archive manifest zellij entry must be a regular file")
PY
}

activate() {
    local target=$1 old_target='' tmp_link
    locally_verified_target "$target" || die "refusing to activate locally unverified binary: $target"
    link_state
    [[ $LINK_OWNERSHIP != foreign* ]] || die "refusing to overwrite $BIN_LINK: it is a $LINK_OWNERSHIP"
    [[ $LINK_OWNERSHIP == managed ]] && old_target=$LINK_TARGET
    tmp_link="$LOCAL_BIN/.zellij-link.$$"
    rm -f "$tmp_link"; ln -s "$target" "$tmp_link"
    link_state
    if [[ $LINK_OWNERSHIP == foreign* ]]; then rm -f "$tmp_link"; die "refusing to overwrite $BIN_LINK: it became a $LINK_OWNERSHIP"; fi
    mv -Tf "$tmp_link" "$BIN_LINK"
    if ! version_matches_tag "$BIN_LINK"; then
        warn 'activated binary failed final version verification; restoring the prior active link'
        if [[ -n $old_target ]]; then ln -s "$old_target" "$tmp_link"; mv -Tf "$tmp_link" "$BIN_LINK"; else rm -f "$BIN_LINK"; fi
        die "final zellij --version did not exactly match selected tag $LATEST_TAG"
    fi
}

install_latest() {
    local target_dir="$OPT_DIR/$LATEST_TAG" target_binary="$OPT_DIR/$LATEST_TAG/zellij"
    local stage archive checksum extracted expected_binary_sha
    link_state
    [[ $LINK_OWNERSHIP != foreign* ]] || die "refusing to overwrite $BIN_LINK: it is a $LINK_OWNERSHIP"
    if [[ -e $target_dir || -L $target_dir ]]; then
        locally_verified_latest || die "refusing unsafe or locally unverified existing latest target: $target_dir"
        printf 'Latest Zellij %s is already verified locally; skipping archive and checksum downloads.\n' "$LATEST_TAG"
        activate "$target_binary"
        return
    fi

    stage="$("$MKTEMP_CMD" -d "$OPT_DIR/.zellij-install.XXXXXX")"
    trap 'rm -rf "$stage"' EXIT
    archive="$stage/$ARCHIVE_NAME"; checksum="$stage/$CHECKSUM_NAME"; extracted="$stage/extracted"
    printf 'Downloading %s for %s...\n' "$LATEST_TAG" "$ARCH_LABEL"
    "$CURL_CMD" --fail --location --silent --show-error "$ARCHIVE_URL" -o "$archive" || die "download failed: $ARCHIVE_NAME"
    "$CURL_CMD" --fail --location --silent --show-error "$CHECKSUM_URL" -o "$checksum" || die "download failed: $CHECKSUM_NAME"
    verify_sha256 "$ARCHIVE_SHA256" "$archive" "$ARCHIVE_NAME (GitHub API digest)"
    validate_tar_manifest "$archive" || die "unsafe archive manifest in $ARCHIVE_NAME"
    mkdir "$extracted"
    "$TAR_CMD" --extract --gzip --file "$archive" --directory "$extracted" --no-same-owner --no-same-permissions zellij || die "could not safely extract $ARCHIVE_NAME"
    [[ -f $extracted/zellij && ! -L $extracted/zellij ]] || die "archive did not extract a regular top-level zellij binary"
    expected_binary_sha="$(checksum_for_binary "$checksum")"
    verify_sha256 "$expected_binary_sha" "$extracted/zellij" 'extracted zellij binary'
    "$INSTALL_CMD" -m 0755 "$extracted/zellij" "$stage/zellij"
    printf '%s\n' "$expected_binary_sha" > "$stage/zellij.sha256"
    [[ -f $stage/zellij && ! -L $stage/zellij && -f $stage/zellij.sha256 && ! -L $stage/zellij.sha256 ]] || die 'staged Zellij artifacts are not regular files'
    [[ $(binary_sha256 "$stage/zellij") == "$expected_binary_sha" ]] || die 'staged zellij digest changed unexpectedly'
    version_matches_tag "$stage/zellij" || die "extracted zellij --version did not exactly match selected tag $LATEST_TAG"

    rm -rf "$extracted" "$archive" "$checksum"
    mv -T "$stage" "$target_dir"; trap - EXIT
    locally_verified_latest || die "installed zellij failed local verification"
    activate "$target_binary"
    printf 'Installed and activated Zellij %s at %s\n' "$LATEST_TAG" "$target_binary"
}

main() {
    parse_args "$@"
    # --help intentionally exits during parsing before dependency preflight.
    preflight_dependencies
    require_ordinary_user
    require_supported_platform
    HOME=${HOME:?HOME must be set}
    OPT_DIR="$HOME/.local/opt/zellij"; LOCAL_BIN="$HOME/.local/bin"; BIN_LINK="$LOCAL_BIN/zellij"
    validate_path_ancestors
    if "$CHECK_ONLY"; then
        fetch_release
        link_state
        show_check
        exit 0
    fi
    prepare_managed_roots
    prepare_lock
    # Latest resolution is inside the lock to serialize convergence/activation.
    fetch_release
    link_state
    [[ $(path_state) == present ]] || warn "$LOCAL_BIN is not in the current PATH; no shell configuration will be changed"
    install_latest
}

main "$@"
