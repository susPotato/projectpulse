"""Custom skills for the agent.

A *skill* is a named, reusable instruction block the user can add and toggle on.
Enabled skills are injected into the agent's system prompt so the agent
follows them (e.g. "luôn viết test", "tuân thủ coding style của team").

Stored as one JSON file per skill under ``~/.cowork_local/skills/``::

    {"name": str, "description": str, "instructions": str, "enabled": bool}
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List

from ..config import CONFIG_DIR

SKILLS_DIR = CONFIG_DIR / "skills"

# Bundled, always-on default skills live here (none ship by default — drop a
# ``.skill`` file in here to add one). They are loaded straight from the
# package by builtin_skills() — never copied into the user's editable skills
# folder — so they always apply to the agent but never appear in (and cannot
# be edited / disabled / deleted from) the Skills manager.
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "skill_templates"

# Bundled BUILT-IN skill LIBRARY (ships with the app). Unlike TEMPLATES_DIR's
# always-on/hidden skills, these are SEEDED once into the user's editable skills
# folder on first run — so they appear in the Skill Manager (visible, toggleable,
# exportable) like any other skill, but arrive out-of-the-box. See
# seed_library_skills(); a user-deleted one is not re-seeded (tracked in config).
LIBRARY_DIR = Path(__file__).resolve().parent.parent / "skill_library"


@dataclass
class Skill:
    name: str
    description: str = ""
    instructions: str = ""
    enabled: bool = True

    @property
    def slug(self) -> str:
        keep = "-_"
        s = "".join(c if (c.isalnum() or c in keep) else "-" for c in self.name.strip().lower())
        return "-".join(filter(None, s.split("-"))) or "skill"


def skills_dir() -> Path:
    return SKILLS_DIR


def _decode_best_effort(raw: bytes) -> str:
    """Decode skill-file bytes trying several encodings, so a file saved as
    UTF-16 or Windows-1252 (common from Notepad/Word) imports as real text
    instead of ``�`` replacement boxes (the "font error" users hit). A BOM,
    if present, is consumed by the matching codec."""
    import codecs

    # UTF-16 ONLY when a BOM says so — without a BOM, Python's utf-16 codec
    # happily "decodes" any even-length bytes into CJK garbage, which would
    # shadow the legitimate encodings tried after it.
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            pass
    try:
        return raw.decode("utf-8-sig")   # plain UTF-8 too; strips a BOM if present
    except UnicodeDecodeError:
        pass
    # UTF-16 saved WITHOUT a BOM (some editors do): text files never contain
    # NUL bytes otherwise, so a large share of NULs is a reliable tell — and
    # their position (odd/even offsets) says which endianness.
    if raw and raw.count(0) > len(raw) // 4:
        nul_even = raw[::2].count(0)
        nul_odd = raw[1::2].count(0)
        try:
            return raw.decode("utf-16-be" if nul_even > nul_odd else "utf-16-le")
        except UnicodeDecodeError:
            pass
    # Last resort: cp1252 covers most Western text; latin-1 never raises.
    try:
        return raw.decode("cp1252")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")


def _parse_frontmatter(text: str):
    """Split a leading ``---`` YAML frontmatter block (as used by Claude's
    Agent Skills ``SKILL.md``) into a ``{key: value}`` dict + the body after
    it. Only simple ``key: value`` scalars are read (enough for name/
    description); returns ``({}, text)`` when there's no valid block."""
    stripped = text.lstrip("﻿\r\n ")
    if not stripped.startswith("---"):
        return {}, text
    lines = stripped.splitlines()
    meta, body_start = {}, None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            body_start = i + 1
            break
        if ":" in lines[i] and not lines[i].lstrip().startswith("#"):
            k, _, v = lines[i].partition(":")
            meta[k.strip().lower()] = v.strip().strip("\"'")
    if body_start is None:
        return {}, text   # no closing --- → not real frontmatter
    return meta, "\n".join(lines[body_start:]).strip()


