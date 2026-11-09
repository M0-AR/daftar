import docx
from fpdf import FPDF

from daftar.ingest import chunk_pages, extract


def test_chunks_cover_all_words_and_respect_size():
    text = " ".join(f"word{i}" for i in range(2000))
    chunks = chunk_pages([(1, text)], size=500, overlap=80)
    assert len(chunks) > 10
    assert all(len(t) <= 500 for _, t in chunks)
    seen = set(" ".join(t for _, t in chunks).split())
    assert seen == set(text.split())  # nothing lost, no word cut in half


def test_chunks_keep_page_numbers_and_skip_blank_pages():
    chunks = chunk_pages([(1, "alpha beta"), (2, "   \n\n "), (3, "gamma")])
    assert chunks == [(1, "alpha beta"), (3, "gamma")]


def test_pdf_extraction_is_per_page(tmp_path):
    pdf = FPDF()
    for line in ("Payment is due within 30 days.", "The contract ends on 1 March 2027."):
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(0, 10, line)
    path = tmp_path / "c.pdf"
    pdf.output(str(path))
    pages = extract(path)
    assert [p for p, _ in pages] == [1, 2]
    assert "30 days" in pages[0][1] and "March 2027" in pages[1][1]


def test_docx_extraction_includes_tables(tmp_path):
    d = docx.Document()
    d.add_paragraph("Supplier: Acme Ltd")
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text, t.rows[0].cells[1].text = "Fee", "4,500 USD"
    path = tmp_path / "a.docx"
    d.save(str(path))
    text = extract(path)[0][1]
    assert "Acme Ltd" in text and "4,500 USD" in text
