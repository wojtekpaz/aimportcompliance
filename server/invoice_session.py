#!/usr/bin/env python3
"""
invoice_session.py — Invoice scanning for AImport Compliance.

WHAT IT DOES:
  1. Extracts line items from an uploaded clean digital PDF using coordinate-
     based word extraction (pdfplumber). No AI reads the PDF — nothing can
     be invented. Extraction is faithful to the document.
  2. Runs each line's description through the EXISTING engine (engine_session.start).
  3. Compares the engine's code against the invoice's declared code and flags
     risk: wrong code, vague description, anti-dumping, missing code.

Architecture guarantee: the classification engine and its anti-hallucination
guard are NOT modified. This module only calls the engine and compares results.
"""
import re
import sys
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import engine_session as es   # noqa: E402
import invoice_ocr as ocr     # noqa: E402  (Tier 2 / 2.5 OCR fallback)

import sqlite3
DB_PATH = es.DB_PATH

# Tier-1 confidence floor: a digital invoice yields plenty of words. Below this
# the page is almost certainly an image-only scan -> hand off to OCR (Tier 2).
TIER1_MIN_WORDS = 15

# ---- deterministic PDF extraction -----------------------------------------

def _norm_code(s):
    digits = re.sub(r"\D", "", s or "")
    return digits if 6 <= len(digits) <= 10 else ""

# Words that appear in column headers and may bleed into the first product row
_HEADER_WORDS = re.compile(
    r"\b(description|product\s*details?|hs\s*code|quantity|total|"
    r"unit\s*price|amount|no\.?)\b", re.I)


def extract_line_items(pdf_path):
    """Coordinate-based extraction: finds HS codes by position, then gathers
    description text from the left column in the same y-band. Works for PDFs
    where descriptions wrap across multiple lines in the same cell."""
    items = []
    meta = {"origin": "", "invoice_no": ""}

    with pdfplumber.open(pdf_path) as pdf:
        full_text = "".join((p.extract_text() or "") for p in pdf.pages)

    m = re.search(r"origin[:\s]+(?:[A-Za-z ]+\(([A-Z]{2})\)|([A-Z]{2}))",
                  full_text, re.I)
    if m:
        meta["origin"] = (m.group(1) or m.group(2) or "").upper()
    m = re.search(r"invoice\s*(?:no|number)[:\s]+([A-Za-z0-9\-/]+)",
                  full_text, re.I)
    if m:
        meta["invoice_no"] = m.group(1)

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            words = page.extract_words(keep_blank_chars=False)
            code_re = re.compile(r"^\d{10}$")
            codes_list = sorted(
                [(w["top"], w["text"], w["x0"], w["x1"])
                 for w in words if code_re.match(w["text"])])
            if not codes_list:
                continue

            code_x0 = min(c[2] for c in codes_list) - 5
            desc_x1 = code_x0 - 2
            qty_x0  = max(c[3] for c in codes_list) + 2

            desc_words = sorted(
                [w for w in words if w["x1"] <= desc_x1],
                key=lambda w: (w["top"], w["x0"]))

            # find header row y to exclude it
            header_y = max(
                (w["top"] for w in words
                 if re.search(r"^(description|product)$", w["text"], re.I)),
                default=0)

            code_ys = [c[0] for c in codes_list]
            boundaries = []
            for i, cy in enumerate(code_ys):
                prev_b = (code_ys[i-1] + cy) / 2 if i > 0 else header_y
                next_b = (cy + code_ys[i+1]) / 2 if i+1 < len(code_ys) else cy+300
                boundaries.append((prev_b, next_b))

            for idx, (cy, code, cx0, cx1) in enumerate(codes_list):
                prev_b, next_b = boundaries[idx]
                prod_words = [w for w in desc_words if prev_b <= w["top"] < next_b]
                desc = " ".join(w["text"] for w in prod_words).strip()
                # remove column header words that bleed into first item
                desc = _HEADER_WORDS.sub("", desc).strip()

                qty_ws = sorted(
                    [w for w in words
                     if w["x0"] >= qty_x0 and abs(w["top"] - cy) <= 8],
                    key=lambda w: w["x0"])
                qty = " ".join(w["text"] for w in qty_ws[:2])

                if desc or code:
                    items.append({"row": len(items)+1, "description": desc,
                                  "code": code, "qty": qty})

    # fallback: if coordinate extraction found nothing, try pdfplumber tables
    if not items:
        _table_fallback(pdf_path, items, meta)

    return items, meta


