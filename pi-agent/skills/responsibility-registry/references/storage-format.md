# Registry document formats

`registry_document.py` contains pure Markdown codecs and domain validators. Callers supply text; the module does not read current files, private audit objects, receipts, or historical archives. Current tables govern current relationships. Audit metadata does not authenticate those tables or grant execution permission.

## Choose the matching API

| Purpose | API | Accepted input | Result / audit boundary |
| --- | --- | --- | --- |
| Read-only current projection | `parse_current_document(lines)` | v2 or v3, one Unicode line per item | `(current, era)`; stops at the exact History heading, with zero requests for the next line |
| Full legacy document / backup | `parse_document(content)` | v2 whole Unicode text only | `(state, era)` including validated inline `history` |
| Split Store document | `parse_store_document(content)` | v3 whole Unicode text only | `(current, era, metadata)`; validates the fixed external-audit notice, never loads an audit |
| Full legacy serialization | `serialize_document(state, era)` | Full validated state, including real history | v2 text only |
| Split serialization | `serialize_current_document(current, era, audit_head=None)` | Exactly five current fields and separate head metadata | v3 text only |

All parsers reject unknown formats. Full-v2 parsing rejects v3, and Store-v3 parsing rejects v2. There is no fallback that treats split history as an empty inline history. The exported constants remain:

```python
FORMAT = 'responsibility-registry/v2'
SPLIT_FORMAT = 'responsibility-registry/v3'
```

The legacy `FORMAT` constant also preserves compatibility with consumers checking the installed codec's existing API. It does not limit the streaming current parser to v2.

## Split v3 layout

The title and Owners, Assignments, Coordination tables retain the v2 layout and cell encoding. The metadata preamble is:

```text
Format: responsibility-registry/v3
Era: <canonical UUID or template>
Revision: <nonnegative integer>
Audit-Head: <canonical UUID or none>
```

`Audit-Head` appears immediately after Revision, with ordinary blank-line handling. It may be omitted when manually reconstructing a valid current document; `parse_store_document` normalizes absence to Python `None`. The serializer always emits it, using lowercase `none` for Python `None`. A nonempty head must be an exact canonical lowercase, hyphenated UUID; any UUID version is accepted. Paths, URLs, uppercase UUIDs, duplicate metadata, and malformed values fail. The value is a transaction locator, never a caller-supplied filesystem path.

After the current tables, the exact History section is:

```text
## History (audit only; never restores current rows)
Audit history is stored separately in the private responsibility-registry store.
```

The Store parser requires this notice verbatim and rejects inline JSON, additional locators, repeated notices, or other nonblank trailing structure. Blank lines and structural CRLF are accepted. The serializer emits LF text with a final newline. The streaming current parser consumes the heading only and deliberately does not inspect the notice or any following bytes.

The recognized instruction lines are optional, in their defined order; unknown prefix prose is rejected. Original v2 `INSTRUCTIONS` and v2 serialization are retained verbatim. v3 uses separate `SPLIT_INSTRUCTIONS`, whose deletion warning distinguishes current rows from retained private audit evidence. Do not change a v2 document's Format line alone and assume its instructions/body have been migrated; use the appropriate serializer through the authorized Store workflow.

## Current state and metadata are separate

Both current APIs return exactly:

```python
{
    'version': 1,
    'revision': observed_revision,
    'owners': owners,
    'assignments': assignments,
    'coordination': coordination,
}
```

Store parsing returns metadata separately as exactly:

```python
{'format': SPLIT_FORMAT, 'audit_head': canonical_uuid_or_None}
```

`validate_current(current)` validates the exact five-field schema and existing identity, hierarchy, assignment, lifecycle, and coordination invariants. `validate_current_envelope(current, era)` additionally checks era/template rules. Extra keys, including `history`, `format`, and `audit_head`, fail. The v3 serializer additionally validates its separate head argument. A template document must be wholly empty at revision zero with no audit head. `RESPONSIBILITIES.template.md` is this v3 template; legacy full-v2 tests and exports must construct their own v2 documents.

The v3 revision is an observed managed label, not a count of private audit objects. Valid manual table edits do not have to update revision or head. Neither a missing receipt nor a mismatch with a private head can make valid current tables disappear through these codecs: no private object is consulted. Store may diagnose audit continuity separately and control managed writes; historical evidence cannot restore current authorization.

## Legacy v2 stays strict

The full-v2 state still requires `version`, `revision`, `owners`, `assignments`, and `history`, with optional `coordination` as before. Its inline History remains a fenced strict JSON array. Full validation requires history length equal to revision, ordered event revisions and IDs, valid event fields, a closing fence, and no nonblank trailing structure. Duplicate JSON keys, NaN/Infinity, nonfinite numbers, unknown/shadow fields, malformed table escaping, invalid eras, and nonempty template states retain their rejection behavior. CRLF, editor table padding, valid separator alignment, one-pass Unicode/entity escaping, and legacy optional fields retain their supported behavior.

`validate_event(event, revision)` extracts the existing event checks for reuse by split storage. It returns `None` on success and does not change the event:

- Required fields remain `revision`, `at`, `actor`, `action`, `reason`, `before`, and `after`; optional fields remain `id`, `occurred`, `authority`, and `changes`.
- Events with an ID keep the existing `event-<revision padded to at least eight digits>` check, exact recorded time, occurred-time validation, authority type, and semantic-change schema.
- Legacy events without an ID retain the original optional-field behavior; the validator does not fabricate or normalize modern fields.
- `before` and `after` remain dictionaries under the existing rules. This helper does not replay them or attest to full snapshot schemas or lifetime audit continuity.

Full `validate(state)` invokes this helper at each one-based history index and retains the independent history-length requirement. A v3 event may instead be validated at its observed current revision without constructing a fake full history.

## Caller and storage responsibilities

For incremental projection, callers must decode binary lines on demand. Eager text loading or `TextIOWrapper` read-ahead can decode invalid UTF-8 in History before the codec receives its first line. See [current-view API](current-view.md) for the binary iterator contract and web consumer boundaries.

Store owns source selection, cooperative locking, generation checks, CAS, durable publication, audit retention, recovery, and explicit migration. Successful codec parsing attests to document/domain validity only. It is not a transaction receipt, an audit-integrity proof, or authority to continue a handoff. Installing this codec does not itself migrate any current file or archive; old full-v2 writers must reject v3 rather than rewriting it as inline v2.
