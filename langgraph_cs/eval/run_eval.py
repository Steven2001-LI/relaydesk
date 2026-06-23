"""
run_eval —— "朴素 vs rerank" 检索质量对比评测（条目级 item_id）。

跑法（从仓库根目录，-m 模块方式保证 import 路径正确）：
    langgraph_cs/.venv/bin/python -m langgraph_cs.eval.run_eval
    langgraph_cs/.venv/bin/python -m langgraph_cs.eval.run_eval --mode realistic --top-n 3
    langgraph_cs/.venv/bin/python -m langgraph_cs.eval.run_eval --mode subset --k 10 --top-n 3
    langgraph_cs/.venv/bin/python -m langgraph_cs.eval.run_eval --stage1 bm25 --write-md  # 弱检索器对照
    langgraph_cs/.venv/bin/python -m langgraph_cs.eval.run_eval --write-md   # 顺手写 eval/results.md
    langgraph_cs/.venv/bin/python -m langgraph_cs.eval.run_eval --self-test  # 离线自测，不连网

前置条件（仅真实评测需要，--self-test 不需要）：
    1) langgraph_cs/.env 已填 SILICONFLOW_API_KEY（rerank 一定联网；stage1=dense 时 embedding 也联网、消耗额度）；
    2) stage1=dense 时已灌库：langgraph_cs/.venv/bin/python -m langgraph_cs.scripts.ingest_faq
       （stage1=bm25 的一阶段是纯本地词法检索，不需要灌库、不连网；只有 B 路 rerank 才连网。）

命中判定（贯穿全脚本）：
    一律用检索到的 chunk 的 metadata["item_id"]（形如 billing-03）是否落在该 query 的
    relevant_ids 里。按"条目级"判定，比按文件名（source）粗粒度更有判别力。

一阶段检索器（--stage1，默认 dense）：
  * dense（默认）：向量检索（硅基流动 bge-large-zh-v1.5）。语义强，正确条目几乎总进 top-3，
    在这份短 FAQ 上 rerank 几乎无救援空间（Hit@3 100% vs 100%，recovered≈0）。
  * bm25：BM25 词法/稀疏检索（本地，不连网）。靠字面词项重合打分，对"换了说法"的 query 召回弱，
    会把正确条目漏出 top-n —— 给 rerank 留出救援空间，得到"rerank 真正有价值"的对照。
  两种 stage1 共用完全相同的对比逻辑/指标/sweep，只是把"一阶段候选来源"从 dense 换成 BM25。

两种对比模式（--mode，默认 realistic）：
  * realistic（头条对比，默认）：
      A 朴素 = 一阶段检索器直接取 top-n（k=n），看它能不能把正确条目排进前 n；
      B +rerank = 一阶段取较宽候选 K_wide（--k-wide，默认 30）→ rerank 精排 → 取 top-n。
      体现 rerank 的真实价值：从更宽的候选里，把朴素 top-n 漏掉的正确条目"捞"进 top-n。
  * subset（保留参考）：
      A = top-k；B = 对同一批 top-k 做 rerank 后取 top-n（n≤k）。
      B 的候选是 A 的子集重排，提升完全来自"重排顺序"，但救不回 top-k 之外的条目。

配额保护：
    rerank 按账户限流（RPM 2000）。本脚本严格"顺序"跑、绝不并发，并支持 --limit 限量。
    realistic 模式每条 query：1 次一阶段检索(K_wide) + 1 次 rerank；A 直接从同一批候选切前 n，
    不额外发请求。BM25 一阶段是本地计算，不计入 RPM。
"""
import argparse
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).parent
DATASET_PATH = _BASE_DIR / "dataset.json"
RESULTS_MD_PATH = _BASE_DIR / "results.md"

# realistic 模式默认的粗排候选宽度（rerank 从这么宽的候选里精排）。
DEFAULT_K_WIDE = 30
# 小 sweep 默认扫的 top-n 取值。
SWEEP_NS = (1, 3, 5)


# --------------------------------------------------------------------------- #
# 指标计算（纯函数，只吃"按排名的 item_id 列表 + relevant_ids 集合"，不碰网络）
#
# 设计成纯函数的好处：可以用 mock 数据离线自测算法对不对（见 _self_test），
# 不必真发 API。每条样本抽象成两样东西：
#   - ranked_ids：检索结果按排名给出的 item_id 列表（rank1, rank2, ...）
#   - relevant：该 query 的 ground-truth 相关 item_id 集合（来自 relevant_ids）
# --------------------------------------------------------------------------- #
def hit_at_n(ranked_ids: list[str], relevant: set[str]) -> float:
    """Hit@n：top-n 结果里只要有一个 item_id 命中相关条目就算命中，返回 1.0 否则 0.0。"""
    for item_id in ranked_ids:
        if item_id in relevant:
            return 1.0
    return 0.0


