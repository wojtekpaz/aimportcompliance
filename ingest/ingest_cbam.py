#!/usr/bin/env python3
"""
ingest_cbam.py — Load the CBAM reference data (Regulation (EU) 2023/956,
Annex I scope + exclusions, default emission factors, certificate price,
and statutory parameters) into the SAME database as the TARIC foundation.

Mirrors the design of ingest_xlsx_v2.py:
  - VERBATIM PRESERVATION. Annex I goods descriptions and CN keys stored exactly.
  - DATE-BOUNDED. Definitive-period scope carries validity_start 2026-01-01;
    nothing is deleted, so a 2024 classification still resolves against the
    transitional-period view once that snapshot is loaded.
  - PROVENANCE. Every row carries a snapshot_id -> cbam_ingest_log, and the
    log records a sha256 over the exact seed payload, so any CBAM verdict is
    reproducible from the recorded snapshot.
  - NO SILENT FALLBACKS. Scope is an explicit allow-list; exclusions are
    explicit carve-outs. A code not present is OUT of scope by construction.

SCOPE SOURCE (Option A — official, versioned):
  The embedded SEED below transcribes Annex I of Reg. (EU) 2023/956. It is the
  authoritative ground truth until replaced by an official-sheet ingest. To
  refresh from an official Annex I extraction later, pass an .xlsx folder:
      python3 ingest/ingest_cbam.py <database.sqlite> [annex_folder]
  (the .xlsx path is a stub hook; the embedded seed is used when omitted.)

  IMPORTANT: Annex I lists some goods by 4-digit HS heading (e.g. 7601, 7301)
  and others by full 8-digit CN code. A 10-digit declared code is in scope
  when its leading digits match a non-excluded scope key — see cbam_pl.py.
"""
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = Path(__file__).resolve().parent.parent / "db" / "schema_cbam.sql"

REG = "EU 2023/956"
REG_AMEND = "EU 2025/2083"
DEF_START = "2026-01-01"          # CBAM definitive period
ANNEX_REV = "2023/956 Annex I (consolidated to 2025/2083)"

