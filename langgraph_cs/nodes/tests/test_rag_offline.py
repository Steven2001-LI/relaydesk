"""
rag_node 的**离线**单测（绝不联网、不发任何真实 embedding/rerank 调用）。

策略：把 rag_node 依赖的两个网络件——build_retriever（向量检索）与 rerank（精排）——
用 mock 顶替（monkeypatch 掉 nodes.rag 模块里 import 进来的同名符号），构造确定的
检索结果与 rerank 结果，断言 rag_node 产出的 retrieved_docs 是**结构化条目**：
每条含 {"item_id", "source", "text", "score"}，且：
  - 开 rerank 时 score = rerank 的 relevance_score；
  - 关 rerank（朴素检索）时 score = None；
  - greeting / other 意图早退仍返回 []；
  - 检索 0 条 / 检索抛错 / rerank 抛错都按既有契约降级，不崩。

运行：
    langgraph_cs/.venv/bin/python -m langgraph_cs.nodes.tests.test_rag_offline
"""
import os

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage

from langgraph_cs.nodes import rag as rag_mod


# ─────────────────────────────────────────────────────────────────────────
# 假检索件：完全本地、零网络。
# ─────────────────────────────────────────────────────────────────────────
class _FakeRetriever:
    """模拟 build_retriever(k=...).invoke(query) -> List[Document]。"""

    def __init__(self, docs):
        self._docs = docs

    def invoke(self, query):
        return self._docs


def _make_docs():
    """两条带 item_id/source metadata 的 FAQ chunk（与 store.py 灌库结构一致）。"""
    return [
        Document(
            page_content="如何重置密码\n进入设置-账户-安全，点重置密码。",
            metadata={"source": "account.md", "item_id": "account-01"},
        ),
        Document(
            page_content="登录失败排查\n先确认网络，再清缓存重试。",
            metadata={"source": "account.md", "item_id": "account-02"},
        ),
    ]


def _state(intent="technical", query="我登录不上去"):
    return {"intent": intent, "messages": [HumanMessage(content=query)]}


def _install(monkey_retriever=None, monkey_rerank=None):
    """把 nodes.rag 里的 build_retriever / rerank 替换成 mock，返回还原函数。"""
    orig_retriever = rag_mod.build_retriever
    orig_rerank = rag_mod.rerank
    if monkey_retriever is not None:
        rag_mod.build_retriever = monkey_retriever
    if monkey_rerank is not None:
        rag_mod.rerank = monkey_rerank

    def restore():
        rag_mod.build_retriever = orig_retriever
        rag_mod.rerank = orig_rerank

    return restore


# ─────────────────────────────────────────────────────────────────────────
# 用例
# ─────────────────────────────────────────────────────────────────────────
def test_rerank_produces_structured_entries_with_score():
    """开 rerank：retrieved_docs 是结构化条目，score 来自 rerank 的 relevance_score。"""
    docs = _make_docs()
    # mock rerank：返回 (原始 index, relevance_score, text)，已降序。
    # 故意把第 2 条排前，验证 rag_node 用 index 映射回原 hits 取 metadata。
    def fake_rerank(query, doc_texts, top_n=None):
        return [(1, 0.97, doc_texts[1]), (0, 0.42, doc_texts[0])]

    restore = _install(
        monkey_retriever=lambda k=5: _FakeRetriever(docs),
        monkey_rerank=fake_rerank,
    )
    os.environ["RAG_USE_RERANK"] = "1"
    try:
        out = rag_mod.rag_node(_state())
    finally:
        restore()

    result = out["retrieved_docs"]
    assert isinstance(result, list) and len(result) == 2, result
    # 每条都是结构化 dict，含四个字段。
    for entry in result:
        assert set(entry.keys()) == {"item_id", "source", "text", "score"}, entry
    # 顺序与 rerank 一致：account-02（score 0.97）在前。
    assert result[0]["item_id"] == "account-02" and result[0]["score"] == 0.97, result[0]
    assert result[0]["source"] == "account.md"
    assert "登录失败" in result[0]["text"]
    assert result[1]["item_id"] == "account-01" and result[1]["score"] == 0.42, result[1]
    print("✓ rerank 开：retrieved_docs 为结构化条目，含 item_id/source/text/score，score=relevance_score")


