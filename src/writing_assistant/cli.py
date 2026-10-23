"""持续运行的中文命令行入口。"""

import argparse
from collections.abc import Callable

from .config import AppConfig, get_project_root, load_config
from .document_workflow import DocumentWorkflow
from .errors import ConfigurationError, InvalidDecisionError, ValidationError, WritingAssistantError
from .llm.base import UnavailableCandidateGenerator
from .llm.deepseek import DeepSeekCandidateGenerator
from .llm.demo import DemoCandidateGenerator
from .models import Document, RevisionRecord, RevisionRequest, RevisionStatus, TextVersion
from .storage.json_store import JsonProjectStore
from .validation import validate_rejection_reason
from .workflow import RevisionWorkflow


HISTORY_PREVIEW_CHARS = 40
STATUS_LABELS = {
    RevisionStatus.PENDING: "等待决定", RevisionStatus.ACCEPTED: "已接受",
    RevisionStatus.REJECTED: "已拒绝", RevisionStatus.CANCELLED: "已放弃",
}
MENU = {
    "1": "新建独立修订", "2": "查看全部修订历史", "3": "创建文档",
    "4": "查看文档列表", "5": "打开文档", "6": "修改文档当前正文",
    "7": "查看文档正文版本", "8": "恢复旧正文版本", "9": "查看未完成任务",
    "10": "继续未完成任务", "11": "查看修订详情", "12": "查看一篇文档的修订历史",
    "0": "退出程序",
}


def build_workflow(
    config: AppConfig,
    *,
    store: JsonProjectStore | None = None,
    demo: bool = False,
    allow_without_key: bool = False,
) -> RevisionWorkflow:
    if demo:
        generator = DemoCandidateGenerator()
    elif config.api_key is None:
        if not allow_without_key:
            raise ConfigurationError("使用 AI 修订前必须在 .env 中填写 DEEPSEEK_API_KEY")
        generator = UnavailableCandidateGenerator()
    else:
        generator = DeepSeekCandidateGenerator(
            api_key=config.api_key, base_url=config.base_url, model=config.model,
            timeout_seconds=config.request_timeout_seconds,
            max_candidate_chars=config.max_input_chars * 2,
        )
    return RevisionWorkflow(
        generator=generator, store=store or JsonProjectStore(config.data_file),
        max_input_chars=config.max_input_chars,
    )


def build_document_workflow(
    config: AppConfig, *, store: JsonProjectStore | None = None,
) -> DocumentWorkflow:
    return DocumentWorkflow(
        store=store or JsonProjectStore(config.data_file),
        max_content_chars=config.max_input_chars,
    )


def read_action(input_function: Callable[[str], str] = input) -> str:
    while True:
        print("\n请选择操作：")
        for number, label in MENU.items():
            print(f"{number}. {label}")
        choice = input_function("请输入 0 到 12：").strip()
        if choice in MENU:
            return choice
        print("请输入 0 到 12。")


def read_decision(input_function: Callable[[str], str] = input) -> bool:
    while True:
        choice = input_function("是否确认？(y/n)：").strip().lower()
        if choice in {"y", "yes", "接受"}:
            return True
        if choice in {"n", "no", "拒绝"}:
            return False
        print("请输入 y 或 n。")


def read_session_decision(input_function: Callable[[str], str] = input) -> str:
    choices = {"y": "accept", "接受": "accept", "n": "reject", "拒绝": "reject",
               "p": "later", "稍后": "later", "x": "cancel", "放弃": "cancel"}
    while True:
        value = input_function("\n接受 y / 拒绝 n / 稍后处理 p / 放弃任务 x：").strip().lower()
        if value in choices:
            return choices[value]
        print("请输入 y、n、p 或 x。")


def read_rejection_reason(input_function: Callable[[str], str] = input) -> str:
    while True:
        try:
            return validate_rejection_reason(input_function("请输入拒绝原因："))
        except ValidationError as exc:
            print(f"请输入有效的拒绝原因：{exc}")


def make_text_preview(text: str | None, *, max_chars: int = HISTORY_PREVIEW_CHARS) -> str:
    if text is None:
        return "尚未决定"
    single_line_text = " ".join(text.split())
    if len(single_line_text) <= max_chars:
        return single_line_text
    return f"{single_line_text[:max_chars]}…"


