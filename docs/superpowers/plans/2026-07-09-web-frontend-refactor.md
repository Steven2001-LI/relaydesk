# Web 前端工程纪律重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 RelayDesk web demo 的 `app.js`/`style.css`/`index.html` 从"能跑但工程纪律差"重构成"模块化、令牌化、BEM 命名、可维护"，视觉与交互行为**完全不变**。

**Architecture:** 三条独立轨道按 A→B1→B2→C 顺序执行，每条轨道一个 task、一次提交、一轮独立验证（既有测试 + 浏览器手工走查）。零构建工具，纯原生 ES module + CSS 自定义属性 + Jinja2（仅 Track C）。

**Tech Stack:** 原生 JS（ES module）、原生 CSS（自定义属性）、FastAPI + Jinja2（Track C 新增依赖）、Node 内置测试脚本、Python `TestClient` 离线测试。

## Global Constraints

- 不引入前端构建工具（Vite/Webpack/Astro 等）——全程原生 ES module + `<link>`/`<script type="module">`。
- 不改视觉设计（配色、排版、动效、布局像素级不变）——所有改动必须是行为等价的机械重构。
- 不做后端架构改动，Track C 除外，且 Track C 只改 `index()` 这一个路由。
- 决策轨迹 5 个 stage（`intent/rag/route/tool/answer`）的顺序和 key 必须在 `js/pipeline.js` 的 `STAGE_ORDER` 与 `server.py` 的 `_STAGES`（Track C 引入）两处保持一致，各自独立声明、互相注释指向，不建共享数据源。
- 每个 task 结束必须跑通对应的既有测试（`node .../test_markdown.mjs`、`.venv/bin/python -m langgraph_cs.web.tests.test_server_offline`），并起本地服务在浏览器里过一遍关键路径，颜色/间距/交互与重构前逐像素一致。
- CSS 类名重命名只改 `class="..."` 属性值 / CSS class 选择器（`.foo`）/ JS 里的 `classList`、`el()`、拼接字符串——**绝不能碰 `id="..."` 属性或 `$("#foo")`/`getElementById` 的 id 查找**，即便某个字符串同时被用作 class 和 id（下面 Task 3 有完整踩坑清单）。

## 与 2026-07-09 设计文档的偏差说明

规划阶段核对代码依赖关系和实际 CSS 结构后，对 spec 做了 4 处必要修正，均不改变 spec 的目标和范围，只是把"文件级设计"落到"函数级/选择器级"时发现的约束：

1. **`enterSeatMode`/`exitSeatMode`/`enterApprovalMode`/`exitApprovalMode` 放进 `pipeline.js` 而不是 `app.js` 入口**：`sse.js` 的 `runStream()` 在 `interrupt` 事件里要直接调用 `enterSeatMode`/`enterApprovalMode`；如果这四个函数留在 `app.js` 入口，会形成 `app.js → sse.js → app.js` 的循环 import。`pipeline.js` 本身不依赖 `sse.js`/`messages.js`，放在这里没有循环问题。
2. **`createBotMessage()` 新增 `onRetry` 回调参数**：原来 `mountRetry()` 直接引用同文件全局的 `onRetry` 函数；拆模块后 `messages.js` 不能反过来 import `app.js`（`app.js` 已经要 import `messages.js`）。改成调用方（`app.js` 入口的 `sendUserMessage`）传入 `{ onRetry }`，行为不变。
3. **不引入 `.btn`/`.pill` 共享块**：spec 里设想的 `.btn.btn--send` 等写法要求存在一个共享的 `.btn { }` 基础规则，但实测 `style.css` 里 `.btn-send`/`.btn-ghost`/`.btn-quick` 从未共享过任何基础样式（各自完整独立定义），强行引入 `.btn` 需要新增一条不存在的 CSS 规则，纯属为了套 BEM 形式而做无意义的结构改动。`.identity-pill`/`.thread-pill` 同理——它们和 `.btn-ghost`/`.status`/`.identity-select-wrap` 共用一条 5 元素分组选择器（`style.css:137-146`）设置 `height:28px` 等布局属性，拆出 `.pill` 块需要拆分这条分组选择器，是真实的结构改动而非纯改名，风险和收益不成比例。**结论：`.btn-ghost`/`.btn-send`/`.btn-quick`/`.identity-pill`/`.thread-pill` 保持原名不变**——它们本来就是合法的 BEM 单词块（Block 不强制要求有 Element/Modifier）。`.btn-approval` 保留原名作为块，`.btn-approve`/`.btn-reject` 改成 `.btn-approval--approve`/`.btn-approval--reject`（这一对确实共享 `.btn-approval` 基础规则，是真正的 block+modifier 关系）。
4. **`.identity-select-wrap` → `.identity`**（块改名）仍按 spec 执行，但只改这一个 token；同一条分组选择器里的 `.identity-pill`/`.thread-pill`/`.btn-ghost`/`.status` 不动（见第 3 点）。

---

## Task 1: Track A — JS 模块化（app.js 拆分成 ES module）

**Effort:** medium（拆分 pure.js/dom.js/session.js/messages.js/sse.js/入口接线）+ low（改 test_markdown.mjs）

**Files:**
- Create: `langgraph_cs/web/static/js/pure.js`
- Create: `langgraph_cs/web/static/js/dom.js`
- Create: `langgraph_cs/web/static/js/session.js`
- Create: `langgraph_cs/web/static/js/pipeline.js`
- Create: `langgraph_cs/web/static/js/messages.js`
- Create: `langgraph_cs/web/static/js/sse.js`
- Modify: `langgraph_cs/web/static/app.js`（整个改写为入口文件）
- Modify: `langgraph_cs/web/static/index.html:200`（`<script>` 标签加 `type="module"`）
- Modify: `langgraph_cs/web/tests/test_markdown.mjs`（去掉 marker/vm 沙箱，改真实 `import()`）

**Interfaces（本 task 内部模块契约，供 Task 3/Task 4 核对用）:**
- `pure.js` 导出：`escapeHtml(s)`、`toolLabel(name)`、`normalizeSessionUserId(id)`、`buildChatBody(message, threadId, sessionUserId)`、`buildSeatResumeBody(threadId, sessionUserId, seatReply)`、`buildApprovalResumeBody(threadId, sessionUserId, approved, note)`、`renderMarkdown(raw)`
- `dom.js` 导出：`$`、`el(tag, className, html)`、`setStatus(kind, text)`、以及一批元素引用常量（见 Step 2 完整列表）
- `session.js` 导出：`state`（可变单例对象，其他模块只做属性赋值，不重新赋值整个对象）、`THREAD_STORAGE_KEY`、`LEGACY_THREAD_STORAGE_KEY`、`loadThreadId`、`newThreadId`、`loadSessionUserId`、`saveSessionUserId`、`renderThreadPill`、`renderIdentityPill`
- `pipeline.js` 导出：`STAGE_ORDER`、`setStage`、`resetPipeline`、`advancePipeline`、`completeStage`、`pipelineToSeat`、`pipelineToApproval`、`seatFlowReset`、`seatFlowEnter`、`seatFlowReply`、`enterSeatMode`、`exitSeatMode`、`enterApprovalMode`、`exitApprovalMode`
- `messages.js` 导出：`INTENT_LABEL`、`AGENT_LABEL`、`addUserMessage(text)`、`createBotMessage({fromSeat, onRetry})`、`addSysLine(text)`
- `sse.js` 导出：`runStream(url, payload, bot, {isResume})`

模块依赖方向（无环）：`dom.js`（无依赖）← `pure.js`（无依赖）← `session.js`（依赖 dom.js）← `pipeline.js`（依赖 dom.js + session.js）← `messages.js`（依赖 dom.js + pure.js）← `sse.js`（依赖 dom.js + pure.js + session.js + pipeline.js + messages.js）← `app.js` 入口（依赖以上全部）。

- [ ] **Step 1: 创建 `pure.js`**

