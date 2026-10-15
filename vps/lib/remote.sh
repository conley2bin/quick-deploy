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
cleanup_prepare() {
    local status=$?
    trap - EXIT HUP INT TERM PIPE
    rm -f "$ROOT/incoming/auth-key" || status=1
    if [[ -f $ROOT/mask-owned ]]; then
        # Even a failed package installation may have created/enabled the
        # public sample. Remove it BEFORE releasing our runtime mask.
        rm -f "$CONF" || status=1
        systemctl disable --now sing-box || status=1
        systemctl unmask --runtime sing-box.service || status=1
        rm -f "$ROOT/mask-owned" || status=1
    fi
    exit "$status"
}
if [[ $ACTION == prepare ]]; then
    trap cleanup_prepare EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
    trap 'exit 141' PIPE
fi
# Refuse other OSes before installing anything.
. /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || { echo 'Only Ubuntu 24.04 is supported; nothing installed.' >&2; exit 1; }
install -d -m 700 "$ROOT"
exec 8>"$ROOT/operation.lock"
if [[ $ACTION == abort ]]; then
    # A timer-triggered recovery must never abandon pending state because a
    # short operation still owns the lock. It runs as soon as that owner exits.
    flock 8
else
    flock -w 30 8 || { echo 'Another VPS operation is running' >&2; exit 1; }
fi

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
    systemctl is-enabled --quiet sing-box && touch "$destination/was-enabled" || true
    systemctl is-active --quiet sing-box && touch "$destination/was-active" || true
    systemctl is-enabled --quiet quick-deploy-vps-firewall.service && touch "$destination/fw-enabled" || true
}

restore() {
    local source=$1
    [[ -d $source ]] || { echo 'No prior checkpoint' >&2; return 1; }
    # Disable FIRST: a crash/reboot during restore must not start a proxy
    # after its firewall or isolated-UID override has been removed.
    systemctl disable --now sing-box
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
    if [[ -f $source/was-active || -f $source/was-enabled ]]; then
        [[ -f $source/had-table && -f $source/service.conf && -f $source/persistent.nft && -f $source/config.json ]] \
            || { echo 'Refusing to start an unguarded sing-box checkpoint' >&2; return 1; }
    fi
    if [[ -f $source/was-active ]]; then
        sing-box check -c "$CONF"
        systemctl start sing-box
        systemctl is-active --quiet sing-box
    fi
    if [[ -f $source/was-enabled ]]; then
        systemctl enable sing-box
    fi
}

