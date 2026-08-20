"""Parse and render congressional bill XML (GPO Bill DTD).

Grounded in observed structure of govinfo bulk BILLS files (119th Congress):
root <bill> -> <legis-body> -> nested structural elements, each carrying
<enum>, optional <header>, and <text>. Amendatory instructions quote strings
with <quote> and cite law with <external-xref parsable-cite="usc/T/S">.
Inserted law text appears inside <quoted-block>.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

# OLC structural hierarchy, outermost first. Each level has a fixed
# designator scheme, which resolves the alpha/roman ambiguity for
# enums like (i): the element tag, not the glyph, decides.
STRUCTURAL_TAGS = (
    "division",
    "title",
    "subtitle",
    "chapter",
    "subchapter",
    "part",
    "subpart",
    "section",
    "subsection",
    "paragraph",
    "subparagraph",
    "clause",
    "subclause",
    "item",
    "subitem",
)

ENUM_SCHEMES = {
    "section": "arabic",
    "subsection": "alpha_lower",
    "paragraph": "arabic",
    "subparagraph": "alpha_upper",
    "clause": "roman_lower",
    "subclause": "roman_upper",
    "item": "alpha_lower_double",
    "subitem": "roman_lower_double",
}

_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


def parse_bill(path: str | Path) -> etree._ElementTree:
    """Parse a bill XML file without resolving its relative DTD reference."""
    parser = etree.XMLParser(load_dtd=False, resolve_entities=False, remove_pis=False)
    return etree.parse(str(path), parser)


def dtd_validate(tree: etree._ElementTree, dtd_path: str | Path) -> tuple[bool, list[str]]:
    """Validate a bill tree against the vendored GPO bill.dtd."""
    with open(dtd_path, "rb") as fh:
        dtd = etree.DTD(fh)
    ok = dtd.validate(tree.getroot())
    errors = [str(e) for e in dtd.error_log.filter_from_errors()]
    return ok, errors


def legis_body(tree: etree._ElementTree) -> etree._Element:
    body = tree.getroot().find("legis-body")
    if body is None:
        # Resolutions use <resolution-body>; out of scope for v0 suites.
        raise ValueError("no <legis-body> element")
    return body


def enum_value(elem: etree._Element) -> str | None:
    """The designator string with punctuation stripped: '(a)' -> 'a', '1.' -> '1'."""
    enum = elem.find("enum")
    if enum is None or enum.text is None:
        return None
    return enum.text.strip().strip("().").strip()


def structural_children(elem: etree._Element) -> list[etree._Element]:
    return [c for c in elem if isinstance(c.tag, str) and c.tag in STRUCTURAL_TAGS]


def iter_structural(body: etree._Element):
    """Yield every structural element under a body, document order."""
    for elem in body.iter():
        if isinstance(elem.tag, str) and elem.tag in STRUCTURAL_TAGS:
            yield elem


def in_quoted_block(elem: etree._Element) -> bool:
    return any(a.tag == "quoted-block" for a in elem.iterancestors())


def _alpha_ord(s: str) -> int | None:
    """a=1..z=26, aa=27... Only same-letter doubling is used by OLC ((aa), (bb))."""
    if not s or not s.isalpha():
        return None
    if len(set(s.lower())) != 1:
        return None
    return (len(s) - 1) * 26 + (ord(s.lower()[0]) - ord("a") + 1)


def _roman_ord(s: str) -> int | None:
    s = s.lower()
    if not s or any(ch not in _ROMAN_VALUES for ch in s):
        return None
    total = 0
    for idx, ch in enumerate(s):
        v = _ROMAN_VALUES[ch]
        if idx + 1 < len(s) and _ROMAN_VALUES[s[idx + 1]] > v:
            total -= v
        else:
            total += v
    return total


def designator_ordinal(tag: str, designator: str) -> int | None:
    """Position of a designator in its level's scheme, or None if unparseable."""
    scheme = ENUM_SCHEMES.get(tag)
    if scheme is None or not designator:
        return None
    if scheme == "arabic":
        return int(designator) if designator.isdigit() else None
    if scheme in ("alpha_lower", "alpha_upper", "alpha_lower_double"):
        return _alpha_ord(designator)
    if scheme in ("roman_lower", "roman_upper", "roman_lower_double"):
        return _roman_ord(designator)
    return None


