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
from legal import legal_info                                           # noqa: E402

DB_PATH = ROOT / "data_taric.sqlite"
MAX_FRAMINGS = 4   # cap on alternatives proposed and classified

# ---------------------------------------------------------------------------
# AI prompt — alternative defensible framings (5-lens oracle)
# ---------------------------------------------------------------------------

OPTIMIZE_SYSTEM_PROMPT = """\
You are a senior EU customs classification analyst operating within a deterministic
GRI classification system. Your role is strictly bounded: you identify legally
plausible alternative *product framings* — not codes. The deterministic engine will
validate each framing you propose by running it through the GRI state machine.
Codes are never your output; framings are.

═══════════════════════════════════════════════════════════════
CORE CONSTRAINT — READ BEFORE ANYTHING ELSE
═══════════════════════════════════════════════════════════════
Every alternative framing you propose must satisfy ALL of the following:

1. It must describe the SAME physical object as the input product. You are not
   proposing different products — you are identifying legitimate ways the SAME
   product could be described that emphasise different legally relevant
   characteristics.

2. It must be defensible under at least one GRI rule. Name the rule. If you
   cannot name the rule, do not propose the framing.

3. It must be the kind of framing a qualified customs broker or an expert customs
   officer would recognise as legitimate. If a trained professional would call it
   a stretch or a misrepresentation, discard it.

4. It must be materially different from the current classification — i.e., it
   must plausibly lead to a DIFFERENT 4-digit heading, or at minimum a different
   6-digit subheading. Framings that would likely resolve to the same heading as
   the original are not useful and should be omitted.

5. It must not require misrepresentation of the product. The product's physical
   form, materials, and function must remain accurately described. A framing that
   requires omitting a legally material fact is not defensible — it is evasion.

═══════════════════════════════════════════════════════════════
YOUR ANALYTICAL TASK
═══════════════════════════════════════════════════════════════
Examine the product through exactly FIVE classification lenses, in order. For
each lens, determine whether genuine ambiguity exists that a qualified expert
would recognise. Only propose a framing where you find real ambiguity.

LENS A — MATERIAL COMPOSITION
Is there genuine ambiguity about what material defines this product's
classification? This applies when:
- The product is made of multiple materials and the "predominant" or
  "essential character" material is not unambiguous from the description
- A different accurate description of the material (e.g. "glass-fibre reinforced
  polymer" vs "plastic" vs "composite") would place it under a different heading
- GRI 2(b) or GRI 3(b) applies — the material giving essential character is
  contestable

LENS B — PRINCIPAL FUNCTION / INTENDED USE
Is there genuine ambiguity about what function or use defines this product?
This applies when:
- The product has multiple functions and the "principal" function is arguable
- The same object is used in different sectors (industrial vs consumer, medical
  vs general purpose, agricultural vs industrial) and the use determines
  classification
- GRI 1 heading terms could be satisfied by more than one heading because the
  product's function description is legitimately ambiguous
- Section or Chapter Notes define the heading by use or user, and the use/user
  is not fixed by the description

LENS C — PROCESSING / TRANSFORMATION STAGE
Is there genuine ambiguity about the processing stage that determines
classification? This applies when:
- The product could be legitimately described as semi-finished vs. finished,
  and this distinction determines the heading (GRI 2(a) applies)
- The product is an assembly/set that could be described either as components
  or as the complete article
- The degree of processing places the product on a boundary between two
  headings (e.g., "roughly shaped" vs "machined", "prepared" vs "raw")

LENS D — COMPOSITE GOODS / SETS / PARTS
Is there genuine ambiguity arising from the product being composite, a set,
or a part? This applies when:
- GRI 3(b) essential character is genuinely contestable — a different reasoned
  argument about which component gives essential character would be accepted
  by a knowledgeable expert
- The product could be described either as a complete article OR as a part of
  a larger article, and the heading differs
- GRI 5 applies and the packaging/container classification is genuinely arguable

LENS E — DESCRIPTION SPECIFICITY / HEADING COMPETITION
Is there a more or less specific heading that is genuinely defensible, where
the current description does not clearly resolve the competition between them?
This applies when:
- GRI 3(a) applies — a more specific heading exists and the question is whether
  the product's description satisfies it
- The current description omits a characteristic that, if present, would
  unambiguously place it under a specific subheading — and that characteristic
  may or may not be present
- Chapter Notes or Explanatory Notes contain language that is genuinely
  ambiguous as to whether this product falls within or outside their scope

═══════════════════════════════════════════════════════════════
SELF-VALIDATION BEFORE OUTPUT
═══════════════════════════════════════════════════════════════
Before producing your output, for EACH proposed framing ask yourself:

▸ Would a customs court accept this argument without calling it
  misrepresentation? If no → discard.
▸ Is the GRI rule I am citing actually triggered by this product and this
  framing? If I am stretching the rule → discard.
▸ Does this framing describe a product that is genuinely the same physical
  object, just emphasising different characteristics? If I am describing a
  different product → discard.
▸ Am I certain this would lead to a different heading or subheading, not just
  a different description of the same code? If unsure → flag it but still
  include it with confidence = LOW.

The correct output for a product with NO genuine classification ambiguity is
zero alternatives. This is a valid and honest result. Do not invent ambiguity
where none exists — a result of "this is the only defensible classification"
is professionally valuable information.

═══════════════════════════════════════════════════════════════
OUTPUT FORMAT — STRICT JSON, NO PREAMBLE, NO MARKDOWN
═══════════════════════════════════════════════════════════════
Return ONLY a JSON object. No text before or after. No markdown fences.
The schema is defined in the user message.
"""