```js
// pure.js —— 零 DOM 依赖的纯函数：markdown 渲染 + 请求体构造 + 工具名映射。
// 供浏览器（其余模块 import）与 Node 单测（test_markdown.mjs 直接 import）共用。

const TOOL_LABEL = {
  query_bill: "查询账单",
  refund_status: "查询退款进度",
  create_refund_ticket: "创建退款工单",
  create_ticket: "创建报障工单",
  check_service_status: "查询服务状态",
};

export function toolLabel(name) {
  return TOOL_LABEL[name] || name || "未知工具";
}

export function normalizeSessionUserId(sessionUserId) {
  return (sessionUserId || "").trim();
}

export function buildChatBody(message, threadId, sessionUserId) {
  return {
    message,
    thread_id: threadId,
    session_user_id: normalizeSessionUserId(sessionUserId),
  };
}

export function buildSeatResumeBody(threadId, sessionUserId, seatReply) {
  return {
    thread_id: threadId,
    session_user_id: normalizeSessionUserId(sessionUserId),
    seat_reply: seatReply,
  };
}

export function buildApprovalResumeBody(threadId, sessionUserId, approved, note) {
  return {
    thread_id: threadId,
    session_user_id: normalizeSessionUserId(sessionUserId),
    approval: { approved, note },
  };
}

export function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// ── 轻量安全 markdown 渲染 ───────────────────────────────
//   原则：先整体转义 HTML（杜绝注入），再在转义后的纯文本上做有限的内联/块级替换。
//   支持：# ~ ###### 标题 · **bold** · `code` · 有序列表(1. ) · 无序列表(- / * ) · 段落与换行。
//   不引第三方库，只覆盖客服回答常见的标题 + 加粗 + 列表 + 换行。
//
//   列表编号 bug 修复（P0-2 关键）：连续的有序列表项——即便项之间夹着空行——
//   必须合并进同一个 <ol>，去掉源里的字面数字，靠 <ol><li> 浏览器自动顺序编号；
//   否则空行会过早 closeList()，下一个「1.」又新开 <ol> 从 1 重启，导致每项都显示「1.」。
//   实现：遇到空行不立刻关列表，先「向后看」——若后续仍是同类型列表项则保持列表打开。
const RE_HEADING = /^\s*(#{1,6})\s+(.*)$/;
const RE_OL = /^\s*\d+\.\s+(.*)$/;
const RE_UL = /^\s*[-*]\s+(.*)$/;

// 行类型分类：返回 { kind: "h"|"ol"|"ul"|"blank"|"p", ... }
function classifyLine(line) {
  const h = line.match(RE_HEADING);
  if (h) return { kind: "h", level: h[1].length, content: h[2].trim() };
  const ol = line.match(RE_OL);
  if (ol) return { kind: "ol", content: ol[1] };
  const ul = line.match(RE_UL);
  if (ul) return { kind: "ul", content: ul[1] };
  if (line.trim() === "") return { kind: "blank" };
  return { kind: "p", content: line };
}

export function renderMarkdown(raw) {
  const text = escapeHtml(raw || "");
  const lines = text.split("\n");
  const classified = lines.map(classifyLine);
  let html = "";
  let listType = null;
  let paraBuf = [];

  const flushPara = () => {
    if (paraBuf.length) {
      html += "<p>" + inline(paraBuf.join("<br>")) + "</p>";
      paraBuf = [];
    }
  };
  const closeList = () => {
    if (listType) { html += "</" + listType + ">"; listType = null; }
  };

  const nextNonBlankKind = (i) => {
    for (let j = i + 1; j < classified.length; j++) {
      if (classified[j].kind !== "blank") return classified[j].kind;
    }
    return null;
  };

  classified.forEach((info, i) => {
    if (info.kind === "h") {
      flushPara();
      closeList();
      const content = inline(info.content);
      if (content) {
        html += `<h${info.level} class="md-h md-h${info.level}">${content}</h${info.level}>`;
      }
    } else if (info.kind === "ol" || info.kind === "ul") {
      flushPara();
      if (listType !== info.kind) { closeList(); html += "<" + info.kind + ">"; listType = info.kind; }
      html += "<li>" + listItemHtml(info.content) + "</li>";
    } else if (info.kind === "blank") {
      flushPara();
      if (listType && nextNonBlankKind(i) !== listType) closeList();
    } else {
      closeList();
      paraBuf.push(info.content);
    }
  });
  flushPara();
  closeList();
  return html;

  function inline(s) {
    return s
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  }

  function listItemHtml(s) {
    const rendered = inline(s);
    const m = rendered.match(/^([^：]{1,24})：(.+)$/) || rendered.match(/^([^:]{1,24}):\s+(.+)$/);
    if (m && !/<(strong|code)>/.test(m[1])) {
      const sep = rendered.indexOf("：") !== -1 ? "：" : ": ";
      return `<span class="li-title">${m[1]}${sep}</span><span class="li-desc">${m[2]}</span>`;
    }
    return rendered;
  }
}
```

- [ ] **Step 2: 创建 `dom.js`**

```js
// dom.js —— 页面元素引用 + 通用 DOM 构建/状态小工具。
// 只做元素查询和零业务语义的 DOM 操作，不含会话/pipeline/消息业务逻辑。

export const $ = (sel) => document.querySelector(sel);

export const messagesEl = $("#messages");
export const inputEl = $("#input");
export const sendBtn = $("#btn-send");
export const composerEl = $("#composer");
export const seatBanner = $("#seat-banner");
export const seatBannerTextEl = $("#seat-banner-text");
export const approvalActionsEl = $("#approval-actions");
export const approveBtn = $("#btn-approve");
export const rejectBtn = $("#btn-reject");
export const identitySelect = $("#identity-select");
export const identityPill = $("#identity-pill");
export const threadPill = $("#thread-pill");
export const newBtn = $("#btn-new");
export const welcomeEl = $("#welcome");   // 开场气泡（首条用户消息后折叠）

// 连接状态指示
export const statusEl = $("#status");
export const statusTextEl = $("#status-text");

// 决策轨迹 pipeline 的五阶段节点 + 值槽 + 标题提示
export const stageEls = {
  intent: $("#stage-intent"),
  rag: $("#stage-rag"),
  route: $("#stage-route"),
  tool: $("#stage-tool"),
  answer: $("#stage-answer"),
};
export const valueEls = {
  intent: $("#value-intent"),
  rag: $("#value-rag"),
  route: $("#value-route"),
  tool: $("#value-tool"),
  answer: $("#value-answer"),
};
export const pipelineEl = $("#pipeline");
export const pipelineHintEl = $("#pipeline-hint");

// 转人工三段状态卡（命中 interrupt / resume 时点亮）
export const seatFlowEl = $("#seat-flow");
export const seatStepEls = {
  judge: $("#seat-step-judge"),
  wait: $("#seat-step-wait"),
  reply: $("#seat-step-reply"),
};
export const seatReplyTextEl = $("#seat-step-reply-text");

// ── 连接状态小圆点 ───────────────────────────────────────
export function setStatus(kind, text) {
  statusEl.classList.remove("is-busy", "is-error", "is-seat");
  if (kind === "busy") statusEl.classList.add("is-busy");
  else if (kind === "error") statusEl.classList.add("is-error");
  else if (kind === "seat") statusEl.classList.add("is-seat");
  if (text != null) statusTextEl.textContent = text;
}

// ── DOM 构建小工具 ───────────────────────────────────────
export function el(tag, className, html) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (html != null) node.innerHTML = html;
  return node;
}
```

- [ ] **Step 3: 创建 `session.js`**

```js
// session.js —— thread_id / session_user_id 持久化 + 共享会话状态 state + 身份/会话 pill 渲染。
import { threadPill, identityPill } from "./dom.js";

// localStorage 里存 thread_id 的 key（新 key + 旧版兼容 key）。
// ⚠️ 必须声明在下面 state 初始化之前：state 初始化会调用 loadThreadId()，
//    它引用这两个 const；若声明挪到 state 初始化之后，会触发 const 暂时性死区(TDZ)
//    的 ReferenceError，导致整个模块在此中断、所有事件绑定都不执行
//    （表现为"按钮/回车没反应"）。
export const THREAD_STORAGE_KEY = "relaydesk_thread_id";
export const LEGACY_THREAD_STORAGE_KEY = "echomind_thread_id";
export const SESSION_USER_STORAGE_KEY = "relaydesk_session_user_id";

export function loadThreadId() {
  let id = localStorage.getItem(THREAD_STORAGE_KEY);
  if (!id) {
    id = localStorage.getItem(LEGACY_THREAD_STORAGE_KEY);
    if (id) {
      localStorage.setItem(THREAD_STORAGE_KEY, id);
      localStorage.removeItem(LEGACY_THREAD_STORAGE_KEY);
    }
  }
  if (!id) {
    id = newThreadId();
    localStorage.setItem(THREAD_STORAGE_KEY, id);
  }
  return id;
}

export function newThreadId() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return "t-" + Date.now() + "-" + Math.random().toString(16).slice(2);
}

export function loadSessionUserId() {
  return (localStorage.getItem(SESSION_USER_STORAGE_KEY) || "").trim();
}

export function saveSessionUserId(userId) {
  const value = (userId || "").trim();
  if (value) localStorage.setItem(SESSION_USER_STORAGE_KEY, value);
  else localStorage.removeItem(SESSION_USER_STORAGE_KEY);
}

// ── 会话状态（跨模块共享的可变单例；其他模块只做属性赋值，不重新赋值整个 state）──
export const state = {
  threadId: loadThreadId(),
  sessionUserId: loadSessionUserId(),
  seatMode: false,
  approvalMode: false,
  busy: false,
  activeStream: null,
  lastUserText: "",
};

export function renderThreadPill() {
  threadPill.textContent = "会话 " + state.threadId.slice(0, 8);
}

export function renderIdentityPill() {
  identityPill.textContent = "身份 " + (state.sessionUserId || "游客");
}
```

- [ ] **Step 4: 创建 `pipeline.js`**

