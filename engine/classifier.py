#!/usr/bin/env python3
"""
classifier.py — Deterministic GRI classification state machine.

ARCHITECTURE (the legal contract of this module):
  - CONTROL FLOW IS CODE. The sequence Chapter -> Heading (GRI-1) ->
    Subheading descent (GRI-6, level by level) is enforced here, never
    delegated to a model.
  - THE ORACLE ONLY CHOOSES FROM SUPPLIED OPTIONS. Any oracle (LLM, human,
    test script) receives options with IDs from the database and must
    return one of those IDs, or UNSURE. A returned ID not in the option
    set raises — hallucinated codes are structurally impossible.
  - UNSURE -> CLARIFICATION QUESTION, never a guess. One question per
    round, hard cap on rounds, then 'needs_review' state.
  - HINTS ARE PRIORS, NOT CONSTRAINTS. A user hint ('it's chapter 61')
    boosts hint-consistent candidates but search evidence outside the
    hint is kept and a conflict is flagged if evidence disagrees.
  - EVERY STEP IS LOGGED to an audit trail (the institutional product).

DATA GAP (documented): CN Section/Chapter legal notes are not yet in the
DB; GRI-1 currently reasons over heading texts only. Notes ingestion is
scheduled (Step 2b) and the audit trail records this limitation.
"""
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tree import (first_level_children, next_level_children,           # noqa
                  descriptions_for, path_text, is_declarable)
from search import candidate_headings, search_codes                    # noqa
from lookup import lookup                                              # noqa
from prompts import (GRI1_HEADING, GRI6_DESCENT,       # noqa
                     detect_industrial_mode, build_system)     # all prompts live in prompts.py

UNSURE = "__UNSURE__"
MAX_QUESTIONS = 6


@dataclass
class Step:
    gri: str
    action: str
    options: list
    chosen: str | None
    note: str = ""


@dataclass
class Result:
    status: str                    # 'classified' | 'needs_question' | 'needs_review'
    code: str | None = None
    question: dict | None = None
    confidence: str = ""
    hint_conflict: bool = False
    trail: list = field(default_factory=list)
    measures: dict | None = None


class Oracle:
    """Interface. choose() must return one of the option ids or UNSURE."""
    def choose(self, prompt: str, options: list[dict], context: dict) -> str:
        raise NotImplementedError

    def propose_headings(self, product_text: str) -> dict:
        """Optional AI INTERPRETATION layer (candidate generator). Non-AI oracles
        propose nothing, so classification falls back to keyword search alone and
        behaviour is unchanged. AI oracles override this to bridge commercial
        terms -> formal headings. Every proposal is DB-validated in classify()."""
        return {"headings": [], "normalized": ""}


class ScriptedOracle(Oracle):
    def __init__(self, answers):
        self.answers = list(answers)

    def choose(self, prompt, options, context):
        return self.answers.pop(0) if self.answers else UNSURE


def _validate_choice(choice: str, options: list[dict]) -> str:
    ids = {o["id"] for o in options}
    if choice == UNSURE or choice in ids:
        return choice
    # Tolerate the model dropping a trailing ":<suffix>" tag (option ids carry a
    # ":80"-style sid on descent levels; the model sometimes returns the bare
    # code). Re-attach the canonical id IFF exactly one option has that base —
    # still anti-hallucination guarded: only a real option code can win.
    base_matches = [o["id"] for o in options
                    if o["id"].split(":", 1)[0] == choice]
    if len(base_matches) == 1:
        return base_matches[0]
    raise ValueError(f"oracle returned id {choice!r} not in option set "
                     f"{sorted(ids)[:8]} — refusing (anti-hallucination guard)")


def _heading_option_text(conn, cand) -> str:
    """Label a GRI-1 heading option by its OWN text — what distinguishes it from
    sibling headings — not the shared chapter prefix. Without this, e.g. all
    footwear headings read 'FOOTWEAR, GAITERS...' and become indistinguishable
    to both the human answering and the AI choosing."""
    h = cand["heading"]
    own = descriptions_for(conn, h + "000000")
    if own and own[0] != "(no description)":
        return f"{h}: {own[0]}"[:300]
    ex = cand.get("examples") or []
    if ex:                                   # fallback: most specific path segment
        return f"{h}: {ex[0]['path'].split(' > ')[-1]}"[:300]
    return h


