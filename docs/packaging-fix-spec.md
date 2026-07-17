# 打包与测试依赖修复规范（Packaging Fix Spec）

> 执行者：Claude Code。评审方：Codex（只读）。仲裁依据：本文件。
> 流水线位置：插在注释清洗（docs/comment-cleanup-spec.md）之前执行；注释清洗基于本阶段合并后的 main 开工。
> 性质：配置/文档级修复，零逻辑代码变更。分支：`fix/packaging-and-test-deps`。
> 修订：v2（2026-07-17）——吸收 Codex no-go 评审的 9 项阻塞修订（门禁可失败化、快照与
> Git 上下文分离、工具链与 Python 版本口径、G4 与 README 决策绑定等）。
> 修订：v3（2026-07-17）——吸收 Codex v2 复核的 4 项最小阻塞（commit 1 基线环境自包含化、
> §3.2 lock 程序自包含化、逐 commit G3 快照生命周期、删除跨平台宣称选项）。

## 0. 事实基线（Claude Code 与 Codex 双方实测确认，2026-07-17）

1. **wheel 缺资源**：`uv build --wheel` 实测，wheel 内 web 目录仅含 3 个 Python 文件 +
   `static/app.js` + `static/style.css`；缺 `web/templates/index.html` 与 `web/static/js/` 下
   7 个模块（dom / messages / pipeline / pure / session / sse / theme）。METADATA 有 13 条
   Requires-Dist，无 jinja2。根因：package-data `"static/*"` glob 不递归，且未声明 `templates/*`。
2. **requirements.lock 失同步**：lock 早于 Jinja2 模板重构（提交 25f37b4），缺 jinja2。
   按 lock 装出的环境：Web import 失败；即使补装 pytest，测试也在**收集阶段**因缺 jinja2 失败；
   补装 jinja2 后才恢复 50/50。
3. **`build==1.5.0` 不是脏依赖**：反向依赖链为 `langgraph-cs-agent → langchain-chroma → chromadb → build`，
   属 chromadb 声明的传递运行依赖，**不得删除**。
4. **可编辑安装的准确口径**：完整按 README 先装 requirements.txt 再 `pip install -e .` 时，
   源码路径绕过 package-data 缺口，可用；但干净环境**单独** `pip install -e .` 仍因 pyproject
   缺 jinja2 失败。
5. **前端 Node 测试**：`web/tests/test_markdown.mjs`（9 用例）+ `web/tests/test_theme.mjs`（3 用例）
   共 12 个，当前全部通过，但未进入任何统一门禁。
6. **门禁工程事实（评审实测）**：缺资源 wheel 上执行 `unzip -l` 退出码仍为 0（故清单式检查不构成
   机器门禁，必须逐项断言）；普通 `uv venv` 创建的环境默认**不带 pip**（`No module named pip`）；
   从 `/tmp` 直接执行 Node 测试会报 `Cannot find module`（必须先 `cd` 到仓库副本根）；
   lock 末行有换行符，`git diff --numstat` 不会因此多出第 3 行差异。

## 1. 硬性约束（违反任一条 = 整体返工）

1. **只改 §2 允许清单内的文件**。零逻辑代码变更：不碰任何 `.py` / `.js` / `.css` / `.html` 内容。
2. **构建与安装类门禁（G1–G5）在系统临时目录的干净快照中进行**，快照用 `git archive HEAD` 生成
   （天然不含 gitignored 与未跟踪文件——`.env`、`.venv`、`*.sqlite`、Chroma 向量库等密钥与
   运行产物一律不得进入临时目录）；**Git 上下文门禁（G6–G7）在真实阶段分支仓库中执行**。
   仓库内不得产生 `build/`、`dist/`、`*.egg-info`。
