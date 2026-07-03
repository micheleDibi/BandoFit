from math import ceil
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int

    @classmethod
    def build(cls, items: list[T], total: int, page: int, page_size: int) -> "Page[T]":
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=max(1, ceil(total / page_size)) if total else 0,
        )


class LookupItem(BaseModel):
    id: int
    nome: str


class AtecoItem(BaseModel):
    id: int
    codice: str
    descrizione: str | None = None
