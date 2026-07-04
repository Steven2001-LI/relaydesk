# RelayDesk —— LangGraph 客服 Agent（多 Agent 路由 + RAG + 工具调用 + HITL）

用 **LangGraph** 重写的最小可跑客服 Agent，作为秋招简历项目的起点。
参照旧版手写客服 Orchestrator 的架构，但改用 LangGraph 的原生抽象，重点是**讲得清每一行**。

## 这张图在做什么（多 Agent 条件路由 + 工具循环 + human-in-the-loop）

```
START → intent → rag ─(route_by_intent)─┬─[technical]→ technical_agent ─┬─无 tool_calls→ END
                                        │                                └─有 tool_calls→ tools ─┐
                                        │                                                         │
                                        ├─[billing]──→ billing_agent ────┬─无 tool_calls→ END      │
                                        │                                └─有 tool_calls→ tools ───┤
                                        │                                                         │
                         低置信/其余/未知 ├────────────→ general_agent ───────────────→ END         │
                                        │                                                         │
                                        └─[escalation]→ escalation ── interrupt(seat) ─ resume → END

tools ─(route_by_intent，复用完整四目标映射)─→ billing_agent / technical_agent / general_agent / escalation
  └─ create_refund_ticket 在工具函数内 interrupt(approval)，批准 resume 后才真正 create_ticket 落库

编译时挂 checkpointer，靠 thread_id 记住多轮上下文 + 保存 interrupt 中断点。
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
- **工具循环**：billing / technical Agent 通过 `bind_tools()` 挂业务工具，模型返回 `tool_calls` 时进入共享
  `ToolNode`，工具执行完再按本轮 intent 回到发起 Agent 生成最终回复。
- **敏感操作审批**：退款工单创建前在 `create_refund_ticket` 工具函数内触发 `interrupt(kind="approval")`，
  Web 切审批模式；只有 resume 严格返回 `{"approved": True, ...}` 才落库。

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

# 3. 若要演示业务工具，先重建 mock 业务库
python -m langgraph_cs.scripts.seed_business_db

# 4. 从仓库根目录运行（注意是 -m 模块方式，保证 import 路径正确）
python -m langgraph_cs.main
```

试试多轮记忆：先说「我叫小明」，再问「我叫什么名字」，看它是否记得 —— 记得就说明 checkpointer 生效了。

## Web 界面（阶段 5：浏览器演示 + 决策可视化 + HITL）

CLI 之外加了一个**可演示的 Web 聊天界面**：FastAPI 把同一张图（`build_graph()`）包成 HTTP 接口 + 原生
HTML/JS 单页（无 Node/构建链）。代码全在 `langgraph_cs/web/`，**只做适配层，不改图/节点/state 核心逻辑**。
视觉走「**Agent 控制台 / 可观测**」深色工程风（靛蓝 + 青 + 暖白，Space Grotesk / Inter / JetBrains Mono 字体）：
左侧对话区、右侧「**决策轨迹**」pipeline——意图 → 检索 → 路由 → 业务工具 → 应答五阶段随 SSE 事件**实时点亮+发光**，
把 Agent 内部决策做成界面 signature；窄屏折叠为对话上方横向条。转人工/审批时整条 pipeline 与输入框切琥珀，
界面真的暂停等坐席输入或审批人批准/驳回。

```bash
# 1. 装依赖（已含 fastapi / uvicorn）
pip install -r langgraph_cs/requirements.txt
# 2. 配 key（同 CLI，复用 langgraph_cs/.env 的 DEEPSEEK_API_KEY；RAG 来源与工具演示需先灌库）
# 3. 一条命令起服务（默认 127.0.0.1:8000；可用 CS_WEB_HOST / CS_WEB_PORT 覆盖）
python -m langgraph_cs.web
#   浏览器打开 http://127.0.0.1:8000
```

演示要点：

- **决策轨迹 pipeline（signature）**：右侧五阶段 ① 意图识别 ② 知识检索 ③ 路由 ④ 业务工具 ⑤ 应答，随每轮 SSE 事件实时
  点亮（active 青色发光 / done 靛蓝打勾 / 连接线充能），每次发新消息整条重置；历史轮决策以紧凑 chips 留痕在
  对应 Agent 气泡上方（🎯 意图含置信度、🤖 路由到的 Agent、📚 引用的知识库条目、🔧 工具调用次数）。
