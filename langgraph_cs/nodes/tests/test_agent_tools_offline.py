"""
Agent 工具循环的**离线**单测（真实 build_graph，不联网）。

策略：
  - patch graph.py 里注册用的 intent_node / rag_node，避免触网；
  - patch agent.build_llm，返回支持 bind_tools 的脚本化 FakeLLM；
  - patch tools.py 里的 business_db 函数，避免访问真实业务库。

运行：
    langgraph_cs/.venv/bin/python -m langgraph_cs.nodes.tests.test_agent_tools_offline
"""
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from langgraph_cs import graph as graph_mod
from langgraph_cs.nodes import agent as agent_mod
from langgraph_cs.nodes import tools as tools_mod


class _SharedScript:
    def __init__(self, responses):
        self.responses = list(responses)
        self.bound_tool_names = []
        self.invocations = []


class _FakeLLM:
    def __init__(self, shared):
        self._shared = shared

    def bind_tools(self, tools):
        self._shared.bound_tool_names.append([tool.name for tool in tools])
        return self

    def invoke(self, messages):
        self._shared.invocations.append(messages)
        if not self._shared.responses:
            raise AssertionError("FakeLLM response queue exhausted")
        return self._shared.responses.pop(0)


def _patch(obj, name, value):
    orig = getattr(obj, name)
    setattr(obj, name, value)

    def restore():
        setattr(obj, name, orig)

    return restore


def _run_with_patches(restores, fn):
    try:
        return fn()
    finally:
        for restore in reversed(restores):
            restore()


def test_billing_agent_calls_tool_and_returns_to_billing():
    """billing 路径：billing_agent -> tools -> billing_agent，最终输出文本回复。"""
    shared = _SharedScript(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "query_bill",
                        "args": {"bill_id": "BILL-1"},
                        "id": "call_bill_1",
                    }
                ],
            ),
            AIMessage(content="已查到 BILL-1，发票状态为已开票。"),
        ]
    )

    restores = [
        _patch(graph_mod, "intent_node", lambda state: {"intent": "billing", "confidence": 0.95, "escalated": False}),
        _patch(graph_mod, "rag_node", lambda state: {"retrieved_docs": []}),
        _patch(agent_mod, "build_llm", lambda temperature=0.5: _FakeLLM(shared)),
        _patch(tools_mod.business_db, "get_bill", lambda bill_id: {"bill_id": bill_id, "invoice_status": "已开票"}),
    ]

    def run():
        graph = graph_mod.build_graph()
        config = {"configurable": {"thread_id": "tool-billing"}}
        updates = list(
            graph.stream(
                {"messages": [HumanMessage(content="帮我查 BILL-1 的发票状态")]},
                config=config,
                stream_mode="updates",
            )
        )
        node_names = [next(iter(update.keys())) for update in updates]
        values = graph.get_state(config).values
        final = values["messages"][-1]
        return node_names, values, final

    node_names, values, final = _run_with_patches(restores, run)

    assert node_names == ["intent", "rag", "billing_agent", "tools", "billing_agent"], node_names
    assert final.content == "已查到 BILL-1，发票状态为已开票。", final
    assert any(isinstance(msg, ToolMessage) for msg in values["messages"]), values["messages"]
    assert shared.bound_tool_names and "query_bill" in shared.bound_tool_names[0], shared.bound_tool_names
    assert any(isinstance(msg, ToolMessage) for msg in shared.invocations[-1]), shared.invocations[-1]
    print("✓ billing 工具循环：billing_agent -> tools -> billing_agent，最终产出文本回复")


def test_general_agent_does_not_bind_or_call_tools():
    """general 路径：不 bind_tools，不经过 tools 节点。"""
    shared = _SharedScript([AIMessage(content="这是通用回复。")])
    restores = [
        _patch(graph_mod, "intent_node", lambda state: {"intent": "other", "confidence": 0.95, "escalated": False}),
        _patch(graph_mod, "rag_node", lambda state: {"retrieved_docs": []}),
        _patch(agent_mod, "build_llm", lambda temperature=0.5: _FakeLLM(shared)),
    ]

    def run():
        graph = graph_mod.build_graph()
        config = {"configurable": {"thread_id": "tool-general"}}
        updates = list(
            graph.stream(
                {"messages": [HumanMessage(content="你好")]},
                config=config,
                stream_mode="updates",
            )
        )
        node_names = [next(iter(update.keys())) for update in updates]
        values = graph.get_state(config).values
        return node_names, values["messages"][-1]

    node_names, final = _run_with_patches(restores, run)

    assert node_names == ["intent", "rag", "general_agent"], node_names
    assert "tools" not in node_names, node_names
    assert shared.bound_tool_names == [], shared.bound_tool_names
    assert final.content == "这是通用回复。", final
    print("✓ general 路径：不绑定工具，不经过 tools 节点")


def _run_all():
    tests = [
        test_billing_agent_calls_tool_and_returns_to_billing,
        test_general_agent_does_not_bind_or_call_tools,
    ]
    for test in tests:
        test()
    print("\n全部 agent tools 离线用例通过 ✅（真实 build_graph + mock LLM/业务库）")


if __name__ == "__main__":
    _run_all()