def test_naive_retrieval_has_none_score():
    """关 rerank（朴素检索）：仍是结构化条目，但 score=None。"""
    docs = _make_docs()
    restore = _install(monkey_retriever=lambda k=5: _FakeRetriever(docs))
    os.environ["RAG_USE_RERANK"] = "0"
    try:
        out = rag_mod.rag_node(_state())
    finally:
        restore()
        os.environ["RAG_USE_RERANK"] = "1"  # 还原默认

    result = out["retrieved_docs"]
    assert len(result) == 2, result
    for entry in result:
        assert set(entry.keys()) == {"item_id", "source", "text", "score"}, entry
        assert entry["score"] is None, entry  # 朴素检索无 rerank 分数
    # 保持粗排顺序与 item_id。
    assert [e["item_id"] for e in result] == ["account-01", "account-02"], result
    print("✓ rerank 关（朴素）：结构化条目，score=None，保留 item_id/source/text")


def test_rerank_failure_falls_back_to_naive_structured():
    """rerank 抛错：降级为粗排，仍产出结构化条目（score=None），不丢检索结果、不崩。"""
    docs = _make_docs()

    def boom_rerank(query, doc_texts, top_n=None):
        raise RuntimeError("模拟 rerank 接口 500")

    restore = _install(
        monkey_retriever=lambda k=5: _FakeRetriever(docs),
        monkey_rerank=boom_rerank,
    )
    os.environ["RAG_USE_RERANK"] = "1"
    try:
        out = rag_mod.rag_node(_state())
    finally:
        restore()

    result = out["retrieved_docs"]
    assert len(result) == 2, result
    for entry in result:
        assert set(entry.keys()) == {"item_id", "source", "text", "score"}, entry
        assert entry["score"] is None, entry  # 降级到粗排，无分数
    print("✓ rerank 失败：降级粗排，仍是结构化条目（score=None），不崩")


def test_skip_intents_return_empty():
    """greeting / other 意图早退，返回 []（不检索、不触网）。"""
    for intent in ("greeting", "other"):
        out = rag_mod.rag_node(_state(intent=intent))
        assert out == {"retrieved_docs": []}, (intent, out)
    print("✓ greeting/other 早退返回 []")


def test_empty_query_returns_empty():
    """没有用户文本时返回 []。"""
    out = rag_mod.rag_node({"intent": "technical", "messages": []})
    assert out == {"retrieved_docs": []}, out
    print("✓ 无用户文本返回 []")


def test_no_hits_returns_empty():
    """检索 0 条返回 []。"""
    restore = _install(monkey_retriever=lambda k=5: _FakeRetriever([]))
    try:
        out = rag_mod.rag_node(_state())
    finally:
        restore()
    assert out == {"retrieved_docs": []}, out
    print("✓ 检索 0 条返回 []")


def test_retriever_failure_returns_empty():
    """向量检索抛错 -> 降级为 []，不崩。"""
    def boom_build(k=5):
        raise RuntimeError("模拟 Chroma 未灌库")

    restore = _install(monkey_retriever=boom_build)
    try:
        out = rag_mod.rag_node(_state())
    finally:
        restore()
    assert out == {"retrieved_docs": []}, out
    print("✓ 检索失败降级为 []")


def _run_all():
    tests = [
        test_rerank_produces_structured_entries_with_score,
        test_naive_retrieval_has_none_score,
        test_rerank_failure_falls_back_to_naive_structured,
        test_skip_intents_return_empty,
        test_empty_query_returns_empty,
        test_no_hits_returns_empty,
        test_retriever_failure_returns_empty,
    ]
    for t in tests:
        t()
    print("\n全部 rag_node 离线用例通过 ✅（结构化条目 + 早退/降级，未发起任何网络调用）")


if __name__ == "__main__":
    _run_all()
