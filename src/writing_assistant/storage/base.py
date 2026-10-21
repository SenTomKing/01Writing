"""存储模块需要遵守的接口。"""

from typing import Protocol

from ..models import RevisionRecord


class RevisionStore(Protocol):
    def add(self, record: RevisionRecord) -> None: ...

    def get(self, record_id: str) -> RevisionRecord: ...

    def update(self, record: RevisionRecord) -> None: ...

    def list_all(self) -> list[RevisionRecord]: ...