3. 在分支 `fix/packaging-and-test-deps` 上做，不直接改 main，不重写历史，每步一个 commit。
4. **lock 最小漂移策略（默认）**：保留现有全部版本行，只新增 jinja2 与 MarkupSafe 两行；
   禁止无约束重新解析、禁止顺带升级任何既有依赖。若最小漂移不可行（见 §3.2 失败分支），
   停下来报告，完整升级须单独列出 diff 并经评审，不得静默发生。
5. `langgraph_cs/requirements.txt` **明确不改**（已含 jinja2；pytest 不进它——
   "requirements.txt 与 pyproject 两处同步"原则自本阶段起限定为**运行时直接依赖**，
   测试依赖只放 optional extra）。
6. **逐 commit 轻量回归**（不假设任何既有环境，仓库内不得创建 venv）：
   - **commit 1**（纯文档，test extra 尚不存在）：自包含基线环境，全部在临时目录现场创建——
     ```bash
     BASE_WORK=$(mktemp -d) && mkdir -p "$BASE_WORK/repo"
     git archive HEAD | tar -x -C "$BASE_WORK/repo"
     uv venv --python "$PYTHON_VERSION" "$BASE_WORK/venv"
     uv pip install -p "$BASE_WORK/venv/bin/python" \
       -r "$BASE_WORK/repo/langgraph_cs/requirements.txt" pytest
     cd "$BASE_WORK/repo"
     "$BASE_WORK/venv/bin/python" -m pytest langgraph_cs -q
     ```
     预期 `50 passed`。
   - **commit 2 起**（test extra 已存在）：每个 commit 后跑 G3，且**每次都重新走完整快照
     生命周期**——新建 `mktemp -d` 临时目录、`git archive HEAD` 提取**当前 commit**、
     创建全新 venv，再执行 G3；不得复用先前的快照目录或 venv，防止"commit 已变化、
     测试的仍是旧快照"。
   - **阶段收尾**：另建全新 `$WORK` 跑完整 G1–G5（§5 前置与快照制备重新执行），
     G6–G7 在真实阶段分支仓库执行。
7. **失败纪律**：门禁跑不过、发现规范想错了、最小漂移出现预期外的 freeze diff，
   停下来报告，不要绕过或猜。
8. **工具链前置**：uv 为强制前置（§5 前置检查 `command -v uv && uv --version`，缺 uv 即整体 FAIL，
   不提供隐含的 pip 等价路径）；凡涉及 pip 的步骤一律在 `uv venv --seed` 出的环境里用
   `python -m pip`，不混用未安装的裸 pip。所有 venv 创建命令显式传入 `--python "$PYTHON_VERSION"`
   （取值见 §7 决策 3）。

## 2. 允许修改文件清单（allowlist，G7 依据）

| # | 文件 | 变更内容 |
|---|---|---|
| 1 | `docs/packaging-fix-spec.md` | 本文件，随首个 commit 入库 |
| 2 | `pyproject.toml` | package-data 加法 + jinja2 依赖 + test extra（§3.1） |
| 3 | `requirements.lock` | 最小漂移补 jinja2 / MarkupSafe（§3.2） |
| 4 | `README.md`（根） | 安装路径三分法与测试说明（§3.3） |
| 5 | `docs/rename-to-baton-spec.md` | 全文同步，非仅 §2（§3.4） |
| 6 | `CLAUDE.md` | 仅阶段指针行；与 AGENTS.md 必须同一 commit（§3.5，选项 A 时才改） |
| 7 | `AGENTS.md` | 同上 |

## 3. 修改内容

### 3.1 pyproject.toml

- package-data **加法**（保留现有 `"static/*"`，防止 `app.js` / `style.css` 回归丢失）：
  ```toml
  "langgraph_cs.web" = ["static/*", "static/js/*", "templates/*"]
  ```
- dependencies 补 `"jinja2>=3.1.0"`（与 requirements.txt 同一下限）。
- 新增：
  ```toml
  [project.optional-dependencies]
  test = ["pytest>=8"]
  ```
  pytest 只放 test extra，不进 dependencies、不进 requirements.txt、不进 lock。
