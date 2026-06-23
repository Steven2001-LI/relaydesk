"""
store —— 向量库（Chroma）的灌库与检索器构造。

整条链路：原始 FAQ 文件 → 按条目切块(每个 ### 条目 = 一个 chunk) → embedding
         → 写入本地持久化 Chroma → build_retriever() 暴露一个 top-k 检索器供 rag_node 使用。

为什么按"条目"切，而不是用 RecursiveCharacterTextSplitter 粗切？
  旧版按 400 字滑窗粗切，一个 FAQ 答案可能被拦腰截成两块，也可能把两条不相关的
  FAQ 黏在同一块里 —— 检索命中后既难溯源到"是哪一条 FAQ"，rerank 的语义边界也很糊。
  本项目的 FAQ 是结构化写的：每条就是"一行三级标题 + 一段答案"，天然就是最佳的检索粒度。
  所以这里自己写解析：每个 "### [<id>] <title>" 条目精确切成一个 chunk，
  page_content = 标题问题文本 + 答案正文，metadata 带上条目级 item_id 方便命中判定与溯源。

  bge-large-zh-v1.5 单条 input 上限 512 token：单条 FAQ（标题+一段答案）远在 512 以内，
  按条目切天然不会超长，不必再担心截断。

为什么持久目录单独放 langgraph_cs/data/chroma_rag/？
  避免污染 EchoMind 主项目的 data/chroma（那是另一套 collection 与 embedding）。
  本目录是教学项目自己的库，互不干扰。
"""
import logging
import re
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document

from langgraph_cs.rag.embeddings import build_embeddings

logger = logging.getLogger(__name__)

# 本模块所在的 langgraph_cs/ 目录。
_BASE_DIR = Path(__file__).parent.parent

# Chroma 持久化目录（单独一份，不碰 EchoMind 主项目的 data/chroma）。
PERSIST_DIR = _BASE_DIR / "data" / "chroma_rag"
# 默认的 FAQ 数据目录。
DEFAULT_DOCS_DIR = _BASE_DIR / "data" / "faq"
# collection 名（与 EchoMind 主库区分）。
COLLECTION_NAME = "cs_faq"

# 条目标题的解析正则：匹配形如
#   ### [billing-03] 为什么我的退款一直失败/退不回来？
# 三级标题行 → 捕获方括号里的 item_id 与其后的问题标题文本。
# item_id 形如 "<domain>-<NN>"（domain 为字母，NN 为两位数字），与数据契约一致。
_ITEM_HEADER_RE = re.compile(r"^###\s*\[([a-zA-Z]+-\d{2})\]\s*(.*)$")


def _parse_faq_file(fp: Path) -> list[Document]:
    """
    解析单个 FAQ 文件，按"每个 ### 条目 = 一个 chunk"切块。

    解析逻辑（逐行扫描，自己实现，不用 RecursiveCharacterTextSplitter）：
      1. 逐行读：遇到匹配 "### [<id>] <title>" 的标题行，就把上一条目收尾、开启新条目；
      2. 标题行之后、下一个标题行之前的所有非空行，都算当前条目的答案正文；
      3. 一个条目的 page_content = 标题问题文本 + "\n" + 答案正文（去掉首尾空白）；
      4. metadata = {"source": 文件名, "item_id": "<id>"}，item_id 即方括号里的 "<domain>-<NN>"。

    标题行之前的内容（如文件顶部的 "# 账单与退款 FAQ" 一级大标题）不属于任何条目，自动忽略。
    """
    docs: list[Document] = []
    cur_id: str | None = None
    cur_title: str = ""
    cur_body: list[str] = []

    def _flush() -> None:
        """把当前累积的条目收尾成一个 Document（标题+答案），追加到 docs。"""
        if cur_id is None:
            return
        body = "\n".join(cur_body).strip()
        # page_content = 问题标题文本 + 答案正文；标题本身也参与 embedding，提升问法匹配。
        content = cur_title if not body else f"{cur_title}\n{body}"
        content = content.strip()
        if content:
            docs.append(
                Document(
                    page_content=content,
                    metadata={"source": fp.name, "item_id": cur_id},
                )
            )

    for raw_line in fp.read_text(encoding="utf-8").splitlines():
        m = _ITEM_HEADER_RE.match(raw_line.strip())
        if m:
            # 命中新标题：先把上一条目收尾，再开启新条目。
            _flush()
            cur_id = m.group(1)
            cur_title = m.group(2).strip()
            cur_body = []
        elif cur_id is not None:
            # 当前在某条目内，收集答案正文（跳过纯空行，正文里的换行用 join 还原）。
            line = raw_line.strip()
            if line:
                cur_body.append(line)
    # 文件结束，别忘了收尾最后一条。
    _flush()
    return docs


