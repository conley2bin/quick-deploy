# Current-view codec API

`registry_document.py` is a pure codec: callers provide text; the codec performs no file I/O, locking, installation, or source-path discovery.

## Read the current prefix

```python
current_state, era = codec.parse_current_document(lines)
```

`lines: Iterable[str]` yields one Unicode text line at a time, either without a terminator or with LF/CRLF. Passing a whole `str`, bytes, non-text items, or multiple lines in one item is rejected.

The parser consumes the preamble, Owners, Assignments, Coordination, and the exact heading:

```text
## History (audit only; never restores current rows)
```

It returns immediately after consuming that heading. It never requests the following line and never buffers, parses, or validates the audit body. Missing/misspelled headings, unknown prefix prose, malformed current tables, duplicate JSON keys, shadow fields, invalid identities/graphs/lifecycles/coordination, and invalid eras fail. Structural CRLF and the full codec's one-pass cell escaping rules are preserved.

`current_state` has **exactly** these keys:

- `version`: canonical version `1`.
- `revision`: nonnegative integer declared by the current preamble.
- `owners`, `assignments`, `coordination`: validated current record dictionaries.

There is no `history` key. The revision is not checked against an unread audit. `template` era requires revision zero and all current dictionaries empty; other eras must be canonical UUIDs.

A valid current prefix can be read even when everything after its History heading is absent, truncated, malformed, or enormous. Success attests to the current view only. It does not attest to full-document validity, repair audit history, or provide a Store mutation envelope.

## Validate a current state

```python
codec.validate_current(current_state)
codec.validate_current_envelope(current_state, era)
```

These functions return `None` on success and apply the same current-domain checks used by the full validator. The envelope variant additionally validates era/template rules. Both reject extra fields, including `history`; neither invents an empty audit or asserts history/revision consistency.

## Full-document callers

`parse_document(content)`, `serialize_document(state, era)`, `validate(state)`, and `validate_envelope(state, era)` retain the full-history contract. They still check audit JSON, history/revision length, ordered revisions and event IDs, event fields, closing fence, and trailing structure. Existing Store and CLI operations continue using that contract. A returned current state cannot be serialized or validated as a full state without supplying a real, independently validated history.

Incremental decoding, source accessibility, cooperative locking, read buffering, file-generation checks, and public projection limits belong to the caller. To avoid allocating the audit in the first place, pass a line iterator rather than reading the entire file and then splitting it.

For a caller-owned binary stream, decode each binary line only when requested:

```python
lines = (line.decode('utf-8') for line in binary_stream)
current_state, era = codec.parse_current_document(lines)
```

Do not use a `TextIOWrapper` when audit bytes must remain undecoded: it may read ahead and decode invalid UTF-8 in History before returning even the first current line. Per-binary-line decoding preserves the boundary: invalid UTF-8 after the History heading is never decoded, while invalid UTF-8 in the current prefix fails. Fixed-size binary buffering may prefetch bytes without logically consuming or decoding audit lines; the codec's zero-next-line guarantee does not control that buffering.
