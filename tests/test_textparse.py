from pathlib import Path

import pytest
from lxml import etree

from legislint import billxml, findings, lint_text, parse_text, textparse

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_PATHS = sorted(FIXTURES.glob("*.xml"))
FTC_BILL = FIXTURES / "BILLS-119hr10003ih.xml"
TRACKED_FINDINGS = {
    "unresolved_reference",
    "duplicate_designation",
    "sequence_gap",
    "invalid_date",
}


def _finding_signature(tree):
    return sorted(
        (finding.type, finding.detail)
        for finding in findings.run_checks(tree)
        if finding.type in TRACKED_FINDINGS
    )


def _structural_signature(tree):
    body = billxml.legis_body(tree)
    return [
        (element.tag, billxml.enum_value(element))
        for element in billxml.iter_structural(body)
    ]


@pytest.mark.parametrize("path", FIXTURE_PATHS, ids=lambda path: path.stem)
def test_rendered_fixture_preserves_findings(path):
    original = billxml.load_bill(path).tree
    parsed = parse_text(billxml.render_text(original))
    assert _finding_signature(parsed) == _finding_signature(original)


@pytest.mark.parametrize("path", FIXTURE_PATHS, ids=lambda path: path.stem)
def test_rendered_fixture_preserves_structure(path):
    original = billxml.load_bill(path).tree
    parsed = parse_text(billxml.render_text(original))
    assert _structural_signature(parsed) == _structural_signature(original)


def test_deleted_quoted_paragraph_is_linted_and_restores_cleanly():
    original = billxml.load_bill(FTC_BILL).tree
    lines = billxml.render_text(original).splitlines()
    matches = [
        index
        for index, line in enumerate(lines)
        if line.lstrip().startswith("(2) Disgorgement ")
    ]
    assert len(matches) == 1

    index = matches[0]
    removed = lines.pop(index)
    edited = "\n".join(lines) + "\n"
    edited_types = {finding.type for finding in findings.run_checks(parse_text(edited))}
    assert edited_types & {"unresolved_reference", "sequence_gap"}

    lines.insert(index, removed)
    restored = "\n".join(lines) + "\n"
    assert findings.run_checks(parse_text(restored)) == []


def test_lint_text_accepts_garbage_without_raising():
    assert isinstance(lint_text("complete garbage ^^^ not a bill"), list)


def test_parse_text_is_deterministic():
    rendered = billxml.render_text(billxml.load_bill(FTC_BILL).tree)
    first = etree.tostring(parse_text(rendered), encoding="unicode")
    second = etree.tostring(parse_text(rendered), encoding="unicode")
    assert first == second


def test_punctuation_continuations_and_quotes_are_preserved():
    tree = parse_text(
        "Preamble\n"
        "Official title\n\n"
        "1. Heading Inline “words” are text.\n"
        "continued on another line\n"
        "    “   \n"
        "        (e) Inserted text\n"
        "    ”; and   \n"
    )
    body = billxml.legis_body(tree)
    section = next(billxml.iter_structural(body))
    quoted_block = section.find("quoted-block")

    assert section.findtext("enum") == "1."
    assert section.findtext("text") == (
        "Heading Inline “words” are text. continued on another line"
    )
    assert quoted_block is not None
    assert quoted_block.findtext("subsection/enum") == "(e)"
    assert quoted_block.findtext("after-quoted-block") == "; and"


def test_schemes_recover_legal_hierarchy_from_bad_indentation():
    tree = parse_text(
        "1. Section\n"
        "(a) Subsection\n"
        "(1) Paragraph\n"
        "(A) Subparagraph\n"
        "(i) Clause\n"
        "(I) Subclause\n"
        "(aa) Item\n"
    )
    elements = list(billxml.iter_structural(billxml.legis_body(tree)))

    assert [element.tag for element in elements] == [
        "section",
        "subsection",
        "paragraph",
        "subparagraph",
        "clause",
        "subclause",
        "item",
    ]
    assert [element.findtext("enum") for element in elements] == [
        "1.",
        "(a)",
        "(1)",
        "(A)",
        "(i)",
        "(I)",
        "(aa)",
    ]
    pairs = zip(elements, elements[1:])
    assert all(child.getparent() is parent for parent, child in pairs)


def test_legal_dedent_uses_indentation_to_choose_parent():
    tree = parse_text(
        "1. Section\n"
        "    (a) Subsection\n"
        "        (1) Nested paragraph\n"
        "    (2) Paragraph directly under the section\n"
    )
    elements = list(billxml.iter_structural(billxml.legis_body(tree)))

    section, subsection, nested, direct = elements
    assert nested.getparent() is subsection
    assert direct.getparent() is section


def test_illegal_indentation_does_not_override_olc_parentage():
    tree = parse_text(
        "1. Section\n"
        "    (a) Subsection\n"
        "        (1) Paragraph\n"
        "            (A) Subparagraph\n"
        "        (i) Clause with edited indentation\n"
    )
    elements = list(billxml.iter_structural(billxml.legis_body(tree)))

    assert elements[-1].tag == "clause"
    assert elements[-1].getparent() is elements[-2]


def test_overindented_subsection_does_not_create_false_reference():
    tree = parse_text(
        "1. Section\n"
        "    (h) Eighth subsection\n"
        "        (i) Ninth subsection\n"
        "    (j) See subsection (i).\n"
    )
    elements = list(billxml.iter_structural(billxml.legis_body(tree)))

    assert [element.tag for element in elements] == [
        "section",
        "subsection",
        "subsection",
        "subsection",
    ]
    assert findings.check_references(tree) == []


def test_lint_text_converts_parser_failure_to_finding(monkeypatch):
    def fail(_text):
        raise ValueError("broken input")

    monkeypatch.setattr(textparse, "parse_text", fail)
    result = textparse.lint_text("anything")

    assert result == [
        findings.Finding(
            type="parse_error",
            criterion=None,
            detail="ValueError: broken input",
            anchor="",
            location="document",
        )
    ]
