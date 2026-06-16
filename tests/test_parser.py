#!/usr/bin/env python3
"""
test_parser.py — Parser correctness tests.

These encode the LEGAL properties the database must satisfy, not just
"the code runs". Run:  python3 tests/test_parser.py
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingest"))
from parse_taric import ingest  # noqa: E402

SAMPLE = Path(__file__).resolve().parent / "sample_taric.xml"

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def main():
    tmp = Path(tempfile.mkdtemp()) / "test.sqlite"
    stats = ingest(SAMPLE, tmp)
    conn = sqlite3.connect(tmp)

    print("\n[1] Ingest completeness — no silent data loss")
    check("zero unknown record types", not stats["unknown_tags"],
          str(stats["unknown_tags"]))
    check("zero rejected records", not stats["rejected"],
          str(stats["rejected"][:3]))
    check("provenance row written",
          conn.execute("SELECT COUNT(*) FROM ingest_log").fetchone()[0] == 1)

    print("\n[2] Nomenclature tree")
    check("3 nomenclature lines loaded",
          conn.execute("SELECT COUNT(*) FROM goods_nomenclature").fetchone()[0] == 3)
    row = conn.execute(
        "SELECT item_id, producline_suffix FROM goods_nomenclature WHERE sid=101"
    ).fetchone()
    check("t-shirt code + suffix correct", row == ("6109100000", "80"), str(row))
    lvl = conn.execute(
        "SELECT indent_level FROM goods_nomenclature_indent WHERE sid=101").fetchone()
    check("indent level preserved", lvl == (1,), str(lvl))

    print("\n[3] Duty lookup: cotton t-shirt (6109100000) from China, today")
    # China inherits erga-omnes (1011) via membership — the geo join.
    duty = conn.execute("""
        SELECT mc.duty_amount FROM measure m
        JOIN measure_component mc ON mc.measure_sid = m.sid
        WHERE m.goods_nomenclature_item_id='6109100000'
          AND m.measure_type_id='103'
          AND m.geographical_area_id IN (
              'CN',
              (SELECT ga2.area_id FROM geographical_area ga1
               JOIN geographical_area_membership gm ON gm.member_sid=ga1.sid
               JOIN geographical_area ga2 ON ga2.sid=gm.group_sid
               WHERE ga1.area_id='CN'))
          AND (m.validity_end IS NULL OR m.validity_end >= date('now'))
    """).fetchall()
    check("exactly ONE valid duty found (expired one excluded)",
          duty == [(12.0,)], str(duty))

    print("\n[4] Anti-dumping: steel code 7308909800 from China")
    add = conn.execute("""
        SELECT m.additional_code, mc.duty_amount, m.regulation_id
        FROM measure m JOIN measure_component mc ON mc.measure_sid=m.sid
        WHERE m.goods_nomenclature_item_id='7308909800'
          AND m.measure_type_id='552' AND m.geographical_area_id='CN'
          AND (m.validity_end IS NULL OR m.validity_end >= date('now'))
    """).fetchone()
    check("ADD 60.1% residual C999 found with legal basis",
          add == ("C999", 60.1, "R2010154"), str(add))

    print("\n[5] Certificates / conditions")
    cert = conn.execute("""
        SELECT c.certificate_type, c.certificate_code, cd.description
        FROM measure_condition c
        LEFT JOIN certificate_description cd
          ON cd.certificate_type=c.certificate_type
         AND cd.certificate_code=c.certificate_code
        WHERE c.measure_sid=9003
    """).fetchone()
    check("ADD measure carries document condition D017 with description",
          cert is not None and cert[0] == "D" and cert[1] == "017"
          and "invoice" in (cert[2] or "").lower(), str(cert))

    print("\n[6] Date discipline")
    expired = conn.execute(
        "SELECT validity_end FROM measure WHERE sid=9004").fetchone()
    check("expired measure stored WITH its end date (history kept)",
          expired == ("2021-12-31",), str(expired))

    print("\n[7] Footnotes")
    fn = conn.execute("""
        SELECT f.description FROM footnote_association_goods a
        JOIN footnote f ON f.footnote_id=a.footnote_id
        WHERE a.goods_nomenclature_sid=200""").fetchone()
    check("footnote linked to steel code",
          fn is not None and "aircraft" in fn[0], str(fn))

    print(f"\n{'='*50}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
