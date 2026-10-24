# Markdown cutover / recovery runbook

This is an **explicit operator action**. Installing source does not authorize migration, resets, service refreshes, or appointments. Obtain the owner's approval for the exact cutover and coordinate current registry CLI writers first. Preserve the existing `registry.lock` inode. Real documents, JSON, backups and migration markers stay private and out of Git.

## Before cutover

1. Identify the live skill COPY and the owned external adapter; do not edit the installed copy as source. Deploy this skill's codec/CLI/template using the selected-skill installer, which excludes `RESPONSIBILITIES.md` categorically. Existing live bytes/inode remain untouched, including invalid documents. Legacy-only destinations are left uninitialized.
2. Deploy the compatible external projector/browser code together with the installed pure codec. The production projector loads only `~/.pi/agent/skills/responsibility-registry/scripts/registry_document.py`, then reads only the sibling `RESPONSIBILITIES.md`, under the existing private lock. It never invokes Store/CLI or imports legacy JSON. HTTP callers cannot select paths. Preflight isolated fixtures and the authenticated real public session schema before the separately approved adapter activation.
3. Old processes already holding the lock must finish before migration snapshots JSON. An old CLI loaded before deployment can be queued; a marker alone cannot stop it. The explicit migration replaces legacy JSON with an old-validator-invalid tombstone **after** durable backup and full Markdown roundtrip verification. Do not delete that tombstone or restore the backup over it.
4. Before MD publication, new readers report missing and new writers refuse. Old code continues using legacy until the fence; after the fence it fails rather than maintaining a second writable authority. Temporary unavailability is expected. Do not use query/init/reinstall to repair it.

## Cutover command

Only after the above approval, using the installed copy:

```bash
RR="$HOME/.pi/agent/skills/responsibility-registry/scripts/registry.py"
python3 "$RR" migrate
python3 "$RR" query
```

`migrate` acquires the existing exclusive flock, validates the **final locked JSON snapshot**, and does not rely on earlier owner/revision counts. It refuses an existing live MD or any completed initialization/cutover marker. It:

- Saves exact raw JSON bytes as a unique mode-0600 private `legacy-<migration UUID>.json`, with SHA256 and fsync.
- Reconstructs a new UUID era with all IDs, registration order, parent/identity/recovery/source, assignment lifecycle/dependencies/time/handover obligations, coordination and complete semantic history. Optional historical omissions remain omissions. No history event is invented for the envelope.
- Extends each coordination boundary with a complete-assignment SHA256 **only when** its legacy owner/status/repo/updated-at checks match. Stale boundaries get null digest; original acknowledgements/boundaries stay intact. Ready/stale classifications must match before and after.
- Serializes a hidden 0600 staging MD in the live document directory; strict-parses it and compares full reconstructed state and readiness, not counts. Fsyncs staging and directory before fencing JSON.
- Saves private `document-control.json` as pending, with migration ID, source/backup/stage hashes and document path. Then atomically/fsynced replaces `registry.json` with the invalid legacy tombstone.
- Publishes by exclusive hard link; an unexpected live filename is never replaced. Reads back published bytes, durably marks completed, removes the staging link. No real graph enters source/fixtures.

Verify full normalized state against the backup (the only permitted delta is the documented coordination digest extension and absent-container normalization). Verify query and sanitized projector, identity binding/public API fields, all rich/history fields and agreement readiness. Record current observed values privately; no earlier counts are a cutover assertion. Adapter activation may require one approved refresh to load new code; subsequent MD edits/template copies need no refresh.

## Explicit crash recovery

```bash
python3 "$RR" resume-migration
# Only interrupted brand-new initialization, not legacy migration:
python3 "$RR" recover-initialization
```

| Durable state | Required behavior |
| --- | --- |
| Before pending/fence | Ordinary legacy remains usable by old code. Retry explicit `migrate` from fresh locked JSON. Orphan staging/backup evidence never triggers read-time import. |
| Pending, ordinary JSON | Explicit resume revalidates current JSON, keeps prior evidence and prepares a fresh backup/stage, including any old writer changes since the crash. Never publish stale staging. |
| Tombstone, missing MD | Queries report missing; mutations fail. Explicit resume verifies marker IDs, exact backup/stage hashes and exclusively publishes the prepared MD. |
| Published MD, pending marker | Reads can show MD; all mutations remain blocked. Explicit resume verifies the exact hard-linked publication and bytes, then completes the marker. Human edits/unexpected copies/divergence stop recovery and preserve bytes for reconciliation. Editing the hard link in place also changes staging: its digest mismatch is a divergence diagnostic. |
| Completed | Migration cannot run again, even when MD is missing/invalid/template. init/rebuild/query/reinstall cannot reconstruct old rows. A valid template reads as empty immediately. |
| Fresh initialization pending | Never auto-refill on reinstall. Explicit `recover-initialization` may exclusively publish the exact blank template or complete an unchanged blank publication; divergent bytes are preserved and refused. |

No automatic fallback/replay, no destructive recovery from backup. Preserve evidence and ask the owning operator when reconciliation is needed.

## Reset and rollback without resurrecting authority

After cutover, the user may pause **registry CLI writes** and run the literal two operations:

```bash
LIVE="$HOME/.pi/agent/skills/responsibility-registry"
rm -- "$LIVE/RESPONSIBILITIES.md"
cp -- "$LIVE/RESPONSIBILITIES.template.md" "$LIVE/RESPONSIBILITIES.md"
```

The valid blank document immediately governs zero current owners, assignments, agreements and current-era history, revision 0. New genuine registration with a newly queried token chooses a fresh UUID era. Old JSON, backups, history, predecessor advice and browser caches cannot restore omitted authority. Reset does not kill running agents or resolve their operational custody; they must reread and cease using omitted registry authority. Missing/corrupt MD returns no partial/last-good hierarchy; web returns native cards. Visibility follows the existing fresh POST cadence (~10 s).

Every mutation checks era/raw bytes/file identity under flock and rechecks immediately before atomic replacement. A non-locking editor can still write in the last-check-to-rename interval; standard POSIX cannot atomically compare the target before rename. The isolated test deliberately demonstrates this boundary. Quiescent registry writers during manual rm/cp are the mitigation; no business-agent shutdown, reset command, JSON deletion or service restart is required.

**Code rollback:** keep live MD, private control marker, tombstone, backup and lock unchanged. Disable the optional owner-group adapter to return to native UI, or restore compatible new-format code. Rolling CLI/projector code back to JSON-only code makes registry functionality unavailable because the tombstone fails its validator; this is intentional. Never make rollback "work" by restoring legacy backup to `registry.json`: that would revive pre-reset authority. A later forward code deployment resumes using the current MD/template, not the backup. Source rollback does not roll back active data.

## Isolated evidence commands

From quick-deploy source (all synthetic state in temporary directories):

```bash
python3 -m unittest discover -s pi-agent/skills/responsibility-registry/tests -v
CODOXEAR_TEST_PYTHON="$PWD/codoxear/.runtime/venv/bin/python" \
  codoxear/owner-groups/.venv/bin/python -m unittest discover -s codoxear/owner-groups/tests -p 'test_*.py' -v
node --test codoxear/owner-groups/tests/core.test.cjs
# Actual installed frontend/SW + synthetic loopback server/browser: see owner-groups README.
```

Tests cover literal delete/copy with old JSON/backup intact, manual current rows, strict codec, rich 12-owner migration/readiness, old lockholding and preloaded old writers, crash/resume/divergence, stale/cooperating writers, explicit final rename boundary, predecessor reset, installer byte/inode preservation, and reused-ID browser epochs. No production reset is a test.
