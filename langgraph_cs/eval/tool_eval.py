"""
tool_eval —— 工具调用质量评测（should-call / tool selection / args accuracy）。

跑法（从仓库根目录，-m 模块方式保证 import 路径正确）：
    langgraph_cs/.venv/bin/python -m langgraph_cs.eval.tool_eval --self-test
    langgraph_cs/.venv/bin/python -m langgraph_cs.eval.tool_eval --limit 3
    langgraph_cs/.venv/bin/python -m langgraph_cs.eval.tool_eval --write-md

真实评测会联网调用 DeepSeek，并顺序执行每条 case 以控制 RPM。写操作工具会被拦截：
`nodes.tools.business_db.create_ticket` 在评测期间替换成记录器，避免技术工单评测污染演示库。
"""
from __future__ import annotations

import argparse
import json
import logging
import uuid
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).parent
DATASET_PATH = _BASE_DIR / "tool_dataset.json"
RESULTS_MD_PATH = _BASE_DIR / "tool_results.md"

MODEL_NAME = "deepseek-chat"
MODEL_TEMPERATURE = 0.5
AGENT_NODES = {"billing_agent", "technical_agent", "general_agent", "escalation"}


def load_dataset(path: Path = DATASET_PATH) -> list[dict]:
    """读取并校验工具评测数据集。"""
    items = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(items, list) or not items:
        raise ValueError(f"工具评测集为空或格式错误：{path}")
    for i, item in enumerate(items):
        if not item.get("id"):
            raise ValueError(f"第 {i} 条缺少 id")
        if not item.get("question"):
            raise ValueError(f"第 {i} 条缺少 question")
        expected = item.get("expected")
        if not isinstance(expected, dict) or "should_call" not in expected:
            raise ValueError(f"第 {i} 条 expected.should_call 缺失")
        if type(expected["should_call"]) is not bool:
            raise ValueError(f"第 {i} 条 expected.should_call 必须是 bool")
        if expected["should_call"]:
            if not expected.get("tool"):
                raise ValueError(f"第 {i} 条正例缺少 expected.tool")
            if not isinstance(expected.get("args", {}), dict):
                raise ValueError(f"第 {i} 条 expected.args 必须是 dict")
    return items


def _stable_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def _normalize_args(args: Any) -> dict:
    if isinstance(args, dict):
        return {str(k): v for k, v in args.items()}
    return {}


def _call_key(call: dict) -> tuple[str, str]:
    return (str(call.get("name") or ""), _stable_json(_normalize_args(call.get("args"))))


def merge_calls(calls: list[dict]) -> list[dict]:
    """
    合并同一轮内重复采集到的同一工具调用。

    create_refund_ticket 的审批流会同时从 AIMessage.tool_calls 和 approval interrupt
    两个来源采到同一调用；合并后保留来源标记，避免实际序列里重复显示一次业务动作。
    """
    merged: list[dict] = []
    by_key: dict[tuple[str, str], dict] = {}
    for raw in calls:
        call = {
            "name": str(raw.get("name") or ""),
            "args": _normalize_args(raw.get("args")),
            "source": str(raw.get("source") or "unknown"),
        }
        key = _call_key(call)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = call
            merged.append(call)
            continue
        sources = set(str(existing.get("source") or "").split("+"))
        sources.add(call["source"])
        existing["source"] = "+".join(sorted(s for s in sources if s))
    return merged


def args_match(expected_args: dict, actual_args: dict) -> tuple[bool, str]:
    """
    比对关键参数。

    id 类字段精确匹配；expected 值为 "*" 时表示该字段只要非空即可。
    只检查 expected 中声明的关键字段，允许模型额外传入无害字段。
    """
    actual_args = _normalize_args(actual_args)
    for key, expected_value in (expected_args or {}).items():
        actual_value = actual_args.get(key)
        if expected_value == "*":
            if actual_value is None or str(actual_value).strip() == "":
                return False, f"{key}=<empty>"
            continue
        if str(actual_value) != str(expected_value):
            return False, f"{key}: expected {expected_value!r}, got {actual_value!r}"
    return True, ""


def select_expected_call(calls: list[dict], expected_tool: str) -> dict | None:
    """正例按集合包含判工具选择：只要 expected_tool 出现在调用集合中就算 tool_hit。"""
    for call in calls:
        if call.get("name") == expected_tool:
            return call
    return None


