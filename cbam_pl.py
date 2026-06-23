"""CBAM (Carbon Border Adjustment Mechanism) — deterministic LOCAL lookup.

Regulation (EU) 2023/956 (as amended by 2025/2083). This module reads ONLY
from the local SQLite database that the CBAM ingest (``ingest/ingest_cbam.py``)
populates. It never imports networking and never calls an LLM — CBAM status is
a deterministic lookup keyed by the declared code + date, exactly like
``isztar_pl`` is for Polish national measures.

WHAT IT DOES
    * Decide whether a declared 10-digit code is IN CBAM scope at a date,
      honouring Annex I exclusions (exclusion beats inclusion).
    * Identify the sector and whether indirect emissions are priced.
    * Produce a TRANSPARENT cost ESTIMATE from default emission factors x the
      CBAM certificate price — clearly flagged as non-authoritative until the
      importer supplies supplier-verified embedded-emissions data.
    * Surface the importer's obligations (authorised declarant, quarterly /
      annual declaration, certificate surrender, 50t threshold) with legal
      citations for the audit trail.

DETERMINISM CONTRACT
    Every figure is computed in Python from values read out of the database.
    The LLM is never consulted. Same input + same snapshot => identical output.

NOTE: like isztar_pl, this is a lookup/estimator. It is surfaced as a
display-only endpoint AFTER a determination exists; it is NOT part of the GRI
control flow.
"""
import os
import sqlite3
from datetime import date as _date
from pathlib import Path

# CBAM reference data lives in the same TARIC foundation DB by default
# (schema_cbam.sql is additive to schema.sql). Override with CBAM_DB.
DB_PATH = Path(os.environ.get(
    "CBAM_DB",
    Path(__file__).resolve().parent / "data_taric.sqlite",
))


def connect(db_path=None):
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def normalize_code(code):
    """Normalise to a 10-digit TARIC code, digits only.

    Tariff codes are hierarchical and grow on the RIGHT (chapter -> heading ->
    subheading -> CN8 -> TARIC10), so a shorter code is widened by padding
    zeros on the RIGHT: an 8-digit CN code '28041000' becomes '2804100000',
    NOT '0028041000'. (This differs from isztar_pl.normalize_code, which
    assumes an already-10-digit input and left-pads; CBAM scope is keyed by
    Annex I CN8/HS4 prefixes, so right-padding is the correct semantics here.)
    Anything longer than 10 digits is truncated to 10.
    """
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    if len(digits) >= 10:
        return digits[:10]
    return digits.ljust(10, "0")


def _prefixes(code10):
    """The candidate Annex-I keys a 10-digit code could match, broad->narrow:
    chapter (2), heading (4), subheading (6), CN8 (8), full (10)."""
    return [code10[:2], code10[:4], code10[:6], code10[:8], code10]


def _match(conn, table, code10, date):
    """Return the most specific in-force row in `table` whose code_prefix is a
    leading prefix of code10. Longer prefix wins (most specific). Date-bounded.
    """
    cands = _prefixes(code10)
    rows = conn.execute(
        f"SELECT * FROM {table} "
        f"WHERE code_prefix IN ({','.join('?' * len(cands))}) "
        f"AND validity_start <= ? "
        f"AND (validity_end IS NULL OR validity_end >= ?)",
        (*cands, date, date)).fetchall()
    if not rows:
        return None
    # most specific = longest matching prefix
    return sorted(rows, key=lambda r: len(r["code_prefix"]))[-1]


def _latest_price(conn, date):
    row = conn.execute(
        "SELECT eur_per_tco2e, price_date, source FROM cbam_certificate_price "
        "WHERE price_date <= ? ORDER BY price_date DESC LIMIT 1",
        (date,)).fetchone()
    return row


def _param(conn, name, date):
    row = conn.execute(
        "SELECT value, unit, applies_to, regulation FROM cbam_parameter "
        "WHERE name=? AND validity_start <= ? "
        "AND (validity_end IS NULL OR validity_end >= ?) "
        "ORDER BY validity_start DESC LIMIT 1",
        (name, date, date)).fetchone()
    return row


