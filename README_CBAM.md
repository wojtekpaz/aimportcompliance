# CBAM Implementation — AImport

Carbon Border Adjustment Mechanism (Regulation (EU) 2023/956, amended by
2025/2083) built into the existing TARIC data foundation. Same design
guarantees as the rest of the repo: date-bounded, snapshot provenance, no
silent fallbacks, deterministic (zero LLM).

## Files

| File | Status | What it is |
|------|--------|-----------|
| `db/schema_cbam.sql` | **new** | CBAM tables (scope, exclusions, default factors, certificate price, parameters). Additive to `schema.sql`; never alters TARIC tables. |
| `ingest/ingest_cbam.py` | **new** | Loads the verified Annex I seed (scope + exclusions + factors + price + thresholds) with a sha256 over the seed payload into `cbam_ingest_log`. |
| `cbam_pl.py` | **new** | Deterministic local lookup — modelled on `isztar_pl.py`. Scope/exclusion decision, sector, cost estimate, obligations, supplier-data request, EN/DE/PL supplier email. |
| `landed_cost_pl.py` | **edited** | Adds an optional CBAM line. Additive: existing calls unchanged; CBAM cost is added to the landed-cost total but **excluded from the VAT base**. |
| `server/app.py` | **edited** | New `/api/cbam` and `/api/cbam/supplier-email`; `/api/landed-cost` gains optional CBAM params. All display-only, outside the GRI flow. |
| `tests/test_cbam.py` | **new** | 28 legal-correctness tests incl. determinism + a no-LLM guard. |

## Install (developer)

```bash
# 1. Load CBAM reference data into the SAME db the engine uses
python3 ingest/ingest_cbam.py data_taric.sqlite

# 2. Run the tests (must be 28/28)
python3 tests/test_cbam.py

# 3. Existing tests still green
python3 tests/test_parser.py        # 11/11
```

The lookup reads `data_taric.sqlite` by default (override with `CBAM_DB`).

## How it behaves

- **Scope is an allow-list.** A code is in scope only if it matches a
  non-excluded Annex I key. Exclusions (scrap 7204/7602, ferro-alloys 7202,
  alu kitchenware 7615, P+K fertiliser 31056000) are checked first and win.
- **Most specific key wins.** Annex I lists some goods by 4-digit heading
  (7601, 7301) and others by 8-digit CN code; a 10-digit declared code
  resolves to the longest matching prefix.
- **Cost is honest.** Default emission factors give a transparent ESTIMATE
  (`is_authoritative=false`). Supplier-verified emissions make it authoritative
  and usually lower it. Carbon price already paid in the origin is deducted
  (Art. 9). CBAM cost is a carbon price, **not** in the VAT base.
- **Obligations are surfaced** with citations: authorised declarant, annual
  declaration (30 Sep), certificate surrender, 50t de-minimis (not for
  hydrogen/electricity), penalty €100/t (3–5× unauthorised).
- **Supplier-data request** flag + EN/DE/PL email draft feed the existing
  clarification/outreach pattern.

## API examples

```
GET /api/cbam?code=7208100000&net_mass_tonnes=100
  -> in_scope=true, sector=iron_steel, estimated_certificate_cost_eur=17325.0,
     is_estimate=true, obligations[…], supplier_data_request{…}, sources[…]

GET /api/cbam?code=7204100000
  -> in_scope=false, exclusion={reason:"ferrous waste and scrap"}

GET /api/landed-cost?customs_value=50000&duty_rate=0%&code=7208100000&cbam_net_mass_tonnes=100
  -> landed_cost includes the CBAM line; VAT base unaffected

GET /api/cbam/supplier-email?code=7601100000&sector=aluminium&lang=DE
  -> ready-to-send supplier emissions-data request
```

## What to do next (when you have time)

1. **Replace the seed with an official Annex I extraction.** `ingest_cbam.py`
   has a stub hook (`annex_folder` arg) that currently raises rather than
   silently fall back — wire it to an official `.xlsx`/XML sheet the same way
   `ingest_xlsx_v2.py` loads the supplementary sheets, and the verbatim Annex I
   list becomes the snapshot of record.
2. **Wire a live ETS price feed** into `cbam_certificate_price` (currently a
   single proxy row at €75/tCO2e). The lookup already uses the latest price
   on/before the valuation date, so this is a data refresh, not a code change.
3. **Verify the embedded scope** against the official Annex I before relying on
   it commercially — the seed transcribes the regulation but, per the repo's
   own rule, a human should eyeball it against the source.
