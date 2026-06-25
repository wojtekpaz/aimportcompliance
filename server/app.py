#!/usr/bin/env python3
"""
app.py — AImport local web service (FastAPI).

Wraps the proven GRI engine in a tiny HTTP API and serves one self-contained
web page. The engine's logic is untouched; this only exposes it.

RUN IT:
    export ANTHROPIC_API_KEY=sk-...
    python3 server/app.py
Then open http://127.0.0.1:8000 in your browser.

ENDPOINTS:
    GET  /                -> the web page
    GET  /classify        -> classification page
    GET  /products        -> saved products dashboard
    GET  /api/health      -> {ok, model, db}  (key/db sanity check)
    POST /api/classify    -> start: {text, origin?, hint?}
    POST /api/answer      -> continue: {session_id, sig, choice}
    POST /api/products/save    -> save a completed classification
    GET  /api/products         -> list/search saved classifications
    POST /api/products/delete  -> delete one saved classification
    GET  /api/products/export  -> download CSV of all saved classifications
"""
import os
import sqlite3
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root: wit_pl, isztar_pl
import logging                         # noqa: E402
import engine_session as es          # noqa: E402
import products_db as pdb            # noqa: E402
import invoice_session as inv           # noqa: E402
import invoice_ocr as ocr               # noqa: E402
import optimize_session as opt_s        # noqa: E402
import tempfile, os as _os              # noqa: E402
from fastapi import UploadFile, File, Request   # noqa: E402

app = FastAPI(title="AImport", docs_url="/api/docs")
HERE = Path(__file__).resolve().parent
log = logging.getLogger("uvicorn.error")


@app.middleware("http")
async def _revalidate_html(request, call_next):
    """HTML pages must always revalidate so a redeploy (new layout) shows up
    immediately instead of a browser serving a stale cached copy. ETag still
    lets the server answer 304 when nothing changed, so it stays cheap."""
    resp = await call_next(request)
    if resp.headers.get("content-type", "").startswith("text/html"):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


# No broker auth in this build (PIN gate only); surveys are attributed to a
# single local broker so the pending-clarifications dashboard has an owner.
DEFAULT_BROKER_ID = "broker-local"


# Public host the app is served from; client survey links must point here, never
# the broker's localhost. Overridable per-deployment via PUBLIC_BASE_URL.
PUBLIC_BASE_DEFAULT = "https://app.aimport.co"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", ""}


def _public_base(request):
    """Base URL used to build the client-facing survey link.

    A client receives this link by e-mail and opens it on their own device, so
    it must be PUBLICLY reachable — never the broker's localhost. PUBLIC_BASE_URL
    overrides everything; otherwise a localhost/loopback request (the broker
    testing locally) is rewritten to the public app domain, and a real hosted
    request keeps its own host but is forced to https."""
    env = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if env:
        return env
    host = (request.url.hostname or "").lower()
    if host in _LOCAL_HOSTS or host.endswith(".local"):
        return PUBLIC_BASE_DEFAULT
    base = str(request.base_url).rstrip("/")
    if base.startswith("http://"):
        base = "https://" + base[len("http://"):]
    return base


