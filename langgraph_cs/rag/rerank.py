"""
rerank —— 硅基流动（SiliconFlow）rerank 接口的薄封装。

为什么要手写而不是用 LangChain 现成件？
  rerank 走的是硅基流动自有接口 POST /v1/rerank（风格接近 Cohere /rerank），
  不是 OpenAI 协议，LangChain / OpenAI SDK 没有现成封装，只能用 httpx 自己调。

接口字段严格按官方 OpenAPI 核实（见 research/siliconflow-embedding-rerank.md），切勿凭记忆：
  - 请求体：model / query / documents(string[]) / top_n(可选) / return_documents(可选)
  - 响应顶层是 results（不是 data）
  - 每项是 index（在原 documents 里的下标）+ relevance_score（注意不是 score）
  - results 已按 relevance_score 降序排好，无需自己再排
  - 鉴权同 embedding：Authorization: Bearer <key>

rerank 在 RAG 里的作用：向量检索（粗排）召回 top-k 后，用交叉编码器对
"query×每个候选"逐对打分（更准但更慢），截取 top-n 作为最终上下文。
"""
import os
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

# 同 embeddings.py：rag/ 是子目录，要往上跳一级取 langgraph_cs/.env。
load_dotenv(Path(__file__).parent.parent / ".env")

SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY")
SILICONFLOW_BASE_URL = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
SILICONFLOW_RERANK_MODEL = os.getenv(
    "SILICONFLOW_RERANK_MODEL", "BAAI/bge-reranker-v2-m3"
)

# 请求超时（秒）。rerank 是网络调用，必须设超时，否则节点可能被挂死。
_TIMEOUT = 30.0


def rerank(
    query: str,
    documents: list[str],
    top_n: Optional[int] = None,
    model: Optional[str] = None,
) -> list[tuple[int, float, str]]:
    """
    用硅基流动 rerank 对候选文档按与 query 的相关性精排。

    参数：
      query：用户问题
      documents：待排序的候选文档文本列表（来自向量检索粗排结果）
      top_n：只返回最相关的前 N 条；不传则返回全部。注意若 top_n 大于候选数，
             接口只会返回候选数那么多条（无需自己特判）。
      model：默认用 .env 里的 SILICONFLOW_RERANK_MODEL。

    返回：
      [(原始下标 index, relevance_score, 文档原文), ...]，已按分数降序。
      其中 index 是该文档在传入 documents 列表里的下标，调用方可借此映射回
      原始 Document 以保留 metadata（PR2 的 rag_node 会这么用）。

    出错处理：
      网络/HTTP 错误会抛出 httpx 异常，由调用方决定如何兜底（例如 rag_node
      可降级为"不重排，直接用粗排结果"）。这里不静默吞错。
    """
    if not documents:
        return []
    if not SILICONFLOW_API_KEY:
        raise RuntimeError(
            "缺少 SILICONFLOW_API_KEY。请在 langgraph_cs/.env 里填入硅基流动的 key。"
        )

    url = f"{SILICONFLOW_BASE_URL.rstrip('/')}/rerank"
    payload: dict = {
        "model": model or SILICONFLOW_RERANK_MODEL,
        "query": query,
        "documents": documents,
        # 让响应带回原文，省得调用方再用 index 回查；index 仍照常返回。
        "return_documents": True,
    }
    if top_n is not None:
        payload["top_n"] = top_n

    headers = {
        "Authorization": f"Bearer {SILICONFLOW_API_KEY}",
        "Content-Type": "application/json",
    }

    resp = httpx.post(url, json=payload, headers=headers, timeout=_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    out: list[tuple[int, float, str]] = []
    # 顶层是 results，已按 relevance_score 降序。
    for item in data.get("results", []):
        idx = item["index"]
        score = item["relevance_score"]
        # return_documents=True 时 document 形如 {"text": "..."}；
        # 万一服务端没带回（字段缺失/为空），用 index 回原列表兜底。
        doc_obj = item.get("document")
        if isinstance(doc_obj, dict) and doc_obj.get("text"):
            text = doc_obj["text"]
        else:
            text = documents[idx]
        out.append((idx, score, text))
    return out
