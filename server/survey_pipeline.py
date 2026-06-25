#!/usr/bin/env python3
"""survey_pipeline.py — turn frozen invoice lines into client survey questions.

For each frozen line: resolve DB candidate codes (survey_candidates), then ask
the question generator (survey_question_generator) for ONE anchored, localized
question. Branch on the generator's verdict:

  * "ok"                 -> keep the line; attach the generated question + closed
                            code-option set for rendering and resume.
  * candidates_mismatch  -> raise a review flag (survey_review) and EXCLUDE the
    / extraction_suspect    line from the client survey. It is NOT downgraded to
                            the legacy simplify_question path — a flag means the
                            candidates were wrong or the extraction was suspect,
                            so the reworded engine question would be equally
                            wrong. The line is held for human review instead.

Locale is the value already resolved upstream (survey_locale, Phase 1); it is
never re-decided here. This module calls the model only via the generator.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import engine_session as es          # noqa: E402  (for the TARIC DB path)
import survey_review as srev         # noqa: E402
from survey_candidates import candidates_for_frozen_line  # noqa: E402
from survey_question_generator import generate_survey_question  # noqa: E402

# Submitted value standing in for the generator's {code: null} fallback option.
NONE_SENTINEL = "__NONE_OF_THESE__"


def build_survey_lines(frozen_lines: list[dict], *, locale: str,
                       broker_id: str, invoice_ref: str) -> tuple[list[dict], int]:
    """Returns (survey_lines, flagged_count). `survey_lines` is the subset of
    frozen lines that became client questions, each with a "generated" payload
    attached to its engine_state_snapshot. Flagged lines are excluded."""
    conn = sqlite3.connect(es.DB_PATH)
    survey_lines: list[dict] = []
    flagged = 0
    try:
        for fl in frozen_lines:
            try:
                cands = candidates_for_frozen_line(fl, conn=conn)
            except Exception:
                cands = []
            res = generate_survey_question({
                "survey_locale": locale,
                "line": _line_payload(fl),
                "ambiguity_reason": fl.get("freeze_reason", ""),
                "candidate_codes": cands,
            })
            status = res.get("status")
            if status == "ok":
                survey_lines.append(_attach_generated(fl, res))
            elif status in ("candidates_mismatch", "extraction_suspect"):
                srev.raise_flag(
                    broker_id=broker_id, invoice_ref=invoice_ref,
                    line_number=fl.get("line_number"),
                    description=(fl.get("description_used") or ""),
                    flag_type=status, observation=res.get("observation", ""),
                    field=res.get("field", ""))
                flagged += 1
            else:
                # Unexpected (e.g. a locale error that should be impossible after
                # Phase 1): keep the line on the legacy path rather than dropping
                # it, so the survey still goes out.
                survey_lines.append(fl)
    finally:
        conn.close()
    return survey_lines, flagged


def _line_payload(fl: dict) -> dict:
    """Map a frozen line to the generator's `line` contract. Prefer a RAW
    verbatim description if the scanner preserved one; the generator quotes it
    back to the customer, so a cleaned string would weaken the anchor."""
    desc = (fl.get("description_verbatim")
            or fl.get("description_raw")
            or fl.get("description_used") or "")
    return {
        "line_number": fl.get("line_number"),
        "description_verbatim": desc,
        "hs_code_stated": fl.get("hs_code_attempted"),
        "origin": fl.get("origin"),
        "quantity": fl.get("quantity"),
        "unit_price": fl.get("unit_price"),
        "distinguishing_tokens": fl.get("distinguishing_tokens") or [],
        "extraction_confidence": fl.get("extraction_confidence", 1.0),
    }


def _attach_generated(fl: dict, res: dict) -> dict:
    """Persist the generator's question + closed option set onto the frozen line.
    option_set carries the submittable VALUES (candidate codes + the none
    sentinel) so the existing submit validation accepts them; the localized
    labels and the question live under engine_state_snapshot['generated']."""
    options = res.get("options") or []
    values, labels = [], []
    for o in options:
        code = o.get("code")
        if code is None:
            values.append(NONE_SENTINEL)
        else:
            values.append(str(code))
        labels.append(o.get("label") or "")

    snap = dict(fl.get("engine_state_snapshot") or {})
    snap["generated"] = {
        "question": res.get("question", ""),
        "anchor_summary": res.get("anchor_summary", ""),
        "values": values,
        "labels": labels,
        "none_sentinel": NONE_SENTINEL,
    }
    out = dict(fl)
    out["engine_state_snapshot"] = snap
    out["engine_question"] = res.get("question", "") or fl.get("engine_question", "")
    out["option_set"] = values
    return out