```js
// pipeline.js —— 决策轨迹 pipeline 状态机 + 转人工/审批模式切换。
//
// enterSeatMode/exitSeatMode/enterApprovalMode/exitApprovalMode 放在这里而不是 app.js 入口，
// 是因为 sse.js 的 runStream() 在收到 interrupt 事件时需要直接调用它们；如果放在 app.js 入口，
// sse.js 要 import app.js、app.js 又要 import sse.js 的 runStream，会形成循环依赖。
// pipeline.js 不依赖 sse.js/messages.js，放在这里没有循环问题。
import {
  stageEls, valueEls, pipelineHintEl,
  seatFlowEl, seatStepEls, seatReplyTextEl,
  seatBanner, seatBannerTextEl, composerEl, inputEl,
  approvalActionsEl, sendBtn, approveBtn, rejectBtn,
  setStatus,
} from "./dom.js";
import { state } from "./session.js";

export const STAGE_ORDER = ["intent", "rag", "route", "tool", "answer"];

export function setStage(name, status, value) {
  const node = stageEls[name];
  if (!node) return;
  node.classList.remove("is-active", "is-done", "is-seat", "is-skipped");
  if (status) node.classList.add("is-" + status);
  if (value != null) valueEls[name].textContent = value;
}

// 每轮用户发新消息前：整条 pipeline 重置为 idle。
export function resetPipeline() {
  for (const name of STAGE_ORDER) {
    stageEls[name].classList.remove("is-active", "is-done", "is-seat", "is-skipped");
    valueEls[name].textContent = "待命";
  }
  pipelineHintEl.textContent = "本轮 Agent 内部决策";
}

// 推进到某阶段：把它点亮为 active，并把它之前的阶段标记为 done（连接线充能）。
export function advancePipeline(name, value) {
  const idx = STAGE_ORDER.indexOf(name);
  STAGE_ORDER.forEach((s, i) => {
    if (i < idx) {
      if (
        !stageEls[s].classList.contains("is-done") &&
        !stageEls[s].classList.contains("is-skipped")
      ) setStage(s, "done");
    }
  });
  setStage(name, "active", value);
}

// 完成某阶段（值保留），用于 answer 收尾。
export function completeStage(name, value) {
  setStage(name, "done", value);
}

// 转人工：把 pipeline 推入醒目的琥珀状态。
export function pipelineToSeat() {
  for (const name of STAGE_ORDER) {
    if (stageEls[name].classList.contains("is-active")) setStage(name, "done");
  }
  if (!stageEls.tool.classList.contains("is-done") && !stageEls.tool.classList.contains("is-seat")) {
    setStage("tool", "skipped", "未使用");
  }
  setStage("answer", "seat", "等待坐席接入");
  pipelineHintEl.textContent = "需要人工介入 · human-in-the-loop";
}

// 审批：同样用琥珀态提示"图已暂停"，但不展示转人工三段卡。
export function pipelineToApproval() {
  for (const name of STAGE_ORDER) {
    if (stageEls[name].classList.contains("is-active")) setStage(name, "done");
  }
  setStage("tool", "seat", "等待人工审批");
  pipelineHintEl.textContent = "敏感操作审批 · approval";
}

// ── 转人工三段状态卡：① AI 已判断 ② 等待坐席接入 ③ 人工回复 ──
export function seatFlowReset() {
  seatFlowEl.hidden = true;
  for (const k of ["judge", "wait", "reply"]) seatStepEls[k].classList.remove("is-on");
  seatReplyTextEl.textContent = "—";
}
export function seatFlowEnter() {
  seatFlowEl.hidden = false;
  seatStepEls.judge.classList.add("is-on");
  seatStepEls.wait.classList.add("is-on");
}
export function seatFlowReply(text) {
  seatStepEls.wait.classList.remove("is-on");
  seatStepEls.reply.classList.add("is-on");
  seatReplyTextEl.textContent = text || "（已回复）";
}

// ── 进入 / 退出坐席模式 ──────────────────────────────────
// ⚠️ Task 1 阶段先原样保留 "seat-mode"/"approval-mode" 字面量（不加 is- 前缀）；
//    Task 3（BEM 改名）会把这两个字符串和对应 CSS 选择器一起改成 "is-seat-mode"/"is-approval-mode"。
export function enterSeatMode(prompt) {
  state.seatMode = true;
  seatBanner.hidden = false;
  seatBannerTextEl.textContent = "已转人工 · 请以坐席身份回复用户";
  composerEl.classList.add("seat-mode");
  inputEl.placeholder = prompt || "以坐席身份回复用户… Enter 发送";
  setStatus("seat", "坐席模式");
  inputEl.focus();
}

export function exitSeatMode() {
  state.seatMode = false;
  if (!state.approvalMode) seatBanner.hidden = true;
  composerEl.classList.remove("seat-mode");
  if (!state.approvalMode) inputEl.placeholder = "输入消息，Enter 发送 · Shift+Enter 换行";
}

export function enterApprovalMode(payload) {
  state.approvalMode = true;
  seatBanner.hidden = false;
  seatBannerTextEl.textContent = (payload && payload.prompt) || "待人工审批";
  composerEl.classList.add("approval-mode");
  approvalActionsEl.hidden = false;
  sendBtn.disabled = true;
  approveBtn.disabled = state.busy;
  rejectBtn.disabled = state.busy;
  inputEl.placeholder = "审批备注（可选）";
  setStatus("seat", "审批模式");
  inputEl.focus();
}

export function exitApprovalMode() {
  state.approvalMode = false;
  if (!state.seatMode) seatBanner.hidden = true;
  composerEl.classList.remove("approval-mode");
  approvalActionsEl.hidden = true;
  approveBtn.disabled = state.busy;
  rejectBtn.disabled = state.busy;
  sendBtn.disabled = state.busy;
  if (!state.seatMode) inputEl.placeholder = "输入消息，Enter 发送 · Shift+Enter 换行";
}
```

- [ ] **Step 5: 创建 `messages.js`**

```js
// messages.js —— 消息气泡渲染 + 意图/agent 人话映射。
import { messagesEl, welcomeEl, el } from "./dom.js";
import { escapeHtml, renderMarkdown } from "./pure.js";

// ════════════════════════════════════════════════════════
// 人话映射：把技术名翻成中文友好词（集中维护，一处可改）。
// 技术原值不丢——以小字 / tooltip 形式降级呈现，方便演示「可观测」。
// ════════════════════════════════════════════════════════
export const INTENT_EMOJI = {
  technical: "🛠️", billing: "💳", complaint: "🙏", greeting: "👋",
  query: "🔎", request: "📝", escalation: "🧑‍💼", other: "💬",
};
export const INTENT_LABEL = {
  technical: "技术支持",
  billing: "账单咨询",
  complaint: "投诉处理",
  greeting: "打招呼",
  query: "信息查询",
  request: "业务请求",
  escalation: "转人工",
  other: "其他咨询",
};
export const AGENT_LABEL = {
  technical_agent: "技术支持",
  billing_agent: "账单客服",
  general_agent: "通用客服",
  escalation: "人工坐席",
};
export const AGENT_EMOJI = {
  technical_agent: "🛠️", billing_agent: "💳",
  general_agent: "💬", escalation: "🧑‍💼",
};

export function confidenceLevel(conf) {
  if (conf == null) return "";
  const c = Number(conf);
  if (c >= 0.8) return "高";
  if (c >= 0.5) return "中";
  return "低";
}

export function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

export function addUserMessage(text) {
  if (welcomeEl) welcomeEl.hidden = true;
  const wrap = el("div", "msg msg-user");
  wrap.appendChild(el("div", "bubble", escapeHtml(text)));
  messagesEl.appendChild(wrap);
  scrollToBottom();
}

// createBotMessage 的 onRetry 从原来的「隐式引用全局函数」改成「显式参数传入」：
// 原 app.js 是单文件，mountRetry() 里直接引用同文件里的 onRetry 函数；拆模块后
// messages.js 不能反过来 import app.js（app.js 已经要 import messages.js，会成环），
// 所以把 onRetry 作为 createBotMessage 的可选参数，由调用方（app.js 入口）传入。
export function createBotMessage({ fromSeat = false, onRetry } = {}) {
  const wrap = el("div", "msg msg-bot" + (fromSeat ? " from-seat" : ""));
  const meta = el("div", "meta-line");
  const bubble = el("div", "bubble typing");
  const actions = el("div", "bubble-actions");
  wrap.appendChild(meta);
  wrap.appendChild(bubble);
  wrap.appendChild(actions);
  messagesEl.appendChild(wrap);
  scrollToBottom();

  let raw = "";
  const segs = { intent: null, route: null, rag: null, tool: null };
  const titles = {};

  function renderMeta() {
    const parts = [segs.intent, segs.route, segs.rag, segs.tool].filter(Boolean);
    if (!parts.length) { meta.remove(); return; }
    meta.innerHTML = parts
      .map((p) => `<span class="meta-seg">${escapeHtml(p)}</span>`)
      .join('<span class="meta-dot" aria-hidden="true">·</span>');
    const titleText = ["intent", "route", "rag", "tool"].map((key) => titles[key]).filter(Boolean).join(" · ");
    if (titleText) meta.title = titleText;
  }

  return {
    setMetaSeg(key, main, title = "") {
      if (key in segs) segs[key] = main;
      if (title) titles[key] = title;
      renderMeta();
      scrollToBottom();
    },
    setSeatTag(main, title = "") {
      segs.route = main;
      if (title) titles.route = title;
      renderMeta();
      scrollToBottom();
    },
    appendToken(t) {
      raw += t;
      bubble.textContent = raw;
      scrollToBottom();
    },
    setText(t) {
      raw = t;
      bubble.textContent = raw;
      scrollToBottom();
    },
    finish() {
      bubble.classList.remove("typing");
      if (raw) bubble.innerHTML = renderMarkdown(raw);
      renderMeta();
    },
    mountRetry() {
      if (fromSeat) return;
      const btn = el("button", "btn-quick", "↻ 重新提问");
      btn.type = "button";
      btn.title = "重发上一条用户消息";
      btn.addEventListener("click", onRetry);
      actions.appendChild(btn);
      scrollToBottom();
    },
    markError() {
      wrap.classList.add("is-error");
      bubble.classList.remove("typing");
      renderMeta();
    },
    hasText: () => raw.length > 0,
  };
}

export function addSysLine(text) {
  messagesEl.appendChild(el("div", "sys-line", escapeHtml(text)));
  scrollToBottom();
}
```

- [ ] **Step 6: 创建 `sse.js`**

