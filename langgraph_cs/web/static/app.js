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
