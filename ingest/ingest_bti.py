#!/usr/bin/env python3
"""
ingest_bti.py — Parse EU EBTI CSV export into bti.sqlite.

Source format (confirmed from EBTI_2025.csv):
  - UTF-8 with BOM
  - Comma-delimited, all fields double-quoted
  - One combined file for all languages (LANGUAGE column: EN, PL, DE, FR, …)
  - NOMENCLATURE_CODE padded with asterisks after digits (e.g. 8513100000**)
  - Dates in DD/MM/YYYY format → normalised to YYYY-MM-DD on ingestion

Usage:
    python3 ingest/ingest_bti.py [input] [output]

    input  : path to .csv or .zip (default: EBTI_2025.csv.zip in project root)
    output : path to output SQLite DB (default: bti.sqlite in project root)

Only EN, PL, DE rows are ingested; all other languages are skipped.
"""
import csv
import io
import re
import sqlite3
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_INPUT  = ROOT / "EBTI_2025.csv.zip"
DEFAULT_OUTPUT = ROOT / "bti.sqlite"

KEEP_LANGUAGES = {"EN", "PL", "DE"}

DDL = """
CREATE TABLE IF NOT EXISTS bti_reference (
    bti_reference     TEXT NOT NULL,
    language          TEXT NOT NULL,
    code              TEXT NOT NULL,
    valid_from        TEXT,
    valid_to          TEXT,
    status            TEXT,
    country           TEXT,
    description       TEXT,
    gri_justification TEXT,
    keywords          TEXT,
    invalidation_reason TEXT,
    PRIMARY KEY (bti_reference, language)
);
CREATE INDEX IF NOT EXISTS idx_bti_code ON bti_reference (code);
CREATE INDEX IF NOT EXISTS idx_bti_ref  ON bti_reference (bti_reference);
CREATE INDEX IF NOT EXISTS idx_bti_status ON bti_reference (status);
"""


def _normalise_code(raw: str) -> str:
    """Strip non-digit characters (asterisks, spaces) and keep up to 10 digits."""
    return re.sub(r"\D", "", raw or "")[:10]


def _normalise_date(raw: str) -> str:
    """Convert DD/MM/YYYY → YYYY-MM-DD; return '' on failure."""
    s = (raw or "").strip()
    if not s:
        return ""
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s   # fall through unchanged if no format matched


def _open_csv(input_path: Path):
    """Yield a text stream for the CSV, handling .zip or plain .csv."""
    if input_path.suffix.lower() == ".zip":
        zf = zipfile.ZipFile(input_path)
        # Find the first .csv entry (skip macOS __MACOSX artefacts)
        csv_names = [n for n in zf.namelist()
                     if n.endswith(".csv") and not n.startswith("__MACOSX")]
        if not csv_names:
            raise FileNotFoundError(f"No .csv found inside {input_path}")
        raw = zf.open(csv_names[0])
        return io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
    else:
        return open(input_path, encoding="utf-8-sig", newline="")


def ingest(input_path: Path, output_path: Path) -> None:
    print(f"Input : {input_path}")
    print(f"Output: {output_path}")

    # ---- set up database ---------------------------------------------------
    conn = sqlite3.connect(output_path)
    conn.executescript(DDL)
    conn.execute("DELETE FROM bti_reference")   # full refresh
    conn.commit()

    # ---- counters ----------------------------------------------------------
    total_read   = 0
    skipped_lang = 0
    inserted     = 0
    replaced     = 0
    by_lang:  dict[str, int] = {}
    by_status: dict[str, int] = {}

    BATCH = 2000
    batch: list[tuple] = []

    def flush():
        nonlocal inserted, replaced
        if not batch:
            return
        conn.executemany("""
            INSERT OR REPLACE INTO bti_reference
                (bti_reference, language, code, valid_from, valid_to,
                 status, country, description, gri_justification,
                 keywords, invalidation_reason)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, batch)
        conn.commit()
        inserted += len(batch)
        batch.clear()

    # ---- stream the CSV ----------------------------------------------------
    with _open_csv(input_path) as fh:
        reader = csv.DictReader(fh)
        # Strip BOM/whitespace from column names defensively
        reader.fieldnames = [n.strip().lstrip("﻿") for n in (reader.fieldnames or [])]

        for row in reader:
            total_read += 1
            lang = (row.get("LANGUAGE") or "").strip().upper()
            if lang not in KEEP_LANGUAGES:
                skipped_lang += 1
                continue

            code = _normalise_code(row.get("NOMENCLATURE_CODE", ""))
            if not code:
                skipped_lang += 1   # no usable code
                continue

            status = (row.get("STATUS") or "").strip().upper()
            by_status[status] = by_status.get(status, 0) + 1
            by_lang[lang]     = by_lang.get(lang, 0) + 1

            batch.append((
                (row.get("BTI_REFERENCE") or "").strip(),
                lang,
                code,
                _normalise_date(row.get("START_DATE_OF_VALIDITY", "")),
                _normalise_date(row.get("END_DATE_OF_VALIDITY", "")),
                status,
                (row.get("ISSUING_COUNTRY") or "").strip().upper(),
                (row.get("DESCRIPTION_OF_GOODS") or "").strip(),
                (row.get("CLASSIFICATION_JUSTIFICATION") or "").strip(),
                (row.get("KEYWORDS") or "").strip(),
                (row.get("INVALIDATION_REASON") or "").strip(),
            ))

            if len(batch) >= BATCH:
                flush()

            if total_read % 50_000 == 0:
                print(f"  … {total_read:,} rows read, {inserted:,} inserted so far")

    flush()
    conn.close()

    # ---- summary -----------------------------------------------------------
    print(f"\n{'─'*52}")
    print(f"Rows read        : {total_read:>10,}")
    print(f"Skipped (lang)   : {skipped_lang:>10,}")
    print(f"Inserted/replaced: {inserted:>10,}")
    print(f"\nBy language:")
    for lang in sorted(by_lang):
        print(f"  {lang:4s}: {by_lang[lang]:,}")
    print(f"\nBy status:")
    for st in sorted(by_status):
        print(f"  {st or '(empty)':20s}: {by_status[st]:,}")
    print(f"{'─'*52}")
    print(f"Done → {output_path}")


if __name__ == "__main__":
    inp = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INPUT
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUTPUT
    if not inp.exists():
        print(f"ERROR: input file not found: {inp}", file=sys.stderr)
        sys.exit(1)
    ingest(inp, out)
