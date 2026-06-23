#!/usr/bin/env python3
"""
file_parsers.py — Excel and XML invoice parsers for AImport Compliance.

Milestone One (V2), Section 1. These parsers turn a broker-uploaded structured
file (Excel .xlsx/.xls or XML) into a list of LineItem records. They DO NOT
classify anything — they only faithfully extract what the document declares,
preserving the raw row for the audit trail. The GRI engine downstream decides
what to do with each line, including freezing lines that cannot be resolved.

No AI reads these files; extraction is deterministic, so nothing can be invented.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field, asdict


@dataclass
class LineItem:
    line_number: int                 # row position in source file (1-based)
    description: str | None = None   # product/goods description; None if blank
    hs_code: str | None = None       # declared HS/CN code if present; None if absent
    quantity: float | None = None
    unit: str | None = None
    country_of_origin: str | None = None
    raw_row: dict = field(default_factory=dict)  # original key->value for audit

    def to_dict(self) -> dict:
        return asdict(self)

    def to_invoice_item(self, row_override: int | None = None) -> dict:
        """Adapt to the dict shape the existing invoice analyzer consumes
        (analyze_item in invoice_session.py): {row, description, code, qty}.
        Missing fields are passed as empty strings so the engine treats them
        as gaps rather than receiving None."""
        return {
            "row": row_override if row_override is not None else self.line_number,
            "description": self.description or "",
            "code": self.hs_code or "",
            "qty": "" if self.quantity is None else _fmt_qty(self.quantity, self.unit),
            "raw_row": self.raw_row,
            "country_of_origin": self.country_of_origin or "",
        }


class ParseWarning(Warning):
    """Raised (as a warning, surfaced as a soft yellow banner — never a hard
    error) when a file's structure cannot be recognised."""


def _fmt_qty(q, unit) -> str:
    if q is None:
        return ""
    if isinstance(q, float) and q.is_integer():
        q = int(q)
    return f"{q} {unit}".strip() if unit else str(q)


# --------------------------------------------------------------------------- #
#  Shared header vocabulary                                                    #
# --------------------------------------------------------------------------- #

_DESC_HEADERS = ["description", "omschrijving", "opis", "товар", "article",
                 "goods", "commodity", "item", "product", "nazwa", "bezeichnung"]
_CODE_HEADERS = ["hs code", "hs", "cn code", "cn", "taric", "tariff", "kod hs",
                 "positie", "commodity code", "code"]
_QTY_HEADERS = ["quantity", "qty", "ilość", "menge", "aantal", "кол-во", "amount"]
_UNIT_HEADERS = ["unit", "uom", "jednostka", "einheit", "ед.изм"]
_ORIGIN_HEADERS = ["origin", "country of origin", "kraj pochodzenia",
                   "ursprung", "coo", "made in"]

_CODE_VALUE_RE = re.compile(r"^\d{4,10}$")


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def _match_score(cell_norm: str, vocabulary) -> int:
    """Length of the longest vocabulary phrase contained in the header cell,
    or 0 if none match. Longer phrase = more specific match, so a 'Description'
    column beats a generic 'Item' index column for the description role."""
    best = 0
    for phrase in vocabulary:
        if phrase in cell_norm and len(phrase) > best:
            best = len(phrase)
    return best


def _norm_code(s) -> str | None:
    """Keep a declared code only if it looks like an HS/CN/TARIC code
    (4–10 digits after stripping non-digits). Otherwise None (absent)."""
    digits = re.sub(r"\D", "", str(s or ""))
    return digits if 4 <= len(digits) <= 10 else None


