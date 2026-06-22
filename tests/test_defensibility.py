"""Phase 6 acceptance: defensibility scores are deterministic and computed from
defined, printed inputs — the LLM does not assign them.

Run:  python3 tests/test_defensibility.py
"""
import os
import socket
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import defensibility  # noqa: E402

STRONG_ALT = {"code": "7326909890", "gri_rule": "GRI-1",
              "trail": [{"gri": "GRI 1"}, {"gri": "GRI 6"}],
              "duty": {"rate": "2.7%"}, "defense": []}
WEAK_ALT = {"code": "7326909890", "gri_rule": "GRI-3b",
            "trail": [{"gri": "GRI 1"}, {"gri": "GRI 3b"}],   # weakest link = 3b
            "duty": {"rate": "2.7%"}, "defense": [{"type_name": "Anti-dumping"}]}


def test_scores_are_deterministic_and_no_llm():
    # network off: proves no LLM/API call; WIT reads only the local store
    real = socket.socket
    socket.socket = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("defensibility scoring attempted a network/LLM call"))
    try:
        a1 = defensibility.score_alternative(STRONG_ALT)
        a2 = defensibility.score_alternative(STRONG_ALT)
    finally:
        socket.socket = real
    assert a1 == a2, "scoring must be deterministic (same inputs -> same score)"
    assert "deterministic" in a1["defensibility_computed_by"]
    print("deterministic OK — STRONG_ALT score:", a1["defensibility_score"],
          a1["defensibility_band"], "| inputs:", a1["defensibility_inputs"])


def test_inputs_are_explainable_and_printed():
    r = defensibility.score_alternative(STRONG_ALT)
    inp = r["defensibility_inputs"]
    for k in ("gri_strength", "gri_rule_used", "wit_support", "wit_rulings",
              "measure_clarity", "weights"):
        assert k in inp, f"missing input {k}"
    print("inputs printed OK:", inp)


def test_weakest_link_and_measure_clarity_lower_the_score():
    strong = defensibility.score_alternative(STRONG_ALT)["defensibility_score"]
    weak = defensibility.score_alternative(WEAK_ALT)["defensibility_score"]
    assert weak < strong, (weak, strong)
    # GRI-3b weakest link recognised
    assert defensibility.score_alternative(WEAK_ALT)["defensibility_inputs"]["gri_rule_used"] == "GRI-3B"
    # anti-dumping lowers measure clarity
    assert defensibility.score_alternative(WEAK_ALT)["defensibility_inputs"]["measure_clarity"] == 0.5
    print(f"weakest-link OK — STRONG={strong} > WEAK={weak} (GRI-3b + anti-dumping)")


if __name__ == "__main__":
    test_scores_are_deterministic_and_no_llm()
    test_inputs_are_explainable_and_printed()
    test_weakest_link_and_measure_clarity_lower_the_score()
    print("\nDefensibility scoring tests passed.")
