# Known STOP, independent external START: zero-control reconciliation

Use this authority only when an attempted-launch retirement has exactly an intent, `stop-issued` and internally **acknowledged/reaped** `stop-completed`, followed by a separately accounted external Start. It does not complete the old retirement transaction. The old chain remains **INCOMPLETE/SUPERSEDED** and its unused START is permanently relinquished.

The service must already be active. Publish the final, reviewed source through the **ordinary qualified installer**, exactly one active-reference append after the predecessor's pinned source. Preserve settings byte-for-byte. Do not publish an intermediate candidate, overwrite an old version, edit a journal, supply a fabricated STOPPED witness or run RETIRE to adopt the successor. An unknown/forked source, extra reference hop or settings change refuses.

Run as the Sunshine desktop user on that host:

```sh
bash commands/install-display-control.sh --reconcile-external-successor \
  --certificate /owned/private/certificate.json \
  --kernel /owned/private/kernel.json \
  --anchored /owned/private/anchored.json \
  --context /owned/private/context.json \
  --lifecycle /owned/private/lifecycle.json
```

These five distinct local files must be regular, single-linked, owned by the invoking user, mode0600 and at most32768 bytes each. Keep them outside controller state and retain them unchanged for exact continuation/replay. The original certificate must remain **outside the entire controller installation**, not in its state/version/cache directories. Paths select evidence only, never the installation, settings, source or service. NSS selects the fixed installation; ambient HOME/XDG/D-Bus values supply no authority. No RPC/shim action is added.

The terminal displays the predecessor/source/external-generation bindings, complete source-preservation map and concrete actor/action/issuer/helper account, then asks for the exact token `RECONCILE`. Permission to recover is not a factual startup account. **Unknown actor, action, specific committed helper work, holders or replay must refuse before any new authority is written.** A long-lived shell/GUI need not be killed merely because it can issue a future command; its particular outstanding/queued work, retry/timer helpers and accepted requests must be accounted for. Maintain the renewed exclusive window until commit.

Every success, failure, cut and replay in this entry issues **zero STOP, START, restart, reload, quit, prepare or other service controls**. The original STOP's machine-acknowledged/reaped completion is distinct from the external issuer's operator-attested completed action. A completed manager job does not prove external child reaping.

## Independently selected originals on every history consumer

Retain the original owned certificate for each external authority for the lifetime of that history. A cached certificate, its cached hash, or an input path/hash described by the mutable transition cannot identify the independent original. Every consumer—including ordinary local RETIRE, partial continuation, descendant-floor validation and read-only replay—requires the same original, explicitly selected by the local operator. Missing proof refuses **before any write or service control**; no consumer falls back to cached assertions.

For an external command, `--certificate` already supplies the current authority's original. Supply every **other** history original once using repeatable read-only flags:

```sh
bash commands/install-display-control.sh --reconcile-external-successor \
  --certificate /owned/private/current-original.json \
  --kernel /owned/private/current-kernel.json --anchored /owned/private/current-anchored.json \
  --context /owned/private/current-context.json --lifecycle /owned/private/current-lifecycle.json \
  --original-certificate /owned/private/earlier-original.json
```

A later ordinary RETIRE, including its read-only replay, supplies the original for **every** prior external supersession:

```sh
bash commands/install-display-control.sh --reconcile-attempted-launch \
  --original-certificate /owned/private/first-original.json \
  --original-certificate /owned/private/second-original.json
```

A committed external replay needs only originals, with **no reimport of historical component files**:

```sh
bash commands/install-display-control.sh --reconcile-external-successor \
  --certificate /owned/private/current-original.json \
  --original-certificate /owned/private/earlier-original.json
```

Omitting all four component options selects a strict **already-committed read-only** check. It cannot create or finish an authority, reconstruct missing cached components, issue controls or publish availability. New/pending reconciliation still requires all four current component files; a partial set refuses. Keep the original certificate permanently; the four original component files are needed for pending continuation, while sealed replay validates their retained cached bytes against the independently selected certificate hashes.

RETIRE without external history still needs no certificate. These flags only select read-only validation inputs; they do not authorize another control, relax admission, adopt source/state or reach RPC/shim. Ordinary RETIRE retains its own separate one-STOP/START/StopWitness authority for a genuinely new qualifying attempt. External reconciliation and all its replays remain zero-control.

At most512 originals total are supported per invocation, including an external command's current original. Each must be canonical bounded certificate JSON, own-user mode0600, regular and single-linked; duplicate files/IDs and unknown/unused originals refuse. File bytes and the **original physical identity** must match the recorded input provenance. A byte-identical new copy or changed cached provenance is insufficient. All selected originals are physically pinned per invocation and rechecked after the **last external observation** on both ordinary and external commit/replay paths. Paths come only from explicit local argv/API selection, are never stored/discovered from a journal, and may not point into the controller installation.

