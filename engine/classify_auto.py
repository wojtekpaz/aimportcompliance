#!/usr/bin/env python3
"""
classify_auto.py — Fully automatic classification using the live Claude oracle.

Same engine and output as classify_cli.py, but Claude answers the GRI
questions instead of you. Requires network + ANTHROPIC_API_KEY (runs on your
machine / Claude Code, not the Anthropic sandbox).

USAGE:
    python3 engine/classify_auto.py <db> "<product>" [origin] [hint]
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from classifier import classify, trail_json          # noqa
from oracles import ClaudeOracle                      # noqa
from lookup import format_result                      # noqa


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    db, product = sys.argv[1], sys.argv[2]
    origin = sys.argv[3] if len(sys.argv) > 3 else ""
    hint = sys.argv[4] if len(sys.argv) > 4 else ""
    conn = sqlite3.connect(db)

    try:
        oracle = ClaudeOracle()
    except RuntimeError as e:
        print(f"Setup problem: {e}")
        sys.exit(2)

    res = classify(conn, product, oracle, hint=hint, origin=origin)
    print("\n" + "#" * 64)
    if res.status == "classified":
        print(f"CLASSIFICATION: {res.code[:4]} {res.code[4:6]} "
              f"{res.code[6:8]} {res.code[8:]}   confidence: {res.confidence}")
        if res.hint_conflict:
            print("!! NOTE: your chapter hint conflicted with the legal "
                  "evidence; result kept the evidence-based path. Verify.")
        if res.measures is not None:
            print()
            print(format_result(conn, res.code, origin, res.measures))
    else:
        print(f"STATUS: {res.status} — the engine will not guess.")
        if res.question:
            print("It needs decided:", res.question["ask"])
            print("Options the AI was unsure between:")
            for o in res.question["options"][:8]:
                print(f"  {o['id']}  {o.get('text','')[:70]}")
    print("\n----- AUDIT TRAIL -----")
    print(trail_json(res))
    # show the AI's reasoning log (stored for audit)
    if oracle.calls:
        print(f"\n({len(oracle.calls)} AI calls made; reasoning logged for audit)")


if __name__ == "__main__":
    main()
