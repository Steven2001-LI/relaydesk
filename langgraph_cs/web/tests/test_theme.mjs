/*
  theme.js 的 nextTheme 纯函数离线单测（原生 Node，无 DOM）。
  运行：
    node langgraph_cs/web/tests/test_theme.mjs
*/
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";

const __dirname = dirname(fileURLToPath(import.meta.url));
const THEME_JS = join(__dirname, "..", "static", "js", "theme.js");

const { nextTheme } = await import(pathToFileURL(THEME_JS));
assert.equal(typeof nextTheme, "function", "nextTheme 未成功导出");

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log("✓ " + name);
}

test("dark -> light", () => {
  assert.equal(nextTheme("dark"), "light");
});

test("light -> dark", () => {
  assert.equal(nextTheme("light"), "dark");
});

test("未知/缺失状态（如尚未设置过）视为非 dark，切换后给 dark", () => {
  assert.equal(nextTheme(undefined), "dark");
  assert.equal(nextTheme(""), "dark");
});

console.log(`\n全部主题切换单测通过 ✅（${passed} 个用例，未触 DOM）`);
