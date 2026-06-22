"""Phase 3 acceptance: get_wit_rulings() is a deterministic, LOCAL-ONLY lookup
that returns binding-ruling evidence by code, and WIT never enters the GRI engine.

Run:  python3 tests/test_wit_pl.py
"""
import os
import socket
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import wit_pl  # noqa: E402


def test_returns_rulings_for_known_code_offline():
    if not wit_pl.DB_PATH.exists():
        print("bti.sqlite absent — skipping known-code check")
        return
    real = socket.socket
    socket.socket = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("get_wit_rulings attempted a network connection"))
    try:
        res = wit_pl.get_wit_rulings("7326909890")          # ~1k rulings in the export
    finally:
        socket.socket = real
    assert res["available"] is True
    assert res["rulings"], "expected WIT rulings for a busy code"
    r0 = res["rulings"][0]
    for field in ("reference", "summary", "valid_from", "valid_to", "status", "country"):
        assert field in r0, f"ruling missing {field}"
    print("known-code lookup OK — code 7326909890 rulings:", len(res["rulings"]),
          "/ total", res.get("total_found"),
          "| first ref:", r0["reference"], "| lang:", r0.get("language"))


def test_empty_state_for_unknown_code():
    res = wit_pl.get_wit_rulings("0000000000")
    assert res["rulings"] == [], "unknown code should yield no rulings (empty state)"
    print("empty-state lookup OK — unknown code returns 0 rulings")


def test_wit_not_imported_by_engine():
    """Hard rule: WIT must not be reachable from the GRI engine / oracle."""
    targets = [
        "engine/classifier.py", "engine/oracles.py", "engine/prompts.py",
        "engine/search.py", "engine/tree.py", "server/engine_session.py",
    ]
    offenders = []
    for rel in targets:
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            src = open(p, encoding="utf-8").read()
            if "wit_pl" in src or "get_wit_rulings" in src:
                offenders.append(rel)
    assert not offenders, f"WIT referenced inside the GRI engine: {offenders}"
    print("engine-isolation OK — wit_pl not imported by classifier/oracle/prompts/engine_session")


if __name__ == "__main__":
    test_returns_rulings_for_known_code_offline()
    test_empty_state_for_unknown_code()
    test_wit_not_imported_by_engine()
    print("\nWIT evidence-layer tests passed.")
