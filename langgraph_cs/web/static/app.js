/*
  RelayDesk 客服 Web 演示 —— 前端逻辑（原生 JS，无框架 / 无构建链）。

  做这几件事：
    1) 多轮聊天：维护 thread_id（localStorage 持久），收发消息气泡。
    2) SSE 流式：POST /api/chat 与 /api/resume 返回 text/event-stream；
       因为 EventSource 只支持 GET，这里用 fetch + ReadableStream 手动解析 SSE。
    3) signature 决策轨迹 pipeline（调试面板化）：把 meta / rag / route / tool / token 事件实时映射到
       右侧五阶段的 idle/active/done/skipped 点亮（当前步骤高亮、已完成低亮、未使用灰化）；右栏主文案
       为产品化人话整句（意图识别：…/知识检索：…/路由分发：…/生成应答：…），技术字段进 tooltip。
       每轮发新消息整条重置。
    4) 人话映射 + 合并标签行：技术名集中翻成中文友好词（INTENT_LABEL / AGENT_LABEL 等）；意图/路由/RAG
       三段合并为每条 Agent 气泡上方「一行点分隔」摘要（技术支持 · 置信度 0.95 · 命中 3 条知识），
       技术原值降级进整行 title/tooltip，方便演示「可观测」。
    5) 轻量安全 markdown：先转义 HTML，再支持 # 标题 / **bold** / 有序·无序列表 / 换行 / `code`；
       连续有序列表项（含项间空行）合并进同一个 <ol>，靠浏览器自动顺序编号（1.2.3.4.）。
    6) HITL 坐席/审批模式：收到 interrupt -> 按 kind 分流，pipeline 转琥珀 + 顶部系统条；
       右栏「转人工三段状态」分段呈现 ①AI 已判断 ②等待坐席接入 ③人工回复；
       坐席输入或审批按钮都走 /api/resume，恢复后继续接收图的后续流式事件。
    7) 业务操作：结束会话（=重置/清空+新 thread_id）；重新提问快捷操作挂在每条回答气泡下方。

  事件协议（与 server.py _stream_graph 对齐，前端绝不改）：
    meta {intent, confidence} · rag {sources[]} · route {agent} · tool {name, status} · token {text}
    interrupt {kind, action, params, prompt, user_message} · done {escalated} · error {message}
*/

const $ = (sel) => document.querySelector(sel);
const messagesEl = $("#messages");
const inputEl = $("#input");
const sendBtn = $("#btn-send");
const composerEl = $("#composer");
const seatBanner = $("#seat-banner");
const seatBannerTextEl = $("#seat-banner-text");
const approvalActionsEl = $("#approval-actions");
const approveBtn = $("#btn-approve");
const rejectBtn = $("#btn-reject");
const identitySelect = $("#identity-select");
const identityPill = $("#identity-pill");
const threadPill = $("#thread-pill");
const newBtn = $("#btn-new");
const welcomeEl = $("#welcome");   // 开场气泡（首条用户消息后折叠）

// 连接状态指示
const statusEl = $("#status");
const statusTextEl = $("#status-text");

// 决策轨迹 pipeline 的五阶段节点 + 值槽 + 标题提示
const stageEls = {
  intent: $("#stage-intent"),
  rag: $("#stage-rag"),
  route: $("#stage-route"),
  tool: $("#stage-tool"),
  answer: $("#stage-answer"),
};
const valueEls = {
  intent: $("#value-intent"),
  rag: $("#value-rag"),
  route: $("#value-route"),
  tool: $("#value-tool"),
  answer: $("#value-answer"),
};
const pipelineEl = $("#pipeline");
const pipelineHintEl = $("#pipeline-hint");

// 转人工三段状态卡（命中 interrupt / resume 时点亮）
const seatFlowEl = $("#seat-flow");
const seatStepEls = {
  judge: $("#seat-step-judge"),
  wait: $("#seat-step-wait"),
  reply: $("#seat-step-reply"),
};
const seatReplyTextEl = $("#seat-step-reply-text");