def _to_float(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    txt = re.sub(r"[^\d.,\-]", "", str(s))
    if not txt:
        return None
    # normalise european decimals: "1.234,56" -> "1234.56"; "1,5" -> "1.5"
    if "," in txt and "." in txt:
        txt = txt.replace(".", "").replace(",", ".")
    elif "," in txt:
        txt = txt.replace(",", ".")
    try:
        return float(txt)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
#  Excel                                                                       #
# --------------------------------------------------------------------------- #

def parse_excel_invoice(file_bytes: bytes, filename: str = "") -> list[LineItem]:
    """Parse an .xlsx/.xls invoice into LineItems. See Section 1a."""
    rows = _read_excel_rows(file_bytes, filename)
    if not rows:
        return []

    header_idx, cols = _detect_excel_header(rows)
    items: list[LineItem] = []

    data_rows = rows[header_idx + 1:] if header_idx is not None else rows
    line_no = 0
    for r in data_rows:
        if r is None or all(c in (None, "") for c in r):
            continue  # blank spreadsheet row
        line_no += 1
        desc = _cell(r, cols.get("description"))
        code = _cell(r, cols.get("hs_code"))
        qty = _cell(r, cols.get("quantity"))
        unit = _cell(r, cols.get("unit"))
        origin = _cell(r, cols.get("country_of_origin"))

        description = (str(desc).strip() or None) if desc not in (None, "") else None
        hs_code = _norm_code(code)

        # Section 1a.4 — skip rows where BOTH description and hs_code are absent.
        if description is None and hs_code is None:
            line_no -= 1
            continue

        raw = {}
        if cols.get("_headers"):
            for j, h in enumerate(cols["_headers"]):
                if j < len(r) and r[j] not in (None, ""):
                    raw[h or f"col{j}"] = r[j]
        else:
            raw = {f"col{j}": v for j, v in enumerate(r) if v not in (None, "")}

        items.append(LineItem(
            line_number=line_no,
            description=description,
            hs_code=hs_code,
            quantity=_to_float(qty),
            unit=(str(unit).strip() or None) if unit not in (None, "") else None,
            country_of_origin=(str(origin).strip().upper() or None)
                              if origin not in (None, "") else None,
            raw_row=raw,
        ))
    return items


def _read_excel_rows(file_bytes: bytes, filename: str) -> list[list]:
    """Return a list of rows (each a list of cell values). Uses openpyxl for
    .xlsx and xlrd for legacy .xls."""
    name = (filename or "").lower()
    is_legacy = name.endswith(".xls") and not name.endswith(".xlsx")
    if not is_legacy:
        # sniff the zip magic for xlsx even if the extension lies
        is_legacy = not file_bytes[:2] == b"PK"
    if is_legacy:
        return _read_xls_rows(file_bytes)
    return _read_xlsx_rows(file_bytes)


def _read_xlsx_rows(file_bytes: bytes) -> list[list]:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True,
                                data_only=True)
    ws = wb.active
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    wb.close()
    return rows


def _read_xls_rows(file_bytes: bytes) -> list[list]:
    import xlrd
    book = xlrd.open_workbook(file_contents=file_bytes)
    sheet = book.sheet_by_index(0)
    return [sheet.row_values(i) for i in range(sheet.nrows)]


def _cell(row, idx):
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def _detect_excel_header(rows) -> tuple[int | None, dict]:
    """Find the header row in the first 10 rows and map logical columns to
    indices. Falls back to heuristics (Section 1a.3) if no header is found."""
    for i, r in enumerate(rows[:10]):
        if not r:
            continue
        norms = [_norm(c) for c in r]
        desc_i = _find_col(norms, _DESC_HEADERS)
        code_i = _find_col(norms, _CODE_HEADERS)
        if desc_i is not None or code_i is not None:
            return i, {
                "description": desc_i,
                "hs_code": code_i,
                "quantity": _find_col(norms, _QTY_HEADERS),
                "unit": _find_col(norms, _UNIT_HEADERS),
                "country_of_origin": _find_col(norms, _ORIGIN_HEADERS),
                "_headers": [str(c).strip() if c not in (None, "") else None
                             for c in r],
            }

    # Heuristic fallback: no recognisable header in the first 10 rows.
    return None, _heuristic_columns(rows)


def _find_col(norm_cells, vocabulary) -> int | None:
    """Column whose header matches the most specific vocabulary phrase."""
    best_j, best_score = None, 0
    for j, cell in enumerate(norm_cells):
        if not cell:
            continue
        score = _match_score(cell, vocabulary)
        if score > best_score:
            best_j, best_score = j, score
    return best_j


def _heuristic_columns(rows) -> dict:
    """No header: the column with the longest string values is the description;
    the column whose values look like codes is hs_code."""
    if not rows:
        return {"description": None, "hs_code": None}
    width = max((len(r) for r in rows if r), default=0)
    avg_len = [0.0] * width
    code_hits = [0] * width
    counts = [0] * width
    for r in rows:
        if not r:
            continue
        for j in range(width):
            v = r[j] if j < len(r) else None
            if v in (None, ""):
                continue
            counts[j] += 1
            avg_len[j] += len(str(v))
            if _CODE_VALUE_RE.match(re.sub(r"\D", "", str(v)) or "x"):
                code_hits[j] += 1
    for j in range(width):
        if counts[j]:
            avg_len[j] /= counts[j]
    desc_i = max(range(width), key=lambda j: avg_len[j]) if width else None
    code_candidates = [j for j in range(width)
                       if counts[j] and code_hits[j] / counts[j] >= 0.5
                       and j != desc_i]
    code_i = code_candidates[0] if code_candidates else None
    return {"description": desc_i, "hs_code": code_i, "quantity": None,
            "unit": None, "country_of_origin": None, "_headers": None}


# --------------------------------------------------------------------------- #
#  XML                                                                         #
# --------------------------------------------------------------------------- #

