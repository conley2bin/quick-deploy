#!/usr/bin/env python3
"""Private, replaceable single-VPS identity and explicit deployment orchestration."""
from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from urllib.parse import quote, urlencode

MODULE = Path(__file__).resolve().parents[1]
DEVICES = ("company", "home", "phone")
# Audited stable release; replacement hosts must not drift to latest.
SING_BOX_VERSION = "1.13.12"
# Explicitly cover shared, benchmark, documentation, multicast and transition
# ranges as well as RFC1918. Keep this list shared with the kernel output guard.
BLOCKED4 = ["0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
            "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
            "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24",
            "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4"]
BLOCKED6 = ["::/3", "4000::/2", "8000::/1", "2001::/23", "2001:db8::/32", "2002::/16",
            "3fff::/20"]
API_URLS = ["https://api.openai.com/v1/models", "https://api.anthropic.com/v1/models",
            "https://generativelanguage.googleapis.com/v1beta/models"]


class Error(Exception):
    pass


def run(argv, **kwargs):
    # Do not put subprocess arguments in exception messages: auth material may
    # appear in outputs from external programs. Callers print bounded context.
    result = subprocess.run([str(a) for a in argv], check=False, **kwargs)
    if result.returncode:
        raise Error(f"{Path(str(argv[0])).name} failed (exit {result.returncode})")
    return result


def private_dir(path):
    path = Path(path)
    if path.is_symlink():
        raise Error(f"Refusing symlink directory: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid != os.getuid():
        raise Error(f"Private directory has another owner: {path}")
    path.chmod(0o700)
    return path


def read_private(path):
    if path.is_symlink() or not path.is_file():
        raise Error(f"Missing regular private file: {path}")
    st = path.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise Error(f"Require current owner and mode 0600: {path}")
    return path.read_text()


def write_private(path, text):
    private_dir(path.parent)
    if path.is_symlink():
        raise Error(f"Refusing symlink file: {path}")
    if path.exists() and read_private(path) == text:
        return
    fd, name = tempfile.mkstemp(prefix=".candidate-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def dumps(value):
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def load(path):
    try:
        value = json.loads(read_private(path))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, json.JSONDecodeError) as exc:
        raise Error(f"Invalid JSON object, preserved without regeneration: {path}") from exc


@contextlib.contextmanager
def locked(state):
    private_dir(state)
    path = state / ".lock"
    if path.is_symlink():
        raise Error("Refusing symlink lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError as exc:
        raise Error("Another vps command holds the private-state lock") from exc
    finally:
        os.close(fd)


def domain(value):
    if not re.fullmatch(r"(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", value):
        raise Error("Expected a literal DNS hostname")
    return value


def new_identity(hostname=None, target="www.microsoft.com"):
    domain(target)
    if hostname:
        domain(hostname)
    # OpenSSL emits RFC8410 PKCS#8/SPKI. Check prefixes instead of silently
    # slicing an arbitrary key representation. No Python crypto dependency.
    key = run(["openssl", "genpkey", "-algorithm", "X25519", "-outform", "DER"], capture_output=True).stdout
    pub = run(["openssl", "pkey", "-inform", "DER", "-pubout", "-outform", "DER"], input=key, capture_output=True).stdout
    if key[:16].hex() != "302e020100300506032b656e04220420" or pub[:12].hex() != "302a300506032b656e032100":
        raise Error("Unexpected OpenSSL X25519 key format")
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
    with tempfile.TemporaryDirectory(prefix="qd-vps-cert-") as directory:
        cert, tlskey = Path(directory) / "cert.pem", Path(directory) / "key.pem"
        run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes",
             "-days", "3650", "-subj", "/CN=qd-vps.invalid", "-addext", "subjectAltName=DNS:qd-vps.invalid",
             "-keyout", tlskey, "-out", cert], capture_output=True)
        return {"version": 1, "hostname": hostname, "reality_target": target,
                "users": {d: {"uuid": str(uuid.uuid4()), "hy2_password": secrets.token_urlsafe(32)} for d in DEVICES},
                "reality": {"private_key": b64(key[16:]), "public_key": b64(pub[12:]), "short_id": secrets.token_hex(4)},
                "hy2": {"cert": cert.read_text(), "key": tlskey.read_text(), "sni": "qd-vps.invalid"}}


