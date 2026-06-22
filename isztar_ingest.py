"""ISZTAR4 (Polish national tariff) ingestion — STANDALONE, network-touching.

Pulls Polish nomenclature descriptions + national measures (VAT, excise,
national non-tariff) from the ISZTAR4 REST API and caches them into
``data_isztar_pl.sqlite``, keyed by 10-digit code + validity date. This is the
ONLY component permitted to hit the network; the request path never imports it.

API contract (read from https://ext-isztar4.mf.gov.pl/taryfa_celna/json-api,
2026-06-22 — note the prompt's /tariff/documentation path is stale):

  base: https://ext-isztar4.mf.gov.pl/tariff/rest/
  GET goods-nomenclature/measures
        ?nomenclatureCode=<10-digit, zero-padded>
        &date=YYYY-MM-DD
        &language=PL
  ->  { "nomenclature": {"code","description","supplementaryUnit"},
        "tariffMeasures": [...],
        "nonTariffMeasures": [ {"description","country":{"code"},"regulation":{"code"}} ],
        "taxes": [ {"description","dutyAmount","dutyAmountWithCodes",
                    "additionalCode":{"code","description"},"country":{"code"}} ] }

  taxes[] carries VAT  ("Podatek od towarów i usług (VAT)")
                 and excise ("Podatek akcyzowy") — the national data the EU
                 TARIC export does NOT contain.

  No authentication, licensing, or rate limit stated in the documentation.

Usage:
  python3 isztar_ingest.py --date 2025-06-02 --sample
  python3 isztar_ingest.py --date 2025-06-02 2208601100 2402209000
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

import isztar_pl

BASE = "https://ext-isztar4.mf.gov.pl/tariff/rest"
USER_AGENT = "aimport-isztar-ingest/1.0 (customs compliance tooling)"

# Representative codes that carry Polish national data (VAT and/or excise):
# spirits, cigarettes, petrol, passenger car, cotton t-shirt.
SAMPLE_CODES = [
    "2208601100",  # vodka — VAT + excise
    "2402209000",  # cigarettes — VAT + excise
    "2710124500",  # petrol — VAT + excise
    "8703231910",  # passenger car — VAT + excise (vehicles)
    "6109100010",  # cotton t-shirt — VAT only
]


def classify_tax(description):
    d = (description or "").lower()
    if "vat" in d or "towarów i usług" in d:
        return "VAT"
    if "akcyz" in d:
        return "EXCISE"
    return "OTHER"


def fetch_measures(code, date, lang="PL", timeout=30):
    """Live call to ISZTAR4. Only the ingestion step calls this."""
    code10 = isztar_pl.normalize_code(code)
    qs = urllib.parse.urlencode({
        "nomenclatureCode": code10, "date": date, "language": lang})
    url = f"{BASE}/goods-nomenclature/measures?{qs}"
    # NOTE: the ISZTAR4 server returns 406 for an explicit Accept: application/json;
    # it negotiates fine with */* (matches the documented JSON response).
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def ingest_code(conn, code, date):
    data = fetch_measures(code, date)
    code10 = isztar_pl.normalize_code(code)
    nom = data.get("nomenclature") or {}

    conn.execute(
        "INSERT OR REPLACE INTO isztar_nomenclature_pl "
        "(code, valid_date, description, supplementary_unit) VALUES (?,?,?,?)",
        (code10, date, nom.get("description"), nom.get("supplementaryUnit")))

    # refresh this (code, date) slice so re-runs are idempotent
    conn.execute("DELETE FROM isztar_taxes_pl WHERE code=? AND valid_date=?", (code10, date))
    conn.execute("DELETE FROM isztar_national_measures_pl WHERE code=? AND valid_date=?", (code10, date))

    n_tax = n_nat = 0
    for t in (data.get("taxes") or []):
        ac = t.get("additionalCode") or {}
        conn.execute(
            "INSERT INTO isztar_taxes_pl (code, valid_date, tax_type, description, "
            "duty_amount, duty_amount_with_codes, additional_code, additional_code_desc, country) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (code10, date, classify_tax(t.get("description")), t.get("description"),
             t.get("dutyAmount"), t.get("dutyAmountWithCodes"),
             ac.get("code"), ac.get("description"), (t.get("country") or {}).get("code")))
        n_tax += 1
    for m in (data.get("nonTariffMeasures") or []):
        conn.execute(
            "INSERT INTO isztar_national_measures_pl (code, valid_date, description, country, regulation) "
            "VALUES (?,?,?,?,?)",
            (code10, date, m.get("description"),
             (m.get("country") or {}).get("code"), (m.get("regulation") or {}).get("code")))
        n_nat += 1
    conn.commit()
    return n_tax, n_nat


def main():
    ap = argparse.ArgumentParser(description="Ingest ISZTAR4 PL national data into the local cache.")
    ap.add_argument("--date", required=True, help="validity date, YYYY-MM-DD")
    ap.add_argument("--sample", action="store_true", help="ingest the built-in representative code set")
    ap.add_argument("--sleep", type=float, default=0.5, help="seconds between API calls (politeness)")
    ap.add_argument("codes", nargs="*", help="explicit 10-digit codes to ingest")
    args = ap.parse_args()

    codes = args.codes or (SAMPLE_CODES if args.sample else [])
    if not codes:
        ap.error("provide one or more codes, or pass --sample")

    conn = isztar_pl.connect()
    isztar_pl.ensure_schema(conn)
    ok = tax_total = nat_total = 0
    for c in codes:
        try:
            nt, nn = ingest_code(conn, c, args.date)
            ok += 1
            tax_total += nt
            nat_total += nn
            print(f"  ingested {isztar_pl.normalize_code(c)}  taxes={nt} national={nn}")
            time.sleep(args.sleep)
        except Exception as e:  # noqa: BLE001 — report and continue
            print(f"  FAILED {c}: {e}", file=sys.stderr)

    nom_rows = conn.execute("SELECT COUNT(*) FROM isztar_nomenclature_pl").fetchone()[0]
    tax_rows = conn.execute("SELECT COUNT(*) FROM isztar_taxes_pl").fetchone()[0]
    nat_rows = conn.execute("SELECT COUNT(*) FROM isztar_national_measures_pl").fetchone()[0]
    conn.close()
    print(f"\nINGEST COMPLETE — codes ok={ok}/{len(codes)} for date {args.date}")
    print(f"store rows -> nomenclature={nom_rows}  taxes={tax_rows}  national_measures={nat_rows}")


if __name__ == "__main__":
    main()
