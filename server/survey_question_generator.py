#!/usr/bin/env python3
"""
survey_question_generator.py — Generate ONE clarifying survey question for a
single invoice line the GRI engine could not resolve.

This is the stronger sibling of ``survey_text.simplify_question``. Where
``simplify_question`` only rewords a question the engine already produced, this
module composes the whole question from DB-supplied candidate classifications:
it selects + phrases (never invents codes), anchors the question to the exact
invoice line, treats ``survey_locale`` as a hard parameter (never inferred), and
self-verifies the candidates against the line before emitting.

It is used after an invoice/spreadsheet scan, when a frozen line is turned into
the client-facing survey that gets emailed out.

Design rules carried over from the rest of the survey stack:
  * The LLM's ONLY job is selection + phrasing. We enforce every hard constraint
    (locale validity, codes ⊆ candidates, closed option set + single fallback)
    in Python so a misbehaving model cannot corrupt the survey.
  * No API key / any LLM error never breaks the survey: we fall back to a
    deterministic, constraint-respecting question built from the verbatim
    description and the candidates' official descriptions.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "engine"))

# --------------------------------------------------------------------------- #
#  The system prompt (verbatim contract). Sent as the model `system` message.  #
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """ROLE
You generate ONE clarifying survey question for a single invoice line that the
GRI engine could not resolve. You select from database-supplied candidate
classifications. You never invent codes, never invent legal text, and never
choose the survey language yourself.

NON-NEGOTIABLE CONSTRAINTS (violating any of these is a critical bug)

1. LANGUAGE IS A PARAMETER, NOT A DECISION.
   You will be given `survey_locale` (e.g. "en", "pl") in the input.
   Write the ENTIRE question and ALL answer labels in that locale and no other.
   Do not detect, infer, or switch language based on the invoice content, the
   goods description, the country of origin, or anything else. If the locale
   value is missing or unrecognized, DO NOT GUESS - return:
       {"error": "missing_or_invalid_survey_locale"}
   and nothing else.

2. CANDIDATES ARE SUPPLIED. YOU ONLY SELECT AND PHRASE.
   You receive `candidate_codes`: a list of {code, official_description}
   pulled from the nomenclature DB. Your answer options MUST be drawn ONLY
   from this list. You may not add, remove, merge, renumber, or reword the
   `code` values. You may lightly reword `official_description` ONLY to make it
   readable to a non-expert (see ANCHORING), never to change its meaning.

3. NO FREE-TEXT ANSWERS. Options are a closed set of the supplied candidates,
   plus exactly one fallback option meaning "none of these / I'm not sure"
   (localized).

ANCHORING - make the question identify the EXACT line item
The customer is looking at an invoice with many lines. A question like
"What is this container made of?" is unusable because they cannot tell which
item you mean. Every question MUST anchor to the specific line using, in order
of preference, whatever is available:
  - the line number ("line 6"),
  - the verbatim description as it appears on their invoice,
  - the single strongest distinguishing detail available: a stated HS code,
    a part/serial/model number from distinguishing_tokens, otherwise quantity
    + unit + origin.

Quote the customer's own description text back to them rather than substituting
your own noun. Do NOT introduce a category word (e.g. "container", "vessel")
that does not appear in their description - if their description is vague, say
so and ask them to confirm what the item physically is, referencing the line.

Ask the question in the plainest language a non-specialist shipper would
understand. Lead with what the item IS and what it's FOR - material, function,
construction - because that is what disambiguates the codes. Translate each
candidate into a short plain-language label, but keep the code visible.

