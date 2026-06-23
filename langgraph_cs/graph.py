"""
组装并编译 LangGraph 图。

图的形态（阶段 3：多 Agent 条件路由 + human-in-the-loop）：

    START -> intent -> rag -(条件路由 route_by_intent)-> 专职 Agent 节点 -> END

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
        阶段 3 用它替代阶段 1/2 的 rag->agent 直连，实现意图路由 + 低置信度降级。
        路由函数（route_by_intent）返回下一个节点名，必须落在映射的 value 集合里。
        —— 这一整套就对应 EchoMind agent_orchestrator.py 手写的"意图路由 + 降级路由"。
  - compile(checkpointer=...)：挂"检查点存储"，让图能按 thread_id 记住每轮状态。
        阶段 3 的 human-in-the-loop（escalation 节点 interrupt/resume）也**依赖**它：
        没有 checkpointer 就无法保存中断点、无法 Command(resume=...) 恢复。
"""
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from langgraph_cs.nodes.agent import billing_agent, general_agent, technical_agent
from langgraph_cs.nodes.escalation import escalation_node
from langgraph_cs.nodes.intent import intent_node
from langgraph_cs.nodes.rag import rag_node
from langgraph_cs.nodes.router import route_by_intent
from langgraph_cs.state import CSState


def build_graph():
    builder = StateGraph(CSState)

    # 前两节点不变：意图识别 -> RAG 检索。
    builder.add_node("intent", intent_node)
    builder.add_node("rag", rag_node)

    # 阶段 3：四个专职 Agent 节点（对照 EchoMind 的多 Agent）。
    builder.add_node("technical_agent", technical_agent)
    builder.add_node("billing_agent", billing_agent)
    builder.add_node("general_agent", general_agent)
    builder.add_node("escalation", escalation_node)

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

    # 四个专职节点跑完都收束到 END。
    builder.add_edge("technical_agent", END)
    builder.add_edge("billing_agent", END)
    builder.add_edge("general_agent", END)
    builder.add_edge("escalation", END)

    # MemorySaver：进程内存版检查点。零依赖、重启即丢。
    # 后续阶段可换成 SqliteSaver / RedisSaver 做持久化，接口完全一样。
    # human-in-the-loop 的 interrupt/resume 依赖它保存中断点。
    checkpointer = MemorySaver()
    return builder.compile(checkpointer=checkpointer)
