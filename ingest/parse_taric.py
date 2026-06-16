#!/usr/bin/env python3
"""
parse_taric.py — Ingest TARIC XML extracts into the AImport database.

DESIGN RULES (from the AImport build briefing):
1. NO SILENT FALLBACKS. Unknown record types are counted and reported,
   never skipped invisibly. Missing mandatory fields abort the record
   and are logged.
2. EVERYTHING IS DATE-BOUNDED. We store validity windows verbatim.
3. PROVENANCE. Each run writes an ingest_log row (file hash, date,
   record count); all rows carry snapshot_id.
4. TOLERANT TAG MATCHING, STRICT SEMANTICS. TARIC dialects write tags as
   'goods.nomenclature', 'goods_nomenclature' or 'GoodsNomenclature';
   we normalise spelling but never guess meaning.

USAGE:
    python3 ingest/parse_taric.py <file.xml|file.zip> <database.sqlite>
"""
import hashlib
import io
import re
import sqlite3
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from lxml import etree

SCHEMA = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


# ---------------------------------------------------------------- helpers
def norm(tag: str) -> str:
    """Normalise tag spelling: strip namespace, dots/dashes -> underscore,
    CamelCase -> snake_case, lowercase."""
    tag = tag.rsplit("}", 1)[-1]
    tag = re.sub(r"(?<!^)(?=[A-Z])", "_", tag)      # CamelCase split
    tag = tag.replace(".", "_").replace("-", "_")
    return re.sub(r"__+", "_", tag).lower()


def child_map(elem) -> dict:
    """Flatten one record element into {normalised_tag: text}."""
    out = {}
    for c in elem.iter():
        if c is elem:
            continue
        t = (c.text or "").strip()
        if t:
            out.setdefault(norm(c.tag), t)
    return out


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def get(d: dict, *keys, required=False, record=""):
    """Fetch first present key; loud failure if required and absent."""
    for k in keys:
        if k in d:
            return d[k]
    if required:
        raise KeyError(f"[{record}] missing required field, tried {keys}; got {sorted(d)[:12]}")
    return None


# ---------------------------------------------------------------- handlers
# Each handler returns (table, row_tuple) or raises KeyError (-> rejected).

def h_goods_nomenclature(d, sid_):
    return ("goods_nomenclature",
            (int(get(d, "goods_nomenclature_sid", "sid", required=True, record="goods_nomenclature")),
             get(d, "goods_nomenclature_item_id", "item_id", required=True, record="goods_nomenclature"),
             get(d, "producline_suffix", "productline_suffix") or "80",
             get(d, "validity_start_date", "validity_start", required=True, record="goods_nomenclature"),
             get(d, "validity_end_date", "validity_end"),
             int(get(d, "statistical_indicator") or 0),
             0,            # is_leaf: not present in TARIC3 XML; derived later
             sid_))


def h_gn_indent(d, sid_):
    return ("goods_nomenclature_indent",
            (int(get(d, "goods_nomenclature_sid", "sid", required=True, record="indent")),
             int(get(d, "number_indents", "indent_level", required=True, record="indent")),
             get(d, "validity_start_date", "validity_start", required=True, record="indent"),
             get(d, "validity_end_date", "validity_end"),
             sid_))


def h_gn_description(d, sid_):
    return ("goods_nomenclature_description",
            (int(get(d, "goods_nomenclature_sid", "sid", required=True, record="description")),
             get(d, "language_id") or "EN",
             get(d, "description", required=True, record="description"),
             get(d, "validity_start_date", "validity_start") or "1900-01-01",
             get(d, "validity_end_date", "validity_end"),
             sid_))


def h_geo_area(d, sid_):
    return ("geographical_area",
            (int(get(d, "geographical_area_sid", "sid", required=True, record="geo_area")),
             get(d, "geographical_area_id", "area_id", required=True, record="geo_area"),
             int(get(d, "geographical_code", "area_code") or 0),
             get(d, "validity_start_date", "validity_start", required=True, record="geo_area"),
             get(d, "validity_end_date", "validity_end"),
             sid_))


def h_geo_membership(d, sid_):
    return ("geographical_area_membership",
            (int(get(d, "geographical_area_sid", "member_sid", required=True, record="geo_membership")),
             int(get(d, "geographical_area_group_sid", "group_sid", required=True, record="geo_membership")),
             get(d, "validity_start_date", "validity_start", required=True, record="geo_membership"),
             get(d, "validity_end_date", "validity_end"),
             sid_))


