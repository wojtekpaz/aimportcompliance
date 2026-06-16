#!/usr/bin/env python3
"""
lookup.py — Inheritance-aware TARIC measure lookup.

LEGAL MECHANISM IMPLEMENTED HERE:
In TARIC, measures attach at any level of the nomenclature tree and apply
to all descendant codes unless a more specific measure overrides them.
E.g. the 12% third-country duty for cotton t-shirts sits at heading
6109000000 and inherits down to 6109100010. A correct lookup therefore:
  1. builds the ancestor chain of the queried code,
  2. collects valid measures (by origin and date) across the whole chain,
  3. for duty-type measures keeps only the MOST SPECIFIC (deepest) level,
  4. keeps ALL non-duty measures (ADD, controls, prohibitions) that apply.

KNOWN LIMITATION (v1, documented): origin matching = exact country code +
ERGA OMNES (1011). Country-group measures (GSP etc.) are returned with a
'group_unresolved' marker instead of silently skipped.
"""
import sqlite3
from datetime import date

# Measure types that are mutually-overriding duties (most specific wins,
# and preferential rates compete with third-country duty at output stage).
DUTY_TYPES = {"103", "105", "106", "112", "115", "117", "119", "122",
              "123", "140", "141", "142", "143", "145", "146"}
# Trade defense — ALWAYS shown, additive to duty.
DEFENSE_TYPES = {"551", "552", "553", "554", "555", "565", "570", "695"}


def ancestor_chain(code: str) -> list[str]:
    """'6109100010' -> ['6109100010','6109100000','6109000000','6100000000']
    (only well-formed prefix levels; DB join filters to ones that exist)."""
    code = code.ljust(10, "0")
    chain = [code]
    for cut in (8, 6, 4, 2):
        anc = code[:cut].ljust(10, "0")
        if anc != chain[-1]:
            chain.append(anc)
    return chain


def lookup(conn: sqlite3.Connection, code: str, origin: str,
           on_date: str | None = None) -> dict:
    """Returns {'duty': [...], 'defense': [...], 'other': [...],
                'group_unresolved': [...]} — each item a dict with full
    provenance (level, regulation, raw text, components, conditions)."""
    on_date = on_date or date.today().isoformat()
    chain = ancestor_chain(code)
    origin = origin.upper().strip()

    placeholders = ",".join("?" * len(chain))
    rows = conn.execute(f"""
        SELECT m.sid, m.goods_nomenclature_item_id, m.measure_type_id,
               mt.description, m.geographical_area_id, m.origin_name,
               m.additional_code, m.regulation_id, m.duty_raw,
               m.validity_start, m.validity_end
        FROM measure m LEFT JOIN measure_type mt USING (measure_type_id)
        WHERE m.goods_nomenclature_item_id IN ({placeholders})
          AND m.validity_start <= ?
          AND (m.validity_end IS NULL OR m.validity_end >= ?)
        """, chain + [on_date, on_date]).fetchall()

    def enrich(r):
        sid = r[0]
        comps = conn.execute(
            "SELECT duty_expression_id, duty_amount, monetary_unit, "
            "measurement_unit FROM measure_component WHERE measure_sid=?",
            (sid,)).fetchall()
        # structured conditions (v2) joined by business key; fall back to the
        # old regex-parsed table only if v2 has nothing for this measure
        goods, add, geo, mtype, vstart = r[1], r[6], r[4], r[2], r[9]
        conds_v2 = conn.execute("""
            SELECT condition_group, sequence, certificate, action_code,
                   cond_amount, monetary_unit
            FROM measure_condition_v2
            WHERE goods_code=? AND origin_code=? AND measure_type=?
              AND (add_code IS ? OR add_code=?)
            ORDER BY condition_group, sequence""",
            (goods, geo, mtype, add, add)).fetchall()
        legal = conn.execute(
            "SELECT official_journal, page, publication_date "
            "FROM legal_basis WHERE legal_base=?", (r[7],)).fetchone()
        add_meaning = None
        if r[6]:
            am = conn.execute(
                "SELECT description FROM additional_code_description "
                "WHERE add_code=?", (r[6],)).fetchone()
            add_meaning = am[0] if am else None
        return {"measure_sid": sid, "code_level": r[1], "type": r[2],
                "type_name": r[3], "geo": r[4], "geo_name": r[5],
                "additional_code": r[6], "additional_code_meaning": add_meaning,
                "regulation": r[7], "legal_oj": legal,
                "duty_raw": r[8], "valid": (r[9], r[10]),
                "components": comps,
                "conditions": conds_v2,    # structured: (group,seq,cert,action,amt,unit)
                "depth": chain.index(r[1])}

    out = {"duty": [], "defense": [], "other": [], "group_unresolved": [],
           "suppressed_by_exclusion": []}

    def is_excluded(goods, geo, mtype, add):
        """Is `origin` carved out of this group measure?"""
        if geo == origin:        # measure targets the country directly
            return False
        rows_x = conn.execute("""
            SELECT excluded_country FROM measure_exclusion
            WHERE goods_code=? AND origin_code=? AND measure_type=?""",
            (goods, geo, mtype)).fetchall()
        return any((x[0] or "").upper() == origin for x in rows_x)

    for r in rows:
        geo = (r[4] or "").strip()
        item = enrich(r)
        if geo == origin or geo == "1011":
            if is_excluded(r[1], geo, r[2], r[6]):
                out["suppressed_by_exclusion"].append(item)
                continue
        elif geo.isdigit():
            # country GROUP: applies only if origin is a member AND not excluded.
            # Membership table not in this export set, so we still surface it —
            # but if origin is explicitly excluded, suppress the false positive.
            if is_excluded(r[1], geo, r[2], r[6]):
                out["suppressed_by_exclusion"].append(item)
            else:
                out["group_unresolved"].append(item)
            continue
        else:
            continue  # measure for a different specific country

        if item["type"] in DUTY_TYPES:
            out["duty"].append(item)
        elif item["type"] in DEFENSE_TYPES:
            out["defense"].append(item)
        else:
            out["other"].append(item)

    # duty: most specific level wins within each measure type
    if out["duty"]:
        best = {}
        for it in out["duty"]:
            k = it["type"]
            if k not in best or it["depth"] < best[k]["depth"]:
                best[k] = it
        out["duty"] = sorted(best.values(), key=lambda x: x["type"])
    return out


