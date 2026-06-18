#!/usr/bin/env python3
"""
optimize_session.py — Alternative Defensible Classifications for AImport Compliance.

WHAT IT DOES:
  1. Takes a product description, its current HS/TARIC code, and origin country.
  2. Asks Claude to identify alternative DEFENSIBLE product framings — legally
     valid ways the same goods could be described such that a different heading
     or subheading applies under the GRI rules of interpretation.
  3. Runs each framing through the EXISTING deterministic classify() engine to
     obtain an engine-confirmed, declarable code.
  4. Discards any alternative that the engine cannot classify or that resolves
     to the original code.
  5. Returns the original + validated alternatives with full GRI paths and duty.

ARCHITECTURAL CONSTRAINT:
  - Does NOT modify classifier.py, oracles.py, prompts.py, engine_session.py,
    or any existing route.
  - Reuses ClaudeOracle for API credentials and model selection.
  - Reuses classify() as a black box; never modifies its inputs mid-run.
  - The AI proposes framings; the engine decides legality. No code appears in
    the output that the deterministic GRI engine did not reach independently.
"""
import json
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

from classifier import classify                                         # noqa: E402
from oracles import ClaudeOracle                                       # noqa: E402
from lookup import lookup as _lookup, _clean_pipes, _duty_display      # noqa: E402

DB_PATH = ROOT / "data_taric.sqlite"
MAX_FRAMINGS = 4   # cap on alternatives proposed and classified

# ---------------------------------------------------------------------------
# AI prompt — alternative defensible framings
# ---------------------------------------------------------------------------

_FRAMING_SYSTEM = """\
You are a senior customs classification counsel with deep knowledge of the
Harmonised System and the EU Combined Nomenclature. Your task is to identify
alternative DEFENSIBLE legal framings of a product for customs classification
purposes.

A defensible framing is one that:
  - Emphasises a real, verifiable attribute of the goods (material composition,
    principal function, state of presentation, essential character, or principal
    use) that the current description underweights.
  - Would cause a trained customs officer or tribunal to begin GRI analysis at a
    different heading or subheading.
  - Could be argued and upheld in a post-clearance audit or appeal.

This is NOT about finding lower duty rates. It is about identifying genuine
legal ambiguity that arises from real product attributes. Do not invent
attributes the goods do not have. Do not propose framings you would not be
prepared to defend before a customs authority.

Return ONLY a JSON array — no prose, no markdown fences. Maximum 4 elements.
Each element must be:
{
  "framing": "revised product description that foregrounds the attribute driving this alternative GRI path",
  "rationale": "the GRI rule, legal principle, or CN note that makes this interpretation defensible",
  "key_attribute": "the specific, verifiable product attribute that distinguishes this classification path"
}

Return [] if no genuinely defensible alternatives exist. An empty array is
the correct answer when the current classification is unambiguous.
"""

_FRAMING_USER = """\
Product description: {description}
Current HS/TARIC code: {current_code}
Origin country: {origin}

Identify alternative defensible framings of this product for EU customs
classification purposes. Focus on genuine legal ambiguity grounded in verifiable
product attributes, not on rate optimisation.
"""

# ---------------------------------------------------------------------------
# internal helpers
# ---------------------------------------------------------------------------

def _normalise_code(raw: str) -> str:
    """Strip non-digits and pad/truncate to 10 digits."""
    digits = re.sub(r"\D", "", raw)
    return digits.ljust(10, "0")[:10]


def _space_code(code: str) -> str:
    return f"{code[:4]} {code[4:6]} {code[6:8]} {code[8:]}"


def _cn_description(conn: sqlite3.Connection, code: str) -> str:
    row = conn.execute(
        "SELECT d.description FROM goods_nomenclature g "
        "JOIN goods_nomenclature_description d ON d.sid=g.sid "
        "WHERE g.item_id=? ORDER BY d.validity_start DESC",
        (code,)).fetchone()
    return _clean_pipes(row[0]) if row else ""


def _extract_duty_info(measures: dict | None) -> dict | None:
    """Pull primary duty + legal basis from a measures dict."""
    if not measures:
        return None
    duties = measures.get("duty", [])
    primary = next((d for d in duties if d.get("type") == "103"),
                   duties[0] if duties else None)
    if not primary:
        return None
    return {
        "rate": _duty_display(primary),
        "name": primary.get("type_name", ""),
        "regulation": primary.get("regulation", ""),
    }


def _extract_defense(measures: dict | None) -> list:
    if not measures:
        return []
    return [
        {
            "name": d.get("type_name", ""),
            "rate": _duty_display(d),
            "regulation": d.get("regulation", ""),
            "meaning": d.get("additional_code_meaning"),
        }
        for d in measures.get("defense", [])
    ]


