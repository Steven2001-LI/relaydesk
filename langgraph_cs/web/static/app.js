/*
  RelayDesk 客服 Web 演示 —— 前端逻辑（原生 JS，无框架 / 无构建链）。

  做这几件事：
    1) 多轮聊天：维护 thread_id（localStorage 持久），收发消息气泡。
    2) SSE 流式：POST /api/chat 与 /api/resume 返回 text/event-stream；
       因为 EventSource 只支持 GET，这里用 fetch + ReadableStream 手动解析 SSE。
    3) signature 决策轨迹 pipeline（调试面板化）：把 meta / rag / route / token 事件实时映射到
       右侧四阶段的 idle/active/done 点亮（当前步骤高亮、已完成低亮、未开始更弱）；右栏主文案
       为产品化人话整句（意图识别：…/知识检索：…/路由分发：…/生成应答：…），技术字段进 tooltip。
       每轮发新消息整条重置。
    4) 人话映射 + 合并标签行：技术名集中翻成中文友好词（INTENT_LABEL / AGENT_LABEL 等）；意图/路由/RAG
       三段合并为每条 Agent 气泡上方「一行点分隔」摘要（技术支持 · 置信度 0.95 · 命中 3 条知识），
       技术原值降级进整行 title/tooltip，方便演示「可观测」。
    5) 轻量安全 markdown：先转义 HTML，再支持 # 标题 / **bold** / 有序·无序列表 / 换行 / `code`；
       连续有序列表项（含项间空行）合并进同一个 <ol>，靠浏览器自动顺序编号（1.2.3.4.）。
    6) HITL 坐席模式：收到 interrupt -> pipeline 转琥珀 + 顶部系统条 + 底栏切坐席皮肤；
       右栏「转人工三段状态」分段呈现 ①AI 已判断 ②等待坐席接入 ③人工回复；
       发送走 /api/resume，恢复后把坐席回复作为带琥珀标签的 Agent 气泡显示并退出坐席模式。
    7) 业务操作：结束会话（=重置/清空+新 thread_id）；重新提问快捷操作挂在每条回答气泡下方。

  事件协议（与 server.py _stream_graph 对齐，前端绝不改）：
    meta {intent, confidence} · rag {sources[]} · route {agent} · token {text}
    interrupt {prompt, user_message} · done {escalated} · error {message}
*/

const $ = (sel) => document.querySelector(sel);
const messagesEl = $("#messages");
const inputEl = $("#input");
const sendBtn = $("#btn-send");
const composerEl = $("#composer");
const seatBanner = $("#seat-banner");
const threadPill = $("#thread-pill");
const newBtn = $("#btn-new");
const welcomeEl = $("#welcome");   // 开场气泡（首条用户消息后折叠）

// 连接状态指示
const statusEl = $("#status");
const statusTextEl = $("#status-text");

// 决策轨迹 pipeline 的四阶段节点 + 值槽 + 标题提示
const stageEls = {
  intent: $("#stage-intent"),
  rag: $("#stage-rag"),
  route: $("#stage-route"),
  answer: $("#stage-answer"),
};
const valueEls = {
  intent: $("#value-intent"),
  rag: $("#value-rag"),
  route: $("#value-route"),
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

// ── 会话状态 ─────────────────────────────────────────────
const state = {
  threadId: loadThreadId(),
  seatMode: false,    // 是否处于坐席模式（命中 interrupt 后为 true）
  busy: false,        // 是否有请求在飞（避免并发发送）
  lastUserText: "",   // 上一条用户消息（供「重新提问」重发）
};

function loadThreadId() {
  let id = localStorage.getItem("echomind_thread_id");
  if (!id) {
    id = newThreadId();
    localStorage.setItem("echomind_thread_id", id);
  }
  return id;
}

function newThreadId() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return "t-" + Date.now() + "-" + Math.random().toString(16).slice(2);
}

