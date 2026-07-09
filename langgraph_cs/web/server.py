"""
server.py —— 把 LangGraph 客服图包成 FastAPI 的"web 适配层"。

它做三件事，且**只做这三件**（不碰图/节点/state 的核心逻辑）：
  1) GET /            用 Jinja2 渲染聊天页（templates/index.html）；其余静态资源走 StaticFiles。
  2) POST /api/chat   把"用户这一句"喂进图，用 SSE 把图执行过程的内部信号流式吐给前端：
                      意图、RAG 引用、路由到的 Agent、专职 Agent 的增量 token、转人工中断、收尾。
  3) POST /api/resume 命中转人工(interrupt)后，前端切坐席模式，坐席输入经此用
                      Command(resume=...) 恢复图，把坐席回复流式/收尾吐回。

为什么用 SSE（Server-Sent Events）而不是 WebSocket？
  - 客服对话是"一问一答、服务端单向推流"，SSE 正好够用，且**无需第三方库**：
    SSE 就是 `Content-Type: text/event-stream` + 每条消息 `data: <json>\n\n`。
    用 Starlette/FastAPI 的 StreamingResponse 包一个生成器即可，零额外依赖。

与 CLI 的关系（复用，不复制）：
  - 同样调用 build_graph()（默认 checkpointer = make_checkpointer()，memory 即可）；
  - thread_id 由前端生成并在每次请求里带上，维持多轮记忆 —— 等价于 CLI 的固定 thread_id，
    只是改成"每个浏览器会话一个 id"。
  - interrupt/resume 闭环与 main.py 完全一致，只是把"CLI input()"换成"前端切坐席 + /api/resume"。

流式实现依据（已按本机 langgraph 1.2.6 实测，不是凭记忆）：
  graph.stream(input, config, stream_mode=["updates", "messages"]) 会逐个 yield (mode, chunk)：
    - mode == "updates"：chunk 形如 {节点名: {被更新的 state 字段...}}；
        * 命中 {"intent": {...}} 之后，直接从该增量字典读 intent/confidence -> meta 事件
          （不用 get_state：流式 yield 那一刻状态还没落 checkpointer，get_state 会读到 null）；
        * 命中 {"rag": {...}} 之后，直接从增量读 retrieved_docs -> rag 事件；
        * 命中 {"technical_agent"/"billing_agent"/"general_agent": {...}} -> route 事件（路由落点）；
        * 命中 {"__interrupt__": (Interrupt(value={...}),)} -> interrupt 事件（转人工，图已暂停）。
    - mode == "messages"：chunk 是 (消息块, metadata)，metadata["langgraph_node"] 是产出该 token
        的节点名；只转发专职 Agent 节点的增量内容做"打字机" token 事件。
  暂停后 get_state(config).next 非空（如 ('escalation',)）；resume 用 Command(resume=...) 续跑。
"""
import json
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.types import Command

from langgraph_cs.config import build_session_config
from langgraph_cs.graph import build_graph

logger = logging.getLogger(__name__)

# 静态资源目录（app.js / style.css / js/ 都在这里）。
_STATIC_DIR = Path(__file__).parent / "static"
# 模板目录（index.html 在这里，用 Jinja2 循环渲染决策轨迹 5 个 stage）。
_TEMPLATES_DIR = Path(__file__).parent / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# 决策轨迹 5 个 stage 的展示数据，供 index.html 模板循环渲染。
# ⚠️ key 的顺序和取值必须和 js/pipeline.js 里的 STAGE_ORDER 完全一致，
#    两处各自独立声明，改一处记得改另一处。
_STAGES = [
    {"key": "intent", "idx": 1, "label": "意图识别"},
    {"key": "rag", "idx": 2, "label": "知识库检索"},
    {"key": "route", "idx": 3, "label": "路由分发"},
    {"key": "tool", "idx": 4, "label": "业务工具"},
    {"key": "answer", "idx": 5, "label": "生成应答"},
]

# 哪些节点产出的 token 算"机器人正文"，需要做打字机。
# 四个专职 Agent + escalation（坐席回复也作为机器人消息显示）。
_AGENT_NODES = {"technical_agent", "billing_agent", "general_agent", "escalation"}
_LLM_AGENT_NODES = {"technical_agent", "billing_agent", "general_agent"}

# 兜底的坐席提示（图里 escalation_node 会带 prompt，这里只是 fallback）。
_DEFAULT_INTERRUPT_PROMPT = "已转人工，请以坐席身份回复用户："


