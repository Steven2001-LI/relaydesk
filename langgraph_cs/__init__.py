"""
langgraph_cs —— 用 LangGraph 重写的最小客服 Agent 骨架。

学习目标（阶段 2）：
  跑通第一张能用的图：intent_node → agent_node，并用 checkpointer 维持多轮记忆。

对照 EchoMind（手写 anthropic SDK）的同名概念：
  - EchoMind 的 Request/Orchestrator 一堆 dataclass  ->  这里的 CSState（TypedDict）
  - EchoMind 的 intent_recognizer.py 三路融合          ->  这里 nodes/intent.py（教学版单路，先跑通）
  - EchoMind 的 conversation_memory.py + redis         ->  这里 MemorySaver（零依赖，后续可换 Redis/SQLite）
"""
