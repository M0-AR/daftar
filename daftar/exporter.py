"""Export a document's extracted text as TXT, Markdown, HTML, DOCX or PDF — all generated locally."""
import html
import io
import re
from pathlib import Path

FORMATS = {
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def _sections(pages: list[tuple[int, str]]) -> list[tuple[int | None, str]]:
    pages = [(p, CONTROL.sub("", t).strip()) for p, t in pages]
    return pages if len(pages) > 1 else [(None, pages[0][1] if pages else "")]


def export(pages: list[tuple[int, str]], name: str, fmt: str) -> tuple[bytes, str, str]:
    if fmt not in FORMATS:
        raise ValueError(f"Unknown export format {fmt!r}")
    title = Path(name).stem
    sections = _sections(pages)
    data = globals()[f"_{fmt}"](title, sections)
    return data, FORMATS[fmt], f"{title}.{fmt}"


def _txt(title, sections) -> bytes:
    parts = [f"--- Page {p} ---\n{t}" if p else t for p, t in sections]
    return "\n\n".join(parts).encode("utf-8")


def _md(title, sections) -> bytes:
    parts = [f"# {title}"] + [f"## Page {p}\n\n{t}" if p else t for p, t in sections]
    return "\n\n".join(parts).encode("utf-8")


def _html(title, sections) -> bytes:
    body = []
    for p, t in sections:
        paras = "".join(f"<p>{html.escape(x).replace(chr(10), '<br>')}</p>" for x in re.split(r"\n\s*\n", t) if x.strip())
        body.append(f"<section>{f'<h2>Page {p}</h2>' if p else ''}{paras}</section>")
    doc = (f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
           "<style>body{font:16px/1.6 system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 16px}"
           "h2{font-size:14px;color:#666;border-top:1px solid #ddd;padding-top:12px}</style></head>"
           f"<body><h1>{html.escape(title)}</h1>{''.join(body)}</body></html>")
    return doc.encode("utf-8")


def _docx(title, sections) -> bytes:
    import docx
    from docx.enum.text import WD_BREAK
    d = docx.Document()
    d.add_heading(title, level=1)
    for i, (p, t) in enumerate(sections):
        if p:
            if i:
                d.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
            d.add_heading(f"Page {p}", level=2)
        for para in re.split(r"\n\s*\n", t):
            if para.strip():
                d.add_paragraph(para.strip())
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _pdf(title, sections) -> bytes:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos
    nl = {"new_x": XPos.LMARGIN, "new_y": YPos.NEXT}
    pdf = FPDF()
    pdf.set_auto_page_break(True, margin=15)
    font = next((f for f in FONT_CANDIDATES if Path(f).exists()), None)
    if font:
        pdf.add_font("body", "", font)
        family = "body"
        fix = lambda s: s  # noqa: E731
    else:  # core fonts only cover Latin-1
        family = "Helvetica"
        fix = lambda s: s.encode("latin-1", "replace").decode("latin-1")  # noqa: E731
    pdf.add_page()
    pdf.set_font(family, size=16)
    pdf.multi_cell(0, 9, fix(title), wrapmode="CHAR", **nl)
    for i, (p, t) in enumerate(sections):
        if p:
            if i:
                pdf.add_page()
            pdf.set_font(family, size=10)
            pdf.set_text_color(110)
            pdf.multi_cell(0, 7, f"Page {p}", **nl)
            pdf.set_text_color(0)
        pdf.set_font(family, size=11)
        pdf.multi_cell(0, 6, fix(t) or " ", wrapmode="CHAR", **nl)
    return bytes(pdf.output())
