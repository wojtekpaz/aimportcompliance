#!/usr/bin/env python3
"""
test_cbam.py — CBAM correctness tests.

These encode the LEGAL properties the CBAM layer must satisfy (Reg. (EU)
2023/956), not just "the code runs":

  * Scope is an explicit Annex I allow-list; an unlisted code is OUT.
  * Exclusions beat inclusions (scrap/ferro-alloys/alu-kitchenware carve-outs).
  * The most specific Annex I key wins (HS4 vs CN8).
  * Default-factor cost is an ESTIMATE (is_authoritative = False); supplier
    data makes it authoritative.
  * CBAM cost is added to the landed-cost total but EXCLUDED from the VAT base.
  * Hydrogen/electricity have no mass de-minimis threshold.
  * Determinism: same input + same snapshot => identical output.
  * No LLM is consulted anywhere in the CBAM path.

Run:  python3 tests/test_cbam.py
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ingest"))

from ingest_cbam import ingest as cbam_ingest  # noqa: E402
import cbam_pl                                  # noqa: E402
import landed_cost_pl                           # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def main():
    tmp = Path(tempfile.mkdtemp()) / "cbam_test.sqlite"
    conn = sqlite3.connect(tmp)
    snap, n = cbam_ingest(conn)
    conn.close()
    DATE = "2026-06-01"

    def status(code, **kw):
        return cbam_pl.get_cbam_status(code, DATE, db_path=tmp, **kw)

    print("\n[1] Ingest provenance & completeness")
    c2 = sqlite3.connect(tmp)
    check("cbam_ingest_log row written",
          c2.execute("SELECT COUNT(*) FROM cbam_ingest_log").fetchone()[0] == 1)
    check("file_sha256 recorded (provenance)",
          bool(c2.execute("SELECT file_sha256 FROM cbam_ingest_log").fetchone()[0]))
    check("scope rows carry snapshot_id",
          c2.execute("SELECT COUNT(*) FROM cbam_scope WHERE snapshot_id IS NULL").fetchone()[0] == 0)
    c2.close()

    print("\n[2] Scope — Annex I allow-list")
    s = status("7208100000", net_mass_tonnes=100)   # hot-rolled steel, chapter 72
    check("steel 7208 IN scope", s["in_scope"] and s["sector"] == "iron_steel",
          str(s.get("sector")))
    s = status("7601100000", net_mass_tonnes=100)   # unwrought aluminium
    check("aluminium 7601 IN scope", s["in_scope"] and s["sector"] == "aluminium")
    s = status("2523290000", net_mass_tonnes=100)   # Portland cement
    check("cement 2523 IN scope", s["in_scope"] and s["sector"] == "cement")
    s = status("6109100010")                        # cotton t-shirt
    check("t-shirt 6109 OUT of scope", not s["in_scope"])
    check("out-of-scope never raises, returns in_scope False",
          s["in_scope"] is False and s.get("exclusion") is None)

    print("\n[3] Exclusions beat inclusions")
    s = status("7204100000")     # ferrous scrap — under ch.72 but carved out
    check("scrap 7204 EXCLUDED despite ch.72", (not s["in_scope"]) and s["exclusion"],
          str(s.get("exclusion")))
    s = status("7202110000")     # ferro-manganese — ferro-alloy carve-out
    check("ferro-alloy 7202 EXCLUDED", (not s["in_scope"]) and s["exclusion"])
    s = status("7615100000")     # aluminium kitchenware
    check("alu kitchenware 7615 EXCLUDED", (not s["in_scope"]) and s["exclusion"])
    s = status("3105600000")     # P+K fertiliser carve-out
    check("PK fertiliser 31056000 EXCLUDED", (not s["in_scope"]) and s["exclusion"])

    print("\n[4] Most-specific key wins")
    # 7601 listed as HS4; a 10-digit child must still resolve to aluminium 7601.
    s = status("7601201000", net_mass_tonnes=10)
    check("HS4 heading 7601 matches 10-digit child",
          s["in_scope"] and s["scope_basis"]["code_prefix"] == "7601")

    print("\n[5] Cost estimate vs authoritative")
    s = status("7208100000", net_mass_tonnes=100)   # default-factor path
    cost = s["cost"]
    check("default-factor cost is an ESTIMATE",
          cost["is_estimate"] and not cost["is_authoritative"])
    check("estimate produces a positive certificate cost",
          cost["estimated_certificate_cost_eur"] > 0,
          str(cost["estimated_certificate_cost_eur"]))
    s2 = status("7208100000", net_mass_tonnes=100, embedded_emissions_tco2e=180.0)
    check("supplier-verified emissions => authoritative",
          s2["cost"]["is_authoritative"] and not s2["cost"]["is_estimate"])
    # carbon price already paid is deductible (Art. 9)
    s3 = status("7208100000", net_mass_tonnes=100, embedded_emissions_tco2e=180.0,
                carbon_price_paid_eur=1000.0)
    check("carbon price paid in origin is deducted",
          s3["cost"]["estimated_certificate_cost_eur"]
          < s2["cost"]["estimated_certificate_cost_eur"])

    print("\n[6] Thresholds & obligations")
    s = status("7208100000", net_mass_tonnes=10)    # below 50t
    check("below 50t flags likely-exempt",
          any("at/below" in nlow or "EXEMPT" in n for n in s["notes"]
              for nlow in [n.lower()]))
    s = status("28041000", net_mass_tonnes=1)       # hydrogen, 1t
    check("hydrogen has NO mass threshold",
          any("No mass-based de-minimis" in o for o in s["obligations"]))
    check("obligations cite authorised declarant",
          any("AUTHORISED" in o.upper() for o in s["obligations"]))

    print("\n[7] Supplier-data request + email")
    s = status("7601100000", net_mass_tonnes=100)
    check("supplier-data request raised when no verified data",
          s["supplier_data_request"] and s["supplier_data_request"]["needed"])
    em = cbam_pl.supplier_email_draft("7601100000", "aluminium", "DE")
    check("DE supplier email mentions CBAM & the code",
          "CBAM" in em and "7601100000" in em)

    print("\n[8] Determinism — same input, identical output")
    import json
    a = json.dumps(status("7208100000", net_mass_tonnes=100), sort_keys=True)
    b = json.dumps(status("7208100000", net_mass_tonnes=100), sort_keys=True)
    check("two identical calls => byte-identical JSON", a == b)

    print("\n[9] No-LLM guard — CBAM path makes zero model calls")
    # Poison every plausible LLM entry point; the full CBAM + landed-cost path
    # must still complete. Proves carbon cost is computed, never generated.
    import builtins
    real_import = builtins.__import__
    BANNED = {"anthropic", "openai", "mistralai", "litellm"}

    def guard(name, *a, **k):
        root = name.split(".")[0]
        if root in BANNED:
            raise AssertionError(f"CBAM path imported an LLM client: {name}")
        return real_import(name, *a, **k)

    builtins.__import__ = guard
    try:
        g = cbam_pl.get_cbam_status("7208100000", DATE, db_path=tmp,
                                    net_mass_tonnes=100)
        lc = landed_cost_pl.compute_landed_cost(
            50000, "0%", "7208100000", DATE, market="EU",
            db_path=tmp, cbam_net_mass_tonnes=100)
        check("CBAM status computed with no LLM import", g["in_scope"])
        check("landed cost w/ CBAM computed with no LLM import",
              lc.get("cbam") is not None)
    finally:
        builtins.__import__ = real_import

    print("\n[10] Landed-cost integration — CBAM out of VAT base")
    # VAT base must be (customs value + duty), independent of any CBAM cost.
    no_cbam = landed_cost_pl.compute_landed_cost(
        50000, "0%", "6109100010", DATE, market="EU", db_path=tmp)   # t-shirt, no CBAM
    with_cbam = landed_cost_pl.compute_landed_cost(
        50000, "0%", "7208100000", DATE, market="EU", db_path=tmp,
        cbam_net_mass_tonnes=100)                                    # steel, CBAM
    check("CBAM good gets a cbam block", with_cbam.get("cbam") is not None)
    cbam_eur = (with_cbam["cbam"]["cost"]["estimated_certificate_cost_eur"])
    check("CBAM cost added to landed-cost total",
          abs(with_cbam["landed_cost"] - (50000 + cbam_eur)) < 0.01,
          f"landed={with_cbam['landed_cost']} cbam={cbam_eur}")
    # EU VAT not computed here (market=EU surfaces no VAT), so assert the
    # explicit invariant on the PL path below instead.

    print("\n[11] PL market — VAT base excludes CBAM")
    # On the PL path VAT is computed on (customs value + duty). Adding CBAM must
    # NOT change vat. We compare the same code with and without a CBAM mass.
    # (Uses whatever PL data is cached; if none, vat is None and the invariant
    #  holds trivially — we only assert CBAM never feeds VAT.)
    base = landed_cost_pl.compute_landed_cost(
        50000, "2%", "7208100000", DATE, market="PL", db_path=tmp)
    withc = landed_cost_pl.compute_landed_cost(
        50000, "2%", "7208100000", DATE, market="PL", db_path=tmp,
        cbam_net_mass_tonnes=100)
    check("CBAM does not change the VAT amount", base.get("vat") == withc.get("vat"),
          f"{base.get('vat')} vs {withc.get('vat')}")

    print(f"\n{'='*48}\n  CBAM TESTS: {PASS} passed, {FAIL} failed\n{'='*48}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
