-- ============================================================
-- AImport — TARIC Data Foundation Schema  (v0.1)
-- Works on SQLite (dev) and PostgreSQL (production).
-- Every legal record is date-bounded: validity_start / validity_end.
-- NULL validity_end = still in force.
-- ============================================================

-- Ingest provenance: every load is logged. A classification later
-- references snapshot_id so results are reproducible forever.
CREATE TABLE IF NOT EXISTS ingest_log (
    snapshot_id     INTEGER PRIMARY KEY,
    source_file     TEXT NOT NULL,
    file_sha256     TEXT NOT NULL,
    taric_extract_date TEXT,          -- date the extract represents
    loaded_at       TEXT NOT NULL,    -- ISO timestamp of our load
    records_loaded  INTEGER NOT NULL,
    notes           TEXT
);

-- ------------------------------------------------------------
-- 1. GOODS NOMENCLATURE (the code tree)
-- goods_nomenclature_item_id: 10 digits (e.g. 6109100010)
-- producline_suffix: usually '80' for declarable lines; intermediate
--   headings carry other suffixes — BOTH are needed to identify a line.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS goods_nomenclature (
    sid             INTEGER PRIMARY KEY,      -- TARIC internal stable ID
    item_id         TEXT NOT NULL,            -- 10-digit code
    producline_suffix TEXT NOT NULL DEFAULT '80',
    validity_start  TEXT NOT NULL,
    validity_end    TEXT,
    statistical_indicator INTEGER DEFAULT 0,
    is_leaf         INTEGER DEFAULT 0,        -- 1 = declarable line
    snapshot_id     INTEGER REFERENCES ingest_log(snapshot_id)
);
CREATE INDEX IF NOT EXISTS idx_gn_item ON goods_nomenclature(item_id);