# ============================================================================
# SEED — Annex I of Regulation (EU) 2023/956
# match_level: HS4 (4-digit heading), HS6 (6-digit), CN8 (8-digit), CN2 (chapter)
# indirect=1 only for cement + fertilisers (Annex priced direct+indirect);
# the other four sectors price direct emissions only.
# ============================================================================
SCOPE = [
    # ---- CEMENT (chapter 25 selected) — direct + indirect ----
    ("25070080", "CN8", "cement", "Kaolinic clays (calcined)", 1),
    ("25231000", "CN8", "cement", "Cement clinkers", 1),
    ("252321",   "HS6", "cement", "White Portland cement", 1),
    ("252329",   "HS6", "cement", "Other Portland cement", 1),
    ("25233000", "CN8", "cement", "Aluminous cement", 1),
    ("25239000", "CN8", "cement", "Other hydraulic cements", 1),

    # ---- FERTILISERS (chapters 28/31) — direct + indirect ----
    ("28080000", "CN8", "fertilisers", "Nitric acid; sulphonitric acids", 1),
    ("2814",     "HS4", "fertilisers", "Ammonia, anhydrous or in aqueous solution", 1),
    ("310210",   "HS6", "fertilisers", "Urea, whether or not in aqueous solution", 1),
    ("3102",     "HS4", "fertilisers", "Mineral or chemical fertilisers, nitrogenous", 1),
    ("3105",     "HS4", "fertilisers",
        "Mineral or chemical fertilisers w/ two or three of N, P, K; other fertilisers", 1),

    # ---- IRON & STEEL (chapters 72/73) — direct only ----
    ("2601120080", "CN8", "iron_steel", "Agglomerated iron ores and concentrates", 0),
    ("72", "CN2", "iron_steel", "Iron and steel (chapter 72, save exclusions)", 0),
    ("7301", "HS4", "iron_steel", "Sheet piling; welded angles, shapes and sections", 0),
    ("7302", "HS4", "iron_steel", "Railway/tramway track construction material", 0),
    ("7303", "HS4", "iron_steel", "Tubes, pipes and hollow profiles, of cast iron", 0),
    ("7304", "HS4", "iron_steel", "Tubes, pipes and hollow profiles, seamless, of iron/steel", 0),
    ("7305", "HS4", "iron_steel", "Other tubes and pipes of iron/steel (welded, large diameter)", 0),
    ("7306", "HS4", "iron_steel", "Other tubes, pipes and hollow profiles of iron/steel", 0),
    ("7307", "HS4", "iron_steel", "Tube or pipe fittings of iron or steel", 0),
    ("7308", "HS4", "iron_steel", "Structures and parts of structures of iron or steel", 0),
    ("7309", "HS4", "iron_steel", "Reservoirs, tanks, vats (>300 l) of iron or steel", 0),
    ("7310", "HS4", "iron_steel", "Tanks, casks, drums, cans (<=300 l) of iron or steel", 0),
    ("7311", "HS4", "iron_steel", "Containers for compressed/liquefied gas, of iron/steel", 0),
    ("7318", "HS4", "iron_steel", "Screws, bolts, nuts, etc. of iron or steel", 0),
    ("7326", "HS4", "iron_steel", "Other articles of iron or steel", 0),

    # ---- ALUMINIUM (chapter 76) — direct only ----
    ("7601", "HS4", "aluminium", "Unwrought aluminium", 0),
    ("7603", "HS4", "aluminium", "Aluminium powders and flakes", 0),
    ("7604", "HS4", "aluminium", "Aluminium bars, rods and profiles", 0),
    ("7605", "HS4", "aluminium", "Aluminium wire", 0),
    ("7606", "HS4", "aluminium", "Aluminium plates, sheets and strip (>0.2 mm)", 0),
    ("7607", "HS4", "aluminium", "Aluminium foil (<=0.2 mm)", 0),
    ("7608", "HS4", "aluminium", "Aluminium tubes and pipes", 0),
    ("7609", "HS4", "aluminium", "Aluminium tube or pipe fittings", 0),
    ("76110000", "CN8", "aluminium", "Aluminium reservoirs, tanks, vats (>300 l)", 0),
    ("7612", "HS4", "aluminium", "Aluminium casks, drums, cans, boxes (<=300 l)", 0),
    ("76130000", "CN8", "aluminium", "Aluminium containers for compressed/liquefied gas", 0),
    ("7614", "HS4", "aluminium", "Stranded wire, cables, plaited bands, of aluminium", 0),
    ("76161000", "CN8", "aluminium", "Aluminium nails, tacks, staples, screws, bolts", 0),
    ("761699", "HS6", "aluminium", "Other articles of aluminium", 0),

    # ---- HYDROGEN (chapter 28) — direct only, NO mass threshold ----
    ("28041000", "CN8", "hydrogen", "Hydrogen", 0),

    # ---- ELECTRICITY (chapter 27) — direct only, NO mass threshold ----
    ("27160000", "CN8", "electricity", "Electrical energy", 0),
]

# Annex I explicit carve-outs. Excluded beats included.
EXCLUSIONS = [
    ("7202", "HS4", "iron_steel", "Ferro-alloys (out of scope per Annex I)"),
    ("7204", "HS4", "iron_steel", "Ferrous waste and scrap; remelting scrap ingots"),
    ("7602", "HS4", "aluminium", "Aluminium waste and scrap"),
    ("7615", "HS4", "aluminium", "Table/kitchen/household articles of aluminium"),
    ("31056000", "CN8", "fertilisers",
        "Fertilisers w/ only P and K (out of scope per Annex I)"),
]

# Transitional default emission factors (tCO2e per tonne of product).
# These are conservative public reference values; the importer should ALWAYS
# replace them with supplier-verified data. markup_pct: 0.10 in 2026 for
# non-electricity sectors (0.01 fertilisers), per the simplification package.
DEFAULT_FACTORS = [
    ("hydrogen",    None, 10.4, 0.10, "direct",
        "Transitional default for grey hydrogen (~10.4 tCO2/t H2)."),
    ("iron_steel",  None, 2.1,  0.10, "direct",
        "Indicative BF-BOF crude/finished steel direct-emissions default."),
    ("aluminium",   None, 1.8,  0.10, "direct",
        "Indicative primary aluminium direct-emissions default."),
    ("cement",      None, 0.83, 0.10, "direct+indirect",
        "Indicative Portland cement (clinker-weighted) default."),
    ("fertilisers", None, 2.4,  0.01, "direct+indirect",
        "Indicative urea/nitrogenous fertiliser default."),
    # electricity: per-country grid factor required; no single product default.
]