def _default_factor(conn, sector, code10, date):
    """Most specific in-force default factor for a sector (code-specific first)."""
    cands = _prefixes(code10)
    row = conn.execute(
        "SELECT * FROM cbam_default_factor WHERE sector=? "
        "AND (code_prefix IS NULL OR code_prefix IN (%s)) "
        "AND validity_start <= ? AND (validity_end IS NULL OR validity_end >= ?) "
        "ORDER BY (code_prefix IS NULL), LENGTH(COALESCE(code_prefix,'')) DESC, "
        "validity_start DESC LIMIT 1" % ",".join("?" * len(cands)),
        (sector, *cands, date, date)).fetchone()
    return row


def get_cbam_status(code, date=None, db_path=None, net_mass_tonnes=None,
                    embedded_emissions_tco2e=None, carbon_price_paid_eur=None):
    """Deterministic, local-only CBAM assessment for a declared code at a date.

    Parameters
    ----------
    code        Declared HS/CN/TARIC code (any length; normalised to 10 digits).
    date        ISO date (YYYY-MM-DD); defaults to today.
    net_mass_tonnes
                Optional consignment / annual net mass. Drives the 50t
                de-minimis hint (steel/alu/cement/fertilisers only).
    embedded_emissions_tco2e
                Supplier-VERIFIED embedded emissions. If given, the estimate is
                marked authoritative and default factors are NOT used.
    carbon_price_paid_eur
                Explicit carbon price already paid in the country of origin
                (deductible per Art. 9). Reduces the estimated certificate cost.

    Returns a dict with: in_scope, sector, exclusion, cost estimate (+ is_estimate
    flag), obligations, supplier-data request flag, sources, and full provenance.
    Never raises on an out-of-scope code — returns in_scope=False.
    """
    code10 = normalize_code(code)
    date = date or _date.today().isoformat()
    conn = connect(db_path)
    try:
        result = {
            "code": code10,
            "requested_date": date,
            "source": f"local:{Path(db_path) if db_path else DB_PATH}",
            "regulation": "EU 2023/956 (amended by EU 2025/2083)",
            "in_scope": False,
            "sector": None,
            "indirect_emissions_priced": None,
            "exclusion": None,
            "scope_basis": None,
            "cost": None,
            "obligations": [],
            "supplier_data_request": None,
            "sources": [],
            "notes": [],
            "determinism": "DETERMINISTIC:NO_LLM",
        }

        # Foundation present?
        try:
            conn.execute("SELECT 1 FROM cbam_scope LIMIT 1")
        except sqlite3.OperationalError:
            result["notes"].append(
                "CBAM reference data not loaded — run ingest/ingest_cbam.py. "
                "Refusing to guess scope (no silent fallback).")
            result["found"] = False
            return result
        result["found"] = True

        # 1) Exclusion beats inclusion — check carve-outs first.
        excl = _match(conn, "cbam_exclusion", code10, date)
        if excl is not None:
            result["exclusion"] = {
                "code_prefix": excl["code_prefix"],
                "reason": excl["reason"],
                "regulation": excl["regulation"],
            }
            result["sources"].append(
                f"{excl['regulation']} — Annex I exclusion ({excl['reason']})")
            result["notes"].append(
                f"Declared code matches Annex I exclusion {excl['code_prefix']} "
                f"({excl['reason']}); OUT of CBAM scope.")
            return result

        # 2) In scope?
        scope = _match(conn, "cbam_scope", code10, date)
        if scope is None:
            result["notes"].append(
                "Declared code is not listed in CBAM Annex I scope at this date; "
                "no CBAM obligation on the basis of the code.")
            return result

        result["in_scope"] = True
        result["sector"] = scope["sector"]
        result["indirect_emissions_priced"] = bool(scope["indirect_emissions"])
        result["scope_basis"] = {
            "code_prefix": scope["code_prefix"],
            "match_level": scope["match_level"],
            "description": scope["description"],
            "regulation": scope["regulation"],
        }
        result["sources"].append(
            f"{scope['regulation']} — Annex I scope ({scope['sector']}, "
            f"key {scope['code_prefix']})")

        # 3) Cost estimate: emissions x certificate price - carbon already paid.
        price_row = _latest_price(conn, date)
        cost = {
            "currency": "EUR",
            "is_estimate": True,
            "is_authoritative": False,
            "embedded_emissions_tco2e": None,
            "emissions_basis": None,
            "certificate_price_eur_per_tco2e": None,
            "certificate_price_date": None,
            "carbon_price_paid_eur": carbon_price_paid_eur or 0.0,
            "estimated_certificate_cost_eur": None,
        }

        if price_row is None:
            result["notes"].append(
                "No CBAM certificate price on/before this date — cannot estimate cost.")
        else:
            cost["certificate_price_eur_per_tco2e"] = price_row["eur_per_tco2e"]
            cost["certificate_price_date"] = price_row["price_date"]
            result["sources"].append(
                f"CBAM certificate price {price_row['eur_per_tco2e']} EUR/tCO2e "
                f"({price_row['price_date']}; {price_row['source']})")

            emissions = None
            if embedded_emissions_tco2e is not None:
                # Supplier-verified path: authoritative.
                emissions = float(embedded_emissions_tco2e)
                cost["emissions_basis"] = "supplier_verified"
                cost["is_authoritative"] = True
                cost["is_estimate"] = False
            elif net_mass_tonnes is not None:
                # Default-factor path: transparent estimate only.
                ff = _default_factor(conn, scope["sector"], code10, date)
                if ff is not None:
                    base = float(net_mass_tonnes) * ff["factor_tco2e_per_tonne"]
                    emissions = base * (1.0 + (ff["markup_pct"] or 0.0))
                    cost["emissions_basis"] = (
                        f"default_factor {ff['factor_tco2e_per_tonne']} tCO2e/t "
                        f"x {net_mass_tonnes} t + {int((ff['markup_pct'] or 0)*100)}% markup "
                        f"({ff['basis']})")
                    result["sources"].append(
                        f"Default emission factor {ff['factor_tco2e_per_tonne']} "
                        f"tCO2e/t ({ff['regulation']}) — {ff['notes']}")
                    result["notes"].append(
                        "Cost uses an EU DEFAULT emission factor — an estimate. "
                        "Replace with supplier-verified data for the real figure.")
                else:
                    result["notes"].append(
                        f"No default factor for sector '{scope['sector']}' "
                        "(e.g. electricity needs a country grid factor); "
                        "cannot estimate without supplier data.")
            else:
                result["notes"].append(
                    "Provide net_mass_tonnes (for a default-factor estimate) or "
                    "embedded_emissions_tco2e (supplier-verified) to compute cost.")

            if emissions is not None:
                cost["embedded_emissions_tco2e"] = round(emissions, 4)
                gross = emissions * price_row["eur_per_tco2e"]
                # Carbon price already paid in origin is deductible (Art. 9).
                net = max(0.0, gross - (carbon_price_paid_eur or 0.0))
                cost["estimated_certificate_cost_eur"] = round(net, 2)
                if carbon_price_paid_eur:
                    result["sources"].append(
                        "Art. 9 EU 2023/956 — carbon price paid in origin deducted")

        result["cost"] = cost

        # 4) Obligations (date-bounded scalars from cbam_parameter).
        thr = _param(conn, "mass_threshold_tonnes", date)
        pen = _param(conn, "penalty_eur_per_tonne", date)
        no_threshold_sector = scope["sector"] in ("hydrogen", "electricity")

        obligations = [
            "Importer (or indirect customs representative) must hold AUTHORISED "
            "CBAM DECLARANT status to import these goods (definitive period).",
            "Submit an annual CBAM declaration by 30 September of the following "
            "year, reporting verified embedded emissions.",
            "Purchase and surrender CBAM certificates equal to embedded "
            "emissions, net of any carbon price paid in the country of origin.",
        ]
        if thr is not None and not no_threshold_sector:
            obligations.append(
                f"De-minimis: importers below the {thr['value']:.0f}-tonne "
                f"cumulative annual mass threshold ({thr['regulation']}) are "
                f"exempt from CBAM obligations.")
            if net_mass_tonnes is not None:
                if float(net_mass_tonnes) <= thr["value"]:
                    result["notes"].append(
                        f"Net mass {net_mass_tonnes} t is at/below the "
                        f"{thr['value']:.0f}-tonne threshold — likely EXEMPT "
                        "(threshold is cumulative per importer per year; verify "
                        "against total annual imports).")
                else:
                    result["notes"].append(
                        f"Net mass {net_mass_tonnes} t EXCEEDS the "
                        f"{thr['value']:.0f}-tonne threshold — obligations apply.")
        elif no_threshold_sector:
            obligations.append(
                "No mass-based de-minimis threshold applies to hydrogen or "
                "electricity — obligations apply from the first tonne.")
        if pen is not None:
            obligations.append(
                f"Penalty for non-compliance: {pen['value']:.0f} EUR per tonne "
                f"of unreported embedded emissions ({pen['regulation']}); "
                "3–5x for importing without authorised-declarant status.")
        result["obligations"] = obligations

        # 5) Supplier-data request flag — feeds the clarification/email layer.
        if embedded_emissions_tco2e is None:
            result["supplier_data_request"] = {
                "needed": True,
                "fields": [
                    "Installation identification & country of production",
                    "Direct embedded emissions (tCO2e per tonne of product)",
                    ("Indirect embedded emissions (electricity) per tonne"
                     if scope["indirect_emissions"] else
                     "Indirect emissions not priced for this sector"),
                    "Production route / methodology (EU method, Annex IV)",
                    "Any carbon price effectively paid in the country of origin",
                    "Third-party verification report (definitive period)",
                ],
                "why": ("Default factors are conservative placeholders. Supplier "
                        "data turns the estimate into an authoritative figure and "
                        "usually lowers the certificate cost."),
            }

        return result
    finally:
        conn.close()


