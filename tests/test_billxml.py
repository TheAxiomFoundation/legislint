from pathlib import Path

import pytest

from legislint import billxml

FIXTURES = Path(__file__).parent / "fixtures"
FTC_BILL = FIXTURES / "BILLS-119hr10003ih.xml"
HSA_BILL = FIXTURES / "BILLS-119hr10072ih.xml"


def test_load_bill_metadata():
    info = billxml.load_bill(FTC_BILL)
    assert info.congress == 119
    assert info.bill_number == "hr10003"
    assert info.stage == "ih"
    assert "Consumer Protection and Recovery Act" in info.title
    assert info.date == "2026-07-30"


def test_structural_walk_finds_hierarchy():
    info = billxml.load_bill(FTC_BILL)
    body = billxml.legis_body(info.tree)
    tags = {e.tag for e in billxml.iter_structural(body)}
    assert {"section", "subsection", "paragraph", "subparagraph", "clause"} <= tags
    sections = [e for e in billxml.structural_children(body) if e.tag == "section"]
    assert [billxml.enum_value(s) for s in sections] == ["1", "2"]


def test_designator_ordinals_roundtrip():
    assert billxml.designator_ordinal("paragraph", "3") == 3
    assert billxml.designator_ordinal("subsection", "c") == 3
    assert billxml.designator_ordinal("subparagraph", "B") == 2
    assert billxml.designator_ordinal("clause", "iv") == 4
    # the tag decides the scheme: clause (i) is roman, not alpha
    assert billxml.designator_ordinal("clause", "i") == 1
    assert billxml.designator_ordinal("subsection", "i") == 9
    for tag, n in [("clause", 7), ("subsection", 26), ("paragraph", 12)]:
        d = billxml.ordinal_to_designator(tag, n)
        assert billxml.designator_ordinal(tag, d) == n


def test_quoted_block_internal_refs_resolve():
    info = billxml.load_bill(FTC_BILL)
    body = billxml.legis_body(info.tree)
    refs = billxml.find_internal_refs(body, adjudicable_only=True)
    # the inserted FTC Act subsection (e) refers to its own paragraphs
    assert refs, "expected adjudicable references in the quoted subsection (e)"
    assert all(r.resolves for r in refs)
    levels = {r.level for r in refs}
    assert "paragraph" in levels


def test_amendatory_refs_not_adjudicable():
    # "in subsection (b)—" in the FTC bill points at the FTC Act, and must
    # never be scored against the bill's own structure
    info = billxml.load_bill(FTC_BILL)
    body = billxml.legis_body(info.tree)
    all_refs = billxml.find_internal_refs(body)
    amendatory = [
        r for r in all_refs if not r.adjudicable and r.scope_elem is not None
    ]
    assert amendatory, "expected non-adjudicable amendatory references"


def test_compound_ref_dangling_in_freestanding_bill():
    # H.R. 7682 s.7(a) references "subsection (c)(1)"; s.7(c) has no
    # paragraphs. A real published-bill defect the harness must catch —
    # terra found it first (2026-08-19 v2 smoke) and the validator did not.
    info = billxml.load_bill(FIXTURES / "BILLS-119hr7682ih.xml")
    body = billxml.legis_body(info.tree)
    refs = billxml.find_internal_refs(body, adjudicable_only=True)
    dangling = [r for r in refs if not r.resolves]
    assert any(r.path == ("c", "1") for r in dangling)


def test_render_text_deterministic_and_faithful():
    info = billxml.load_bill(FTC_BILL)
    text1 = billxml.render_text(info.tree)
    text2 = billxml.render_text(billxml.load_bill(FTC_BILL).tree)
    assert text1 == text2
    assert "Conforming amendment" in text1
    assert "15 U.S.C. 56" in text1
    assert "H. R. 10003" in text1


def test_render_delimits_quoted_strings():
    # 'by striking <quote>and</quote>' must render with quote marks, or the
    # renderer itself creates an undelimited-instruction defect
    info = billxml.load_bill(FTC_BILL)
    text = billxml.render_text(info.tree)
    assert "by inserting “has violated,” after “corporation”" in text
    assert "by striking “that” and inserting “that either (A)”" in text


def test_dtd_validates_real_bills():
    dtd = Path(__file__).parents[1] / "vendor" / "bill.dtd"
    for bill in (FTC_BILL, HSA_BILL):
        info = billxml.load_bill(bill)
        ok, errors = billxml.dtd_validate(info.tree, dtd)
        assert ok, f"{bill.name}: {errors[:3]}"


def test_load_bill_rejects_odd_names(tmp_path):
    bogus = tmp_path / "whatever.xml"
    bogus.write_text("<bill/>")
    with pytest.raises(ValueError):
        billxml.load_bill(bogus)