def recall_at_n(ranked_ids: list[str], relevant: set[str]) -> float:
    """
    Recall@n：top-n 覆盖到的相关 item_id 占该 query 全部 relevant_ids 的比例。

    按"去重后的 item_id"算（覆盖率），同一条目重复出现只算 1 次，不让 recall 虚高。
    relevant 为空时定义为 1.0（无可召回项，不惩罚）。
    """
    if not relevant:
        return 1.0
    covered = {item_id for item_id in ranked_ids if item_id in relevant}
    return len(covered) / len(relevant)


def reciprocal_rank(ranked_ids: list[str], relevant: set[str]) -> float:
    """
    单条样本的 Reciprocal Rank：第一个命中相关条目的排名倒数 1/rank（rank 从 1 起）。

    一个都没命中则为 0。对所有样本取均值即 MRR（Mean Reciprocal Rank）。
    MRR 越高说明"正确条目排得越靠前"。
    """
    for i, item_id in enumerate(ranked_ids, start=1):
        if item_id in relevant:
            return 1.0 / i
    return 0.0


def aggregate(samples: list[tuple[list[str], set[str]]]) -> dict[str, float]:
    """
    对一批样本汇总三项指标（取均值）。

    samples：[(ranked_ids, relevant), ...]。
    返回 {"hit": ..., "recall": ..., "mrr": ...}；样本为空时全 0。
    """
    n = len(samples)
    if n == 0:
        return {"hit": 0.0, "recall": 0.0, "mrr": 0.0}
    hit = sum(hit_at_n(rs, rel) for rs, rel in samples) / n
    recall = sum(recall_at_n(rs, rel) for rs, rel in samples) / n
    mrr = sum(reciprocal_rank(rs, rel) for rs, rel in samples) / n
    return {"hit": hit, "recall": recall, "mrr": mrr}


def count_recovered(
    a_samples: list[tuple[list[str], set[str]]],
    b_samples: list[tuple[list[str], set[str]]],
) -> dict[str, int]:
    """
    统计 B 相比 A 的"救援"效果（按 item_id 的 Hit）。

    - recovered：A 没命中（miss）但 B 命中（hit）的 query 数 —— B 把它"救"回来了。
    - regressed：A 命中但 B 没命中的 query 数 —— 反向退化（理想为 0）。
    - changed：top-n 的 item_id 集合在 A 与 B 间发生了变化的 query 数（顺序无关，只看集合）。

    a_samples 与 b_samples 必须等长且一一对应（同一条 query 的 A、B 结果）。
    """
    if len(a_samples) != len(b_samples):
        raise ValueError("A、B 样本数不一致，无法逐条对比")
    recovered = regressed = changed = 0
    for (a_ids, rel), (b_ids, _rel_b) in zip(a_samples, b_samples):
        a_hit = hit_at_n(a_ids, rel) == 1.0
        b_hit = hit_at_n(b_ids, rel) == 1.0
        if (not a_hit) and b_hit:
            recovered += 1
        if a_hit and (not b_hit):
            regressed += 1
        if set(a_ids) != set(b_ids):
            changed += 1
    return {"recovered": recovered, "regressed": regressed, "changed": changed}