The operator must select the genuinely retained originals, not files suggested by a corrupted cache or freshly fabricated replacements. Ownership, a matching self-reported hash or canonical JSON alone does not establish authenticity. This explicit local selection and the truthful factual account are the trust boundary; the program adds no cryptographic/root-privileged authenticity claim. Earlier historical components need not be reimported: the selected original certificate independently binds their raw hashes, while the retained cached bytes and semantic/source/control links are still validated.

Internal local API: `local_attempt_retirement(original_certificates=[Path(...)])` and `local_external_successor(..., original_certificates=[earlier_paths...])`; the latter automatically includes its current `certificate` argument. `local_external_successor(certificate, original_certificates=[earlier_paths...])` with no four component arguments is committed read-only replay only. Validator/floor/replay helpers receive typed per-invocation snapshots created from those local files. A missing original cannot be replaced with a caller JSON flag or a cached path/hash.

## Certificate schema1

The certificate must be canonical unique JSON: ASCII JSON, sorted keys, `ensure_ascii=True`, separators `(',', ':')`, followed by one newline. All fields below are mandatory; additional fields, duplicate keys and wrong types refuse. This undeployed schema1 requires the post-admission clock binding and complete source-prefix field below; earlier unshipped certificate shapes are rejected without a compatibility fallback. SHA values are lowercase64-character SHA-256 strings. A generation has the exact existing `Generation` fields: `unit`, `uid`, `boot_id`, `invocation_id`, `pid`, `start_ticks`, `start_monotonic_usec`.

| Field | Required value |
|---|---|
| `schema` | Integer1, not a Boolean. |
| `kind` | `operator_attested_components_not_stop_witness` |
| `predecessor` | Object with `id`, `intent_sha256`, `records_sha256`, `source_manifest_sha256`, `settings_sha256`. The ID names the exact old attempted transaction. Digests bind its canonical intent, canonical complete predecessor physical identity map, canonical source manifest and exact settings bytes. |
| `approved_source` | Object with `version`, integer `serial`, `manifest_sha256`, `settings_sha256`, matching the one normally published append. |
| `external` | Object with `generation`, decimal-string `job_id`, `start_cursor`, `completed_cursor`. The distinct cursors identify exact manager Start and Started events for that unit/invocation/job/boot/user. |
| `account` | The concrete factual account described below. |
| `ordering` | Exactly the post-admission/same-host clock object described below. |
| `source_version_prefix` | Complete map of **every** existing immutable source-version name to its physical commitment, captured before the first authority write; referenced and unreferenced versions are both included. |
| `unknown` | Exactly the six fields listed below, each the string `unknown`. |
| `attachments` | Object with exactly `kernel`, `anchored`, `context`, `lifecycle`. Each is `{"sha256":RAW_BYTES_SHA,"observer":CONCRETE_OBSERVER,"clock":{"basis":"UTC","value":RAW_OBSERVATION_TIME}}`. |

`records_sha256` covers every original state record and permanent state lock, including all old history and the three predecessor phases—not a caller-chosen subset or a hardcoded record count. `external_predecessor()` derives this map from the actual sealed old journal, original records, control phases and lock identities; each physical record must agree. File names or a supplied floor do not substitute for this binding. `source_manifest_sha256` hashes the canonical decoded ownership manifest, not an arbitrary printed label. All source versions/reference identities remain preserved.

The `ordering` object has exactly these fields:

- `brand`: `operator_attested_causal_order`; `old_invocation` and `external_invocation`: the exact bound old/new invocation IDs.
- `components_after_old_admission`: Boolean `true`. The operator attests that both absence components concern the lifetime **after the qualified original admission**, not an earlier absence of the same pathname/PID.
- `old_admission_intent_identity`: the exact seven-field physical identity of the original `attempt-transition-<id>.bak` from the independently bound complete predecessor record map (`sha256`, `device`, `inode`, `mode`, `uid`, `size`, `mtime_ns`).
- `clock_basis`: exactly `same_host_realtime_utc_and_sealed_mtime_ns`. This explicitly attests that the UTC observations, manager realtime stamps and original file mtimes use the same host realtime clock.
- `scope_absence_before_external_start`: Boolean `true`; `unaccounted_clock_changes`: Boolean `false`. Unknown clock changes or an unaccounted interval refuse; these factual assertions are not inferred from permission.

