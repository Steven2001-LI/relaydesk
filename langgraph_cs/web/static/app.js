/*
  EchoMind 客服 Web 演示 —— 前端逻辑（原生 JS，无框架/无构建）。

  做四件事：
    1) 多轮聊天：维护 thread_id（localStorage 持久），收发消息气泡。
    2) SSE 流式：POST /api/chat 与 /api/resume 返回 text/event-stream；
       因为 EventSource 只支持 GET，这里用 fetch + ReadableStream 手动解析 SSE。
    3) 决策可视化：把 meta(意图) / rag(来源) / route(路由) 事件渲染成机器人消息上方的 chips；
       token 事件逐字追加做打字机。
    4) HITL 坐席模式：收到 interrupt 事件 -> 顶部横幅 + 底栏切琥珀坐席皮肤 + placeholder 改坐席口吻；
       此时发送走 /api/resume，恢复后把坐席回复作为机器人消息显示并退出坐席模式。

  事件协议（与 server.py _stream_graph 对齐）：
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

// 意图 -> emoji，路由节点名 -> 展示名/emoji。纯展示用，识别不到就给个兜底。
const INTENT_EMOJI = {
  technical: "🛠️", billing: "💳", complaint: "🙏", greeting: "👋",
  query: "🔎", request: "📝", escalation: "🧑‍💼", other: "💬",
};
const AGENT_LABEL = {
  technical_agent: "🤖 technical_agent",
  billing_agent: "🤖 billing_agent",
  general_agent: "🤖 general_agent",
  escalation: "🧑‍💼 escalation",
};

// ── 会话状态 ─────────────────────────────────────────────
const state = {
  threadId: loadThreadId(),
  seatMode: false,   // 是否处于坐席模式（命中 interrupt 后为 true）
  busy: false,       // 是否有请求在飞（避免并发发送）
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
  // 优先用 crypto.randomUUID；老浏览器兜底用时间戳+随机。
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return "t-" + Date.now() + "-" + Math.random().toString(16).slice(2);
}

function renderThreadPill() {
  threadPill.textContent = "会话 " + state.threadId.slice(0, 8);
}

// ── DOM 构建小工具 ───────────────────────────────────────
function el(tag, className, html) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (html != null) node.innerHTML = html;
  return node;
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

// 追加一条用户气泡
function addUserMessage(text) {
  const wrap = el("div", "msg msg-user");
  wrap.appendChild(el("div", "bubble", escapeHtml(text)));
  messagesEl.appendChild(wrap);
  scrollToBottom();
}

// 创建一条机器人消息（含一行 chips 容器 + 气泡），返回操作句柄。
function createBotMessage({ fromSeat = false } = {}) {
  const wrap = el("div", "msg msg-bot" + (fromSeat ? " from-seat" : ""));
  const chips = el("div", "chips");
  const bubble = el("div", "bubble typing");
  wrap.appendChild(chips);
  wrap.appendChild(bubble);
  messagesEl.appendChild(wrap);
  scrollToBottom();

  let raw = "";
  return {
    addChip(cls, text) {
      const c = el("span", "chip " + cls, escapeHtml(text));
      chips.appendChild(c);
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
      // 若没有任何 chip，移除空容器，避免留一行空隙
      if (!chips.children.length) chips.remove();
    },
    markError() {
      wrap.classList.add("is-error");
      bubble.classList.remove("typing");
      if (!chips.children.length) chips.remove();
    },
    hasText: () => raw.length > 0,
  };
}

function addSeatHint(text) {
  const hint = el("div", "seat-hint", escapeHtml(text));
  messagesEl.appendChild(hint);
  scrollToBottom();
}

// ── SSE 解析：把 fetch 的字节流按 `\n\n` 切成一个个事件，回调每条 JSON ──
async function readSSE(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // SSE 事件以空行(\n\n)分隔
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
  inputEl.placeholder = prompt || "请以坐席身份回复用户…";
  inputEl.focus();
}

function exitSeatMode() {
  state.seatMode = false;
  seatBanner.hidden = true;
  composerEl.classList.remove("seat-mode");
  inputEl.placeholder = "输入消息，Enter 发送 / Shift+Enter 换行";
}

// ── 一次"流式请求"的统一处理：chat 与 resume 共用 ──
async function runStream(url, payload, bot, { isResume = false } = {}) {
  let interrupted = false;
  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!resp.ok || !resp.body) {
      bot.setText("网络异常（HTTP " + resp.status + "），请稍后重试。");
      bot.markError();
      return { interrupted: false };
    }

    await readSSE(resp, (evt) => {
      switch (evt.type) {
        case "meta": {
          if (evt.intent) {
            const emoji = INTENT_EMOJI[evt.intent] || "🎯";
            const conf = (evt.confidence != null) ? " " + Number(evt.confidence).toFixed(2) : "";
            bot.addChip("chip-intent", `${emoji} ${evt.intent}${conf}`);
          }
          break;
        }
        case "rag": {
          const sources = evt.sources || [];
          if (sources.length) {
            bot.addChip("chip-rag", "📚 " + sources.join(" · "));
          }
          break;
        }
        case "route": {
          if (evt.agent && AGENT_LABEL[evt.agent]) {
            bot.addChip("chip-route", AGENT_LABEL[evt.agent]);
          }
          break;
        }
        case "token":
          bot.appendToken(evt.text || "");
          break;
        case "interrupt":
          interrupted = true;
          if (!bot.hasText()) bot.setText("（已转人工，等待坐席接管…）");
          bot.finish();
          enterSeatMode(evt.prompt);
          addSeatHint("🧑‍💼 " + (evt.prompt || "请以坐席身份回复用户"));
          break;
        case "done":
          bot.finish();
          break;
        case "error":
          bot.setText(evt.message || "出了点问题，请稍后重试。");
          bot.markError();
          break;
        default:
          break;
      }
    });
  } catch (e) {
    console.error(e);
    bot.setText("连接中断，请检查服务是否在运行。");
    bot.markError();
  }
  return { interrupted };
}

// ── 发送（普通用户消息）──────────────────────────────────
async function sendUserMessage(text) {
  addUserMessage(text);
  const bot = createBotMessage();
  const { interrupted } = await runStream(
    "/api/chat",
    { message: text, thread_id: state.threadId },
    bot
  );
  // interrupted 时已在事件处理里进了坐席模式；这里无需额外动作。
  return interrupted;
}

// ── 提交坐席回复（resume）──────────────────────────────────
async function sendSeatReply(text) {
  // 坐席的话也以"用户视角的输入"显示在右侧，让演示能看到坐席输了什么。
  addUserMessage(text);
  const bot = createBotMessage({ fromSeat: true });
  bot.addChip("chip-route", AGENT_LABEL.escalation);
  await runStream(
    "/api/resume",
    { thread_id: state.threadId, seat_reply: text },
    bot,
    { isResume: true }
  );
  exitSeatMode();
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
}

// 输入框高度自适应
function autoGrow() {
  inputEl.style.height = "auto";
  inputEl.style.height = Math.min(inputEl.scrollHeight, 140) + "px";
}

// ── 新会话：重置 thread_id + 清空对话 + 退出坐席模式 ──
function resetSession() {
  state.threadId = newThreadId();
  localStorage.setItem("echomind_thread_id", state.threadId);
  exitSeatMode();
  // 保留开场气泡（第一条），清掉其余
  while (messagesEl.children.length > 1) {
    messagesEl.removeChild(messagesEl.lastChild);
  }
  renderThreadPill();
  inputEl.focus();
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
inputEl.focus();
