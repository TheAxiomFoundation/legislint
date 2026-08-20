"""Conforming-amendments recall task.

OLC drafters carry "what else must change" in their heads: a substantive
amendment usually forces conforming amendments elsewhere (definitions,
cross-references, tables of contents, penalty lists). Real bills publish
that knowledge in sections and subsections headed "Conforming amendment(s)".

Task construction: remove those blocks from a bill, renumber the surviving
siblings so the removal leaves no designator gap that would leak its
location, and ask the model to enumerate the conforming amendments the bill
needs. Gold = the U.S. Code citations targeted by the removed blocks
(external-xref parsable-cite attributes, e.g. "usc/15/56").

Scoring is recall-first: the model recovers the drafters' actual conforming
targets. Precision is advisory — a model may propose a legitimate conforming
change the drafters placed elsewhere or omitted.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from lxml import etree

from . import billxml

_CONFORMING_HEADER_RE = re.compile(r"^\s*conforming\s+(and\s+\w+\s+)?amendments?\s*$", re.I)
_CONFORMING_TOC_RE = re.compile(r"conforming\s+(and\s+\w+\s+)?amendments?", re.I)

# fallback textual citation: "Section 16(a)(2)(A) of the Federal Trade
# Commission Act (15 U.S.C. 56(a)(2)(A))"
_USC_TEXT_RE = re.compile(r"\b(\d+)\s+U\.S\.C\.\s+(\d+[a-z0-9\-]*)", re.I)


@dataclass
class ConformingBlock:
    elem_tag: str
    designator: str | None
    header: str
    usc_cites: list[str]     # normalized "usc/15/56"
    text: str


def _norm_cite(parsable: str) -> str | None:
    """Normalize a parsable-cite to usc/<title>/<section>."""
    parts = parsable.strip("/").split("/")
    if len(parts) >= 3 and parts[0] == "usc":
        return f"usc/{parts[1]}/{parts[2]}"
    return None


def normalize_usc_text_cite(title: str, section: str) -> str:
    """'15 U.S.C. 56(a)(2)(A)' -> usc/15/56 (strip subsection path)."""
    sec = re.split(r"[(\s]", section)[0]
    return f"usc/{title}/{sec}"


def find_conforming_blocks(tree) -> list[tuple[etree._Element, ConformingBlock]]:
    body = billxml.legis_body(tree)
    found = []
    for elem in billxml.iter_structural(body):
        if billxml.in_quoted_block(elem):
            continue
        header = elem.find("header")
        if header is None or header.text is None:
            continue
        if not _CONFORMING_HEADER_RE.match("".join(header.itertext())):
            continue
        cites: list[str] = []
        for xref in elem.iter("external-xref"):
            if xref.get("legal-doc") == "usc":
                c = _norm_cite(xref.get("parsable-cite", ""))
                if c:
                    cites.append(c)
        text = "".join(elem.itertext())
        if not cites:
            for m in _USC_TEXT_RE.finditer(text):
                cites.append(normalize_usc_text_cite(m.group(1), m.group(2)))
        cites = sorted(set(cites))
        found.append(
            (
                elem,
                ConformingBlock(
                    elem_tag=elem.tag,
                    designator=billxml.enum_value(elem),
                    header="".join(header.itertext()).strip(),
                    usc_cites=cites,
                    text=re.sub(r"\s+", " ", text).strip(),
                ),
            )
        )
    return found


def _renumber_siblings(parent: etree._Element, tag: str) -> None:
    """Reassign consecutive designators to `tag` children after a removal,
    preserving each element's punctuation style."""
    sibs = [c for c in billxml.structural_children(parent) if c.tag == tag]
    if not sibs:
        return
    first = billxml.enum_value(sibs[0])
    start = billxml.designator_ordinal(tag, first) if first else None
    if start is None:
        return
    for offset, sib in enumerate(sibs):
        enum = sib.find("enum")
        if enum is None or enum.text is None:
            continue
        desig = billxml.ordinal_to_designator(tag, start + offset)
        if desig is None:
            continue
        style = enum.text.strip()
        enum.text = f"({desig})" if style.startswith("(") else f"{desig}."


def build_conforming_case(tree) -> tuple[str, list[str], int] | None:
    """Return (redacted_text, gold_cites, n_blocks_removed) or None if the
    bill has no usable conforming blocks."""
    blocks = find_conforming_blocks(tree)
    blocks = [(e, b) for e, b in blocks if b.usc_cites]
    if not blocks:
        return None
    work = copy.deepcopy(tree)
    # re-find in the copy (same document order)
    work_blocks = find_conforming_blocks(work)
    work_blocks = [(e, b) for e, b in work_blocks if b.usc_cites]
    gold: list[str] = []
    parents = []
    for elem, block in work_blocks:
        gold.extend(block.usc_cites)
        parent = elem.getparent()
        parents.append((parent, elem.tag))
        parent.remove(elem)
    for parent, tag in parents:
        _renumber_siblings(parent, tag)
    _scrub_toc_leak(work)
    text = billxml.render_text(work)
    return text, sorted(set(gold)), len(work_blocks)


def _scrub_toc_leak(tree) -> None:
    """A table of contents naming the removed block ("Sec. 5. Conforming
    amendments.") would leak the redaction. Drop those entries."""
    root = tree.getroot()
    for tag in ("toc-entry", "multi-column-toc-entry"):
        for entry in list(root.iter(tag)):
            if _CONFORMING_TOC_RE.search("".join(entry.itertext())):
                entry.getparent().remove(entry)


def parse_model_cites(items: list[dict]) -> set[str]:
    """Normalize model-proposed amendments to usc/T/S keys.

    Accepts {"citation": "15 U.S.C. 56(a)(2)(A)"} or {"citation": "usc/15/56"}.
    """
    out: set[str] = set()
    for item in items:
        raw = str(item.get("citation", "")).strip()
        if not raw:
            continue
        norm = _norm_cite(raw) if raw.lower().startswith("usc") else None
        if norm:
            out.add(norm)
            continue
        m = _USC_TEXT_RE.search(raw)
        if m:
            out.add(normalize_usc_text_cite(m.group(1), m.group(2)))
    return out
