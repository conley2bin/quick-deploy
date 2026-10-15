#!/usr/bin/env bash
# Executed ONLY on the explicitly selected VPS. Never source this on a PC.
set -euo pipefail
umask 077
ROOT=/var/lib/quick-deploy-vps
LIB=/usr/local/lib/quick-deploy-vps
CONF=/etc/sing-box/config.json
FW=/etc/quick-deploy-vps.nft
ACTION=${1:?remote action required}
[[ $EUID == 0 ]] || { echo 'VPS requires root/passwordless sudo' >&2; exit 1; }
# Refuse other OSes before installing anything.
. /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || { echo 'Only Ubuntu 24.04 is supported; nothing installed.' >&2; exit 1; }
install -d -m 700 "$ROOT"
exec 8>"$ROOT/operation.lock"
flock -w 30 8 || { echo 'Another VPS operation is running' >&2; exit 1; }

param() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$ROOT/incoming/params.json" "$1"; }

snapshot() {
    # Snapshot only module-owned nft state; never flush other tables (Tailscale).
    local destination=$1
    install -d -m 700 "$destination"
    if [[ -f $CONF ]]; then cp -p "$CONF" "$destination/config.json"; fi
    if nft list table inet qd_vps >"$destination/table.nft" 2>/dev/null; then
        touch "$destination/had-table"
    else
        : >"$destination/table.nft"
    fi
    if [[ -f $FW ]]; then cp -p "$FW" "$destination/persistent.nft"; fi
    if [[ -f /etc/ssh/sshd_config.d/00-quick-deploy-vps.conf ]]; then
        cp -p /etc/ssh/sshd_config.d/00-quick-deploy-vps.conf "$destination/sshd.conf"
    fi
    if [[ -f /etc/systemd/system/sing-box.service.d/90-quick-deploy-vps.conf ]]; then
        cp -p /etc/systemd/system/sing-box.service.d/90-quick-deploy-vps.conf "$destination/service.conf"
    fi
    systemctl is-active --quiet sing-box && touch "$destination/was-active" || true
    systemctl is-enabled --quiet quick-deploy-vps-firewall.service && touch "$destination/fw-enabled" || true
}

restore() {
    local source=$1
    [[ -d $source ]] || { echo 'No prior checkpoint' >&2; return 1; }
    # Stop first: never run old/new proxy config without its matching firewall.
    systemctl stop sing-box
    local transaction="$ROOT/restore.nft"
    : >"$transaction"
    if nft list table inet qd_vps >/dev/null 2>&1; then echo 'delete table inet qd_vps' >>"$transaction"; fi
    if [[ -f $source/had-table ]]; then cat "$source/table.nft" >>"$transaction"; fi
    nft -f "$transaction"
    if [[ -f $source/config.json ]]; then
        cp -p "$source/config.json" "$CONF.restore"
        mv -f "$CONF.restore" "$CONF"
    else
        rm -f "$CONF"
    fi
    if [[ -f $source/persistent.nft ]]; then
        cp -p "$source/persistent.nft" "$FW"
    else
        rm -f "$FW"
    fi
    if [[ -f $source/fw-enabled ]]; then
        systemctl enable quick-deploy-vps-firewall.service
    else
        systemctl disable quick-deploy-vps-firewall.service 2>/dev/null || true
    fi
    local ssh=/etc/ssh/sshd_config.d/00-quick-deploy-vps.conf
    if [[ -f $source/sshd.conf ]]; then cp -p "$source/sshd.conf" "$ssh"; else rm -f "$ssh"; fi
    sshd -t
    systemctl reload ssh
    local override=/etc/systemd/system/sing-box.service.d/90-quick-deploy-vps.conf
    if [[ -f $source/service.conf ]]; then cp -p "$source/service.conf" "$override"; else rm -f "$override"; fi
    systemctl daemon-reload
    if [[ -f $source/was-active ]]; then
        sing-box check -c "$CONF"
        systemctl start sing-box
        systemctl is-active --quiet sing-box
    fi
}

health() {
    systemctl is-active --quiet sing-box
    systemctl is-active --quiet tailscaled
    nft list table inet qd_vps >/dev/null
    ss -H -ltn 'sport = :443' | grep -q .
    ss -H -lun 'sport = :443' | grep -q .
    ss -H -lun 'sport = :40000' | grep -q .
    timeout 10 tailscale status --json | python3 -c '
import json,sys
s=json.load(sys.stdin)
assert s.get("BackendState") == "Running", "Tailscale is not Running"
assert "tag:relay" in s.get("Self",{}).get("Tags",[]), "tag:relay not approved"
'
    echo 'PASS sing-box active, TCP443 + UDP443/40000 listening, owned firewall, tagged Tailscale running'
}

