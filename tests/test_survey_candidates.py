#!/usr/bin/env python3
"""Tests for survey_candidates — the candidate-source adapter.

Hermetic: a synthetic TARIC fixture DB (the shipped sample_taric.xml has only a
handful of codes, too thin to prove scoping/capping). The headline test is the
PATH (b) SCOPING ASSERTION: a scoped lookup must return a bounded set whose codes
all share the reached-heading prefix and explicitly NOT the whole chapter — the
exact failure that produced valve/cask/colourant candidates for a furniture line.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import survey_candidates as sc  # noqa: E402


# --------------------------------------------------------------------------- #
#  Synthetic TARIC fixture: furniture heading 9403 with 9 immediate           #
#  subheadings (to exceed the cap), a decoy heading 9401 (seats) in the SAME   #
#  chapter, and a decoy heading 8481 (valves) in a DIFFERENT chapter.         #
# --------------------------------------------------------------------------- #
def _make_taric(tmp_path) -> sqlite3.Connection:
    db = tmp_path / "taric_fixture.sqlite"
    c = sqlite3.connect(db)
    c.executescript("""
    CREATE TABLE goods_nomenclature (
        sid INTEGER PRIMARY KEY, item_id TEXT, producline_suffix TEXT,
        is_leaf INTEGER, validity_start TEXT);
    CREATE TABLE goods_nomenclature_indent (sid INTEGER, indent_level INTEGER);
    CREATE TABLE goods_nomenclature_description (
        sid INTEGER, description TEXT, validity_start TEXT);
    """)

    rows = []          # (item_id, indent, is_leaf, description)
    # Heading 9403 — base + 14 immediate children (> MAX_CANDIDATES, to exercise
    # the cap), one of which is the "Parts" subheading that sorts last.
    rows.append(("9403000000", 0, 0, "Other furniture and parts thereof"))
    furniture = [
        ("9403100000", "Metal furniture of a kind used in offices"),
        ("9403200000", "Other metal furniture"),
        ("9403300000", "Wooden furniture of a kind used in offices"),
        ("9403400000", "Wooden furniture of a kind used in the kitchen"),
        ("9403500000", "Wooden furniture of a kind used in the bedroom"),
        ("9403600000", "Other wooden furniture"),
        ("9403700000", "Furniture of plastics"),
        ("9403810000", "Furniture of bamboo"),
        ("9403820000", "Furniture of rattan"),
        ("9403830000", "Furniture of other materials"),
        ("9403891000", "Furniture of cane / osier"),
        ("9403892000", "Furniture of other materials, n.e.s."),
        ("9403990000", "Parts of furniture, other"),
        ("9403910000", "Parts of furniture"),
    ]
    for item, desc in furniture:
        rows.append((item, 1, 1, desc))
    # Decoy heading 9401 (seats) — same chapter, must NOT appear for 9403.
    rows.append(("9401000000", 0, 0, "Seats"))
    rows.append(("9401300000", 1, 1, "Swivel seats with variable height"))
    # Decoy heading 8481 (valves) — different chapter, must NOT appear for 9403.
    rows.append(("8481000000", 0, 0, "Taps, cocks, valves"))
    rows.append(("8481100000", 1, 1, "Pressure-reducing valves"))

    for sid, (item, indent, leaf, desc) in enumerate(rows, start=1):
        c.execute("INSERT INTO goods_nomenclature VALUES (?,?,?,?,?)",
                  (sid, item, "80", leaf, "2020-01-01"))
        c.execute("INSERT INTO goods_nomenclature_indent VALUES (?,?)", (sid, indent))
        c.execute("INSERT INTO goods_nomenclature_description VALUES (?,?,?)",
                  (sid, desc, "2020-01-01"))
    c.commit()
    return c


@pytest.fixture
def taric(tmp_path):
    conn = _make_taric(tmp_path)
    yield conn
    conn.close()


# --------------------------------------------------------------------------- #
#  PATH (b) — the non-negotiable scoping assertion                            #
# --------------------------------------------------------------------------- #
def test_path_b_scoped_bounded_and_heading_prefixed(taric):
    # A pre-classify freeze (no code-bearing options) with a stated furniture
    # HS code 9403 — the only scope signal. Must scope to heading 9403.
    frozen = {
        "engine_state_snapshot": {"kind": "pre_classify"},
        "hs_code_attempted": "9403",
        "partial_heading": None,
        "description_used": "Parts and accessories",
    }
    cands = sc.candidates_for_frozen_line(frozen, conn=taric)
    codes = [c["code"] for c in cands]

    # bounded
    assert 0 < len(cands) <= sc.MAX_CANDIDATES, codes
    # every code shares the reached-heading prefix
    assert all(c.startswith("9403") for c in codes), codes
    # explicitly NOT the whole chapter / a grab-bag: no same-chapter other
    # heading (9401) and no other-chapter decoy (8481).
    assert not any(c.startswith("9401") for c in codes), codes
    assert not any(c.startswith("8481") for c in codes), codes
    # descriptions came from the DB (readable, non-empty)
    assert all(c["official_description"] for c in cands)


def test_path_b_caps_at_max(taric):
    # Heading 9403 has 9 immediate children; the lookup must cap at MAX_CANDIDATES.
    frozen = {"engine_state_snapshot": {"kind": "pre_classify"},
              "hs_code_attempted": "9403910000"}  # 10-digit stated code -> heading 9403
    cands = sc.candidates_for_frozen_line(frozen, conn=taric)
    assert len(cands) == sc.MAX_CANDIDATES


def test_partial_heading_scopes_when_present(taric):
    frozen = {"engine_state_snapshot": {"kind": "pre_classify"},
              "partial_heading": "9403", "hs_code_attempted": None}
    codes = [c["code"] for c in sc.candidates_for_frozen_line(frozen, conn=taric)]
    assert codes and all(c.startswith("9403") for c in codes)


# --------------------------------------------------------------------------- #
#  PATH (a) — engine node siblings ARE the codes                              #
# --------------------------------------------------------------------------- #
def test_path_a_uses_option_map_codes(taric):
    # A node freeze whose option ids are real codes ("item_id:suffix").
    frozen = {
        "engine_state_snapshot": {
            "kind": "node",
            "option_map": {
                "Parts of furniture": "9403910000:80",
                "Furniture of plastics": "9403700000:80",
            }},
        "hs_code_attempted": None,
    }
    cands = sc.candidates_for_frozen_line(frozen, conn=taric)
    codes = sorted(c["code"] for c in cands)
    assert codes == ["9403700000", "9403910000"]
    # DB description attached (not the raw option label) when available
    by_code = {c["code"]: c["official_description"] for c in cands}
    assert by_code["9403910000"] == "Parts of furniture"


def test_path_a_heading_stage_4digit_ids(taric):
    frozen = {"engine_state_snapshot": {
        "kind": "node", "option_map": {"Furniture": "9403", "Seats": "9401"}}}
    codes = sorted(c["code"] for c in sc.candidates_for_frozen_line(frozen, conn=taric))
    assert codes == ["9401", "9403"]


# --------------------------------------------------------------------------- #
#  Fail-safe — no scope -> empty -> (generator yields candidates_mismatch)    #
# --------------------------------------------------------------------------- #
def test_no_scope_returns_empty(taric):
    frozen = {"engine_state_snapshot": {"kind": "pre_classify"},
              "hs_code_attempted": None, "partial_heading": None,
              "description_used": "misc goods"}
    assert sc.candidates_for_frozen_line(frozen, conn=taric) == []


def test_empty_candidates_feed_mismatch():
    # Reuse the generator's contract: [] candidates -> candidates_mismatch.
    import survey_question_generator as sqg
    out = sqg.generate_survey_question(
        {"survey_locale": "en",
         "line": {"line_number": 6, "description_verbatim": "Parts and accessories"},
         "candidate_codes": []})
    assert out["status"] == "candidates_mismatch"
