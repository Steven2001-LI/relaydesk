"""
answer_eval —— 端到端**答案质量**评测（LLM-as-judge，本地、不上云、不依赖外部账号）。

和 run_eval 的区别：
  - run_eval 评的是**检索层**指标（Hit/Recall/MRR，只看召回对不对）。
  - answer_eval 评的是**端到端答案质量**：把一组客服问题真的跑过 build_graph()，
    拿到最终回复，再让 DeepSeek 当 judge 按「准确性 / 有用性」各 1~5 打分。
    这一层更贴近"用户实际收到的答案好不好"，是 LangSmith evaluate 的本地保底版。

判分维度（各 1~5，5 最好）：
  - accuracy（准确性）：回答是否事实正确、不编造、与客服场景相符。
  - helpfulness（有用性）：是否真正解决用户问题、给出可操作步骤、表述清晰。

跑法（从仓库根目录，-m 模块方式保证 import 路径正确）：
    # 真实评测：跑图（DeepSeek 对话）+ DeepSeek judge 打分，会联网消耗额度
    langgraph_cs/.venv/bin/python -m langgraph_cs.eval.answer_eval
    langgraph_cs/.venv/bin/python -m langgraph_cs.eval.answer_eval --limit 3      # 只跑前 3 条省额度
    langgraph_cs/.venv/bin/python -m langgraph_cs.eval.answer_eval --write-md     # 顺手写 eval/answer_results.md
    # 离线自测：用 mock judge 验证打分解析与聚合逻辑，**不连网、不调 LLM**
    langgraph_cs/.venv/bin/python -m langgraph_cs.eval.answer_eval --self-test

前置条件（仅真实评测需要，--self-test 不需要）：
    1) langgraph_cs/.env 已填 DEEPSEEK_API_KEY（跑图 + judge 都用 DeepSeek）；
    2) 若问题会触发 RAG 检索，需先灌库并配 SILICONFLOW_API_KEY（同 run_eval 前置）。
"""
import argparse
import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).parent
DATASET_PATH = _BASE_DIR / "answer_dataset.json"
RESULTS_MD_PATH = _BASE_DIR / "answer_results.md"

# 打分区间与解析失败时的"中性分"（1~5 的中点）。解析不出来时给中性分而非 0/5，
# 避免一次 judge 抽风把均值带偏，同时打日志便于排查。
SCORE_MIN, SCORE_MAX = 1, 5
NEUTRAL_SCORE = 3

# judge 的系统提示：要求只输出 JSON，给两个维度打分 + 一句简短理由。
_JUDGE_SYSTEM_PROMPT = (
    "你是一个严格的客服答案质量评审员。给定【用户问题】和【客服回答】，"
    "请从两个维度各打 1~5 分（5 最好，1 最差）：\n"
    "  - accuracy（准确性）：回答是否事实正确、不编造、契合客服场景；\n"
    "  - helpfulness（有用性）：是否真正解决问题、给出可操作步骤、表述清晰。\n"
    "只输出 JSON，格式：{\"accuracy\": <1-5整数>, \"helpfulness\": <1-5整数>, \"reason\": \"<一句话理由>\"}，"
    "不要任何多余文字。"
)


# --------------------------------------------------------------------------- #
# 打分解析（纯函数，可离线自测）
#
# 把"judge 返回的原始文本 -> 结构化分数"抽成纯函数，好处是能用 mock 文本离线断言
# 解析逻辑（含各种兜底分支），不必真发 judge 请求。
# --------------------------------------------------------------------------- #
def _strip_code_fence(text: str) -> str:
    """去掉 LLM 可能加的 markdown 代码围栏（```json ... ```），与 intent_node 同款兜底。"""
    s = text.strip()
    if not s.startswith("```"):
        return s
    s = s[3:]
    if s[:4].lower() == "json":
        s = s[4:]
    s = s.strip()
    if s.endswith("```"):
        s = s[:-3]
    return s.strip()


def _clamp_score(value) -> int:
    """把一个分值夹到 [SCORE_MIN, SCORE_MAX] 整数区间；非法值抛 ValueError 交给上层兜底。"""
    score = int(round(float(value)))
    if score < SCORE_MIN:
        return SCORE_MIN
    if score > SCORE_MAX:
        return SCORE_MAX
    return score


