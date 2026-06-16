# AImport — Data Foundation (Step 1)

Versioned TARIC nomenclature & measures database: the legal ground truth
that the classification engine, duty calculator, and audit trail will sit on.

## What's here

```
db/schema.sql            Database schema (SQLite dev / PostgreSQL prod)
ingest/inspect_xml.py    STEP A: format discovery — run FIRST on any real file
ingest/parse_taric.py    STEP B: parser/loader with loud failure modes
ingest/report.py         STEP C: human verification report
tests/sample_taric.xml   Synthetic TARIC sample (t-shirts, steel + anti-dumping)
tests/test_parser.py     11 legal-correctness tests (all passing)
```

## Design guarantees

- **No silent fallbacks.** Unknown record types or rejected records abort
  trust: the ingest prints `INGEST INCOMPLETE — do not use for classification`.
- **Everything date-bounded.** Expired measures are kept (history = audits),
  excluded from "today" queries by date filter, never deleted.
- **Provenance.** Every load logs file hash + timestamp; every row carries a
  snapshot_id, so any classification is reproducible years later.
- **Origin logic is relational.** "China" inherits erga-omnes (1011) duties
  via geographical group membership — the same mechanism TARIC itself uses.

## How to run (developer)

```
pip install lxml
python3 tests/test_parser.py                                  # must be 11/11
python3 ingest/inspect_xml.py  your_taric_file.xml            # ALWAYS first
python3 ingest/parse_taric.py  your_taric_file.xml  taric.sqlite
python3 ingest/report.py       taric.sqlite 10 CN             # verify by eye
```

## How to get real TARIC data (no coding required) — for Wojciech

Option 1 — EU Tariff Portal XML extractions (preferred):
  1. Google "TARIC consultation" or go to the EC TARIC site
     (ec.europa.eu/taxation_customs/dds2/taric/).
  2. The new EU Tariff Portal has an "XML extractions" section where monthly
     full extractions and daily files can be downloaded as .zip — pick the
     latest FULL extraction.
  3. Upload the .zip here in our chat. Do not unzip it.

Option 2 — Swedish Customs Tariff File Distribution (open, well documented):
  https://distr.tullverket.se/tulltaxan — full files + daily diffs, free.
  (Swedish national layer included, but the TARIC core is identical EU data.)

Option 3 — data.europa.eu, dataset "EU Customs Tariff (TARIC)".

Whichever file you bring, our first action is ALWAYS:
`inspect_xml.py` → read the format report → adapt parser if the dialect
differs → run tests → ingest → verification report → you compare 10 random
codes against the official TARIC website before we trust anything.

## Next steps (per the build briefing)

2. Deterministic GRI engine (state machine, candidate narrowing)
3. Clarification loop (LLM questions, information-gain selection)
4. Output assembly (duty + ADD + certificates by origin — pure SQL, done in
   prototype form already inside report.py)
5. Audit layer / user product database
6. Invoice reading


## v0.2 — Supplementary compliance sheets (integrated)

Four further EU Tariff Portal sheets are ingested by `ingest/ingest_xlsx_v2.py`:
- **Measure conditions** (46,181 rows) — structured certificates/licences with
  action codes (replaces fragile regex parsing of duty text).
- **Measure exclusions** (31,385 rows) — country carve-outs from group measures;
  prevents false-positive compliance flags.
- **Additional codes** (3,306 EN) — resolves anti-dumping company codes (e.g.
  B009) to actual company names.
- **Legal basis** (4,480) — Official Journal provenance for the audit trail.

Run after the base ingest:
```
python3 ingest/ingest_xlsx.py    <folder> taric.sqlite
python3 ingest/ingest_xlsx_v2.py <folder> taric.sqlite
```

Tests: 21 engine + 11 parser = 32, all green.
