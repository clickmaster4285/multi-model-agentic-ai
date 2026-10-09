"""Allowlisted tools for agentic workers (no arbitrary shell)."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from src.artifacts import (
    artifact_event,
    list_artifacts,
    mime_for,
    safe_filename,
    write_bytes,
    write_text,
)
from src.config import Config, ROOT
from src.doc_builder import (
    resolve_embed_images,
    section_limit,
    subtitle_line,
    template_name,
)
from src.llm_client import LLMClient


class ToolError(RuntimeError):
    pass


def _allowlisted(url: str, config: Config) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    allowed = {h.strip().lower() for h in config.http_allowlist.split(",") if h.strip()}
    return host in allowed or any(host.endswith(f".{h}") for h in allowed)


def _ok(meta: dict[str, Any], *, job_id: str, message: str) -> str:
    return json.dumps(
        {
            "ok": True,
            "message": message,
            "artifact": meta,
            "event": artifact_event(meta, job_id=job_id),
        },
        ensure_ascii=False,
    )


def _sections_from_args(args: dict[str, Any]) -> list[tuple[str, str]]:
    """Accept content string or sections:[{heading,body}]."""
    sections = args.get("sections")
    if isinstance(sections, list) and sections:
        out: list[tuple[str, str]] = []
        for row in sections[:40]:
            if not isinstance(row, dict):
                continue
            heading = str(row.get("heading") or row.get("title") or "").strip()
            body = str(row.get("body") or row.get("content") or "").strip()
            if heading or body:
                out.append((heading or "Section", body))
        if out:
            return out
    content = str(args.get("content") or "").strip()
    title = str(args.get("title") or "").strip()
    if content:
        return [(title or "Document", content)]
    return [("Document", "(empty)")]


def run_tool(
    name: str,
    args: dict[str, Any],
    *,
    job_id: str,
    config: Config,
) -> str:
    if name == "read_repo_file":
        rel = str(args.get("path") or "").replace("\\", "/").lstrip("/")
        if ".." in rel.split("/"):
            raise ToolError("Path traversal is not allowed.")
        path = (ROOT / rel).resolve()
        if not str(path).startswith(str(ROOT.resolve())):
            raise ToolError("Path outside workspace.")
        if not path.is_file():
            raise ToolError(f"File not found: {rel}")
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[:12000]

    if name == "write_artifact":
        filename = safe_filename(str(args.get("filename") or "notes.md"), default="notes.md")
        content = str(args.get("content") or "")
        meta = write_text(config, job_id, filename, content, kind="document")
        return _ok(meta, job_id=job_id, message=f"Wrote {meta['filename']}")

    if name == "write_html":
        filename = safe_filename(str(args.get("filename") or "page.html"), default="page.html")
        if not filename.lower().endswith((".html", ".htm")):
            filename = f"{Path(filename).stem}.html"
        title = str(args.get("title") or Path(filename).stem)
        tmpl = template_name(args)
        sub = subtitle_line(args, tmpl)
        sections = _sections_from_args(args)[: section_limit(tmpl)]
        embeds, missing_imgs = resolve_embed_images(config, job_id, args)
        body_parts = []
        if sub:
            body_parts.append(f"<p class='sub'>{_esc(sub)}</p>")
        for i, (heading, body) in enumerate(sections, start=1):
            label = f"{i}. {heading}" if tmpl == "report" else heading
            body_html = "<br/>".join(_esc(line) for line in body.splitlines())
            body_parts.append(f"<h2>{_esc(label)}</h2><p>{body_html}</p>")
        # Keep base64 embeds under write_text truncation (MAX_TEXT_CHARS=400k)
        embed_budget = 220_000
        for path in embeds:
            try:
                raw = path.read_bytes()
                if len(raw) <= embed_budget:
                    import base64

                    b64 = base64.b64encode(raw).decode("ascii")
                    body_parts.append(
                        f"<figure><img src='data:{mime_for(path.name)};base64,{b64}' "
                        f"alt='{_esc(path.name)}'/></figure>"
                    )
                    embed_budget -= len(raw)
                else:
                    body_parts.append(f"<p><em>Image (too large to inline): {_esc(path.name)}</em></p>")
            except OSError:
                body_parts.append(f"<p><em>Image: {_esc(path.name)}</em></p>")
        max_w = "640px" if tmpl == "one_pager" else "720px"
        h1_size = "1.35rem" if tmpl == "pitch" else "1.6rem"
        html = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'/>"
            f"<title>{_esc(title)}</title>"
            f"<style>body{{font-family:Segoe UI,system-ui,sans-serif;max-width:{max_w};"
            "margin:2rem auto;padding:0 1rem;line-height:1.5;color:#1a1a1a}"
            f"h1{{font-size:{h1_size}}}h2{{font-size:1.15rem;margin-top:1.4rem}}"
            ".sub{color:#555;margin-top:-0.5rem}img{max-width:100%;height:auto}"
            "figure{margin:1.2rem 0}</style>"
            f"</head><body><h1>{_esc(title)}</h1>{''.join(body_parts)}</body></html>"
        )
        meta = write_text(config, job_id, filename, html, kind="html")
        msg = f"Wrote HTML {meta['filename']}"
        if missing_imgs:
            msg += f" (missing images: {', '.join(missing_imgs)})"
        return _ok(meta, job_id=job_id, message=msg)
    if name == "write_docx":
        try:
            from docx import Document
            from docx.shared import Inches, Pt
        except ImportError as exc:
            raise ToolError("python-docx not installed. pip install python-docx") from exc
        filename = safe_filename(str(args.get("filename") or "document.docx"), default="document.docx")
        if not filename.lower().endswith(".docx"):
            filename = f"{Path(filename).stem}.docx"
        title = str(args.get("title") or Path(filename).stem)
        tmpl = template_name(args)
        sub = subtitle_line(args, tmpl)
        sections = _sections_from_args(args)[: section_limit(tmpl)]
        embeds, missing_imgs = resolve_embed_images(config, job_id, args)
        doc = Document()
        doc.add_heading(title, level=0)
        if sub:
            p = doc.add_paragraph()
            run = p.add_run(sub)
            run.italic = True
            run.font.size = Pt(11)
        if tmpl == "pitch":
            kp = doc.add_paragraph()
            run = kp.add_run("Key points")
            run.bold = True
        for i, (heading, body) in enumerate(sections, start=1):
            label = f"{i}. {heading}" if tmpl == "report" else heading
            if label and label != title:
                doc.add_heading(label, level=1)
            for para in body.split("\n\n"):
                text = para.strip()
                if text:
                    doc.add_paragraph(text)
        for path in embeds:
            try:
                doc.add_picture(str(path), width=Inches(5.5 if tmpl != "one_pager" else 4.5))
                cap = doc.add_paragraph()
                cr = cap.add_run(path.name)
                cr.italic = True
            except (OSError, ValueError) as exc:
                doc.add_paragraph(f"[Could not embed {path.name}: {exc}]")
        buf = BytesIO()
        doc.save(buf)
        meta = write_bytes(config, job_id, filename, buf.getvalue(), kind="docx")
        msg = f"Wrote Word {meta['filename']}"
        if missing_imgs:
            msg += f" (missing images: {', '.join(missing_imgs)})"
        return _ok(meta, job_id=job_id, message=msg)
    if name == "write_pdf":
        try:
            from fpdf import FPDF
        except ImportError as exc:
            raise ToolError("fpdf2 not installed. pip install fpdf2") from exc
        filename = safe_filename(str(args.get("filename") or "report.pdf"), default="report.pdf")
        if not filename.lower().endswith(".pdf"):
            filename = f"{Path(filename).stem}.pdf"
        title = str(args.get("title") or Path(filename).stem)
        tmpl = template_name(args)
        sub = subtitle_line(args, tmpl)
        sections = _sections_from_args(args)[: section_limit(tmpl)]
        embeds, missing_imgs = resolve_embed_images(config, job_id, args)
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        title_size = 18 if tmpl == "pitch" else (14 if tmpl == "one_pager" else 16)
        body_size = 10 if tmpl == "one_pager" else 11

        def _cell(text: str, *, h: float = 6) -> None:
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(0, h, text)

        pdf.set_font("Helvetica", "B", title_size)
        _cell(_latin(title), h=10)
        if sub:
            pdf.set_font("Helvetica", "I", 10)
            _cell(_latin(sub), h=6)
        pdf.ln(4)
        pdf.set_font("Helvetica", size=body_size)
        for i, (heading, body) in enumerate(sections, start=1):
            label = f"{i}. {heading}" if tmpl == "report" else heading
            if label and label != title:
                pdf.set_font("Helvetica", "B", 13 if tmpl != "one_pager" else 11)
                _cell(_latin(label), h=8)
                pdf.ln(1)
                pdf.set_font("Helvetica", size=body_size)
            for line in body.splitlines() or [""]:
                _cell(_latin(line) if line.strip() else " ", h=5 if tmpl == "one_pager" else 6)
            pdf.ln(2)
        for path in embeds:
            try:
                if pdf.get_y() > 200:
                    pdf.add_page()
                w = 120 if tmpl == "one_pager" else 160
                pdf.set_x(pdf.l_margin)
                pdf.image(str(path), w=w)
                pdf.ln(4)
                pdf.set_font("Helvetica", "I", 9)
                _cell(_latin(path.name), h=5)
                pdf.set_font("Helvetica", size=body_size)
            except (OSError, ValueError, RuntimeError) as exc:
                _cell(_latin(f"[Could not embed {path.name}: {exc}]"))
        raw = pdf.output()
        data = raw if isinstance(raw, (bytes, bytearray)) else bytes(raw)
        meta = write_bytes(config, job_id, filename, bytes(data), kind="pdf")
        msg = f"Wrote PDF {meta['filename']}"
        if missing_imgs:
            msg += f" (missing images: {', '.join(missing_imgs)})"
        return _ok(meta, job_id=job_id, message=msg)
    if name == "write_pptx":
        try:
            from pptx import Presentation
            from pptx.util import Inches, Pt
        except ImportError as exc:
            raise ToolError("python-pptx not installed. pip install python-pptx") from exc
        filename = safe_filename(str(args.get("filename") or "deck.pptx"), default="deck.pptx")
        if not filename.lower().endswith(".pptx"):
            filename = f"{Path(filename).stem}.pptx"
        title = str(args.get("title") or Path(filename).stem)
        tmpl = template_name(args)
        sub = subtitle_line(args, tmpl) or "MulteAgent"
        sections = _sections_from_args(args)[: section_limit(tmpl)]
        embeds, missing_imgs = resolve_embed_images(config, job_id, args)
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[0])
        slide.shapes.title.text = title
        if slide.placeholders and len(slide.placeholders) > 1:
            slide.placeholders[1].text = sub
        bullet_cap = 6 if tmpl == "pitch" else (8 if tmpl == "one_pager" else 12)
        font_pt = 20 if tmpl == "pitch" else 18
        for heading, body in sections:
            layout = prs.slide_layouts[1] if len(prs.slide_layouts) > 1 else prs.slide_layouts[0]
            s = prs.slides.add_slide(layout)
            s.shapes.title.text = heading
            bullets = [ln.strip(" -•\t") for ln in body.splitlines() if ln.strip()]
            if not bullets:
                bullets = [body] if body else ["…"]
            if len(s.shapes.placeholders) > 1:
                tf = s.placeholders[1].text_frame
                tf.clear()
                for i, bullet in enumerate(bullets[:bullet_cap]):
                    p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                    p.text = bullet
                    p.level = 0
                    p.font.size = Pt(font_pt)
            else:
                box = s.shapes.add_textbox(Inches(1), Inches(2), Inches(8), Inches(4))
                box.text_frame.text = "\n".join(bullets[:bullet_cap])
        for path in embeds:
            try:
                blank = prs.slide_layouts[6] if len(prs.slide_layouts) > 6 else prs.slide_layouts[0]
                s = prs.slides.add_slide(blank)
                if hasattr(s.shapes, "title") and s.shapes.title is not None:
                    s.shapes.title.text = path.name
                s.shapes.add_picture(str(path), Inches(1.2), Inches(1.4), width=Inches(7.5))
            except (OSError, ValueError) as exc:
                layout = prs.slide_layouts[1] if len(prs.slide_layouts) > 1 else prs.slide_layouts[0]
                s = prs.slides.add_slide(layout)
                s.shapes.title.text = "Image"
                if len(s.shapes.placeholders) > 1:
                    s.placeholders[1].text = f"Could not embed {path.name}: {exc}"
        buf = BytesIO()
        prs.save(buf)
        meta = write_bytes(config, job_id, filename, buf.getvalue(), kind="pptx")
        msg = f"Wrote PowerPoint {meta['filename']}"
        if missing_imgs:
            msg += f" (missing images: {', '.join(missing_imgs)})"
        return _ok(meta, job_id=job_id, message=msg)

    if name == "write_xlsx":
        try:
            from openpyxl import Workbook
        except ImportError as exc:
            raise ToolError("openpyxl not installed. pip install openpyxl") from exc
        filename = safe_filename(str(args.get("filename") or "data.xlsx"), default="data.xlsx")
        if not filename.lower().endswith(".xlsx"):
            filename = f"{Path(filename).stem}.xlsx"
        rows = args.get("rows")
        wb = Workbook()
        ws = wb.active
        ws.title = str(args.get("sheet") or "Sheet1")[:31]
        if isinstance(rows, list) and rows:
            for r_i, row in enumerate(rows[:500], start=1):
                if isinstance(row, (list, tuple)):
                    for c_i, cell in enumerate(row[:40], start=1):
                        ws.cell(r_i, c_i, str(cell))
                elif isinstance(row, dict):
                    if r_i == 1:
                        for c_i, key in enumerate(row.keys(), start=1):
                            ws.cell(1, c_i, str(key))
                        for c_i, val in enumerate(row.values(), start=1):
                            ws.cell(2, c_i, str(val))
                    else:
                        for c_i, val in enumerate(row.values(), start=1):
                            ws.cell(r_i + 1, c_i, str(val))
        else:
            ws["A1"] = str(args.get("title") or "Data")
            ws["A2"] = str(args.get("content") or "")
        buf = BytesIO()
        wb.save(buf)
        meta = write_bytes(config, job_id, filename, buf.getvalue(), kind="xlsx")
        return _ok(meta, job_id=job_id, message=f"Wrote Excel {meta['filename']}")

    if name == "package_zip":
        filename = safe_filename(str(args.get("filename") or "bundle.zip"), default="bundle.zip")
        if not filename.lower().endswith(".zip"):
            filename = f"{Path(filename).stem}.zip"
        names = args.get("files")
        arts = list_artifacts(config, job_id)
        if isinstance(names, list) and names:
            want = {Path(str(n)).name for n in names}
            arts = [a for a in arts if a.get("filename") in want]
        if not arts:
            raise ToolError("No artifacts to zip. Create files first.")
        buf = BytesIO()
        added = 0
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for art in arts:
                rel = str(art.get("relpath") or "")
                path = (Path(config.artifacts_dir) / rel).resolve()
                root = Path(config.artifacts_dir).resolve()
                if root not in path.parents and path != root:
                    continue
                if path.is_file():
                    zf.write(path, arcname=str(art.get("filename") or path.name))
                    added += 1
        if added == 0:
            raise ToolError("No readable artifact files to zip.")
        meta = write_bytes(config, job_id, filename, buf.getvalue(), kind="zip")
        return _ok(meta, job_id=job_id, message=f"Wrote zip {meta['filename']}")

    if name == "list_artifacts":
        arts = list_artifacts(config, job_id)
        return json.dumps({"ok": True, "artifacts": arts}, indent=2)

    if name == "http_get":
        url = str(args.get("url") or "")
        if not url.startswith(("http://", "https://")):
            raise ToolError("Only http/https URLs are allowed.")
        if not _allowlisted(url, config):
            raise ToolError(f"Host not in HTTP_ALLOWLIST: {urlparse(url).hostname}")
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.text[:12000]

    if name == "list_models":
        client = LLMClient(config)
        try:
            tags = client.list_tags()
            names = [str(t.get("name") or t.get("model")) for t in tags]
            return json.dumps(names, indent=2)
        except Exception as exc:  # noqa: BLE001
            raise ToolError(str(exc)) from exc

    if name == "search_logs":
        query = str(args.get("query") or "").lower()
        limit = min(int(args.get("limit") or 5), 20)
        hits: list[str] = []
        log_dir = config.log_dir
        if log_dir.exists():
            for path in sorted(log_dir.glob("*.md"), reverse=True):
                text = path.read_text(encoding="utf-8", errors="replace")
                if query in text.lower():
                    hits.append(f"{path.name}: {text[:400]}")
                if len(hits) >= limit:
                    break
        return "\n\n".join(hits) if hits else "No matching logs."

    raise ToolError(f"Unknown tool: {name}")


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _latin(text: str) -> str:
    """FPDF core fonts are Latin-1; drop unsupported chars."""
    return text.encode("latin-1", errors="replace").decode("latin-1")