# --------------------------------------------------------------------------- #
# 数据加载与校验
# --------------------------------------------------------------------------- #
def load_dataset(path: Path = DATASET_PATH) -> list[dict]:
    """
    读取并校验 dataset.json（顶层为数组），返回 items 列表。

    每项必含：question（非空）、relevant_ids（非空字符串数组）、expected_keywords（数组）。
    字段缺失/类型不对会直接报错。
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    # 兼容两种写法：顶层就是数组，或 {"items": [...]}。
    items = data if isinstance(data, list) else data.get("items", [])
    if not isinstance(items, list) or not items:
        raise ValueError(f"数据集为空或格式错误：{path}")
    for i, it in enumerate(items):
        if not it.get("question"):
            raise ValueError(f"第 {i} 条缺少 question")
        if not isinstance(it.get("relevant_ids"), list) or not it["relevant_ids"]:
            raise ValueError(f"第 {i} 条 relevant_ids 缺失或为空")
        if not isinstance(it.get("expected_keywords"), list):
            raise ValueError(f"第 {i} 条 expected_keywords 缺失或类型错误")
    return items


# --------------------------------------------------------------------------- #
# 检索：把检索结果抽象成"按排名的 item_id 列表"喂给上面的纯函数指标
#
# 真实网络调用集中在这里，便于隔离。命中判定一律用 metadata["item_id"]。
# --------------------------------------------------------------------------- #
def _ids_from_hits(hits) -> list[str]:
    """从一批 LangChain Document 里按顺序抽出 metadata['item_id']（未知记 '?'）。"""
    return [h.metadata.get("item_id", "?") for h in hits]


class _BM25RetrieverAdapter:
    """
    把 BM25Retriever 适配成"和向量 retriever 同样的 .invoke(query) → List[Document]"接口。

    这样评测脚本里所有检索/rerank 辅助函数（retrieve_topn / rerank_topn）都无需改动，
    一阶段是 dense 还是 bm25 对它们透明——只是 invoke 背后换了实现。
    BM25 一阶段纯本地、不连网；返回的 Document 仍带 item_id/source metadata（与向量库同源）。
    """

    def __init__(self, bm25_retriever, k: int) -> None:
        self._bm25 = bm25_retriever
        self._k = k

    def invoke(self, query: str):
        return self._bm25.search_documents(query, k=self._k)


def build_stage1_retriever(stage1: str, k: int):
    """
    按 --stage1 构造一阶段检索器，统一暴露 .invoke(query) → List[Document] 接口。

    - dense：向量检索（需已灌库 + embedding 联网），用 rag.build_retriever。
    - bm25：BM25 词法检索（本地、不连网），用 rag.bm25.build_bm25_retriever 后包一层适配器。

    真实依赖在函数内才 import，保证 `import run_eval` 本身轻量。
    """
    if stage1 == "bm25":
        from langgraph_cs.rag.bm25 import build_bm25_retriever
        return _BM25RetrieverAdapter(build_bm25_retriever(), k=k)
    from langgraph_cs.rag import build_retriever
    return build_retriever(k=k)


def retrieve_topn(retriever, query: str, top_n: int) -> list[str]:
    """朴素：用向量检索取候选，取前 top_n 的 item_id（保持检索排序）。"""
    hits = retriever.invoke(query)
    return _ids_from_hits(hits)[:top_n]


def rerank_topn(retriever, rerank_fn, query: str, top_n: int) -> list[str]:
    """
    加 rerank：先用 retriever 取候选，再 rerank 精排截 top_n，
    用 rerank 返回的原始 index 映射回候选 Document 的 item_id（保留 metadata）。
    """
    hits = retriever.invoke(query)
    if not hits:
        return []
    doc_texts = [h.page_content for h in hits]
    ranked = rerank_fn(query, doc_texts, top_n=top_n)  # [(index, score, text), ...] 已降序
    return [hits[idx].metadata.get("item_id", "?") for idx, _score, _text in ranked]


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
def _fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _improve(a: float, b: float) -> str:
    """B 相对 A 的提升：绝对值（百分点）+ 相对值（%）。a=0 时只给绝对值。"""
    delta = b - a
    if a == 0:
        return f"+{delta * 100:.1f}pp"
    rel = delta / a * 100
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta * 100:.1f}pp ({sign}{rel:.1f}%)"


def _mode_labels(mode: str, k: int, k_wide: int, top_n: int) -> tuple[str, str]:
    """根据模式返回 (A 列标题, B 列标题)。"""
    if mode == "realistic":
        return f"A 朴素 (top-{top_n})", f"B +rerank (K={k_wide}→top-{top_n})"
    return f"A 朴素 (top-{k})", f"B +rerank (top-{top_n})"


def render_table(naive: dict, reranked: dict, a_label: str, b_label: str) -> str:
    """把 A vs B 三项指标并排成一张文本表，附提升幅度。"""
    rows = [
        ("指标", a_label, b_label, "提升 (B vs A)"),
        ("Hit", _fmt_pct(naive["hit"]), _fmt_pct(reranked["hit"]),
         _improve(naive["hit"], reranked["hit"])),
        ("Recall", _fmt_pct(naive["recall"]), _fmt_pct(reranked["recall"]),
         _improve(naive["recall"], reranked["recall"])),
        ("MRR", f"{naive['mrr']:.4f}", f"{reranked['mrr']:.4f}",
         _improve(naive["mrr"], reranked["mrr"])),
    ]
    widths = [max(len(r[c]) for r in rows) for c in range(4)]
    lines = []
    for ri, row in enumerate(rows):
        line = " | ".join(cell.ljust(widths[c]) for c, cell in enumerate(row))
        lines.append(line)
        if ri == 0:
            lines.append("-+-".join("-" * w for w in widths))
    return "\n".join(lines)


def render_sweep_table(sweep: list[dict]) -> str:
    """把 n ∈ {1,3,5} 的小 sweep 渲染成一张对比表（含 recovered）。"""
    header = ("top-n", "A Hit", "B Hit", "A MRR", "B MRR", "recovered", "changed")
    rows = [header]
    for r in sweep:
        rows.append((
            str(r["n"]),
            _fmt_pct(r["naive"]["hit"]),
            _fmt_pct(r["reranked"]["hit"]),
            f"{r['naive']['mrr']:.3f}",
            f"{r['reranked']['mrr']:.3f}",
            str(r["recovered"]),
            str(r["changed"]),
        ))
    widths = [max(len(r[c]) for r in rows) for c in range(len(header))]
    lines = []
    for ri, row in enumerate(rows):
        lines.append(" | ".join(cell.ljust(widths[c]) for c, cell in enumerate(row)))
        if ri == 0:
            lines.append("-+-".join("-" * w for w in widths))
    return "\n".join(lines)


# results.md 里两个 stage1 各占一节，用下面这对 marker 圈定，便于"只更新本节、不覆盖另一节"。
_SECTION_BEGIN = "<!-- STAGE1:{stage1} BEGIN -->"
_SECTION_END = "<!-- STAGE1:{stage1} END -->"
# 顶部汇总小表的某一行也用 marker 标注，便于按 stage1 单独替换。
_SUMMARY_ROW = "<!-- SUMMARY:{stage1} -->"


def _stage1_title(stage1: str) -> str:
    return "弱检索器 BM25 对照" if stage1 == "bm25" else "强检索器 dense（向量）对照"


def _render_section(
    stage1: str, mode: str, k: int, k_wide: int, top_n: int, n_samples: int,
    naive: dict, reranked: dict, recovered: dict, sweep: list[dict],
) -> str:
    """渲染某一个 stage1 的完整一节（用 marker 包起来，方便单独替换）。"""
    a_label, b_label = _mode_labels(mode, k, k_wide, top_n)
    if mode == "realistic":
        setup = f"模式 = realistic（A=top-{top_n}；B=K_wide({k_wide})→rerank→top-{top_n}）"
    else:
        setup = f"模式 = subset（A=top-{k}；B=对同一批 top-{k} rerank→top-{top_n}）"

    if stage1 == "bm25":
        stage1_note = ("一阶段 = BM25 词法/稀疏检索（本地，不连网）。BM25 对\"换了说法\"的 query 召回弱，"
                       "正确条目常被漏出 top-n，给 rerank 留出救援空间 —— 这才看得出 rerank 的真实价值。")
    else:
        stage1_note = ("一阶段 = dense 向量检索（硅基流动 bge-large-zh-v1.5）。语义强，正确条目几乎总进 top-3，"
                       "rerank 救援空间小（recovered≈0）。")

    sweep_rows = "\n".join(
        f"| {r['n']} | {_fmt_pct(r['naive']['hit'])} | {_fmt_pct(r['reranked']['hit'])} "
        f"| {r['naive']['mrr']:.3f} | {r['reranked']['mrr']:.3f} "
        f"| {r['recovered']} | {r['changed']} |"
        for r in sweep
    )

    begin = _SECTION_BEGIN.format(stage1=stage1)
    end = _SECTION_END.format(stage1=stage1)
    return f"""{begin}