def _skill_from_text(text: str, suffix: str, stem: str, folder: str = "") -> Skill:
    """Parse decoded skill text into a Skill. ``suffix``/``stem``/``folder``
    describe the source (a real file or a zip member) so the name can fall
    back sensibly: explicit name > first heading > containing folder > stem."""
    if suffix.lower() == ".json" or text.lstrip().startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None  # fall through to text parsing
        # Only a JSON OBJECT is a skill payload — a stray array/scalar file
        # must not crash skill listing (fall back to plain-text parsing).
        if isinstance(data, dict):
            return Skill(
                name=data.get("name", stem),
                description=data.get("description", ""),
                instructions=data.get("instructions") or data.get("content", ""),
                # Skills are OFF by default — the user opts in (ticks) to use one.
                enabled=bool(data.get("enabled", False)),
            )
    meta, body = _parse_frontmatter(text)
    name = meta.get("name") or ""
    description = meta.get("description", "")
    if not meta:
        body = text.strip()
        for line in text.splitlines():
            if line.strip().startswith("#"):
                name = line.lstrip("#").strip()
                break
    # No explicit name from frontmatter/heading: a Claude SKILL.md is usually
    # in a folder named after the skill, so prefer that over the generic stem.
    if not name:
        name = (folder if stem.lower() == "skill" else "") or stem or folder
    return Skill(name=name, description=description, instructions=body, enabled=False)