```js
// sse.js —— SSE 事件流解析 + 一次"流式请求"（chat/resume 共用）的统一处理。
import { stageEls, setStatus } from "./dom.js";
import { toolLabel } from "./pure.js";
import { state } from "./session.js";
import {
  advancePipeline, setStage, completeStage,
  pipelineToSeat, pipelineToApproval, seatFlowReset, seatFlowEnter,
  enterSeatMode, enterApprovalMode,
} from "./pipeline.js";
import { INTENT_LABEL, AGENT_LABEL, addSysLine } from "./messages.js";

// ── SSE 解析：把 fetch 的字节流按 `\n\n` 切成事件，回调每条 JSON ──
export async function readSSE(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const chunk = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      const jsonText = line.slice(5).trim();
      if (!jsonText) continue;
      try {
        onEvent(JSON.parse(jsonText));
      } catch (e) {
        console.warn("SSE 解析失败：", jsonText, e);
      }
    }
  }
}

// ── 一次"流式请求"的统一处理：chat 与 resume 共用 ──
//   bot：当前 Agent 气泡句柄；isResume：resume 时 pipeline 走应答阶段而非整轮重置。
export async function runStream(url, payload, bot, { isResume = false } = {}) {
  const controller = new AbortController();
  state.activeStream = controller;
  let interrupted = false;
  let failed = false;
  let answered = false;
  let toolCount = 0;
  let toolDoneCount = 0;
  let toolFinalized = false;
  const toolNames = [];

  function updateToolMeta() {
    if (toolCount > 0) {
      bot.setMetaSeg("tool", `🔧 调用 ${toolCount} 次工具`, toolNames.join(" → "));
    }
  }

  function handleToolStart(name) {
    const label = toolLabel(name);
    toolCount += 1;
    toolNames.push(label);
    toolFinalized = false;
    advancePipeline("tool", `调用 ${label}…`);
    updateToolMeta();
  }

  function handleToolDone(name) {
    const label = toolLabel(name);
    if (toolCount === 0 || toolDoneCount >= toolCount) {
      toolCount += 1;
      toolNames.push(label);
      updateToolMeta();
    }
    toolDoneCount += 1;
    toolFinalized = false;
    const done = Math.min(toolDoneCount, toolCount);
    const value = done >= toolCount ? `已返回 ${done} 次调用` : `已返回 ${done}/${toolCount} 次调用`;
    advancePipeline("tool", value);
  }

  function markToolSkippedIfNeeded() {
    if (
      toolCount === 0 &&
      !stageEls.tool.classList.contains("is-skipped") &&
      !stageEls.tool.classList.contains("is-seat")
    ) {
      setStage("tool", "skipped", "未使用");
    }
  }

  function finishToolsIfNeeded() {
    if (toolCount > 0 && !toolFinalized) {
      completeStage("tool", `完成 ${toolCount} 次调用`);
      toolFinalized = true;
    }
  }

  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify(payload),
    });
    if (!resp.ok || !resp.body) {
      bot.setText("网络异常（HTTP " + resp.status + "），请稍后重试。");
      bot.markError();
      setStatus("error", "网络异常");
      return { interrupted: false, failed: true };
    }

    await readSSE(resp, (evt) => {
      switch (evt.type) {
        case "meta": {
          if (evt.intent) {
            const cn = INTENT_LABEL[evt.intent] || evt.intent;
            const raw = (evt.confidence != null) ? Number(evt.confidence).toFixed(2) : "";
            advancePipeline("intent", raw ? `识别为${cn}，置信度 ${raw}` : `识别为${cn}`);
            const main = raw ? `${cn} · 置信度 ${raw}` : cn;
            bot.setMetaSeg("intent", main, `intent=${evt.intent}${raw ? ` confidence=${raw}` : ""}`);
          }
          break;
        }
        case "rag": {
          const sources = evt.sources || [];
          if (sources.length) {
            advancePipeline("rag", `命中 ${sources.length} 条相关资料`);
            bot.setMetaSeg(
              "rag",
              `命中 ${sources.length} 条知识`,
              "rag · " + sources.map((s) => String(s)).join(" · ")
            );
          } else {
            advancePipeline("rag", "未命中相关资料");
          }
          break;
        }
        case "route": {
          if (evt.agent) {
            const cn = AGENT_LABEL[evt.agent] || evt.agent;
            advancePipeline("route", `分配到${cn}`);
            bot.setMetaSeg("route", `分配到${cn}`, "agent=" + evt.agent);
          }
          break;
        }
        case "tool": {
          if (evt.status === "start") {
            handleToolStart(evt.name);
          } else if (evt.status === "done") {
            handleToolDone(evt.name);
          }
          break;
        }
        case "token":
          if (!answered) {
            if (toolCount > 0) finishToolsIfNeeded();
            else markToolSkippedIfNeeded();
            answered = true;
            advancePipeline("answer", "正在生成回复…");
          }
          bot.appendToken(evt.text || "");
          break;
        case "interrupt":
          interrupted = true;
          if (!bot.hasText()) {
            bot.setText(evt.kind === "approval" ? "（待人工审批，等待处理…）" : "（已转人工，等待坐席接入…）");
          }
          bot.finish();
          if (evt.kind === "approval") {
            seatFlowReset();
            pipelineToApproval();
            enterApprovalMode(evt);
            addSysLine(evt.prompt || "待人工审批");
          } else {
            pipelineToSeat();
            seatFlowEnter();
            enterSeatMode(evt.prompt);
            addSysLine(evt.prompt || "已转人工，请以坐席身份回复用户");
          }
          break;
        case "done":
          if (toolCount > 0) finishToolsIfNeeded();
          else if (!answered) markToolSkippedIfNeeded();
          bot.finish();
          if (isResume) {
            completeStage("answer", state.approvalMode ? "审批已处理" : "坐席已回复");
          } else if (answered) {
            completeStage("answer", evt.escalated ? "已转人工" : "已完成");
            bot.mountRetry();
          }
          break;
        case "error":
          failed = true;
          bot.setText(evt.message || "出了点问题，请稍后重试。");
          bot.markError();
          setStatus("error", "出错");
          break;
        default:
          break;
      }
    });
  } catch (e) {
    if (e && e.name === "AbortError") {
      return { interrupted: false, failed: false, aborted: true };
    }
    console.error(e);
    failed = true;
    bot.setText("连接中断，请检查服务是否在运行。");
    bot.markError();
    setStatus("error", "连接中断");
  } finally {
    if (state.activeStream === controller) state.activeStream = null;
  }
  return { interrupted, failed, aborted: false };
}
```

- [ ] **Step 7: 改写 `app.js`（入口）**

```js
/*
  RelayDesk 客服 Web 演示 —— 前端入口（原生 JS，无框架 / 无构建链）。

  这是拆模块后的入口文件：负责挂 DOM 事件监听、串联各模块、做页面初始化。
  具体逻辑分别在 js/pure.js（纯函数）、js/dom.js（元素引用）、js/session.js（会话状态）、
  js/pipeline.js（决策轨迹 + 转人工/审批模式）、js/messages.js（消息渲染）、
  js/sse.js（SSE 流式处理）。

  事件协议（与 server.py _stream_graph 对齐，前端绝不改）：
    meta {intent, confidence} · rag {sources[]} · route {agent} · tool {name, status} · token {text}
    interrupt {kind, action, params, prompt, user_message} · done {escalated} · error {message}
*/
import {
  messagesEl, inputEl, sendBtn, approveBtn, rejectBtn,
  identitySelect, newBtn, welcomeEl, setStatus,
} from "./js/dom.js";
import { buildChatBody, buildSeatResumeBody, buildApprovalResumeBody } from "./js/pure.js";
import {
  state, newThreadId, THREAD_STORAGE_KEY, LEGACY_THREAD_STORAGE_KEY,
  saveSessionUserId, renderThreadPill, renderIdentityPill,
} from "./js/session.js";
import {
  resetPipeline, seatFlowReset, seatFlowReply, exitSeatMode, exitApprovalMode,
} from "./js/pipeline.js";
import { addUserMessage, createBotMessage, addSysLine } from "./js/messages.js";
import { runStream } from "./js/sse.js";

// ── 发送（普通用户消息）──────────────────────────────────
async function sendUserMessage(text) {
  resetPipeline();
  seatFlowReset();
  state.lastUserText = text;
  addUserMessage(text);
  const bot = createBotMessage({ onRetry });
  const { interrupted, aborted } = await runStream(
    "/api/chat",
    buildChatBody(text, state.threadId, state.sessionUserId),
    bot
  );
  if (aborted) return false;
  return interrupted;
}

// ── 提交坐席回复（resume）──────────────────────────────────
async function sendSeatReply(text) {
  addUserMessage(text);
  const bot = createBotMessage({ fromSeat: true });
  bot.setSeatTag("人工坐席已接管", "agent=escalation");
  seatFlowReply(text);
  const { interrupted, failed, aborted } = await runStream(
    "/api/resume",
    buildSeatResumeBody(state.threadId, state.sessionUserId, text),
    bot,
    { isResume: true }
  );
  if (aborted) return;
  if (failed) {
    addSysLine("坐席回复提交失败，请重试");
    return;
  }
  if (!interrupted) {
    exitSeatMode();
    setStatus(null, "就绪");
  } else if (state.approvalMode) {
    exitSeatMode();
  }
}

// ── 提交审批决定（resume）──────────────────────────────────
async function sendApprovalDecision(approved) {
  if (!state.approvalMode || state.busy) return;
  setBusy(true);
  const note = inputEl.value.trim();
  inputEl.value = "";
  autoGrow();
  addSysLine((approved ? "审批通过" : "审批驳回") + (note ? "：" + note : ""));

  const bot = createBotMessage();
  try {
    const { interrupted, failed, aborted } = await runStream(
      "/api/resume",
      buildApprovalResumeBody(state.threadId, state.sessionUserId, approved, note),
      bot,
      { isResume: true }
    );
    if (aborted) {
      return;
    }
    if (failed) {
      inputEl.value = note;
      autoGrow();
      addSysLine("审批提交失败，请重试");
    } else if (!interrupted) {
      exitApprovalMode();
      setStatus(null, "就绪");
    } else if (state.seatMode) {
      exitApprovalMode();
    }
  } finally {
    setBusy(false);
    inputEl.focus();
  }
}

// ── 输入框统一提交入口 ───────────────────────────────────
async function onSubmit() {
  if (state.busy || state.approvalMode) return;
  const text = inputEl.value.trim();
  if (!text) return;
  inputEl.value = "";
  autoGrow();
  setBusy(true);
  try {
    if (state.seatMode) {
      await sendSeatReply(text);
    } else {
      await sendUserMessage(text);
    }
  } finally {
    setBusy(false);
    inputEl.focus();
  }
}

function setBusy(b) {
  state.busy = b;
  sendBtn.disabled = b || state.approvalMode;
  inputEl.disabled = b;
  approveBtn.disabled = b;
  rejectBtn.disabled = b;
  if (b) {
    setStatus("busy", "推理中");
  } else if (state.approvalMode) {
    setStatus("seat", "审批模式");
  } else if (!state.seatMode) {
    setStatus(null, "就绪");
  }
}

// 输入框高度自适应
function autoGrow() {
  inputEl.style.height = "auto";
  inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + "px";
}

// ── 结束会话：重置 thread_id + 清空对话 + 退出坐席模式 + 重置 pipeline / 三段卡 ──
function resetSession({ keepIdentity = true, notice = "" } = {}) {
  if (state.activeStream) {
    state.activeStream.abort();
    state.activeStream = null;
  }
  state.busy = false;
  if (!keepIdentity) {
    state.sessionUserId = "";
    saveSessionUserId("");
    if (identitySelect) identitySelect.value = "";
  }
  state.threadId = newThreadId();
  localStorage.setItem(THREAD_STORAGE_KEY, state.threadId);
  localStorage.removeItem(LEGACY_THREAD_STORAGE_KEY);
  state.lastUserText = "";
  exitSeatMode();
  exitApprovalMode();
  resetPipeline();
  seatFlowReset();
  setBusy(false);
  setStatus(null, "就绪");
  while (messagesEl.children.length > 1) {
    messagesEl.removeChild(messagesEl.lastChild);
  }
  if (welcomeEl) welcomeEl.hidden = false;
  renderThreadPill();
  renderIdentityPill();
  if (notice) addSysLine(notice);
  inputEl.focus();
}

function onIdentityChange() {
  const nextUserId = (identitySelect.value || "").trim();
  if (nextUserId === state.sessionUserId) return;
  state.sessionUserId = nextUserId;
  saveSessionUserId(nextUserId);
  const label = nextUserId || "游客";
  resetSession({
    keepIdentity: true,
    notice: `已切换 demo 身份为 ${label}，并开启新会话。`,
  });
}

// ── 重新提问：重发上一条用户消息（坐席模式 / 忙时禁用） ──
async function onRetry() {
  if (state.busy || state.seatMode || state.approvalMode || !state.lastUserText) return;
  const text = state.lastUserText;
  setBusy(true);
  try {
    await sendUserMessage(text);
  } finally {
    setBusy(false);
    inputEl.focus();
  }
}

// ── 事件绑定 ─────────────────────────────────────────────
sendBtn.addEventListener("click", onSubmit);
approveBtn.addEventListener("click", () => sendApprovalDecision(true));
rejectBtn.addEventListener("click", () => sendApprovalDecision(false));
newBtn.addEventListener("click", resetSession);
identitySelect.addEventListener("change", onIdentityChange);
inputEl.addEventListener("input", autoGrow);
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    onSubmit();
  }
});

// 初始化
identitySelect.value = state.sessionUserId;
renderThreadPill();
renderIdentityPill();
resetPipeline();
seatFlowReset();
inputEl.focus();
```

