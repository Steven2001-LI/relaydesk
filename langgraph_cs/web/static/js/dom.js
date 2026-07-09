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
