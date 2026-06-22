"""Phase 4 acceptance: landed cost under market=PL folds in Polish VAT/excise
read from the LOCAL ISZTAR store (no network), and EU stays unaffected.

Run:  python3 tests/test_landed_cost_pl.py
"""
import os
import socket
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import isztar_pl       # noqa: E402
import landed_cost_pl  # noqa: E402


def test_pl_landed_cost_uses_local_vat_and_excise():
    if not isztar_pl.DB_PATH.exists():
        print("ISZTAR cache absent — run: python3 isztar_ingest.py --date 2025-06-02 --sample")
        return
    real = socket.socket
    socket.socket = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("landed-cost touched the network"))
    try:
        r = landed_cost_pl.compute_landed_cost(
            1000.0, "2.7%", code="2208601100", date="2025-06-02", market="PL")
    finally:
        socket.socket = real

    assert r["market"] == "PL"
    assert r["duty"] == 27.0, r
    assert r["vat_rate"] == 0.23, r                  # standard PL VAT (V999) from local store
    assert abs(r["vat"] - 236.21) < 0.01, r          # 0.23 * (1000 + 27)
    assert abs(r["landed_cost"] - 1263.21) < 0.01, r
    assert r["excise_rate"], "excise rate should be surfaced from the local store"
    print("PL landed cost OK — landed:", r["landed_cost"], "| VAT:", r["vat"],
          "| excise:", (r["excise_rate"] or "")[:24], "| source:", r["pl_source"])


def test_eu_landed_cost_unaffected():
    r = landed_cost_pl.compute_landed_cost(1000.0, "2.7%", market="EU")
    assert r["vat"] is None and r["landed_cost"] == 1027.0, r
    print("EU landed cost OK (no PL taxes) — landed:", r["landed_cost"])


if __name__ == "__main__":
    test_pl_landed_cost_uses_local_vat_and_excise()
    test_eu_landed_cost_unaffected()
    print("\nLanded-cost (PL) tests passed.")
