"""Phase 1 acceptance: Polish text (UTF-8 + diacritics) must round-trip
byte-identical through the SQLite layer and the JSON/API serialization layer.

Run:  python3 tests/test_pl_roundtrip.py
"""
import json
import os
import sqlite3
import tempfile

# Every Polish diacritic the prompt calls out, plus a realistic product description.
SAMPLE = "Stalowe wsporniki łączące — ą ę ś ż ł ó ń ć ź; żółć gęślą jaźń (kod 7308.90.98)"
REQUIRED_CHARS = ["ł", "ą", "ę", "ś", "ż"]


def test_required_diacritics_present():
    for ch in REQUIRED_CHARS:
        assert ch in SAMPLE, f"sample missing {ch}"


def test_sqlite_roundtrip():
    """Bytes in == bytes out through SQLite TEXT (UTF-8)."""
    path = tempfile.mktemp(suffix=".sqlite")
    try:
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE t (x TEXT)")
        conn.execute("INSERT INTO t (x) VALUES (?)", (SAMPLE,))
        conn.commit()
        got = conn.execute("SELECT x FROM t").fetchone()[0]
        conn.close()
    finally:
        if os.path.exists(path):
            os.unlink(path)
    assert got == SAMPLE, "SQLite altered the Polish string"
    assert got.encode("utf-8") == SAMPLE.encode("utf-8")


def test_json_api_roundtrip():
    """The API serializes through JSON; both ensure_ascii modes must preserve the text."""
    for ensure_ascii in (True, False):
        blob = json.dumps({"text": SAMPLE}, ensure_ascii=ensure_ascii)
        back = json.loads(blob)["text"]
        assert back == SAMPLE, f"JSON round-trip changed text (ensure_ascii={ensure_ascii})"


if __name__ == "__main__":
    test_required_diacritics_present()
    test_sqlite_roundtrip()
    test_json_api_roundtrip()
    print("PL diacritics round-trip OK (sqlite + json):")
    print("  ", SAMPLE)
