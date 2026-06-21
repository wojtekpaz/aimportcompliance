# DO_NOT_TOUCH — Engine & Functional Layer Inventory

> Produced during the read phase of the brand/site redesign. This is the explicit,
> reviewable boundary required by §2A of the master prompt. Anything listed here is
> **read-only** for the redesign. The redesign may *read* these files to understand
> the data shapes it presents; it may **not** edit them.
>
> Verification rule (§10.3): `git diff --name-only main...redesign/brand-v2` must contain
> **none** of the paths below — only templates, static assets, token CSS, and planning/brief `.md` docs.

## 1. Classification engine — `engine/`
The product. Determinism is the commercial thesis. **Do not edit.**

| File | Role |
|---|---|
| `engine/classifier.py` | GRI / classification state machine |
| `engine/search.py` | beam-search / semantic search |
| `engine/tree.py` | nomenclature tree traversal |
| `engine/oracles.py` | Anthropic oracle integration (constrained option selection) |
| `engine/prompts.py` | oracle prompt construction |
| `engine/lookup.py` | tariff / nomenclature lookup |
| `engine/bti_lookup.py` | BTI reference lookup |
| `engine/notes.py` | section/chapter legal notes |
| `engine/legal.py` | legal-basis citation lookup *(currently uncommitted on main — founder's work)* |
| `engine/classify_auto.py`, `engine/classify_cli.py` | CLI / batch entry points |
| `engine/eval_harness.py`, `engine/run_accuracy.py` | evaluation harness |

## 2. Server-side business logic — `server/` (Python only)
The HTML templates in `server/` are **presentation** (see REDESIGN_PLAN). The Python is **not**.

| File | Role | Boundary |
|---|---|---|
| `server/app.py` | FastAPI route handlers | **Logic read-only.** Routes, paths, methods, returns, auth, side effects are frozen. The *only* permitted change is an **additive** `StaticFiles` mount + font/asset serving — and only with founder approval (see REDESIGN_PLAN §"Open approvals"). |
| `server/engine_session.py` | classification session state, oracle wiring | Do not edit *(also uncommitted on main)* |
| `server/optimize_session.py` | duty-optimization logic | Do not edit *(also uncommitted on main)* |
| `server/invoice_session.py` | invoice analysis orchestration | Do not edit |
| `server/invoice_ocr.py` | Tesseract OCR preprocessing, confidence gating | Do not edit |
| `server/products_db.py` | `saved_products` store access layer | Do not edit |

## 3. Data ingestion — `ingest/`
`extract_notes.py`, `ingest_bti.py`, `ingest_xlsx.py`, `ingest_xlsx_v2.py`, `inspect_xml.py`,
`parse_taric.py`, `report.py` — **do not edit.**

## 4. Database & schema
- `db/schema.sql` — schema. Do not edit.
- `bti.sqlite`, `data_taric.sqlite`, `saved_products.sqlite` — data stores. Do not edit (gitignored).

## 5. Tests, evaluation, fixtures
- `tests/` (all), `run_golden.py`, `accuracy_report.json` — do not edit.

## 6. API routes — behaviour frozen (paths/methods/returns/auth/side-effects)
From `server/app.py`:
- `GET /` · `GET /classify` · `GET /products` · `GET /invoice` · `GET /optimize` — page routes (template they render *may* be reskinned; what they serve/return is frozen).
- `GET /api/health` · `POST /api/classify` · `POST /api/answer` · `POST /api/products/save` · `GET /api/products` · `POST /api/products/delete` · `GET /api/products/export` · `POST /api/invoice/analyze` · `POST /api/optimize` — **fully frozen.** Not touched by the redesign.

## 7. The demo gate (PIN) — logic frozen
The PIN gate is **client-side JS inside `index.html`** (`ACCESS_PIN`, `sessionStorage('aimport_access')`,
redirect to `/classify`) — a velvet rope, not a server route. The redesign reskins its markup/CSS
**only**; the PIN constant, the storage key, the grant flow, and the redirect target are **frozen**.

---

### If the redesign appears to require touching anything above
Stop and surface it to the founder with the specific reason (§2A rule 2). There is almost always a
presentation-layer solution.
