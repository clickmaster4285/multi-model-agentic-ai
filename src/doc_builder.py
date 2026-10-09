"""Shared helpers for document tools: templates + image embeds + light markdown."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Iterator

from src.artifacts import resolve_job_file
from src.config import Config

TEMPLATES = frozenset({"default", "report", "one_pager", "pitch"})
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_MD_CODE = re.compile(r"`([^`]+)`")
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_MD_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_MD_NUMBER = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_MD_RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_MOJIBAKE = {
    "â€”": "—",
    "â€“": "–",
    "â€˜": "‘",
    "â€™": "’",
    "â€œ": "“",
    "â€\x9d": "”",
    "â€¦": "…",
    "Â·": "·",
    "Â ": " ",
}


def template_name(args: dict[str, Any]) -> str:
    raw = str(args.get("template") or "default").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in ("onepager", "one_page", "onepage"):
        raw = "one_pager"
    if raw in ("deck", "pitch_deck"):
        raw = "pitch"
    return raw if raw in TEMPLATES else "default"


def subtitle_line(args: dict[str, Any], template: str) -> str:
    sub = str(args.get("subtitle") or "").strip()
    if sub:
        return sub
    if template == "report":
        return f"Report · {date.today().isoformat()}"
    if template == "one_pager":
        return "One-pager"
    if template == "pitch":
        return "Pitch"
    return ""


def fix_mojibake(text: str) -> str:
    """Repair common UTF-8-as-Latin-1 artifacts from local models."""
    if not text:
        return text
    out = text
    for bad, good in _MOJIBAKE.items():
        if bad in out:
            out = out.replace(bad, good)
    # Standalone â€ sequences often mean a broken em/en dash
    out = re.sub(r"â€[\x80-\xbf\u2014\u2013]?", "—", out)
    return out


def parse_inline_markdown(text: str) -> list[tuple[str, dict[str, bool]]]:
    """Split **bold** / *italic* / `code` into (text, {bold, italic, code}) segments."""
    text = fix_mojibake(text)
    if not text:
        return []
    segments: list[tuple[str, dict[str, bool]]] = []
    pos = 0
    pattern = re.compile(
        r"(\*\*.+?\*\*|(?<!\*)\*(?!\*).+?(?<!\*)\*(?!\*)|`[^`]+`)",
        re.DOTALL,
    )
    for m in pattern.finditer(text):
        if m.start() > pos:
            segments.append((text[pos : m.start()], {"bold": False, "italic": False, "code": False}))
        token = m.group(0)
        if token.startswith("**") and token.endswith("**") and len(token) > 4:
            segments.append((token[2:-2], {"bold": True, "italic": False, "code": False}))
        elif token.startswith("*") and token.endswith("*") and len(token) > 2:
            segments.append((token[1:-1], {"bold": False, "italic": True, "code": False}))
        elif token.startswith("`") and token.endswith("`") and len(token) > 2:
            segments.append((token[1:-1], {"bold": False, "italic": False, "code": True}))
        else:
            segments.append((token, {"bold": False, "italic": False, "code": False}))
        pos = m.end()
    if pos < len(text):
        segments.append((text[pos:], {"bold": False, "italic": False, "code": False}))
    return segments or [(text, {"bold": False, "italic": False, "code": False})]


def strip_inline_markdown(text: str) -> str:
    text = fix_mojibake(text)
    text = _MD_BOLD.sub(r"\1", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text)
    text = _MD_CODE.sub(r"\1", text)
    return text.strip()


def iter_markdown_blocks(body: str) -> Iterator[dict[str, Any]]:
    """Yield heading/bullet/number/rule/paragraph blocks from light markdown text."""
    text = fix_mojibake(body or "")
    lines = text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if _MD_RULE.match(line):
            yield {"type": "rule"}
            i += 1
            continue
        heading = _MD_HEADING.match(line)
        if heading:
            level = min(len(heading.group(1)), 4)
            title = strip_inline_markdown(heading.group(2))
            if title:
                yield {"type": "heading", "level": level, "text": title}
            i += 1
            continue
        if _MD_BULLET.match(line):
            while i < n:
                bline = lines[i].strip()
                if not bline:
                    break
                bm = _MD_BULLET.match(bline)
                if not bm:
                    break
                yield {
                    "type": "bullet",
                    "text": strip_inline_markdown(bm.group(1)),
                    "inline": bm.group(1),
                }
                i += 1
            continue
        if _MD_NUMBER.match(line):
            while i < n:
                nline = lines[i].strip()
                if not nline:
                    break
                nm = _MD_NUMBER.match(nline)
                if not nm:
                    break
                yield {
                    "type": "number",
                    "text": strip_inline_markdown(nm.group(1)),
                    "inline": nm.group(1),
                }
                i += 1
            continue
        para_lines = [line]
        i += 1
        while i < n:
            nxt = lines[i].strip()
            if (
                not nxt
                or _MD_RULE.match(nxt)
                or _MD_HEADING.match(nxt)
                or _MD_BULLET.match(nxt)
                or _MD_NUMBER.match(nxt)
            ):
                break
            para_lines.append(nxt)
            i += 1
        joined = "\n".join(para_lines)
        yield {"type": "paragraph", "text": strip_inline_markdown(joined), "inline": joined}


def should_skip_duplicate_title(text: str, *titles: str) -> bool:
    """True when a block is just a restatement of the document/section title."""
    plain = strip_inline_markdown(text or "").strip().strip("#").strip().lower()
    if not plain:
        return False
    for t in titles:
        t_plain = strip_inline_markdown(str(t or "")).strip().strip("#").strip().lower()
        if t_plain and plain == t_plain:
            return True
        if t_plain and plain.lstrip("0123456789.) ") == t_plain.lstrip("0123456789.) "):
            return True
    return False


def resolve_embed_images(
    config: Config, job_id: str, args: dict[str, Any]
) -> tuple[list[Path], list[str]]:
    """Return (found_paths, missing_or_rejected_names)."""
    names: list[str] = []
    single = args.get("image")
    if single:
        names.append(str(single))
    imgs = args.get("images")
    if isinstance(imgs, list):
        names.extend(str(x) for x in imgs if x)
    out: list[Path] = []
    missing: list[str] = []
    seen: set[str] = set()
    for name in names[:8]:
        key = Path(name).name
        if not key or key in seen:
            continue
        seen.add(key)
        if Path(key).suffix.lower() not in IMAGE_EXTS:
            missing.append(key)
            continue
        try:
            path = resolve_job_file(config, job_id, key)
        except FileNotFoundError:
            missing.append(key)
            continue
        out.append(path)
    return out, missing


def section_limit(template: str) -> int:
    if template == "one_pager":
        return 6
    if template == "pitch":
        return 10
    return 40


def markdown_to_plain(body: str) -> str:
    """Markdown → plain text with real line breaks (for PDF / simple writers)."""
    parts: list[str] = []
    for block in iter_markdown_blocks(body):
        kind = block["type"]
        if kind == "heading":
            parts.append(block["text"])
        elif kind == "bullet":
            parts.append(f"• {block['text']}")
        elif kind == "number":
            parts.append(f"• {block['text']}")
        elif kind == "rule":
            parts.append("")
        elif kind == "paragraph":
            parts.append(block["text"])
    return "\n".join(parts).strip()


def append_markdown_to_docx(
    doc: Any,
    body: str,
    *,
    skip_titles: tuple[str, ...] = (),
    max_heading_level: int = 3,
) -> None:
    """Render light markdown into a python-docx Document (no raw # / ** left)."""
    from docx.enum.text import WD_BREAK
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    def _add_runs(paragraph: Any, inline: str) -> None:
        for text, style in parse_inline_markdown(inline):
            if not text:
                continue
            run = paragraph.add_run(text)
            run.bold = True if style.get("bold") else None
            run.italic = True if style.get("italic") else None
            if style.get("code"):
                run.font.name = "Consolas"
                run.font.size = Pt(10)

    def _rule() -> None:
        p = doc.add_paragraph()
        p_pr = p._p.get_or_add_pPr()
        borders = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), "6")
        bottom.set(qn("w:space"), "1")
        bottom.set(qn("w:color"), "AAAAAA")
        borders.append(bottom)
        p_pr.append(borders)
        p.paragraph_format.space_before = Pt(4)
        p.paragraph_format.space_after = Pt(4)

    for block in iter_markdown_blocks(body):
        kind = block["type"]
        if kind == "rule":
            _rule()
            continue
        if kind == "heading":
            text = block["text"]
            if skip_titles and should_skip_duplicate_title(text, *skip_titles):
                continue
            level = max(1, min(int(block["level"]), max_heading_level))
            # Word Heading 1 maps to outline level 1; use level-1 so ## → Heading 1 style under doc title
            style = f"Heading {level}"
            try:
                h = doc.add_heading(level=level)
            except (KeyError, ValueError):
                h = doc.add_paragraph(style="Heading 1")
            try:
                h.style = style
            except (KeyError, ValueError):
                pass
            _add_runs(h, text)
            continue
        if kind == "bullet":
            p = doc.add_paragraph(style="List Bullet")
            _add_runs(p, block["inline"])
            continue
        if kind == "number":
            p = doc.add_paragraph(style="List Number")
            _add_runs(p, block["inline"])
            continue
        # paragraph — soft line breaks inside one block
        p = doc.add_paragraph()
        inline = block.get("inline") or block.get("text") or ""
        parts = inline.split("\n")
        for idx, part in enumerate(parts):
            if idx:
                p.add_run().add_break(WD_BREAK.LINE)
            _add_runs(p, part)
