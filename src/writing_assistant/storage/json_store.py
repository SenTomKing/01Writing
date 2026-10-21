"""使用本地 JSON 文件保存修订记录。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Any

from ..errors import RecordNotFoundError, StorageError
from ..models import RevisionRecord


SCHEMA_VERSION = 1


class JsonRevisionStore:
    """带格式校验和原子写入的 JSON 存储。"""

    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        self._lock = RLock()

    def add(self, record: RevisionRecord) -> None:
        with self._lock:
            records = self._load_records()
            if any(existing.id == record.id for existing in records):
                raise StorageError(f"修订记录已经存在：{record.id}")
            records.append(record)
            self._write_records(records)

    def get(self, record_id: str) -> RevisionRecord:
        with self._lock:
            for record in self._load_records():
                if record.id == record_id:
                    return record
        raise RecordNotFoundError(f"找不到修订记录：{record_id}")

    def update(self, record: RevisionRecord) -> None:
        with self._lock:
            records = self._load_records()
            for index, existing in enumerate(records):
                if existing.id == record.id:
                    records[index] = record
                    self._write_records(records)
                    return
        raise RecordNotFoundError(f"找不到修订记录：{record.id}")

    def list_all(self) -> list[RevisionRecord]:
        with self._lock:
            return self._load_records()

    def _load_records(self) -> list[RevisionRecord]:
        if not self._file_path.exists():
            return []

        try:
            with self._file_path.open("r", encoding="utf-8") as file:
                document = json.load(file)
        except json.JSONDecodeError as exc:
            raise StorageError(
                f"数据文件不是合法 JSON，已停止写入以保护原文件：{self._file_path}"
            ) from exc
        except OSError as exc:
            raise StorageError(f"无法读取数据文件：{exc}") from exc

        try:
            self._validate_document(document)
            return [RevisionRecord.from_dict(item) for item in document["revisions"]]
        except (KeyError, TypeError, ValueError) as exc:
            raise StorageError(
                f"数据文件结构不正确，已停止写入以保护原文件：{self._file_path}"
            ) from exc

    def _write_records(self, records: list[RevisionRecord]) -> None:
        document = {
            "schema_version": SCHEMA_VERSION,
            "revisions": [record.to_dict() for record in records],
        }
        self._file_path.parent.mkdir(parents=True, exist_ok=True)

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=self._file_path.parent,
                prefix=f".{self._file_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                json.dump(document, temporary_file, ensure_ascii=False, indent=2)
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
                temporary_path = Path(temporary_file.name)

            os.replace(temporary_path, self._file_path)
        except OSError as exc:
            raise StorageError(f"无法保存数据文件：{exc}") from exc
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _validate_document(document: Any) -> None:
        if not isinstance(document, dict):
            raise ValueError("根节点必须是对象")
        if document.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("不支持的数据版本")
        if not isinstance(document.get("revisions"), list):
            raise ValueError("revisions 必须是数组")