def identity(state):
    value = load(state / "identity.json")
    try:
        assert value["version"] == 1
        domain(value["reality_target"])
        if value.get("hostname"):
            domain(value["hostname"])
        for d in DEVICES:
            uuid.UUID(value["users"][d]["uuid"])
            assert len(value["users"][d]["hy2_password"]) >= 24
        for key in ("private_key", "public_key"):
            assert len(base64.urlsafe_b64decode(value["reality"][key] + "=")) == 32
        assert re.fullmatch(r"[0-9a-f]{8}", value["reality"]["short_id"])
        ssl.PEM_cert_to_DER_cert(value["hy2"]["cert"])
        assert "PRIVATE KEY" in value["hy2"]["key"]
    except (AssertionError, KeyError, ValueError, TypeError) as exc:
        raise Error("Invalid identity; restore your backup. No identity was regenerated.") from exc
    return value


def init(state, args):
    path = state / "identity.json"
    if path.exists() or path.is_symlink():
        identity(state)
        print("Existing identity preserved; init never rotates credentials.")
    else:
        write_private(path, dumps(new_identity(args.hostname, args.reality_target)))
        print("Created private identity.")
    inventory = state / "inventory.json"
    if inventory.exists() or inventory.is_symlink():
        load(inventory)
    else:
        write_private(inventory, dumps({"version": 1, "current": None, "previous": [],
                                        "company_name": getattr(args, "company_name", None), "home_name": getattr(args, "home_name", None),
                                        "ssh_public_keys": [], "backup_alias": None}))


def tail_ip(value):
    address = ipaddress.IPv4Address(value)
    if address not in ipaddress.ip_network("100.64.0.0/10"):
        raise Error("Company/home must have a Tailscale IPv4 within 100.64.0.0/10")
    return str(address)


def peers(inventory, company=None, home=None):
    # Explicit offline values or saved inventory win. Discovery only reads the
    # local daemon; it never changes PC Tailscale settings or pings any peer.
    company = company or inventory.get("company_ip")
    home = home or inventory.get("home_ip")
    if not (company and home):
        for role, address in (("company", company), ("home", home)):
            if not address and not inventory.get(role + "_name"):
                raise Error(f"Set private inventory {role}_ip or {role}_name (or init --{role}-name)")
        document = json.loads(run(["tailscale", "status", "--json"], capture_output=True, text=True).stdout)
        nodes = [document.get("Self", {})] + list((document.get("Peer") or {}).values())
        def find(role):
            name = (inventory.get(role + "_name") or "").lower().rstrip(".")
            matches = [n for n in nodes if n.get("HostName", "").lower() == name or n.get("DNSName", "").split(".")[0].lower() == name]
            if len(matches) != 1:
                raise Error(f"Cannot uniquely discover {role}; set {role}_ip in private inventory")
            ips = [a for a in matches[0].get("TailscaleIPs", []) if ":" not in a]
            if len(ips) != 1:
                raise Error(f"No unique IPv4 for {role}")
            return ips[0]
        company, home = company or find("company"), home or find("home")
    company, home = tail_ip(company), tail_ip(home)
    if company == home:
        raise Error("Company and home must be different peers")
    return company, home


