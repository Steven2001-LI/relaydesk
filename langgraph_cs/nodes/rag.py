"""
rag_node —— 检索增强节点。

它夹在 intent_node 与 agent_node 之间（intent → rag → agent）：
拿用户问题先从知识库召回相关文档，写进 state["retrieved_docs"]，
agent_node 再把这些文档拼进上下文作答。

两个教学点：
  1) 按意图决定是否检索。greeting（问候）/ other（闲聊兜底）这类意图
     根本用不到知识库，直接早退（返回空列表），既省 token 又省一次网络调用。
  2) 朴素检索 vs rerank 的开关。向量检索（粗排）召回 top-k 后，可选地用
     rerank（交叉编码器精排）截 top-n。这个开关由 RAG_USE_RERANK 控制，
     是 PR3 评测"朴素 vs rerank"两组指标的关键。

节点契约：
  输入：state（读 intent + messages）
  输出：{"retrieved_docs": [文档文本, ...]}  —— 只返回要更新的字段
"""
import logging
import os

from langgraph_cs.nodes.utils import last_user_text
from langgraph_cs.rag import build_retriever, rerank

logger = logging.getLogger(__name__)

# 这些意图不需要查知识库，rag_node 直接早退（不检索）。
_SKIP_INTENTS = {"greeting", "other"}

# 粗排候选数（向量检索 top-k）：先召回 k 条作候选，开 rerank 时再精排截 top-n。
# 本知识库很小（FAQ 切块后约 10 块），运行期 k=5 已够；评测脚本会扫更大的 k（默认 10）
# 来放大"粗排→精排"的差异。n≤k。
_RETRIEVE_K = 5
# rerank 精排后保留的文档数（top-n）。
_RERANK_TOP_N = 3


def _rerank_enabled() -> bool:
    """
    是否启用 rerank。默认开（不设环境变量即为开）。
    PR3 评测时把 RAG_USE_RERANK 设成 0/false/no 即可关掉，对比朴素检索。
    """
    val = os.getenv("RAG_USE_RERANK", "1").strip().lower()
    return val not in ("0", "false", "no", "off", "")


def rag_node(state) -> dict:
    intent = state.get("intent") or "other"

    # 教学点 1：按意图早退。greeting/other 不查知识库，省 token 省一次网络调用。
    if intent in _SKIP_INTENTS:
        logger.info("rag 早退（intent=%s 无需检索）", intent)
        return {"retrieved_docs": []}

    query = last_user_text(state)
    if not query:
        return {"retrieved_docs": []}

    # 整段检索都兜底：任何错误（缺 key、网络、Chroma 未灌库等）都降级为空列表，
    # 只记 warning，绝不让整张图崩——agent_node 拿到空列表会照常作答。
    try:
        retriever = build_retriever(k=_RETRIEVE_K)
        hits = retriever.invoke(query)  # -> List[Document]
    except Exception as ex:  # noqa: BLE001 教学版统一兜底，避免整图崩
        logger.warning("向量检索失败，本轮降级为不检索：%s", ex)
        return {"retrieved_docs": []}

    if not hits:
        logger.info("rag 检索 0 条（intent=%s）", intent)
        return {"retrieved_docs": []}

    # 教学点 2：rerank 开关。开则精排截 top-n；关或精排失败则用粗排结果。
    if _rerank_enabled():
        try:
            doc_texts = [h.page_content for h in hits]
            ranked = rerank(query, doc_texts, top_n=_RERANK_TOP_N)
            # rerank 返回 (原始下标 index, 分数, 文本)，已按分数降序。
            # 用 index 映射回原 hits 取文本（比用返回文本更稳，且能保留映射关系，
            # 评测脚本同样靠 index 回查 metadata.source）。本节点只往 state 写纯文本。
            docs = [hits[idx].page_content for idx, _score, _text in ranked]
            logger.info("rag 检索 %d 条 → rerank 取 %d 条（intent=%s）",
                        len(hits), len(docs), intent)
            return {"retrieved_docs": docs}
        except Exception as ex:  # noqa: BLE001 rerank 失败降级为粗排，不丢检索结果
            logger.warning("rerank 失败，降级为用粗排结果：%s", ex)

    # 朴素检索（不 rerank）：直接用粗排 top-k 文本。
    docs = [h.page_content for h in hits]
    logger.info("rag 检索 %d 条（朴素，intent=%s）", len(docs), intent)
    return {"retrieved_docs": docs}
