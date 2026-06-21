#!/usr/bin/env python3
"""
legal.py — Deterministic legal-basis link & status derivation.

A legal citation in AImport is ALWAYS a deterministic value read from the
tariff DB (`measure.regulation_id`). This module turns that stored reference
into a *stable* EUR-Lex URL and an honest in-force/expired status. It invents
NOTHING: if a reference cannot be mapped to a real CELEX document type, no
link is produced (the bare reference is shown instead). The LLM is never in
this path.

Stored reference format (verified across the export): "<Type> NNNN/YY", e.g.
"Regulation 1734/96", "Decision 0263/22". The number is already zero-padded to
four digits; the year is two digits.

CELEX structure for EU legislation: sector `3` + 4-digit year + document-type
letter + 4-digit number. So Regulation 1734/96 -> 31996R1734 ->
https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=celex:31996R1734
"""
import re
from datetime import date

# Reference word -> CELEX document-type letter. Only types that map to a real,
# citable CELEX legislation document are included. "Accession", "Draft" and
# "Information" deliberately have NO mapping: we will not fabricate a CELEX id
# for a reference we cannot resolve (guardrail: never invent an identifier).
_TYPE_LETTER = {
    "regulation": "R",
    "decision": "D",
    "directive": "L",
}

_REF_RE = re.compile(r"^\s*([A-Za-z]+)\s+0*(\d+)\s*/\s*(\d{2})\s*$")

_EURLEX_CELEX = "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=celex:{}"


def _expand_year(yy: int) -> int:
    """Two-digit OJ year -> four digits. 70-99 -> 19xx, 00-69 -> 20xx.
    (TARIC legal bases run from the 1960s; the pivot keeps recent regs in the
    2000s while old amending regs stay in the 1900s.)"""
    return 1900 + yy if yy >= 70 else 2000 + yy


def celex_from_regulation(reg: str | None) -> str | None:
    """'Regulation 1734/96' -> '31996R1734'. Returns None if the reference is
    empty, malformed, or of a type we cannot map to a real CELEX document."""
    if not reg:
        return None
    m = _REF_RE.match(reg)
    if not m:
        return None
    word, num, yy = m.group(1).lower(), m.group(2), int(m.group(3))
    letter = _TYPE_LETTER.get(word)
    if not letter:
        return None
    year = _expand_year(yy)
    return f"3{year}{letter}{int(num):04d}"


def eurlex_url(reg: str | None) -> str | None:
    """Stable EUR-Lex CELEX URL for a stored regulation reference, or None when
    no CELEX id can be derived (caller then shows the bare reference, no link)."""
    celex = celex_from_regulation(reg)
    return _EURLEX_CELEX.format(celex) if celex else None


def legal_status(validity_end: str | None, on_date: str | None = None) -> str:
    """Honest in-force/expired status for the MEASURE that carries this legal
    basis, derived from its validity_end (the only validity signal we hold).
    NULL/empty end date => still in force. Returns 'in_force' or 'expired'."""
    if not validity_end:
        return "in_force"
    on_date = on_date or date.today().isoformat()
    return "in_force" if validity_end >= on_date else "expired"


def legal_info(reg: str | None, validity_end: str | None = None,
               legal_oj=None, on_date: str | None = None) -> dict:
    """Bundle everything a frontend needs to render a legal basis honestly.

    `legal_oj` is the optional (official_journal, page, publication_date) tuple
    from the legal_basis table. Returns a dict with: reference, url (or None),
    status ('in_force'/'expired'), and oj (display string or '')."""
    oj = ""
    if legal_oj:
        journal, page, pub = (list(legal_oj) + [None, None, None])[:3]
        bits = [b for b in (journal, f"p.{page}" if page else None, pub) if b]
        oj = " ".join(bits)
    return {
        "reference": reg or "",
        "url": eurlex_url(reg),
        "status": legal_status(validity_end, on_date),
        "oj": oj,
    }


if __name__ == "__main__":
    # Quick self-check of the verified reference points.
    for r, expect in [("Regulation 1734/96", "31996R1734"),
                      ("Regulation 1054/97", "31997R1054"),
                      ("Decision 0263/22", "32022D0263"),
                      ("Accession 1/03", None),
                      ("", None)]:
        got = celex_from_regulation(r)
        flag = "ok" if got == expect else "FAIL"
        print(f"  [{flag}] {r!r:24} -> {got!r}  (expect {expect!r})")
    print("  url:", eurlex_url("Regulation 1734/96"))
