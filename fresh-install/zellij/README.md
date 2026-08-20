# Zellij latest-release installer

`install.sh` installs the current stable [Zellij](https://zellij.dev/) release
for one ordinary Ubuntu user. It supports Ubuntu 24.04+ on `amd64` and `arm64`;
it neither calls `sudo` nor edits shell startup files or Zellij configuration.

```bash
./install.sh
./install.sh --check
```

Every invocation, including `--check`, queries GitHub’s latest-release endpoint.
The selected release must be a non-draft, non-prerelease `vMAJOR.MINOR.PATCH`
release with exact canonical GitHub Release URLs and assets:

| Architecture | Target triple | Archive | Binary checksum asset |
| --- | --- | --- | --- |
| `amd64` | `x86_64-unknown-linux-musl` | `zellij-x86_64-unknown-linux-musl.tar.gz` | `zellij-x86_64-unknown-linux-musl.sha256sum` |
| `arm64` | `aarch64-unknown-linux-musl` | `zellij-aarch64-unknown-linux-musl.tar.gz` | `zellij-aarch64-unknown-linux-musl.sha256sum` |

Pinned versions are intentionally unsupported: rerunning converges to the
newest stable release. A same-version rerun still queries the API but skips the
archive and checksum downloads only when its local verification succeeds.

## Files, ownership, and activation

The verified binary and its locally managed digest sidecar are kept at:

```text
~/.local/opt/zellij/<tag>/zellij
~/.local/opt/zellij/<tag>/zellij.sha256
```

`~/.local/bin/zellij` is a managed absolute symlink to that binary. Activation
uses a temporary symlink and same-directory rename, so a prior active version
remains active until the replacement has been fully verified. A foreign file or
foreign symlink at `~/.local/bin/zellij` is a conflict and is never overwritten.
Old version directories remain.

Before any current-state lookup or write, the installer requires `$HOME` and
all existing managed ancestors (`.local`, `.local/bin`, `.local/opt`,
`.local/opt/zellij`) to be non-symlink directories owned by the invoking user.
`--check` permits missing descendants without creating them. Normal runs create
only missing descendants after that validation, then validate again. This avoids
following a redirected managed path; it is not a defense against an attacker
who can rewrite the user’s home directory after validation.

A pre-existing selected version is locally verified only if its version
directory, `zellij`, and `zellij.sha256` are owned non-symlink regular objects;
the sidecar contains exactly one SHA-256 digest; the binary matches that digest;
and its version output matches the tag. Missing, malformed, or mismatched
sidecars are blocked rather than executed or overwritten. The sidecar detects
ordinary local corruption; it is not a cryptographic protection against an
attacker able to modify both it and the binary in `$HOME`.

`--check` performs no filesystem writes. It reports platform, link ownership and
version, latest tag/asset, PATH state, and planned action. It emits a PATH
warning when `~/.local/bin` is absent from the invoking shell’s PATH.

## Integrity boundary

For a new version the installer verifies two release artifacts:

1. The compressed archive must match the archive asset’s GitHub API `digest`
   field (`sha256:<64 hex>`).
2. The gzip tar manifest must contain exactly one regular top-level `zellij`
   entry. The installer extracts only that member without restoring archive
   ownership or permissions, then verifies it against exactly one checksum line
   for `target/<target-triple>/release/zellij`.

Zellij’s `.sha256sum` hashes that extracted binary path, not the `.tar.gz`
archive. The API digest and checksum asset are delivered through the same GitHub
Release channel, so they detect corruption/mismatch but are **not** an
independent publisher signature or trust root.

Normal installs take a nonblocking per-user `flock` on
`~/.local/opt/zellij/.install.lock` before requesting latest metadata and hold
it through activation. A concurrent normal installer fails clearly instead of
allowing an older API response to activate after a newer run. `--check` does
not lock or create files. `flock` is supplied by Ubuntu’s `util-linux` package.

## Authentication and dependencies

When GitHub’s unauthenticated API quota is exhausted, the API request honors
`GITHUB_TOKEN` first and then `GH_TOKEN`. The token is sent only as an API
`Authorization: Bearer` header; release asset downloads remain unauthenticated,
and the token is never printed. There is no stale-metadata fallback or retry.

Required commands are checked explicitly (except that `--help` needs none):
`curl`, `python3`, `sha256sum`, `tar`, `install`, `awk`, `stat`, `readlink`,
`mktemp`, and `flock` (plus standard `mkdir`, `mv`, `rm`, and `ln`). Ubuntu
24.04 supplies these; `flock` comes from `util-linux`. Ambient curl proxy
variables such as `HTTPS_PROXY`, `HTTP_PROXY`, and `NO_PROXY` are honored and
never logged.

## Test suite

```bash
./tests/run.sh
```

The suite runs the real installer with temporary `HOME`/`PATH` values and
mocked platform/network commands; it never contacts GitHub or writes under your
real home directory.
