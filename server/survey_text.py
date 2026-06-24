#!/usr/bin/env python3
"""
survey_text.py — Plain-language rewriting of engine questions for clients.

Milestone One (V2), Section 5. The client (importer) is not a customs expert, so
the GRI engine's question text is rewritten into plain language before display.
This is the oracle's ONLY generative role in the survey: it never produces a
code and never alters the constrained option set — it only rewrites wording.

If no API key is configured (or any error occurs), we fall back to the original
question verbatim. Results are cached per question text.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

_cache: dict[str, str] = {}

# English prompt (kept as the EN fallback / legacy behaviour).
SIMPLIFY_PROMPT_EN = (
    "Rewrite this customs classification question in plain, non-technical "
    "language for an importer who is not a customs expert. Be brief. Keep it "
    "under 20 words. Return ONLY the rewritten question, no quotes.")

# Polish prompt — Polish is the primary, default survey language.
SIMPLIFY_PROMPT_PL = """Jesteś ekspertem ds. handlu zagranicznego i klasyfikacji celnej.

Poniższe pytanie pochodzi z systemu klasyfikacji taryfowej. Przepisz je tak, żeby importerzy i pracownicy magazynów mogli je zrozumieć bez wiedzy celnej.

Zasady:
- Maksymalnie 20 słów.
- Prosty, codzienny język. Bez skrótów celnych (nie używaj: GRI, CN, TARIC, pozycja taryfowa, dział, rozdział).
- Jeśli pytanie dotyczy materiału — zapytaj o materiał.
- Jeśli dotyczy zastosowania — zapytaj o zastosowanie.
- Jeśli dotyczy zawartości alkoholu lub składu — zapytaj wprost o wartość procentową lub skład.
- Odpowiedz TYLKO przepisanym pytaniem. Żadnych wyjaśnień, żadnych cudzysłowów, żadnych wstępów."""


def _model() -> str:
    try:
        from oracles import ClaudeOracle
        return ClaudeOracle.DEFAULT_MODEL
    except Exception:
        return "claude-sonnet-4-6"


def simplify_question(engine_question: str, language: str = "pl") -> str:
    """Rewrite one engine question into plain client-facing language, in the
    session's language (Polish by default). Cached per (language, question) so EN
    and PL never collide. Falls back to the original text on any error / no key."""
    q = (engine_question or "").strip()
    if not q:
        return q
    lang = (language or "pl").lower()
    ckey = f"{lang}::{q}"
    if ckey in _cache:
        return _cache[ckey]

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        _cache[ckey] = q
        return q

    system = SIMPLIFY_PROMPT_PL if lang == "pl" else SIMPLIFY_PROMPT_EN
    body = {
        "model": _model(),
        "max_tokens": 120,
        "temperature": 0,
        "system": system,
        "messages": [{"role": "user", "content": q}],
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
        simplified = text.strip().strip('"').strip() or q
    except Exception:
        simplified = q  # additive nicety; never break the survey on an LLM error
    _cache[ckey] = simplified
    return simplified


# --------------------------------------------------------------------------- #
#  Polish display labels (options, freeze-reason context, intro)              #
#  Option VALUES stay English (the engine processes them); only the displayed #
#  label is translated. Unknown options fall back to the original verbatim.   #
# --------------------------------------------------------------------------- #

OPTION_TRANSLATIONS_PL = {
    # Polymers / materials
    "Polyethylene (PE)": "Polietylen (PE)",
    "Polypropylene (PP)": "Polipropylen (PP)",
    "Mixed / cannot determine": "Mieszanina / nie wiem",
    "Polyethylene terephthalate (PET)": "Politereftalan etylenu (PET)",
    "Polyvinyl chloride (PVC)": "Polichlorek winylu (PVC)",
    "Polystyrene (PS)": "Polistyren (PS)",
    "Other plastic": "Inny materiał plastikowy",
    # States / forms
    "Frozen": "Mrożone",
    "Fresh / chilled": "Świeże / chłodzone",
    "Dried": "Suszone",
    "Processed / preserved": "Przetworzone / konserwowane",
    # Alcohol
    "Ethyl alcohol (ethanol)": "Alkohol etylowy (etanol)",
    "Isopropyl alcohol (IPA)": "Alkohol izopropylowy (IPA)",
    "Other alcohol base": "Inna baza alkoholowa",
    "Non-alcoholic": "Bezalkoholowy",
    # Function
    "Primary function: lighting": "Główna funkcja: oświetlenie",
    "Primary function: charging / power supply": "Główna funkcja: ładowanie / zasilanie",
    "Both functions equally important": "Obie funkcje równie ważne",
    # Machine parts
    "Parts for injection moulding machines": "Części do wtryskarek",
    "Parts for extrusion machines": "Części do wytłaczarek",
    "Parts for CNC / machining centres": "Części do centrów CNC / obróbczych",
    "General purpose / universal machine parts": "Części ogólnego przeznaczenia",
    "Cannot determine — no machine model provided": "Nie można określić — brak modelu maszyny",
    # Big-bag / packaging
    "Flexible intermediate bulk container (FIBC / big-bag)": "Elastyczny kontener pośredni (big-bag)",
    "Woven synthetic fibre sack": "Worek tkany z włókna syntetycznego",
    "Non-woven sack": "Worek z włókniny",
    # Generic
    "Yes": "Tak",
    "No": "Nie",
    "I don't know": "Nie wiem",
    "Other": "Inne",
    "Please provide more detail": "Proszę podać więcej szczegółów",
    # Fixed field prompts (survey_freeze MISSING_DESC_OPTIONS) + OCR confirm
    "Please provide a product description": "Proszę podać opis produktu",
    "I have a photo I can share": "Mam zdjęcie, które mogę udostępnić",
    "I will describe it in more detail": "Opiszę to dokładniej",
    "The value above is wrong — I will type the correct one":
        "Powyższa wartość jest błędna — wpiszę poprawną",
}


def translate_option(option: str, language: str = "pl") -> str:
    """Polish display label for an engine option (original returned if unknown
    or when language is not Polish)."""
    if (language or "pl").lower() != "pl":
        return option
    return OPTION_TRANSLATIONS_PL.get(option, option)


# Small muted context line shown above the question to explain WHY it is asked.
FREEZE_REASON_LABEL_PL = {
    "MISSING_DESCRIPTION": "Brak opisu towaru w dokumentach",
    "AMBIGUOUS_PRODUCT": "Opis towaru wymaga doprecyzowania",
    "LOW_OCR_CONFIDENCE": "Nie udało się odczytać tej pozycji z dokumentu",
    "INVALID_CODE": "Podany kod celny wymaga weryfikacji",
    "NEEDS_DETAIL": "Potrzebujemy jednej dodatkowej informacji",
}


def freeze_reason_label(reason: str, language: str = "pl") -> str:
    if (language or "pl").lower() != "pl":
        return ""
    return FREEZE_REASON_LABEL_PL.get(reason or "", "")


def survey_intro(language: str = "pl") -> str:
    if (language or "pl").lower() == "pl":
        return "Twój agent celny potrzebuje kilku szczegółów dotyczących przesyłki."
    return "Your customs broker needs a few details about your shipment."


def pick_option_for_freetext(answer_detail: str, option_set: list,
                             market: str = "EU") -> str | None:
    """Section 8.6: when the client supplies supplementary free text, the oracle
    selects the best-matching option from the constrained set — verbatim. It can
    only pick from the options; it never generates a code. Returns the chosen
    option text, or None on failure (caller then leaves the line frozen).
    """
    detail = (answer_detail or "").strip()
    options = [o for o in (option_set or []) if isinstance(o, str)]
    if not detail or not options:
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    opt_block = "\n".join(f"- {o}" for o in options)
    prompt = (f"Given this product description: {detail}\n\nand these "
              f"classification options:\n{opt_block}\n\nWhich option best "
              f"describes it? Return only the option text verbatim, exactly as "
              f"written above.")
    body = {"model": _model(), "max_tokens": 120, "temperature": 0,
            "messages": [{"role": "user", "content": prompt}]}
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
    except Exception:
        return None
    text = text.strip().strip('"').strip()
    # Constrain to the option set — verbatim match, else closest by containment.
    for o in options:
        if text == o:
            return o
    low = text.lower()
    for o in options:
        if o.lower() in low or low in o.lower():
            return o
    return None