- 依赖注释中"镜像 requirements.txt"的表述改为"运行时直接依赖两处同步；测试依赖只在 test extra"。

### 3.2 requirements.lock（最小漂移程序）

本程序**完全自包含**，不依赖 §5 的 `$WORK`（执行时点在 commit 3，门禁快照尚未制备）；
自己定义独立变量，路径全部显式：

```bash
FREEZE_WORK=$(mktemp -d)
REPO_ROOT=$(git rev-parse --show-toplevel)
# 带 pip 的全新 venv（普通 uv venv 默认无 pip，评审实测 No module named pip，必须 --seed）
uv venv --seed --python "$PYTHON_VERSION" "$FREEZE_WORK/venv"
"$FREEZE_WORK/venv/bin/python" -m pip install \
  -r "$REPO_ROOT/requirements.lock"
"$FREEZE_WORK/venv/bin/python" -m pip install "jinja2>=3.1.0"
"$FREEZE_WORK/venv/bin/python" -m pip freeze \
  > "$FREEZE_WORK/freeze.txt"
DIFF_RC=0
diff "$REPO_ROOT/requirements.lock" "$FREEZE_WORK/freeze.txt" || DIFF_RC=$?
```

diff 比较对象**明确固定**为 `$REPO_ROOT/requirements.lock` 与 `$FREEZE_WORK/freeze.txt`：
**预期仅显示新增 `Jinja2==X`、`MarkupSafe==Y` 两行**（`>` 侧），无删除、无版本变化。
**退出码语义（diff 检出差异时返回 1，属预期，故用 `|| DIFF_RC=$?` 捕获，兼容 `set -e` 环境）**：
`DIFF_RC=1` = 检出差异（预期路径，接着人工核对差异内容是否恰为两行新增）；
`DIFF_RC=0` = 无差异（**异常**：说明 jinja2 安装未产生新增行，停下核查）；
`DIFF_RC>=2` = diff 自身出错（如文件缺失，停止）。
参考实测：Python 3.14.4 下为 `Jinja2==3.1.6`、`MarkupSafe==3.0.3`，且无大小写/连字符
规范化漂移；最终数值以 `$PYTHON_VERSION` 下实测为准。
确认后将这两行按字母序插入 `$REPO_ROOT/requirements.lock`；若 diff 出现任何其他
新增/删除/版本变化 → 触发 §1.7 失败纪律，**立即停止**并报告。

不加 pytest；不删 `build==1.5.0`（事实基线 3）。

### 3.3 README.md（根）

安装说明改为三分法：

| 用途 | 命令 |
|---|---|
| 开发 / 测试 | `pip install -e ".[test]"` |
| 普通运行安装 | `pip install .` |
| 精确运行时约束安装 | `pip install -c requirements.lock .` |

- 精确安装路径的最终写法随 §7 决策 2 定：采用 `-c` 约束式（推荐），或保留
  `pip install -r requirements.lock` 并**必须注明它只安装依赖快照、不安装本项目**。
  G4 验证的就是 README 最终推荐的那条路径（见 §5 G4 的甲/乙变体），不允许"写一条、测另一条"。
- lock 的适用范围措辞**固定**（非决策项）：
  > requirements.lock 仅承诺：指定的 `$PYTHON_VERSION` + 生成时 OS/架构组合。
  README 必须同步写出该限定。现 lock 为 pip freeze 产物，无平台元数据、无 hashes；
  若未来要宣称跨平台可复现，必须另开阶段并增加多平台安装门禁，不能靠措辞修改完成。
- 测试跑法说明同步：pytest 由 `[test]` extra 提供，不再假设环境自带。
- 不改 README 其他内容（评测数据段的重构属于后续阶段，非本次目标）。

### 3.4 docs/rename-to-baton-spec.md（全文同步，非仅 §2）