def load_faq_documents(docs_dir: Path | str = DEFAULT_DOCS_DIR) -> list[Document]:
    """
    读取 docs_dir 下所有 .md 文件，按"每个 ### 条目 = 一个 chunk"精确切块。

    这是**唯一**的 FAQ 解析入口，向量库（store.ingest）与词法库（bm25.build_bm25_retriever）
    都复用它，保证两条检索链路吃的是**完全相同的 121 个条目 chunk**（同样的 page_content
    与 metadata），评测对比才公平（遵循 code-reuse：解析逻辑只此一份，绝不复制）。

    每个 chunk 带上 metadata={"source": 文件名, "item_id": "<domain>-<NN>"}：
      - source 用于按文件溯源；
      - item_id 是条目级唯一标识，评测时用它做命中判定（检索到的 chunk 的 item_id
        是否落在该 query 的 relevant_ids 里）。

    page_content = 问题标题文本 + 答案正文（见 _parse_faq_file），词法/向量检索同源。
    """
    docs_dir = Path(docs_dir)
    docs: list[Document] = []
    files = sorted(docs_dir.glob("*.md"))
    for fp in files:
        file_docs = _parse_faq_file(fp)
        if not file_docs:
            logger.warning("文件未解析出任何条目（缺少 '### [id] 标题' 结构？）：%s", fp.name)
        docs.extend(file_docs)
        logger.info("解析 %s：切出 %d 个条目 chunk", fp.name, len(file_docs))
    logger.info("从 %s 读取 %d 个文件，共切出 %d 个条目 chunk", docs_dir, len(files), len(docs))
    return docs


def _reset_collection() -> None:
    """
    重建前先清掉旧 collection，避免与上一版（粗切的 10 块）混在一起。

    用 Chroma 客户端按名删除 collection；collection 不存在时静默忽略。
    只删本项目这一个 collection，不动 PERSIST_DIR 下别的东西。
    """
    if not PERSIST_DIR.exists():
        return
    try:
        store = Chroma(
            collection_name=COLLECTION_NAME,
            embedding_function=build_embeddings(),
            persist_directory=str(PERSIST_DIR),
        )
        store.delete_collection()
        logger.info("已清除旧 collection：%s", COLLECTION_NAME)
    except Exception as exc:  # noqa: BLE001 —— 旧库不存在/损坏都不该阻塞重建
        logger.info("清除旧 collection 跳过（可能本就不存在）：%s", exc)


def ingest(docs_dir: Path | str = DEFAULT_DOCS_DIR) -> int:
    """
    把 docs_dir 下的 FAQ 文件按条目切块、embedding 后灌入本地持久化 Chroma。

    返回写入的条目 chunk 数量（= FAQ 条目总数）。
    灌库前会先清掉旧 collection 再重建，避免与上一版混淆、避免重复条目。
    """
    docs_dir = Path(docs_dir)
    if not docs_dir.exists():
        raise FileNotFoundError(f"FAQ 数据目录不存在：{docs_dir}")

    documents = load_faq_documents(docs_dir)
    if not documents:
        logger.warning("没有可灌入的条目（目录为空或文件缺少 '### [id]' 结构）：%s", docs_dir)
        return 0

    PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    # 先清旧 collection，确保是干净重建而非追加。
    _reset_collection()
    # from_documents 会调用 embedding（真实网络请求，需要 key）并写入磁盘。
    Chroma.from_documents(
        documents=documents,
        embedding=build_embeddings(),
        collection_name=COLLECTION_NAME,
        persist_directory=str(PERSIST_DIR),
    )
    logger.info("已灌入 %d 个条目 chunk 到 Chroma：%s", len(documents), PERSIST_DIR)
    return len(documents)


def build_retriever(k: int = 5):
    """
    打开已持久化的 Chroma，返回一个 top-k 检索器。

    参数 k：向量检索返回的候选数。做 rerank 实验时通常先取较大的 k（如 20）
            作为粗排候选，再用 rerank 精排截 top-n。

    用法（PR2 的 rag_node）：
      retriever = build_retriever(k=20)
      hits = retriever.invoke("用户问题")   # -> List[Document]，每个 doc 的
                                            # metadata 含 source 与 item_id
    """
    vectorstore = Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=build_embeddings(),
        persist_directory=str(PERSIST_DIR),
    )
    return vectorstore.as_retriever(search_kwargs={"k": k})


if __name__ == "__main__":
    # 一键灌库入口：python -m langgraph_cs.rag.store
    # （也可用 scripts/ingest_faq.py，二者等价）
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    n = ingest()
    print(f"灌库完成，共写入 {n} 个条目 chunk 到 {PERSIST_DIR}")
