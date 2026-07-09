# Web 前端工程纪律重构 — 设计文档

- 日期：2026-07-09
- 范围：`langgraph_cs/web/static/{index.html,style.css,app.js}` + `langgraph_cs/web/server.py`（仅 `index()` 路由）
- 阶段：本次为 **Phase 1（工程纪律清理）**，视觉设计基本不变；Phase 2（视觉迭代）不在本文档范围内，留待后续单独一轮 brainstorm。

## 背景

现有 web demo（RelayDesk，LangGraph 多 Agent 客服的可视化前端）是一个零构建工具的纯静态页面：`index.html` + 单文件 `style.css`（869 行）+ 单文件 `app.js`（988 行），由 FastAPI 的 `StaticFiles` 挂载 + 一个 `FileResponse` 路由提供。视觉设计（深色工程控制台风格、CSS 自定义属性做调色板）本身是刻意设计过的，质量不差；问题集中在**工程纪律**：

1. `app.js` 单文件把「零 DOM 依赖的纯函数」和「DOM 编排逻辑」混在一起，导致 `test_markdown.mjs` 只能用注释 marker + `vm.runInContext` 沙箱硬抠代码块来测试纯函数，脆弱且不直观。
2. `style.css` 里散落 26 处硬编码色值，其中大部分已有对应语义但没有对应 token（如错误红、按钮渐变深色端、亮色按钮上的文字色）。
3. 类名命名不统一：多数是 kebab-case 的"组件名-元素名"（如 `.msg-user`、`.stage-node`），不是严格 BEM；状态类前缀也不统一（`is-busy`/`is-active` 有 `is-` 前缀，`seat-mode`/`approval-mode`/`typing` 没有）。
4. 决策轨迹侧栏 5 个 stage 的 `<li>` 结构在 `index.html` 里手写复制了 5 遍。

这次重构的目标：**不改视觉，把这三类问题清理掉，交给未来的自己或其他人接手时更好维护**。

## 非目标

- 不引入前端构建工具（Vite/Webpack/Astro 等）——单页 demo 用不上，原生 ES module 已经够用。
- 不改视觉设计（配色、排版、动效、布局）——这是 Phase 2 的事。
- 不做后端架构改动（`docs/project-review.md` 里提到的并发锁、SSE 协议、checkpoint 生命周期等问题不在本次范围）。
- 不为决策轨迹的 5 个 stage 建立前后端共享数据源（JS 的 `STAGE_ORDER` 和 Jinja 模板的循环数据各自声明，注释标注需同步维护即可，做共享数据源对 5 项数据是过度设计）。

## Track A · JS 模块化

把 `app.js` 按职责拆成 `langgraph_cs/web/static/js/` 下的原生 ES module，`app.js` 保留在原路径作为入口（`<script src="/static/app.js">` 加 `type="module"`）：

| 文件 | 迁入内容（对应现 app.js 行号区间，供实施时核对） |
|---|---|
| `js/pure.js` | `TOOL_LABEL`/`toolLabel`、`normalizeSessionUserId`、`buildChatBody`/`buildSeatResumeBody`/`buildApprovalResumeBody`、`escapeHtml`、`RE_HEADING`/`RE_OL`/`RE_UL`、`classifyLine`、`renderMarkdown`（现 189–337 行，即现有 `PURE-MARKDOWN-BLOCK` marker 包裹的区间）。零 DOM 依赖，全部 `export`。 |
| `js/dom.js` | 所有 `$(sel)` 元素引用、`el()` 助手、`stageEls`/`valueEls`/`seatStepEls`（现 27–72 行） |
| `js/session.js` | `THREAD_STORAGE_KEY` 等常量、`loadThreadId`/`newThreadId`/`loadSessionUserId`/`saveSessionUserId`、`renderThreadPill`/`renderIdentityPill`（现 118–173 行） |
| `js/pipeline.js` | `STAGE_ORDER`、`setStage`/`resetPipeline`/`advancePipeline`/`completeStage`/`pipelineToSeat`/`pipelineToApproval`、`seatFlowReset`/`seatFlowEnter`/`seatFlowReply`（现 441–524 行） |
| `js/messages.js` | `INTENT_EMOJI`/`INTENT_LABEL`/`AGENT_LABEL`/`AGENT_EMOJI`/`confidenceLevel`、`addUserMessage`/`createBotMessage`/`addSysLine`/`scrollToBottom`（现 78–117、339–435 行） |
| `js/sse.js` | `readSSE`、`runStream`（现 525–781 行） |
| `app.js`（入口） | `enterSeatMode`/`exitSeatMode`/`enterApprovalMode`/`exitApprovalMode`、`sendUserMessage`/`sendSeatReply`/`sendApprovalDecision`/`onSubmit`/`setBusy`/`autoGrow`/`resetSession`/`onIdentityChange`/`onRetry`，import 上面各模块，挂 DOM 事件监听，做初始化 |

**测试联动**：`test_markdown.mjs` 改为直接 `import(pathToFileURL(pure.js 路径))` 加载真实模块，删掉现有的 marker 常量（`PURE-MARKDOWN-BLOCK-START/END`）和 `vm.createContext`/`vm.runInContext` 沙箱代码（约 15 行 hack）。

