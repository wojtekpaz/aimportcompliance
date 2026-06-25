#!/usr/bin/env python3
"""survey_locale.py — deterministic resolution of the survey language.

The survey language is a PARAMETER decided in control flow, never inferred by
the model. Resolution priority (first non-empty wins):
  1. broker account setting
  2. UI language of the originating request
  3. invoice-detected language (last resort, only if already present)
  4. DEFAULT_SURVEY_LOCALE
Anything outside SUPPORTED_LOCALES coerces to DEFAULT_SURVEY_LOCALE.
"""
from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SURVEY_LOCALE = "en"  # explicit; fail safe to neutral, never silently to pl


def _discover_locales() -> set[str]:
    try:
        found = {p.stem.lower() for p in (ROOT / "locales").glob("*.json")}
        return found or {"en", "pl"}
    except Exception:
        return {"en", "pl"}


SUPPORTED_LOCALES = _discover_locales()


def _norm(v) -> str:
    """Normalize a raw locale-ish value to a bare lowercase language code."""
    if not isinstance(v, str):
        return ""
    s = v.strip().lower()
    # tolerate 'pl-PL', 'en_US', 'en;q=0.9', 'pl-PL,pl;q=0.9,en;q=0.8' style values
    for sep in ("-", "_", ";", ","):
        if sep in s:
            s = s.split(sep, 1)[0].strip()
    return s


def resolve_survey_locale(
    *,
    broker_locale=None,
    ui_locale=None,
    invoice_locale=None,
) -> str:
    """Resolve the survey language deterministically. See module docstring.

    All args are optional raw values (may be None / 'pl-PL' / 'en;q=0.9').
    Returns a value guaranteed to be in SUPPORTED_LOCALES.
    """
    for candidate in (broker_locale, ui_locale, invoice_locale):
        code = _norm(candidate)
        if code in SUPPORTED_LOCALES:
            return code
    return DEFAULT_SURVEY_LOCALE if DEFAULT_SURVEY_LOCALE in SUPPORTED_LOCALES else "en"
