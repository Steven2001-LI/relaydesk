"""
一键把 langgraph_cs/data/faq 下的 FAQ 灌进本地 Chroma。

用法（从仓库根运行，确保能 import 到 langgraph_cs 包）：
    langgraph_cs/.venv/bin/python -m langgraph_cs.scripts.ingest_faq

前置条件：
    langgraph_cs/.env 里已填好 SILICONFLOW_API_KEY（灌库会真实调用 embedding，需联网且消耗额度）。

效果：
    按条目切块（每个 ### 条目 = 一个 chunk）→ embedding → 写入
    langgraph_cs/data/chroma_rag/（collection: cs_faq）。灌库前会先清掉旧 collection 再重建。
    之后 rag_node 用 store.build_retriever(k) 即可检索（chunk 带 source 与 item_id）。
"""
import logging

from langgraph_cs.rag.store import PERSIST_DIR, ingest


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    n = ingest()
    print(f"灌库完成，共写入 {n} 个条目 chunk 到 {PERSIST_DIR}")


if __name__ == "__main__":
    main()
