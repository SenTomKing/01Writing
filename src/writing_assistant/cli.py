"""命令行用户界面。"""

from collections.abc import Callable

from .config import AppConfig, load_config
from .errors import WritingAssistantError
from .llm.deepseek import DeepSeekCandidateGenerator
from .models import RevisionRecord
from .storage.json_store import JsonRevisionStore
from .workflow import RevisionWorkflow


HISTORY_PREVIEW_CHARS = 40


def build_workflow(config: AppConfig) -> RevisionWorkflow:
    generator = DeepSeekCandidateGenerator(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        timeout_seconds=config.request_timeout_seconds,
        max_candidate_chars=config.max_input_chars * 2,
    )
    store = JsonRevisionStore(config.data_file)
    return RevisionWorkflow(
        generator=generator,
        store=store,
        max_input_chars=config.max_input_chars,
    )


def read_decision(input_function: Callable[[str], str] = input) -> bool:
    """持续询问，直到获得明确的接受或拒绝。"""

    accepted_values = {"y", "yes", "接受"}
    rejected_values = {"n", "no", "拒绝"}
    while True:
        choice = input_function("\n是否接受修改？(y/n)：").strip().lower()
        if choice in accepted_values:
            return True
        if choice in rejected_values:
            return False
        print("请输入 y 或 n。")


def read_action(input_function: Callable[[str], str] = input) -> str:
    """持续询问，直到使用者选择新建修订或查看历史。"""

    while True:
        print("\n请选择操作：")
        print("1. 新建修订")
        print("2. 查看修订历史")
        choice = input_function("请输入 1 或 2：").strip()
        if choice in {"1", "2"}:
            return choice
        print("请输入 1 或 2。")


def make_text_preview(
    text: str | None,
    *,
    max_chars: int = HISTORY_PREVIEW_CHARS,
) -> str:
    """把多行长文本整理成适合历史列表显示的单行摘要。"""

    if text is None:
        return "尚未决定"

    single_line_text = " ".join(text.split())
    if len(single_line_text) <= max_chars:
        return single_line_text
    return f"{single_line_text[:max_chars]}…"


def display_history(records: list[RevisionRecord]) -> None:
    """在终端显示修订历史，不改变任何记录。"""

    if not records:
        print("\n暂无修订记录。")
        return

    print(f"\n共有 {len(records)} 条修订记录：")
    for index, record in enumerate(records, start=1):
        print(f"\n[{index}] 状态：{record.status.value}")
        print(f"记录 ID：{record.id}")
        print(f"原文：{make_text_preview(record.original_text)}")
        print(f"最终正文：{make_text_preview(record.final_text)}")
        print(f"创建时间：{record.created_at}")


def main() -> int:
    try:
        config = load_config()
        workflow = build_workflow(config)

        action = read_action()
        if action == "2":
            records = workflow.list_history()
            display_history(records)
            return 0

        original_text = input("请输入一段文字：")
        record = workflow.create_candidate(original_text)

        print("\n原文：")
        print(record.original_text)
        print("\n修改候选：")
        print(record.candidate.text)
        print("\n修改原因：")
        print(record.candidate.reason)

        accept = read_decision()
        decided_record = workflow.decide(record.id, accept=accept)

        if accept:
            print("\n已接受修改。")
        else:
            print("\n已拒绝修改，保留原文。")
        print("最终正文：")
        print(decided_record.final_text)
        print(f"\n修订记录已保存到：{config.data_file}")
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\n操作已取消。")
        return 130
    except WritingAssistantError as exc:
        print(f"\n错误：{exc}")
        return 1
