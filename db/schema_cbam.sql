-- ============================================================
-- AImport — CBAM Data Foundation Schema  (v0.1)
-- Carbon Border Adjustment Mechanism — Regulation (EU) 2023/956,
-- as amended by Reg. (EU) 2025/2083 (simplification).
--
-- Same design guarantees as schema.sql:
--   * Every legal record is date-bounded (validity_start / validity_end).
--     NULL validity_end = still in force.
--   * Provenance: every row carries snapshot_id -> cbam_ingest_log.
--   * No silent fallbacks: scope is an explicit allow-list of CN codes
--     from Annex I; a code that is not present is OUT of scope, never
--     guessed in.
--   * Exclusions are first-class: Annex I carves out specific codes
--     (ferro-alloys 7202, scrap 7204/7602, alu kitchenware 7615, NP/K
--     fertiliser 31056000). Storing them prevents FALSE POSITIVES.
--
-- This file is additive. It is applied to the SAME database as
-- schema.sql (the TARIC foundation) and never alters those tables.
-- ============================================================

-- Ingest provenance for CBAM reference data (kept separate from the TARIC
-- ingest_log so a CBAM reload never perturbs TARIC snapshot numbering).
CREATE TABLE IF NOT EXISTS cbam_ingest_log (
    snapshot_id     INTEGER PRIMARY KEY,
    source_file     TEXT NOT NULL,
    file_sha256     TEXT NOT NULL,
    regulation      TEXT,             -- e.g. 'EU 2023/956 Annex I'
    annex_revision  TEXT,            -- which consolidated version the scope reflects
    loaded_at       TEXT NOT NULL,    -- ISO timestamp of our load
    records_loaded  INTEGER NOT NULL,
    notes           TEXT
);

-- ------------------------------------------------------------
-- 1. CBAM SCOPE  (Annex I allow-list, by CN code or HS heading)
--
-- match_level distinguishes how broad the listed key is:
--   'CN8'     -> matches an 8-digit CN code exactly (+ its 10-digit children)
--   'HS6'     -> matches a 6-digit subheading prefix
--   'HS4'     -> matches a 4-digit heading prefix (e.g. 7601, 7301)
--   'CN2'     -> a whole chapter listed wholesale (rare; e.g. parts of 72)
-- The prefix semantics mirror Annex I, which lists some goods by 4-digit
-- heading and others by full 8-digit code. A 10-digit declared code is in
-- scope if its leading digits match a non-excluded scope key.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cbam_scope (
    code_prefix     TEXT NOT NULL,    -- digits only: '7208', '76011000', '25070080'
    match_level     TEXT NOT NULL,    -- 'HS4' | 'HS6' | 'CN8' | 'CN2'
    sector          TEXT NOT NULL,    -- 'iron_steel' | 'aluminium' | 'cement'
                                      -- | 'fertilisers' | 'hydrogen' | 'electricity'
    description     TEXT,             -- verbatim Annex I goods description
    indirect_emissions INTEGER DEFAULT 0,  -- 1 if indirect emissions priced
                                           -- (cement + fertilisers per Annex; else direct only)
    validity_start  TEXT NOT NULL,
    validity_end    TEXT,
    regulation      TEXT,             -- legal basis (audit trail)
    snapshot_id     INTEGER REFERENCES cbam_ingest_log(snapshot_id)
);
CREATE INDEX IF NOT EXISTS idx_cbam_scope_prefix ON cbam_scope(code_prefix);
CREATE INDEX IF NOT EXISTS idx_cbam_scope_sector ON cbam_scope(sector);

-- ------------------------------------------------------------
-- 2. CBAM EXCLUSIONS  (Annex I carve-outs)
-- A declared code matching one of these is OUT of scope even if it also
-- matches a broader scope prefix (e.g. 7204 sits under chapter-72 steel
-- but is excluded as scrap). Excluded wins over included — checked first.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cbam_exclusion (
    code_prefix     TEXT NOT NULL,
    match_level     TEXT NOT NULL,    -- same vocabulary as cbam_scope
    sector          TEXT,
    reason          TEXT,             -- e.g. 'ferrous waste and scrap'
    validity_start  TEXT NOT NULL,
    validity_end    TEXT,
    regulation      TEXT,
    snapshot_id     INTEGER REFERENCES cbam_ingest_log(snapshot_id)
);
CREATE INDEX IF NOT EXISTS idx_cbam_excl_prefix ON cbam_exclusion(code_prefix);

-- ------------------------------------------------------------
-- 3. DEFAULT EMISSION FACTORS  (tCO2e per tonne of product)
--
-- Used ONLY as a transparent fallback when the importer has no
-- supplier-verified embedded-emissions data. Every figure produced from a
-- default MUST be surfaced as is_authoritative = false downstream.
--
-- markup_pct: the EU default-value mark-up added on top of the base factor
-- (10% in 2026 rising to 30% from 2028 for non-electricity sectors;
-- fertilisers carry only 1%). Stored as a fraction, e.g. 0.10.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cbam_default_factor (
    sector          TEXT NOT NULL,
    code_prefix     TEXT,             -- NULL => sector-wide default
    factor_tco2e_per_tonne REAL NOT NULL,
    markup_pct      REAL DEFAULT 0.0, -- default-value mark-up as a fraction
    basis           TEXT,             -- 'direct' | 'direct+indirect'
    validity_start  TEXT NOT NULL,
    validity_end    TEXT,
    regulation      TEXT,
    notes           TEXT,
    snapshot_id     INTEGER REFERENCES cbam_ingest_log(snapshot_id)
);
CREATE INDEX IF NOT EXISTS idx_cbam_factor_sector ON cbam_default_factor(sector);

-- ------------------------------------------------------------
-- 4. CBAM CERTIFICATE PRICE  (€ per tCO2e; tracks EU ETS)
-- A small time series. The price used for any estimate is the most recent
-- entry on/before the valuation date — never a hard-coded constant.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cbam_certificate_price (
    price_date      TEXT NOT NULL,    -- ISO date the price applies from
    eur_per_tco2e   REAL NOT NULL,
    source          TEXT,             -- e.g. 'EU ETS weekly average (proxy)'
    snapshot_id     INTEGER REFERENCES cbam_ingest_log(snapshot_id),
    PRIMARY KEY (price_date)
);

-- ------------------------------------------------------------
-- 5. CBAM PARAMETERS  (thresholds, penalties — date-bounded scalars)
-- Avoids magic numbers in code; every scalar is sourced and date-bounded.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cbam_parameter (
    name            TEXT NOT NULL,    -- 'mass_threshold_tonnes', 'penalty_eur_per_tonne'
    value           REAL NOT NULL,
    unit            TEXT,
    applies_to      TEXT,             -- optional scope note (e.g. 'excl. H2/electricity')
    validity_start  TEXT NOT NULL,
    validity_end    TEXT,
    regulation      TEXT,
    snapshot_id     INTEGER REFERENCES cbam_ingest_log(snapshot_id)
);
CREATE INDEX IF NOT EXISTS idx_cbam_param_name ON cbam_parameter(name);