def supplier_email_draft(code, sector, lang="EN"):
    """Plain, deterministic supplier request for embedded-emissions data.
    Multilingual stub (EN/DE/PL) — mirrors the engine's clarification-email idea
    without invoking an LLM. Text only; no figures invented.
    """
    code10 = normalize_code(code)
    templates = {
        "EN": (
            "Subject: CBAM embedded-emissions data request (CN {code})\n\n"
            "Dear supplier,\n\n"
            "For the goods we import from you under CN code {code} ({sector}), EU "
            "law (Regulation (EU) 2023/956, CBAM) requires us to report the "
            "embedded greenhouse-gas emissions. Could you please provide, per "
            "tonne of product: direct emissions (tCO2e), the production route and "
            "methodology (EU method, Annex IV), any carbon price already paid in "
            "your country, and — for the definitive period — a third-party "
            "verification report.\n\n"
            "Accurate data usually reduces the CBAM cost versus EU default values.\n\n"
            "Thank you,\n"),
        "DE": (
            "Betreff: CBAM-Datenanfrage zu eingebetteten Emissionen (CN {code})\n\n"
            "Sehr geehrter Lieferant,\n\n"
            "Für die von Ihnen unter der CN-Nummer {code} ({sector}) bezogenen "
            "Waren verpflichtet uns die EU-Verordnung 2023/956 (CBAM), die "
            "eingebetteten Treibhausgasemissionen zu melden. Bitte teilen Sie uns "
            "je Tonne Produkt mit: direkte Emissionen (tCO2e), Herstellungsroute "
            "und Methodik (EU-Methode, Anhang IV), einen ggf. in Ihrem Land bereits "
            "gezahlten CO2-Preis sowie — für die definitive Phase — einen "
            "Verifizierungsbericht einer dritten Stelle.\n\n"
            "Genaue Daten senken die CBAM-Kosten in der Regel gegenüber den "
            "EU-Standardwerten.\n\n"
            "Mit freundlichen Grüßen,\n"),
        "PL": (
            "Temat: Prośba o dane o emisjach wbudowanych CBAM (CN {code})\n\n"
            "Szanowny Dostawco,\n\n"
            "Dla towarów importowanych od Państwa pod kodem CN {code} ({sector}) "
            "rozporządzenie UE 2023/956 (CBAM) wymaga od nas raportowania "
            "wbudowanych emisji gazów cieplarnianych. Prosimy o podanie na tonę "
            "produktu: emisji bezpośrednich (tCO2e), trasy produkcji i metodyki "
            "(metoda UE, załącznik IV), ewentualnej ceny emisji już zapłaconej w "
            "Państwa kraju oraz — dla okresu definitywnego — raportu weryfikacji "
            "przez stronę trzecią.\n\n"
            "Dokładne dane zwykle obniżają koszt CBAM względem wartości domyślnych UE.\n\n"
            "Z poważaniem,\n"),
    }
    tpl = templates.get((lang or "EN").upper(), templates["EN"])
    return tpl.format(code=code10, sector=sector or "CBAM good")


if __name__ == "__main__":
    import json
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "7208100000"
    date = sys.argv[2] if len(sys.argv) > 2 else _date.today().isoformat()
    mass = float(sys.argv[3]) if len(sys.argv) > 3 else 100.0
    print(json.dumps(
        get_cbam_status(code, date, net_mass_tonnes=mass),
        ensure_ascii=False, indent=2))
