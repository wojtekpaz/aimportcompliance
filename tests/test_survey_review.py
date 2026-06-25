#!/usr/bin/env python3
"""Tests for survey_review — the review-flag store and its server-side render.
Hermetic: no API key, no model, and a temp DB so the real survey DB is never
touched (we monkeypatch survey_db.DB_PATH; survey_review reuses survey_db._conn,
which reads DB_PATH at call time, and ensures its table on every connection)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import survey_db as sdb        # noqa: E402
import survey_review as srev   # noqa: E402


@pytest.fixture(autouse=True)
def _temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(sdb, "DB_PATH", tmp_path / "test_review.sqlite")
    yield


def test_raise_and_list_candidates_mismatch():
    srev.raise_flag(broker_id="broker-local", invoice_ref="INV-1",
                    line_number=6, description="Parts and accessories",
                    flag_type="candidates_mismatch",
                    observation="Candidates are wine and casks; no fit.")
    flags = srev.list_flags()
    assert len(flags) == 1
    f = flags[0]
    assert f["flag_type"] == "candidates_mismatch"
    assert f["line_number"] == 6
    assert f["observation"] == "Candidates are wine and casks; no fit."
    assert f["status"] == "open"


def test_unknown_flag_type_rejected():
    with pytest.raises(ValueError):
        srev.raise_flag(broker_id="b", invoice_ref="INV", line_number=1,
                        description="x", flag_type="totally_made_up",
                        observation="nope")
    assert srev.list_flags() == []  # nothing stored


def test_extraction_suspect_stores_field():
    srev.raise_flag(broker_id="b", invoice_ref="INV-2", line_number=3,
                    description="g@rbled", flag_type="extraction_suspect",
                    observation="HS code contradicts description.",
                    field="hs_code_stated")
    f = srev.list_flags()[0]
    assert f["flag_type"] == "extraction_suspect"
    assert f["field"] == "hs_code_stated"


def test_mark_reviewed_drops_from_open():
    res = srev.raise_flag(broker_id="b", invoice_ref="INV-3", line_number=1,
                          description="x", flag_type="candidates_mismatch",
                          observation="o")
    srev.mark_reviewed(res["id"])
    assert srev.list_flags(status="open") == []
    reviewed = srev.list_flags(status="reviewed")
    assert len(reviewed) == 1 and reviewed[0]["status"] == "reviewed"


def test_list_is_newest_first():
    for i in range(3):
        srev.raise_flag(broker_id="b", invoice_ref=f"INV-{i}", line_number=i,
                        description=f"item {i}", flag_type="candidates_mismatch",
                        observation=f"obs {i}")
    refs = [f["invoice_ref"] for f in srev.list_flags()]
    assert refs == ["INV-2", "INV-1", "INV-0"]


def test_migration_is_idempotent():
    # Running the table-creation path twice in one process must not error.
    c1 = srev._conn(); c1.close()
    c2 = srev._conn(); c2.close()


def test_render_empty_state_both_locales():
    assert "No lines awaiting review." in srev.render_review_page([], "en")
    assert "Brak pozycji oczekujących na przegląd." in srev.render_review_page([], "pl")


def test_render_seeded_state_both_locales():
    srev.raise_flag(broker_id="b", invoice_ref="INV-9", line_number=6,
                    description="Parts and accessories",
                    flag_type="candidates_mismatch",
                    observation="No fit between candidates and the line.")
    flags = srev.list_flags()

    en = srev.render_review_page(flags, "en")
    assert "Lines for review" in en
    assert "Parts and accessories" in en
    assert "Candidate codes did not fit the line" in en
    assert "Mark reviewed" in en
    assert "/survey/review/" in en and "/reviewed" in en  # the action form

    pl = srev.render_review_page(flags, "pl")
    assert "Pozycje do przeglądu" in pl
    assert "Kody kandydujące nie pasowały do pozycji" in pl
    assert "Oznacz jako sprawdzone" in pl


def test_render_escapes_html():
    srev.raise_flag(broker_id="b", invoice_ref="INV", line_number=1,
                    description="<script>alert(1)</script>",
                    flag_type="candidates_mismatch", observation="x")
    out = srev.render_review_page(srev.list_flags(), "en")
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out