def display_history(records: list[RevisionRecord]) -> None:
    if not records:
        print("\n暂无修订记录。")
        return
    print(f"\n共有 {len(records)} 条修订记录：")
    for index, record in enumerate(records, start=1):
        print(f"\n[{index}] 状态：{STATUS_LABELS[record.status]}")
        print(f"记录编号：{record.id}")
        print(f"所属文档：{record.document_id or '独立修订'}")
        print(f"原文：{make_text_preview(record.original_text)}")
        instruction = make_text_preview(record.instruction) if record.instruction is not None else "未记录"
        print(f"修改要求：{instruction}")
        print(f"最终正文：{make_text_preview(record.final_text)}")
        if record.status is RevisionStatus.REJECTED:
            print(f"拒绝原因：{make_text_preview(record.rejection_reason or '未记录')}")
        if record.previous_record_id is not None:
            print(f"上一条记录编号：{record.previous_record_id}")
        if record.abandoned_at is not None:
            print(f"已停止重试：{record.abandoned_at}")
        print(f"创建时间：{record.created_at}")


def display_candidate(record: RevisionRecord) -> None:
    print("\n原文：")
    print(record.original_text)
    print("\n修改候选：")
    print(record.candidate.text)
    print("\n修改原因：")
    print(record.candidate.reason)


def display_record_detail(record: RevisionRecord) -> None:
    print(f"\n记录编号：{record.id}；状态：{STATUS_LABELS[record.status]}")
    print(f"所属文档：{record.document_id or '独立修订'}")
    print(f"基准正文版本：{record.base_version_id or '不适用'}")
    print(f"生成模型：{record.model}")
    print(f"修改要求：{record.instruction or '旧记录未记录'}")
    display_candidate(record)
    print(f"\n拒绝原因：{record.rejection_reason or '无'}")
    print(f"上一条记录：{record.previous_record_id or '无'}")
    print(f"创建时间：{record.created_at}；决定时间：{record.decided_at or '尚未决定'}")
    print(f"停止重试时间：{record.abandoned_at or '无'}")
    print("最终正文：")
    print(record.final_text if record.final_text is not None else "尚未决定")


def display_documents(documents: list[Document]) -> None:
    if not documents:
        print("\n暂无文档。")
        return
    print(f"\n共有 {len(documents)} 篇文档：")
    for index, document in enumerate(documents, start=1):
        print(f"\n[{index}] {document.title}")
        print(f"文档编号：{document.id}")
        print(f"当前正文版本编号：{document.current_version_id}")
        print(f"最近更新时间：{document.updated_at}")


def display_opened_document(document: Document, current_version: TextVersion) -> None:
    print(f"\n文档：{document.title}")
    print(f"文档编号：{document.id}")
    print(f"当前正文版本：第 {current_version.version_number} 版")
    print("\n当前正文：")
    print(current_version.content)


def display_versions(versions: list[TextVersion], current_version_id: str) -> None:
    for version in versions:
        marker = "（当前采用）" if version.id == current_version_id else ""
        print(f"\n第 {version.version_number} 版{marker}；版本编号：{version.id}")
        print(f"创建时间：{version.created_at}")
        print(f"来源修订：{version.source_revision_id or '无'}")
        print(f"恢复自版本：{version.restored_from_version_id or '无'}")
        print(version.content)


def run_create_document(
    workflow: DocumentWorkflow, input_function: Callable[[str], str] = input,
) -> tuple[Document, TextVersion]:
    title = input_function("请输入文档标题：")
    content = input_function("请输入初始正文（单行）：")
    document, version = workflow.create_document(title=title, initial_content=content)
    print("\n文档创建成功。")
    print(f"文档编号：{document.id}")
    print(f"当前正文版本：第 {version.version_number} 版")
    return document, version


