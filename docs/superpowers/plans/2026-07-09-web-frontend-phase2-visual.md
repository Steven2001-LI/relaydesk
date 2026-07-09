# Web 前端 Phase 2 视觉迭代 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 RelayDesk web demo 加双主题（亮色默认 + 暗色，跟随系统/手动切换），复用 Phase 1 已建的 token 体系，视觉方向按 spec 定的"B 骨架 + A 语义色 + C 顶栏渐变"执行，交互行为不变。

**Architecture:** `:root` 存亮色 token 值，`:root[data-theme="dark"]` 覆盖成暗色值；所有组件规则只认 token/`color-mix()`，不再有任何绑死某个主题色相的字面量。新增 `js/theme.js` 独立处理主题切换（不进 `app.js` 入口）。

**Tech Stack:** 原生 CSS 自定义属性 + `color-mix()`（现代浏览器原生支持，Chrome 111+/Safari 16.2+/Firefox 113+，2023 年之后的版本，这是个人 demo 项目，不用兼容旧浏览器）、原生 ES module、Node 内置测试脚本。

## Global Constraints

- 不改变现有 DOM 结构/BEM 类名（除了本计划明确新增的：主题切换按钮、composer 常驻提示文字）。
- 不改交互逻辑，只改视觉呈现（颜色/主题切换）。
- 决策轨迹（pipeline）内部不出现绿色；`done`=低饱和青（`--cyan-soft`），`active`=高亮青（`--cyan`）。
- 全局连接状态灯默认（"就绪"）态用新 token `--success`（绿），不用青色。
- 亮色主题关闭发光效果（`--glow-cyan`/`--glow-seat` 在亮色下取值 `none`）。
- 首屏必须同步读取主题、不能有"先亮后暗"或"先暗后亮"的闪烁。
- `.btn-ghost:hover` 等已经是"改背景/边框/文字色"而非滤镜/阴影的交互，不新增浮动阴影效果。

## 本计划相对 spec 的补充说明

Spec（`docs/superpowers/specs/2026-07-09-web-frontend-phase2-visual-design.md`）给出了 token 表和交互状态取舍表，但写 spec 时没有逐行核对 `style.css` 里除 `:root` 之外散落的字面量颜色——实际读代码发现 `:root` 外还有 **25 处**字面量 `rgba()`（比如 `.brand__mark` 背景、决策轨迹 active/done 态的发光和底色、状态灯的阴影等），全部硬编码着暗色主题的色相 RGB 值（如 `rgba(52, 229, 196, .14)`），亮色主题下直接复用会因为色相不对而"糊"或"太浅看不清"。

处理方式：这些字面量不再逐个建新 token（会让 token 表膨胀到无法维护），改用 CSS 原生 `color-mix()` 函数，让它们从对应的具名 token 里动态取色——比如 `rgba(52, 229, 196, .14)` 改成 `color-mix(in srgb, var(--cyan) 14%, transparent)`，值不变，但换主题时自动跟着 `--cyan` 的当前值走，不用维护两套平行的 rgba 表。这是本计划相对 spec 的**技术实现细化**，不改变 spec 定的颜色语义和 token 表，只是把"怎么让所有装饰性色调都跟着主题走"这件事做得更彻底。

另外两处 spec 没明确提到、必须做取舍的地方：

1. **`body` 背景的径向高光渐变**（现有代码里两团很淡的蓝/青径向渐变，营造"角落发光"的暗色工程感）——**只保留在暗色主题**，亮色主题用纯色背景（`var(--bg)`）。理由：可视化对比时亮色 mockup 展示的就是纯色背景，没有加径向渐变；现在保持和已验证过的 mockup 一致。
2. **顶栏渐变的具体色值**：spec 只写了"现有值 × 0.7"，但顶栏渐变在真实代码里**目前根本不存在**（现有 `.topbar` 只有一层纯色半透明 + 模糊，没有渐变）——这是 Phase 2 要新增的效果，不是"调低已有值"。取用可视化定稿版（v3 mockup）里验证过的具体数值：亮色 `rgba(109,139,255,.07)` → `rgba(52,229,196,.055)`；暗色 `rgba(109,139,255,.13)` → `rgba(52,229,196,.09)`。

---

## Task 1: 双主题 Token 体系 + 主题切换（核心）

