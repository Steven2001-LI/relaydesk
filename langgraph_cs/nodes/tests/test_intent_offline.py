"""
intent_node 的**离线**单测（绝不联网、不发任何真实 LLM 调用）。

策略：把 intent_node 依赖的 build_llm 顶替成 mock，构造确定的 LLM 输出或异常，
断言：
  - 每轮必经的 intent_node 会复位 escalated=False；
  - LLM 调用失败时降级为 other/0.0，不抛异常。

运行：
    langgraph_cs/.venv/bin/python -m langgraph_cs.nodes.tests.test_intent_offline
"""
from langchain_core.messages import HumanMessage

from langgraph_cs.nodes import intent as intent_mod


class _FakeResponse:
    """模拟 LangChain chat model response，只暴露 intent_node 需要的 content。"""

    def __init__(self, content):
        self.content = content


class _FakeLLM:
    """模拟 build_llm(...).invoke(messages)。"""

    def __init__(self, content=None, error=None):
        self._content = content
        self._error = error

    def invoke(self, messages):
        if self._error is not None:
            raise self._error
        return _FakeResponse(self._content)


def _state(query="我要查一下账单"):
    return {"messages": [HumanMessage(content=query)]}


def _install(fake_llm):
    """把 nodes.intent 里的 build_llm 替换成 mock，返回还原函数。"""
    orig_build_llm = intent_mod.build_llm
    intent_mod.build_llm = lambda temperature=0.0: fake_llm

    def restore():
        intent_mod.build_llm = orig_build_llm

    return restore


def test_intent_node_resets_escalated_flag():
    """正常识别时：返回 intent/confidence，同时复位 escalated=False。"""
    restore = _install(_FakeLLM('{"intent": "billing", "confidence": 0.91}'))
    try:
        out = intent_mod.intent_node(_state())
    finally:
        restore()

    assert out["intent"] == "billing", out
    assert out["confidence"] == 0.91, out
    assert out["escalated"] is False, out
    print("✓ intent_node 正常识别时复位 escalated=False")


def test_llm_failure_falls_back_to_other():
    """LLM invoke 抛错：intent_node 不抛，降级为 other/0.0，并复位 escalated=False。"""
    restore = _install(_FakeLLM(error=RuntimeError("模拟 LLM 超时")))
    try:
        out = intent_mod.intent_node(_state("网络错误还能继续吗"))
    finally:
        restore()

    assert out == {"intent": "other", "confidence": 0.0, "escalated": False}, out
    print("✓ LLM 失败：降级为 other/0.0，复位 escalated=False，不崩图")


def _run_all():
    tests = [
        test_intent_node_resets_escalated_flag,
        test_llm_failure_falls_back_to_other,
    ]
    for t in tests:
        t()
    print("\n全部 intent_node 离线用例通过 ✅（escalated 复位 + LLM 失败兜底，未发起任何网络调用）")


if __name__ == "__main__":
    _run_all()
