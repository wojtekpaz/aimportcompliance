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
                        market="EU", db_path=None):
    """Return a deterministic landed-cost breakdown.

    customs_value : numeric base on which duty is assessed (e.g. units * unit cost).
    duty_rate     : '2.7%' or 0.027.
    market="PL"   : adds Polish VAT (computed) and surfaces excise (rate-only)
                    from the local ISZTAR cache.
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
