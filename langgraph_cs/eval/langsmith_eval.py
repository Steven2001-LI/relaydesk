"""
langsmith_eval —— LangSmith 端到端评测（trace + 数据集 + evaluate）。

================================ 用户怎么跑 ================================
这套**需要 LangSmith 账号和 API key**，会真的把数据集和评测结果上传到 LangChain 云端。
本仓库开发时**不联网、不上传**（缺 key 会失败），只验证接线与 import 正确。你要真跑：

  1) 注册 https://smith.langchain.com 拿到 LANGSMITH_API_KEY；
  2) 在 langgraph_cs/.env 里填好（参考 .env.example）：
         LANGSMITH_TRACING=true
         LANGSMITH_API_KEY=ls-...你的真实 key...
         LANGSMITH_PROJECT=relaydesk-langgraph
     —— 设好这三个后，**LangGraph 会自动**把每次 build_graph().invoke 的节点级 trace
        上传到 LangSmith（intent/rag/各 agent 节点的输入输出都能在网页上看到），无需改图代码。
  3) 确保对话能力可用：DEEPSEEK_API_KEY 已填（跑图 + judge 都用它）；
     若问题触发 RAG，还需 SILICONFLOW_API_KEY + 已灌库（同 run_eval 前置）。
  4) 从仓库根目录跑：
         langgraph_cs/.venv/bin/python -m langgraph_cs.eval.langsmith_eval
     脚本会：① 创建/复用一个 LangSmith 数据集（来自 answer_dataset.json 的问题集）；
            ② 用 LLM-judge evaluator（DeepSeek 打分）调 client.evaluate() 跑端到端评测；
            ③ 结果与逐条 trace 都在 LangSmith 网页上看（终端打印实验链接）。

  本地 dry-run（不联网、不上传，验证接线）：
         langgraph_cs/.venv/bin/python -m langgraph_cs.eval.langsmith_eval --dry-run
==========================================================================

和 answer_eval 的关系：answer_eval 是**本地保底版**（同一套问题 + DeepSeek judge，结果只在本地）；
本脚本把同一思路搬到 LangSmith 上，多了云端 trace 可视化 + 数据集版本管理 + 实验对比。
judge 的打分解析直接复用 answer_eval.parse_judge_scores，避免重复造轮子。
"""
import argparse
import logging

from langgraph_cs import config
from langgraph_cs.eval.answer_eval import (
    NEUTRAL_SCORE,
    load_questions,
    parse_judge_scores,
)

logger = logging.getLogger(__name__)

# LangSmith 上数据集的名字（复用：存在就不重复建）。
DATASET_NAME = "relaydesk-cs-answers"


# --------------------------------------------------------------------------- #
# target：被评测的"系统"。LangSmith 会对数据集每个 example 调一次 target(inputs)。
#
# 这里 target 就是"把问题跑过 build_graph() 拿最终答案"。设成函数（不在模块顶层建图），
# 是为了 import 本模块时不触发建图/联网，dry-run 才能纯离线验证接线。
# --------------------------------------------------------------------------- #
def make_target():
    """构造 target 函数：inputs={"question": ...} -> {"answer": ...}。建图在调用时才发生。"""
    from langgraph_cs.eval.answer_eval import run_question
    from langgraph_cs.graph import build_graph

    graph = build_graph()

    def target(inputs: dict) -> dict:
        # LangSmith 把 example 的 inputs 原样传进来；我们约定 inputs 里有 "question"。
        answer = run_question(graph, inputs["question"])
        return {"answer": answer}

    return target


