"""Deterministic landed-cost computation for the PL market profile.

For ``market="PL"`` the national taxes (VAT, excise) are read from the local
ISZTAR store via ``isztar_pl.get_pl_national_measures`` — no network, no LLM,
and nothing here touches the GRI engine. VAT is ad-valorem so it is computed;
excise on many goods is *specific* (per hl/kg), so its rate is surfaced as a
note rather than guessed into the total without a known quantity (honest, not
fabricated).
"""
import re

import isztar_pl


def parse_percent(value):
    """'2.7%' / '23 %' / 0.027 -> 0.027 ; returns None if not a percentage."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"([\d.,]+)\s*%", str(value))
    if not m:
        return None
    return float(m.group(1).replace(",", ".")) / 100.0


def compute_landed_cost(customs_value, duty_rate, code=None, date=None,
                        market="EU", db_path=None,
                        cbam_net_mass_tonnes=None,
                        cbam_embedded_emissions_tco2e=None):
    """Return a deterministic landed-cost breakdown.

    customs_value : numeric base on which duty is assessed (e.g. units * unit cost).
    duty_rate     : '2.7%' or 0.027.
    market="PL"   : adds Polish VAT (computed) and surfaces excise (rate-only)
                    from the local ISZTAR cache.

    CBAM (additive, opt-in): if ``code`` is in CBAM Annex I scope, a carbon-cost
    line is attached. It is an ESTIMATE (default factors) unless
    ``cbam_embedded_emissions_tco2e`` (supplier-verified) is supplied. The CBAM
    cost is reported as its own line and added to the landed-cost total, but it
    is deliberately EXCLUDED from the VAT base — CBAM is a carbon price, not a
    fiscal duty, so it does not enter the taxable amount on import.
    """
    cv = float(customs_value)
    dr = parse_percent(duty_rate) or 0.0
    duty = round(cv * dr, 2)
    after_duty = round(cv + duty, 2)

    out = {
        "market": market,
        "currency": "PLN" if market == "PL" else "EUR",
        "customs_value": round(cv, 2),
        "duty_rate": dr,
        "duty": duty,
        "vat_rate": None,
        "vat": None,
        "excise_rate": None,
        "national_measures": [],
        "cbam": None,
        "landed_cost": after_duty,
        "pl_source": None,
        "notes": [],
    }

    if market == "PL" and code and date:
        m = isztar_pl.get_pl_national_measures(code, date, db_path=db_path)
        out["pl_source"] = m["source"]
        if not m["found"]:
            out["notes"].append("Brak danych krajowych ISZTAR dla tego kodu — wymagana ingestia.")
            return out

        vat = parse_percent(m.get("vat_standard"))
        if vat is not None:
            out["vat_rate"] = vat
            out["vat"] = round(after_duty * vat, 2)      # PL VAT base = customs value + duty
            out["landed_cost"] = round(after_duty + out["vat"], 2)

        if m.get("excise"):
            out["excise_rate"] = m["excise"][0]["rate"]
            out["notes"].append(
                "Akcyza jest stawką specyficzną (np. za hl/kg); podano stawkę, "
                "nie doliczono kwoty bez znanej ilości.")

        out["national_measures"] = [x["description"] for x in m.get("national_measures", [])]

    # ---- CBAM (additive; never alters the VAT base above) -----------------
    # Attach a carbon-cost line if the code is in Annex I scope. Estimate by
    # default; authoritative only with supplier-verified emissions. VAT has
    # already been computed on (customs value + duty), so CBAM stays out of it.
    if code and date:
        try:
            import cbam_pl
            c = cbam_pl.get_cbam_status(
                code, date, db_path=db_path,
                net_mass_tonnes=cbam_net_mass_tonnes,
                embedded_emissions_tco2e=cbam_embedded_emissions_tco2e)
            if c.get("in_scope"):
                out["cbam"] = c
                cost = c.get("cost") or {}
                cbam_eur = cost.get("estimated_certificate_cost_eur")
                if cbam_eur:
                    out["landed_cost"] = round(out["landed_cost"] + cbam_eur, 2)
                    label = ("CBAM (verified)" if cost.get("is_authoritative")
                             else "CBAM (estimate)")
                    out["notes"].append(
                        f"{label}: +{cbam_eur} EUR carbon cost added to landed "
                        "cost; excluded from VAT base (carbon price, not a duty).")
                else:
                    out["notes"].append(
                        "CBAM applies to this code but cost not quantified "
                        "(supply net mass or supplier-verified emissions).")
            elif c.get("exclusion"):
                out["cbam"] = c  # record the carve-out for the audit trail
        except Exception:
            pass  # CBAM is additive; never break the core landed-cost response

    return out


if __name__ == "__main__":
    import json
    import sys
    from datetime import date as _date
    cv = float(sys.argv[1]) if len(sys.argv) > 1 else 1000.0
    dr = sys.argv[2] if len(sys.argv) > 2 else "2.7%"
    code = sys.argv[3] if len(sys.argv) > 3 else "2208601100"
    print(json.dumps(
        compute_landed_cost(cv, dr, code, _date.today().isoformat(), market="PL"),
        ensure_ascii=False, indent=2))
