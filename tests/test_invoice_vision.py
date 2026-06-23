#!/usr/bin/env python3
"""Vision tier + validation tests (Fix 1).

- Validation unit tests run with no API key.
- The live vision extraction test runs only when ANTHROPIC_API_KEY is set; it
  reproduces the failing 'European Foods Trading GmbH' invoice (address/VAT
  block + one product line) and asserts only the product line comes back.

Run: python3 tests/test_invoice_vision.py          (validation only)
     ANTHROPIC_API_KEY=sk-... python3 tests/test_invoice_vision.py  (+ vision)
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import invoice_vision as iv


def make_invoice_pdf(path):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.platypus import Table, TableStyle
    from reportlab.lib import colors
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    y = h - 60
    # --- administrative / address block (the text that fooled the old parser) ---
    for line in [
        "European Foods Trading GmbH",
        "Hauptstrasse 42, 60311 Frankfurt, Hesse, Germany",
        "VAT ID (USt-IdNr): DE123456789",
        "EORI: DE987654321000000",
        "Bank: Deutsche Bank   IBAN: DE89370400440532013000   BIC: DEUTDEFF",
        "Invoice No: EFT-2024-0817      Payment Terms: 30 days",
        "Consignee: Polska Spozywcza Sp. z o.o., Warsaw, Poland",
        "Port of Loading: Hamburg     Country of Origin: Germany",
    ]:
        c.setFont("Helvetica", 10); c.drawString(50, y, line); y -= 16
    y -= 14
    # --- the goods table ---
    data = [
        ["No", "Description", "HS Code", "Quantity", "Unit", "Origin"],
        ["1", "Frozen Sweet Corn Kernels (Golden) with LDPE Food Grade Blue liner",
         "0710 4000", "970", "Bags", "Germany"],
    ]
    t = Table(data, colWidths=[28, 230, 70, 55, 45, 55])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    tw, th = t.wrapOn(c, w, h)
    t.drawOn(c, 50, y - th)
    c.showPage(); c.save()


def test_validation():
    # HS validation: VAT/EORI numbers must NOT pass as codes
    assert iv._is_valid_hs_code("0710 4000") and iv.clean_hs_code("0710 4000") == "07104000"
    assert iv.clean_hs_code("DE123456789") is None         # VAT id
    assert iv.clean_hs_code("DE987654321000000") is None   # EORI
    assert iv.clean_hs_code("not-a-code") is None
    assert iv._is_valid_hs_code("07104000")

    # description validation: administrative text rejected
    assert not iv.is_valid_description("VAT ID (USt-IdNr): DE123456789")
    assert not iv.is_valid_description("Hauptstrasse 42, 60311 Frankfurt, Hesse, Germany")
    assert not iv.is_valid_description("Bank: Deutsche Bank IBAN: DE89...")
    assert not iv.is_valid_description("Country of Origin: Germany")
    assert iv.is_valid_description("Frozen Sweet Corn Kernels (Golden)")

    # clean a mixed list: only the real product survives, VAT code nulled
    mixed = [
        {"row": 1, "description": "VAT ID (USt-IdNr): DE123456789", "code": "DE123456789", "qty": ""},
        {"row": 2, "description": "Hauptstrasse 42, 60311 Frankfurt", "code": "", "qty": ""},
        {"row": 3, "description": "Frozen Sweet Corn Kernels (Golden)", "code": "0710 4000", "qty": "970"},
    ]
    cleaned = iv.clean_invoice_items(mixed)
    assert len(cleaned) == 1, cleaned
    assert cleaned[0]["description"].startswith("Frozen Sweet Corn")
    assert cleaned[0]["code"] == "07104000"
    print("validation: PASS")


def test_vision_live():
    if not os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") == "dummy-test-key":
        print("vision live: SKIPPED (no real ANTHROPIC_API_KEY)")
        return
    if not iv.vision_available():
        print("vision live: SKIPPED (PyMuPDF or key missing)")
        return
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        pdf = f.name
    make_invoice_pdf(pdf)
    items, meta = iv.extract_line_items_vision(pdf)
    os.unlink(pdf)
    print("vision returned", len(items), "item(s):")
    for it in items:
        print("  ", it["row"], repr(it["description"])[:60], "| code", it["code"], "| qty", it["qty"])
    assert len(items) == 1, f"expected 1 product line, got {len(items)}"
    it = items[0]
    assert "corn" in it["description"].lower()
    assert it["code"] == "07104000"
    # the bug condition: no address/VAT text, no VAT id as code
    blob = (it["description"] + " " + it["code"]).lower()
    assert "vat" not in blob and "frankfurt" not in blob and "de123" not in blob
    print("vision live: PASS")


if __name__ == "__main__":
    test_validation()
    test_vision_live()
    print("ALL VISION TESTS DONE")