**Effort:** high —— 改动面覆盖 `style.css` 全文、新增 JS 模块、新增 HTML 元素，且没有自动化视觉回归测试兜底，需要两套主题各走一遍完整的浏览器验证清单。

**Files:**
- Modify: `langgraph_cs/web/static/style.css`
- Modify: `langgraph_cs/web/templates/index.html`
- Create: `langgraph_cs/web/static/js/theme.js`
- Create: `langgraph_cs/web/tests/test_theme.mjs`

**Interfaces：**
- `theme.js` 导出：`nextTheme(current)` — 纯函数，`"dark"` 输入返回 `"light"`，其余任何输入（含 `"light"`/`undefined`）返回 `"dark"`。
- `theme.js` 内部（不导出）：`initThemeToggle()` — 读取 `<html>` 当前 `data-theme`、初始化按钮态、绑定点击切换；用 `typeof document !== "undefined"` 守卫，模块加载时若在浏览器环境自动执行一次，Node 环境（单测 import 时）跳过，只暴露 `nextTheme` 供测试。

- [ ] **Step 1: 改写 `style.css` 的 `:root` 令牌块为亮色默认值**

打开 `langgraph_cs/web/static/style.css`，把整个第 16–66 行的 `:root { ... }` 块替换成：

```css
:root {
  /* ── 调色板（亮色 · 默认主题）── */
  --bg: #fbfbfd;
  --bg-2: #f1f2f7;
  --panel: #f6f7fa;
  --panel-2: #ffffff;

  /* 蓝：用户消息 + 主按钮 */
  --blue: #3d5afe;
  --blue-2: #2f46e0;
  --blue-soft: #7686ff;

  /* 青：AI / 决策轨迹进行中(--cyan) / 已完成(--cyan-soft，低饱和) */
  --cyan: #0f8f82;
  --cyan-soft: #6bb9b0;

  /* 绿：全局"就绪/成功"状态灯专用，不进决策轨迹（决策轨迹只用青色系） */
  --success: #0a8a58;
  --success-bg: rgba(10, 138, 88, .10);

  --ink: #1a1d29;         /* 主文字 */
  --ink-soft: #565b70;    /* 次要文字 */
  --ink-faint: #8b8fa3;   /* 更弱文字（占位/待命） */
  --line: rgba(10, 12, 24, .08);   /* 细边框 */
  --line-strong: rgba(10, 12, 24, .14);

  /* 橙：转人工 / 警告 / 人工介入 */
  --seat: #b3690a;
  --seat-soft: #8a5108;
  --seat-line: rgba(179, 105, 10, .35);
  --seat-bg: rgba(179, 105, 10, .08);
  --seat-2: #cf8324;          /* 按钮渐变深色端 */

  /* 红：错误/危险 */
  --danger: #c92a2a;
  --danger-soft: #a61e1e;
  --danger-line: rgba(201, 42, 42, .30);
  --danger-bg: rgba(201, 42, 42, .07);

  /* 强调色按钮上的文字色：亮色主题按钮本身是深色系强调色，文字要浅色 */
  --ink-on-accent: #ffffff;

  /* ── 统一圆角层级 ── */
  --radius-bubble: 12px;
  --radius-btn: 13px;
  --radius-tag: 8px;
  --radius-panel: 10px;

  /* 字体栈：标题 / 正文 / 等宽数据（两套主题共用，不随主题变） */
  --font-display: "Space Grotesk", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
  --font-body: "Inter", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", system-ui, sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;

  /* 辉光：亮色主题关闭（发光在白底上显脏），暗色主题下方覆盖成真的发光值 */
  --glow-cyan: none;
  --glow-seat: none;

  /* 顶栏渐变（新增效果，C 方向的少量气质点缀） */
  --topbar-glow-1: rgba(109, 139, 255, .07);
  --topbar-glow-2: rgba(52, 229, 196, .055);

  /* 毛玻璃层背景（topbar / composer 半透明+模糊时的底色） */
  --topbar-wash: rgba(255, 255, 255, .72);
  --composer-wash: rgba(255, 255, 255, .65);

  /* 零散装饰色（原先绑死暗色字面量，现在改令牌，配合 color-mix() 使用） */
  --code-bg: rgba(10, 12, 24, .06);
  --skipped-bg: rgba(10, 12, 24, .035);
  --scrollbar-thumb: rgba(10, 12, 24, .15);
  --scrollbar-thumb-hover: rgba(10, 12, 24, .25);
}
```