def parse_judge_scores(raw_text: str) -> dict:
    """
    解析 judge 的原始输出为 {"accuracy": int, "helpfulness": int, "reason": str, "parsed": bool}。

    多重兜底（任何一步失败都退化到中性分，并把 parsed 置 False 让上层记日志）：
      1) 先剥代码围栏再 json.loads；
      2) JSON 里缺字段/类型不对 -> 该维度记中性分；
      3) 连 JSON 都解析不出 -> 用正则从文本里抠 accuracy/helpfulness 数字（容忍 judge 不守格式）；
      4) 正则也抠不到 -> 两维度全给中性分。
    """
    text = _strip_code_fence(raw_text or "")

    # 路径 1：标准 JSON（剥围栏后 json.loads，缺字段/类型错就落到下面的兜底）。
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            acc = _clamp_score(data["accuracy"])
            helpful = _clamp_score(data["helpfulness"])
            reason = str(data.get("reason", "")).strip()
            return {"accuracy": acc, "helpfulness": helpful, "reason": reason, "parsed": True}
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        pass

    # 路径 2：JSON 失败 -> 正则从纯文本里抠两个维度的数字。
    acc = _regex_pick(text, "accuracy")
    helpful = _regex_pick(text, "helpfulness")
    if acc is not None or helpful is not None:
        logger.warning("judge 输出非标准 JSON，已用正则兜底解析：%s", text[:80])
        return {
            "accuracy": acc if acc is not None else NEUTRAL_SCORE,
            "helpfulness": helpful if helpful is not None else NEUTRAL_SCORE,
            "reason": "(正则兜底解析)",
            "parsed": False,
        }

    # 路径 3：彻底解析失败 -> 全中性分。
    logger.warning("judge 输出无法解析，给中性分 %d：%s", NEUTRAL_SCORE, text[:80])
    return {
        "accuracy": NEUTRAL_SCORE,
        "helpfulness": NEUTRAL_SCORE,
        "reason": "(解析失败，给中性分)",
        "parsed": False,
    }


def _regex_pick(text: str, key: str):
    """从纯文本里用正则抠 `key` 后面的第一个数字（1~5），抠不到返回 None。"""
    m = re.search(rf"{key}\D*([1-5])", text, flags=re.IGNORECASE)
    if not m:
        return None
    try:
        return _clamp_score(m.group(1))
    except ValueError:
        return None


def aggregate_scores(results: list[dict]) -> dict:
    """
    对一批逐条结果聚合：accuracy / helpfulness / overall 三个均值，外加解析失败计数。

    results：[{"accuracy": int, "helpfulness": int, "parsed": bool, ...}, ...]。
    overall = (accuracy + helpfulness) / 2 的逐条均值。空列表返回全 0。
    """
    n = len(results)
    if n == 0:
        return {"accuracy": 0.0, "helpfulness": 0.0, "overall": 0.0, "n": 0, "parse_failures": 0}
    acc = sum(r["accuracy"] for r in results) / n
    helpful = sum(r["helpfulness"] for r in results) / n
    overall = sum((r["accuracy"] + r["helpfulness"]) / 2 for r in results) / n
    parse_failures = sum(0 if r.get("parsed", True) else 1 for r in results)
    return {
        "accuracy": acc, "helpfulness": helpful, "overall": overall,
        "n": n, "parse_failures": parse_failures,
    }


# --------------------------------------------------------------------------- #
# 数据加载
# --------------------------------------------------------------------------- #
def load_questions(path: Path = DATASET_PATH) -> list[dict]:
    """读取 answer_dataset.json（顶层数组），每项含 id / category / question。"""
    items = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(items, list) or not items:
        raise ValueError(f"答案评测集为空或格式错误：{path}")
    for i, it in enumerate(items):
        if not it.get("question"):
            raise ValueError(f"第 {i} 条缺少 question")
    return items


# --------------------------------------------------------------------------- #
# 真实评测：跑图拿答案 + 调 judge 打分
#
# 真实网络/LLM 调用集中在这里。--self-test 不会走到本段（用 mock judge）。
# --------------------------------------------------------------------------- #
def run_question(graph, question: str) -> str:
    """把一个问题跑过图，取最终 AI 回复文本。每条用独立 thread_id，避免历史串味。"""
    import uuid

    from langchain_core.messages import HumanMessage

    config = {"configurable": {"thread_id": f"answer-eval-{uuid.uuid4().hex[:8]}"}}
    result = graph.invoke({"messages": [HumanMessage(content=question)]}, config=config)
    return result["messages"][-1].content