## {_stage1_title(stage1)}

- 一阶段检索器：`--stage1 {stage1}`；{setup}
- {stage1_note}

### 主对比（top-n = {top_n}）

| 指标 | {a_label} | {b_label} | 提升 (B vs A) |
|---|---|---|---|
| Hit | {_fmt_pct(naive['hit'])} | {_fmt_pct(reranked['hit'])} | {_improve(naive['hit'], reranked['hit'])} |
| Recall | {_fmt_pct(naive['recall'])} | {_fmt_pct(reranked['recall'])} | {_improve(naive['recall'], reranked['recall'])} |
| MRR | {naive['mrr']:.4f} | {reranked['mrr']:.4f} | {_improve(naive['mrr'], reranked['mrr'])} |

- recovered（B 把 A 漏掉的 query 救成命中）：**{recovered['recovered']}**
- regressed（A 命中但 B 漏掉，理想为 0）：{recovered['regressed']}
- changed（top-{top_n} 条目集合发生变化的 query 数）：{recovered['changed']}

### n sweep（n ∈ {{1, 3, 5}}）

| top-n | A Hit | B Hit | A MRR | B MRR | recovered | changed |
|---|---|---|---|---|---|---|
{sweep_rows}

> 数字由 `python -m langgraph_cs.eval.run_eval --stage1 {stage1} --mode {mode} --write-md` 生成。
{end}"""


def _summary_row(stage1: str, top_n: int, naive: dict, reranked: dict, recovered: dict) -> str:
    """顶部汇总小表里属于本 stage1 的那一行（带 marker，便于单独替换）。"""
    marker = _SUMMARY_ROW.format(stage1=stage1)
    return (f"| {stage1} | {_fmt_pct(naive['hit'])} | {_fmt_pct(reranked['hit'])} "
            f"| {naive['mrr']:.4f} | {reranked['mrr']:.4f} | {recovered['recovered']} | "
            f"{_improve(naive['hit'], reranked['hit'])} | {_improve(naive['mrr'], reranked['mrr'])} "
            f"{marker} |")


def _extract_marked(text: str, begin: str, end: str) -> str | None:
    """从已有 results.md 里抠出 begin..end 之间（含 marker）的整段；没有则返回 None。"""
    bi = text.find(begin)
    if bi == -1:
        return None
    ei = text.find(end, bi)
    if ei == -1:
        return None
    return text[bi:ei + len(end)]


def _extract_summary_row(text: str, stage1: str) -> str | None:
    """从已有 results.md 顶部汇总表里抠出某 stage1 的那一行（按行内 marker 定位）。"""
    marker = _SUMMARY_ROW.format(stage1=stage1)
    for line in text.splitlines():
        if marker in line:
            return line
    return None


def write_results_md(
    stage1: str, mode: str, k: int, k_wide: int, top_n: int, n_samples: int,
    naive: dict, reranked: dict, recovered: dict, sweep: list[dict],
) -> None:
    """
    把对比结果（含 sweep 表与 recovered 数）落一份 Markdown，方便贴进 README / 简历。

    支持 dense 与 bm25 两个 stage1 各占一节：本次只更新「当前 stage1」这一节与顶部汇总表里
    它对应的那一行，**不覆盖另一个 stage1** 已有的结果（用 marker 圈定、就地替换/追加）。
    顶部有一张 dense vs bm25 的汇总小表，一眼看出两种一阶段检索器下 rerank 的救援差异。
    """
    existing = RESULTS_MD_PATH.read_text(encoding="utf-8") if RESULTS_MD_PATH.exists() else ""

    # 本次这一节、本次的汇总行。
    this_section = _render_section(
        stage1, mode, k, k_wide, top_n, n_samples, naive, reranked, recovered, sweep
    )
    this_summary_row = _summary_row(stage1, top_n, naive, reranked, recovered)

    # 另一个 stage1（保留它已有的结果，没有就留占位）。
    other = "bm25" if stage1 == "dense" else "dense"
    other_section = _extract_marked(
        existing,
        _SECTION_BEGIN.format(stage1=other),
        _SECTION_END.format(stage1=other),
    )
    other_summary_row = _extract_summary_row(existing, other)
    if other_summary_row is None:
        # 另一个 stage1 还没跑过：占位一行（dash），跑过后会被替换成真实数字。
        marker = _SUMMARY_ROW.format(stage1=other)
        other_summary_row = f"| {other} | - | - | - | - | - | - | - {marker} |"
    if other_section is None:
        other_section = (f"{_SECTION_BEGIN.format(stage1=other)}\n"
                         f"## {_stage1_title(other)}\n\n"
                         f"> 该一阶段尚未跑过。运行 "
                         f"`python -m langgraph_cs.eval.run_eval --stage1 {other} --write-md` 后填入。\n"
                         f"{_SECTION_END.format(stage1=other)}")

    # 汇总表两行按固定顺序排（dense 在上、bm25 在下），便于阅读对照。
    dense_row = this_summary_row if stage1 == "dense" else other_summary_row
    bm25_row = this_summary_row if stage1 == "bm25" else other_summary_row
    # 各节也按 dense → bm25 顺序拼。
    dense_section = this_section if stage1 == "dense" else other_section
    bm25_section = this_section if stage1 == "bm25" else other_section

    md = f"""# RAG 检索评测结果（条目级 item_id）

