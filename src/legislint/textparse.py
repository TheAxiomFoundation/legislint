"""Parse legislint's deterministic plain-text bill rendering."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree

from . import billxml, findings

_SECTION_LINE_RE = re.compile(
    r"^(?P<enum>[0-9]+[A-Za-z]*\.)(?:\s+(?P<text>.*))?$"
)
_PAREN_LINE_RE = re.compile(
    r"^(?P<enum>\((?P<value>[0-9A-Za-z]+)\))(?:\s+(?P<text>.*))?$"
)
_ROMAN_LOWER = frozenset("ivxlcdm")
_ROMAN_UPPER = frozenset("IVXLCDM")
_TAG_RANK = {tag: index for index, tag in enumerate(billxml.STRUCTURAL_TAGS)}
_ALLOWED_PARENT_TAGS = {
    "section": frozenset(),
    "subsection": frozenset({"section"}),
    "paragraph": frozenset({"section", "subsection"}),
    "subparagraph": frozenset({"paragraph"}),
    "clause": frozenset({"subparagraph"}),
    "subclause": frozenset({"clause"}),
    "item": frozenset({"subclause"}),
    "subitem": frozenset({"item"}),
}


@dataclass
class _Frame:
    element: etree._Element
    tag: str
    indent: int

    @property
    def rank(self) -> int:
        return _TAG_RANK[self.tag]


@dataclass
class _Context:
    root: etree._Element
    base_indent: int
    stack: list[_Frame] = field(default_factory=list)


def _line_parts(line: str) -> tuple[str, str, list[str]] | None:
    section_match = _SECTION_LINE_RE.fullmatch(line)
    if section_match is not None:
        return (
            section_match.group("enum"),
            section_match.group("text") or "",
            ["section"],
        )

    paren_match = _PAREN_LINE_RE.fullmatch(line)
    if paren_match is None:
        return None

    enum = paren_match.group("enum")
    value = paren_match.group("value")
    remainder = paren_match.group("text") or ""
    if value.isdigit():
        return enum, remainder, ["paragraph"]

    repeated_letter = len(set(value.lower())) == 1
    candidates: list[str] = []
    if value.islower():
        if repeated_letter:
            candidates.append("subsection")
        if set(value) <= _ROMAN_LOWER:
            candidates.append("clause")
        if len(value) >= 2 and repeated_letter:
            candidates.append("item")
        if len(value) >= 2 and set(value) <= _ROMAN_LOWER:
            candidates.append("subitem")
    elif value.isupper():
        if repeated_letter:
            candidates.append("subparagraph")
        if set(value) <= _ROMAN_UPPER:
            candidates.append("subclause")

    if not candidates:
        return None
    return enum, remainder, candidates


def _parent_index(stack: list[_Frame], rank: int) -> int:
    for index in range(len(stack) - 1, -1, -1):
        if stack[index].rank < rank:
            return index
    return -1


def _resolved_parent_index(stack: list[_Frame], rank: int, indent: int) -> int:
    indented = [
        index
        for index, frame in enumerate(stack)
        if frame.indent < indent and frame.rank < rank
    ]
    if indented:
        return max(indented, key=lambda index: (stack[index].indent, index))
    return _parent_index(stack, rank)


def _parent_resolution(
    tag: str, indent: int, context: _Context
) -> tuple[int, bool, bool, bool, int]:
    allowed = _ALLOWED_PARENT_TAGS[tag]
    options = [
        index for index, frame in enumerate(context.stack) if frame.tag in allowed
    ]
    if context.root.tag == "quoted-block" or tag == "section":
        options.append(-1)

    def expected_indent(index: int) -> int:
        if index < 0:
            return context.base_indent
        return context.stack[index].indent + 4

    if options:
        exact = [index for index in options if expected_indent(index) == indent]
        if exact:
            parent_index = max(exact)
        elif len(context.stack) - 1 in options:
            parent_index = len(context.stack) - 1
        else:
            parent_index = min(
                options,
                key=lambda index: (
                    abs(indent - expected_indent(index)),
                    -index,
                ),
            )
        distance = abs(indent - expected_indent(parent_index))
        return (
            parent_index,
            True,
            distance == 0,
            parent_index == len(context.stack) - 1,
            distance,
        )

    rank = _TAG_RANK[tag]
    parent_index = _resolved_parent_index(context.stack, rank, indent)
    expected = (
        context.stack[parent_index].indent + 4
        if parent_index >= 0
        else context.base_indent
    )
    return (
        parent_index,
        False,
        expected == indent,
        parent_index == len(context.stack) - 1,
        abs(indent - expected),
    )


def _choose_tag(candidates: list[str], indent: int, context: _Context) -> str:
    if len(candidates) == 1:
        return candidates[0]
    if not context.stack:
        return min(candidates, key=lambda tag: _TAG_RANK[tag])

    def score(tag: str) -> tuple[int, int, int, int, int, int, int]:
        rank = _TAG_RANK[tag]
        parent_index, legal, exact, current, distance = _parent_resolution(
            tag, indent, context
        )
        if parent_index >= 0:
            parent = context.stack[parent_index]
            rank_gap = rank - parent.rank
        else:
            rank_gap = rank
        return (
            not legal,
            not exact,
            not current,
            distance,
            -parent_index,
            rank_gap,
            rank,
        )

    return min(candidates, key=score)


def _append_continuation(context: _Context, content: str) -> None:
    if not context.stack:
        return
    addition = content.strip()
    if not addition:
        return
    text_element = context.stack[-1].element.find("text")
    if text_element is None:
        text_element = etree.SubElement(context.stack[-1].element, "text")
    current = text_element.text or ""
    text_element.text = f"{current} {addition}".strip()


def parse_text(text: str) -> etree._ElementTree:
    root = etree.Element("bill")
    body = etree.SubElement(root, "legis-body")
    contexts = [_Context(body, 0)]
    started = False

    for raw_line in text.splitlines():
        expanded = raw_line.expandtabs(4)
        content = expanded.lstrip(" ")
        indent = len(expanded) - len(content)

        if not started:
            parts = _line_parts(content)
            if parts is None or parts[2] != ["section"]:
                continue
            started = True
        else:
            parts = _line_parts(content)

        if content.rstrip() == "“":
            context = contexts[-1]
            if context.stack:
                quoted_block = etree.SubElement(
                    context.stack[-1].element, "quoted-block"
                )
                contexts.append(_Context(quoted_block, indent + 4))
            continue

        if content.startswith("”"):
            if len(contexts) > 1:
                context = contexts.pop()
                suffix = content[1:].strip()
                if suffix:
                    after = etree.SubElement(context.root, "after-quoted-block")
                    after.text = suffix
            else:
                _append_continuation(contexts[-1], content)
            continue

        if parts is None:
            _append_continuation(contexts[-1], content)
            continue

        enum, remainder, candidates = parts
        context = contexts[-1]
        tag = _choose_tag(candidates, indent, context)
        parent_index, _, _, _, _ = _parent_resolution(tag, indent, context)
        if parent_index >= 0:
            parent = context.stack[parent_index].element
            context.stack = context.stack[: parent_index + 1]
        else:
            parent = context.root
            context.stack.clear()

        element = etree.SubElement(parent, tag)
        enum_element = etree.SubElement(element, "enum")
        enum_element.text = enum
        text_element = etree.SubElement(element, "text")
        text_element.text = remainder.strip()
        context.stack.append(_Frame(element, tag, indent))

    return etree.ElementTree(root)


def lint_text(text: str) -> list[findings.Finding]:
    try:
        return findings.run_checks(parse_text(text))
    except Exception as exc:
        return [
            findings.Finding(
                type="parse_error",
                criterion=None,
                detail=f"{type(exc).__name__}: {exc}",
                anchor="",
                location="document",
            )
        ]