def score_case(item: dict, calls: list[dict], route: str = "") -> dict:
    """对单条 case 判分，返回逐条诊断信息。"""
    expected = item["expected"]
    should_call = expected["should_call"]
    calls = merge_calls(calls)
    called = bool(calls)

    result = {
        "id": item["id"],
        "category": item.get("category", "?"),
        "question": item["question"],
        "route": route or "?",
        "should_call": should_call,
        "called": called,
        "expected_tool": expected.get("tool", ""),
        "expected_args": expected.get("args", {}),
        "actual_calls": calls,
        "tool_hit": None,
        "args_ok": None,
        "pass": False,
        "diagnosis": "",
    }

    if not should_call:
        result["pass"] = not called
        result["diagnosis"] = "TN: no tool call" if not called else "FP: unexpected tool call"
        return result

    if not called:
        result["tool_hit"] = False
        result["args_ok"] = False
        result["diagnosis"] = "FN: expected tool call but none"
        return result

    expected_tool = expected["tool"]
    matched = select_expected_call(calls, expected_tool)
    if matched is None:
        result["tool_hit"] = False
        result["args_ok"] = False
        actual_names = " → ".join(call["name"] for call in calls)
        result["diagnosis"] = f"wrong tool: got {actual_names or '-'}"
        return result

    ok, reason = args_match(expected.get("args", {}), matched.get("args", {}))
    result["tool_hit"] = True
    result["args_ok"] = ok
    result["pass"] = ok
    result["diagnosis"] = "OK" if ok else f"bad args: {reason}"
    return result


def aggregate_results(results: list[dict]) -> dict:
    """汇总混淆矩阵、工具选择准确率、参数准确率与按 category 统计。"""
    matrix = {
        "should_call_and_called": 0,
        "should_call_but_not_called": 0,
        "should_not_call_but_called": 0,
        "should_not_call_and_not_called": 0,
    }
    positives = [r for r in results if r["should_call"]]
    negatives = [r for r in results if not r["should_call"]]
    for r in results:
        if r["should_call"] and r["called"]:
            matrix["should_call_and_called"] += 1
        elif r["should_call"] and not r["called"]:
            matrix["should_call_but_not_called"] += 1
        elif (not r["should_call"]) and r["called"]:
            matrix["should_not_call_but_called"] += 1
        else:
            matrix["should_not_call_and_not_called"] += 1

    tool_hits = [r for r in positives if r.get("tool_hit") is True]
    args_ok = [r for r in tool_hits if r.get("args_ok") is True]

    by_category: dict[str, dict] = {}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        grouped[r["category"]].append(r)
    for category, rows in sorted(grouped.items()):
        cat_pos = [r for r in rows if r["should_call"]]
        cat_tool_hits = [r for r in cat_pos if r.get("tool_hit") is True]
        by_category[category] = {
            "n": len(rows),
            "pass": sum(1 for r in rows if r["pass"]),
            "should_call": len(cat_pos),
            "called": sum(1 for r in rows if r["called"]),
            "tool_hit": len(cat_tool_hits),
            "args_ok": sum(1 for r in cat_tool_hits if r.get("args_ok") is True),
        }

    n = len(results)
    return {
        "n": n,
        "pass": sum(1 for r in results if r["pass"]),
        "matrix": matrix,
        "positive_n": len(positives),
        "negative_n": len(negatives),
        "tool_selection_accuracy": (len(tool_hits) / len(positives)) if positives else 0.0,
        "args_accuracy": (len(args_ok) / len(tool_hits)) if tool_hits else 0.0,
        "by_category": by_category,
    }


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _actual_calls_label(calls: list[dict]) -> str:
    if not calls:
        return "-"
    labels = []
    for call in calls:
        args = _normalize_args(call.get("args"))
        labels.append(f"{call['name']}({_stable_json(args)})")
    return " → ".join(labels)


def _md_escape(text: Any) -> str:
    return str(text).replace("|", "/").replace("\n", " ")