def _attach_survey(result, request):
    """If invoice analysis froze ≥1 line, create the survey session server-side
    and return a sanitised preview + the shareable survey URL. Engine state
    (snapshots, option maps) never leaves the server."""
    if not isinstance(result, dict):
        return result
    # Pre-classification plausibility guard (extraction-level): if the extractor
    # judged the read suspect (every line an invalid code), route it to the same
    # human-review surface the survey generator uses, rather than presenting junk.
    if result.get("extraction_status") == "extraction_suspect":
        try:
            import survey_review as srev
            inv_ref = ((result.get("summary") or {}).get("invoice_no")
                       or (result.get("meta") or {}).get("invoice_no") or "")
            srev.raise_flag(
                broker_id=DEFAULT_BROKER_ID, invoice_ref=inv_ref,
                line_number=0,
                description=(result.get("meta") or {}).get("invoice_no") or "(scanned invoice)",
                flag_type="extraction_suspect", field="extraction",
                observation=result.get("message", "Extraction looks wrong."))
        except Exception:
            pass  # additive; never break analysis
    frozen = result.get("frozen_lines") or []
    if not frozen:
        result.pop("frozen_lines", None)
        for it in result.get("items") or []:
            it.pop("frozen", None)
        return result
    invoice_ref = ((result.get("summary") or {}).get("invoice_no")
                   or (result.get("meta") or {}).get("invoice_no") or "")
    survey_lines = frozen
    try:
        import survey_db as sdb
        import survey_pipeline as spipe
        from survey_locale import resolve_survey_locale
        # Language is decided in control flow, never inferred from invoice
        # content. No broker auth exists in this build (single DEFAULT_BROKER_ID),
        # and the invoice carries no detected-language signal, so the genuine
        # source is the broker's UI request (Accept-Language); everything else
        # fails safe to DEFAULT_SURVEY_LOCALE ("en").
        survey_locale = resolve_survey_locale(
            broker_locale=None,
            ui_locale=request.headers.get("accept-language"),
            invoice_locale=None,
        )
        # Phase 2: generate one anchored, localized question per line from DB
        # candidates; lines the generator flags are routed to /survey/review and
        # excluded from the client survey (never simplify_question-downgraded).
        survey_lines, flagged = spipe.build_survey_lines(
            frozen, locale=survey_locale,
            broker_id=DEFAULT_BROKER_ID, invoice_ref=invoice_ref)
        result["flagged_for_review"] = flagged
        if survey_lines:
            created = sdb.create_session(broker_id=DEFAULT_BROKER_ID,
                                         invoice_ref=invoice_ref,
                                         frozen_lines=survey_lines,
                                         language=survey_locale)
            token = created["token"]
            base = _public_base(request)
            result["survey_token"] = token
            result["survey_url"] = f"{base}/survey/{token}"
    except Exception as e:                       # additive; never break analysis
        result["survey_error"] = str(e)[:160]
    # client-facing preview only — no engine internals (only lines that became a
    # survey question; flagged lines are held for broker review, not shown here)
    result["clarifications"] = [
        {"line_number": f.get("line_number"),
         "description": f.get("description_used"),
         "freeze_reason": f.get("freeze_reason"),
         "engine_question": f.get("engine_question")}
        for f in survey_lines]
    result.pop("frozen_lines", None)
    for it in result.get("items") or []:
        it.pop("frozen", None)
    return result


@app.on_event("startup")
def _ocr_startup_check():
    """OCR silently no-ops if the tesseract binary is missing from the deployed
    container, so fail loud at boot rather than mysteriously at upload time."""
    ok, detail = ocr.tesseract_status()
    if ok:
        log.info("OCR fallback ready (%s).", detail)
    else:
        log.warning("OCR fallback DISABLED — %s. Scanned/image-only invoices "
                    "will report as unreadable. Install tesseract-ocr + "
                    "poppler-utils (see railpack.json).", detail)


class ClassifyIn(BaseModel):
    text: str
    origin: str = ""
    hint: str = ""
    market: str = "EU"


class AnswerIn(BaseModel):
    session_id: str
    sig: str
    choice: str
    market: str = "EU"


def _localize_pl(result, market):
    """PL profile only: rewrite a clarification question into Polish AFTER the
    engine has produced it. The engine itself is untouched, so the English path
    (market=EU, the default) is byte-for-byte unchanged."""
    if (market or "EU").upper() != "PL" or not isinstance(result, dict):
        return result
    try:
        import pl_question
        q = result.get("question")
        if isinstance(q, dict):
            result["question"] = pl_question.localize(q, "PL")
        elif isinstance(q, str) and q.strip():
            tq = pl_question.translate_text(q)
            if tq:
                result["question"] = tq
        # classified result: attach the authoritative Polish nomenclature
        # description (from the local ISZTAR store) for the determined code
        if result.get("status") == "classified" and result.get("code"):
            import isztar_pl
            from datetime import date as _date
            m = isztar_pl.get_pl_national_measures(result["code"], _date.today().isoformat())
            if m.get("description_pl"):
                result["description_pl"] = m["description_pl"]
    except Exception:
        pass  # localization is additive; never break the classification response
    return result


