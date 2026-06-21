# REDESIGN_PLAN — AImport Brand & Site Redesign

> Per §2B.1 / build-order step 1: this plan is for **founder approval before any building.**
> Nothing in §4–§9 (tokens, components, reskin) gets built until this is approved.

## 0. Brand name parameter (BLOCKING — §0)
Single source of truth, defined once, referenced everywhere:
```
BRAND_NAME       = "AImport"
BRAND_DOMAIN     = "aimport.co"
BRAND_NAME_LOWER = "aimport"
```
**Awaiting founder confirmation** that the name is `AImport` (not a rebrand to `Cimport`).
No build proceeds until confirmed.

## 1. Stack as found (no changes)
- FastAPI serving **self-contained static HTML** via `FileResponse` (no Jinja, no bundler, no framework).
- CSS is **inline `<style>` per file**, each with its own dark-theme `:root` tokens. Fonts via Google Fonts CDN (Inter only).
- SQLite data stores. Stack stays exactly as-is (§2B.4).

## 2. Surface map (what is presentation vs. frozen)

| Path | File | Role | Phase | Action |
|---|---|---|---|---|
| `GET /` | `index.html` (root) | Marketing landing **+ PIN gate** | **1** | Full reskin to new light system |
| — | `landing.html` (root) | Orphan, not routed (legacy) | — | Move to `legacy/`, preserve |
| — | `server/index.html` | Orphan, not routed (legacy) | — | Move to `legacy/`, preserve |
| `GET /classify` | `server/classify.html` | Product UI | **2** | Chrome-only reskin (later, if approved) |
| `GET /products` | `server/products.html` | Product UI | **2** | Chrome-only reskin |
| `GET /invoice` | `server/invoice.html` | Product UI | **2** | Chrome-only reskin |
| `GET /optimize` | `server/optimize.html` | Product UI | **2** | Chrome-only reskin |
| all `/api/*` | `server/app.py` | Logic | — | **Frozen** (DO_NOT_TOUCH) |

> Phase 1 = marketing + demo gate only. Phase 2 product chrome is **separately approved**; until then those pages stay on current styling (§6 — no half-conversion).

## 3. Files ADDED (all presentation/assets/docs)
```
static/
  css/
    design-tokens.css      # §4 single source of truth — all color/type/space/radius/shadow/motion
    base.css               # reset + base typography wired to tokens
    marketing.css          # landing-specific component styles
  fonts/                   # self-hosted woff2: Inter, Inter Tight, IBM Plex Mono (if self-host chosen)
  img/                     # logo SVGs (solid-dark, customs-blue), icon set
  illustrations/           # labelled placeholder slots (NOT fabricated art)
assets/
  ILLUSTRATION_BRIEF.md    # prompt base + per-scene endings for commissioned art
legacy/                    # preserved original assets (landing.html, server/index.html, dark index snapshot)
DO_NOT_TOUCH.md            # committed (done)
REDESIGN_PLAN.md           # this file
```

## 4. Files CHANGED (presentation only)
- `index.html` — whole-file rewrite to the new system after reading it (preferred for presentation files, §3). The PIN gate markup is reskinned; **its JS logic is copied verbatim, untouched.** Original preserved in `legacy/`.
- `server/app.py` — **ONE additive change only, pending approval:** mount `StaticFiles` at `/static` so `design-tokens.css`, self-hosted fonts, logos, icons, and placeholders can be served. No existing route changes. *(If founder prefers zero `app.py` change, fallback is to keep all CSS inline and fonts on a privacy host — see Open Approvals.)*

