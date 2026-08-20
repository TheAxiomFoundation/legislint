# legislint

Deterministic findings on congressional bill XML.

```bash
uv run legislint check BILLS-119hr7682ih.xml
# unresolved_reference [L1.1a]: subsection (c)(1) does not resolve in its context  @ section(7)/subsection(a)
```

Validity checking for legislation exists today only embedded — inside the
House's own drafting environment, inside commercial suites, inside
legislative editors. legislint is the standalone version: open source,
machine-consumable findings, runnable by anyone on any bill.

## Checks (v0.1)

All decidable from the bill alone; each finding carries a snippet anchor
and a designator path, and criteria ids follow the Axis-A deterministic
layer of the implementability-criteria framework where one exists.

| finding | meaning |
|---|---|
| `unresolved_reference` (L1.1a) | an adjudicable internal cross-reference whose designator chain does not resolve, or whose designator cannot exist at the named level ("paragraph (A)") |
| `duplicate_designation` | two siblings carrying the same designator |
| `sequence_gap` | an interior gap in an otherwise strictly increasing designator run |
| `invalid_date` | February 29 in a non-leap year, June 31, and friends |
| `designation_collision` (L1.6) | two separate insertions each add the same subsection designator to the same U.S. Code section |
| `dtd_invalid` | fails GPO's Bill DTD (vendored) |

**Adjudicability** is the load-bearing idea: quoted-block references are
checked when they scope inside their own inserted block; freestanding
(non-amendatory) text is checked against the bill itself; references in
amendatory context target the statute being amended and are never
adjudicated from the bill alone. "Paragraph (2) of section 5"-style
references, which name their parent elsewhere, are excluded.

Statute-aware checks (strike-text existence, external citation validity)
require the U.S. Code and land next, alongside the amendatory-instruction
execution engine.

## Corpus sweeps

```bash
uv run legislint sweep data/bills-119-2-hr --out sweep.json
```

Sweep output is a **candidate list, not a defect rate** — the summary says
so. H.R. 7682 §7(a) (a reference to subsection (c)(1) where §7(c) has no
paragraphs) is the verified exemplar, pinned by a regression test.

## Consumers

- **PolicyBench Draft** uses these checks as the oracle behind its
  mutation integrity gate, pinned by version.
- Drafting pipelines use `check` as a compile gate.
- Base-rate work uses `sweep` for candidate generation.

Data: bills from [govinfo bulk data](https://www.govinfo.gov/bulkdata/BILLS)
(public domain, 17 U.S.C. §105). Test fixtures are unmodified govinfo
files.