- [ ] **Step 2: 紧接着新增暗色主题覆盖块**

在刚才那个 `:root { ... }` 块的 `}` 之后（原来的第 66 行之后），插入一整个新块：

```css

/* ════════════════════════════════════════════════════════════
   暗色主题：token 值覆盖（Phase 1 原有的深色工程控制台配色，
   --bg/--panel/--panel-2 三个值比 Phase 1 略深/略分层，
   --topbar-wash/--composer-wash/--glow-* 是本轮新增或恢复发光）
   ════════════════════════════════════════════════════════════ */
:root[data-theme="dark"] {
  --bg: #0a0c16;
  --bg-2: #0e1530;
  --panel: #161a2c;
  --panel-2: #1a2038;

  --blue: #6d8bff;
  --blue-2: #5a78f0;
  --blue-soft: #aab9ff;

  --cyan: #34e5c4;
  --cyan-soft: #7ef0d9;

  --success: #5be8cc;
  --success-bg: rgba(91, 232, 204, .12);

  --ink: #e6eaf5;
  --ink-soft: #8a93b2;
  --ink-faint: #59617f;
  --line: rgba(255, 255, 255, .08);
  --line-strong: rgba(255, 255, 255, .14);

  --seat: #ffb454;
  --seat-soft: #ffcd8a;
  --seat-line: rgba(255, 180, 84, .35);
  --seat-bg: rgba(255, 180, 84, .10);
  --seat-2: #e09238;

  --danger: #ff6b6b;
  --danger-soft: #ff9d9d;
  --danger-line: rgba(255, 107, 107, .35);
  --danger-bg: rgba(255, 107, 107, .1);

  --ink-on-accent: #0b1020;

  --glow-cyan: 0 0 0 1px rgba(52, 229, 196, .5), 0 0 18px rgba(52, 229, 196, .28);
  --glow-seat: 0 0 0 1px rgba(255, 180, 84, .55), 0 0 18px rgba(255, 180, 84, .3);

  --topbar-glow-1: rgba(109, 139, 255, .13);
  --topbar-glow-2: rgba(52, 229, 196, .09);

  --topbar-wash: rgba(11, 16, 32, .6);
  --composer-wash: rgba(11, 16, 32, .5);

  --code-bg: rgba(255, 255, 255, .07);
  --skipped-bg: rgba(255, 255, 255, .03);
  --scrollbar-thumb: rgba(255, 255, 255, .12);
  --scrollbar-thumb-hover: rgba(255, 255, 255, .2);
}
```

- [ ] **Step 3: `body` 背景——亮色纯色，暗色恢复径向高光**

找到（原第 75–86 行）：

```css
body {
  background:
    radial-gradient(900px 520px at 100% -8%, rgba(109, 139, 255, .12) 0%, transparent 60%),
    radial-gradient(800px 480px at -5% 105%, rgba(52, 229, 196, .08) 0%, transparent 55%),
    var(--bg);
  color: var(--ink);
  font-family: var(--font-body);
  font-size: 15px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}
```

改成：

```css
body {
  background: var(--bg);
  color: var(--ink);
  font-family: var(--font-body);
  font-size: 15px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}

/* 暗色主题专属：角落径向高光，亮色主题不需要（mockup 验证过纯色即可） */
:root[data-theme="dark"] body {
  background:
    radial-gradient(900px 520px at 100% -8%, rgba(109, 139, 255, .12) 0%, transparent 60%),
    radial-gradient(800px 480px at -5% 105%, rgba(52, 229, 196, .08) 0%, transparent 55%),
    var(--bg);
}
```

- [ ] **Step 4: `.topbar` 加渐变，`.topbar`/`.composer` 背景换成 wash token**

找到（原第 102–111 行）：

```css
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 22px;
  border-bottom: 1px solid var(--line);
  background: rgba(11, 16, 32, .6);
  backdrop-filter: blur(8px);
}
```

改成：

```css
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 22px;
  border-bottom: 1px solid var(--line);
  background:
    linear-gradient(120deg, var(--topbar-glow-1), var(--topbar-glow-2) 60%, transparent),
    var(--topbar-wash);
  backdrop-filter: blur(8px);
}
```

找到（原第 486–493 行）：

```css
.composer {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 18px 16px;
  border-top: 1px solid var(--line);
  background: rgba(11, 16, 32, .5);
}
```

改成：

