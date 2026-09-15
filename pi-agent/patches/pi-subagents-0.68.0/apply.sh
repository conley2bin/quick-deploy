#!/usr/bin/env bash
set -euo pipefail

check_only=false
if [[ ${1:-} == "--check" ]]; then
  check_only=true
  shift
fi
if [[ $# -ne 1 ]]; then
  echo "usage: $0 [--check] /path/to/pi-subagents-0.68.0" >&2
  exit 64
fi

self_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
target=$(cd -- "$1" 2>/dev/null && pwd -P) || {
  echo "target is not an accessible directory: $1" >&2
  exit 66
}
patch_file="$self_dir/0001-bind-revival-startup-protocol.patch"

hash_file() { sha256sum -- "$target/$1" | awk '{print $1}'; }
expect_hash() {
  local relative=$1 expected=$2 actual
  [[ -f "$target/$relative" ]] || { echo "guard failed: missing $relative" >&2; exit 65; }
  actual=$(hash_file "$relative")
  [[ "$actual" == "$expected" ]] || {
    echo "guard failed: $relative has sha256 $actual, expected $expected; preserving target unchanged" >&2
    exit 65
  }
}

identity=$(node -e 'const p=require(process.argv[1]);process.stdout.write(`${p.name ?? ""}@${p.version ?? ""}`)' "$target/package.json")
[[ "$identity" == "pi-subagents@0.68.0" ]] || { echo "guard failed: expected pi-subagents@0.68.0, found '$identity'" >&2; exit 65; }
printf '%s  %s\n' '3af85b735c00cfd5cdde2a95f95a96a6a4ee9a3e713c696b7059c4f6149e88fa' "$patch_file" | sha256sum --check --status || {
  echo "guard failed: replay patch content changed" >&2
  exit 65
}
expect_hash package.json 6b2d3d85c5c8a97491be89ceb7bd0d5e7d71a2a260596db8c2c019d1f6842faa

if [[ -f "$target/src/runs/shared/revival-startup-protocol.ts" ]] \
  && [[ "$(hash_file src/runs/background/async-execution.ts)" == 4023c6c472022420780f76441902dfdaacb65ce9bafb8c333671685a47f9a406 ]] \
  && [[ "$(hash_file src/runs/background/subagent-runner.ts)" == 8de49ea4b358cbc2d89d4ab0025c869874edace03cf37ccd02667fe43d0ab57b ]] \
  && [[ "$(hash_file src/runs/shared/revival-startup-protocol.ts)" == 58da65af820ab117d32d2921089d36d630f7e2e68a36c94131dd620dd259390e ]]; then
  if $check_only; then echo "guard ok: patched pi-subagents 0.68.0 source";
  else echo "already applied: guarded pi-subagents 0.68.0 startup patch"; fi
  exit 0
fi

expect_hash src/runs/background/async-execution.ts b3870b8728828840345008704f9ddcaf1dfab1461e397f28924f75affb168587
expect_hash src/runs/background/subagent-runner.ts dad54a5743ff8f112f561d701236f63a54080bfc2285b410e6c4c3134d9de382
[[ ! -e "$target/src/runs/shared/revival-startup-protocol.ts" ]] || {
  echo "guard failed: preserving existing src/runs/shared/revival-startup-protocol.ts" >&2
  exit 65
}
if $check_only; then
  echo "guard ok: pristine pi-subagents 0.68.0 source"
  exit 0
fi

patch --batch --forward --dry-run -p1 -d "$target" < "$patch_file" >/dev/null
patch --batch --forward -p1 -d "$target" < "$patch_file" >/dev/null
expect_hash src/runs/background/async-execution.ts 4023c6c472022420780f76441902dfdaacb65ce9bafb8c333671685a47f9a406
expect_hash src/runs/background/subagent-runner.ts 8de49ea4b358cbc2d89d4ab0025c869874edace03cf37ccd02667fe43d0ab57b
expect_hash src/runs/shared/revival-startup-protocol.ts 58da65af820ab117d32d2921089d36d630f7e2e68a36c94131dd620dd259390e
expect_hash package.json 6b2d3d85c5c8a97491be89ceb7bd0d5e7d71a2a260596db8c2c019d1f6842faa
echo "applied: guarded pi-subagents 0.68.0 startup patch"
