#!/usr/bin/env python3
"""
survey_freeze.py — Capturing the moments the GRI engine cannot proceed.

Milestone One (V2), Section 2. When a line item cannot be classified — missing
description, ambiguous goods, low OCR confidence, unresolvable node — we FREEZE
that line and record exactly what the engine needs: its own constrained question
and option set, plus a snapshot to resume from. We never invent a code; a frozen
honest gap is the correct behaviour.

The engine is not modified. Its existing `needs_question` / `needs_pre_classify`
returns ARE the freeze signal; this module turns them into FrozenClassification
records. `option_set` is ALWAYS non-empty: from the engine's DB-derived options
at an ambiguous node, or a small fixed set of field prompts when the description
is entirely absent.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import engine_session as es  # noqa: E402

# OCR confidence floor (Milestone Zero integration). A field below this is
# treated as MISSING and freezes the line.
OCR_CONF_FLOOR = 70

# Fixed field prompts used when the engine cannot produce options because the
# description is absent/too vague. The last opens a supplementary free-text
# field that re-enters as a constrained oracle call — never a direct code.
MISSING_DESC_OPTIONS = [
    "Please provide a product description",
    "I have a photo I can share",
    "I will describe it in more detail",
]
FREETEXT_OPTION = "I will describe it in more detail"
OCR_WRONG_OPTION = "The value above is wrong — I will type the correct one"


@dataclass
class FrozenClassification:
    line_number: int
    description_used: str | None
    hs_code_attempted: str | None
    freeze_reason: str                 # MISSING_DESCRIPTION | AMBIGUOUS_PRODUCT |
                                       # LOW_OCR_CONFIDENCE | INVALID_CODE | NEEDS_DETAIL
    engine_question: str
    option_set: list = field(default_factory=list)   # constrained options, NEVER EMPTY
    engine_state_snapshot: dict = field(default_factory=dict)
    partial_heading: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _heading_from_sig(sig: str) -> str | None:
    """The engine signature is 'stage|heading|ids'; the middle field is the
    partial heading the engine reached, if any."""
    try:
        parts = (sig or "").split("|")
        h = parts[1].strip() if len(parts) >= 2 else ""
        return h or None
    except Exception:
        return None


def classify_line(description: str | None, origin: str = "", hint: str = "",
                  line_number: int = 1, hs_code_attempted: str | None = None
                  ) -> dict:
    """Classify one line through the existing engine. Returns:
        {"resolved": True,  "code": "...", "result": <engine dict>}              or
        {"resolved": False, "frozen": <FrozenClassification dict>}

    `option_set` on a frozen line is guaranteed non-empty.
    """
    res = es.start(description or "", origin or "", hint or "")
    if res.get("status") == "classified":
        return {"resolved": True, "code": res.get("code"), "result": res}
    fc = build_from_engine_result(res, description, origin, hint, line_number,
                                  hs_code_attempted)
    return {"resolved": False, "frozen": fc.to_dict() if fc else None}


def build_from_engine_result(res: dict, description: str | None, origin: str,
                             hint: str, line_number: int,
                             hs_code_attempted: str | None
                             ) -> FrozenClassification | None:
    """Turn an engine result (from es.start) into a FrozenClassification, reusing
    the snapshot from the session the result came from. Returns None if the
    result is actually classified (not frozen). `option_set` is never empty."""
    status = res.get("status")
    if status == "classified":
        return None

    snap = es.export_snapshot(res.get("session_id")) or _fallback_snap(
        description, origin, hint)

    if status == "needs_question":
        q = res.get("question") or {}
        opts = q.get("options") or []
        option_texts = [o.get("text") or o.get("id") for o in opts]
        option_map = {(o.get("text") or o.get("id")): o.get("id") for o in opts}
        if not option_texts:
            option_texts = list(MISSING_DESC_OPTIONS)   # never empty
            option_map = {}
        return FrozenClassification(
            line_number=line_number, description_used=description,
            hs_code_attempted=hs_code_attempted, freeze_reason="NEEDS_DETAIL",
            engine_question=(q.get("ask") or q.get("why")
                             or "Which option matches your product?"),
            option_set=option_texts,
            engine_state_snapshot={**snap, "sig": res.get("sig"), "kind": "node",
                                   "option_map": option_map, "why": q.get("why", "")},
            partial_heading=_heading_from_sig(res.get("sig", "")))

    if status == "needs_pre_classify":
        has_desc = bool((description or "").strip())
        reason = "AMBIGUOUS_PRODUCT" if has_desc else "MISSING_DESCRIPTION"
        return FrozenClassification(
            line_number=line_number, description_used=description,
            hs_code_attempted=hs_code_attempted, freeze_reason=reason,
            engine_question=(res.get("question")
                             or "Could you tell us a bit more about this product?"),
            option_set=list(MISSING_DESC_OPTIONS),
            engine_state_snapshot={**snap, "sig": "__pre_classify__",
                                   "kind": "pre_classify",
                                   "missing_attribute": res.get("missing_attribute", "")},
            partial_heading=None)

    # error / needs_review / anything else: still frozen, with field prompts.
    return FrozenClassification(
        line_number=line_number, description_used=description,
        hs_code_attempted=hs_code_attempted, freeze_reason="NEEDS_DETAIL",
        engine_question=(res.get("message")
                         or "We need a little more detail to classify this line."),
        option_set=list(MISSING_DESC_OPTIONS),
        engine_state_snapshot={**snap, "sig": "__pre_classify__",
                               "kind": "pre_classify"},
        partial_heading=None)


def frozen_from_result(item: dict, origin: str, result: dict) -> dict | None:
    """Build a FrozenClassification dict from an ALREADY-COMPUTED engine result
    for an invoice line item (so analyze_item need not classify twice). The OCR
    confidence gate runs first. Returns None if the line resolved cleanly."""
    ocr_frozen = _ocr_gate(item)
    if ocr_frozen is not None:
        return ocr_frozen.to_dict()
    if (result or {}).get("status") == "classified":
        return None
    fc = build_from_engine_result(
        result or {}, item.get("description"), origin, "",
        item.get("row", 0), (item.get("code") or "").strip() or None)
    return fc.to_dict() if fc else None


def _fallback_snap(description, origin, hint) -> dict:
    return {"text": description or "", "origin": origin or "",
            "hint": hint or "", "human_answers": {}, "llm_cache": {}}


def _ocr_gate(item: dict) -> FrozenClassification | None:
    """Milestone Zero OCR gate. If a scanned field's confidence is below the
    floor, freeze the line with LOW_OCR_CONFIDENCE and a confirm/correct option
    set, BEFORE we trust it for classification."""
    low = item.get("low_confidence") or []
    code_uncertain = bool(item.get("code_uncertain"))
    code_conf = item.get("code_conf")
    line_number = item.get("row", 0)

    field_below = False
    candidate = ""
    if "code" in low or code_uncertain or (
            isinstance(code_conf, (int, float)) and code_conf < OCR_CONF_FLOOR):
        field_below = True
        candidate = (item.get("code") or "").strip()
    elif "value" in low:
        field_below = True
        candidate = str(item.get("value") or item.get("qty") or "").strip()

    if not field_below:
        return None

    shown = candidate or "(could not read a value)"
    options = []
    if candidate:
        options.append(candidate)
    options.append(OCR_WRONG_OPTION)
    return FrozenClassification(
        line_number=line_number,
        description_used=item.get("description"),
        hs_code_attempted=candidate or None,
        freeze_reason="LOW_OCR_CONFIDENCE",
        engine_question=("We couldn't read this field clearly from the scanned "
                         f"document. Please confirm: {shown}"),
        option_set=options,
        engine_state_snapshot={"sig": "__ocr_confirm__", "kind": "ocr_confirm",
                               "text": item.get("description") or "",
                               "origin": "", "hint": "",
                               "human_answers": {}, "llm_cache": {}},
        partial_heading=None,
    )


def frozen_from_invoice_item(item: dict, origin: str = "") -> dict | None:
    """Given an invoice line item (the {row, description, code, qty, ...} shape
    used across the invoice scanner), return a FrozenClassification dict if the
    line cannot be cleanly resolved, else None.

    Order: the OCR confidence gate runs first (a shaky read is treated as
    MISSING before we trust it); otherwise the line goes through the engine.
    """
    ocr_frozen = _ocr_gate(item)
    if ocr_frozen is not None:
        return ocr_frozen.to_dict()

    outcome = classify_line(
        item.get("description"), origin=origin, hint="",
        line_number=item.get("row", 0),
        hs_code_attempted=(item.get("code") or "").strip() or None,
    )
    if outcome.get("resolved"):
        return None
    return outcome.get("frozen")
