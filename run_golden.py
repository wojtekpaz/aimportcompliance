#!/usr/bin/env python3
"""
run_golden.py - feed a golden-seed JSON through the AImport engine and score it.

Usage:
    cd ~/Desktop/aimport
    export ANTHROPIC_API_KEY=your-key
    python3 run_golden.py data_taric.sqlite tests/golden_seed_expanded.json

What it measures (honestly):
  - EXACT  : engine returned a declarable code matching all 10 digits
  - >=8 / >=6 / heading / chapter : partial leading-digit agreement
  - NEEDS_Q: engine stopped to ask a clarification question. NOT counted as
    a failure -- it means the engine correctly recognised it lacked an
    attribute. Reported separately so you can see "classified outright" vs
    "would have asked the broker one question".
  - REVIEW : engine ended in needs_review (no candidates / dead-end).

Why NEEDS_Q is separate: in manual testing you answered the questions. A batch
runner has no human to answer, so a question is the engine behaving correctly,
not getting the product wrong. Conflating the two would understate accuracy.
"""
import sys, json, os, sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "engine"))

from classifier import classify, Result          # noqa
from oracles import ClaudeOracle                  # noqa


def leading_match(got: str, want: str) -> int:
    got = (got or "").replace(" ", "")
    want = (want or "").replace(" ", "")
    n = 0
    for a, b in zip(got, want):
        if a != b:
            break
        n += 1
    return n


def grade(got, want):
    n = leading_match(got, want)
    if n >= 10: return "EXACT"
    if n >= 8:  return f"8-digit"
    if n >= 6:  return f"6-digit"
    if n >= 4:  return f"heading"
    if n >= 2:  return f"chapter"
    return "WRONG"


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 run_golden.py <db_path> <golden_seed.json>")
        sys.exit(1)
    db_path, seed_path = sys.argv[1], sys.argv[2]
    with open(seed_path, encoding="utf-8") as f:
        cases = json.load(f)

    conn = sqlite3.connect(db_path)
    oracle = ClaudeOracle()

    exact = eight = six = needs_q = review = errors = 0
    total = len(cases)

    for i, c in enumerate(cases, 1):
        text, want = c["text"], c["code"]
        origin, hint, conf = c.get("origin",""), c.get("hint",""), c.get("confidence","?")
        try:
            res = classify(conn, text, oracle, hint=hint, origin=origin)
            status = res.status
            got = res.code
        except Exception as e:
            errors += 1
            print(f"[{i:>2}/{total}] ERROR: {str(e)[:60]}  ({text[:30]})")
            continue

        if status == "classified":
            n = leading_match(got, want)
            if n >= 10: exact += 1
            if n >= 8:  eight += 1
            if n >= 6:  six += 1
            flag = "  <-- MISS, check seed" if (n < 10 and conf in ("low","medium")) else ""
            print(f"[{i:>2}/{total}] want {want:<11} got {str(got):<12} "
                  f"{grade(got,want):<8} conf={conf}{flag}")
        elif status == "needs_question":
            needs_q += 1
            q = (res.question or {}).get("stage","?")
            print(f"[{i:>2}/{total}] want {want:<11} NEEDS_QUESTION ({q})   conf={conf}")
        else:
            review += 1
            print(f"[{i:>2}/{total}] want {want:<11} NEEDS_REVIEW            conf={conf}")

    answered = total - needs_q - review - errors
    print("\n" + "="*64)
    print(f"Classified outright : {answered}/{total}")
    print(f"  of those EXACT 10 : {exact}   (={100*exact/total:.0f}% of all cases)")
    print(f"  of those >=8-digit: {eight}")
    print(f"  of those >=6-digit: {six}")
    print(f"Asked a question    : {needs_q}/{total}  (engine knew it needed an attribute)")
    print(f"Needs review        : {review}/{total}")
    print(f"Errors              : {errors}/{total}")
    print("="*64)
    print("\nMISS on a low/medium-confidence seed may be the SEED being wrong, "
          "not the engine. Verify those codes against the live tariff.")
    conn.close()


if __name__ == "__main__":
    main()
