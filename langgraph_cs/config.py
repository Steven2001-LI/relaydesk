"""
配置与 LLM 客户端构造。

DeepSeek 兼容 OpenAI 协议，所以我们用 langchain-openai 的 ChatOpenAI，
只要把 base_url 指向 DeepSeek 即可——这是把"OpenAI 生态的轮子"复用到 DeepSeek 上的标准做法。
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

# 显式加载本模块同目录下的 .env（langgraph_cs/.env）。
# 不能用裸 load_dotenv()：从仓库根运行时它会去找根目录的 .env，读不到这里的 key。
load_dotenv(Path(__file__).parent / ".env")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# ── 阶段 4：LangSmith 可观测/评测相关环境变量 ──────────────────────────────
# 只是从 .env 读出来（load_dotenv 已加载），不在这里做任何联网。
# 关键点：LANGSMITH_TRACING / LANGSMITH_API_KEY / LANGSMITH_PROJECT 这三个变量
# 一旦设进进程环境，**LangGraph/LangChain 会自动**把每次 invoke 的节点级 trace 上传到
# LangSmith 云端，无需改任何图代码（这就是"接线即生效"的可观测性）。
# 缺 key 时这些为 None，本地评测（answer_eval）不受影响、照样能跑。
LANGSMITH_TRACING = os.getenv("LANGSMITH_TRACING")
LANGSMITH_API_KEY = os.getenv("LANGSMITH_API_KEY")
LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT", "echomind-langgraph")


def build_llm(temperature: float = 0.3) -> ChatOpenAI:
    """
    构造一个指向 DeepSeek 的 LLM 客户端。

    temperature：意图识别用低温度（更确定），自由对话可调高一点。
    """
    if not DEEPSEEK_API_KEY:
        raise RuntimeError(
            "缺少 DEEPSEEK_API_KEY。请复制 .env.example 为 .env 并填入你的 DeepSeek key。"
        )
    return ChatOpenAI(
        model=DEEPSEEK_MODEL,
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=temperature,
        timeout=30,
    )
