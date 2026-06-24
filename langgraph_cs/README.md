# langgraph_cs —— LangGraph 客服 Agent（阶段 3 多 Agent 路由 + RAG）

用 **LangGraph** 重写的最小可跑客服 Agent，作为秋招简历项目的起点。
参照本仓库 EchoMind（手写 anthropic SDK）的架构，但改用 LangGraph 的原生抽象，重点是**讲得清每一行**。

## 这张图在做什么（阶段 3：多 Agent 条件路由 + human-in-the-loop）

```
                                          ┌─[technical]──────→ technical_agent ─┐
                                          │                                     │
START → intent → rag ─(route_by_intent)──┼─[billing]────────→ billing_agent  ─┤
                                          │                                     ├→ END
                          低置信/其余/未知 ─┼──────────────────→ general_agent  ─┤
                                          │                                     │
                                          └─[escalation]─────→ escalation ──────┘
                                                                 │（interrupt 暂停等人工 → resume）
                  编译时挂 MemorySaver(checkpointer)，靠 thread_id 记住多轮上下文 + 保存中断点
```

阶段 1/2 是「一个 `agent_node` + 按意图换 system prompt」；阶段 3 升级为**真正的多 Agent 编排**：

- **意图路由**：`rag → agent` 直连换成 `add_conditional_edges`，路由函数 `route_by_intent` 按
  `state["intent"]` 把请求分流到 `technical_agent` / `billing_agent` / `general_agent` / `escalation`。
- **失败降级（两层）**：
  1. *低置信度降级*——路由函数里 `confidence < 0.5` 时不管什么意图一律落到 `general_agent`（低置信不交给专职 Agent 自作主张）。
  2. *运行时降级*——专职节点内部对 LLM 调用 `try/except`，单个 Agent 报错时返回兜底回复，不让整图崩。
- **human-in-the-loop**：意图为 `escalation`（用户要求转人工）时走 `escalation` 节点，用 LangGraph 原生
  `interrupt()` **暂停整张图**抛出坐席提示；CLI 读人工输入后用 `Command(resume=...)` 恢复，把人工回复写回对话。
  依赖 checkpointer（MemorySaver）+ thread_id 保存/恢复中断点。

`rag_node` 行为不变：对 greeting/other 意图早退（不检索），其余意图先从知识库召回 top-k，
可选地再用 rerank 精排截 top-n，写进 `state["retrieved_docs"]`，每个专职 Agent 都会带上这些参考资料作答。

试一句「我要转人工」即可体验暂停/恢复：程序会要求你以坐席身份输入一句回复，再作为最终回复继续。

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

## Web 界面（阶段 5：浏览器演示 + 决策可视化 + HITL）

CLI 之外加了一个**可演示的 Web 聊天界面**：FastAPI 把同一张图（`build_graph()`）包成 HTTP 接口 + 原生
HTML/JS 单页（无 Node/构建链）。代码全在 `langgraph_cs/web/`，**只做适配层，不改图/节点/state 核心逻辑**。
面试时直接打开浏览器演示，比 CLI 直观：边打字边出字、每条回复带「意图 / 路由 / 知识库来源」标签、转人工时
界面真的暂停等坐席输入。

```bash
# 1. 装依赖（已含 fastapi / uvicorn）
pip install -r langgraph_cs/requirements.txt
# 2. 配 key（同 CLI，复用 langgraph_cs/.env 的 DEEPSEEK_API_KEY；RAG 来源需先灌库）
# 3. 一条命令起服务（默认 127.0.0.1:8000；可用 CS_WEB_HOST / CS_WEB_PORT 覆盖）
python -m langgraph_cs.web
#   浏览器打开 http://127.0.0.1:8000
```

演示要点：

- **决策可视化**：每条机器人消息上方一行小标签 chips —— 🎯 意图(含置信度)、🤖 路由到的 Agent、📚 引用的知识库条目。
- **流式打字机**：DeepSeek 的 token 经 SSE 逐字追加（`stream_mode=["updates","messages"]` 同时拿节点状态更新 + LLM token）。
- **转人工（human-in-the-loop）**：说一句「转人工」，界面顶部弹出「🧑‍💼 已转人工」横幅、底部输入框切坐席皮肤，
  你以坐席身份输入并发送 → 走 `/api/resume`（`Command(resume=...)`）恢复图 → 坐席回复作为机器人消息显示并退出坐席模式。
- **多轮 / 新会话**：thread_id 存 localStorage 维持多轮；右上角「新会话」按钮重置 thread_id 清空对话。

接口协议（SSE，`text/event-stream`，每条 `data: <json>\n\n`，带 `type` 字段）：

| 事件 `type` | 载荷 | 何时发 |
|---|---|---|
| `meta` | `{intent, confidence}` | intent 节点后 |
| `rag` | `{sources: [...]}` | rag 节点后（无检索则空） |
| `route` | `{agent}` | 路由到的专职节点名 |
| `token` | `{text}` | 专职 Agent 增量 token（打字机） |
| `interrupt` | `{prompt, user_message}` | 命中转人工，图已暂停，前端切坐席模式 |
| `done` | `{escalated}` | 本轮收尾 |
| `error` | `{message}` | 任意异常（不让连接 500 崩） |

