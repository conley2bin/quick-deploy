# One VPS: native 3x-ui + Xray, an exact-path TLS front

Selected architecture, 2026-10-18. This replaces the earlier sing-box/relay and
independent-static-publisher proposal. Production contact requires a separate
operator gate; a source implementation is not deployed-client acceptance.

## Ownership

- **3x-ui** owns SQLite schema, admin authentication, device accounts, Xray
  generation, full Mihomo profiles and base64 URI subscriptions. No public
  converter and no local node-format engine.
- **Xray** serves VLESS/TCP/REALITY/Vision on TCP443 by default. One validated
  `config.proxy_port` setting owns inbound, native export, health and backup;
  optional `--port` still means SSH only. Each of company/home/phone has its own
  UUID and SubID. A native Clients operation can revoke one device.
- **Caddy** serves trusted HTTPS on TCP8443, proxying only up to six exact feed paths.
  Panel2053 and subscription2096 bind127.0.0.1. Management is through SSH forwarding.
- **TCP80** belongs exclusively to HTTP01 validation. All normal HTTP requests
  return404. Automatic redirects and TLS-ALPN challenges are disabled; Caddy
  must not claim443. Unavailable/invalid certificates cannot become HTTP feeds.
- **Operator** owns grey DNS and the registered hostname. No DNS API credential
  is needed or installed. DNS propagation, client refresh and cold-replacement
  downtime are real operational steps, not automatic failover.
- **Management PC** holds a private0700/0600 off-node recovery set. The default
  archive is unencrypted. SSH transport encryption is not encryption at rest.

The wrapper owns one private remote tree and explicitly named systemd units,
not sshd, authorized_keys, Tailscale, DERP, firewall policy, PC routes or apps.
Conflicting services, ports, legacy trees and unreviewed firewall rules are
refused rather than adopted, flushed, masked or deleted.

## Source and release policy

`release-lock.json` pins official Linux amd64 artifacts: 3x-ui3.10.2,
Xray26.10.10 and Caddy2.11.7. Downloads are checked against fixed SHA256s.
The source audit obtained the official release API digests and compared actual
bytes with official checksum files (Caddy's checksum file uses SHA512).
This trusts upstream release publishing and GitHub TLS; it does not claim
independently signed/reproducible 3x-ui or Xray builds.

Extract only executables and official geodata, not upstream installers, units,
CLI menus or Fail2ban configuration. Do not use panel one-click upgrades or
unbounded latest. A release change requires a newly tested lock, off-node
backup and a matching-database restore drill; never claim config-only rollback
also rolls back binaries/schema. Client versions are diagnostic evidence, not
an arbitrary allow-list.

## Lifecycle invariants

1. Resolve explicit SSH target/alias with normal verified host trust, no
   multiplexing, forwarding or proxy jump. Pin the public endpoint and remote
   machine-id before uploading credentials. Initial support is a dedicated
   Ubuntu24.04 amd64 machine; other platforms stop before installation.
2. Create/reuse native accounts without rotating them on repeated deploy.
   Missing/corrupt or legacy private state must not be regenerated silently.
3. Candidate feeds remain unpublished while native Xray checks, all three
   native Mihomo profiles, URI scope and public-CA TLS are validated. An
   off-node complete backup receipt and a fresh SSH call precede publication.
4. Persistent pending state records boot identity/deadline. Recovery must work
   after lost SSH and reboot; a transient timer alone is insufficient. Failed
   private candidates are retained for inspection, not erased.
5. Replacement needs no old-node contact: restore the off-node native DB,
   account/SubID mapping, certificates and exact releases on a fresh host.
   Default replacement rotates node keys and proxy UUIDs, preserving SubIDs.
   Dedicated native client endpoints perform rotation; inbound forms cannot
   overwrite authoritative global client rows in v3.10.2.
   Controlled restore is the explicit exception preserving proxy credentials.
6. Only existing owned resources may change. Repeating the same desired state
   is health/backup work, not an unnecessary service restart or new identity.

Mihomo domestic/Bili and node/subscription-underlay DIRECT rules belong in the
upstream supported inline routing setting. The phone URI feed updates nodes,
not Shadowrocket policy: its local domestic-direct rule mode must be verified.
No existing airport profile, TUN, system proxy or app selection is overwritten.

## Limits that survive the implementation

A single node cannot provide availability while it is dead. A stable URL can
resume after database/certificate restoration and DNS changes; it does not
migrate existing TCP connections or revoke a lost host. If the old host leaked
SubIDs, retaining those URLs retains those leaked capabilities. Compromise
recovery requires new SubIDs and client re-import. Upstream management remains
a privileged process; loopback/public-path isolation limits network exposure,
not the consequences of a privileged-process exploit.

Real cloud firewall/HTTP01 renewal, peak RAM/disk, REALITY target handshake,
negative authentication and desktop/mobile refresh from the user's networks
remain production acceptance gates. Isolated native parsing is not a phone
handshake or a business-account success.