// ════════════════════════════════════════════════════════
// 人话映射：把技术名翻成中文友好词（集中维护，一处可改）。
// 技术原值不丢——以小字 / tooltip 形式降级呈现，方便演示「可观测」。
// ════════════════════════════════════════════════════════
const INTENT_EMOJI = {
  technical: "🛠️", billing: "💳", complaint: "🙏", greeting: "👋",
  query: "🔎", request: "📝", escalation: "🧑‍💼", other: "💬",
};
// 意图技术名 -> 中文主词
const INTENT_LABEL = {
  technical: "技术支持",
  billing: "账单咨询",
  complaint: "投诉处理",
  greeting: "打招呼",
  query: "信息查询",
  request: "业务请求",
  escalation: "转人工",
  other: "其他咨询",
};
// 路由节点（agent）技术名 -> 中文主词
const AGENT_LABEL = {
  technical_agent: "技术支持",
  billing_agent: "账单客服",
  general_agent: "通用客服",
  escalation: "人工坐席",
};
const AGENT_EMOJI = {
  technical_agent: "🛠️", billing_agent: "💳",
  general_agent: "💬", escalation: "🧑‍💼",
};

// 置信度数值 -> 高/中/低（人话表达）
function confidenceLevel(conf) {
  if (conf == null) return "";
  const c = Number(conf);
  if (c >= 0.8) return "高";
  if (c >= 0.5) return "中";
  return "低";
}

// localStorage 里存 thread_id 的 key（新 key + 旧版兼容 key）。
// ⚠️ 必须声明在 loadThreadId() 调用之前：下面 state 初始化时就会调用 loadThreadId()，
//    它引用这两个 const；若声明在后，会触发 const 暂时性死区(TDZ) 的 ReferenceError，
//    导致整段 app.js 在此中断、所有事件绑定都不执行（表现为"按钮/回车没反应"）。
const THREAD_STORAGE_KEY = "relaydesk_thread_id";
const LEGACY_THREAD_STORAGE_KEY = "echomind_thread_id";
const SESSION_USER_STORAGE_KEY = "relaydesk_session_user_id";

// ── 会话状态 ─────────────────────────────────────────────
const state = {
  threadId: loadThreadId(),
  sessionUserId: loadSessionUserId(),
  seatMode: false,    // 是否处于坐席模式（命中 interrupt 后为 true）
  approvalMode: false, // 是否处于审批模式（敏感操作 interrupt 后为 true）
  busy: false,        // 是否有请求在飞（避免并发发送）
  activeStream: null, // 当前在飞 SSE fetch 的 AbortController（结束会话时主动取消）
  lastUserText: "",   // 上一条用户消息（供「重新提问」重发）
};

function loadThreadId() {
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

function newThreadId() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return "t-" + Date.now() + "-" + Math.random().toString(16).slice(2);
}

function loadSessionUserId() {
  return (localStorage.getItem(SESSION_USER_STORAGE_KEY) || "").trim();
}

function saveSessionUserId(userId) {
  const value = (userId || "").trim();
  if (value) localStorage.setItem(SESSION_USER_STORAGE_KEY, value);
  else localStorage.removeItem(SESSION_USER_STORAGE_KEY);
}

function renderThreadPill() {
  threadPill.textContent = "会话 " + state.threadId.slice(0, 8);
}

function renderIdentityPill() {
  identityPill.textContent = "身份 " + (state.sessionUserId || "游客");
}

// ── 连接状态小圆点 ───────────────────────────────────────
function setStatus(kind, text) {
  statusEl.classList.remove("is-busy", "is-error", "is-seat");
  if (kind === "busy") statusEl.classList.add("is-busy");
  else if (kind === "error") statusEl.classList.add("is-error");
  else if (kind === "seat") statusEl.classList.add("is-seat");
  if (text != null) statusTextEl.textContent = text;
}

// ── DOM 构建小工具 ───────────────────────────────────────
function el(tag, className, html) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (html != null) node.innerHTML = html;
  return node;
}

// >>> PURE-MARKDOWN-BLOCK-START（此区间为无 DOM 依赖的纯函数，供 Node 单测原样抽取）
const TOOL_LABEL = {
  query_bill: "查询账单",
  refund_status: "查询退款进度",
  create_refund_ticket: "创建退款工单",
  create_ticket: "创建报障工单",
  check_service_status: "查询服务状态",
};

function toolLabel(name) {
  return TOOL_LABEL[name] || name || "未知工具";
}

function normalizeSessionUserId(sessionUserId) {
  return (sessionUserId || "").trim();
}

function buildChatBody(message, threadId, sessionUserId) {
  return {
    message,
    thread_id: threadId,
    session_user_id: normalizeSessionUserId(sessionUserId),
  };
}

