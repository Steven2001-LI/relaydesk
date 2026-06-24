"""
langgraph_cs.web —— 给 LangGraph 客服图加的"可演示 Web 适配层"。

定位（很重要）：本包**只做适配，不改图核心**。
  - 复用 langgraph_cs.graph.build_graph()（与 CLI / 评测同一张图、同一套节点/state）；
  - 把图的执行包成 HTTP：一个 FastAPI 应用（server.py）+ 一个原生 HTML/JS 单页（static/）；
  - 不引入 Node/构建链，前端是手写的单文件页面，开箱即跑。

为什么要这一层？
  阶段 1~4 的能力（意图识别 / 多 Agent 路由 / RAG 引用 / human-in-the-loop 转人工）
  之前只能在 CLI 里体验。面试演示时，浏览器里"边打字边出字 + 一眼看到它怎么决策 +
  转人工时界面真的停下来等坐席输入"远比 CLI 直观。这一层就是把图的内部信号
  （intent / retrieved_docs / 路由到的节点 / __interrupt__）通过 SSE 喂给前端可视化。

入口：python -m langgraph_cs.web （见 __main__.py，用 uvicorn 起 server:app）。
"""

from langgraph_cs.web.server import app, build_app

__all__ = ["app", "build_app"]
