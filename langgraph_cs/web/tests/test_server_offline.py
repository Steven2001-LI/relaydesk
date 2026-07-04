"""
web 适配层的**离线**验证（绝不联网、不发任何真实 LLM 调用）。

策略：用一个假的 FakeGraph 顶替 build_graph() 返回的编译图，
构造它的 .stream(...) 产出"假的 (mode, chunk) 序列"，以及 .get_state(...) 的假状态。
这样就能在零依赖、零网络的情况下，断言：
  1) GET /            返回 200 + HTML
  2) /api/chat        的 SSE 事件拼装顺序与字段（meta / rag / route / token / done）
  3) interrupt        命中转人工时发 interrupt 事件且不发 done
  4) /api/resume      用 Command(resume=...) 续跑，发坐席 token + done(escalated=True)
  5) error            图 stream 抛异常时转 error 事件，请求不 500

运行：
    langgraph_cs/.venv/bin/python -m langgraph_cs.web.tests.test_server_offline
（也可用 pytest，但这里写成可直接 python 运行的自测，零额外依赖。）
"""
import json

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langgraph.types import Command, Interrupt

from langgraph_cs.web import server


# ─────────────────────────────────────────────────────────────────────────
# 假对象：模拟编译图的 .stream() 与 .get_state()。
# ─────────────────────────────────────────────────────────────────────────
def _chunk(content, id="lc_run--1", **kwargs):
    """真实流式增量：server 只应把 AIMessageChunk 的非空 content 发成 token。"""
    return AIMessageChunk(content=content, id=id, **kwargs)


def _full(content, id="uuid-replay", **kwargs):
    """节点 return 的完整 AIMessage 重发：即使 id 全新且 content 非空，也不能发成 token。"""
    return AIMessage(content=content, id=id, **kwargs)


class _FakeStateSnapshot:
    """模拟 graph.get_state(config) 的返回（只用到 .values）。"""

    def __init__(self, values):
        self.values = values


class FakeGraph:
    """
    可编排的假图：
      - stream(input, config, stream_mode): 第一次调用吐 normal_seq，
        若 input 是 Command（resume）则吐 resume_seq。
      - get_state(config): 按"已 stream 到哪一步"返回对应 state values。
    用类属性把"应该吐什么"写死，便于每个用例定制。
    """

    def __init__(self, normal_seq, resume_seq=None, state_values=None, raise_on_stream=False):
        self._normal_seq = normal_seq
        self._resume_seq = resume_seq or []
        self._state_values = state_values or {}
        self._raise = raise_on_stream
        self.inputs = []
        self.resume_values = []

    def stream(self, graph_input, config=None, stream_mode=None):
        if self._raise:
            raise RuntimeError("模拟 LLM/检索炸了")
        self.inputs.append(graph_input)
        if isinstance(graph_input, Command):
            self.resume_values.append(graph_input.resume)
        seq = self._resume_seq if isinstance(graph_input, Command) else self._normal_seq
        for item in seq:
            yield item

    def get_state(self, config):
        return _FakeStateSnapshot(self._state_values)


def _make_client(fake_graph):
    """把 server 的图单例替换成 fake，并返回 TestClient。"""
    server._graph = fake_graph  # 直接注入单例，跳过 build_graph()
    return TestClient(server.app)


def _parse_sse(text):
    """把 SSE 响应体解析成事件 dict 列表。"""
    events = []
    for block in text.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
    return events


# ─────────────────────────────────────────────────────────────────────────
# 用例
# ─────────────────────────────────────────────────────────────────────────
def test_index_returns_html():
    client = _make_client(FakeGraph(normal_seq=[]))
    resp = client.get("/")
    assert resp.status_code == 200, resp.status_code
    assert "text/html" in resp.headers["content-type"]
    assert "RelayDesk" in resp.text
    print("✓ GET / -> 200 + HTML（含 RelayDesk 品牌）")


