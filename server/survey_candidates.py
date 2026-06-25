#!/usr/bin/env python3
"""survey_candidates.py — resolve {code, official_description} candidates for a
frozen invoice line, for the survey question generator.

Two deterministic sources, chosen by the nature of the freeze. NEITHER invents a
code; both read only from the TARIC nomenclature DB (data_taric.sqlite):

  (a) ENGINE AMBIGUOUS-NODE SIBLINGS. When the GRI engine froze the line at a
      node (kind == "node"), its option ids ARE nomenclature codes (the heading
      stage yields 4-digit heading ids; the subheading stage yields
      "item_id:suffix"). Those codes ARE the candidates — the engine has already
      narrowed to a relevant set, so they are guaranteed scoped. Descriptions
      are attached from the DB.

  (b) SCOPED DB LOOKUP. When the freeze carries no code-bearing siblings
      (pre-classify / missing-description / OCR-confirm freezes), look up the
      immediate subheadings under the heading the engine reached, or the heading
      of the stated HS code. Scope is the single reached heading — NEVER the
      whole chapter and never a keyword grab-bag (that is exactly what produced
      valve/cask/colourant candidates for a "Parts and accessories" line). The
      set is bounded; every code shares the reached-heading prefix.

If neither source yields anything (e.g. a vague pre-classify freeze with no
stated code and no reached heading), return [] — the generator turns an empty
candidate list into candidates_mismatch, which routes the line to human review.
That honest fail-safe is correct; we never fabricate candidates to fill the gap.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
from tree import first_level_children  # noqa: E402  (engine's own scoping logic)

# Bound on a path-(b) scoped lookup — a sanity cap against a pathologically broad
# heading, NOT a tight filter. It must comfortably cover a real heading's full
# set of immediate subheadings (e.g. 9403 has 9, including the "Parts"
# subheading that sorts last); too low a cap silently drops the very subheading a
# "Parts and accessories" line needs. Scope is still ONE heading, never a chapter.
MAX_CANDIDATES = 12


def candidates_for_frozen_line(frozen_line: dict, *, conn) -> list[dict]:
    """Return [{"code": <str, from DB>, "official_description": <str, from DB>}],
    deduplicated and order-stable. See module docstring for the (a)/(b) rule."""
    snap = frozen_line.get("engine_state_snapshot") or {}
    kind = snap.get("kind")
    option_map = snap.get("option_map") or {}

    # ---- Path (a): engine node freeze — option ids are nomenclature codes ----
    if kind == "node" and option_map:
        cands = _from_option_map(option_map, conn)
        if cands:
            return cands
        # else fall through: a node freeze whose options were the fixed
        # MISSING_DESC prompts (no codes) still has a reached heading to scope to.

    # ---- Path (b): no code-bearing siblings — scoped lookup under heading ----
    heading4 = _reached_heading(frozen_line)
    if heading4:
        return _scoped_lookup(conn, heading4)

    return []  # no scope -> empty -> generator yields candidates_mismatch (review)


# --------------------------------------------------------------------------- #
#  Path (a)                                                                    #
# --------------------------------------------------------------------------- #
def _from_option_map(option_map: dict, conn) -> list[dict]:
    out: list[dict] = []
    seen: set = set()
    for text, oid in option_map.items():
        code = str(oid).split(":", 1)[0].strip()  # "9403910000:80" -> "9403910000"
        if not code.isdigit() or code in seen:
            continue
        seen.add(code)
        out.append({"code": code,
                    "official_description": _official_description(conn, code) or
                    _clean(text)})
    return out


# --------------------------------------------------------------------------- #
#  Path (b)                                                                    #
# --------------------------------------------------------------------------- #
def _reached_heading(frozen_line: dict) -> str | None:
    """The single heading to scope a lookup to: the heading the engine reached
    (partial_heading, from the engine signature) or, failing that, the heading
    of the HS code stated on the invoice. 4 digits, numeric, or None."""
    ph = (frozen_line.get("partial_heading") or "").strip()
    if len(ph) >= 4 and ph[:4].isdigit():
        return ph[:4]
    hs = frozen_line.get("hs_code_attempted") or ""
    digits = "".join(ch for ch in str(hs) if ch.isdigit())
    if len(digits) >= 4:
        return digits[:4]
    return None


def _scoped_lookup(conn, heading4: str) -> list[dict]:
    """Immediate subheadings under one heading, via the engine's own child logic
    so scoping matches classification. Bounded by MAX_CANDIDATES; every code is
    asserted to share the heading prefix (never chapter-wide)."""
    out: list[dict] = []
    seen: set = set()
    for k in first_level_children(conn, heading4):
        code = str(k.get("item_id") or "")
        if not code.startswith(heading4) or code in seen:
            continue  # scope guard: only codes under THIS heading
        seen.add(code)
        out.append({"code": code,
                    "official_description": _clean(k.get("display")
                                                   or k.get("desc") or "")})
        if len(out) >= MAX_CANDIDATES:
            break
    return out


# --------------------------------------------------------------------------- #
#  DB helpers                                                                  #
# --------------------------------------------------------------------------- #
def _official_description(conn, code: str) -> str:
    """Newest nomenclature description for a code. A 4-digit heading is padded to
    its base 10-digit item_id ('9403' -> '9403000000')."""
    item_id = code if len(code) >= 10 else code.ljust(10, "0")
    row = conn.execute(
        "SELECT d.description FROM goods_nomenclature g "
        "JOIN goods_nomenclature_description d ON d.sid = g.sid "
        "WHERE g.item_id = ? ORDER BY d.validity_start DESC",
        (item_id,)).fetchone()
    return _clean(row[0]) if row and row[0] else ""


def _clean(text) -> str:
    """Collapse the pipe separators TARIC uses inside descriptions."""
    import re
    return re.sub(r"\s*\|\s*", " ", str(text or "")).strip()
