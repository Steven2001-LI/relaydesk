"""
embeddings —— 硅基流动（SiliconFlow）embedding 封装。

为什么能直接用 langchain_openai.OpenAIEmbeddings？
  硅基流动的 /v1/embeddings 与 OpenAI 协议兼容（请求/响应结构一致），
  所以只要把 base_url 指向硅基流动、填上自己的 key，就能复用 OpenAI 生态的轮子。
  这和 config.py 里把 ChatOpenAI 指向 DeepSeek 是同一个套路。

模型 BAAI/bge-large-zh-v1.5 的关键约束（已核实自官方 OpenAPI，见 research 文件）：
  - 输出向量维度固定 1024（bge-large 架构固有，不可调）
  - 单条 input 上限 512 token —— 所以长文档落库前必须先切块（见 store.py）
  - 批量数组上限 32 —— 用 chunk_size=32 让客户端自动按 32 一批，避免 400
  - 不支持 dimensions 参数（仅 Qwen3 系列支持）—— 千万别传
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings

# rag/ 在 langgraph_cs/ 的子目录里，所以要往上跳一级才是 langgraph_cs/.env。
# 不能用裸 load_dotenv()：从仓库根运行时它会找错目录、读不到这里的 key。
load_dotenv(Path(__file__).parent.parent / ".env")

SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY")
SILICONFLOW_BASE_URL = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
SILICONFLOW_EMBEDDING_MODEL = os.getenv(
    "SILICONFLOW_EMBEDDING_MODEL", "BAAI/bge-large-zh-v1.5"
)

# bge-large-zh-v1.5 输出维度（落库建表/校验时可参考）。
EMBEDDING_DIM = 1024
# 硅基流动 embedding 批量上限，对齐官方 maxItems:32，避免一次塞太多触发 400。
EMBEDDING_BATCH_SIZE = 32


def build_embeddings() -> OpenAIEmbeddings:
    """
    构造一个指向硅基流动的 embedding 客户端。

    返回的对象有 embed_query(text)->List[float] 和
    embed_documents(texts)->List[List[float]] 两个方法，Chroma 会直接拿来用。
    """
    if not SILICONFLOW_API_KEY:
        raise RuntimeError(
            "缺少 SILICONFLOW_API_KEY。请在 langgraph_cs/.env 里填入硅基流动的 key"
            "（注册并实名后于 https://cloud.siliconflow.cn/account/ak 创建）。"
        )
    return OpenAIEmbeddings(
        model=SILICONFLOW_EMBEDDING_MODEL,
        api_key=SILICONFLOW_API_KEY,
        base_url=SILICONFLOW_BASE_URL,
        # 对齐硅基流动批量上限 32，客户端会自动分批，避免超限报错。
        chunk_size=EMBEDDING_BATCH_SIZE,
        # bge 系列不支持 dimensions 参数，这里不传（保持默认 1024 维）。
        # 关掉 OpenAI 专属的 tiktoken token 估算逻辑：bge 不是 OpenAI 模型，
        # 按 OpenAI 规则估 token 可能导致异常截断，交给服务端按 512 token 处理即可。
        check_embedding_ctx_length=False,
    )
