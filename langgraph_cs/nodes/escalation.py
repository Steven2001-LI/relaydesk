"""
escalation_node —— 转人工节点（human-in-the-loop）。

当意图被识别为 escalation（用户要求人工坐席 / 专职 Agent 无法处理）时，
路由函数把请求送到这里。本节点不调 LLM，而是用 LangGraph 原生的 `interrupt()`
**暂停整张图**，把"请人工坐席输入回复"的提示抛给外层（CLI / 评测）。
外层拿到人工输入后，用 `graph.invoke(Command(resume=<人工输入>))` 恢复，
`interrupt()` 的返回值就是那段人工输入，本节点再把它作为 AIMessage 写回 messages。

另一种实现是检测关键词后仅把 escalated 置位（占位，并未真正阻塞等人工）；
这里用 interrupt 做到了**真正暂停 + 等人工 + 恢复**的闭环。

为什么把 interrupt 放在最前、且不依赖 LLM？
  - 体验上：转人工就该立即停下等人，不必再花一次 LLM 调用。
  - 工程上：interrupt 在前 + 无 LLM，使本节点可在不联网、无 API key 的情况下离线验证
    （图能暂停、能用 Command(resume=...) 恢复、人工输入被写回 messages）。

依赖：编译图时必须挂 checkpointer（我们用 MemorySaver）+ 传 thread_id，
否则 interrupt 无法保存/恢复中断点。

import 说明（langgraph 1.x）：
  interrupt / Command 均来自 langgraph.types。
  invoke 返回结果里若含 "__interrupt__" 键，表示图停在了中断点（外层据此判断要不要等人工）。
"""
import logging

from langchain_core.messages import AIMessage
from langgraph.types import interrupt

from langgraph_cs.nodes.utils import last_user_text

logger = logging.getLogger(__name__)

# 抛给外层的中断提示。外层（CLI）会把它打印给人工坐席看，提示需要人工接管。
_ESCALATION_PROMPT = "已转人工，本轮需要人工坐席处理。请坐席输入要回复用户的内容："


def escalation_node(state) -> dict:
    """
    暂停整张图等人工介入。

    流程：
      1) interrupt(payload) 抛出中断 —— 图在这里停住，invoke 返回里带 "__interrupt__"。
      2) 外层用 Command(resume=<人工回复>) 恢复，interrupt() 返回那段人工回复。
      3) 把人工回复作为 AIMessage 写回 messages，并标记 escalated=True。
    """
    user_text = last_user_text(state)
    logger.info("触发转人工（human-in-the-loop），暂停等待坐席输入。用户原话：%s", user_text)

    # interrupt 的入参会原样出现在外层拿到的 Interrupt.value 里，方便 CLI 展示上下文。
    # 第一次执行到这里会"抛出"中断暂停；resume 后再执行到这里则直接返回人工输入的值。
    human_reply = interrupt(
        {
            "kind": "seat",
            "prompt": _ESCALATION_PROMPT,
            "user_message": user_text,
        }
    )

    logger.info("收到人工坐席回复，恢复图执行。")
    return {
        "messages": [AIMessage(content=str(human_reply))],
        "escalated": True,
    }
