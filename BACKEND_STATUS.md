# AImport Backend — Status & Roadmap

## Verified working (tested against real EU data, 22/22 tests passing)

**Data foundation.** The five EU Tariff Portal Excel files are ingested into a
44 MB database: 25,681 nomenclature lines (all chapters), 16,597 declarable
codes, 138,482 measures, 136,220 conditions/certificates — each row carrying
legal basis (regulation), validity dates, and verbatim duty text. Confirmed
against the official site via your screenshots: code 6109 and 7308 branches
match exactly.

**Classification engine.** Free text → candidate headings (search, used only as
a generator) → GRI-1 heading selection → GRI-6 level-by-level descent →
declarable code → duties + anti-dumping + certificates by origin → JSON audit
trail. Anti-hallucination guard prevents any invented code (it fired twice
during development and caught real bugs both times).

**"Other" / basket-provision handling (this session).** Residual lines now
render with full broker context: "6109 10 00 90" displays as
*"Other Of cotton — excluding: T-shirts"* rather than a bare "Other". This
fixes readability for you, the clarification questions for users, and — most
importantly — the choices presented to the LLM oracle. Excel "|" artifacts are
stripped. Approach is grounded in how customs professionals read basket
provisions (defined by exclusion from named siblings + parent context).

## Notation note (not a bug)
Database stores codes as `6109100010`; the EU site shows `6109 10 00 10`.
Identical codes, different spacing. Verification report v2 uses the site's
spacing for easy one-to-one comparison.

## Remaining to complete the backend

1. **Your manual verification** (~15 min): check report v2 codes against the
   official site. Certifies the foundation on real authority, not just tests.

2. **CN Section & Chapter legal notes** (Step 2b). Currently GRI-1 reasons over
   heading text only. The notes are legally binding and decide edge cases
   (e.g. what counts as a "part", exclusions between chapters). Source: the CN
   regulation / the portal's notes files. This is the biggest remaining
   accuracy lever.

3. **Geographic group membership** (Step 2b). Resolve preferential/GSP measures
   to specific countries. Currently surfaced as "group not auto-resolved"
   rather than silently dropped. Source: one more portal table or the XML feed.

4. **Live Claude oracle + BTI golden-set evaluation.** Wire the real LLM
   (temperature 0, JSON-validated, already written) and measure accuracy
   against actual EU BTI rulings. **This produces the accuracy number that is
   the product's commercial credibility.**

5. **Audit/persistence layer.** "Add product to my database": store each
   classification + trail per user (the institutional due-diligence feature).

6. **API layer (FastAPI).** Wrap the engine in HTTP endpoints so a frontend /
   ERP can call it: POST product → questions or result; GET classification
   history. This is the boundary the website will talk to.

7. **Web UI** — the commercial face (wizard, confidence display, audit viewer).

8. **Invoice reading** — reuses the whole stack per line item; deliberately last.

## Recommended next step
Either (2)+(3) to close the legal-accuracy gaps, or (4) to get a credibility
number. If a Gdynia/investor demo is near, (6)+(7) make it visual. My pick:
(2)+(3) then (4), because accuracy is the moat — but (6) can run in parallel
since the engine interface is now stable.
