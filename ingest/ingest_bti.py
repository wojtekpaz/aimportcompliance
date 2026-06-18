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


def _iter_csv_streams(input_path: Path):
    """Yield (name, text_stream) for each CSV in chronological order.

    Handles three cases:
      - a zip containing multiple CSVs (sorted alphabetically = chronologically)
      - a zip containing a single CSV
      - a plain .csv file
    Processing in year order means newer rows overwrite older ones for the
    same (bti_reference, language) primary key, so the DB reflects the most
    recent known state of every ruling.
    """
    if input_path.suffix.lower() == ".zip":
        zf = zipfile.ZipFile(input_path)
        csv_names = sorted(
            n for n in zf.namelist()
            if n.endswith(".csv") and not n.startswith("__MACOSX")
        )
        if not csv_names:
            raise FileNotFoundError(f"No .csv found inside {input_path}")
        for name in csv_names:
            raw = zf.open(name)
            yield name, io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
    else:
        yield input_path.name, open(input_path, encoding="utf-8-sig", newline="")


def _ingest_stream(conn, fh, name, counters):
    """Stream one CSV file into the open DB connection, updating counters in place."""
    reader = csv.DictReader(fh)
    reader.fieldnames = [n.strip().lstrip("﻿") for n in (reader.fieldnames or [])]

    BATCH = 2000
    batch: list[tuple] = []

    def flush():
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
        counters["inserted"] += len(batch)
        batch.clear()

    file_read = 0
    for row in reader:
        counters["total_read"] += 1
        file_read += 1
        lang = (row.get("LANGUAGE") or "").strip().upper()
        if lang not in KEEP_LANGUAGES:
            counters["skipped_lang"] += 1
            continue

        code = _normalise_code(row.get("NOMENCLATURE_CODE", ""))
        if not code:
            counters["skipped_lang"] += 1
            continue

        status = (row.get("STATUS") or "").strip().upper()
        counters["by_status"][status] = counters["by_status"].get(status, 0) + 1
        counters["by_lang"][lang]     = counters["by_lang"].get(lang, 0) + 1

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

    flush()
    return file_read


def ingest(input_path: Path, output_path: Path) -> None:
    print(f"Input : {input_path}")
    print(f"Output: {output_path}")

    # ---- set up database ---------------------------------------------------
    conn = sqlite3.connect(output_path)
    conn.executescript(DDL)
    conn.execute("DELETE FROM bti_reference")   # full refresh (once, before all files)
    conn.commit()

    # ---- counters ----------------------------------------------------------
    counters = {
        "total_read": 0, "skipped_lang": 0, "inserted": 0,
        "by_lang": {}, "by_status": {},
    }

    # ---- stream each CSV in chronological order ----------------------------
    for name, fh in _iter_csv_streams(input_path):
        before = counters["inserted"]
        n = _ingest_stream(conn, fh, name, counters)
        added = counters["inserted"] - before
        print(f"  {name}: {n:,} rows → {added:,} upserted")

    # ---- prune INVALID rulings and compact ---------------------------------
    # We stored all statuses during ingestion (so chronological overwriting
    # correctly reflects the current state of each ruling), but INVALID
    # rulings are never surfaced by bti_for_code() and only waste space.
    print("  Pruning INVALID rulings…")
    conn.execute("DELETE FROM bti_reference WHERE status != 'VALID'")
    conn.commit()
    valid_count = conn.execute("SELECT COUNT(*) FROM bti_reference").fetchone()[0]
    print("  Running VACUUM…")
    conn.execute("VACUUM")
    conn.close()

    # ---- summary -----------------------------------------------------------
    print(f"\n{'─'*52}")
    print(f"Rows read        : {counters['total_read']:>10,}")
    print(f"Skipped (lang)   : {counters['skipped_lang']:>10,}")
    print(f"Inserted/replaced: {counters['inserted']:>10,}")
    print(f"VALID kept       : {valid_count:>10,}")
    print(f"\nBy language (all rows, before prune):")
    for lang in sorted(counters["by_lang"]):
        print(f"  {lang:4s}: {counters['by_lang'][lang]:,}")
    db_mb = output_path.stat().st_size / 1024 / 1024
    print(f"\nDatabase size    : {db_mb:.0f} MB  (VALID rulings only)")
    print(f"{'─'*52}")
    print(f"Done → {output_path}")


if __name__ == "__main__":
    inp = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INPUT
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUTPUT
    if not inp.exists():
        print(f"ERROR: input file not found: {inp}", file=sys.stderr)
        sys.exit(1)
    ingest(inp, out)
