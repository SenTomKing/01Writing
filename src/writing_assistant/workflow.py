"""组织“生成候选—保存待确认记录—人工决定”的业务流程。"""

from .errors import InvalidDecisionError, ValidationError, VersionConflictError
from .llm.base import CandidateGenerator
from .models import (
    RevisionFeedback,
    RevisionRecord,
    RevisionRequest,
    RevisionStatus,
)
from .storage.base import ProjectStore
from .validation import (
    validate_candidate,
    validate_feedback,
    validate_input,
    validate_instruction,
    validate_rejection_reason,
)


class RevisionWorkflow:
    def __init__(
        self,
        *,
        generator: CandidateGenerator,
        store: ProjectStore,
        max_input_chars: int,
    ) -> None:
        self._generator = generator
        self._store = store
        self._max_input_chars = max_input_chars

    def create_candidate(
        self,
        request: RevisionRequest,
        *,
        previous_record_id: str | None = None,
        document_id: str | None = None,
        base_version_id: str | None = None,
    ) -> RevisionRecord:
        """校验修订请求、生成候选，并在返回前保存 pending 记录。"""

        if not isinstance(request, RevisionRequest):
            raise ValidationError("修订请求必须是 RevisionRequest")
        if not isinstance(request.feedback_history, tuple):
            raise ValidationError("反馈历史必须是 tuple")
        if previous_record_id is not None and (
            not isinstance(previous_record_id, str) or not previous_record_id.strip()
        ):
            raise ValidationError("上一条记录 ID 必须是非空字符串或 None")

        normalized_request = RevisionRequest(
            source_text=validate_input(
                request.source_text,
                max_chars=self._max_input_chars,
            ),
            instruction=validate_instruction(request.instruction),
            feedback_history=tuple(
                validate_feedback(
                    feedback,
                    max_candidate_chars=self._max_input_chars * 2,
                )
                for feedback in request.feedback_history
            ),
        )
        if previous_record_id is not None:
            previous_record = self._store.get(previous_record_id)
            if previous_record.status is not RevisionStatus.REJECTED:
                raise InvalidDecisionError("上一条记录必须是 rejected 状态")
            if previous_record.rejection_reason is None:
                raise InvalidDecisionError("上一条拒绝记录缺少拒绝原因")
            expected_feedback = RevisionFeedback(
                rejected_candidate=previous_record.candidate,
                user_feedback=previous_record.rejection_reason,
            )
            if (
                not normalized_request.feedback_history
                or normalized_request.feedback_history[-1] != expected_feedback
            ):
                raise InvalidDecisionError("反馈历史与上一条拒绝记录不一致")
            expected_request = self.get_retry_request(previous_record_id)
            if normalized_request != expected_request:
                raise InvalidDecisionError("反馈历史或原文、要求与已保存的任务不一致")
            if document_id is None:
                document_id = previous_record.document_id
                base_version_id = previous_record.base_version_id
            if (document_id, base_version_id) != (
                previous_record.document_id, previous_record.base_version_id,
            ):
                raise InvalidDecisionError("反馈重试不能切换文档或基准版本")
        elif normalized_request.feedback_history:
            raise InvalidDecisionError("反馈历史必须连接已保存的拒绝记录")

        if (document_id is None) != (base_version_id is None):
            raise ValidationError("文档和基准版本编号必须同时提供")
        if document_id is not None:
            document = self._store.get_document(document_id)
            base = self._store.get_text_version(base_version_id)
            if document.current_version_id != base.id or base.document_id != document.id:
                raise VersionConflictError("当前正文已经变化，请重新打开文档发起修订")
            if base.content != normalized_request.source_text:
                raise InvalidDecisionError("文档修订原文必须等于基准版本正文")
            for unfinished in self._store.list_unfinished(document.id):
                if unfinished.id != previous_record_id:
                    raise InvalidDecisionError("这篇文档还有未完成任务，请先继续或放弃")

        candidate = self._generator.generate(normalized_request)
        candidate = validate_candidate(
            candidate,
            max_text_chars=self._max_input_chars * 2,
        )
        record = RevisionRecord.create_pending(
            original_text=normalized_request.source_text,
            instruction=normalized_request.instruction,
            candidate=candidate,
            model=self._generator.model_name,
            previous_record_id=previous_record_id,
            document_id=document_id,
            base_version_id=base_version_id,
        )
        self._store.add(record)
        return record

    def decide(
        self,
        record_id: str,
        *,
        accept: bool,
        rejection_reason: str | None = None,
    ) -> RevisionRecord:
        """接受或拒绝一条仍处于 pending 状态的记录。"""

        record = self._store.get(record_id)
        if record.status is not RevisionStatus.PENDING:
            raise InvalidDecisionError("这条修订已经作出决定，不能重复操作")

        try:
            if accept:
                if rejection_reason is not None:
                    raise InvalidDecisionError("接受修改时不能提供拒绝原因")
                record.accept()
            else:
                if rejection_reason is None:
                    raise ValidationError("拒绝原因不能为空或只包含空白字符")
                normalized_reason = validate_rejection_reason(rejection_reason)
                record.reject(normalized_reason)
        except ValueError as exc:
            raise InvalidDecisionError(str(exc)) from exc

        self._store.save_decision(record)
        return record

    def create_retry_request(
        self,
        request: RevisionRequest,
        rejected_record_id: str,
    ) -> RevisionRequest:
        """根据已保存的拒绝记录，为下一次生成建立请求。"""

        if not isinstance(request, RevisionRequest):
            raise ValidationError("修订请求必须是 RevisionRequest")

        record = self._store.get(rejected_record_id)
        if record.status is not RevisionStatus.REJECTED:
            raise InvalidDecisionError("只有 rejected 记录可以用于反馈重试")
        if record.rejection_reason is None:
            raise InvalidDecisionError("拒绝记录缺少拒绝原因，无法继续生成")
        if record.instruction is None:
            raise InvalidDecisionError("拒绝记录缺少原修改要求，无法继续生成")

        restored = self.get_retry_request(record.id)
        if (
            validate_input(request.source_text, max_chars=self._max_input_chars) != restored.source_text
            or validate_instruction(request.instruction) != restored.instruction
            or request.feedback_history != restored.feedback_history[:-1]
        ):
            raise InvalidDecisionError("传入请求与已保存的反馈链不一致")
        return restored

    def create_document_candidate(self, document_id: str, instruction: str) -> RevisionRecord:
        """从磁盘的当前正文生成候选，关联文档与基准版本。"""

        document = self._store.get_document(document_id.strip())
        version = self._store.get_text_version(document.current_version_id)
        return self.create_candidate(
            RevisionRequest(source_text=version.content, instruction=instruction),
            document_id=document.id,
            base_version_id=version.id,
        )

    def get_retry_request(self, rejected_record_id: str) -> RevisionRequest:
        """仅根据持久化反馈链重建上下文，进程重启不会遗失此前反馈。"""

        record = self._store.get(rejected_record_id)
        if record.status is not RevisionStatus.REJECTED or record.abandoned_at is not None:
            raise InvalidDecisionError("只有尚未放弃的拒绝记录可以继续重试")
        if any(item.previous_record_id == record.id for item in self._store.list_all()):
            raise InvalidDecisionError("已经存在后续尝试，请继续任务最后一条记录")
        if not record.instruction or not record.rejection_reason:
            raise InvalidDecisionError("旧记录缺少修改要求或拒绝原因，请新建修订")
        feedback_history = []
        seen: set[str] = set()
        cursor = record
        while True:
            if cursor.id in seen:
                raise InvalidDecisionError("反馈链存在循环，不能恢复")
            seen.add(cursor.id)
            if cursor.status is not RevisionStatus.REJECTED or not cursor.rejection_reason:
                raise InvalidDecisionError("反馈链不完整，无法恢复")
            if (
                cursor.original_text != record.original_text
                or cursor.instruction != record.instruction
                or cursor.document_id != record.document_id
                or cursor.base_version_id != record.base_version_id
            ):
                raise InvalidDecisionError("反馈链包含不同任务的数据")
            feedback_history.append(RevisionFeedback(cursor.candidate, cursor.rejection_reason))
            if cursor.previous_record_id is None:
                break
            cursor = self._store.get(cursor.previous_record_id)
        feedback_history.reverse()
        return RevisionRequest(
            source_text=record.original_text,
            instruction=record.instruction,
            feedback_history=tuple(feedback_history),
        )

    def close_task(self, record_id: str) -> RevisionRecord:
        return self._store.close_task(record_id.strip())

    def list_unfinished(self, document_id: str | None = None) -> list[RevisionRecord]:
        return self._store.list_unfinished(document_id)

    def list_history(self) -> list[RevisionRecord]:
        """返回全部修订记录，供命令行界面展示历史。"""

        return self._store.list_all()
