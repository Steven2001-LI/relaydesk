"""
rag —— 检索增强（RAG）的"地基"模块（PR1）。

本包只负责"检索能力"本身，不碰图/节点/评测：
  - embeddings.py：把文本转成向量（指向硅基流动的 OpenAI 兼容 embedding）
  - rerank.py：用硅基流动自有 /rerank 接口对召回结果精排
  - store.py：切块 → 灌入本地 Chroma → 暴露 retriever

设计取向：复用 LangChain 的标准件（OpenAIEmbeddings / Chroma / TextSplitter），
只对硅基流动 rerank 这类"没有现成封装"的接口手写薄封装。
"""

from langgraph_cs.rag.embeddings import build_embeddings
from langgraph_cs.rag.rerank import rerank
from langgraph_cs.rag.store import build_retriever, ingest

__all__ = ["build_embeddings", "rerank", "build_retriever", "ingest"]
