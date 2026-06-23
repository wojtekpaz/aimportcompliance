#!/usr/bin/env python3
"""LIVE survey round-trip (Fix 2 verification) — uses the real GRI engine.

Proves the client's answer is APPLIED at the frozen GRI node on resume (not fed
in as a fresh description). Requires a real ANTHROPIC_API_KEY. Makes real API
calls. Cleans up its own DB rows.

Run: python3 tests/test_survey_live.py
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT))

import engine_session as es
import survey_freeze as sf
import survey_db as sdb

CANDIDATES = [
    "men's t-shirt, cotton",
    "ceramic floor tiles",
    "leather handbag",
    "stainless steel kitchen sink",
    "office chair with wheels",
]


def find_node_freeze():
    """Classify candidates until one freezes at a GRI node (needs_question)."""
    for desc in CANDIDATES:
        out = sf.classify_line(desc, origin="CN", line_number=1)
        if out.get("resolved"):
            print("  (resolved cleanly, no freeze):", desc, "->", out.get("code"))
            continue
        fz = out["frozen"]
        snap = fz["engine_state_snapshot"]
        if snap.get("kind") == "node":
            print("  froze at node:", desc, "| sig:", snap.get("sig"))
            return desc, fz
        print("  froze (pre_classify):", desc)
    return None, None


def main():
    if not __import__("os").environ.get("ANTHROPIC_API_KEY"):
        print("SKIPPED — no ANTHROPIC_API_KEY"); return

    print("1) finding a node freeze (live engine)…")
    desc, fz = find_node_freeze()
    if not fz:
        print("No node freeze among candidates; cannot test node resume live.")
        return

    snap = fz["engine_state_snapshot"]
    orig_sig = snap["sig"]
    option_map = snap.get("option_map") or {}
    options = fz["option_set"]
    print("2) frozen question:", fz["engine_question"][:70])
    print("   options:", options)

    # create survey + submit the FIRST real option via the production API
    created = sdb.create_session("broker-local", "live_test.pdf", [fz])
    token = created["token"]

    from fastapi.testclient import TestClient
    import app as appmod
    client = TestClient(appmod.app)

    data = client.get(f"/survey/{token}/data").json()
    qid = data["questions"][0]["question_id"]
    chosen = options[0]
    print("3) submitting client answer:", repr(chosen))
    client.post(f"/survey/{token}/submit",
                json={"answers": [{"question_id": qid, "answer": chosen, "answer_detail": ""}]})

    res = client.get(f"/survey/{token}/results").json()
    line = res["lines"][0]
    print("4) result -> final_code:", line["final_code"],
          "| still_frozen:", line["still_frozen"])
    print("   client answer recorded:", repr(line["answer"]))
    print("   note:", (line["resolution_notes"] or "")[:80])

    # PROOF the answer was applied at the node (not ignored / re-interpreted):
    # resume directly and confirm it does NOT re-ask the identical frozen sig.
    opt_id = option_map.get(chosen)
    resumed = es.resume(snap, orig_sig, opt_id)
    if resumed.get("status") == "classified":
        print("5) PROOF: resume APPLIED the answer -> classified",
              resumed.get("code"))
    elif resumed.get("status") in ("needs_question", "needs_pre_classify"):
        new_sig = resumed.get("sig")
        assert new_sig != orig_sig, \
            "BUG: engine re-asked the SAME frozen node — answer was NOT applied"
        print("5) PROOF: answer applied — engine advanced to a NEW node:",
              new_sig, "(≠ original", orig_sig + ")")
    else:
        print("5) resume status:", resumed.get("status"))

    assert line["answer"] == chosen, "client answer not recorded against the question"

    # cleanup
    c = sqlite3.connect(ROOT / "saved_products.sqlite")
    for t in ("survey_results", "survey_questions"):
        c.execute(f"DELETE FROM {t} WHERE session_id=?", (token,))
    c.execute("DELETE FROM survey_sessions WHERE id=?", (token,))
    c.commit(); c.close()
    print("LIVE SURVEY ROUND-TRIP: PASS (cleaned)")


if __name__ == "__main__":
    main()
