"""A／B／C 可复跑评测：程序检查与待人工语义复核分别报告。"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from writing_server.generation import ModelFailure, generate
from writing_server.settings import Settings


def checks(task, output):
    return {
        **{f"包含:{term}": term in output for term in task.get("required", [])},
        **{f"不包含:{term}": term not in output for term in task.get("forbidden", [])},
        **({"长度": len(output) <= task["max_chars"]} if "max_chars" in task else {}),
    }


def main():
    parser = argparse.ArgumentParser(description="修订 A／B／C 评测；默认离线演示，不作为真实模型效果")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--dataset", type=Path, help="可选的自建任务JSON数组")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--max-calls", type=int, default=300)
    parser.add_argument("--output", type=Path, default=Path("outputs/eval-report.json"))
    args = parser.parse_args()
    if not 1 <= args.limit <= 50 or not 1 <= args.max_calls <= 450:
        parser.error("limit 为1—50，max-calls 为1—450")
    settings = Settings.load()
    tasks = json.loads(Path(__file__).with_name("tasks.json").read_text(encoding="utf-8"))
    additions = json.loads(Path(__file__).with_name("agent_tasks.json").read_text(encoding="utf-8"))
    if args.dataset:
        tasks = []
        additions = json.loads(args.dataset.read_text(encoding="utf-8"))
    for task in additions:
        task["context"] = {"sources": [], "memories": []}
        for field, target in (("evidence", "sources"), ("memory", "memories")):
            if field in task:
                task["context"][target].append({"id": task["id"] + field, "title": task["group"], "content": task[field]})
    tasks = (tasks + additions)[:args.limit]
    if args.live and not settings.api_key:
        parser.error("真实评测需要服务端模型密钥")
    call_count = 0

    def budget():
        nonlocal call_count
        if call_count >= args.max_calls:
            raise RuntimeError("达到预设调用次数上限")
        if args.live:
            call_count += 1

    rows = []

    def save_report(completed=False):
        report = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "mode": "live" if args.live else "demo",
            "model": settings.model if args.live else "demo-v1",
            "prompt_version": "comparison-v2.0.1",
            "completed": completed,
            "completed_trials": len(rows),
            "planned_trials": len(tasks) * 3,
            "task_count": len(tasks),
            "dataset_version": args.dataset.name if args.dataset else "writing-eval-50-v2",
            "budget_exhausted": args.live and call_count >= args.max_calls,
            "model_calls": call_count,
            "semantic_task_success_rate": None,
            "human_acceptance_rate": None,
            "cost": None,
            "cost_note": "未配置价格或缺少用量时费用为未知；程序检查通过率不等于人工接受率或语义成功率。",
            "summary": {
                name: {
                    "trials": sum(r["strategy"] == name for r in rows),
                    "program_passed": sum(r["strategy"] == name and r["program_checks_passed"] for r in rows),
                }
                for name in ("direct", "workflow", "agent")
            },
            "results": rows,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(args.output)
        return report

    for task in tasks:
        for strategy in ("direct", "workflow", "agent"):
            if args.live and call_count >= args.max_calls:
                break
            started = time.monotonic()
            row = {"task_id": task["id"], "group": task["group"], "strategy": strategy, "repeat": task.get("repeat", 1)}
            try:
                result = generate(
                    settings,
                    source=task["source"],
                    instruction=task["instruction"],
                    feedback=task.get("feedback", []),
                    provider="deepseek" if args.live else "demo",
                    model=settings.model,
                    strategy=strategy,
                    on_call=budget,
                    context=task.get("context"),
                )
                row.update(
                    text=result.candidate.text,
                    reason=result.candidate.reason,
                    usage=result.usage,
                    checks=checks(task, result.candidate.text),
                    semantic_review="pending",
                    citations=(result.usage or {}).get("citations", []),
                    evidence_support_review="pending" if task.get("evidence") else "not_applicable",
                )
                row["program_checks_passed"] = all(row["checks"].values())
            except Exception as exc:
                row.update(
                    error=getattr(exc, "code", "evaluation_failed"),
                    error_message=str(exc) if isinstance(exc, ModelFailure) else "评测执行失败",
                    program_checks_passed=False,
                    usage=getattr(exc, "usage", None),
                    semantic_review="not_applicable",
                )
            row["duration_ms"] = int((time.monotonic() - started) * 1000)
            rows.append(row)
            save_report()
            print(f"{len(rows)}/{len(tasks)*3} {task['id']} {strategy}: {row.get('error', '完成')}，累计请求 {call_count}", flush=True)
    report = save_report(completed=len(rows) == len(tasks) * 3)
    print(
        json.dumps({key: report[key] for key in ("mode", "task_count", "model_calls", "summary")}, ensure_ascii=False)
    )
    print("报告已保存；请逐题复核数字对应关系、原意与修改完成度，真实费用与语义结果尚需核验。")


if __name__ == "__main__":
    main()
