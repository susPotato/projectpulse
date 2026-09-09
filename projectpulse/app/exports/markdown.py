"""A `ReportDoc` as Markdown, for the email or the wiki page.

The cheapest of the three renderers and the most used, because it is the one
that needs no application to open. It is also the format a PM pastes into a
chat, which is why the tables are real Markdown tables rather than aligned
spaces: a monospace table that wraps in a chat client becomes unreadable, and a
pipe table degrades to something still parseable.

Formats nothing. Every value arriving here is already a string - see
`document.py`. This module's only decisions are about punctuation.
"""

from __future__ import annotations

from app.exports.document import Block, ReportDoc

#: What separates blocks. Markdown needs the blank line: two paragraphs on
#: consecutive lines render as one.
_GAP = "\n\n"


def _escape_cell(text: str) -> str:
    """A pipe inside a cell ends the cell, so it has to be escaped.

    Cells here carry task labels and scenario summaries, and a scenario summary
    joins its moves with a separator we control - but a task label comes from a
    PM's spreadsheet and can contain anything at all.
    """
    return text.replace("|", "\\|").replace("\n", " ")


def _render_block(block: Block) -> str:
    if block.kind == "heading":
        return f"{'#' * (block.level + 1)} {block.text}"

    if block.kind == "paragraph":
        if block.label and block.text:
            return f"**{block.label.strip()}** {block.text}"
        if block.label:
            return f"**{block.label.strip()}**"
        return block.text

    if block.kind == "note":
        # A blockquote, because these are the asides a reader is allowed to skip
        # and must not mistake for a finding.
        return f"> {block.text}"

    if block.kind == "bullets":
        return "\n".join(f"- {item}" for item in block.items)

    if block.kind == "table":
        header = "| " + " | ".join(_escape_cell(c) for c in block.columns) + " |"
        divider = "| " + " | ".join("---" for _ in block.columns) + " |"
        body = [
            "| " + " | ".join(_escape_cell(cell) for cell in row) + " |"
            for row in block.rows
        ]
        table = "\n".join([header, divider, *body])
        return f"{block.text}\n\n{table}" if block.text else table

    return block.text  # pragma: no cover - BlockKind is closed


def render_markdown(doc: ReportDoc) -> str:
    """The whole report as one Markdown string."""
    parts: list[str] = [f"# {doc.title}"]
    parts.extend(_render_block(block) for block in doc.preamble)

    for section in doc.sections:
        parts.append(f"## {section.title}")
        parts.extend(_render_block(block) for block in section.blocks)

    # A trailing newline: a file without one appends to the next thing a shell
    # prints, and `sync report` writes this straight to disk.
    return _GAP.join(part for part in parts if part) + "\n"


def markdown_bytes(doc: ReportDoc) -> bytes:
    """UTF-8, because task labels and owner names are not ASCII.

    The CLI is held to ASCII (a judge's console may be cp932) but a *file* is
    not - it is opened by an editor that reads the encoding.
    """
    return render_markdown(doc).encode("utf-8")
