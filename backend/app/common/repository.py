"""Repository base: the only layer that talks to the ORM session."""

from __future__ import annotations

from typing import Generic, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class Repository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, pk: int) -> ModelT | None:
        return self.session.get(self.model, pk)

    def require(self, pk: int) -> ModelT:
        from app.common.errors import NotFoundError

        obj = self.get(pk)
        if obj is None:
            raise NotFoundError(f"{self.model.__name__} {pk} 不存在")
        return obj

    def add(self, obj: ModelT) -> ModelT:
        self.session.add(obj)
        self.session.flush()
        return obj

    def count(self, *where) -> int:
        stmt = select(func.count()).select_from(self.model)
        if where:
            stmt = stmt.where(*where)
        return int(self.session.execute(stmt).scalar_one())

    def list_page(self, stmt, limit: int, offset: int) -> list[ModelT]:
        return list(self.session.execute(stmt.limit(limit).offset(offset)).scalars())