- §2"打包三连修"改为：**此前已于 packaging-fix 阶段完成，rename 阶段只保留并回归验证**
  （wheel 门禁保留，作为改名后的回归断言）；
- 文首"流水线位置"、§3 执行步骤、§6 验收门禁中与三连修相关的表述同步更新；
- 原文"6 个 js 文件"更正为 **7 个**；
- 其余内容（命名总表、venv 重建等）不动。

### 3.5 阶段指针（CLAUDE.md + AGENTS.md，二选一，所有者拍板）

两个文件各有一行"当前阶段规范：docs/comment-cleanup-spec.md"，**改则必须同一 commit 内两处同步**：

- **选项 A（默认建议）**：分支首个 commit 把两处指针改为本 spec；阶段收尾 commit 把两处指针
  切回 `docs/comment-cleanup-spec.md`（本阶段完成后下一阶段即注释清洗）。
- **选项 B**：本阶段短平快，两个指针全程不动，始终指向 comment-cleanup-spec。

## 4. 执行顺序（每步一个 commit）

1. 自 main 建分支 `fix/packaging-and-test-deps`。
2. commit 1：`docs(packaging): 新增打包与测试依赖修复规范` —— 本 spec 入库
   （选项 A 时同 commit 更新 CLAUDE.md + AGENTS.md 指针）。轻量回归：§1.6 自包含基线环境
   （临时目录现场创建），pytest 50/50。
3. commit 2：`fix(packaging): pyproject 补 templates/static-js package-data、jinja2 依赖与 test extra`。
   本 commit 起轻量回归改用 G3，且每次新建临时目录 + `git archive HEAD`（当前 commit）+ 新 venv（§1.6）。
4. commit 3：`fix(packaging): requirements.lock 最小漂移补 Jinja2/MarkupSafe`。
5. commit 4：`docs: README 安装路径三分法与测试依赖说明`。
6. commit 5：`docs: rename-to-baton-spec 同步（三连修已提前完成、7 个 js）`。
7. commit 6（仅选项 A）：`chore: 阶段指针切回 comment-cleanup-spec`。
8. 跑 §5 全量门禁 → `git push` + `gh pr create --draft`，PR 描述附门禁**原始输出**与变更摘要表。

## 5. 验收门禁

**网络口径（如实声明，不夸大）**：测试与冒烟不调用业务 API / LLM；依赖安装可能访问包索引。
本阶段不声称"完全离线"；如需完全离线复现，须另建 wheelhouse 并强制 `--offline`，属非目标。

**执行上下文划分**：G1–G5 在临时快照 `$WORK` 中执行；G6–G7 在真实阶段分支仓库中执行
（快照由 `git archive` 生成、不含 `.git`，在快照里跑 `git diff` 必然失败，二者不可混）。

**快照生命周期**：快照与 venv 均为一次性——逐 commit 轻量回归每次重新执行"前置与快照制备"
（新 `mktemp -d` + `git archive HEAD` 提取当前 commit + 新 venv，见 §1.6）；阶段收尾的
全量门禁同样另建全新 `$WORK`，不复用任何先前的快照目录或 venv。

**前置与快照制备**：

```bash
command -v uv && uv --version          # 工具链前置：缺 uv 即整体 FAIL（§1.8）
PYTHON_VERSION=<§7 决策 3 的取值>
WORK=$(mktemp -d) && mkdir -p "$WORK/repo"
git archive HEAD | tar -x -C "$WORK/repo"   # 干净快照：无 gitignored/未跟踪文件，无密钥与运行产物
```

### G1 wheel 构建与逐项断言（任一缺失退出非零）

评审实测：缺资源 wheel 上 `unzip -l` 退出码仍为 0，故本门禁**不用清单目视**，用 `zipfile` 逐项断言：