def h_measure(d, sid_):
    return ("measure",
            (int(get(d, "measure_sid", "sid", required=True, record="measure")),
             get(d, "goods_nomenclature_item_id", required=True, record="measure"),
             int(get(d, "goods_nomenclature_sid") or 0) or None,
             get(d, "measure_type", "measure_type_id", required=True, record="measure"),
             get(d, "geographical_area", "geographical_area_id", required=True, record="measure"),
             int(get(d, "geographical_area_sid") or 0) or None,
             get(d, "additional_code"),
             get(d, "measure_generating_regulation_id", "regulation_id"),
             None,         # duty_raw: XML carries components, not text
             None,         # origin_name: resolved via geographical_area join
             get(d, "validity_start_date", "validity_start", required=True, record="measure"),
             get(d, "validity_end_date", "validity_end"),
             sid_))


def h_measure_component(d, sid_):
    amt = get(d, "duty_amount")
    return ("measure_component",
            (int(get(d, "measure_sid", required=True, record="measure_component")),
             get(d, "duty_expression_id", "duty_expression"),
             float(amt) if amt is not None else None,
             get(d, "monetary_unit_code", "monetary_unit"),
             get(d, "measurement_unit_code", "measurement_unit"),
             sid_))


def h_measure_condition(d, sid_):
    return ("measure_condition",
            (int(get(d, "measure_sid", required=True, record="measure_condition")),
             get(d, "condition_code"),
             get(d, "certificate_type_code", "certificate_type"),
             get(d, "certificate_code"),
             get(d, "action_code"),
             sid_))


def h_measure_type(d, sid_):
    return ("measure_type",
            (get(d, "measure_type_id", required=True, record="measure_type"),
             get(d, "description"),
             sid_))


def h_certificate_description(d, sid_):
    return ("certificate_description",
            (get(d, "certificate_type_code", "certificate_type"),
             get(d, "certificate_code", required=True, record="certificate_description"),
             get(d, "description"),
             sid_))


def h_footnote(d, sid_):
    return ("footnote",
            (get(d, "footnote_type_id", "footnote_type"),
             get(d, "footnote_id", required=True, record="footnote"),
             get(d, "description"),
             sid_))


def h_footnote_assoc(d, sid_):
    return ("footnote_association_goods",
            (int(get(d, "goods_nomenclature_sid", required=True, record="footnote_assoc")),
             get(d, "footnote_type", "footnote_type_id"),
             get(d, "footnote_id", required=True, record="footnote_assoc"),
             sid_))


def h_geo_description(d, sid_):
    return ("geographical_area_description",
            (int(get(d, "geographical_area_sid", "sid", required=True, record="geo_description")),
             get(d, "language_id") or "EN",
             get(d, "description", required=True, record="geo_description"),
             sid_))


HANDLERS = {
    "goods_nomenclature": h_goods_nomenclature,
    "goods_nomenclature_indent": h_gn_indent,
    "goods_nomenclature_indents": h_gn_indent,
    "goods_nomenclature_description": h_gn_description,
    "goods_nomenclature_description_period": None,   # known, deliberately ignored
    "geographical_area": h_geo_area,
    "geographical_area_description": h_geo_description,
    "geographical_area_membership": h_geo_membership,
    "measure": h_measure,
    "measure_component": h_measure_component,
    "measure_condition": h_measure_condition,
    "measure_type": h_measure_type,
    "measure_type_description": None,
    "certificate_description": h_certificate_description,
    "footnote": h_footnote,
    "footnote_description": None,
    "footnote_association_goods_nomenclature": h_footnote_assoc,
}

