"""
bm25 —— BM25 词法（稀疏）检索器，作为"弱一阶段检索器"用于 rerank 价值对照实验。

为什么要它？
  本项目的 dense 向量检索器（硅基流动 bge-large-zh-v1.5）在这份短 FAQ 上太强了：
  正确条目几乎总被排进 top-3，rerank 几乎"没活可干"（Hit@3 100% vs 100%，recovered=0）。
  要证明 rerank 真的有价值，得先有一个**会犯错**的一阶段检索器，让 rerank 有救援空间。

  BM25 正是这样一个"弱"检索器：它是**词法/稀疏**检索——靠 query 与文档的**字面词项重合**
  打分（TF-IDF 家族），不理解语义。所以当用户**换了说法**（同义改写、口语化、近义词）时，
  字面词项对不上，BM25 就很容易把正确条目排在后面甚至漏掉。这种"召回弱"正是 rerank
  （交叉编码器，能理解 query×文档语义）发挥价值的来源：BM25 取宽候选 → rerank 精排捞回。

中文分词：
  BM25 按"词项"匹配，中文没有天然空格，必须先分词。这里用 jieba 把 query 和文档都切成词，
  query 与文档用**同一套**分词逻辑，词项空间才一致。

数据同源（关键，保证评测公平）：
  BM25 索引建在与向量库**完全相同**的条目 chunk 上——直接复用 store.load_faq_documents()
  解析出的 Document（同样的 page_content=问题+答案、同样的 metadata={source,item_id}）。
  绝不在这里重写一份 faq 解析逻辑。这样 dense 与 bm25 两条链路吃的是
  同一批 chunk，唯一差别就是"一阶段检索算法"，对照才干净。
"""
import logging
from pathlib import Path

import jieba
from langchain_core.documents import Document
from rank_bm25 import BM25Okapi

from langgraph_cs.rag.store import DEFAULT_DOCS_DIR, load_faq_documents

logger = logging.getLogger(__name__)


def _tokenize(text: str) -> list[str]:
    """
    中文分词：用 jieba 切词，去掉纯空白 token。

    query 与文档共用本函数，保证词项空间一致（同一个词在两侧切法相同才能匹配上）。
    这里用最朴素的精确模式分词，不做停用词过滤，保持切词透明；
    停用词反而可能把"忘记/怎么/如何"这类对客服 FAQ 有区分度的词去掉。
    """
    return [tok for tok in jieba.lcut(text) if tok.strip()]


class BM25Retriever:
    """
    基于 rank_bm25.BM25Okapi 的本地词法检索器（纯本地，**不连网**）。

    内部持有：
      - self.documents：与向量库同源的 121 个条目 Document（保留 item_id/source metadata）；
      - self.bm25：在这些文档的分词结果上建好的 BM25Okapi 索引。

    检索时把 query 同样 jieba 分词，用 BM25 给每个文档打分，取分数最高的 top-k。
    """

    def __init__(self, documents: list[Document]) -> None:
        self.documents = documents
        # 对每个文档的 page_content 分词，喂给 BM25Okapi 建库（语料是"分词后的 token 列表的列表"）。
        corpus_tokens = [_tokenize(doc.page_content) for doc in documents]
        self.bm25 = BM25Okapi(corpus_tokens)
        logger.info("BM25 索引建好：%d 个条目 chunk（词法/稀疏检索，本地、不连网）", len(documents))

    def search(self, query: str, k: int = 5) -> list[tuple[str, str, str, float]]:
        """
        对 query 做 BM25 词法检索，返回分数最高的 top-k。

        返回：[(item_id, source, text, score), ...]，按 BM25 分数降序。
          - item_id / source 取自该条目 Document 的 metadata（与向量库一致，可做命中判定）；
          - text 即 page_content（问题+答案）；
          - score 是 BM25 相关性分数（字面词项重合度，越大越相关；可能为 0 = 完全没词对上）。

        注意：BM25 是**词法**匹配——query 若与正确条目**没有共同词项**（换了说法），
        正确条目分数可能很低甚至 0，被排到后面，于是 miss。这正是 rerank 要救的场景。
        """
        if not self.documents:
            return []
        query_tokens = _tokenize(query)
        scores = self.bm25.get_scores(query_tokens)  # 每个文档一个分数，下标与 self.documents 对齐
        # 按分数降序取下标的前 k 个。
        ranked_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        out: list[tuple[str, str, str, float]] = []
        for i in ranked_idx:
            doc = self.documents[i]
            out.append((
                doc.metadata.get("item_id", "?"),
                doc.metadata.get("source", "?"),
                doc.page_content,
                float(scores[i]),
            ))
        return out

    def search_documents(self, query: str, k: int = 5) -> list[Document]:
        """
        与 search 同源，但返回原始 Document 列表（按 BM25 分数降序，保留完整 metadata）。

        方便评测脚本像用向量 retriever 一样拿到带 item_id 的 Document，
        统一走 _ids_from_hits / rerank 那套逻辑，无需为 BM25 单写一套。
        """
        if not self.documents:
            return []
        query_tokens = _tokenize(query)
        scores = self.bm25.get_scores(query_tokens)
        ranked_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        return [self.documents[i] for i in ranked_idx]


def build_bm25_retriever(docs_dir: Path | str = DEFAULT_DOCS_DIR) -> BM25Retriever:
    """
    构造 BM25 词法检索器：复用 store.load_faq_documents() 解析出与向量库同源的条目 chunk，
    在其上建 BM25 索引，返回 BM25Retriever。

    纯本地，不发任何网络请求（与 dense 检索器需要 embedding API 不同），
    因此可在离线/无 key 环境下直接自测词法召回质量。
    """
    documents = load_faq_documents(docs_dir)
    return BM25Retriever(documents)


if __name__ == "__main__":
    # 离线自测入口：python -m langgraph_cs.rag.bm25
    # 建索引 → 对几条"换了说法"的 query 检索 → 打印 top-k 的 item_id，
    # 直观看到 BM25 经常把正确条目漏出 top-3（证明留有 rerank 救援空间）。
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    retriever = build_bm25_retriever()
    demos = [
        # (口语化/换说法的 query, 期望命中的 item_id)
        ("登录密码忘了进不去，怎么重新弄个新的？", "account-01"),
        ("收到提醒说我账号在外地登录了，是不是号被盗了？", "account-05"),
        ("付款时要的那个支付密码我忘了，怎么重新设置？", "account-04"),
    ]
    for q, expect in demos:
        top = retriever.search(q, k=3)
        ids = [t[0] for t in top]
        hit = "HIT" if expect in ids else "MISS"
        print(f"[{hit}] 期望 {expect} | BM25 top-3 = {ids} | query: {q}")
