"""One markdown walker, shared by every adapter that reads a docs tree.

Two adapters now read the same directory and ask different questions of it:
`featuremap_markdown` asks what the documents say about *code*, and
`progress_markdown` asks what they say about *work*. Both need the same
traversal — heading stack, table rows with the header suppressed, bullets,
and above all skipping fenced blocks so a `|` in a code sample is not read
as a table.

That traversal lives here once. Forked, it would be fixed in one copy and
not the other, and the copy that keeps the bug is the one nobody is looking
at. The walker deliberately makes no judgements: it emits blocks, and what
counts as a feature or a task is the caller's business.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")
TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
TABLE_RULE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
CODE_SPAN = re.compile(r"`([^`\n]+)`")
FENCE = re.compile(r"^\s*(```|~~~)")

# Leading section numbering a person wrote for navigation: "3.2.1.4".
LEADING_NUMBER = re.compile(r"^\s*\d+(?:\.\d+)*[.)]?\s*")
# Anything that is not a letter, digit, space or bracket: emoji, box-drawing,
# bullets. Stripped from labels only, never from claims or ids.
DECORATION = re.compile(r"[^\w\s()\[\]/.,:&'’\-—–]+", re.UNICODE)

KIND_HEADING = "heading"
KIND_ROW = "row"          # a table *body* row; headers and rules are dropped
KIND_BULLET = "bullet"
KIND_TEXT = "text"        # any other non-blank line outside a fence


@dataclass
class Block:
    """One addressable piece of a document."""

    kind: str
    line: int                       # 1-based, as an editor shows it
    raw: str                        # the line exactly as written
    text: str                       # heading title, bullet body, or the line
    heading: tuple[str, ...] = ()   # cleaned heading stack at this point
    level: int = 0                  # heading depth
    cells: tuple[str, ...] = ()     # row cells, stripped
    #: The header row of this row's table, so a consumer can key cells by
    #: the document's own column names instead of by position. Keeping the
    #: author's vocabulary is what lets an adapter record a column it does
    #: not understand rather than discarding it.
    header: tuple[str, ...] = ()


def clean_label(text: str) -> str:
    """Strip markdown, navigation numbering and decoration from a label."""
    text = CODE_SPAN.sub(r"\1", text)
    text = re.sub(r"\*\*|__|\*|_{2,}", "", text)
    text = LEADING_NUMBER.sub("", text)
    text = DECORATION.sub(" ", text)
    return re.sub(r"\s{2,}", " ", text).strip(" -–—:|")


def split_row(line: str) -> list[str]:
    inner = TABLE_ROW.match(line).group(1)
    return [c.strip() for c in inner.split("|")]


def walk(text: str) -> Iterator[Block]:
    """Blocks of one document, in reading order.

    Table handling is the fiddly part and is worth stating: the first
    `|...|` line of a table is its header and is *not* emitted, the
    `|---|---|` rule is not emitted, and every row after that is. A table
    with no rule row still loses its first line, which is right — that line
    is column names either way.
    """
    heading: list[str] = []
    in_fence = False
    in_table = False
    table_header_seen = False
    header: tuple[str, ...] = ()

    for n, line in enumerate(text.splitlines(), start=1):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        m = HEADING.match(line)
        if m:
            level = len(m.group(1))
            heading = heading[: level - 1]
            while len(heading) < level - 1:
                heading.append("")
            heading.append(clean_label(m.group(2)) or m.group(2).strip())
            in_table = False
            table_header_seen = False
            header = ()
            yield Block(kind=KIND_HEADING, line=n, raw=line,
                        text=heading[-1], heading=tuple(heading), level=level)
            continue

        if TABLE_RULE.match(line):
            in_table = True
            table_header_seen = True
            continue

        if TABLE_ROW.match(line):
            if not in_table and not table_header_seen:
                in_table = True          # header row: consumed, not emitted
                header = tuple(clean_label(c) for c in split_row(line))
                continue
            yield Block(kind=KIND_ROW, line=n, raw=line, text=line,
                        heading=tuple(heading), cells=tuple(split_row(line)),
                        header=header)
            continue

        in_table = False
        table_header_seen = False
        header = ()

        b = BULLET.match(line)
        if b:
            yield Block(kind=KIND_BULLET, line=n, raw=line, text=b.group(1),
                        heading=tuple(heading))
            continue

        if line.strip():
            yield Block(kind=KIND_TEXT, line=n, raw=line, text=line.strip(),
                        heading=tuple(heading))
