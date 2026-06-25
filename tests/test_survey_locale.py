#!/usr/bin/env python3
"""Tests for survey_locale.resolve_survey_locale — deterministic, hermetic
(no API key, no DB). Locks down the reported bug: an English UI must never
produce a Polish survey, and a missing signal must fail safe to "en"."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import survey_locale as sl  # noqa: E402


def test_ui_english():
    assert sl.resolve_survey_locale(ui_locale="en") == "en"


def test_broker_beats_ui():
    assert sl.resolve_survey_locale(broker_locale="pl", ui_locale="en") == "pl"


def test_reported_bug_english_ui_never_polish():
    # English UI + English-ish invoice must resolve to English, not pl.
    assert sl.resolve_survey_locale(
        broker_locale="en", ui_locale="en", invoice_locale=None) == "en"


def test_normalization_region_tag():
    assert sl.resolve_survey_locale(ui_locale="PL-pl") == "pl"


def test_normalization_quality_value():
    assert sl.resolve_survey_locale(ui_locale="en;q=0.9") == "en"


def test_normalization_accept_language_list():
    # A full Accept-Language header takes its primary language.
    assert sl.resolve_survey_locale(ui_locale="pl-PL,pl;q=0.9,en;q=0.8") == "pl"


def test_unknown_coerces_to_default():
    assert sl.resolve_survey_locale(ui_locale="zz") == sl.DEFAULT_SURVEY_LOCALE


def test_all_empty_is_default_and_default_is_english():
    # Guards against a future silent flip back to pl.
    assert sl.resolve_survey_locale() == sl.DEFAULT_SURVEY_LOCALE
    assert sl.DEFAULT_SURVEY_LOCALE == "en"


def test_invoice_locale_only_used_last():
    # Invoice language is last-resort: it wins only when broker+UI are empty.
    assert sl.resolve_survey_locale(invoice_locale="pl") == "pl"
    assert sl.resolve_survey_locale(ui_locale="en", invoice_locale="pl") == "en"