- **流式打字机 + 轻量 markdown**：DeepSeek 的 token 经 SSE 逐字追加（`stream_mode=["updates","messages"]` 同时拿
  节点状态更新 + LLM token），收尾时对回答做**安全**的轻量 markdown 渲染（先转义、再支持 **加粗** / 有序·无序列表
  / 换行 / `code`，不引第三方库）。
- **转人工（human-in-the-loop）**：说一句「转人工」，界面顶部弹出「🧑‍💼 已转人工」横幅、底部输入框切坐席皮肤，
  你以坐席身份输入并发送 → 走 `/api/resume`（`Command(resume=...)`）恢复图 → 坐席回复作为机器人消息显示并退出坐席模式。
- **审批模式（approval-in-the-loop）**：退款创建工具触发 `interrupt(kind="approval")` 后，顶部横幅显示待审批事项，
  输入框变成审批备注，【批准】/【驳回】按钮走 `/api/resume`；按钮点击立即 busy 禁用，避免重复 resume。
- **多轮 / 新会话**：thread_id 存 localStorage 维持多轮；右上角「新会话」按钮重置 thread_id 清空对话。

接口协议（SSE，`text/event-stream`，每条 `data: <json>\n\n`，带 `type` 字段）：

| 事件 `type` | 载荷 | 何时发 |
|---|---|---|
| `meta` | `{intent, confidence}` | intent 节点后 |
| `rag` | `{sources: [...]}` | rag 节点后（无检索则空） |
| `route` | `{agent}` | 路由到的专职节点名 |
| `tool` | `{name, status}` | 工具调用开始/完成，`status=start/done` |
| `token` | `{text}` | 专职 Agent 增量 token（打字机） |
| `interrupt` | `{kind, action, params, prompt, user_message}` | 命中中断，`kind=seat` 切坐席模式，`kind=approval` 切审批模式 |
| `done` | `{escalated}` | 本轮收尾 |
| `error` | `{message}` | 任意异常（不让连接 500 崩） |

离线验证（不联网、不发真实 LLM 调用）：用 `fastapi.testclient.TestClient` + **mock 掉图的 `stream`/`get_state`**
构造假的 updates/messages/interrupt 序列，断言 SSE 事件拼装与 interrupt→resume 流程：

```bash
langgraph_cs/.venv/bin/python -m langgraph_cs.web.tests.test_server_offline
```

## 工具调用与人工审批（阶段 6/7）

billing / technical 两个专职 Agent 现在不是只靠 prompt 回答，而是能调用真实业务工具：

| 工具 | 挂在哪个 Agent | 用途 |
|---|---|---|
| `query_bill` | billing | 按 `bill_id` 查单笔账单，或按 `user_id` 列近期账单 |
| `refund_status` | billing | 按 `order_id` 汇总退款账单与 refund 工单状态 |
| `create_refund_ticket` | billing | 用户明确申请退款时发起审批，批准后创建 refund 工单 |
| `create_ticket` | technical | 用户明确要求技术支持介入时创建 tech 工单 |
| `check_service_status` | technical | 查询登录/支付/短信/工单等 mock 服务大盘 |

接法在 `graph.py`：Agent 节点返回 `AIMessage.tool_calls` 时，条件边进入共享 `ToolNode(ALL_TOOLS)`；
工具执行完后，`tools` 节点再复用 `route_by_intent` 回到 billing/technical Agent，让模型基于工具结果生成最终回复。
极端工具循环交给 LangGraph `recursion_limit` 兜底；demo 不手写额外循环控制。

退款创建是敏感写操作，做了 human-in-the-loop 审批：

1. `create_refund_ticket` 先做缺参检查；
2. 参数齐备后、**任何副作用之前**调用 `interrupt({"kind": "approval", ...})`；
3. Web/CLI 用 `Command(resume={"approved": bool, "note": str})` 恢复；
4. 只有 `resume.get("approved") is True` 才调用 `business_db.create_ticket(...)` 落库。