- [ ] **Step 8: 改 `index.html` 的 `<script>` 标签**

打开 `langgraph_cs/web/static/index.html`，把最后一行（原 200 行）：

```html
  <script src="/static/app.js"></script>
```

改成：

```html
  <script type="module" src="/static/app.js"></script>
```

（`app.js` 内部用 `import ... from "./js/xxx.js"`，浏览器按相对路径解析成 `/static/js/xxx.js`，FastAPI 现有的 `StaticFiles` 挂载已经能直接serve，不用改 `server.py`。）

- [ ] **Step 9: 改写 `test_markdown.mjs`，去掉 marker/vm 沙箱**

打开 `langgraph_cs/web/tests/test_markdown.mjs`，把第 1–58 行（文件头注释 + marker 抽取 + vm 沙箱 + typeof 断言）整体替换成：

```js
/*
  renderMarkdown 等纯函数的离线单测（原生 Node，无构建链、无 DOM、无网络）。

  策略：pure.js 是零 DOM 依赖的 ES module（拆分自原单文件 app.js），
  可以直接 import 求值，不再需要注释 marker + vm 沙箱硬抠代码块。

  核心覆盖（P0-2 修复点）：
    项间夹空行的有序列表 -> 合并进同一个 <ol>，靠浏览器自动顺序编号（不再每项都「1.」）。

  运行：
    node langgraph_cs/web/tests/test_markdown.mjs
*/
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";

const __dirname = dirname(fileURLToPath(import.meta.url));
const PURE_JS = join(__dirname, "..", "static", "js", "pure.js");

const {
  renderMarkdown,
  toolLabel,
  buildChatBody,
  buildSeatResumeBody,
  buildApprovalResumeBody,
} = await import(pathToFileURL(PURE_JS));

assert.equal(typeof renderMarkdown, "function", "renderMarkdown 未成功导出");
assert.equal(typeof toolLabel, "function", "toolLabel 未成功导出");
assert.equal(typeof buildChatBody, "function", "buildChatBody 未成功导出");
assert.equal(typeof buildSeatResumeBody, "function", "buildSeatResumeBody 未成功导出");
assert.equal(typeof buildApprovalResumeBody, "function", "buildApprovalResumeBody 未成功导出");
```

第 60 行开始（`// ── 断言小工具 ──` 及之后的全部测试用例代码）保持完全不变，不用动。

- [ ] **Step 10: 跑测试**

```bash
node langgraph_cs/web/tests/test_markdown.mjs
```

期望：输出 9 个 `✓ ...`，末尾 `全部 markdown 单测通过 ✅（9 个用例，未触网/未触 DOM）`，退出码 0。

```bash
langgraph_cs/.venv/bin/python -m langgraph_cs.web.tests.test_server_offline
```

期望：输出一系列 `✓ ...`，末尾 `全部离线用例通过 ✅（未发起任何真实网络/ LLM 调用）`，退出码 0。

- [ ] **Step 11: 浏览器手工走查**

```bash
cd /Users/xuyangli/projects/03_个人项目/langgraph-cs-agent
langgraph_cs/.venv/bin/python -m langgraph_cs.web
```

浏览器打开 `http://127.0.0.1:8000`，打开开发者工具 Console 确认无红色报错（尤其是 `Uncaught SyntaxError`/`Uncaught ReferenceError`/`Failed to load module`），然后：
1. 发一条消息（如"你好"），确认决策轨迹 5 个 stage 依次点亮，收到回答。
2. 回答气泡下方出现"↻ 重新提问"按钮，点击后重新发送同一条消息。
3. 输入"转人工"（或触发 escalation 意图的话术），确认系统条 + 右侧转人工三段状态卡正确显示，提交一条坐席回复。
4. 点击"结束会话"，确认对话清空、thread_id 变化、开场气泡重新出现。
5. 切换顶部"身份"下拉，确认会话重置并提示已切换身份。
6. 缩小浏览器窗口到 <900px、<560px，确认布局折叠正常。

以上任何一步和重构前行为不一致（不只是报错，包括视觉/交互细节），停下来排查，不要进入 Step 12。

- [ ] **Step 12: 提交**

```bash
cd /Users/xuyangli/projects/03_个人项目/langgraph-cs-agent
git add langgraph_cs/web/static/js/ langgraph_cs/web/static/app.js langgraph_cs/web/static/index.html langgraph_cs/web/tests/test_markdown.mjs
git commit -m "$(cat <<'EOF'
refactor(web): app.js 拆分成职责化 ES module

按纯函数/DOM引用/会话状态/决策轨迹/消息渲染/SSE流式六个职责拆出
js/{pure,dom,session,pipeline,messages,sse}.js，app.js 只做入口接线。
test_markdown.mjs 相应改为直接 import pure.js，去掉 marker+vm 沙箱 hack。
行为不变，仅工程结构调整。
EOF
)"
```

---

## Task 2: Track B1 — CSS 令牌化（补 token，替换硬编码色值）

**Effort:** low

**Files:**
- Modify: `langgraph_cs/web/static/style.css`

**Interfaces:** 无新增函数/组件，纯 CSS 自定义属性新增 + 值替换。

- [ ] **Step 1: 在 `:root` 补充 4 组 token**

打开 `langgraph_cs/web/static/style.css`，在现有 `:root { ... }` 块内、`--glow-seat` 那一行（第 57 行）之后、闭合 `}`（第 58 行）之前插入：

```css

  /* ── 补充 token（Phase 1 工程纪律清理新增）── */
  --danger: #ff6b6b;
  --danger-soft: #ff9d9d;
  --danger-line: rgba(255, 107, 107, .35);
  --danger-bg: rgba(255, 107, 107, .1);
  --seat-2: #e09238;          /* 按钮渐变深色端 */
  --ink-on-accent: #0b1020;   /* 亮色按钮/渐变上的深色文字；与 --bg 同值但语义不同，不复用 --bg */
```

- [ ] **Step 2: 替换 `--danger` 系列的 4 处硬编码**

逐一查找并替换（`grep -n '#ff6b6b\|#ff9d9d' langgraph_cs/web/static/style.css` 定位）：

| 位置 | 旧 | 新 |
|---|---|---|
| `.status.is-error .status-dot` 规则里 | `background: #ff6b6b; box-shadow: 0 0 8px rgba(255, 107, 107, .7);` | `background: var(--danger); box-shadow: 0 0 8px rgba(255, 107, 107, .7);` |
| 错误提示文字颜色（`color: #ff9d9d;` 那条规则，边框 `rgba(255, 107, 107, .35)`） | `border-color: rgba(255, 107, 107, .35);` / `color: #ff9d9d;` | `border-color: var(--danger-line);` / `color: var(--danger-soft);` |
| 另一处同结构（`background: rgba(255, 107, 107, .1); color: #ff9d9d; border-color: rgba(255, 107, 107, .35);`） | 同上三行 | `background: var(--danger-bg); color: var(--danger-soft); border-color: var(--danger-line);` |

（`box-shadow` 里的 `rgba(255, 107, 107, .7)` 是发光效果专用的第三个透明度值，不在 `--danger-*` 四个 token 里，保持字面量不变——不要为了"消灭所有硬编码"强行新增一个只用一次的 token。）

- [ ] **Step 3: 替换 `--seat-2` 的 2 处硬编码**

```bash
grep -n '#e09238' langgraph_cs/web/static/style.css
```

两处都形如 `linear-gradient(135deg, #e09238, var(--seat))`，替换成 `linear-gradient(135deg, var(--seat-2), var(--seat))`。

- [ ] **Step 4: 替换 `--ink-on-accent` 的 5 处硬编码**

```bash
grep -n '#0b1020' langgraph_cs/web/static/style.css
```

除了 `:root` 里 `--bg: #0b1020;` 这一行本身（不要动），其余 5 处 `color: #0b1020;`（`.identity-select option`、`.btn-send` 文字色、`.msg-user .bubble strong`、`.btn-approve` 文字色等）全部替换成 `color: var(--ink-on-accent);`。

