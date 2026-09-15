"""Structural (not just text) extraction from PPTX/XLSX files — layouts,
fonts, colors, cell formatting — so a template's exact look can be captured
into a Skill (see ``skills.py::generate_skill_from_template``).

``doc_extract.py`` already extracts these formats, but ONLY plain text — every
font/color/layout/formatting detail is discarded there. This module is the
opposite: it skips body text (already covered by ``doc_extract``) and instead
summarizes the STYLE, as a compact, LLM-readable text block (not raw XML/JSON).

Uses ``python-pptx``/``openpyxl`` (auto-installed on first use via
``deps.ensure_module``, same pattern as ``doc_extract._pdf``'s pypdf).
"""
from __future__ import annotations

from .deps import ensure_module

MAX_SLIDES = 60
MAX_SHEETS = 20
MAX_ROWS_PER_SHEET = 100  # a style summary only needs a representative sample
_RUN_PREVIEW_CHARS = 40


def _run_style(font) -> str:
    bits: list[str] = []
    try:
        if font.name:
            bits.append(font.name)
        if font.size:
            bits.append(f"{font.size.pt:g}pt")
        if font.bold:
            bits.append("bold")
        if font.italic:
            bits.append("italic")
        rgb = None
        try:
            rgb = font.color.rgb if font.color and font.color.type else None
        except (AttributeError, KeyError, TypeError):
            pass  # theme color or no color set — not an RGB value
        if rgb:
            bits.append(f"#{rgb}")
    except Exception:  # noqa: BLE001 - a single malformed run must not abort extraction
        pass
    return ", ".join(bits) or "default style"


def extract_pptx_structure(path) -> str:
    """Per-slide layout name + each text run's font/size/bold/italic/color."""
    pptx_mod = ensure_module("pptx", "python-pptx")
    if pptx_mod is None:
        return ""
    try:
        prs = pptx_mod.Presentation(str(path))
        slides = list(prs.slides)
    except Exception:  # noqa: BLE001 - corrupt/unreadable file: degrade like a missing dependency
        return ""
    lines: list[str] = []
    for i, slide in enumerate(slides[:MAX_SLIDES], 1):
        layout_name = "?"
        try:
            if slide.slide_layout is not None:
                layout_name = slide.slide_layout.name
        except Exception:  # noqa: BLE001
            pass
        lines.append(f"Slide {i} (layout: {layout_name}):")
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False):
                continue
            ph_type = ""
            try:
                if shape.is_placeholder:
                    ph_type = f" [{shape.placeholder_format.type}]"
            except Exception:  # noqa: BLE001
                pass
            for para in shape.text_frame.paragraphs:
                for run in para.runs:
                    text = run.text.strip()
                    if not text:
                        continue
                    preview = text[:_RUN_PREVIEW_CHARS]
                    lines.append(f'  - {shape.shape_type}{ph_type} text "{preview}" — {_run_style(run.font)}')
    if len(slides) > MAX_SLIDES:
        lines.append(f"... ({len(slides) - MAX_SLIDES} more slides truncated)")
    return "\n".join(lines)


def extract_xlsx_structure(path) -> str:
    """Per-sheet header styling, column widths, merged cells, number formats."""
    openpyxl_mod = ensure_module("openpyxl", "openpyxl")
    if openpyxl_mod is None:
        return ""
    try:
        wb = openpyxl_mod.load_workbook(str(path), data_only=False)
    except Exception:  # noqa: BLE001 - corrupt/unreadable file: degrade like a missing dependency
        return ""
    lines: list[str] = []
    for ws in wb.worksheets[:MAX_SHEETS]:
        lines.append(f'Sheet "{ws.title}" ({ws.dimensions}):')
        try:
            ranges = list(ws.merged_cells.ranges)
        except Exception:  # noqa: BLE001
            ranges = []
        if ranges:
            lines.append(f"  merged cells: {', '.join(str(r) for r in ranges[:20])}")
        widths = [f"{letter}={dim.width:g}" for letter, dim in list(ws.column_dimensions.items())[:20]
                 if dim.width]
        if widths:
            lines.append(f"  column widths: {', '.join(widths)}")
        header_cells = []
        for cell in next(ws.iter_rows(min_row=1, max_row=1), []):
            if cell.value is None:
                continue
            bits = []
            try:
                if cell.font and cell.font.bold:
                    bits.append("bold")
                if cell.font and cell.font.name:
                    bits.append(cell.font.name)
                if cell.font and cell.font.size:
                    bits.append(f"{cell.font.size:g}pt")
                fg = getattr(getattr(cell, "fill", None), "fgColor", None)
                fill_rgb = getattr(fg, "rgb", None)
                if fill_rgb and fill_rgb not in ("00000000", None):
                    bits.append(f"fill #{fill_rgb}")
                if cell.number_format and cell.number_format != "General":
                    bits.append(f"format={cell.number_format}")
            except Exception:  # noqa: BLE001
                pass
            style = ", ".join(bits) or "default style"
            header_cells.append(f'{cell.coordinate}="{cell.value}" ({style})')
        if header_cells:
            lines.append("  header row: " + "; ".join(header_cells))
        formats: dict[str, str] = {}
        last_row = min(ws.max_row or 1, MAX_ROWS_PER_SHEET)
        if last_row >= 2:
            for row in ws.iter_rows(min_row=2, max_row=last_row):
                for cell in row:
                    if cell.value is not None and cell.number_format != "General":
                        formats.setdefault(cell.column_letter, cell.number_format)
        if formats:
            lines.append("  column number formats: " + ", ".join(f"{k}={v}" for k, v in formats.items()))
    return "\n".join(lines)


def extract_structure(path) -> str:
    """Dispatch by suffix. Returns "" for an unsupported type or a missing
    optional dependency — callers should fall back to plain text in that case."""
    suffix = str(path).lower().rsplit(".", 1)[-1] if "." in str(path) else ""
    if suffix == "pptx":
        return extract_pptx_structure(path)
    if suffix in ("xlsx", "xlsm"):
        return extract_xlsx_structure(path)
    return ""
