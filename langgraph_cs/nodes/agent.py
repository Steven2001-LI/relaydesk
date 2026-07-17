"""
应答节点：technical / billing / general 三个专职 Agent + 失败降级。

所有专职节点共享 _run_agent()（构造消息 + 带 RAG 参考资料 + 调 LLM + 兜底），
各自只传入不同的 system prompt 与工具集。备选方案是"单 agent 节点 + 按 intent
换 system prompt"；拆成独立节点后 billing/technical 才能各挂各的工具集，
路由与降级也由图结构显式表达。单个 Agent 的 LLM 调用失败在节点内 try/except
降级（另一层"低置信度降级到 general"在路由函数里做）。

节点契约（所有专职节点统一）：
  输入：state（读 intent / messages / retrieved_docs）
  输出：{"messages": [AIMessage(...)]}  —— add_messages reducer 会把它追加进历史
"""
import logging

from langchain_core.messages import AIMessage, SystemMessage

from langgraph_cs.config import build_llm
from langgraph_cs.nodes.tools import BILLING_TOOLS, TECHNICAL_TOOLS

logger = logging.getLogger(__name__)

# 意图 -> system prompt 的映射，各专职节点按需取用；
# general_agent 对未命中专用 prompt 的意图用 _DEFAULT_PROMPT 兜底。
_PROMPTS = {
    "technical": (
        "你是技术支持专家。专注故障排查、错误诊断、配置问题，给出清晰的分步解决方案。\n"
        "工具使用规范：涉及用户个人的技术工单、报障记录、服务大盘状态时，必须调用工具查询或创建工单，禁止编造数据；"
        "缺少 user_id 或故障详情时先向用户询问，不要猜测；"
        "知识库资料用于回答政策/流程类问题，工具用于处理用户个人数据类问题，两者可结合。"
    ),
    "billing": (
        "你是账单服务专家。专注账单查询、退款、发票、订阅，保持准确专业。涉及实际退款说明需人工审核。\n"
        "工具使用规范：涉及具体订单、账单、发票、退款进度、退款工单的问题，必须调用工具查询或创建工单，禁止编造数据；"
        "但如果用户明确限制不要查询系统、不要调用工具、不要查看订单/账单/退款状态，或说明只需要政策流程/自己已知道状态，则尊重该限制，只依据知识库回答政策或流程，不调用业务工具；"
        "缺少 user_id、order_id 或 bill_id 等必要标识时先向用户询问，不要猜测；"
        "知识库资料用于回答政策/流程类问题，工具用于处理用户个人数据类问题，两者可结合。"
    ),
    "complaint": "你是客户关系专员。先共情安抚用户情绪，再给出可行的解决方案。",
    "greeting": "你是 RelayDesk 智能客服，热情简洁地回应问候并引导用户说明需求。",
}
_DEFAULT_PROMPT = "你是 RelayDesk 智能客服。友好、简洁地回答用户问题；超出能力范围时如实说明并建议转人工。"

# 当 rag_node 检索到知识库文档时，追加这段指示：优先依据知识库，没有就如实说。
_RAG_INSTRUCTION = (
    "下面是从知识库检索到的参考资料。回答时优先依据这些资料；"
    "若资料中没有相关信息，就如实告知用户你暂时没有查到，不要编造。"
)

# 专职 Agent 调用 LLM 失败时的兜底回复（降级体验，不让图崩）。
_FALLBACK_REPLY = "抱歉，我这边暂时遇到点问题，没能完整处理你的请求。你可以换个说法再问一次，或回复“转人工”由人工坐席接手。"


def _build_rag_context(retrieved_docs) -> str:
    """
    把检索到的条目拼成一段"参考资料"文本；没有则返回空串。

    retrieved_docs 是 rag_node 写的结构化条目列表（list[dict]，见 state.py）：
    每条取 doc["text"] 作为正文拼进上下文（item_id/source/score 不进 prompt，留给可观测层）。
    """
    if not retrieved_docs:
        return ""
    blocks = [f"[资料{i + 1}] {doc['text']}" for i, doc in enumerate(retrieved_docs)]
    return _RAG_INSTRUCTION + "\n\n参考资料：\n" + "\n\n".join(blocks)


def _run_agent(state, system_prompt: str, agent_name: str, tools=None) -> dict:
    """
    所有专职 Agent 节点共用的"调用 LLM 出字"辅助函数。

    职责：
      - 用传入的 system_prompt 作为人设；
      - 带上 rag 检索到的参考资料（_build_rag_context）；
      - 接完整对话历史调 LLM；
      - billing/technical 可按需 bind_tools，让模型先查业务库再回答；
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
        runnable = llm.bind_tools(tools) if tools else llm
        resp = runnable.invoke(messages)
    except Exception as ex:  # noqa: BLE001 节点边界兜底：单个 Agent 失败 -> 降级回复，不让整图崩
        logger.warning("%s 调用 LLM 失败，返回兜底回复：%s", agent_name, ex)
        return {"messages": [AIMessage(content=_FALLBACK_REPLY)]}

    logger.info("%s 应答完成（参考资料 %d 条）",
                agent_name, len(state.get("retrieved_docs") or []))
    return {"messages": [resp if isinstance(resp, AIMessage) else AIMessage(content=resp.content)]}


# ---- 专职 Agent 节点 ----

def technical_agent(state) -> dict:
    """技术支持 Agent：故障排查、错误诊断、配置问题。"""
    return _run_agent(state, _PROMPTS["technical"], "technical_agent", tools=TECHNICAL_TOOLS)


def billing_agent(state) -> dict:
    """账单服务 Agent：账单查询、退款、发票、订阅。"""
    return _run_agent(state, _PROMPTS["billing"], "billing_agent", tools=BILLING_TOOLS)


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
