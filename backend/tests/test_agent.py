from copy import deepcopy
import json

import pytest

from writing_server.agent import run_agent
from writing_server.generation import ModelFailure
from writing_server.knowledge import search
from writing_server.settings import Settings


SETTINGS = Settings(database_url="postgresql+psycopg://unused", migration_url=None)
SOURCE = "本实验共有12个样本啊。"
CONTEXT = {
    "sources": [{"id": "public-example", "title": "实验记录", "content": "本实验共有12个样本，均完成随访。"}],
    "memories": [{"title": "写作习惯", "content": "不夸大结论"}],
}


def run(**kwargs):
    return run_agent(
        SETTINGS, source=SOURCE, instruction="保持数字，删除口语", feedback=[], provider="demo", model="test", **kwargs
    )


def tool(name, arguments):
    return {
        "model": "scripted",
        "usage": {"total_tokens": 10},
        "message": {
            "tool_calls": [
                {"id": "call_test", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
            ]
        },
    }


def test_demo_dynamic_path_citations_and_budget():
    result = run(context=CONTEXT)
    assert result.candidate.text == "本实验共有12个样本。 [S1:0]"
    assert [step["tool"] for step in result.usage["trace"]] == ["read_preferences", "read_source", "submit_revision"]
    assert result.usage["reserved_calls"] == 3
    assert result.usage["citations"][0]["quote"] in CONTEXT["sources"][0]["content"]


def test_interruption_resumes_saved_tool_selection_without_repeating_model():
    snapshots = []

    def save(state, message):
        snapshots.append(deepcopy(state))
        if state["next"] == "tools":
            raise InterruptedError("模拟进程中断")

    with pytest.raises(InterruptedError):
        run(context=CONTEXT, save=save)
    result = run(context=CONTEXT, checkpoint=snapshots[-1])
    assert result.usage["reserved_calls"] == 3
    assert len(result.usage["trace"]) == 3


def test_pre_call_reservation_survives_crash():
    snapshots = []

    def save(state, message):
        snapshots.append(deepcopy(state))
        raise InterruptedError()

    with pytest.raises(InterruptedError):
        run(save=save)
    assert snapshots[-1]["reserved_calls"] == 1
    assert run(checkpoint=snapshots[-1]).usage["reserved_calls"] == 3


@pytest.mark.parametrize(
    "name,args",
    [
        ("shell", {"command": "ignored"}),
        ("read_source", {"ref": "S99:0"}),
        ("read_preferences", {"user_id": "someone-else"}),
    ],
)
def test_disallowed_or_malformed_tools_stop_at_limit(name, args):
    with pytest.raises(ModelFailure) as caught:
        run(context=CONTEXT, completion=lambda *_: tool(name, args))
    assert caught.value.code == "agent_step_limit"
    assert caught.value.usage["reserved_calls"] == 6
    assert all(step["outcome"] == "未通过" for step in caught.value.usage["trace"])


@pytest.mark.parametrize(
    "text,quote",
    [
        ("本实验共有13个样本。 [S1:0]", "本实验共有12个样本"),
        ("本实验共有12个样本。 [S1:0]", "这是一段伪造的原文"),
        ("本实验共有12个样本。 [S9:0]", "本实验共有12个样本"),
    ],
)
def test_fabricated_citations_and_changed_numbers_never_submit(text, quote):
    count = 0

    def scripted(*_):
        nonlocal count
        count += 1
        if count == 1:
            return tool("read_source", {"ref": "S1:0"})
        return tool("submit_revision", {"text": text, "reason": "测试", "citations": [{"ref": "S1:0", "quote": quote}]})

    with pytest.raises(ModelFailure) as caught:
        run(context={"sources": CONTEXT["sources"], "memories": []}, completion=scripted)
    assert caught.value.code == "agent_step_limit"


def test_utf8_context_budget_prevents_model_call():
    with pytest.raises(ModelFailure) as caught:
        run_agent(SETTINGS, source="中" * 20000, instruction="改写", feedback=[], provider="demo", model="x")
    assert caught.value.code == "context_budget"


def test_search_uses_selected_sources_and_returns_locatable_excerpts():
    result = search(CONTEXT["sources"], "样本随访")
    assert result[0]["ref"] == "S1:0"
    assert result[0]["text"] == CONTEXT["sources"][0]["content"]
    assert search(CONTEXT["sources"], "不相干词") == []


def test_insufficient_evidence_stops_and_requests_human_input():
    snapshots = []
    with pytest.raises(ModelFailure) as caught:
        run(
            context=CONTEXT,
            completion=lambda *_: tool("request_clarification", {"question": "请提供实验原始记录"}),
            save=lambda state, message: snapshots.append(state),
        )
    assert caught.value.code == "evidence_needed"
    assert caught.value.usage["reserved_calls"] == 1
    assert snapshots[-1]["next"] == "done"
    assert snapshots[-1]["result"] is None


@pytest.mark.parametrize("recorded", ["agent-v2.0", None])
def test_resumed_checkpoint_keeps_prompt_provenance(recorded):
    snapshots = []

    def save(state, message):
        snapshots.append(deepcopy(state))
        if state["next"] == "tools":
            raise InterruptedError()

    with pytest.raises(InterruptedError):
        run(context=CONTEXT, save=save)
    checkpoint = snapshots[-1]
    # 恢复旧请求时，不能因为代码升级就把原有提示词冒记为新版本。
    checkpoint["messages"][0]["content"] = "旧版本系统提示词"
    if recorded is None:
        checkpoint.pop("prompt_version")
    else:
        checkpoint["prompt_version"] = recorded
    result = run(context=CONTEXT, checkpoint=checkpoint)
    assert result.usage["prompt_version"] == "agent-v2.0"
    assert result.usage["reserved_calls"] == 3