**风险**：原生 ES module 默认 `defer` 执行（等价于现在脚本放在 `</body>` 前的效果），执行时机不变；主要风险是模块间循环依赖或漏迁移某个函数——迁移时逐个函数核对，迁完跑测试 + 浏览器过一遍。

## Track B · CSS 令牌化 + BEM 命名

### B-1 · 补 token，替换硬编码色值

在 `:root` 补充（命名对齐现有 `--seat`/`--seat-soft`/`--seat-line`/`--seat-bg` 的模式）：

```css
--danger: #ff6b6b;
--danger-soft: #ff9d9d;
--danger-line: rgba(255, 107, 107, .35);
--danger-bg: rgba(255, 107, 107, .1);
--seat-2: #e09238;          /* 按钮渐变深色端，现 2 处硬编码 */
--ink-on-accent: #0b1020;   /* 亮色按钮/渐变上的深色文字；与 --bg 同值但语义不同，不复用 --bg */
```

替换点（已用 grep 定位）：
- `#ff6b6b`/`#ff9d9d` 及其 rgba 变体 → `.status.is-error`、`.retry`/错误提示相关选择器（约 5 处）
- `#e09238` → `.btn-approve` 渐变、`.composer.seat-mode .btn-send` 渐变（2 处）
- `#0b1020`（`:root` 定义本身除外）→ `.identity-select option`、`.btn-send`/同类渐变按钮文字色、`.msg-user .bubble strong`、`.btn-approve` 文字色（5 处）

### B-2 · BEM 改名

规则：结构类名改成 `Block__Element--Modifier`；**状态类保留 `is-*` 前缀风格不变**（这是 BEM 生态里通行的状态钩子约定，不算 utility 类），顺带把目前不一致的 `seat-mode`/`approval-mode`/`typing`（无前缀）统一改成 `is-seat-mode`/`is-approval-mode`/`is-typing`。

完整改名表（CSS 选择器 + HTML class 属性 + JS 里 `classList`/`el()`/字符串拼接引用，三处要联动改）：

| 旧类名 | 新类名 | 说明 |
|---|---|---|
| `.msg-user` | `.msg--user` | |
| `.msg-bot` | `.msg--bot` | |
| `.from-seat` | `.msg--from-seat` | |
| `.welcome` | `.msg--welcome` | |
| `.bubble` | `.msg__bubble` | |
| `typing`（状态类） | `is-typing` | 统一状态类前缀 |
| `.meta-line` | `.msg__meta` | |
| `.meta-seg` | `.msg__meta-seg` | |
| `.meta-dot` | `.msg__meta-dot` | |
| `.bubble-actions` | `.msg__actions` | |
| `.sys-line` | `.messages__sys-line` | |
| `.btn-ghost` | `.btn.btn--ghost` | |
| `.btn-send` | `.btn.btn--send` | |
| `.btn-quick` | `.btn.btn--quick` | |
| `.btn-approval.btn-approve` | `.btn.btn--approve` | 去掉冗余的 `.btn-approval` 包装类 |
| `.btn-approval.btn-reject` | `.btn.btn--reject` | 同上 |
| `.stage-rail` | `.stage__rail` | |
| `.stage-node` | `.stage__node` | |
| `.stage-idx` | `.stage__idx` | |
| `.stage-line` | `.stage__line` | |
| `.stage-body` | `.stage__body` | |
| `.stage-label` | `.stage__label` | |
| `.stage-value` | `.stage__value` | |
| `.seat-step-tag` | `.seat-step__tag` | |
| `.seat-step-text` | `.seat-step__text` | |
| `.seat-flow-title` | `.seat-flow__title` | |
| `.seat-banner-dot` | `.seat-banner__dot` | |
| `.seat-banner-text` | `.seat-banner__text` | |
| `.status-dot` | `.status__dot` | |
| `.status-text` | `.status__text` | |
| `.brand-mark` | `.brand__mark` | |
| `.brand-text` | `.brand__text` | |
| `.brand-name` | `.brand__name` | |
| `.brand-sub` | `.brand__sub` | |
| `.identity-select-wrap` | `.identity` | 块改名 |
| `.identity-label` | `.identity__label` | |
| `.identity-select` | `.identity__select` | JS 只用 `#identity-select` 这个 id，不依赖此 class，改动安全 |
| `.identity-pill` | `.pill.pill--identity` | 与 `.thread-pill` 合并进 `.pill` 块（CSS 里已用分组选择器共享 `height/display/align-items`，说明本来就是同一族） |
| `.thread-pill` | `.pill.pill--thread` | 同上 |
| `.topbar-actions` | `.topbar__actions` | |
| `.composer-row` | `.composer__row` | |
| `.input`（composer 内的 textarea） | `.composer__input` | 避免过于通用的全局类名，也更贴合"禁 utility 类"的精神 |
| `.approval-actions` | `.composer__approval-actions` | |
| `seat-mode`（状态类，加在 `.composer` 上） | `is-seat-mode` | 统一状态类前缀 |
| `approval-mode`（状态类） | `is-approval-mode` | 同上 |
| `.pipeline-head` | `.pipeline__head` | |
| `.pipeline-title` | `.pipeline__title` | |
| `.pipeline-hint` | `.pipeline__hint` | |
| `.pipeline-foot` | `.pipeline__foot` | |

