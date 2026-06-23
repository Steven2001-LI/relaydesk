# RAG 检索评测结果（条目级 item_id）

- 测试集样本数：55
- 命中判定：检索 chunk 的 `metadata.item_id` 是否落在该 query 的 `relevant_ids`
- 指标定义见 `langgraph_cs/eval/__init__.py`

## 汇总：dense vs bm25 一阶段下的 rerank 效果（top-n = 3）

| 一阶段 | A Hit | B Hit | A MRR | B MRR | recovered | Hit 提升 | MRR 提升 |
|---|---|---|---|---|---|---|---|
| dense | 100.0% | 100.0% | 0.9606 | 0.9606 | 0 | +0.0pp (+0.0%) | +0.0pp (+0.0%) <!-- SUMMARY:dense --> |
| bm25 | 98.2% | 100.0% | 0.9273 | 0.9636 | 1 | +1.8pp (+1.9%) | +3.6pp (+3.9%) <!-- SUMMARY:bm25 --> |

> A = 一阶段朴素 top-n；B = 一阶段取宽候选 → rerank → top-n。
> recovered = B 相比 A 把原本 miss 的 query 救成 hit 的数量 —— 弱一阶段(bm25)下应明显大于强一阶段(dense)，
> 这正是"rerank 真正有价值"的对照证据。

<!-- STAGE1:dense BEGIN -->
## 强检索器 dense（向量）对照

- 一阶段检索器：`--stage1 dense`；模式 = realistic（A=top-3；B=K_wide(30)→rerank→top-3）
- 一阶段 = dense 向量检索（硅基流动 bge-large-zh-v1.5）。语义强，正确条目几乎总进 top-3，rerank 救援空间小（recovered≈0）。

### 主对比（top-n = 3）

| 指标 | A 朴素 (top-3) | B +rerank (K=30→top-3) | 提升 (B vs A) |
|---|---|---|---|
| Hit | 100.0% | 100.0% | +0.0pp (+0.0%) |
| Recall | 100.0% | 100.0% | +0.0pp (+0.0%) |
| MRR | 0.9606 | 0.9606 | +0.0pp (+0.0%) |

- recovered（B 把 A 漏掉的 query 救成命中）：**0**
- regressed（A 命中但 B 漏掉，理想为 0）：0
- changed（top-3 条目集合发生变化的 query 数）：36

### n sweep（n ∈ {1, 3, 5}）

| top-n | A Hit | B Hit | A MRR | B MRR | recovered | changed |
|---|---|---|---|---|---|---|
| 1 | 92.7% | 92.7% | 0.927 | 0.927 | 0 | 1 |
| 3 | 100.0% | 100.0% | 0.961 | 0.961 | 0 | 36 |
| 5 | 100.0% | 100.0% | 0.961 | 0.961 | 0 | 47 |

> 数字由 `python -m langgraph_cs.eval.run_eval --stage1 dense --mode realistic --write-md` 生成。
<!-- STAGE1:dense END -->

<!-- STAGE1:bm25 BEGIN -->
## 弱检索器 BM25 对照

- 一阶段检索器：`--stage1 bm25`；模式 = realistic（A=top-3；B=K_wide(30)→rerank→top-3）
- 一阶段 = BM25 词法/稀疏检索（本地，不连网）。BM25 对"换了说法"的 query 召回弱，正确条目常被漏出 top-n，给 rerank 留出救援空间 —— 这才看得出 rerank 的真实价值。

### 主对比（top-n = 3）

| 指标 | A 朴素 (top-3) | B +rerank (K=30→top-3) | 提升 (B vs A) |
|---|---|---|---|
| Hit | 98.2% | 100.0% | +1.8pp (+1.9%) |
| Recall | 98.2% | 100.0% | +1.8pp (+1.9%) |
| MRR | 0.9273 | 0.9636 | +3.6pp (+3.9%) |

- recovered（B 把 A 漏掉的 query 救成命中）：**1**
- regressed（A 命中但 B 漏掉，理想为 0）：0
- changed（top-3 条目集合发生变化的 query 数）：50

### n sweep（n ∈ {1, 3, 5}）

| top-n | A Hit | B Hit | A MRR | B MRR | recovered | changed |
|---|---|---|---|---|---|---|
| 1 | 87.3% | 92.7% | 0.873 | 0.927 | 3 | 3 |
| 3 | 98.2% | 100.0% | 0.927 | 0.964 | 1 | 49 |
| 5 | 98.2% | 100.0% | 0.927 | 0.964 | 1 | 51 |

> 数字由 `python -m langgraph_cs.eval.run_eval --stage1 bm25 --mode realistic --write-md` 生成。
<!-- STAGE1:bm25 END -->

> Hit = 命中率（top-n 含至少一个正确条目的 query 占比）；
> Recall = 平均覆盖正确条目的比例；MRR = 第一个命中条目排名倒数的均值；
> recovered = B 相比 A 把原本 miss 的 query 救成 hit 的数量。
