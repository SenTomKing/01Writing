"""命令行用户界面。"""

from collections.abc import Callable

from .config import AppConfig, load_config
from .errors import WritingAssistantError
from .llm.deepseek import DeepSeekCandidateGenerator
from .storage.json_store import JsonRevisionStore
from .workflow import RevisionWorkflow


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


def main() -> int:
    try:
        config = load_config()
        workflow = build_workflow(config)

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
