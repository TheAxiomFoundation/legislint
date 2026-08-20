import copy
import subprocess
import sys
from pathlib import Path

from lxml import etree

from legislint import billxml, findings

FIXTURES = Path(__file__).parent / "fixtures"
FTC_BILL = FIXTURES / "BILLS-119hr10003ih.xml"
H2B_BILL = FIXTURES / "BILLS-119hr7682ih.xml"
DTD = Path(__file__).parents[1] / "vendor" / "bill.dtd"


def test_clean_bill_yields_no_findings():
    tree = billxml.load_bill(FTC_BILL).tree
    assert findings.run_checks(tree, dtd_path=DTD) == []


def test_hr7682_regression_unresolved_compound_reference():
    # H.R. 7682 §7(a) references "subsection (c)(1)"; §7(c) has no
    # paragraphs. Found by a model during eval calibration, 2026-08-19.
    tree = billxml.load_bill(H2B_BILL).tree
    found = findings.run_checks(tree)
    refs = [f for f in found if f.type == "unresolved_reference"]
    assert any("subsection (c)(1)" in f.detail for f in refs)
    assert all(f.criterion == "L1.1a" for f in refs)


def test_duplicate_designation_detected():
    tree = billxml.load_bill(FTC_BILL).tree
    body = billxml.legis_body(tree)
    sections = [e for e in billxml.structural_children(body) if e.tag == "section"]
    sections[1].find("enum").text = "1."
    found = findings.check_duplicate_designations(tree)
    assert len(found) == 1
    assert "designator (1)" in found[0].detail


def test_sequence_gap_detected():
    tree = billxml.load_bill(FTC_BILL).tree
    body = billxml.legis_body(tree)
    # clauses (i)..(vii) exist in section 2; bump the last to (ix)
    clauses = [e for e in billxml.iter_structural(body) if e.tag == "clause"]
    run = [c for c in clauses if c.getparent() is clauses[-1].getparent()]
    run[-1].find("enum").text = "(ix)"
    found = findings.check_sequence_gaps(tree)
    assert any(f.type == "sequence_gap" for f in found)


def test_invalid_date_detected():
    tree = billxml.load_bill(FTC_BILL).tree
    body = billxml.legis_body(tree)
    text = next(body.iter("text"))
    text.text = (text.text or "") + " The deadline is February 29, 2027."
    found = findings.check_dates(tree)
    assert len(found) == 1
    assert "February 29, 2027" in found[0].detail


def _add_subsection_insertion(section_elem, cite: str, designator: str):
    """Append a subsection with an amendatory chapeau + quoted-block adding
    subsection (designator) to the given USC cite."""
    ns_sub = etree.SubElement(section_elem, "subsection")
    enum = etree.SubElement(ns_sub, "enum")
    enum.text = "(z)"
    text = etree.SubElement(ns_sub, "text")
    text.text = "Section 1 of the Test Act ("
    xref = etree.SubElement(text, "external-xref")
    xref.set("legal-doc", "usc")
    xref.set("parsable-cite", cite)
    xref.text = "1 U.S.C. 1"
    xref.tail = ") is amended by adding at the end the following:"
    qb = etree.SubElement(ns_sub, "quoted-block")
    sub = etree.SubElement(qb, "subsection")
    sub_enum = etree.SubElement(sub, "enum")
    sub_enum.text = f"({designator})"
    sub_text = etree.SubElement(sub, "text")
    sub_text.text = "Inserted content."


def test_designation_collision_detected():
    tree = billxml.load_bill(FTC_BILL).tree
    body = billxml.legis_body(tree)
    sections = [e for e in billxml.structural_children(body) if e.tag == "section"]
    clean = copy.deepcopy(tree)
    _add_subsection_insertion(sections[1], "usc/1/1", "g")
    _add_subsection_insertion(sections[1], "usc/1/1", "g")
    found = findings.check_designation_collisions(tree)
    assert len(found) == 1
    assert "subsection (g)" in found[0].detail
    assert found[0].criterion == "L1.6"
    # distinct designators to the same cite do not collide
    body2 = billxml.legis_body(clean)
    sections2 = [e for e in billxml.structural_children(body2) if e.tag == "section"]
    _add_subsection_insertion(sections2[1], "usc/1/1", "g")
    _add_subsection_insertion(sections2[1], "usc/1/1", "h")
    assert findings.check_designation_collisions(clean) == []


def test_cli_exit_codes(tmp_path):
    env_ok = subprocess.run(
        [sys.executable, "-m", "legislint.cli", "check", str(FTC_BILL)],
        capture_output=True, text=True,
    )
    assert env_ok.returncode == 0, env_ok.stdout + env_ok.stderr
    env_bad = subprocess.run(
        [sys.executable, "-m", "legislint.cli", "check", str(H2B_BILL), "--json"],
        capture_output=True, text=True,
    )
    assert env_bad.returncode == 1
    assert "unresolved_reference" in env_bad.stdout
