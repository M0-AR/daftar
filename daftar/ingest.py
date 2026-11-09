"""Turn files into page-tagged text chunks."""
import re
from pathlib import Path

SUPPORTED = {".pdf", ".docx", ".txt", ".md", ".markdown", ".csv"}
WORDS = re.compile(r"\w+", re.UNICODE)


def word_count(text: str) -> int:
    return len(WORDS.findall(text))


def extract(path: Path) -> list[tuple[int, str]]:
    """Return [(page_number, text), ...]. Non-paged formats are one page."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        return [(i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages)]
    if ext == ".docx":
        import docx
        d = docx.Document(str(path))
        parts = [p.text for p in d.paragraphs]
        for table in d.tables:
            parts += [" | ".join(c.text for c in row.cells) for row in table.rows]
        return [(1, "\n".join(parts))]
    if ext in SUPPORTED:
        return [(1, path.read_text(encoding="utf-8", errors="replace"))]
    raise ValueError(f"Unsupported file type {ext}")


def chunk_pages(pages: list[tuple[int, str]], size: int = 1000, overlap: int = 150) -> list[tuple[int, str]]:
    """Split each page into ~size-character chunks that break on whitespace and overlap a little."""
    chunks = []
    for page, text in pages:
        text = re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()
        start = 0
        while start < len(text):
            end = min(start + size, len(text))
            if end < len(text):
                cut = max(text.rfind("\n", start + size // 2, end), text.rfind(" ", start + size // 2, end))
                if cut > start:
                    end = cut
            piece = text[start:end].strip()
            if piece:
                chunks.append((page, piece))
            if end >= len(text):
                break
            start = max(end - overlap, start + 1)
            # Do not start an overlapping chunk mid-word.
            space = text.find(" ", start, end)
            if space != -1:
                start = space + 1
    return chunks
