#!/usr/bin/env python3
"""End-to-end test for the client-clarification survey (Milestone One V2).

Stubs the engine's classify() so freeze + resume are deterministic without a
live API key, then drives create -> data -> submit -> results through the API.
Run: ANTHROPIC_API_KEY=dummy python3 tests/test_survey_flow.py
"""
import os
import sqlite3
import sys
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "dummy-test-key")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT))

import engine_session as es
import survey_freeze as sf
import survey_text as stext

stext.simplify_question = lambda q, language="pl": q          # no live LLM in test

SIG = "sub|6907|a,b,c"


class R:
    def __init__(self, **k):
        self.__dict__.update(k)


def fake(conn, text, oracle, hint="", origin=""):
    t = (text or "").strip()
    if not t:
        oracle.pre_classify_question = {"question": "What is this product made of?",
                                        "missing_attribute": "identity"}
        return R(status="needs", confidence="", hint_conflict=False, trail=[], measures=None)
    if "ceramic tiles" in t and SIG not in oracle.human_answers:
        raise es.NeedHumanAnswer(SIG, {
            "ask": "Water absorption rate?", "why": "absorption decides subheading",
            "options": [{"id": "a", "text": "Up to 0.5%"},
                        {"id": "b", "text": "0.5 to 10%"},
                        {"id": "c", "text": "Over 10%"}]})
    code = "6907210000" if "ceramic tiles" in t else "6912008500"
    return R(status="classified", code=code, confidence="high",
             hint_conflict=False, trail=[], measures=None)


def main():
    es.classify = fake
    node = sf.classify_line("glazed ceramic tiles", origin="CN", line_number=2)["frozen"]
    pre = sf.classify_line("", origin="", line_number=5)["frozen"]

    from fastapi.testclient import TestClient
    import app as appmod
    client = TestClient(appmod.app)

    cr = client.post("/survey/create", json={
        "session_broker_id": "broker-X", "invoice_ref": "inv_081.xlsx",
        "frozen_lines": [node, pre]}).json()
    tok = cr["survey_token"]
    print("1) token ok, url", cr["survey_url"])

    d = client.get("/survey/" + tok + "/data").json()
    print("2) Qs", len(d["questions"]), "q1 opts", d["questions"][0]["options"],
          "q2 freetext", d["questions"][1]["freetext_options"])
    blob = str(d)
    assert "engine_state" not in blob and "final_code" not in blob \
        and "option_map" not in blob, "client payload leak"
    assert d["questions"][1]["freetext_options"], "pre-classify should allow free text"

    pg = client.get("/survey/" + tok)
    assert pg.status_code == 200 and "wizard" in pg.text
    print("3) form renders")

    ans = [{"question_id": d["questions"][0]["question_id"],
            "answer": "0.5 to 10%", "answer_detail": ""},
           {"question_id": d["questions"][1]["question_id"],
            "answer": "I will describe it in more detail",
            "answer_detail": "glazed porcelain floor tile"}]
    sb = client.post("/survey/" + tok + "/submit", json={"answers": ans}).json()
    print("4) submit", sb)
    assert sb["ok"] and sb["resolved_any"]

    rs = client.get("/survey/" + tok + "/results").json()
    for l in rs["lines"]:
        print("   line", l["line_number"], "final", l["final_code"],
              "frozen", l["still_frozen"])
    assert sorted(l["final_code"] for l in rs["lines"]) == ["6907210000", "6912008500"]
    assert all(not l["still_frozen"] for l in rs["lines"])

    pg2 = client.get("/survey/" + tok)
    assert "przesłane" in pg2.text.lower()        # already-submitted page (Polish)
    print("6) re-open -> already submitted")
    assert client.get("/survey/" + "0" * 32).status_code == 404

    # cleanup test rows
    c = sqlite3.connect(ROOT / "saved_products.sqlite")
    for t in ("survey_results", "survey_questions"):
        c.execute(f"DELETE FROM {t} WHERE session_id=?", (tok,))
    c.execute("DELETE FROM survey_sessions WHERE id=?", (tok,))
    c.commit()
    c.close()
    print("PHASE 4+5+6: PASS (cleaned)")


if __name__ == "__main__":
    main()