> 截图占位：![Web 界面截图](./docs/web-screenshot.png) ·
> ![转人工坐席模式截图](./docs/web-seat-mode.png) ——（演示时各截一张，放到 `langgraph_cs/docs/` 后替换路径）

离线验证（不联网、不发真实 LLM 调用）：用 `fastapi.testclient.TestClient` + **mock 掉图的 `stream`/`get_state`**
构造假的 updates/messages/interrupt 序列，断言 SSE 事件拼装与 interrupt→resume 流程：

```bash
langgraph_cs/.venv/bin/python -m langgraph_cs.web.tests.test_server_offline
```

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

## 持久化 + 可观测/评测（阶段 4）

把项目从「能跑的 demo」收尾成「状态持久 + 可观测 + 可量化」的闭环，三块：

### 1. 持久化：MemorySaver → SqliteSaver（进程重启后仍记得上文）

编译时的 checkpointer 从硬编码改成**可切换工厂** `make_checkpointer()`，按环境变量 `CS_CHECKPOINT` 选：

| `CS_CHECKPOINT` | 后端 | 行为 |
|---|---|---|
| `memory`（默认） | `MemorySaver` | 进程内存版，零依赖、**重启即丢**（= 阶段 1/2/3 原行为，不破坏） |
| `sqlite` | `SqliteSaver` | 落 `data/checkpoints.sqlite`，**进程重启后同一 thread_id 仍记得上文** |

```bash
# 用 SQLite 持久化跑 CLI：先说"我叫小明"，退出后再起同样命令、问"我叫什么"，它仍记得
CS_CHECKPOINT=sqlite langgraph_cs/.venv/bin/python -m langgraph_cs.main

# 离线证明跨进程持久化（不调 LLM）：本进程写检查点 -> 全新子进程读回 -> 断言 messages 仍在
langgraph_cs/.venv/bin/python -m langgraph_cs.scripts.verify_persistence
```

> **连接生命周期（关键点）**：`SqliteSaver.from_conn_string(path)` 是**上下文管理器**，离开 `with` 块连接就关，
> 只适合"用完即走"的脚本。CLI 是**长驻进程**，编译后还要在后续多轮 invoke 里反复读写检查点，
> 所以正确写法是**自建并持有** `sqlite3.connect(path, check_same_thread=False)` 后交给 `SqliteSaver(conn)`，
> 连接由进程持有到退出（见 `graph.make_sqlite_checkpointer`）。`checkpoints.sqlite` 是运行时产物，已 gitignore。

### 2. 本地端到端答案质量评测（LLM-as-judge，不上云、保底）

`run_eval` 评的是**检索层**（Hit/Recall/MRR）；这一层评**端到端答案质量**：把一组客服问题真的跑过
`build_graph()` 拿最终回复，再让 DeepSeek 当 judge 按「准确性 / 有用性」各 1~5 打分，输出逐条分 + 平均分。

```bash
# 真实评测（跑图 + DeepSeek judge，联网消耗额度）
langgraph_cs/.venv/bin/python -m langgraph_cs.eval.answer_eval
langgraph_cs/.venv/bin/python -m langgraph_cs.eval.answer_eval --limit 3   # 只跑前 3 条省额度
langgraph_cs/.venv/bin/python -m langgraph_cs.eval.answer_eval --write-md  # 顺手写 eval/answer_results.md
# 离线自测：mock judge 验证打分解析（JSON/围栏/越界/正则兜底/失败）+ 聚合逻辑，不连网
langgraph_cs/.venv/bin/python -m langgraph_cs.eval.answer_eval --self-test
```

问题集在 `eval/answer_dataset.json`（13 条，覆盖技术/账单/通用）。打分解析有多重兜底：
标准 JSON → 剥代码围栏 → 正则抠数字 → 解析失败给中性分 3 并打日志，一次 judge 抽风不会带偏均值。

### 3. LangSmith：节点级 trace + 数据集 + evaluate（需 key，上 LangChain 云）