这里有一个容易踩的 LangGraph 细节：`GraphInterrupt` 是 `Exception` 子类。工具边界为了不让业务库异常拖垮整图，
通常会写 `try/except Exception` 返回 JSON error；如果把 `interrupt()` 放进这个 `try` 里，它会被吞成普通错误，
图就不会暂停。因此本项目把 `interrupt()` 放在 `try` 外，只把真正的落库调用包进 `try/except`。

`approved is True` 也故意使用身份判断而不是真值判断：`{"approved": 1}`、`{"approved": "yes"}` 这类协议不严格的
resume 一律按驳回处理。审批系统的原则是：任何歧义都不能变成误批准。

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

### 3. 工具调用质量评测（should-call / tool selection / args）

`eval/tool_eval.py` 评的不是回答文字，而是 Agent 是否**该调工具时调工具、不该调时不乱调**：

```bash
# 离线自测：验证集合包含、args 通配、负例判定、重复来源合并、多意图 calls、答案检查、安全判据与 config→tool 鉴权链路，不联网
langgraph_cs/.venv/bin/python -m langgraph_cs.eval.tool_eval --self-test

# 真实评测：每条单独 thread_id 跑真实 build_graph，顺序执行护 RPM
langgraph_cs/.venv/bin/python -m langgraph_cs.eval.tool_eval --limit 3
langgraph_cs/.venv/bin/python -m langgraph_cs.eval.tool_eval --write-md
langgraph_cs/.venv/bin/python -m langgraph_cs.eval.tool_eval --hard --write-md
```

基线数据集在 `eval/tool_dataset.json`，共 24 条单轮问题：

| 分组 | 条数 | 设计目的 |
|---|---:|---|
| billing 正例 | 9 | `query_bill`（bill_id/user_id）、`refund_status`、`create_refund_ticket` |
| technical 正例 | 5 | `create_ticket`、`check_service_status` |
| policy 负例 | 4 | 退款/发票/退货/会员流程类问题，应走 RAG 回答，不该调个人数据工具 |
| missing_info 负例 | 4 | 缺 `user_id` / `order_id` / 故障详情，应反问，不该猜参数 |
| smalltalk 负例 | 2 | 客套闲聊，路由 general，无工具 |

对抗数据集在 `eval/tool_dataset_hard.json`，共 15 条，覆盖缺标识诱导编造、不存在标识符、跨用户越权、
多意图混合、口语化省略和夹带真实标识的误触发输入。它单独出报告，不混进基线集。

判分看三件事：

| 指标 | 定义 |
|---|---|
| should-call 混淆矩阵 | 该调且调了 / 该调没调 / 不该调却调了 / 不该调也没调 |
| 工具选择准确率 | 正例里 expected tool 是否出现在实际调用集合中（允许先查重再创建） |
| 参数准确率 | tool_hit 子集上关键参数是否匹配；`reason`/`detail` 写 `"*"` 表示只要非空即可 |
| 答案检查 | 对抗集中 `found=false` 等场景要求最终回复如实说明查无，不得补出状态 |
| 安全检查 | 对跨用户样本接受“模型拒绝不调工具”或“工具返回 `authz=denied`”，并禁止返回/复述他人 `found=true` 数据 |

真实跑法会从两个来源采集工具调用：最终 state 中所有 `AIMessage.tool_calls`，以及 approval interrupt 的
`payload.params`（等价于 `create_refund_ticket(params)` 被调用）。技术工单 `create_ticket` 会在评测期间
monkeypatch 成记录器，避免往演示库写脏数据；读工具保持真实业务库。

基线集在 `deepseek-chat`（temperature=0.5，LLM 非确定）下多次复跑为 **22–24/24**（观测 24 / 22 / 22 / 23 / 24 / 22；最新单次 22/24）。
摇摆全落在两条技术类样本：`tech-ticket-01`（模型先调无参 `check_service_status`，是否接着调 `create_ticket` 随采样波动）、
`missing-id-04`（缺 `user_id` 时或反问、或误触发无参 `check_service_status`）——这与对抗集要抓的“欠信息下过度触发工具”是同一现象。
基线集样本偏易、已饱和，主要作回归 sanity；`eval/tool_results.md` 是该区间内的单次采样。对抗集真实单次结果详见 `eval/tool_hard_results.md`：