def test_chat_normal_flow():
    """普通一问一答：meta -> rag -> route -> token... -> done。"""
    # 正常 stream 序列：intent 更新、rag 更新、technical_agent 更新 + token 流。
    # 关键：updates 的增量里直接带 intent/confidence 与 retrieved_docs（meta/rag 应从这里取）。
    # 两个 token chunk 共享同一个流式 id（lc_run--1）—— 模拟 LLM 真实流式生成。
    # retrieved_docs 现在是**结构化条目**（list[dict]，含 item_id/source/text/score），
    # rag 事件的 sources 应取稳定的 item_id（不再截 text 首行）。
    normal_seq = [
        ("updates", {"intent": {"intent": "technical", "confidence": 0.95}}),
        ("updates", {"rag": {"retrieved_docs": [
            {"item_id": "account-01", "source": "account.md",
             "text": "如何重置密码\n答案正文…", "score": 0.97},
            {"item_id": "account-02", "source": "account.md",
             "text": "登录失败排查\n步骤…", "score": 0.81},
        ]}}),
        ("messages", (_chunk("你", id="lc_run--1"), {"langgraph_node": "technical_agent"})),
        ("messages", (_chunk("好", id="lc_run--1"), {"langgraph_node": "technical_agent"})),
        ("updates", {"technical_agent": {"messages": ["...AIMessage..."]}}),
    ]
    # 复刻真实 bug 场景：流式 yield update 那一刻，intent/rag 字段尚未落 checkpointer，
    # 故 get_state().values 里 intent/confidence/retrieved_docs 仍为 null/空。
    # 若代码错误地从 get_state 取值，meta 的 intent/confidence 就会是 null —— 测试会失败。
    # 正确实现应从 updates 增量(delta)直接取，断言才能拿到 technical/0.95。
    state_values = {
        "intent": None,
        "confidence": None,
        "retrieved_docs": [],
        "escalated": False,
    }
    fake = FakeGraph(normal_seq=normal_seq, state_values=state_values)
    client = _make_client(fake)

    resp = client.post("/api/chat", json={"message": "电脑连不上网", "thread_id": "t-1"})
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]

    # 顺序断言：meta 在最前，done 在最后，中间有 rag/route/token。
    assert types[0] == "meta", types
    assert "rag" in types and "route" in types
    assert types.count("token") == 2, types
    assert types[-1] == "done", types

    meta = next(e for e in events if e["type"] == "meta")
    assert meta["intent"] == "technical" and meta["confidence"] == 0.95
    rag = next(e for e in events if e["type"] == "rag")
    # sources 现在是稳定的 item_id（不再是 text 首行截断）。
    assert rag["sources"] == ["account-01", "account-02"], rag["sources"]
    route = next(e for e in events if e["type"] == "route")
    assert route["agent"] == "technical_agent"
    # route 只发一次（messages 分支补发 + updates 分支互斥，不重复）。
    assert types.count("route") == 1, types
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert tokens == "你好", tokens
    done = next(e for e in events if e["type"] == "done")
    assert done["escalated"] is False
    print("✓ /api/chat 正常流：meta/rag/route/token×2/done 顺序与字段正确，token 拼接=", tokens)


