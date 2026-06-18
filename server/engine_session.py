#!/usr/bin/env python3
"""
engine_session.py — Web-facing wrapper around the proven GRI engine.

PRE-CLASSIFICATION: ambiguity detection is now folded into the existing
INTERPRET call (propose_headings) rather than a separate API call.
If the AI returns a "question" field instead of headings, we surface it
to the user before GRI begins. Zero extra API calls, zero extra latency.
"""
import json
import sqlite3
import sys
import threading
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

from classifier import classify, trail_json, UNSURE        # noqa: E402
from oracles import ClaudeOracle                            # noqa: E402
from lookup import format_result, _clean_pipes, _duty_display   # noqa: E402

try:
    from bti_lookup import bti_for_code as _bti_for_code   # noqa: E402
except ImportError:
    def _bti_for_code(*_a, **_kw): return []               # bti.sqlite not yet ingested

DB_PATH = ROOT / "data_taric.sqlite"
MAX_ROUNDS = 8


class NeedHumanAnswer(Exception):
    def __init__(self, sig: str, question: dict):
        super().__init__(sig)
        self.sig = sig
        self.question = question


def _signature(options, context) -> str:
    stage = context.get("stage", "?")
    heading = context.get("heading", "")
    ids = ",".join(sorted(o["id"] for o in options))
    return f"{stage}|{heading}|{ids}"


def _humanize_ask(context, why="") -> str:
    stage = context.get("stage")
    if stage == "heading":
        base = ("Which product category fits best? These are the legal headings "
                "the AI is weighing.")
    else:
        base = ("One detail decides this. Which option matches your product at "
                "this level?")
    if why:
        base += f"  (The AI needs to know: {why})"
    return base


class HybridOracle:
    def __init__(self, claude: ClaudeOracle, human_answers: dict,
                 llm_cache: dict):
        self.claude = claude
        self.human_answers = human_answers
        self.llm_cache = llm_cache
        self.calls = claude.calls
        # pre-classify question surfaced by propose_headings, if any
        self.pre_classify_question = None

    def propose_headings(self, product_text):
        result = self.claude.propose_headings(product_text)
        # If the INTERPRET prompt returned a clarifying question instead of
        # headings, capture it so the session layer can surface it.
        if result.get("question") and not result.get("headings"):
            self.pre_classify_question = {
                "question": result["question"],
                "missing_attribute": result.get("missing_attribute", ""),
            }
            return {"headings": [], "normalized": ""}
        return result

    def choose(self, prompt, options, context):
        if len(options) == 1:
            return options[0]["id"]
        sig = _signature(options, context)
        if sig in self.human_answers:
            return self.human_answers[sig]
        if sig in self.llm_cache:
            return self.llm_cache[sig]
        choice = self.claude.choose(prompt, options, context)
        if choice == UNSURE:
            why = (getattr(self.claude, "last_reason", "") or "").strip()
            raise NeedHumanAnswer(sig, {
                "ask": _humanize_ask(context, why),
                "why": why,
                "options": [{"id": o["id"],
                             "text": _clean_pipes(o.get("text", "")) or o["id"]}
                            for o in options],
            })
        self.llm_cache[sig] = choice
        return choice


# ---- session store ---------------------------------------------------------

_sessions: dict[str, dict] = {}
_lock = threading.Lock()


def _new_session(text, origin, hint) -> str:
    sid = uuid.uuid4().hex
    with _lock:
        _sessions[sid] = {
            "text": text, "origin": origin or "", "hint": hint or "",
            "human_answers": {}, "llm_cache": {},
            "claude": ClaudeOracle(),
        }
    return sid


def _friendly_api_error(msg: str) -> str:
    low = msg.lower()
    if "credit balance" in low or "too low" in low or "billing" in low:
        return ("The Anthropic API has no available credit. Add credits in the "
                "Anthropic Console (Plans & Billing), then classify again.")
    if "401" in msg or "rejected the key" in low:
        return ("The Anthropic API rejected the key. Check ANTHROPIC_API_KEY is "
                "set for the server process and is still active.")
    if "model" in low and ("not found" in low or "404" in msg):
        return ("The configured model was not found — it may have been rotated. "
                "Update ClaudeOracle.DEFAULT_MODEL in engine/oracles.py.")
    if "unreachable" in low or "urlopen" in low or "connection" in low:
        return ("The Anthropic API could not be reached. Check the server's "
                "internet connection and try again.")
    return "Classification failed: " + msg.strip()[:200]


