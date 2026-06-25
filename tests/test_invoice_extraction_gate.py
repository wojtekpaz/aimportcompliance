#!/usr/bin/env python3
"""Hermetic tests for the invoice-extraction fix: header/party/totals rejection,
space-separated HS-code matching, code-less goods rows kept, the shared validity
gate, and the extraction-suspect plausibility guard. No OCR engine, no API key —
these pin the deterministic row-parsing logic with synthetic OCR rows."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import invoice_ocr as ocr          # noqa: E402
import invoice_session as inv      # noqa: E402


def _w(text, left, conf=95.0):
    return {"text": text, "conf": conf, "left": left, "top": 0}


def _row(*texts):
    return [_w(t, i * 50) for i, t in enumerate(texts)]


# --------------------------------------------------------------------------- #
#  Header / party / totals rows are not goods lines                           #
# --------------------------------------------------------------------------- #
def test_vat_row_rejected():
    assert ocr._is_non_goods_row(_row("VAT:", "NL8123.45.678.B01"))


def test_eori_row_rejected():
    assert ocr._is_non_goods_row(_row("EORI:", "DE517734221"))


def test_totals_rows_rejected():
    for label in ("Subtotal", "Freight", "Insurance", "TOTAL(USD)", "Value:"):
        assert ocr._is_non_goods_row(_row(label, "1,234.00")), label


def test_goods_row_not_rejected():
    assert not ocr._is_non_goods_row(
        _row("1", "Men's", "T-shirts,", "cotton", "6109", "10", "00", "2,100.00"))


# --------------------------------------------------------------------------- #
#  Space-separated HS codes are matched across tokens                         #
# --------------------------------------------------------------------------- #
def test_spaced_hs_code_matched():
    code, toks, conf = ocr._match_hs(_row("Bicycles", "8712", "00", "30", "88.00"))
    assert code == "87120030"
    assert {t["text"] for t in toks} == {"8712", "00", "30"}


def test_dotted_single_token_code_still_matched():
    code, _, _ = ocr._match_hs(_row("Laptop", "8471.30.00", "13,120.00"))
    assert code == "84713000"


# --------------------------------------------------------------------------- #
#  _parse_rows end to end on synthetic rows                                    #
# --------------------------------------------------------------------------- #
def test_parse_drops_headers_keeps_goods_and_codeless():
    rows = [
        _row("VAT:", "NL8123.45.678.B01"),                       # header -> drop
        _row("EORI:", "DE517734221"),                            # header -> drop
        _row("1", "Men's", "T-shirts", "cotton", "6109", "10", "00", "2,100.00"),
        _row("7", "Photovoltaic", "solar", "modules", "450W", "11,400.00"),  # code-less goods
        _row("Subtotal", "55,800.00"),                           # totals -> drop
    ]
    items = ocr._parse_rows(rows)
    descs = [it["description"] for it in items]
    codes = [it["code"] for it in items]
    assert len(items) == 2, descs
    assert any("T-shirts" in d for d in descs)
    assert any("solar" in d.lower() for d in descs)
    assert "61091000" in codes
    assert "" in codes  # the code-less solar line survived


def test_codeless_money_without_description_dropped():
    # a bare money figure with no real description is not a goods line
    rows = [_row("9,999.00")]
    assert ocr._parse_rows(rows) == []


# --------------------------------------------------------------------------- #
#  Shared validity gate (invoice_session) — applies to any tier's output      #
# --------------------------------------------------------------------------- #
def test_drop_non_goods_filters_header_and_taxid():
    items = [
        {"description": "VAT:", "code": "812345678"},
        {"description": "EORI:", "code": "DE517734221"},
        {"description": "Men's T-shirts", "code": "61091000"},
    ]
    out = inv._drop_non_goods(items)
    assert len(out) == 1
    assert out[0]["description"] == "Men's T-shirts"
    assert out[0]["row"] == 1


# --------------------------------------------------------------------------- #
#  Extraction-suspect plausibility guard                                       #
# --------------------------------------------------------------------------- #
def test_all_codes_malformed_true_for_header_junk(monkeypatch):
    monkeypatch.setattr(inv, "_code_is_valid", lambda c: False)
    items = [{"code": "812345678"}, {"code": "517734221"}]
    assert inv._all_codes_malformed(items) is True


def test_all_codes_malformed_false_with_one_valid(monkeypatch):
    monkeypatch.setattr(inv, "_code_is_valid", lambda c: c == "61091000")
    items = [{"code": "812345678"}, {"code": "61091000"}]
    assert inv._all_codes_malformed(items) is False


def test_all_codes_malformed_false_when_no_codes(monkeypatch):
    monkeypatch.setattr(inv, "_code_is_valid", lambda c: False)
    # code-less goods (e.g. solar) is not a malformed-code signal
    assert inv._all_codes_malformed([{"code": ""}, {"code": ""}]) is False