- [ ] **Step 5: 验证无遗漏硬编码**

```bash
grep -n '#ff6b6b\|#ff9d9d\|#e09238' langgraph_cs/web/static/style.css
```
期望：无输出（0 处）。

```bash
grep -c '#0b1020' langgraph_cs/web/static/style.css
```
期望：`1`（只剩 `:root` 里 `--bg: #0b1020;` 那一处定义本身）。

- [ ] **Step 6: 浏览器核对**

```bash
langgraph_cs/.venv/bin/python -m langgraph_cs.web
```

浏览器打开页面，因为是"字面量 → 等值 token"的替换（数值完全相同），理论上零视觉差异；打开开发者工具确认 `style.css` 无解析错误（Elements 面板里选中 `.status-dot`、`.btn-send`、`.msg-user .bubble strong` 等元素，Computed 面板颜色应和替换前一致）。

- [ ] **Step 7: 提交**

```bash
git add langgraph_cs/web/static/style.css
git commit -m "$(cat <<'EOF'
refactor(web): CSS 补充 danger/seat-2/ink-on-accent token，替换硬编码色值

26 处硬编码色值里剩下的 11 处（错误红系、按钮渐变深色端、亮色按钮文字色）
补上对应语义 token，全部等值替换，视觉不变。
EOF
)"
```

---

## Task 3: Track B2 — BEM 改名（CSS + HTML + JS 三处联动）

**Effort:** high —— 改动面最大、跨文件一致性要求高、易漏改且不易被现有测试兜住，逐步来、每步都验证。

**Files:**
- Modify: `langgraph_cs/web/static/index.html`
- Modify: `langgraph_cs/web/static/style.css`
- Modify: `langgraph_cs/web/static/js/pipeline.js`
- Modify: `langgraph_cs/web/static/js/messages.js`

**⚠️ 高风险陷阱：以下 11 个字符串在 HTML 里同时被用作 `class` 值和 `id` 值（同一个元素上，如 `<span class="status-dot" id="status-dot">`）。只改 `class` 属性和 CSS class 选择器，`id` 属性和 JS 里所有 `$("#...")` 一律原样保留，绝不能碰：**

`status-dot`、`status-text`、`identity-select`、`identity-pill`、`thread-pill`、`btn-send`、`btn-approve`、`btn-reject`、`approval-actions`、`seat-banner-text`、`pipeline-hint`。

（`identity-pill`/`thread-pill`/`btn-send` 本身不改名——见前面"偏差说明"第 3 点——列在这里是提醒：即使某个字符串本次不改名，也不要在别的改名操作中被正则误伤。）

**改名表**（CSS 选择器用 `.` 前缀形式书写，表示"这个 token 前面一定跟着 `.`，据此在文件里做精确字符串替换"；HTML/JS 里对应去掉 `.` 用裸词替换）：

| 旧 token | 新 token | 出现位置 |
|---|---|---|
| `.msg-user` | `.msg--user` | CSS + JS(`messages.js`) |
| `.msg-bot` | `.msg--bot` | CSS + JS |
| `.from-seat` | `.msg--from-seat` | CSS + JS |
| `.welcome` | `.msg--welcome` | CSS + HTML |
| `.bubble-actions` | `.msg__actions` | CSS + JS（**必须先改这个，再改下面的 `.bubble`**，否则 `.bubble-actions` 里的 `.bubble` 子串会被提前吃掉） |
| `.bubble` | `.msg__bubble` | CSS + HTML + JS |
| `.meta-line` | `.msg__meta` | CSS + JS |
| `.meta-seg` | `.msg__meta-seg` | CSS + JS |
| `.meta-dot` | `.msg__meta-dot` | CSS + JS |
| `.sys-line` | `.messages__sys-line` | CSS + JS |
| `.stage-rail` | `.stage__rail` | CSS + HTML |
| `.stage-node` | `.stage__node` | CSS + HTML |
| `.stage-idx` | `.stage__idx` | CSS + HTML |
| `.stage-line` | `.stage__line` | CSS + HTML |
| `.stage-body` | `.stage__body` | CSS + HTML |
| `.stage-label` | `.stage__label` | CSS + HTML |
| `.stage-value` | `.stage__value` | CSS + HTML |
| `.seat-step-tag` | `.seat-step__tag` | CSS + HTML |
| `.seat-step-text` | `.seat-step__text` | CSS + HTML |
| `.seat-flow-title` | `.seat-flow__title` | CSS + HTML |
| `.seat-banner-dot` | `.seat-banner__dot` | CSS + HTML |
| `.seat-banner-text` | `.seat-banner__text` | HTML only（CSS 里没有这条规则） |
| `.status-dot` | `.status__dot` | CSS + HTML（id 不变） |
| `.status-text` | `.status__text` | CSS + HTML（id 不变） |
| `.brand-mark` | `.brand__mark` | CSS + HTML |
| `.brand-text` | `.brand__text` | CSS + HTML |
| `.brand-name` | `.brand__name` | CSS + HTML |
| `.brand-sub` | `.brand__sub` | CSS + HTML |
| `.identity-select-wrap` | `.identity` | CSS + HTML（**必须先改这个，再改下面的 `.identity-select`**，否则子串被提前吃掉） |
| `.identity-label` | `.identity__label` | CSS + HTML |
| `.identity-select` | `.identity__select` | CSS + HTML（id 不变） |
| `.topbar-actions` | `.topbar__actions` | CSS + HTML |
| `.composer-row` | `.composer__row` | CSS + HTML |
| `.input`（仅 composer 里的 textarea，见下方说明） | `.composer__input` | CSS + HTML |
| `.approval-actions` | `.composer__approval-actions` | CSS + HTML（id 不变） |
| `.pipeline-head` | `.pipeline__head` | CSS + HTML |
| `.pipeline-title` | `.pipeline__title` | CSS + HTML |
| `.pipeline-hint` | `.pipeline__hint` | CSS + HTML（id 不变） |
| `.pipeline-foot` | `.pipeline__foot` | CSS + HTML |
| `.btn-approve`（组合类里的第二个类） | `.btn-approval--approve` | CSS + HTML（id 不变） |
| `.btn-reject`（组合类里的第二个类） | `.btn-approval--reject` | CSS + HTML（id 不变） |
| `seat-mode`（状态类，JS `classList` 里，无 `.` 前缀写法因为是拼接字符串） | `is-seat-mode` | CSS + JS(`pipeline.js`) |
| `approval-mode`（状态类） | `is-approval-mode` | CSS + JS(`pipeline.js`) |
| `typing`（状态类） | `is-typing` | CSS + JS(`messages.js`) |

**不改**（已是合法的单词 BEM 块，无共享基础样式，强行拆分反而要动 CSS 结构）：`.app`、`.layout`、`.chat`、`.topbar`、`.messages`（块名本身）、`.composer`（块名本身）、`.pipeline`（块名本身）、`.stages`、`.seat-steps`、`.status`（块名本身）、`.btn-ghost`、`.btn-send`、`.btn-quick`、`.btn-approval`（块名本身）、`.identity-pill`、`.thread-pill`、`.msg`（块名本身）、`.stage`（块名本身）、`.seat-step`（块名本身）、`.seat-banner`（块名本身）、`.seat-flow`（块名本身）、`.brand`（块名本身）。

- [ ] **Step 1: 改写 `index.html`**

把整个 `<body>...</body>`（原第 21–201 行）替换成（`<head>` 部分不变；脚本标签已在 Task 1 加了 `type="module"`，这里延续）：

