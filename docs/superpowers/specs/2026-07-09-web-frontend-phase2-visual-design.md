# Web 前端 Phase 2 视觉迭代 — 设计文档

- 日期：2026-07-09
- 范围：`langgraph_cs/web/templates/index.html`、`langgraph_cs/web/static/style.css`、新增 `langgraph_cs/web/static/js/theme.js`
- 前置：Phase 1（工程纪律重构，已合并到 main，commit `6961c4c`）已经把所有视觉值收敛成 CSS 自定义属性（token）+ BEM 类名。Phase 2 建立在这套体系之上，**不重新设计交互逻辑，只给现有状态类配一套新的双主题 token**。

## 背景与方向确认

通过可视化对比三个方向（A 深色工程控制台 2.0 / B 简洁明亮 SaaS / C Liquid Glass）后确定组合方向：**B 做骨架，A 的语义色系统做状态反馈，C 只贡献顶栏一条极淡渐变（不做玻璃模糊）**。亮色为默认主题，暗色跟随系统或手动切换，两套主题共用同一份 DOM 结构和同一份 token 命名，只是值不同——不是另做一套暗色皮肤。

## 非目标

- 不改变现有 DOM 结构/BEM 类名（Phase 1 刚定好，这次只加两个新元素：主题切换按钮、输入框下方常驻提示）。
- 不重新设计交互逻辑（发送/转人工/审批/重试等业务流程不变）。
- 不引入新的第三方图标库——主题切换按钮用内联 SVG 或 emoji，不加依赖。

## 一、主题切换机制

**Token 层**：`:root`（无属性，默认）承载亮色值；`:root[data-theme="dark"]` 承载暗色值。两者 token 名字完全一致（`--bg`、`--panel`、`--blue`、`--cyan`、`--seat`、`--danger`、`--ink`、`--line`……），只是数值不同——所有现有 CSS 规则（`var(--xxx)`）不用改一行，换主题就是换 `<html>` 的 `data-theme` 属性。

**首屏防闪白**：在 `templates/index.html` 的 `<head>` 里，**紧跟在 `<meta charset>` 之后、样式表 `<link>` 之前**，加一段同步内联 `<script>`（不能是 `type="module"`，必须阻塞式同步执行，赶在 CSS 生效前把属性写上）：

```html
<script>
  (function () {
    var saved = localStorage.getItem("relaydesk_theme");
    var theme = saved || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    if (theme === "dark") document.documentElement.setAttribute("data-theme", "dark");
  })();
</script>
```

**切换控件**：`topbar__actions` 里新增一个主题切换按钮（放在"结束会话"左边或右边均可，实现时按视觉平衡定），复用 `.btn-ghost` 的按钮样式，图标用内联 SVG（太阳/月亮两态切换，`hidden` 属性控制显隐，避免用 JS 拼 innerHTML）。按钮属性：

```html
<button class="btn-ghost theme-toggle" id="btn-theme" type="button"
        aria-label="切换到暗色主题" aria-pressed="false">
  <!-- 两个 SVG 图标，用 hidden 属性切换显隐 -->
</button>
```

点击后：切换 `<html data-theme>`、更新 `aria-label`（当前是亮色则显示"切换到暗色主题"，反之亦然）、更新 `aria-pressed`、写 `localStorage.setItem("relaydesk_theme", theme)`。这部分逻辑放新文件 `js/theme.js`（不放进 `app.js` 入口，因为它在 DOMContentLoaded 前就可能需要跑，且和聊天业务逻辑无关，职责独立）。

## 二、颜色语义：决策轨迹内不加绿色，但全局"就绪"状态保留绿色

这是本次唯一在语义规则上做的取舍，明确写下来避免后续误改：

- **决策轨迹（pipeline）内部**：继续沿用 Phase 1 定的"青=AI/成功、蓝=用户、橙=转人工"三色语义，不引入第四个色相。`done`（已完成）用**低饱和青**，`active`（进行中）用**高亮青**——同一色相两个饱和度台阶，不是"完成=灰、进行中=青"（Phase 1 原来是完成态直接褪成灰色；这次改成完成态也保留青色识别度，只是饱和度更低）。
- **全局连接状态指示灯**（`.status__dot`，顶栏那个小圆点）：默认"就绪"态改用**绿色**（新 token `--success`），不再用青色打底——绿色是"一切正常"的通用视觉共识，不为了凑三色规则牺牲这个大众认知。`is-busy`（蓝）/`is-error`（红）/`is-seat`（橙）三个状态覆盖逻辑不变，只是新增一个"什么状态都不是"时的默认值从青变绿。

