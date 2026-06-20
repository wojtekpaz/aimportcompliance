#!/usr/bin/env python3
"""
make_test_invoices.py — synthetic invoice fixtures for the OCR ingestion work.

Real test PDFs (commercial.pdf, a photographed CBP-7501) were not available in
this environment, so we generate stand-ins that exercise each extraction tier:

  fixtures/digital_invoice.pdf   text-layer PDF  -> Tier 1 (pdfplumber) must use it
  fixtures/image_invoice.pdf     flattened image -> Tier 2 (OCR) two line items
  fixtures/form_scan.pdf         single-line CBP-7501-ish image -> Tier 2 parser
  fixtures/blank.pdf             blank image     -> Tier 3 honest-failure

The image fixtures contain NO text layer (the page is a single embedded raster),
which is exactly the failure mode the OCR fallback exists to handle.
"""
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
FIX.mkdir(exist_ok=True)

ARIAL = "/System/Library/Fonts/Supplemental/Arial.ttf"
ARIAL_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def _font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


# ---- 1. Digital text-layer invoice (Tier 1 target) ------------------------
# Codes are bare 10-digit tokens so the existing ^\d{10}$ anchor matches.
def make_digital():
    path = FIX / "digital_invoice.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    y = h - 70
    c.setFont("Helvetica-Bold", 16)
    c.drawString(60, y, "COMMERCIAL INVOICE")
    c.setFont("Helvetica", 10)
    c.drawString(60, y - 22, "Invoice No: INV-2026-0042")
    c.drawString(60, y - 36, "Country of Origin: China (CN)")

    # table header
    ty = y - 80
    c.setFont("Helvetica-Bold", 10)
    c.drawString(60, ty, "Description")
    c.drawString(330, ty, "HS Code")
    c.drawString(470, ty, "Qty")
    c.line(60, ty - 6, 540, ty - 6)

    rows = [
        ("Portable laptop computers, 14-inch", "8471300000", "20 pcs"),
        ("Swivel office chairs, upholstered", "9401300000", "50 pcs"),
    ]
    c.setFont("Helvetica", 10)
    ry = ty - 26
    for desc, code, qty in rows:
        c.drawString(60, ry, desc)
        c.drawString(330, ry, code)
        c.drawString(470, ry, qty)
        ry -= 28
    c.showPage()
    c.save()
    return path


# ---- image helpers --------------------------------------------------------
def _img_to_pdf(img, path):
    img.convert("RGB").save(str(path), "PDF", resolution=150.0)


# ---- 2. Image-only invoice (Tier 2 target, two line items) ----------------
# Codes are dotted (8471.30.00) to exercise the tolerant HS-code regex.
def make_image_invoice():
    path = FIX / "image_invoice.pdf"
    W, H = 1654, 2339  # ~A4 at 200 dpi
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    big = _font(ARIAL_BOLD, 46)
    hdr = _font(ARIAL_BOLD, 30)
    body = _font(ARIAL, 30)

    d.text((120, 110), "COMMERCIAL INVOICE", font=big, fill="black")
    d.text((120, 200), "Invoice No: INV-2026-0042", font=body, fill="black")
    d.text((120, 250), "Country of Origin: China (CN)", font=body, fill="black")

    cols = [(120, "DESCRIPTION"), (900, "HS CODE"), (1300, "VALUE")]
    ty = 380
    for x, label in cols:
        d.text((x, ty), label, font=hdr, fill="black")
    d.line((120, ty + 48, 1530, ty + 48), fill="black", width=2)

    rows = [
        ("Portable laptop computers 14in", "8471.30.00", "$13,120.00"),
        ("Swivel office chairs upholstered", "9401.30.00", "$4,250.00"),
    ]
    ry = ty + 90
    for desc, code, val in rows:
        d.text((120, ry), desc, font=body, fill="black")
        d.text((900, ry), code, font=body, fill="black")
        d.text((1300, ry), val, font=body, fill="black")
        ry += 70
    _img_to_pdf(img, path)
    return path


# ---- 3. CBP-7501-ish single line (Tier 2 parser, looser alignment) --------
def make_form_scan():
    path = FIX / "form_scan.pdf"
    W, H = 1654, 2339
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    title = _font(ARIAL_BOLD, 34)
    body = _font(ARIAL, 30)

    d.text((120, 120), "ENTRY SUMMARY (CBP FORM 7501)", font=title, fill="black")
    d.text((120, 230), "Line  Description of Merchandise        HTSUS No.", font=body, fill="black")
    d.line((120, 285, 1500, 285), fill="black", width=2)
    # description + code on the same visual row, loosely aligned
    d.text((120, 330), "001   DRWRSLIDES BASE METAL MOUNTINGS", font=body, fill="black")
    d.text((1180, 330), "8302.42.3015", font=body, fill="black")
    d.text((120, 430), "Value: $ 8,900.00", font=body, fill="black")
    _img_to_pdf(img, path)
    return path


# ---- 4. Blank image PDF (Tier 3 honest failure) ---------------------------
def make_blank():
    path = FIX / "blank.pdf"
    img = Image.new("RGB", (1654, 2339), "white")
    _img_to_pdf(img, path)
    return path


# ---- 5. Heavily-blurred scan (Tier 2.5 confidence gating) -----------------
# Tesseract confidence is bimodal: it reads cleanly until it breaks down. At
# this blur the code garbles to an invalid token and the value misreads
# (13,120 -> 13.120), both at low confidence — the exact corruption Tier 2.5
# must FLAG rather than trust.
def make_degraded():
    path = FIX / "degraded.pdf"
    W, H = 1500, 420
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    body = _font(ARIAL, 30)
    d.text((40, 40), "DESCRIPTION", font=body, fill="black")
    d.text((760, 40), "HS CODE", font=body, fill="black")
    d.text((1120, 40), "VALUE", font=body, fill="black")
    # description + code stay sharp (read at high confidence)...
    d.text((40, 160), "Aluminium window frames, mill finish", font=body, fill="black")
    d.text((760, 160), "7610.10.00", font=body, fill="black")
    # ...the value cell is the smudged part of the scan (a coffee-ring / fold).
    d.text((1120, 160), "$13,120.00", font=body, fill="black")
    box = (1100, 130, W, 210)
    region = img.crop(box).filter(ImageFilter.GaussianBlur(2.6))
    img.paste(region, box)
    _img_to_pdf(img, path)
    return path


if __name__ == "__main__":
    for fn in (make_digital, make_image_invoice, make_form_scan, make_blank,
               make_degraded):
        p = fn()
        print(f"wrote {p}  ({os.path.getsize(p)} bytes)")
