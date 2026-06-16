#!/usr/bin/env python3
"""
search.py — Full-text candidate search over nomenclature path texts.

ROLE BOUNDARY (lesson from the v12 audit):
Text search (FTS/BM25) is a CANDIDATE GENERATOR that runs BEFORE legal
reasoning. It may propose headings; it may NEVER override or re-rank a
classification after GRI logic has run. The v12 bug — BM25 silently
replacing legally-determined codes post-GRI — is structurally impossible
here because search results only ever enter the pipeline as inputs.

Index design: each declarable code is indexed under its FULL PATH TEXT
(chapter > heading > ... > line), so "cotton t-shirt" matches code
6109100010 even though its own line just says "T-shirts".
"""
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tree import path_text  # noqa: E402


def build_index(conn):
    conn.execute("DROP TABLE IF EXISTS code_search")
    conn.execute("""CREATE VIRTUAL TABLE code_search USING fts5(
        item_id UNINDEXED, heading UNINDEXED, path,
        tokenize='porter unicode61')""")
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT item_id FROM goods_nomenclature WHERE is_leaf=1")]
    for code in codes:
        conn.execute("INSERT INTO code_search VALUES (?,?,?)",
                     (code, code[:4], path_text(conn, code)))
    conn.commit()
    return len(codes)


def _sanitize(q: str) -> str:
    """FTS5 treats punctuation as syntax; keep only word tokens, OR them
    so partial matches still rank (BM25 puts better matches first)."""
    words = re.findall(r"[A-Za-z0-9]{2,}", q.lower())
    return " OR ".join(words) if words else '""'


def candidate_headings(conn, query: str, limit_headings: int = 8) -> list[dict]:
    """Top distinct 4-digit HEADINGS for a free-text query, each with its
    best-matching example codes. Headings — not codes — are what GRI-1
    reasons over first."""
    rows = conn.execute("""
        SELECT heading, item_id, path, bm25(code_search) AS score
        FROM code_search WHERE code_search MATCH ?
        ORDER BY score LIMIT 60""", (_sanitize(query),)).fetchall()
    by_heading = {}
    for heading, item, path, score in rows:
        h = by_heading.setdefault(heading, {"heading": heading,
                                            "best_score": score,
                                            "examples": []})
        if len(h["examples"]) < 3:
            h["examples"].append({"item_id": item, "path": path})
    out = sorted(by_heading.values(), key=lambda x: x["best_score"])
    return out[:limit_headings]


def search_codes(conn, query: str, within_prefix: str = "",
                 limit: int = 12) -> list[dict]:
    sql = """SELECT item_id, path, bm25(code_search) AS score
             FROM code_search WHERE code_search MATCH ?"""
    args = [_sanitize(query)]
    if within_prefix:
        sql += " AND item_id LIKE ?"
        args.append(within_prefix.rstrip("0") + "%")
    sql += " ORDER BY score LIMIT ?"
    args.append(limit)
    return [{"item_id": r[0], "path": r[1], "score": r[2]}
            for r in conn.execute(sql, args)]


if __name__ == "__main__":
    conn = sqlite3.connect(sys.argv[1])
    if len(sys.argv) > 2 and sys.argv[2] == "build":
        n = build_index(conn)
        print(f"indexed {n} declarable codes")
    else:
        for h in candidate_headings(conn, " ".join(sys.argv[2:])):
            print(f"heading {h['heading']}  (score {h['best_score']:.2f})")
            for e in h["examples"]:
                print(f"   {e['item_id']}  {e['path'][:95]}")