```html
<body>
  <div class="app">
    <!-- ── 顶部品牌栏 ── -->
    <header class="topbar">
      <div class="brand">
        <span class="brand__mark" aria-hidden="true">ʕ•ᴥ•ʔ</span>
        <div class="brand__text">
          <span class="brand__name">RelayDesk</span>
          <span class="brand__sub">LangGraph 多 Agent 智能客服 · 决策可观测</span>
        </div>
      </div>
      <div class="topbar__actions">
        <span class="status" id="status" title="服务连接状态">
          <span class="status__dot" id="status-dot"></span>
          <span class="status__text" id="status-text">就绪</span>
        </span>
        <label class="identity" title="Demo 身份：客户端声明，非认证">
          <span class="identity__label">身份</span>
          <select id="identity-select" class="identity__select" aria-label="选择 demo 身份">
            <option value="">游客（未登录）</option>
            <option value="user_001">user_001</option>
            <option value="user_002">user_002</option>
            <option value="user_003">user_003</option>
            <option value="user_004">user_004</option>
            <option value="user_005">user_005</option>
            <option value="user_006">user_006</option>
            <option value="user_007">user_007</option>
            <option value="user_008">user_008</option>
            <option value="user_009">user_009</option>
            <option value="user_010">user_010</option>
          </select>
        </label>
        <span class="identity-pill" id="identity-pill" title="当前 demo 身份">身份 游客</span>
        <span class="thread-pill" id="thread-pill" title="当前会话 thread_id">会话 —</span>
        <button class="btn-ghost" id="btn-new" title="结束当前会话并开启新 thread_id">结束会话</button>
      </div>
    </header>

    <!-- 转人工系统条（默认隐藏，命中 interrupt 时显示）。
         低调细长、左对齐，不喧宾夺主。 -->
    <div class="seat-banner" id="seat-banner" hidden>
      <span class="seat-banner__dot" aria-hidden="true"></span>
      <span class="seat-banner__text" id="seat-banner-text">已转人工 · 请以坐席身份回复用户</span>
    </div>

    <!-- ── 主体：左对话区 + 右决策轨迹 ── -->
    <div class="layout">
      <!-- 左：对话区 -->
      <section class="chat" aria-label="对话区">
        <main class="messages" id="messages">
          <!-- 开场提示气泡（用户发出第一条消息后折叠隐藏，省空间） -->
          <div class="msg msg--bot msg--welcome" id="welcome">
            <div class="msg__bubble">
              你好，我是 RelayDesk 智能客服 ʕ•ᴥ•ʔ<br />
              问我技术或账单问题，右侧「<strong>决策轨迹</strong>」会随我思考实时点亮：意图 → 检索 → 路由 → 应答。<br />
              说一句「<em>转人工</em>」可体验界面暂停 + 坐席接管。
            </div>
          </div>
        </main>

        <!-- 底部输入区 -->
        <footer class="composer" id="composer">
          <div class="composer__row">
            <textarea
              id="input"
              class="composer__input"
              rows="1"
              placeholder="输入消息，Enter 发送 · Shift+Enter 换行"
              autocomplete="off"
            ></textarea>
            <button class="btn-send" id="btn-send" aria-label="发送">
              <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
                <path fill="currentColor" d="M3.4 20.4 21 12 3.4 3.6 3 10l12 2-12 2z" />
              </svg>
            </button>
          </div>
          <div class="composer__approval-actions" id="approval-actions" hidden>
            <button class="btn-approval btn-approval--approve" id="btn-approve" type="button">批准</button>
            <button class="btn-approval btn-approval--reject" id="btn-reject" type="button">驳回</button>
          </div>
          <!-- 「重新提问」已移到每条回答气泡下方做成快捷操作（见 app.js mountRetry），此处不再放置 -->
        </footer>
      </section>

      <!-- 右：决策轨迹侧栏（signature）。窄屏时折叠为对话上方横向条。 -->
      <aside class="pipeline" id="pipeline" aria-label="决策轨迹">
        <div class="pipeline__head">
          <span class="pipeline__title">决策轨迹</span>
          <span class="pipeline__hint" id="pipeline-hint">本轮 Agent 内部决策</span>
        </div>

        <ol class="stages" id="stages">
          <!-- ① 意图识别 -->
          <li class="stage" data-stage="intent" id="stage-intent">
            <div class="stage__rail" aria-hidden="true">
              <span class="stage__node"><span class="stage__idx">1</span></span>
              <span class="stage__line"></span>
            </div>
            <div class="stage__body">
              <span class="stage__label">意图识别</span>
              <span class="stage__value" id="value-intent">待命</span>
            </div>
          </li>

          <!-- ② 知识检索 -->
          <li class="stage" data-stage="rag" id="stage-rag">
            <div class="stage__rail" aria-hidden="true">
              <span class="stage__node"><span class="stage__idx">2</span></span>
              <span class="stage__line"></span>
            </div>
            <div class="stage__body">
              <span class="stage__label">知识库检索</span>
              <span class="stage__value" id="value-rag">待命</span>
            </div>
          </li>

          <!-- ③ 路由 -->
          <li class="stage" data-stage="route" id="stage-route">
            <div class="stage__rail" aria-hidden="true">
              <span class="stage__node"><span class="stage__idx">3</span></span>
              <span class="stage__line"></span>
            </div>
            <div class="stage__body">
              <span class="stage__label">路由分发</span>
              <span class="stage__value" id="value-route">待命</span>
            </div>
          </li>

          <!-- ④ 业务工具 -->
          <li class="stage" data-stage="tool" id="stage-tool">
            <div class="stage__rail" aria-hidden="true">
              <span class="stage__node"><span class="stage__idx">4</span></span>
              <span class="stage__line"></span>
            </div>
            <div class="stage__body">
              <span class="stage__label">业务工具</span>
              <span class="stage__value" id="value-tool">待命</span>
            </div>
          </li>

          <!-- ⑤ 应答 -->
          <li class="stage" data-stage="answer" id="stage-answer">
            <div class="stage__rail" aria-hidden="true">
              <span class="stage__node"><span class="stage__idx">5</span></span>
            </div>
            <div class="stage__body">
              <span class="stage__label">生成应答</span>
              <span class="stage__value" id="value-answer">待命</span>
            </div>
          </li>
        </ol>

        <!-- 转人工三段状态（默认隐藏，命中 interrupt / resume 时分段显示）。
             ① AI 已判断：需要人工 ② 系统状态：等待坐席接入 ③ 人工回复：<内容> -->
        <div class="seat-flow" id="seat-flow" hidden>
          <div class="seat-flow__title">转人工进度</div>
          <ol class="seat-steps">
            <li class="seat-step" id="seat-step-judge">
              <span class="seat-step__tag">AI 已判断</span>
              <span class="seat-step__text">需要人工介入</span>
            </li>
            <li class="seat-step" id="seat-step-wait">
              <span class="seat-step__tag">系统状态</span>
              <span class="seat-step__text">等待坐席接入</span>
            </li>
            <li class="seat-step" id="seat-step-reply">
              <span class="seat-step__tag">人工回复</span>
              <span class="seat-step__text" id="seat-step-reply-text">—</span>
            </li>
          </ol>
        </div>

        <p class="pipeline__foot">
          每轮对话实时呈现 <code>intent → rag → route → tool → answer</code>
        </p>
      </aside>
    </div>
  </div>

  <script type="module" src="/static/app.js"></script>
</body>
```

- [ ] **Step 2: 改 `style.css` —— 逐 token 替换**

按上面改名表，从上到下依次在 `style.css` 里查找每个 `.旧token` 字符串并替换成 `.新token`（**`.bubble-actions` 必须先于 `.bubble` 处理；`.identity-select-wrap` 必须先于 `.identity-select` 处理**，原因见改名表内注释）。

有 5 处是"一行里同时含多个待替换 token"或"值也要跟着变"，单独处理：

第 137–141 行（分组选择器，只改这一个 token，其余四个不动）：
```css
.status,
.identity-select-wrap,
.identity-pill,
.thread-pill,
.btn-ghost {
```
改成：
```css
.status,
.identity,
.identity-pill,
.thread-pill,
.btn-ghost {
```

`seat-mode`/`approval-mode`/`typing` 这三个状态类没有 `.` 前缀能唯一定位（它们总是紧跟在另一个类名后面组成复合选择器），按下面 6 行精确替换：

```css
.composer.seat-mode { background: var(--seat-bg); border-top-color: var(--seat-line); }
.composer.seat-mode .input { border-color: var(--seat-line); background: rgba(255, 180, 84, .06); }
.composer.seat-mode .input:focus,
.composer.seat-mode .input:focus-visible { border-color: var(--seat); }
.composer.seat-mode .btn-send { background: linear-gradient(135deg, var(--seat-2), var(--seat)); }
```
（注意最后一行 `var(--seat-2)` 是 Task 2 已经替换过的，不是 `#e09238` 了）
改成：
```css
.composer.is-seat-mode { background: var(--seat-bg); border-top-color: var(--seat-line); }
.composer.is-seat-mode .composer__input { border-color: var(--seat-line); background: rgba(255, 180, 84, .06); }
.composer.is-seat-mode .composer__input:focus,
.composer.is-seat-mode .composer__input:focus-visible { border-color: var(--seat); }
.composer.is-seat-mode .btn-send { background: linear-gradient(135deg, var(--seat-2), var(--seat)); }
```

```css
.composer.approval-mode { background: var(--seat-bg); border-top-color: var(--seat-line); }
.composer.approval-mode .input { border-color: var(--seat-line); background: rgba(255, 180, 84, .06); }
.composer.approval-mode .input:focus,
.composer.approval-mode .input:focus-visible { border-color: var(--seat); }
```
改成：
```css
.composer.is-approval-mode { background: var(--seat-bg); border-top-color: var(--seat-line); }
.composer.is-approval-mode .composer__input { border-color: var(--seat-line); background: rgba(255, 180, 84, .06); }
.composer.is-approval-mode .composer__input:focus,
.composer.is-approval-mode .composer__input:focus-visible { border-color: var(--seat); }
```

`.bubble.typing::after { ... }`（出现 2 处：主规则 + `prefers-reduced-motion` 媒体查询里那条）都改成 `.msg__bubble.is-typing::after { ... }`。

`.btn-approve`/`.btn-reject` 两条独立规则块（约第 551–558 行）：
```css
.btn-approve {
  color: var(--ink-on-accent);
  background: linear-gradient(135deg, var(--seat-2), var(--seat));
}
.btn-reject {
  color: var(--seat-soft);
  background: rgba(255, 180, 84, .08);
}
```
改成：
```css
.btn-approval--approve {
  color: var(--ink-on-accent);
  background: linear-gradient(135deg, var(--seat-2), var(--seat));
}
.btn-approval--reject {
  color: var(--seat-soft);
  background: rgba(255, 180, 84, .08);
}
```

`.input`（textarea）相关的独立规则块（约第 490–512 行）——`.input`/`.input:focus`/`.input:focus-visible`/`.input::placeholder` 四条全部把 `.input` 换成 `.composer__input`（这几条本身就是 composer 输入框专用规则，不用额外确认上下文）。

- [ ] **Step 3: 改 `js/pipeline.js` 的状态类字符串**

在 `enterSeatMode`/`exitSeatMode` 里把 `composerEl.classList.add("seat-mode")` 改成 `composerEl.classList.add("is-seat-mode")`，`composerEl.classList.remove("seat-mode")` 改成 `composerEl.classList.remove("is-seat-mode")`；在 `enterApprovalMode`/`exitApprovalMode` 里把 `"approval-mode"` 同样改成 `"is-approval-mode"`（`.add`/`.remove` 各一处，共 4 处改动）。

- [ ] **Step 4: 改 `js/messages.js` 的类名字符串**

```js
export function addUserMessage(text) {
  if (welcomeEl) welcomeEl.hidden = true;
  const wrap = el("div", "msg msg--user");
  wrap.appendChild(el("div", "msg__bubble", escapeHtml(text)));
  messagesEl.appendChild(wrap);
  scrollToBottom();
}
```

```js
export function createBotMessage({ fromSeat = false, onRetry } = {}) {
  const wrap = el("div", "msg msg--bot" + (fromSeat ? " msg--from-seat" : ""));
  const meta = el("div", "msg__meta");
  const bubble = el("div", "msg__bubble is-typing");
  const actions = el("div", "msg__actions");
  ...
```

`renderMeta()` 内部：

```js
    meta.innerHTML = parts
      .map((p) => `<span class="msg__meta-seg">${escapeHtml(p)}</span>`)
      .join('<span class="msg__meta-dot" aria-hidden="true">·</span>');
```

