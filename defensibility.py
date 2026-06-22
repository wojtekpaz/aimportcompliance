"""Deterministic defensibility scoring for tariff-optimization alternatives.

Phase 6. The LLM NEVER assigns this score. It is computed in Python from three
defined, explainable inputs:

  1. GRI path strength  — from the engine's deterministic classification trail
     (weakest-link: defensibility is limited by the most contestable GRI step).
  2. WIT/BTI support    — count of binding rulings for the code, from the local
     store (real-world precedent strengthens a position).
  3. Measure clarity    — whether duty is known and free of trade-defence
     complications (anti-dumping / countervailing reduce clarity).

Standalone: not imported by the classifier, oracle, or optimize engine. Applied
as a post-processing layer over alternatives the deterministic engine already
produced and validated.
"""
import wit_pl

# Defensibility anchor per GRI rule: a named/specific heading is stronger than
# an essential-character or tie-break determination.
GRI_STRENGTH = {
    "GRI-1": 1.00, "GRI-6": 0.95,
    "GRI-2A": 0.85, "GRI-3A": 0.80,
    "GRI-5A": 0.60, "GRI-5B": 0.60,
    "GRI-2B": 0.55, "GRI-3B": 0.55,
    "GRI-3C": 0.40, "GRI-4": 0.35,
}
WEIGHTS = {"gri": 50, "wit": 30, "measure": 20}


def _norm(rule):
    return (rule or "").upper().replace("–", "-").replace("—", "-").replace(" ", "-").strip()


def gri_strength_from_trail(trail, fallback_rule=""):
    """Weakest-link GRI strength across the engine's deterministic trail."""
    found = []
    for st in (trail or []):
        g = _norm(st.get("gri") if isinstance(st, dict) else getattr(st, "gri", ""))
        for key, val in GRI_STRENGTH.items():
            if key in g:
                found.append((val, key))
                break
    if found:
        return min(found, key=lambda x: x[0])
    fr = _norm(fallback_rule)
    for key, val in GRI_STRENGTH.items():
        if key in fr:
            return (val, key)
    return (0.5, "?")


def measure_clarity(duty, defense):
    rate = duty.get("rate") if isinstance(duty, dict) else None
    if not rate:
        return 0.5                      # unknown duty -> uncertain
    if defense:                         # anti-dumping / countervailing complicate it
        return 0.5
    return 1.0


def wit_support(code, db_path=None):
    res = wit_pl.get_wit_rulings(code, db_path=db_path)
    n = res.get("total_found", 0) if res.get("available") else 0
    if n == 0:
        return 0.0, 0
    if n <= 5:
        return 0.5, n
    return 1.0, n


def score_alternative(alt, db_path=None):
    """Return a deterministic defensibility score (0-100) + band + the inputs."""
    gri_val, gri_rule = gri_strength_from_trail(alt.get("trail"), alt.get("gri_rule", ""))
    wit_val, wit_n = wit_support(alt.get("code", ""), db_path=db_path)
    clarity = measure_clarity(alt.get("duty"), alt.get("defense"))

    score = round(gri_val * WEIGHTS["gri"] + wit_val * WEIGHTS["wit"] + clarity * WEIGHTS["measure"])
    band = "STRONG" if score >= 70 else ("ARGUABLE" if score >= 45 else "WEAK")
    return {
        "defensibility_score": score,
        "defensibility_band": band,
        "defensibility_inputs": {
            "gri_strength": gri_val,
            "gri_rule_used": gri_rule,
            "wit_support": wit_val,
            "wit_rulings": wit_n,
            "measure_clarity": clarity,
            "weights": WEIGHTS,
        },
        "defensibility_computed_by": "deterministic (Python) — not the LLM",
    }


if __name__ == "__main__":
    import json
    demo = {"code": "7326909890", "gri_rule": "GRI-3b",
            "trail": [{"gri": "GRI 1"}, {"gri": "GRI 3b"}, {"gri": "GRI 6"}],
            "duty": {"rate": "2.7%"}, "defense": []}
    print(json.dumps(score_alternative(demo), ensure_ascii=False, indent=2))
