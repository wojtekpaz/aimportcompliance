#!/usr/bin/env python3
"""Tests for survey_question_generator — the deterministic guards and output
constraints. These run WITHOUT an API key (the LLM path falls back), so they
exercise the contract Python enforces regardless of the model."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import survey_question_generator as sqg  # noqa: E402


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch):
    # Force the deterministic path so tests are hermetic.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


CANDS = [
    {"code": "8466.93", "official_description": "Parts for metal-working machine tools"},
    {"code": "8473.30", "official_description": "Parts and accessories of data-processing machines"},
]


def _line(**kw):
    base = {"line_number": 6, "description_verbatim": "Parts and accessories",
            "origin": "IT", "quantity": "80 pcs", "distinguishing_tokens": [],
            "extraction_confidence": 0.9}
    base.update(kw)
    return base


def test_missing_locale_errors():
    out = sqg.generate_survey_question({"line": _line(), "candidate_codes": CANDS})
    assert out == {"error": "missing_or_invalid_survey_locale"}


def test_unrecognized_locale_errors():
    out = sqg.generate_survey_question(
        {"survey_locale": "zz", "line": _line(), "candidate_codes": CANDS})
    assert out == {"error": "missing_or_invalid_survey_locale"}


def test_no_candidates_is_mismatch():
    out = sqg.generate_survey_question(
        {"survey_locale": "en", "line": _line(), "candidate_codes": []})
    assert out["status"] == "candidates_mismatch"
    assert out["line_number"] == 6


def test_fallback_is_anchored_and_closed_set():
    out = sqg.generate_survey_question(
        {"survey_locale": "en", "line": _line(), "candidate_codes": CANDS})
    assert out["status"] == "ok"
    assert out["line_number"] == 6
    # Anchored: contains the line number AND the verbatim description.
    assert "6" in out["question"]
    assert "Parts and accessories" in out["question"]
    # Every non-null code is a supplied candidate; exactly one null fallback.
    codes = [o["code"] for o in out["options"]]
    assert codes.count(None) == 1
    real = [c for c in codes if c is not None]
    assert set(real) <= {"8466.93", "8473.30"}
    assert all(o["label"] for o in out["options"])


def test_locale_is_respected_not_inferred():
    # Polish locale must yield Polish scaffolding even with English goods text.
    out = sqg.generate_survey_question(
        {"survey_locale": "pl", "line": _line(), "candidate_codes": CANDS})
    assert out["options"][-1]["label"] == "Żadne z powyższych / Nie wiem"
    assert out["anchor_summary"].startswith("Pozycja")


def test_vague_description_asks_what_it_is():
    out = sqg.generate_survey_question(
        {"survey_locale": "en", "line": _line(description_verbatim="x"),
         "candidate_codes": CANDS})
    assert "physically" in out["question"].lower()


def test_validate_drops_invented_codes():
    raw = {"status": "ok", "line_number": 6, "anchor_summary": "Line 6",
           "question": "Line 6 - Parts and accessories: which one?",
           "options": [
               {"code": "8466.93", "label": "Machine-tool parts"},
               {"code": "9999.99", "label": "Invented code"},  # not a candidate
               {"code": None, "label": "None of these"},
           ]}
    out = sqg._validate_model_output(raw, "en", 6, sqg._clean_candidates(CANDS))
    codes = [o["code"] for o in out["options"]]
    assert "9999.99" not in codes
    assert codes.count(None) == 1


def test_validate_appends_missing_fallback():
    raw = {"status": "ok", "line_number": 6, "anchor_summary": "Line 6",
           "question": "Line 6 - Parts and accessories: which one?",
           "options": [{"code": "8466.93", "label": "Machine-tool parts"}]}
    out = sqg._validate_model_output(raw, "en", 6, sqg._clean_candidates(CANDS))
    assert out["options"][-1]["code"] is None
    assert out["options"][-1]["label"] == "None of these / I'm not sure"


def test_passthrough_extraction_suspect():
    raw = {"status": "extraction_suspect", "line_number": 6,
           "field": "hs_code_stated", "observation": "Stated code contradicts text."}
    out = sqg._validate_model_output(raw, "en", 6, sqg._clean_candidates(CANDS))
    assert out["status"] == "extraction_suspect"
    assert out["field"] == "hs_code_stated"
