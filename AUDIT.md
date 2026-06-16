# AImport — Infrastructure Audit

**What this document is.** A complete, ground-truth account of the AImport
classification system as it stands now: the data, the engine, the web app, the
flow of a request, and every instruction the AI is given. It is written so an
external engineer could rebuild the system from this document alone. Where a
piece pre-existed (handed over from the original Claude chat) versus was built/
changed in the recent sessions, it is marked **[PRE-EXISTING]** or **[ADDED]**.

Last verified: 2026-06-14. Engine tests: 28/28 passing. Reachability: 100%.

---

## 1. One-paragraph overview

AImport turns a plain product description (e.g. "men's cotton knitted t-shirt")
into the correct 10-digit EU customs code, plus the duties and trade measures
for a given country of origin, plus a full audit trail of how it got there. The
legal reasoning follows the WCO **General Interpretative Rules (GRI)**: pick a
4-digit heading (GRI-1), then descend dash-level by dash-level to a declarable
code (GRI-6). **Control flow is ordinary code; the AI is only ever a "chooser"**
that selects among options the code hands it — it can never invent a code. An
optional web app exposes this as a wizard that asks the user a question only
when the AI is genuinely unsure.

---

## 2. Architecture map

```
                         ┌─────────────────────────────────────────┐
   BROWSER               │  server/index.html  (one static page)   │
   (the user)  ───────►  │  textbox + origin + hint + Classify     │
                         └───────────────┬─────────────────────────┘
                                         │ HTTP JSON
                         ┌───────────────▼─────────────────────────┐
   WEB API   [ADDED]     │  server/app.py  (FastAPI)                │
                         │   GET /  POST /api/classify  /api/answer │
                         └───────────────┬─────────────────────────┘
                         ┌───────────────▼─────────────────────────┐
   SESSION   [ADDED]     │  server/engine_session.py                │
                         │   HybridOracle + in-memory sessions      │
                         │   (holds the conversation across clicks) │
                         └───────────────┬─────────────────────────┘
                         ┌───────────────▼─────────────────────────┐
   ENGINE                │  engine/classifier.py  classify(...)     │
   [PRE-EXISTING core,   │   GRI state machine (control = code)     │
    [ADDED] interp layer │   ├─ search.py        candidate headings │
    + prompt changes]    │   ├─ oracles.py       the AI chooser     │
                         │   ├─ prompts.py       all AI instructions│
                         │   ├─ notes.py         legal notes (GRI-1)│
                         │   ├─ tree.py          dash-level descent  │
                         │   └─ lookup.py        duties & measures   │
                         └───────────────┬─────────────────────────┘
                         ┌───────────────▼─────────────────────────┐
   DATA      [PRE-EXIST] │  data_taric.sqlite  (~53 MB, 25,681 GN   │
                         │  lines; built by ingest/ from EU files)  │
                         └─────────────────────────────────────────┘
```

Two entry points share the **same** `classify()` engine:
- `engine/classify_auto.py` — CLI, AI answers every step (`ClaudeOracle`).
- `engine/classify_cli.py`  — CLI, a human answers every step (`HumanOracle`).
- the web app — AI answers, asks the human only when unsure (`HybridOracle`).

---

## 3. File inventory

### Data build pipeline `ingest/` **[PRE-EXISTING]**
Run only when the EU publishes a tariff update; they build `data_taric.sqlite`
from the EU Tariff Portal Excel/PDF exports.
- `ingest_xlsx.py`, `ingest_xlsx_v2.py` — load nomenclature, measures,
  conditions, exclusions, legal basis.
- `extract_notes.py` — pull Section/Chapter legal Notes from the CN annex PDF.
- `parse_taric.py`, `inspect_xml.py`, `report.py` — XML parsing + QA reports.
- After ingest: `python3 engine/search.py <db> build` rebuilds the FTS index.

