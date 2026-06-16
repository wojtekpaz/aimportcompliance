#!/usr/bin/env python3
"""
classify_cli.py — Interactive GRI classification demo.

You answer the legal questions the AI oracle will answer in production.
The control flow, candidate generation, audit trail and duty lookup are
EXACTLY the production path — only the chooser differs.

USAGE:
    python3 engine/classify_cli.py <db> "<product description>" [origin] [hint]
e.g.
    python3 engine/classify_cli.py data/taric.sqlite "ceramic wall tiles, glazed" CN
    python3 engine/classify_cli.py data/taric.sqlite "men's cotton t-shirt" CN 61
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from classifier import classify, trail_json          # noqa
from oracles import HumanOracle                      # noqa
from lookup import format_result                     # noqa


def main():
    db, product = sys.argv[1], sys.argv[2]
    origin = sys.argv[3] if len(sys.argv) > 3 else ""
    hint = sys.argv[4] if len(sys.argv) > 4 else ""
    conn = sqlite3.connect(db)

    res = classify(conn, product, HumanOracle(), hint=hint, origin=origin)
    print("\n" + "#" * 64)
    if res.status == "classified":
        print(f"CLASSIFICATION: {res.code[:4]} {res.code[4:6]} "
              f"{res.code[6:8]} {res.code[8:]}   confidence: {res.confidence}")
        if res.hint_conflict:
            print("!! NOTE: your chapter hint conflicted with the legal "
                  "evidence — result kept evidence-based path; verify.")
        if res.measures is not None:
            print()
            print(format_result(conn, res.code, origin, res.measures))
    else:
        print(f"STATUS: {res.status} — the engine refuses to guess.")
        if res.question:
            print("It needs the following decided:", res.question["ask"])
    print("\n----- AUDIT TRAIL (stored with the decision) -----")
    print(trail_json(res))


if __name__ == "__main__":
    main()
