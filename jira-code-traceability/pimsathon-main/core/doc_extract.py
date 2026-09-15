"""Best-effort text extraction from documents so the agent can actually read
attachments in the Cowork and Code tabs.

Office Open XML (.docx/.xlsx/.pptx) and OpenDocument (.odt/.ods/.odp) are just
ZIP archives of XML, so they are parsed with the standard library — no external
packages required. PDF uses pypdf/PyPDF2 when available; anything else (legacy
.doc/.xls/.ppt, scanned PDF, unknown binary) falls back to a headless LibreOffice
conversion when LibreOffice is installed.
"""
from __future__ import annotations

import html
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

MAX_ROWS = 2000  # per spreadsheet sheet, to keep extraction bounded

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".tif", ".svg", ".ico"}


def is_image(path) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTS


def is_zip(path) -> bool:
    """A .zip archive (by suffix OR by magic bytes). Office files are ALSO zips,
    so callers must check office suffixes first when they mean 'a plain archive'."""
    p = Path(path)
    if p.suffix.lower() == ".zip":
        return True
    try:
        with open(p, "rb") as f:
            return f.read(4) == b"PK\x03\x04" and p.suffix.lower() not in (
                ".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".odt", ".ods", ".odp")
    except OSError:
        return False


def extract_archive(path, dest_dir, max_files: int = 300,
                    max_total_bytes: int = 300_000_000) -> list[Path]:
    """Safely extract a .zip into ``dest_dir`` and return the extracted file paths.

    Path-traversal guarded (entries escaping ``dest_dir`` are skipped) and capped
    by file count + total uncompressed size (zip-bomb guard). Never raises —
    returns whatever it managed to extract."""
    dest = Path(dest_dir)
    out: list[Path] = []
    try:
        dest.mkdir(parents=True, exist_ok=True)
        root = dest.resolve()
        with zipfile.ZipFile(path) as zf:
            total = 0
            for info in zf.infolist():
                if info.is_dir() or len(out) >= max_files:
                    continue
                target = (dest / info.filename).resolve()
                if os.path.commonpath([str(target), str(root)]) != str(root):
                    continue                      # entry tries to escape → skip
                total += info.file_size
                if total > max_total_bytes:
                    break
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                out.append(target)
    except Exception:  # noqa: BLE001 - never break the caller
        pass
    return out

# File types considered valid input data when auto-scanning a workspace/output
# folder (Cowork's "[Workspace files]"/"[Project files]" and Schedule Task's
# linked-project-folder scan both filter on this).
INPUT_EXTS = {
    ".csv", ".json", ".txt", ".md", ".log", ".xml", ".yaml", ".yml",
    ".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".pdf", ".odt", ".ods", ".odp",
    ".rtf", ".tsv",
}


def find_input_files(folder: Path, exts: set[str] | None = None,
                     max_files: int = 0) -> tuple[list[Path], int]:
    """Recursively list readable files under ``folder``, any depth of
    sub-folders included, so a linked folder's nested files are found too —
    not just the ones sitting directly at its top level.

    Skips dot-files AND anything inside a dot-directory (internal sandbox
    scratch areas like ``.turns``/``.scratch`` must never be picked back up as
    "input"). Returns ``(files, total_matched)``: ``files`` is sorted and
    capped at ``max_files`` (0 = unlimited), ``total_matched`` is the count
    before that cap, so a caller can report how many were skipped."""
    exts = exts or INPUT_EXTS
    try:
        matched = sorted(
            f for f in folder.rglob("*")
            if f.is_file()
            and not any(part.startswith(".") for part in f.relative_to(folder).parts)
            and f.suffix.lower() in exts
        )
    except OSError:
        return [], 0
    files = matched if max_files <= 0 else matched[:max_files]
    return files, len(matched)


def find_soffice() -> str | None:
    """Locate the LibreOffice launcher (env override → PATH → common installs)."""
    env = os.environ.get("SOFFICE_PATH")
    if env and Path(env).exists():
        return env
    for name in ("soffice", "soffice.exe", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for c in (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        "/usr/bin/soffice",
        "/usr/bin/libreoffice",
    ):
        if Path(c).exists():
            return c
    return None


def extract_text(path, progress=None) -> tuple[str | None, str]:
    """Return ``(text, note)``. ``text`` is None when nothing readable could be
    extracted (``note`` then explains why).

    ``progress``, if given, is called as ``progress(page, total)`` while a
    multi-page PDF is being read, so the UI can show e.g. "page 12/40"."""
    p = Path(path)
    suffix = p.suffix.lower()
    try:
        if suffix in (".docx", ".docm"):
            return _docx(p), ""
        if suffix in (".xlsx", ".xlsm"):
            return _xlsx(p), ""
        if suffix == ".pptx":
            return _pptx(p), ""
        if suffix in (".odt", ".ods", ".odp"):
            return _odf(p), ""
        if suffix == ".pdf":
            return _pdf(p, progress)
        if suffix in (".doc", ".xls", ".ppt", ".rtf"):
            return _soffice_to_text(p)
        raw = p.read_bytes()
        if b"\x00" in raw[:8192]:
            text, note = _soffice_to_text(p)
            return (text, note) if text is not None else (None, "binary file — content not extracted")
        return raw.decode("utf-8", errors="replace"), ""
    except Exception as exc:  # noqa: BLE001 - never break prompt building
        text, _ = _soffice_to_text(p)
        if text is not None:
            return text, ""
        return None, f"could not read ({exc})"


# --------------------------------------------------------------------------
# Office Open XML (docx / xlsx / pptx)
# --------------------------------------------------------------------------
def _docx(p: Path) -> str:
    with zipfile.ZipFile(p) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    out: list[str] = []
    for m in re.finditer(r"<w:t[^>]*>(.*?)</w:t>|<w:tab/>|<w:br\s*/>|</w:p>", xml, re.DOTALL):
        token = m.group(0)
        if token.startswith("<w:t"):
            out.append(html.unescape(m.group(1)))
        elif token == "<w:tab/>":
            out.append("\t")
        else:  # <w:br/> or </w:p>
            out.append("\n")
    return "".join(out).strip()


def _pptx(p: Path) -> str:
    out: list[str] = []
    with zipfile.ZipFile(p) as z:
        slides = [n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)]
        slides.sort(key=lambda n: int(re.search(r"(\d+)", n).group(1)))
        for i, name in enumerate(slides, 1):
            xml = z.read(name).decode("utf-8", "replace")
            texts = [html.unescape(t) for t in re.findall(r"<a:t>(.*?)</a:t>", xml, re.DOTALL)]
            if texts:
                out.append(f"--- Slide {i} ---\n" + "\n".join(texts))
    return "\n\n".join(out).strip()


def _xlsx(p: Path) -> str:
    with zipfile.ZipFile(p) as z:
        names = z.namelist()
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            sx = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
            for si in re.findall(r"<si>(.*?)</si>", sx, re.DOTALL):
                shared.append("".join(
                    html.unescape(t) for t in re.findall(r"<t[^>]*>(.*?)</t>", si, re.DOTALL)))
        sheets = [n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n)]
        sheets.sort(key=lambda n: int(re.search(r"(\d+)", n).group(1)))
        out: list[str] = []
        for idx, sheet in enumerate(sheets, 1):
            xml = z.read(sheet).decode("utf-8", "replace")
            rows_out: list[str] = []
            for row in re.findall(r"<row[^>]*>(.*?)</row>", xml, re.DOTALL)[:MAX_ROWS]:
                cells: list[str] = []
                for cm in re.finditer(r"<c\b([^>]*)(?:/>|>(.*?)</c>)", row, re.DOTALL):
                    attrs, body = cm.group(1) or "", cm.group(2) or ""
                    tmatch = re.search(r'\bt="([^"]+)"', attrs)
                    ctype = tmatch.group(1) if tmatch else ""
                    vmatch = re.search(r"<v>(.*?)</v>", body, re.DOTALL)
                    if vmatch:
                        val = html.unescape(vmatch.group(1))
                        if ctype == "s":
                            try:
                                val = shared[int(val)]
                            except (ValueError, IndexError):
                                val = ""
                    else:
                        inline = re.findall(r"<t[^>]*>(.*?)</t>", body, re.DOTALL)
                        val = "".join(html.unescape(x) for x in inline)
                    cells.append(val)
                if any(c.strip() for c in cells):
                    rows_out.append("\t".join(cells))
            if rows_out:
                out.append(f"--- Sheet {idx} ---\n" + "\n".join(rows_out))
    return "\n\n".join(out).strip()


