"""
web 模块入口：`python -m langgraph_cs.web`。

一条命令起服务（默认 127.0.0.1:8000），浏览器打开 http://127.0.0.1:8000 即可演示。
可用环境变量覆盖监听地址/端口：
    CS_WEB_HOST（默认 127.0.0.1）
    CS_WEB_PORT（默认 8000）

为什么单独放 __main__.py 而不写在 server.py 的 `if __name__ == "__main__"`？
  python -m langgraph_cs.web 会执行包内的 __main__.py（标准约定），这样入口最直观；
  server.py 保持"只定义 app"的纯净，便于被 uvicorn 字符串导入和被测试 import。
"""
import logging
import os

import uvicorn

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> None:
    host = os.getenv("CS_WEB_HOST", "127.0.0.1")
    port = int(os.getenv("CS_WEB_PORT", "8000"))
    print(f"EchoMind 客服 Web 演示 ʕ•ᴥ•ʔ  ->  http://{host}:{port}")
    print("提示：说\"转人工\"可体验 human-in-the-loop（界面暂停 -> 你以坐席身份回复 -> 恢复）")
    # 用字符串路径 "langgraph_cs.web.server:app" 而非直接传 app 对象，
    # 这样 uvicorn 能正确支持（未来如需）reload；这里 reload=False，演示足够。
    uvicorn.run("langgraph_cs.web.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