SELF-VERIFICATION (do this silently, then act on it)
Before emitting the question, check the candidates against the line:

  A. RELEVANCE CHECK. Do the candidate_codes plausibly relate to
     `description_verbatim`? If NONE of them do, this is a likely MISREAD or a
     wrong-line freeze. Do not fabricate a question to fit bad candidates.
     Instead return:
       {
         "status": "candidates_mismatch",
         "line_number": ...,
         "observation": "<one sentence: why the candidates don't fit the line>"
       }

  B. EXTRACTION CHECK. If extraction_confidence is low, or
     description_verbatim looks garbled/truncated, OR a stated HS code
     contradicts the description, flag it rather than papering over it:
       {
         "status": "extraction_suspect",
         "line_number": ...,
         "field": "<which field looks wrong>",
         "observation": "<one sentence>"
       }
     Prefer asking the customer to confirm the item identity over guessing.

  C. ANCHOR CHECK. Confirm your drafted question contains at least the line
     number AND either the verbatim description or a distinguishing token.
     If you cannot anchor it, do not emit a vague question - emit
     extraction_suspect with field "anchor".

When genuinely uncertain whether candidates fit, lean toward flagging (status
A or B) - a flagged line gets human review, a confidently-wrong question gets a
wrong classification.

OUTPUT (on success)
Return ONLY this JSON, in `survey_locale`, no preamble, no markdown:
{
  "status": "ok",
  "line_number": <int>,
  "anchor_summary": "<localized: 'Line 6 - \\"Parts and accessories\\", origin IT, 80 pcs'>",
  "question": "<localized, plain-language, anchored question>",
  "options": [
     {"code": "<from candidates, unchanged>", "label": "<localized plain-language>"},
     ...
     {"code": null, "label": "<localized 'None of these / I'm not sure'>"}
  ]
}"""


# --------------------------------------------------------------------------- #
#  Locale support — discovered from the shipped locale files (en, pl).         #
# --------------------------------------------------------------------------- #
def _discover_locales() -> set[str]:
    try:
        return {p.stem.lower() for p in (ROOT / "locales").glob("*.json")} or {"en", "pl"}
    except Exception:
        return {"en", "pl"}


SUPPORTED_LOCALES = _discover_locales()

# Localized strings used only by the deterministic (no-LLM) fallback path.
_FALLBACK_OPTION_LABEL = {
    "en": "None of these / I'm not sure",
    "pl": "Żadne z powyższych / Nie wiem",
}
_FALLBACK_QUESTION = {
    "en": 'Line {n} on your invoice reads "{desc}". Which of these best describes that item?',
    "pl": 'Pozycja {n} na fakturze brzmi „{desc}". Która z poniższych najlepiej opisuje ten towar?',
}
_FALLBACK_VAGUE = {
    "en": 'Line {n} on your invoice reads "{desc}", which is not specific enough to classify. What is this item, physically?',
    "pl": 'Pozycja {n} na fakturze brzmi „{desc}", co nie wystarcza do klasyfikacji. Czym fizycznie jest ten towar?',
}


def _error_invalid_locale() -> dict:
    return {"error": "missing_or_invalid_survey_locale"}


# --------------------------------------------------------------------------- #
#  Public entry point                                                          #
# --------------------------------------------------------------------------- #
def generate_survey_question(payload: dict) -> dict:
    """Generate one clarifying survey question for a frozen invoice line.

    `payload` is the input contract documented in the system prompt:
        {
          "survey_locale": "en" | "pl" | ...,
          "line": {line_number, description_verbatim, hs_code_stated, origin,
                   quantity, unit_price, distinguishing_tokens, extraction_confidence},
          "ambiguity_reason": "...",
          "candidate_codes": [{"code": ..., "official_description": ...}, ...]
        }

    Returns one of the prompt's JSON shapes: the {"error": ...} object, a
    {"status": "candidates_mismatch"|"extraction_suspect"|"ok", ...} object.
    Every hard constraint (locale validity, codes ⊆ candidates, single fallback
    option) is enforced here regardless of what the model returns.
    """
    payload = payload or {}
    locale = (payload.get("survey_locale") or "").strip().lower()

    # CONSTRAINT 1: locale is a parameter. Unknown/missing -> do not guess.
    if locale not in SUPPORTED_LOCALES:
        return _error_invalid_locale()

    line = payload.get("line") or {}
    line_number = _coerce_int(line.get("line_number"))
    candidates = _clean_candidates(payload.get("candidate_codes"))

    # CONSTRAINT 2/3 precondition: with no candidates there is nothing to ask;
    # surface it as a mismatch for human review rather than inventing options.
    if not candidates:
        return {
            "status": "candidates_mismatch",
            "line_number": line_number,
            "observation": "No candidate classifications were supplied for this line.",
        }

    raw = _call_model(payload, locale)
    if raw is not None:
        validated = _validate_model_output(raw, locale, line_number, candidates)
        if validated is not None:
            return validated

    # Deterministic fallback (no API key / LLM error / unusable output).
    return _fallback_question(locale, line, line_number, candidates)


# --------------------------------------------------------------------------- #
#  Model call                                                                  #
# --------------------------------------------------------------------------- #
def _model() -> str:
    try:
        from oracles import ClaudeOracle
        return ClaudeOracle.DEFAULT_MODEL
    except Exception:
        return "claude-sonnet-4-6"


def _call_model(payload: dict, locale: str) -> dict | None:
    """Call Claude with the verbatim system prompt and the input payload as the
    user message. Returns the parsed JSON object, or None on any failure."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    # Re-serialize a clean copy of the contract so the model sees exactly the
    # documented input shape (and an unambiguous locale).
    user_payload = {
        "survey_locale": locale,
        "line": payload.get("line") or {},
        "ambiguity_reason": payload.get("ambiguity_reason") or "",
        "candidate_codes": _clean_candidates(payload.get("candidate_codes")),
    }
    body = {
        "model": _model(),
        "max_tokens": 700,
        "temperature": 0,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user",
                      "content": json.dumps(user_payload, ensure_ascii=False)}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json",
                 "x-api-key": api_key,
                 "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        text = "".join(b.get("text", "") for b in data.get("content", [])).strip()
        return _parse_json_object(text)
    except Exception:
        return None


