# langgraph_cs —— LangGraph 客服 Agent（阶段 2 骨架 + 阶段 1 RAG）

用 **LangGraph** 重写的最小可跑客服 Agent，作为秋招简历项目的起点。
参照本仓库 EchoMind（手写 anthropic SDK）的架构，但改用 LangGraph 的原生抽象，重点是**讲得清每一行**。

## 这张图在做什么

```
START → intent(意图识别) → rag(检索知识库) → agent(按意图+检索结果应答) → END
                                  ↑
                  编译时挂 MemorySaver(checkpointer)，靠 thread_id 记住多轮上下文
```

`rag_node` 对 greeting/other 意图早退（不检索），其余意图先从知识库召回 top-k，
可选地再用 rerank 精排截 top-n，写进 `state["retrieved_docs"]` 供 agent 引用。

## 怎么跑

```bash
# 1. 装依赖（可复用仓库根的 .venv）
pip install -r langgraph_cs/requirements.txt

# 2. 配 key
cp langgraph_cs/.env.example langgraph_cs/.env
#   编辑 .env，填入 DEEPSEEK_API_KEY

# 3. 从仓库根目录运行（注意是 -m 模块方式，保证 import 路径正确）
python -m langgraph_cs.main
```

试试多轮记忆：先说「我叫小明」，再问「我叫什么名字」，看它是否记得 —— 记得就说明 checkpointer 生效了。

## RAG 检索链路（朴素 vs rerank）

### 链路组成

```
FAQ 文件(data/faq/*.md, 121 条 ### 条目)
   └─切块(每个 ### [item_id] 条目 = 1 chunk, metadata={source,item_id})
      └─embedding(硅基流动 bge-large-zh-v1.5, 1024 维, OpenAI 兼容)
         └─灌入本地 Chroma(data/chroma_rag/, collection=cs_faq)
            └─检索时: 向量粗排 top-k ──(可选)── rerank(bge-reranker-v2-m3) 精排 top-n
```

- embedding/rerank 都走**硅基流动**（`base_url=https://api.siliconflow.cn/v1`，需单独 `SILICONFLOW_API_KEY`）；对话仍用 DeepSeek。
- rerank 走硅基流动自有 `/v1/rerank`（非 OpenAI 协议），用 httpx 自封装（`rag/rerank.py`）。
- `rag_node` 用 `RAG_USE_RERANK` 开关切换两种检索（默认开；设 `0/false/no` 关）。

### 怎么跑评测

评测对比 **(A) 朴素向量检索 top-k** 与 **(B) 粗排 top-k → rerank 取 top-n** 的检索质量。

```bash
# 0. 配 key：在 .env 里填好 SILICONFLOW_API_KEY（embedding/rerank 都会真实联网、消耗额度）

# 1. 灌库（一次即可；会真实调 embedding）
langgraph_cs/.venv/bin/python -m langgraph_cs.scripts.ingest_faq

# 2. 跑评测（顺序执行、不并发，护住 rerank RPM）
langgraph_cs/.venv/bin/python -m langgraph_cs.eval.run_eval --k 10 --top-n 3
#   --stage1 dense  一阶段=向量检索（默认；需先灌库）
#   --stage1 bm25   一阶段=BM25 词法检索（本地、不连网；只有 B 路 rerank 联网）
#   --limit N  只跑前 N 条（先小规模试跑/省额度）
#   --write-md 额外把结果写到 eval/results.md（dense / bm25 各占一节，互不覆盖）
#   --self-test 离线自测指标算法（不发网络请求）
```

> **为什么加 BM25 这个"弱一阶段检索器"？** dense 向量检索（bge-large-zh-v1.5）在这份短 FAQ 上太强，
> 正确条目几乎总进 top-3，rerank 几乎无救援空间（Hit@3 100% vs 100%，recovered≈0）。
> 换上 BM25 词法检索——它靠**字面词项重合**打分、不懂语义，对"换了说法"的 query 召回弱、会把正确条目
> 漏出 top-n——才给 rerank 留出救援空间，得到"rerank 真正有价值"的对照。BM25 检索纯本地，
> 离线就能验证它的 miss（`python -m langgraph_cs.rag.bm25`）。

测试集在 `eval/dataset.json`（55 条 QA，覆盖账户/账单/订单/技术/会员五类，各 11 条）。每条形如：

