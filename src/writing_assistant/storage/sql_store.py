"""第一阶段的数据库文档存储，不承担 AI 修订状态操作。"""

from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..errors import DocumentNotFoundError, StorageError, TextVersionNotFoundError, ValidationError
from ..models import Document, TextVersion, utc_now
from ..validation import validate_document_content, validate_document_title
from .tables import DocumentRow, TextVersionRow


class SqlDocumentStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_document(self, document: Document, initial_version: TextVersion) -> None:
        if (initial_version.document_id != document.id
                or document.current_version_id != initial_version.id
                or initial_version.version_number != 1
                or initial_version.source_revision_id is not None
                or initial_version.restored_from_version_id is not None):
            raise StorageError("文档与初始正文版本的关联不正确")
        validate_document_title(document.title)
        validate_document_content(initial_version.content)
        try:
            with self._session.begin():
                # 两条插入同属一个事务；延迟外键在提交时核对双向关系。
                self._session.execute(insert(DocumentRow).values(**document.to_dict()))
                self._session.execute(insert(TextVersionRow).values(**initial_version.to_dict()))
        except SQLAlchemyError as exc:
            raise StorageError("保存文档失败，修改已回滚") from exc

    @staticmethod
    def _document(row: DocumentRow) -> Document:
        return Document(
            id=row.id, title=row.title, current_version_id=row.current_version_id,
            created_at=row.created_at, updated_at=row.updated_at,
        )

    def get_document(self, document_id: str) -> Document:
        row = self._session.get(DocumentRow, document_id)
        if row is None:
            raise DocumentNotFoundError("找不到指定文档")
        return self._document(row)

    def get_text_version(self, version_id: str) -> TextVersion:
        row = self._session.get(TextVersionRow, version_id)
        if row is None:
            raise TextVersionNotFoundError("找不到指定正文版本")
        return TextVersion(
            id=row.id, document_id=row.document_id, version_number=row.version_number,
            content=row.content, created_at=row.created_at,
            source_revision_id=row.source_revision_id,
            restored_from_version_id=row.restored_from_version_id,
        )

    def list_documents(self) -> list[Document]:
        rows = self._session.scalars(
            select(DocumentRow).order_by(DocumentRow.created_at, DocumentRow.id)
        )
        return [self._document(row) for row in rows]

    def list_page(self, *, page: int, page_size: int) -> tuple[list[Document], int]:
        if not 1 <= page <= 1_000_000 or not 1 <= page_size <= 100:
            raise ValidationError("分页参数超出允许范围")
        total = self._session.scalar(select(func.count()).select_from(DocumentRow)) or 0
        rows = self._session.scalars(
            select(DocumentRow)
            .order_by(DocumentRow.created_at.desc(), DocumentRow.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return [self._document(row) for row in rows], total

    def rename_document(self, document_id: str, title: str) -> Document:
        normalized = validate_document_title(title)
        try:
            with self._session.begin():
                row = self._session.execute(
                    update(DocumentRow).where(DocumentRow.id == document_id)
                    .values(title=normalized, updated_at=utc_now())
                    .returning(DocumentRow)
                ).scalar_one_or_none()
                if row is None:
                    raise DocumentNotFoundError("找不到指定文档")
                document = self._document(row)
            return document
        except SQLAlchemyError as exc:
            raise StorageError("修改标题失败，修改已回滚") from exc