The original intent was created **after** qualified old-generation admission. Its already sealed `mtime_ns` therefore supplies a conservative observable lower bound, without inventing an admission wall/monotonic timestamp. Integer-nanosecond consistency requires:

```text
intent.mtime_ns <= kernel.UTC <= anchored.UTC < external_Start.realtime_usec * 1000
intent.mtime_ns <= stop_issued.mtime_ns <= stop_completed.mtime_ns <= external_Start.realtime_usec * 1000
```

Equality at the intent bound is intentionally accepted because admission precedes intent creation; equality with external Start is rejected. A component one nanosecond before the intent bound is rejected. This does **not** require observations after STOP completion: the lower bound is the admission-bearing intent, not the later completion. UTC strings denote declared point instants: `YYYY-MM-DDTHH:MM:SS[.1-to-9-digits]Z` or `+00:00`. Fractional digits are scaled exactly to nanoseconds; no float conversion, fractional truncation, invented rounding or clock-drift headroom is used. If a coarse original observation falls below the bound, do not move its timestamp forward to pass. The positive components and truthful causal/clock account remain necessary; UTC comparisons alone do not prove retirement.

For `source_version_prefix`, each key is a valid immutable version name. Each value is SHA256 of the canonical JSON map from **every file name in that version directory** to its seven-field physical `record_identity`; this includes all SOURCES, settings and ownership marker. `external_version_prefix()` computes this complete map after validating every version's bytes/manifest-derived name. The certificate must equal the full current map at first/pending admission, including old versions not referenced by an active record. Every version named by the already sealed active-reference prefix must be covered independently.

On every partial/committed replay, the transition's map must equal the **explicitly selected original certificate's complete map**, and every promised version/key/physical commitment must remain present. The independently selected original bytes/physical provenance are compared against every cached copy and purported provenance; stored attachment identities and component hashes remain checked before continuation. Deleting a key, changing a commitment or resealing only new authority records cannot weaken that promise. Committed replay allows legitimate later versions and reference appends outside the fixed original map; it does not permanently pin the entire future installation keyset.

The `account` object has exactly these fields:

- Nonempty concrete strings `actor`, `action`, `issuer`, `issuer_completion` (each at most512 UTF-8 bytes, no controls). Values such as `unknown`, `unaccounted`, `pending` or `?` refuse.
- `issuer_completion_brand`: exactly `operator_attested_action_finished_not_child_reaping`.
- `helpers`: an explicit list of zero to16 helpers. Each has concrete `identity`, `work`, `completion` strings and `state` exactly `finished_no_committed_work`. Duplicate identities or unfinished/unknown work refuse. An empty list asserts that no such helper exists.
- `specific_outstanding_work`, `outside_request_holders`, `request_fd_handoff`, `process_migration`, `authenticated_replay`, `other_deviations`, `policy_config_changes`: each exactly `none`. These are factual exclusions endorsed in the TTY ceremony, not default values inferred by the program.
- `exclusive_window`: exactly `renewed_until_commit`.

The `unknown` object is exactly:

```json
{"historical_global_jobs":"unknown","historical_repeated_typed_policy":"unknown","historical_process_fds":"unknown","historical_loaded_config":"unknown","historical_capture":"unknown","historical_monotonic_observation":"unknown"}
```

Do not substitute historical empty jobs, zero FD-store, repeated proc observations, a loaded digest, capture success or a monotonic observation time that was never collected. This certificate is specific to this authority; normal RETIRE still requires its internally collected StopWitness before START.

## Raw provenance components

Attachments preserve exact original bytes, hashes and owned physical input provenance. Their diagnostic/purpose fields are not proof. JSON duplicate keys, malformed values, wrong source hashes, unlinked objects/generations/parents or inconsistent clocks refuse. No agent-memory location or particular transaction/UUID/path is embedded in the implementation.

- **kernel**: UTC `utc`; `components.boot.value` equals the old boot, `old_birth.value` is positively `absent`, `cgroup_path.value` names the exact old canonical unit cgroup and `parent_identity.value` equals the old pinned parent. A FileNotFoundError string or parent cardinality alone supplies no removal proof.
- **anchored**: UTC `observed_utc`; exact `transaction`, `target`, `expected_parent`, literal Boolean `targeted_scope_removed:true`, audited targeted-lookup `runtime_sha256`, source-snapshot digest provenance, integer successful `native_unit_query_exit`, and exact `native_unit_properties` (`inactive/dead`, MainPID0, ControlPID0, empty ControlGroup/Job, old InvocationID). `start_issued_record_exists` must be false. The known collector source digest is deliberately distinct from the predecessor's installed legacy runtime. The source-snapshot digest is provenance, not a new executable-source authority.
- **context**: UTC `observed_utc`; exact `old_generation`, `current_boot_id`, `same_boot:true`, old scope identity and replacement `current_old_path_identity`; native active unit/process birth/exe/cgroup/invocation/start-monotonic fields. They must match the fresh successor and changed scope inode under the original unchanged parent. A new PID or reused pathname alone proves no old extinction.
- **lifecycle**: UTC `observed_utc`; successful scoped manager journal query with empty stderr and at most256 `manager_events`. The two certificate cursors must identify distinct Start/Started message IDs with exact unit, UID, boot, invocation, `JOB_TYPE:start`, job ID and actual realtime/monotonic stamps bracketing the current unit start. No monotonic component-observation stamp is invented.