def judge_answer(judge_llm, question: str, answer: str) -> dict:
    """
    调 DeepSeek judge 给一条「问题+回答」打分，返回 parse_judge_scores 的结构化结果。

    judge_llm 由 config.build_llm 构造（复用对话同一套 LLM 客户端，低温度求稳定）。
    judge 调用本身也包 try/except：judge 报错时退化为中性分，不让评测整体崩。
    """
    user_msg = f"【用户问题】\n{question}\n\n【客服回答】\n{answer}"
    try:
        resp = judge_llm.invoke(
            [{"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
             {"role": "user", "content": user_msg}]
        )
        return parse_judge_scores(resp.content)
    except Exception as ex:  # noqa: BLE001 judge 失败统一兜底，不崩评测
        logger.warning("judge 调用失败，给中性分：%s", ex)
        return {"accuracy": NEUTRAL_SCORE, "helpfulness": NEUTRAL_SCORE,
                "reason": f"(judge 调用失败: {ex})", "parsed": False}


def run(limit: int | None, write_md: bool) -> dict:
    """
    真实端到端评测：每条问题跑图拿答案 -> judge 打分 -> 逐条打印 + 聚合。

    重依赖（build_graph / build_llm）在函数内 import，保证 `import answer_eval`
    本身轻量、纯函数（解析/聚合）可被单独 import 自测。
    """
    from langgraph_cs.config import build_llm
    from langgraph_cs.graph import build_graph

    items = load_questions()
    if limit is not None:
        items = items[:limit]
    n = len(items)
    logger.info("答案质量评测开始：%d 条问题（跑图 + DeepSeek judge）", n)

    graph = build_graph()
    judge_llm = build_llm(temperature=0.0)  # judge 求稳定，温度调 0

    results: list[dict] = []
    for i, it in enumerate(items, start=1):
        question = it["question"]
        answer = run_question(graph, question)
        scored = judge_answer(judge_llm, question, answer)
        scored = {**scored, "id": it.get("id", f"q{i}"), "category": it.get("category", "?"),
                  "question": question, "answer": answer}
        results.append(scored)
        logger.info("[%d/%d] %s | acc=%d help=%d", i, n, it.get("id", "?"),
                    scored["accuracy"], scored["helpfulness"])

    agg = aggregate_scores(results)
    _print_report(results, agg)
    if write_md:
        write_results_md(results, agg)
    return {"results": results, "aggregate": agg}


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
def _print_report(results: list[dict], agg: dict) -> None:
    """逐条分数表 + 聚合均值打印到终端。"""
    print()
    print(f"=== 端到端答案质量评测（n={agg['n']}，DeepSeek judge）===")
    header = ("id", "类别", "准确性", "有用性", "理由")
    rows = [header]
    for r in results:
        rows.append((str(r["id"]), str(r.get("category", "?")),
                     str(r["accuracy"]), str(r["helpfulness"]),
                     (r.get("reason") or "")[:30]))
    widths = [max(len(r[c]) for r in rows) for c in range(len(header))]
    for ri, row in enumerate(rows):
        print(" | ".join(cell.ljust(widths[c]) for c, cell in enumerate(row)))
        if ri == 0:
            print("-+-".join("-" * w for w in widths))
    print()
    print(f"平均：准确性 {agg['accuracy']:.2f} / 有用性 {agg['helpfulness']:.2f} / "
          f"综合 {agg['overall']:.2f}（满分 5）")
    if agg["parse_failures"]:
        print(f"注意：{agg['parse_failures']} 条 judge 输出解析失败，已按中性分 {NEUTRAL_SCORE} 计入。")


def write_results_md(results: list[dict], agg: dict) -> None:
    """把答案质量评测结果落一份 Markdown（eval/answer_results.md）。"""
    lines = [
        "# 端到端答案质量评测结果（LLM-as-judge，本地 DeepSeek）",
        "",
        f"- 样本数：{agg['n']}",
        "- judge：DeepSeek（`config.build_llm`，temperature=0），按准确性/有用性各 1~5 打分",
        f"- **平均：准确性 {agg['accuracy']:.2f} / 有用性 {agg['helpfulness']:.2f} / "
        f"综合 {agg['overall']:.2f}**（满分 5）",
    ]
    if agg["parse_failures"]:
        lines.append(f"- 解析失败按中性分 {NEUTRAL_SCORE} 计入：{agg['parse_failures']} 条")
    lines += [
        "",
        "| id | 类别 | 准确性 | 有用性 | 理由 |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        reason = (r.get("reason") or "").replace("|", "/").replace("\n", " ")
        lines.append(f"| {r['id']} | {r.get('category', '?')} | {r['accuracy']} | "
                     f"{r['helpfulness']} | {reason[:60]} |")
    lines += [
        "",
        "> 由 `python -m langgraph_cs.eval.answer_eval --write-md` 生成。",
        "> 这是 LangSmith evaluate 的**本地保底版**：不上云、不依赖外部账号，",
        "> 评的是端到端答案质量（vs run_eval 评检索层 Hit/Recall/MRR）。",
    ]
    RESULTS_MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("已写入答案质量评测结果：%s", RESULTS_MD_PATH)


# --------------------------------------------------------------------------- #
# 离线自测：mock judge，验证解析 + 聚合（不连网、不调真 LLM）
# --------------------------------------------------------------------------- #
def _self_test() -> None:
    """
    用 mock 的 judge 文本离线自测打分解析与聚合逻辑（不发任何网络请求）。
    跑法：langgraph_cs/.venv/bin/python -m langgraph_cs.eval.answer_eval --self-test
    """
    # 1) 标准 JSON 正常解析。
    r = parse_judge_scores('{"accuracy": 5, "helpfulness": 4, "reason": "清晰可操作"}')
    assert r == {"accuracy": 5, "helpfulness": 4, "reason": "清晰可操作", "parsed": True}, r

    # 2) 带代码围栏的 JSON 也能解析（DeepSeek 常包 ```json）。
    r = parse_judge_scores('```json\n{"accuracy": 3, "helpfulness": 2, "reason": "一般"}\n```')
    assert r["accuracy"] == 3 and r["helpfulness"] == 2 and r["parsed"] is True, r

    # 3) 越界分值被夹回区间（7 -> 5，0 -> 1）。
    r = parse_judge_scores('{"accuracy": 7, "helpfulness": 0, "reason": "x"}')
    assert r["accuracy"] == 5 and r["helpfulness"] == 1, r

    # 4) 非标准 JSON，但文本里有数字 -> 正则兜底，parsed=False。
    r = parse_judge_scores("准确性 accuracy: 4 分，有用性 helpfulness 是 5 分。")
    assert r["accuracy"] == 4 and r["helpfulness"] == 5 and r["parsed"] is False, r

    # 5) 完全无法解析 -> 全中性分，parsed=False。
    r = parse_judge_scores("我无法评分。")
    assert r["accuracy"] == NEUTRAL_SCORE and r["helpfulness"] == NEUTRAL_SCORE, r
    assert r["parsed"] is False, r

    # 6) JSON 缺字段 -> 走正则兜底（找不到数字）-> 中性分。
    r = parse_judge_scores('{"accuracy": 4}')
    assert r["accuracy"] == 4 and r["parsed"] is False, r  # accuracy 被正则抠到，helpfulness 补中性

    # 7) 聚合：三条 (5,4)/(3,2)/(1,1) -> acc=3.0, help=2.33..., overall=均值((4.5+2.5+1.0)/3)
    sample = [
        {"accuracy": 5, "helpfulness": 4, "parsed": True},
        {"accuracy": 3, "helpfulness": 2, "parsed": True},
        {"accuracy": 1, "helpfulness": 1, "parsed": False},
    ]
    agg = aggregate_scores(sample)
    assert abs(agg["accuracy"] - 3.0) < 1e-9, agg
    assert abs(agg["helpfulness"] - (7 / 3)) < 1e-9, agg
    assert abs(agg["overall"] - ((4.5 + 2.5 + 1.0) / 3)) < 1e-9, agg
    assert agg["parse_failures"] == 1, agg
    assert agg["n"] == 3, agg

    # 8) 空列表聚合不报错。
    assert aggregate_scores([])["n"] == 0

    # 9) 端到端走一遍（mock judge + mock graph 答案），验证 run 的解析/聚合管线接得通。
    class _MockJudge:
        """mock judge：按答案长度给个确定分，证明 judge_answer -> parse -> aggregate 串得起来。"""

        def invoke(self, messages):
            class _Resp:
                content = '{"accuracy": 4, "helpfulness": 4, "reason": "mock"}'
            return _Resp()

    scored = judge_answer(_MockJudge(), "问题", "回答")
    assert scored["accuracy"] == 4 and scored["parsed"] is True, scored

    print("self-test 通过：judge 打分解析（JSON/围栏/越界/正则兜底/失败）与聚合逻辑正确（未连网）。")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="端到端答案质量评测（跑图 + DeepSeek judge 按准确性/有用性打分）。"
    )
    parser.add_argument("--limit", type=int, default=None,
                        help="只跑前 N 条问题（护额度/快速试跑）。默认跑全部。")
    parser.add_argument("--write-md", action="store_true",
                        help="把结果写到 eval/answer_results.md。")
    parser.add_argument("--self-test", action="store_true",
                        help="只跑解析+聚合离线自测（mock judge，不连网、不调 LLM）。")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.self_test:
        _self_test()
        return

    run(limit=args.limit, write_md=args.write_md)


if __name__ == "__main__":
    main()