```css
.composer {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 18px 16px;
  border-top: 1px solid var(--line);
  background: var(--composer-wash);
}
```

- [ ] **Step 5: 把 25 处绑死暗色色相的字面量 `rgba()` 换成 `color-mix()` 或既有 token**

逐条查找并替换（每条都是精确字符串替换，不影响其他行）：

| 所在规则 / 原文位置 | 旧文本 | 新文本 |
|---|---|---|
| `.app` 顶部高光（原 95-96 行） | `rgba(255, 255, 255, .015)` | **不改**——0.015 透明度在任何底色上都不可见，两套主题都是无效果，不值得为此加令牌 |
| `.brand__mark` 背景 | `background: rgba(52, 229, 196, .12);` | `background: color-mix(in srgb, var(--cyan) 12%, transparent);` |
| `.status__dot` 默认态（原 166-172 行整块） | `background: var(--cyan);`<br>`box-shadow: 0 0 8px rgba(52, 229, 196, .7);` | `background: var(--success);`<br>`box-shadow: 0 0 8px color-mix(in srgb, var(--success) 70%, transparent);` |
| `.status.is-busy .status__dot` | `box-shadow: 0 0 8px rgba(109, 139, 255, .7);` | `box-shadow: 0 0 8px color-mix(in srgb, var(--blue) 70%, transparent);` |
| `.status.is-error .status__dot` | `box-shadow: 0 0 8px rgba(255, 107, 107, .7);` | `box-shadow: 0 0 8px color-mix(in srgb, var(--danger) 70%, transparent);` |
| `.status.is-seat .status__dot` | `box-shadow: 0 0 8px rgba(255, 180, 84, .7);` | `box-shadow: 0 0 8px color-mix(in srgb, var(--seat) 70%, transparent);` |
| `.identity-pill` 背景/边框 | `background: rgba(52, 229, 196, .08);`<br>`border: 1px solid rgba(52, 229, 196, .18);` | `background: color-mix(in srgb, var(--cyan) 8%, transparent);`<br>`border: 1px solid color-mix(in srgb, var(--cyan) 18%, transparent);` |
| `.btn-ghost:hover` 背景 | `background: rgba(255, 107, 107, .08);` | `background: var(--danger-bg);`（已有现成 token，不用 color-mix） |
| `.seat-banner__dot` 阴影 | `box-shadow: 0 0 6px rgba(255, 180, 84, .6);` | `box-shadow: 0 0 6px color-mix(in srgb, var(--seat) 60%, transparent);` |
| `.msg__bubble code` 背景 | `background: rgba(255, 255, 255, .07);` | `background: var(--code-bg);` |
| `.btn-quick:hover` 边框 | `border-color: rgba(52, 229, 196, .35);` | `border-color: color-mix(in srgb, var(--cyan) 35%, transparent);` |
| `.btn-approval--reject` 背景 | `background: rgba(255, 180, 84, .08);` | `background: color-mix(in srgb, var(--seat) 8%, transparent);` |
| `.composer.is-seat-mode .composer__input` / `.composer.is-approval-mode .composer__input`（两处，文本相同） | `background: rgba(255, 180, 84, .06);` | `background: color-mix(in srgb, var(--seat) 6%, transparent);` |
| `.stage.is-active .stage__node` 背景 | `background: rgba(52, 229, 196, .14);` | `background: color-mix(in srgb, var(--cyan) 14%, transparent);` |
| `@keyframes nodepulse`（两条 box-shadow） | `0%, 100% { box-shadow: 0 0 0 1px rgba(52, 229, 196, .5), 0 0 12px rgba(52, 229, 196, .22); }`<br>`50% { box-shadow: 0 0 0 1px rgba(52, 229, 196, .6), 0 0 22px rgba(52, 229, 196, .42); }` | `0%, 100% { box-shadow: 0 0 0 1px color-mix(in srgb, var(--cyan) 50%, transparent), 0 0 12px color-mix(in srgb, var(--cyan) 22%, transparent); }`<br>`50% { box-shadow: 0 0 0 1px color-mix(in srgb, var(--cyan) 60%, transparent), 0 0 22px color-mix(in srgb, var(--cyan) 42%, transparent); }` |
| `.stage.is-done .stage__node` 背景/边框（原 703-708 行） | `background: rgba(52, 229, 196, .08);`<br>`border-color: rgba(52, 229, 196, .4);` | `background: color-mix(in srgb, var(--cyan-soft) 8%, transparent);`<br>`border-color: color-mix(in srgb, var(--cyan-soft) 40%, transparent);` |
| `.stage.is-done .stage__line`（原 719-721 行，出现 2 处：桌面版一处 + 900px 媒体查询里一处） | `background: rgba(52, 229, 196, .35);` | `background: color-mix(in srgb, var(--cyan-soft) 35%, transparent);` |
| `.stage.is-skipped .stage__node` 背景 | `background: rgba(255, 255, 255, .03);` | `background: var(--skipped-bg);` |
| 滚动条 thumb（原 822-827 行） | `background: rgba(255, 255, 255, .12);`（默认态）<br>`background: rgba(255, 255, 255, .2);`（hover 态） | `background: var(--scrollbar-thumb);`<br>`background: var(--scrollbar-thumb-hover);` |