### Engine `engine/`
| File | Role | Status |
|------|------|--------|
| `classifier.py` | The GRI state machine — `classify()`. Orchestrates everything. | [PRE-EXISTING] core + [ADDED] interpretation layer, single-option handling, option labels |
| `oracles.py` | The "chooser" implementations: `ClaudeOracle` (AI), `HumanOracle` (CLI). | [PRE-EXISTING] + [ADDED] `propose_headings`, `last_reason` |
| `prompts.py` | **Every AI instruction in one place.** | [ADDED] |
| `search.py` | FTS5 keyword search → candidate headings (a generator only). | [PRE-EXISTING] |
| `tree.py` | Dash-level (indent) aware navigation of the code tree. | [PRE-EXISTING] |
| `notes.py` | Retrieves binding Section/Chapter Notes for GRI-1. | [PRE-EXISTING] |
| `lookup.py` | Inheritance-aware duty/measure lookup by code + origin. | [PRE-EXISTING] |
| `classify_auto.py` / `classify_cli.py` | CLI entry points. | [PRE-EXISTING] |
| `eval_harness.py` | Reachability test via a "perfect oracle". | [PRE-EXISTING] |
| `run_accuracy.py` | Live-AI accuracy vs a golden set. | [PRE-EXISTING] + [ADDED] crash-hardening |

### Web app `server/` **[ADDED, this work]**
| File | Role |
|------|------|
| `app.py` | FastAPI service: serves the page + the JSON API. |
| `engine_session.py` | `HybridOracle` + session store; bridges the engine to stateless HTTP. |
| `index.html` | Single self-contained page (HTML+CSS+JS), the UI. |

---

## 4. The data layer (`data_taric.sqlite`)

SQLite, ~53 MB, schema in `db/schema.sql`. Every legal row is date-bounded
(`validity_start` / `validity_end`, NULL end = still in force) and carries its
legal basis (regulation / Official Journal) for the audit trail.

**Core tables actually used by the engine:**
- `goods_nomenclature` — the code tree. `item_id` (10 digits), `producline_suffix`
  (usually `80` for declarable lines), `is_leaf` (1 = declarable).
- `goods_nomenclature_indent` — `indent_level` = number of dashes = tree depth.
  **This, not digit-pairs, defines the hierarchy.**
- `goods_nomenclature_description` — the legal text per line.
- `legal_note` — Section & Chapter Notes (`kind` ∈ {section, chapter}, `ident`).
- `measure` / `measure_type` — duties, ADD, preferences, controls; each links a
  code + a measure type + a geographical area (+ optional additional code).
- `measure_component` — compound duty parts (e.g. 4.5% MIN 0.3 EUR/NAR).
- `measure_condition_v2` — structured certificate/licence conditions.
- `measure_exclusion` — country carve-outs from group measures (prevents false
  positives).
- `legal_basis` — Official Journal provenance.
- `additional_code_description`, `certificate_meaning` — code meanings.
- `code_search` — an **FTS5** virtual table: each declarable code indexed under
  its **full path text** (chapter > heading > … > line), so "cotton t-shirt"
  matches code 6109100010 even though its own line only says "T-shirts".

---

## 5. The classification flow (end to end)

Entry: `classify(conn, product_text, oracle, hint="", origin="")` in
`engine/classifier.py`. Returns a `Result` (`status`, `code`, `confidence`,
`question`, `trail`, `measures`).

**Stage 0 — candidate generation (find plausible headings).**
1. `candidate_headings()` (FTS keyword search) → top distinct 4-digit headings.
2. If a `hint` was given, hint headings are merged in as a *prior* (not a
   constraint); a conflict with search evidence is flagged.
3. **[ADDED] AI interpretation layer:** `oracle.propose_headings(product_text)`
   asks the AI which headings could plausibly apply (bridging commercial names
   like "smartwatch" that the tariff never uses). **Every proposed heading is
   validated against `goods_nomenclature` before it is added** — an invented
   heading is silently dropped. This both fixes recall and surfaces the genuine
   competing headings.