health() {
    systemctl is-active --quiet sing-box
    systemctl is-active --quiet tailscaled
    local proxy_uid table pid process_uid loaded_user
    proxy_uid=$(id -u qd-vps-proxy)
    table=$(nft --numeric list table inet qd_vps)
    grep -qF "meta skuid $proxy_uid jump proxy_output" <<<"$table" \
        || { echo 'nft proxy UID guard is missing or substituted incorrectly' >&2; return 1; }
    grep -qx 'User=qd-vps-proxy' /etc/systemd/system/sing-box.service.d/90-quick-deploy-vps.conf
    loaded_user=$(systemctl show -p User --value sing-box)
    [[ $loaded_user == qd-vps-proxy ]] || { echo 'Loaded sing-box User override is missing' >&2; return 1; }
    pid=$(systemctl show -p MainPID --value sing-box)
    [[ $pid =~ ^[0-9]+$ && $pid -gt 1 ]] || { echo 'Invalid sing-box MainPID' >&2; return 1; }
    process_uid=$(ps -o euid= -p "$pid")
    [[ ${process_uid//[[:space:]]/} == "$proxy_uid" ]] || { echo 'sing-box MainPID does not run as proxy UID' >&2; return 1; }
    local listeners
    listeners=$(ss -H -ltn 'sport = :443'); grep -q . <<<"$listeners"
    listeners=$(ss -H -lun 'sport = :443'); grep -q . <<<"$listeners"
    listeners=$(ss -H -lun 'sport = :40000'); grep -q . <<<"$listeners"
    timeout 10 tailscale status --json | python3 -c '
import json,sys
s=json.load(sys.stdin)
assert s.get("BackendState") == "Running", "Tailscale is not Running"
assert "tag:relay" in s.get("Self",{}).get("Tags",[]), "tag:relay not approved"
'
    echo 'PASS sing-box active, TCP443 + UDP443/40000 listening, owned firewall, tagged Tailscale running'
}

prune_checkpoints() {
    # Retain three newest plus every referenced recovery point. Only private
    # module checkpoint directories are eligible; never follow a symlink.
    python3 - "$ROOT" <<'PY'
import pathlib, shutil, sys
root = pathlib.Path(sys.argv[1])
protected = set()
for name in ('previous', 'prepared', 'pending', 'pending-target'):
    marker = root / name
    if marker.is_file():
        value = marker.read_text().strip()
        if value:
            protected.add(pathlib.Path(value).absolute())
checkpoints = sorted((p for p in root.glob('checkpoint-*') if p.is_dir() and not p.is_symlink()),
                     key=lambda p: p.stat().st_mtime_ns, reverse=True)
protected.update(checkpoints[:3])
for path in checkpoints:
    if path not in protected:
        shutil.rmtree(path)
PY
}

stop_timer() {
    systemctl stop quick-deploy-vps-revert.timer 2>/dev/null || true
}

arm_transaction() {
    local checkpoint=$1 kind=$2 target=${3:-}
    stop_timer
    printf '%s\n' "$checkpoint" >"$ROOT/pending"
    printf '%s\n' "$kind" >"$ROOT/pending-kind"
    printf '%s\n' "$target" >"$ROOT/pending-target"
    trap 'flock -u 8; "$LIB/remote.sh" abort || true' EXIT
    systemd-run --collect --unit=quick-deploy-vps-revert --timer-property=RemainAfterElapse=no --on-active=180s "$LIB/remote.sh" abort
}

transaction_health() {
    [[ -f $ROOT/pending ]] || { echo 'No pending transaction; it may already have reverted' >&2; return 1; }
    if [[ $(cat "$ROOT/pending-kind") == rollback && ! -f $(cat "$ROOT/pending-target")/config.json ]]; then
        # Pristine rollback deliberately has no proxy to probe.
        if systemctl is-active --quiet sing-box || systemctl is-enabled --quiet sing-box; then
            echo 'Pristine rollback must be stopped and disabled' >&2; return 1
        fi
        [[ ! -e $CONF ]]
        echo 'PASS pristine rollback: sing-box stopped, disabled, no config'
    else
        health
    fi
}

case "$ACTION" in
prepare)
    [[ ! -e $ROOT/pending ]] || { echo 'Refusing prepare: live transaction pending; run setup.sh abort or wait for revert' >&2; exit 1; }
    # A prepared candidate has never changed the data plane. Replace it under
    # the operation/upload locks instead of permanently blocking redeployment.
    if [[ -f $ROOT/prepared ]]; then echo 'Replacing stale prepared candidate'; rm -f "$ROOT/prepared"; fi
    prune_checkpoints
    export DEBIAN_FRONTEND=noninteractive
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
    version=$(param sing_box_version)
    [[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Invalid pinned sing-box version' >&2; exit 1; }
    if ! command -v sing-box >/dev/null; then
        # Never let a package post-install start an unconfigured proxy before
        # the destination guard exists. Existing installations are not upgraded.
        touch "$ROOT/mask-owned"
        systemctl mask --runtime sing-box.service
        apt-get update
        apt-get install -y "sing-box=$version"
        # Upstream ships a public example password. It must NEVER enter the
        # pristine recovery checkpoint or become boot-enabled unguarded.
        rm -f "$CONF"
        systemctl disable --now sing-box
        systemctl unmask --runtime sing-box.service
        rm -f "$ROOT/mask-owned"
    fi
    installed_version=$(dpkg-query -W -f='${Version}' sing-box)
    [[ $installed_version == "$version" ]] || { echo "sing-box version mismatch: expected $version, installed $installed_version; no automatic downgrade/upgrade" >&2; exit 1; }
    apt-mark hold sing-box
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
    grep -qF "meta skuid $uid jump proxy_output" "$ROOT/candidate.nft" \
        || { echo 'Candidate nft UID substitution failed' >&2; exit 1; }
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
    # Same independently timed transaction is used for updates and rollback.
    arm_transaction "$checkpoint" apply
    systemctl disable --now sing-box
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
    user=$(param ssh_user)
    connection=${SSH_CONNECTION:-}
    client_ip=${2:-${connection%% *}}
    [[ $client_ip =~ ^[0-9a-fA-F.:]+$ ]] || { echo 'Missing/invalid SSH client IP for Match evaluation' >&2; exit 1; }
    effective=$(sshd -T -C "user=$user,host=x,addr=$client_ip")
    grep -qx 'passwordauthentication no' <<<"$effective"
    grep -qx 'permitrootlogin without-password' <<<"$effective"
    systemctl reload ssh
    ;;
commit)
    [[ -f $ROOT/pending ]] || { echo 'No pending transaction (timer may have rolled back)' >&2; exit 1; }
    transaction_health
    kind=$(cat "$ROOT/pending-kind")
    if [[ $kind == apply ]]; then
        systemctl enable quick-deploy-vps-firewall.service
        cp "$ROOT/pending" "$ROOT/previous"
    fi
    prune_checkpoints
    stop_timer
    systemctl reset-failed quick-deploy-vps-revert.service 2>/dev/null || true
    rm -f "$ROOT/pending" "$ROOT/pending-kind" "$ROOT/pending-target" "$ROOT/prepared"
    # Rollback preserves the target's enabled state (including pristine OFF).
    # An apply enables only after its guarded config and firewall are durable.
    if [[ $kind == apply ]]; then systemctl enable sing-box; fi
    echo 'Committed. Packages and Tailscale enrollment are outside config rollback.'
    ;;
abort)
    stop_timer
    if [[ -f $ROOT/pending ]]; then
        checkpoint=$(cat "$ROOT/pending")
        restore "$checkpoint"
        rm -f "$ROOT/pending" "$ROOT/pending-kind" "$ROOT/pending-target" "$ROOT/prepared"
        prune_checkpoints
        echo 'Restored pre-deployment config, owned nft table and sshd settings.'
    elif [[ -f $ROOT/prepared ]]; then
        rm -f "$ROOT/prepared"
        prune_checkpoints
        echo 'Cleared prepared candidate; live config was not changed.'
    else
        echo 'Refusing abort: no pending transaction or prepared candidate on this host' >&2
        exit 1
    fi
    ;;
rollback)
    [[ ! -f $ROOT/pending ]] || { echo 'Pending: use abort instead' >&2; exit 1; }
    [[ -f $ROOT/previous ]] || { echo 'No committed checkpoint' >&2; exit 1; }
    target=$(cat "$ROOT/previous")
    checkpoint="$ROOT/checkpoint-$(date +%Y%m%dT%H%M%S)-rollback-$$"
    snapshot "$checkpoint"
    arm_transaction "$checkpoint" rollback "$target"
    restore "$target"
    transaction_health
    trap - EXIT
    echo 'Rollback pending: fresh key SSH and YES required within 180s; otherwise undo rollback.'
    ;;
transaction-health) transaction_health ;;
tailnet-ip) tailscale ip -4 ;;
health) health ;;
*) echo "Unknown remote action: $ACTION" >&2; exit 2 ;;
esac