替换完后跑一次校验，不依赖行号（用 `awk` 按 `:root` 块的花括号配对，把两个 token 定义块整个挖掉，只看剩下的正文里还有没有字面量 `rgba(`）：

```bash
awk '
  /^:root/ { inblock=1 }
  inblock && /}/ { inblock=0; next }
  !inblock { print }
' langgraph_cs/web/static/style.css | grep -noE 'rgba\([0-9., ]+\)'
```

期望：只剩一行 `rgba(255, 255, 255, .015)`（`.app` 那处刻意不改的、不可见的高光效果）。如果输出还有别的 `rgba(...)`，说明 Step 5 的替换表有遗漏，逐条核对没做完的那一行。

- [ ] **Step 6: 决策轨迹"已完成"态文字颜色从灰改青**

找到（原第 715-717 行）：

```css
.stage.is-done .stage__body { opacity: .75; }
.stage.is-done .stage__label { color: var(--ink-soft); }
.stage.is-done .stage__value { color: var(--ink-faint); }
```

改成：

```css
.stage.is-done .stage__body { opacity: .75; }
.stage.is-done .stage__label { color: var(--cyan-soft); }
.stage.is-done .stage__value { color: var(--cyan-soft); }
```

（label/value 用同一个 token 没关系，层级感靠 `.stage__body` 已有的 `opacity: .75` 撑开，不用再叠一个更淡的 token。）

- [ ] **Step 7: `templates/index.html` 加首屏防闪白脚本 + 主题切换按钮**

在 `<head>` 里，`<meta charset="UTF-8" />` 之后、`<meta name="viewport" ...>` 之前（原第 4-5 行之间），插入：

```html
  <script>
    (function () {
      var saved = localStorage.getItem("relaydesk_theme");
      var theme = saved || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      if (theme === "dark") document.documentElement.setAttribute("data-theme", "dark");
    })();
  </script>
```

在 `.topbar__actions` 里，`结束会话` 按钮（原第 55 行 `<button class="btn-ghost" id="btn-new" ...>结束会话</button>`）**之前**插入主题切换按钮：

```html
        <button class="btn-ghost theme-toggle" id="btn-theme" type="button"
                aria-label="切换到暗色主题" aria-pressed="false">
          <svg class="theme-toggle__sun" viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
            <circle cx="12" cy="12" r="4.2" fill="currentColor" />
            <g stroke="currentColor" stroke-width="1.6" stroke-linecap="round">
              <path d="M12 2.5v2.4M12 19.1v2.4M4.4 4.4l1.7 1.7M17.9 17.9l1.7 1.7M2.5 12h2.4M19.1 12h2.4M4.4 19.6l1.7-1.7M17.9 6.1l1.7-1.7" />
            </g>
          </svg>
          <svg class="theme-toggle__moon" viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" hidden>
            <path fill="currentColor" d="M20 14.5A8.5 8.5 0 0 1 9.5 4 8.5 8.5 0 1 0 20 14.5Z" />
          </svg>
        </button>
```

- [ ] **Step 8: `style.css` 补主题切换按钮样式**

在 `.btn-ghost:hover { ... }` 规则块（原第 237-241 行）之后插入：

```css
.theme-toggle {
  width: 28px;
  padding: 0;
  justify-content: center;
}
.theme-toggle__sun,
.theme-toggle__moon {
  display: block;
}
```

- [ ] **Step 9: 创建 `js/theme.js`**

```js
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
  const sun = btn.querySelector(".theme-toggle__sun");
  const moon = btn.querySelector(".theme-toggle__moon");
  if (sun) sun.hidden = theme === "dark";
  if (moon) moon.hidden = theme !== "dark";
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
```

