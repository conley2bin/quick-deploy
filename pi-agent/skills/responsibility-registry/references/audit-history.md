# Read historical context without changing current

`query --history` reads the logical history attached to the **current** Markdown. After a clear it is empty; after a restore it follows the restored branch. Private transaction evidence and migrated v2 events remain retained independently. Select an earlier publication by its transaction UUID to read that historical context without restoring its rows.

## Select a known publication

Keep the `transaction_id` returned by `migrate-storage` or another split-storage publication. If only its operation ID is known, `audit --operation-id ID --intent-digest DIGEST` returns the transaction ID. Plain `audit` lists terminal attempts; their `prepare.op_id` is the same locator. Verify the intended operation, status, era and reason before selecting it. An external operation ID, event ID, backup ID, owner ID or filesystem path is not a transaction UUID.

```bash
RR="$HOME/.pi/agent/skills/responsibility-registry/scripts/registry.py"
# TX is the exact canonical transaction UUID from retained operation evidence.
python3 "$RR" audit --transaction "$TX" --history
# Inspect only that attempt's PREPARE/OUTCOME, without hydrating logical history:
python3 "$RR" audit --transaction "$TX"
```

Both commands are read-only. They use the existing configured private ROOT, stable lock and private audit evidence, without opening current Markdown. They work after clear, after restoring a different branch, and with current missing or invalid. They do not create a registry, issue a fresh document token, recover a journal, restore relationships, or authorize a historical owner to resume work. Missing private ROOT/lock or damaged evidence produces an error, never initialization or repair. Retain output privately: events can include original identities, appointment evidence and recovery context.

`--transaction` is mutually exclusive with `--operation-id` and its `--intent-digest`. `audit --history` requires `--transaction`; it cannot be attached to operation-status lookup or the default all-attempts view. No arbitrary archive/checkpoint path is accepted.

## Result contract

`Store.audit_history_at(transaction_uuid)` returns the same historical result as `audit --transaction UUID --history`. The optional keyword `include_history=False` inspects attempt evidence only, as the CLI does without `--history`.

| Field | Meaning |
| --- | --- |
| `selected.transaction_id` | Exact selected canonical UUID. |
| `selected.operation_id` | External operation ID recorded in that PREPARE. |
| `selected.status` | Terminal OUTCOME status, or null while its journal is unresolved. |
| `selected.disposition` | `publication` or `noop`, as recorded. |
| `source_era`, `revision` | Selected COMMITTED publication's **after** era/revision. These are historical labels, not observations of current. Both are null for noncommitted/noop attempt-only inspection. |
| `history_loaded` | True only when logical history was explicitly loaded. |
| `history` | Present only with history loading. Includes legacy events and committed deltas on the selected logical branch; clear resets that branch and restore follows its validated checkpoint. |
| `prepare`, `outcome` | Selected immutable evidence; outcome may be null for an unresolved attempt. Any tokens inside it are retained historical evidence, not fresh mutation permission. |
| `pending` | Whether the existing pending journal belongs to this attempt. Reading leaves it untouched. |
| `current_authority` | Always false. Query current separately before any decision or mutation. |

History loading requires a terminal `COMMITTED` **publication**. Pending attempts, `PUBLICATION_UNKNOWN`, `ABORTED` and `NOT_APPLICABLE` receipts can be inspected without `--history`; requesting their logical history fails with `history_unavailable`. An UNKNOWN attempt's possible delta is never presented as a committed change. A durable COMMITTED outcome can be inspected even if metadata cleanup is still pending; this read does not perform that cleanup.

Noncanonical UUIDs fail with `invalid_transaction`; absent UUIDs with `transaction_not_found`. Missing ROOT/lock fails with `registry_unavailable`; missing/corrupt referenced audit evidence fails with `audit_damaged`. No failure substitutes current history, an empty list or another archive. The no-selector `audit` and operation-status response retain their existing behavior; `query --history` continues to describe only current logical history.

## Legacy source integrity

For every migrated v2 archive, the explicit history and checkpoint readers verify:

- Actual raw-byte SHA256 equals both the manifest SHA256 and the referencing migration `PREPARE.base.document_sha256`.
- Actual byte count equals the manifest; archive ID, v2 format, era and revision match that migration source.
- The exact raw document passes the full v2 parser and agrees with the anchored era/revision.

A checkpoint's embedded manifest is checked against its included corresponding migration PREPARE under the same rule. Every embedded legacy archive needs that source anchor, and every included migration archive reference needs its embedded manifest. Recomputing an outer checkpoint hash cannot legitimize a different raw/manifest pair. Contradictions report damaged evidence and preserve current and all private files for explicit operator reconciliation.

These checks detect mispaired or damaged evidence relative to an existing source anchor. They do not authenticate evidence against a malicious process with the same UID. Historical hydration and checkpoint validation are explicit, potentially expensive operations. Normal current query (including `--active`) and managed writes do not read archives or enumerate historical directories.
