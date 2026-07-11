# RelayDesk 注释清洗规范（Comment Cleanup Spec）

> 执行者：coding agent。目标：把全库注释从"分阶段教学"口吻改写为面向工程读者的设计文档口吻。
> 这是一次**纯注释级变更**——一行代码逻辑都不许动。所有行号基于 2026-07-11 的 main 分支。

## 0. 背景与基线

仓库注释的信息质量很高（设计取舍、踩坑记录齐全），但通篇是教学旅程口吻，存在三类硬伤：

1. **学习叙事**直接暴露"跟课搭建"痕迹，最严重的一处在 intent.py:4-7（"教学版……等你把单路跑通……就能在简历里讲"）；
2. **悬空引用**：大量"对照旧版 xxx.py"指向本仓库不存在的文件；
3. **过期事实**：如 rag.py:36 写"知识库约 10 块"，实际语料已是 121 个条目 chunk。

清洗前禁词扫描基线：**110 处命中 / 27 个文件**。Top 分布：

| 文件 | 命中 |
|---|---|
| langgraph_cs/README.md | 29 |
| nodes/agent.py | 10 |
| nodes/intent.py | 9 |
| graph.py | 8 |
| rag/store.py | 6 |
| nodes/rag.py | 6 |
| pyproject.toml / requirements.txt / \_\_init\_\_.py | 各 5 |
| nodes/router.py | 4 |
| 其余 17 个文件 | 各 1–3 |

完整清单由 §6.2 的命令复现。

## 1. 硬性约束（违反任一条 = 整体返工）

1. **仅允许修改**：`#` 行注释、docstring、README、requirements.txt / pyproject.toml / .env.example 中的说明性文字。**禁止修改**：任何可执行代码、标识符、字符串字面量（含所有 prompt 常量）、配置默认值、JSON 数据。
2. **诚实性**：改写不得虚构未发生的评测或对比。只有 `eval/` 下有对应产物支撑的结论才能写成"实测/评测表明"；拿不准就用"备选方案是……，当前选择 X，原因 Y；若 Z 变化可重新评估"的句式。
3. 注释**保持中文**，不做中英翻译（避免无面试价值的巨型 diff）。
4. 每完成一个文件即运行 `python -m pytest langgraph_cs -q`，**50 条离线测试必须全过**。
5. **完全不碰**：`data/faq/`（知识库语料）、`eval/*.json`（评测数据）、`.git/`。

## 2. 范围

