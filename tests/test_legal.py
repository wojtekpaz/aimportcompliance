#!/usr/bin/env python3
"""
test_legal.py — deterministic legal-basis link/status derivation.

Locks in the EUR-Lex CELEX URL construction (replacing the old, 404-prone
search URL) and the honest in-force/expired status + empty-state behaviour.
Run: python3 tests/test_legal.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))
from legal import (celex_from_regulation, eurlex_url, legal_status,  # noqa: E402
                   legal_info)

_p = _f = 0


def check(name, cond):
    global _p, _f
    if cond:
        _p += 1; print(f"  PASS  {name}")
    else:
        _f += 1; print(f"  FAIL  {name}")


print("[1] CELEX id from stored 'Type NNNN/YY' references")
check("Regulation 1734/96 -> 31996R1734",
      celex_from_regulation("Regulation 1734/96") == "31996R1734")
check("Regulation 1054/97 -> 31997R1054 (in-force example)",
      celex_from_regulation("Regulation 1054/97") == "31997R1054")
check("Decision 0263/22 -> 32022D0263 (leading-zero number kept)",
      celex_from_regulation("Decision 0263/22") == "32022D0263")
check("year pivot: 96 -> 1996",
      celex_from_regulation("Regulation 0001/96") == "31996R0001")
check("year pivot: 22 -> 2022",
      celex_from_regulation("Regulation 0001/22") == "32022R0001")
check("number zero-padded to 4 digits",
      celex_from_regulation("Regulation 12/05") == "32005R0012")

print("\n[2] Unmappable / empty references invent NOTHING (no fabrication)")
check("Accession act -> None (no CELEX guess)",
      celex_from_regulation("Accession 1/03") is None)
check("Draft -> None", celex_from_regulation("Draft 5/20") is None)
check("Information -> None", celex_from_regulation("Information 9/19") is None)
check("empty -> None", celex_from_regulation("") is None)
check("None -> None", celex_from_regulation(None) is None)
check("garbage -> None", celex_from_regulation("see annex") is None)

print("\n[3] Stable EUR-Lex URL (CELEX, not the 404-prone search URL)")
check("url uses legal-content/celex form",
      eurlex_url("Regulation 1734/96") ==
      "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=celex:31996R1734")
check("no search.html in URL",
      "search.html" not in (eurlex_url("Regulation 1734/96") or ""))
check("unmappable ref -> no link (None)",
      eurlex_url("Accession 1/03") is None)

print("\n[4] Honest in-force / expired status from measure validity_end")
check("NULL end -> in_force", legal_status(None) == "in_force")
check("empty end -> in_force", legal_status("") == "in_force")
check("future end -> in_force", legal_status("2999-01-01") == "in_force")
check("past end -> expired",
      legal_status("2000-01-01", on_date="2026-06-20") == "expired")

print("\n[5] legal_info bundle")
b = legal_info("Regulation 1734/96", validity_end=None,
               legal_oj=("L 238", "1", "1996-09-19"))
check("reference preserved", b["reference"] == "Regulation 1734/96")
check("url present", b["url"].endswith("celex:31996R1734"))
check("status in_force", b["status"] == "in_force")
check("oj string assembled", b["oj"] == "L 238 p.1 1996-09-19")
empty = legal_info("", validity_end=None)
check("empty reference -> no url", empty["url"] is None)
check("empty reference -> empty oj", empty["oj"] == "")

print("\n" + "=" * 50)
print(f"RESULT: {_p} passed, {_f} failed")
sys.exit(1 if _f else 0)
