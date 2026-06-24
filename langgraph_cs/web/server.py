"""
server.py —— 把 LangGraph 客服图包成 FastAPI 的"web 适配层"。

它做三件事，且**只做这三件**（不碰图/节点/state 的核心逻辑）：
  1) GET /            返回静态聊天页（static/index.html）；其余静态资源走 StaticFiles。
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
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from langgraph_cs.graph import build_graph

logger = logging.getLogger(__name__)

# 静态资源目录（index.html / app.js / style.css 都在这里）。
_STATIC_DIR = Path(__file__).parent / "static"

# 哪些节点产出的 token 算"机器人正文"，需要做打字机。
# 四个专职 Agent + escalation（坐席回复也作为机器人消息显示）。
_AGENT_NODES = {"technical_agent", "billing_agent", "general_agent", "escalation"}

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
    rag 节点跑完后，尽力给出"引用了哪些知识库条目"。

    诚实说明：rag_node 往 state 只写**纯文本** retrieved_docs（不带 item_id），
    所以这里拿不到稳定的条目 id。做法：取每条检索文本的首行（FAQ 的标题问题）做简短摘要，
    既能在前端 chips 上展示"引用了什么"，又不谎称有 id。没有检索结果就返回空列表。
    （若日后 rag_node 改为往 state 写 item_id，这里优先用 id 即可，无需改前端。）

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
        text = (doc or "").strip()
        if not text:
            continue
        # 取首行作为来源摘要，超长截断，避免 chip 过长。
        head = text.splitlines()[0].strip()
        sources.append(head[:24] + ("…" if len(head) > 24 else ""))
    return sources


def _interrupt_payload(chunk) -> dict:
    """
    从 updates 里的 {"__interrupt__": (Interrupt(value={...}),)} 取出坐席提示。

    chunk["__interrupt__"] 是 Interrupt 对象的元组；取第一个的 .value（dict），
    里面带 escalation_node 写的 {"prompt", "user_message"}。兜底给默认提示。
    """
    interrupts = chunk.get("__interrupt__") or ()
    if interrupts:
        value = getattr(interrupts[0], "value", None)
        if isinstance(value, dict):
            return {
                "prompt": value.get("prompt", _DEFAULT_INTERRUPT_PROMPT),
                "user_message": value.get("user_message", ""),
            }
    return {"prompt": _DEFAULT_INTERRUPT_PROMPT, "user_message": ""}


def _stream_graph(graph_input, config):
    """
    核心：把一次 graph.stream(...) 的产出翻译成一串 SSE 文本（生成器）。

    chat 与 resume 共用它 —— 唯一区别是 graph_input：
      - chat   传 {"messages": [HumanMessage(...)]}
      - resume 传 Command(resume=<坐席输入>)
    两者之后的事件协议完全一致（route/token/interrupt/done），所以抽成一份（见 code-reuse 指南）。

    事件协议（每条都是 `data: {"type": ...}\n\n`）：
      meta      {intent, confidence}           —— intent 节点后
      rag       {sources: [...]}               —— rag 节点后（没有就空）
      route     {agent}                        —— 路由落点（专职节点名）
      token     {text}                         —— 专职 Agent 增量 token（打字机）
      interrupt {prompt, user_message}         —— 命中转人工，图已暂停；前端切坐席模式
      done      {escalated}                    —— 本轮收尾
      error     {message}                      —— 任意异常（不让请求 500 崩）

    异常处理（遵循 error-handling：不向客户端抛裸异常）：
      整段 stream 包在 try/except 里；LLM/检索/图执行任何报错都转成 error 事件后正常结束流，
      前端据此提示用户，而不是收到一个断开的连接或 500。
    """
    graph = get_graph()
    interrupted = False
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
                        # 专职 Agent 节点开始产出 -> 这就是路由落点。
                        yield _sse("route", agent=node_name)
            elif mode == "messages":
                # chunk: (消息块, metadata)。只转发专职 Agent 节点产生的增量正文。
                msg, meta = chunk
                node = (meta or {}).get("langgraph_node")
                text = getattr(msg, "content", None)
                if node in _AGENT_NODES and text:
                    yield _sse("token", text=text)

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
    app = FastAPI(title="EchoMind LangGraph 客服 Web 演示", docs_url=None, redoc_url=None)

    @app.get("/")
    def index():
        """返回聊天主页面。"""
        return FileResponse(_STATIC_DIR / "index.html")

    @app.post("/api/chat")
    async def chat(request: Request):
        """
        入参 JSON：{message: str, thread_id: str}
        以 SSE 流式返回本轮图执行的全部事件（见 _stream_graph 协议）。
        """
        body = await request.json()
        message = (body.get("message") or "").strip()
        thread_id = (body.get("thread_id") or "").strip()
        if not message or not thread_id:
            # 参数缺失也走 SSE error，让前端用同一套通道处理。
            return _sse_response(
                iter([_sse("error", message="缺少 message 或 thread_id。")])
            )
        config = {"configurable": {"thread_id": thread_id}}
        graph_input = {"messages": [HumanMessage(content=message)]}
        return _sse_response(_stream_graph(graph_input, config))

    @app.post("/api/resume")
    async def resume(request: Request):
        """
        转人工后恢复图。入参 JSON：{thread_id: str, seat_reply: str}
        用 Command(resume=seat_reply) 续跑，SSE 把坐席回复(token)与 done(escalated) 吐回。
        """
        body = await request.json()
        thread_id = (body.get("thread_id") or "").strip()
        seat_reply = body.get("seat_reply")
        if not thread_id or seat_reply is None:
            return _sse_response(
                iter([_sse("error", message="缺少 thread_id 或 seat_reply。")])
            )
        config = {"configurable": {"thread_id": thread_id}}
        return _sse_response(_stream_graph(Command(resume=str(seat_reply)), config))

    # 静态资源（app.js / style.css 等）挂在 /static 下。
    # 放在路由注册之后，避免 StaticFiles 抢占根路径。
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    return app


# 模块级单例 app —— uvicorn "langgraph_cs.web.server:app" 与 __main__.py 都用它。
app = build_app()
