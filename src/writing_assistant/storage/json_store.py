"""使用一个本地 JSON 文件保存文档、正文版本和修订记录。"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import tempfile
from threading import RLock
from typing import Any

from ..errors import (
    DocumentNotFoundError,
    InvalidDecisionError,
    RecordNotFoundError,
    StorageError,
    TextVersionNotFoundError,
    VersionConflictError,
)
from ..models import Document, RevisionRecord, RevisionStatus, TextVersion, utc_now


LEGACY_SCHEMA_VERSION = 1
SCHEMA_VERSION = 2


@dataclass(slots=True)
class _StoredData:
    """JSON 根节点在 Python 内部的结构，不对项目其他模块公开。"""

    documents: list[Document]
    text_versions: list[TextVersion]
    revisions: list[RevisionRecord]
    source_schema_version: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "documents": [document.to_dict() for document in self.documents],
            "text_versions": [version.to_dict() for version in self.text_versions],
            "revisions": [record.to_dict() for record in self.revisions],
        }


class JsonProjectStore:
    """带旧数据保护、结构校验和原子写入的 JSON 项目存储。"""

    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        self._lock = RLock()

    @property
    def file_path(self) -> Path:
        return self._file_path

    @property
    def legacy_backup_path(self) -> Path:
        return self._file_path.with_name(
            f"{self._file_path.stem}.schema1.backup{self._file_path.suffix}"
        )

    def add(self, record: RevisionRecord) -> None:
        """新增一条修订记录，并在需要时把第一版文件安全升级为第二版。"""

        with self._lock:
            data = self._load_data()
            if any(existing.id == record.id for existing in data.revisions):
                raise StorageError(f"修订记录已经存在：{record.id}")
            if record.document_id is not None:
                document = self._document_in(data, record.document_id)
                if document.current_version_id != record.base_version_id:
                    raise VersionConflictError("生成期间正文已经变化，请基于当前版本重新发起修订")
                for unfinished in self._unfinished_records(data.revisions):
                    if (
                        unfinished.document_id == record.document_id
                        and unfinished.id != record.previous_record_id
                    ):
                        raise InvalidDecisionError("这篇文档还有未完成任务，请先继续或放弃")
            data.revisions.append(record)
            self._write_data(data)

    def get(self, record_id: str) -> RevisionRecord:
        with self._lock:
            for record in self._load_data().revisions:
                if record.id == record_id:
                    return record
        raise RecordNotFoundError(f"找不到修订记录：{record_id}")

    def update(self, record: RevisionRecord) -> None:
        if record.document_id is not None:
            raise InvalidDecisionError("文档修订必须通过保存决定同时维护正文版本")
        with self._lock:
            data = self._load_data()
            for index, existing in enumerate(data.revisions):
                if existing.id == record.id:
                    data.revisions[index] = record
                    self._write_data(data)
                    return
        raise RecordNotFoundError(f"找不到修订记录：{record.id}")

    def list_all(self) -> list[RevisionRecord]:
        with self._lock:
            return self._load_data().revisions

    def add_document(
        self,
        document: Document,
        initial_version: TextVersion,
    ) -> None:
        """在一次原子写入中保存文档和它的第一版正文。"""

        if document.current_version_id != initial_version.id:
            raise StorageError("文档的当前版本必须指向同时保存的第一版正文")
        if initial_version.document_id != document.id:
            raise StorageError("第一版正文必须属于同时保存的文档")
        if initial_version.version_number != 1:
            raise StorageError("新文档的第一版正文版本号必须是 1")

        with self._lock:
            data = self._load_data()
            if any(existing.id == document.id for existing in data.documents):
                raise StorageError(f"文档已经存在：{document.id}")
            if any(
                existing.id == initial_version.id
                for existing in data.text_versions
            ):
                raise StorageError(f"正文版本已经存在：{initial_version.id}")

            data.documents.append(document)
            data.text_versions.append(initial_version)
            self._validate_relationships(data.documents, data.text_versions)
            self._write_data(data)

    def get_document(self, document_id: str) -> Document:
        with self._lock:
            for document in self._load_data().documents:
                if document.id == document_id:
                    return document
        raise DocumentNotFoundError(f"找不到文档：{document_id}")

    def list_documents(self) -> list[Document]:
        with self._lock:
            return self._load_data().documents

    def get_text_version(self, version_id: str) -> TextVersion:
        with self._lock:
            for version in self._load_data().text_versions:
                if version.id == version_id:
                    return version
        raise TextVersionNotFoundError(f"找不到正文版本：{version_id}")

    def list_text_versions(self, document_id: str) -> list[TextVersion]:
        with self._lock:
            data = self._load_data()
            if not any(document.id == document_id for document in data.documents):
                raise DocumentNotFoundError(f"找不到文档：{document_id}")
            versions = [
                version
                for version in data.text_versions
                if version.document_id == document_id
            ]
            return sorted(versions, key=lambda version: version.version_number)

    def save_decision(self, record: RevisionRecord) -> None:
        """一次写入决定与正式版本，避免只保存其中一半。"""

        with self._lock:
            data = self._load_data()
            saved = self._record_in(data, record.id)
            if saved.status is not RevisionStatus.PENDING:
                raise InvalidDecisionError("这条修订已经作出决定，不能重复操作")
            for name in (
                "original_text", "instruction", "candidate", "document_id",
                "base_version_id", "previous_record_id", "model", "created_at",
            ):
                if getattr(saved, name) != getattr(record, name):
                    raise InvalidDecisionError("保存决定时不能改写修订的原始信息")
            if record.status not in {RevisionStatus.ACCEPTED, RevisionStatus.REJECTED}:
                raise InvalidDecisionError("决定必须是接受或拒绝")
            if record.document_id is not None and record.status is RevisionStatus.ACCEPTED:
                document = self._document_in(data, record.document_id)
                if document.current_version_id != record.base_version_id:
                    raise VersionConflictError("正文已变化，旧候选不能覆盖当前版本；请放弃后重新修订")
                version = TextVersion.create_next(
                    document_id=document.id,
                    version_number=self._next_number(data, document.id),
                    content=record.candidate.text,
                    source_revision_id=record.id,
                )
                data.text_versions.append(version)
                document.current_version_id = version.id
                document.updated_at = version.created_at
            index = next(i for i, item in enumerate(data.revisions) if item.id == record.id)
            data.revisions[index] = record
            self._write_data(data)

    def close_task(self, record_id: str) -> RevisionRecord:
        """放弃未决定候选，或停止已拒绝任务的后续重试。"""

        with self._lock:
            data = self._load_data()
            record = self._record_in(data, record_id)
            if any(item.previous_record_id == record.id for item in data.revisions):
                raise InvalidDecisionError("该记录已有下一次尝试，请操作任务的最后一条记录")
            if record.status is RevisionStatus.PENDING:
                record.cancel()
            elif record.status is RevisionStatus.REJECTED and record.abandoned_at is None:
                record.abandoned_at = utc_now()
            else:
                raise InvalidDecisionError("该任务已经结束，不能重复放弃")
            self._write_data(data)
            return record

    def list_unfinished(self, document_id: str | None = None) -> list[RevisionRecord]:
        with self._lock:
            data = self._load_data()
            records = self._unfinished_records(data.revisions)
            if document_id is None:
                return records
            self._document_in(data, document_id)
            return [record for record in records if record.document_id == document_id]

    def restore_version(
        self,
        document_id: str,
        version_id: str,
        *,
        expected_current_version_id: str,
    ) -> TextVersion:
        """复制旧内容成为新版本，保留全部正文和修订历史。"""

        with self._lock:
            data = self._load_data()
            document = self._document_in(data, document_id)
            source = self._version_in(data, version_id)
            if source.document_id != document.id:
                raise InvalidDecisionError("不能恢复另一篇文档的正文")
            if document.current_version_id != expected_current_version_id:
                raise VersionConflictError("确认恢复期间正文已经变化，请重新打开后操作")
            version = TextVersion.create_next(
                document_id=document.id,
                version_number=self._next_number(data, document.id),
                content=source.content,
                restored_from_version_id=source.id,
            )
            data.text_versions.append(version)
            document.current_version_id = version.id
            document.updated_at = version.created_at
            self._write_data(data)
            return version

    @staticmethod
    def _record_in(data: _StoredData, record_id: str) -> RevisionRecord:
        for record in data.revisions:
            if record.id == record_id:
                return record
        raise RecordNotFoundError(f"找不到修订记录：{record_id}")

    @staticmethod
    def _document_in(data: _StoredData, document_id: str) -> Document:
        for document in data.documents:
            if document.id == document_id:
                return document
        raise DocumentNotFoundError(f"找不到文档：{document_id}")

    @staticmethod
    def _version_in(data: _StoredData, version_id: str) -> TextVersion:
        for version in data.text_versions:
            if version.id == version_id:
                return version
        raise TextVersionNotFoundError(f"找不到正文版本：{version_id}")

    @staticmethod
    def _next_number(data: _StoredData, document_id: str) -> int:
        numbers = [v.version_number for v in data.text_versions if v.document_id == document_id]
        return max(numbers, default=0) + 1

    @staticmethod
    def _unfinished_records(records: list[RevisionRecord]) -> list[RevisionRecord]:
        parents = {record.previous_record_id for record in records if record.previous_record_id}
        return [
            record for record in records
            if record.id not in parents
            and record.abandoned_at is None
            and (
                record.status is RevisionStatus.PENDING
                or (
                    record.status is RevisionStatus.REJECTED
                    and bool(record.instruction)
                    and bool(record.rejection_reason)
                )
            )
        ]

    def _load_data(self) -> _StoredData:
        if not self._file_path.exists():
            return _StoredData(
                documents=[],
                text_versions=[],
                revisions=[],
                source_schema_version=SCHEMA_VERSION,
            )

        try:
            with self._file_path.open("r", encoding="utf-8") as file:
                raw_document = json.load(file)
        except json.JSONDecodeError as exc:
            raise StorageError(
                f"数据文件不是合法 JSON，已停止写入以保护原文件：{self._file_path}"
            ) from exc
        except (OSError, UnicodeError) as exc:
            raise StorageError(f"无法读取数据文件：{exc}") from exc

        try:
            return self._parse_data(raw_document)
        except (KeyError, TypeError, ValueError) as exc:
            raise StorageError(
                f"数据文件结构不正确，已停止写入以保护原文件：{self._file_path}"
            ) from exc

    def _parse_data(self, raw_document: Any) -> _StoredData:
        if not isinstance(raw_document, dict):
            raise ValueError("根节点必须是对象")

        schema_version = raw_document.get("schema_version")
        if type(schema_version) is not int:
            raise ValueError("结构版本必须是整数")
        revisions_data = raw_document.get("revisions")
        if not isinstance(revisions_data, list):
            raise ValueError("revisions 必须是数组")

        revisions = [RevisionRecord.from_dict(item) for item in revisions_data]
        self._validate_unique_ids(revisions, "修订记录")

        if schema_version == LEGACY_SCHEMA_VERSION:
            if any(record.document_id is not None for record in revisions):
                raise ValueError("第一版文件不能包含缺失文档集合的关联记录")
            return _StoredData(
                documents=[],
                text_versions=[],
                revisions=revisions,
                source_schema_version=LEGACY_SCHEMA_VERSION,
            )

        if schema_version != SCHEMA_VERSION:
            raise ValueError("不支持的数据版本")

        documents_data = raw_document.get("documents")
        versions_data = raw_document.get("text_versions")
        if not isinstance(documents_data, list):
            raise ValueError("documents 必须是数组")
        if not isinstance(versions_data, list):
            raise ValueError("text_versions 必须是数组")

        documents = [Document.from_dict(item) for item in documents_data]
        text_versions = [TextVersion.from_dict(item) for item in versions_data]
        self._validate_unique_ids(documents, "文档")
        self._validate_unique_ids(text_versions, "正文版本")
        self._validate_relationships(documents, text_versions)
        self._validate_records(revisions, documents, text_versions)

        return _StoredData(
            documents=documents,
            text_versions=text_versions,
            revisions=revisions,
            source_schema_version=SCHEMA_VERSION,
        )

    @staticmethod
    def _validate_unique_ids(items: list[Any], item_name: str) -> None:
        identifiers: set[str] = set()
        for item in items:
            if not item.id:
                raise ValueError(f"{item_name} ID 不能为空")
            if item.id in identifiers:
                raise ValueError(f"{item_name} ID 重复：{item.id}")
            identifiers.add(item.id)

    @staticmethod
    def _validate_relationships(
        documents: list[Document],
        text_versions: list[TextVersion],
    ) -> None:
        documents_by_id = {document.id: document for document in documents}
        versions_by_id = {version.id: version for version in text_versions}
        version_numbers: set[tuple[str, int]] = set()

        for version in text_versions:
            if version.document_id not in documents_by_id:
                raise ValueError("正文版本指向了不存在的文档")
            number_key = (version.document_id, version.version_number)
            if number_key in version_numbers:
                raise ValueError("同一文档的正文版本号不能重复")
            version_numbers.add(number_key)

        for document in documents:
            current_version = versions_by_id.get(document.current_version_id)
            if current_version is None:
                raise ValueError("文档指向了不存在的当前正文版本")
            if current_version.document_id != document.id:
                raise ValueError("文档的当前正文版本属于其他文档")

    @staticmethod
    def _validate_records(
        records: list[RevisionRecord],
        documents: list[Document],
        versions: list[TextVersion],
    ) -> None:
        records_by_id = {record.id: record for record in records}
        documents_by_id = {document.id: document for document in documents}
        versions_by_id = {version.id: version for version in versions}
        used_parents: set[str] = set()
        for record in records:
            if record.status is RevisionStatus.PENDING:
                if record.final_text is not None or record.decided_at is not None:
                    raise ValueError("等待决定的记录不能含最终正文或决定时间")
            else:
                expected = record.candidate.text if record.status is RevisionStatus.ACCEPTED else record.original_text
                if record.final_text != expected or record.decided_at is None:
                    raise ValueError("修订状态与最终正文、决定时间不一致")
            if record.abandoned_at is not None and record.status is not RevisionStatus.REJECTED:
                raise ValueError("只有已拒绝记录可以标记停止重试")
            if record.document_id is not None:
                base = versions_by_id.get(record.base_version_id)
                if record.document_id not in documents_by_id or base is None:
                    raise ValueError("修订指向不存在的文档或基准版本")
                if base.document_id != record.document_id or base.content != record.original_text:
                    raise ValueError("修订原文与所属文档的基准正文不一致")
            parent_id = record.previous_record_id
            if parent_id is not None:
                parent = records_by_id.get(parent_id)
                if parent is None or parent.status is not RevisionStatus.REJECTED:
                    raise ValueError("反馈链的上一条必须是已拒绝记录")
                if parent_id in used_parents or parent.abandoned_at is not None:
                    raise ValueError("反馈链不能分叉或接到已放弃的任务")
                used_parents.add(parent_id)
                for name in ("document_id", "base_version_id", "original_text", "instruction"):
                    if getattr(parent, name) != getattr(record, name):
                        raise ValueError("反馈链混入不同文档、原文或修改要求")
                seen = {record.id}
                cursor = parent
                while cursor is not None:
                    if cursor.id in seen:
                        raise ValueError("反馈链不能成环")
                    seen.add(cursor.id)
                    cursor = records_by_id.get(cursor.previous_record_id)
        source_ids: set[str] = set()
        for version in versions:
            if version.source_revision_id is not None:
                record = records_by_id.get(version.source_revision_id)
                if record is None or record.status is not RevisionStatus.ACCEPTED:
                    raise ValueError("正文来源必须是已接受修订")
                if record.document_id != version.document_id or record.final_text != version.content:
                    raise ValueError("正文与来源修订不一致")
                if record.id in source_ids:
                    raise ValueError("同一次接受不能产生多个正文版本")
                source_ids.add(record.id)
            if version.restored_from_version_id is not None:
                source = versions_by_id.get(version.restored_from_version_id)
                if source is None or source.document_id != version.document_id:
                    raise ValueError("恢复版本来源不存在或属于另一篇文档")
                if source.version_number >= version.version_number or source.content != version.content:
                    raise ValueError("恢复版本必须复制更早的同文档正文")
        for record in records:
            if record.document_id is not None and record.status is RevisionStatus.ACCEPTED:
                if record.id not in source_ids:
                    raise ValueError("已接受文档修订缺少对应正文版本")

    def _write_data(self, data: _StoredData) -> None:
        try:
            self._parse_data(data.to_dict())
        except (KeyError, TypeError, ValueError) as exc:
            raise StorageError("待保存的数据结构或关联不正确，未写入文件") from exc

        temporary_path: Path | None = None
        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            if data.source_schema_version == LEGACY_SCHEMA_VERSION:
                self._ensure_legacy_backup()
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=self._file_path.parent,
                prefix=f".{self._file_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(
                    data.to_dict(),
                    temporary_file,
                    ensure_ascii=False,
                    indent=2,
                )
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())

            os.replace(temporary_path, self._file_path)
        except OSError as exc:
            raise StorageError(f"无法保存数据文件：{exc}") from exc
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink(missing_ok=True)

    def _ensure_legacy_backup(self) -> None:
        backup_path = self.legacy_backup_path
        try:
            if backup_path.exists():
                if backup_path.read_bytes() == self._file_path.read_bytes():
                    return
                raise StorageError(
                    "第一版备份文件已经存在且内容不同，已停止迁移以保护数据："
                    f"{backup_path}"
                )
            shutil.copy2(self._file_path, backup_path)
        except StorageError:
            raise
        except OSError as exc:
            raise StorageError(f"无法备份第一版数据文件，已停止迁移：{exc}") from exc


class JsonRevisionStore(JsonProjectStore):
    """旧类名的兼容入口；现有修订工作流可以继续使用。"""