# ─────────────────────────────────────────────────────────────────────────
# 图单例：懒加载。
#   懒加载的意义：import server 时**不**触发 build_graph()，于是离线测试 / py_compile /
#   仅起静态页都不需要 DEEPSEEK key；只有真正 /api/chat 时才编译图。测试里也能在
#   build_graph 被 mock 掉后，通过 _reset_graph() 拿到 mock 版单例。
# ─────────────────────────────────────────────────────────────────────────
_graph = None


def get_graph():
    """返回编译好的图单例（首次调用时才 build_graph，之后复用）。"""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def _reset_graph() -> None:
    """清掉图单例缓存。仅供测试在 mock build_graph 后强制重建用。"""
    global _graph
    _graph = None


# ─────────────────────────────────────────────────────────────────────────
# SSE 工具：把 dict 拼成一条 `data: <json>\n\n` 文本。
#   ensure_ascii=False 让中文按原样传（前端 EventSource 按 UTF-8 解码），更省字节也更好调试。
# ─────────────────────────────────────────────────────────────────────────
def _sse(event_type: str, **payload) -> str:
    """生成一条 SSE 数据行：data: {"type": <event_type>, ...payload}\n\n"""
    data = {"type": event_type, **payload}
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _intent_meta(graph, config, delta=None) -> dict:
    """
    intent 节点跑完后，给出 intent/confidence。

    取值来源（优先 delta，回退 get_state）：
      为什么优先用 delta（即 updates chunk 里该节点返回的 state 增量）而非 get_state？
      —— 在 `graph.stream(stream_mode=["updates","messages"])` 流式 yield 某个 update 的"那一瞬间"，
         该节点的状态**尚未落到 checkpointer**，此刻 get_state(config).values 读到的还是旧值（intent/confidence 为 null）。
         而 updates 的增量字典本身就携带该节点刚返回的字段（intent 节点的增量里直接有 intent/confidence），
         从 delta 直接读才是"当下、可靠"的值。get_state 仅作无 delta 时的兜底。
    """
    if delta is not None:
        return {
            "intent": delta.get("intent"),
            "confidence": delta.get("confidence"),
        }
    values = graph.get_state(config).values
    return {
        "intent": values.get("intent"),
        "confidence": values.get("confidence"),
    }


def _rag_sources(graph, config, delta=None) -> list:
    """
    rag 节点跑完后，给出"引用了哪些知识库条目"——返回稳定的条目 item_id 列表。

    rag_node 现在往 state 写**结构化**条目（list[dict]，每条含 item_id/source/text/score，
    见 nodes/rag.py 与 state.py）。这里优先取稳定的 item_id（形如 "billing-03"）作为来源标识，
    不再截 text 首行——前端据此在 tooltip 里列条目 id。
    兼容兜底：万一某条缺 item_id（理论上不会），退而取 source；再不行才截 text 首行。
    没有检索结果就返回空列表。

    取值同样优先 delta（updates 增量里直接含 rag 节点写的 retrieved_docs），理由同 _intent_meta：
    流式 yield 的瞬间状态未落 checkpointer，从增量读才稳；取不到再回退 get_state。
    """
    if delta is not None and delta.get("retrieved_docs") is not None:
        docs = delta.get("retrieved_docs") or []
    else:
        values = graph.get_state(config).values
        docs = values.get("retrieved_docs") or []
    sources = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        # 优先用稳定的 item_id；缺失时降级到 source；都没有再截 text 首行兜底。
        label = doc.get("item_id") or doc.get("source")
        if not label:
            text = (doc.get("text") or "").strip()
            if not text:
                continue
            head = text.splitlines()[0].strip()
            label = head[:24] + ("…" if len(head) > 24 else "")
        sources.append(label)
    return sources


def _interrupt_payload(chunk) -> dict:
    """
    从 updates 里的 {"__interrupt__": (Interrupt(value={...}),)} 取出中断载荷。

    chunk["__interrupt__"] 是 Interrupt 对象的元组；取第一个的 .value（dict），
    按 kind 区分 seat（转人工聊天）与 approval（敏感操作审批）。
    缺 kind 时按 seat 兼容旧 payload；缺字段给默认值。
    """
    interrupts = chunk.get("__interrupt__") or ()
    if interrupts:
        value = getattr(interrupts[0], "value", None)
        if isinstance(value, dict):
            params = value.get("params") or {}
            if not isinstance(params, dict):
                params = {}
            # 统一用 `or` 兜底：显式传 None 的畸形字段（如 "prompt": None）也回落默认值，
            # 与 params 的 isinstance 防御保持同一强度。
            return {
                "kind": value.get("kind") or "seat",
                "action": value.get("action") or "",
                "params": params,
                "prompt": value.get("prompt") or _DEFAULT_INTERRUPT_PROMPT,
                "user_message": value.get("user_message") or "",
            }
    return {
        "kind": "seat",
        "action": "",
        "params": {},
        "prompt": _DEFAULT_INTERRUPT_PROMPT,
        "user_message": "",
    }


