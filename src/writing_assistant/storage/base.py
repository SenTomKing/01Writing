"""存储模块需要遵守的接口。"""

from typing import Protocol

from ..models import Document, RevisionRecord, TextVersion


class RevisionStore(Protocol):
    def add(self, record: RevisionRecord) -> None: ...

    def get(self, record_id: str) -> RevisionRecord: ...

    def update(self, record: RevisionRecord) -> None: ...

    def list_all(self) -> list[RevisionRecord]: ...

    def save_decision(self, record: RevisionRecord) -> None: ...

    def close_task(self, record_id: str) -> RevisionRecord: ...

    def list_unfinished(self, document_id: str | None = None) -> list[RevisionRecord]: ...


class DocumentCatalogStore(Protocol):
    """创建、列出和打开文档所需的存储能力。"""

    def add_document(
        self,
        document: Document,
        initial_version: TextVersion,
    ) -> None: ...

    def get_document(self, document_id: str) -> Document: ...

    def list_documents(self) -> list[Document]: ...

    def get_text_version(self, version_id: str) -> TextVersion: ...


class DocumentStore(DocumentCatalogStore, Protocol):
    """在基础文档存储之上，提供完整的版本操作。"""

    def list_text_versions(self, document_id: str) -> list[TextVersion]: ...

    def restore_version(
        self,
        document_id: str,
        version_id: str,
        *,
        expected_current_version_id: str,
    ) -> TextVersion: ...


class ProjectStore(RevisionStore, DocumentStore, Protocol):
    """完整修订工作流所需的文档与修订存储能力。"""