-- Indents define tree depth (number of dashes in printed tariff).
-- Hierarchy is derived from item_id ordering + indent level.
CREATE TABLE IF NOT EXISTS goods_nomenclature_indent (
    sid             INTEGER,                  -- FK to goods_nomenclature.sid
    indent_level    INTEGER NOT NULL,
    validity_start  TEXT NOT NULL,
    validity_end    TEXT,
    snapshot_id     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_gni_sid ON goods_nomenclature_indent(sid);

CREATE TABLE IF NOT EXISTS goods_nomenclature_description (
    sid             INTEGER,                  -- FK to goods_nomenclature.sid
    language_id     TEXT NOT NULL DEFAULT 'EN',
    description     TEXT NOT NULL,
    validity_start  TEXT NOT NULL,
    validity_end    TEXT,
    snapshot_id     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_gnd_sid ON goods_nomenclature_description(sid);

-- ------------------------------------------------------------
-- 2. GEOGRAPHY (origin determines everything downstream)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS geographical_area (
    sid             INTEGER PRIMARY KEY,
    area_id         TEXT NOT NULL,            -- 'CN', '1011' (erga omnes), 'EU'...
    area_code       INTEGER,                  -- 0=country 1=group 2=region
    validity_start  TEXT NOT NULL,
    validity_end    TEXT,
    snapshot_id     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ga_area ON geographical_area(area_id);

CREATE TABLE IF NOT EXISTS geographical_area_description (
    sid             INTEGER,
    language_id     TEXT DEFAULT 'EN',
    description     TEXT NOT NULL,
    snapshot_id     INTEGER
);

-- Which countries belong to which group (e.g. CN ∈ group 1011).
CREATE TABLE IF NOT EXISTS geographical_area_membership (
    member_sid      INTEGER NOT NULL,         -- the country
    group_sid       INTEGER NOT NULL,         -- the group it belongs to
    validity_start  TEXT NOT NULL,
    validity_end    TEXT,
    snapshot_id     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_gam ON geographical_area_membership(member_sid, group_sid);

-- ------------------------------------------------------------
-- 3. MEASURES (duties, anti-dumping, restrictions...)
-- A measure links: nomenclature line + measure type + geo area
--   (+ optional additional code, e.g. company-specific ADD rates)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS measure_type (
    measure_type_id TEXT PRIMARY KEY,         -- '103' third-country duty,
                                              -- '552' definitive ADD, '142' pref. duty...
    description     TEXT,
    snapshot_id     INTEGER
);

CREATE TABLE IF NOT EXISTS measure (
    sid             INTEGER PRIMARY KEY,
    goods_nomenclature_item_id TEXT NOT NULL,
    goods_nomenclature_sid     INTEGER,
    measure_type_id TEXT NOT NULL,
    geographical_area_id  TEXT NOT NULL,
    geographical_area_sid INTEGER,
    additional_code TEXT,                     -- e.g. 'C999', ADD company codes
    regulation_id   TEXT,                     -- legal basis (audit trail!)
    duty_raw        TEXT,                     -- verbatim duty text (audit: nothing lost)
    origin_name     TEXT,
    validity_start  TEXT NOT NULL,
    validity_end    TEXT,
    snapshot_id     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_m_item ON measure(goods_nomenclature_item_id);
CREATE INDEX IF NOT EXISTS idx_m_geo  ON measure(geographical_area_id);

-- Duty components: a duty can be compound (e.g. 6.5% + 1.2 EUR/kg).
CREATE TABLE IF NOT EXISTS measure_component (
    measure_sid     INTEGER NOT NULL,
    duty_expression_id TEXT,                  -- '01' ad valorem, '04' +, '99' supplementary...
    duty_amount     REAL,
    monetary_unit   TEXT,                     -- NULL => percentage
    measurement_unit TEXT,                    -- e.g. 'KGM'
    snapshot_id     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_mc ON measure_component(measure_sid);

-- Conditions: "duty X applies IF document Y presented" — this is where
-- certificates & licences live (TARIC condition/document codes).
CREATE TABLE IF NOT EXISTS measure_condition (
    measure_sid     INTEGER NOT NULL,
    condition_code  TEXT,                     -- 'B' presentation of certificate...
    certificate_type TEXT,                    -- 'Y', 'C', 'D', 'N'...
    certificate_code TEXT,                    -- e.g. 'Y929', 'C644'
    action_code     TEXT,                     -- what happens if (not) met
    snapshot_id     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_mcond ON measure_condition(measure_sid);

CREATE TABLE IF NOT EXISTS certificate_description (
    certificate_type TEXT,
    certificate_code TEXT,
    description     TEXT,
    snapshot_id     INTEGER
);

-- Footnotes: legal scope notes attached to codes or measures.
CREATE TABLE IF NOT EXISTS footnote (
    footnote_type   TEXT,
    footnote_id     TEXT,
    description     TEXT,
    snapshot_id     INTEGER
);
CREATE TABLE IF NOT EXISTS footnote_association_goods (
    goods_nomenclature_sid INTEGER,
    footnote_type   TEXT,
    footnote_id     TEXT,
    snapshot_id     INTEGER
);

-- ============================================================
-- v0.2 additions: structured conditions, exclusions, legal basis,
-- additional-code descriptions. Joined to `measure` by the composite
-- business key (goods_code, add_code, origin_code, measure_type, start).
-- ============================================================

-- Structured measure conditions (replaces regex-parsed certificate text).
-- One measure -> several condition groups (B/C/Q/Y...), each with sequenced
-- sub-conditions. certificate present + action 27 => measure applies;
-- absent + action 07/08 => measure not applicable / entry prevented.
CREATE TABLE IF NOT EXISTS measure_condition_v2 (
    goods_code      TEXT NOT NULL,
    add_code        TEXT,
    origin_code     TEXT NOT NULL,
    measure_type    TEXT NOT NULL,
    validity_start  TEXT NOT NULL,
    validity_end    TEXT,
    condition_group TEXT,                 -- 'B','C','Q','Y'...
    sequence        INTEGER,
    certificate     TEXT,                 -- 'C074','Y864','U088',...
    cond_amount     REAL,
    monetary_unit   TEXT,
    measurement_unit TEXT,
    action_code     TEXT,                 -- '27','07','08',...
    snapshot_id     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_mc2 ON measure_condition_v2(
    goods_code, origin_code, measure_type);

-- Country carve-outs from a GROUP measure. Without this, group measures
-- (e.g. erga-omnes controls) produce FALSE POSITIVES for excluded countries.
CREATE TABLE IF NOT EXISTS measure_exclusion (
    goods_code      TEXT NOT NULL,
    add_code        TEXT,
    origin_code     TEXT NOT NULL,        -- the GROUP the measure targets
    measure_type    TEXT NOT NULL,
    validity_start  TEXT NOT NULL,
    validity_end    TEXT,
    excluded_country TEXT NOT NULL,       -- ISO code carved out
    snapshot_id     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_mexcl ON measure_exclusion(
    goods_code, origin_code, measure_type);

-- Legal basis (Official Journal provenance for the audit trail).
CREATE TABLE IF NOT EXISTS legal_basis (
    legal_base      TEXT PRIMARY KEY,
    official_journal TEXT,
    page            TEXT,
    publication_date TEXT,
    snapshot_id     INTEGER
);

-- Additional-code meanings (what C999/B009/2200 etc. denote).
CREATE TABLE IF NOT EXISTS additional_code_description (
    add_code        TEXT,
    language_id     TEXT DEFAULT 'EN',
    description     TEXT,
    snapshot_id     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_acd ON additional_code_description(add_code);

-- Certificate / document code meanings (Y-codes, C-codes, U-codes...).
CREATE TABLE IF NOT EXISTS certificate_meaning (
    certificate     TEXT PRIMARY KEY,     -- 'C074','Y864',...
    description     TEXT,
    snapshot_id     INTEGER
);
