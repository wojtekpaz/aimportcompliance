# Aimport — Frontend ↔ Backend Handoff (for the new backend's LLM)

**Audience:** the LLM/engineer wiring this frontend to a *new* backend.
**What this repo is:** a customs tariff-classification web app. Today a single
FastAPI process (`server/app.py`) does two jobs at once: it (1) serves the static
HTML pages and (2) answers the JSON API the pages call. You are likely replacing
job (2). This note tells you every page, every route, and the exact contract each
page expects so the pages keep working against your backend.

---

## 0. THE ONE THING THAT MATTERS MOST

**All frontend calls are same-origin, relative paths** — e.g. `fetch('/api/classify')`,
`fetch('/api/products?...')`, `fetch('/survey/' + token + '/data')`. There is **no
configurable API base URL anywhere in the frontend** and **no hardcoded hostname.**

So to connect this frontend to your backend you must do **one** of:

1. **Same-origin (simplest, recommended):** have your backend serve these HTML files
   *and* expose the routes below at the same host/port, exactly as `server/app.py`
   does today. Then nothing in the HTML needs to change.
2. **Separate origins:** add an `API_BASE` constant to each page and prefix every
   `fetch(...)` with it, **and** enable CORS on your backend. Search each HTML file
   for `fetch(` and for `post(` (there is a small `post()` helper in
   `server/classify.html`). There are only ~15 call sites total (listed in §3).

The pages are plain HTML with inline `<script>` — no build step, no framework, no
bundler. Edit the `.html` files directly.

The current router is the source of truth: **`server/app.py`** (main routes) and
**`server/survey_api.py`** (survey routes, mounted via `app.include_router`).

---

## 1. Pages (HTML the browser loads)

| URL | File served | Purpose |
|-----|-------------|---------|
| `GET /` | `index.html` (repo root) | Marketing landing page + **PIN gate** (login overlay). Static; the only API-ish thing it does is link to `/classify`. |
| `GET /classify` | `server/classify.html` | Core classification UI (the main app). Stateful flow — see §4. |
| `GET /products` | `server/products.html` | Saved-classifications dashboard (list / search / delete / export). |
| `GET /invoice` | `server/invoice.html` | Upload a commercial-invoice PDF; get a per-line classification + risk flags. |
| `GET /optimize` | `server/optimize.html` | Given a code + description, propose defensible alternative codes with duty deltas. |
| `GET /survey/{token}` | `server/survey_form.html` (or `survey_expired.html` / `survey_already_submitted.html`) | **Client-facing** clarification survey opened from an emailed link. No login. |
| `GET /survey/review` | rendered in Python (`server/survey_text.py`) | Broker's internal review page (HTML built server-side, not a file). |

Navigation between app pages is via plain `<a href="/classify">`, `/products`,
`/invoice`, `/optimize` links in each page's header.

**PIN gate (`index.html`):** purely client-side. It stores
`sessionStorage['aimport_access']='granted'` and redirects to `/classify`. There is
**no backend auth** in this build. If your backend needs real auth, this is where to
add it (the gate is in the inline script near the bottom of `index.html`,
function `grant()`).

---

## 2. i18n (English / Polish)

- Two markets: `EU` (English, default) and `PL` (Polish).
- The **landing page** (`index.html`) carries its Polish strings **inline** (a
  `var PL={...}` dictionary + an `applyLang()` function). It does **not** call the
  backend for translations.
- The **app pages** load UI strings from `GET /locales/pl.json` (and `en.json`).
  Your backend must serve these two files from `/locales/{name}` (see
  `app.py` `locales()` — it only allows `en.json` and `pl.json`). Files live in
  `locales/` in the repo.
- Market is passed to the API as a `market` field (`"EU"` or `"PL"`) on classify/
  answer/invoice calls. In `EU` the engine path is unchanged; `PL` adds Polish
  translation in/out and Polish national measures. If your backend is EU-only you
  can ignore `market` and always behave as `EU`.

---

## 3. Every frontend API call site (page → endpoint)