4. If after all that there are still no candidates → `needs_review`.

**Stage 1 — GRI-1 heading selection.**
5. Build options, one per candidate, each labelled by the heading's **own**
   distinguishing text (`_heading_option_text`, [ADDED] — previously they shared
   an indistinguishable chapter prefix).
6. `notes.py` retrieves the binding Section/Chapter Notes for the candidate
   chapters; they are injected into the prompt as legal context.
7. `oracle.choose(GRI1_HEADING, options, ctx)` → an option id or `UNSURE`.
   - id → that heading is selected.
   - `UNSURE` + only one option → auto-select it ([ADDED]; no dead-end question).
   - `UNSURE` + multiple → return `needs_question` (CLI/auto) or raise to the web
     layer (see §6).

**Stage 2 — GRI-6 descent (dash level by dash level).**
8. `first_level_children()` returns the first dash-level subdivisions.
9. For each level: build options from `tree.py`; `oracle.choose(GRI6_DESCENT,…)`.
   - A residual "Other" line is rendered with the siblings it excludes so its
     meaning is visible.
   - `UNSURE` + one option → auto-take ([ADDED]). `UNSURE` + many → ask.
10. Descend via `next_level_children()` until no children remain.

**Stage 3 — terminal.**
11. If the endpoint `is_declarable()` → `status = classified`, set `code`,
    `confidence`, and append the full path to the trail.
12. If `origin` given → `lookup(conn, code, origin)` attaches duties/measures.

**The anti-hallucination guard** (`_validate_choice`): every value the oracle
returns must be one of the supplied option ids (with a tolerant re-match if the
AI drops a trailing `:80` tag). Anything else **raises** — a fabricated code is
structurally impossible.

**Duty lookup** (`lookup.py`): builds the ancestor chain of the code, collects
all valid measures across the chain by origin + date, keeps the **most specific**
duty per type, keeps all trade-defence/control measures, and surfaces country-
**group** measures (GSP, EPAs) as `group_unresolved` rather than guessing
membership (a documented data gap).

---

## 6. The user-input flow (web app)

Each GRI step is a **separate, stateless AI call**, and HTTP requests are
stateless too — so the conversation is reconstructed on every click. Mechanism:

**HybridOracle** (`server/engine_session.py`) implements the engine's `Oracle`
interface but is "AI-first, ask-the-human-only-when-stuck":
- Each decision point gets a stable **signature** = `stage | heading | sorted
  option ids` (deterministic, so it is identical on every re-run).
- `choose()`:
  1. If only one option → take it (never asks a one-option question).
  2. If the user already answered this signature → replay it.
  3. If the AI already answered this signature → replay from cache (cheap,
     deterministic; no duplicate API calls).
  4. Otherwise call the AI. If it answers → cache + return. If it returns
     `UNSURE` → **raise `NeedHumanAnswer`** carrying the question, the real
     options, and the AI's stated reason (`last_reason`).