def classify(conn, product_text: str, oracle: Oracle,
             hint: str = "", origin: str = "",
             max_questions: int = MAX_QUESTIONS) -> Result:
    res = Result(status="needs_review")
    questions_asked = 0

    # ---- Stage 1: candidate headings (search = generator only) --------
    cands = candidate_headings(conn, product_text)
    hint = (hint or "").strip()
    hint_headings = []
    if hint:
        # hint as PRIOR: pull headings under the hint prefix via search...
        hint_hits = search_codes(conn, product_text,
                                 within_prefix=hint, limit=20)
        hint_headings = sorted({h["item_id"][:4] for h in hint_hits})
        # ...and ALSO seed the hint's own heading(s) directly from the tree,
        # so a hint always works even when search text is sparse/absent.
        if len(hint) >= 4:
            if conn.execute("SELECT 1 FROM goods_nomenclature WHERE "
                            "substr(item_id,1,4)=? LIMIT 1", (hint[:4],)).fetchone():
                if hint[:4] not in hint_headings:
                    hint_headings.append(hint[:4])
        elif len(hint) == 2:
            for (hh,) in conn.execute(
                    "SELECT DISTINCT substr(item_id,1,4) FROM goods_nomenclature "
                    "WHERE substr(item_id,1,2)=? LIMIT 30", (hint,)):
                if hh not in hint_headings:
                    hint_headings.append(hh)
        evidence_headings = {c["heading"] for c in cands}
        if hint_headings and not any(h.startswith(hint[:2])
                                     for h in evidence_headings) and cands:
            res.hint_conflict = True
        for hh in hint_headings:
            if hh not in evidence_headings:
                ex = search_codes(conn, product_text, within_prefix=hh, limit=3)
                cands.append({"heading": hh, "best_score": 0,
                              "examples": ex or
                              [{"item_id": hh + "000000",
                                "path": path_text(conn, hh + "000000")}]})
    # ---- AI interpretation layer (candidate generator) -----------------
    # The oracle may PROPOSE headings from world knowledge, bridging commercial
    # terms ('smartwatch', 'hoodie') to formal tariff headings that keyword
    # search misses. EVERY proposal is validated against the DB here, so an
    # invented heading cannot enter — the anti-hallucination guarantee holds.
    # It also surfaces the genuine competing headings, which is what lets GRI-1
    # ask a real either/or question instead of dead-ending.
    try:
        sugg = oracle.propose_headings(product_text)
    except Exception:
        sugg = {"headings": [], "normalized": ""}
    evidence_headings = {c["heading"] for c in cands}
    ai_added = []
    for h in (sugg.get("headings") or []):
        h = "".join(ch for ch in str(h) if ch.isdigit())[:4]
        if len(h) == 4 and h not in evidence_headings and conn.execute(
                "SELECT 1 FROM goods_nomenclature WHERE substr(item_id,1,4)=? "
                "LIMIT 1", (h,)).fetchone():
            ex = search_codes(conn, product_text, within_prefix=h, limit=3)
            cands.append({"heading": h, "best_score": 0,
                          "examples": ex or [{"item_id": h + "000000",
                                              "path": path_text(conn, h + "000000")}]})
            evidence_headings.add(h)
            ai_added.append(h)
    if sugg.get("headings") or sugg.get("normalized"):
        res.trail.append(Step("pre-GRI", "ai_interpretation", ai_added, None,
                              f"normalized={(sugg.get('normalized') or '')[:80]!r}; "
                              f"added={ai_added}"))

    industrial = detect_industrial_mode(
        product_text, [c["heading"] for c in cands])
    res.trail.append(Step("pre-GRI", "candidate_generation",
                          [c["heading"] for c in cands], None,
                          f"hint={hint or '-'} conflict={res.hint_conflict} "
                          f"industrial={industrial}"))
    if not cands:
        res.trail.append(Step("pre-GRI", "no_candidates", [], None,
                              "search found nothing — needs review"))
        return res

    # ---- Stage 2: GRI-1 heading selection ------------------------------
    options = [{"id": c["heading"], "text": _heading_option_text(conn, c)}
               for c in cands]
    # Retrieve binding Section/Chapter Notes for the candidate chapters and
    # feed them to the oracle as legal context (GRI-1 requires this).
    from notes import (notes_for_chapters, format_notes_for_prompt,
                       chapters_from_headings)
    cand_chapters = chapters_from_headings([o["id"] for o in options])
    legal_notes = notes_for_chapters(conn, cand_chapters)
    notes_blob = format_notes_for_prompt(legal_notes)
    res.trail.append(Step("GRI-1", "notes_retrieved",
                          [f"ch{c}" for c in cand_chapters], None,
                          f"{len(legal_notes['chapter'])} chapter + "
                          f"{len(legal_notes['section'])} section note blocks"))
    notes_preamble = (f"\n\nBINDING LEGAL NOTES (apply these — a heading is "
                      f"excluded if its Section/Chapter Note says so):\n"
                      f"{notes_blob}\n" if notes_blob else "")
    while True:
        choice = _validate_choice(
            oracle.choose(
                GRI1_HEADING.format(product=product_text, notes=notes_preamble),
                options,
                 {"stage": "heading", "product": product_text,
                 "notes": legal_notes, "industrial": industrial}), options)
        if choice != UNSURE:
            heading = choice
            res.trail.append(Step("GRI-1", "heading_selected",
                                  [o["id"] for o in options], heading))
            break
        if len(options) == 1:           # only one candidate heading — nothing to ask
            heading = options[0]["id"]
            res.trail.append(Step("GRI-1", "heading_selected",
                                  [o["id"] for o in options], heading,
                                  "single candidate — auto-selected (low evidence)"))
            break
        if questions_asked >= max_questions:
            res.trail.append(Step("GRI-1", "max_questions_reached",
                                  [o["id"] for o in options], None))
            return res
        questions_asked += 1
        res.status = "needs_question"
        res.question = {"stage": "heading",
                        "options": options,
                        "ask": "Which heading fits? Provide missing "
                               "attribute (material/function/state)."}
        res.trail.append(Step("GRI-1", "clarification_needed",
                              [o["id"] for o in options], None))
        return res   # caller collects an answer, then re-invokes with it

    # ---- Stage 3: GRI-6 descent, by DASH (indent) level -----------------
    heading4 = heading
    kids = first_level_children(conn, heading4)
    cur_item, cur_suffix = None, None
    # Heading is itself the declarable code (no real subdivisions): the only
    # "child" is the heading row itself. Terminate immediately.
    if len(kids) == 1 and kids[0]["item_id"] == heading4 + "000000" \
            and is_declarable(conn, heading4 + "000000"):
        cur_item = heading4 + "000000"
        kids = []
    while kids:
        options = [{"id": k["item_id"] + ":" + k["suffix"],
                    "item_id": k["item_id"], "suffix": k["suffix"],
                    "text": k["display"][:300], "residual": k["is_other"]}
                   for k in kids]
        choice = _validate_choice(
            oracle.choose(
                GRI6_DESCENT.format(
                    heading=heading4,
                    heading_desc="; ".join(
                        descriptions_for(conn, heading4 + "000000"))[:120],
                    product=product_text),
                options, {"stage": "subheading", "heading": heading4,
                           "industrial": industrial}), options)
        if choice == UNSURE:
            if len(options) == 1:       # only one subdivision at this level — take it
                choice = options[0]["id"]
                res.trail.append(Step("GRI-6", "level_selected",
                                      [o["id"] for o in options], choice,
                                      "single subdivision — auto-selected"))
                cur_item, cur_suffix = choice.split(":")
                kids = next_level_children(conn, heading4, cur_item, cur_suffix)
                continue
            if questions_asked >= max_questions:
                res.trail.append(Step("GRI-6", "max_questions_reached",
                                      [o["id"] for o in options], None))
                return res
            res.status = "needs_question"
            res.question = {"stage": "subheading", "options": options,
                            "ask": "Which subdivision fits at this level? "
                                   "(one attribute decides it)"}
            res.trail.append(Step("GRI-6", "clarification_needed",
                                  [o["id"] for o in options], None))
            return res
        res.trail.append(Step("GRI-6", "level_selected",
                              [o["id"] for o in options], choice))
        cur_item, cur_suffix = choice.split(":")
        kids = next_level_children(conn, heading4, cur_item, cur_suffix)

    current = cur_item if cur_item else heading4 + "000000"

    # ---- Terminal --------------------------------------------------------
    if not is_declarable(conn, current):
        res.status = "needs_review"
        res.trail.append(Step("terminal", "non_declarable_endpoint", [], current,
                              "descent ended on a non-declarable line — review"))
        return res
    res.status = "classified"
    res.code = current
    res.confidence = "high" if not res.hint_conflict else "review-hint-conflict"
    res.trail.append(Step("terminal", "declarable_code_reached", [], current,
                          path_text(conn, current)))
    if origin:
        res.measures = lookup(conn, current, origin)
    return res


def trail_json(res: Result) -> str:
    return json.dumps({
        "status": res.status, "code": res.code,
        "confidence": res.confidence, "hint_conflict": res.hint_conflict,
        "trail": [{"gri": s.gri, "action": s.action, "chosen": s.chosen,
                   "options": s.options, "note": s.note} for s in res.trail],
    }, indent=2)
