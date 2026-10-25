"""每个请求提供独立会话，请求结束后释放连接和未提交事务。"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from ..storage.sql_store import SqlDocumentStore


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.database.sessions() as session:
        yield session


SessionDependency = Annotated[Session, Depends(get_session)]


def get_document_store(session: SessionDependency) -> SqlDocumentStore:
    return SqlDocumentStore(session)


DocumentStoreDependency = Annotated[SqlDocumentStore, Depends(get_document_store)]