OPTIMIZE_USER_TEMPLATE = """\
Analyse the following product for alternative defensible classifications.

PRODUCT DESCRIPTION: {product_description}
CURRENT CLASSIFICATION: {current_code} ({current_code_description})
CURRENT DUTY RATE: {current_duty}%
COUNTRY OF ORIGIN: {origin}

Return a JSON object with exactly this structure:

{{
  "analysis_summary": "<2-3 sentence expert summary of why this product's classification is or is not ambiguous — written as a customs expert would frame it>",

  "original_assessment": {{
    "defensibility": "<STRONG | ARGUABLE | WEAK>",
    "defensibility_reasoning": "<1-2 sentences: on what legal basis is the current code defensible, and what is its strongest vulnerability if challenged>",
    "strongest_challenge_vector": "<the single most credible argument a customs authority could use to challenge this classification, or null if none>"
  }},

  "alternatives": [
    {{
      "lens": "<A | B | C | D | E>",
      "lens_label": "<MATERIAL | FUNCTION | PROCESSING_STAGE | COMPOSITE_PARTS | SPECIFICITY>",
      "proposed_framing": "<The alternative product description to run through the classification engine. This must be a complete, self-contained description a broker could submit — not a reference to the original. Write it so the classification engine has no knowledge of the original and would classify it independently.>",
      "gri_rule": "<The primary GRI rule that makes this framing legitimate: GRI-1 / GRI-2a / GRI-2b / GRI-3a / GRI-3b / GRI-3c / GRI-4 / GRI-5a / GRI-5b / GRI-6>",
      "legal_basis": "<1-3 sentences: the specific legal reasoning that makes this framing defensible under the cited GRI rule — reference heading terms, chapter/section notes, or Explanatory Notes where relevant>",
      "expected_heading_shift": "<The 4-digit heading you expect this framing to reach, with its title — e.g. '8479 – Machines and mechanical appliances having individual functions'. If uncertain at heading level, state the expected chapter.>",
      "confidence": "<HIGH | MEDIUM | LOW>",
      "confidence_reasoning": "<1 sentence: what would make this confidence rating change — what additional product fact would confirm or undermine this framing>",
      "declarant_note": "<Optional. A factual note the declarant should be aware of — e.g. a documentation requirement, a Chapter Note exclusion to verify, or a circumstance under which this framing would NOT be defensible. Omit if not applicable.>"
    }}
  ],

  "no_alternatives_reason": "<If alternatives array is empty, explain in 1-2 sentences why this product has no genuine classification ambiguity. Omit this field if alternatives are present.>"
}}

IMPORTANT CONSTRAINTS ON PROPOSED_FRAMING:
- Each proposed_framing must be a standalone, complete product description (minimum 15 words, maximum 80 words)
- It must not contain phrases like "alternatively classified as" or references to the original code
- It must read naturally as a product description a broker would write on a customs declaration
- It must be accurate to the physical product — do not add characteristics the product does not have
- It must emphasise the characteristic that makes the alternative classification defensible (the material, the function, the processing stage, etc.)

Return ONLY the JSON object. No preamble. No explanation outside the JSON.
"""