def _parse_json_object(text: str) -> dict | None:
    """Pull the first JSON object out of the model text, tolerating stray
    markdown fences or preamble despite the prompt asking for none."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        nl = t.find("\n")
        if nl != -1 and t[:nl].strip().lower() in ("json", ""):
            t = t[nl + 1:]
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        obj = json.loads(t[start:end + 1])
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
#  Output validation — Python owns the hard constraints                        #
# --------------------------------------------------------------------------- #
def _validate_model_output(raw: dict, locale: str, line_number: int | None,
                           candidates: list[dict]) -> dict | None:
    """Coerce the model's JSON into a contract-valid object, or return None to
    signal the caller to use the deterministic fallback."""
    # The model honoured the locale guard itself.
    if raw.get("error") == "missing_or_invalid_survey_locale":
        return _error_invalid_locale()

    status = raw.get("status")

    if status in ("candidates_mismatch", "extraction_suspect"):
        out = {"status": status,
               "line_number": _coerce_int(raw.get("line_number"), line_number)}
        if status == "extraction_suspect":
            out["field"] = str(raw.get("field") or "").strip() or "description_verbatim"
        out["observation"] = str(raw.get("observation") or "").strip()
        if not out["observation"]:
            return None
        return out

    if status == "ok":
        question = str(raw.get("question") or "").strip()
        options = _sanitize_options(raw.get("options"), candidates, locale)
        if not question or options is None:
            return None
        return {
            "status": "ok",
            "line_number": _coerce_int(raw.get("line_number"), line_number),
            "anchor_summary": str(raw.get("anchor_summary") or "").strip(),
            "question": question,
            "options": options,
        }

    return None


def _sanitize_options(opts, candidates: list[dict], locale: str) -> list | None:
    """Enforce constraints 2 & 3: every non-null option code MUST be one of the
    supplied candidates (unchanged); the set is closed; exactly one null
    fallback option is present (appended if the model forgot it)."""
    if not isinstance(opts, list):
        return None
    valid_codes = {c["code"] for c in candidates}
    seen: set = set()
    cleaned: list[dict] = []
    has_fallback = False
    for o in opts:
        if not isinstance(o, dict):
            continue
        code = o.get("code")
        label = str(o.get("label") or "").strip()
        if code is None:
            if has_fallback or not label:
                continue  # keep exactly one fallback
            cleaned.append({"code": None, "label": label})
            has_fallback = True
            continue
        if code not in valid_codes or code in seen:
            continue  # never invent / never duplicate a code
        seen.add(code)
        cleaned.append({"code": code, "label": label or _official(candidates, code)})

    if not any(o["code"] is not None for o in cleaned):
        return None  # no real option survived -> fall back
    if not has_fallback:
        cleaned.append({"code": None,
                        "label": _FALLBACK_OPTION_LABEL.get(locale, _FALLBACK_OPTION_LABEL["en"])})
    return cleaned


# --------------------------------------------------------------------------- #
#  Deterministic fallback (no LLM)                                             #
# --------------------------------------------------------------------------- #
def _fallback_question(locale: str, line: dict, line_number: int | None,
                       candidates: list[dict]) -> dict:
    """Constraint-respecting question without an LLM: anchor by line number +
    verbatim description, label each option with its official description
    (verbatim is always meaning-preserving), append the localized fallback."""
    desc = (line.get("description_verbatim") or "").strip()
    n = line_number if line_number is not None else (line.get("line_number") or "?")
    vague = len(desc) < 3
    tmpl = (_FALLBACK_VAGUE if vague else _FALLBACK_QUESTION).get(
        locale, _FALLBACK_QUESTION["en"])
    question = tmpl.format(n=n, desc=desc or "(no description)")

    options = [{"code": c["code"], "label": c["official_description"]}
               for c in candidates]
    options.append({"code": None,
                    "label": _FALLBACK_OPTION_LABEL.get(locale, _FALLBACK_OPTION_LABEL["en"])})

    return {
        "status": "ok",
        "line_number": _coerce_int(line_number, 0) or 0,
        "anchor_summary": _anchor_summary(locale, line, n),
        "question": question,
        "options": options,
    }


def _anchor_summary(locale: str, line: dict, n) -> str:
    desc = (line.get("description_verbatim") or "").strip()
    bits = [f'„{desc}"' if locale == "pl" else f'"{desc}"'] if desc else []
    origin = (line.get("origin") or "").strip()
    if origin:
        bits.append((f"pochodzenie {origin}" if locale == "pl"
                     else f"origin {origin}"))
    qty = (line.get("quantity") or "").strip()
    if qty:
        bits.append(qty)
    head = "Pozycja" if locale == "pl" else "Line"
    return f"{head} {n} - " + ", ".join(bits) if bits else f"{head} {n}"


# --------------------------------------------------------------------------- #
#  Small helpers                                                               #
# --------------------------------------------------------------------------- #
def _coerce_int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _clean_candidates(raw) -> list[dict]:
    """Keep only well-formed {code, official_description} entries with a
    non-empty string code; de-duplicate by code, preserving order."""
    out: list[dict] = []
    seen: set = set()
    for c in (raw or []):
        if not isinstance(c, dict):
            continue
        code = c.get("code")
        if not isinstance(code, str) or not code.strip() or code in seen:
            continue
        seen.add(code)
        out.append({"code": code,
                    "official_description": str(c.get("official_description") or "").strip()})
    return out


def _official(candidates: list[dict], code: str) -> str:
    for c in candidates:
        if c["code"] == code:
            return c["official_description"]
    return code


if __name__ == "__main__":  # quick manual smoke test
    demo = {
        "survey_locale": "en",
        "line": {"line_number": 6, "description_verbatim": "Parts and accessories",
                 "hs_code_stated": None, "origin": "IT", "quantity": "80 pcs",
                 "unit_price": "12.50", "distinguishing_tokens": ["MOD-X7"],
                 "extraction_confidence": 0.91},
        "ambiguity_reason": "Heading reached but subheading ambiguous",
        "candidate_codes": [
            {"code": "8466.93", "official_description": "Parts for machine tools working by removing metal"},
            {"code": "8473.30", "official_description": "Parts and accessories of automatic data-processing machines"},
        ],
    }
    print(json.dumps(generate_survey_question(demo), ensure_ascii=False, indent=2))
