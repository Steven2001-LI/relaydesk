"""
图的"状态"定义 —— LangGraph 的核心概念。

State 是在各个节点之间流动、被不断更新的一个字典。
每个节点接收当前 state，返回"要更新哪些字段"的局部字典，LangGraph 负责合并。

关键点：messages 字段用了 add_messages 这个 reducer。
  - 普通字段（如 intent）：节点返回新值 -> 直接覆盖
  - messages 字段：节点返回新消息 -> "追加"到已有列表，而不是覆盖
这就是为什么多轮对话历史能自动累积，而不用我们手写 append。
"""
from typing import Annotated, Optional, TypedDict

from langgraph.graph.message import add_messages


class CSState(TypedDict):
    # 对话消息列表（HumanMessage / AIMessage / SystemMessage）。
    # add_messages reducer 让每个节点返回的新消息自动追加。
    messages: Annotated[list, add_messages]

    # 意图识别结果（intent_node 写入，agent_node 读取用来选 system prompt）。
    intent: Optional[str]

    # 意图置信度（0~1）。教学版先放着，阶段 3 做"低置信度转人工"时会用到。
    confidence: Optional[float]
