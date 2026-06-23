"""
应答节点（阶段 3：多 Agent 条件路由 + 失败降级）。

阶段 1/2 是"一个 agent_node + 按 intent 换 system prompt"。
阶段 3 拆成多个**专职 Agent 节点**——technical / billing / general——再由 graph.py 用
`add_conditional_edges` 按意图路由到对应节点。对照 EchoMind 的 agents/agent_orchestrator.py：
那里有 GeneralAgent / TechnicalAgent / BillingAgent 多个 Agent 类 + Orchestrator 做路由/降级；
这里用 LangGraph 原生的"多节点 + 条件边"表达同一套思路。

三个教学点：
  1) 复用：所有专职节点共享 _run_agent()（构造消息 + 调 LLM + 兜底），
     只是各自传入不同的 system prompt，避免复制粘贴 agent_node 的逻辑（见 code-reuse 指南）。
  2) RAG 上下文：每个专职节点都保留 _build_rag_context 行为，带上 rag 检索到的文档。
  3) 降级：节点内部对 LLM 调用做 try/except，单个 Agent 报错不让整图崩，返回兜底消息。
     （另一层"低置信度降级到 general"在 graph.py 的路由函数里做。）

节点契约（所有专职节点统一）：
  输入：state（读 intent / messages / retrieved_docs）
  输出：{"messages": [AIMessage(...)]}  —— add_messages reducer 会把它追加进历史
"""
import logging

from langchain_core.messages import AIMessage, SystemMessage

from langgraph_cs.config import build_llm

logger = logging.getLogger(__name__)

# 意图 -> system prompt 的映射（对照 EchoMind 各 Agent 的 system_prompt）。
# 这些 prompt 沿用阶段 1/2 的写法，现在被各专职节点按需取用。
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

# 专职 Agent 调用 LLM 失败时的兜底回复（降级体验，不让图崩）。
_FALLBACK_REPLY = "抱歉，我这边暂时遇到点问题，没能完整处理你的请求。你可以换个说法再问一次，或回复“转人工”由人工坐席接手。"


def _build_rag_context(retrieved_docs) -> str:
    """把检索到的文档拼成一段"参考资料"文本；没有则返回空串。"""
    if not retrieved_docs:
        return ""
    blocks = [f"[资料{i + 1}] {doc}" for i, doc in enumerate(retrieved_docs)]
    return _RAG_INSTRUCTION + "\n\n参考资料：\n" + "\n\n".join(blocks)


def _run_agent(state, system_prompt: str, agent_name: str) -> dict:
    """
    所有专职 Agent 节点共用的"调用 LLM 出字"辅助函数。

    职责：
      - 用传入的 system_prompt 作为人设；
      - 带上 rag 检索到的参考资料（_build_rag_context）；
      - 接完整对话历史调 LLM；
      - try/except 兜底：LLM 报错时返回降级消息，不抛出（保证整图不崩）。

    这样三个专职节点只需各传一个 prompt，逻辑不复制。
    """
    llm = build_llm(temperature=0.5)

    # 把 system prompt 放在最前。若 rag_node 检索到了文档，再追加一条
    # SystemMessage 作为"参考资料"背景，指示模型优先依据知识库作答。
    messages = [SystemMessage(content=system_prompt)]
    rag_context = _build_rag_context(state.get("retrieved_docs"))
    if rag_context:
        messages.append(SystemMessage(content=rag_context))
    # 后面接完整对话历史（state["messages"] 已经累积好了）。
    messages += list(state["messages"])

    try:
        resp = llm.invoke(messages)
    except Exception as ex:  # noqa: BLE001 教学版统一兜底：单个 Agent 失败 -> 降级回复，不崩图
        logger.warning("%s 调用 LLM 失败，返回兜底回复：%s", agent_name, ex)
        return {"messages": [AIMessage(content=_FALLBACK_REPLY)]}

    logger.info("%s 应答完成（参考资料 %d 条）",
                agent_name, len(state.get("retrieved_docs") or []))
    return {"messages": [AIMessage(content=resp.content)]}


# ---- 专职 Agent 节点（对照 EchoMind 的 TechnicalAgent / BillingAgent / GeneralAgent）----

def technical_agent(state) -> dict:
    """技术支持 Agent：故障排查、错误诊断、配置问题。"""
    return _run_agent(state, _PROMPTS["technical"], "technical_agent")


def billing_agent(state) -> dict:
    """账单服务 Agent：账单查询、退款、发票、订阅。"""
    return _run_agent(state, _PROMPTS["billing"], "billing_agent")


def general_agent(state) -> dict:
    """
    通用 Agent，也是"降级落点"。

    路由函数把 greeting / query / complaint / request / other / 未知意图，
    以及"低置信度"的任何意图，统统送到这里。所以这里按 intent 灵活取 prompt：
    命中专用 prompt（如 complaint/greeting）就用，否则用通用兜底 prompt。
    """
    intent = state.get("intent") or "other"
    system_prompt = _PROMPTS.get(intent, _DEFAULT_PROMPT)
    return _run_agent(state, system_prompt, "general_agent")
