"""Text extraction and chunking tests (W3-B)."""

import pytest

from backend.app.text_processing import (
    TextProcessingError,
    extract_pages,
    split_chunks,
)
from tests.fixtures import make_pdf_bytes


# --------------------------------------------------------------------------
# TXT extraction
# --------------------------------------------------------------------------

def test_txt_extracts_single_page():
    pages = extract_pages("txt", "第一行\n第二行".encode("utf-8"))
    assert len(pages) == 1
    assert pages[0].page_number == 1
    assert "第一行" in pages[0].text


def test_txt_blank_lines_stripped():
    pages = extract_pages("txt", "  \n第一段  \n\n第二段\n".encode("utf-8"))
    assert pages[0].text == "第一段\n第二段"


def test_empty_txt_fails():
    with pytest.raises(TextProcessingError):
        extract_pages("txt", b"   \n  ")


def test_invalid_source_type_fails():
    with pytest.raises(TextProcessingError):
        extract_pages("docx", b"anything")


# --------------------------------------------------------------------------
# PDF extraction
# --------------------------------------------------------------------------

def test_pdf_extracts_pages_and_skips_blank():
    pages = extract_pages("pdf", make_pdf_bytes(["Page one text", "", "Page three"]))
    assert len(pages) == 2
    assert pages[0].page_number == 1
    assert pages[1].page_number == 3
    assert "Page one" in pages[0].text
    assert "Page three" in pages[1].text


def test_pdf_with_no_text_fails():
    with pytest.raises(TextProcessingError):
        extract_pages("pdf", make_pdf_bytes(["", "", ""]))


def test_broken_pdf_fails():
    with pytest.raises(TextProcessingError):
        extract_pages("pdf", b"this is not a pdf at all")


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------

def test_chunk_index_continuous_from_zero():
    pages = extract_pages("txt", b"a" * 1200)
    chunks = split_chunks(pages, chunk_size=500, overlap=50)
    indexes = [c.chunk_index for c in chunks]
    assert indexes == list(range(len(chunks)))
    assert indexes[0] == 0


def test_chunk_page_number_preserved():
    pages = extract_pages("txt", b"x" * 800)
    chunks = split_chunks(pages, chunk_size=300, overlap=30)
    assert all(c.page_number == 1 for c in chunks)


def test_chunks_overlap():
    pages = extract_pages("txt", b"abcdefghijklmnopqrstuvwxyz" * 100)
    chunks = split_chunks(pages, chunk_size=100, overlap=20)
    assert len(chunks) >= 2
    # Every chunk (except the last) shares its tail with the next head.
    for a, b in zip(chunks, chunks[1:]):
        overlap_text = a.content[-20:]
        assert overlap_text in b.content


def test_chunk_never_crosses_page():
    pages = extract_pages("txt", b"AAA\nBBB\nCCC")
    chunks = split_chunks(pages, chunk_size=500, overlap=50)
    assert len(chunks) == 1
    assert "AAA" in chunks[0].content


def test_overlap_must_be_less_than_chunk_size():
    pages = extract_pages("txt", b"hello world")
    with pytest.raises(TextProcessingError):
        split_chunks(pages, chunk_size=10, overlap=10)


def test_chunk_size_must_be_positive():
    pages = extract_pages("txt", b"hello")
    with pytest.raises(TextProcessingError):
        split_chunks(pages, chunk_size=0, overlap=0)


def test_empty_pages_fail_chunking():
    with pytest.raises(TextProcessingError):
        split_chunks([], chunk_size=100, overlap=10)