## 三、Token 表（新增 + 改值）

Phase 1 已有的圆角、字体 token（`--radius-*`、`--font-*`）两套主题共用，不重复定义。以下是需要双主题赋值的颜色 token：

| Token | 亮色（新默认） | 暗色（`[data-theme="dark"]`，多数沿用 Phase 1 原值） |
|---|---|---|
| `--bg` | `#fbfbfd` | `#0a0c16`（比 Phase 1 原 `#0b1020` 略深，配合下面 panel 分层） |
| `--bg-2` | `#f1f2f7` | `#0e1530`（沿用） |
| `--panel` | `#f6f7fa` | `#161a2c`（比背景亮一级，Phase 1 原值 `#121a33` 层级不够，这次拉开） |
| `--panel-2` | `#ffffff` | `#1a2038` |
| `--blue` | `#3d5afe` | `#6d8bff`（沿用） |
| `--blue-2` | `#2f46e0` | `#5a78f0`（沿用） |
| `--blue-soft` | `#7686ff` | `#aab9ff`（沿用） |
| `--cyan` | `#0f8f82`（进行中，高亮） | `#34e5c4`（沿用，进行中） |
| `--cyan-soft` | `#6bb9b0`（已完成，低饱和） | `#7ef0d9`（已完成，低饱和——沿用原 token 但改变用途：Phase 1 里已完成态是纯灰，这次改用这个 token） |
| `--success`（新增） | `#0a8a58` | `#5be8cc` |
| `--success-bg`（新增） | `rgba(10, 138, 88, .10)` | `rgba(91, 232, 204, .12)` |
| `--ink` | `#1a1d29` | `#e6eaf5`（沿用） |
| `--ink-soft` | `#565b70` | `#8a93b2`（沿用） |
| `--ink-faint` | `#8b8fa3` | `#59617f`（沿用） |
| `--ink-on-accent` | `#ffffff`（浅色文字，配深色系强调色按钮——和暗色主题方向相反，见下方说明） | `#0b1020`（深色文字，配浅色系强调色按钮，Phase 1 原值沿用） |
| `--line` | `rgba(10, 12, 24, .08)` | `rgba(255, 255, 255, .08)`（沿用） |
| `--line-strong` | `rgba(10, 12, 24, .14)` | `rgba(255, 255, 255, .14)`（沿用） |
| `--seat` | `#b3690a` | `#ffb454`（沿用） |
| `--seat-soft` | `#8a5108` | `#ffcd8a`（沿用） |
| `--seat-2` | `#cf8324` | `#e09238`（沿用） |
| `--seat-line` | `rgba(179, 105, 10, .35)` | `rgba(255, 180, 84, .35)`（沿用） |
| `--seat-bg` | `rgba(179, 105, 10, .08)` | `rgba(255, 180, 84, .10)`（沿用） |
| `--danger` | `#c92a2a` | `#ff6b6b`（沿用） |
| `--danger-soft` | `#a61e1e` | `#ff9d9d`（沿用） |
| `--danger-line` | `rgba(201, 42, 42, .30)` | `rgba(255, 107, 107, .35)`（沿用） |
| `--danger-bg` | `rgba(201, 42, 42, .07)` | `rgba(255, 107, 107, .10)`（沿用） |
| `--glow-cyan` | `none`（亮色关发光） | `0 0 0 1px rgba(52, 229, 196, .5), 0 0 18px rgba(52, 229, 196, .28)`（沿用） |
| `--glow-seat` | `none` | `0 0 0 1px rgba(255, 180, 84, .55), 0 0 18px rgba(255, 180, 84, .3)`（沿用） |
| 顶栏渐变 alpha | 现有值 × 0.7（如 `.10`→`.07`） | 现有值 × 0.7（同比例收敛，两套主题都要收） |

**`--ink-on-accent` 方向说明**：暗色主题里强调色按钮（蓝/橙渐变）本身是"浅色系"（配深底才够亮），所以按钮文字要用深色（`#0b1020`）才能读。亮色主题里同样几个按钮，为了在白底上有存在感改用"深色系"强调色（`#3d5afe` 这种更饱和的蓝），所以按钮文字反过来要用浅色/白色。**Token 名字和引用它的 CSS 规则完全不变，只是两套主题的取值方向相反**——这是实现时最容易搞错的一点，写代码时格外注意核对，不要想当然套用同一个方向的深浅逻辑。

## 四、交互状态：大部分靠 token 自动换肤，少数需要新 CSS

逐项过一遍 Phase 1 已有的状态类，标注这次要不要动：

