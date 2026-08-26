# Task: plain-text → bill-structure parser for legislint

Repo: you are in TheAxiomFoundation/legislint (Python, src layout,
package `legislint`). NO NETWORK in this sandbox. The venv is prebuilt:
run tests with `uv run --no-sync pytest -q`. Do not touch pyproject
dependencies. Do not create PROGRESS.md or any report/scratch markdown —
code and tests only, plus the one module docstring.

## Goal

`src/legislint/textparse.py`: parse the plain-text rendering that
`legislint.billxml.render_text` produces back into an lxml tree shaped
like a govinfo bill, so `legislint.findings.run_checks` works on typed or
edited TEXT. This powers a live "does it compile" editor: a user edits
bill text, we parse and re-lint on every change.

## The input format (read `billxml.render_text` first — it is the spec)

- Line 1-2 optionally: legis-num ("H. R. 10003") and the official title;
  then a blank line. Treat any leading lines before the first
  section-designator line as preamble and ignore them structurally.
- One structural element per line: `<designator> [heading words] [text]`.
  Designators: sections are `N.` (arabic + period); lower levels are
  parenthesized: subsection `(a)`, paragraph `(1)`, subparagraph `(A)`,
  clause `(i)`, subclause `(I)`, item `(aa)`.
- Nesting is by indentation: 4 spaces per depth level (see
  `_render_structural`). BUT do not trust indentation alone — users edit
  text. Resolve each line's level primarily by designator SCHEME (the
  tag decides: `1.`→section, `(a)`-alpha-lower→subsection,
  `(1)`-arabic→paragraph, `(A)`-alpha-upper→subparagraph,
  `(i)`-roman-lower→clause, `(I)`-roman-upper→subclause), using
  indentation and the current stack to disambiguate the ambiguous cases
  ((i) alpha-vs-roman, (I) etc.): prefer the interpretation that nests
  legally under the current open element per the OLC hierarchy in
  `billxml.STRUCTURAL_TAGS` / `ENUM_SCHEMES`.
- Quoted blocks: a line consisting of `“` opens a quoted-block; a line
  starting `”` closes it (with optional trailing punctuation). Content
  inside is the same structural grammar; attach the quoted-block to the
  most recent structural element (as its child), matching how
  `render_text` emits it.
- Continuation lines (no leading designator): append to the previous
  element's text with a space.
- Typographic quotes around inline strings (“…”) are ordinary text.

Build elements exactly as `findings` expects them: structural tag,
`<enum>` child with the ORIGINAL punctuation style (`(a)` / `1.`),
`<text>` child carrying the line's remaining content. Wrap everything in
`<bill><legis-body>…</legis-body></bill>`. No external-xref
reconstruction (parsed text has no parsable-cite attributes — the
designation_collision check will simply find nothing; that is expected
and fine). Return an `lxml.etree._ElementTree`.

Public API:

```python
def parse_text(text: str) -> etree._ElementTree: ...
def lint_text(text: str) -> list[findings.Finding]: ...   # parse + run_checks (no DTD)
```

`lint_text` must be total: any input returns findings (possibly []) or a
single Finding(type="parse_error", criterion=None, detail=..., anchor="",
location="document") — never raises.

## Acceptance tests (write in tests/test_textparse.py; all must pass)

1. Round-trip fidelity on ALL FOUR fixtures in tests/fixtures: for each,
   `original = load_bill(f).tree`; `parsed = parse_text(render_text(original))`;
   the finding sets of `run_checks(original)` and `run_checks(parsed)`
   must be equal when both are filtered to types
   {unresolved_reference, duplicate_designation, sequence_gap,
   invalid_date} — compare as sorted (type, detail) pairs. (hr7682's
   unresolved subsection (c)(1) must survive the round trip; hr10003
   must stay clean.)
2. Structural fidelity: for each fixture, the sequence of
   (tag, enum_value) pairs from `billxml.iter_structural` over the
   parsed legis-body equals the original's sequence.
   EXCEPTION allowed: elements whose original text/enums live in
   constructs render_text flattens; if you hit a genuine information
   loss, prefer fixing your parser; only if impossible, document the
   narrowed assertion in the test with a comment naming the construct.
3. Live-edit behavior: take hr10003's rendering, delete the line for
   paragraph (2) of the quoted subsection (e) (the whole line), parse →
   findings must now include an unresolved_reference (the block text
   references paragraph (2)) or a sequence_gap — assert at least one of
   those appears; then restore the line → findings clean again.
4. `lint_text("complete garbage ^^^ not a bill")` returns a list (no
   raise).
5. Determinism: parse_text twice on the same input → identical
   serializations.

Also EXPORT both functions from `legislint/__init__.py`.

Run the full suite (`uv run --no-sync pytest -q`) — all existing tests
must stay green. Commit nothing; leave the working tree for review.