def test_route_emitted_before_first_token():
    """
    route 时机修正：messages 的第一个 agent token 先于 updates 那条节点 state 更新到达时，
    route 事件应在第一个 token **之前（或同时）** 发出，且全程只发一次。

    复刻真实顺序：intent/rag 更新 -> agent 的两个 token（messages，先到）->
    最后才是 technical_agent 的 update（updates，后到）。
    旧实现只在 updates 分支发 route，会让 route 落在 token 之后；新实现在第一个 token 前补发。
    """
    normal_seq = [
        ("updates", {"intent": {"intent": "technical", "confidence": 0.95}}),
        ("updates", {"rag": {"retrieved_docs": []}}),
        # token 先到（messages 流），此刻 technical_agent 的 update 还没来。
        ("messages", (_chunk("你", id="lc_run--1"), {"langgraph_node": "technical_agent"})),
        ("messages", (_chunk("好", id="lc_run--1"), {"langgraph_node": "technical_agent"})),
        # 节点 state 更新最后才到（updates 流）——旧实现要等到这里才发 route。
        ("updates", {"technical_agent": {"messages": ["...AIMessage..."]}}),
    ]
    state_values = {"intent": None, "confidence": None, "retrieved_docs": [], "escalated": False}
    fake = FakeGraph(normal_seq=normal_seq, state_values=state_values)
    client = _make_client(fake)

    resp = client.post("/api/chat", json={"message": "电脑连不上网", "thread_id": "t-route"})
    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]

    # route 恰好出现一次。
    assert types.count("route") == 1, types
    # route 在第一个 token 之前（索引更小）。
    first_route = types.index("route")
    first_token = types.index("token")
    assert first_route < first_token, (
        f"route 应在第一个 token 之前发出，实际 types={types}"
    )
    # route 落点正确。
    route = next(e for e in events if e["type"] == "route")
    assert route["agent"] == "technical_agent", route
    # token 仍正常拼接（去重未被破坏）。
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert tokens == "你好", tokens
    print("✓ route 时机：第一个 token 之前补发 route（agent=technical_agent），只发一次，去重不破")


def test_chat_dedups_resent_agent_message():
    """
    回归守护：LangGraph 对 agent 节点的 messages 流会重发节点返回的完整消息，
    导致答案 ×2。server 按消息类型放行 AIMessageChunk，丢弃完整 AIMessage 重发，
    token 拼接应只出现**一份**答案。

    复刻真实重发：
      · 第一组 id="lc_run--1" 的两个 chunk（LLM 流式 token）—— 要保留；
      · 第二组 id="77f5c943-uuid" 的一条完整消息（节点 return 被二次重发）—— 要丢弃。
    """
    normal_seq = [
        ("updates", {"intent": {"intent": "technical", "confidence": 0.95}}),
        ("updates", {"rag": {"retrieved_docs": []}}),
        # 第一组：真实流式 token（共享 lc_run--1）。
        ("messages", (_chunk("你", id="lc_run--1"), {"langgraph_node": "technical_agent"})),
        ("messages", (_chunk("好", id="lc_run--1"), {"langgraph_node": "technical_agent"})),
        # 第二组：节点返回消息被 messages 流二次重发（不同 id），整段答案重复一遍 -> 应被丢弃。
        ("messages", (_full("你好", id="77f5c943-uuid"), {"langgraph_node": "technical_agent"})),
        ("updates", {"technical_agent": {"messages": ["...AIMessage..."]}}),
    ]
    state_values = {"intent": None, "confidence": None, "retrieved_docs": [], "escalated": False}
    fake = FakeGraph(normal_seq=normal_seq, state_values=state_values)
    client = _make_client(fake)

    resp = client.post("/api/chat", json={"message": "电脑连不上网", "thread_id": "t-dedup"})
    events = _parse_sse(resp.text)
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    # 去重生效：只放行第一组（lc_run--1）的两个 chunk，重发那条被丢弃 -> 答案只一份。
    assert tokens == "你好", f"去重后应只出现一份答案，实际={tokens!r}"
    assert [e["type"] for e in events if e["type"] == "token"].count("token") == 2, tokens
    print("✓ /api/chat 去重：node 返回消息二次重发被丢弃，token 只拼出一份答案 =", tokens)


