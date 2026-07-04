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
from langgraph.types import Command

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


def _refund_tool_call_message():
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "create_refund_ticket",
                "args": {
                    "user_id": "user_003",
                    "order_id": "ORD-20260506-003",
                    "reason": "尺码不合适",
                },
                "id": "call_refund_1",
            }
        ],
    )


def _run_refund_approval_flow(resume_value, final_text):
    shared = _SharedScript([_refund_tool_call_message(), AIMessage(content=final_text)])
    create_calls = []

    def fake_create_ticket(user_id, ticket_type, detail):
        create_calls.append({"user_id": user_id, "ticket_type": ticket_type, "detail": detail})
        return {
            "ticket_id": "TKT-REFUND-1",
            "user_id": user_id,
            "ticket_type": ticket_type,
            "status": "待审批",
            "detail": detail,
        }

    restores = [
        _patch(graph_mod, "intent_node", lambda state: {"intent": "billing", "confidence": 0.96, "escalated": False}),
        _patch(graph_mod, "rag_node", lambda state: {"retrieved_docs": []}),
        _patch(agent_mod, "build_llm", lambda temperature=0.5: _FakeLLM(shared)),
        _patch(tools_mod.business_db, "create_ticket", fake_create_ticket),
    ]

    def run():
        graph = graph_mod.build_graph()
        config = {"configurable": {"thread_id": "tool-refund-approval"}}
        first_updates = list(
            graph.stream(
                {"messages": [HumanMessage(content="我要申请退款，订单 ORD-20260506-003，尺码不合适")]},
                config=config,
                stream_mode="updates",
            )
        )
        interrupt_update = next(update for update in first_updates if "__interrupt__" in update)
        payload = interrupt_update["__interrupt__"][0].value

        resume_updates = list(
            graph.stream(
                Command(resume=resume_value),
                config=config,
                stream_mode="updates",
            )
        )
        values = graph.get_state(config).values
        final = values["messages"][-1]
        tool_messages = [msg for msg in values["messages"] if isinstance(msg, ToolMessage)]
        return payload, resume_updates, values, final, tool_messages

    payload, resume_updates, values, final, tool_messages = _run_with_patches(restores, run)
    return payload, resume_updates, values, final, tool_messages, create_calls, shared


def test_create_refund_ticket_interrupt_approved_resume_creates_once():
    payload, _updates, values, final, tool_messages, create_calls, shared = _run_refund_approval_flow(
        {"approved": True, "note": "请尽快处理"},
        "已为你提交退款申请，工单 TKT-REFUND-1。",
    )

    assert payload["kind"] == "approval", payload
    assert payload["action"] == "create_refund_ticket", payload
    assert payload["params"] == {
        "user_id": "user_003",
        "order_id": "ORD-20260506-003",
        "reason": "尺码不合适",
    }, payload
    assert len(create_calls) == 1, create_calls
    assert create_calls[0]["ticket_type"] == "refund", create_calls
    assert "审批备注：请尽快处理" in create_calls[0]["detail"], create_calls
    assert final.content == "已为你提交退款申请，工单 TKT-REFUND-1。", final
    assert any('"approved": true' in msg.content for msg in tool_messages), tool_messages
    assert values.get("escalated") is False, values
    assert len(shared.bound_tool_names) >= 2, shared.bound_tool_names
    print("✓ create_refund_ticket：真实图中断 approval，批准 resume 后只落库一次并返回最终回复")


def test_create_refund_ticket_interrupt_rejected_resume_does_not_create():
    payload, _updates, values, final, tool_messages, create_calls, _shared = _run_refund_approval_flow(
        {"approved": False, "note": "资料不全"},
        "退款申请未通过：资料不全。",
    )

    assert payload["kind"] == "approval", payload
    assert create_calls == [], create_calls
    assert final.content == "退款申请未通过：资料不全。", final
    assert any('"rejected": true' in msg.content and "资料不全" in msg.content for msg in tool_messages), tool_messages
    assert values.get("escalated") is False, values
    print("✓ create_refund_ticket：驳回 resume 后不落库，工具返回 rejected JSON，Agent 给出文本回复")


def test_create_refund_ticket_interrupt_non_dict_resume_rejected():
    payload, _updates, values, final, tool_messages, create_calls, _shared = _run_refund_approval_flow(
        "yes",
        "退款申请未通过：审批协议异常。",
    )

    assert payload["kind"] == "approval", payload
    assert create_calls == [], create_calls
    assert final.content == "退款申请未通过：审批协议异常。", final
    assert any('"rejected": true' in msg.content and "yes" in msg.content for msg in tool_messages), tool_messages
    assert values.get("escalated") is False, values
    print("✓ create_refund_ticket：非 dict resume 按驳回处理，不落库")


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
        test_create_refund_ticket_interrupt_approved_resume_creates_once,
        test_create_refund_ticket_interrupt_rejected_resume_does_not_create,
        test_create_refund_ticket_interrupt_non_dict_resume_rejected,
        test_general_agent_does_not_bind_or_call_tools,
    ]
    for test in tests:
        test()
    print("\n全部 agent tools 离线用例通过 ✅（真实 build_graph + mock LLM/业务库）")


if __name__ == "__main__":
    _run_all()
