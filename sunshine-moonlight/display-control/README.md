# Display-control host protocol

This companion owns screen selection and the managed session lifecycle without modifying Sunshine or Moonlight binaries. `host.py` accepts one bounded JSON request on stdin and writes one JSON response. An installer supplies the adjacent private `host-settings.json`; requests cannot supply filesystem paths, commands or authority claims.

The implemented host-operation adapter uses Linux KMS and the pinned Sunshine build. The root connector now uses this protocol for live selection and supervised streaming. Windows/macOS native enumeration exists; their service/graphical-session adapters and native deployment acceptance remain pending.

## Ownership

Use one controller installation and one persistent state directory per Sunshine host. Every managed launch/resume must use this controller. Concurrent GUI/direct-client/admin bypasses are outside this single-authority contract. An existing unmanaged stream is never adopted or cancelled.

`reservation.json` holds ownership across selection, preparation, launch, streaming, reconnect and release. A private capability and revision identify the owner; every successful mutation returns the next revision. The operation mutex additionally serializes whole requests, including configuration/restart work. Process exit, elapsed time, EOF, Moonlight exit0 and raw Sunshine FREE do not release ambiguous ownership.

Untouched selection can refresh or cancel after hotplug. Known owned Desktop cleanup does not require capture usability. Unknown host/process/config/application, interrupted preparation and unconfirmed issued attempts remain blocked until trusted reconciliation. No RPC force/reset exists.

## Requests

Common request:

```json
{"version":1,"action":"list","target":{"address":"100.100.20.30","port":47989}}
```

`target` is the canonical Tailnet IPv4 and Sunshine base port. The host compares it with the actual owned configuration and endpoint UUID. Requests are unique-key UTF-8 JSON, at most2048 bytes; responses are at most32768 bytes.

| Action | Additional fields | Purpose |
|---|---|---|
| `list` | — | Current native screens, mode facts, selection snapshot and ownership revision. |
| `status` | — | Ownership-only revision/phase/uncertainty; no fresh screen snapshot. |
| `enroll` | `revision` | Explicit host-operator exclusivity ceremony plus fresh qualified idle facts. |
| `acquire` | `revision`, `snapshot` | Acquire the host before interactive selection. |
| `refresh` | `revision`, `capability` | Refresh pristine SELECTING intent; returns a new revision and menu. |
| `prepare` | `revision`, `capability`, `snapshot`, `display_id` | Revalidate selection, update only output_name, restart when necessary, verify correspondence. |
| `launch`, `resume`, `observe_active` | `revision`, `capability` | Persist intent before client dispatch; then observe the matching owned Desktop. |
| `begin_release`, `finish_release` | `revision`, `capability` | Persist positive-before-cancel intent, then confirm same-generation closure. |
| `abort` | `revision`, `capability` | Cancel untouched selection or explicitly completed preparation before any launch. |
| `control_lost` | `revision`, `capability` | Retain ownership and require reconciliation. |

Capabilities are secrets: carry them only in protected stdin/transport or private state, never argv, logs or public status. Errors contain a code and sanitized detail. An ambiguous response is not proof of release; do not retry a mutating request with guessed revisions.

## Client contract

1. Present actual host `list` facts and acquire before interactive selection. `selectable` with `selection_intent:"prepare"` permits a preparation attempt; it does not claim verified capture. Current screen Hz, requested FPS and measured video FPS differ.
2. Prepare the selected output, then preserve official list → manual PIN/pair when explicitly unpaired → fresh list confirmation, under the same reservation.
3. After preparation, freshly resolve the paired app catalogue using stock Moonlight's case-insensitive first-title rule. Its selected ID must equal the host's inspected `desktop_app_id` before `launch`. The number is not a supported alternate CLI selector. Host also rejects Desktop title aliases and command/preparation hooks.
4. Persist `launch` before issuing official `stream`; supervise the process and confirm `observe_active`. The stock CLI is single-shot; the current connector holds the lease through that child and implements no automatic reconnect/resume loop. Any separately issued resume process must receive a fresh controller `resume` intent under the same lease before dispatch. Launch preflight refusal preserves known ownership only after fresh validation; an issued unobserved attempt remains uncertain.
5. Force `stream --no-quit-after`: this overrides `StreamingPreferences.quitAppAfter`, which controls host cancellation. The CLI QML `quitAfter` separately exits the local process and does not override that preference. After the stream ends, use `begin_release` while the owned Desktop remains positive, invoke official authenticated `quit -- HOST:PORT`, then call `finish_release`. Exit0 alone proves neither stream success nor closure.
6. On cancellation before launch, use `abort`. On genuinely uncertain control/dispatch/closure, retain the lease and report reconciliation rather than choosing a default screen or erasing state.

## Installation and evidence

Settings schema2 pins the user service/config/apps/assets/state paths, host UUID/base port, qualified executable/source provenance and retry-guard digest. `commands/install-display-control.sh` verifies the exact reference package against installed bytes; hashing arbitrary installed files is not qualification. See [installation instructions](../README.md#在已安装的-a-独立安装显示控制伴随程序). Private settings/receipts are0600 and state is0700. Installation does not enroll or restart; fixed system shim publication requires explicit `--publish-shim`, and enrollment is a local interactive `quick-deploy-display-control --enroll` ceremony, unavailable through ordinary stdin RPC. Installed execution uses `-I -B`; complete RPC responses, including refusals, exit0 and must be interpreted from JSON. Local enrollment failures exit1.

A controlled-start receipt is issued only after an owned restart, old-generation retirement, new matching endpoint, stable qualified files and completed source-matched probe.

Native inventory, effective backend/encoder, selected connector correspondence and captured-image/live-frame evidence are distinct. Fixture suites exercise the real JSON/coordinator/state/config path using injected runtime/native/endpoint facts. They do not establish Windows/macOS runtime support or live selected-image acceptance. Real switching must use an explicitly available host; never interrupt an existing stream for testing.