```bash
cd "$WORK/repo" && uv build --wheel --out-dir "$WORK/dist"
python3 - "$WORK"/dist/*.whl <<'EOF'
import sys, zipfile, fnmatch
whl = sys.argv[1]
zf = zipfile.ZipFile(whl)
names = zf.namelist()
required = [
    "langgraph_cs/web/templates/index.html",
    "langgraph_cs/web/static/app.js",
    "langgraph_cs/web/static/style.css",
] + [f"langgraph_cs/web/static/js/{m}.js"
     for m in ("dom", "messages", "pipeline", "pure", "session", "sse", "theme")]
missing = [p for p in required if p not in names]
def count(pat): return len(fnmatch.filter(names, pat))
if count("langgraph_cs/data/faq/*.md") < 5:  missing.append("data/faq/*.md >= 5")
if count("langgraph_cs/eval/*.json") < 4:    missing.append("eval/*.json >= 4")
if count("langgraph_cs/.env.example") != 1:  missing.append(".env.example")
meta = next(n for n in names if n.endswith("dist-info/METADATA"))
if not any(l.lower().startswith("requires-dist: jinja2")
           for l in zf.read(meta).decode().splitlines()):
    missing.append("METADATA Requires-Dist: jinja2")
if missing:
    print("G1 FAIL:", missing)
    sys.exit(1)
print("G1 OK: 10 files + 3 data globs + metadata jinja2")
EOF
```

预期输出：`G1 OK: 10 files + 3 data globs + metadata jinja2`，退出码 0；任一缺失打印
`G1 FAIL: [...]` 并退出 1。

### G2 wheel 安装冒烟（干净 venv + 中性 cwd + TestClient）

```bash
uv venv --python "$PYTHON_VERSION" "$WORK/venv-whl"
uv pip install -p "$WORK/venv-whl/bin/python" "$WORK"/dist/*.whl
cd /tmp && "$WORK/venv-whl/bin/python" - <<'EOF'
import langgraph_cs
assert "site-packages" in langgraph_cs.__file__, langgraph_cs.__file__  # 防源码目录遮蔽
from fastapi.testclient import TestClient
from langgraph_cs.web.server import build_app
client = TestClient(build_app())
urls = ["/", "/static/app.js", "/static/style.css"] + [
    f"/static/js/{m}.js" for m in
    ("dom", "messages", "pipeline", "pure", "session", "sse", "theme")]
for u in urls:
    r = client.get(u)
    assert r.status_code == 200, (u, r.status_code)
print("SMOKE OK", len(urls), "urls")
EOF
```

预期输出：`SMOKE OK 10 urls`。无需监听端口、无需 API key（graph 懒加载，静态页与 `GET /`
不触发 build_graph）。该脚本已由评审方在源码环境实跑预演，得到 `SMOKE OK 10 urls`。

### G3 干净环境单独可编辑安装 + 测试链路（同时回归事实基线 4）

```bash
uv venv --python "$PYTHON_VERSION" "$WORK/venv-dev" && cd "$WORK/repo"
uv pip install -p "$WORK/venv-dev/bin/python" -e ".[test]"
"$WORK/venv-dev/bin/python" -m pytest langgraph_cs -q
```

预期：安装成功（jinja2 由 pyproject 解析进来，不预装 requirements.txt）；`50 passed`。

### G4 lock 安装路径验证（与 §7 决策 2 绑定，验证 README 最终推荐的那条路径）

**变体甲**（README 采用 `-c` 约束式安装，推荐）：

```bash
uv venv --seed --python "$PYTHON_VERSION" "$WORK/venv-lock" && cd "$WORK/repo"
"$WORK/venv-lock/bin/python" -m pip install -c requirements.lock .
cd /tmp && "$WORK/venv-lock/bin/python" - <<'EOF'
import langgraph_cs
assert "site-packages" in langgraph_cs.__file__, langgraph_cs.__file__  # 中性 cwd，防源码遮蔽
import langgraph_cs.web.server
print("LOCK IMPORT OK")
EOF
```