**清洗**：`langgraph_cs/**/*.py`（含 tests、scripts、eval/*.py）、`web/static/js/*.js`、`requirements.txt`、`pyproject.toml`（注释与 description）、`.env.example`、根 `README.md`、`langgraph_cs/README.md`。

**两个 README 的特殊规则**：本次只做"排雷级"清洗（删教学/简历类字眼、修过期事实），**不做结构重写**——它们将在企业场景迁移时整体重写，现在重构是浪费工时。langgraph_cs/README.md 的 29 处命中大多属于"阶段式"章节结构，排雷即可，结构留给迁移。

**不清洗**：`eval/*results*.md`（迁移后会重新生成）。注意其中"一阶段/二阶段"指检索的粗排/精排，是合法术语，任何时候都不算命中。

**docs/superpowers/**：不清洗，见 §8 决策点 1。

## 3. 五类问题与处理规则（附本仓库真实案例）

### A 类｜学习旅程叙事 → 删除

特征词：`教学版` / `教学点` / `等你` / `进阶` / `就能在简历里讲` / `（见 xx 指南）` / `别忘了`。

规则：整句删除；若句中夹带设计信息，把信息拆出来按 B/C 类改写。

**案例（intent.py:4-7）**

改前：
> 教学版：只用 LLM 单路识别（朴素但好懂）。对照旧版 core/intent_recognizer.py：那里是 LLM(70%) + Embedding(20%) + 关键词(10%) 三路加权融合。等你把单路跑通、理解了节点怎么读写 state，阶段进阶时再把另外两路加回来，就能在简历里讲"为什么要三路融合、各自补什么短板"。

改后：
> 意图识别采用 LLM 单路分类。备选方案是 LLM/Embedding/关键词多路加权融合；当前意图集与语料规模下，单路 LLM 已满足路由准确率需求且实现更简单。若意图数量增长导致单路准确率下降，多路融合是既定扩展点。

### B 类｜阶段叙事与施工自述 → 改写为现状描述

特征：`阶段 N 改动` / `阶段 1/2 是……` / `本阶段暂不做` / `教学阈值`。

规则：只描述"现在是什么 + 为什么 + 什么条件下要重新审视"；演进历史交给 git log 承载，不留在注释里。

**案例 1（router.py:21-22）**

改前：
> \# 低于这个置信度就降级到 general_agent（教学阈值，对照旧版低置信度降级）。

改后：
> \# 低于该置信度即降级到 general_agent。当前为经验值，尚未经 dev 集校准；调整时须同步跑路由评测、更新基线。

（迁移期完成阈值校准后，再改为"经 dev 集校准，依据见 eval/ 对应报告"。）

**案例 2（graph.py:27-30）**

改前：
> 阶段 4 改动：checkpointer 从硬编码 MemorySaver 改为可切换工厂 make_checkpointer()，按环境变量 CS_CHECKPOINT=memory|sqlite 选择……

改后：
> checkpointer 由 make_checkpointer() 工厂按环境变量 CS_CHECKPOINT=memory|sqlite 选择；build_graph() 支持外部注入自定义实例（供评测/测试隔离），不传则走工厂默认。

### C 类｜悬空引用 → 转译或删除

特征：`对照旧版` / `agent_orchestrator.py` / `core/intent_recognizer.py` 等本仓库不存在的路径。

规则：对照内容若承载设计备选信息 → 改写为"另一种实现是……"（不再提不存在的文件名）；若只是课程交叉引用 → 删除。

**案例（router.py:7-13）**

改前：
> 对照旧版手写 agent_orchestrator.py 三层路由：1) 意图路由……2) 降级路由……3) 升级路由……

改后：
> 路由分三层：1) 意图路由：technical / billing / escalation 映射到专职节点；2) 降级路由：置信度低于阈值时统一落 general_agent——低置信不该交给专职 Agent 自作主张；3) 升级路由：escalation 直达 human-in-the-loop。

（保留原注释里"写成不依赖 LLM、无副作用的纯函数，可离线穷举断言"这句设计理由。）

### D 类｜过期事实 → 核对并修正

**已知实例（rag.py:36）**

改前：
> \# 本知识库很小（FAQ 切块后约 10 块），运行期 k=5 已够……

改后：
> \# 语料为条目级 chunk（百级规模，实数以 ingest 日志为准），k=5 的召回充分性已由 eval/dataset.json 的检索评测验证……

附加规则：避免在注释里内嵌易变的具体数字；确需引用时指向单一事实来源（数据文件 / ingest 日志 / 评测报告）。清洗过程中，对遇到的**每一个具体数字**做一次现状核对。

### E 类｜施工标记残留 → 按功能重组

特征：`PR1/PR2/PR3`、"阶段 2 骨架依赖"这类 requirements.txt 与注释里的施工分组。

规则：依赖分组按功能命名。requirements.txt 目标分组：**编排核心**（langgraph / langchain-* / python-dotenv）、**RAG 检索与重排**（langchain-chroma / text-splitters / httpx / rank-bm25 / jieba）、**持久化与评测**（langgraph-checkpoint-sqlite / langsmith）、**Web 演示**（fastapi / uvicorn / jinja2）。pyproject.toml 的依赖注释同步此分组。

## 4. 保护清单（禁止删除，只许口吻级润色）

以下注释是全库价值最高的部分，误删任何一条都算返工：

1. **graph.py:56-67**：SqliteSaver 连接生命周期陷阱（from_conn_string 是上下文管理器、长驻进程须自持连接、check_same_thread=False 的原因）。
2. **tools.py:141-144**：interrupt resume 的重放语义（ToolNode 会重跑同批工具、只读工具重复执行无害、唯一写操作被审批结果门控）。
3. **tools.py:28-35**：身份只从 Runnable config 带外注入、绝不从消息文本解析的安全设计。
4. **store.py:7-15**：条目级切块 vs 400 字滑窗粗切的取舍（溯源能力与 rerank 语义边界），以及 bge 512 token 上限的适配说明。
5. **router.py:43-45**：escalation 不受置信度门槛约束的理由（"宁可错转也不漏转"）。
6. **intent.py:28-30**：真实误路由案例。改写口吻但**必须保留事实**，参考改法：
   > \# 类别仅给名字时存在真实误分：曾出现"查退款进度"被 0.95 高置信分入 query、"申请退款"分入 request，导致 billing 工具不可达。因此类别定义附一行说明，并加边界规则：凡涉及具体订单/账单/退款/发票，一律 billing 优先。
7. **escalation.py:13-16**：interrupt 前置且不依赖 LLM 的两条理由（转人工应立即停下；无 LLM 依赖使节点可离线验证）。

## 5. 改写风格

- 每条注释只回答三问之一："**是什么契约** / **为什么这样设计** / **什么条件下要重新审视**"，不回答"我是怎么一步步学到这里的"。
- 删除判据：删掉这条注释后，一名中级工程师读代码是否损失信息？否 → 删。例：store.py:95 "# 文件结束，别忘了收尾最后一条。" → 删除，或改为"# 收尾最后一个条目"。
- module docstring 目标形态 **≤ 10 行**：一句职责 + 节点契约（输入/输出）+ 关键设计决策。现有多个 docstring 长达 20-30 行，超出部分基本都是 A/B/C 类内容，按规则处理后自然瘦身。
- 不追求注释密度指标；宁缺勿滥，但 §4 保护清单的优先级高于精简。

## 6. 执行流程

1. 新建分支 `chore/comment-cleanup`；确认基线 `python -m pytest langgraph_cs -q` 50 条全过。
2. 生成并存档命中清单（后续用同一命令做门禁）：

```bash
grep -rnE "教学|阶段 ?[0-9]|\bPR[0-9]\b|旧版|简历|等你|别忘了|code-reuse|进阶" \
  --include="*.py" --include="*.js" --include="*.md" --include="*.txt" \
  --include="*.toml" --include="*.example" . | grep -v "data/faq\|eval/.*\.json\|\.git/"
```

3. 处理顺序：`nodes/` → `rag/` → `graph.py / state.py / config.py / main.py / __init__.py` → `business/` → `eval/*.py` → `scripts/` → `web/` → `requirements.txt / pyproject.toml / .env.example` → 两个 README（仅排雷）。
4. **每个文件一个 commit**，message 格式：`docs(cleanup): <path> 注释口吻清洗`。
5. 全部完成后执行 §7 门禁，并输出**变更摘要表**：文件 / 删除行数 / 改写行数 / 涉及的保护清单条目 / 豁免项及理由。

## 7. 验收门禁

1. §6.2 扫描命令重跑为**零命中**；确属合法用法的豁免（例如注释里以业务语义使用"阶段"一词）逐条列入摘要表说明，不允许静默豁免。
2. `python -m pytest langgraph_cs -q` → 50/50 通过。
3. 逐文件 diff 复核：除注释 / docstring / 说明性文字外**无任何变更行**。
4. 抽查 intent.py、router.py、graph.py 三处改写，须符合 §3-§5。

## 8. 留给仓库所有者的决策点（agent 只报告，不执行）

1. **docs/superpowers/（plans + specs）**：这是 AI 编码工作流的过程产物（文件开头即"For agentic workers: REQUIRED SUB-SKILL…"）。建议 `git rm -r docs/superpowers` 从公开仓库移除、本地留档；若想把"规范驱动的 AI 协作流程"保留为面试叙事，则明确保留并在 README 说明。二选一，等所有者拍板。
2. **git 提交历史**：本规范不重写历史——增量提交本身是工程加分项。agent 执行 `git log --oneline | grep -cE "教学|简历|阶段 ?[0-9]"` 并报告命中数即可，是否 rebase 由所有者决定（默认建议：不动）。
3. **pyproject 的 name（langgraph-cs-agent）与 description（"……智能客服"）**：属于功能性元数据，随企业场景迁移一起改，本次不动。
