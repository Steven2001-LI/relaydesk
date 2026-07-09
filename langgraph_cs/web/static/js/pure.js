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
