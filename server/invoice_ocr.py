#!/usr/bin/env python3
"""
invoice_ocr.py — Tier 2 / 2.5 OCR fallback for image-only invoices.

WHY THIS EXISTS:
  pdfplumber's coordinate extraction (Tier 1, in invoice_session.py) returns
  NOTHING on scans, phone photos and flat-image PDF exports — the silent
  "0 line items" failure. This module recovers those documents with Tesseract.

GUARANTEES:
  * No cloud OCR. Tesseract runs locally — invoice images never leave the host
    (commercial-data / GDPR surface). Do not change this.
  * Returns line items in the SAME shape as Tier 1
    ({"row","description","code","qty"}) plus extra, non-breaking keys
    ("value", "low_confidence", "code_uncertain") so downstream code is unchanged.
  * Per-field OCR confidence is carried through. A confidently-wrong OCR read is
    the same failure mode as an LLM hallucination, so a shaky code/value is
    FLAGGED, never silently trusted (Tier 2.5).

TESTED RECIPE (do not improvise — these were measured, not guessed):
  * grayscale at 300 DPI is the sweet spot; raw colour nearly as good.
  * DO NOT binarize/threshold — it lost the HS code and dropped confidence.
  * --psm 6 (uniform block) recovered the value field where --psm 4 did not.
"""
import re
import logging

import PIL.Image
PIL.Image.MAX_IMAGE_PIXELS = None          # avoid the DecompressionBomb guard
from PIL import ImageOps

log = logging.getLogger("invoice_ocr")

try:
    from pdf2image import convert_from_path
    import pytesseract
    from pytesseract import Output
    _OCR_IMPORT_OK = True
except Exception as _e:                     # pragma: no cover - import guard
    _OCR_IMPORT_OK = False
    _IMPORT_ERR = _e

# ---- tuning ---------------------------------------------------------------
OCR_DPI = 300            # 300 best; 250 acceptable floor (see handoff §4)
LOWCONF_DPI = 300        # retry DPI is the same; we upscale instead (see below)
CONF_THRESHOLD = 70      # tokens of a code/value below this are low_confidence
OCR_MIN_CONF = 50        # below this AND no code parsed -> treat read as failed
MAX_PAGES = 5            # demo cap; OCR is ~1-2s/page
PSM = "--psm 6"

# A row carrying either of these tokens is a strong line-item signal.
HS_RE = re.compile(r"\d{4}[.\s]?\d{2}[.\s]?\d{2,4}")
MONEY_RE = re.compile(r"\$?\d[\d,]*\.\d{2}")
_HAS_LETTER = re.compile(r"[A-Za-z]")


def tesseract_available():
    """True iff the OCR stack imports AND the tesseract binary is on PATH.
    OCR silently no-ops if the binary is missing, so callers must check."""
    if not _OCR_IMPORT_OK:
        return False
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def tesseract_status():
    """Human-readable status for the startup check / health endpoint."""
    if not _OCR_IMPORT_OK:
        return False, f"pytesseract/pdf2image not importable: {_IMPORT_ERR}"
    try:
        v = pytesseract.get_tesseract_version()
        return True, f"tesseract {v}"
    except Exception as e:
        return False, f"tesseract binary not found on PATH: {e}"


# ---- OCR per page ---------------------------------------------------------

def _prep(im):
    return ImageOps.grayscale(im)           # grayscale beat raw + beat binarize


def _ocr_lines(im):
    """Run word-level OCR and regroup words into visual rows using the
    (block,par,line) keys from image_to_data — the y-grouping the coordinate
    approach already trusts, just sourced from OCR. Returns a list of rows;
    each row is a list of {text, conf, left, top} word dicts in reading order."""
    data = pytesseract.image_to_data(_prep(im), output_type=Output.DICT,
                                     config=PSM)
    n = len(data["text"])
    rows = {}
    for i in range(n):
        txt = (data["text"][i] or "").strip()
        if not txt:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        rows.setdefault(key, []).append({
            "text": txt, "conf": conf,
            "left": data["left"][i], "top": data["top"][i],
        })
    out = []
    for key in sorted(rows):
        words = sorted(rows[key], key=lambda w: w["left"])
        out.append(words)
    return out


def _mean_conf(rows):
    vals = [w["conf"] for row in rows for w in row if w["conf"] >= 0]
    return round(sum(vals) / len(vals), 1) if vals else 0.0


# ---- row -> line item -----------------------------------------------------

def _find_token(words, regex, skip=None):
    """Return (word_dict, match_text) for the first word matching regex, else
    (None, None). Codes/values came back as single OCR tokens in testing.
    `skip` excludes an already-claimed word so a money search doesn't latch onto
    the HS code's own prefix (MONEY_RE matches '8471.30' inside '8471.30.00')."""
    for w in words:
        if w is skip:
            continue
        m = regex.search(w["text"])
        if m:
            return w, m.group(0)
    return None, None