def _load_skill_file(path: Path) -> Skill | None:
    """Load a skill from a file. Supports JSON (.json or JSON-bodied .skill),
    and Markdown/plain text (.skill/.md/.txt/.yaml) — including Claude Agent
    Skill ``SKILL.md`` files, whose ``---`` frontmatter supplies the name and
    description; otherwise the first heading is the name. Encoding is
    auto-detected (see ``_decode_best_effort``)."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if raw[:4] == b"PK\x03\x04":
        # A zip-formatted .skill dropped straight into the skills folder —
        # same handling as importing the package via the Import button.
        return _load_skill_from_zip(path)
    return _skill_from_text(_decode_best_effort(raw), path.suffix, path.stem, path.parent.name)


def builtin_skills() -> List[Skill]:
    """Bundled default skills that always run but stay hidden from the manager.

    Loaded directly from the packaged ``skill_templates`` folder and forced
    ``enabled`` — the user can neither see, edit, disable nor delete them. Their
    instructions are always injected into the agent by active_skills_text()."""
    out: List[Skill] = []
    if not TEMPLATES_DIR.exists():
        return out
    for path in sorted(TEMPLATES_DIR.glob("*.skill")):
        skill = _load_skill_file(path)
        if skill is not None:
            skill.enabled = True  # built-ins are always on
            out.append(skill)
    return out


def _builtin_slugs() -> set[str]:
    return {s.slug for s in builtin_skills()}


def library_skills() -> List[Skill]:
    """The bundled built-in skill LIBRARY (from the packaged ``skill_library``
    folder). These are seeded into the user's editable skills on first run — see
    seed_library_skills() — so they show up in the Skill Manager. Loaded here
    disabled (opt-in), as normal skills."""
    out: List[Skill] = []
    if not LIBRARY_DIR.exists():
        return out
    for path in sorted(LIBRARY_DIR.glob("*.skill")) + sorted(LIBRARY_DIR.glob("*.md")):
        skill = _load_skill_file(path)
        if skill is not None and skill.name:
            skill.enabled = False
            out.append(skill)
    return out


def _lib_hash(skill: Skill) -> str:
    """Short content fingerprint of a bundled skill (name + instructions) — used
    to detect when the SHIPPED version changed so the seeded copy can be refreshed."""
    import hashlib
    raw = (skill.name + "\x00" + skill.instructions).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:10]


def seed_library_skills(seeded_tags=None, directory: Path | None = None) -> List[str]:
    """Seed/refresh the bundled library skills in the user's Skill Manager, and
    return the FULL list of version tags (``slug@hash``) to persist.

    Per skill, content-versioned so updates ship without clobbering user edits:
      * brand-new skill → seeded;
      * SHIPPED content changed (tag not recorded) AND the file is still present →
        (over)written, delivering the update;
      * unchanged (tag already recorded) → left as-is (user edits preserved);
      * user deleted it (a prior tag for this slug was recorded but the file is
        gone) → respected, not recreated.
    """
    directory = directory or SKILLS_DIR
    already = set(seeded_tags or [])
    tags: List[str] = []
    for skill in library_skills():
        slug = skill.slug
        tag = f"{slug}@{_lib_hash(skill)}"
        tags.append(tag)                 # record the current shipped version
        if tag in already:
            continue                     # this exact version already handled
        exists = (directory / f"{slug}.json").exists() or (directory / f"{slug}.skill").exists()
        prior = any(t.split("@", 1)[0] == slug for t in already)
        if prior and not exists:
            continue                     # previously seeded then deleted → respect it
        save_skill(skill, directory)     # first seed OR shipped-content upgrade
    return tags


def export_skill_md(skill: Skill, out_path) -> Path:
    """Write a skill to a Markdown ``.md`` file with YAML frontmatter (name +
    description) followed by the instructions body — the same shape a Claude
    Agent ``SKILL.md`` uses, so it round-trips back through import_skill_file."""
    p = Path(out_path)
    if p.suffix.lower() != ".md":
        p = p.with_suffix(".md")
    desc = (skill.description or "").replace("\n", " ").strip()
    lines = ["---", f"name: {skill.name}"]
    if desc:
        lines.append(f"description: {desc}")
    lines += ["---", "", f"# {skill.name}", ""]
    if desc:
        lines += [f"> {desc}", ""]
    lines.append((skill.instructions or "").strip())
    p.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return p


def list_skills(directory: Path | None = None) -> List[Skill]:
    """User-managed skills shown in the Skills manager.

    Built-in default skills are deliberately excluded so they never appear in —
    nor can be toggled from — any management UI; see builtin_skills()."""
    directory = directory or SKILLS_DIR
    if not directory.exists():
        return []
    builtin = _builtin_slugs()
    out: List[Skill] = []
    seen: set[str] = set()
    for path in sorted(directory.glob("*.json")) + sorted(directory.glob("*.skill")):
        if path.name in seen:
            continue
        seen.add(path.name)
        skill = _load_skill_file(path)
        if skill is not None and skill.slug not in builtin:
            out.append(skill)
    return out


def save_skill(skill: Skill, directory: Path | None = None, old_name: str = "") -> Path:
    directory = directory or SKILLS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    if old_name and old_name != skill.name:
        delete_skill(old_name, directory)
    path = directory / f"{skill.slug}.json"
    path.write_text(json.dumps(asdict(skill), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def delete_skill(name: str, directory: Path | None = None) -> None:
    directory = directory or SKILLS_DIR
    path = directory / f"{Skill(name=name).slug}.json"
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def _load_skill_from_zip(path: Path) -> "Skill | None":
    """Import a Claude Agent Skill distributed as a .zip: find the SKILL.md
    (or any .md/.skill/.json/.txt) inside and parse it, reading the member
    bytes directly (no temp extraction). The name falls back to the member's
    own folder inside the archive, else the .zip filename."""
    import zipfile

    def _rank(n: str) -> int:
        low = n.lower()
        if low.endswith("skill.md"):
            return 0
        if low.endswith((".md", ".skill")):
            return 1
        if low.endswith(".json"):
            return 2
        return 3

    try:
        with zipfile.ZipFile(path) as zf:
            candidates = sorted(
                (n for n in zf.namelist()
                 if not n.endswith("/") and n.lower().endswith((".md", ".skill", ".json", ".txt"))),
                key=_rank)
            if not candidates:
                return None
            member = candidates[0]
            text = _decode_best_effort(zf.read(member))
    except (zipfile.BadZipFile, OSError, KeyError):
        return None
    mp = Path(member)
    folder = mp.parent.name or path.stem
    return _skill_from_text(text, mp.suffix, mp.stem, folder)


def _is_zip_file(path: Path) -> bool:
    """A Claude Agent Skill package is a ZIP whatever its extension — official
    exports ship as ``<name>.skill`` which IS a zip archive. Sniff the magic
    bytes instead of trusting the suffix, or the archive gets decoded as text
    and the skill imports as binary mojibake ("lỗi font")."""
    try:
        with path.open("rb") as f:
            return f.read(4) == b"PK\x03\x04"
    except OSError:
        return False


def import_skill_file(path, directory: Path | None = None) -> Skill:
    """Import an external skill and save it. Supports single files (.json,
    .skill, .md, .txt, .yaml — including Claude ``SKILL.md`` with frontmatter)
    and Claude Agent Skill packages (.zip, or a zip-formatted .skill)."""
    directory = directory or SKILLS_DIR
    p = Path(path)
    if p.suffix.lower() == ".zip" or _is_zip_file(p):
        skill = _load_skill_from_zip(p)
    else:
        skill = _load_skill_file(p)
    if skill is None or not skill.name:
        raise ValueError(f"Could not read a skill from: {path}")
    save_skill(skill, directory)
    return skill


def prune_seeded_builtins(directory: Path | None = None) -> None:
    """Remove previously-seeded copies of built-in skills from the user's folder.

    Earlier versions copied the bundled ``.skill`` templates (e.g. 'HTML Document
    Builder') into the user's skills dir on first run. Built-ins are now loaded
    straight from the package (see builtin_skills()) and must stay hidden from the
    Skills manager, so any leftover seeded copy is deleted here on startup. Only
    ``.skill`` files are touched — user-created / imported skills are always saved
    as ``.json`` — so a user's own skills are never removed."""
    directory = directory or SKILLS_DIR
    if not directory.exists():
        return
    builtin = _builtin_slugs()
    try:
        for path in directory.glob("*.skill"):
            skill = _load_skill_file(path)
            if skill is not None and skill.slug in builtin:
                path.unlink()
    except OSError:
        pass


def active_skills_text(directory: Path | None = None) -> str:
    """Combine built-in (always-on) + enabled user skills into one block (or '').

    Built-ins come first so any bundled default capabilities always apply,
    even though they never show up in the Skills manager."""
    directory = directory or SKILLS_DIR
    skills = builtin_skills() + [s for s in list_skills(directory) if s.enabled]
    parts = [
        f"## Skill: {s.name}\n{s.instructions.strip()}"
        for s in skills
        if s.instructions.strip()
    ]
    return "\n\n".join(parts)


def generate_skill(provider, prompt: str, cancel=None) -> "Skill":
    """Auto-generate a FULL skill (name + description + instructions) from a free-text
    description. Asks the model for a JSON object; if that doesn't parse, falls back
    to the instructions-only generator plus a name/description derived from the
    prompt. Raises ValueError only when nothing usable could be produced, so the
    caller can show a friendly message."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("empty description")
    messages = [
        {"role": "system", "content":
            "You design reusable agent skills. From the user's description, output a SINGLE JSON "
            "object with EXACTLY these keys: \"name\" (a short Title Case name), \"description\" "
            "(one sentence), \"instructions\" (clear imperative guidance — a few short bullet "
            "points telling a coding/assistant agent how to behave whenever this skill is "
            "active). Reply with ONLY the JSON object — no code fences, no preamble."},
        {"role": "user", "content": prompt},
    ]
    content = ""
    try:
        a = provider.chat(messages, tools=None, on_text=None, cancel=cancel)
        content = (a.get("content") or "").strip()
    except Exception:  # noqa: BLE001 - generation must never raise into the UI thread
        content = ""

    name = description = instructions = ""
    start, end = content.find("{"), content.rfind("}")
    if 0 <= start < end:
        try:
            data = json.loads(content[start:end + 1])
            name = str(data.get("name", "")).strip()
            description = str(data.get("description", "")).strip()
            instructions = str(data.get("instructions") or data.get("content", "")).strip()
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass
    if not instructions:
        # Model didn't return clean JSON — reuse the instructions-only generator.
        instructions = generate_skill_instructions(provider, prompt, "", cancel)
    if not instructions:
        raise ValueError("generation produced no instructions")
    if not name:
        name = (prompt[:40].strip().rstrip(".") or "New Skill")
    if not description:
        description = prompt[:80].strip()
    return Skill(name=name, description=description, instructions=instructions, enabled=False)


def generate_skill_instructions(provider, description: str = "", name: str = "", cancel=None) -> str:
    """Best-effort: turn a short description into the INSTRUCTIONS body of a skill.
    Returns '' on any error (so the dialog never breaks)."""
    description = (description or "").strip()
    if not description and not name:
        return ""
    user = (f"Skill name: {name}\n" if name else "") + f"Short description: {description}"
    messages = [
        {"role": "system", "content":
            "You write the INSTRUCTIONS body of a reusable agent skill. Given a short description, "
            "produce clear, imperative guidance (a few short bullet points or paragraphs) telling a "
            "coding assistant how to behave whenever this skill is active. Reply with ONLY the "
            "instructions text — no preamble, no title."},
        {"role": "user", "content": user},
    ]
    try:
        a = provider.chat(messages, tools=None, on_text=None, cancel=cancel)
    except Exception:  # noqa: BLE001 - generation must never break the dialog
        return ""
    return (a.get("content") or "").strip()


def generate_skill_from_template(provider, file_path, cancel=None) -> "Skill":
    """Analyze a pptx/xlsx TEMPLATE file's structure/styling (via
    ``doc_style_extract``) plus its own text content (placeholders, sample
    data, notes — via ``doc_extract``) and draft a skill whose instructions
    tell the agent how to replicate this exact template — layout, fonts,
    colors, formatting — the next time it generates a similar file. Raises
    ValueError when the file type is unsupported or nothing usable could be
    produced, so the caller can show a friendly message (same contract as
    :func:`generate_skill`)."""
    from .doc_extract import extract_text
    from .doc_style_extract import extract_structure

    path = Path(file_path)
    suffix = path.suffix.lower().lstrip(".")
    if suffix not in ("pptx", "xlsx", "xlsm"):
        raise ValueError(f"unsupported template type: .{suffix}")
    structure = extract_structure(path)
    text, _note = extract_text(path)
    if not structure and not text:
        raise ValueError("could not read the template's structure or content")

    parts = [f"Template file: {path.name} (.{suffix})"]
    if structure:
        parts.append("Structure/styling:\n" + structure[:6000])
    if text:
        parts.append("Text content (placeholders, sample data, notes):\n" + text[:3000])
    context = "\n\n".join(parts)

    messages = [
        {"role": "system", "content":
            "You design reusable agent skills. Below is a structural/styling analysis "
            "of a PowerPoint or Excel TEMPLATE file, plus its own text content. Output a "
            "SINGLE JSON object with EXACTLY these keys: \"name\" (a short Title Case "
            "name based on what the template is for), \"description\" (one sentence), "
            "\"instructions\" (clear, imperative guidance telling a coding agent EXACTLY "
            "how to replicate this template's layout/fonts/colors/formatting/structure "
            "the next time it generates a similar file via python-pptx/openpyxl — be "
            "specific about fonts, sizes, colors, and layout/structure, not generic "
            "advice). Reply with ONLY the JSON object — no code fences, no preamble."},
        {"role": "user", "content": context},
    ]
    content = ""
    try:
        a = provider.chat(messages, tools=None, on_text=None, cancel=cancel)
        content = (a.get("content") or "").strip()
    except Exception:  # noqa: BLE001 - generation must never raise into the UI thread
        content = ""

    name = description = instructions = ""
    start, end = content.find("{"), content.rfind("}")
    if 0 <= start < end:
        try:
            data = json.loads(content[start:end + 1])
            name = str(data.get("name", "")).strip()
            description = str(data.get("description", "")).strip()
            instructions = str(data.get("instructions") or data.get("content", "")).strip()
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass
    if not instructions:
        raise ValueError("generation produced no instructions")
    if not name:
        name = path.stem[:40] or "Template Skill"
    if not description:
        description = f"Replicate the layout/styling of {path.name}."
    return Skill(name=name, description=description, instructions=instructions, enabled=False)


def skill_prefix_for(slug: str, directory: Path | None = None) -> str:
    """Return the ``## Skill: <name>\\n<instructions>`` block for a single skill
    chosen by ``slug`` (matched against user skills AND always-on built-ins), or
    ``''`` when the slug is empty, unknown, or the skill has no instructions.

    Used by Schedule Task to apply a chosen skill to an unattended run — the
    block is prepended to the task prompt, same shape ``active_skills_text`` and
    ``parse_skill_command`` produce for the interactive chat."""
    if not slug:
        return ""
    directory = directory or SKILLS_DIR
    low = slug.strip().lower()
    for s in list_skills(directory) + builtin_skills():
        if (s.slug == low or s.name.lower() == low) and s.instructions.strip():
            return f"## Skill: {s.name}\n{s.instructions.strip()}"
    return ""


def parse_skill_command(text: str, directory: Path | None = None):
    """Parse a ``/skill`` command anywhere in the chat box text (usable from any
    chat box) — not just when it's the first thing typed, so the user can put
    context before and/or after it.

    Returns ``(prefix, request, info)``:
      * ``prefix``  – skill instructions to prepend to the agent prompt ('' if none)
      * ``request`` – the user's request with the command stripped
      * ``info``    – when not None, answer this inline (no agent turn)

    Forms: ``/skill`` (list) · ``/skill:<name> <req>`` (one skill) ·
    ``/skill <req>`` (all enabled skills). The command may appear at the
    start, middle, or end of the message; the surrounding text is kept and
    joined together as the request."""
    directory = directory or SKILLS_DIR
    import re

    raw = (text or "").strip()
    # The command may sit mid-sentence, so punctuation right after it must not
    # break recognition ("dùng /skill:fpt-slide-generator, tạo slide…"): match
    # up to a word boundary, then trim punctuation the char-class swallowed.
    m = re.search(r"(?<!\S)/skill(?::([\w\-.]+))?(?=$|[\s.,;:!?)\]}»”’'\"、。」])",
                  raw, re.DOTALL)
    if not m:
        return "", text, None
    slug = m.group(1)
    if slug:
        slug = slug.rstrip(".")   # "/skill:name." — the dot was sentence punctuation
    rest = (raw[:m.start()] + " " + raw[m.end():]).strip()
    rest = re.sub(r"[ \t]{2,}", " ", rest)   # no double space where the command sat
    user_skills = list_skills(directory)
    builtins = builtin_skills()
    builtin_slugs = {s.slug for s in builtins}
    # Built-in skills are always on and hidden from the Skills manager, but the
    # /skill command must still surface them — otherwise a fresh install (no custom
    # skills yet) makes /skill look empty/broken.
    skills = user_skills + builtins
    if slug:
        low = slug.lower()
        match = next((s for s in skills
                      if s.slug == low or s.name.lower() == low or s.slug == slug), None)
        if match is None:
            return "", text, (f"Skill `{slug}` not found. "
                              "Type `/skill` to see the available skills.")
        if not rest:
            return "", text, (f"Skill **{match.name}** selected — add your request, e.g. "
                              f"`/skill:{slug} summarise this file`.")
        return f"## Skill: {match.name}\n{match.instructions.strip()}", rest, None
    if not rest:
        if not skills:
            return "", text, ("No skills found yet. Add one in the Skills manager "
                              "(**Skills** button).")

        def _tag(s: "Skill") -> str:
            if s.slug in builtin_slugs:
                return "  _(built-in, always on)_"
            return "" if s.enabled else "  _(disabled)_"

        listing = "\n".join(
            f"- `/skill:{s.slug}` — **{s.name}**"
            + (f": {s.description}" if s.description else "")
            + _tag(s)
            for s in skills
        )
        return "", text, ("**Available skills**\n" + listing
                          + "\n\nApply one with `/skill:<name> <your request>`, "
                          "or `/skill <your request>` to use all enabled skills.")
    # /skill <request> with no specific name → apply all enabled skills.
    active = active_skills_text(directory)
    if active:
        return active, rest, None
    if len(skills) == 1:
        s = skills[0]
        return f"## Skill: {s.name}\n{s.instructions.strip()}", rest, None
    listing = "\n".join(
        f"- `/skill:{s.slug}` — **{s.name}**" + (f": {s.description}" if s.description else "")
        for s in skills
    )
    return "", text, ("Chưa bật skill nào. Chọn một skill cụ thể:\n" + listing)