在 `.env` 里设好三个变量（参考 `.env.example`），**LangGraph 会自动**把每次 `invoke` 的节点级 trace
（intent/rag/各 agent 的输入输出）上传到 LangSmith，**无需改图代码**：

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=ls-...你的真实 key...   # 到 https://smith.langchain.com 注册拿
LANGSMITH_PROJECT=echomind-langgraph
```

`eval/langsmith_eval.py` 用 langsmith SDK 创建/复用一个数据集（来自上面的问题集）+ 一个 LLM-judge
evaluator + 调 `client.evaluate()` 跑端到端评测，结果与逐条 trace 都在网页上看：

```bash
# 真跑（需 key、联网、会上传到 LangChain 云）
langgraph_cs/.venv/bin/python -m langgraph_cs.eval.langsmith_eval
# 离线 dry-run（不上传、不调 LLM，只验证 import + 接线正确）
langgraph_cs/.venv/bin/python -m langgraph_cs.eval.langsmith_eval --dry-run
```

> 本地评测（第 2 块）是 LangSmith evaluate 的**保底版**：缺 key、不联网也能量化答案质量；
> LangSmith（第 3 块）多了云端 trace 可视化 + 数据集版本管理 + 实验对比。两者 judge 逻辑复用同一套。

## 文件导览（对照 EchoMind 学习）

| 文件 | 作用 | 对照 EchoMind |
|---|---|---|
| `state.py` | 图的状态 `CSState`（TypedDict + `add_messages`，阶段 3 加 `escalated` 标记） | 一堆 Request/Result dataclass |
| `nodes/intent.py` | 意图识别节点（教学版单路，阶段 3 加 `escalation` 意图） | `core/intent_recognizer.py`（三路融合） |
| `nodes/agent.py` | 多个专职 Agent 节点（technical/billing/general），共享 `_run_agent` 出字 + 失败兜底 | `agents/agent_orchestrator.py` 的 TechnicalAgent/BillingAgent/GeneralAgent |
| `nodes/router.py` | 路由函数 `route_by_intent`（意图路由 + 低置信度降级） | Orchestrator 的 `_route` / 降级路由 |
| `nodes/escalation.py` | 转人工节点：`interrupt()` 暂停等人工，resume 后写回回复 | `_needs_escalation` 关键词检测（占位，未真正阻塞） |
| `graph.py` | 组装 StateGraph + `add_conditional_edges` + checkpointer 工厂（memory/sqlite 可切换） | Orchestrator 的 run 编排 + 三层路由 + redis 会话 |
| `main.py` | CLI 入口（支持 interrupt → 人工输入 → resume 循环；读 `CS_CHECKPOINT` 选后端） | `api/main.py` 的 `_cli()` |
| `web/server.py` | Web 适配层：FastAPI 把图包成 `/api/chat`·`/api/resume`（SSE 流式）+ 提供静态聊天页；复用 `build_graph()`，不改图核心 | `api/main.py` 的 FastAPI（旧 anthropic 应用，不复用） |
| `web/static/` | 原生 HTML/JS/CSS 单页：决策可视化 chips + 打字机 + 坐席模式（HITL），无 Node/构建链 | （新增能力，EchoMind 无对应） |
| `scripts/verify_persistence.py` | 离线证明 SqliteSaver 跨进程持久化（本进程写 → 子进程读回断言） | redis 会话持久化的验证 |
| `eval/answer_eval.py` | 端到端答案质量评测（跑图 + DeepSeek judge 打分，本地保底，不上云） | （新增能力，EchoMind 无对应） |
| `eval/langsmith_eval.py` | LangSmith trace + 数据集 + `evaluate` 端到端评测（需 key、上云） | （新增能力，EchoMind 无对应） |

### 对照 EchoMind 的 `agent_orchestrator.py`

EchoMind 在 `agents/agent_orchestrator.py` 里**手写**了三层路由：意图路由（按 `IntentCategory` 选专属 Agent）、
性能路由（同类多实例按 `routing_score()` 选最优）、降级路由（专属 Agent 不可用/失败 → `GeneralAgent`），
升级靠 `_needs_escalation` 关键词检测（`转人工/人工客服/escalate/无法处理`）置 `escalate` 标志（**仅占位、未真正阻塞等人工**）。

langgraph_cs 用 LangGraph **原生能力**表达同一套思路，少写很多胶水代码：

| EchoMind（手写） | langgraph_cs（LangGraph 原生） |
|---|---|
| Orchestrator 里 `if intent == ...` 选 Agent | `add_conditional_edges` + `route_by_intent` |
| `_execute_with_fallback` try/except 降级到 General | 路由函数低置信度降级 + 专职节点内部 try/except |
| `_needs_escalation` 置 `escalate` 标志（不阻塞） | `escalation` 节点 `interrupt()` **真正暂停 + 等人工 + `Command(resume=...)` 恢复** |
| redis 手写会话管理 | 编译时挂 `MemorySaver` checkpointer，按 thread_id 自动维持 |
| 性能路由（routing_score 选优） | **暂未做**，留作扩展点（同类多 Agent 实例时再加） |

## 下一步（路线图）

- **阶段 1/RAG**（已完成）：在 agent 前加 `rag_node`，用 LangChain Retriever 检索知识库，做朴素 vs rerank 的指标对比（见上方「RAG 检索链路」）
- **阶段 3**（已完成）：`add_edge` 换成 `add_conditional_edges`，按意图路由到多个真正的 Agent 节点 + 失败降级（低置信 + 运行时）+ `interrupt` 实现 human-in-the-loop 转人工（见上方路由图）
- **性能路由（扩展点）**：同类多 Agent 实例时按 `routing_score` 选最优（对照 EchoMind 性能路由，本阶段先不做）
- **阶段 4**（已完成）：持久化（`MemorySaver` → `SqliteSaver`，`CS_CHECKPOINT` 可切换，跨进程记忆）+ 本地端到端答案质量评测（DeepSeek judge）+ LangSmith 节点级 trace/数据集/evaluate（见上方「持久化 + 可观测/评测」）
- **持久化（扩展点）**：`SqliteSaver` → `RedisSaver`/`PostgresSaver`（生产级、分布式，本阶段不做）
