"""Build a REAL .xlsx from text content so an agent can CREATE Excel by calling
save_file/write_file('report.xlsx', <table text>).

A .xlsx is a binary ZIP package — writing the model's text straight to a .xlsx
corrupts it (the file won't open). This turns the content the model produces
(CSV / TSV / a Markdown table / JSON rows) into a genuine workbook via openpyxl
(already a dependency). Pure logic (no Qt) → unit-testable.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import List


def _openpyxl():
    """Import openpyxl, auto-installing it on first use if it isn't present —
    same self-healing path the Excel VIEWER and doc_style_extract use
    (``deps.ensure_module``). openpyxl is a declared dependency, so this only
    matters for a from-source run whose venv is missing it; a normal install /
    frozen build already bundles it. Returns the module or None."""
    try:
        from .deps import ensure_module
        return ensure_module("openpyxl", "openpyxl")
    except Exception:  # noqa: BLE001 - fall back to a plain import
        try:
            import openpyxl  # noqa: F401
            return openpyxl
        except Exception:  # noqa: BLE001
            return None


def is_available() -> bool:
    return _openpyxl() is not None


def _rows_from_text(content: str) -> List[list]:
    """Parse table content into a list of rows. Accepts JSON (list-of-lists or
    list-of-dicts), a Markdown table, or CSV/TSV (delimiter sniffed)."""
    content = content or ""
    s = content.strip()
    # JSON: [[...],[...]] or [{...},{...}] or {"rows"/"data": [...]}
    if s[:1] in ("[", "{"):
        try:
            data = json.loads(s)
            if isinstance(data, dict):
                data = data.get("rows") or data.get("data") or [data]
            rows: List[list] = []
            if isinstance(data, list):
                if data and isinstance(data[0], dict):
                    headers = list(dict.fromkeys(k for d in data if isinstance(d, dict) for k in d))
                    rows.append(headers)
                    for d in data:
                        rows.append([d.get(h, "") for h in headers] if isinstance(d, dict) else [d])
                else:
                    for r in data:
                        rows.append(list(r) if isinstance(r, (list, tuple)) else [r])
            if rows:
                return rows
        except (ValueError, TypeError):
            pass
    lines = [ln for ln in content.splitlines() if ln.strip()]
    # Markdown table: rows delimited by '|', a --- separator row skipped.
    if lines and lines[0].lstrip().startswith("|"):
        rows = []
        for ln in lines:
            body = ln.strip()
            if set(body) <= set("|-: "):     # separator row like |---|---|
                continue
            rows.append([c.strip() for c in body.strip("|").split("|")])
        if rows:
            return rows
    # CSV / TSV — sniff which delimiter dominates.
    delim = "\t" if content.count("\t") > content.count(",") else ","
    return list(csv.reader(io.StringIO(content), delimiter=delim))


def build_xlsx_from_text(path, content: str) -> bool:
    """Write a genuine .xlsx at ``path`` from CSV/TSV/Markdown-table/JSON
    ``content``. Returns True on success, False if openpyxl is unavailable or the
    write fails (caller can then fall back). Never raises."""
    openpyxl = _openpyxl()
    if openpyxl is None:
        return False
    try:
        rows = _rows_from_text(content)
        wb = openpyxl.Workbook()
        ws = wb.active
        for r in rows:
            ws.append(["" if v is None else v for v in r])
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        wb.save(str(p))
        return True
    except Exception:  # noqa: BLE001
        return False