DESCRIPTION_TAGS = [
    "Description", "GoodsDescription", "CommodityDescription",
    "ItemDescription", "Opis", "NazwaTowaru", "Bezeichnung",
    "GoodsDesc", "ProductDescription", "ArticleDescription",
]
CODE_TAGS = [
    "HSCode", "CommodityCode", "TariffCode", "CNCode", "HsCode",
    "KodHS", "TaricCode", "StatisticalCode", "HSCode", "CommodityCodeID",
]
QTY_TAGS = ["Quantity", "Qty", "Ilosc", "Menge", "OrderedQuantity"]
UNIT_TAGS = ["Unit", "UnitOfMeasure", "UoM", "MeasureUnit", "Jednostka"]
ORIGIN_TAGS = ["CountryOfOrigin", "Origin", "OriginCountry", "KrajPochodzenia"]

_ITEM_HINT_RE = re.compile(
    r"(line|item|good|article|position|order.?line|product|towar)", re.I)


def parse_xml_invoice(file_bytes: bytes) -> list[LineItem]:
    """Parse an XML invoice/order/declaration into LineItems. See Section 1b.

    Raises ParseWarning if no repeating item-level elements can be found, so the
    caller can surface a soft yellow banner instead of a hard error.
    """
    from lxml import etree

    parser = etree.XMLParser(recover=True, resolve_entities=False,
                             no_network=True)
    try:
        root = etree.fromstring(file_bytes, parser=parser)
    except Exception as e:
        raise ParseWarning(f"XML could not be parsed — manual review required "
                           f"({str(e)[:80]}).")
    if root is None:
        raise ParseWarning("XML structure not recognised — manual review required")

    item_elems = _find_item_elements(root)
    if not item_elems:
        raise ParseWarning("XML structure not recognised — manual review required")

    items: list[LineItem] = []
    line_no = 0
    for el in item_elems:
        desc = _first_child_text(el, DESCRIPTION_TAGS)
        code = _first_child_text(el, CODE_TAGS)
        if desc is None and code is None:
            continue
        line_no += 1
        items.append(LineItem(
            line_number=line_no,
            description=desc,
            hs_code=_norm_code(code),
            quantity=_to_float(_first_child_text(el, QTY_TAGS)),
            unit=_first_child_text(el, UNIT_TAGS),
            country_of_origin=(_first_child_text(el, ORIGIN_TAGS) or "").upper()
                              or None,
            raw_row=_element_raw(el),
        ))
    if not items:
        raise ParseWarning("XML structure not recognised — manual review required")
    return items


def _localname(tag) -> str:
    """Strip any namespace: '{ns}Description' -> 'Description'."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _find_item_elements(root) -> list:
    """Find the repeating item-level elements. Strategy: among elements at
    depth 2–4, pick the most common tag whose name hints at a line/item AND
    which contains a description or code child. Falls back to the largest
    repeating sibling group containing a known child tag."""
    from collections import defaultdict

    by_tag = defaultdict(list)
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue  # comments / PIs
        depth = _depth(el, root)
        if 1 <= depth <= 5:
            by_tag[_localname(el.tag)].append(el)

    known = set(t.lower() for t in DESCRIPTION_TAGS + CODE_TAGS)

    def has_known_child(el) -> bool:
        for ch in el.iter():
            if ch is el:
                continue
            if _localname(ch.tag).lower() in known:
                return True
        return False

    # 1) hinted repeating tags that carry a known child
    candidates = []
    for tag, els in by_tag.items():
        if len(els) < 1:
            continue
        carriers = [e for e in els if has_known_child(e)]
        if not carriers:
            continue
        hinted = bool(_ITEM_HINT_RE.search(tag))
        candidates.append((hinted, len(carriers), tag, carriers))

    if not candidates:
        return []
    # prefer hinted tags, then the most numerous group
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    return candidates[0][3]


def _depth(el, root) -> int:
    d = 0
    p = el.getparent()
    while p is not None:
        d += 1
        p = p.getparent()
    return d


def _first_child_text(el, tag_names) -> str | None:
    """First descendant whose localname matches any of tag_names (case-
    insensitive), returning its stripped text."""
    wanted = {t.lower() for t in tag_names}
    for ch in el.iter():
        if ch is el:
            continue
        if _localname(ch.tag).lower() in wanted:
            txt = (ch.text or "").strip()
            if txt:
                return txt
    return None


def _element_raw(el) -> dict:
    """A flat key->text snapshot of an item element's leaf children, for the
    audit trail."""
    raw = {}
    for ch in el.iter():
        if ch is el:
            continue
        if len(ch) == 0 and isinstance(ch.tag, str):
            txt = (ch.text or "").strip()
            if txt:
                raw[_localname(ch.tag)] = txt
    return raw