- [ ] **Step 10: `templates/index.html` 引入 theme.js**

在 `<script type="module" src="/static/app.js"></script>`（原第 154 行）**之前**插入一行：

```html
  <script type="module" src="/static/js/theme.js"></script>
```

（两个 `<script type="module">` 顺序不影响功能——ES module 之间没有共享状态依赖，`theme.js` 完全独立于 `app.js`。）

- [ ] **Step 11: 创建 `test_theme.mjs`**

```js
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
```

- [ ] **Step 12: 跑测试**

```bash
node langgraph_cs/web/tests/test_theme.mjs
```
期望：3 个 `✓`，末尾 `全部主题切换单测通过 ✅（3 个用例，未触 DOM）`，退出码 0。

```bash
node langgraph_cs/web/tests/test_markdown.mjs
langgraph_cs/.venv/bin/python -m langgraph_cs.web.tests.test_server_offline
```
期望：两个既有测试套件保持全绿（本 task 不改它们覆盖的代码，但改了 `style.css`/`index.html`，跑一遍确认没有意外波及）。

- [ ] **Step 13: 浏览器走查（亮色 + 暗色各一遍）**

```bash
langgraph_cs/.venv/bin/python -m langgraph_cs.web
```

**防闪白检查**：先手动把 localStorage 清空（devtools Application 面板或 `localStorage.clear()`），分别在系统"浅色模式"和"深色模式"下（可以用 devtools 的 rendering 面板模拟 `prefers-color-scheme`，不用真的切系统设置）硬刷新页面，确认没有"先出现另一个主题的颜色再跳变"的闪烁。

**两套主题各跑一遍**（先亮色，点主题切换按钮切到暗色再跑一遍）：
1. 发一条消息 → 决策轨迹依次点亮，`active` 态是高亮青、`done` 态是低饱和青（不是灰、不是绿）
2. 顶部状态灯默认"就绪"是绿色（`--success`），发消息时变蓝（`is-busy`）
3. 触发"转人工" → 系统条、坐席三段卡、输入区变色，琥珀色在当前主题下清晰可辨
4. "结束会话"、切换身份下拉——功能不变
5. 窄屏 <900px/<560px 折叠正常
6. 主题切换按钮：点击后图标（太阳/月亮）切换、`aria-label`/`aria-pressed` 变化（用 devtools accessibility 面板或 `read_page` 工具确认属性值，不只是看图标）；刷新页面后主题保持上次选择
7. Tab 键走一遍所有可交互元素（含新的主题按钮），确认每个都有清晰可见的焦点环，亮/暗两套主题下都能看清

- [ ] **Step 14: 提交**

```bash
cd /Users/xuyangli/projects/03_个人项目/langgraph-cs-agent
git add langgraph_cs/web/static/style.css langgraph_cs/web/templates/index.html langgraph_cs/web/static/js/theme.js langgraph_cs/web/tests/test_theme.mjs
git commit -m "$(cat <<'EOF'
feat(web): 新增亮/暗双主题，亮色为默认主题

:root 存亮色 token 值，:root[data-theme="dark"] 覆盖暗色值；所有原先
绑死暗色色相的字面量 rgba() 改成 color-mix() 动态取色，换主题不用维护
平行色表。新增 js/theme.js 处理主题切换（跟随系统 + 手动覆盖 +
localStorage 持久化），首屏用同步内联脚本防闪白。决策轨迹"已完成"态
文字色从灰改成低饱和青；全局状态灯默认态从青改绿（--success）。
EOF
)"
```

---

## Task 2: 输入框文案 + 可访问性收尾

**Effort:** low-medium —— 改动范围小、无跨文件一致性风险，主要是核对文字/焦点环细节。

**Files:**
- Modify: `langgraph_cs/web/templates/index.html`
- Modify: `langgraph_cs/web/static/style.css`

- [ ] **Step 1: composer 输入框文案改动**

打开 `langgraph_cs/web/templates/index.html`，找到（Task 1 完成后，原第 84-90 行的位置不变，只是前面多了主题按钮和 theme.js 引入，行号会往后偏移——用文本内容定位而不是行号）：

```html
            <textarea
              id="input"
              class="composer__input"
              rows="1"
              placeholder="输入消息，Enter 发送 · Shift+Enter 换行"
              autocomplete="off"
            ></textarea>
```

