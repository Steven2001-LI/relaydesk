/*
  renderMarkdown 的离线单测（原生 Node，无构建链、无 DOM、无网络）。

  策略：app.js 是浏览器脚本，加载即访问 DOM（$("#messages") 等），不能直接 import。
  这里只抽取被显式标注的「前端纯函数块」（toolLabel / escapeHtml / classifyLine /
  renderMarkdown，及其依赖常量），在隔离 vm 沙箱里求值后做断言。

  核心覆盖（P0-2 修复点）：
    项间夹空行的有序列表 -> 合并进同一个 <ol>，靠浏览器自动顺序编号（不再每项都「1.」）。

  运行：
    node langgraph_cs/web/tests/test_markdown.mjs
*/
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";
import assert from "node:assert/strict";

const __dirname = dirname(fileURLToPath(import.meta.url));
const APP_JS = join(__dirname, "..", "static", "app.js");

// ── 从 app.js 抽取被标注的纯函数块（两个 marker 之间） ──
const src = readFileSync(APP_JS, "utf8");
const START = ">>> PURE-MARKDOWN-BLOCK-START";
const END = "<<< PURE-MARKDOWN-BLOCK-END";
const i = src.indexOf(START);
const j = src.indexOf(END);
assert.ok(i !== -1 && j !== -1 && j > i, "未在 app.js 找到纯 markdown 函数块的 marker");
// 从 START 行的下一行开始、到 END 行的上一行结束，避免把 marker 注释本身（含 >>> / <<<）带进去。
const afterStart = src.indexOf("\n", i) + 1;
const beforeEnd = src.lastIndexOf("\n", j);
const block = src.slice(afterStart, beforeEnd);

// 在隔离沙箱里求值，导出纯函数。
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(
  block + [
    "",
    "this.renderMarkdown = renderMarkdown;",
    "this.toolLabel = toolLabel;",
    "this.buildChatBody = buildChatBody;",
    "this.buildSeatResumeBody = buildSeatResumeBody;",
    "this.buildApprovalResumeBody = buildApprovalResumeBody;",
  ].join("\n"),
  sandbox
);
const renderMarkdown = sandbox.renderMarkdown;
const toolLabel = sandbox.toolLabel;
const buildChatBody = sandbox.buildChatBody;
const buildSeatResumeBody = sandbox.buildSeatResumeBody;
const buildApprovalResumeBody = sandbox.buildApprovalResumeBody;
assert.equal(typeof renderMarkdown, "function", "renderMarkdown 未成功导出");
assert.equal(typeof toolLabel, "function", "toolLabel 未成功导出");
assert.equal(typeof buildChatBody, "function", "buildChatBody 未成功导出");
assert.equal(typeof buildSeatResumeBody, "function", "buildSeatResumeBody 未成功导出");
assert.equal(typeof buildApprovalResumeBody, "function", "buildApprovalResumeBody 未成功导出");

// ── 断言小工具 ──
let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log("✓ " + name);
}
// 统计子串出现次数
const count = (hay, needle) => hay.split(needle).length - 1;
const plain = (value) => JSON.parse(JSON.stringify(value));

// ════════════════════════════════════════════════════════
// 用例
// ════════════════════════════════════════════════════════

test("工具名中文映射：已知工具转中文，未知工具回退原文", () => {
  assert.equal(toolLabel("query_bill"), "查询账单");
  assert.equal(toolLabel("refund_status"), "查询退款进度");
  assert.equal(toolLabel("create_refund_ticket"), "创建退款工单");
  assert.equal(toolLabel("create_ticket"), "创建报障工单");
  assert.equal(toolLabel("check_service_status"), "查询服务状态");
  assert.equal(toolLabel("unknown_tool"), "unknown_tool");
});

test("前端请求 body：chat/resume 都包含 session_user_id", () => {
  assert.deepEqual(
    plain(buildChatBody("查账单", "t-1", " user_001 ")),
    { message: "查账单", thread_id: "t-1", session_user_id: "user_001" }
  );
  assert.deepEqual(
    plain(buildSeatResumeBody("t-2", "user_001", "坐席回复")),
    { thread_id: "t-2", session_user_id: "user_001", seat_reply: "坐席回复" }
  );
  assert.deepEqual(
    plain(buildApprovalResumeBody("t-3", "user_001", true, "已核实")),
    { thread_id: "t-3", session_user_id: "user_001", approval: { approved: true, note: "已核实" } }
  );
  assert.equal(buildChatBody("游客", "t-4", "").session_user_id, "");
});

