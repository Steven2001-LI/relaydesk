"""
rag_node —— 检索增强节点。

它夹在 intent_node 与 agent_node 之间（intent → rag → agent）：
拿用户问题先从知识库召回相关文档，写进 state["retrieved_docs"]，
agent_node 再把这些文档拼进上下文作答。

设计要点：
  1) 按意图决定是否检索。greeting（问候）/ other（闲聊兜底）这类意图
     用不到知识库，直接早退（返回空列表），既省 token 又省一次网络调用。
  2) 朴素检索 vs rerank 的开关。向量检索（粗排）召回 top-k 后，可选地用
     rerank（交叉编码器精排）截 top-n。开关由 RAG_USE_RERANK 控制，
     也是检索评测"朴素 vs rerank"两组指标的切换点。

节点契约：
  输入：state（读 intent + messages）
  输出：{"retrieved_docs": [条目 dict, ...]}  —— 只返回要更新的字段
        每个条目 dict 形如 {"item_id", "source", "text", "score"}（见 state.py 说明）：
          - item_id / source 取自 chunk 的 metadata；
          - text 是条目正文（agent 拼上下文用）；
          - score 是 rerank 的 relevance_score，朴素检索无分数时为 None。
        这样下游（agent / web 层 / 评测）拿得到稳定的条目标识与分数，不必再截文本首行当来源。
"""
import logging
import os

from langgraph_cs.nodes.utils import last_user_text
from langgraph_cs.rag import build_retriever, rerank

logger = logging.getLogger(__name__)

# 这些意图不需要查知识库，rag_node 直接早退（不检索）。
_SKIP_INTENTS = {"greeting", "other"}

# 粗排候选数（向量检索 top-k）：先召回 k 条作候选，开 rerank 时再精排截 top-n。
# 语料为条目级 chunk（百级规模，实数以 ingest 日志为准），k=5 的召回充分性已由
# eval/dataset.json 的检索评测验证；评测脚本会扫更大的 k（默认 10）来放大
# "粗排→精排"的差异。n≤k。
_RETRIEVE_K = 5
# rerank 精排后保留的文档数（top-n）。
_RERANK_TOP_N = 3


def _rerank_enabled() -> bool:
    """
    是否启用 rerank。默认开（不设环境变量即为开）。
    检索评测对比朴素路线时，把 RAG_USE_RERANK 设成 0/false/no 即可关掉。
    """
    val = os.getenv("RAG_USE_RERANK", "1").strip().lower()
    return val not in ("0", "false", "no", "off", "")


def _doc_entry(hit, score) -> dict:
    """
    把一个检索到的 LangChain Document 转成结构化条目 dict。

    item_id / source 取自 chunk 的 metadata（灌库时写入，见 rag/store.py）；
    text 是条目正文；score 是 rerank 的 relevance_score（朴素检索无分数时传 None）。
    metadata 缺字段时降级为 None，绝不抛错。
    """
    meta = getattr(hit, "metadata", None) or {}
    return {
        "item_id": meta.get("item_id"),
        "source": meta.get("source"),
        "text": hit.page_content,
        "score": score,
    }


def rag_node(state) -> dict:
    intent = state.get("intent") or "other"

    # 按意图早退：greeting/other 不查知识库，省 token 省一次网络调用。
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
    except Exception as ex:  # noqa: BLE001 检索边界兜底，避免整图崩
        logger.warning("向量检索失败，本轮降级为不检索：%s", ex)
        return {"retrieved_docs": []}

    if not hits:
        logger.info("rag 检索 0 条（intent=%s）", intent)
        return {"retrieved_docs": []}

    # rerank 开关：开则精排截 top-n；关或精排失败则用粗排结果。
    if _rerank_enabled():
        try:
            doc_texts = [h.page_content for h in hits]
            ranked = rerank(query, doc_texts, top_n=_RERANK_TOP_N)
            # rerank 返回 (原始下标 index, 分数, 文本)，已按分数降序。
            # 用 index 映射回原 hits 取条目（比用返回文本更稳，且能保留 metadata 映射，
            # 评测脚本同样靠 index 回查 item_id/source）。把 relevance_score 一并写进结构化条目。
            docs = [_doc_entry(hits[idx], score) for idx, score, _text in ranked]
            logger.info("rag 检索 %d 条 → rerank 取 %d 条（intent=%s）",
                        len(hits), len(docs), intent)
            return {"retrieved_docs": docs}
        except Exception as ex:  # noqa: BLE001 rerank 失败降级为粗排，不丢检索结果
            logger.warning("rerank 失败，降级为用粗排结果：%s", ex)

    # 朴素检索（不 rerank）：直接用粗排 top-k 条目，无 rerank 分数故 score=None。
    docs = [_doc_entry(h, None) for h in hits]
    logger.info("rag 检索 %d 条（朴素，intent=%s）", len(docs), intent)
    return {"retrieved_docs": docs}