def render_markdown(results: list[dict], summary: dict) -> str:
    """渲染 eval/tool_results.md。"""
    m = summary["matrix"]
    lines = [
        "# 工具调用质量评测结果",
        "",
        f"- 模型：`{MODEL_NAME}`",
        f"- 温度：{MODEL_TEMPERATURE}",
        "- 运行方式：单次运行；LLM 有非确定性，数字用于回归与方向性判断。",
        f"- 样本数：{summary['n']}（正例 {summary['positive_n']} / 负例 {summary['negative_n']}）",
        "",
        "## 汇总",
        "",
        f"- 总通过率：**{summary['pass']}/{summary['n']} = {_fmt_pct(summary['pass'] / summary['n'] if summary['n'] else 0)}**",
        f"- 正例工具选择准确率：**{_fmt_pct(summary['tool_selection_accuracy'])}**",
        f"- tool_hit 子集参数准确率：**{_fmt_pct(summary['args_accuracy'])}**",
        "",
        "## should-call 混淆矩阵",
        "",
        "| 维度 | 数量 |",
        "|---|---:|",
        f"| 该调且调了 | {m['should_call_and_called']} |",
        f"| 该调没调 | {m['should_call_but_not_called']} |",
        f"| 不该调却调了 | {m['should_not_call_but_called']} |",
        f"| 不该调也没调 | {m['should_not_call_and_not_called']} |",
        "",
        "## 按 category 分组",
        "",
        "| category | n | pass | should_call | called | tool_hit | args_ok |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for category, row in summary["by_category"].items():
        lines.append(
            f"| {category} | {row['n']} | {row['pass']} | {row['should_call']} | "
            f"{row['called']} | {row['tool_hit']} | {row['args_ok']} |"
        )

    lines += [
        "",
        "## 逐条明细",
        "",
        "| id | category | route | expected | actual calls | tool_hit | args_ok | pass | diagnosis |",
        "|---|---|---|---|---|---:|---:|---:|---|",
    ]
    for r in results:
        expected = "no tool"
        if r["should_call"]:
            expected = f"{r['expected_tool']}({_stable_json(r['expected_args'])})"
        tool_hit = "-" if r["tool_hit"] is None else str(bool(r["tool_hit"]))
        args_ok = "-" if r["args_ok"] is None else str(bool(r["args_ok"]))
        lines.append(
            f"| {r['id']} | {r['category']} | {r['route']} | {_md_escape(expected)} | "
            f"{_md_escape(_actual_calls_label(r['actual_calls']))} | {tool_hit} | {args_ok} | "
            f"{str(bool(r['pass']))} | {_md_escape(r['diagnosis'])} |"
        )

    lines += [
        "",
        "> 由 `python -m langgraph_cs.eval.tool_eval --write-md` 生成。读工具使用真实业务库；",
        "> 写工具 `create_ticket` 在评测期间被 monkeypatch 为记录器，避免污染演示库；",
        "> `create_refund_ticket` 通过 approval interrupt 捕获参数，不 resume、不落库。",
    ]
    return "\n".join(lines) + "\n"


def print_report(results: list[dict], summary: dict) -> None:
    """终端输出简表。"""
    m = summary["matrix"]
    print()
    print(f"=== 工具调用质量评测（n={summary['n']}，model={MODEL_NAME}, temp={MODEL_TEMPERATURE}）===")
    print(f"通过率：{summary['pass']}/{summary['n']} = {_fmt_pct(summary['pass'] / summary['n'] if summary['n'] else 0)}")
    print(f"工具选择准确率（正例）：{_fmt_pct(summary['tool_selection_accuracy'])}")
    print(f"参数准确率（tool_hit 子集）：{_fmt_pct(summary['args_accuracy'])}")
    print()
    print("should-call 混淆矩阵：")
    print(f"  该调且调了      {m['should_call_and_called']}")
    print(f"  该调没调        {m['should_call_but_not_called']}")
    print(f"  不该调却调了    {m['should_not_call_but_called']}")
    print(f"  不该调也没调    {m['should_not_call_and_not_called']}")
    print()
    print("逐条结果：")
    for r in results:
        status = "PASS" if r["pass"] else "FAIL"
        print(f"- [{status}] {r['id']} route={r['route']} actual={_actual_calls_label(r['actual_calls'])} :: {r['diagnosis']}")


def _fake_ticket(user_id: str, ticket_type: str, detail: str, db_path=None) -> dict:  # noqa: ARG001
    return {
        "ticket_id": f"TKT-EVAL-{uuid.uuid4().hex[:8]}",
        "user_id": user_id,
        "ticket_type": ticket_type,
        "status": "待审批",
        "detail": detail,
        "created_at": "2026-07-04T00:00:00",
    }


def _extract_tool_calls_from_state(values: dict) -> list[dict]:
    """从最终 state 的 AIMessage.tool_calls 采集工具调用。"""
    calls: list[dict] = []
    for msg in values.get("messages") or []:
        for call in getattr(msg, "tool_calls", None) or []:
            calls.append({
                "name": call.get("name", ""),
                "args": deepcopy(call.get("args") or {}),
                "source": "ai_state",
            })
    return calls


def run_case(graph, item: dict) -> dict:
    """真实跑一条 case，返回 score_case 的逐条结果。"""
    from langchain_core.messages import HumanMessage

    thread_id = f"tool-eval-{item['id']}-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 30}
    calls: list[dict] = []
    route = ""
    interrupted = False

    for update in graph.stream(
        {"messages": [HumanMessage(content=item["question"])]},
        config=config,
        stream_mode="updates",
    ):
        if "__interrupt__" in update:
            interrupted = True
            interrupt_obj = (update.get("__interrupt__") or [None])[0]
            payload = getattr(interrupt_obj, "value", None)
            if isinstance(payload, dict) and payload.get("kind") == "approval":
                calls.append({
                    "name": payload.get("action") or "create_refund_ticket",
                    "args": deepcopy(payload.get("params") or {}),
                    "source": "approval_interrupt",
                })
            break
        for node_name in update.keys():
            if node_name in AGENT_NODES:
                route = node_name

    values = graph.get_state(config).values
    calls.extend(_extract_tool_calls_from_state(values))
    result = score_case(item, calls, route=route)
    if interrupted and not result["route"]:
        result["route"] = route or "?"
    return result


def run(limit: int | None = None, write_md: bool = False) -> dict:
    """真实工具调用评测。"""
    from langgraph_cs.graph import build_graph
    from langgraph_cs.nodes import tools as tools_mod

    items = load_dataset()
    if limit is not None:
        items = items[:limit]

    graph = build_graph()
    orig_create_ticket = tools_mod.business_db.create_ticket
    tools_mod.business_db.create_ticket = _fake_ticket
    try:
        results = []
        for i, item in enumerate(items, start=1):
            logger.info("[%d/%d] %s", i, len(items), item["id"])
            results.append(run_case(graph, item))
    finally:
        tools_mod.business_db.create_ticket = orig_create_ticket

    summary = aggregate_results(results)
    print_report(results, summary)
    if write_md:
        RESULTS_MD_PATH.write_text(render_markdown(results, summary), encoding="utf-8")
        logger.info("已写入工具评测结果：%s", RESULTS_MD_PATH)
    return {"results": results, "summary": summary}


def _self_test() -> None:
    """离线自测判分逻辑，不构建图、不联网。"""
    # 集合包含：先调 refund_status 再调 create_refund_ticket，正例应算 tool_hit。
    item = {
        "id": "mock-refund",
        "category": "billing",
        "question": "mock",
        "expected": {
            "should_call": True,
            "tool": "create_refund_ticket",
            "args": {"user_id": "user_003", "order_id": "ORD-1", "reason": "*"},
        },
    }
    calls = [
        {"name": "refund_status", "args": {"order_id": "ORD-1"}, "source": "ai_state"},
        {"name": "create_refund_ticket", "args": {"user_id": "user_003", "order_id": "ORD-1", "reason": "尺码不合适"}, "source": "approval_interrupt"},
    ]
    r = score_case(item, calls, route="billing_agent")
    assert r["tool_hit"] is True and r["args_ok"] is True and r["pass"] is True, r

    # 通配参数必须非空。
    bad = deepcopy(calls)
    bad[1]["args"]["reason"] = ""
    r = score_case(item, bad, route="billing_agent")
    assert r["tool_hit"] is True and r["args_ok"] is False and r["pass"] is False, r

    # 负例：无调用 pass，有调用 fail。
    neg = {"id": "mock-neg", "category": "policy", "question": "mock", "expected": {"should_call": False}}
    assert score_case(neg, [], route="general_agent")["pass"] is True
    assert score_case(neg, [{"name": "query_bill", "args": {"user_id": "user_001"}}], route="billing_agent")["pass"] is False

    # 重复来源合并：AIMessage + approval interrupt 同一调用只显示一次，source 合并。
    dup = merge_calls([
        {"name": "create_refund_ticket", "args": {"order_id": "ORD-1"}, "source": "ai_state"},
        {"name": "create_refund_ticket", "args": {"order_id": "ORD-1"}, "source": "approval_interrupt"},
    ])
    assert len(dup) == 1 and dup[0]["source"] == "ai_state+approval_interrupt", dup

    # 聚合：1 TP, 1 FN, 1 FP, 1 TN；正例 tool selection 1/2，args 1/1。
    results = [
        score_case(item, calls, route="billing_agent"),
        score_case(item, [], route="billing_agent"),
        score_case(neg, [{"name": "query_bill", "args": {"user_id": "user_001"}}], route="billing_agent"),
        score_case(neg, [], route="general_agent"),
    ]
    summary = aggregate_results(results)
    assert summary["matrix"] == {
        "should_call_and_called": 1,
        "should_call_but_not_called": 1,
        "should_not_call_but_called": 1,
        "should_not_call_and_not_called": 1,
    }, summary
    assert abs(summary["tool_selection_accuracy"] - 0.5) < 1e-9, summary
    assert abs(summary["args_accuracy"] - 1.0) < 1e-9, summary

    print("self-test 通过：集合包含、args 通配、负例判定、重复合并、聚合指标均正确。")


def main() -> None:
    parser = argparse.ArgumentParser(description="RelayDesk 工具调用质量评测。")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条 case，便于真实试跑控额度。")
    parser.add_argument("--write-md", action="store_true", help="写入 eval/tool_results.md。")
    parser.add_argument("--self-test", action="store_true", help="离线自测判分逻辑，不联网。")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.self_test:
        _self_test()
        return

    run(limit=args.limit, write_md=args.write_md)


if __name__ == "__main__":
    main()