def _tool_call_refs(msg) -> list[tuple[str, str]]:
    """
    从 AIMessage/AIMessageChunk 里提取工具调用引用，返回 [(去重 key, tool name)]。

    tool_call_chunks 会随流式增量分片到达；同一个调用可能出现多次，所以调用方必须按 key 去重。
    key 优先用 tool_call id；缺 id 时退到 name+index。

    关键细节（已用 AIMessageChunk 实测确认）：真实流式下只有**第一个**分片带 name/id，
    后续纯参数续传分片 name 和 id 都是 None——这类分片必须跳过，否则会按
    "unknown:index" 造出一个假 key，凭空多发一条 name="unknown" 的 tool start 事件。
    跳过不丢信息：带 name 的首分片（或节点返回消息里解析好的 tool_calls）总会先到。
    """
    refs = []
    for call in getattr(msg, "tool_calls", None) or []:
        name = call.get("name")
        if not name:
            continue
        key = call.get("id") or f"{name}:{call.get('index', 0)}"
        refs.append((key, name))
    for call in getattr(msg, "tool_call_chunks", None) or []:
        name = call.get("name")
        if not name:  # 参数续传分片：name/id 均为 None，跳过
            continue
        key = call.get("id") or f"{name}:{call.get('index', 0)}"
        refs.append((key, name))
    return refs


def _tool_messages(delta) -> list[tuple[str, str]]:
    """从 tools 节点的 state 增量里提取 ToolMessage，返回 [(去重 key, tool name)]。"""
    messages = []
    if isinstance(delta, dict):
        raw = delta.get("messages") or []
        messages = raw if isinstance(raw, list) else [raw]

    refs = []
    for msg in messages:
        if isinstance(msg, ToolMessage):
            name = getattr(msg, "name", None) or "unknown"
            key = getattr(msg, "tool_call_id", None) or name
            refs.append((key, name))
    return refs


