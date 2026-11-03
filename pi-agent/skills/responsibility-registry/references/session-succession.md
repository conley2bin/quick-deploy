# Same-owner session succession API v1

`session_succession.py` changes the current carrier of one stable owner. It keeps
that owner's parent, children, appointment, assignments, goals, dependencies,
lifecycle and coordination records. A separate assignment handover remains a
different operation. This API does not appoint an owner or transfer native child
supervision.

## Fixed installed entry point

The caller loads the fixed installed script, independent of its project cwd:

```python
import importlib.util
from pathlib import Path

path = Path.home() / '.pi/agent/skills/responsibility-registry/scripts/session_succession.py'
spec = importlib.util.spec_from_file_location('_handoff_registry_succession_v1', path)
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)
if service.SCHEMA != 'responsibility-session-succession/v1':
    raise RuntimeError('Incompatible registry succession service')
```

Keep the loaded service for the operation. No sys.path adjustment, browser path,
CLI subprocess, agent launch or cwd inference is needed. The service loads the
canonical codec and Store from its own directory into a path-specific private
package, executes their fresh source bytes, and leaves generic `registry`,
`registry_document`, `audit_storage` module names untouched. `Store()` retains its
configured private-root/current-document defaults. Runtime callers omit `store`;
explicit Store injection is for isolated temporary tests only. Missing Store
protocol support returns `unavailable/codec_incompatible`. Succession never
migrates legacy storage implicitly.

## Freeze intent, then obtain fresh current observation

The synchronous stdlib interface is:

```python
prepare_owner_session_succession(operation_id, source_pi_session_id, *, store=None)
build_intent_digest(plan, target, authority, custody)
commit_owner_session_succession(plan, target, *, expected_document, authority, custody, store=None)
get_owner_session_succession(operation_id, intent_digest, *, store=None)
```

Every prepare/commit/get domain outcome has exactly `schema`, `status`,
`operation_id`, `intent_digest`, `plan`, `receipt`, `current_binding_valid`,
`no_owner_valid`, `error`. Plans and receipts follow the frozen v1 field sets;
there are no extra top-level fields. Digest validation raises typed
`SuccessionValidationError` with `code`, `commit_state='not_committed'` and
`retryable=False`. Unexpected programming/infrastructure exceptions propagate;
they are not evidence that publication did not happen.

1. Prepare reads full validated Store current state and never writes or repairs.
   `owner_id=null` is an observation that the source is not registered; no
   placeholder is created. Multiple claims of the actual source Pi are conflicts.
2. Persist the plan's immutable version/operation/store/era/owner/source binding,
   exact verified target, authority and custody before first commit. Use a
   canonical lowercase hyphenated UUID of any version for the operation ID;
   actual Pi identities are opaque nonempty strings.
3. Use `build_intent_digest` to hash those frozen inputs. Canonical JSON sorts
   keys, uses compact separators and UTF-8, and rejects unknown fields/nonfinite
   numbers. Fresh document tokens and observation revisions are excluded.
4. Immediately before first commit, prepare again. Require identical immutable
   plan fields, then use its fresh `document_token` as `expected_document`.
   A token conflict permits only a bounded fresh observation with the same frozen
   source/target/authority/custody. Never substitute era, owner or operation ID to
   evade a conflict.
5. Commit validates source identity/unique owner and target Pi occupancy inside
   the Store transaction, and patches only identity, recovery and updated_at in
   the latest locked owner record. It does not restore a start-time graph.

Target identity/recovery use the existing canonical validators. Target actual Pi
requires `current-session` provenance. A null contact needs an explicit unknown
reason and `contact_source='unavailable'`; nonnull contact requires
`target-intercom-status`. Contact equality does not establish owner identity and
does not independently prohibit succession. The host verifies actual
broker/launch/challenge/header facts; the service validates asserted provenance.
Only challenge/header SHA256 digests are accepted, never a raw nonce secret.
Recovery locators are explicit canonical absolute paths or null with a reason;
the service does not open them or search session transcripts.

