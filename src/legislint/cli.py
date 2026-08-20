"""legislint CLI: deterministic findings on congressional bill XML."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import billxml, findings

VENDORED_DTD = Path(__file__).resolve().parents[2] / "vendor" / "bill.dtd"


def _dtd_path(args) -> Path | None:
    if args.no_dtd:
        return None
    if args.dtd:
        return Path(args.dtd)
    return VENDORED_DTD if VENDORED_DTD.exists() else None


def cmd_check(args) -> int:
    tree = billxml.parse_bill(args.bill)
    found = findings.run_checks(tree, dtd_path=_dtd_path(args))
    if args.json:
        print(json.dumps([findings.finding_dict(f) for f in found], indent=1))
    else:
        if not found:
            print(f"{args.bill}: clean ({len(findings.CHECKS)} checks + DTD)")
        for f in found:
            crit = f" [{f.criterion}]" if f.criterion else ""
            print(f"{f.type}{crit}: {f.detail}  @ {f.location}")
    return 1 if found else 0


def cmd_sweep(args) -> int:
    paths = sorted(Path(args.dir).glob("BILLS-*.xml"))
    rows = []
    for p in paths:
        try:
            tree = billxml.parse_bill(p)
            found = findings.run_checks(tree, dtd_path=None)
        except Exception as exc:  # noqa: BLE001 — a sweep reports, never dies
            rows.append({"bill": p.stem, "error": str(exc)[:200]})
            continue
        if found:
            rows.append(
                {
                    "bill": p.stem,
                    "findings": [findings.finding_dict(f) for f in found],
                }
            )
    flagged = [r for r in rows if "findings" in r]
    summary = {
        "scanned": len(paths),
        "flagged_bills": len(flagged),
        "findings": sum(len(r["findings"]) for r in flagged),
        "note": "candidates for verification, not a defect rate",
    }
    out = {"summary": summary, "bills": rows}
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=1))
    print(json.dumps(summary, indent=1))
    return 0


def main() -> None:
    p = argparse.ArgumentParser(prog="legislint")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="lint one bill XML file")
    c.add_argument("bill")
    c.add_argument("--json", action="store_true")
    c.add_argument("--dtd", default=None, help="path to bill.dtd (default: vendored)")
    c.add_argument("--no-dtd", action="store_true", help="skip DTD validation")
    c.set_defaults(func=cmd_check)

    s = sub.add_parser("sweep", help="lint every BILLS-*.xml in a directory")
    s.add_argument("dir")
    s.add_argument("--out", default=None, help="write full results JSON here")
    s.set_defaults(func=cmd_sweep)

    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