def _stream_graph(graph_input, config):
    """
    核心：把一次 graph.stream(...) 的产出翻译成一串 SSE 文本（生成器）。

    chat 与 resume 共用它 —— 唯一区别是 graph_input：
      - chat   传 {"messages": [HumanMessage(...)]}
      - resume 传 Command(resume=<坐席回复或审批结果>)
    两者之后的事件协议完全一致（route/token/interrupt/done），所以抽成一份（见 code-reuse 指南）。

    事件协议（每条都是 `data: {"type": ...}\n\n`）：
      meta      {intent, confidence}           —— intent 节点后
      rag       {sources: [...]}               —— rag 节点后（没有就空）
      route     {agent}                        —— 路由落点（专职节点名）
      tool      {name, status}                 —— 工具调用开始/完成（前端本阶段忽略）
      token     {text}                         —— 专职 Agent 增量 token（打字机）
      interrupt {kind, action, params, prompt, user_message}
                                              —— 命中中断，图已暂停；前端按 kind 切坐席/审批模式
      done      {escalated}                    —— 本轮收尾
      error     {message}                      —— 任意异常（不让请求 500 崩）

    异常处理（遵循 error-handling：不向客户端抛裸异常）：
      整段 stream 包在 try/except 里；LLM/检索/图执行任何报错都转成 error 事件后正常结束流，
      前端据此提示用户，而不是收到一个断开的连接或 500。
    """
    graph = get_graph()
    interrupted = False
    # ── route 事件"只发一次、且尽早发"的标记 ───────────────────────────────────
    # 为什么需要它？（已用 langgraph 实测确认，非凭记忆）
    #   route 表示"路由落到了哪个专职 Agent"。原先它只在 updates 分支（agent 节点的
    #   state 更新到达）才发；但 messages 流的**第一个 token** 往往**先于**那条 update 到达，
    #   导致前端"路由"步骤点亮晚于"应答"步骤，观感是先答后路由。
    # 改进：在 messages 分支里，一旦识别出第一个专职 Agent 节点产出的 token
    #   （metadata 的 langgraph_node ∈ _AGENT_NODES），若 route 尚未发过，就**先**补发一个
    #   route 事件（agent=该 node），再发该 token。updates 分支则只在 route 还没发过时才发，
    #   二者择一、只发一次（用 route_sent 标记互斥）。
    route_sent = False
    # ── 工具事件与 token 去重 ────────────────────────────────────────────────
    # 工具调用会先产生 content="" 的 tool_call chunks，随后 tools 节点产出 ToolMessage，
    # 最终 Agent 再生成正文。route 必须排在本轮第一个 agent 信号（tool 或 token）之前。
    #
    # token 去重的关键规则：只放行 AIMessageChunk 的正文。节点 return 的完整 AIMessage
    # 会以另一个新 id 二次重发，若按"新 id 就放行"会让答案 ×2，所以完整 AIMessage 一律不发 token。
    # escalation 是坐席回复，不走 LLM 流式重发，保留原行为。
    seen_tool_starts = set()
    seen_tool_dones = set()

    def ensure_route(node_name):
        nonlocal route_sent
        if not route_sent:
            route_sent = True
            return _sse("route", agent=node_name)
        return None

    def tool_start_events(msg):
        events = []
        for key, name in _tool_call_refs(msg):
            if key in seen_tool_starts:
                continue
            seen_tool_starts.add(key)
            events.append(_sse("tool", name=name, status="start"))
        return events

    def tool_done_events(delta):
        events = []
        for key, name in _tool_messages(delta):
            if key in seen_tool_dones:
                continue
            seen_tool_dones.add(key)
            events.append(_sse("tool", name=name, status="done"))
        return events
    try:
        for mode, chunk in graph.stream(
            graph_input, config=config, stream_mode=["updates", "messages"]
        ):
            if mode == "updates":
                # chunk: {节点名: {被更新字段...}} 或 {"__interrupt__": (...)}。
                if "__interrupt__" in chunk:
                    interrupted = True
                    payload = _interrupt_payload(chunk)
                    yield _sse("interrupt", **payload)
                    # 命中中断后图已暂停，本次 stream 到此为止（不再有后续节点）。
                    break
                # chunk.items() 给到 (节点名, 该节点返回的 state 增量)。
                # 直接把增量(delta)喂给取值函数：intent/confidence、retrieved_docs 都在增量里，
                # 不依赖 get_state（流式那一刻状态尚未落 checkpointer，get_state 会读到 null）。
                for node_name, delta in chunk.items():
                    if node_name == "intent":
                        yield _sse("meta", **_intent_meta(graph, config, delta=delta))
                    elif node_name == "rag":
                        yield _sse("rag", sources=_rag_sources(graph, config, delta=delta))
                    elif node_name in _AGENT_NODES:
                        # 专职 Agent 节点的 state 更新到达 -> 这就是路由落点。
                        # 但若该节点的第一个 token 已先一步在 messages 分支里发过 route，
                        # 这里就不再重复发（route_sent 互斥，保证只发一次）。
                        route_event = ensure_route(node_name)
                        if route_event:
                            yield route_event
                        if isinstance(delta, dict):
                            for msg in delta.get("messages") or []:
                                for event in tool_start_events(msg):
                                    yield event
                    elif node_name == "tools":
                        for event in tool_done_events(delta):
                            yield event
            elif mode == "messages":
                # chunk: (消息块, metadata)。只转发专职 Agent 节点产生的增量正文。
                msg, meta = chunk
                node = (meta or {}).get("langgraph_node")
                text = getattr(msg, "content", None)
                if node in _AGENT_NODES:
                    if _tool_call_refs(msg):
                        route_event = ensure_route(node)
                        if route_event:
                            yield route_event
                        for event in tool_start_events(msg):
                            yield event

                    if node == "escalation" and text:
                        route_event = ensure_route(node)
                        if route_event:
                            yield route_event
                        yield _sse("token", text=text)
                    elif node in _LLM_AGENT_NODES and text and isinstance(msg, AIMessageChunk):
                        route_event = ensure_route(node)
                        if route_event:
                            yield route_event
                        yield _sse("token", text=text)
                    elif node in _LLM_AGENT_NODES and text and isinstance(msg, AIMessage):
                        # 完整 AIMessage 是节点 return 的二次重发，即使 id 没见过也不能放行。
                        continue

        # 没有中断才发 done（中断时前端在等坐席输入，done 留给 resume 之后发）。
        if not interrupted:
            escalated = bool(get_graph().get_state(config).values.get("escalated"))
            yield _sse("done", escalated=escalated)
    except Exception as ex:  # noqa: BLE001 web 边界统一兜底：转 error 事件，不让连接 500 崩
        logger.warning("图执行流式过程中出错，转 error 事件：%s", ex)
        yield _sse("error", message="处理你的请求时出了点问题，请稍后重试。")