def test_tool_call_stream_events_and_final_answer_dedup():
    """
    工具调用轮：route 必须先于 tool 事件；同一 tool_call 的多个 chunk 只发一次 start；
    最终答案只放行 AIMessageChunk，完整 AIMessage 重发即使是新 id + 非空 content 也要丢弃。
    """
    tool_call = {
        "name": "query_bill",
        "args": {"bill_id": "BILL-1"},
        "id": "call_bill_1",
    }
    normal_seq = [
        ("updates", {"intent": {"intent": "billing", "confidence": 0.96}}),
        ("updates", {"rag": {"retrieved_docs": []}}),
        # 同一个工具调用分两个 chunk 到达；只能发一次 tool start。
        ("messages", (_chunk(
            "",
            id="lc_run--tool",
            tool_call_chunks=[{"name": "query_bill", "args": '{"bill_id"', "id": "call_bill_1", "index": 0}],
        ), {"langgraph_node": "billing_agent"})),
        # 真实流式的续传分片：name 和 id 都是 None（只有首分片带 name/id）。
        # 这条必须既不产生第二个 tool start，也不产生 name="unknown" 的幽灵事件。
        ("messages", (_chunk(
            "",
            id="lc_run--tool",
            tool_call_chunks=[{"name": None, "args": ': "BILL-1"}', "id": None, "index": 0}],
        ), {"langgraph_node": "billing_agent"})),
        # agent 节点完整消息更新也带 tool_calls；应被 start 去重挡住。
        ("updates", {"billing_agent": {"messages": [_full("", id="agent-tool-call", tool_calls=[tool_call])]}}),
        ("updates", {"tools": {"messages": [
            ToolMessage(content='{"found": true}', name="query_bill", tool_call_id="call_bill_1")
        ]}}),
        # 工具后最终答案：真实 token 是 AIMessageChunk。
        ("messages", (_chunk("已", id="lc_run--answer"), {"langgraph_node": "billing_agent"})),
        ("messages", (_chunk("查到", id="lc_run--answer"), {"langgraph_node": "billing_agent"})),
        # 节点 return 的完整重发：新 id + 非空 content，必须丢弃，不能让答案 ×2。
        ("messages", (_full("已查到", id="new-uuid-replay"), {"langgraph_node": "billing_agent"})),
        ("updates", {"billing_agent": {"messages": [_full("已查到", id="new-uuid-replay")]}}),
    ]
    state_values = {"intent": None, "confidence": None, "retrieved_docs": [], "escalated": False}
    fake = FakeGraph(normal_seq=normal_seq, state_values=state_values)
    client = _make_client(fake)

    resp = client.post("/api/chat", json={"message": "查账单 BILL-1", "thread_id": "t-tool"})
    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]
    tool_events = [e for e in events if e["type"] == "tool"]
    starts = [e for e in tool_events if e["status"] == "start"]
    dones = [e for e in tool_events if e["status"] == "done"]
    tokens = "".join(e["text"] for e in events if e["type"] == "token")

    assert types.count("route") == 1, types
    assert "tool" in types and types.index("route") < types.index("tool"), types
    assert len(starts) == 1 and starts[0]["name"] == "query_bill", tool_events
    assert len(dones) == 1 and dones[0]["name"] == "query_bill", tool_events
    assert tokens == "已查到", tokens
    assert types.count("token") == 2, types
    print("✓ 工具流式：route 先于 tool，tool start/done 各一次，最终答案去重正确")


def test_chat_interrupt_then_resume():
    """转人工：chat 命中 interrupt（发 interrupt、不发 done），resume 后发坐席 token + done。"""
    # chat 阶段：intent=escalation、rag 空、然后 __interrupt__。
    interrupt_obj = Interrupt(
        value={"prompt": "已转人工，请坐席回复：", "user_message": "我要转人工"}
    )
    normal_seq = [
        ("updates", {"intent": {"intent": "escalation", "confidence": 0.99}}),
        ("updates", {"rag": {"retrieved_docs": []}}),
        ("updates", {"__interrupt__": (interrupt_obj,)}),
    ]
    # resume 阶段：escalation 节点把坐席回复作为 token 吐出 + 更新 escalated。
    resume_seq = [
        ("messages", (_full("您好，我是坐席小李，这就帮您处理。", id="seat-reply-1"), {"langgraph_node": "escalation"})),
        ("updates", {"escalation": {"messages": ["..."], "escalated": True}}),
    ]
    state_values = {
        "intent": "escalation",
        "confidence": 0.99,
        "retrieved_docs": [],
        "escalated": True,
    }
    fake = FakeGraph(normal_seq=normal_seq, resume_seq=resume_seq, state_values=state_values)
    client = _make_client(fake)

    # 1) chat -> 应有 interrupt，且无 done。
    r1 = client.post("/api/chat", json={"message": "我要转人工", "thread_id": "t-2"})
    e1 = _parse_sse(r1.text)
    t1 = [e["type"] for e in e1]
    assert "interrupt" in t1, t1
    assert "done" not in t1, "中断时不应发 done（done 留给 resume）"
    inter = next(e for e in e1 if e["type"] == "interrupt")
    assert inter["kind"] == "seat"
    assert "转人工" in inter["prompt"]
    assert inter["user_message"] == "我要转人工"

    # 2) resume -> 应有坐席 token + done(escalated=True)。
    r2 = client.post("/api/resume", json={"thread_id": "t-2", "seat_reply": "您好，我是坐席小李"})
    e2 = _parse_sse(r2.text)
    t2 = [e["type"] for e in e2]
    assert "token" in t2 and t2[-1] == "done", t2
    seat_text = "".join(e["text"] for e in e2 if e["type"] == "token")
    assert "坐席小李" in seat_text
    done = next(e for e in e2 if e["type"] == "done")
    assert done["escalated"] is True
    assert fake.resume_values == ["您好，我是坐席小李"], fake.resume_values
    print("✓ interrupt->resume：chat 发 interrupt（无 done），resume 发坐席 token + done(escalated=True)")