def server_config(ident, company, host):
    tail_ip(company)
    own = ipaddress.ip_address(host)
    users = ident["users"]
    return {"log": {"level": "warn", "timestamp": True},
            "dns": {"servers": [{"type": "udp", "tag": "public-dns", "server": "1.1.1.1"}]},
            "inbounds": [
                {"type": "vless", "tag": "reality-in", "listen": "::", "listen_port": 443,
                 "users": [{"name": d, "uuid": users[d]["uuid"], "flow": "xtls-rprx-vision"} for d in DEVICES],
                 "tls": {"enabled": True, "server_name": ident["reality_target"], "reality": {
                     "enabled": True, "handshake": {"server": ident["reality_target"], "server_port": 443},
                     "private_key": ident["reality"]["private_key"], "short_id": [ident["reality"]["short_id"]]}}},
                {"type": "hysteria2", "tag": "hy2-in", "listen": "::", "listen_port": 443,
                 "users": [{"name": d, "password": users[d]["hy2_password"]} for d in DEVICES],
                 "tls": {"enabled": True, "server_name": ident["hy2"]["sni"],
                         "certificate": ident["hy2"]["cert"].splitlines(), "key": ident["hy2"]["key"].splitlines()}}],
            "outbounds": [{"type": "direct", "tag": "direct"}],
            "route": {"default_domain_resolver": "public-dns", "final": "direct", "rules": [
                {"action": "resolve", "server": "public-dns"},
                {"auth_user": ["phone"], "ip_cidr": [company + "/32"], "network": "tcp", "port": [8743],
                 "action": "route", "outbound": "direct"},
                {"ip_cidr": BLOCKED4 + BLOCKED6 + [f"{own}/{own.max_prefixlen}"], "ip_is_private": True, "action": "reject"}]}}


def fingerprint(ident):
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(ident["hy2"]["cert"])).hexdigest().upper()


def nodes(ident, host, device):
    u, r = ident["users"][device], ident["reality"]
    return [{"name": "SELF-Reality", "type": "vless", "server": host, "port": 443, "uuid": u["uuid"],
             "flow": "xtls-rprx-vision", "tls": True, "udp": True, "servername": ident["reality_target"],
             "client-fingerprint": "chrome", "reality-opts": {"public-key": r["public_key"], "short-id": r["short_id"]}},
            {"name": "SELF-Hy2", "type": "hysteria2", "server": host, "port": 443, "password": u["hy2_password"],
             "sni": ident["hy2"]["sni"], "fingerprint": fingerprint(ident), "skip-cert-verify": False}]


def uris(ident, host):
    address = f"[{host}]" if ":" in host else host
    u, r = ident["users"]["phone"], ident["reality"]
    reality = urlencode({"encryption": "none", "security": "reality", "type": "tcp", "flow": "xtls-rprx-vision",
                         "sni": ident["reality_target"], "fp": "chrome", "pbk": r["public_key"], "sid": r["short_id"]})
    # Official Hysteria2 URI Scheme defines this exact case-sensitive field.
    # Third-party import support still needs an on-phone negative-pin test.
    hy2 = urlencode({"sni": ident["hy2"]["sni"], "pinSHA256": fingerprint(ident).lower()})
    return [f"vless://{u['uuid']}@{address}:443?{reality}#SELF-Reality",
            f"hysteria2://{quote(u['hy2_password'], safe='')}@{address}:443?{hy2}#SELF-Hy2"]


def policy(company, home):
    return {"tagOwners": {"tag:relay": ["autogroup:admin"]}, "hosts": {"company": tail_ip(company), "home": tail_ip(home)},
            "grants": [{"src": ["autogroup:member"], "dst": ["autogroup:self"], "ip": ["*"]},
                       {"src": ["tag:relay"], "dst": ["company"], "ip": ["tcp:8743"]},
                       {"src": ["company", "home"], "dst": ["tag:relay"], "app": {"tailscale.com/cap/relay": []}}]}


def firewall(company, ssh_port=22, uid=999):
    tail_ip(company)
    if not 1 <= int(ssh_port) <= 65535 or int(uid) < 1:
        raise Error("Invalid firewall port/UID")
    ports = ", ".join(str(p) for p in sorted({22, int(ssh_port), 443}))
    return f"""table inet qd_vps {{
  chain input {{
    type filter hook input priority 10; policy drop;
    ct state established,related accept
    iifname "lo" accept
    meta l4proto {{ icmp, ipv6-icmp }} accept
    meta nfproto ipv6 udp sport 547 udp dport 546 accept
    # No new tailnet connections to VPS services. Replies use conntrack above.
    iifname "tailscale0" drop
    tcp dport {{ {ports} }} accept
    udp dport {{ 443, 40000, 41641 }} accept
  }}
  chain output {{
    type filter hook output priority 10; policy accept;
    meta skuid {int(uid)} jump proxy_output
  }}
  chain proxy_output {{
    ct state established,related accept
    fib daddr type local reject
    oifname "tailscale0" ip daddr {company} tcp dport 8743 accept
    ip daddr {{ {', '.join(BLOCKED4)} }} reject
    ip6 daddr {{ {', '.join(BLOCKED6)} }} reject
    oifname "tailscale0" reject
  }}
}}
"""


