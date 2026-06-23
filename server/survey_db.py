#!/usr/bin/env python3
"""
survey_db.py — Client-clarification survey store for AImport Compliance.

Milestone One (V2), Section 3. Holds broker-initiated survey sessions, the
frozen line-item questions within each, and the resolved results after the
client answers. Lives in the SAME user-data database as saved_products.sqlite
(never the tariff DB, which is replaced wholesale on EU updates). The engine
never reads or writes this module.

Tokens are UUIDs (never sequential, never a hash of invoice data). No invoice
commercial data is placed in any URL.
"""
import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# Same user-data DB file as saved classifications. AIMPORT_DATA_DIR (a mounted
# persistent volume in a hosted deployment) keeps survey sessions across
# redeploys; defaults to the repo root locally. Must match products_db.py.
DB_PATH = Path(os.environ.get("AIMPORT_DATA_DIR")
               or Path(__file__).resolve().parent.parent) / "saved_products.sqlite"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)   # create the volume dir if needed

SESSION_TTL_DAYS = 14


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    """Create the survey tables if absent. Safe to call on every startup."""
    c = _conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS survey_sessions (
        id          TEXT PRIMARY KEY,
        broker_id   TEXT NOT NULL,
        invoice_ref TEXT,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        expires_at  DATETIME NOT NULL,
        status      TEXT DEFAULT 'pending',
        client_email TEXT,
        notification_sent_at DATETIME,
        language    TEXT DEFAULT 'pl'
    );

    CREATE TABLE IF NOT EXISTS survey_questions (
        id              TEXT PRIMARY KEY,
        session_id      TEXT NOT NULL REFERENCES survey_sessions(id),
        line_number     INTEGER NOT NULL,
        description_used TEXT,
        hs_code_attempted TEXT,
        freeze_reason   TEXT NOT NULL,
        engine_question TEXT NOT NULL,
        option_set_json TEXT NOT NULL,
        engine_state_json TEXT NOT NULL,
        partial_heading TEXT,
        answer          TEXT,
        answer_detail   TEXT,
        answered_at     DATETIME
    );

    CREATE TABLE IF NOT EXISTS survey_results (
        id              TEXT PRIMARY KEY,
        session_id      TEXT NOT NULL REFERENCES survey_sessions(id),
        question_id     TEXT NOT NULL REFERENCES survey_questions(id),
        line_number     INTEGER NOT NULL,
        final_code      TEXT,
        still_frozen    INTEGER DEFAULT 0,
        resolution_notes TEXT,
        resolved_at     DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_sq_session ON survey_questions(session_id);
    CREATE INDEX IF NOT EXISTS idx_sr_session ON survey_results(session_id);
    CREATE INDEX IF NOT EXISTS idx_ss_broker  ON survey_sessions(broker_id);
    """)
    c.commit()
    # Migration for DBs created before the language column existed (idempotent:
    # check PRAGMA first so a re-run never errors on 'duplicate column').
    cols = [r[1] for r in c.execute("PRAGMA table_info(survey_sessions)").fetchall()]
    if "language" not in cols:
        c.execute("ALTER TABLE survey_sessions ADD COLUMN language TEXT DEFAULT 'pl'")
        c.commit()
    c.close()


# --------------------------------------------------------------------------- #
#  Create                                                                      #
# --------------------------------------------------------------------------- #

def create_session(broker_id: str, invoice_ref: str,
                   frozen_lines: list[dict], client_email: str = "",
                   language: str = "pl") -> dict:
    """Create a survey session plus one question row per frozen line.

    `frozen_lines` is a list of FrozenClassification dicts (see survey_freeze).
    `language` is the client-facing survey language (Polish by default).
    Returns {token, question_ids}.
    """
    token = uuid.uuid4().hex
    now = datetime.utcnow()
    expires = now + timedelta(days=SESSION_TTL_DAYS)
    c = _conn()
    c.execute(
        "INSERT INTO survey_sessions "
        "(id, broker_id, invoice_ref, created_at, expires_at, status, "
        " client_email, language) VALUES (?,?,?,?,?,?,?,?)",
        (token, broker_id or "", invoice_ref or "",
         now.isoformat(sep=" ", timespec="seconds"),
         expires.isoformat(sep=" ", timespec="seconds"),
         "pending", (client_email or "") or None, (language or "pl")))
    qids = []
    for fl in frozen_lines:
        qid = uuid.uuid4().hex
        qids.append(qid)
        c.execute(
            "INSERT INTO survey_questions "
            "(id, session_id, line_number, description_used, hs_code_attempted, "
            " freeze_reason, engine_question, option_set_json, engine_state_json, "
            " partial_heading) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (qid, token, fl.get("line_number", 0),
             fl.get("description_used"), fl.get("hs_code_attempted"),
             fl.get("freeze_reason", "NEEDS_DETAIL"),
             fl.get("engine_question", ""),
             json.dumps(fl.get("option_set") or []),
             json.dumps(fl.get("engine_state_snapshot") or {}),
             fl.get("partial_heading")))
    c.commit()
    c.close()
    return {"token": token, "question_ids": qids}


# --------------------------------------------------------------------------- #
#  Read                                                                        #
# --------------------------------------------------------------------------- #

def _is_expired(row) -> bool:
    try:
        exp = datetime.fromisoformat(row["expires_at"])
    except Exception:
        return False
    return datetime.utcnow() > exp


def get_session(token: str) -> dict | None:
    c = _conn()
    r = c.execute("SELECT * FROM survey_sessions WHERE id=?", (token,)).fetchone()
    c.close()
    if not r:
        return None
    d = dict(r)
    d["expired"] = _is_expired(r)
    return d


def get_questions(token: str) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM survey_questions WHERE session_id=? ORDER BY line_number",
        (token,)).fetchall()
    c.close()
    out = []
    for r in rows:
        d = dict(r)
        d["option_set"] = json.loads(d.pop("option_set_json") or "[]")
        d["engine_state"] = json.loads(d.pop("engine_state_json") or "{}")
        out.append(d)
    return out


def get_question(question_id: str) -> dict | None:
    c = _conn()
    r = c.execute("SELECT * FROM survey_questions WHERE id=?",
                  (question_id,)).fetchone()
    c.close()
    if not r:
        return None
    d = dict(r)
    d["option_set"] = json.loads(d.pop("option_set_json") or "[]")
    d["engine_state"] = json.loads(d.pop("engine_state_json") or "{}")
    return d


def get_results(token: str) -> list[dict]:
    c = _conn()
    rows = c.execute(
        "SELECT * FROM survey_results WHERE session_id=? ORDER BY line_number",
        (token,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
#  Write                                                                       #
# --------------------------------------------------------------------------- #

def record_answer(question_id: str, answer: str, answer_detail: str = ""):
    c = _conn()
    c.execute(
        "UPDATE survey_questions SET answer=?, answer_detail=?, "
        "answered_at=CURRENT_TIMESTAMP WHERE id=?",
        (answer, answer_detail or None, question_id))
    c.commit()
    c.close()


def write_result(token: str, question_id: str, line_number: int,
                 final_code: str | None, still_frozen: bool,
                 resolution_notes: str = ""):
    c = _conn()
    c.execute(
        "INSERT INTO survey_results "
        "(id, session_id, question_id, line_number, final_code, still_frozen, "
        " resolution_notes) VALUES (?,?,?,?,?,?,?)",
        (uuid.uuid4().hex, token, question_id, line_number,
         final_code, 1 if still_frozen else 0, resolution_notes or ""))
    c.commit()
    c.close()


def mark_completed(token: str):
    c = _conn()
    c.execute("UPDATE survey_sessions SET status='completed' WHERE id=?", (token,))
    c.commit()
    c.close()


def set_client_email(token: str, email: str):
    c = _conn()
    c.execute("UPDATE survey_sessions SET client_email=? WHERE id=?",
              (email or None, token))
    c.commit()
    c.close()


def mark_notification_sent(token: str):
    c = _conn()
    c.execute("UPDATE survey_sessions SET notification_sent_at=CURRENT_TIMESTAMP "
              "WHERE id=?", (token,))
    c.commit()
    c.close()


# --------------------------------------------------------------------------- #
#  Dashboard                                                                   #
# --------------------------------------------------------------------------- #

def list_pending(broker_id: str = "") -> list[dict]:
    """Sessions for a broker (or all if blank), newest first, with a pending-line
    count and a derived display status."""
    c = _conn()
    sql = "SELECT * FROM survey_sessions"
    args = []
    if broker_id:
        sql += " WHERE broker_id=?"
        args.append(broker_id)
    sql += " ORDER BY created_at DESC"
    rows = c.execute(sql, args).fetchall()
    out = []
    for r in rows:
        token = r["id"]
        n = c.execute("SELECT COUNT(*) FROM survey_questions WHERE session_id=?",
                      (token,)).fetchone()[0]
        d = dict(r)
        d["lines_pending"] = n
        if _is_expired(r) and r["status"] != "completed":
            d["display_status"] = "expired"
        elif r["status"] == "completed":
            d["display_status"] = "complete"
        elif r["notification_sent_at"]:
            d["display_status"] = "awaiting"
        else:
            d["display_status"] = "not_sent"
        out.append(d)
    c.close()
    return out


# Ensure tables exist on import.
init()
