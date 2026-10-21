"""组织“生成候选—保存待确认记录—人工决定”的业务流程。"""

from .errors import InvalidDecisionError
from .llm.base import CandidateGenerator
from .models import RevisionRecord, RevisionStatus
from .storage.base import RevisionStore
from .validation import validate_candidate, validate_input


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

    def create_candidate(self, original_text: str) -> RevisionRecord:
        """校验原文、生成候选，并在返回前保存 pending 记录。"""

        normalized_text = validate_input(
            original_text,
            max_chars=self._max_input_chars,
        )
        candidate = self._generator.generate(normalized_text)
        candidate = validate_candidate(
            candidate,
            max_text_chars=self._max_input_chars * 2,
        )
        record = RevisionRecord.create_pending(
            original_text=normalized_text,
            candidate=candidate,
            model=self._generator.model_name,
        )
        self._store.add(record)
        return record

    def decide(self, record_id: str, *, accept: bool) -> RevisionRecord:
        """接受或拒绝一条仍处于 pending 状态的记录。"""

        record = self._store.get(record_id)
        if record.status is not RevisionStatus.PENDING:
            raise InvalidDecisionError("这条修订已经作出决定，不能重复操作")

        try:
            if accept:
                record.accept()
            else:
                record.reject()
        except ValueError as exc:
            raise InvalidDecisionError(str(exc)) from exc

        self._store.update(record)
        return record

    def list_history(self) -> list[RevisionRecord]:
        """返回全部修订记录，供命令行界面展示历史。"""

        return self._store.list_all()
