#!/usr/bin/env python3
"""
invoice_vision.py — Tier-2 vision extraction + validation for invoice parsing.

Part of the three-tier cascade in invoice_session.extract_with_fallback:
    Tier 1: pdfplumber coordinate/table extraction (clean digital PDFs)
    Tier 2: Claude vision extraction (THIS module — all other layout types,
            including mixed-content invoices where the address/VAT block sits
            in the text stream above the goods table)
    Tier 3: Tesseract OCR (image-only scanned PDFs — invoice_ocr.py)

Why vision: a naive text reader treats "Frankfurt … VAT ID: DE123456789" as a
product line. Vision reads the document spatially and is told, explicitly, to
return ONLY the goods table — never addresses, VAT/EORI/IBAN, or bank details.

The Claude call uses the SAME model as the classification oracle
(ClaudeOracle.DEFAULT_MODEL) over the same raw-HTTP path the engine uses, so no
new SDK dependency is introduced. Items are returned in the dict shape the rest
of the invoice pipeline already consumes: {row, description, code, qty, ...}.
"""
import base64
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

log = logging.getLogger("uvicorn.error")


def _model() -> str:
    try:
        from oracles import ClaudeOracle
        return ClaudeOracle.DEFAULT_MODEL
    except Exception:
        return "claude-sonnet-4-6"


# ── Validation ────────────────────────────────────────────────────────────────
# Strings that look like a product description but are administrative noise.
FALSE_DESCRIPTION_PATTERNS = [
    r"VAT\s?(ID)?", r"USt-?IdNr", r"\bIBAN\b", r"\bBIC\b", r"\bSWIFT\b",
    r"\bEORI\b", r"Tax\s*(No|Number|ID)", r"Reg(\.|istration)?\s*No",
    r"Frankfurt|Hamburg|Berlin|Munich|Warsaw|Warszawa|Rotterdam|Gdansk|Gdańsk",
    r"^\s*\d{5}\s+\w+",                 # postcode + city
    r"\bBank\b\s*:?", r"Account\s*(No|Number)", r"Sort\s*Code",
    r"Invoice\s*(No|Number|Date)", r"Payment\s*Terms", r"Due\s*Date",
    r"Port\s+of\b", r"Place\s+of\b", r"Country\s+of\s+Origin\b",
    r"Buyer\s*/|Seller\s*/|Consignee|Shipper|Notify\s*Party",
    r"^\s*(Total|Subtotal|Grand\s*Total)\b", r"GmbH|Sp\.?\s?z\s?o\.?o|B\.?V\.?|Ltd\b|S\.?A\.?\b",
]


def _is_valid_hs_code(value) -> bool:
    """True only for an HS/CN/TARIC code: 4–10 DIGITS once spaces/dots are
    removed. Crucially, any other character (e.g. the 'DE' of DE123456789)
    makes it invalid, so VAT/EORI numbers cannot pass as codes."""
    if not value:
        return False
    cleaned = re.sub(r"[\s.]", "", str(value))
    return bool(re.fullmatch(r"\d{4,10}", cleaned))


def clean_hs_code(value):
    """Return the digit-only code if it is a valid HS/CN code, else None."""
    if value is None:
        return None
    cleaned = re.sub(r"[\s.]", "", str(value))
    return cleaned if _is_valid_hs_code(cleaned) else None


def is_valid_description(value) -> bool:
    """False for administrative/address text or anything too short to be goods."""
    if not value or len(str(value).strip()) < 3:
        return False
    for pat in FALSE_DESCRIPTION_PATTERNS:
        if re.search(pat, str(value), re.IGNORECASE):
            return False
    return True


def _parse_float(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^\d.,]", "", str(value)).replace(",", ".")
    # collapse a trailing thousands/decimal ambiguity conservatively
    if cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "", cleaned.count(".") - 1)
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def clean_invoice_items(items):
    """Scrub a list of pipeline invoice-item dicts ({row, description, code, ...}):
    drop administrative/address rows, null out invalid codes (VAT/EORI/IBAN),
    and re-number. Used to harden BOTH the pdfplumber and vision tiers."""
    cleaned = []
    for it in items or []:
        desc = (it.get("description") or "").strip()
        code = clean_hs_code(it.get("code"))
        desc_ok = is_valid_description(desc)
        if not desc_ok:
            desc = ""
        # a row with neither a usable description nor a valid code is noise
        if not desc and not code:
            continue
        new = dict(it)
        new["description"] = desc
        new["code"] = code or ""
        new["row"] = len(cleaned) + 1
        cleaned.append(new)
    return cleaned


# ── Rasterisation ─────────────────────────────────────────────────────────────

