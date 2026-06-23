"""
命令行入口：跑通一轮（或多轮）对话。

用法：
    python -m langgraph_cs.main

每次输入一句话，图会执行 intent -> agent，并打印识别到的意图和回复。
因为编译时挂了 checkpointer，同一个 thread_id 下的多轮对话会自动记得上文，
你可以试着先说"我叫小明"，下一句问"我叫什么"，看它是否记得。
"""
import logging

from langchain_core.messages import HumanMessage

from langgraph_cs.graph import build_graph

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> None:
    graph = build_graph()

    # thread_id 标识一次会话。同一个 id = 同一段记忆。
    config = {"configurable": {"thread_id": "demo-session-1"}}

    print("EchoMind (LangGraph 骨架) ʕ•ᴥ•ʔ  输入 quit/退出 结束\n")
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

        intent = result.get("intent")
        confidence = result.get("confidence") or 0.0
        reply = result["messages"][-1].content
        print(f"\n[意图: {intent} ({confidence:.2f})]")
        print(f"EchoMind: {reply}\n")


if __name__ == "__main__":
    main()
