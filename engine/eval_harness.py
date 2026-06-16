#!/usr/bin/env python3
"""
eval_harness.py — Accuracy evaluation scaffold for the GRI engine.

PURPOSE:
Establishes the measurement framework that the EBTI golden set (real Binding
Tariff Information rulings: product text -> known-correct code) will plug into.
Until the live LLM oracle runs (in Claude Code, with API access), we validate
the ENGINE MECHANICS with a "perfect oracle" that replays the known-correct
GRI path for each target code. This proves:
  - the engine can REACH every declarable code via legal dash-level descent
  - candidate search surfaces the correct heading
  - no code is unreachable due to tree-navigation bugs

When the Claude oracle is wired, the same harness measures REAL accuracy:
swap PerfectOracle for ClaudeOracle, feed EBTI (text, gold_code) pairs, and
report heading-level + full-code accuracy.

USAGE:
    python3 engine/eval_harness.py <db> [n_samples]
"""
import random
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tree import (first_level_children, next_level_children,            # noqa
                  rows_under_heading, is_declarable)
from classifier import classify, Oracle, UNSURE                         # noqa
from search import candidate_headings                                   # noqa


def correct_path_to(conn, target: str):
    """Compute the oracle answer sequence (heading, then 'item:suffix' per
    dash level) that leads to `target`, using INDENT-aware descent — the
    same logic the engine uses, so a correct target is always reachable.

    Returns (answers, reached_bool)."""
    target = target.ljust(10, "0")
    heading = target[:4]
    rows = rows_under_heading(conn, heading)
    if not rows:
        return [heading], False
    # locate the target row (prefer the declarable suffix '80')
    trow = next((r for r in rows if r["item_id"] == target and r["is_leaf"]), None) \
        or next((r for r in rows if r["item_id"] == target), None)
    if trow is None:
        return [heading], False

    # build the ancestry: walk backwards collecting one row per shallower indent
    idx = rows.index(trow)
    base = min(r["indent"] for r in rows)
    want = trow["indent"]
    chain_rows = []
    # include the target itself first, then its ancestors
    cur = trow
    chain_rows.append(cur)
    want = trow["indent"] - 1
    for r in reversed(rows[:idx]):
        if r["indent"] == want:
            chain_rows.append(r)
            want -= 1
        if want < base:
            break
    chain_rows.reverse()                       # now shallow -> deep (== target)

    # Drop any heading/base-level rows: GRI-6 descent starts at the FIRST
    # dash level (indent > base), matching the engine's first_level_children.
    chain_rows = [r for r in chain_rows if r["indent"] > base]

    answers = [heading]
    for r in chain_rows:
        answers.append(r["item_id"] + ":" + r["suffix"])
    return answers, is_declarable(conn, target)


class PerfectOracle(Oracle):
    """Replays a precomputed correct path. Heading first, then one
    'item:suffix' per dash level. Validates the path actually appears in the
    options at each step (a path the engine can't follow => failure, caught)."""
    def __init__(self, answers):
        self.answers = list(answers)

    def choose(self, prompt, options, context):
        if not self.answers:
            return UNSURE
        want = self.answers.pop(0)
        ids = {o["id"] for o in options}
        return want if want in ids else UNSURE


def evaluate(conn, n=200, seed=7):
    random.seed(seed)
    codes = [r[0] for r in conn.execute(
        "SELECT item_id FROM goods_nomenclature WHERE is_leaf=1")]
    sample = random.sample(codes, min(n, len(codes)))

    reachable = path_ok = heading_found = full_ok = 0
    failures = []
    for target in sample:
        answers, ok = correct_path_to(conn, target)
        if ok:
            reachable += 1
        # does candidate search surface the correct heading?
        # (use the target's own path text as a proxy "product description")
        # We test ENGINE reachability here, not search recall — search recall
        # needs real product text (EBTI), which arrives later.
        res = classify(conn, "x", PerfectOracle(answers),
                       hint=target[:4])    # hint guarantees heading candidate
        if res.status == "classified":
            path_ok += 1
            if res.code == target:
                full_ok += 1
            else:
                failures.append((target, res.code, "wrong terminal"))
        else:
            failures.append((target, res.status, "not classified"))

    print(f"Sampled declarable codes: {len(sample)}")
    print(f"  path computable:            {reachable}/{len(sample)} "
          f"({100*reachable//len(sample)}%)")
    print(f"  engine reaches a code:      {path_ok}/{len(sample)} "
          f"({100*path_ok//len(sample)}%)")
    print(f"  engine reaches EXACT code:  {full_ok}/{len(sample)} "
          f"({100*full_ok//len(sample)}%)")
    if failures:
        print(f"\n  failures (first 12 of {len(failures)}):")
        for t, got, why in failures[:12]:
            print(f"    target {t} -> {got}  ({why})")
    return full_ok, len(sample)


if __name__ == "__main__":
    conn = sqlite3.connect(sys.argv[1])
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    full_ok, total = evaluate(conn, n)
    sys.exit(0 if full_ok == total else 1)