Authority is the existing source owner's or user's attestation, not a new peer
privilege. Custody explicitly distinguishes verified no runs, known active runs
and unknown/partially known custody. Unknown is not evidence of empty children;
no receipt reassigns native supervisors, transfers processes or fabricates ACKs.

Injected temporary Stores may be loaded in another module namespace. Service
compatibility uses the documented methods and typed error attributes
(`code`, `commit_state`, `retryable`), rather than Python class identity or error
message parsing. Unknown exceptions still propagate.

## Receipt recovery and the ready gate

Store receipts are checked before stale document/source preconditions. Same
operation and frozen intent never publish a second event. A changed intent
conflicts. An absent receipt alone does not establish that an earlier transport
attempt had no effects.

The registry gate allows only:

- `committed` or `already_committed`, **and** `current_binding_valid=true`; or
- `not_applicable`, **and** `no_owner_valid=true`.

A durable no-owner disposition writes only private transaction metadata: no owner,
assignment, current-document write, revision increment or event. A newly
registered source invalidates that disposition. For a committed owner receipt,
`current_binding_valid` checks only the same configured Store and era, existence
of the frozen stable owner, its identity source remaining `current-session`, its
actual Pi ID equaling the frozen target Pi ID, and that Pi being claimed by only
that owner across the current graph. A changed Pi, removed owner, duplicate Pi,
lost current-session provenance or era reset/restore makes the receipt
`superseded`.

Legal parent, role, assignment, coordination, contact, unknown-reason and recovery
updates preserve qualification while the same target Pi remains the unique current
carrier. Later registration of the old source Pi elsewhere does not change that
target binding. The complete target identity, recovery and evidence remain frozen
in the original intent and audit; changing them on retry still conflicts.
Metadata equality is not a continuation credential.

`get` is strictly read-only. Pending journal evidence or unknown publication
returns `needs_recovery`, with truthful commit_state; it never repairs journals,
replays changes or treats unknown publication as an abort. A definitive receipt
with unresolved metadata still blocks ready. Recovery belongs to the Store's
explicit transaction-recovery operation. Invalid/unavailable current state also
blocks ready even when a historical commit is known.

Receipt `document_token_after` is a genuine post-publication/recovery observation,
or null if that observation is unavailable. It is never fabricated from a SHA
or a stale source token, and it never replaces fresh current flags. A later
qualifying current snapshot cannot fill a missing historical token.

The handoff host must finish registry qualification **before** immutable ready
publication/helper success. If registry commit is known but ready publication
fails, query the same original operation/digest and recheck the current flags;
do not perform another migration or re-prepare a new no-owner intent. Report,
review, target/nonce and immutable-ready checks remain separate required gates.
Registry current publication and ready publication are not a cross-resource
atomic transaction, and current may change after qualification.

## Cooperative carrier fencing

A stable owner UUID, operation UUID, document token and actor string are not OS
capabilities. Current-Pi checks and the source stopping its owner work are a
cooperative caller protocol. The old source may retain bounded native child
result-relay custody, but must not continue acting as current owner after
confirmed succession. Arbitrary old processes/tools are not revoked by this API.

## Validation boundary

`tests/test_session_succession.py` uses synthetic owners only. Its in-memory
`ProtocolStore` tests schema/digest, policy, patch preservation, errors, receipt
ordering and read-only behavior; those tests alone do not attest to lock, fsync,
crash recovery or transaction durability.

A separate `ActualStoreSuccessionTests` group exercises the real Store against
only temporary registry files: same-operation one-event behavior, same-Pi contact
and recovery updates with read-only lookup/retry and unchanged frozen audit,
latest graph preservation/CAS, private no-owner receipts, missing-owner and
duplicate-Pi/provenance rejection, clear/restore-era supersession,
same-operation and different-source concurrent writers, cross-module injected
Store errors, and publication failure followed by read-only status and explicit
metadata-only recovery. Valid manual reversion after uncertain publication stays
unknown and is never replayed. These tests explicitly skip if the current Store
dependency is absent; a skip is not transaction acceptance. No real owner handoff,
live registry read, install or deployment is part of these tests.
