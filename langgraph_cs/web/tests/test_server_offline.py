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
from langgraph.types import Command, Interrupt

from langgraph_cs.web import server


# ─────────────────────────────────────────────────────────────────────────
# 假对象：模拟编译图的 .stream() 与 .get_state()。
# ─────────────────────────────────────────────────────────────────────────
class _FakeMsg:
    """模拟 messages 流里的消息块：只需要 .content。"""

    def __init__(self, content):
        self.content = content


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

    def stream(self, graph_input, config=None, stream_mode=None):
        if self._raise:
            raise RuntimeError("模拟 LLM/检索炸了")
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
    assert "EchoMind" in resp.text
    print("✓ GET / -> 200 + HTML（含 EchoMind 品牌）")


def test_chat_normal_flow():
    """普通一问一答：meta -> rag -> route -> token... -> done。"""
    # 正常 stream 序列：intent 更新、rag 更新、technical_agent 更新 + token 流。
    # 关键：updates 的增量里直接带 intent/confidence 与 retrieved_docs（meta/rag 应从这里取）。
    normal_seq = [
        ("updates", {"intent": {"intent": "technical", "confidence": 0.95}}),
        ("updates", {"rag": {"retrieved_docs": ["如何重置密码\n答案正文…", "登录失败排查\n步骤…"]}}),
        ("messages", (_FakeMsg("你"), {"langgraph_node": "technical_agent"})),
        ("messages", (_FakeMsg("好"), {"langgraph_node": "technical_agent"})),
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
    assert rag["sources"] and "如何重置密码" in rag["sources"][0]
    route = next(e for e in events if e["type"] == "route")
    assert route["agent"] == "technical_agent"
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert tokens == "你好", tokens
    done = next(e for e in events if e["type"] == "done")
    assert done["escalated"] is False
    print("✓ /api/chat 正常流：meta/rag/route/token×2/done 顺序与字段正确，token 拼接=", tokens)


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
        ("messages", (_FakeMsg("您好，我是坐席小李，这就帮您处理。"), {"langgraph_node": "escalation"})),
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
    print("✓ interrupt->resume：chat 发 interrupt（无 done），resume 发坐席 token + done(escalated=True)")


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
        test_chat_interrupt_then_resume,
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