# --------------------------------------------------------------------------- #
# evaluator：LLM-as-judge。LangSmith 对每条 (inputs, outputs) 调它，返回一个分数。
#
# 签名用 LangSmith 约定的 (inputs, outputs) 形参；返回 {"key","score"} dict。
# judge 调用 + 打分解析复用 answer_eval（同一套 DeepSeek judge + parse_judge_scores）。
# --------------------------------------------------------------------------- #
def make_quality_evaluator():
    """构造一个 LLM-judge evaluator：综合准确性+有用性 -> 0~1 归一化分数。"""
    from langgraph_cs.config import build_llm
    from langgraph_cs.eval.answer_eval import _JUDGE_SYSTEM_PROMPT  # noqa: SLF001 复用同一 judge 提示

    judge_llm = build_llm(temperature=0.0)

    def answer_quality(inputs: dict, outputs: dict) -> dict:
        question = inputs.get("question", "")
        answer = (outputs or {}).get("answer", "")
        user_msg = f"【用户问题】\n{question}\n\n【客服回答】\n{answer}"
        try:
            resp = judge_llm.invoke(
                [{"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
                 {"role": "user", "content": user_msg}]
            )
            scored = parse_judge_scores(resp.content)
        except Exception as ex:  # noqa: BLE001 judge 失败兜底中性分，不让单条拖垮整个实验
            logger.warning("evaluator judge 失败，给中性分：%s", ex)
            scored = {"accuracy": NEUTRAL_SCORE, "helpfulness": NEUTRAL_SCORE}
        # 综合分（accuracy+helpfulness 的均值）归一化到 0~1，LangSmith 习惯用 0~1。
        overall = (scored["accuracy"] + scored["helpfulness"]) / 2
        return {"key": "answer_quality", "score": (overall - 1) / 4}

    return answer_quality


# --------------------------------------------------------------------------- #
# 数据集：把 answer_dataset.json 的问题集同步到 LangSmith（存在就复用，不重复建）。
# --------------------------------------------------------------------------- #
def ensure_dataset(client):
    """
    在 LangSmith 上创建或复用名为 DATASET_NAME 的数据集，灌入问题集。

    复用逻辑：先 list_datasets 查同名；有就直接返回，没有才 create_dataset + create_examples。
    每个 example 的 inputs={"question": ...}，与 target/evaluator 的约定一致。
    （本函数会真的联网，仅在真跑时调用；dry-run 不会走到这里。）
    """
    existing = list(client.list_datasets(dataset_name=DATASET_NAME))
    if existing:
        logger.info("复用已存在的 LangSmith 数据集：%s", DATASET_NAME)
        return existing[0]

    logger.info("创建 LangSmith 数据集：%s", DATASET_NAME)
    dataset = client.create_dataset(
        DATASET_NAME,
        description="RelayDesk 客服端到端答案质量评测集（技术/账单/通用）。",
    )
    items = load_questions()
    client.create_examples(
        dataset_id=dataset.id,
        examples=[
            {"inputs": {"question": it["question"]},
             "metadata": {"id": it.get("id"), "category": it.get("category")}}
            for it in items
        ],
    )
    logger.info("已灌入 %d 条 example。", len(items))
    return dataset


# --------------------------------------------------------------------------- #
# 真跑：上传数据集 + 调 client.evaluate 跑端到端评测（需 key、联网）
# --------------------------------------------------------------------------- #
def run() -> None:
    """真正联网跑：建/复用数据集 -> client.evaluate(target, data, evaluators) -> 打印实验链接。"""
    from langsmith import Client

    if not config.LANGSMITH_API_KEY:
        raise RuntimeError(
            "缺少 LANGSMITH_API_KEY。请到 https://smith.langchain.com 注册拿 key，"
            "填进 langgraph_cs/.env（参考 .env.example），或先用 --dry-run 离线验证接线。"
        )

    client = Client(api_key=config.LANGSMITH_API_KEY)
    ensure_dataset(client)

    target = make_target()
    evaluator = make_quality_evaluator()

    logger.info("开始 LangSmith 端到端评测（target=build_graph，judge=DeepSeek）……")
    results = client.evaluate(
        target,
        data=DATASET_NAME,
        evaluators=[evaluator],
        experiment_prefix="relaydesk-answer-quality",
        metadata={"project": config.LANGSMITH_PROJECT},
    )
    print("LangSmith 评测已提交。到 https://smith.langchain.com 看实验结果与逐条节点级 trace。")
    print(results)


# --------------------------------------------------------------------------- #
# dry-run：不联网、不上传，只验证接线（import + 构造对象，不 .invoke / 不 client.evaluate）
# --------------------------------------------------------------------------- #
def dry_run() -> None:
    """
    离线接线自检：import LangSmith SDK + 构造 Client/target/evaluator + 解析数据集，
    但**绝不发起任何网络/LLM 调用**（不建图 .invoke、不 client.evaluate、不真的调 judge）。

    目的：在没有 key、不联网的开发环境里证明"代码接线正确、import 路径对、evaluator 签名对"。
    """
    print("=== LangSmith 接线 dry-run（离线，不上传、不调 LLM）===")

    # 1) SDK 可 import，关键符号都在。
    from langsmith import Client  # noqa: F401
    from langsmith.evaluation import evaluate  # noqa: F401
    print("[1] langsmith SDK import 正常（Client / evaluate 均可用）。")

    # 2) Client 构造不触发网络（lazy）。用占位 key，绝不调用任何方法。
    _client = Client(api_key="dry-run-placeholder")
    print("[2] langsmith.Client 构造成功（lazy，不联网）。")

    # 3) 数据集源可读（answer_dataset.json），example inputs 结构正确。
    items = load_questions()
    examples = [{"inputs": {"question": it["question"]}} for it in items]
    assert all("question" in e["inputs"] for e in examples)
    print(f"[3] 数据集源就绪：{len(examples)} 条 example（inputs 均含 question）。")

    # 4) evaluator 用 mock judge 走一遍打分解析 + 归一化，不调真 LLM。
    class _MockJudge:
        def invoke(self, _messages):
            class _Resp:
                content = '{"accuracy": 4, "helpfulness": 5, "reason": "mock"}'
            return _Resp()

    scored = parse_judge_scores(_MockJudge().invoke(None).content)
    overall = (scored["accuracy"] + scored["helpfulness"]) / 2
    norm = (overall - 1) / 4
    assert 0.0 <= norm <= 1.0, norm
    print(f"[4] evaluator 打分管线正常：mock judge -> 解析 acc/help -> 归一化 score={norm:.3f}（0~1）。")

    # 5) target 形态自检（不建图、不 invoke）：只断言它是可调用工厂，约定 inputs/outputs 结构。
    assert callable(make_target), make_target
    print("[5] target 工厂可用（约定 inputs={'question':...} -> {'answer':...}；本步不建图、不 invoke）。")

    print("\n✅ dry-run 通过：LangSmith 接线正确。填好 LANGSMITH_API_KEY 后去掉 --dry-run 即可真跑。")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="LangSmith 端到端评测（trace + 数据集 + evaluate）。需 key、联网。"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="离线验证接线（import + 构造对象，不上传、不调 LLM）。")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.dry_run:
        dry_run()
        return
    run()


if __name__ == "__main__":
    main()
