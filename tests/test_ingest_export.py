import io
import zipfile

import docx
import pytest
from fpdf import FPDF
from pypdf import PdfReader

from daftar.exporter import FORMATS, export
from daftar.ingest import chunk_pages, extract, word_count


def test_chunks_cover_all_words_and_respect_size():
    text = " ".join(f"word{i}" for i in range(2000))
    chunks = chunk_pages([(1, text)], size=500, overlap=80)
    assert len(chunks) > 10
    assert all(len(t) <= 500 for _, t in chunks)
    assert set(" ".join(t for _, t in chunks).split()) == set(text.split())  # nothing lost or cut


def test_bigger_blocks_mean_fewer_chunks():
    text = " ".join(f"w{i}" for i in range(3000))
    assert len(chunk_pages([(1, text)], 2000, 100)) < len(chunk_pages([(1, text)], 500, 100))


def test_chunks_keep_page_numbers_and_skip_blank_pages():
    assert chunk_pages([(1, "alpha beta"), (2, "  \n\n "), (3, "gamma")]) == [(1, "alpha beta"), (3, "gamma")]


def test_word_count_handles_unicode():
    assert word_count("Murabaha عقد المرابحة is a sale.") == 6


def make_pdf(path, *pages):
    pdf = FPDF()
    for line in pages:
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(0, 10, line)
    pdf.output(str(path))


def test_pdf_extraction_is_per_page(tmp_path):
    make_pdf(tmp_path / "c.pdf", "Payment is due within 30 days.", "The contract ends on 1 March 2027.")
    pages = extract(tmp_path / "c.pdf")
    assert [p for p, _ in pages] == [1, 2]
    assert "30 days" in pages[0][1] and "March 2027" in pages[1][1]


def test_docx_extraction_includes_tables(tmp_path):
    d = docx.Document()
    d.add_paragraph("Supplier: Acme Ltd")
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text, t.rows[0].cells[1].text = "Fee", "4,500 USD"
    d.save(str(tmp_path / "a.docx"))
    text = extract(tmp_path / "a.docx")[0][1]
    assert "Acme Ltd" in text and "4,500 USD" in text


PAGES = [(1, "First page about the lease.\n\nSecond paragraph."), (2, "Rent is 2,350 euros. Arabic: الإيجار")]


@pytest.mark.parametrize("fmt", list(FORMATS))
def test_every_export_format_contains_the_text(fmt):
    data, media, filename = export(PAGES, "lease.pdf", fmt)
    assert filename == f"lease.{fmt}" and media == FORMATS[fmt]
    if fmt == "pdf":
        assert data.startswith(b"%PDF")
        text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)
        assert "2,350" in text and "Page 2" in text
    elif fmt == "docx":
        assert zipfile.is_zipfile(io.BytesIO(data))
        text = "\n".join(p.text for p in docx.Document(io.BytesIO(data)).paragraphs)
        assert "2,350" in text and "الإيجار" in text
    else:
        text = data.decode("utf-8")
        assert "2,350" in text and "الإيجار" in text and "Page 2" in text


def test_html_export_escapes_markup():
    data, _, _ = export([(1, "<script>alert(1)</script>")], "x.txt", "html")
    assert b"<script>alert" not in data and b"&lt;script&gt;" in data


def test_unknown_format_rejected():
    with pytest.raises(ValueError):
        export(PAGES, "x.pdf", "exe")


def test_pdf_text_cleanup():
    from daftar.ingest import clean_pdf_text
    assert clean_pdf_text("ﬁnancial infor-\nmation") == "financial information"
    assert clean_pdf_text("ﻣﺮﺎﺑﺤﺔ") == "مرابحة"
    assert clean_pdf_text("Item A-\nB stays") == "Item A-\nB stays"  # not a split lowercase word
