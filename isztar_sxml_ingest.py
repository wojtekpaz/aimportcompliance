"""Bulk ingest of Polish nomenclature descriptions from the ISZTAR4 SXML base file.

The ISZTAR4 "startup file" (base-*.xml inside startup-file-*.zip) is the full
offline tariff database (1.5M records, multilingual). This streams it and loads
the Polish (PL) description for every goods-nomenclature code into the local
store, so the PL profile shows authoritative Polish descriptions / legal notes
for ANY determined code — deterministic, offline, no LLM.

The base file carries the nomenclature but NOT per-code measures, so VAT/excise
still come from isztar_ingest.py (the API path). Run once per startup file.

Usage:
  python3 isztar_sxml_ingest.py polish_aimport/startup-file-20260101T084600.zip
"""
import sys
import xml.etree.ElementTree as ET
import zipfile

import isztar_pl


def _ln(tag):
    return tag.rsplit("}", 1)[-1]


def _base_xml_name(zf):
    for n in zf.namelist():
        if n.startswith("base-") and n.endswith(".xml"):
            return n
    raise SystemExit("no base-*.xml found in the archive")


def pl_description(gn):
    """(code, pl_desc, en_desc) for a <GoodsNomenclature> element."""
    code = pl = en = None
    for child in gn:
        t = _ln(child.tag)
        if t == "goodsNomenclatureItemId":
            code = (child.text or "").strip()
        elif t == "goodsNomenclatureDescriptionPeriod":
            for d in child:
                if _ln(d.tag) != "goodsNomenclatureDescription":
                    continue
                desc = lang = None
                for dc in d:
                    tt = _ln(dc.tag)
                    if tt == "description":
                        desc = (dc.text or "").strip()
                    elif tt == "language":
                        for lc in dc:
                            if _ln(lc.tag) == "languageId":
                                lang = (lc.text or "").strip()
                if desc and lang == "PL":
                    pl = desc
                elif desc and lang == "EN":
                    en = desc
    return code, pl, en


def main():
    zip_path = (sys.argv[1] if len(sys.argv) > 1
                else "polish_aimport/startup-file-20260101T084600.zip")
    zf = zipfile.ZipFile(zip_path)
    name = _base_xml_name(zf)

    conn = isztar_pl.connect()
    isztar_pl.ensure_schema(conn)

    valid_date = None
    section = None       # current find*Response wrapper (for memory cleanup)
    depth = 0
    n = records = 0

    with zf.open(name) as f:
        context = ET.iterparse(f, events=("start", "end"))
        _, _root = next(context)          # consume the document root
        for ev, el in context:
            if ev == "start":
                depth += 1
                tag = _ln(el.tag)
                if tag.startswith("find") and tag.endswith("Response"):
                    section = el
                continue

            # ev == "end"
            depth -= 1
            tag = _ln(el.tag)
            if tag == "databaseDate" and valid_date is None:
                valid_date = (el.text or "")[:10]

            if depth == 2:                # a record-level element just closed
                if tag == "GoodsNomenclature":
                    code, pl, en = pl_description(el)
                    desc = pl or en       # prefer Polish, fall back to English
                    if code and len(code) == 10 and desc:
                        conn.execute(
                            "INSERT OR REPLACE INTO isztar_nomenclature_pl"
                            "(code, valid_date, description, supplementary_unit) VALUES(?,?,?,?)",
                            (code, valid_date or "2026-01-01", desc, None))
                        n += 1
                        if n % 5000 == 0:
                            conn.commit()
                            print(f"  {n} PL descriptions...", flush=True)
                el.clear()                # free this record
                records += 1
                if section is not None and records % 5000 == 0:
                    section.clear()       # drop processed shells (safe between records)

    conn.commit()
    total = conn.execute(
        "SELECT COUNT(DISTINCT code) FROM isztar_nomenclature_pl").fetchone()[0]
    conn.close()
    print(f"DONE — ingested {n} PL descriptions (valid_date {valid_date}); "
          f"store distinct codes now: {total}")


if __name__ == "__main__":
    main()
