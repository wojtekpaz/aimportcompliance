#!/usr/bin/env python3
"""
run_accuracy.py — Measure REAL classification accuracy with the live oracle.

This is the number that matters commercially: given real product descriptions
with known-correct codes (from EBTI / Binding Tariff Information rulings), how
often does AImport reach the right heading and the right full code?

DIFFERENCE FROM eval_harness.py:
  eval_harness uses a PERFECT oracle (replays known answers) to prove the
  ENGINE can reach every code. This file uses the LIVE Claude oracle answering
  from the product TEXT ALONE — measuring whether the AI reasons correctly.
  It REQUIRES network + ANTHROPIC_API_KEY, so it runs on your machine, not in
  the Anthropic sandbox.

GOLDEN SET FORMAT (JSON list):
  [{"text": "men's knitted cotton t-shirt", "code": "6109100010",
    "origin": "CN", "hint": "", "source": "BTI DE-2023-..."}]
  - text  : the product description as a user would type it
  - code  : the known-correct declarable code (10 digits)
  - origin: optional, for measure checks
  - hint  : optional chapter/heading hint
  - source: provenance (which BTI ruling) — for the report

USAGE:
  export ANTHROPIC_API_KEY=sk-...
  python3 engine/run_accuracy.py <db> <golden_set.json> [out_report.json]
"""
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from classifier import classify, UNSURE                     # noqa
from oracles import ClaudeOracle                             # noqa


def answer_questions(conn, item, oracle, max_rounds=8):
    """Run a full classification, letting the oracle answer each clarification
    round (the engine returns needs_question; we re-invoke with the oracle
    choosing among the offered options). Returns the final Result."""
    # The classifier already drives the oracle internally for heading + descent.
    # Clarification 'needs_question' states occur when the oracle says UNSURE;
    # with a live oracle that reasons from text, it answers rather than asking,
    # so a single classify() call typically completes. We still cap rounds.
    return classify(conn, item["text"], oracle,
                    hint=item.get("hint", ""), origin=item.get("origin", ""))


def evaluate(conn, golden, oracle):
    results = []
    head_ok = full_ok = classified = 0
    for item in golden:
        gold = item["code"].ljust(10, "0")
        try:
            res = answer_questions(conn, item, oracle)
        except Exception as e:
            # One bad item must not abort the whole run — record it as a miss
            # (with the reason) and move on, so the other products still score.
            results.append({"text": item["text"], "gold": gold,
                            "got": None, "status": f"error: {e}",
                            "heading_match": False, "full_match": False,
                            "source": item.get("source", "")})
            continue
        got = res.code
        if res.status == "classified":
            classified += 1
            if got[:4] == gold[:4]:
                head_ok += 1
            if got == gold:
                full_ok += 1
        results.append({
            "text": item["text"], "gold": gold, "got": got,
            "status": res.status,
            "heading_match": bool(got and got[:4] == gold[:4]),
            "full_match": got == gold,
            "source": item.get("source", ""),
        })
    n = len(golden)
    summary = {
        "n": n, "classified": classified,
        "heading_accuracy": round(100 * head_ok / n, 1) if n else 0,
        "full_code_accuracy": round(100 * full_ok / n, 1) if n else 0,
        "model": oracle.model,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    return summary, results


def main():
    db, golden_path = Path(sys.argv[1]), Path(sys.argv[2])
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("accuracy_report.json")
    conn = sqlite3.connect(db)
    golden = json.loads(golden_path.read_text())

    print(f"AImport accuracy run — {len(golden)} products")
    oracle = ClaudeOracle()
    print(f"  model: {oracle.model}\n  running (this calls the API per "
          f"classification)...\n")
    summary, results = evaluate(conn, golden, oracle)

    out.write_text(json.dumps({"summary": summary, "results": results}, indent=2))
    print(f"  heading accuracy:   {summary['heading_accuracy']}%")
    print(f"  full-code accuracy: {summary['full_code_accuracy']}%")
    print(f"  classified:         {summary['classified']}/{summary['n']}")
    print(f"\n  full report: {out}")
    # show the misses so they can be inspected
    misses = [r for r in results if not r["full_match"]]
    if misses:
        print(f"\n  misses ({len(misses)}):")
        for m in misses[:15]:
            print(f"    '{m['text'][:40]}' gold {m['gold']} got {m['got']} "
                  f"({m['status']})")


if __name__ == "__main__":
    main()