test("项间夹空行的有序列表 -> 单个 <ol> 顺序编号（P0-2 关键）", () => {
  const md = [
    "1. 第一步",
    "",
    "2. 第二步",
    "",
    "3. 第三步",
    "",
    "4. 第四步",
  ].join("\n");
  const html = renderMarkdown(md);
  // 只有一个 <ol>（不是每项各自一个 -> 否则会从 1 重启变成全 1.）
  assert.equal(count(html, "<ol>"), 1, "应只有一个 <ol>，实际：" + html);
  assert.equal(count(html, "</ol>"), 1, "应只有一个 </ol>，实际：" + html);
  // 四个 <li>，顺序靠浏览器自动编号（源里字面数字已去掉）
  assert.equal(count(html, "<li>"), 4, "应有 4 个 <li>，实际：" + html);
  // 字面数字不应残留在渲染产物里（靠 <ol> 自增，不是写死 1.2.3.）
  assert.ok(!/<li>[^<]*\d+\./.test(html), "li 内不应残留字面序号：" + html);
});

test("紧挨着（无空行）的有序列表 -> 同样单个 <ol>", () => {
  const html = renderMarkdown("1. a\n2. b\n3. c");
  assert.equal(count(html, "<ol>"), 1, html);
  assert.equal(count(html, "<li>"), 3, html);
});

test("无序列表项间夹空行 -> 单个 <ul>", () => {
  const html = renderMarkdown("- a\n\n- b\n\n- c");
  assert.equal(count(html, "<ul>"), 1, html);
  assert.equal(count(html, "<li>"), 3, html);
});

test("列表后接普通段落 -> 列表正常闭合，段落另起", () => {
  const html = renderMarkdown("1. a\n2. b\n\n这是一段普通说明");
  assert.equal(count(html, "<ol>"), 1, html);
  assert.equal(count(html, "</ol>"), 1, html);
  assert.ok(html.includes("<p>这是一段普通说明</p>"), html);
  // 段落必须在 </ol> 之后
  assert.ok(html.indexOf("</ol>") < html.indexOf("<p>这是一段普通说明"), html);
});

test("有序列表后紧跟无序列表 -> 类型切换，各自一个列表", () => {
  const html = renderMarkdown("1. a\n2. b\n- x\n- y");
  assert.equal(count(html, "<ol>"), 1, html);
  assert.equal(count(html, "<ul>"), 1, html);
  assert.ok(html.indexOf("</ol>") < html.indexOf("<ul>"), html);
});

test("步骤项「标题：说明」-> 标题包 .li-title、说明包 .li-desc（P0-4）", () => {
  const html = renderMarkdown("1. 重置密码：进入设置页点击重置");
  assert.ok(html.includes('class="li-title">重置密码：</span>'), html);
  assert.ok(html.includes('class="li-desc">进入设置页点击重置</span>'), html);
});

test("与标题/加粗/code 兼容，且先转义防 XSS", () => {
  const html = renderMarkdown("# 标题\n\n1. **粗**步骤\n\n2. 用 `code`\n\n<script>x</script>");
  assert.ok(html.includes('<h1 class="md-h md-h1">标题</h1>'), html);
  assert.ok(html.includes("<strong>粗</strong>"), html);
  assert.ok(html.includes("<code>code</code>"), html);
  // XSS：尖括号必须被转义，绝不出现真实 <script>
  assert.ok(!html.includes("<script>"), "XSS：不应出现未转义 <script>：" + html);
  assert.ok(html.includes("&lt;script&gt;"), html);
  // 这一整块仍是一个 <ol>（标题不在中间打断列表，但空行+标题分隔时列表正确闭合）
  assert.equal(count(html, "<ol>"), 1, html);
  assert.equal(count(html, "<li>"), 2, html);
});

console.log(`\n全部 markdown 单测通过 ✅（${passed} 个用例，未触网/未触 DOM）`);
