#!/usr/bin/env python3
"""survey_review.py — persist and read survey review flags.

A flag is raised when the survey question generator returns a status that means
"do not send this line to the client" (candidates_mismatch / extraction_suspect).
This module is the home those flags land in so a human can see and count them.
It makes no classification decisions and calls no model.

The flags live in the SAME SQLite file survey_db.py already manages (the
AIMPORT_DATA_DIR-rooted user-data DB, never the tariff DB). We reuse survey_db's
single connection helper so there is one DB-path source of truth, and ensure the
table idempotently on every connection (cheap CREATE TABLE IF NOT EXISTS) so the
feature is self-contained and survives a monkeypatched test DB path.
"""
from __future__ import annotations

import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

sys.path.insert(0, str(Path(__file__).resolve().parent))
import survey_db as sdb  # noqa: E402  (reuse its DB-path + connection helper)

# Constrained to exactly the two generator statuses that must NOT become a
# client survey. We never silently store an unknown flag type.
_ALLOWED_FLAG_TYPES = {"candidates_mismatch", "extraction_suspect"}

_COLS = ["id", "created_at", "broker_id", "invoice_ref", "line_number",
         "description", "flag_type", "field", "observation", "status"]


def _ensure_table(c: sqlite3.Connection) -> None:
    c.executescript("""
    CREATE TABLE IF NOT EXISTS survey_review_flags (
        id            TEXT PRIMARY KEY,
        created_at    DATETIME NOT NULL,
        broker_id     TEXT,
        invoice_ref   TEXT,
        line_number   INTEGER,
        description   TEXT,
        flag_type     TEXT NOT NULL,
        field         TEXT,
        observation   TEXT,
        status        TEXT DEFAULT 'open'
    );
    CREATE INDEX IF NOT EXISTS idx_srf_status ON survey_review_flags(status);
    CREATE INDEX IF NOT EXISTS idx_srf_broker ON survey_review_flags(broker_id);
    """)
    c.commit()


def _conn() -> sqlite3.Connection:
    """Same DB file + row_factory as survey_db, with the flags table ensured."""
    c = sdb._conn()
    _ensure_table(c)
    return c


# --------------------------------------------------------------------------- #
#  Write / read                                                                #
# --------------------------------------------------------------------------- #
def raise_flag(*, broker_id: str, invoice_ref: str, line_number,
               description: str, flag_type: str, observation: str,
               field: str = "") -> dict:
    """Persist one review flag. flag_type MUST be one of the two generator
    statuses; anything else is a programming error and is rejected (we never
    silently store an unknown flag type)."""
    if flag_type not in _ALLOWED_FLAG_TYPES:
        raise ValueError(f"unknown flag_type: {flag_type!r}")
    fid = uuid.uuid4().hex
    ln = None
    if line_number is not None and str(line_number).strip().lstrip("-").isdigit():
        ln = int(line_number)
    c = _conn()
    c.execute(
        "INSERT INTO survey_review_flags "
        "(id, created_at, broker_id, invoice_ref, line_number, description, "
        " flag_type, field, observation, status) "
        "VALUES (?,?,?,?,?,?,?,?,?, 'open')",
        (fid, datetime.utcnow().isoformat(sep=" ", timespec="seconds"),
         broker_id or "", invoice_ref or "", ln,
         description or "", flag_type, field or "", observation or ""))
    c.commit()
    c.close()
    return {"id": fid}


def list_flags(*, broker_id: str = "", status: str = "open",
               limit: int = 200) -> list[dict]:
    """Read flags for the review list, newest first. Defaults to open flags.
    The rowid tiebreak keeps insertion order stable within the same second."""
    c = _conn()
    rows = c.execute(
        "SELECT id, created_at, broker_id, invoice_ref, line_number, "
        "       description, flag_type, field, observation, status "
        "FROM survey_review_flags "
        "WHERE (? = '' OR broker_id = ?) AND (? = '' OR status = ?) "
        "ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (broker_id, broker_id, status, status, int(limit))).fetchall()
    c.close()
    return [dict(zip(_COLS, tuple(r))) for r in rows]


def mark_reviewed(flag_id: str) -> dict:
    c = _conn()
    c.execute("UPDATE survey_review_flags SET status='reviewed' WHERE id=?",
              (flag_id,))
    c.commit()
    c.close()
    return {"id": flag_id, "status": "reviewed"}


# --------------------------------------------------------------------------- #
#  Minimal server-side rendered review page (one template, no new dependency)  #
#  Copy register: expert customs-professional, EN/PL per resolved UI locale.   #
# --------------------------------------------------------------------------- #
_LABELS = {
    "en": {
        "title": "Lines for review",
        "subtitle": "Lines held back from the client survey. Confirm the item, then mark reviewed.",
        "empty": "No lines awaiting review.",
        "col_invoice": "Invoice", "col_line": "Line", "col_desc": "Description",
        "col_type": "Flag", "col_field": "Field", "col_obs": "Observation",
        "col_time": "Flagged (UTC)", "mark": "Mark reviewed",
        "candidates_mismatch": "Candidate codes did not fit the line",
        "extraction_suspect": "Extraction looked wrong",
    },
    "pl": {
        "title": "Pozycje do przeglądu",
        "subtitle": "Pozycje wstrzymane przed wysłaniem ankiety do klienta. Potwierdź towar, następnie oznacz jako sprawdzone.",
        "empty": "Brak pozycji oczekujących na przegląd.",
        "col_invoice": "Faktura", "col_line": "Pozycja", "col_desc": "Opis",
        "col_type": "Flaga", "col_field": "Pole", "col_obs": "Obserwacja",
        "col_time": "Oznaczono (UTC)", "mark": "Oznacz jako sprawdzone",
        "candidates_mismatch": "Kody kandydujące nie pasowały do pozycji",
        "extraction_suspect": "Odczyt wygląda na błędny",
    },
}


# Jinja2 environment with autoescaping ON for .html — every {{ value }} in the
# template is HTML-escaped by the engine, so fields added to the template later
# are safe by default (no per-field html.escape() to forget). Nothing uses |safe.
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


def render_review_page(flags: list[dict], locale: str = "en") -> str:
    """Render the open-flags list via the autoescaped Jinja2 template — testable
    in both locales without a browser. No JS; the mark-reviewed action is a plain
    form POST (a human-initiated, non-destructive status flip)."""
    L = _LABELS.get(locale, _LABELS["en"])
    rows = [{**f, "type_label": L.get(f.get("flag_type", ""), f.get("flag_type", ""))}
            for f in flags]
    return _env.get_template("survey_review.html").render(
        rows=rows, L=L, locale=locale)