# --------------------------------------------------------------------------
# OpenDocument (odt / ods / odp)
# --------------------------------------------------------------------------
def _odf(p: Path) -> str:
    with zipfile.ZipFile(p) as z:
        xml = z.read("content.xml").decode("utf-8", "replace")
    xml = re.sub(r"<text:line-break\s*/>", "\n", xml)
    xml = re.sub(r"<text:tab\s*/>", "\t", xml)
    xml = re.sub(r"</text:p>|</text:h>|</table:table-row>", "\n", xml)
    xml = re.sub(r"</table:table-cell>", "\t", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    return html.unescape(text).strip()


# --------------------------------------------------------------------------
# PDF + LibreOffice fallback
# --------------------------------------------------------------------------
def _pdf(p: Path, progress=None) -> tuple[str | None, str]:
    from .deps import ensure_module

    # Auto-install pypdf when missing (no manual install needed); fall back to
    # PyPDF2, then to a headless LibreOffice conversion.
    tried_reader = False
    for module, package in (("pypdf", "pypdf"), ("PyPDF2", "PyPDF2")):
        reader_mod = ensure_module(module, package)
        if reader_mod is None:
            continue
        try:
            pages = list(reader_mod.PdfReader(str(p)).pages)
            tried_reader = True
            total = len(pages)
            parts: list[str] = []
            for i, pg in enumerate(pages, 1):
                parts.append(pg.extract_text() or "")
                if progress is not None:
                    try:
                        progress(i, total)
                    except Exception:  # noqa: BLE001 - a UI callback must never break extraction
                        pass
            text = "\n".join(parts).strip()
            if text:
                return text, ""
        except Exception:  # noqa: BLE001
            continue
    text, _ = _soffice_to_text(p)
    if text:
        return text, ""
    if tried_reader:
        # The PDF opened fine but no page yielded a text layer — almost always a
        # scanned/image-only (or digitally-signed-and-flattened) PDF. Say so
        # explicitly instead of the generic message, since this is the case an
        # end user actually hits and wonders why nothing came through.
        return None, ("PDF appears to be scanned/image-based (no extractable text layer); "
                       "OCR is not available in this app yet, and LibreOffice was not found "
                       "for a fallback conversion")
    return None, "PDF text could not be extracted (no internet to fetch pypdf, and LibreOffice not found)"


def convert_to_pdf(path, out_dir) -> str | None:
    """Convert an office document (ppt/pptx/doc/docx/xls/xlsx/odt/...) to PDF so
    it can be RENDERED (not just text-extracted). Tries headless LibreOffice
    first; if LibreOffice is missing/fails, falls back to driving the installed
    **Microsoft Office** app silently via COM (Windows only). Returns the output
    ``.pdf`` path or None. Best-effort; never raises."""
    src = Path(path)
    out = Path(out_dir)
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    pdf = out / (src.stem + ".pdf")

    soffice = find_soffice()
    if soffice:
        import subprocess
        try:
            subprocess.run(
                [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(out), str(src)],
                capture_output=True, timeout=120,
            )
        except Exception:  # noqa: BLE001
            pass
        if pdf.exists():
            return str(pdf)

    # No LibreOffice (or it failed) → drive MS Office silently via COM.
    return _office_com_to_pdf(src, pdf)


def _office_com_to_pdf(src: Path, pdf: Path) -> str | None:
    """Convert via the installed Microsoft Office app (PowerPoint/Word/Excel)
    using COM automation, run in the background with no visible window. Windows
    + Office only; returns None otherwise or on any failure."""
    if sys.platform != "win32":
        return None
    suffix = src.suffix.lower()
    try:
        import pythoncom
        import win32com.client as win32
    except Exception:  # noqa: BLE001 - pywin32 not installed
        return None

    PP_SAVE_PDF, WD_FMT_PDF, XL_TYPE_PDF = 32, 17, 0
    pythoncom.CoInitialize()
    app = None
    try:
        if suffix in (".ppt", ".pptx", ".odp"):
            app = win32.Dispatch("PowerPoint.Application")
            pres = app.Presentations.Open(str(src), WithWindow=False, ReadOnly=True)
            pres.SaveAs(str(pdf), PP_SAVE_PDF)
            pres.Close()
        elif suffix in (".doc", ".docx", ".rtf", ".odt"):
            app = win32.Dispatch("Word.Application")
            app.Visible = False
            doc = app.Documents.Open(str(src), ReadOnly=True)
            doc.SaveAs(str(pdf), FileFormat=WD_FMT_PDF)
            doc.Close(False)
        elif suffix in (".xls", ".xlsx", ".ods", ".csv"):
            app = win32.Dispatch("Excel.Application")
            app.Visible = False
            wb = app.Workbooks.Open(str(src), ReadOnly=True)
            wb.ExportAsFixedFormat(XL_TYPE_PDF, str(pdf))
            wb.Close(False)
        else:
            return None
    except Exception:  # noqa: BLE001 - Office not installed / automation blocked
        return None
    finally:
        try:
            if app is not None:
                app.Quit()
        except Exception:  # noqa: BLE001
            pass
        pythoncom.CoUninitialize()
    return str(pdf) if pdf.exists() else None


def _soffice_to_text(p: Path) -> tuple[str | None, str]:
    soffice = find_soffice()
    if not soffice:
        return None, "no extractor available (install LibreOffice)"
    import subprocess
    import tempfile

    fmt = "csv" if p.suffix.lower() in (".xls", ".xlsx", ".ods", ".csv") else "txt:Text"
    try:
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(
                [soffice, "--headless", "--convert-to", fmt, "--outdir", td, str(p)],
                capture_output=True, timeout=90,
            )
            for f in sorted(Path(td).glob("*")):
                if f.suffix.lower() in (".txt", ".csv"):
                    return f.read_text(encoding="utf-8", errors="replace").strip(), ""
    except Exception as exc:  # noqa: BLE001
        return None, f"LibreOffice extraction failed ({exc})"
    return None, "LibreOffice produced no text output"