**变体乙**（README 保留 `-r` 快照式）：`-r` 只安装依赖快照、**不安装本项目**，故 import
必须在快照根目录以源码提供包，且门禁结论仅限"依赖快照完备"：

```bash
uv venv --seed --python "$PYTHON_VERSION" "$WORK/venv-lock" && cd "$WORK/repo"
"$WORK/venv-lock/bin/python" -m pip install -r requirements.lock
"$WORK/venv-lock/bin/python" -c "import langgraph_cs.web.server; print('LOCK IMPORT OK')"
```

预期输出（两变体同）：`LOCK IMPORT OK`（修复前该 import 因缺 jinja2 失败）。

### G5 Node 前端用例（必须先 cd 到快照根，评审实测自 /tmp 直接执行报 Cannot find module）

```bash
cd "$WORK/repo"
node langgraph_cs/web/tests/test_markdown.mjs   # 预期：9 个用例全过
node langgraph_cs/web/tests/test_theme.mjs      # 预期：3 个用例全过
```

### G6 lock 最小漂移断言（真实阶段分支仓库中执行）

```bash
git diff --numstat main...HEAD -- requirements.lock
```

预期：`2  0  requirements.lock`（仅新增 2 行、零删除；评审已确认现 lock 末行有换行符，
不会出现第 3 行差异）。

### G7 diff 门禁（真实阶段分支仓库中执行；allowlist + 格式 + 人工语义复核）

```bash
git diff --name-only main...HEAD | sort        # 输出必须 ⊆ §2 允许清单
git diff --check main...HEAD                   # 无空白错误
```

外加**人工语义复核**：逐文件确认无逻辑代码变更、无 §6 非目标内容混入。
本条门禁**不全是机器可判定**，人工环节的结论写入 PR 描述。

## 6. 非目标（明确不做，别顺手做）

- 不做 CI / GitHub Actions（后续独立阶段）；
- 不执行注释清洗（comment-cleanup-spec 的活）；
- 不执行 Baton 改名或企业场景迁移；
- 不把 Node 用例接进 pytest 或统一 runner（本阶段只把它们写进门禁命令清单）；
- 不升级除 jinja2 / MarkupSafe 之外的任何依赖；
- 不建 wheelhouse / 完全离线安装体系；不给 lock 补 hashes / 平台元数据（跨平台锁定属后续课题）；
- 不改任何 `.py` / `.js` / `.css` / `.html` / 评测数据 / FAQ 语料。

## 7. 留给仓库所有者的决策点（执行前拍板）

1. **阶段指针方案**：§3.5 选项 A（切换并收尾切回，默认建议）还是选项 B（全程不动）。
2. **README lock 安装路径（绑定 G4 变体）**：甲——改为 `pip install -c requirements.lock .`
   约束式（推荐，G4 走变体甲）；乙——保留 `pip install -r requirements.lock` 并加
   "只装依赖快照、不装本项目"限定（G4 走变体乙）。不允许 README 写一条、G4 测另一条。
3. **`$PYTHON_VERSION` 取值**：仓库仅约束 `requires-python = ">=3.10"`，无"项目现用版本"的
   仓库内证据；由所有者指定一个具体小版本，lock 重建（§3.2）与 G2/G3/G4 的 venv 创建统一
   显式传入，并写入 PR 描述。参考：评审环境 Python 3.14.4 下最小漂移实测为
   `Jinja2==3.1.6` + `MarkupSafe==3.0.3`。
4. **分支名确认**：`fix/packaging-and-test-deps`。

（原"lock 平台承诺口径"决策项已取消：本阶段唯一允许的口径固定为 §3.3 的限定表述——
lock 仅承诺指定 `$PYTHON_VERSION` + 生成时 OS/架构组合；跨平台宣称须另开阶段并增加
多平台安装门禁，不属所有者措辞选择范围。）