不改动（单次使用的页面级容器，无重复元素/无 modifier，强行拆 BEM 属过度设计）：`.app`、`.layout`、`.chat`、`.topbar`、`.messages`、`.composer`（块名本身不变，只有内部元素改）、`.pipeline`（同）、`.stages`、`.seat-steps`。

**风险**：这是三条轨道里改动面最大、回归风险最高的一步——CSS 选择器、HTML class 属性、JS 里的 `classList.add/remove/contains` 调用和 `el()` 字符串拼接三处必须同步改，漏一处会导致某个状态样式静默失效（不一定报错，测试也不一定能兜住，因为现有测试不做视觉断言）。改完必须逐个交互状态过一遍浏览器（见「验收标准」）。

## Track C · 决策轨迹 HTML 模板化

`index.html` 移到 `langgraph_cs/web/templates/index.html`（Jinja 惯例：模板和静态资源分开目录，`static/` 只留 CSS/JS）。

`requirements.txt` 加 `jinja2`（当前 venv 未安装，FastAPI 也未把它当传递依赖带进来）。

`server.py` 的 `index()` 路由从 `FileResponse(_STATIC_DIR / "index.html")` 改成 `Jinja2Templates(directory=...).TemplateResponse`，服务端传入：

```python
STAGES = [
    {"key": "intent", "idx": 1, "label": "意图识别"},
    {"key": "rag", "idx": 2, "label": "知识库检索"},
    {"key": "route", "idx": 3, "label": "路由分发"},
    {"key": "tool", "idx": 4, "label": "业务工具"},
    {"key": "answer", "idx": 5, "label": "生成应答"},
]
```

模板里用 `{% for stage in stages %}` 循环生成 5 个 `<li class="stage" data-stage="{{ stage.key }}" id="stage-{{ stage.key }}">`，`id="value-{{ stage.key }}"` 等，**必须和 `js/pipeline.js` 里的 `STAGE_ORDER = ["intent","rag","route","tool","answer"]` 保持 key 一致**（两处独立声明，各自加注释互相指向，不建共享数据源）。第 5 个 stage（`answer`）没有 `.stage__line` 连接线，模板循环里用 `loop.last` 判断。

**风险**：`test_server_offline.py` 的 `test_index_returns_html` 断言 `resp.status_code == 200` + `"RelayDesk" in resp.text`，TemplateResponse 不影响这两点，风险低；主要核对点是 5 个 `id` 精确匹配、favicon/字体 `<link>` 等静态部分原样保留。

## 执行顺序与 Effort 建议

| 步骤 | Effort | 理由 |
|---|---|---|
| A-1 拆 `pure.js` | medium | 机械搬运，但要保证签名/依赖不漏 |
| A-2 拆其余模块 + 改 `app.js` 入口接线 | medium | 边界已在本文档定好，主要是执行力 |
| A-3 改 `test_markdown.mjs` 用真 `import()` | low | 改法明确 |
| A 收尾验证 | medium | 跑测试 + 浏览器核对关键路径 |
| B-1 CSS 补 token 替换硬编码色值 | low | 纯替换，定位和目标 token 已列全 |
| B-2 BEM 改名（CSS+HTML+JS 联动） | high | 改动面最大、跨文件一致性要求高、易漏改且不易被测试兜住 |
| B 收尾验证 | medium | 浏览器逐状态核对，视觉应像素级不变 |
| C Jinja2 模板化 | medium | 新依赖 + 服务端路由改法，但范围小、约束清楚 |
| C 收尾验证 | medium | 跑 `test_server_offline.py` + 浏览器确认页面加载和决策轨迹点亮 |

按 A → B → C 顺序执行，每条轨道独立可验证，出问题能定位到具体是哪条轨道引入的。

## 验收标准

1. `pytest langgraph_cs/web/tests/test_server_offline.py` 全绿。
2. `node langgraph_cs/web/tests/test_markdown.mjs` 全绿（且已改为真实 `import()`，不再依赖 marker/vm hack）。
3. 起本地服务（`python -m langgraph_cs.web`），浏览器手工过一遍：
   - 发送消息 → 决策轨迹 5 个 stage 依次点亮 → 收到回答
   - 说"转人工" → 系统条 + 坐席三段状态卡显示 → 提交坐席回复
   - 触发一次审批模式（如有对应测试身份/场景）→ 批准/驳回按钮可用
   - 消息气泡下方"重新提问"按钮可点击
   - 窄屏（<900px、<560px）布局折叠正常
   - 以上视觉与重构前**逐像素一致**（无意外的颜色/间距变化）
4. `git diff` 里除了本文档涉及的三个 Track，无其他文件被动到。

## 未决问题

无——三条轨道的范围、命名映射、执行顺序均已在本文档中明确。