def render_outputs(state, ident, inventory, device, host, company, home):
    output = private_dir(state / "generated")
    addresses = list(inventory.get("vps_ips", []))
    try:
        addresses.append(str(ipaddress.ip_address(host)))
    except ValueError:
        current = inventory.get("current") or {}
        if current.get("host"):
            addresses.append(current["host"])
    addresses = sorted({str(ipaddress.ip_address(address)) for address in addresses})
    if not addresses:
        raise Error("Stable hostname needs current.host or private inventory vps_ips for DIRECT underlay rules")
    for d in ("company", "home"):
        write_private(output / f"clash-{d}.json", dumps({"version": 1, "proxies": nodes(ident, host, d), "server_ips": addresses}))
    write_private(output / "clash-nodes.json", dumps({"version": 1, "proxies": nodes(ident, host, device), "server_ips": addresses}))
    links = uris(ident, host)
    write_private(output / "shadowrocket.txt", "\n".join(links) + "\n")
    write_private(output / "shadowrocket-subscription.txt", base64.b64encode(("\n".join(links) + "\n").encode()).decode() + "\n")
    write_private(output / "tailnet-policy.json", dumps(policy(company, home)))
    write_private(output / "hy2-sha256.txt", fingerprint(ident) + "\n")
    write_private(output / "手机和策略说明.txt", f"""先导出备份当前 Shadowrocket 配置和 Tailnet policy，再修改。
节点订阅不包含规则。节点名均以 SELF- 开头，使用 phone 独立身份。
1. 从 [General] tun-excluded-routes 删除 100.64.0.0/10，并确认 skip-proxy 不含它。
2. 添加 [Proxy Group] 下的行：
SELF = select,policy-regex-filter=^SELF-
3. 在 [Rule] 第一行添加：
IP-CIDR,{company}/32,SELF,no-resolve
然后浏览器访问 http://{company}:8743，继续使用 Codoxear 密码。
Hysteria2 URI 使用官方 pinSHA256={fingerprint(ident).lower()}，默认不跳过TLS验证。
在手机将pin故意改错，确认连接失败，再恢复正确pin。未做此负向验收前优先用Reality。
只有App因自签CA拒绝正确pin时，才手动添加insecure=1作为兼容fallback，且仍须保留pin并再次验证错pin失败。
若App忽略pin或错pin仍能连接，不要使用该Hy2节点；insecure=1且不校验pin会遭中间人冒充。
以上 Shadowrocket URI 与规则字段须在实际 App 版本核对，尚未实机验收。
先导出当前 Tailnet policy。合并 tailnet-policy.json；删除原有 allow-all ACL/grant，
否则新增限制不会抵消旧全放行规则。不要覆盖不相关成员授权。admin console 必须校验通过。
member -> self 只涵盖同一拥有者的未打标签设备；两台PC若属于不同用户须另授权。
先验证 VPS -> company:8743 可达且 -> company:22 不可达，再验手机。
""")
    if shutil.which("qrencode"):
        for link, name in zip(links, ("reality", "hy2")):
            target = output / f"shadowrocket-{name}.png"
            if target.is_symlink():
                raise Error(f"Refusing symlink QR output: {target}")
            # Stage inside the private directory; never follow an output link.
            with tempfile.TemporaryDirectory(prefix=".qr-", dir=output) as directory:
                candidate = Path(directory) / "qr.png"
                run(["qrencode", "-o", candidate], input=link.encode())
                candidate.chmod(0o600)
                os.replace(candidate, target)
    else:
        print("qrencode not installed; QR PNGs not generated (URIs are available).")
    print(f"Private client outputs: {output}. Clash runtime unchanged.")


