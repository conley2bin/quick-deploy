#!/bin/sh
set -eu
mode=${1:?usage: replay-stage-c.sh check|apply PACKAGE_ROOT}
root=${2:?usage: replay-stage-c.sh check|apply PACKAGE_ROOT}
patch_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
patch_file="$patch_dir/stage-c-recent-cache.patch"

[ "$mode" = check ] || [ "$mode" = apply ] || { echo "unknown mode: $mode" >&2; exit 2; }
[ "$(node -p "require('$root/package.json').version")" = "0.2.0" ] || { echo 'expected pi-tmux-images 0.2.0' >&2; exit 1; }
[ "$(sha256sum "$patch_file" | awk '{print $1}')" = 1adc52386d8d1f6c748fc3425358f9c290d6c0a299bc09e7500b366a40fc60bf ] || { echo 'unexpected replay patch digest' >&2; exit 1; }

hash() { sha256sum "$root/$1" 2>/dev/null | awk '{print $1}'; }
extension=$(hash extensions/index.ts)
runtime=$(hash src/runtime.ts)
renderer=$(hash src/renderer.ts)
transcript=$(hash src/transcript-entry.ts)
if [ -f "$root/src/provenance.ts" ] && [ ! -L "$root/src/provenance.ts" ]; then provenance=$(hash src/provenance.ts); else provenance=absent; fi
capability=$(hash src/capabilities.ts)
case "$capability" in
  b9a498a9839ae04909995e530a8c052d0490653eadde7c7a5ec1941ea27357c8|c7272ee59ebc5f78c96c1740fcbcdf57cdc2480475241292cb91d3e158e0625a) ;;
  *) echo "unexpected source: src/capabilities.ts $capability" >&2; exit 1 ;;
esac

state=unknown
if [ "$extension" = f5609ccf498e1d8a93725ded160d9cb256168c9423585f4053bb227deeb46de3 ] \
  && [ "$runtime" = 4d9aadc3b5f6fd76f3a90cadb071a6b49c66a3e86063285a863e01c0bbd4580d ] \
  && [ "$renderer" = 5fe7c4bf7ad9db64421a0bbf30d639a528ea4a71a6fd5b3f3b75efe72e9b4c0b ] \
  && [ "$transcript" = 05df244cf5d747a519454f9d38824afc0d422bd8378adc9dcf6045f7be19fa14 ] \
  && [ "$provenance" = absent ]; then
  state=pristine
elif [ "$extension" = b6fa457459741708cd643fedb9fda408c5f402e268d133c7fc2f31ddbf0c29ec ] \
  && [ "$runtime" = 41c3e8b0125f2f8e97fc2ca48fd365fa90ad9c509d78b3c76648e93c012e5e8a ] \
  && [ "$renderer" = d917035605ae578a2d01a887ffe6619441ac042b055626154d87492adbef18e1 ] \
  && [ "$transcript" = f1375a025880f9095d8c31f930cc444e601ea59b6c66cba4baf818d4e56cdd47 ] \
  && [ "$provenance" = c15f4fc606fcf39806336ffb53ee899da7e2b6595c51fffa1df348665efa6f56 ]; then
  state=patched
fi

if [ "$state" = unknown ]; then
  echo "unknown/partial pi-tmux-images source state" >&2
  printf '%s\n' \
    "extensions/index.ts $extension" \
    "src/runtime.ts $runtime" \
    "src/renderer.ts $renderer" \
    "src/transcript-entry.ts $transcript" \
    "src/provenance.ts $provenance" >&2
  exit 1
fi

if [ "$mode" = check ]; then
  echo "$state"
  exit 0
fi
if [ "$state" = patched ]; then
  echo 'already-patched'
  exit 0
fi
patch -d "$root" -p1 --dry-run < "$patch_file" >/dev/null
patch -d "$root" -p1 < "$patch_file" >/dev/null
exec "$0" check "$root"
