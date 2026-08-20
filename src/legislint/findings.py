"""Deterministic findings over a bill.

One source of truth for what counts as a defect. Consumers:
- the legislint CLI (staffer- and pipeline-facing lint),
- PolicyBench Draft (validators for its mutation integrity gate),
- corpus sweeps (candidate generation for base-rate work).

Finding types and, where one exists, the implementability-criteria id they
correspond to (Axis A deterministic layer):

- unresolved_reference   (L1.1a) — an adjudicable internal cross-reference
  whose designator chain does not resolve, or whose designator cannot parse
  at the named level ("paragraph (A)").
- duplicate_designation  — two siblings carrying the same designator.
- sequence_gap           — an interior gap in an otherwise strictly
  increasing sibling designator run of three or more.
- invalid_date           — a calendar-impossible date in bill text.
- designation_collision  — two different quoted-block insertions adding the
  same subsection designator to the same U.S. Code section.
- dtd_invalid            — the document fails GPO's Bill DTD (only when a
  DTD path is supplied).

Adjudicability rules live in billxml.find_internal_refs: quoted-block
references must scope inside their block; freestanding text is checked
against the bill itself; amendatory-context references (which target the
statute being amended) are never adjudicated from the bill alone.
"""

from __future__ import annotations

import calendar
import re
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path

from . import billxml

_DATE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),\s+(\d{4})\b"
)
_MONTH_NUM = {m: i for i, m in enumerate(calendar.month_name) if m}


@dataclass
class Finding:
    type: str
    criterion: str | None    # implementability-criteria id where one exists
    detail: str
    anchor: str              # snippet present in the plain rendering
    location: str            # designator path


def _designator_path(elem) -> str:
    parts = []
    node = elem
    while node is not None and isinstance(node.tag, str):
        if node.tag in billxml.STRUCTURAL_TAGS:
            d = billxml.enum_value(node)
            parts.append(f"{node.tag}({d})" if d else node.tag)
        node = node.getparent()
    return "/".join(reversed(parts))


def check_references(tree) -> list[Finding]:
    body = billxml.legis_body(tree)
    out = []
    for r in billxml.find_internal_refs(body, adjudicable_only=True):
        parses = billxml.designator_ordinal(r.level, r.path[0]) is not None
        if parses and r.resolves:
            continue
        reason = (
            f"designator ({r.path[0]}) cannot exist at the level named"
            if not parses
            else "does not resolve in its context"
        )
        out.append(
            Finding(
                type="unresolved_reference",
                criterion="L1.1a",
                detail=f"{r.surface} {reason}",
                anchor=r.surface,
                location=_designator_path(r.text_elem.getparent()),
            )
        )
    return out


def check_duplicate_designations(tree) -> list[Finding]:
    body = billxml.legis_body(tree)
    out = []
    for parent in body.iter():
        if not isinstance(parent.tag, str):
            continue
        by_tag: dict[str, list] = {}
        for k in billxml.structural_children(parent):
            if billxml.enum_value(k):
                by_tag.setdefault(k.tag, []).append(k)
        for tag, sibs in by_tag.items():
            seen: dict[str, object] = {}
            for s in sibs:
                d = billxml.enum_value(s)
                if d in seen:
                    out.append(
                        Finding(
                            type="duplicate_designation",
                            criterion=None,
                            detail=f"two sibling {tag}s carry designator ({d})",
                            anchor=s.find("enum").text.strip(),
                            location=_designator_path(s),
                        )
                    )
                seen[d] = s
    return out