| 状态 | 亮色主题下怎么办 | 需要新 CSS？ |
|---|---|---|
| `is-busy`/`is-error`/`is-seat`（连接状态灯） | 沿用现有规则，改 `--blue`/`--danger`/`--seat` 取值即可；默认态（无 class）从 `var(--cyan)` 改成 `var(--success)` | 是——`.status__dot` 基础规则要改一行 |
| `is-typing`（打字动效） | 纯 token 驱动，无需改规则 | 否 |
| `is-active`/`is-done`/`is-skipped`（决策轨迹三态） | `is-active` 用 `--cyan`，`is-done` 从"灰"改成 `--cyan-soft`（新语义，见上），`is-skipped` 沿用灰 | 是——`is-done` 那几行选择器要把 `var(--ink-faint)`/`var(--ink-soft)` 换成 `var(--cyan-soft)` |
| `is-on`（转人工三段卡） | 纯 token 驱动 | 否 |
| `is-seat-mode`/`is-approval-mode`（输入区变色） | 纯 token 驱动 | 否 |
| `.btn-send:hover`（高饱和主按钮） | `brightness(1.08)` 两套主题都还行，沿用 | 否 |
| `.btn-ghost:hover`（描边淡色按钮） | 现有规则本来就是"改 `border-color`/`background`/`color`"（危险色调），不是滤镜/阴影——**天然就是你要的效果**，纯 token 驱动 | 否 |
| `:focus-visible`（键盘焦点环） | Phase 1 已给 `.btn-send`/`.btn-ghost`/`.btn-approval`/`.composer__input` 加了，但焦点环颜色目前靠浏览器默认（`outline` 没显式定颜色），亮色下可能对比度不稳 | 是——显式加 `outline-color: var(--cyan)`，新的主题切换按钮也要补进这组 focus-visible 选择器 |
| 顶栏渐变 | 两套主题各收敛 30%（见 token 表） | 是——`.topbar` 背景渐变的 alpha 值要改 |
| 发光效果（`--glow-cyan`/`--glow-seat`） | 亮色主题关掉（token 值设 `none`），暗色不变 | 否（纯 token 驱动，因为发光本来就是 `box-shadow: var(--glow-xxx)` 引用 token） |

## 五、输入框文案

- `placeholder` 从"输入消息，Enter 发送 · Shift+Enter 换行"简化成"输入消息…"。
- 换行/发送提示改成 composer 下方一条常驻小字提示："Enter 发送 · Shift+Enter 换行"，用现有 `--ink-faint` 色阶，**不放进 `title` 属性**（移动端不可见、发现性差）——常驻显示，不因窄屏隐藏（这正是要解决的"发现性差"问题，窄屏更需要显式提示而不是藏起来）。

## 六、品牌图标核实

`.brand__mark`（ʕ•ᴥ•ʔ）在真实代码里已经显式设置 `font-family: var(--font-mono)`（等宽字体栈），线上渲染正常，不是编码问题——之前可视化对比 mockup 里显得像乱码是因为 mockup 用的是浏览器默认字体，没套项目字体栈，无需改代码。

## 验收标准

1. 首屏加载时不出现"先亮后暗"或"先暗后亮"的闪烁（同步内联脚本在 CSS 生效前设好 `data-theme`）。
2. localStorage 有记录时以记录为准；无记录时跟随系统 `prefers-color-scheme`；系统也没有偏好时默认亮色。
3. 主题切换按钮：点击后立即生效、`aria-label`/`aria-pressed` 同步更新、刷新页面后保持上次选择。
4. 决策轨迹里不出现绿色；顶栏"就绪"状态灯是绿色。
5. 亮色主题下无发光效果；暗色主题发光效果与 Phase 1 视觉一致。
6. 键盘 Tab 到任意可交互元素（含新的主题切换按钮），焦点环在亮/暗两套主题下都清晰可辨。
7. 输入框 placeholder 为"输入消息…"，composer 下方常驻显示"Enter 发送 · Shift+Enter 换行"，窄屏（<560px）下依然可见。
8. 用 Task 1 已经建好的浏览器走查清单（发消息/转人工/审批/结束会话/切身份/窄屏折叠）在**两套主题下各跑一遍**，视觉与交互无回归。

## 未决问题

无——颜色语义、token 值、主题切换机制、交互状态取舍均已在本文档中明确。实现阶段如果发现具体某个 token 的对比度实测不达标（比如 `--cyan-soft` 在某些屏幕上看起来发灰），允许在验收阶段微调具体色值，不需要重新走一遍设计讨论——只要三色语义规则和主题切换机制本身不变。