def _run(sid: str) -> dict:
    with _lock:
        s = _sessions.get(sid)
    if s is None:
        return {"status": "error", "message": "session expired — start again"}

    oracle = HybridOracle(s["claude"], s["human_answers"], s["llm_cache"])
    conn = sqlite3.connect(DB_PATH)
    try:
        try:
            res = classify(conn, s["text"], oracle,
                           hint=s["hint"], origin=s["origin"])
        except NeedHumanAnswer as q:
            return {"status": "needs_question", "session_id": sid,
                    "sig": q.sig, "question": q.question,
                    "rounds": len(s["human_answers"])}
        except RuntimeError as e:
            return {"status": "error", "message": _friendly_api_error(str(e))}

        # Check if propose_headings surfaced a pre-classify question.
        # This happens when the INTERPRET prompt decides the description
        # is too vague — it returns a product question instead of headings.
        if oracle.pre_classify_question:
            return {
                "status": "needs_pre_classify",
                "session_id": sid,
                "sig": "__pre_classify__",
                "question": oracle.pre_classify_question["question"],
                "missing_attribute": oracle.pre_classify_question.get(
                    "missing_attribute", ""),
            }

        return _serialize(conn, res, s["origin"], sid)
    finally:
        conn.close()


def _serialize(conn, res, origin, sid) -> dict:
    out = {"status": res.status, "session_id": sid,
           "confidence": res.confidence, "hint_conflict": res.hint_conflict,
           "trail": [{"gri": st.gri, "action": st.action, "chosen": st.chosen,
                      "note": st.note} for st in res.trail]}
    if res.status == "classified":
        c = res.code
        out["code"] = c
        out["code_spaced"] = f"{c[:4]} {c[4:6]} {c[6:8]} {c[8:]}"
        desc = conn.execute(
            "SELECT d.description FROM goods_nomenclature g "
            "JOIN goods_nomenclature_description d ON d.sid=g.sid "
            "WHERE g.item_id=? ORDER BY d.validity_start DESC",
            (c.ljust(10, "0"),)).fetchone()
        out["description"] = _clean_pipes(desc[0]) if desc else ""
        out["duty_text"] = (format_result(conn, c, origin, res.measures)
                            if res.measures is not None else
                            "(enter an origin country to see duties)")
        out["origin"] = origin or ""
        out["duty"] = None
        out["defense"] = []
        if res.measures is not None:
            m = res.measures
            duties = m.get("duty", [])
            primary = next((d for d in duties if d.get("type") == "103"),
                           duties[0] if duties else None)
            if primary:
                out["duty"] = {"rate": _duty_display(primary),
                               "name": primary.get("type_name", ""),
                               "regulation": primary.get("regulation", "")}
            out["defense"] = [{"name": d.get("type_name", ""),
                               "rate": _duty_display(d),
                               "regulation": d.get("regulation", ""),
                               "meaning": d.get("additional_code_meaning")}
                              for d in m.get("defense", [])]
        try:
            out["bti_refs"] = _bti_for_code(c)
        except Exception:
            out["bti_refs"] = []
    else:
        out["message"] = ("The engine reached a point it will not guess past. "
                           "This is by design — it never invents a code.")
    return out


# ---- public API used by app.py --------------------------------------------

def start(text, origin, hint) -> dict:
    sid = _new_session(text, origin, hint)
    return _run(sid)


def answer(sid, sig, choice) -> dict:
    with _lock:
        s = _sessions.get(sid)
        if s is None:
            return {"status": "error", "message": "session expired — start again"}

        # PRE-CLASSIFY ANSWER: append to product text, re-run
        if sig == "__pre_classify__":
            if choice and choice.strip():
                s["text"] = s["text"] + " — " + choice.strip()
            return _run(sid)

        if len(s["human_answers"]) >= MAX_ROUNDS:
            return {"status": "needs_review", "session_id": sid, "trail": [],
                    "message": "Too many open questions — needs a human expert."}
        s["human_answers"][sig] = choice

    return _run(sid)
