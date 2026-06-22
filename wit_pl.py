"""WIT (Wiążąca Informacja Taryfowa) — Polish view of EU binding tariff rulings.

Phase 3 of the PL market profile. WIT is the Polish term for BTI (Binding
Tariff Information). The EU publishes EBTI as periodic CSV EXPORTS (not a live
API); the project already ingests them into ``bti.sqlite`` (table
``bti_reference``, ~81k rows) via ``ingest/ingest_bti.py``. This module is a
deterministic, LOCAL-ONLY lookup over that existing store, keyed by code.

HARD RULE (master prompt): WIT is display-only EVIDENCE attached AFTER the
determination. It must NEVER enter the GRI control flow or the oracle's option
set. This module is therefore standalone — it is not imported by the
classifier, the oracle, or the prompt builder; it is reached only through the
additive ``/api/wit`` endpoint, after a result exists. It imports no networking.
"""
import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get(
    "BTI_DB", Path(__file__).resolve().parent / "bti.sqlite"))

# Prefer a Polish ruling description; fall back to EN, then DE.
_LANG_PREF = {"PL": 0, "EN": 1, "DE": 2}


def normalize_code(code):
    """Digits only. EBTI stores 8- or 10-digit codes; the engine emits 10-digit."""
    return "".join(ch for ch in str(code) if ch.isdigit())


def get_wit_rulings(code, limit=8, db_path=None, de_cap=3):
    """Return WIT/BTI rulings for a code from the local store.

    Each ruling: reference number, summary (PL-preferred description), validity
    dates (valid_from / valid_to), status, country. Deterministic, no network.
    """
    digits = normalize_code(code)
    path = Path(db_path) if db_path else DB_PATH
    out = {"code": digits, "source": f"local:{path}", "available": path.exists(),
           "rulings": []}
    if not path.exists() or not digits:
        return out

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        # Match the exact code; also try the 10- and 8-digit forms so a
        # 10-digit determination still finds 8-digit-keyed rulings.
        candidates = {digits}
        if len(digits) >= 10:
            candidates.add(digits[:10])
            candidates.add(digits[:8])
        elif len(digits) == 8:
            candidates.add(digits + "00")
        placeholders = ",".join("?" * len(candidates))
        rows = conn.execute(
            f"SELECT bti_reference, language, valid_from, valid_to, status, "
            f"country, description, keywords FROM bti_reference "
            f"WHERE code IN ({placeholders})",
            tuple(candidates)).fetchall()
    finally:
        conn.close()

    # Collapse to one entry per ruling reference, keeping the best-language
    # description (PL > EN > DE) and remembering every language a ruling carries.
    by_ref = {}
    langs_by_ref = {}
    for r in rows:
        ref = r["bti_reference"]
        lang = (r["language"] or "").upper()
        langs_by_ref.setdefault(ref, set()).add(lang)
        rank = _LANG_PREF.get(lang, 9)
        if ref not in by_ref or rank < by_ref[ref]["_rank"]:
            by_ref[ref] = {
                "_rank": rank,
                "reference": ref,
                "language": r["language"],
                "summary": r["description"],
                "keywords": r["keywords"],
                "valid_from": r["valid_from"],
                "valid_to": r["valid_to"],
                "status": r["status"],
                "country": r["country"],
            }

    def _tier(ref):
        # Polish-system priority: rulings carrying a Polish text rank first, then
        # English, then German-only last — so German references stay limited.
        ls = langs_by_ref.get(ref, set())
        if "PL" in ls:
            return 0
        if "EN" in ls:
            return 1
        return 2

    # recency first, then stable-sort by language tier (PL → EN → DE-only)
    rulings = sorted(by_ref.values(), key=lambda x: (x["valid_from"] or ""), reverse=True)
    rulings.sort(key=lambda x: _tier(x["reference"]))
    for x in rulings:
        x["available_languages"] = sorted(langs_by_ref.get(x["reference"], set()))
        x.pop("_rank", None)

    # Limit German: show all Polish/English rulings first, then at most `de_cap`
    # German-only ones (the EU EBTI database is ~91% German, so without this cap
    # most codes would be a wall of German references).
    pl_en = [r for r in rulings if _tier(r["reference"]) < 2]
    de_only = [r for r in rulings if _tier(r["reference"]) == 2]
    selected = pl_en[:limit]
    if len(selected) < limit and de_cap > 0:
        selected += de_only[:min(de_cap, limit - len(selected))]

    out["rulings"] = selected
    out["total_found"] = len(by_ref)
    out["language_counts"] = {
        "pl": sum(1 for ref in by_ref if "PL" in langs_by_ref.get(ref, set())),
        "en": sum(1 for ref in by_ref
                  if "PL" not in langs_by_ref.get(ref, set()) and "EN" in langs_by_ref.get(ref, set())),
        "de_only": sum(1 for ref in by_ref
                       if langs_by_ref.get(ref, set()) and not (langs_by_ref.get(ref, set()) & {"PL", "EN"})),
    }
    # Honest note when there is no Polish (or English) evidence for this code.
    if not pl_en and de_only:
        out["note"] = ("Brak polskich (WIT) decyzji dla tego kodu w bazie EBTI; "
                       "pokazano ograniczoną liczbę decyzji unijnych (głównie niemieckich) "
                       "jako materiał poglądowy.")
    return out


if __name__ == "__main__":
    import json
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "7326909890"
    print(json.dumps(get_wit_rulings(code), ensure_ascii=False, indent=2))