def format_result(conn, code, origin, res) -> str:
    desc = conn.execute("""
        SELECT d.description FROM goods_nomenclature g
        JOIN goods_nomenclature_description d ON d.sid=g.sid
        WHERE g.item_id=? ORDER BY d.validity_start DESC""",
        (code.ljust(10, "0"),)).fetchone()
    lines = [f"CODE {code}  origin {origin}",
             f"  description: {_clean_pipes(desc[0]) if desc else '?'}"]
    for it in res["duty"]:
        lines.append(f"  DUTY [{it['type']} {it['type_name']}] "
                     f"(set at level {it['code_level']}): {_duty_display(it)}  "
                     f"[{it['regulation']}]")
    for it in res["defense"]:
        ac = f" add.code {it['additional_code']}" if it["additional_code"] else ""
        meaning = (f" ({it['additional_code_meaning']})"
                   if it.get("additional_code_meaning") else "")
        lines.append(f"  TRADE DEFENSE [{it['type']} {it['type_name']}]{ac}{meaning}: "
                     f"{_duty_display(it)}  [{it['regulation']}]")
        _fmt_conditions(lines, it)
    for it in res["other"]:
        lines.append(f"  OTHER [{it['type']} {it['type_name']}]: "
                     f"{(it['duty_raw'] or '').strip()[:70]}  [{it['regulation']}]")
        _fmt_conditions(lines, it)
    for it in res["group_unresolved"]:
        lines.append(f"  ! group-targeted measure not auto-resolved "
                     f"[{it['type']} {it['type_name']}] geo-group {it['geo']} "
                     f"({it['geo_name']}) — check membership manually")
    for it in res.get("suppressed_by_exclusion", []):
        lines.append(f"  (suppressed: {origin} is excluded from "
                     f"[{it['type']} {it['type_name']}] group {it['geo']})")
    return "\n".join(lines)


def _clean_pipes(s):
    import re
    return re.sub(r"\s*\|\s*", " ", s).strip() if s else s


def _duty_display(it):
    """Prefer clean duty; hide raw 'Cond:...' text when structured conditions
    exist to render separately."""
    raw = _clean_pipes(it["duty_raw"] or "")
    if raw.startswith("Cond:"):
        return "(conditional — see conditions below)" if it["conditions"] else raw
    return raw


def _fmt_conditions(lines, it):
    """Render structured conditions grouped by condition group."""
    if not it["conditions"]:
        return
    groups = {}
    for grp, seq, cert, action, amt, unit in it["conditions"]:
        groups.setdefault(grp, []).append((seq, cert, action, amt, unit))
    for grp, subs in groups.items():
        parts = []
        for seq, cert, action, amt, unit in subs:
            doc = cert or "(no document)"
            act = {"27": "→ measure applies", "07": "→ not applicable",
                   "08": "→ entry prevented"}.get(action, f"action {action}")
            amt_s = f" {amt}{unit or ''}" if amt is not None else ""
            parts.append(f"{doc}{amt_s} {act}")
        lines.append(f"      condition {grp}: " + " | ".join(parts))


if __name__ == "__main__":
    import sys
    conn = sqlite3.connect(sys.argv[1])
    res = lookup(conn, sys.argv[2], sys.argv[3])
    print(format_result(conn, sys.argv[2], sys.argv[3], res))