def public_keys(inventory):
    local = Path.home() / ".ssh/conley.pub"
    if not local.is_file():
        raise Error("Missing ~/.ssh/conley.pub")
    keys = [local.read_text().strip()] + inventory.get("ssh_public_keys", [])
    unique = []
    for key in keys:
        if not isinstance(key, str) or "\n" in key or not re.fullmatch(r"(?:ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp\d+) [A-Za-z0-9+/=]+(?: [^\r\n]+)?", key):
            raise Error("Invalid public key in inventory")
        # Real OpenSSH validation, no private key read here.
        run(["ssh-keygen", "-l", "-f", "-"], input=(key + "\n").encode(), capture_output=True)
        if key.split()[:2] not in [k.split()[:2] for k in unique]:
            unique.append(key)
    if len(unique) < 2:
        raise Error("Add the OTHER PC public key to private inventory ssh_public_keys before deploying")
    return unique


def host_record(args):
    address = str(ipaddress.ip_address(args.host))
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", args.user) or not 1 <= args.port <= 65535:
        raise Error("Invalid SSH user/port")
    return {"host": address, "user": args.user, "port": args.port, "provider": args.provider}


def updated_inventory(inventory, record):
    result = dict(inventory)
    old = result.get("current")
    if old != record:
        previous = list(result.get("previous", []))
        if old and old not in previous:
            previous.append(old)
        result.update(current=record, previous=previous)
    return result


def ssh_args(record, fresh=False):
    # Disable inherited ProxyCommand/Jump: management should use the real WAN.
    # Keep normal host-key verification; never auto-accept a changed VPS key.
    argv = ["ssh", "-p", str(record["port"]), "-i", str(Path.home() / ".ssh/conley"),
            "-o", "ConnectTimeout=15", "-o", "ProxyCommand=none", "-o", "ProxyJump=none"]
    if fresh:
        argv += ["-o", "ControlMaster=no", "-o", "ControlPath=none", "-o", "BatchMode=yes",
                 "-o", "PreferredAuthentications=publickey", "-o", "PasswordAuthentication=no",
                 "-o", "IdentitiesOnly=yes"]
    return argv + [f"{record['user']}@{record['host']}"]


def elevated(record, command):
    return ("" if record["user"] == "root" else "sudo -n ") + command


def remote(record, action, fresh=False, **kwargs):
    command = "/usr/local/lib/quick-deploy-vps/remote.sh " + shlex.quote(action)
    if action == "harden":
        # Expand in the SSH shell before sudo can filter SSH_CONNECTION.
        command += ' "${SSH_CONNECTION%% *}"'
    return run(ssh_args(record, fresh) + [elevated(record, command)], **kwargs)


def bundle(ident, company, record, keys, auth=None, without_tailscale=False):
    files = {"remote.sh": (MODULE / "lib/remote.sh").read_bytes(),
             "config.json": dumps(server_config(ident, company, record["host"])).encode(),
             "params.json": dumps({"company": company, "host": record["host"], "ssh_port": record["port"],
                                   "ssh_user": record["user"], "target": ident["reality_target"],
                                   "sing_box_version": SING_BOX_VERSION, "without_tailscale": without_tailscale}).encode(),
             "authorized_keys": ("\n".join(keys) + "\n").encode(),
             "firewall.template": firewall(company, record["port"], 999).encode()}
    if auth and not without_tailscale:
        files["auth-key"] = auth.encode()
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as archive:
        for name, content in files.items():
            item = tarfile.TarInfo(name)
            item.size, item.mode = len(content), 0o600
            archive.addfile(item, io.BytesIO(content))
    return data.getvalue()


def confirm(message):
    if input(message + " 输入 YES: ").strip() != "YES":
        raise Error("Not confirmed; pending transaction will automatically revert")


