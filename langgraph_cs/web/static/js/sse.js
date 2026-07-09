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
