#!/usr/bin/env python3
"""
tree.py — Dash-level (indent) aware navigation of the goods nomenclature.

CORRECTED MODEL (grounded in WCO/CBSA classification rules and EU site):
Classification descends by INDENT (dash) level, not by digit pairs. At each
step you compare ONLY siblings at the same indent under the same parent.
Some indent levels are intermediate/unnumbered lines (e.g. "Solely or
principally of sheet" between 7308 90 and 7308 90 98) and must still be
offered as a choice.

"OTHER" RENDERING: a bare "Other" is rendered with parent context + the
named siblings it excludes, so humans and the LLM oracle see its meaning.
"""
import re


def _clean(text):
    if not text:
        return text
    return re.sub(r"\s*\|\s*", " ", text).strip()


def rows_under_heading(conn, heading4):
    rows = conn.execute("""
        SELECT g.item_id, g.producline_suffix, g.is_leaf,
               i.indent_level, d.description
        FROM goods_nomenclature g
        LEFT JOIN goods_nomenclature_indent i ON i.sid = g.sid
        LEFT JOIN goods_nomenclature_description d ON d.sid = g.sid
        WHERE substr(g.item_id,1,4) = ?
        ORDER BY g.item_id, g.producline_suffix""", (heading4,)).fetchall()
    out, seen = [], set()
    for item, suffix, leaf, indent, desc in rows:
        key = (item, suffix)
        if key in seen:
            continue
        seen.add(key)
        out.append({"item_id": item, "suffix": suffix, "is_leaf": bool(leaf),
                    "indent": indent if indent is not None else 0,
                    "desc": _clean(desc) or "(no description)"})
    return out


def descriptions_for(conn, item_id):
    rows = conn.execute("""
        SELECT d.description FROM goods_nomenclature g
        JOIN goods_nomenclature_description d ON d.sid = g.sid
        WHERE g.item_id = ? ORDER BY g.producline_suffix, d.validity_start DESC""",
        (item_id,)).fetchall()
    seen, out = set(), []
    for (d,) in rows:
        d = _clean(d)
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _enrich_other(kids):
    named = [k["desc"] for k in kids
             if k["desc"].lower() not in ("other", "(no description)")]
    for k in kids:
        if k["desc"].lower() == "other" and named:
            k["display"] = ("Other — i.e. not: " + "; ".join(named))[:300]
            k["is_other"] = True
        else:
            k["display"] = k["desc"]
            k["is_other"] = False
    return kids


def first_level_children(conn, heading4):
    rows = rows_under_heading(conn, heading4)
    if not rows:
        return []
    base = min(r["indent"] for r in rows)
    child_indent = min((r["indent"] for r in rows if r["indent"] > base),
                       default=None)
    if child_indent is None:
        return _enrich_other(rows[:1])
    return _enrich_other([r for r in rows if r["indent"] == child_indent])


def next_level_children(conn, heading4, parent_item, parent_suffix=None):
    rows = rows_under_heading(conn, heading4)
    idx = next((k for k, r in enumerate(rows)
                if r["item_id"] == parent_item
                and (parent_suffix is None or r["suffix"] == parent_suffix)), None)
    if idx is None:
        return []
    parent_indent = rows[idx]["indent"]
    block = []
    for r in rows[idx + 1:]:
        if r["indent"] <= parent_indent:
            break
        block.append(r)
    if not block:
        return []
    child_indent = min(r["indent"] for r in block)
    return _enrich_other([r for r in block if r["indent"] == child_indent])


def path_text(conn, code):
    heading4 = code[:4]
    rows = rows_under_heading(conn, heading4)
    code10 = code.ljust(10, "0")
    target = next((r for r in rows if r["item_id"] == code10), None)
    chain = []
    chapter_txt = descriptions_for(conn, code[:2].ljust(10, "0"))
    if chapter_txt:
        chain.append(chapter_txt[0])
    # heading text = the indent-0 (base) row under this heading
    base_rows = [r for r in rows if r["indent"] == min((rr["indent"] for rr in rows), default=0)] if rows else []
    if base_rows:
        chain.append(base_rows[0]["desc"])
    if target and rows:
        idx = rows.index(target)
        base = min(r["indent"] for r in rows)
        want = target["indent"] - 1
        ancestors = []
        for r in reversed(rows[:idx]):
            if r["indent"] == want:
                ancestors.append(r["desc"])
                want -= 1
            if want < base:
                break
        chain.extend(reversed(ancestors))
        if target["desc"] not in chain[-1:]:
            chain.append(target["desc"])
    return " > ".join(chain)


def is_declarable(conn, code):
    r = conn.execute("SELECT MAX(is_leaf) FROM goods_nomenclature WHERE item_id=?",
                     (code.ljust(10, "0"),)).fetchone()
    return bool(r and r[0])