case "$ACTION" in
prepare)
    trap 'rm -f "$ROOT/incoming/auth-key"; if [[ ${masked:-false} == true ]]; then systemctl unmask --runtime sing-box.service; fi' EXIT
    [[ ! -e $ROOT/pending && ! -e $ROOT/prepared ]] || { echo 'Pending/prepared deployment; wait for timer or abort it first' >&2; exit 1; }
    # Install signed packages via current official routes. No curl | sh on
    # unchecked downloads: download must complete before executing installer.
    apt-get update
    apt-get install -y ca-certificates curl gnupg nftables openssl python3 rsync
    install -d -m 755 /etc/apt/keyrings
    curl -fsSL https://sing-box.app/gpg.key -o /etc/apt/keyrings/sagernet.asc
    chmod 644 /etc/apt/keyrings/sagernet.asc
    cat >/etc/apt/sources.list.d/sagernet.sources <<'EOF'
Types: deb
URIs: https://deb.sagernet.org/
Suites: *
Components: *
Enabled: yes
Signed-By: /etc/apt/keyrings/sagernet.asc
EOF
    if ! command -v sing-box >/dev/null; then
        # Never let a package post-install start an unconfigured proxy before
        # the destination guard exists. Existing installations are not upgraded.
        systemctl mask --runtime sing-box.service
        masked=true
        apt-get update
        apt-get install -y sing-box
        systemctl unmask --runtime sing-box.service
        masked=false
    fi
    if ! command -v tailscale >/dev/null; then
        curl -fsSL https://tailscale.com/install.sh -o "$ROOT/install-tailscale.sh"
        sh "$ROOT/install-tailscale.sh"
    fi
    # Live installed CLI capability gates, rather than assuming a version string.
    up_help=$(tailscale up --help 2>&1)
    set_help=$(tailscale set --help 2>&1)
    for flag in advertise-tags accept-dns accept-routes hostname timeout auth-key; do
        grep -q -- "--$flag" <<<"$up_help" || { echo "Unsupported tailscale up --$flag" >&2; exit 1; }
    done
    grep -q -- --relay-server-port <<<"$set_help" || { echo 'Tailscale >=1.86 with relay-server-port required' >&2; exit 1; }
    grep -q -- 'file:' <<<"$up_help" || { echo 'Tailscale auth-key file support required' >&2; exit 1; }
    install -d -m 700 "$LIB"
    install -m 700 "$ROOT/incoming/remote.sh" "$LIB/remote.sh"
    # Keys are appended without replacing unrelated authorized_keys entries.
    user=$(param ssh_user)
    user_home=$(getent passwd "$user" | cut -d: -f6)
    [[ -n $user_home && -d $user_home ]] || { echo 'Invalid SSH account home' >&2; exit 1; }
    [[ ! -L $user_home/.ssh && ! -L $user_home/.ssh/authorized_keys ]] || { echo 'Refuse symlink SSH paths' >&2; exit 1; }
    install -d -m 700 -o "$user" "$user_home/.ssh"
    touch "$user_home/.ssh/authorized_keys"
    while IFS= read -r key; do
        grep -qxF -- "$key" "$user_home/.ssh/authorized_keys" || printf '%s\n' "$key" >>"$user_home/.ssh/authorized_keys"
    done <"$ROOT/incoming/authorized_keys"
    chown "$user" "$user_home/.ssh/authorized_keys"
    chmod 600 "$user_home/.ssh/authorized_keys"
    systemctl enable --now tailscaled
    auth=()
    if [[ -s $ROOT/incoming/auth-key ]]; then auth=(--auth-key="file:$ROOT/incoming/auth-key"); fi
    # No shell trace or secret-valued arguments. URL goes straight to terminal.
    tailscale up --advertise-tags=tag:relay --accept-dns=false --accept-routes=false --hostname=qd-vps-relay --timeout=10m "${auth[@]}"
    rm -f "$ROOT/incoming/auth-key"
    tailscale set --relay-server-port=40000
    target=$(param target)
    # A real TLS1.3 + h2 handshake FROM the VPS is required at deploy time.
    timeout 20 openssl s_client -connect "$target:443" -servername "$target" -tls1_3 -alpn h2 </dev/null >"$ROOT/target-check.txt" 2>&1
    grep -q 'TLSv1.3' "$ROOT/target-check.txt"
    grep -q 'ALPN protocol: h2' "$ROOT/target-check.txt"
    getent passwd qd-vps-proxy >/dev/null || useradd --system --user-group --no-create-home --shell /usr/sbin/nologin qd-vps-proxy
    uid=$(id -u qd-vps-proxy)
    [[ $uid -gt 0 && $uid -lt 1000 ]] || { echo 'qd-vps-proxy must be an isolated system UID' >&2; exit 1; }
    sed "s/meta skuid 999 /meta skuid $uid /" "$ROOT/incoming/firewall.template" >"$ROOT/candidate.nft"
    sing-box check -c "$ROOT/incoming/config.json"
    # nft check needs an atomic delete/create batch on subsequent deployments.
    : >"$ROOT/candidate-transaction.nft"
    if nft list table inet qd_vps >/dev/null 2>&1; then echo 'delete table inet qd_vps' >>"$ROOT/candidate-transaction.nft"; fi
    cat "$ROOT/candidate.nft" >>"$ROOT/candidate-transaction.nft"
    nft -c -f "$ROOT/candidate-transaction.nft"
    checkpoint="$ROOT/checkpoint-$(date +%Y%m%dT%H%M%S)-$$"
    snapshot "$checkpoint"
    printf '%s\n' "$checkpoint" >"$ROOT/prepared"
    echo 'Prepared candidate. No firewall/config swap yet.'
    ;;
