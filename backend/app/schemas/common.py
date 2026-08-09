"""Shared Pydantic schemas used by multiple endpoints."""

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Generic paginated list response shared by every list endpoint."""

    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int