UTC comparisons reject observable contradictions against the sealed admission/control interval; they remain consistency checks, not retirement proof. Authority additionally requires positive component content, source/control/generation/parent links and the operator's explicit bounded post-admission causal-order/same-host clock account. Time ordering, filenames and error text alone are insufficient.

## Current qualification, publication and replay

The entry reuses the existing `attempt_shape`, admitted `attempt_sample`, `attempt_promise` and original-derived `attempt_compound`. It freshly verifies same UUID/unit/UID/boot/root/build/disk config/Desktop, stable actual process/exe/environment, idle endpoint, typed no-hook/no-activation/zero-FD-store policy, empty global jobs and current control quiescence. Unknown facts refuse. It does not invoke `retire_attempt` or manufacture old phases, a StartReceipt, loaded-config or capture evidence.

Before the first durable authority, complete specimens for every new record, all provenance attachments, every future physical-identity envelope and live replacements must fit unchanged32768-byte private /16384-byte reservation limits. A valid large context can be ineligible; it is never truncated. The narrowly recognized predecessor code is either the audited legacy/corrected source bytes, exact executing source, or an exact source approved by an earlier validated external authority. This is not a general source migration facility.

`external-transition-<old-id>.bak` binds the operator certificate, input provenance, exact original set, old/new sources, a cached copy of the source-version map committed in the independently selected original certificate, reference prefix, permanent locks and a fixed successor-promise digest. This record and its cached certificate never select the original root. Exact raw inputs are copied into `external-{certificate,kernel,anchored,context,lifecycle}-<old-id>.bak`. `external-successor-<old-id>.bak` seals their physical identities and the original-derived promise. `external-committed-<old-id>.bak` describes the complete blocked live baseline; its name does not itself prove that availability was published.

All four original records are rename-only archived under the **original attempted transaction ID**. The original operation lease blocks the missing-reservation cut; an invalid reservation sentinel precedes lease removal; then lease-free unacquirable SELECTING blocks both dispatchers. After the last actual external observation, filesystem-only checks cover every old/new artifact, originals, live baseline, input, source/version/reference/history and permanent lock. The **sole** availability replacement derives R+1 from the decoded original reservation, preserves enrollment and historical receipt/backup, and clears lease/attempt/active/uncertainty. No external observation follows that fence.

Pending continuation accepts only the same fixed source/account/provenance/promise. Issued-only STOP, missing authority, an orphan phase, changed original or a contradictory successor remains blocked. Deleted authority cannot be recreated around already archived originals. Committed replay writes nothing, rejects below-floor rollback and self-resealed numeric/enrollment/record promises, and allows legitimate later acquire/prepare/launch/reconnect/release, another bounded recovery and ordinary source evolution. Later pending transactions can preserve an earlier floor only through exact original records and a physical history containing that complete sealed earlier authority; arbitrary backup files never supply a floor.

Descriptors bind earlier proof identities and original-derived semantics; their own inode is pinned over the current invocation's final observation/fence. Cross-invocation replay validates exact contents/semantics rather than claiming a recursive durable self-inode guarantee. Original archives, inputs, source/version prefixes, earlier histories and permanent locks remain physically sealed.

On interruption, retain all files and the active service. Re-run only the exact local entry with unchanged inputs and every independently selected history original. Do not discard originals after copying them into controller state. An error after the final atomic replacement may leave publication already complete; sealed replay distinguishes that outcome without issuing controls or a second availability write. Do not repair failures by deleting journals, replacing source6, resetting state, inventing observations or issuing another service command.

Private tests: `bash tests/run-external-successor.sh -v`. They reconstruct the actual audited legacy source, reproduce its removed558-sibling failure after completed STOP, simulate an independent Start without old phases, normally publish the new source and exercise zero-control success/cuts/negatives/future workflows. These fixtures do not establish a native operator account or successful live execution.
