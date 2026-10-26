#!/usr/bin/env bash
# Exercise only a copied installer and temporary home; no apt, network or tmux server.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd -P)
WORK=$(mktemp -d /tmp/pi-tmux-recovery-install.XXXXXX)
trap '/bin/rm -rf -- "$WORK"' EXIT
CHECKOUT="$WORK/checkout"
export HOME="$WORK/home" PI_CODING_AGENT_DIR="$WORK/pi-home" TRACE="$WORK/trace"
mkdir -p "$CHECKOUT/fresh-install/modules/tmux" "$WORK/bin" "$HOME/.tmux/.git"
printf '# fixture tmux config\n' > "$HOME/.tmux/.tmux.conf"
cp "$ROOT/fresh-install/modules/tmux/install.sh" "$ROOT/fresh-install/modules/tmux/tmux.conf.local" "$CHECKOUT/fresh-install/modules/tmux/"
for name in pi-suspend-guard pi-tmux-window-status; do
  mkdir -p "$CHECKOUT/pi-agent/extensions/$name"
  cp "$ROOT/pi-agent/extensions/$name/install.sh" "$CHECKOUT/pi-agent/extensions/$name/install.sh"
done
cat > "$WORK/bin/sudo" <<'STUB'
#!/usr/bin/env bash
case "$*" in
  'apt update'|'apt install -y tmux git xclip wl-clipboard') printf 'sudo %s\n' "$*" >> "$TRACE" ;;
  *) echo "unexpected sudo: $*" >&2; exit 91 ;;
esac
STUB
cat > "$WORK/bin/git" <<'STUB'
#!/usr/bin/env bash
if [[ "$*" == "-C $HOME/.tmux pull --ff-only" ]]; then
  printf 'git pull\n' >> "$TRACE"
  exit 0
fi
# Leaf installers must take the ordinary non-Git Pi-home path.
[[ "$*" == "-C $PI_CODING_AGENT_DIR rev-parse --git-dir" ]] || { echo "unexpected git: $*" >&2; exit 91; }
exit 1
STUB
cat > "$WORK/bin/tmux" <<'STUB'
#!/usr/bin/env bash
[[ "$*" == '-V' ]] || { echo "unexpected tmux: $*" >&2; exit 91; }
printf 'tmux 3.4\n'
STUB
chmod +x "$WORK/bin/"* "$CHECKOUT/pi-agent/extensions/"*/install.sh
export PATH="$WORK/bin:$PATH"
for pass in 1 2; do
  bash "$CHECKOUT/fresh-install/modules/tmux/install.sh" > "$WORK/install-$pass.log"
  for name in pi-suspend-guard pi-tmux-window-status; do
    target="$PI_CODING_AGENT_DIR/extensions/$name"
    [[ -L "$target" && $(readlink -f "$target") == "$CHECKOUT/pi-agent/extensions/$name" ]] || { echo "missing managed $name on pass $pass" >&2; exit 1; }
  done
  [[ $(find "$PI_CODING_AGENT_DIR/extensions" -mindepth 1 -maxdepth 1 | wc -l) -eq 2 ]] || { echo 'duplicate extension link' >&2; exit 1; }
done
[[ $(grep -c '^sudo ' "$TRACE") -eq 4 && $(grep -c '^git pull$' "$TRACE") -eq 2 ]]
echo 'PASS: standalone tmux installer installs recovery owner and display once, and is idempotent'
