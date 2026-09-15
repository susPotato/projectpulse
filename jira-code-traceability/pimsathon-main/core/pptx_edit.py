"""Edit a .pptx in place — silently, without opening PowerPoint.

Uses ``python-pptx`` (pure Python) so it works in the background on any OS with
no Office/PowerPoint window. The deck is exposed as a marker-delimited document
— one block per shape (text box, picture, table, …) — carrying its type,
position/size and (for text shapes) its text, so a human OR the AI editor can
change any of them and we map each block straight back onto its shape:

    ### Slide 1 / Box 1
    type: text
    pos: 1.00, 0.50
    size: 8.00, 1.20
    text:
    Quarterly Review

    ### Slide 1 / Box 2
    type: picture
    pos: 1.00, 2.00
    size: 4.00, 3.00
    image: (keep — set a file path to replace this image)

Editable per block: ``text`` (text shapes), ``pos``/``size`` (inches, any
shape → move/resize) and, for pictures, ``image:`` set to a file path to
REPLACE the picture in place. Layout, other images and formatting are
preserved. Pure logic (no Qt) so it's directly unit-testable.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Tuple

_MARK = re.compile(r"^###\s*Slide\s*(\d+)\s*/\s*Box\s*(\d+)\s*$")
_FIELD = re.compile(r"^(type|pos|size|image|font|text)\s*:\s*(.*)$")
_EMU_PER_IN = 914400
_KEEP_PREFIX = "("   # image values like "(keep …)" mean "don't change"


def is_available() -> bool:
    try:
        import pptx  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _in(emu) -> float:
    return round((emu or 0) / _EMU_PER_IN, 2)


def _kind(shape) -> str:
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    try:
        if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
            return "picture"
        if shape.shape_type == MSO_SHAPE_TYPE.TABLE:
            return "table"
    except Exception:  # noqa: BLE001
        pass
    # Recognise placeholders (title / body / SLIDE NUMBER / footer / date …) so
    # the slide-number box is identifiable even though its value is a dynamic
    # field (not literal text).
    try:
        if shape.is_placeholder:
            pt = shape.placeholder_format.type
            role = pt.name.lower() if pt is not None else "placeholder"
            return f"placeholder:{role}"
    except Exception:  # noqa: BLE001
        pass
    return "text" if shape.has_text_frame else "shape"


def _slide_number_text(shape, slide_index: int) -> str:
    """The number a slide-number placeholder shows. Its run text is usually
    empty (the value is a ``<a:fld>`` field resolved at display time), so fall
    back to the slide's 1-based index so the number is still recognised."""
    txt = shape.text_frame.text.strip()
    if txt:
        return txt
    # Pull cached field text if present, else use the slide index.
    try:
        from pptx.oxml.ns import qn
        for fld in shape.text_frame._txBody.iter(qn("a:fld")):
            t = fld.find(qn("a:t"))
            if t is not None and t.text:
                return t.text
    except Exception:  # noqa: BLE001
        pass
    return str(slide_index)


def pptx_to_text(path: str) -> str:
    """Marker-delimited editable document for every shape in the deck."""
    from pptx import Presentation

    prs = Presentation(path)
    out: List[str] = []
    for si, slide in enumerate(prs.slides, 1):
        for bi, shape in enumerate(slide.shapes, 1):
            kind = _kind(shape)
            out.append(f"### Slide {si} / Box {bi}")
            out.append(f"type: {kind}")
            out.append(f"pos: {_in(shape.left):.2f}, {_in(shape.top):.2f}")
            out.append(f"size: {_in(shape.width):.2f}, {_in(shape.height):.2f}")
            if kind == "picture":
                out.append("image: (keep — set a file path to replace this image)")
            if kind == "placeholder:slide_number":
                # Surface the slide number explicitly (its text is a dynamic field).
                out.append(f"slide_number: {_slide_number_text(shape, si)}")
            if shape.has_text_frame:
                out.append("font: " + _read_font(shape))
                out.append("text:")
                out.append(shape.text_frame.text)
            out.append("")
    return ("\n".join(out).rstrip() + "\n") if out else ""