def run_revision_session(
    workflow: RevisionWorkflow, input_function: Callable[[str], str] = input,
    *, document_id: str | None = None, record: RevisionRecord | None = None,
) -> RevisionRecord:
    if record is None:
        if document_id is None:
            source = input_function("请输入一段文字：")
            instruction = input_function("请输入本轮修改要求：")
            record = workflow.create_candidate(RevisionRequest(source, instruction))
        else:
            instruction = input_function("请输入本轮修改要求：")
            record = workflow.create_document_candidate(document_id, instruction)
    elif record.status not in {RevisionStatus.PENDING, RevisionStatus.REJECTED}:
        raise InvalidDecisionError("这条任务已结束，不能继续")
    while True:
        if record.status is RevisionStatus.REJECTED:
            request = workflow.get_retry_request(record.id)
            record = workflow.create_candidate(request, previous_record_id=record.id)
        display_candidate(record)
        choice = read_session_decision(input_function)
        if choice == "later":
            print(f"已保存待确认候选，可从未完成任务继续。记录编号：{record.id}")
            return record
        if choice == "cancel":
            record = workflow.close_task(record.id)
            print("已放弃任务，正式正文保持不变。")
            return record
        if choice == "accept":
            record = workflow.decide(record.id, accept=True)
            print("\n已接受修改。")
            print("最终正文：")
            print(record.final_text)
            return record
        reason = read_rejection_reason(input_function)
        record = workflow.decide(record.id, accept=False, rejection_reason=reason)
        if record.instruction is None:
            print("旧记录未保存修改要求，拒绝已保存；如需重试请新建修订。")
            return record
        print("\n已拒绝本次候选，将根据反馈重新生成。")


def run_action(
    action: str, config: AppConfig, store: JsonProjectStore,
    input_function: Callable[[str], str], *, demo: bool,
) -> None:
    documents = build_document_workflow(config, store=store)
    if action == "1":
        run_revision_session(build_workflow(config, store=store, demo=demo), input_function)
    elif action == "2":
        display_history(store.list_all())
    elif action == "3":
        run_create_document(documents, input_function)
    elif action == "4":
        display_documents(documents.list_documents())
    elif action in {"5", "6", "7", "8", "12"}:
        document, version = documents.open_document(input_function("请输入文档编号："))
        if action == "5":
            display_opened_document(document, version)
        elif action == "6":
            workflow = build_workflow(config, store=store, demo=demo)
            run_revision_session(workflow, input_function, document_id=document.id)
        elif action == "7":
            display_versions(documents.list_versions(document.id), document.current_version_id)
        elif action == "8":
            display_versions(documents.list_versions(document.id), document.current_version_id)
            version_id = input_function("请输入要恢复的正文版本编号：").strip()
            if read_decision(input_function):
                restored = store.restore_version(
                    document.id, version_id,
                    expected_current_version_id=document.current_version_id,
                )
                print(f"恢复成功，已追加为第 {restored.version_number} 版。")
        else:
            display_history([r for r in store.list_all() if r.document_id == document.id])
    elif action == "9":
        print("等待决定的候选可以直接确认；已拒绝任务可继续重试。")
        display_history(store.list_unfinished())
    elif action == "10":
        record = store.get(input_function("请输入未完成任务的记录编号：").strip())
        if record.status is RevisionStatus.REJECTED:
            print("继续根据已保存的拒绝反馈生成候选？y 继续；n 停止重试。")
        if record.status is RevisionStatus.REJECTED and not read_decision(input_function):
            store.close_task(record.id)
            print("已停止重试，正式正文保持不变。")
        else:
            workflow = build_workflow(config, store=store, demo=demo, allow_without_key=True)
            run_revision_session(workflow, input_function, record=record)
    elif action == "11":
        display_record_detail(store.get(input_function("请输入修订记录编号：").strip()))


def main(argv: list[str] | None = None, *, input_function: Callable[[str], str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="01Writing 文档修订工作台")
    parser.add_argument("--demo", action="store_true", help="离线演示，独立数据，不读取.env或调用模型")
    arguments = parser.parse_args(argv)
    read = input_function or input
    try:
        if arguments.demo:
            config = AppConfig(None, "", "离线演示", get_project_root() / "data/demo-project.json", 10000, 60.0)
            print("离线演示：只验证工作流程，不代表真实模型改写质量。")
        else:
            config = load_config()
        store = JsonProjectStore(config.data_file)
        print(f"项目数据：{config.data_file}")
        while True:
            action = read_action(read)
            if action == "0":
                print("已退出；保存的文档和未完成任务可在下次继续。")
                return 0
            try:
                run_action(action, config, store, read, demo=arguments.demo)
            except WritingAssistantError as exc:
                print(f"\n错误：{exc}\n已返回主菜单，已有数据保留。")
    except (KeyboardInterrupt, EOFError):
        print("\n操作已取消；已保存的任务可以从菜单继续。")
        return 130
    except WritingAssistantError as exc:
        print(f"\n错误：{exc}")
        return 1