**Request lifecycle:**
```
POST /api/classify {text, origin?, hint?}
   → start(): create session {text, origin, hint, human_answers={}, llm_cache={}, claude}
   → run classify() with a HybridOracle
       • completes            → {status:"classified", code, code_spaced,
                                  description, duty_text, confidence, trail}
       • NeedHumanAnswer      → {status:"needs_question", session_id, sig,
                                  question:{ask, why, options:[{id,text}]}}

POST /api/answer {session_id, sig, choice}
   → answer(): record human_answers[sig]=choice, re-run classify()
       (replays prior answers + AI cache, advances to the next question or the
        final code; capped at MAX_ROUNDS=8 questions)
```
The page (`index.html`) renders a question as radio options, posts the pick,
and shows the final code + duties + a collapsible audit trail. The AI's
`reason` is folded into the question text, so the user sees *why* it is being
asked (e.g. "'sneakers' doesn't state the upper material — rubber, leather, or
textile — which decides between headings 6402–6405").

**Endpoints:** `GET /` (page), `GET /api/health` (key/db sanity),
`POST /api/classify`, `POST /api/answer`. Run: `python3 server/app.py` →
http://127.0.0.1:8000.

---

## 7. The guidance given to the AI (all prompts)

All four prompts live in **`engine/prompts.py`** [ADDED — consolidated from
strings previously scattered across `oracles.py` and `classifier.py`]. Tuning
the AI's reasoning is now editing plain text in this one file.

**How they are injected** (each GRI step is a fresh `POST /v1/messages` to the
Claude API; model `claude-sonnet-4-6`, `temperature: 0`, raw `urllib`, no SDK):
- `system` field  = **`SYSTEM_RULES`** — sent on **every** step.
- `user` content  = the per-stage prompt (`GRI1_HEADING` or `GRI6_DESCENT`)
  followed by `OPTIONS:` and the id+text of each allowed choice.
- `INTERPRET` is the `system` field of the separate up-front proposer call.

### `SYSTEM_RULES` — always-on, read on every GRI step
> You are a customs classification reasoner applying the WCO General
> Interpretative Rules (GRI). You are given a product description and a closed
> list of options, each with an id. Apply these rules on EVERY step:
> 1. Choose by the LEGAL TEXT of the options and the product's FUNCTION and
>    essential character — not by whether the product's commercial name
>    literally appears. Example: a device that sends or receives data over a
>    wireless network IS "apparatus for the transmission or reception of data",
>    even if the word for that device never appears in the tariff text.
> 2. Apply ONLY the GRI stage named in the prompt; never skip ahead to a deeper
>    level.
> 3. A residual "Other" option is a LAST RESORT. NEVER choose "Other" if any
>    NAMED sibling option could plausibly cover the product. Always prefer the
>    most specific NAMED option whose legal text the product satisfies.
> 4. If the product description does not contain the attribute that DECIDES
>    between the options, you MUST answer UNSURE. Never guess.
>
> Respond ONLY with JSON: {"choice": "<option id or __UNSURE__>", "reason":
> "<one sentence naming the deciding attribute or the legal text you relied on>"}

### `GRI1_HEADING` — the heading step
> GRI-1: which 4-digit heading legally covers this product: '{product}'? Choose
> by the heading's legal text and the product's function — not its commercial
> name. If the description lacks the attribute that decides between the headings,
> answer UNSURE.{notes}

`{product}` = the user's text; `{notes}` = the retrieved binding Section/Chapter
Notes block (or empty).

### `GRI6_DESCENT` — each descent step
> GRI-6: within heading {heading} ({heading_desc}), which subdivision at THIS
> dash level covers: '{product}'?
> - Compare ONLY these same-level options; do not skip to a deeper level.
> - Before choosing any residual "Other", examine EACH named option at this
>   level and confirm the product fails its legal text. Choose "Other" ONLY if
>   every named option is genuinely excluded; otherwise pick the named option.
> - Answer UNSURE if the attribute that decides between these options is missing
>   from the description.

### `INTERPRET` — the front interpretation layer (proposes, then DB-validated)
> You are an expert EU customs (CN/HS) classifier. Given a product, list the
> 4-digit headings that could plausibly cover it. INCLUDE the real alternatives
> an officer would weigh by function, material and essential character; prefer
> 2-5 headings over a single guess. Do NOT pick a full code. Respond ONLY with
> JSON: {"headings": ["nnnn", ...], "normalized": "<product restated in formal
> tariff terms>"}

---

## 8. The safety guarantees (why this is auditable)

1. **No invented codes.** The AI only ever returns an option id from a DB-derived
   list; `_validate_choice` rejects anything else. `INTERPRET`'s suggestions are
   DB-validated before use.
2. **Legal order enforced by code, not the model.** Chapter → GRI-1 → GRI-6
   descent is a state machine; the model cannot skip steps.
3. **Refuses to guess.** Missing deciding attribute → a clarifying question (or
   `needs_review`), never a fabricated answer.
4. **Every decision logged.** The `trail` records candidate generation, the AI
   interpretation, notes retrieved, each heading/level chosen, and the terminal
   path — the institutional/audit product.
5. **Provenance preserved.** Duties carry their regulation / Official Journal;
   unresolved country-group measures are surfaced, not silently dropped.

---

## 9. Changelog — what was built/changed in the recent sessions

1. **Environment fix:** macOS python.org Python had no trusted certificate
   bundle → all API calls failed with an SSL error mislabelled as "no internet".
   Fixed once via `Install Certificates.command`.
2. **Accuracy-run hardening (`run_accuracy.py`, `classifier.py`):** the
   anti-hallucination guard crashed the whole accuracy run when the AI dropped a
   `:80` id suffix. Now: tolerant id re-match + per-item error isolation.
3. **Web app (`server/`)** — FastAPI service, single-page UI, and the
   `HybridOracle` conversational loop that asks the user only when the AI is
   unsure (works across stateless HTTP).
4. **AI interpretation layer (`oracles.propose_headings` + `classify`):** maps
   commercial names to candidate headings; fixed "smartwatch" (was 0 candidates)
   and makes ambiguous products ask good questions. DB-validated → safe.
5. **Smarter questions:** one-option steps auto-resolve (no dead-end "pick the
   only choice"); the AI's stated reason is shown with each question; GRI-1
   option labels now use each heading's own distinguishing text.
6. **Prompt consolidation (`prompts.py`)** + a strengthened anti-"Other"/basket
   rule. Verified effect: "smartwatch" answered as a communication device now
   descends to the named **8517 62 00 00** instead of the residual 8517 69 90.

---

## 10. Known limitations / open items

- **Country-group measures unresolved.** GSP/EPA preferential rates are surfaced
  as "check membership manually" — membership table not in the current export.
- **Accuracy number is illustrative.** Only an 8-item seed golden set exists; a
  real credibility figure needs 30–100 real EU BTI rulings (parked: the EU's own
  EBTI/EUR-Lex sites block automated fetching; needs human-in-the-loop export).
- **"Other" fix is prompt-only.** An optional adversarial double-check (force the
  AI to justify "Other" vs each named sibling) was discussed but not built.
- **Smartwatch confidence.** Bare "smartwatch" can still pick 9102 confidently at
  GRI-1 rather than asking; tuning decision, not a bug.
- **Sessions are in-memory.** No persistence/"save my classifications" layer yet.

---

## 11. How to reproduce from scratch

```bash
# 1. Python 3.10+ ; from the project folder:
pip install -r requirements.txt           # lxml, openpyxl, pdfplumber, fastapi, uvicorn
# (macOS python.org builds: run the "Install Certificates.command" once)

# 2. API key (kept in memory only, never written to disk):
export ANTHROPIC_API_KEY=sk-ant-...

# 3. Confirm the connection:
python3 engine/oracles.py                 # -> RESULT: OK

# 4. Classify from the terminal (AI answers):
python3 engine/classify_auto.py data_taric.sqlite "men's cotton knitted t-shirt" CN

# 5. Run the web app:
python3 server/app.py                      # -> http://127.0.0.1:8000

# 6. Tests:
python3 tests/test_engine.py data_taric.sqlite     # 28/28
python3 engine/eval_harness.py data_taric.sqlite   # reachability 100%

# Rebuilding the DB after an EU tariff update:
python3 ingest/ingest_xlsx.py    <folder> data_taric.sqlite
python3 ingest/ingest_xlsx_v2.py <folder> data_taric.sqlite
python3 ingest/extract_notes.py  <CN_annex_pdf> data_taric.sqlite
python3 engine/search.py         data_taric.sqlite build
```

**To change how the AI reasons:** edit `engine/prompts.py` only.
**To change the legal flow:** edit `engine/classifier.py` (the state machine).
**Model rotation:** update `ClaudeOracle.DEFAULT_MODEL` in `engine/oracles.py`.