def test_chat_approval_interrupt_payload():
    """审批中断：interrupt 事件透传 kind/action/params/prompt，且中断时不发 done。"""
    interrupt_obj = Interrupt(
        value={
            "kind": "approval",
            "action": "create_refund_ticket",
            "params": {
                "user_id": "user_003",
                "order_id": "ORD-20260506-003",
                "reason": "尺码不合适",
            },
            "prompt": "待人工审批：user_003 申请退款 订单 ORD-20260506-003（原因：尺码不合适）",
        }
    )
    normal_seq = [
        ("updates", {"intent": {"intent": "billing", "confidence": 0.96}}),
        ("updates", {"rag": {"retrieved_docs": []}}),
        ("updates", {"__interrupt__": (interrupt_obj,)}),
    ]
    fake = FakeGraph(
        normal_seq=normal_seq,
        state_values={"intent": "billing", "confidence": 0.96, "retrieved_docs": [], "escalated": False},
    )
    client = _make_client(fake)

    resp = client.post("/api/chat", json={"message": "我要退款", "thread_id": "t-approval"})
    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]
    assert "interrupt" in types, types
    assert "done" not in types, types
    inter = next(e for e in events if e["type"] == "interrupt")
    assert inter["kind"] == "approval", inter
    assert inter["action"] == "create_refund_ticket", inter
    assert inter["params"]["order_id"] == "ORD-20260506-003", inter
    assert "待人工审批" in inter["prompt"], inter
    print("✓ approval interrupt：SSE 透传 kind/action/params/prompt，且中断时不发 done")


def test_approval_resume_uses_structured_resume_and_done_not_escalated():
    """审批 resume：传结构化 dict 给图；done.escalated=false。"""
    resume_seq = [
        # resume 恢复时可能先跑 tools 节点，因此 tool done 可早于 route 到达。
        ("updates", {"tools": {"messages": [
            ToolMessage(content='{"created": true}', name="create_refund_ticket", tool_call_id="call_refund_1")
        ]}}),
        ("messages", (_chunk("已", id="lc_run--approval-answer"), {"langgraph_node": "billing_agent"})),
        ("messages", (_chunk("受理", id="lc_run--approval-answer"), {"langgraph_node": "billing_agent"})),
        ("updates", {"billing_agent": {"messages": [_full("已受理", id="approval-final-replay")]}}),
    ]
    fake = FakeGraph(
        normal_seq=[],
        resume_seq=resume_seq,
        state_values={"intent": "billing", "confidence": 0.96, "retrieved_docs": [], "escalated": False},
    )
    client = _make_client(fake)

    resp = client.post(
        "/api/resume",
        json={"thread_id": "t-approval", "approval": {"approved": True, "note": "请尽快处理"}},
    )
    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]
    assert fake.resume_values == [{"approved": True, "note": "请尽快处理"}], fake.resume_values
    assert "tool" in types and "route" in types and "token" in types, types
    assert types.index("tool") < types.index("route"), types
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert tokens == "已受理", tokens
    done = next(e for e in events if e["type"] == "done")
    assert done["escalated"] is False, done
    print("✓ approval resume：结构化 resume 传入图；tool 可早于 route；done.escalated=false")


