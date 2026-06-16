#!/usr/bin/env python3
"""
extract_notes.py — One-time extraction of Section & Chapter legal Notes
from the Combined Nomenclature (Annex I to Reg. 2658/87, OJ L 2025/1926).

SCOPE (deliberately small): the binding Section Notes and Chapter Notes are a
bounded, slow-moving corpus (21 sections, ~97 chapters, updated ~yearly). We
extract them ONCE into a clean table the GRI-1 prompt can retrieve. This is
NOT a daily pipeline and is intentionally simple.

METHOD: pdfplumber page-by-page (clean text; pypdf mangles the font). For each
CHAPTER/SECTION header we capture the note block from the 'Note(s)' line up to
the start of the tariff table or the next header. Includes 'Additional notes'
(EU-specific) since they carry legal weight too.

VERIFICATION: prints a per-chapter summary; a human spot-checks a few against
the PDF before the notes are trusted in classification.

USAGE:
    python3 ingest/extract_notes.py <CN_annex_pdf> <database.sqlite>
"""
import re
import sqlite3
import sys
from pathlib import Path
import pdfplumber

HEADER_RE = re.compile(r"^(SECTION ([IVXLC]+)|CHAPTER (\d{1,2}))\s*$")
# A tariff table starts with rows like 'CN code' header or '0201 ...' dotted
# codes. We stop the note block when we hit clear table content.
TABLE_MARKERS = ("CN code", "Description", "Conventional rate of duty",
                 "Supplementary unit")
NOTE_START_RE = re.compile(r"^(Notes?|Subheading notes?|Additional notes?)\b",
                           re.IGNORECASE)


def clean_line(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def is_footer(line: str) -> bool:
    l = line.strip()
    return (l.startswith("OJ L,") or l.startswith("ELI:")
            or l in ("EN",) or re.match(r"^\d+/\d+$", l)
            or "data.europa.eu/eli" in l)


def extract(pdf_path: Path, max_pages: int = 1000):
    """Yield dicts: {kind, ident, title, notes_text, page}."""
    current = None
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            if i >= max_pages:
                break
            text = page.extract_text() or ""
            page.flush_cache()
            lines = [l for l in text.split("\n") if not is_footer(l)]
            j = 0
            while j < len(lines):
                line = clean_line(lines[j])
                m = HEADER_RE.match(line)
                if m:
                    # flush previous
                    if current and current["notes"]:
                        yield current
                    kind = "section" if m.group(2) else "chapter"
                    ident = m.group(2) or m.group(3)
                    title = clean_line(lines[j + 1]) if j + 1 < len(lines) else ""
                    current = {"kind": kind, "ident": ident, "title": title,
                               "page": i, "notes": [], "in_notes": False}
                    j += 2
                    continue
                if current is not None:
                    if NOTE_START_RE.match(line):
                        current["in_notes"] = True
                        current["notes"].append(line)
                        j += 1
                        continue
                    if current["in_notes"]:
                        # stop at the tariff table
                        if any(mk in line for mk in TABLE_MARKERS):
                            current["in_notes"] = False
                        elif line:
                            current["notes"].append(line)
                j += 1
        if current and current["notes"]:
            yield current


def load(pdf_path: Path, db_path: Path):
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS legal_note (
        kind TEXT, ident TEXT, title TEXT, note_text TEXT,
        source TEXT, page INTEGER)""")
    conn.execute("DELETE FROM legal_note")  # one-shot, idempotent
    rows = 0
    summary = []
    for rec in extract(pdf_path):
        note_text = "\n".join(rec["notes"])
        conn.execute("INSERT INTO legal_note VALUES (?,?,?,?,?,?)",
                     (rec["kind"], rec["ident"], rec["title"], note_text,
                      pdf_path.name, rec["page"]))
        rows += 1
        summary.append((rec["kind"], rec["ident"], len(note_text),
                        rec["title"][:40]))
    conn.commit()
    conn.close()
    return rows, summary


def main():
    pdf_path, db_path = Path(sys.argv[1]), Path(sys.argv[2])
    rows, summary = load(pdf_path, db_path)
    chapters = [s for s in summary if s[0] == "chapter"]
    sections = [s for s in summary if s[0] == "section"]
    print(f"Extracted {rows} note blocks: "
          f"{len(sections)} sections, {len(chapters)} chapters\n")
    print("kind     id   chars  title")
    for kind, ident, n, title in summary:
        flag = "  <-- short, check" if n < 30 else ""
        print(f"  {kind:8} {ident:>3}  {n:>5}  {title}{flag}")
    # coverage warning
    chap_ids = {int(c[1]) for c in chapters if c[1].isdigit()}
    missing = [c for c in range(1, 98) if c not in chap_ids and c != 77]
    if missing:
        print(f"\n! chapters with no notes captured: {missing}")
        print("  (some chapters legitimately have no notes; verify a sample)")
    print("\nVERIFY: open the PDF, compare 3-4 chapters' notes against the table.")


if __name__ == "__main__":
    main()