| Page | Call | Endpoint |
|------|------|----------|
| `classify.html` | `post('/api/classify', {...})` | `POST /api/classify` |
| `classify.html` | `post('/api/answer', {...})` | `POST /api/answer` |
| `classify.html` | `post('/api/products/save', {...})` | `POST /api/products/save` |
| `classify.html` | `fetch('/api/wit?code=...')` | `GET /api/wit` (PL evidence, optional) |
| `classify.html` | `fetch('/api/pl-measures?code=...')` | `GET /api/pl-measures` (PL, optional) |
| `classify.html` | `/api/landed-cost?...` | `GET /api/landed-cost` (optional calc) |
| `classify.html` | `fetch('/locales/pl.json')` | `GET /locales/{name}` |
| `products.html` | `fetch('/api/products?'+params)` | `GET /api/products` |
| `products.html` | `fetch('/api/products/delete', {POST})` | `POST /api/products/delete` |
| `products.html` | (export link/button) | `GET /api/products/export` (CSV download) |
| `invoice.html` | `fetch(url, {method:'POST', body: FormData})` | `POST /api/invoice/analyze` |
| `optimize.html` | `fetch('/api/optimize', {POST})` | `POST /api/optimize` |
| `survey_form.html` | `fetch('/survey/'+token+'/data')` | `GET /survey/{token}/data` |
| `survey_form.html` | `fetch('/survey/'+token+'/submit', {POST})` | `POST /survey/{token}/submit` |
| all app pages | `fetch('/locales/pl.json')` | `GET /locales/{name}` |

---

## 4. API contract — main app (`server/app.py`)

JSON in / JSON out unless noted. Request models are the Pydantic classes in
`app.py`; response shapes come from the modules it calls
(`engine_session.py`, `products_db.py`, `invoice_session.py`, `optimize_session.py`).
**Verify exact fields against those files** — line refs given.

### Classification is STATEFUL (read this carefully)

The engine is a multi-turn state machine. `POST /api/classify` *starts* a session;
`POST /api/answer` *continues* it. The server holds the engine state in memory keyed
by `session_id`; the client only ever echoes back the `session_id` + `sig` it was
given. Your backend must keep equivalent per-session state.

**`POST /api/classify`** — start.
Request: `{ "text": str, "origin": str="", "hint": str="", "market": "EU"|"PL"="EU" }`
Response is one of these `status` values (see `engine_session.py:146-280`):
- `"needs_question"` → `{status, session_id, sig, question, ...}` — ask the user.
  `question` may be a string or `{question, options:[{id,label,...}]}`.
- `"needs_pre_classify"` → like above with `sig:"__pre_classify__"`.
- `"needs_review"` → ambiguous, needs user review.
- `"classified"` → `{status, session_id, code, trail:[{gri,action,chosen,...}], ...}`
  (`trail` is the GRI reasoning path; `code` is the final CN/TARIC code).
- `"error"` → `{status:"error", message: str}`.

**`POST /api/answer`** — continue.
Request: `{ "session_id": str, "sig": str, "choice": str, "market": "EU"|"PL"="EU" }`
- `choice` is the chosen option `id` (digits/colon) **or** free text. In `PL`, free
  text is auto-translated to English for the engine.
Response: same `status` union as `/api/classify`.

> Frontend handles these statuses in `classify.html`: `needs_question`,
> `needs_pre_classify`, `classified`, `error` (plus `expired`/`in_force` which come
> from the WIT/measures display calls, not the classify flow).

### Saved products (dashboard)

- **`POST /api/products/save`** — Request `{ "result": <the classified result dict>, "note": str="" }`. Saves a completed classification. (`products_db.save`)
- **`GET /api/products`** — Query: `search, origin, chapter, confidence, has_defense` (all optional strings). Response `{ "products": [...], "stats": {...} }`. (`products_db.list_products` + `stats`)
- **`POST /api/products/delete`** — Request `{ "id": str }`.
- **`GET /api/products/export`** — returns **CSV** (`Content-Disposition: attachment; filename=aimport_classifications.csv`), not JSON.

Storage: `saved_products.sqlite`, path = `AIMPORT_DATA_DIR` env (default `.`); schema in `server/products_db.py`. This DB is **runtime data**, not shipped reference data.

### Invoice analysis

- **`POST /api/invoice/analyze`** — **multipart/form-data** (not JSON).
  Fields: `file` (a **PDF**, required), `origin` (str), `market` (`EU`|`PL`).
  Response (`invoice_session.py:387-409`): `{ summary:{total,issues,ok,origin,...}, items:[...], meta:{...}, extraction_status }`.
  If any line is too vague to classify, the server creates a client survey and adds
  `clarifications:[...]`, `survey_token`, `survey_url` (see `_attach_survey` in
  `app.py:92`). OCR fallback needs system `tesseract-ocr` + `poppler-utils`
  (see `railpack.json`).