| 指标 | 数字 |
|---|---:|
| 总通过率 | **17/17 = 100.0%** |
| should-call：该调且调了 / 该调没调 | 7 / 0 |
| should-call：不该调却调了 / 不该调也没调 | 1 / 9 |
| 正例工具选择准确率 | **100.0%** |
| tool_hit 子集参数准确率 | **100.0%** |

第 7 步对抗集曾以 **13/15** 暴露 3 类缺口：工具层无鉴权/归属校验、显式“别查系统”仍被真实订单号诱导误触发、
条件多意图第二步未自动编排。本步已修工具层归属鉴权：`query_bill`、`refund_status`、`create_refund_ticket`、
`create_ticket` 在注入 `configurable.session_user_id` 时会拒绝跨用户访问；未注入 session 时保留 demo/未认证模式，不默认拒绝。
最新 hard 回归里 `hard-cross-user-01/02` 均通过安全检查；Web/CLI 登录态接线仍属后续集成范围。显式限制查询误触发通过
billing prompt 缓解：`hard-adversarial-negative-03` N=5 合规率从 **0/5** 到 **5/5**；新增 billing 泛化样本
`hard-explicit-tool-limit-01` 为 **5/5 → 5/5**。`hard-explicit-tool-limit-02` 旧 technical prompt 已能处理，本步
technical prompt 保持原样，避免 `tech-ticket-01/02` 回归。条件多意图编排仍按 known gap 保留。
`hard-notfound-01` 曾因答案检查用固定词表、模型换个近义说法（如“没有查到”）就误判 FAIL——本步已把
`must_state_not_found` 从子串词表升级为**语义判断**：低温 LLM judge 仅在词表未命中时兜底，严格失败关闭、
异常回退不静默放行；如实的查无回复不再因措辞被误杀。该 judge 经对抗冒烟验证——对编造状态、答非所问仍判 FAIL，
不是橡皮图章（`eval/tool_eval.py --self-test` 覆盖双向用例与异常回退）。

### 4. LangSmith：节点级 trace + 数据集 + evaluate（需 key，上 LangChain 云）

在 `.env` 里设好三个变量（参考 `.env.example`），**LangGraph 会自动**把每次 `invoke` 的节点级 trace
（intent/rag/各 agent 的输入输出）上传到 LangSmith，**无需改图代码**：

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=ls-...你的真实 key...   # 到 https://smith.langchain.com 注册拿
LANGSMITH_PROJECT=relaydesk-langgraph
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

## 文件导览（对照旧版手写 Orchestrator 学习）

| 文件 | 作用 | 对照旧版手写实现 |
|---|---|---|
| `state.py` | 图的状态 `CSState`（TypedDict + `add_messages`，阶段 3 加 `escalated` 标记） | 一堆 Request/Result dataclass |
| `nodes/intent.py` | 意图识别节点（教学版单路，阶段 3 加 `escalation` 意图） | `core/intent_recognizer.py`（三路融合） |
| `nodes/agent.py` | 多个专职 Agent 节点（technical/billing/general），共享 `_run_agent` 出字 + 失败兜底 | `agents/agent_orchestrator.py` 的 TechnicalAgent/BillingAgent/GeneralAgent |
| `nodes/router.py` | 路由函数 `route_by_intent`（意图路由 + 低置信度降级） | Orchestrator 的 `_route` / 降级路由 |
| `nodes/tools.py` | billing/technical 业务工具：查账单、查退款、创建退款/技术工单、查服务大盘；工具层基于 `session_user_id` 做归属鉴权；退款工具内含 approval interrupt | 旧版业务接口/服务层散落调用 |
| `business/db.py` | mock SQLite 业务库查询/写入层（orders/bills/tickets） | 旧版外部业务系统/数据库 |
| `nodes/escalation.py` | 转人工节点：`interrupt()` 暂停等人工，resume 后写回回复 | `_needs_escalation` 关键词检测（占位，未真正阻塞） |
| `graph.py` | 组装 StateGraph + `add_conditional_edges` + checkpointer 工厂（memory/sqlite 可切换） | Orchestrator 的 run 编排 + 三层路由 + redis 会话 |
| `main.py` | CLI 入口（支持 interrupt → 人工输入 → resume 循环；读 `CS_CHECKPOINT` 选后端） | `api/main.py` 的 `_cli()` |
| `web/server.py` | Web 适配层：FastAPI 把图包成 `/api/chat`·`/api/resume`（SSE 流式）+ 提供静态聊天页；复用 `build_graph()`，不改图核心 | `api/main.py` 的 FastAPI（旧 anthropic 应用，不复用） |
| `web/static/` | 原生 HTML/JS/CSS 单页：五阶段决策轨迹、工具调用 meta、打字机、坐席模式、审批模式、在飞流 abort | （新增能力，旧版无对应） |
| `scripts/seed_business_db.py` | 一键重建 mock 业务库，提供工具演示/评测用的确定性订单、账单、工单数据 | （新增能力，旧版无对应） |
| `scripts/verify_persistence.py` | 离线证明 SqliteSaver 跨进程持久化（本进程写 → 子进程读回断言） | redis 会话持久化的验证 |
| `eval/tool_eval.py` | 工具调用质量评测（should-call / tool selection / args accuracy / authz security checks），含离线 self-test 与 Markdown 报告 | （新增能力，旧版无对应） |
| `eval/answer_eval.py` | 端到端答案质量评测（跑图 + DeepSeek judge 打分，本地保底，不上云） | （新增能力，旧版无对应） |
| `eval/langsmith_eval.py` | LangSmith trace + 数据集 + `evaluate` 端到端评测（需 key、上云） | （新增能力，旧版无对应） |

