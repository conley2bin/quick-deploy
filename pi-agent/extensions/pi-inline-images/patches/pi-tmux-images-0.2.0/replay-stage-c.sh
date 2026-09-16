#!/bin/sh
set -eu
mode=${1:?usage: replay-stage-c.sh check|apply PACKAGE_ROOT}
root=${2:?usage: replay-stage-c.sh check|apply PACKAGE_ROOT}
patch_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
patch_file="$patch_dir/stage-c-recent-cache.patch"
review_upgrade="$patch_dir/stage-c-review-fixes.patch"
ownership_upgrade="$patch_dir/stage-c-ownership-fixes.patch"

[ "$mode" = check ] || [ "$mode" = apply ] || { echo "unknown mode: $mode" >&2; exit 2; }
[ "$(node -p "require('$root/package.json').version")" = "0.2.0" ] || { echo 'expected pi-tmux-images 0.2.0' >&2; exit 1; }
[ "$(sha256sum "$patch_file" | awk '{print $1}')" = f233b9eb57668791eb3159e80f68c6e26dc84816d4793fe910fa9e371a263dff ] || { echo 'unexpected replay patch digest' >&2; exit 1; }
[ "$(sha256sum "$review_upgrade" | awk '{print $1}')" = 4898db2958ee9c4541e468fc388dd663b9b1633df97de1abfe42cdabd787d46b ] || { echo 'unexpected review upgrade patch digest' >&2; exit 1; }
[ "$(sha256sum "$ownership_upgrade" | awk '{print $1}')" = 61b262f43893e8c8ac48b1ff9b23e44c095c5edc4ef769fe10375c33d159135d ] || { echo 'unexpected ownership upgrade patch digest' >&2; exit 1; }

hash() { sha256sum "$root/$1" 2>/dev/null | awk '{print $1}'; }
extension=$(hash extensions/index.ts)
automatic=$(hash src/automatic.ts)
loader=$(hash src/loader.ts)
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
  && [ "$automatic" = 1a105684fcbbb5a8e110b7add2af8941cdfbcd51d581857508ab6f525b8de381 ] \
  && [ "$loader" = 2168c57921e23d2f72abb745803644e0db8d24734d550e8bf189808231abbeb8 ] \
  && [ "$runtime" = 4d9aadc3b5f6fd76f3a90cadb071a6b49c66a3e86063285a863e01c0bbd4580d ] \
  && [ "$renderer" = 5fe7c4bf7ad9db64421a0bbf30d639a528ea4a71a6fd5b3f3b75efe72e9b4c0b ] \
  && [ "$transcript" = 05df244cf5d747a519454f9d38824afc0d422bd8378adc9dcf6045f7be19fa14 ] \
  && [ "$provenance" = absent ]; then
  state=pristine
elif [ "$extension" = b6fa457459741708cd643fedb9fda408c5f402e268d133c7fc2f31ddbf0c29ec ] \
  && [ "$automatic" = 1a105684fcbbb5a8e110b7add2af8941cdfbcd51d581857508ab6f525b8de381 ] \
  && [ "$loader" = 2168c57921e23d2f72abb745803644e0db8d24734d550e8bf189808231abbeb8 ] \
  && [ "$runtime" = 41c3e8b0125f2f8e97fc2ca48fd365fa90ad9c509d78b3c76648e93c012e5e8a ] \
  && [ "$renderer" = d917035605ae578a2d01a887ffe6619441ac042b055626154d87492adbef18e1 ] \
  && [ "$transcript" = f1375a025880f9095d8c31f930cc444e601ea59b6c66cba4baf818d4e56cdd47 ] \
  && [ "$provenance" = c15f4fc606fcf39806336ffb53ee899da7e2b6595c51fffa1df348665efa6f56 ]; then
  state=legacy-patched
elif [ "$extension" = 09b2245981f14df3a82c2acbfec874c4bf02bfd2c386b33bca21e6709b107618 ] \
  && [ "$automatic" = 1a105684fcbbb5a8e110b7add2af8941cdfbcd51d581857508ab6f525b8de381 ] \
  && [ "$loader" = 0b3996ff6fc475fac5985f1fa56a76c9009f86524b02f047812b9c0a7848ad1d ] \
  && [ "$runtime" = bb69de792f40dc5d576d26f50773ae9dd168d7e52c76abf42146e61d724c26e3 ] \
  && [ "$renderer" = d917035605ae578a2d01a887ffe6619441ac042b055626154d87492adbef18e1 ] \
  && [ "$transcript" = f1375a025880f9095d8c31f930cc444e601ea59b6c66cba4baf818d4e56cdd47 ] \
  && [ "$provenance" = c15f4fc606fcf39806336ffb53ee899da7e2b6595c51fffa1df348665efa6f56 ]; then
  state=previous-patched
elif [ "$extension" = ae0a7dcf44cfb4a0bb5656ac6aed67c929fe5378fad7b067e099dbd4c3f4b0f0 ] \
  && [ "$automatic" = 34978081cc6e08a4dec9e5415dbea0ca469b2ae7731b9503d49acb38a9aa0c13 ] \
  && [ "$loader" = 0b3996ff6fc475fac5985f1fa56a76c9009f86524b02f047812b9c0a7848ad1d ] \
  && [ "$runtime" = 0f13f7c4212b14a3065bff2a65a833eae67a4a242ae514ba962ecf0e690946b9 ] \
  && [ "$renderer" = 79f0c47716ccb8af14f3d7c1fe0923ff8933b65a5f64aa719cd71f5ed7a75c23 ] \
  && [ "$transcript" = ef9c21f1406c9a041c8db35f19fd0bfccbddabb9233ba25f585e986840103d14 ] \
  && [ "$provenance" = c15f4fc606fcf39806336ffb53ee899da7e2b6595c51fffa1df348665efa6f56 ]; then
  state=patched
fi

if [ "$state" = unknown ]; then
  echo "unknown/partial pi-tmux-images source state" >&2
  printf '%s\n' \
    "extensions/index.ts $extension" \
    "src/automatic.ts $automatic" \
    "src/loader.ts $loader" \
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
if [ "$state" = legacy-patched ]; then
  patch -d "$root" -p1 --dry-run < "$review_upgrade" >/dev/null
  patch -d "$root" -p1 < "$review_upgrade" >/dev/null
  exec "$0" apply "$root"
elif [ "$state" = previous-patched ]; then
  patch -d "$root" -p1 --dry-run < "$ownership_upgrade" >/dev/null
  patch -d "$root" -p1 < "$ownership_upgrade" >/dev/null
else
  patch -d "$root" -p1 --dry-run < "$patch_file" >/dev/null
  patch -d "$root" -p1 < "$patch_file" >/dev/null
fi
exec "$0" check "$root"