function renderThreadPill() {
  threadPill.textContent = "会话 " + state.threadId.slice(0, 8);
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
  const segs = { intent: null, route: null, rag: null };
  const titles = [];

  function renderMeta() {
    const parts = [segs.intent, segs.route, segs.rag].filter(Boolean);
    if (!parts.length) { meta.remove(); return; }
    // 点分隔：<span> · <span> · <span>，整行可 hover 看技术原值。
    meta.innerHTML = parts
      .map((p) => `<span class="meta-seg">${escapeHtml(p)}</span>`)
      .join('<span class="meta-dot" aria-hidden="true">·</span>');
    if (titles.length) meta.title = titles.join(" · ");
  }

  return {
    // 人话 meta 段：key=intent|route|rag，main=主词，title=技术原值（进整行 tooltip）。
    setMetaSeg(key, main, title = "") {
      if (key in segs) segs[key] = main;
      if (title) titles.push(title);
      renderMeta();
      scrollToBottom();
    },
    // 坐席标签（保留独立 chip 观感，复用 meta 行渲染单段）。
    setSeatTag(main, title = "") {
      segs.route = main;
      if (title) titles.push(title);
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
const STAGE_ORDER = ["intent", "rag", "route", "answer"];

function setStage(name, status, value) {
  const node = stageEls[name];
  if (!node) return;
  node.classList.remove("is-active", "is-done", "is-seat");
  if (status) node.classList.add("is-" + status);
  if (value != null) valueEls[name].textContent = value;
}

// 每轮用户发新消息前：整条 pipeline 重置为 idle。
function resetPipeline() {
  for (const name of STAGE_ORDER) {
    stageEls[name].classList.remove("is-active", "is-done", "is-seat");
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
      if (!stageEls[s].classList.contains("is-done")) setStage(s, "done");
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
  // 之前已点亮的 intent（escalation 意图）标 done，应答阶段切坐席态。
  for (const name of STAGE_ORDER) {
    if (stageEls[name].classList.contains("is-active")) setStage(name, "done");
  }
  setStage("answer", "seat", "等待坐席接入");
  // 人话主词在前，技术名 human-in-the-loop 降级为括注小字。
  pipelineHintEl.textContent = "需要人工介入 · human-in-the-loop";
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
  composerEl.classList.add("seat-mode");
  inputEl.placeholder = prompt || "以坐席身份回复用户… Enter 发送";
  setStatus("seat", "坐席模式");
  inputEl.focus();
}

function exitSeatMode() {
  state.seatMode = false;
  seatBanner.hidden = true;
  composerEl.classList.remove("seat-mode");
  inputEl.placeholder = "输入消息，Enter 发送 · Shift+Enter 换行";
}

// ── 一次"流式请求"的统一处理：chat 与 resume 共用 ──
//   bot：当前 Agent 气泡句柄；isResume：resume 时 pipeline 走应答阶段而非整轮重置。
async function runStream(url, payload, bot, { isResume = false } = {}) {
  let interrupted = false;
  let answered = false;       // 是否已收到第一个 token（点亮④）
  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!resp.ok || !resp.body) {
      bot.setText("网络异常（HTTP " + resp.status + "），请稍后重试。");
      bot.markError();
      setStatus("error", "网络异常");
      return { interrupted: false };
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
        case "token":
          // 第一个 token -> 点亮④（生成应答中）
          if (!answered) {
            answered = true;
            advancePipeline("answer", "正在生成回复…");
          }
          bot.appendToken(evt.text || "");
          break;
        case "interrupt":
          interrupted = true;
          if (!bot.hasText()) bot.setText("（已转人工，等待坐席接入…）");
          bot.finish();
          pipelineToSeat();
          seatFlowEnter();           // 三段状态卡：点亮 ① AI 已判断 + ② 等待坐席接入
          enterSeatMode(evt.prompt);
          addSysLine(evt.prompt || "已转人工，请以坐席身份回复用户");
          break;
        case "done":
          bot.finish();
          // ④ 完成（人话整句「生成应答：已完成」）
          if (isResume) {
            completeStage("answer", "坐席已回复");
          } else if (answered) {
            completeStage("answer", evt.escalated ? "已转人工" : "已完成");
            // P1：回答落地后，把「重新提问」快捷操作挂到该气泡下方。
            bot.mountRetry();
          }
          break;
        case "error":
          bot.setText(evt.message || "出了点问题，请稍后重试。");
          bot.markError();
          setStatus("error", "出错");
          break;
        default:
          break;
      }
    });
  } catch (e) {
    console.error(e);
    bot.setText("连接中断，请检查服务是否在运行。");
    bot.markError();
    setStatus("error", "连接中断");
  }
  return { interrupted };
}

// ── 发送（普通用户消息）──────────────────────────────────
async function sendUserMessage(text) {
  resetPipeline();            // 新一轮：整条 pipeline 重置为 idle
  seatFlowReset();            // 清掉上一轮的转人工三段卡
  state.lastUserText = text;  // 记下用于「重新提问」（快捷操作挂在每条回答气泡下方）
  addUserMessage(text);
  const bot = createBotMessage();
  const { interrupted } = await runStream(
    "/api/chat",
    { message: text, thread_id: state.threadId },
    bot
  );
  return interrupted;
}

// ── 提交坐席回复（resume）──────────────────────────────────
async function sendSeatReply(text) {
  // 坐席的话以"输入"显示在右侧，让演示看到坐席输了什么。
  addUserMessage(text);
  const bot = createBotMessage({ fromSeat: true });
  bot.setSeatTag("人工坐席已接管", "agent=escalation");
  seatFlowReply(text);        // 三段状态卡：点亮 ③ 人工回复 + 填入内容
  await runStream(
    "/api/resume",
    { thread_id: state.threadId, seat_reply: text },
    bot,
    { isResume: true }
  );
  exitSeatMode();
  setStatus(null, "就绪");
}

// ── 输入框统一提交入口 ───────────────────────────────────
async function onSubmit() {
  const text = inputEl.value.trim();
  if (!text || state.busy) return;
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
  sendBtn.disabled = b;
  inputEl.disabled = b;
  if (b) {
    setStatus("busy", "推理中");
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
function resetSession() {
  state.threadId = newThreadId();
  localStorage.setItem("echomind_thread_id", state.threadId);
  state.lastUserText = "";
  exitSeatMode();
  resetPipeline();
  seatFlowReset();
  setStatus(null, "就绪");
  // 保留开场气泡（第一条），清掉其余；并重新展开开场气泡
  while (messagesEl.children.length > 1) {
    messagesEl.removeChild(messagesEl.lastChild);
  }
  if (welcomeEl) welcomeEl.hidden = false;
  renderThreadPill();
  inputEl.focus();
}

// ── 重新提问：重发上一条用户消息（坐席模式 / 忙时禁用） ──
async function onRetry() {
  if (state.busy || state.seatMode || !state.lastUserText) return;
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
newBtn.addEventListener("click", resetSession);
inputEl.addEventListener("input", autoGrow);
inputEl.addEventListener("keydown", (e) => {
  // Enter 发送，Shift+Enter 换行
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    onSubmit();
  }
});

// 初始化
renderThreadPill();
resetPipeline();
seatFlowReset();
inputEl.focus();