```json
{ "question": "登录密码忘了进不去，怎么重新弄个新的？",
  "relevant_ids": ["account-01"],
  "expected_keywords": ["忘记密码", "验证码", "重置"] }
```

question 故意**换种说法**（不照抄 FAQ 标题），才测得出检索泛化能力；命中判定看检索到的
chunk 的 `metadata["item_id"]`（条目级，121 条各有唯一 id）是否落在 `relevant_ids`。

### 三个检索层指标

| 指标 | 含义 |
|---|---|
| **Hit@k** | top 结果里只要有一篇命中正确来源就算命中（0/1），均值即命中率 |
| **Recall@k** | top 结果覆盖到的正确来源占该 query 全部正确来源的比例（去重，不让重复 chunk 虚高） |
| **MRR** | 第一篇命中文档的排名倒数 1/rank 的均值，衡量"正确文档排得多靠前" |

### 实验结论（真实数字，121 条 FAQ / 55 条测试集 / K_wide=30）

完整数字以 [`eval/results.md`](eval/results.md) 为准（`run_eval --write-md` 生成）。核心发现：
**rerank 的收益取决于一阶段检索器的强弱**——强检索器已饱和时几乎无增益，弱检索器下才显著。

**汇总（top-n=3）**

| 一阶段检索器 | A 朴素 Hit | B +rerank Hit | A MRR | B MRR | recovered | 结论 |
|---|---|---|---|---|---|---|
| dense（bge-large-zh，强） | 100.0% | 100.0% | 0.9606 | 0.9606 | 0 | rerank ≈ 0 |
| **BM25（词法，弱）** | 98.2% | **100.0%** | 0.9273 | **0.9636** | 1 | **rerank 显著** |

**弱一阶段 BM25 的 n-sweep（rerank 价值最清楚）**

| top-n | A 朴素 Hit | B +rerank Hit | A MRR | B MRR | recovered |
|---|---|---|---|---|---|
| **1** | 87.3% | **92.7%** | 0.873 | **0.927** | **3** |
| 3 | 98.2% | 100.0% | 0.927 | 0.964 | 1 |

- 为什么 dense 看不出 rerank 价值：bge-large-zh 在短 FAQ 上太强，正确条目几乎总在 top-3，rerank 无救援空间（recovered=0）。
- BM25 靠字面词项打分、不懂语义，对"换了说法"的 query 召回弱（如「登录密码忘了进不去…」把正确条目 `account-01` 排到第 9 位），正是 rerank 该捞回的——B 路从 K_wide=30 候选里把它精排进 top-n。

> **简历可这样写**："构建 RAG 检索评测（121 条 FAQ / 55 条改写 query / 条目级命中判定），量化 rerank 的 ROI：
> 在强 dense 检索器上 rerank 增益≈0（已饱和），在弱 BM25 词法检索器上 rerank 将 Hit@1 从 87.3% 提升到 92.7%、
> MRR 从 0.873 提升到 0.927，救回 3 条原本漏检的 query。结论：rerank 价值取决于一阶段召回质量，而非默认收益。"
> —— 这种"测了 + 讲得清何时该用"的数据驱动判断，比空喊"加了 rerank 提升 X%"更有说服力。

## 文件导览（对照 EchoMind 学习）

| 文件 | 作用 | 对照 EchoMind |
|---|---|---|
| `state.py` | 图的状态 `CSState`（TypedDict + `add_messages`） | 一堆 Request/Result dataclass |
| `nodes/intent.py` | 意图识别节点（教学版单路） | `core/intent_recognizer.py`（三路融合） |
| `nodes/agent.py` | 按意图选 system prompt 应答 | `agents/agent_orchestrator.py` 各 Agent |
| `graph.py` | 组装 StateGraph + 挂 checkpointer | Orchestrator 的 run 编排 + redis 会话 |
| `main.py` | CLI 入口 | `api/main.py` 的 `_cli()` |

## 下一步（路线图）

- **阶段 3**：`add_edge` 换成 `add_conditional_edges`，按意图路由到多个真正的 Agent 节点 + 失败降级
- **阶段 1/RAG**（已完成）：在 agent 前加 `rag_node`，用 LangChain Retriever 检索知识库，做朴素 vs rerank 的指标对比（见上方「RAG 检索链路」）
- **阶段 4**：`interrupt` 实现 human-in-the-loop（低置信度转人工）；接 LangSmith 自动评测
- **持久化**：`MemorySaver` → `SqliteSaver`/`RedisSaver`