def _sse_response(generator) -> StreamingResponse:
    """把 SSE 生成器包成正确的 StreamingResponse（带防缓冲头）。"""
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 关掉 nginx 等反代的缓冲，确保 token 实时吐出（本地直连也无害）。
            "X-Accel-Buffering": "no",
        },
    )


# ─────────────────────────────────────────────────────────────────────────
# FastAPI 应用装配。
# ─────────────────────────────────────────────────────────────────────────
def build_app() -> FastAPI:
    """构造 FastAPI 应用（抽成函数，方便测试拿到全新实例）。"""
    app = FastAPI(title="RelayDesk LangGraph 客服 Web 演示", docs_url=None, redoc_url=None)

    @app.get("/")
    def index(request: Request):
        """返回聊天主页面。"""
        return _templates.TemplateResponse(request, "index.html", {"stages": _STAGES})

    @app.post("/api/chat")
    async def chat(request: Request):
        """
        入参 JSON：{message: str, thread_id: str, session_user_id?: str}
        以 SSE 流式返回本轮图执行的全部事件（见 _stream_graph 协议）。
        """
        body = await request.json()
        message = (body.get("message") or "").strip()
        thread_id = (body.get("thread_id") or "").strip()
        session_user_id = (body.get("session_user_id") or "").strip()
        if not message or not thread_id:
            # 参数缺失也走 SSE error，让前端用同一套通道处理。
            return _sse_response(
                iter([_sse("error", message="缺少 message 或 thread_id。")])
            )
        # demo 身份由客户端声明，非认证；生产必须由服务端从已认证会话派生，不可信客户端输入。
        config = build_session_config(thread_id, session_user_id)
        graph_input = {"messages": [HumanMessage(content=message)]}
        return _sse_response(_stream_graph(graph_input, config))

    @app.post("/api/resume")
    async def resume(request: Request):
        """
        中断后恢复图。
          - 转人工：{thread_id: str, session_user_id?: str, seat_reply: str}
          - 审批：  {thread_id: str, session_user_id?: str, approval: {approved: bool, note: str}}
        用 Command(resume=...) 续跑，SSE 把后续 token/tool/done 吐回。

        信任边界（demo 限定，评审已记录）：本端点与 /api/chat 共用无鉴权的
        thread_id，发起对话的客户端可以自己 resume——生产环境必须把坐席/审批
        拆到带鉴权的独立端，此处不做。
        """
        body = await request.json()
        thread_id = (body.get("thread_id") or "").strip()
        session_user_id = (body.get("session_user_id") or "").strip()
        seat_reply = body.get("seat_reply")
        approval = body.get("approval")
        if not thread_id:
            return _sse_response(
                iter([_sse("error", message="缺少 thread_id。")])
            )
        if seat_reply is not None and approval is not None:
            # 两种 resume 语义互斥：歧义请求直接拒绝，不做静默取舍
            # （若静默取 seat_reply，字符串会被审批工具按"非 dict -> 驳回"处理，
            #   审批人明明批准却被驳回，前端 bug 会被完全掩盖）。
            return _sse_response(
                iter([_sse("error", message="seat_reply 与 approval 只能二选一。")])
            )
        if seat_reply is not None:
            resume_value = str(seat_reply)
        elif approval is not None:
            if not isinstance(approval, dict):
                return _sse_response(
                    iter([_sse("error", message="approval 必须是对象：{approved: bool, note?: str}。")])
                )
            if type(approval.get("approved")) is not bool:
                return _sse_response(
                    iter([_sse("error", message="approval.approved 必须是 bool。")])
                )
            note = approval.get("note")
            if note is not None and not isinstance(note, str):
                return _sse_response(
                    iter([_sse("error", message="approval.note 必须是字符串。")])
                )
            resume_value = {
                "approved": approval["approved"],
                "note": (note or "").strip(),
            }
        else:
            return _sse_response(
                iter([_sse("error", message="缺少 seat_reply 或 approval。")])
            )
        # demo 身份由客户端声明，非认证；生产必须由服务端从已认证会话派生，不可信客户端输入。
        config = build_session_config(thread_id, session_user_id)
        return _sse_response(_stream_graph(Command(resume=resume_value), config))

    # 静态资源（app.js / style.css 等）挂在 /static 下。
    # 放在路由注册之后，避免 StaticFiles 抢占根路径。
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    return app


# 模块级单例 app —— uvicorn "langgraph_cs.web.server:app" 与 __main__.py 都用它。
app = build_app()