def _rasterise(pdf_path, dpi: int = 150):
    """Render every page to a base64 JPEG. Returns [] if PyMuPDF is unavailable."""
    try:
        import fitz  # PyMuPDF
    except Exception as e:
        log.warning("Vision tier unavailable — PyMuPDF (fitz) not installed: %s", e)
        return []
    images = []
    doc = fitz.open(pdf_path)
    try:
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        for page in doc:
            pix = page.get_pixmap(matrix=mat)
            images.append(base64.standard_b64encode(pix.tobytes("jpeg")).decode())
    finally:
        doc.close()
    return images


VISION_PROMPT = """You are an expert at reading commercial invoices and customs documents.

Examine this invoice page image carefully. Extract ONLY the product line items from the goods/items table.

Return a JSON array. Each element must be an object with these exact keys:
{
  "line_number": <integer, 1-based>,
  "description": <string product/goods description, or null>,
  "hs_code": <string HS/CN/TARIC code, digits only, no spaces, or null>,
  "quantity": <number or null>,
  "unit": <string unit of measure, or null>,
  "country_of_origin": <string or null>
}

CRITICAL RULES:
- Include ONLY rows from the product/goods/items table.
- DO NOT include company names, addresses, VAT IDs, IBAN/BIC, EORI numbers, bank details, invoice header text, payment terms, port information, or totals.
- A valid HS/CN code is 4-10 digits. Never return a VAT registration number, company ID, or any other number as hs_code.
- If a field is missing or unclear, use null.
- Return ONLY the JSON array. No prose, no markdown fences.

Example for a one-line invoice:
[{"line_number": 1, "description": "Frozen Sweet Corn Kernels, Golden variety, LDPE Food Grade packaging", "hs_code": "07104000", "quantity": 970, "unit": "Bags", "country_of_origin": "Germany"}]
"""


def _vision_call(img_b64: str, timeout: int = 60):
    """One messages call with an image + the extraction prompt. Returns the
    parsed JSON list, or None on any error."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    body = {
        "model": _model(),
        "max_tokens": 2000,
        "temperature": 0,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64",
                 "media_type": "image/jpeg", "data": img_b64}},
                {"type": "text", "text": VISION_PROMPT},
            ],
        }],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "x-api-key": api_key,
                 "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except Exception as e:
        log.warning("Vision tier API call failed: %s", str(e)[:160])
        return None
    raw = "".join(b.get("text", "") for b in data.get("content", [])).strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else None
    except json.JSONDecodeError:
        log.warning("Vision tier returned non-JSON output.")
        return None


def vision_available() -> bool:
    """True when both PyMuPDF and an API key are present."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import fitz  # noqa: F401
        return True
    except Exception:
        return False


def extract_line_items_vision(pdf_path, dpi: int = 150):
    """Tier-2 vision extraction. Returns (items, meta) in the pipeline dict shape.
    items: [{row, description, code, qty, unit, country_of_origin, extraction_tier}]
    Returns ([], {...}) when vision is unavailable or extraction fails."""
    meta = {"origin": "", "invoice_no": "", "extraction_tier": "vision"}
    images = _rasterise(pdf_path, dpi=dpi)
    if not images:
        return [], meta

    items = []
    for img_b64 in images:
        page = _vision_call(img_b64)
        if not page:
            continue
        for obj in page:
            if not isinstance(obj, dict):
                continue
            desc = obj.get("description")
            code = clean_hs_code(obj.get("hs_code"))
            qty = _parse_float(obj.get("quantity"))
            unit = obj.get("unit")
            coo = (obj.get("country_of_origin") or "")
            items.append({
                "row": len(items) + 1,
                "description": (desc or "").strip(),
                "code": code or "",
                "qty": "" if qty is None else (f"{qty:g} {unit}".strip()
                                               if unit else f"{qty:g}"),
                "unit": unit or "",
                "country_of_origin": coo.strip(),
                "extraction_tier": "vision",
            })

    items = clean_invoice_items(items)
    # derive an invoice-level origin from the first line that carries one
    for it in items:
        if it.get("country_of_origin"):
            meta["origin"] = _country_to_iso(it["country_of_origin"]) or ""
            if meta["origin"]:
                break
    if items:
        log.info("Tier 2 (vision): extracted %d line item(s) across %d page(s)",
                 len(items), len(images))
    return items, meta


_COUNTRY_ISO = {
    "germany": "DE", "deutschland": "DE", "poland": "PL", "polska": "PL",
    "netherlands": "NL", "france": "FR", "italy": "IT", "spain": "ES",
    "china": "CN", "united kingdom": "GB", "uk": "GB", "czechia": "CZ",
    "czech republic": "CZ", "belgium": "BE", "austria": "AT",
}


def _country_to_iso(name):
    if not name:
        return ""
    n = str(name).strip()
    if re.fullmatch(r"[A-Za-z]{2}", n):
        return n.upper()
    return _COUNTRY_ISO.get(n.lower(), "")
