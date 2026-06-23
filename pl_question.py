"""Localize a clarification question into Polish — POST-PROCESSING ONLY.

The GRI engine is NEVER touched. After the engine returns its (English)
clarification question, this layer rewrites it into Polish for the PL profile:

  - fixed question template -> deterministic Polish strings
  - option labels           -> Polish nomenclature descriptions from the local
                               ISZTAR store (deterministic; English fallback)
  - free-text reasoning     -> one isolated translation call (UI text only; it
                               can never change the classification or its codes)

EU/English requests never enter this module, so the English engine path is
byte-for-byte unchanged.
"""
import os
from datetime import date as _date

import isztar_pl

# Polish equivalents of the two fixed templates in engine_session._humanize_ask
_TEMPLATES = {
    "Which product category fits best? These are the legal headings the AI is weighing.":
        "Która kategoria produktu pasuje najlepiej? To są pozycje prawne, które rozważa silnik.",
    "One detail decides this. Which option matches your product at this level?":
        "Jeden szczegół to rozstrzyga. Która opcja odpowiada Twojemu produktowi na tym poziomie?",
}
_NEEDS_PL = "Silnik musi ustalić:"


def _model():
    """Reuse the engine's configured model (same one the oracle calls)."""
    try:
        from oracles import ClaudeOracle
        return ClaudeOracle.DEFAULT_MODEL
    except Exception:
        return "claude-sonnet-4-6"


def _heading_pl(code, date):
    digits = "".join(c for c in str(code) if c.isdigit())
    if not digits:
        return None
    # Option ids can be 4-digit headings ("6102") or 10-digit subheadings
    # ("9403300000:80" -> 12 digits). Right-pad partial codes to the 10-digit
    # nomenclature form (headings are stored as 6102000000, not 0000006102).
    code10 = (digits + "0000000000")[:10]
    try:
        return isztar_pl.get_pl_national_measures(code10, date).get("description_pl")
    except Exception:
        return None


def _api(text, system):
    """One isolated Anthropic call (urllib, same path the engine uses). None on
    failure. Independent of the classification oracle — cannot affect determinism."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        import json
        import urllib.request
        body = {
            "model": _model(), "max_tokens": 700, "temperature": 0,
            "system": system,
            "messages": [{"role": "user", "content": text}],
        }
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(body).encode("utf-8"),
            headers={"content-type": "application/json",
                     "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                     "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode("utf-8"))
        out = "".join(b.get("text", "") for b in data.get("content", [])
                      if b.get("type") == "text").strip()
        return out or None
    except Exception:
        return None


def translate_text(text):
    """English UI text -> natural Polish (for displaying questions/results)."""
    return _api(text, "You translate UI text for a customs-tariff tool into natural, "
                "professional Polish for customs brokers. Output ONLY the Polish "
                "translation — no preamble, no quotes. Keep CN/HS codes, numbers, "
                "chapter/heading references and product codes unchanged.")


def translate_to_english(text):
    """Polish product text -> concise English for the (English-trained) GRI
    engine's keyword retrieval. Returns None on failure (caller keeps original).

    This feeds the engine clean English so its FTS candidate search works exactly
    as it does for English users; the engine itself is never modified."""
    return _api(text, "You translate a product description from Polish to concise, "
                "precise English suitable for EU customs tariff classification. "
                "Output ONLY the English translation — no preamble, no quotes. "
                "Preserve material, function, form and any numbers/dimensions exactly.")


def localize(question, market="EU", date=None):
    """Return a Polish version of an engine clarification-question dict (PL only)."""
    if (market or "").upper() != "PL" or not isinstance(question, dict):
        return question
    date = date or _date.today().isoformat()
    q = dict(question)

    why = (q.get("why") or "").strip()
    why_pl = translate_text(why) if why else None
    if why_pl:
        q["why"] = why_pl

    ask = q.get("ask") or ""
    base_pl = None
    for en, pl in _TEMPLATES.items():
        if ask.startswith(en):
            base_pl = pl
            break
    if base_pl is not None:
        new_ask = base_pl
        tail = why_pl or why
        if tail:
            new_ask += f"  ({_NEEDS_PL} {tail})"
        q["ask"] = new_ask
    else:
        q["ask"] = translate_text(ask) or ask

    opts = []
    for o in (q.get("options") or []):
        oo = dict(o)
        pl = _heading_pl(o.get("id", ""), date)
        if pl:
            oo["text"] = pl
        opts.append(oo)
    if opts:
        q["options"] = opts
    return q
