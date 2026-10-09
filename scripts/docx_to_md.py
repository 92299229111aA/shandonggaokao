"""One-off: convert the client-supplied handoff DOCX into docs/handoff/spec.md.

Keeps table order interleaved with paragraphs so the spec stays readable in-repo.
Run: python scripts/docx_to_md.py <in.docx> <out.md>
"""

from __future__ import annotations

import sys
from pathlib import Path

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph


def iter_blocks(document: docx.Document):
    body = document.element.body
    for child in body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, document)
        elif child.tag.endswith("}tbl"):
            yield Table(child, document)


def para_to_md(p: Paragraph) -> str:
    text = p.text.strip()
    if not text:
        return ""
    style = p.style.name if p.style is not None else "Normal"
    if style == "Heading 1":
        return f"## {text}"
    if style == "Heading 2":
        return f"### {text}"
    if style == "Heading 3":
        return f"#### {text}"
    if style == "List Bullet":
        return f"- {text}"
    if style == "List Number":
        return f"1. {text}"
    return text


def table_to_md(t: Table) -> str:
    rows = [[c.text.strip().replace("\n", " ") for c in r.cells] for r in t.rows]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    for r in rows[1:]:
        out.append("| " + " | ".join(c.replace("|", "\\|") for c in r) + " |")
    return "\n".join(out)


def main() -> int:
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    document = docx.Document(str(src))
    parts: list[str] = []
    for block in iter_blocks(document):
        chunk = para_to_md(block) if isinstance(block, Paragraph) else table_to_md(block)
        if chunk:
            parts.append(chunk)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    print(f"wrote {dst} ({len(parts)} blocks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
