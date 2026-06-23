"""
组装并编译 LangGraph 图。

图的形态（阶段 2 最简版）：
    START -> intent_node -> agent_node -> END

关键概念：
  - add_node：把函数注册为节点
  - add_edge：连接节点（这里是固定直连；阶段 3 会换成 add_conditional_edges 做意图路由）
  - compile(checkpointer=...)：编译时挂上"检查点存储"，让图能按 thread_id 记住每轮对话状态
        —— 这一行就替代了 EchoMind 里手写的 redis 会话管理。
"""
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from langgraph_cs.nodes.agent import agent_node
from langgraph_cs.nodes.intent import intent_node
from langgraph_cs.state import CSState


def build_graph():
    builder = StateGraph(CSState)

    builder.add_node("intent", intent_node)
    builder.add_node("agent", agent_node)

    builder.add_edge(START, "intent")
    builder.add_edge("intent", "agent")
    builder.add_edge("agent", END)

    # MemorySaver：进程内存版检查点。零依赖、重启即丢。
    # 后续阶段可换成 SqliteSaver / RedisSaver 做持久化，接口完全一样。
    checkpointer = MemorySaver()
    return builder.compile(checkpointer=checkpointer)
