// session.js —— thread_id / session_user_id 持久化 + 共享会话状态 state + 身份/会话 pill 渲染。
import { threadPill, identityPill } from "./dom.js";

// localStorage 里存 thread_id 的 key（新 key + 历史兼容 key）。
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
