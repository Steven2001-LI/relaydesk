"""
agent_node —— 应答节点。

教学版：根据 intent_node 写入的意图，挑一个对应的 system prompt，然后带着完整对话历史调用 LLM。
对照 EchoMind 的 agents/agent_orchestrator.py：那里有 General/Technical/Billing 多个 Agent 类，
由 Orchestrator 做路由。这里先用"一个节点 + 按意图换 system prompt"的最简形式跑通；
阶段 3 再拆成多个真正的 Agent 节点 + 条件边路由（conditional edges）。

节点契约：
  输入：state（读 intent + messages）
  输出：{"messages": [AIMessage(...)]}  —— add_messages reducer 会把它追加进历史
"""
import logging

from langchain_core.messages import AIMessage, SystemMessage

from langgraph_cs.config import build_llm

logger = logging.getLogger(__name__)

# 意图 -> system prompt 的映射（对照 EchoMind 各 Agent 的 system_prompt）。
_PROMPTS = {
    "technical": "你是技术支持专家。专注故障排查、错误诊断、配置问题，给出清晰的分步解决方案。",
    "billing": "你是账单服务专家。专注账单查询、退款、发票、订阅，保持准确专业。涉及实际退款说明需人工审核。",
    "complaint": "你是客户关系专员。先共情安抚用户情绪，再给出可行的解决方案。",
    "greeting": "你是 EchoMind 智能客服，热情简洁地回应问候并引导用户说明需求。",
}
_DEFAULT_PROMPT = "你是 EchoMind 智能客服。友好、简洁地回答用户问题；超出能力范围时如实说明并建议转人工。"


def agent_node(state) -> dict:
    intent = state.get("intent") or "other"
    system_prompt = _PROMPTS.get(intent, _DEFAULT_PROMPT)

    llm = build_llm(temperature=0.5)

    # 把 system prompt 放在最前，后面接完整对话历史（state["messages"] 已经累积好了）。
    messages = [SystemMessage(content=system_prompt)] + list(state["messages"])
    resp = llm.invoke(messages)

    logger.info("agent 应答完成（intent=%s）", intent)
    return {"messages": [AIMessage(content=resp.content)]}
