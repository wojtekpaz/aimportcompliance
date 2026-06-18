#!/usr/bin/env python3
"""
bti_lookup.py — Retrieve Binding Tariff Information rulings for a TARIC code.

LOOKUP LOGIC:
  1. Exact 10-digit code match (match_type = 'exact').
  2. If none, 8-digit prefix match (match_type = 'related_subheading').
  3. If none, 6-digit prefix match (match_type = 'related_heading').
  Results from a more specific level always take priority over a broader one.

LANGUAGE PREFERENCE:
  A single BTI ruling can appear in multiple language rows.
  bti_for_code() groups by bti_reference and returns one row per ruling,
  preferring lang_pref → EN → any available.

FILTERING:
  Only VALID rows are returned. INVALID rulings are never surfaced.

DEPENDENCIES:
  Pure sqlite3 + stdlib — no LLM calls, no external packages.
"""
import re
import sqlite3
from pathlib import Path

BTI_DB = Path(__file__).resolve().parent.parent / "bti.sqlite"

# Match-type labels surfaced to callers / UI
_MATCH_LABELS = {0: "exact", 1: "related_subheading", 2: "related_heading"}


def _date_sort_key(iso_date: str | None) -> int:
    """YYYY-MM-DD → integer YYYYMMDD for descending sort (negate to sort DESC)."""
    try:
        return -int((iso_date or "").replace("-", "")[:8])
    except (ValueError, TypeError):
        return 0


def bti_for_code(code: str, lang_pref: str = "EN", limit: int = 5) -> list:
    """Return up to `limit` VALID BTI rulings for `code`.

    Tries exact 10-digit match, then 8-digit prefix, then 6-digit prefix.
    Groups by bti_reference; picks the best-language row per ruling.
    Returns [] if bti.sqlite does not exist or no VALID rulings are found.
    """
    if not BTI_DB.exists():
        return []

    digits = re.sub(r"\D", "", code or "")
    if not digits:
        return []

    code10  = digits.ljust(10, "0")[:10]
    prefix8 = code10[:8]
    prefix6 = code10[:6]

    try:
        conn = sqlite3.connect(BTI_DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT *,
                CASE
                    WHEN code = :c10 THEN 0
                    WHEN substr(code, 1, 8) = :p8 THEN 1
                    WHEN substr(code, 1, 6) = :p6 THEN 2
                    ELSE 9
                END AS match_rank
            FROM bti_reference
            WHERE status = 'VALID'
              AND (code = :c10
                   OR substr(code, 1, 8) = :p8
                   OR substr(code, 1, 6) = :p6)
            ORDER BY match_rank, valid_from DESC
        """, {"c10": code10, "p8": prefix8, "p6": prefix6}).fetchall()
        conn.close()
    except Exception:
        return []

    if not rows:
        return []

    # --- group by bti_reference, pick best language per ruling ---------------
    # Priority: lang_pref=0, EN=1, anything else=99
    lang_prio: dict[str, int] = {lang_pref: 0, "EN": 1}
    if lang_pref == "EN":
        lang_prio = {"EN": 0}   # collapse to single priority

    grouped: dict[str, dict] = {}
    for row in rows:
        ref   = row["bti_reference"]
        lang  = (row["language"] or "").upper()
        rank  = row["match_rank"]
        lscore = lang_prio.get(lang, 99)

        if ref not in grouped:
            grouped[ref] = {"row": row, "rank": rank, "lscore": lscore}
        else:
            cur = grouped[ref]
            # prefer lower match_rank first, then better language score
            if rank < cur["rank"] or (rank == cur["rank"] and lscore < cur["lscore"]):
                grouped[ref] = {"row": row, "rank": rank, "lscore": lscore}

    # --- sort: exact first, then newest valid_from within each match level ---
    sorted_items = sorted(
        grouped.values(),
        key=lambda x: (x["rank"], _date_sort_key(x["row"]["valid_from"])),
    )

    # --- build output dicts --------------------------------------------------
    result = []
    for item in sorted_items[:limit]:
        row = item["row"]
        result.append({
            "bti_reference":   row["bti_reference"],
            "code":            row["code"],
            "valid_from":      row["valid_from"] or "",
            "valid_to":        row["valid_to"]   or "",
            "status":          row["status"]     or "",
            "language":        row["language"]   or "",
            "country":         row["country"]    or "",
            "description":     row["description"]     or "",
            "gri_justification": row["gri_justification"] or "",
            "keywords":        row["keywords"]   or "",
            "match_type":      _MATCH_LABELS.get(item["rank"], "related"),
        })

    return result
