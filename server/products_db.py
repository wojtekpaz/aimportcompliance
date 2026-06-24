#!/usr/bin/env python3
"""
products_db.py — Saved classifications store for AImport Compliance.

SEPARATION OF CONCERNS (deliberate):
  This module owns its OWN database file, saved_products.sqlite, completely
  independent of data_taric.sqlite. The tariff database is read-only reference
  data REPLACED WHOLESALE on every EU update; user-saved classifications must
  survive those updates, so they live in a separate file the engine never reads
  or writes. Nothing in engine/ imports this module — the classification logic
  is structurally untouched.

  A saved record is a FROZEN SNAPSHOT of a classification result at save time:
  code, description, origin, duty, anti-dumping measures, confidence, and the
  full audit trail. Duties can change later in TARIC; the saved record preserves
  what was determined and why — the due-diligence value.
"""
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

DB_PATH = Path(os.environ.get("AIMPORT_DATA_DIR", ".")) / "saved_products.sqlite"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)   # create the volume dir if missing


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    """Create the table if absent. Safe to call on every startup."""
    c = _conn()
    c.execute("""
        CREATE TABLE IF NOT EXISTS saved_product (
            id            TEXT PRIMARY KEY,
            saved_at      INTEGER NOT NULL,
            description   TEXT,
            code          TEXT,
            code_spaced   TEXT,
            cn_description TEXT,
            origin        TEXT,
            chapter       TEXT,
            duty_rate     TEXT,
            duty_regulation TEXT,
            confidence    TEXT,
            industrial    INTEGER DEFAULT 0,
            has_defense   INTEGER DEFAULT 0,
            defense_json  TEXT,
            trail_json    TEXT,
            note          TEXT DEFAULT ''
        )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sp_code ON saved_product(code)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sp_origin ON saved_product(origin)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sp_saved ON saved_product(saved_at)")
    c.commit()
    c.close()


def save(result: dict, note: str = "") -> dict:
    """Persist a classification result (the dict from engine_session._serialize).
    Returns {ok, id}. Only completed classifications can be saved."""
    if not result or result.get("status") != "classified" or not result.get("code"):
        return {"ok": False, "error": "Only completed classifications can be saved."}
    rid = uuid.uuid4().hex
    code = result.get("code", "")
    defense = result.get("defense") or []
    duty = result.get("duty") or {}
    c = _conn()
    c.execute("""
        INSERT INTO saved_product
        (id, saved_at, description, code, code_spaced, cn_description, origin,
         chapter, duty_rate, duty_regulation, confidence, industrial,
         has_defense, defense_json, trail_json, note)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        rid, int(time.time()),
        result.get("description", ""),
        code,
        result.get("code_spaced", ""),
        result.get("cn_description", result.get("description", "")),
        (result.get("origin") or "").upper(),
        code[:2] if code else "",
        duty.get("rate", ""),
        duty.get("regulation", ""),
        result.get("confidence", ""),
        1 if result.get("industrial") else 0,
        1 if defense else 0,
        json.dumps(defense),
        json.dumps(result.get("trail") or []),
        note or "",
    ))
    c.commit()
    c.close()
    return {"ok": True, "id": rid}


def _row_to_dict(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"], "saved_at": r["saved_at"],
        "description": r["description"], "code": r["code"],
        "code_spaced": r["code_spaced"], "cn_description": r["cn_description"],
        "origin": r["origin"], "chapter": r["chapter"],
        "duty_rate": r["duty_rate"], "duty_regulation": r["duty_regulation"],
        "confidence": r["confidence"], "industrial": bool(r["industrial"]),
        "has_defense": bool(r["has_defense"]),
        "defense": json.loads(r["defense_json"] or "[]"),
        "trail": json.loads(r["trail_json"] or "[]"),
        "note": r["note"],
    }


def list_products(search: str = "", origin: str = "", chapter: str = "",
                  confidence: str = "", has_defense: str = "") -> list:
    """Saved products, newest first, with optional filters."""
    sql = "SELECT * FROM saved_product WHERE 1=1"
    args: list = []
    if search:
        sql += " AND (description LIKE ? OR cn_description LIKE ? OR code LIKE ?)"
        like = f"%{search}%"
        args += [like, like, like]
    if origin:
        sql += " AND origin = ?"
        args.append(origin.upper())
    if chapter:
        sql += " AND chapter = ?"
        args.append(chapter)
    if confidence:
        sql += " AND confidence LIKE ?"
        args.append(f"%{confidence}%")
    if has_defense == "1":
        sql += " AND has_defense = 1"
    sql += " ORDER BY saved_at DESC"
    c = _conn()
    rows = [_row_to_dict(r) for r in c.execute(sql, args).fetchall()]
    c.close()
    return rows


def delete(rid: str) -> dict:
    c = _conn()
    cur = c.execute("DELETE FROM saved_product WHERE id = ?", (rid,))
    c.commit()
    deleted = cur.rowcount
    c.close()
    return {"ok": True, "deleted": deleted}


def stats() -> dict:
    c = _conn()
    total = c.execute("SELECT COUNT(*) FROM saved_product").fetchone()[0]
    with_def = c.execute(
        "SELECT COUNT(*) FROM saved_product WHERE has_defense=1").fetchone()[0]
    origins = c.execute(
        "SELECT COUNT(DISTINCT origin) FROM saved_product "
        "WHERE origin != ''").fetchone()[0]
    c.close()
    return {"total": total, "with_defense": with_def, "origins": origins}


def export_csv() -> str:
    import csv
    import io
    rows = list_products()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Saved at (UTC)", "Product", "CN code", "CN description",
                "Origin", "Duty rate", "Legal basis", "Confidence",
                "Industrial mode", "Trade defence", "Note"])
    for r in rows:
        w.writerow([
            time.strftime("%Y-%m-%d %H:%M", time.gmtime(r["saved_at"])),
            r["description"], r["code_spaced"], r["cn_description"],
            r["origin"], r["duty_rate"], r["duty_regulation"],
            r["confidence"], "yes" if r["industrial"] else "no",
            "yes" if r["has_defense"] else "no", r["note"],
        ])
    return buf.getvalue()


# Ensure the table exists on import.
init()