def _table_fallback(pdf_path, items, meta):
    """Last-resort: try pdfplumber table extraction."""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in (page.extract_tables() or []):
                if not table or len(table) < 2:
                    continue
                header = [re.sub(r"\s+", " ", (c or "")).strip().lower()
                          for c in table[0]]
                desc_i = next((i for i, h in enumerate(header)
                               if "desc" in h or "product" in h), None)
                code_i = next((i for i, h in enumerate(header)
                               if "hs" in h or "code" in h), None)
                qty_i  = next((i for i, h in enumerate(header)
                               if "qty" in h or "quantity" in h), None)
                if desc_i is None or code_i is None:
                    continue
                for row in table[1:]:
                    cells = [re.sub(r"\s+", " ", (c or "")).strip() for c in row]
                    desc = cells[desc_i] if desc_i < len(cells) else ""
                    code = _norm_code(cells[code_i]) if code_i < len(cells) else ""
                    qty  = cells[qty_i] if (qty_i and qty_i < len(cells)) else ""
                    if desc or code:
                        items.append({"row": len(items)+1, "description": desc,
                                      "code": code, "qty": qty})
                if items:
                    return


# ---- tiered extraction with an honest floor -------------------------------

def _tier1_word_count(pdf_path):
    """The Tier-1 confidence signal: how many words the text layer yields.
    Image-only PDFs return ~0 here, which is what triggers OCR."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            return sum(len(p.extract_words()) for p in pdf.pages)
    except Exception:
        return 0


def extract_with_fallback(pdf_path, lang="eng"):
    """Three-tier strategy. Returns (items, meta, status) where status is one of
    'ok' (text layer), 'ocr_used' (recovered by OCR), or 'unreadable'.

    Tier 1 (pdfplumber) is unchanged and is accepted whenever it yields a real
    text layer AND at least one parsed line item — so digital invoices behave
    exactly as before. Otherwise we fall through to OCR. The empty-vs-unreadable
    distinction is the whole point of the honest floor: a readable PDF with no
    line-item table is 'ok' with 0 items; an image we could not read is
    'unreadable'."""
    items, meta = extract_line_items(pdf_path)
    wc = _tier1_word_count(pdf_path)

    # Tier 1 — accept today's happy path untouched.
    if wc >= TIER1_MIN_WORDS and items:
        return items, meta, "ok"

    # Tier 2 — OCR (primary path for real scans, not just a fallback).
    ocr_ok = ocr.tesseract_available()
    if ocr_ok:
        oitems, ometa = ocr.extract_line_items_ocr(pdf_path, lang=lang)
    else:
        oitems, ometa = [], {"origin": "", "invoice_no": "", "ocr_mean_conf": 0.0}

    # Accept OCR output only if it's actually usable: at least one parsed code,
    # or a mean confidence above the floor. A pile of low-confidence money-ish
    # rows with gibberish descriptions is a failed read, not line items —
    # emitting it would be the confidently-wrong failure mode we're avoiding.
    if oitems:
        has_code = any(it.get("code") for it in oitems)
        if has_code or ometa.get("ocr_mean_conf", 0.0) >= ocr.OCR_MIN_CONF:
            merged = dict(ometa)
            # prefer anything Tier 1 already parsed from the (sparse) text layer
            merged["origin"] = meta.get("origin") or ometa.get("origin", "")
            merged["invoice_no"] = meta.get("invoice_no") or ometa.get("invoice_no", "")
            return oitems, merged, "ocr_used"

    # Tier 3 — honest failure. Keep any Tier-1 items even if sparse...
    if items:
        return items, meta, "ok"
    # ...a readable text layer with no parseable table is a genuine empty invoice...
    if wc >= TIER1_MIN_WORDS:
        return [], meta, "ok"
    # ...otherwise we simply could not read the file.
    return [], (ometa if ocr_ok else meta), "unreadable"


# ---- code validity check --------------------------------------------------

def _code_is_valid(code):
    try:
        conn = sqlite3.connect(DB_PATH)
        r = conn.execute(
            "SELECT 1 FROM goods_nomenclature WHERE item_id=? LIMIT 1",
            (code.ljust(10, "0"),)).fetchone()
        conn.close()
        return bool(r)
    except Exception:
        return True


# ---- per-line analysis ----------------------------------------------------

def analyze_item(item, origin):
    declared = (item.get("code") or "").strip()
    low = item.get("low_confidence") or []          # subset of {"code","value"}
    code_uncertain = bool(item.get("code_uncertain"))
    flags = []

    # Tier 2.5 — never silently trust a shaky OCR read. A low-confidence code is
    # treated as "declared code uncertain, re-derive from description": the engine
    # reclassifies from the description anyway, so a garbled code becomes a flag,
    # not a corruption. We do NOT raise MALFORMED/MISMATCH off an unreliable read.
    if not declared and code_uncertain:
        cc = item.get("code_conf")
        conf_txt = f" ({cc:.0f}% conf)" if isinstance(cc, (int, float)) else ""
        flags.append({"type": "LOW_CONFIDENCE_CODE", "severity": "medium",
                      "message": "Declared code couldn't be read reliably from "
                                 f"the scan{conf_txt}; re-deriving from the "
                                 "description below — please confirm."})
    elif not declared:
        flags.append({"type": "MISSING_CODE", "severity": "high",
                      "message": "No commodity code declared on this line."})
    elif code_uncertain:
        cc = item.get("code_conf")
        conf_txt = f" ({cc:.0f}% conf)" if isinstance(cc, (int, float)) else ""
        flags.append({"type": "LOW_CONFIDENCE_CODE", "severity": "medium",
                      "message": f"Scan read the code as {declared}{conf_txt}, but "
                                 "OCR confidence is low; treating it as uncertain "
                                 "and re-deriving from the description."})
    elif not _code_is_valid(declared):
        # A code that doesn't exist in the tariff DB is almost certainly an OCR
        # error (or a genuine bad declaration) — flag it, never feed it on.
        flags.append({"type": "MALFORMED_CODE", "severity": "high",
                      "message": f"Declared code {declared} is not a valid "
                                 f"CN commodity code."})

    if "value" in low:
        val = item.get("value") or item.get("qty") or ""
        flags.append({"type": "LOW_CONFIDENCE_VALUE", "severity": "medium",
                      "message": f"Check this figure — scan unclear: {val}".strip()})

    engine_code = None
    try:
        result = es.start(item["description"], origin, "")
        status = result.get("status")
        if status == "needs_pre_classify":
            flags.append({"type": "VAGUE_DESCRIPTION", "severity": "high",
                          "message": "Description too vague for a defensible "
                                     "classification: "
                                     + (result.get("question") or "")})
        elif status == "needs_question":
            flags.append({"type": "VAGUE_DESCRIPTION", "severity": "medium",
                          "message": "Description lacks a detail needed to "
                                     "confirm the code under audit."})
        elif status == "classified":
            engine_code = result.get("code")
            if declared and not code_uncertain and engine_code \
                    and engine_code != declared.ljust(10, "0"):
                flags.append({
                    "type": "CODE_MISMATCH", "severity": "high",
                    "message": f"Declared {declared}, but the description "
                               f"classifies as {engine_code}."})
            for d in (result.get("defense") or []):
                flags.append({
                    "type": "ANTIDUMPING", "severity": "high",
                    "message": (f"Trade-defence measure may apply: "
                                f"{d.get('name','')} {d.get('rate','')}").strip()})
        elif status == "error":
            flags.append({"type": "ENGINE_ERROR", "severity": "low",
                          "message": result.get("message", "Engine error.")})
    except Exception as e:
        flags.append({"type": "ENGINE_ERROR", "severity": "low",
                      "message": f"Could not classify this line: {str(e)[:120]}"})

    if not flags:
        flags.append({"type": "OK", "severity": "low",
                      "message": "Declared code matches the engine's "
                                 "classification. No issues found."})

    sev_rank = {"high": 3, "medium": 2, "low": 1}
    worst = max(flags, key=lambda f: sev_rank[f["severity"]])
    status = "issue" if worst["severity"] in ("high", "medium") else "ok"

    return {"row": item["row"], "description": item["description"],
            "declared_code": declared, "engine_code": engine_code,
            "qty": item.get("qty", ""), "value": item.get("value", ""),
            "low_confidence": low, "flags": flags, "status": status}


UNREADABLE_MESSAGE = ("Couldn't read line items from this PDF. It may be a "
                      "low-quality scan or photo. Try a clearer copy, or add "
                      "items manually.")


def analyze_invoice(pdf_path, origin_override="", lang="eng"):
    items, meta, extraction_status = extract_with_fallback(pdf_path, lang=lang)
    origin = (origin_override or meta.get("origin") or "").upper()

    # Tier 3 — honest failure. A failed read is NOT a "0 line items" empty
    # invoice; the UI must be able to tell them apart.
    if extraction_status == "unreadable":
        return {
            "summary": {"total": 0, "issues": 0, "ok": 0, "origin": origin,
                        "invoice_no": meta.get("invoice_no", ""),
                        "extraction_status": "unreadable"},
            "items": [], "meta": meta,
            "extraction_status": "unreadable",
            "message": UNREADABLE_MESSAGE,
        }

    results = [analyze_item(it, origin) for it in items]
    summary = {
        "total": len(results),
        "issues": sum(1 for r in results if r["status"] == "issue"),
        "ok": sum(1 for r in results if r["status"] == "ok"),
        "origin": origin,
        "invoice_no": meta.get("invoice_no", ""),
        "extraction_status": extraction_status,
        "ocr_mean_conf": meta.get("ocr_mean_conf", 0.0),
    }
    return {"summary": summary, "items": results, "meta": meta,
            "extraction_status": extraction_status}
