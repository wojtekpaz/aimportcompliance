#!/usr/bin/env python3
"""
oracles.py — Oracle implementations for the GRI classifier.

1) ClaudeOracle  — production: Claude API, temperature 0, JSON-schema
   constrained, may ONLY return an option id or UNSURE (the state machine
   re-validates anyway — defense in depth).
2) HumanOracle   — interactive CLI: the user answers the questions. This
   doubles as (a) a no-API-key demo and (b) the broker-facing flow where
   a professional confirms each legal step.

Run the interactive demo:
    python3 engine/classify_cli.py <db> "<product>" [origin] [hint]
"""
import json
import os
import urllib.request
import urllib.error

from classifier import Oracle, UNSURE
from prompts import SYSTEM_RULES, INTERPRET, build_system  # noqa


class ClaudeOracle(Oracle):
    """Production oracle. Calls the Claude API at temperature 0, returns ONLY
    an option id or UNSURE (the state machine re-validates). Every call is
    logged for the audit trail. Requires network access + ANTHROPIC_API_KEY
    (does NOT run inside Anthropic's sandbox — use Claude Code / your server).

    Model default is the current Sonnet string. If Anthropic rotates models,
    update DEFAULT_MODEL or pass model=... ; run `python3 engine/oracles.py`
    to self-test the connection and model string before a full run.
    """
    DEFAULT_MODEL = "claude-sonnet-4-6"

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 max_retries: int = 3):
        self.model = model or self.DEFAULT_MODEL
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.max_retries = max_retries
        self.calls = []                      # logged: prompt+response (audit)
        self.last_reason = ""                # AI's stated deciding-attribute (for
                                             # better clarifying questions on UNSURE)
        if not self.api_key:
            raise RuntimeError(
                "No API key. Set the ANTHROPIC_API_KEY environment variable "
                "(see HANDOFF.md). The key is never written to disk by AImport.")

    def choose(self, prompt, options, context):
        import time
        industrial = context.get("industrial", False) if context else False
        body = {
            "model": self.model,
            "max_tokens": 500,
            "temperature": 0,
            "system": build_system(industrial),
            "messages": [{
                "role": "user",
                "content": prompt + "\n\nOPTIONS:\n" + "\n".join(
                    f"id={o['id']} :: {o['text']}" for o in options)
            }],
        }
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(body).encode(),
            headers={"content-type": "application/json",
                     "x-api-key": self.api_key,
                     "anthropic-version": "2023-06-01"})
        last_err = None
        for attempt in range(self.max_retries):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = json.loads(r.read())
                text = "".join(b.get("text", "") for b in data.get("content", []))
                self.calls.append({"prompt": prompt[:200], "raw": text})
                try:
                    parsed = json.loads(text.strip().removeprefix("```json")
                                        .removesuffix("```").strip())
                    self.last_reason = parsed.get("reason", "")
                    choice = str(parsed.get("choice", UNSURE) or UNSURE).strip()
                    # Strip 'id=' prefix if the model echoes back the option format
                    if choice.startswith("id="):
                        choice = choice[3:].split(" ")[0].strip()
                    return choice
                except json.JSONDecodeError:
                    self.last_reason = ""
                    return UNSURE    # malformed model output -> question, not guess
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "ignore")[:300]
                if e.code == 401:
                    raise RuntimeError(
                        "API rejected the key (401). Check ANTHROPIC_API_KEY "
                        "is correct and active.") from e
                if e.code == 404 and "model" in detail.lower():
                    raise RuntimeError(
                        f"Model '{self.model}' not found (404). Anthropic may "
                        f"have rotated it — update ClaudeOracle.DEFAULT_MODEL. "
                        f"Detail: {detail}") from e
                last_err = f"HTTP {e.code}: {detail}"
                if e.code in (429, 500, 502, 503, 529):      # transient
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"API error: {last_err}") from e
            except urllib.error.URLError as e:
                last_err = str(e)
                time.sleep(2 ** attempt)
        raise RuntimeError(f"API unreachable after {self.max_retries} tries: "
                           f"{last_err}. Check your internet connection.")

    def propose_headings(self, product_text: str) -> dict:
        """AI INTERPRETATION LAYER — a candidate generator, NOT a classifier.

        Real users type commercial names ('smartwatch', 'hoodie', 'sneakers')
        that the tariff text never uses, so keyword search alone misses them.
        Here the AI maps the product to the 4-digit headings that could plausibly
        cover it — INCLUDING the genuine alternatives a customs officer would
        weigh (a smartwatch competes 8517 vs 9102), which is what lets the engine
        ask a real either/or question downstream.

        Output is UNVALIDATED: classify() checks every heading against the DB
        before use, so an invented heading cannot enter — the anti-hallucination
        guarantee is untouched. Never raises; returns empty on any failure so a
        network hiccup can't block a classification that search could still make.
        """
        body = {"model": self.model, "max_tokens": 400, "temperature": 0,
                "system": INTERPRET,
                "messages": [{"role": "user", "content": product_text}]}
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(body).encode(),
            headers={"content-type": "application/json",
                     "x-api-key": self.api_key,
                     "anthropic-version": "2023-06-01"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
            text = "".join(b.get("text", "") for b in data.get("content", []))
            self.calls.append({"prompt": "propose_headings: " + product_text[:160],
                               "raw": text})
            parsed = json.loads(text.strip().removeprefix("```json")
                                .removesuffix("```").strip())
            return {"headings": [str(h) for h in parsed.get("headings", [])],
                    "normalized": parsed.get("normalized", "")}
        except Exception:
            return {"headings": [], "normalized": ""}

    def self_test(self) -> bool:
        """Minimal call to confirm key + model + network all work."""
        opts = [{"id": "A", "text": "the correct answer"},
                {"id": "B", "text": "a wrong answer"}]
        r = self.choose("Test: choose option A.", opts, {})
        return r in ("A", UNSURE)


class HumanOracle(Oracle):
    def choose(self, prompt, options, context):
        print("\n" + "=" * 64)
        print(prompt)
        for i, o in enumerate(options, 1):
            print(f"  [{i}] {o['id']}  {o['text'][:160]}")
        print("  [0] UNSURE / none of these")
        while True:
            raw = input("choice> ").strip()
            if raw == "0":
                return UNSURE
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return options[int(raw) - 1]["id"]
            print("enter a number from the list")


if __name__ == "__main__":
    # Connection self-test. Run this FIRST on your machine to confirm the API
    # key, model string and network all work, before any full classification.
    import sys
    print("AImport — Claude API connection self-test")
    try:
        oracle = ClaudeOracle()
        print(f"  model: {oracle.model}")
        print("  calling API...")
        ok = oracle.self_test()
        if ok:
            print("  RESULT: OK — key, model and network all working.")
            sys.exit(0)
        print("  RESULT: reachable but unexpected reply — check model output.")
        sys.exit(1)
    except RuntimeError as e:
        print(f"  RESULT: FAILED\n  {e}")
        sys.exit(2)