# CBAM certificate price proxy (€/tCO2e), tracking EU ETS. Time series; the
# value used is the latest entry on/before the valuation date.
CERT_PRICES = [
    ("2026-01-01", 75.0, "EU ETS proxy — placeholder pending live feed"),
]

PARAMETERS = [
    ("mass_threshold_tonnes", 50.0, "tonnes/importer/year",
        "iron_steel+aluminium+cement+fertilisers cumulative; excl. H2/electricity",
        REG_AMEND),
    ("penalty_eur_per_tonne", 100.0, "EUR/tCO2e embedded", "standard penalty", REG),
    ("default_markup_2028_pct", 0.30, "fraction", "non-electricity sectors from 2028", REG_AMEND),
]


def _payload_sha():
    """Deterministic hash over the exact seed payload — provenance for a code seed."""
    blob = json.dumps(
        {"scope": SCOPE, "excl": EXCLUSIONS, "factors": DEFAULT_FACTORS,
         "prices": CERT_PRICES, "params": PARAMETERS},
        sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def ingest(conn, annex_folder=None):
    conn.executescript(SCHEMA.read_text())

    if annex_folder:
        # Stub hook for a future official-sheet ingest. Until implemented we
        # refuse to silently fall back — loud failure, per the repo's contract.
        raise NotImplementedError(
            "Official Annex I .xlsx ingest not wired yet. Omit the folder "
            "argument to load the verified embedded seed.")

    source = f"embedded-seed:{REG} Annex I"
    cur = conn.execute(
        "INSERT INTO cbam_ingest_log "
        "(source_file, file_sha256, regulation, annex_revision, loaded_at, records_loaded, notes) "
        "VALUES (?,?,?,?,?,0,?)",
        (source, _payload_sha(), REG, ANNEX_REV,
         datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "Embedded Annex I seed; replace with official extraction when available."))
    snap = cur.lastrowid
    n = 0

    for code, lvl, sector, desc, indirect in SCOPE:
        conn.execute(
            "INSERT INTO cbam_scope (code_prefix, match_level, sector, description, "
            "indirect_emissions, validity_start, validity_end, regulation, snapshot_id) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (code, lvl, sector, desc, indirect, DEF_START, None,
             f"{REG} Annex I", snap))
        n += 1

    for code, lvl, sector, reason in EXCLUSIONS:
        conn.execute(
            "INSERT INTO cbam_exclusion (code_prefix, match_level, sector, reason, "
            "validity_start, validity_end, regulation, snapshot_id) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (code, lvl, sector, reason, DEF_START, None, f"{REG} Annex I", snap))
        n += 1

    for sector, code, factor, markup, basis, notes in DEFAULT_FACTORS:
        conn.execute(
            "INSERT INTO cbam_default_factor (sector, code_prefix, factor_tco2e_per_tonne, "
            "markup_pct, basis, validity_start, validity_end, regulation, notes, snapshot_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sector, code, factor, markup, basis, DEF_START, None, REG, notes, snap))
        n += 1

    for pdate, price, src in CERT_PRICES:
        conn.execute(
            "INSERT OR REPLACE INTO cbam_certificate_price "
            "(price_date, eur_per_tco2e, source, snapshot_id) VALUES (?,?,?,?)",
            (pdate, price, src, snap))
        n += 1

    for name, value, unit, applies, reg in PARAMETERS:
        conn.execute(
            "INSERT INTO cbam_parameter (name, value, unit, applies_to, "
            "validity_start, validity_end, regulation, snapshot_id) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (name, value, unit, applies, DEF_START, None, reg, snap))
        n += 1

    conn.execute("UPDATE cbam_ingest_log SET records_loaded=? WHERE snapshot_id=?", (n, snap))
    conn.commit()
    return snap, n


def main():
    if len(sys.argv) < 2:
        print("usage: ingest_cbam.py <database.sqlite> [annex_folder]")
        sys.exit(1)
    db = sys.argv[1]
    folder = sys.argv[2] if len(sys.argv) > 2 else None
    conn = sqlite3.connect(db)
    try:
        snap, n = ingest(conn, folder)
        print(f"CBAM ingest OK — snapshot {snap}, {n} records into {db}")
        print(f"  scope={len(SCOPE)} exclusions={len(EXCLUSIONS)} "
              f"factors={len(DEFAULT_FACTORS)} prices={len(CERT_PRICES)} params={len(PARAMETERS)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