def _call_claude(oracle: ClaudeOracle, system: str, user: str,
                 max_tokens: int = 1400) -> str:
    """One raw Claude API call using the oracle's key + model. Returns '' on error."""
    body = {
        "model": oracle.model,
        "max_tokens": max_tokens,
        "temperature": 0,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(),
        headers={
            "content-type": "application/json",
            "x-api-key": oracle.api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        return "".join(b.get("text", "") for b in data.get("content", []))
    except Exception:
        return ""


def _propose_framings(oracle: ClaudeOracle, description: str,
                      current_code: str, origin: str) -> list:
    """Ask Claude for alternative defensible framings; returns list of dicts."""
    user = _FRAMING_USER.format(
        description=description,
        current_code=current_code,
        origin=origin or "not specified",
    )
    raw = _call_claude(oracle, _FRAMING_SYSTEM, user)
    if not raw:
        return []
    try:
        cleaned = (raw.strip()
                   .removeprefix("```json").removeprefix("```")
                   .removesuffix("```").strip())
        result = json.loads(cleaned)
        if not isinstance(result, list):
            return []
        return [f for f in result
                if isinstance(f, dict) and (f.get("framing") or "").strip()]
    except (json.JSONDecodeError, ValueError):
        return []


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def analyze(description: str, current_code: str, origin: str) -> dict:
    """Main entry point for the /api/optimize endpoint.

    Returns:
        {
          "original": {...},
          "alternatives": [...],
          "framings_proposed": int,
        }
        or {"error": str} on failure.
    """
    code_norm = _normalise_code(current_code)
    if len(re.sub(r"\D", "", current_code)) < 6:
        return {"error": "Please enter a valid HS/TARIC code (minimum 6 digits)."}

    # --- validate original code and retrieve its duty -----------------------
    try:
        conn = sqlite3.connect(DB_PATH)
        exists = conn.execute(
            "SELECT 1 FROM goods_nomenclature WHERE item_id=? LIMIT 1",
            (code_norm,)).fetchone()
        if not exists:
            conn.close()
            return {"error": f"Code {current_code} was not found in the nomenclature database."}

        orig_cn_desc = _cn_description(conn, code_norm)
        orig_measures = _lookup(conn, code_norm, origin) if origin else None
        orig_duty = _extract_duty_info(orig_measures)
        orig_defense = _extract_defense(orig_measures)
        conn.close()
    except Exception as e:
        return {"error": f"Database error: {str(e)[:160]}"}

    # --- create oracle (raises if no API key) --------------------------------
    try:
        oracle = ClaudeOracle()
    except RuntimeError as e:
        return {"error": str(e)}

    # --- propose alternative framings ----------------------------------------
    framings = _propose_framings(oracle, description, current_code, origin)

    # --- classify each framing, discard non-classifiable / same code ---------
    alternatives = []
    seen_codes = {code_norm}

    for framing_obj in framings[:MAX_FRAMINGS]:
        framing_text = (framing_obj.get("framing") or "").strip()
        if not framing_text:
            continue
        try:
            framing_oracle = ClaudeOracle()
            conn = sqlite3.connect(DB_PATH)
            try:
                res = classify(conn, framing_text, framing_oracle,
                               origin=origin or "")
                if res.status != "classified" or not res.code:
                    continue
                if res.code in seen_codes:
                    continue
                seen_codes.add(res.code)

                alt_code = res.code
                alt_measures = res.measures   # set by classify() when origin passed
                if origin and alt_measures is None:
                    alt_measures = _lookup(conn, alt_code, origin)

                alt_duty = _extract_duty_info(alt_measures)
                alt_defense = _extract_defense(alt_measures)
                alt_cn_desc = _cn_description(conn, alt_code)

                trail = [
                    {
                        "gri": st.gri,
                        "action": st.action,
                        "chosen": st.chosen,
                        "note": st.note,
                    }
                    for st in res.trail
                ]

                alternatives.append({
                    "code": alt_code,
                    "code_spaced": _space_code(alt_code),
                    "cn_description": alt_cn_desc,
                    "framing": framing_text,
                    "rationale": framing_obj.get("rationale", ""),
                    "key_attribute": framing_obj.get("key_attribute", ""),
                    "duty": alt_duty,
                    "defense": alt_defense,
                    "trail": trail,
                    "legal_basis": alt_duty.get("regulation", "") if alt_duty else "",
                })
            finally:
                conn.close()
        except Exception:
            continue   # discard any framing the engine cannot handle

    return {
        "original": {
            "code": code_norm,
            "code_spaced": _space_code(code_norm),
            "input_description": description,
            "cn_description": orig_cn_desc,
            "duty": orig_duty,
            "defense": orig_defense,
        },
        "alternatives": alternatives,
        "framings_proposed": len(framings),
    }
