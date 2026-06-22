"""Polish national tariff data (ISZTAR4) — deterministic LOCAL lookup.

Phase 2 of the PL market profile. This module reads ONLY from the local
SQLite cache (``data_isztar_pl.sqlite``). It never imports networking and
never calls out — the ingestion step (``isztar_ingest.py``) is the only thing
that touches the live ISZTAR4 API. Per the master prompt, ISZTAR is a
deterministic lookup keyed by code, not an LLM search.

NOTE: not wired into the classifier or landed-cost yet (that is Phase 4).
"""
import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get(
    "ISZTAR_PL_DB",
    Path(__file__).resolve().parent / "data_isztar_pl.sqlite",
))

SCHEMA = """
CREATE TABLE IF NOT EXISTS isztar_nomenclature_pl (
  code        TEXT NOT NULL,
  valid_date  TEXT NOT NULL,
  description TEXT,
  supplementary_unit TEXT,
  PRIMARY KEY (code, valid_date)
);
CREATE TABLE IF NOT EXISTS isztar_taxes_pl (
  code        TEXT NOT NULL,
  valid_date  TEXT NOT NULL,
  tax_type    TEXT NOT NULL,            -- VAT | EXCISE | OTHER
  description TEXT,
  duty_amount TEXT,
  duty_amount_with_codes TEXT,
  additional_code      TEXT,
  additional_code_desc TEXT,
  country     TEXT
);
CREATE TABLE IF NOT EXISTS isztar_national_measures_pl (
  code        TEXT NOT NULL,
  valid_date  TEXT NOT NULL,
  description TEXT,
  country     TEXT,
  regulation  TEXT
);
CREATE INDEX IF NOT EXISTS ix_isztar_tax_code ON isztar_taxes_pl(code, valid_date);
CREATE INDEX IF NOT EXISTS ix_isztar_nat_code ON isztar_national_measures_pl(code, valid_date);
"""


def connect(db_path=None):
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def normalize_code(code):
    """10-digit, digits only, zero-padded (ISZTAR keys on a 10-digit code)."""
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    return digits.zfill(10)[:10]


def _resolve_valid_date(conn, code, date):
    """Exact validity date if present, else the most recent snapshot on/before it."""
    row = conn.execute(
        "SELECT 1 FROM isztar_nomenclature_pl WHERE code=? AND valid_date=?",
        (code, date)).fetchone()
    if row:
        return date
    row = conn.execute(
        "SELECT valid_date FROM isztar_nomenclature_pl "
        "WHERE code=? AND valid_date<=? ORDER BY valid_date DESC LIMIT 1",
        (code, date)).fetchone()
    if row:
        return row["valid_date"]
    # nomenclature row may be absent even if taxes were cached
    row = conn.execute(
        "SELECT valid_date FROM isztar_taxes_pl "
        "WHERE code=? AND valid_date<=? ORDER BY valid_date DESC LIMIT 1",
        (code, date)).fetchone()
    return row["valid_date"] if row else None


def get_pl_national_measures(code, date, db_path=None):
    """Deterministic, local-only lookup of Polish national measures.

    Returns a dict with the Polish nomenclature description, VAT entries,
    excise entries, and national non-tariff measures for ``code`` at ``date``
    (YYYY-MM-DD). Reads only the local SQLite cache — no network access.
    """
    code = normalize_code(code)
    conn = connect(db_path)
    try:
        ensure_schema(conn)
        def _latest(table):
            row = conn.execute(
                f"SELECT valid_date FROM {table} WHERE code=? AND valid_date<=? "
                f"ORDER BY valid_date DESC LIMIT 1", (code, date)).fetchone()
            return row["valid_date"] if row else None

        # Resolve each data type INDEPENDENTLY by date: Polish nomenclature
        # descriptions (bulk SXML load, e.g. 2026-01-01) and VAT/excise/national
        # measures (per-code API ingest) can carry different validity dates.
        vd_nom = _latest("isztar_nomenclature_pl")
        vd_tax = _latest("isztar_taxes_pl")
        vd_nat = _latest("isztar_national_measures_pl")
        result = {
            "code": code,
            "requested_date": date,
            "valid_date": vd_nom or vd_tax or vd_nat,
            "source": f"local:{Path(db_path) if db_path else DB_PATH}",
            "found": any([vd_nom, vd_tax, vd_nat]),
            "description_pl": None,
            "vat": [],
            "vat_standard": None,
            "excise": [],
            "national_measures": [],
        }
        if not result["found"]:
            return result

        if vd_nom:
            nom = conn.execute(
                "SELECT description FROM isztar_nomenclature_pl WHERE code=? AND valid_date=?",
                (code, vd_nom)).fetchone()
            if nom:
                result["description_pl"] = nom["description"]

        if vd_tax:
            for t in conn.execute(
                    "SELECT * FROM isztar_taxes_pl WHERE code=? AND valid_date=?", (code, vd_tax)):
                entry = {
                    "description": t["description"],
                    "rate": t["duty_amount"],
                    "rate_coded": t["duty_amount_with_codes"],
                    "additional_code": t["additional_code"],
                    "condition": t["additional_code_desc"],
                    "country": t["country"],
                }
                if t["tax_type"] == "VAT":
                    result["vat"].append(entry)
                elif t["tax_type"] == "EXCISE":
                    result["excise"].append(entry)

        if vd_nat:
            for m in conn.execute(
                    "SELECT * FROM isztar_national_measures_pl WHERE code=? AND valid_date=?",
                    (code, vd_nat)):
                result["national_measures"].append({
                    "description": m["description"],
                    "country": m["country"],
                    "regulation": m["regulation"],
                })

        # The standard VAT rate is the residual additional code (V999 = "Pozostałe").
        std = [v for v in result["vat"] if v["additional_code"] == "V999"]
        result["vat_standard"] = (std[0]["rate"] if std
                                  else (result["vat"][0]["rate"] if result["vat"] else None))
        return result
    finally:
        conn.close()


if __name__ == "__main__":
    import json
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "2208601100"
    date = sys.argv[2] if len(sys.argv) > 2 else "2025-06-02"
    print(json.dumps(get_pl_national_measures(code, date), ensure_ascii=False, indent=2))
