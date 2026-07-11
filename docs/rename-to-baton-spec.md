# 改名执行规范：RelayDesk → Baton（Rename Spec）

> 执行者：Claude Code。评审方：Codex（只读）。仲裁依据：本文件。
> 流水线位置：**注释清洗分支合并之后、企业场景迁移动工之前**。
> 性质：机械性重构 + 顺带修复三处已知打包缺口。一次分支、一轮评审预期通过。

## 0. 命名总表（唯一事实来源）

| 项 | 旧 | 新 |
|---|---|---|
| GitHub 仓库 | relaydesk | baton（所有者手动改，见 §5） |
| 发行名（pyproject name） | langgraph-cs-agent | baton |
| 包目录 / import 名 | langgraph_cs | baton |
| 控制台入口 | langgraph-cs / langgraph-cs-web | baton / baton-web |
| State 类 | CSState | BatonState |
| 环境变量 | CS_CHECKPOINT | BATON_CHECKPOINT |
| Chroma collection | cs_faq | baton_kb |
| LANGSMITH_PROJECT 默认值 | relaydesk-langgraph | baton |
| UI / prompt 中的品牌词 | RelayDesk | Baton |
| version | 0.1.0 | **不动**（迁移完成时升 0.2.0） |

## 1. 硬性约束

1. **只换名字，不换语义**。prompt 字符串里仅替换品牌词 "RelayDesk" → "Baton"，其余一字不动——"你是 RelayDesk 智能客服" 改成 "你是 Baton 智能客服"，**"客服"二字保留**，场景措辞属于迁移阶段，不属于本次。
2. 不改任何业务逻辑、算法、阈值、路由规则、测试断言语义。
3. 目录移动一律用 `git mv`（保 history）；不重写 git 历史。
4. 不碰：`data/faq/*.md` 语料内容、`eval/*.json` 数据内容（二者随迁移整体替换）、`.git/`、`reviews/`。
5. 每个大步骤后运行 `python -m pytest baton -q`，50 条离线测试必须全过（见 §4 关于 venv 重建的前置）。

## 2. 范围与顺带修复

本次唯一允许的"超出改名"变更：**pyproject 打包三连修**——理由是这些行在改名中本来就要整体重写，拆两次提交反而制造重复 diff：

- dependencies 补上 `"jinja2>=3.1.0"`（requirements.txt 已有，pyproject 漏了，两处声称同步实为失同步）；
- package-data 补 `"templates/*"`（当前 wheel 会丢 index.html）；
- package-data 的 `"static/*"` 不递归，补 `"static/js/*"`（当前 wheel 会丢 6 个 js 文件）。

除此之外任何"顺手优化"都算违规，由评审方拦截。

## 3. 执行步骤（按序，每步一个 commit）

1. **包目录迁移**：`git mv langgraph_cs baton`。
2. **import 与模块路径全量重写**：`from langgraph_cs...` / `import langgraph_cs` / `python -m langgraph_cs...` → `baton`。覆盖范围：全部 .py（含 tests、scripts、eval）、两个 README 的命令示例、pyproject 的 `packages` 与 `package-data` 键、docstring 中出现的模块路径。
3. **pyproject 重写**：name / scripts（`baton = "baton.main:main"`，`baton-web = "baton.web.__main__:main"`）/ packages / package-data（含 §2 三连修）/ 补 jinja2；description 改为**场景中立**表述："基于 LangGraph 的多 Agent 服务台系统：意图路由 → RAG → 工具调用 → 人工审批 → 评测闭环"（企业场景措辞留给迁移）；keywords 去掉 customer-service，换 multi-agent / service-desk / human-in-the-loop / rag / langgraph。
4. **标识符替换**：CSState → BatonState（state.py 及所有引用处，含测试）；CS_CHECKPOINT → BATON_CHECKPOINT（graph.py、.env.example、README、verify_persistence.py、相关测试）；COLLECTION_NAME "cs_faq" → "baton_kb"（store.py）；LANGSMITH_PROJECT 默认值 → "baton"（config.py、.env.example）。
5. **品牌词替换**：全库 "RelayDesk"/"relaydesk" → "Baton"/"baton"，覆盖 agent prompts、web/templates/index.html、web/static/js、两个 README 标题与正文、.env.example 注释。遵守 §1.1 的"只换名字"边界。
6. **README 命令与路径同步**：快速开始里的 venv 路径、`python -m` 命令、gh 仓库链接（等所有者完成 §5.1 后填新 URL）。
7. **requirements.lock 检查**：若含旧发行名（如 `-e .` 的 egg 信息或 `langgraph-cs-agent==`），在重建 venv 后 `pip freeze` 重新生成。
8. **CLAUDE.md / AGENTS.md 更新**：测试命令改为 `python -m pytest baton -q`；"当前规范路径"指向本文件。

## 4. 本地环境重建（执行方必做，否则测试必挂）

- **venv 不可迁移**：旧 venv 在 `langgraph_cs/.venv`，`git mv` 后其内部路径全部失效。删除并在新位置重建（建议直接放仓库根 `.venv`，README 同步），重装 requirements。
- **运行时产物重置**：删除 `baton/data/chroma_rag` 与 `checkpoints.sqlite`（均 gitignored）。collection 名已变，旧库无法沿用；重灌需要真实 key，由所有者在冒烟环节执行（§6.3），agent 不得使用本地凭据。

## 5. 所有者手动步骤（agent 只提示，不执行）

1. GitHub Settings 里把仓库 relaydesk 改名为 baton（旧 URL 自动重定向），同步改仓库 description；本地 `git remote set-url origin <新地址>`。**建议在 agent 开工前完成**，README 里的新链接一步到位。
2. 评审通过后由所有者 merge。
3. 带 key 冒烟（约 5 分钟）：重建 venv → `python -m baton.scripts.ingest_faq` → `python -m baton.scripts.seed_business_db` → 起 web 问一条账单类问题，确认路由、检索、工具、页面品牌词全部正常。

## 6. 验收门禁

1. **零残留扫描**（排除 data/faq、eval/*.json、.git、reviews、requirements.lock 单独核）：
   ```bash
   grep -rniE "langgraph_cs|langgraph-cs|relaydesk|CSState|CS_CHECKPOINT|cs_faq" \
     --exclude-dir=.git --exclude-dir=reviews \
     --exclude-dir=faq --exclude="*.json" --exclude="requirements.lock" .
   ```
   预期零命中；确属合法的豁免逐条列入摘要表。
2. `python -m pytest baton -q` → 50/50。
3. **wheel 构建验证**（打包三连修的证据）：
   ```bash
   pip install build && python -m build --wheel
   unzip -l dist/*.whl | grep -E "templates/index.html|static/js/|faq/.*\.md"
   ```
   三类资源都必须出现在 wheel 清单中，且 wheel 元数据含 jinja2 依赖。
4. diff 复核：无 §1/§2 之外的变更。

## 7. 已接受的遗留（评审方不作违规处理）

- `docs/web-screenshot.png`、`docs/web-seat-mode.png` 截图内仍显示旧品牌与客服场景——**接受**，迁移收尾（Demo 走查日）统一重截；README 中在两图旁加一行"截图待场景迁移后更新"即可。
- git 历史中的旧名——按既定决策不重写。
- 注释清洗规范（docs/comment-cleanup-spec.md）内的旧路径引用——历史文档，不回改。
