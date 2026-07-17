"""
langgraph_cs —— 用 LangGraph 搭建的多 Agent 客服系统。

核心链路：intent → rag →(条件路由)→ 专职 Agent（technical/billing/general/escalation），
用 add_conditional_edges 做意图路由 + 低置信度降级，用 interrupt/resume 做
human-in-the-loop，用 checkpointer（CS_CHECKPOINT 选 memory/sqlite）维持多轮记忆。

关键设计取舍：状态用 CSState（TypedDict + add_messages reducer）承载；意图识别为
LLM 单路分类（多路融合是既定扩展点）；会话记忆交给 checkpointer，生产可换
Redis/Postgres 等后端。
"""
