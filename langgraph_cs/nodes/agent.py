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

# 当 rag_node 检索到知识库文档时，追加这段指示：优先依据知识库，没有就如实说。
_RAG_INSTRUCTION = (
    "下面是从知识库检索到的参考资料。回答时优先依据这些资料；"
    "若资料中没有相关信息，就如实告知用户你暂时没有查到，不要编造。"
)


def _build_rag_context(retrieved_docs) -> str:
    """把检索到的文档拼成一段"参考资料"文本；没有则返回空串。"""
    if not retrieved_docs:
        return ""
    blocks = [f"[资料{i + 1}] {doc}" for i, doc in enumerate(retrieved_docs)]
    return _RAG_INSTRUCTION + "\n\n参考资料：\n" + "\n\n".join(blocks)


def agent_node(state) -> dict:
    intent = state.get("intent") or "other"
    system_prompt = _PROMPTS.get(intent, _DEFAULT_PROMPT)

    llm = build_llm(temperature=0.5)

    # 把 system prompt 放在最前。若 rag_node 检索到了文档，再追加一条
    # SystemMessage 作为"参考资料"背景，指示模型优先依据知识库作答。
    messages = [SystemMessage(content=system_prompt)]
    rag_context = _build_rag_context(state.get("retrieved_docs"))
    if rag_context:
        messages.append(SystemMessage(content=rag_context))
    # 后面接完整对话历史（state["messages"] 已经累积好了）。
    messages += list(state["messages"])
    resp = llm.invoke(messages)

    logger.info("agent 应答完成（intent=%s，参考资料 %d 条）",
                intent, len(state.get("retrieved_docs") or []))
    return {"messages": [AIMessage(content=resp.content)]}