def ordinal_to_designator(tag: str, ordinal: int) -> str | None:
    """Inverse of designator_ordinal for ordinals we can render."""
    scheme = ENUM_SCHEMES.get(tag)
    if scheme is None or ordinal < 1:
        return None
    if scheme == "arabic":
        return str(ordinal)
    if scheme in ("alpha_lower", "alpha_upper", "alpha_lower_double"):
        reps, pos = divmod(ordinal - 1, 26)
        ch = chr(ord("a") + pos)
        out = ch * (reps + 1)
        return out.upper() if scheme == "alpha_upper" else out
    if scheme in ("roman_lower", "roman_upper", "roman_lower_double"):
        pairs = [
            (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"),
            (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
            (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
        ]
        n, out = ordinal, ""
        for val, sym in pairs:
            while n >= val:
                out += sym
                n -= val
        return out.upper() if scheme == "roman_upper" else out
    return None


# ---------------------------------------------------------------------------
# Internal cross-references
# ---------------------------------------------------------------------------

# "subsection (b)", "paragraphs (1) and (3)", "subsection (c)(1)(A)" ...
_REF_RE = re.compile(
    r"\b(subsection|paragraph|subparagraph|clause|subclause)s?\s+"
    r"((?:\([0-9a-zA-Z]{1,4}\))+)"
)

_COMPONENT_RE = re.compile(r"\(([0-9a-zA-Z]{1,4})\)")

_LEVEL_PARENT_TAG = {
    "subsection": "section",
    "paragraph": "subsection",
    "subparagraph": "paragraph",
    "clause": "subparagraph",
    "subclause": "clause",
}

_LEVEL_CHILD_TAG = {
    "subsection": "paragraph",
    "paragraph": "subparagraph",
    "subparagraph": "clause",
    "clause": "subclause",
    "subclause": "item",
    "item": "subitem",
}

# "paragraph (2) of section 5" / "of such Act" — the reference names its own
# parent elsewhere; resolving it against the enclosing context is wrong
_OF_ELSEWHERE_RE = re.compile(
    r"^\s*of\s+(section|subsection|paragraph|subparagraph|clause|subclause|"
    r"such|this\s+(?:section|subsection|paragraph|subparagraph|clause|Act|title)|"
    r"the|an?\s|title\b)",
    re.I,
)

# text signalling that surrounding references target the law being amended,
# which the bill alone cannot adjudicate
_AMEND_MARKERS = re.compile(
    r"\bis amended\b|\bare amended\b|\bis further amended\b|\bby striking\b|"
    r"\bby inserting\b|\bby adding\b|\bby redesignating\b|\bis repealed\b|"
    r"\bare repealed\b"
)


@dataclass
class InternalRef:
    level: str              # level of the FIRST path component, e.g. "subsection"
    path: tuple[str, ...]   # designator chain, e.g. ("c", "1")
    text_elem: etree._Element
    scope_elem: etree._Element | None  # element the first component resolves against
    adjudicable: bool       # validity decidable from the bill alone
    resolves: bool          # full chain resolves (meaningful when adjudicable)

    @property
    def designator(self) -> str:
        return self.path[0]

    @property
    def surface(self) -> str:
        return f"{self.level} " + "".join(f"({d})" for d in self.path)


def _text_content(elem: etree._Element) -> str:
    return "".join(elem.itertext())


def _scope_for(level: str, context: etree._Element) -> etree._Element | None:
    """Nearest ancestor (or self) that contains designators of `level`.

    A reference like "paragraph (2)" written inside a paragraph of
    subsection (e) resolves among the paragraphs of that subsection.
    """
    parent_tag = _LEVEL_PARENT_TAG[level]
    node = context
    while node is not None:
        if isinstance(node.tag, str) and node.tag == parent_tag:
            return node
        node = node.getparent()
    return None


def _find_child(scope: etree._Element, level: str, designator: str) -> etree._Element | None:
    for child in iter_structural(scope):
        if child.tag == level and enum_value(child) == designator:
            return child
    return None


def _resolve_chain(scope: etree._Element, level: str, path: tuple[str, ...]) -> bool:
    """Resolve a designator chain: the first component at `level` within
    `scope`, each further component one level down within the resolved
    element ("subsection (c)(1)" = paragraph (1) of subsection (c))."""
    node = _find_child(scope, level, path[0])
    if node is None:
        return False
    current_level = level
    for component in path[1:]:
        child_level = _LEVEL_CHILD_TAG.get(current_level)
        if child_level is None:
            return False
        node = _find_child(node, child_level, component)
        if node is None:
            return False
        current_level = child_level
    return True


def _enclosing_quoted_block(elem: etree._Element) -> etree._Element | None:
    return next((a for a in elem.iterancestors() if a.tag == "quoted-block"), None)


def _in_amendatory_context(text_elem: etree._Element) -> bool:
    """True when the element's own content, or the direct <text> of any
    structural ancestor, carries amendatory language — references there point
    at the target statute, which the bill alone cannot adjudicate."""
    own = _text_content(text_elem)
    if _AMEND_MARKERS.search(own):
        return True
    node = text_elem.getparent()
    while node is not None:
        if isinstance(node.tag, str) and node.tag in STRUCTURAL_TAGS:
            t = node.find("text")
            if t is not None and _AMEND_MARKERS.search(_text_content(t)):
                return True
        node = node.getparent()
    return False


def find_internal_refs(
    body: etree._Element, adjudicable_only: bool = False
) -> list[InternalRef]:
    """Every "<level> (x)(y)..." reference in <text>/<header> nodes.

    A reference is ADJUDICABLE from the bill alone when either:
    - it sits inside a <quoted-block> and its resolution scope is inside the
      same block (inserted law carries its own context), or
    - it sits in freestanding (non-amendatory) bill text, where in-bill
      references resolve against the bill itself.

    References in amendatory context outside quoted blocks point at the law
    being amended and are never adjudicable without the target statute.
    """
    refs: list[InternalRef] = []
    for text_elem in body.iter("text", "header"):
        content = _text_content(text_elem)
        for m in _REF_RE.finditer(content):
            level = m.group(1)
            path = tuple(_COMPONENT_RE.findall(m.group(2)))
            trailing = content[m.end() : m.end() + 40]
            if _OF_ELSEWHERE_RE.match(trailing):
                # "paragraph (2) of section 5" — parented elsewhere; the
                # enclosing context is the wrong scope, so never adjudicate
                continue
            scope = _scope_for(level, text_elem)
            block = _enclosing_quoted_block(text_elem)
            if scope is None:
                adjudicable = False
                resolves = False
            elif block is not None:
                scope_in_block = scope is block or any(
                    a is block for a in scope.iterancestors()
                )
                adjudicable = scope_in_block
                resolves = adjudicable and _resolve_chain(scope, level, path)
            else:
                adjudicable = not _in_amendatory_context(text_elem)
                resolves = adjudicable and _resolve_chain(scope, level, path)
            if adjudicable_only and not adjudicable:
                continue
            refs.append(
                InternalRef(
                    level=level,
                    path=path,
                    text_elem=text_elem,
                    scope_elem=scope,
                    adjudicable=adjudicable,
                    resolves=resolves,
                )
            )
    return refs


# ---------------------------------------------------------------------------
# Deterministic plain-text rendering
# ---------------------------------------------------------------------------

def text_with_quotes(elem: etree._Element) -> str:
    """Text content with <quote> children delimited by typographic quotes,
    matching the official rendering. Bare itertext() would turn
    'by striking <quote>and</quote>' into 'by striking and' — an apparent
    drafting defect the renderer itself created."""
    parts: list[str] = []

    def walk(e: etree._Element) -> None:
        if not isinstance(e.tag, str):
            if e.tail:
                parts.append(e.tail)
            return
        is_quote = e.tag == "quote"
        if is_quote:
            parts.append("“")
        if e.text:
            parts.append(e.text)
        for c in e:
            walk(c)
        if is_quote:
            parts.append("”")
        if e.tail:
            parts.append(e.tail)

    is_quote = elem.tag == "quote"
    if is_quote:
        parts.append("“")
    if elem.text:
        parts.append(elem.text)
    for c in elem:
        walk(c)
    if is_quote:
        parts.append("”")
    return "".join(parts)


def render_text(tree: etree._ElementTree) -> str:
    """Deterministic OLC-style plain rendering of a bill's legislative body.

    Used for mutation-suite inputs: original and mutant render through the
    same code, so the injected defect is the only difference.
    """
    root = tree.getroot()
    lines: list[str] = []
    form = root.find("form")
    if form is not None:
        legis_num = form.findtext("legis-num", default="").strip()
        official_title = form.findtext("official-title", default="").strip()
        if legis_num:
            lines.append(legis_num)
        if official_title:
            lines.append(official_title)
        lines.append("")
    body = root.find("legis-body")
    if body is not None:
        _render_children(body, lines, depth=0)
    return "\n".join(lines).rstrip() + "\n"


def _norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _render_children(elem: etree._Element, lines: list[str], depth: int) -> None:
    for child in elem:
        if not isinstance(child.tag, str):
            continue
        if child.tag in STRUCTURAL_TAGS:
            _render_structural(child, lines, depth)
        elif child.tag == "quoted-block":
            lines.append("    " * depth + "“")
            _render_children(child, lines, depth + 1)
            after = child.findtext("after-quoted-block", default="")
            lines.append("    " * depth + "”" + _norm_space(after))
        elif child.tag in ("text", "header", "enum", "after-quoted-block"):
            continue  # handled by the structural parent
        else:
            _render_children(child, lines, depth)


def _render_structural(elem: etree._Element, lines: list[str], depth: int) -> None:
    indent = "    " * depth
    enum = elem.find("enum")
    header = elem.find("header")
    text = elem.find("text")
    lead = ""
    if enum is not None and enum.text:
        lead = enum.text.strip()
    if header is not None:
        lead = f"{lead} {_norm_space(text_with_quotes(header))}".strip()
    first_line = lead
    if text is not None:
        body_text = _norm_space(text_with_quotes(text))
        first_line = f"{lead} {body_text}".strip() if lead else body_text
    if first_line:
        lines.append(indent + first_line)
    for child in elem:
        if not isinstance(child.tag, str):
            continue
        if child.tag in STRUCTURAL_TAGS:
            _render_structural(child, lines, depth + 1)
        elif child.tag == "quoted-block":
            lines.append(indent + "    “")
            _render_children(child, lines, depth + 2)
            after = child.findtext("after-quoted-block", default="")
            lines.append(indent + "    ”" + _norm_space(after))
        elif child.tag == "text":
            # inner quoted blocks live inside <text> in some bills
            for qb in child.findall("quoted-block"):
                lines.append(indent + "    “")
                _render_children(qb, lines, depth + 2)
                after = qb.findtext("after-quoted-block", default="")
                lines.append(indent + "    ”" + _norm_space(after))


@dataclass
class BillInfo:
    path: Path
    package_id: str          # BILLS-119hr10003ih
    congress: int
    bill_number: str         # hr10003
    stage: str               # ih, rh, eh, enr ...
    title: str
    date: str                # dc:date
    tree: etree._ElementTree = field(repr=False)


_PKG_RE = re.compile(r"BILLS-(\d+)([a-z]+\d+)([a-z]+)\.xml$")


def load_bill(path: str | Path) -> BillInfo:
    path = Path(path)
    m = _PKG_RE.search(path.name)
    if not m:
        raise ValueError(f"unrecognized bill filename: {path.name}")
    tree = parse_bill(path)
    ns = {"dc": "http://purl.org/dc/elements/1.1/"}
    title = tree.getroot().findtext("metadata/dublinCore/dc:title", default="", namespaces=ns)
    date = tree.getroot().findtext("metadata/dublinCore/dc:date", default="", namespaces=ns)
    if not date:
        action_date = tree.getroot().find("form/action/action-date")
        stamp = action_date.get("date") if action_date is not None else None
        if stamp and len(stamp) == 8 and stamp.isdigit():
            date = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}"
    return BillInfo(
        path=path,
        package_id=path.stem,
        congress=int(m.group(1)),
        bill_number=m.group(2),
        stage=m.group(3),
        title=title,
        date=date,
        tree=tree,
    )