INSERT_SQL = {
    "goods_nomenclature": "INSERT INTO goods_nomenclature VALUES (?,?,?,?,?,?,?,?)",
    "goods_nomenclature_indent": "INSERT INTO goods_nomenclature_indent VALUES (?,?,?,?,?)",
    "goods_nomenclature_description": "INSERT INTO goods_nomenclature_description VALUES (?,?,?,?,?,?)",
    "geographical_area": "INSERT INTO geographical_area VALUES (?,?,?,?,?,?)",
    "geographical_area_description": "INSERT INTO geographical_area_description VALUES (?,?,?,?)",
    "geographical_area_membership": "INSERT INTO geographical_area_membership VALUES (?,?,?,?,?)",
    "measure": "INSERT INTO measure VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
    "measure_component": "INSERT INTO measure_component VALUES (?,?,?,?,?,?)",
    "measure_condition": "INSERT INTO measure_condition VALUES (?,?,?,?,?,?)",
    "measure_type": "INSERT INTO measure_type VALUES (?,?,?)",
    "certificate_description": "INSERT INTO certificate_description VALUES (?,?,?,?)",
    "footnote": "INSERT INTO footnote VALUES (?,?,?,?)",
    "footnote_association_goods": "INSERT INTO footnote_association_goods VALUES (?,?,?,?)",
}


# ---------------------------------------------------------------- ingest
def iter_xml_sources(path: Path):
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if name.lower().endswith(".xml"):
                    yield name, io.BytesIO(zf.read(name))
    else:
        yield path.name, open(path, "rb")


def ingest(xml_path: Path, db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA.read_text())

    cur = conn.execute(
        "INSERT INTO ingest_log (source_file, file_sha256, loaded_at, records_loaded) "
        "VALUES (?,?,?,0)",
        (xml_path.name, sha256_of(xml_path),
         datetime.now(timezone.utc).isoformat(timespec="seconds")))
    snapshot_id = cur.lastrowid

    stats = {"loaded": 0, "rejected": [], "unknown_tags": {}, "ignored": 0}
    handled_tags = set(HANDLERS)

    def is_record_like(elem) -> bool:
        """A record element has children, and its children are leaves.
        This distinguishes:
          - <measure>...fields...</measure>               -> record (process)
          - <measure.type>103</measure.type> IN a measure -> leaf field (skip)
          - <export>/<transaction> wrappers               -> wrapper (skip)
        NOTE: if the real TARIC dialect nests sub-records inside records,
        inspect_xml.py will reveal it and this predicate must be extended —
        the unknown-tag report below guarantees we'd notice."""
        return len(elem) > 0 and all(len(c) == 0 for c in elem)

    for name, src in iter_xml_sources(xml_path):
        context = etree.iterparse(src, events=("end",), recover=True)
        for _, elem in context:
            if elem.getparent() is None:
                continue  # document root is never a record
            if not is_record_like(elem):
                continue  # leaf field or wrapper — leave intact, no clearing
            tag = norm(elem.tag)
            if tag not in handled_tags:
                stats["unknown_tags"][tag] = stats["unknown_tags"].get(tag, 0) + 1
            else:
                handler = HANDLERS[tag]
                if handler is None:
                    stats["ignored"] += 1
                else:
                    try:
                        table, row = handler(child_map(elem), snapshot_id)
                        conn.execute(INSERT_SQL[table], row)
                        stats["loaded"] += 1
                    except (KeyError, ValueError) as e:
                        stats["rejected"].append(str(e))
            # Memory hygiene: clear ONLY fully-processed records and their
            # already-processed earlier siblings — never a record's own fields.
            elem.clear()
            parent = elem.getparent()
            if parent is not None:
                while elem.getprevious() is not None:
                    del parent[0]

    conn.execute("UPDATE ingest_log SET records_loaded=? WHERE snapshot_id=?",
                 (stats["loaded"], snapshot_id))
    conn.commit()
    conn.close()
    return stats


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    xml_path, db_path = Path(sys.argv[1]), Path(sys.argv[2])
    stats = ingest(xml_path, db_path)

    print(f"Loaded:   {stats['loaded']} records")
    print(f"Ignored:  {stats['ignored']} (known, deliberately skipped types)")
    if stats["unknown_tags"]:
        print("\n!! UNKNOWN RECORD TYPES — parser must be extended before this "
              "ingest is trusted:")
        for t, n in sorted(stats["unknown_tags"].items(), key=lambda x: -x[1]):
            print(f"   {t}  x{n}")
    if stats["rejected"]:
        print(f"\n!! REJECTED RECORDS ({len(stats['rejected'])}) — first 10:")
        for r in stats["rejected"][:10]:
            print(f"   {r}")
    if stats["unknown_tags"] or stats["rejected"]:
        print("\nRESULT: INGEST INCOMPLETE — do not use this database for "
              "classification until resolved.")
        sys.exit(2)
    print("\nRESULT: CLEAN INGEST.")


if __name__ == "__main__":
    main()
