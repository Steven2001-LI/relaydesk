// theme.js —— 主题（亮/暗）切换：读取当前状态、绑定顶栏按钮、持久化到 localStorage。
//
// 首屏防闪白的初始 data-theme 设置由 templates/index.html <head> 里的同步内联脚本完成
// （必须在 CSS 生效前跑完，不能等这个 ES module 异步加载）；这个模块只负责按钮之后的
// 交互——读取 <html> 上已经设好的 data-theme 来初始化按钮态，不重复做一遍
// localStorage/系统偏好判断，避免两处逻辑分别判断、以后改一处忘了改另一处。

const THEME_STORAGE_KEY = "relaydesk_theme";

// 纯函数，可在 Node 里直接单测：不是 "dark" 的输入都切成 "dark"，是 "dark" 的切成 "light"。
export function nextTheme(current) {
  return current === "dark" ? "light" : "dark";
}

function currentTheme() {
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

function applyTheme(theme, btn) {
  if (theme === "dark") document.documentElement.setAttribute("data-theme", "dark");
  else document.documentElement.removeAttribute("data-theme");
  localStorage.setItem(THEME_STORAGE_KEY, theme);

  btn.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
  btn.setAttribute("aria-label", theme === "dark" ? "切换到亮色主题" : "切换到暗色主题");
  // 注意：图标是 <svg>（SVGElement），不像 HTMLElement 那样反射 `.hidden` IDL 属性，
  // 直接 `sun.hidden = ...` 只会挂一个无效的 JS expando、不改真实 hidden 属性，图标不会切换。
  // 用 toggleAttribute（定义在 Element 上，SVG 也生效）显式增删 hidden 属性。
  const sun = btn.querySelector(".theme-toggle__sun");
  const moon = btn.querySelector(".theme-toggle__moon");
  if (sun) sun.toggleAttribute("hidden", theme === "dark");
  if (moon) moon.toggleAttribute("hidden", theme !== "dark");
}

function initThemeToggle() {
  const btn = document.getElementById("btn-theme");
  if (!btn) return;
  applyTheme(currentTheme(), btn);
  btn.addEventListener("click", () => {
    applyTheme(nextTheme(currentTheme()), btn);
  });
}

// 只在浏览器环境自动跑；Node 单测 import 这个文件时 document 不存在，跳过，
// 只用得到上面导出的 nextTheme。
if (typeof document !== "undefined") {
  initThemeToggle();
}
