"""Text extraction and chunking for the knowledge base (W3-B).

Responsibilities:

- extract plain text from TXT or PDF bytes;
- drop blank pages and clean the text;
- split into overlapping chunks with a continuous ``chunk_index`` starting at
  0 and keep the source ``page_number`` when known;
- raise a clear ``TextProcessingError`` for empty documents, so the ingestion
  task can mark the item ``failed`` with an ``error_message``.

The chunking strategy is simple character-based sliding windows on the
cleaned text: ``chunk_size`` characters per chunk with ``overlap`` characters
shared with the previous chunk. A single page's text becomes its own chunk
boundary so ``page_number`` stays meaningful.
"""

from dataclasses import dataclass


class TextProcessingError(Exception):
    """Text could not be extracted or is empty."""


@dataclass
class PageText:
    """Plain text of one page plus its 1-based page number."""

    page_number: int
    text: str


@dataclass
class TextChunk:
    """One chunk ready for embedding: content, page and sequence index."""

    chunk_index: int
    content: str
    page_number: int | None


def extract_pages(
    source_type: str, raw_bytes: bytes, file_name: str = ""
) -> list[PageText]:
    """Extract per-page plain text from TXT or PDF bytes.

    TXT files are treated as a single page (page 1). PDFs use ``pypdf`` and
    pages whose text is empty after cleaning are dropped.
    """
    if source_type == "txt":
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            # Some editors produce GBK/GB18030; try it before giving up.
            try:
                text = raw_bytes.decode("gb18030")
            except UnicodeDecodeError as exc:
                raise TextProcessingError("TXT 文件编码无法识别") from exc
        cleaned = _clean_text(text)
        if not cleaned:
            raise TextProcessingError("文档内容为空")
        return [PageText(page_number=1, text=cleaned)]

    if source_type == "pdf":
        return _extract_pdf_pages(raw_bytes, file_name)

    raise TextProcessingError(f"不支持的文档类型: {source_type}")


def _extract_pdf_pages(raw_bytes: bytes, file_name: str) -> list[PageText]:
    try:
        from pypdf import PdfReader

        reader = PdfReader(__import__("io").BytesIO(raw_bytes))
    except Exception as exc:
        raise TextProcessingError(f"PDF 解析失败: {exc}") from exc

    pages: list[PageText] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            raw = page.extract_text() or ""
        except Exception:
            raw = ""
        cleaned = _clean_text(raw)
        if cleaned:
            pages.append(PageText(page_number=index, text=cleaned))
    if not pages:
        raise TextProcessingError("PDF 没有可提取的文本")
    return pages


def _clean_text(text: str) -> str:
    """Normalise whitespace and drop blank lines."""
    lines = [line.strip() for line in text.splitlines()]
    non_blank = [line for line in lines if line]
    return "\n".join(non_blank).strip()


def split_chunks(
    pages: list[PageText],
    *,
    chunk_size: int = 500,
    overlap: int = 50,
) -> list[TextChunk]:
    """Split cleaned page texts into overlapping chunks.

    Each chunk carries a continuous ``chunk_index`` (0-based) and the page
    number it belongs to. Chunks never cross a page boundary so the citation
    stays accurate. A chunk larger than ``chunk_size`` (e.g. a single very
    long paragraph) is hard-split with the overlap applied.
    """
    if overlap >= chunk_size:
        raise TextProcessingError("overlap 必须小于 chunk_size")
    if chunk_size <= 0:
        raise TextProcessingError("chunk_size 必须大于 0")

    chunks: list[TextChunk] = []
    index = 0
    for page in pages:
        for piece in _split_single_text(page.text, chunk_size, overlap):
            chunks.append(
                TextChunk(
                    chunk_index=index,
                    content=piece,
                    page_number=page.page_number,
                )
            )
            index += 1
    if not chunks:
        raise TextProcessingError("切片结果为空")
    return chunks


def _split_single_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    pieces: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + chunk_size, length)
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end == length:
            break
        # Move forward by chunk_size - overlap (slide the window).
        start = max(end - overlap, start + 1)
    return pieces