class SaveIn(BaseModel):
    result: dict
    note: str = ""


class DeleteIn(BaseModel):
    id: str


class OptimizeIn(BaseModel):
    description: str
    current_code: str
    origin: str = ""


ROOT = HERE.parent


@app.get("/")
def index():
    return FileResponse(ROOT / "index.html")


# i18n: serve locale string tables (additive; presentation-layer only).
# The classifier never reads these — they are UI strings consumed by the browser.
@app.get("/locales/{name}")
def locales(name: str):
    if name not in ("en.json", "pl.json"):
        return Response(status_code=404)
    path = ROOT / "locales" / name
    if not path.exists():
        return Response(status_code=404)
    return FileResponse(path, media_type="application/json")


@app.get("/classify")
def classify_page():
    return FileResponse(HERE / "classify.html")


@app.get("/products")
def products_page():
    return FileResponse(HERE / "products.html")


@app.get("/api/health")
def health():
    info = {"ok": True, "model": es.ClaudeOracle.DEFAULT_MODEL,
            "db": es.DB_PATH.name, "key_present": bool(os.environ.get("ANTHROPIC_API_KEY"))}
    try:
        conn = sqlite3.connect(es.DB_PATH)
        n = conn.execute("SELECT COUNT(*) FROM goods_nomenclature").fetchone()[0]
        conn.close()
        info["nomenclature_lines"] = n
    except Exception as e:
        info["ok"] = False
        info["db_error"] = str(e)
    ocr_ok, ocr_detail = ocr.tesseract_status()
    info["ocr_available"] = ocr_ok
    info["ocr"] = ocr_detail
    if not info["key_present"]:
        info["ok"] = False
        info["warning"] = "ANTHROPIC_API_KEY not set — classification will fail."
    return info


def _pl_to_en(text):
    """PL product text -> English for the engine; falls back to the original."""
    try:
        import pl_question
        return pl_question.translate_to_english(text) or text
    except Exception:
        return text


@app.post("/api/classify")
def classify_endpoint(body: ClassifyIn):
    text = body.text.strip()
    if not text:
        return {"status": "error", "message": "Please describe the product."}
    # PL profile: the engine's candidate search is English-only, so feed it English
    # and localize the output back to Polish. The engine itself is unchanged.
    engine_text = _pl_to_en(text) if (body.market or "EU").upper() == "PL" else text
    return _localize_pl(es.start(engine_text, body.origin.strip(), body.hint.strip()), body.market)


@app.post("/api/answer")
def answer_endpoint(body: AnswerIn):
    choice = body.choice
    # A free-text clarification answer (PL) is translated for the engine; an
    # option id (digits/colon only) is passed through untouched.
    if (body.market or "EU").upper() == "PL" and any(c.isalpha() for c in choice):
        choice = _pl_to_en(choice)
    return _localize_pl(es.answer(body.session_id, body.sig, choice), body.market)


# WIT (Wiążąca Informacja Taryfowa) — Polish view of binding tariff rulings.
# Display-only EVIDENCE, fetched by the frontend AFTER a determination exists.
# Deliberately NOT part of /api/classify or /api/answer: WIT never enters the
# GRI control flow or the oracle's option set.
@app.get("/api/wit")
def wit_rulings(code: str = ""):
    import wit_pl  # local import keeps WIT fully out of the engine module graph
    return wit_pl.get_wit_rulings(code)


# PL national measures (VAT, excise, national non-tariff) + Polish description,
# read from the local ISZTAR cache. Display-only; fetched after a determination.
@app.get("/api/pl-measures")
def pl_measures(code: str = "", date: str = ""):
    import isztar_pl
    from datetime import date as _date
    return isztar_pl.get_pl_national_measures(code, date or _date.today().isoformat())