### Optimize (alternative codes)

- **`POST /api/optimize`** — Request `{ "description": str, "current_code": str, "origin": str="" }`.
  Response (`optimize_session.py`): `{ analysis_summary, original, alternatives:[...] }`
  or `{ "error": str }`. The server then attaches a deterministic
  `defensibility` score and `duty_delta_pct` to each alternative (`app.py:381-397`).

### PL-only display helpers (safe to stub/ignore for an EU-only backend)

- **`GET /api/wit?code=`** — Polish binding tariff rulings (WIT). Display-only.
- **`GET /api/pl-measures?code=&date=`** — Polish VAT/excise + Polish description, from `data_isztar_pl.sqlite`.
- **`GET /api/landed-cost?customs_value=&duty_rate=&code=&date=&market=`** — deterministic landed-cost calc.

### Utility

- **`GET /api/health`** — `{ ok, model, db, key_present, nomenclature_lines, ocr_available, ... }`. Good smoke test that DB + API key are wired.
- **`GET /locales/{name}`** — serves `locales/en.json` | `locales/pl.json` only.

---

## 5. API contract — survey (`server/survey_api.py`, mounted on the same app)

Client flow (the importer, from an emailed link — no auth, token-gated):
- **`GET /survey/{token}`** → serves `survey_form.html` (or `survey_expired.html` /
  `survey_already_submitted.html` depending on state).
- **`GET /survey/{token}/data`** → JSON: the questions to render.
- **`POST /survey/{token}/submit`** → JSON body of answers; records them.
- **`GET /survey/{token}/results`** → JSON results.

Broker/internal flow:
- **`POST /survey/create`** → create a survey session, returns `{token, ...}`.
  (Normally called server-side by `_attach_survey`, not by the browser.)
- **`GET /survey/pending/list`** → list of pending clarifications for the broker.
- **`POST /survey/{token}/mark-sent`** → mark the link as emailed.
- **`GET /survey/review`** (HTML) and **`POST /survey/review/{flag_id}/reviewed`** → broker review queue.

Survey state lives in its own SQLite (`server/survey_db.py`). The public link host is
computed by `_public_base()` in `app.py` — it rewrites localhost to the public domain
and is overridable with `PUBLIC_BASE_URL`. **If you move backends, set
`PUBLIC_BASE_URL` to your public app URL** so emailed survey links point at the right
host.

---

## 6. Environment variables

| Var | Required | Meaning |
|-----|----------|---------|
| `ANTHROPIC_API_KEY` | **Yes** | The classifier calls Claude (`engine/oracles.py`, model `claude-sonnet-4-6`). Without it, classification fails. |
| `PUBLIC_BASE_URL` | Recommended | Public base for client-facing survey links. Defaults to `https://app.aimport.co`. |
| `AIMPORT_DATA_DIR` | Optional | Where `saved_products.sqlite` is written (use a persistent volume in prod). Default: current dir. |
| `ISZTAR_PL_DB` | Optional | Override path to `data_isztar_pl.sqlite` (PL measures). |

---

## 7. Data files (reference data the engine reads)

- `data_taric.sqlite` — **required.** The EU tariff nomenclature/measures DB. Read
  from the repo root (`engine_session.py:30 → DB_PATH = ROOT / "data_taric.sqlite"`).
  Ships in the repo. Without it, classification has no ground truth.
- `data_isztar_pl.sqlite` — Polish ISZTAR cache (PL national measures + descriptions).
  Needed only for the `PL` market.
- `bti.sqlite` — optional EBTI rulings cache. Engine degrades gracefully if absent
  (`engine_session.py:28`).
- Large source archives (`*.zip`, `*.7z`, `polish_aimport/`) are **build inputs only**
  and are git-ignored; you do not need them to run the app.

> Note: `.gitignore` lists `*.sqlite`, but `data_taric.sqlite` and
> `data_isztar_pl.sqlite` are deliberately force-tracked because the app loads them
> from the repo. Keep them.

---

## 8. How to run the current backend (reference behavior to match)

```
pip install -r requirements.txt        # FastAPI + uvicorn + pdfplumber + OCR libs
export ANTHROPIC_API_KEY=sk-...
python3 server/app.py                   # serves http://127.0.0.1:8000
```

`server/app.py`'s docstring (top of file) is a concise endpoint list; this document
expands it. If your backend reproduces the routes in §1, §4 and §5 at the same
origin, the existing HTML works unchanged.
