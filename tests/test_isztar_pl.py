"""Phase 2 acceptance: get_pl_national_measures() is a deterministic, LOCAL-ONLY
lookup that returns Polish VAT / excise / national measures from the cache.

Run:  python3 tests/test_isztar_pl.py
"""
import os
import socket
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import isztar_pl  # noqa: E402


def _seed_db(path):
    """A tiny, network-free fixture mirroring the real ISZTAR taxes shape."""
    conn = sqlite3.connect(path)
    isztar_pl.ensure_schema(conn)
    code, date = "2208601100", "2025-06-02"
    conn.execute("INSERT INTO isztar_nomenclature_pl VALUES (?,?,?,?)",
                 (code, date, "Wódka (test)", "l alk. 100%"))
    conn.executemany(
        "INSERT INTO isztar_taxes_pl (code,valid_date,tax_type,description,duty_amount,"
        "duty_amount_with_codes,additional_code,additional_code_desc,country) VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (code, date, "VAT", "Podatek od towarów i usług (VAT)", "23%", "23%", "V999", "Pozostałe", "ERGA OMNES"),
            (code, date, "EXCISE", "Podatek akcyzowy", "7991 zł / hl 100% obj.", "7991 PLN / LPX", "X019", "alk > 1,2%", "ERGA OMNES"),
        ])
    conn.execute("INSERT INTO isztar_national_measures_pl VALUES (?,?,?,?,?)",
                 (code, date, "Kontrola jakości handlowej", "ERGA OMNES", "R1234"))
    conn.commit()
    conn.close()


def test_lookup_returns_vat_and_excise_offline():
    """Correct VAT/excise from a seeded local DB — and proven with networking disabled."""
    tmp = tempfile.mktemp(suffix=".sqlite")
    _seed_db(tmp)

    real_socket = socket.socket
    socket.socket = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("get_pl_national_measures attempted a network connection"))
    try:
        res = isztar_pl.get_pl_national_measures("2208 60 11 00", "2025-06-02", db_path=tmp)
    finally:
        socket.socket = real_socket
        os.unlink(tmp)

    assert res["found"] is True
    assert res["vat_standard"] == "23%", res
    assert any(v["rate"] == "23%" and v["additional_code"] == "V999" for v in res["vat"])
    assert any("akcyz" in (e["description"] or "").lower() for e in res["excise"])
    assert res["national_measures"], "expected national non-tariff measures"
    print("offline seeded lookup OK — VAT:", res["vat_standard"],
          "| excise entries:", len(res["excise"]),
          "| national:", len(res["national_measures"]))


def test_real_cache_sample_if_present():
    """If the live ingestion has populated data_isztar_pl.sqlite, verify a real code."""
    if not isztar_pl.DB_PATH.exists():
        print("real cache absent — skipping (run: python3 isztar_ingest.py --date 2025-06-02 --sample)")
        return
    res = isztar_pl.get_pl_national_measures("2208601100", "2025-06-02")
    if not res["found"]:
        print("sample code not in cache — skipping")
        return
    assert res["vat"], "real cache: expected VAT entries"
    print("real cache lookup OK — code 2208601100 VAT:", res["vat_standard"],
          "| excise:", len(res["excise"]), "| desc:", (res["description_pl"] or "")[:40])


if __name__ == "__main__":
    test_lookup_returns_vat_and_excise_offline()
    test_real_cache_sample_if_present()
    print("\nISZTAR PL data-layer tests passed.")
