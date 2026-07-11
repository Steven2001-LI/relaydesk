"""
组装并编译 LangGraph 图。

图的形态（多 Agent 条件路由 + human-in-the-loop）：

    START -> intent -> rag -(条件路由 route_by_intent)-> 专职 Agent 节点 -> END
                                               └─ 有 tool_calls -> tools -> 回到发起 Agent

  rag 之后不再固定直连一个 agent，而是用 add_conditional_edges 按意图分流：
      technical  -> technical_agent
      billing    -> billing_agent
      escalation -> escalation        （interrupt 暂停等人工）
      其余/未知/低置信 -> general_agent  （general 也是降级落点）
  四个专职节点跑完都连到 END。

关键概念：
  - add_node：把函数注册为节点
  - add_edge：固定直连（START->intent->rag，以及各专职节点->END）
  - add_conditional_edges(源节点, 路由函数, {返回值: 目标节点}):
        用它实现意图路由 + 低置信度降级（替代 rag->agent 直连）。
        路由函数（route_by_intent）返回下一个节点名，必须落在映射的 value 集合里。
  - compile(checkpointer=...)：挂"检查点存储"，让图能按 thread_id 记住每轮状态。
        human-in-the-loop（escalation 节点 interrupt/resume）也**依赖**它：
        没有 checkpointer 就无法保存中断点、无法 Command(resume=...) 恢复。

checkpointer 由 make_checkpointer() 工厂按环境变量 CS_CHECKPOINT=memory|sqlite 选择内存版
或 SQLite 持久版（落本地文件，进程重启后同一 thread_id 仍记得上文）；build_graph() 支持
外部注入自定义实例（供评测/测试隔离），不传则走工厂默认。
"""
import os
import sqlite3
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from langgraph_cs.nodes.agent import billing_agent, general_agent, technical_agent
from langgraph_cs.nodes.escalation import escalation_node
from langgraph_cs.nodes.intent import intent_node
from langgraph_cs.nodes.rag import rag_node
from langgraph_cs.nodes.router import route_by_intent
from langgraph_cs.nodes.tools import ALL_TOOLS
from langgraph_cs.state import CSState

# SQLite 检查点文件落在 langgraph_cs/data/checkpoints.sqlite（与 chroma 向量库同目录，
# 都被 .gitignore 忽略，属于本地运行时产物，不入库）。
_DATA_DIR = Path(__file__).parent / "data"
DEFAULT_SQLITE_PATH = _DATA_DIR / "checkpoints.sqlite"


