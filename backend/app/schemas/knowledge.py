"""Pydantic schemas for the knowledge base (W3-B).

Response schemas are defensive: they expose only safe metadata (never API
keys, database passwords or internal configuration), and the search result
carries the source title / chunk index so the frontend can cite it. The
``KnowledgeSearchRequest`` enforces ``query`` non-empty and ``top_k`` in
[1, 20] before the service runs.
"""

from pydantic import BaseModel, Field

MAX_TOP_K = 20
MAX_SEARCH_QUERY_LENGTH = 500


class KnowledgeItemOut(BaseModel):
    """List/detail item for a knowledge document."""

    id: str
    title: str
    source_type: str
    file_name: str
    file_size_bytes: int
    status: str
    error_message: str | None = None
    uploaded_by: str
    uploader_name: str = ""
    created_at: str


class KnowledgeItemListResponse(BaseModel):
    items: list[KnowledgeItemOut]
    total: int
    page: int
    page_size: int
    pages: int


class KnowledgeItemDetail(KnowledgeItemOut):
    """Detail payload: adds chunk count and embedding count."""

    chunk_count: int
    embedding_count: int


class KnowledgeItemCreateResponse(BaseModel):
    """``POST /knowledge-items`` returns the item immediately (201)."""

    id: str
    title: str
    source_type: str
    file_name: str
    file_size_bytes: int
    status: str
    created_at: str


class KnowledgeSearchRequest(BaseModel):
    model_config = {"extra": "forbid"}

    query: str = Field(min_length=1, max_length=MAX_SEARCH_QUERY_LENGTH)
    top_k: int = Field(default=5, ge=1, le=MAX_TOP_K)


class SearchResultItem(BaseModel):
    """One retrieved chunk with its source and similarity score."""

    chunk_id: str
    content: str
    knowledge_item_id: str
    title: str
    chunk_index: int
    page_number: int | None = None
    similarity_score: float


class KnowledgeSearchResponse(BaseModel):
    items: list[SearchResultItem]