def _read_font(shape) -> str:
    """Summarise the first run's font as ``name=.. size=.. bold=.. color=RRGGBB``
    (blank fields when a property isn't set). ``color`` is the solid RGB hex, or
    empty when inherited/theme-based."""
    name = size = color = ""
    bold = 0
    try:
        paras = shape.text_frame.paragraphs
        run = None
        for p in paras:
            if p.runs:
                run = p.runs[0]
                break
        font = run.font if run is not None else paras[0].font
        name = font.name or ""
        if font.size is not None:
            size = str(int(font.size.pt))
        bold = 1 if font.bold else 0
        try:
            if font.color is not None and font.color.type is not None and font.color.rgb is not None:
                color = str(font.color.rgb)
        except Exception:  # noqa: BLE001 - theme/inherited colour has no rgb
            color = ""
    except Exception:  # noqa: BLE001
        pass
    return f"name={name} size={size} bold={bold} color={color}"


def _apply_font(shape, spec: str) -> bool:
    """Apply a ``name=.. size=.. bold=.. color=RRGGBB`` spec to every run in the
    text box (only the fields actually provided). Returns True if applied."""
    from pptx.dml.color import RGBColor
    from pptx.util import Pt

    attrs = {}
    for kv in (spec or "").split():
        if "=" in kv:
            k, v = kv.split("=", 1)
            attrs[k.strip()] = v.strip()
    if not attrs:
        return False
    applied = False
    for para in shape.text_frame.paragraphs:
        runs = list(para.runs)
        if not runs and para.text:
            runs = [para.add_run()]
        for run in runs:
            f = run.font
            if attrs.get("name"):
                f.name = attrs["name"]; applied = True
            if attrs.get("size"):
                try:
                    f.size = Pt(float(attrs["size"])); applied = True
                except ValueError:
                    pass
            if "bold" in attrs and attrs["bold"] != "":
                f.bold = attrs["bold"] in ("1", "true", "True", "yes"); applied = True
            if attrs.get("color"):
                try:
                    f.color.rgb = RGBColor.from_string(attrs["color"].lstrip("#").upper())
                    applied = True
                except Exception:  # noqa: BLE001 - bad hex → ignore
                    pass
    return applied


def _parse(text: str) -> Dict[Tuple[int, int], dict]:
    blocks: Dict[Tuple[int, int], dict] = {}
    cur: Tuple[int, int] | None = None
    fields: dict = {}
    in_text = False
    textbuf: List[str] = []

    def _flush():
        if cur is not None:
            if in_text:
                fields["text"] = "\n".join(textbuf).strip("\n")
            blocks[cur] = dict(fields)

    for line in (text or "").splitlines():
        m = _MARK.match(line)
        if m:
            _flush()
            cur = (int(m.group(1)), int(m.group(2)))
            fields = {}
            in_text = False
            textbuf = []
            continue
        if cur is None:
            continue
        if in_text:
            textbuf.append(line)
            continue
        fm = _FIELD.match(line)
        if fm:
            key, val = fm.group(1), fm.group(2)
            if key == "text":
                in_text = True
                textbuf = [val] if val else []
            else:
                fields[key] = val.strip()
    _flush()
    return blocks


def _pair(val: str):
    try:
        a, b = (x.strip() for x in val.split(",", 1))
        return float(a), float(b)
    except Exception:  # noqa: BLE001
        return None


def image_change_requested(text: str) -> bool:
    """True if the edited document asks to REPLACE any picture (an ``image:``
    field pointing at a real file) — used to confirm before touching images."""
    for f in _parse(text).values():
        img = (f.get("image") or "").strip()
        if img and not img.startswith(_KEEP_PREFIX) and os.path.isfile(img):
            return True
    return False


def apply_text_to_pptx(path: str, text: str) -> Tuple[int, bool]:
    """Write the edited document back onto the deck and save in place. Returns
    ``(shapes_changed, image_changed)``."""
    from pptx import Presentation
    from pptx.util import Emu

    blocks = _parse(text)
    prs = Presentation(path)
    changed = 0
    image_changed = False
    for si, slide in enumerate(prs.slides, 1):
        for bi, shape in enumerate(slide.shapes, 1):
            f = blocks.get((si, bi))
            if not f:
                continue
            touched = False
            # geometry (move / resize)
            pos = _pair(f.get("pos", "")) if "pos" in f else None
            if pos is not None:
                new_left, new_top = Emu(int(pos[0] * _EMU_PER_IN)), Emu(int(pos[1] * _EMU_PER_IN))
                if shape.left != new_left or shape.top != new_top:
                    shape.left, shape.top = new_left, new_top
                    touched = True
            size = _pair(f.get("size", "")) if "size" in f else None
            if size is not None:
                new_w, new_h = Emu(int(size[0] * _EMU_PER_IN)), Emu(int(size[1] * _EMU_PER_IN))
                if shape.width != new_w or shape.height != new_h:
                    shape.width, shape.height = new_w, new_h
                    touched = True
            # text
            if "text" in f and shape.has_text_frame and f["text"] != shape.text_frame.text:
                shape.text_frame.text = f["text"]
                touched = True
            # font (name / size / bold / colour) — applied AFTER text so it lands
            # on the new runs. This is how AI edit recognises & changes colour/font.
            if "font" in f and shape.has_text_frame:
                if _apply_font(shape, f["font"]):
                    touched = True
            # image replace (in place — keeps position/size)
            img = (f.get("image") or "").strip()
            if img and not img.startswith(_KEEP_PREFIX) and os.path.isfile(img):
                if _replace_picture(slide, shape, img):
                    image_changed = True
                    touched = True
            if touched:
                changed += 1
    prs.save(path)
    return changed, image_changed