def check_sequence_gaps(tree) -> list[Finding]:
    body = billxml.legis_body(tree)
    out = []
    for parent in body.iter():
        if not isinstance(parent.tag, str):
            continue
        by_tag: dict[str, list] = {}
        for k in billxml.structural_children(parent):
            if billxml.enum_value(k):
                by_tag.setdefault(k.tag, []).append(k)
        for tag, sibs in by_tag.items():
            if len(sibs) < 3:
                continue
            ordinals = [
                billxml.designator_ordinal(tag, billxml.enum_value(s)) for s in sibs
            ]
            if any(o is None for o in ordinals):
                continue
            increasing = all(b > a for a, b in zip(ordinals, ordinals[1:]))
            if not increasing:
                continue
            for i, (a, b) in enumerate(zip(ordinals, ordinals[1:])):
                if b != a + 1:
                    out.append(
                        Finding(
                            type="sequence_gap",
                            criterion=None,
                            detail=(
                                f"{tag} designators jump from "
                                f"({billxml.enum_value(sibs[i])}) to "
                                f"({billxml.enum_value(sibs[i + 1])})"
                            ),
                            anchor=sibs[i + 1].find("enum").text.strip(),
                            location=_designator_path(sibs[i + 1]),
                        )
                    )
    return out


def check_dates(tree) -> list[Finding]:
    body = billxml.legis_body(tree)
    out = []
    for text_elem in body.iter("text", "header"):
        content = "".join(text_elem.itertext())
        for m in _DATE_RE.finditer(content):
            month, day, year = m.group(1), int(m.group(2)), int(m.group(3))
            if day > calendar.monthrange(year, _MONTH_NUM[month])[1]:
                out.append(
                    Finding(
                        type="invalid_date",
                        criterion=None,
                        detail=f"'{m.group(0)}' is not a calendar date",
                        anchor=m.group(0),
                        location=_designator_path(text_elem.getparent()),
                    )
                )
    return out


def _governing_usc_cite(qb) -> str | None:
    node = qb.getparent()
    while node is not None:
        if isinstance(node.tag, str) and node.tag in billxml.STRUCTURAL_TAGS:
            t = node.find("text")
            if t is not None:
                x = t.find(".//external-xref")
                if x is not None and x.get("legal-doc") == "usc":
                    return x.get("parsable-cite")
        node = node.getparent()
    return None


def check_designation_collisions(tree) -> list[Finding]:
    """Two quoted-block insertions adding the same SUBSECTION designator to
    the same U.S. Code section. Restricted to subsections because a
    section-level cite is exactly the granularity subsections attach at;
    deeper levels would need the chapeau path to avoid false collisions."""
    body = billxml.legis_body(tree)
    additions: dict[tuple[str, str], list] = defaultdict(list)
    for qb in body.iter("quoted-block"):
        kids = billxml.structural_children(qb)
        if not kids or kids[0].tag != "subsection":
            continue
        d = billxml.enum_value(kids[0])
        cite = _governing_usc_cite(qb)
        if d and cite:
            additions[(cite, d)].append(kids[0])
    out = []
    for (cite, d), elems in additions.items():
        if len(elems) < 2:
            continue
        out.append(
            Finding(
                type="designation_collision",
                criterion="L1.6",
                detail=(
                    f"{len(elems)} separate insertions each add subsection "
                    f"({d}) to {cite}"
                ),
                anchor=elems[-1].find("enum").text.strip(),
                location=_designator_path(elems[-1]),
            )
        )
    return out


def check_dtd(tree, dtd_path: str | Path) -> list[Finding]:
    ok, errors = billxml.dtd_validate(tree, dtd_path)
    if ok:
        return []
    return [
        Finding(
            type="dtd_invalid",
            criterion=None,
            detail=errors[0] if errors else "document fails the Bill DTD",
            anchor="",
            location="document",
        )
    ]


CHECKS = {
    "unresolved_reference": check_references,
    "duplicate_designation": check_duplicate_designations,
    "sequence_gap": check_sequence_gaps,
    "invalid_date": check_dates,
    "designation_collision": check_designation_collisions,
}


def run_checks(tree, dtd_path: str | Path | None = None) -> list[Finding]:
    out: list[Finding] = []
    for check in CHECKS.values():
        out.extend(check(tree))
    if dtd_path is not None:
        out.extend(check_dtd(tree, dtd_path))
    return out


def finding_dict(f: Finding) -> dict:
    return asdict(f)
