#!/usr/bin/env python3
"""
report.py — Human verification report after ingest.

Produces a report a NON-PROGRAMMER can check: counts, the nomenclature
tree integrity check, and N random declarable codes with their
descriptions, duties for a chosen origin, conditions and footnotes —
to be compared line-by-line against the official EU TARIC consultation
website. If our database and the EU website agree on every sampled code,
the ingest is trustworthy.

USAGE:
    python3 ingest/report.py <database.sqlite> [n_samples] [origin]
"""
import sqlite3
import sys
import random
from pathlib import Path


def tree_integrity(conn) -> list[str]:
    """Property check: every code's parent prefix must exist in the tree.
    Catches hierarchy/off-by-one ingest errors."""
    problems = []
    codes = {r[0] for r in conn.execute(
        "SELECT DISTINCT item_id FROM goods_nomenclature")}
    chapters = {c[:2] for c in codes}
    for code in codes:
        # a 10-digit code's 4-digit heading must exist (padded with zeros)
        heading = code[:4] + "000000"
        if code != heading and code[:4] + "000000" not in codes \
                and any(c.startswith(code[:4]) and c.endswith("000000") for c in [heading]):
            if heading not in codes:
                problems.append(f"code {code}: heading {code[:4]} not in tree")
    return problems[:20], len(chapters), len(codes)


def duty_text(conn, measure_sid) -> str:
    parts = []
    for de, amt, mu, unit in conn.execute(
            "SELECT duty_expression_id, duty_amount, monetary_unit, measurement_unit "
            "FROM measure_component WHERE measure_sid=?", (measure_sid,)):
        if amt is None:
            continue
        if mu:
            parts.append(f"{amt} {mu}/{unit or ''}".strip("/"))
        else:
            parts.append(f"{amt}%")
    return " + ".join(parts) if parts else "(no duty component)"


def main():
    db = Path(sys.argv[1])
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    origin = sys.argv[3] if len(sys.argv) > 3 else "CN"
    conn = sqlite3.connect(db)

    print("=" * 70)
    print("AIMPORT INGEST VERIFICATION REPORT")
    print("=" * 70)

    for sf, sha, loaded, at in conn.execute(
            "SELECT source_file, substr(file_sha256,1,12), records_loaded, loaded_at FROM ingest_log"):
        print(f"Source: {sf}  sha256:{sha}…  records:{loaded}  loaded:{at}")

    print("\n--- Table counts ---")
    for t in ["goods_nomenclature", "goods_nomenclature_description",
              "goods_nomenclature_indent", "measure", "measure_component",
              "measure_condition", "geographical_area",
              "geographical_area_membership", "footnote"]:
        c = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:<38} {c:>9}")

    problems, n_chapters, n_codes = tree_integrity(conn)
    print(f"\n--- Tree integrity ---\n  chapters: {n_chapters}, codes: {n_codes}")
    if problems:
        print("  !! PROBLEMS (first 20):")
        for p in problems:
            print(f"     {p}")
    else:
        print("  OK — every code's heading exists in the tree.")

    # Which geo groups does the origin belong to?
    group_ids = [origin] + [r[0] for r in conn.execute("""
        SELECT ga2.area_id FROM geographical_area ga1
        JOIN geographical_area_membership m ON m.member_sid = ga1.sid
        JOIN geographical_area ga2 ON ga2.sid = m.group_sid
        WHERE ga1.area_id = ?""", (origin,))]
    print(f"\n--- Origin {origin}: member of geo groups {group_ids} ---")

    declarable = [r for r in conn.execute(
        "SELECT sid, item_id FROM goods_nomenclature WHERE producline_suffix='80'")]
    random.shuffle(declarable)
    print(f"\n--- {min(n, len(declarable))} random declarable codes "
          f"(VERIFY AGAINST official TARIC site, origin={origin}) ---")
    qmarks = ",".join("?" * len(group_ids))
    for sid, item_id in declarable[:n]:
        desc = conn.execute(
            "SELECT description FROM goods_nomenclature_description "
            "WHERE sid=? ORDER BY validity_start DESC", (sid,)).fetchone()
        print(f"\n  CODE {item_id[:4]} {item_id[4:6]} {item_id[6:8]} {item_id[8:]}")
        print(f"    desc: {desc[0] if desc else '!! MISSING DESCRIPTION'}")
        rows = conn.execute(f"""
            SELECT m.sid, m.measure_type_id, mt.description, m.geographical_area_id,
                   m.regulation_id, m.additional_code
            FROM measure m LEFT JOIN measure_type mt USING (measure_type_id)
            WHERE m.goods_nomenclature_item_id = ?
              AND m.geographical_area_id IN ({qmarks})
              AND (m.validity_end IS NULL OR m.validity_end >= date('now'))
            """, [item_id] + group_ids).fetchall()
        if not rows:
            print("    (no measures for this origin in DB — check site: "
                  "may be inherited from parent code)")
        for msid, mtype, mdesc, geo, reg, addcode in rows:
            duty = duty_text(conn, msid)
            extra = f" addcode={addcode}" if addcode else ""
            print(f"    measure {mtype} ({mdesc or '?'}) geo={geo}{extra}: "
                  f"{duty}   [reg {reg}]")
            for cc, ctype, ccode, action in conn.execute(
                    "SELECT condition_code, certificate_type, certificate_code, "
                    "action_code FROM measure_condition WHERE measure_sid=?", (msid,)):
                cdesc = conn.execute(
                    "SELECT description FROM certificate_description WHERE "
                    "certificate_type=? AND certificate_code=?",
                    (ctype, ccode)).fetchone()
                print(f"      condition {cc}: doc {ctype}{ccode or ''} "
                      f"({cdesc[0] if cdesc else 'no description'}) action={action}")
    print("\n" + "=" * 70)
    print("HOW TO VERIFY: open the EU TARIC consultation site, enter each code")
    print(f"above with origin country = {origin}, and compare description, duty")
    print("rate, anti-dumping measures and document codes. Any mismatch = stop.")


if __name__ == "__main__":
    main()