def _description_span(words, exclude_texts):
    """Longest run of letter-bearing words on the row, excluding the matched
    code/value tokens. Mirrors 'longest non-numeric text span on/near the row'."""
    spans, cur = [], []
    for w in words:
        t = w["text"]
        if t in exclude_texts or not _HAS_LETTER.search(t):
            if cur:
                spans.append(cur); cur = []
            continue
        cur.append(w)
    if cur:
        spans.append(cur)
    if not spans:
        return ""
    best = max(spans, key=lambda s: sum(len(w["text"]) for w in s))
    return " ".join(w["text"] for w in best).strip()


def _build_item(row_words, prev_words):
    """Turn one OCR row into a line item dict (Tier-1 shape + OCR extras).
    Returns None if the row carries neither an HS code nor a money token."""
    code_w, code_txt = _find_token(row_words, HS_RE)
    money_w, money_txt = _find_token(row_words, MONEY_RE, skip=code_w)
    if not code_w and not money_w:
        return None

    exclude = set()
    low_conf = []
    code = ""
    if code_w:
        exclude.add(code_w["text"])
        code = re.sub(r"\D", "", code_txt)          # digits only, Tier-1 style
        if not (6 <= len(code) <= 10):
            code = ""                               # not a plausible code length
        elif code_w["conf"] < CONF_THRESHOLD:
            low_conf.append("code")
    value = ""
    if money_w:
        exclude.add(money_w["text"])
        value = money_txt
        if money_w["conf"] < CONF_THRESHOLD:
            low_conf.append("value")

    desc = _description_span(row_words, exclude)
    # If the row was code/value only (description wrapped to the line above),
    # borrow the previous row's text — y-adjacent, same line item.
    if len(desc) < 3 and prev_words:
        desc = _description_span(prev_words, set())

    return {
        "description": desc,
        "code": code,
        "value": value,
        "qty": value,                # Tier-1 'qty' slot carries the figure
        "low_confidence": low_conf,  # subset of {"code","value"}
        "code_uncertain": ("code" in low_conf) or (code_w is not None and not code),
        "code_conf": round(code_w["conf"], 1) if code_w else None,
        "value_conf": round(money_w["conf"], 1) if money_w else None,
    }


def _parse_rows(rows):
    """HS-code rows are the primary line-item signal. Only if none exist do we
    fall back to money-token rows (covers code-less invoices) — this stops a
    stray 'Value: $...' line from becoming a spurious item when codes are present."""
    hs_items, money_items = [], []
    for i, row in enumerate(rows):
        prev = rows[i - 1] if i > 0 else None
        if _find_token(row, HS_RE)[0]:
            it = _build_item(row, prev)
            if it:
                hs_items.append(it)
        elif _find_token(row, MONEY_RE)[0]:
            it = _build_item(row, prev)
            if it:
                money_items.append(it)
    items = hs_items if hs_items else money_items
    for n, it in enumerate(items, 1):
        it["row"] = n
    return items


# ---- public entry ---------------------------------------------------------

def extract_line_items_ocr(pdf_path, page_cap=MAX_PAGES):
    """OCR a PDF into line items. Returns (items, meta) where meta carries
    ocr_mean_conf so the caller can decide whether the read is trustworthy."""
    meta = {"origin": "", "invoice_no": "", "ocr_mean_conf": 0.0}
    if not tesseract_available():
        log.warning("OCR requested but tesseract is unavailable; returning empty.")
        return [], meta

    pages = convert_from_path(pdf_path, dpi=OCR_DPI)
    items, all_rows = [], []
    confs = []
    for page in pages[:page_cap]:
        rows = _ocr_lines(page)
        # If a page is very low-confidence, retry upscaled 1.5x (second attempt
        # only — upscaling adds words but no field benefit on clean pages).
        if rows and _mean_conf(rows) < 55:
            big = page.resize((int(page.width * 1.5), int(page.height * 1.5)))
            rows_big = _ocr_lines(big)
            if _mean_conf(rows_big) > _mean_conf(rows):
                rows = rows_big
        confs.append(_mean_conf(rows))
        all_rows.extend(rows)
        items.extend(_parse_rows(rows))

    # renumber across pages
    for n, it in enumerate(items, 1):
        it["row"] = n

    # page-level origin / invoice no from the full OCR text (same regexes as Tier 1)
    full_text = "\n".join(" ".join(w["text"] for w in row) for row in all_rows)
    m = re.search(r"origin[:\s]+(?:[A-Za-z ]+\(([A-Z]{2})\)|([A-Z]{2}))",
                  full_text, re.I)
    if m:
        meta["origin"] = (m.group(1) or m.group(2) or "").upper()
    m = re.search(r"invoice\s*(?:no|number)[:\s]+([A-Za-z0-9\-/]+)",
                  full_text, re.I)
    if m:
        meta["invoice_no"] = m.group(1)
    meta["ocr_mean_conf"] = round(sum(confs) / len(confs), 1) if confs else 0.0
    return items, meta
