#!/bin/sh
set -eu
root=${1:?usage: apply-stage-c-disposable.sh DISPOSABLE_PACKAGE_ROOT}
patch_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
[ "$(node -p "require('$root/package.json').version")" = "0.2.0" ] || { echo 'expected pi-tmux-images 0.2.0' >&2; exit 1; }
check() { actual=$(sha256sum "$root/$1" | awk '{print $1}'); [ "$actual" = "$2" ] || { echo "unexpected source: $1" >&2; exit 1; }; }
check extensions/index.ts f5609ccf498e1d8a93725ded160d9cb256168c9423585f4053bb227deeb46de3
check src/runtime.ts 4d9aadc3b5f6fd76f3a90cadb071a6b49c66a3e86063285a863e01c0bbd4580d
check src/renderer.ts 5fe7c4bf7ad9db64421a0bbf30d639a528ea4a71a6fd5b3f3b75efe72e9b4c0b
check src/transcript-entry.ts 05df244cf5d747a519454f9d38824afc0d422bd8378adc9dcf6045f7be19fa14
[ ! -e "$root/src/provenance.ts" ] && [ ! -L "$root/src/provenance.ts" ] || { echo 'unexpected source: src/provenance.ts already exists' >&2; exit 1; }
patch -d "$root" -p1 --dry-run < "$patch_dir/stage-c-recent-cache.patch"
patch -d "$root" -p1 < "$patch_dir/stage-c-recent-cache.patch"
