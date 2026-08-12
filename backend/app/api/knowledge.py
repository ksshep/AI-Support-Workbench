"""Knowledge base endpoints (W3-B).

Thin HTTP layer over ``knowledge_service``. The task spec names the routes
``/knowledge-items`` and ``/knowledge-search`` (the design doc uses
``/knowledge``); the task spec wins, consistent with earlier milestones.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi import status as http_status
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import User
from ..schemas.knowledge import (
    KnowledgeItemCreateResponse,
    KnowledgeItemDetail,
    KnowledgeItemListResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)
from ..services import knowledge_service

router = APIRouter(tags=["knowledge"])


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post(
    "/knowledge-items",
    response_model=KnowledgeItemCreateResponse,
    status_code=http_status.HTTP_201_CREATED,
)
async def upload_knowledge_item(
    request: Request,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload a TXT/PDF knowledge document (admin only).

    Returns 201 with ``status=processing`` immediately; parsing, chunking and
    embedding happen in the RQ worker.
    """
    raw = await file.read()
    return knowledge_service.create_knowledge_item(
        db,
        file_name=file.filename or "unknown",
        raw=raw,
        title=title,
        current_user=current_user,
        ip_address=_client_ip(request),
    )


@router.get("/knowledge-items", response_model=KnowledgeItemListResponse)
def list_knowledge_items(
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List knowledge items (admin: all; agent: ready; customer: 403)."""
    if page < 1:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_input", "message": "page 必须大于等于 1"},
        )
    if page_size < 1 or page_size > 100:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_input", "message": "page_size 必须在 1-100 之间"},
        )
    return knowledge_service.list_knowledge_items(
        db,
        current_user=current_user,
        page=page,
        page_size=page_size,
        status_filter=status,
    )


@router.get("/knowledge-items/{item_id}", response_model=KnowledgeItemDetail)
def get_knowledge_item(
    item_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return a knowledge item's detail incl. chunk / embedding counts."""
    return knowledge_service.get_knowledge_item(
        db, item_id=item_id, current_user=current_user
    )


@router.delete("/knowledge-items/{item_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_knowledge_item(
    item_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a knowledge item (admin only), cascading its chunks."""
    knowledge_service.delete_knowledge_item(
        db,
        item_id=item_id,
        current_user=current_user,
        ip_address=_client_ip(request),
    )
    return None


@router.post("/knowledge-search", response_model=KnowledgeSearchResponse)
def search_knowledge(
    body: KnowledgeSearchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Vector-similarity search over ready knowledge chunks (agent/admin)."""
    return knowledge_service.search_knowledge(
        db,
        query=body.query,
        top_k=body.top_k,
        current_user=current_user,
    )
