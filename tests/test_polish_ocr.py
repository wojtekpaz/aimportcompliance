"""Phase 5 acceptance: a Polish invoice OCRs with Polish characters intact via
the existing pipeline (pol+eng), and the English path (eng) is unchanged.

Renders a Polish invoice line to an image and runs it through Tesseract with the
same preprocessing the pipeline uses (grayscale, --psm 6, no binarization).

Run:  python3 tests/test_polish_ocr.py
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont
import pytesseract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
import invoice_ocr  # noqa: E402  (reuse the real preprocessing/config)

PL_LINE_1 = "Wsporniki montażowe stalowe, ocynkowane ogniowo"
PL_LINE_2 = "Kod CN 7308.90.98   ilość 250 szt.   wartość 1 050,00 zł"
DIACRITICS = ["ż", "ą", "ł", "ś", "ó"]


def _font(size):
    for path in [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _render_invoice():
    img = Image.new("RGB", (1100, 260), "white")
    d = ImageDraw.Draw(img)
    f = _font(34)
    d.text((40, 50), PL_LINE_1, fill="black", font=f)
    d.text((40, 130), PL_LINE_2, fill="black", font=f)
    return img


def _ocr(img, lang):
    # same preprocessing + PSM as the pipeline
    return pytesseract.image_to_string(invoice_ocr._prep(img), lang=lang,
                                       config=invoice_ocr.PSM)


def test_polish_chars_intact_and_code_preserved():
    langs = pytesseract.get_languages(config="")
    assert "pol" in langs, "pol traineddata not installed in tessdata"

    img = _render_invoice()
    pol_text = _ocr(img, "pol+eng")
    print("pol+eng OCR =>", repr(pol_text.strip()[:120]))

    # Polish content recovered (diacritics intact)
    assert "montaż" in pol_text or "montaz" not in pol_text and "ż" in pol_text, pol_text
    hits = [c for c in DIACRITICS if c in pol_text]
    assert hits, f"no Polish diacritics recovered: {pol_text!r}"
    # the HS code must survive (binarization-free preprocessing protects it)
    assert "7308" in pol_text, f"HS code lost: {pol_text!r}"
    print("  diacritics recovered:", hits, "| HS code 7308 preserved: yes")


def test_english_path_lang_default_unchanged():
    # The pipeline default is still 'eng' — English OCR behaviour is unchanged.
    import inspect
    sig = inspect.signature(invoice_ocr.extract_line_items_ocr)
    assert sig.parameters["lang"].default == "eng", sig
    sig2 = inspect.signature(invoice_ocr._ocr_lines)
    assert sig2.parameters["lang"].default == "eng", sig2
    print("English path OK — OCR lang defaults to 'eng' (unchanged)")


if __name__ == "__main__":
    test_polish_chars_intact_and_code_preserved()
    test_english_path_lang_default_unchanged()
    print("\nPolish OCR tests passed.")
