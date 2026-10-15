#!/usr/bin/env bash
# Executed ONLY on the explicitly selected VPS. Never source this on a PC.
set -euo pipefail
umask 077
ROOT=/var/lib/quick-deploy-vps
LIB=/usr/local/lib/quick-deploy-vps
CONF=/etc/sing-box/config.json
FW=/etc/quick-deploy-vps.nft
ACTION=${1:?remote action required}
ROLE=${3:-proxy}
[[ $ROLE == proxy || $ROLE == relay ]] || { echo 'Invalid role (proxy|relay)' >&2; exit 1; }
[[ $EUID == 0 ]] || { echo 'VPS requires root/passwordless sudo' >&2; exit 1; }
cleanup_prepare() {
    local status=$?
    trap - EXIT HUP INT TERM PIPE
    rm -f "$ROOT/incoming/auth-key" || status=1
    if [[ $ROLE == proxy && -f $ROOT/upgrade-unheld ]]; then
        apt-mark hold sing-box || status=1
        rm -f "$ROOT/upgrade-unheld" || status=1
    fi
    if [[ $ROLE == proxy && -f $ROOT/mask-owned ]]; then
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

if [[ -f $ROOT/role && $(cat "$ROOT/role") != "$ROLE" ]]; then
    echo 'Refusing ambiguous host: this machine belongs to the other VPS role' >&2; exit 1
fi
if [[ $ROLE == relay && ! -f $ROOT/role && ( -e $CONF || -e /etc/systemd/system/sing-box.service.d/90-quick-deploy-vps.conf ) ]]; then
    echo 'Refusing relay ownership: existing proxy configuration on this host' >&2; exit 1
fi
if [[ $ACTION == prepare ]]; then printf '%s\n' "$ROLE" >"$ROOT/role"; fi

param() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$ROOT/incoming/params.json" "$1"; }

tailscale_mode() {
    python3 - "$ROOT/active-params.json" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
# Legacy full deployments have no active marker and retain full checks.
params = json.loads(path.read_text()) if path.exists() else {}
without = params.get('without_tailscale', False)
if not isinstance(without, bool):
    raise ValueError('active without_tailscale must be boolean')
print('skipped' if without else 'enabled')
PY
}

relay_endpoint_from() {
    python3 - "$1" <<'PY'
import ipaddress, json, sys
p = json.load(open(sys.argv[1]))
a = ipaddress.ip_address(p['host'])
if not a.is_global or a.is_multicast or a.is_unspecified:
    raise ValueError('relay host must be a global unicast literal')
expected = f'[{a}]:40000' if a.version == 6 else f'{a}:40000'
if p.get('relay_endpoint', expected) != expected:
    raise ValueError('relay endpoint does not match numeric host')
print(expected)
PY
}

verify_relay_prefs() {
    local expected='' prefs
    if [[ $ROLE == relay ]]; then expected=$(relay_endpoint_from "$ROOT/active-params.json") || return 1; fi
    prefs=$(timeout 10 tailscale debug prefs) || return 1
    python3 -c '
import json,sys
p=json.load(sys.stdin)
endpoints=p.get("RelayServerStaticEndpoints") or []
if sys.argv[1] == "relay":
    assert p.get("RelayServerPort") == 40000, "relay port pref is not40000"
    assert sys.argv[2] in endpoints, "expected relay static endpoint missing from prefs"
    print("PASS advertised relay endpoint: " + sys.argv[2])
else:
    assert p.get("RelayServerPort") is None, "proxy still advertises a relay port"
    assert not endpoints, "proxy still advertises relay static endpoints"
    print("PASS proxy relay preferences disabled/empty")
' "$ROLE" "$expected" <<<"$prefs" || return 1
}

proxy_relay_guard() {
    local action=$1
    if ! command -v tailscale >/dev/null; then return 0; fi
    if ! systemctl is-active --quiet tailscaled; then
        echo 'WARNING: tailscaled inactive; proxy relay preferences could not be verified' >&2
        return 0
    fi
    if [[ $action == clear ]]; then
        tailscale set --relay-server-port="" --relay-server-static-endpoints="" || return 1
    fi
    verify_relay_prefs
}

restore_params() {
    local source=$1
    if [[ -f $source/active-params.json ]]; then
        cp -p "$source/active-params.json" "$ROOT/active-params.json.restore"
        mv -f "$ROOT/active-params.json.restore" "$ROOT/active-params.json"
    else
        rm -f "$ROOT/active-params.json"
    fi
}

restore_sshd() {
    local source=$1 ssh=/etc/ssh/sshd_config.d/00-quick-deploy-vps.conf
    if [[ -f $source/sshd.conf ]]; then cp -p "$source/sshd.conf" "$ssh"; else rm -f "$ssh"; fi
    sshd -t
    systemctl reload ssh
}

activate_params() {
    install -m 600 "$ROOT/incoming/params.json" "$ROOT/active-params.json.candidate"
    mv -f "$ROOT/active-params.json.candidate" "$ROOT/active-params.json"
}

snapshot() {
    # Snapshot only module-owned nft state; never flush other tables (Tailscale).
    local destination=$1
    install -d -m 700 "$destination"
    if [[ $ROLE == proxy && -f $CONF ]]; then cp -p "$CONF" "$destination/config.json"; fi
    if [[ -f $ROOT/active-params.json ]]; then cp -p "$ROOT/active-params.json" "$destination/active-params.json"; fi
    if nft list table inet qd_vps >"$destination/table.nft" 2>/dev/null; then
        touch "$destination/had-table"
    else
        : >"$destination/table.nft"
    fi
    if [[ -f $FW ]]; then cp -p "$FW" "$destination/persistent.nft"; fi
    if [[ -f /etc/ssh/sshd_config.d/00-quick-deploy-vps.conf ]]; then
        cp -p /etc/ssh/sshd_config.d/00-quick-deploy-vps.conf "$destination/sshd.conf"
    fi
    if [[ $ROLE == proxy && -f /etc/systemd/system/sing-box.service.d/90-quick-deploy-vps.conf ]]; then
        cp -p /etc/systemd/system/sing-box.service.d/90-quick-deploy-vps.conf "$destination/service.conf"
    fi
    if [[ $ROLE == proxy ]]; then
        systemctl is-enabled --quiet sing-box && touch "$destination/was-enabled" || true
        systemctl is-active --quiet sing-box && touch "$destination/was-active" || true
    else
        systemctl is-enabled --quiet tailscaled && touch "$destination/ts-enabled" || true
        systemctl is-active --quiet tailscaled && touch "$destination/ts-active" || true
    fi
    systemctl is-enabled --quiet quick-deploy-vps-firewall.service && touch "$destination/fw-enabled" || true
}

restore() {
    local source=$1
    [[ -d $source ]] || { echo 'No prior checkpoint' >&2; return 1; }
    if [[ $ROLE == proxy ]]; then proxy_relay_guard clear; fi
    if [[ $ROLE == proxy && ${2:-false} == true ]]; then
        # The proxy/firewall were byte-identical and never changed. Recover
        # stage/SSH metadata without interrupting existing proxy connections.
        restore_params "$source"
        restore_sshd "$source"
        return
    fi
    # Disable FIRST: a crash/reboot during restore must not start a proxy
    # after its firewall or isolated-UID override has been removed.
    if [[ $ROLE == proxy ]]; then systemctl disable --now sing-box; fi
    local transaction="$ROOT/restore.nft"
    : >"$transaction"
    if nft list table inet qd_vps >/dev/null 2>&1; then echo 'delete table inet qd_vps' >>"$transaction"; fi
    if [[ -f $source/had-table ]]; then cat "$source/table.nft" >>"$transaction"; fi
    nft -f "$transaction"
    if [[ $ROLE == proxy ]]; then
        if [[ -f $source/config.json ]]; then
            cp -p "$source/config.json" "$CONF.restore"
            mv -f "$CONF.restore" "$CONF"
        else
            rm -f "$CONF"
        fi
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
    restore_params "$source"
    restore_sshd "$source"
    if [[ $ROLE == relay ]]; then
        # Enrollment is not cloned/undone. Relay advertisement and daemon
        # activation are restored to the module checkpoint.
        systemctl start tailscaled
        if [[ -f $source/active-params.json ]]; then
            tailscale set --relay-server-port=40000 --relay-server-static-endpoints="$(relay_endpoint_from "$source/active-params.json")"
        else
            tailscale set --relay-server-port="" --relay-server-static-endpoints=""
        fi
        if [[ ! -f $source/ts-active ]]; then systemctl stop tailscaled; fi
        if [[ -f $source/ts-enabled ]]; then systemctl enable tailscaled; else systemctl disable tailscaled; fi
        return
    fi
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
        wait_health systemctl is-active --quiet sing-box
    fi
    if [[ -f $source/was-enabled ]]; then
        systemctl enable sing-box
    fi
}

running_current_binary() {
    local pid executable
    pid=$(systemctl show -p MainPID --value sing-box) || return 1
    [[ $pid =~ ^[0-9]+$ && $pid -gt 1 ]] || return 1
    executable=$(readlink "/proc/$pid/exe") || return 1
    [[ $executable == /usr/bin/sing-box ]]
}

wait_health() {
    local attempt
    for attempt in {1..20}; do
        if "$@" >"$ROOT/health-wait.log" 2>&1; then cat "$ROOT/health-wait.log"; return 0; fi
        sleep 1
    done
    cat "$ROOT/health-wait.log" >&2
    echo 'Health check did not become ready within 20 polls' >&2
    return 1
}

proxy_health() {
    systemctl is-active --quiet sing-box || return 1
    running_current_binary || { echo 'sing-box MainPID uses a stale/deleted binary' >&2; return 1; }
    local proxy_uid table pid process_uid loaded_user
    proxy_uid=$(id -u qd-vps-proxy) || return 1
    table=$(nft --numeric list table inet qd_vps) || return 1
    grep -qF "meta skuid $proxy_uid jump proxy_output" <<<"$table" \
        || { echo 'nft proxy UID guard is missing or substituted incorrectly' >&2; return 1; }
    grep -qx 'User=qd-vps-proxy' /etc/systemd/system/sing-box.service.d/90-quick-deploy-vps.conf || return 1
    loaded_user=$(systemctl show -p User --value sing-box) || return 1
    [[ $loaded_user == qd-vps-proxy ]] || { echo 'Loaded sing-box User override is missing' >&2; return 1; }
    pid=$(systemctl show -p MainPID --value sing-box) || return 1
    [[ $pid =~ ^[0-9]+$ && $pid -gt 1 ]] || { echo 'Invalid sing-box MainPID' >&2; return 1; }
    process_uid=$(ps -o euid= -p "$pid") || return 1
    [[ ${process_uid//[[:space:]]/} == "$proxy_uid" ]] || { echo 'sing-box MainPID does not run as proxy UID' >&2; return 1; }
    local listeners
    listeners=$(ss -H -ltn 'sport = :443') || return 1; grep -q . <<<"$listeners" || return 1
    listeners=$(ss -H -lun 'sport = :443') || return 1; grep -q . <<<"$listeners" || return 1
    echo 'PASS sing-box proxy UID/guard and TCP443 + UDP443 listening'
}

health() {
    if [[ $ROLE == proxy ]]; then proxy_health || return 1; else nft list table inet qd_vps >/dev/null || return 1; fi
    local mode
    mode=$(tailscale_mode) || return 1
    if [[ $mode == skipped ]]; then
        proxy_relay_guard verify || return 1
        echo 'SKIP Tailscale install/enrollment/bridge health (--without-tailscale)'
        return
    fi
    systemctl is-active --quiet tailscaled || return 1
    verify_relay_prefs || return 1
    local listeners
    if [[ $ROLE == relay ]]; then
        listeners=$(ss -H -lun 'sport = :40000') || return 1; grep -q . <<<"$listeners" || return 1
        tag=tag:relay
    else
        tag=tag:egress
    fi
    timeout 10 tailscale status --json | python3 -c '
import json,sys
s=json.load(sys.stdin)
assert s.get("BackendState") == "Running", "Tailscale is not Running"
assert sys.argv[1] in s.get("Self",{}).get("Tags",[]), sys.argv[1] + " not approved"
' "$tag" || return 1
    echo "PASS $ROLE owned firewall and Tailscale $tag running (relay UDP40000 only for relay role)"
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
    rm -f "$ROOT/pending-no-proxy-change"
    printf '%s\n' "$checkpoint" >"$ROOT/pending"
    printf '%s\n' "$kind" >"$ROOT/pending-kind"
    printf '%s\n' "$target" >"$ROOT/pending-target"
    trap 'flock -u 8; "$LIB/remote.sh" abort "" "$ROLE" || true' EXIT
    systemd-run --collect --unit=quick-deploy-vps-revert --timer-property=RemainAfterElapse=no --on-active=180s "$LIB/remote.sh" abort '' "$ROLE"
}

transaction_health() {
    [[ -f $ROOT/pending ]] || { echo 'No pending transaction; it may already have reverted' >&2; return 1; }
    if [[ $ROLE == relay && $(cat "$ROOT/pending-kind") == rollback && ! -f $(cat "$ROOT/pending-target")/active-params.json ]]; then
        local listeners
        listeners=$(ss -H -lun 'sport = :40000') || return 1
        [[ -z $listeners ]] || { echo 'Pristine relay rollback still listens on40000' >&2; return 1; }
        echo 'PASS pristine relay rollback: no relay listener'
    elif [[ $ROLE == proxy && $(cat "$ROOT/pending-kind") == rollback && ! -f $(cat "$ROOT/pending-target")/config.json ]]; then
        # Pristine rollback deliberately has no proxy to probe.
        if systemctl is-active --quiet sing-box || systemctl is-enabled --quiet sing-box; then
            echo 'Pristine rollback must be stopped and disabled' >&2; return 1
        fi
        [[ ! -e $CONF ]] || return 1
        echo 'PASS pristine rollback: sing-box stopped, disabled, no config'
    else
        health
    fi
}

install_firewall() {
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
    local before=''
    if [[ $ROLE == proxy ]]; then before=sing-box.service; fi
    cat >/etc/systemd/system/quick-deploy-vps-firewall.service <<EOF
[Unit]
Description=quick-deploy VPS owned nft table
Before=$before
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
}

case "$ACTION" in
prepare)
    [[ ! -e $ROOT/pending ]] || { echo 'Refusing prepare: live transaction pending; run setup.sh abort or wait for revert' >&2; exit 1; }
    # A prepared candidate has never changed the data plane. Replace it under
    # the operation/upload locks instead of permanently blocking redeployment.
    if [[ -f $ROOT/prepared ]]; then echo 'Replacing stale prepared candidate'; rm -f "$ROOT/prepared"; fi
    export DEBIAN_FRONTEND=noninteractive
    # Install signed packages via current official routes. No curl | sh on
    # unchecked downloads: download must complete before executing installer.
    apt-get -o DPkg::Lock::Timeout=300 update
    # Stage2 must not upgrade already-present proxy dependencies and trigger
    # unrelated service restarts through package maintenance hooks.
    apt-get -o DPkg::Lock::Timeout=300 install -y --no-upgrade ca-certificates curl gnupg nftables openssl python3 rsync iproute2 procps openssh-server
    # Python-backed pruning/params are first used AFTER bootstrap.
    prune_checkpoints
    if [[ $(param role) != "$ROLE" ]]; then echo 'Incoming role does not match explicit target role' >&2; exit 1; fi
    if [[ $ROLE == proxy ]]; then
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
        apt-get -o DPkg::Lock::Timeout=300 update
        apt-get -o DPkg::Lock::Timeout=300 install -y "sing-box=$version"
        # Upstream ships a public example password. It must NEVER enter the
        # pristine recovery checkpoint or become boot-enabled unguarded.
        rm -f "$CONF"
        systemctl disable --now sing-box
        systemctl unmask --runtime sing-box.service
        rm -f "$ROOT/mask-owned"
    fi
    installed_version=$(dpkg-query -W -f='${Version}' sing-box)
    if [[ $installed_version != "$version" ]]; then
        allow_upgrade=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("allow_upgrade", False))' "$ROOT/incoming/params.json")
        [[ $allow_upgrade == True ]] || { echo "sing-box version mismatch: expected $version, installed $installed_version; rerun deploy --upgrade and confirm" >&2; exit 1; }
        touch "$ROOT/upgrade-unheld"
        apt-mark unhold sing-box
        apt-get -o DPkg::Lock::Timeout=300 install -y "sing-box=$version"
        installed_version=$(dpkg-query -W -f='${Version}' sing-box)
        [[ $installed_version == "$version" ]] || { echo 'Exact sing-box upgrade verification failed' >&2; exit 1; }
    fi
    apt-mark hold sing-box
    rm -f "$ROOT/upgrade-unheld"
    fi
    without_tailscale=$(param without_tailscale)
    [[ $without_tailscale == True || $without_tailscale == False ]] || { echo 'without_tailscale must be boolean' >&2; exit 1; }
    if [[ $without_tailscale == False ]]; then
        if ! command -v tailscale >/dev/null; then
            curl -fIsS --connect-timeout 10 --max-time 30 https://pkgs.tailscale.com/stable/ >/dev/null \
                || { echo 'pkgs.tailscale.com unreachable: official Tailscale installation stopped; no mirror fallback' >&2; exit 1; }
            curl -fsSL https://tailscale.com/install.sh -o "$ROOT/install-tailscale.sh"
            sh "$ROOT/install-tailscale.sh" \
                || { echo 'Official Tailscale install failed (check pkgs.tailscale.com reachability)' >&2; exit 1; }
        fi
        # Live CLI capability gates apply only to the enrollment stage.
        up_help=$(tailscale up --help 2>&1)
        set_help=$(tailscale set --help 2>&1)
        for flag in advertise-tags accept-dns accept-routes hostname timeout auth-key; do
            grep -q -- "--$flag" <<<"$up_help" || { echo "Unsupported tailscale up --$flag" >&2; exit 1; }
        done
        grep -q -- --relay-server-port <<<"$set_help" || { echo 'Tailscale >=1.86 with relay-server-port required' >&2; exit 1; }
        grep -q -- --relay-server-static-endpoints <<<"$set_help" || { echo 'Tailscale relay static endpoints support required' >&2; exit 1; }
        grep -q -- 'file:' <<<"$up_help" || { echo 'Tailscale auth-key file support required' >&2; exit 1; }
    else
        echo 'SKIP Tailscale installation and CLI/enrollment (--without-tailscale)'
    fi
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
    checkpoint="$ROOT/checkpoint-$(date +%Y%m%dT%H%M%S)-$$"
    snapshot "$checkpoint"
    if [[ $without_tailscale == False ]]; then
        systemctl enable --now tailscaled
        auth=()
        if [[ -s $ROOT/incoming/auth-key ]]; then auth=(--auth-key="file:$ROOT/incoming/auth-key"); fi
        # No shell trace or secret-valued arguments. URL goes straight to terminal.
        if [[ $ROLE == proxy ]]; then tag=tag:egress; else tag=tag:relay; fi
        tailscale up --advertise-tags="$tag" --accept-dns=false --accept-routes=false --hostname="qd-vps-$ROLE" --timeout=10m "${auth[@]}"
        rm -f "$ROOT/incoming/auth-key"
    fi
    if [[ $ROLE == proxy ]]; then
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
    else
        cp "$ROOT/incoming/firewall.template" "$ROOT/candidate.nft"
    fi
    # nft check needs an atomic delete/create batch on subsequent deployments.
    : >"$ROOT/candidate-transaction.nft"
    if nft list table inet qd_vps >/dev/null 2>&1; then echo 'delete table inet qd_vps' >>"$ROOT/candidate-transaction.nft"; fi
    cat "$ROOT/candidate.nft" >>"$ROOT/candidate-transaction.nft"
    nft -c -f "$ROOT/candidate-transaction.nft"
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
    if [[ $ROLE == relay ]]; then
        tailscale set --relay-server-port=40000 --relay-server-static-endpoints="$(param relay_endpoint)"
    else
        # Fresh proxy-only hosts do not install/execute Tailscale. Existing
        # active daemons must not advertise relay prefs, even in stage1.
        proxy_relay_guard clear
    fi
    if [[ $ROLE == relay ]]; then
        nft -f "$ROOT/candidate-transaction.nft"
        cp "$ROOT/candidate.nft" "$FW"
        install_firewall
        activate_params
        wait_health health
        trap - EXIT
        echo 'Relay pending: fresh publickey login and YES required; no proxy installed.'
        exit 0
    fi
    if cmp -s "$ROOT/incoming/config.json" "$CONF" && cmp -s "$ROOT/candidate.nft" "$FW" \
            && [[ -f /etc/systemd/system/sing-box.service.d/90-quick-deploy-vps.conf ]] \
            && systemctl is-enabled --quiet sing-box && running_current_binary; then
        # Enrollment in prepare adds the overlay without touching proxy state.
        proxy_health
        touch "$ROOT/pending-no-proxy-change"
        activate_params
        health
        trap - EXIT
        echo 'Proxy unchanged: no stop/restart, config swap or nft reload; stage confirmation pending.'
        exit 0
    fi
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
    install_firewall
    systemctl restart sing-box
    activate_params
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
    rm -f "$ROOT/pending" "$ROOT/pending-kind" "$ROOT/pending-target" "$ROOT/pending-no-proxy-change" "$ROOT/prepared"
    # Rollback preserves the target's enabled state (including pristine OFF).
    # An apply enables only after its guarded config and firewall are durable.
    if [[ $kind == apply && $ROLE == proxy ]]; then systemctl enable sing-box; fi
    echo 'Committed. Packages and Tailscale enrollment are outside config rollback.'
    ;;
abort)
    stop_timer
    if [[ -f $ROOT/pending ]]; then
        checkpoint=$(cat "$ROOT/pending")
        preserve_proxy=false
        if [[ -f $ROOT/pending-no-proxy-change ]]; then preserve_proxy=true; fi
        restore "$checkpoint" "$preserve_proxy"
        rm -f "$ROOT/pending" "$ROOT/pending-kind" "$ROOT/pending-target" "$ROOT/pending-no-proxy-change" "$ROOT/prepared"
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
    wait_health transaction_health
    trap - EXIT
    echo 'Rollback pending: fresh key SSH and YES required within 180s; otherwise undo rollback.'
    ;;
transaction-health) transaction_health ;;
relay-endpoint) verify_relay_prefs >/dev/null; relay_endpoint_from "$ROOT/active-params.json" ;;
tailnet-ip) mode=$(tailscale_mode); if [[ $mode == skipped ]]; then echo skipped; else tailscale ip -4; fi ;;
health) health ;;
*) echo "Unknown remote action: $ACTION" >&2; exit 2 ;;
esac
