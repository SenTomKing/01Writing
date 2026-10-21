"""组织“生成候选—保存待确认记录—人工决定”的业务流程。"""

from .errors import InvalidDecisionError, ValidationError
from .llm.base import CandidateGenerator
from .models import (
    RevisionFeedback,
    RevisionRecord,
    RevisionRequest,
    RevisionStatus,
)
from .storage.base import RevisionStore
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
        store: RevisionStore,
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

        self._store.update(record)
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

        feedback = RevisionFeedback(
            rejected_candidate=record.candidate,
            user_feedback=record.rejection_reason,
        )
        return RevisionRequest(
            source_text=record.original_text,
            instruction=record.instruction,
            feedback_history=request.feedback_history + (feedback,),
        )

    def list_history(self) -> list[RevisionRecord]:
        """返回全部修订记录，供命令行界面展示历史。"""

        return self._store.list_all()
