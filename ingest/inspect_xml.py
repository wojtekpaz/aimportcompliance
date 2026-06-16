#!/usr/bin/env python3
"""
inspect_xml.py — TARIC file format discovery.

WHY THIS EXISTS:
TARIC data is published in several XML dialects (daily TARIC3 transmissions,
full extractions from the EU Tariff Portal, member-state distributions like
Tullverket's per-table files). Writing a parser blind against the wrong
dialect produces silently wrong data — the worst possible failure for a
legal product. So rule #1: NEVER parse a file we haven't inspected.

USAGE:
    python3 ingest/inspect_xml.py path/to/file.xml   (or .zip)

OUTPUT: a human-readable report of element names, counts, nesting and
samples — enough to confirm (or adapt) the parser before ingest.
"""
import sys
import zipfile
import io
from collections import Counter, defaultdict
from pathlib import Path
from lxml import etree


def iter_xml_sources(path: Path):
    """Yield (name, file-like) for the path; transparently opens zips."""
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if name.lower().endswith(".xml"):
                    yield name, io.BytesIO(zf.read(name))
    else:
        yield path.name, open(path, "rb")


def strip_ns(tag: str) -> str:
    """'{namespace}tag' -> 'tag'. Namespaces vary between dialects."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def inspect(source, name: str, sample_limit: int = 3) -> str:
    tag_counts = Counter()
    parent_child = defaultdict(Counter)
    samples = defaultdict(list)
    depth_of = {}
    root_tag = None

    # iterparse = constant memory even on multi-GB files
    context = etree.iterparse(source, events=("start", "end"), recover=True)
    stack = []
    for event, elem in context:
        tag = strip_ns(elem.tag)
        if event == "start":
            if root_tag is None:
                root_tag = tag
            if stack:
                parent_child[stack[-1]][tag] += 1
            stack.append(tag)
            depth_of.setdefault(tag, len(stack))
        else:  # end
            stack.pop()
            tag_counts[tag] += 1
            text = (elem.text or "").strip()
            if text and len(samples[tag]) < sample_limit:
                samples[tag].append(text[:80])
            # free memory
            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]

    lines = [f"=== Inspection report: {name} ===",
             f"Root element: <{root_tag}>",
             f"Distinct element types: {len(tag_counts)}",
             "", "--- Element counts (top 40) ---"]
    for tag, n in tag_counts.most_common(40):
        lines.append(f"  {tag:<45} x{n:>9}  depth~{depth_of.get(tag,'?')}")
    lines.append("")
    lines.append("--- Sample values (leaf elements) ---")
    for tag, vals in list(samples.items())[:40]:
        if not parent_child.get(tag):  # leaf
            lines.append(f"  {tag}: {vals}")
    lines.append("")
    lines.append("--- Structure (parent -> children) ---")
    for parent, children in list(parent_child.items())[:30]:
        kids = ", ".join(f"{c}({n})" for c, n in children.most_common(8))
        lines.append(f"  {parent} -> {kids}")
    return "\n".join(lines)


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    path = Path(sys.argv[1])
    for name, src in iter_xml_sources(path):
        print(inspect(src, name))
        print()


if __name__ == "__main__":
    main()
