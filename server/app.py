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
import logging                         # noqa: E402
import engine_session as es          # noqa: E402
import products_db as pdb            # noqa: E402
import invoice_session as inv           # noqa: E402
import invoice_ocr as ocr               # noqa: E402
import optimize_session as opt_s        # noqa: E402
import tempfile, os as _os              # noqa: E402
from fastapi import UploadFile, File    # noqa: E402

app = FastAPI(title="AImport", docs_url="/api/docs")
HERE = Path(__file__).resolve().parent
log = logging.getLogger("uvicorn.error")


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


class AnswerIn(BaseModel):
    session_id: str
    sig: str
    choice: str


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


@app.post("/api/classify")
def classify_endpoint(body: ClassifyIn):
    text = body.text.strip()
    if not text:
        return {"status": "error", "message": "Please describe the product."}
    return es.start(text, body.origin.strip(), body.hint.strip())


@app.post("/api/answer")
def answer_endpoint(body: AnswerIn):
    return es.answer(body.session_id, body.sig, body.choice)


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
async def invoice_analyze(file: UploadFile = File(...), origin: str = ""):
    # Save the uploaded PDF to a temp file, analyse, then delete it.
    if not file.filename.lower().endswith(".pdf"):
        return {"error": "Please upload a PDF invoice."}
    suffix = ".pdf"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        content = await file.read()
        tmp.write(content)
        tmp.close()
        result = inv.analyze_invoice(tmp.name, origin_override=origin)
        return result
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
    return opt_s.analyze(description, current_code, body.origin.strip())


if __name__ == "__main__":
    import uvicorn
    print("AImport web app — open http://127.0.0.1:8000 in your browser")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("  WARNING: ANTHROPIC_API_KEY is not set; classification will fail.")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