# Deterministic landed-cost calc; market=PL folds in Polish VAT/excise from the
# local store. Not part of the GRI flow.
@app.get("/api/landed-cost")
def landed_cost(customs_value: float = 0.0, duty_rate: str = "0%",
                code: str = "", date: str = "", market: str = "EU"):
    import landed_cost_pl
    from datetime import date as _date
    return landed_cost_pl.compute_landed_cost(
        customs_value, duty_rate, code or None,
        date or _date.today().isoformat(), market)


@app.post("/api/products/save")
def products_save(body: SaveIn):
    return pdb.save(body.result, body.note)


@app.get("/api/products")
def products_list(search: str = "", origin: str = "", chapter: str = "",
                  confidence: str = "", has_defense: str = ""):
    return {"products": pdb.list_products(search, origin, chapter,
                                          confidence, has_defense),
            "stats": pdb.stats()}


@app.post("/api/products/delete")
def products_delete(body: DeleteIn):
    return pdb.delete(body.id)


@app.get("/api/products/export")
def products_export():
    csv_text = pdb.export_csv()
    return Response(content=csv_text, media_type="text/csv",
                    headers={"Content-Disposition":
                             "attachment; filename=aimport_classifications.csv"})

@app.get("/invoice")
def invoice_page():
    return FileResponse(HERE / "invoice.html")


@app.post("/api/invoice/analyze")
async def invoice_analyze(request: Request, file: UploadFile = File(...),
                          origin: str = "", market: str = "EU"):
    # PL profile reads Polish invoices with the pol model (pol+eng for mixed
    # PL/EN); EU stays on "eng" so existing English OCR behaviour is unchanged.
    ocr_lang = "pol+eng" if (market or "").upper() == "PL" else "eng"
    # Save the uploaded PDF to a temp file, analyse, then delete it.
    if not file.filename.lower().endswith(".pdf"):
        return {"error": "Please upload a PDF invoice."}
    suffix = ".pdf"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        content = await file.read()
        tmp.write(content)
        tmp.close()
        result = inv.analyze_invoice(tmp.name, origin_override=origin, lang=ocr_lang)
        # Create the client survey if any line froze, and attach its public URL.
        return _attach_survey(result, request)
    except Exception as e:
        return {"error": f"Could not process the invoice: {str(e)[:200]}"}
    finally:
        try:
            _os.unlink(tmp.name)
        except Exception:
            pass
@app.get("/optimize")
def optimize_page():
    return FileResponse(HERE / "optimize.html")


@app.post("/api/optimize")
def optimize_endpoint(body: OptimizeIn):
    description = body.description.strip()
    current_code = body.current_code.strip()
    if not description:
        return {"error": "Product description is required."}
    if not current_code:
        return {"error": "Current HS/TARIC code is required."}
    result = opt_s.analyze(description, current_code, body.origin.strip())
    # Phase 6: attach a DETERMINISTIC defensibility score + duty delta to each
    # alternative (computed in Python from GRI path strength, WIT support, and
    # measure clarity — the LLM does not assign these). Additive; opt_s.analyze
    # is unchanged, and alternatives are already engine-validated (no GRI-rejected
    # codes reach here).
    try:
        import defensibility
        import landed_cost_pl
        orig = (result.get("original") or {}).get("duty") or {}
        orig_rate = landed_cost_pl.parse_percent(orig.get("rate"))
        for alt in (result.get("alternatives") or []):
            alt.update(defensibility.score_alternative(alt))
            alt_rate = landed_cost_pl.parse_percent((alt.get("duty") or {}).get("rate"))
            if orig_rate is not None and alt_rate is not None:
                alt["duty_delta_pct"] = round((alt_rate - orig_rate) * 100, 2)  # negative = saving
    except Exception:
        pass  # scoring is additive; never break the existing optimize response
    return result


# Client-clarification survey: public tokenised survey URL + one-click broker
# email. Additive router; existing classify/products/invoice routes untouched.
import survey_api                      # noqa: E402
app.include_router(survey_api.router)


if __name__ == "__main__":
    import uvicorn
    print("AImport web app — open http://127.0.0.1:8000 in your browser")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("  WARNING: ANTHROPIC_API_KEY is not set; classification will fail.")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