## 5. Design system (§4) — summary of what gets tokenised
- **Color:** ink `#111111`, canvas `#F7F5EF`, panel `#E7E3DA`, brand `#1E4F8A`, amber `#D8A342` (rationed: actions/warnings only), green `#6E8F75`. Named tint/shade tokens; no inline opacity, no raw hex outside the token file.
- **Type:** Inter Tight (display 600/700), Inter (body 400/500), IBM Plex Mono (data 400/500 — load-bearing: TARIC/CN codes, confidence, source counts). Modular scale, line-heights per step, `font-display: swap`, preload above-fold faces.
- **Space:** 4px base; scale 4/8/12/16/24/32/48/64/96/128. **Radius:** 6/10/16. **Elevation:** max 2 shadow tokens. **Motion:** `--ease-out`, `--dur-fast 150ms`, `--dur-base 300ms`, `prefers-reduced-motion` respected globally.

## 6. Component build order (§7)
Nav → Hero → Verticals (keyboard-accessible selector, no layout shift) → Platform modules →
**★ Signature provenance/data view** (TARIC card in mono + ordered audit-trail line — the one place boldness is spent) → Footer → Demo-gate reskin (logic untouched).

## 7. Copy & credibility (§8) — RESOLVED with founder
- Voice: operational verbs, sentence case, banned-word list enforced. Never imply autonomy ("supports, documents, makes defensible" — not "decides/auto-files").
- **Module cards — confirmed status (founder decision):**
  - ✅ **TARIC Classification** — LIVE (market as shipped).
  - ✅ **Audit Trail** — LIVE.
  - ✅ **Duty Optimization** — LIVE (`/optimize` + `optimize_session.py`).
  - ✅ **Product Database / Saved Products** — LIVE (`/products` + `products_db.py`) → use as the 4th card.
  - ⛔ **Broker Workspace** — NOT confirmed live → **omit** the card (revisit if/when shipped).
  - ⛔ **CBAM Readiness** — NOT confirmed live → **omit** (or hedge "in development" only if founder later asks). Default: omit.
- All sample TARIC codes / confidence / source counts in marketing UI labelled "illustrative."

## 8. Illustrations (§9) — honest handling
Build **labelled placeholder slots** at correct aspect ratios with the prompt embedded as a comment.
**No fabricated/generated art.** `assets/ILLUSTRATION_BRIEF.md` carries prompt base + per-scene endings
(hero, TARIC classification, audit trail, CBAM, duty optimization) so real art drops in without code changes.

## 9. Anti-"AI-look" self-check (§5)
- No serif display (Inter Tight grotesque instead). ✔ Mono layer mandatory + load-bearing. ✔
- Numbered/stepped markers used **only** for the audit trail (a real ordered sequence). ✔
- Boldness spent in exactly one place (provenance view); everything else quiet. ✔
- Plan re-read for "generic warm-cream SaaS template" smell → distinguisher is the load-bearing mono/TARIC layer + real audit-trail structure, not decoration.

## 10. Verification plan (§10) before handoff
1. App boots. 2. Every pre-existing route returns same status + payload; PIN authenticates identically.
3. `git diff --name-only main...redesign/brand-v2` = only templates/static/tokens/`.md`. No engine/DB/OCR/survey/citation file.
4. Full screenshot set: every page, desktop + mobile (~360px), incl. hover/focus/error/empty/loading.

---

## ✅ Approvals — RESOLVED (2026-06-21)
1. **Brand name** → `AImport` (no rebrand). `BRAND_NAME = "AImport"`.
2. **Dirty `main` tree** → **founder commits their in-progress work first**; I branch only from a clean tree.
   *(Awaiting clean `git status` before creating `redesign/brand-v2`.)*
3. **`StaticFiles` mount in `app.py`** → **Approved** (single additive, presentation-only change).
4. **Fonts** → **self-host woff2** (Inter Tight / Inter / IBM Plex Mono).
5. **Module cards** → live: TARIC Classification, Audit Trail, Duty Optimization, Product Database.
   Omit: Broker Workspace, CBAM Readiness.

## ⏸ Remaining gate before build
- [ ] Founder commits/stashes the in-progress `main` changes → working tree clean.
- [ ] Founder gives the go to start building Phase 1 on `redesign/brand-v2`.