def recover_abort(record):
    for fresh in (True, False):
        try:
            remote(record, "abort", fresh=fresh)
            return
        except (Error, OSError):
            if fresh:
                print("Fresh abort login failed; trying existing ControlMaster connection.", file=sys.stderr)
    print("Abort SSH paths failed; independent revert waits for the operation lock. Inspect the provider console if needed.", file=sys.stderr)


def deploy(state, args):
    record = host_record(args)
    if args.dry_run:
        # Deliberately before identity, inventory, keys, daemon discovery, locks
        # and even DNS: this command works on a pristine, disconnected PC.
        print(f"DRY RUN: {record['user']}@{record['host']}:{record['port']} (NO CONTACT / NO WRITES)")
        print("1. Verify Ubuntu 24.04; require root or passwordless sudo; acquire remote deployment lock.")
        print("2. Install pinned official sing-box; firewall + both PC public keys + verified sshd hardening.")
        if args.without_tailscale:
            print("3. SKIP Tailscale installation, CLI, enrollment and relay (--without-tailscale).")
        else:
            print("3. Install Tailscale; tailscale up --advertise-tags=tag:relay --accept-dns=false --accept-routes=false --hostname=qd-vps-relay")
            print("   Interactive URL or private auth file; tailscale set --relay-server-port=40000.")
        print("4. Verify Reality target TLS1.3 + ALPN h2; preserve a healthy unchanged proxy without restart.")
        print("5. Back up config and owned nft table locally/remotely; sing-box check candidate; arm 180s auto-revert.")
        print("6. Swap/restart ONLY changed proxy; unchanged proxy stays running. Health + NEW key SSH + explicit YES.")
        print("7. Persist owned firewall; harden sshd AFTER key login, fresh login again, cancel revert; save inventory/render.")
        return
    ident, inv = identity(state), load(state / "inventory.json")
    current = inv.get("current") or {}
    if current.get("host") == record["host"] and not args.provider:
        record["provider"] = current.get("provider", "")
    company, home = peers(inv, args.company_ip, args.home_ip)
    keys = public_keys(inv)
    auth = None
    if args.without_tailscale:
        print("SKIP Tailscale auth input/install/enrollment (--without-tailscale).")
    else:
        auth = os.environ.get("TS_AUTHKEY")
        if args.auth_key_file:
            auth = read_private(args.auth_key_file).strip()
    confirm(f"Will install/update ONLY VPS {record['host']}; inspect provider firewall and SSH host fingerprint first.")
    # A fixed trusted extraction path, root-only; upload in a single SSH session.
    # remote lock protects the fixed staging directory across all deployers.
    upload = elevated(record, "bash -c ") + shlex.quote(
        "set -euo pipefail; umask 077; install -d -m 700 /var/lib/quick-deploy-vps; "
        "exec 9>/var/lib/quick-deploy-vps/upload.lock; flock -n 9; "
        "if test -e /var/lib/quick-deploy-vps/pending; then echo 'Refusing upload: transaction pending; use setup.sh abort' >&2; exit 1; fi; "
        "install -d -m 700 /var/lib/quick-deploy-vps/incoming; "
        "rm -f /var/lib/quick-deploy-vps/incoming/auth-key; "
        "tar -xf - -C /var/lib/quick-deploy-vps/incoming; "
        "bash /var/lib/quick-deploy-vps/incoming/remote.sh prepare")
    run(ssh_args(record) + [upload], input=bundle(ident, company, record, keys, auth, args.without_tailscale))
    # Fetch the exact remote checkpoint (contains private previous config) before
    # applying any data-plane changes; local state inherits restrictive modes.
    try:
        backup = remote(record, "export-backup", capture_output=True).stdout
        checkpoint = private_dir(state / "backups") / (time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(4) + ".tar")
        write_private(checkpoint, "")
        checkpoint.write_bytes(backup)
        remote(record, "apply")
        remote(record, "health", fresh=True)
        confirm("Fresh key SSH and VPS health succeeded. Commit firewall and disable password login?")
        remote(record, "harden", fresh=True)
        remote(record, "health", fresh=True)
        remote(record, "commit", fresh=True)
    except (Error, OSError, EOFError, KeyboardInterrupt):
        print("Deployment failed/not confirmed; requesting recovery.", file=sys.stderr)
        recover_abort(record)
        raise
    inv = updated_inventory(inv, record)
    inv.update(company_ip=company, home_ip=home, device=args.device, without_tailscale=args.without_tailscale)
    write_private(state / "inventory.json", dumps(inv))
    render_outputs(state, ident, inv, args.device, ident.get("hostname") or record["host"], company, home)


