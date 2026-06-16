# AImport — Handoff Guide (moving to your machine / Claude Code)

This guide gets the **live AI classification** running on your own computer or
server, where (unlike the chat sandbox) the internet and your API key are
available. Written so you can follow it without coding knowledge — every step
is a command you copy, and what you should see if it worked.

---

## What you have in this folder

A complete, tested classification backend:
- `data_taric.sqlite` — the full EU tariff database (nomenclature, duties,
  anti-dumping, certificates, exclusions, legal notes). ~53 MB.
- `engine/` — the classification engine (GRI state machine, search, lookup,
  legal-notes retrieval, the Claude oracle, the accuracy runner).
- `ingest/` — the scripts that built the database from the EU Excel/PDF files
  (you only re-run these when the EU publishes an update).
- `tests/` — automated checks. 39 of them, all passing.

The only thing that does NOT work in the chat sandbox is the **live AI**,
because the sandbox has no internet. That is what this guide switches on.

---

## Step 0 — One-time setup

You need three things: Python, the project folder, and an Anthropic API key.

1. **Python** (3.10 or newer). Check by opening a terminal and typing:
   ```
   python3 --version
   ```
   If it prints a version number, you're set. If not, install from python.org.

2. **The dependencies** (one command, run inside the project folder):
   ```
   pip install -r requirements.txt
   ```

3. **An API key.** Get one from the Anthropic Console (console.anthropic.com →
   API keys). Then, in your terminal, paste it in like this (replace the x's):
   ```
   export ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxx
   ```
   This keeps the key in memory for the session only; AImport never writes it
   to disk. (On Windows PowerShell use: `$env:ANTHROPIC_API_KEY="sk-ant-..."`)

---

## Step 1 — Confirm the connection works (do this first)

Before anything else, run the self-test. It makes one tiny API call:
```
python3 engine/oracles.py
```
**What you should see:** `RESULT: OK — key, model and network all working.`

If it fails, the message tells you why in plain terms (bad key, model rotated,
no internet). Fix that before continuing — nothing else will work until this
passes.

---

## Step 2 — Try one classification with the live AI

The interactive version (you answer the legal questions) already worked in the
sandbox. Now try the **fully automatic** version, where Claude answers them:
```
python3 engine/classify_auto.py data_taric.sqlite "men's cotton knitted t-shirt" CN
```
**What you should see:** the code `6109 10 00 10`, the duty for China, and the
audit trail showing each GRI step the AI took — including which legal notes it
was given. Try other products: a ceramic tile, a steel bracket, anything.

If the AI is unsure (the description lacks a deciding attribute), it will say
so and ask — exactly as designed. It never guesses.

---

## Step 3 — Measure real accuracy (the credibility number)

This is the number for Gdynia and investors: how often the AI gets the code
right from a plain description. It needs a **golden set** — real products with
their known-correct codes.

A small seed set is included so you can see it work immediately:
```
python3 engine/run_accuracy.py data_taric.sqlite tests/golden_seed.json
```
**What you should see:** heading accuracy %, full-code accuracy %, and a list
of any misses. With only 8 seed items the number is just illustrative.

**To get a real number**, replace the seed file with 30–100 products taken
from actual EBTI / BTI rulings (the EU's published binding classifications).
Format (a plain text file, same shape as `golden_seed.json`):
```
[
  {"text": "the product description", "code": "1234567890",
   "origin": "CN", "hint": "", "source": "BTI ruling reference"}
]
```
The more real rulings you add, the more trustworthy the accuracy figure. I can
help assemble this set from EBTI exports when you have them.

---

## Which model, and cost

Default model: **claude-sonnet-4-6** — strong reasoning at low cost
(about $3 per million input tokens). A single classification is a few thousand
tokens, so a full accuracy run of 100 products costs roughly a few cents to a
few tens of cents. If Anthropic rotates models and a call fails with "model not
found", update `DEFAULT_MODEL` near the top of `engine/oracles.py`.

---

## When the EU updates the tariff (every so often)

You re-download the Excel/PDF export set and re-run the ingest scripts (same
ones that built the current database). Order:
```
python3 ingest/ingest_xlsx.py     <folder> data_taric.sqlite
python3 ingest/ingest_xlsx_v2.py  <folder> data_taric.sqlite
python3 ingest/extract_notes.py   <CN_annex_pdf> data_taric.sqlite
python3 engine/search.py          data_taric.sqlite build
```
Then run the tests (`python3 tests/test_engine.py data_taric.sqlite`) to
confirm everything still passes before trusting the new data.

---

## Using Claude Code for the rest of the build

From here, the remaining work — a web interface, the "save my classifications"
audit database, invoice reading — is best done in Claude Code, where it can run
and be tested live. Install it (Anthropic Console → Claude Code), point it at
this folder, and you can ask it to continue in plain language, just like our
chat. It keeps the work on disk so nothing is lost between sessions.

---

## What is proven vs. what still needs your machine

PROVEN (tested in the sandbox, 39/39 tests green):
- the database is correct and complete
- the engine reaches 100% of codes via legal GRI descent
- duties, anti-dumping, certificates, exclusions, legal notes all wired in
- the AI oracle is anti-hallucination guarded (can only pick real codes)

NEEDS YOUR MACHINE (network required — that's what this guide enables):
- the live AI answering from product text
- the real accuracy number against EBTI rulings
