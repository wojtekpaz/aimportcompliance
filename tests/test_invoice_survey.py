#!/usr/bin/env python3
"""Phase 7 integration: invoice upload -> auto-created survey + dashboard.

Stubs the engine so one line freezes and one classifies, uploads an Excel file
through the real route, and checks the survey is created and sanitised.
Run: ANTHROPIC_API_KEY=dummy python3 tests/test_invoice_survey.py
"""
import io
import os
import sqlite3
import sys
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "dummy-test-key")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT))

import engine_session as es
import survey_text as stext
stext.simplify_question = lambda q, language="pl": q

SIG = "sub|6907|a,b,c"


class R:
    def __init__(self, **k):
        self.__dict__.update(k)


def fake(conn, text, oracle, hint="", origin=""):
    t = (text or "").strip()
    if "ceramic" in t and SIG not in oracle.human_answers:
        raise es.NeedHumanAnswer(SIG, {
            "ask": "Water absorption rate?", "why": "absorption decides subheading",
            "options": [{"id": "a", "text": "Up to 0.5%"},
                        {"id": "b", "text": "0.5 to 10%"},
                        {"id": "c", "text": "Over 10%"}]})
    return R(status="classified", code="7308909890", confidence="high",
             hint_conflict=False, trail=[], measures=None)


def main():
    es.classify = fake
    import invoice_session
    invoice_session._code_is_valid = lambda c: True   # avoid tariff DB dependency

    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Description", "HS Code"])
    ws.append(["Steel brackets, galvanised", "7308909890"])
    ws.append(["Glazed ceramic tiles", ""])          # will freeze
    buf = io.BytesIO(); wb.save(buf)

    from fastapi.testclient import TestClient
    import app as appmod
    client = TestClient(appmod.app)

    r = client.post("/api/invoice/analyze",
                    files={"file": ("inv_081.xlsx", buf.getvalue(),
                                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    j = r.json()
    print("1) needs_clarification:", j["summary"].get("needs_clarification"),
          "| survey_url:", bool(j.get("survey_url")))
    assert j["summary"]["needs_clarification"] == 1
    assert j.get("survey_url") and j.get("survey_token")
    assert j.get("clarifications") and len(j["clarifications"]) == 1
    # sanitisation: no engine internals reach the broker JSON
    blob = str(j)
    assert "engine_state" not in blob and "option_map" not in blob \
        and "llm_cache" not in blob, "engine internals leaked to client"
    assert "frozen_lines" not in j and all("frozen" not in it for it in j["items"])
    print("2) sanitised OK; clarification:", j["clarifications"][0]["freeze_reason"])

    token = j["survey_token"]

    # dashboard lists it as 'not_sent'
    pend = client.get("/survey/pending/list?broker_id=broker-local").json()
    mine = [s for s in pend["sessions"] if s["id"] == token]
    assert mine and mine[0]["display_status"] == "not_sent" and mine[0]["lines_pending"] == 1
    print("3) dashboard: not_sent, lines_pending=1")

    # mark-sent -> awaiting
    ms = client.post("/survey/" + token + "/mark-sent",
                     json={"client_email": "client@acme.com"}).json()
    assert ms.get("ok")
    pend2 = client.get("/survey/pending/list?broker_id=broker-local").json()
    mine2 = [s for s in pend2["sessions"] if s["id"] == token][0]
    assert mine2["display_status"] == "awaiting"
    print("4) mark-sent -> awaiting")

    # the client survey data for this token is also sanitised
    d = client.get("/survey/" + token + "/data").json()
    assert "engine_state" not in str(d) and d["questions"][0]["options"]
    print("5) client data sanitised; options:", d["questions"][0]["options"])

    # cleanup
    c = sqlite3.connect(ROOT / "saved_products.sqlite")
    for t in ("survey_results", "survey_questions"):
        c.execute(f"DELETE FROM {t} WHERE session_id=?", (token,))
    c.execute("DELETE FROM survey_sessions WHERE id=?", (token,))
    c.commit(); c.close()
    print("PHASE 7: PASS (cleaned)")


if __name__ == "__main__":
    main()