function buildSeatResumeBody(threadId, sessionUserId, seatReply) {
  return {
    thread_id: threadId,
    session_user_id: normalizeSessionUserId(sessionUserId),
    seat_reply: seatReply,
  };
}

function buildApprovalResumeBody(threadId, sessionUserId, approved, note) {
  return {
    thread_id: threadId,
    session_user_id: normalizeSessionUserId(sessionUserId),
    approval: { approved, note },
  };
}

function escapeHtml(s) {
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

function renderMarkdown(raw) {
  const text = escapeHtml(raw || "");
  const lines = text.split("\n");
  const classified = lines.map(classifyLine);
  let html = "";
  let listType = null;          // 当前列表类型："ol" | "ul" | null
  let paraBuf = [];             // 正在累积的普通段落行

  const flushPara = () => {
    if (paraBuf.length) {
      html += "<p>" + inline(paraBuf.join("<br>")) + "</p>";
      paraBuf = [];
    }
  };
  const closeList = () => {
    if (listType) { html += "</" + listType + ">"; listType = null; }
  };

  // 向后看：从空行 i 之后，跳过连续空行，返回下一条非空行的类型（或 null）。
  const nextNonBlankKind = (i) => {
    for (let j = i + 1; j < classified.length; j++) {
      if (classified[j].kind !== "blank") return classified[j].kind;
    }
    return null;
  };

  classified.forEach((info, i) => {
    if (info.kind === "h") {
      // 标题自成块，先收尾段落与列表。文本已整体转义，故这里安全。
      flushPara();
      closeList();
      const content = inline(info.content);
      if (content) {
        html += `<h${info.level} class="md-h md-h${info.level}">${content}</h${info.level}>`;
      }
    } else if (info.kind === "ol" || info.kind === "ul") {
      flushPara();
      // 列表类型切换才关旧开新；同类型则续用同一个 <ol>/<ul>（自动顺序编号）。
      if (listType !== info.kind) { closeList(); html += "<" + info.kind + ">"; listType = info.kind; }
      html += "<li>" + listItemHtml(info.content) + "</li>";
    } else if (info.kind === "blank") {
      // 空行：永远结束段落；但列表只在「后续不再是同类型列表项」时才关闭，
      // 这样「项间夹空行」的有序列表会合并进同一个 <ol>，浏览器自动 1.2.3. 顺序编号。
      flushPara();
      if (listType && nextNonBlankKind(i) !== listType) closeList();
    } else {
      // 普通段落行：开启段落即结束列表。
      closeList();
      paraBuf.push(info.content);
    }
  });
  flushPara();
  closeList();
  return html;

  // 内联级：**bold** 与 `code`（输入已转义，故这里安全）
  function inline(s) {
    return s
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  }

  // 列表项渲染（P0-4 步骤化易扫读）：若项内是「标题：说明」结构，
  //   把「标题：」包成 .li-title（CSS 加粗），其余说明留默认（CSS 次要色弱化）。
  //   判定克制：标题侧不含已有粗体/代码标记、且较短（≤24 字）时才拆，避免误伤普通句子。
  function listItemHtml(s) {
    const rendered = inline(s);
    // 用转义后的全角「：」或半角「: 」（后接空格）切一次。
    const m = rendered.match(/^([^：]{1,24})：(.+)$/) || rendered.match(/^([^:]{1,24}):\s+(.+)$/);
    if (m && !/<(strong|code)>/.test(m[1])) {
      const sep = rendered.indexOf("：") !== -1 ? "：" : ": ";
      return `<span class="li-title">${m[1]}${sep}</span><span class="li-desc">${m[2]}</span>`;
    }
    return rendered;
  }
}
// <<< PURE-MARKDOWN-BLOCK-END

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

// 追加一条用户气泡（首条用户消息后折叠开场气泡，省空间）
function addUserMessage(text) {
  if (welcomeEl) welcomeEl.hidden = true;
  const wrap = el("div", "msg msg-user");
  wrap.appendChild(el("div", "bubble", escapeHtml(text)));
  messagesEl.appendChild(wrap);
  scrollToBottom();
}

// 创建一条机器人消息（含 meta 标签行 + 气泡 + 气泡下快捷操作），返回操作句柄。
//   P0-3：意图/路由/RAG 三段不再各自分离成 chip，而是合并为「一行点分隔」摘要：
//     技术支持 · 置信度 0.95 · 命中 3 条知识。技术原值进 title/tooltip；无 RAG 时省略「命中」段。
//   P1：「重新提问」快捷操作放在回答气泡「下方」。
function createBotMessage({ fromSeat = false } = {}) {
  const wrap = el("div", "msg msg-bot" + (fromSeat ? " from-seat" : ""));
  const meta = el("div", "meta-line");   // 合并后的单行标签（点分隔）
  const bubble = el("div", "bubble typing");
  const actions = el("div", "bubble-actions");  // 气泡下方快捷操作条
  wrap.appendChild(meta);
  wrap.appendChild(bubble);
  wrap.appendChild(actions);
  messagesEl.appendChild(wrap);
  scrollToBottom();

  let raw = "";
  // 累积 meta 段（按事件到达陆续填充），finish() 时渲染成一行。
  const segs = { intent: null, route: null, rag: null, tool: null };
  const titles = {};

  function renderMeta() {
    const parts = [segs.intent, segs.route, segs.rag, segs.tool].filter(Boolean);
    if (!parts.length) { meta.remove(); return; }
    // 点分隔：<span> · <span> · <span>，整行可 hover 看技术原值。
    meta.innerHTML = parts
      .map((p) => `<span class="meta-seg">${escapeHtml(p)}</span>`)
      .join('<span class="meta-dot" aria-hidden="true">·</span>');
    const titleText = ["intent", "route", "rag", "tool"].map((key) => titles[key]).filter(Boolean).join(" · ");
    if (titleText) meta.title = titleText;
  }

  return {
    // 人话 meta 段：key=intent|route|rag，main=主词，title=技术原值（进整行 tooltip）。
    setMetaSeg(key, main, title = "") {
      if (key in segs) segs[key] = main;
      if (title) titles[key] = title;
      renderMeta();
      scrollToBottom();
    },
    // 坐席标签（保留独立 chip 观感，复用 meta 行渲染单段）。
    setSeatTag(main, title = "") {
      segs.route = main;
      if (title) titles.route = title;
      renderMeta();
      scrollToBottom();
    },
    appendToken(t) {
      raw += t;
      // 流式期间用纯文本追加（便宜、不闪），完成时再做 markdown 渲染。
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
      // 收尾时把累积文本做安全 markdown 渲染，提升可读性（bold / 列表 / 换行）。
      if (raw) bubble.innerHTML = renderMarkdown(raw);
      renderMeta();
    },
    // P1：把「重新提问」快捷操作挂到这条回答气泡下方（非坐席回复才挂）。
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

// 系统提示：消息流里的细长系统条（左对齐、低调，不居中不大块）。
function addSysLine(text) {
  messagesEl.appendChild(el("div", "sys-line", escapeHtml(text)));
  scrollToBottom();
}

// ── 转人工三段状态卡：① AI 已判断 ② 等待坐席接入 ③ 人工回复 ──
function seatFlowReset() {
  seatFlowEl.hidden = true;
  for (const k of ["judge", "wait", "reply"]) seatStepEls[k].classList.remove("is-on");
  seatReplyTextEl.textContent = "—";
}
// 命中 interrupt：显示卡片，点亮①②（AI 已判断 + 等待坐席接入）。
function seatFlowEnter() {
  seatFlowEl.hidden = false;
  seatStepEls.judge.classList.add("is-on");
  seatStepEls.wait.classList.add("is-on");
}
// 坐席回复落地：点亮③并填入内容。
function seatFlowReply(text) {
  seatStepEls.wait.classList.remove("is-on"); // 等待结束
  seatStepEls.reply.classList.add("is-on");
  seatReplyTextEl.textContent = text || "（已回复）";
}

// ════════════════════════════════════════════════════════
// signature：决策轨迹 pipeline 的点亮控制
// ════════════════════════════════════════════════════════
const STAGE_ORDER = ["intent", "rag", "route", "tool", "answer"];

function setStage(name, status, value) {
  const node = stageEls[name];
  if (!node) return;
  node.classList.remove("is-active", "is-done", "is-seat", "is-skipped");
  if (status) node.classList.add("is-" + status);
  if (value != null) valueEls[name].textContent = value;
}

// 每轮用户发新消息前：整条 pipeline 重置为 idle。
function resetPipeline() {
  for (const name of STAGE_ORDER) {
    stageEls[name].classList.remove("is-active", "is-done", "is-seat", "is-skipped");
    valueEls[name].textContent = "待命";
  }
  pipelineHintEl.textContent = "本轮 Agent 内部决策";
}

// 推进到某阶段：把它点亮为 active，并把它之前的阶段标记为 done（连接线充能）。
function advancePipeline(name, value) {
  const idx = STAGE_ORDER.indexOf(name);
  STAGE_ORDER.forEach((s, i) => {
    if (i < idx) {
      // 前序阶段：若还没 done 就标 done（保留各自值）
      if (
        !stageEls[s].classList.contains("is-done") &&
        !stageEls[s].classList.contains("is-skipped")
      ) setStage(s, "done");
    }
  });
  setStage(name, "active", value);
}

// 完成某阶段（值保留），用于 answer 收尾。
function completeStage(name, value) {
  setStage(name, "done", value);
}

// 转人工：把 pipeline 推入醒目的琥珀状态。
function pipelineToSeat() {
  // 之前已点亮的 intent（escalation 意图）标 done；无工具调用时工具阶段显式标未使用。
  for (const name of STAGE_ORDER) {
    if (stageEls[name].classList.contains("is-active")) setStage(name, "done");
  }
  if (!stageEls.tool.classList.contains("is-done") && !stageEls.tool.classList.contains("is-seat")) {
    setStage("tool", "skipped", "未使用");
  }
  setStage("answer", "seat", "等待坐席接入");
  // 人话主词在前，技术名 human-in-the-loop 降级为括注小字。
  pipelineHintEl.textContent = "需要人工介入 · human-in-the-loop";
}

// 审批：同样用琥珀态提示"图已暂停"，但不展示转人工三段卡。
function pipelineToApproval() {
  for (const name of STAGE_ORDER) {
    if (stageEls[name].classList.contains("is-active")) setStage(name, "done");
  }
  setStage("tool", "seat", "等待人工审批");
  pipelineHintEl.textContent = "敏感操作审批 · approval";
}

// ── SSE 解析：把 fetch 的字节流按 `\n\n` 切成事件，回调每条 JSON ──
async function readSSE(response, onEvent) {
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

// ── 进入 / 退出坐席模式 ──────────────────────────────────
function enterSeatMode(prompt) {
  state.seatMode = true;
  seatBanner.hidden = false;
  seatBannerTextEl.textContent = "已转人工 · 请以坐席身份回复用户";
  composerEl.classList.add("seat-mode");
  inputEl.placeholder = prompt || "以坐席身份回复用户… Enter 发送";
  setStatus("seat", "坐席模式");
  inputEl.focus();
}

function exitSeatMode() {
  state.seatMode = false;
  if (!state.approvalMode) seatBanner.hidden = true;
  composerEl.classList.remove("seat-mode");
  if (!state.approvalMode) inputEl.placeholder = "输入消息，Enter 发送 · Shift+Enter 换行";
}

function enterApprovalMode(payload) {
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

function exitApprovalMode() {
  state.approvalMode = false;
  if (!state.seatMode) seatBanner.hidden = true;
  composerEl.classList.remove("approval-mode");
  approvalActionsEl.hidden = true;
  approveBtn.disabled = state.busy;
  rejectBtn.disabled = state.busy;
  sendBtn.disabled = state.busy;
  if (!state.seatMode) inputEl.placeholder = "输入消息，Enter 发送 · Shift+Enter 换行";
}

// ── 一次"流式请求"的统一处理：chat 与 resume 共用 ──
//   bot：当前 Agent 气泡句柄；isResume：resume 时 pipeline 走应答阶段而非整轮重置。
async function runStream(url, payload, bot, { isResume = false } = {}) {
  const controller = new AbortController();
  state.activeStream = controller;
  let interrupted = false;
  let failed = false;         // 网络/服务端异常：resume 场景下调用方必须保留中断态 UI 供重试
  let answered = false;       // 是否已收到第一个 token（点亮⑤）
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
    // resume 流可能从 tools 节点恢复，只收到 done 而没有本次流内的 start。
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
            // pipeline ①：人话整句「意图识别：技术支持，置信度 0.95」
            advancePipeline("intent", raw ? `识别为${cn}，置信度 ${raw}` : `识别为${cn}`);
            // 合并标签行 · 段1：主词 +（可选）置信度，技术原值进整行 tooltip
            const main = raw ? `${cn} · 置信度 ${raw}` : cn;
            bot.setMetaSeg("intent", main, `intent=${evt.intent}${raw ? ` confidence=${raw}` : ""}`);
          }
          break;
        }
        case "rag": {
          // sources 现在是稳定的条目 item_id 列表（如 ["billing-03", "account-01"]）。
          const sources = evt.sources || [];
          // pipeline ②：人话整句「知识检索：命中 3 条相关资料」
          if (sources.length) {
            advancePipeline("rag", `命中 ${sources.length} 条相关资料`);
            // 合并标签行 · 段3：主词「命中 N 条知识」不变，tooltip 里列出命中的 item_id。
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
            // pipeline ③：人话整句「路由分发：分配到技术支持」
            const cn = AGENT_LABEL[evt.agent] || evt.agent;
            advancePipeline("route", `分配到${cn}`);
            // 合并标签行 · 段2：分配到 <agent>，技术名进 tooltip
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
          // 第一个 token -> 工具阶段收束（完成或未使用），再点亮⑤（生成应答中）。
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
            seatFlowEnter();         // 三段状态卡：点亮 ① AI 已判断 + ② 等待坐席接入
            enterSeatMode(evt.prompt);
            addSysLine(evt.prompt || "已转人工，请以坐席身份回复用户");
          }
          break;
        case "done":
          if (toolCount > 0) finishToolsIfNeeded();
          else if (!answered) markToolSkippedIfNeeded();
          bot.finish();
          // ⑤ 完成（人话整句「生成应答：已完成」）
          if (isResume) {
            completeStage("answer", state.approvalMode ? "审批已处理" : "坐席已回复");
          } else if (answered) {
            completeStage("answer", evt.escalated ? "已转人工" : "已完成");
            // P1：回答落地后，把「重新提问」快捷操作挂到该气泡下方。
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

// ── 发送（普通用户消息）──────────────────────────────────
async function sendUserMessage(text) {
  resetPipeline();            // 新一轮：整条 pipeline 重置为 idle
  seatFlowReset();            // 清掉上一轮的转人工三段卡
  state.lastUserText = text;  // 记下用于「重新提问」（快捷操作挂在每条回答气泡下方）
  addUserMessage(text);
  const bot = createBotMessage();
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
  // 坐席的话以"输入"显示在右侧，让演示看到坐席输了什么。
  addUserMessage(text);
  const bot = createBotMessage({ fromSeat: true });
  bot.setSeatTag("人工坐席已接管", "agent=escalation");
  seatFlowReply(text);        // 三段状态卡：点亮 ③ 人工回复 + 填入内容
  const { interrupted, failed, aborted } = await runStream(
    "/api/resume",
    buildSeatResumeBody(state.threadId, state.sessionUserId, text),
    bot,
    { isResume: true }
  );
  if (aborted) return;
  // 提交失败：后端图仍停在中断点，保留坐席模式供重试（错误状态已由 runStream 展示）。
  if (failed) {
    addSysLine("坐席回复提交失败，请重试");
    return;
  }
  if (!interrupted) {
    exitSeatMode();
    setStatus(null, "就绪");
  } else if (state.approvalMode) {
    // resume 流内又出现审批中断：收起坐席态，审批 UI 已在流内建好。
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
      // 提交失败：后端图仍停在审批中断点。保留审批 UI 与备注供重试，
      // 不覆盖 runStream 已展示的错误状态。
      inputEl.value = note;
      autoGrow();
      addSysLine("审批提交失败，请重试");
    } else if (!interrupted) {
      exitApprovalMode();
      setStatus(null, "就绪");
    } else if (state.seatMode) {
      // resume 流内转成坐席中断：收起审批控件，坐席 UI 已在流内建好。
      exitApprovalMode();
    }
    // interrupted 且仍是审批（连环审批）：流内 enterApprovalMode 已刷新横幅，保持不动。
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
    // 坐席模式下保持 seat 状态指示，非坐席恢复就绪
    setStatus(null, "就绪");
  }
}

// 输入框高度自适应
function autoGrow() {
  inputEl.style.height = "auto";
  inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + "px";
}

// ── 结束会话：重置 thread_id + 清空对话 + 退出坐席模式 + 重置 pipeline / 三段卡 ──
//   （等价于「新会话」：是可用的业务操作，把当前会话清空、开启新 thread_id）
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
  // 保留开场气泡（第一条），清掉其余；并重新展开开场气泡
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
  // Enter 发送，Shift+Enter 换行
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