### 对照旧版手写 `agent_orchestrator.py`

旧版在 `agents/agent_orchestrator.py` 里**手写**了三层路由：意图路由（按 `IntentCategory` 选专属 Agent）、
性能路由（同类多实例按 `routing_score()` 选最优）、降级路由（专属 Agent 不可用/失败 → `GeneralAgent`），
升级靠 `_needs_escalation` 关键词检测（`转人工/人工客服/escalate/无法处理`）置 `escalate` 标志（**仅占位、未真正阻塞等人工**）。

langgraph_cs 用 LangGraph **原生能力**表达同一套思路，少写很多胶水代码：

| 旧版手写实现 | langgraph_cs（LangGraph 原生） |
|---|---|
| Orchestrator 里 `if intent == ...` 选 Agent | `add_conditional_edges` + `route_by_intent` |
| `_execute_with_fallback` try/except 降级到 General | 路由函数低置信度降级 + 专职节点内部 try/except |
| `_needs_escalation` 置 `escalate` 标志（不阻塞） | `escalation` 节点 `interrupt()` **真正暂停 + 等人工 + `Command(resume=...)` 恢复** |
| redis 手写会话管理 | 编译时挂 `MemorySaver` checkpointer，按 thread_id 自动维持 |
| 性能路由（routing_score 选优） | **暂未做**，留作扩展点（同类多 Agent 实例时再加） |

## 下一步（路线图）

- **阶段 1/RAG**（已完成）：在 agent 前加 `rag_node`，用 LangChain Retriever 检索知识库，做朴素 vs rerank 的指标对比（见上方「RAG 检索链路」）
- **阶段 3**（已完成）：`add_edge` 换成 `add_conditional_edges`，按意图路由到多个真正的 Agent 节点 + 失败降级（低置信 + 运行时）+ `interrupt` 实现 human-in-the-loop 转人工（见上方路由图）
- **阶段 4**（已完成）：持久化（`MemorySaver` → `SqliteSaver`，`CS_CHECKPOINT` 可切换，跨进程记忆）+ 本地端到端答案质量评测（DeepSeek judge）+ LangSmith 节点级 trace/数据集/evaluate（见上方「持久化 + 可观测/评测」）
- **阶段 5**（已完成）：FastAPI + 原生前端 Web 演示，SSE 流式 token、五阶段决策轨迹、坐席/审批模式、在飞流 abort
- **阶段 6**（已完成）：billing/technical 工具调用、ToolNode 回流、退款创建前人工审批、mock 业务库与离线工具测试
- **阶段 7**（已完成）：工具调用质量评测 + README 收口，记录 should-call / 工具选择 / 参数准确率真实数字
- **性能路由（扩展点）**：同类多 Agent 实例时按 `routing_score` 选最优（对照旧版性能路由，本阶段先不做）
- **持久化（扩展点）**：`SqliteSaver` → `RedisSaver`/`PostgresSaver`（生产级、分布式，本阶段不做）