`finish()`/`markError()` 里两处 `bubble.classList.remove("typing")` 都改成 `bubble.classList.remove("is-typing")`。

`addSysLine`：

```js
export function addSysLine(text) {
  messagesEl.appendChild(el("div", "messages__sys-line", escapeHtml(text)));
  scrollToBottom();
}
```

（`mountRetry()` 里的 `el("button", "btn-quick", ...)` 不用改——`.btn-quick` 保留原名。）

- [ ] **Step 5: 验证旧类名清零**

```bash
cd langgraph_cs/web/static
grep -noE '\.(msg-user|msg-bot|from-seat|bubble-actions|meta-line|meta-seg|meta-dot|sys-line|stage-rail|stage-node|stage-idx|stage-line|stage-body|stage-label|stage-value|seat-step-tag|seat-step-text|seat-flow-title|seat-banner-dot|status-dot|status-text|brand-mark|brand-text|brand-name|brand-sub|identity-select-wrap|identity-label|identity-select|topbar-actions|composer-row|approval-actions|pipeline-head|pipeline-title|pipeline-hint|pipeline-foot)\b' style.css index.html
```
期望：无输出。`.bubble`（不带 `-actions`）单独检查（用「`.bubble` 后面跟非字母非连字符，或到行尾」代替 lookahead，本机 `grep` 是 `ugrep`，不支持 `(?!...)` 语法）：
```bash
grep -noE '\.bubble([^a-zA-Z-]|$)' style.css index.html
```
期望：无输出（说明所有裸 `.bubble` 都已变成 `.msg__bubble`；如果这条命令本身报语法错误，换成 `grep -n '\.bubble[^-]' style.css index.html` 肉眼过一遍结果里有没有漏改的裸 `.bubble`）。

```bash
grep -n 'seat-mode\|approval-mode' style.css ../js/pipeline.js
```
期望：只剩 `is-seat-mode`/`is-approval-mode`，不再有裸 `seat-mode`/`approval-mode`。

```bash
grep -n '"typing"\|\.typing\b' ../js/messages.js style.css
```
期望：无输出（全部是 `is-typing`）。

- [ ] **Step 6: id 未被误伤抽查**

```bash
grep -n 'id="status-dot"\|id="status-text"\|id="identity-select"\|id="identity-pill"\|id="thread-pill"\|id="btn-send"\|id="btn-approve"\|id="btn-reject"\|id="approval-actions"\|id="seat-banner-text"\|id="pipeline-hint"' index.html
```
期望：11 行全部原样存在（一个不少），确认前面"高风险陷阱"列的 id 没被误删或误改。

- [ ] **Step 7: 浏览器完整走查**

重复 Task 1 Step 11 的全部 6 项走查（发消息/重新提问/转人工/结束会话/切身份/窄屏折叠），这次要逐项对照颜色、间距、圆角、hover 效果和重构前**完全一致**——这是本 task 唯一的行为安全网，因为没有自动化视觉回归测试。额外检查：审批模式（如果本地能触发一次敏感操作 interrupt）下批准/驳回按钮的渐变配色是否和之前一致。

- [ ] **Step 8: 提交**

```bash
git add langgraph_cs/web/static/index.html langgraph_cs/web/static/style.css langgraph_cs/web/static/js/pipeline.js langgraph_cs/web/static/js/messages.js
git commit -m "$(cat <<'EOF'
refactor(web): CSS/HTML/JS 类名统一改成 BEM（Block__Element--Modifier）

结构类名（.msg-user/.stage-node/... 等 39 个）改成 BEM 形式；状态类统一
is-* 前缀（seat-mode/approval-mode/typing -> is-seat-mode/is-approval-mode/
is-typing）。.btn-ghost/.btn-send/.btn-quick/.identity-pill/.thread-pill
保留原名（本来就是合法的单词 BEM 块，无共享基础样式不强行拆分）。
纯改名，视觉与交互行为不变。
EOF
)"
```

---

## Task 4: Track C — 决策轨迹 HTML 服务端模板化（Jinja2）

**Effort:** medium

**Files:**
- Modify: `langgraph_cs/requirements.txt`
- Move: `langgraph_cs/web/static/index.html` → `langgraph_cs/web/templates/index.html`
- Modify: `langgraph_cs/web/server.py`

- [ ] **Step 1: 加依赖**

打开 `langgraph_cs/requirements.txt`，参考现有 `fastapi>=0.115.0` 那行的注释风格，新增一行：

```
jinja2>=3.1.0              # index.html 决策轨迹 5-stage 循环渲染，避免手写复制
```

安装到项目 venv：

```bash
langgraph_cs/.venv/bin/python -m pip install "jinja2>=3.1.0"
```

- [ ] **Step 2: 移动文件**

```bash
mkdir -p langgraph_cs/web/templates
git mv langgraph_cs/web/static/index.html langgraph_cs/web/templates/index.html
```

- [ ] **Step 3: 在 `templates/index.html` 里把 5 个 stage 的手写 `<li>` 换成循环**

把 Task 3 Step 1 里写的 `<ol class="stages" id="stages">...</ol>`（5 个 `<li class="stage" ...>` 块）替换成：

```html
        <ol class="stages" id="stages">
          {% for stage in stages %}
          <li class="stage" data-stage="{{ stage.key }}" id="stage-{{ stage.key }}">
            <div class="stage__rail" aria-hidden="true">
              <span class="stage__node"><span class="stage__idx">{{ stage.idx }}</span></span>
              {% if not loop.last %}<span class="stage__line"></span>{% endif %}
            </div>
            <div class="stage__body">
              <span class="stage__label">{{ stage.label }}</span>
              <span class="stage__value" id="value-{{ stage.key }}">待命</span>
            </div>
          </li>
          {% endfor %}
        </ol>
```

文件其余部分（`<head>`、顶栏、composer、pipeline 侧栏其他部分、`<script type="module">` 标签等）保持 Task 3 结束时的内容不变。

- [ ] **Step 4: 改 `server.py`**

`server.py` 顶部 import（第 38–40 行）：

```python
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
```

改成：

```python
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
```

`_STATIC_DIR` 定义（第 50 行）之后新增模板目录 + 实例 + stage 数据：

```python
# 静态资源目录（app.js / style.css / js/ 都在这里）。
_STATIC_DIR = Path(__file__).parent / "static"
# 模板目录（index.html 在这里，用 Jinja2 循环渲染决策轨迹 5 个 stage）。
_TEMPLATES_DIR = Path(__file__).parent / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# 决策轨迹 5 个 stage 的展示数据，供 index.html 模板循环渲染。
# ⚠️ key 的顺序和取值必须和 js/pipeline.js 里的 STAGE_ORDER 完全一致，
#    两处各自独立声明，改一处记得改另一处。
_STAGES = [
    {"key": "intent", "idx": 1, "label": "意图识别"},
    {"key": "rag", "idx": 2, "label": "知识库检索"},
    {"key": "route", "idx": 3, "label": "路由分发"},
    {"key": "tool", "idx": 4, "label": "业务工具"},
    {"key": "answer", "idx": 5, "label": "生成应答"},
]
```

`index()` 路由（原第 389–392 行）：

```python
    @app.get("/")
    def index():
        """返回聊天主页面。"""
        return FileResponse(_STATIC_DIR / "index.html")
```

改成：

```python
    @app.get("/")
    def index(request: Request):
        """返回聊天主页面。"""
        return _templates.TemplateResponse(request, "index.html", {"stages": _STAGES})
```

- [ ] **Step 5: 跑测试**

```bash
langgraph_cs/.venv/bin/python -m langgraph_cs.web.tests.test_server_offline
```

期望：和 Task 1 Step 10 一样全绿，`✓ GET / -> 200 + HTML（含 RelayDesk 品牌）` 这一条必须通过（说明 `TemplateResponse` 渲染正常）。

- [ ] **Step 6: 浏览器核对**

```bash
langgraph_cs/.venv/bin/python -m langgraph_cs.web
```

浏览器打开页面，确认：
1. 页面正常加载，无 500 错误、无 Jinja 语法报错。
2. 开发者工具里查看 `#stage-intent`、`#stage-rag`、`#stage-route`、`#stage-tool`、`#stage-answer`、`#value-intent`…`#value-answer` 十个 id 都存在且唯一。
3. 发一条消息，决策轨迹 5 个 stage 照常依次点亮（验证渲染出的 id 和 `js/pipeline.js` 的 `STAGE_ORDER` 对得上）。

- [ ] **Step 7: 提交**

```bash
git add langgraph_cs/requirements.txt langgraph_cs/web/templates/index.html langgraph_cs/web/server.py
git status  # 确认 static/index.html 已被 git mv 追踪为移动，不是"删除+新增"
git commit -m "$(cat <<'EOF'
refactor(web): 决策轨迹 5-stage 列表改 Jinja2 服务端模板循环生成

index.html 移到 templates/ 目录，5 个手写复制的 <li class="stage"> 改成
{% for stage in stages %} 循环；stages 数据在 server.py 里声明，key 顺序
与 js/pipeline.js 的 STAGE_ORDER 保持一致（各自独立声明，不共享数据源）。
新增 jinja2 依赖。
EOF
)"
```

---

## 最终验收（四个 task 全部完成后）

- [ ] `node langgraph_cs/web/tests/test_markdown.mjs` 全绿
- [ ] `langgraph_cs/.venv/bin/python -m langgraph_cs.web.tests.test_server_offline` 全绿
- [ ] `git diff main --stat`（或 `git log --stat`）里改动文件只有：`langgraph_cs/web/static/js/*.js`（新增）、`langgraph_cs/web/static/app.js`、`langgraph_cs/web/static/style.css`、`langgraph_cs/web/templates/index.html`（原 `static/index.html`）、`langgraph_cs/web/server.py`、`langgraph_cs/web/tests/test_markdown.mjs`、`langgraph_cs/requirements.txt`——没有意外改到其他文件。
- [ ] 浏览器里完整过一遍 Task 1 Step 11 的 6 项走查，视觉与交互和重构前逐像素一致。
