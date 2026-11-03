# Current-view codec API

`registry_document.py` is a pure codec: callers provide text; the codec performs no file I/O, locking, installation, or source-path discovery. The streaming current view accepts both inline-audit v2 and split-audit v3. See [storage formats](storage-format.md) for the separate full-v2 and v3 Store APIs.

## Read the current prefix

```python
current_state, era = codec.parse_current_document(lines)
```

`lines: Iterable[str]` yields one Unicode text line at a time, either without a terminator or with LF/CRLF. Passing a whole `str`, bytes, non-text items, or multiple lines in one item is rejected.

The parser recognizes `Format: responsibility-registry/v2` or `Format: responsibility-registry/v3`, consumes the preamble, Owners, Assignments, Coordination, and the exact heading:

```text
## History (audit only; never restores current rows)
```

It returns immediately after consuming that heading. It never requests the following line and never buffers, parses, or validates the audit body. Missing/misspelled headings, unknown prefix prose, malformed current tables, duplicate JSON keys, shadow fields, invalid identities/graphs/lifecycles/coordination, and invalid eras fail. Structural CRLF and the full codec's one-pass cell escaping rules are preserved.

`current_state` has **exactly** these keys:

- `version`: canonical version `1`.
- `revision`: nonnegative integer declared by the current preamble.
- `owners`, `assignments`, `coordination`: validated current record dictionaries.

There is no `history`, `format`, or `audit_head` key. The revision is not checked against an unread audit. In v3, optional `Audit-Head` metadata is syntax-checked as `none` or a canonical UUID but never returned or dereferenced; absence is allowed. It is not proof of current authority or audit availability. `template` era requires revision zero and all current dictionaries empty, with no audit head in v3; other eras must be canonical UUIDs.

A valid current prefix can be read even when everything after its History heading is absent, truncated, malformed, or enormous. Success attests to the current view only. It does not attest to full-document validity, repair audit history, or provide a Store mutation envelope.

## Validate a current state

```python
codec.validate_current(current_state)
codec.validate_current_envelope(current_state, era)
```

These functions return `None` on success and apply the same current-domain checks used by the full validator. The envelope variant additionally validates era/template rules. Both reject extra fields, including `history`; neither invents an empty audit or asserts history/revision consistency.

## Full-v2 and Store-v3 callers

`FORMAT` remains `responsibility-registry/v2`. `parse_document(content)`, `serialize_document(state, era)`, `validate(state)`, and `validate_envelope(state, era)` retain the full-v2 history contract. They still check audit JSON, history/revision length, ordered revisions and event IDs, event fields, closing fence, and trailing structure. `parse_document` rejects v3 explicitly; it never invents an empty history or silently upgrades a legacy writer. A returned current state cannot be serialized or validated as a full state without supplying a real, independently validated history.

`SPLIT_FORMAT` is `responsibility-registry/v3`. Its separate `parse_store_document(content)` returns five current fields, era, and format/audit-head metadata after also validating the fixed external-audit notice and trailing structure. `serialize_current_document(current_state, era, audit_head=None)` writes that v3 layout. Neither function reads private audit data. They validate the document representation; Store owns locking, current-generation CAS, transactions, and audit persistence. A streaming current view omits the Store metadata and does not validate the v3 notice, so it cannot replace this API.

`validate_event(event, revision)` applies the existing event rules independently of a history container. Full-v2 validation calls it for each ordered event and still requires history length to equal revision. Legacy optional fields and snapshot validation are unchanged.

Incremental decoding, source accessibility, cooperative locking, read buffering, file-generation checks, and public projection limits belong to the caller. To avoid allocating the audit in the first place, pass a line iterator rather than reading the entire file and then splitting it.

For a caller-owned binary stream, decode each binary line only when requested:

```python
lines = (line.decode('utf-8') for line in binary_stream)
current_state, era = codec.parse_current_document(lines)
```

Do not use a `TextIOWrapper` when audit bytes must remain undecoded: it may read ahead and decode invalid UTF-8 in History before returning even the first current line. Per-binary-line decoding preserves the boundary: invalid UTF-8 after the History heading is never decoded, while invalid UTF-8 in the current prefix fails. Fixed-size binary buffering may prefetch bytes without logically consuming or decoding audit lines; the codec's zero-next-line guarantee does not control that buffering.

## Codoxear consumer

The owner-groups adapter uses this API under its existing shared registry lock. `project_current(state, codec)` calls `codec.validate_current(state)` on the exact five-field state before applying the same public whitelist and 500-owner/64-depth bounds as full-state `project(state)`. The latter retains its complete-input history-container/revision contract; no empty history is invented to convert a current state into a full state.

The web reader has no whole-document 8/64 MiB gate and never falls back to `parse_document` if the installed current API is missing. Install the compatible codec through the canonical selected-skill, runtime-preserving workflow before activating the adapter. Current success attests only to current fields, including coordination structure; it does not attest to audit validity or coordination readiness.

Each app/worker process owns one generation reader, caching only the minimal public projection. It checks current MD and codec readability plus `(dev, ino, size, mtime_ns, ctime_ns)` on each call, singleflights cold loads with its worker mutex and the existing shared flock, and verifies descriptor/path generations before publishing. Same-generation hot calls perform no MD content read or parse. History-only edits invalidate once but reread only the current prefix; unavailable/invalid input never reuses old success. Fresh source compilation avoids timestamp `.pyc` reuse. Authentication and native session/log linkage remain per-request and are never cached with the public projection. Source changes are request-observed; existing browser polling still determines when a visible page refreshes. See the [consumer documentation](../../../../codoxear/owner-groups/README.md) for the full cache, identity, and failure boundaries.
