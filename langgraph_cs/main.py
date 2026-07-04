"""
命令行入口：跑通一轮（或多轮）对话，并支持 human-in-the-loop 的暂停/恢复。

用法：
    python -m langgraph_cs.main

每次输入一句话，图会执行 intent -> rag -(条件路由)-> 专职 Agent，并打印识别到的意图和回复。
因为编译时挂了 checkpointer，同一个 thread_id 下的多轮对话会自动记得上文，
你可以试着先说"我叫小明"，下一句问"我叫什么"，看它是否记得。

阶段 4 新增：持久化后端可切换（环境变量 CS_CHECKPOINT）。
    CS_CHECKPOINT=memory（默认）：内存版，进程退出即丢。
    CS_CHECKPOINT=sqlite     ：落 data/checkpoints.sqlite，**进程重启后仍记得上文**。
        体验跨进程记忆：先 `CS_CHECKPOINT=sqlite python -m langgraph_cs.main` 说"我叫小明"，
        退出后再起一次同样命令、用同一句问"我叫什么"，它仍记得 —— 状态来自 SQLite 文件。
具体选哪个由 build_graph()->make_checkpointer() 按 CS_CHECKPOINT 决定，本文件不必区分。

阶段 3 新增：human-in-the-loop。
当意图是 escalation（用户要求转人工）时，图会停在 escalation 节点的 interrupt() 处，
invoke 的返回结果里会带 "__interrupt__"。这里检测到后，提示并读取"人工坐席"的输入，
再用 graph.invoke(Command(resume=<人工输入>)) 恢复，图把这段人工回复作为最终回复继续到 END。
试一句"我要转人工"即可体验：程序会要求你以坐席身份输入一句回复。
"""
import logging
import os

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from langgraph_cs.config import require_api_key
from langgraph_cs.graph import build_graph

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def _is_interrupted(result) -> bool:
    """invoke 返回里带 "__interrupt__" 键，表示图停在了 interrupt（等人工）。"""
    return isinstance(result, dict) and "__interrupt__" in result


def _interrupt_prompt(result) -> str:
    """从中断结果里取出要展示给人工坐席的提示文本（兜底给个默认提示）。"""
    interrupts = result.get("__interrupt__") or []
    if interrupts:
        value = interrupts[0].value
        if isinstance(value, dict):
            return value.get("prompt", "需要人工处理，请输入回复：")
    return "需要人工处理，请输入回复："


def _print_reply(result) -> None:
    """打印一轮的意图与最终回复。"""
    intent = result.get("intent")
    confidence = result.get("confidence") or 0.0
    reply = result["messages"][-1].content
    tag = "（人工坐席）" if result.get("escalated") else ""
    print(f"\n[意图: {intent} ({confidence:.2f})]")
    print(f"RelayDesk{tag}: {reply}\n")


def main() -> None:
    require_api_key()

    # build_graph() 内部按 CS_CHECKPOINT 选 saver（memory|sqlite），这里只读出来给用户提示。
    graph = build_graph()
    backend = os.getenv("CS_CHECKPOINT", "memory").strip().lower()

    # thread_id 标识一次会话。同一个 id = 同一段记忆，也是 interrupt/resume 定位中断点的依据。
    config = {"configurable": {"thread_id": "demo-session-1"}}

    print(f"RelayDesk (LangGraph 骨架) ʕ•ᴥ•ʔ  [持久化后端: {backend}]  输入 quit/退出 结束")
    print("提示：说\"转人工\"可体验 human-in-the-loop（图暂停 -> 你以坐席身份输入 -> 恢复）\n")
    while True:
        try:
            text = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见 ʕ•ᴥ•ʔ")
            break
        if not text or text.lower() in ("quit", "exit", "退出"):
            print("再见 ʕ•ᴥ•ʔ")
            break

        # invoke：把这一句作为新的 HumanMessage 喂进图。
        # 注意：我们只传"本轮新消息"，历史由 checkpointer + add_messages 自动补齐。
        result = graph.invoke({"messages": [HumanMessage(content=text)]}, config=config)

        # human-in-the-loop：若图停在 interrupt，提示并读取人工坐席输入，再 resume 继续。
        # 用 while 兜住"理论上可能连续多次中断"的情况（本图只会中断一次）。
        while _is_interrupted(result):
            print(f"\n>>> {_interrupt_prompt(result)}")
            try:
                human_reply = input("坐席: ").strip()
            except (EOFError, KeyboardInterrupt):
                human_reply = "（坐席未回复）"
            # Command(resume=...) 恢复图：resume 的值会成为 escalation_node 里 interrupt() 的返回值。
            result = graph.invoke(Command(resume=human_reply), config=config)

        _print_reply(result)


if __name__ == "__main__":
    main()