def client_config(node, port):
    return {"mixed-port": port, "bind-address": "127.0.0.1", "allow-lan": False,
            "mode": "rule", "log-level": "warning", "ipv6": True, "proxies": [node],
            "rules": ["MATCH," + node["name"]]}


def check_clients(ident, inv, device, binary=None):
    binary = binary or shutil.which("verge-mihomo") or shutil.which("mihomo")
    if not binary:
        raise Error("Install mihomo/verge-mihomo first, or supply --mihomo; no silent client fallback")
    record = inv.get("current")
    if not record:
        raise Error("No current host in inventory")
    failed = False
    for node in nodes(ident, ident.get("hostname") or record["host"], device):
        with tempfile.TemporaryDirectory(prefix="qd-vps-check-") as directory:
            # No tun, controllers, DNS listener or system proxy configuration.
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            root = Path(directory)
            config = root / "config.json"
            config.write_text(dumps(client_config(node, port)))
            config.chmod(0o600)
            with (root / "client.log").open("wb") as log:
                process = subprocess.Popen([binary, "-d", directory, "-f", str(config)], stdout=log, stderr=log)
                try:
                    ready = False
                    for _ in range(100):
                        if process.poll() is not None:
                            break
                        try:
                            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                                ready = True
                                break
                        except OSError:
                            time.sleep(0.05)
                    for url in API_URLS:
                        result = None
                        if ready:
                            result = subprocess.run(["curl", "--silent", "--show-error", "--noproxy", "", "--proxy",
                                                     f"http://127.0.0.1:{port}", "--max-time", "40", "--output", "/dev/null",
                                                     "--write-out", "%{http_code}", url], capture_output=True, text=True)
                        status = result.stdout.strip() if result else "000"
                        ok = bool(result is not None and result.returncode == 0 and re.fullmatch(r"[1-5][0-9]{2}", status))
                        failed |= not ok
                        print(f"{node['name']} {'PASS' if ok else 'FAIL'} {url} HTTP {status}")
                    if not ready:
                        print(f"{node['name']} client failed to bind; inspect binary compatibility/port collision.", file=sys.stderr)
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
    if failed:
        raise Error("One or more node/API reachability checks failed")


