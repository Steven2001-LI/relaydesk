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
// 状态类统一 is- 前缀（Task 3 BEM 改名）：坐席/审批模式对应
// "is-seat-mode"/"is-approval-mode"，与 style.css 里的 .composer.is-seat-mode /
// .composer.is-approval-mode 选择器一致。
export function enterSeatMode(prompt) {
  state.seatMode = true;
  seatBanner.hidden = false;
  seatBannerTextEl.textContent = "已转人工 · 请以坐席身份回复用户";
  composerEl.classList.add("is-seat-mode");
  inputEl.placeholder = prompt || "以坐席身份回复用户… Enter 发送";
  setStatus("seat", "坐席模式");
  inputEl.focus();
}

export function exitSeatMode() {
  state.seatMode = false;
  if (!state.approvalMode) seatBanner.hidden = true;
  composerEl.classList.remove("is-seat-mode");
  if (!state.approvalMode) inputEl.placeholder = "输入消息，Enter 发送 · Shift+Enter 换行";
}

export function enterApprovalMode(payload) {
  state.approvalMode = true;
  seatBanner.hidden = false;
  seatBannerTextEl.textContent = (payload && payload.prompt) || "待人工审批";
  composerEl.classList.add("is-approval-mode");
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
  composerEl.classList.remove("is-approval-mode");
  approvalActionsEl.hidden = true;
  approveBtn.disabled = state.busy;
  rejectBtn.disabled = state.busy;
  sendBtn.disabled = state.busy;
  if (!state.seatMode) inputEl.placeholder = "输入消息，Enter 发送 · Shift+Enter 换行";
}
