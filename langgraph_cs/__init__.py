"""
langgraph_cs —— 用 LangGraph 重写的最小客服 Agent 骨架。

学习目标（阶段 3）：
  跑通多 Agent 编排：intent → rag →(条件路由)→ 专职 Agent（technical/billing/general/escalation），
  用 add_conditional_edges 做意图路由 + 低置信度降级，用 interrupt/resume 做 human-in-the-loop，
  并用 checkpointer 维持多轮记忆。

对照旧版手写客服项目的同名概念：
  - 旧版 Request/Orchestrator 一堆 dataclass  ->  这里的 CSState（TypedDict）
  - 旧版 intent_recognizer.py 三路融合         ->  这里 nodes/intent.py（教学版单路，先跑通）
  - 旧版 conversation_memory.py + redis        ->  这里 MemorySaver（零依赖，后续可换 Redis/SQLite）
"""