def make_sqlite_checkpointer(db_path: Path = DEFAULT_SQLITE_PATH) -> SqliteSaver:
    """
    构造一个**长驻可用**的 SqliteSaver（SQLite 持久化检查点）。

    连接生命周期是这里的关键点（也是最容易踩坑的地方）：
      - 官方便捷入口 `SqliteSaver.from_conn_string(path)` 是一个**上下文管理器**
        （`with SqliteSaver.from_conn_string(...) as saver: ...`），离开 with 块连接就关了。
        它适合"用完即走"的脚本，但**不适合 CLI 长驻进程**——图编译后还要在
        后续多轮 invoke 里反复读写检查点，连接一旦关闭就用不了了。
      - 因此长驻场景的正确写法是**自己建并持有 sqlite3 连接**，再交给 SqliteSaver：
        sqlite3.connect(path, check_same_thread=False) —— check_same_thread=False
        是因为 LangGraph 可能在不同线程里访问同一个 saver；连接由调用方（main.py）
        持有到进程结束，不在这里随手关闭。
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    saver = SqliteSaver(conn)
    # setup() 建表（checkpoints / writes 等）。重复调用幂等，第一次跑会自动建好库表。
    saver.setup()
    return saver


def make_checkpointer():
    """
    按环境变量 CS_CHECKPOINT 选择检查点存储（工厂方法）。

      - CS_CHECKPOINT=memory（默认）：MemorySaver，进程内存版，零依赖、重启即丢。
        与不设该变量时的默认行为一致。
      - CS_CHECKPOINT=sqlite：SqliteSaver，落 data/checkpoints.sqlite，进程重启后
        同一 thread_id 仍记得上文（见 scripts/verify_persistence.py 的跨进程验证）。

    两种 saver 接口完全一样（都实现 BaseCheckpointSaver），编译/路由/HITL 代码无需区分。
    """
    backend = os.getenv("CS_CHECKPOINT", "memory").strip().lower()
    if backend == "sqlite":
        return make_sqlite_checkpointer()
    return MemorySaver()


def route_after_agent(state) -> str:
    """
    专职 Agent 回复后决定是否执行工具。

    billing/technical 绑定工具后，模型可能先返回一条带 tool_calls 的 AIMessage；
    此时进入共享 tools 节点执行工具。若没有 tool_calls，则本轮直接结束。
    极端情况下模型反复调用工具会由 LangGraph 的 recursion_limit 兜底阻断。
    """
    messages = state.get("messages") or []
    last = messages[-1] if messages else None
    if getattr(last, "tool_calls", None):
        return "tools"
    return END


def build_graph(checkpointer=None):
    builder = StateGraph(CSState)

    # 前两节点不变：意图识别 -> RAG 检索。
    builder.add_node("intent", intent_node)
    builder.add_node("rag", rag_node)

    # 四个专职 Agent 节点。
    builder.add_node("technical_agent", technical_agent)
    builder.add_node("billing_agent", billing_agent)
    builder.add_node("general_agent", general_agent)
    builder.add_node("escalation", escalation_node)
    # ToolNode 使用默认 handle_tool_errors；工具内部也返回 JSON error。
    # 若模型极端情况下反复请求工具，循环上限交给 LangGraph recursion_limit 兜底。
    builder.add_node("tools", ToolNode(ALL_TOOLS))

    builder.add_edge(START, "intent")
    builder.add_edge("intent", "rag")

    # 核心改动：rag 之后按意图条件路由（含低置信度降级到 general_agent）。
    # 第三个参数是"路由函数返回值 -> 目标节点"的映射；这里 key 和 value 同名，
    # 直接表达 route_by_intent 可能返回的四个节点。
    builder.add_conditional_edges(
        "rag",
        route_by_intent,
        {
            "technical_agent": "technical_agent",
            "billing_agent": "billing_agent",
            "general_agent": "general_agent",
            "escalation": "escalation",
        },
    )

    # billing/technical 可能先产生 tool_calls：有工具调用 -> tools；否则 -> END。
    builder.add_conditional_edges(
        "technical_agent",
        route_after_agent,
        {
            "tools": "tools",
            END: END,
        },
    )
    builder.add_conditional_edges(
        "billing_agent",
        route_after_agent,
        {
            "tools": "tools",
            END: END,
        },
    )

    # tools 执行完后回到"本轮 intent 对应的发起 Agent"。映射复用 rag 出边的完整四目标，
    # 避免条件边映射不全导致运行期 KeyError；实际工具路径只会回 billing/technical。
    builder.add_conditional_edges(
        "tools",
        route_by_intent,
        {
            "technical_agent": "technical_agent",
            "billing_agent": "billing_agent",
            "general_agent": "general_agent",
            "escalation": "escalation",
        },
    )

    # general/escalation 不挂工具，行为保持原样，跑完收束到 END。
    builder.add_edge("general_agent", END)
    builder.add_edge("escalation", END)

    # 检查点存储：不传则走工厂 make_checkpointer()（按 CS_CHECKPOINT 选 memory|sqlite）。
    # 传了就用调用方给的（评测/测试可注入一个临时 SqliteSaver，不影响默认行为）。
    # human-in-the-loop 的 interrupt/resume 依赖它保存中断点。
    if checkpointer is None:
        checkpointer = make_checkpointer()
    return builder.compile(checkpointer=checkpointer)
