"""
节点共用的小工具。

为什么单独抽一个文件？
  intent_node 和 rag_node 都需要"从 state 里取最后一条用户消息"这同一段逻辑。
  按 code-reuse 指南：同一段逻辑出现在 2 个以上节点，就抽成共享工具，
  避免复制粘贴导致两边行为悄悄分叉（比如以后改了消息类型判断只改了一处）。
"""
from langchain_core.messages import HumanMessage


def last_user_text(state) -> str:
    """从 state.messages 里取出最后一条用户消息的文本；没有则返回空串。"""
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            return msg.content
    return ""
