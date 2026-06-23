#!/usr/bin/env python3
"""Polish client survey (feature test).

Verifies the survey renders/serves in Polish: session language stored, data
payload carries Polish option labels + context label while keeping the original
English option VALUE, and a resume still works off the English value.

Run: ANTHROPIC_API_KEY=dummy python3 tests/test_survey_pl.py
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

# stub the rewrite so we don't call the live LLM; prove language is threaded in
stext.simplify_question = lambda q, language="pl": f"[{language}] {q}"

SIG = "subheading|3901|a,b,c"


class R:
    def __init__(self, **k):
        self.__dict__.update(k)


def fake(conn, text, oracle, hint="", origin=""):
    t = (text or "").strip()
    if "granulat" in t and SIG not in oracle.human_answers:
        raise es.NeedHumanAnswer(SIG, {
            "ask": "Which polymer predominates by weight?",
            "why": "the primary polymer decides the heading",
            "options": [{"id": "a", "text": "Polypropylene (PP)"},
                        {"id": "b", "text": "Polyethylene (PE)"},
                        {"id": "c", "text": "Mixed / cannot determine"}]})
    return R(status="classified", code="3902100000", confidence="high",
             hint_conflict=False, trail=[], measures=None)


def main():
    es.classify = fake
    frozen = sf.classify_line("granulat polimerowy", origin="PL", line_number=1)["frozen"]
    # context label maps from the engine freeze reason
    frozen["freeze_reason"] = "AMBIGUOUS_PRODUCT"

    from fastapi.testclient import TestClient
    import app as appmod
    client = TestClient(appmod.app)

    cr = client.post("/survey/create", json={
        "session_broker_id": "broker-local", "invoice_ref": "FV/2026/06/0841",
        "frozen_lines": [frozen], "language": "pl"}).json()
    tok = cr["survey_token"]

    # session stored language='pl'
    c = sqlite3.connect(ROOT / "saved_products.sqlite")
    lang = c.execute("SELECT language FROM survey_sessions WHERE id=?", (tok,)).fetchone()[0]
    c.close()
    assert lang == "pl", f"language not stored: {lang}"
    print("1) session.language =", lang)

    d = client.get("/survey/" + tok + "/data").json()
    q = d["questions"][0]
    print("2) language:", d.get("language"), "| intro:", d["intro"][:40])
    print("   question:", q["question"])
    print("   option values:", q["options"])
    print("   option labels:", q["option_labels"])
    print("   context_label:", q["context_label"])

    assert d["language"] == "pl"
    assert d["intro"] == "Twój agent celny potrzebuje kilku szczegółów dotyczących przesyłki."
    # simplify received language='pl'
    assert q["question"].startswith("[pl] ")
    # VALUES stay English (engine processes these on submit)
    assert q["options"] == ["Polypropylene (PP)", "Polyethylene (PE)", "Mixed / cannot determine"]
    # LABELS are Polish (from the static map)
    assert q["option_labels"] == ["Polipropylen (PP)", "Polietylen (PE)", "Mieszanina / nie wiem"]
    # context label is the Polish freeze-reason line
    assert q["context_label"] == "Opis towaru wymaga doprecyzowania"
    # no engine internals leak
    assert "engine_state" not in str(d) and "option_map" not in str(d)
    print("3) PL payload correct; English values preserved")

    # submit using the ENGLISH value -> engine resumes and resolves
    sb = client.post("/survey/" + tok + "/submit", json={"answers": [
        {"question_id": q["question_id"], "answer": "Polypropylene (PP)", "answer_detail": ""}]}).json()
    assert sb["ok"] and sb["resolved_any"], sb
    rs = client.get("/survey/" + tok + "/results").json()
    assert rs["lines"][0]["final_code"] == "3902100000"
    assert rs["lines"][0]["answer"] == "Polypropylene (PP)"
    print("4) submit with English value resolved ->", rs["lines"][0]["final_code"])

    # cleanup
    c = sqlite3.connect(ROOT / "saved_products.sqlite")
    for t in ("survey_results", "survey_questions"):
        c.execute(f"DELETE FROM {t} WHERE session_id=?", (tok,))
    c.execute("DELETE FROM survey_sessions WHERE id=?", (tok,))
    c.commit(); c.close()
    print("POLISH SURVEY: PASS (cleaned)")


if __name__ == "__main__":
    main()