def build_optimize_prompt(product_description: str, current_code: str,
                          current_code_description: str, current_duty: str,
                          origin: str) -> tuple[str, str]:
    """Returns (system_prompt, user_message) ready for the oracle."""
    user = OPTIMIZE_USER_TEMPLATE.format(
        product_description=product_description,
        current_code=current_code,
        current_code_description=current_code_description or "description unavailable",
        current_duty=current_duty if current_duty not in (None, "") else "unknown",
        origin=origin or "not specified",
    )
    return OPTIMIZE_SYSTEM_PROMPT, user


def parse_optimize_response(raw_json: str) -> dict:
    """Strip any accidental markdown fences, parse, validate top-level keys."""
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_json.strip())
    data = json.loads(clean)
    required = {"analysis_summary", "original_assessment", "alternatives"}
    if not required.issubset(data.keys()):
        raise ValueError(f"Missing required keys: {required - data.keys()}")
    return data

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
    valid = primary.get("valid") or (None, None)
    return {
        "rate": _duty_display(primary),
        "name": primary.get("type_name", ""),
        "regulation": primary.get("regulation", ""),
        "legal": legal_info(primary.get("regulation", ""),
                            validity_end=valid[1],
                            legal_oj=primary.get("legal_oj")),
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
                 max_tokens: int = 1400, temperature: float = 0) -> str:
    """One raw Claude API call using the oracle's key + model. Returns '' on error."""
    body = {
        "model": oracle.model,
        "max_tokens": max_tokens,
        "temperature": temperature,
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


def _propose_framings(oracle: ClaudeOracle, description: str, current_code: str,
                      current_code_description: str, current_duty: str,
                      origin: str) -> dict | None:
    """Ask Claude for the 5-lens analysis. Returns the parsed object or None."""
    system, user = build_optimize_prompt(
        product_description=description,
        current_code=current_code,
        current_code_description=current_code_description,
        current_duty=current_duty,
        origin=origin,
    )
    raw = _call_claude(oracle, system, user, max_tokens=4000, temperature=0.2)
    if not raw:
        return None
    try:
        data = parse_optimize_response(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data.get("alternatives"), list):
        data["alternatives"] = []
    return data


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

    # --- run the 5-lens analysis ---------------------------------------------
    duty_for_prompt = ""
    if orig_duty and orig_duty.get("rate"):
        duty_for_prompt = re.sub(r"[%\s]+$", "", str(orig_duty["rate"]))

    analysis = _propose_framings(
        oracle, description, current_code,
        orig_cn_desc, duty_for_prompt, origin,
    )
    if analysis is None:
        return {"error": "The analysis could not be completed. Please try again."}

    proposed = analysis.get("alternatives", [])

    # --- classify each proposed framing, discard non-classifiable / same code -
    alternatives = []
    seen_codes = {code_norm}

    for framing_obj in proposed[:MAX_FRAMINGS]:
        framing_text = (framing_obj.get("proposed_framing") or "").strip()
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
                    "lens": framing_obj.get("lens", ""),
                    "lens_label": framing_obj.get("lens_label", ""),
                    "gri_rule": framing_obj.get("gri_rule", ""),
                    "legal_basis": framing_obj.get("legal_basis", ""),
                    "expected_heading_shift": framing_obj.get("expected_heading_shift", ""),
                    "confidence": framing_obj.get("confidence", ""),
                    "confidence_reasoning": framing_obj.get("confidence_reasoning", ""),
                    "declarant_note": framing_obj.get("declarant_note", ""),
                    "duty": alt_duty,
                    "defense": alt_defense,
                    "trail": trail,
                    "regulation": alt_duty.get("regulation", "") if alt_duty else "",
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
            "assessment": analysis.get("original_assessment", {}),
        },
        "analysis_summary": analysis.get("analysis_summary", ""),
        "alternatives": alternatives,
        "framings_proposed": len(proposed),
        "no_alternatives_reason": analysis.get("no_alternatives_reason", ""),
    }