- 测试集样本数：{n_samples}
- 命中判定：检索 chunk 的 `metadata.item_id` 是否落在该 query 的 `relevant_ids`
- 指标定义见 `langgraph_cs/eval/__init__.py`

## 汇总：dense vs bm25 一阶段下的 rerank 效果（top-n = {top_n}）

| 一阶段 | A Hit | B Hit | A MRR | B MRR | recovered | Hit 提升 | MRR 提升 |
|---|---|---|---|---|---|---|---|
{dense_row}
{bm25_row}

> A = 一阶段朴素 top-n；B = 一阶段取宽候选 → rerank → top-n。
> recovered = B 相比 A 把原本 miss 的 query 救成 hit 的数量 —— 弱一阶段(bm25)下应明显大于强一阶段(dense)，
> 这正是\"rerank 真正有价值\"的对照证据。

{dense_section}

{bm25_section}

> Hit = 命中率（top-n 含至少一个正确条目的 query 占比）；
> Recall = 平均覆盖正确条目的比例；MRR = 第一个命中条目排名倒数的均值；
> recovered = B 相比 A 把原本 miss 的 query 救成 hit 的数量。
"""
    RESULTS_MD_PATH.write_text(md, encoding="utf-8")
    logger.info("已写入评测结果（stage1=%s 节已更新，另一节保留）：%s", stage1, RESULTS_MD_PATH)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def _eval_one_n(
    items: list[dict], retriever, wide_retriever, rerank_fn, mode: str, top_n: int,
) -> dict:
    """
    对给定 top_n 跑一遍 A/B 并汇总（被主对比与 sweep 共用）。

    - realistic：A=从 wide 候选切前 top_n；B=对 wide 候选 rerank 取 top_n。
                 （A、B 共用同一次 wide_retriever.invoke 的结果由各自函数内部独立取，
                  这里为简单清晰各调一次；顺序执行，不并发。）
    - subset：A=top-k 全量（retriever 已按 k 构造）；B=对同一批 top-k rerank 取 top_n。

    返回 {"naive": agg, "reranked": agg, "recovered": int, "changed": int,
          "naive_samples": [...], "rerank_samples": [...]}。
    """
    naive_samples: list[tuple[list[str], set[str]]] = []
    rerank_samples: list[tuple[list[str], set[str]]] = []

    n_total = len(items)
    for i, it in enumerate(items, start=1):
        query = it["question"]
        relevant = set(it["relevant_ids"])

        if mode == "realistic":
            # A 朴素：从较宽候选里直接切前 top_n（= 朴素向量检索的 top-n 头条）。
            a_ids = retrieve_topn(wide_retriever, query, top_n=top_n)
            # B：对同一批较宽候选 rerank，截 top_n。顺序执行，绝不并发，护 RPM。
            b_ids = rerank_topn(wide_retriever, rerank_fn, query, top_n=top_n)
        else:  # subset
            # A 朴素：top-k 全量参与命中判定（retriever 是按 --k 构造的）。
            a_ids = retrieve_topn(retriever, query, top_n=10 ** 9)
            # B：对同一批 top-k rerank，截 top_n。
            b_ids = rerank_topn(retriever, rerank_fn, query, top_n=top_n)

        naive_samples.append((a_ids, relevant))
        rerank_samples.append((b_ids, relevant))
        logger.info("[n=%d %d/%d] %s | A=%s | B=%s",
                    top_n, i, n_total, query[:18], a_ids[:3], b_ids[:3])

    naive = aggregate(naive_samples)
    reranked = aggregate(rerank_samples)
    rec = count_recovered(naive_samples, rerank_samples)
    return {
        "naive": naive, "reranked": reranked,
        "recovered": rec["recovered"], "regressed": rec["regressed"],
        "changed": rec["changed"],
        "naive_samples": naive_samples, "rerank_samples": rerank_samples,
    }


def run(mode: str, k: int, k_wide: int, top_n: int, limit: int | None,
        write_md: bool, stage1: str = "dense") -> dict:
    """
    跑完整评测：主对比（给定 top_n）+ n∈{1,3,5} 小 sweep，打印对比表。

    stage1：一阶段检索器（dense=向量检索，bm25=BM25 词法检索）。
            两者共用全部对比逻辑/指标，只是候选来源不同。

    真实检索依赖（embedding/Chroma/rerank/bm25）在函数内才 import，
    这样 `import run_eval` 本身不需要这些重依赖，指标纯函数也能被单独 import 自测。
    rerank 总是要的（B 路精排）；dense 还需 embedding，bm25 一阶段则纯本地。
    """
    from langgraph_cs.rag import rerank

    items = load_dataset()
    if limit is not None:
        items = items[:limit]
    n = len(items)
    logger.info("评测开始：stage1=%s，mode=%s，%d 条 query，k=%d，k_wide=%d，top_n=%d",
                stage1, mode, n, k, k_wide, top_n)

    # subset 模式用窄 retriever（k）；realistic 模式用宽 retriever（k_wide）做粗排候选。
    # 一阶段是 dense 还是 bm25 由 build_stage1_retriever 决定，下游函数对此透明。
    retriever = build_stage1_retriever(stage1, k=k)
    wide_retriever = build_stage1_retriever(stage1, k=k_wide)

    a_label, b_label = _mode_labels(mode, k, k_wide, top_n)

    # 主对比（给定 top_n）。
    main = _eval_one_n(items, retriever, wide_retriever, rerank, mode, top_n)

    print()
    print(f"=== 检索质量对比（stage1={stage1}，mode={mode}，n={n}，"
          f"{'K_wide=' + str(k_wide) if mode == 'realistic' else 'k=' + str(k)}，"
          f"top_n={top_n}）===")
    print(render_table(main["naive"], main["reranked"], a_label, b_label))
    print()
    print(f"救援分析：recovered={main['recovered']}（B 把 A 漏掉的救成命中），"
          f"regressed={main['regressed']}（反向退化），"
          f"changed={main['changed']}（top-{top_n} 条目集合变化的 query 数）。")

    # 小 sweep：n ∈ {1,3,5}。
    sweep: list[dict] = []
    for nn in SWEEP_NS:
        r = _eval_one_n(items, retriever, wide_retriever, rerank, mode, nn)
        sweep.append({
            "n": nn, "naive": r["naive"], "reranked": r["reranked"],
            "recovered": r["recovered"], "changed": r["changed"],
        })

    print()
    print(f"=== n sweep（n ∈ {list(SWEEP_NS)}，stage1={stage1}，mode={mode}）===")
    print(render_sweep_table(sweep))
    print()
    stage1_word = "BM25 词法检索" if stage1 == "bm25" else "朴素向量检索"
    if mode == "realistic":
        print(f"说明：A={stage1_word} top-n；B=从 K_wide 候选 rerank 取 top-n。"
              "rerank 的价值体现在把朴素 top-n 漏掉的正确条目捞回（recovered）。")
    else:
        print("说明：A=top-k；B=对同一批 top-k rerank 取 top-n。"
              "B 救不回 top-k 之外的条目，提升仅来自重排顺序。")

    recovered = {"recovered": main["recovered"], "regressed": main["regressed"],
                 "changed": main["changed"]}
    if write_md:
        write_results_md(stage1, mode, k, k_wide, top_n, n,
                         main["naive"], main["reranked"], recovered, sweep)

    return {"naive": main["naive"], "reranked": main["reranked"],
            "recovered": recovered, "sweep": sweep}


def _self_test() -> None:
    """
    用 mock 的 ranked item_id 列表离线自测指标算法（不发任何网络请求）。
    跑法：langgraph_cs/.venv/bin/python -m langgraph_cs.eval.run_eval --self-test
    """
    rel = {"billing-03"}
    # rank1 即命中：Hit=1, RR=1, Recall=1
    assert hit_at_n(["billing-03", "billing-09"], rel) == 1.0
    assert reciprocal_rank(["billing-03", "billing-09"], rel) == 1.0
    assert recall_at_n(["billing-03", "billing-09"], rel) == 1.0
    # rank2 才命中：Hit=1, RR=0.5
    assert reciprocal_rank(["account-01", "billing-03"], rel) == 0.5
    # 全未命中：Hit=0, RR=0, Recall=0
    assert hit_at_n(["account-01", "tech-05"], rel) == 0.0
    assert reciprocal_rank(["account-01", "tech-05"], rel) == 0.0
    assert recall_at_n(["account-01", "tech-05"], rel) == 0.0
    # 多 relevant，覆盖一半：Recall=0.5
    rel2 = {"member-02", "member-03"}
    assert recall_at_n(["member-02", "account-01"], rel2) == 0.5
    # 同一条目重复召回不让 recall 虚高
    assert recall_at_n(["member-02", "member-02"], rel2) == 0.5

    # 聚合：两条样本 hit=1/1 与 0/1 -> 均值 0.5；mrr=(1 + 0)/2=0.5；recall=(1 + 0)/2=0.5
    agg = aggregate([(["billing-03"], rel), (["account-01"], rel)])
    assert abs(agg["hit"] - 0.5) < 1e-9
    assert abs(agg["mrr"] - 0.5) < 1e-9
    assert abs(agg["recall"] - 0.5) < 1e-9

    # recovered/regressed/changed：
    #   q1: A miss（tech-05）, B hit（billing-03） -> recovered
    #   q2: A hit（billing-03）, B miss（tech-05） -> regressed
    #   q3: A 与 B 集合相同（命中）-> 不计 changed
    a = [(["tech-05"], rel), (["billing-03"], rel), (["billing-03"], rel)]
    b = [(["billing-03"], rel), (["tech-05"], rel), (["billing-03"], rel)]
    rec = count_recovered(a, b)
    assert rec["recovered"] == 1, rec
    assert rec["regressed"] == 1, rec
    assert rec["changed"] == 2, rec  # 前两条集合变了，第三条没变

    # changed 只看集合不看顺序：同集合不同序 -> 不算 changed
    a2 = [(["billing-03", "billing-09"], rel)]
    b2 = [(["billing-09", "billing-03"], rel)]
    assert count_recovered(a2, b2)["changed"] == 0

    print("self-test 通过：Hit / Recall / MRR / aggregate / recovered 计算正确（按 item_id）。")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="朴素向量检索 vs 加 rerank 的 RAG 检索质量对比评测（条目级 item_id）。"
    )
    parser.add_argument("--stage1", choices=["dense", "bm25"], default="dense",
                        help="一阶段检索器：dense（默认，向量检索，需灌库+embedding 联网）；"
                             "bm25（BM25 词法检索，本地、不连网，弱一阶段，给 rerank 留救援空间）。")
    parser.add_argument("--mode", choices=["realistic", "subset"], default="realistic",
                        help="realistic（默认，头条对比）：A=top-n，B=K_wide→rerank→top-n；"
                             "subset：A=top-k，B=对同一批 top-k rerank→top-n。")
    parser.add_argument("--k", type=int, default=10,
                        help="subset 模式下向量检索的 top-k（A 按此评，B 从此批精排）。默认 10。")
    parser.add_argument("--k-wide", type=int, default=DEFAULT_K_WIDE, dest="k_wide",
                        help="realistic 模式下较宽候选数 K_wide（rerank 从这么宽里精排）。默认 30。")
    parser.add_argument("--top-n", type=int, default=3, dest="top_n",
                        help="主对比保留的文档数 top-n。默认 3。（sweep 固定扫 1/3/5）")
    parser.add_argument("--limit", type=int, default=None,
                        help="只跑前 N 条 query（护配额/快速试跑）。默认跑全部。")
    parser.add_argument("--write-md", action="store_true",
                        help="把对比结果（含 sweep 表与 recovered 数）写到 eval/results.md。")
    parser.add_argument("--self-test", action="store_true",
                        help="只跑指标算法离线自测（不发网络请求），验证 Hit/Recall/MRR/recovered。")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.self_test:
        _self_test()
        return

    if args.mode == "subset" and args.top_n > args.k:
        parser.error(f"subset 模式下 --top-n({args.top_n}) 不应大于 --k({args.k})："
                     "rerank 是从 top-k 候选里精排。")
    if args.mode == "realistic" and args.top_n > args.k_wide:
        parser.error(f"realistic 模式下 --top-n({args.top_n}) 不应大于 --k-wide({args.k_wide})。")

    run(mode=args.mode, k=args.k, k_wide=args.k_wide, top_n=args.top_n,
        limit=args.limit, write_md=args.write_md, stage1=args.stage1)


if __name__ == "__main__":
    main()
