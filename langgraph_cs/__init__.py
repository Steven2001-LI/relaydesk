"""
langgraph_cs —— 用 LangGraph 编排的最小客服 Agent 骨架。

架构：intent → rag →(条件路由)→ 专职 Agent（technical/billing/general/escalation），
用 add_conditional_edges 做意图路由 + 低置信度降级，用 interrupt/resume 做 human-in-the-loop，
用 checkpointer 维持多轮记忆。

关键结构：状态用 CSState（TypedDict），意图识别为 LLM 单路分类，
多轮记忆默认走 MemorySaver（零依赖，可切换 SQLite，见 graph.py）。
"""