改成：

```html
            <textarea
              id="input"
              class="composer__input"
              rows="1"
              placeholder="输入消息…"
              autocomplete="off"
            ></textarea>
```

在 `.composer__row` 那个 `<div>` 结束标签之后（即 `</div>`，原第 96 行，紧挨着发送按钮所在的那个 row 容器关闭标签之后）、`.composer__approval-actions` 那个 `<div>` 之前，插入一条常驻小字提示：

```html
          <p class="composer__hint">Enter 发送 · Shift+Enter 换行</p>
```

- [ ] **Step 2: `.composer__hint` 样式**

在 `style.css` 的 `.composer__input::placeholder { color: var(--ink-faint); }` 规则（原第 520 行）之后插入：

```css
.composer__hint {
  margin: 0;
  font-size: 11px;
  color: var(--ink-faint);
  font-family: var(--font-mono);
}
```

- [ ] **Step 3: `:focus-visible` 补显式轮廓色 + 主题按钮加入焦点组**

找到（原第 813-819 行）：

```css
/* ── 焦点可见（可访问性底线）── */
:focus-visible {
  outline: 2px solid var(--cyan);
  outline-offset: 2px;
  border-radius: 4px;
}
.btn-send:focus-visible, .btn-ghost:focus-visible, .btn-approval:focus-visible { outline-offset: 3px; }
```

`:focus-visible` 这条规则本来就已经显式写了 `outline: 2px solid var(--cyan);`（不是依赖浏览器默认色），Task 1 的 Step 1/2 已经让 `--cyan` 在两套主题下都有合适取值，**这条规则不用改**。只需要把新增的主题切换按钮加进第二条选择器：

```css
.btn-send:focus-visible, .btn-ghost:focus-visible, .btn-approval:focus-visible, .theme-toggle:focus-visible { outline-offset: 3px; }
```

（`.theme-toggle` 本身复用 `.btn-ghost` 类，`:focus-visible` 通用规则已经覆盖它；这一步只是让它和其他按钮一样有 `outline-offset: 3px` 的偏移量，视觉上和相邻的 `.btn-ghost`/`.btn-send` 一致。）

- [ ] **Step 4: 跑测试**

```bash
node langgraph_cs/web/tests/test_markdown.mjs
node langgraph_cs/web/tests/test_theme.mjs
langgraph_cs/.venv/bin/python -m langgraph_cs.web.tests.test_server_offline
```
期望：三个套件全绿（本 task 不改任何 JS 逻辑，纯 HTML/CSS 文案与样式微调）。

- [ ] **Step 5: 浏览器核对**

```bash
langgraph_cs/.venv/bin/python -m langgraph_cs.web
```

确认：输入框占位符是"输入消息…"；composer 下方常驻显示"Enter 发送 · Shift+Enter 换行"，窄屏（<560px）下依然可见（不因为空间紧张被隐藏）；Tab 到主题切换按钮时焦点环偏移量和旁边的"结束会话"按钮视觉一致。

- [ ] **Step 6: 提交**

```bash
git add langgraph_cs/web/templates/index.html langgraph_cs/web/static/style.css
git commit -m "$(cat <<'EOF'
polish(web): 输入框占位符简化 + 常驻发送提示 + 主题按钮焦点环收尾

placeholder 从"输入消息，Enter 发送 · Shift+Enter 换行"简化为
"输入消息…"；换行/发送说明改成 composer 下方常驻小字（不塞进 title，
移动端不可见、发现性差）。主题切换按钮补进 :focus-visible 分组。
EOF
)"
```

---

## 最终验收（两个 task 全部完成后）

- [ ] `node langgraph_cs/web/tests/test_markdown.mjs` 全绿
- [ ] `node langgraph_cs/web/tests/test_theme.mjs` 全绿
- [ ] `langgraph_cs/.venv/bin/python -m langgraph_cs.web.tests.test_server_offline` 全绿
- [ ] 亮色（默认）+ 暗色（手动切换）各完整走查一遍 Task 1 Step 13 的 7 项检查
- [ ] Task 1 Step 5 的 `awk` 校验命令跑一遍，输出只剩 `rgba(255, 255, 255, .015)` 一行
- [ ] `git diff main --stat` 改动文件只有：`style.css`、`templates/index.html`、新增的 `static/js/theme.js`、新增的 `tests/test_theme.mjs`