def _blank_layout(prs):
    """A slide layout with no placeholders ('Blank'), so added boxes aren't
    fighting template placeholders. Falls back to a sensible index/last layout."""
    layouts = list(prs.slide_layouts)
    for lay in layouts:
        try:
            if len(lay.placeholders) == 0:
                return lay
        except Exception:  # noqa: BLE001
            pass
    if len(layouts) > 6:
        return layouts[6]
    return layouts[-1] if layouts else prs.slide_layouts[0]


def _add_box(slide, f: dict) -> None:
    """Add one shape (text box or picture) to ``slide`` from a parsed block."""
    from pptx.util import Emu

    pos = _pair(f.get("pos", "")) or (0.5, 0.5)
    size = _pair(f.get("size", "")) or (9.0, 1.2)
    left, top = Emu(int(pos[0] * _EMU_PER_IN)), Emu(int(pos[1] * _EMU_PER_IN))
    width, height = Emu(int(size[0] * _EMU_PER_IN)), Emu(int(size[1] * _EMU_PER_IN))
    kind = (f.get("type") or "text").lower()
    img = (f.get("image") or "").strip()
    has_real_img = bool(img) and not img.startswith(_KEEP_PREFIX) and os.path.isfile(img)
    if kind.startswith("picture") and has_real_img:
        try:
            slide.shapes.add_picture(img, left, top, width, height)
            return
        except Exception:  # noqa: BLE001 - bad image → fall through to a text box
            pass
    tb = slide.shapes.add_textbox(left, top, width, height)
    tf = tb.text_frame
    tf.word_wrap = True
    tf.text = f.get("text", "")
    if "font" in f:
        _apply_font(tb, f["font"])


def create_pptx_from_text(path: str, text: str) -> Tuple[int, int]:
    """Create a NEW .pptx at ``path`` from a marker-delimited document — the same
    '### Slide N / Box M' format ``pptx_to_text`` produces. When ``text`` has no
    markers, fall back to one text-box slide per blank-line-separated block so a
    plain outline still yields a valid deck. Returns ``(slides, boxes)``."""
    from pptx import Presentation

    prs = Presentation()
    blank = _blank_layout(prs)
    blocks = _parse(text)
    boxes = 0
    if blocks:
        slide_nums = sorted({si for si, _bi in blocks})
        slide_map = {si: prs.slides.add_slide(blank) for si in slide_nums}
        for (si, _bi), f in sorted(blocks.items()):
            _add_box(slide_map[si], f)
            boxes += 1
        prs.save(path)
        return len(slide_nums), boxes
    # Fallback: no markers → split on blank lines, one full-width text box per slide.
    chunks = [c.strip() for c in re.split(r"\n\s*\n", (text or "").strip()) if c.strip()]
    if not chunks:
        chunks = [""]
    for chunk in chunks:
        slide = prs.slides.add_slide(blank)
        _add_box(slide, {"type": "text", "pos": "0.5, 0.5", "size": "9.0, 6.0", "text": chunk})
        boxes += 1
    prs.save(path)
    return len(chunks), boxes


def _replace_picture(slide, shape, image_path: str) -> bool:
    """Swap a picture's image blob in place (geometry preserved). Best-effort;
    returns False for non-picture shapes or on failure."""
    try:
        blip = shape._element.blipFill.blip
    except Exception:  # noqa: BLE001 - not a picture / no blip
        return False
    try:
        image_part, rId = slide.part.get_or_add_image_part(image_path)
        blip.rEmbed = rId
        return True
    except Exception:  # noqa: BLE001
        return False
