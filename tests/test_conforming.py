from pathlib import Path

from legislint import billxml, conforming

FIXTURES = Path(__file__).parent / "fixtures"
FTC_BILL = FIXTURES / "BILLS-119hr10003ih.xml"


def test_find_conforming_blocks():
    info = billxml.load_bill(FTC_BILL)
    blocks = conforming.find_conforming_blocks(info.tree)
    assert len(blocks) == 1
    _, block = blocks[0]
    assert block.header == "Conforming amendment"
    assert block.usc_cites == ["usc/15/56"]
    assert block.designator == "b"


def test_build_case_removes_and_renumbers():
    info = billxml.load_bill(FTC_BILL)
    built = conforming.build_conforming_case(info.tree)
    assert built is not None
    text, gold, n = built
    assert gold == ["usc/15/56"]
    assert n == 1
    assert "Conforming amendment" not in text
    # old subsection (c) Applicability renumbers to (b): no designator gap leaks
    assert "(b) Applicability" in text
    assert "15 U.S.C. 56" not in text
    # source tree untouched
    assert len(conforming.find_conforming_blocks(info.tree)) == 1


def test_multi_target_case_with_toc_scrub():
    info = billxml.load_bill(FIXTURES / "BILLS-119hr7050ih.xml")
    built = conforming.build_conforming_case(info.tree)
    assert built is not None
    text, gold, n = built
    assert gold == ["usc/21/360eee", "usc/21/379aa", "usc/42/262"]
    # the whole conforming SECTION is gone, and so is its TOC entry
    assert "onforming" not in text
    assert n == 1


def test_parse_model_cites_normalization():
    items = [
        {"citation": "15 U.S.C. 56(a)(2)(A)", "description": "strike parenthetical"},
        {"citation": "usc/15/53", "description": "x"},
        {"citation": "Section 16 of the FTC Act (15 U.S.C. 56)", "description": "y"},
        {"citation": "", "description": "empty"},
        {"notacitation": True},
    ]
    assert conforming.parse_model_cites(items) == {"usc/15/56", "usc/15/53"}
