"""
route_by_intent —— 意图路由函数（阶段 3：conditional edges 的核心）。

graph.py 用 `add_conditional_edges("rag", route_by_intent, {...})` 把 rag 之后的流向
交给这个纯函数决定：它读 state 里的 intent / confidence，返回**下一个节点的名字**。

对照旧版手写 agent_orchestrator.py 三层路由：
  1) 意图路由：technical -> technical_agent，billing -> billing_agent，escalation -> escalation。
  2) 降级路由（本函数实现的关键点）：confidence 低于阈值时，不管什么意图一律落到 general_agent
     —— 低置信不该交给专职 Agent 自作主张，先用通用 Agent 稳妥应答。
     （旧版的"专职 Agent 运行时失败 -> 降级 general"那层，我们放在各专职节点内部 try/except。）
  3) 升级路由：escalation 意图 -> escalation 节点（human-in-the-loop）。
  本阶段暂不做性能路由（同类多实例按 routing_score 选优），留作扩展点。

把它写成不依赖 LLM、无副作用的纯函数，好处是可以离线穷举各种 intent/confidence 组合做断言。
"""
import logging

logger = logging.getLogger(__name__)

# 低于这个置信度就降级到 general_agent（教学阈值，对照旧版低置信度降级）。
CONFIDENCE_THRESHOLD = 0.5

# 意图 -> 专职节点 的映射。未列出的意图（greeting/query/complaint/request/other 等）
# 统一走 general_agent。
_INTENT_TO_NODE = {
    "technical": "technical_agent",
    "billing": "billing_agent",
    "escalation": "escalation",
}

# 路由可能返回的所有目标节点名（graph.py 注册条件边时复用，保证一致）。
ROUTE_TARGETS = ["technical_agent", "billing_agent", "general_agent", "escalation"]


def route_by_intent(state) -> str:
    """根据 intent + confidence 决定 rag 之后走哪个节点；返回节点名字符串。"""
    intent = state.get("intent") or "other"
    confidence = state.get("confidence")
    if confidence is None:
        confidence = 0.0

    # 降级路由优先级最高：低置信度时，无论什么意图都走 general_agent。
    # 注意：escalation 是用户显式要求转人工，属于"宁可错转也不漏转"，不受置信度门槛影响。
    if intent != "escalation" and confidence < CONFIDENCE_THRESHOLD:
        logger.info("路由：置信度 %.2f < %.2f，降级到 general_agent（原意图 %s）",
                    confidence, CONFIDENCE_THRESHOLD, intent)
        return "general_agent"

    target = _INTENT_TO_NODE.get(intent, "general_agent")
    logger.info("路由：intent=%s (%.2f) -> %s", intent, confidence, target)
    return target