def test_approval_resume_rejects_non_bool_approved():
    """审批 resume 的 approved 必须是 bool，1/'yes' 等真值不允许通过 server 校验。"""
    fake = FakeGraph(normal_seq=[])
    client = _make_client(fake)
    resp = client.post(
        "/api/resume",
        json={"thread_id": "t-approval", "approval": {"approved": 1, "note": "yes"}},
    )
    events = _parse_sse(resp.text)
    assert events and events[0]["type"] == "error", events
    assert fake.resume_values == [], fake.resume_values
    print("✓ approval resume：approved 非 bool 时返回 error，不调用图 resume")


def test_approval_resume_rejects_malformed_requests():
    """
    resume 协议加固：三类畸形请求都必须 error 且不触碰图。
      1) seat_reply 与 approval 同传（歧义：静默取舍会掩盖前端 bug，
         且 str resume 会被审批工具按"非 dict -> 驳回"处理，批准被反转成驳回）；
      2) approval 不是对象（如 "yes"）；
      3) approval.note 不是字符串（任意 JSON 值会被 str() 成 repr 注入工单 detail）。
    """
    cases = [
        ({"thread_id": "t-x", "seat_reply": "hi", "approval": {"approved": True}}, "二选一"),
        ({"thread_id": "t-x", "approval": "yes"}, "对象"),
        ({"thread_id": "t-x", "approval": {"approved": True, "note": {"evil": 1}}}, "字符串"),
    ]
    for body, keyword in cases:
        fake = FakeGraph(normal_seq=[])
        client = _make_client(fake)
        resp = client.post("/api/resume", json=body)
        events = _parse_sse(resp.text)
        assert events and events[0]["type"] == "error", (body, events)
        assert keyword in events[0]["message"], (keyword, events[0])
        assert fake.resume_values == [], (body, fake.resume_values)
    print("✓ approval resume 加固：同传歧义 / 非对象 / note 非字符串 均 error 且不触碰图")


def test_chat_error_event_on_exception():
    """图 stream 抛异常时，应转成 error 事件（HTTP 仍 200，不 500 崩）。"""
    fake = FakeGraph(normal_seq=[], raise_on_stream=True)
    client = _make_client(fake)
    resp = client.post("/api/chat", json={"message": "随便问", "thread_id": "t-3"})
    assert resp.status_code == 200, resp.status_code
    events = _parse_sse(resp.text)
    assert events and events[-1]["type"] == "error", events
    print("✓ stream 抛异常 -> error 事件（HTTP 200，不 500）")


def test_missing_params_error():
    """缺 message/thread_id 时走 error 事件。"""
    client = _make_client(FakeGraph(normal_seq=[]))
    resp = client.post("/api/chat", json={"thread_id": "t-4"})
    events = _parse_sse(resp.text)
    assert events and events[0]["type"] == "error"
    print("✓ 缺参数 -> error 事件")


def _run_all():
    tests = [
        test_index_returns_html,
        test_chat_normal_flow,
        test_route_emitted_before_first_token,
        test_chat_dedups_resent_agent_message,
        test_tool_call_stream_events_and_final_answer_dedup,
        test_chat_interrupt_then_resume,
        test_chat_approval_interrupt_payload,
        test_approval_resume_uses_structured_resume_and_done_not_escalated,
        test_approval_resume_rejects_non_bool_approved,
        test_approval_resume_rejects_malformed_requests,
        test_chat_error_event_on_exception,
        test_missing_params_error,
    ]
    for t in tests:
        # 每个用例都重置图单例，避免相互污染。
        server._reset_graph()
        t()
    print("\n全部离线用例通过 ✅（未发起任何真实网络/ LLM 调用）")


if __name__ == "__main__":
    _run_all()
