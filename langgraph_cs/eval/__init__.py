"""
eval —— RAG 检索质量评测（条目级 item_id）。

本包做一件事：用一份固定的 QA 测试集（dataset.json），对比"朴素 vs rerank"两种检索
方式的检索层指标，且按"条目级 item_id"评估（比按文件名更有判别力）。

两种对比模式（run_eval.py --mode，默认 realistic）：
  - realistic（头条对比）：A 朴素 = 向量检索直接取 top-n（k=n）；
    B = 向量检索取较宽候选 K_wide（默认 30）→ rerank 精排 → 取 top-n。
    体现 rerank 从更宽候选里把朴素 top-n 漏掉的正确条目"捞"回来的真实价值。
  - subset（保留参考）：A = top-k；B = 对同一批 top-k rerank 后取 top-n（n≤k）。

指标都在检索层（不依赖 LLM 生成答案，跑起来便宜、可复现），且按 item_id 计算：
  - Hit@n：top-n 里只要有一个 item_id 命中 relevant_ids 就算这条 query 命中（0/1）。
  - Recall@n：top-n 命中的 item_id 占该 query 全部 relevant_ids 的比例（去重覆盖率）。
  - MRR：第一个命中条目的排名倒数（1/rank），衡量"命中得多靠前"。
  - recovered：B 相比 A 把原本 miss 的 query 救成 hit 的数量（rerank 的核心价值）。

命中判定：一律用 retrieved chunk 的 metadata["item_id"]（形如 billing-03）是否落在该
query 的 relevant_ids 里。chunk 按"每个 ### 条目 = 一个 chunk"切出，item_id 即条目
标题方括号里的 "<domain>-<NN>"，天然可溯源到具体条目。

入口：python -m langgraph_cs.eval.run_eval
"""
