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
  const wrap = el("div", "msg msg--user");
  wrap.appendChild(el("div", "msg__bubble", escapeHtml(text)));
  messagesEl.appendChild(wrap);
  scrollToBottom();
}

// createBotMessage 的 onRetry 从原来的「隐式引用全局函数」改成「显式参数传入」：
// 原 app.js 是单文件，mountRetry() 里直接引用同文件里的 onRetry 函数；拆模块后
// messages.js 不能反过来 import app.js（app.js 已经要 import messages.js，会成环），
// 所以把 onRetry 作为 createBotMessage 的可选参数，由调用方（app.js 入口）传入。
export function createBotMessage({ fromSeat = false, onRetry } = {}) {
  const wrap = el("div", "msg msg--bot" + (fromSeat ? " msg--from-seat" : ""));
  const meta = el("div", "msg__meta");
  const bubble = el("div", "msg__bubble is-typing");
  const actions = el("div", "msg__actions");
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
      .map((p) => `<span class="msg__meta-seg">${escapeHtml(p)}</span>`)
      .join('<span class="msg__meta-dot" aria-hidden="true">·</span>');
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
      bubble.classList.remove("is-typing");
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
      bubble.classList.remove("is-typing");
      renderMeta();
    },
    hasText: () => raw.length > 0,
  };
}

export function addSysLine(text) {
  messagesEl.appendChild(el("div", "messages__sys-line", escapeHtml(text)));
  scrollToBottom();
}