export-backup)
    checkpoint=$(cat "$ROOT/prepared")
    tar -cf - -C "$checkpoint" .
    ;;
apply)
    [[ ! -e $ROOT/pending ]] || { echo 'Already pending' >&2; exit 1; }
    checkpoint=$(cat "$ROOT/prepared")
    [[ -d $checkpoint ]] || exit 1
    # Timer executes separately even if this SSH process dies/disconnects.
    printf '%s\n' "$checkpoint" >"$ROOT/pending"
    systemd-run --collect --unit=quick-deploy-vps-revert --on-active=180s "$LIB/remote.sh" abort
    trap 'flock -u 8; "$LIB/remote.sh" abort || true' EXIT
    systemctl stop sing-box
    install -d -m 755 /etc/systemd/system/sing-box.service.d
    cat >/etc/systemd/system/sing-box.service.d/90-quick-deploy-vps.conf <<'EOF'
[Unit]
Requires=quick-deploy-vps-firewall.service
After=quick-deploy-vps-firewall.service
[Service]
User=qd-vps-proxy
Group=qd-vps-proxy
AmbientCapabilities=
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
ExecStart=
ExecStart=/usr/bin/sing-box run -c /etc/sing-box/config.json
EOF
    install -d -m 755 /etc/sing-box
    install -m 600 -o qd-vps-proxy -g qd-vps-proxy "$ROOT/incoming/config.json" "$CONF.candidate"
    mv -f "$CONF.candidate" "$CONF"
    nft -f "$ROOT/candidate-transaction.nft"
    cp "$ROOT/candidate.nft" "$FW"
    # Unit loads ONLY our table, including on reboot; unrelated tables survive.
    cat >"$LIB/load-firewall.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
umask 077
candidate=$(mktemp /run/qd-vps-nft.XXXXXX)
trap 'rm -f "$candidate"' EXIT
if nft list table inet qd_vps >/dev/null 2>&1; then echo 'delete table inet qd_vps' >"$candidate"; fi
cat /etc/quick-deploy-vps.nft >>"$candidate"
nft -f "$candidate"
EOF
    chmod 700 "$LIB/load-firewall.sh"
    cat >/etc/systemd/system/quick-deploy-vps-firewall.service <<'EOF'
[Unit]
Description=quick-deploy VPS owned nft table
Before=sing-box.service
After=network-pre.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/lib/quick-deploy-vps/load-firewall.sh
[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl start quick-deploy-vps-firewall.service
    systemctl restart sing-box
    sleep 2
    health
    trap - EXIT
    echo 'Pending: fresh key SSH + YES confirmation within 180 seconds, otherwise automatic rollback.'
    ;;
harden)
    [[ -f $ROOT/pending ]] || { echo 'No pending transaction' >&2; exit 1; }
    cat >/etc/ssh/sshd_config.d/00-quick-deploy-vps.conf <<'EOF'
# Written only after a fresh publickey login.
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
    sshd -t
    # Cloud images can have earlier/Match-scoped overrides. Verify effective
    # settings for this caller, and refuse a misleading hardening claim.
    sshd -T | grep -qx 'passwordauthentication no'
    sshd -T | grep -qx 'permitrootlogin without-password'
    systemctl reload ssh
    ;;
commit)
    [[ -f $ROOT/pending ]] || { echo 'No pending transaction (timer may have rolled back)' >&2; exit 1; }
    health
    systemctl enable sing-box quick-deploy-vps-firewall.service
    cp "$ROOT/pending" "$ROOT/previous"
    rm -f "$ROOT/pending" "$ROOT/prepared"
    systemctl stop quick-deploy-vps-revert.timer
    systemctl reset-failed quick-deploy-vps-revert.service 2>/dev/null || true
    echo 'Committed. Packages and Tailscale enrollment are outside config rollback.'
    ;;
abort)
    if [[ -f $ROOT/pending ]]; then
        checkpoint=$(cat "$ROOT/pending")
        restore "$checkpoint"
        rm -f "$ROOT/pending" "$ROOT/prepared"
        systemctl stop quick-deploy-vps-revert.timer
        echo 'Restored pre-deployment config, owned nft table and sshd settings.'
    elif [[ -f $ROOT/prepared ]]; then
        rm -f "$ROOT/prepared"
        echo 'Cleared prepared candidate; live config was not changed.'
    fi
    ;;
rollback)
    [[ ! -f $ROOT/pending ]] || { echo 'Pending: use abort instead' >&2; exit 1; }
    [[ -f $ROOT/previous ]] || { echo 'No committed checkpoint' >&2; exit 1; }
    checkpoint=$(cat "$ROOT/previous")
    restore "$checkpoint"
    echo 'Previous configuration restored (repeat rollback is a no-op restore, not a toggle).'
    ;;
health) health ;;
*) echo "Unknown remote action: $ACTION" >&2; exit 2 ;;
esac
