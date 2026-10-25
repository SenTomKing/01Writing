"""组织文档创建、列表和重新打开流程。"""

from .errors import StorageError
from .models import Document, TextVersion
from .storage.base import DocumentCatalogStore, DocumentStore
from .validation import validate_document_content, validate_document_title


class DocumentCatalogWorkflow:
    """让命令行和 HTTP 入口共用文档创建与读取规则。"""

    def __init__(
        self,
        *,
        store: DocumentCatalogStore,
        max_content_chars: int,
    ) -> None:
        self._store = store
        self._max_content_chars = max_content_chars

    def create_document(
        self,
        *,
        title: str,
        initial_content: str,
    ) -> tuple[Document, TextVersion]:
        """校验输入，同时创建文档和第一版正文并保存。"""

        normalized_title = validate_document_title(title)
        normalized_content = validate_document_content(
            initial_content,
            max_chars=self._max_content_chars,
        )
        document, initial_version = Document.create_with_initial_version(
            title=normalized_title,
            initial_content=normalized_content,
        )
        self._store.add_document(document, initial_version)
        return document, initial_version

    def list_documents(self) -> list[Document]:
        """按保存顺序返回全部文档。"""

        return self._store.list_documents()

    def open_document(self, document_id: str) -> tuple[Document, TextVersion]:
        """返回指定文档及其当前正文版本。"""

        document = self._store.get_document(document_id.strip())
        current_version = self._store.get_text_version(
            document.current_version_id
        )
        if current_version.document_id != document.id:
            raise StorageError("文档与当前正文版本的所属关系不一致")
        return document, current_version


class DocumentWorkflow(DocumentCatalogWorkflow):
    """完整文档工作流，继续支持已有的版本查看与恢复。"""

    _store: DocumentStore

    def __init__(self, *, store: DocumentStore, max_content_chars: int) -> None:
        super().__init__(store=store, max_content_chars=max_content_chars)

    def list_versions(self, document_id: str) -> list[TextVersion]:
        return self._store.list_text_versions(document_id.strip())

    def restore_version(self, document_id: str, version_id: str) -> TextVersion:
        document, _ = self.open_document(document_id)
        return self._store.restore_version(
            document.id,
            version_id.strip(),
            expected_current_version_id=document.current_version_id,
        )
