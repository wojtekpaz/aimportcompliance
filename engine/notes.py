#!/usr/bin/env python3
"""
notes.py — Retrieve binding Section & Chapter legal Notes for GRI-1 context.

ROLE: the Notes are natural-language LAW that GRI-1 requires ("classification
shall be determined according to the terms of the headings AND any relative
Section or Chapter Notes"). They cannot be reduced to a lookup table without
losing meaning, so we inject them as RETRIEVED CONTEXT into the oracle's
heading-selection prompt. The oracle reads them and applies exclusions; the
deterministic engine still controls flow. LLM = interpreter, never code source.

Section number for a chapter is derived from the standard HS section map.
"""
import sqlite3

# HS chapter -> section (roman). Stable mapping from the Harmonized System.
_SECTION_OF_CHAPTER = {}
_RANGES = [
    ("I", 1, 5), ("II", 6, 14), ("III", 15, 15), ("IV", 16, 24),
    ("V", 25, 27), ("VI", 28, 38), ("VII", 39, 40), ("VIII", 41, 43),
    ("IX", 44, 46), ("X", 47, 49), ("XI", 50, 63), ("XII", 64, 67),
    ("XIII", 68, 70), ("XIV", 71, 71), ("XV", 72, 83), ("XVI", 84, 85),
    ("XVII", 86, 89), ("XVIII", 90, 92), ("XIX", 93, 93), ("XX", 94, 96),
    ("XXI", 97, 97),
]
for _rom, _a, _b in _RANGES:
    for _c in range(_a, _b + 1):
        _SECTION_OF_CHAPTER[_c] = _rom


def section_of(chapter: int) -> str:
    return _SECTION_OF_CHAPTER.get(chapter, "")


def notes_for_chapters(conn, chapters: list[int]) -> dict:
    """Return {'chapter': {n: text}, 'section': {rom: text}} for the given
    chapters and their sections. De-duplicated."""
    out = {"chapter": {}, "section": {}}
    secs = set()
    for ch in chapters:
        r = conn.execute(
            "SELECT note_text FROM legal_note WHERE kind='chapter' AND ident=?",
            (str(ch),)).fetchone()
        if r and r[0].strip():
            out["chapter"][ch] = r[0]
        rom = section_of(ch)
        if rom:
            secs.add(rom)
    for rom in secs:
        r = conn.execute(
            "SELECT note_text FROM legal_note WHERE kind='section' AND ident=?",
            (rom,)).fetchone()
        if r and r[0].strip():
            out["section"][rom] = r[0]
    return out


def format_notes_for_prompt(notes: dict, max_chars: int = 4000) -> str:
    """Compact text block for the GRI-1 oracle prompt."""
    parts = []
    for rom, txt in notes["section"].items():
        parts.append(f"[Section {rom} notes]\n{txt}")
    for ch, txt in notes["chapter"].items():
        parts.append(f"[Chapter {ch} notes]\n{txt}")
    blob = "\n\n".join(parts)
    if len(blob) > max_chars:
        blob = blob[:max_chars] + "\n…(notes truncated; full text in DB)"
    return blob


def chapters_from_headings(headings: list[str]) -> list[int]:
    out = []
    for h in headings:
        try:
            c = int(str(h)[:2])
            if c not in out:
                out.append(c)
        except ValueError:
            pass
    return out
