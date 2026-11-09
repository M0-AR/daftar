"""Turn files into page-tagged text chunks."""
import re
import unicodedata
from pathlib import Path

DOCUMENTS = {".pdf", ".docx", ".txt", ".md", ".markdown", ".csv", ".html", ".htm", ".rst", ".log"}
CODE = {".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".java", ".kt", ".go", ".rs", ".c", ".h", ".cpp", ".hpp",
        ".cs", ".rb", ".php", ".swift", ".scala", ".sql", ".sh", ".ps1", ".r", ".m", ".lua", ".dart", ".vue",
        ".json", ".yaml", ".yml", ".toml", ".ini", ".xml", ".css", ".scss", ".ipynb"}
SUPPORTED = DOCUMENTS | CODE
WORDS = re.compile(r"\w+", re.UNICODE)


def word_count(text: str) -> int:
    return len(WORDS.findall(text))


def clean_pdf_text(text: str) -> str:
    """PDF text layers often hold ligatures (ﬁ) and Arabic presentation forms (ﻣﺮ); NFKC maps them to normal
    letters. Words hyphenated across a line break are re-joined (only lowercase-to-lowercase, to keep
    real compounds and list markers)."""
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"([a-z])-\n([a-z])", r"\1\2", text)


def html_text(markup: str) -> str:
    """Visible text of an HTML page; h1-h6 become Markdown headings so sections are detected."""
    from html.parser import HTMLParser

    class Collect(HTMLParser):
        skip, out = 0, []

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "noscript", "template"):
                self.skip += 1
            elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                self.out.append("\n" + "#" * int(tag[1]) + " ")
            elif tag in ("p", "br", "li", "tr", "div", "section", "article"):
                self.out.append("\n")

        def handle_endtag(self, tag):
            if tag in ("script", "style", "noscript", "template"):
                self.skip = max(0, self.skip - 1)
            elif tag in ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "tr"):
                self.out.append("\n")

        def handle_data(self, data):
            if not self.skip:
                self.out.append(data)

    c = Collect()
    c.out = []
    c.feed(markup)
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", "".join(c.out))).strip()


def extract(path: Path) -> list[tuple[int, str]]:
    """Return [(page_number, text), ...]. Non-paged formats are one page."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        return [(i + 1, clean_pdf_text(page.extract_text() or "")) for i, page in enumerate(reader.pages)]
    if ext == ".docx":
        import docx
        d = docx.Document(str(path))
        parts = []
        for para in d.paragraphs:  # Word heading styles become Markdown headings so sections are detected
            style = (para.style.name if para.style is not None else "") or ""
            level = 1 if style == "Title" else int(style[8:]) if style.startswith("Heading ") and style[8:].isdigit() else 0
            parts.append(("#" * level + " " + para.text) if level and para.text.strip() else para.text)
        for table in d.tables:
            parts += [" | ".join(c.text for c in row.cells) for row in table.rows]
        return [(1, "\n".join(parts))]
    if ext in (".html", ".htm"):
        return [(1, html_text(path.read_text(encoding="utf-8", errors="replace")))]
    if ext == ".ipynb":
        import json
        cells = json.loads(path.read_text(encoding="utf-8", errors="replace")).get("cells", [])
        return [(1, "\n\n".join("".join(c.get("source", [])) for c in cells))]
    if ext in SUPPORTED:
        return [(1, path.read_text(encoding="utf-8", errors="replace"))]
    raise ValueError(f"Unsupported file type {ext}")


HEADING_PATTERNS = (
    # (regex, level function). Matched against one stripped line.
    (re.compile(r"^(#{1,6})\s+(.{1,120})$"), lambda m: (len(m.group(1)), m.group(2).strip(" #"))),
    (re.compile(r"^((?:section|article|clause|chapter|part|schedule|appendix|annex)\s+[\w.\-]{1,12})"
                r"(?:[ .:\-]+([^.:\n]{1,60}))?", re.IGNORECASE),
     lambda m: (2, (m.group(1) + (" " + m.group(2).strip() if m.group(2) and m.group(2)[:1].isupper() else "")).strip())),
    (re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})[.)]?\s+([A-Z][^.:\n]{1,60})$"),
     lambda m: (2 + m.group(1).count("."), f"{m.group(1)} {m.group(2).strip()}")),
    (re.compile(r"^((?:المادة|الفصل|الباب|البند|القسم)\s+\S{1,20}(?:\s+[^\n.:]{1,40})?)"), lambda m: (2, m.group(1).strip())),
)


def heading_of(line: str) -> tuple[int, str] | None:
    line = line.strip()
    if not line or len(line) > 160:
        return None
    for pattern, make in HEADING_PATTERNS:
        m = pattern.match(line)
        if m:
            return make(m)
    return None


def chunk_pages(pages: list[tuple[int, str]], size: int = 1000, overlap: int = 150) -> list[tuple[int, str]]:
    """Split each page into ~size-character chunks that break on whitespace and overlap a little."""
    return [(page, text) for page, text, _ in chunk_sections(pages, size, overlap)]


def chunk_sections(pages: list[tuple[int, str]], size: int = 1000,
                   overlap: int = 150) -> list[tuple[int, str, str | None]]:
    """Like chunk_pages, plus the heading path in effect for each chunk (e.g. "Staff Handbook > Remote Work").
    Sections carry over page breaks."""
    chunks = []
    stack: list[tuple[int, str]] = []  # (level, title)
    carried: list[tuple[int, str]] = []  # section in effect at the end of the previous page
    for page, text in pages:
        text = re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()
        marks = []  # (offset, stack snapshot after this heading)
        offset = 0
        for line in text.split("\n"):
            found = heading_of(line)
            if found:
                level, title = found
                stack = [h for h in stack if h[0] < level] + [(level, title)]
                marks.append((offset, list(stack)))
            offset += len(line) + 1
        start = 0
        chunk_list = []
        while start < len(text):
            end = min(start + size, len(text))
            if end < len(text):
                # Recursive-splitter order: paragraph, line, sentence (Latin/Arabic), then word boundary.
                for sep in ("\n\n", "\n", ". ", "؟ ", "? ", "! ", "، ", " "):
                    cut = text.rfind(sep, start + size // 2, end)
                    if cut > start:
                        end = cut + (1 if sep[0] in ".؟?!،" else 0)
                        break
            piece = text[start:end].strip()
            if piece:
                chunk_list.append((start, piece))
            if end >= len(text):
                break
            start = max(end - overlap, start + 1)
            # Do not start an overlapping chunk mid-word.
            space = text.find(" ", start, end)
            if space != -1:
                start = space + 1
        for chunk_start, piece in chunk_list:
            # Headings at or before the start, plus headings the chunk opens with (only headings/blank lines
            # before them), so "# Guide / ## Install" labels as Install but "# Guide / intro / ## Install" as Guide.
            in_effect = [snap for off, snap in marks if off <= chunk_start or
                         (off <= chunk_start + 80 and all(heading_of(ln) or not ln.strip()
                                                         for ln in text[chunk_start:off].split("\n")))]
            path = in_effect[-1] if in_effect else carried
            chunks.append((page, piece, " > ".join(t for _, t in path) if path else None))
        if marks:
            carried = marks[-1][1]
    return chunks