def backup(state, inv, alias):
    alias = alias or inv.get("backup_alias")
    if not alias or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", alias):
        raise Error("Set backup_alias in private inventory, or --alias (existing SSH alias)")
    source_host = socket.gethostname()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,252}", source_host):
        raise Error("Cannot use this source hostname as an isolated backup directory")
    target = f".local/state/quick-deploy-private/vps-backups/{source_host}"
    run(["ssh", alias, f"umask 077; mkdir -p ~/{target}; chmod 700 ~/{target}"])
    # No --delete; an interrupted source cannot erase the second copy. Lock is
    # local coordination, not recovery state, and must not replace a peer lock.
    run(["rsync", "-a", "--chmod=D700,F600", "--exclude=.lock", "-e", "ssh", str(state) + "/", f"{alias}:{target}/"])
    print(f"Private backup: {alias}:~/{target}/ (peer live VPS state unchanged). Verify a staged restore before use.")


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state-dir", type=Path, default=Path.home() / ".local/state/quick-deploy-private/vps")
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("init")
    a.add_argument("--hostname")
    a.add_argument("--reality-target", default="www.microsoft.com")
    a.add_argument("--company-name", help="private Tailscale hostname for discovery")
    a.add_argument("--home-name", help="private Tailscale hostname for discovery")
    a = sub.add_parser("deploy")
    a.add_argument("--host", required=True)
    a.add_argument("--user", default="root")
    a.add_argument("--port", type=int, default=22)
    a.add_argument("--provider", default="")
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--without-tailscale", action="store_true", help="stage1: proxy/firewall/sshd only; skip Tailscale")
    a.add_argument("--auth-key-file", type=Path)
    a.add_argument("--company-ip")
    a.add_argument("--home-ip")
    a.add_argument("--device", choices=("company", "home"), required=True)
    a = sub.add_parser("render")
    a.add_argument("--device", choices=("company", "home"))
    a.add_argument("--host", help="offline render address override; does not update inventory")
    a.add_argument("--company-ip")
    a.add_argument("--home-ip")
    a = sub.add_parser("check")
    a.add_argument("--mihomo")
    a.add_argument("--device", choices=("company", "home"))
    a.add_argument("--ping", action="store_true")
    a = sub.add_parser("backup")
    a.add_argument("--alias")
    sub.add_parser("rollback")
    a = sub.add_parser("abort", help="recover pending/prepared transaction; --host works before inventory commit")
    a.add_argument("--host")
    a.add_argument("--user", default="root")
    a.add_argument("--port", type=int, default=22)
    a.set_defaults(provider="")
    sub.add_parser("status")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    os.umask(0o077)
    state = args.state_dir.expanduser()
    try:
        if args.command == "deploy" and args.dry_run:
            deploy(state, args)
            return 0
        with locked(state):
            if args.command == "init":
                init(state, args)
                return 0
            if args.command == "deploy":
                deploy(state, args)
                return 0
            if args.command == "abort":
                inventory_path = state / "inventory.json"
                record = host_record(args) if args.host else (load(inventory_path).get("current") if inventory_path.exists() else None)
                if not record:
                    raise Error("Refusing abort: no current VPS; supply abort --host <IP> --user <user> --port <port>")
                remote(record, "abort")
                return 0
            ident, inv = identity(state), load(state / "inventory.json")
            device = getattr(args, "device", None) or inv.get("device")
            if args.command == "render":
                if not device:
                    raise Error("Select this PC identity with render --device company|home")
                company, home = peers(inv, args.company_ip, args.home_ip)
                current = inv.get("current") or {}
                host = args.host or ident.get("hostname") or current.get("host")
                if not host:
                    raise Error("No current VPS; supply render --host for offline rendering")
                try:
                    ipaddress.ip_address(host)
                except ValueError:
                    domain(host)
                render_outputs(state, ident, inv, device, host, company, home)
                inv.update(company_ip=company, home_ip=home, device=device)
                write_private(state / "inventory.json", dumps(inv))
            elif args.command == "backup":
                backup(state, inv, args.alias)
            else:
                record = inv.get("current")
                if not record:
                    raise Error("No current VPS in inventory")
                if args.command == "status":
                    print(f"Current host: {record['host']}; prior hosts: {len(inv.get('previous', []))}")
                    remote(record, "health", fresh=True)
                elif args.command == "rollback":
                    confirm("Begin timed rollback of previous VPS config and owned nft table?")
                    try:
                        remote(record, "rollback", fresh=True)
                        remote(record, "transaction-health", fresh=True)
                        confirm("Fresh key SSH verified rollback. Keep restored configuration?")
                        remote(record, "commit", fresh=True)
                    except (Error, OSError, EOFError, KeyboardInterrupt):
                        recover_abort(record)
                        raise
                else:
                    if not device:
                        raise Error("Select check --device company|home or save the device with render first")
                    remote(record, "health", fresh=True)
                    check_clients(ident, inv, device, args.mihomo)
                    if args.ping:
                        # Read from this exact inventory host, never a colliding
                        # MagicDNS name still held by the replacement's old node.
                        address = remote(record, "tailnet-ip", fresh=True, capture_output=True, text=True).stdout.strip()
                        if address == "skipped":
                            print("SKIP Tailscale ping (--without-tailscale on this VPS).")
                        else:
                            run(["tailscale", "ping", tail_ip(address)])
        return 0
    except (Error, OSError, ValueError, EOFError) as exc:
        print(f"vps: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("vps: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
