#!/usr/bin/env python3
"""
test_engine.py — GRI engine tests against the REAL ingested database.
Run: python3 tests/test_engine.py /path/to/taric.sqlite
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))
from classifier import classify, ScriptedOracle, UNSURE, _validate_choice  # noqa
from search import candidate_headings                                       # noqa

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {'' if cond else detail}")


def main(db):
    conn = sqlite3.connect(db)

    print("\n[1] Anti-hallucination guard")
    try:
        _validate_choice("9999", [{"id": "6109"}])
        check("invented option id rejected", False, "no exception raised")
    except ValueError:
        check("invented option id rejected", True)

    print("\n[2] Full classification: cotton t-shirt -> 6109 10 00 10")
    oracle = ScriptedOracle(["6109", "6109100000:80", "6109100010:80"])
    res = classify(conn, "men's t-shirt of cotton, knitted", oracle, origin="CN")
    check("status classified", res.status == "classified", res.status)
    check("code 6109100010", res.code == "6109100010", str(res.code))
    check("duty attached from origin CN",
          res.measures and any(d["type"] == "103" and "12.000" in (d["duty_raw"] or "")
                               for d in res.measures["duty"]),
          str(res.measures["duty"] if res.measures else None))
    check("audit trail records GRI-1 then GRI-6 order",
          [s.gri for s in res.trail][:2] == ["pre-GRI", "GRI-1"] and
          [s.gri for s in res.trail][-1] == "terminal" and
          "GRI-6" in [s.gri for s in res.trail],
          str([s.gri for s in res.trail]))

    print("\n[3] UNSURE -> question, never a guess")
    res = classify(conn, "scooter", ScriptedOracle([UNSURE]))
    check("needs_question status", res.status == "needs_question", res.status)
    check("question offers real headings incl. 9503 or 8711",
          res.question and any(o["id"] in ("9503", "8711")
                               for o in res.question["options"]),
          str(res.question and [o["id"] for o in res.question["options"]]))
    check("no code emitted", res.code is None, str(res.code))

    print("\n[4] Hint is prior, not constraint")
    # wrong hint: woven chapter 62 for a KNITTED shirt — evidence is 61
    cands = {c["heading"] for c in candidate_headings(conn, "knitted cotton t-shirt")}
    res = classify(conn, "knitted cotton t-shirt", ScriptedOracle([UNSURE]),
                   hint="62")
    in_q = {o["id"] for o in (res.question["options"] if res.question else [])}
    check("evidence headings (61xx) kept despite hint 62",
          any(h.startswith("61") for h in in_q), str(in_q))
    check("hint headings (62xx) also offered (prior honored)",
          any(h.startswith("62") for h in in_q), str(in_q))

    print("\n[5] Correct hint speeds things up without distortion")
    res = classify(conn, "ceramic wall tiles, glazed",
                   ScriptedOracle(["6907", UNSURE]), hint="6907", origin="CN")
    # after heading, first GRI-6 level should ask between real 6907 children
    check("descends within 6907",
          res.question and all(o["id"].startswith("6907")
                               for o in res.question["options"]),
          str(res.question and [o["id"] for o in res.question["options"]]))


    print("\n[6] Dash-level descent through nested 'Other' (7308 90 98 11)")
    from tree import first_level_children, next_level_children
    lvl1 = first_level_children(conn, "7308")
    check("entry level offers 5 one-dash subheadings incl. residual Other",
          len(lvl1) == 5 and any(k["is_other"] for k in lvl1), str(len(lvl1)))
    lvl2 = next_level_children(conn, "7308", "7308900000", "80")
    check("7308 90 splits into 'sheet' vs 'Other' (intermediate dash line kept)",
          any("sheet" in k["desc"].lower() for k in lvl2)
          and any(k["is_other"] for k in lvl2), str([k["desc"][:20] for k in lvl2]))
    other_label = next((k["display"] for k in lvl2 if k["is_other"]), "")
    check("residual 'Other' is rendered with excluded-sibling meaning",
          "not:" in other_label and "sheet" in other_label.lower(), other_label)
    res = classify(conn, "steel wind tower sections",
                   ScriptedOracle(["7308","7308900000:80","7308909800:80","7308909811:80"]))
    check("full descent reaches 7308909811 through two Other levels",
          res.code == "7308909811", str(res.code))


    print("\n[7] Supplementary sheets (conditions, exclusions, add-codes, legal)")
    cur = conn.cursor()
    n_cond = cur.execute("SELECT COUNT(*) FROM measure_condition_v2").fetchone()[0]
    check("structured conditions ingested (>40k)", n_cond > 40000, str(n_cond))
    n_excl = cur.execute("SELECT COUNT(*) FROM measure_exclusion").fetchone()[0]
    check("exclusions ingested (>30k)", n_excl > 30000, str(n_excl))
    n_add = cur.execute("SELECT COUNT(*) FROM additional_code_description").fetchone()[0]
    check("additional-code meanings ingested (>3k)", n_add > 3000, str(n_add))
    # additional code resolves to a company name in defense measures
    from lookup import lookup as _lk
    r = _lk(conn, "6907210000", "CN")
    has_name = any(it.get("additional_code_meaning") for it in r["defense"])
    check("anti-dumping add-codes resolve to company names", has_name)
    # structured condition join produces actionable cert+action
    direct = cur.execute("""SELECT goods_code, origin_code, measure_type
        FROM measure_condition_v2 WHERE certificate IS NOT NULL LIMIT 1""").fetchone()
    rr = _lk(conn, direct[0], direct[1])
    structured = any(it["conditions"] for b in ("duty","defense","other") for it in rr[b])
    check("structured conditions join into lookup output", structured)
    # legal basis enrichment present
    lb = cur.execute("SELECT COUNT(*) FROM legal_basis").fetchone()[0]
    check("legal basis (OJ provenance) ingested", lb > 4000, str(lb))


    print("\n[8] Engine reachability (random declarable codes)")
    from eval_harness import evaluate
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        full_ok, total = evaluate(conn, 150)
    check("engine reaches 100% of sampled codes via legal descent",
          full_ok == total, f"{full_ok}/{total}")


    print("\n[9] Legal notes (GRI-1 binding context)")
    nn = cur.execute("SELECT COUNT(*) FROM legal_note").fetchone()[0]
    check("legal notes extracted (>90 blocks)", nn > 90, str(nn))
    ch61 = cur.execute("SELECT note_text FROM legal_note WHERE kind='chapter' AND ident='61'").fetchone()
    check("chapter 61 excludes worn clothing (heading 6309)",
          ch61 and "6309" in ch61[0], "missing 6309 exclusion")
    ch94 = cur.execute("SELECT note_text FROM legal_note WHERE kind='chapter' AND ident='94'").fetchone()
    check("chapter 94 furniture notes present (wood/furniture resolver)",
          ch94 and "does not cover" in ch94[0].lower())
    from notes import notes_for_chapters, section_of
    check("section mapping correct (ch61 -> section XI)", section_of(61) == "XI")
    nt = notes_for_chapters(conn, [61])
    check("notes retrieval returns chapter + section for ch61",
          61 in nt["chapter"] and "XI" in nt["section"])
    res = classify(conn, "knitted cotton t-shirt",
                   ScriptedOracle(["6109","6109100000:80","6109100010:80"]))
    note_step = [s for s in res.trail if s.action == "notes_retrieved"]
    check("GRI-1 trail records notes retrieval", len(note_step) == 1)

    print(f"\n{'=' * 50}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/home/claude/data/taric.sqlite")
