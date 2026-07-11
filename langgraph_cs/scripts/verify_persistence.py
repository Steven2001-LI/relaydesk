"""
verify_persistence —— **离线**证明 SqliteSaver 跨进程持久化（不依赖任何 LLM / 网络）。

要证明的命题：用 SqliteSaver 写入一段含 messages 的检查点后，**换一个全新的解释器进程**、
用同一个 sqlite 文件 + 同一个 thread_id 调 get_state(config)，仍能读回那段 messages。
这正是「MemorySaver → SqliteSaver，进程重启后同一 thread_id 仍记得上文」的核心证据。

怎么做到"跨进程"且"离线"：
  - 不跑真实客服图（那要调 LLM）。改用一张**极简单节点图**：节点只把传入的消息原样收下，
    靠 add_messages reducer 累积进 state["messages"]。invoke 一次就把消息写进 SQLite 检查点。
  - 进程 A（write 阶段）：建 SqliteSaver(同一 db 文件) → 编译极简图 → invoke 写入一句话。
  - 进程 B（read 阶段，**真的另起一个 python 子进程**）：重新打开同一个 db 文件 + 同一 thread_id，
    只调 graph.get_state(config) 读回，断言那句话仍在。
  - 用 subprocess 起子进程（python -m ... --phase read），是为了证明"内存里什么都没留"，
    数据完全来自磁盘上的 SQLite 文件 —— 这比在同进程里反复 new 一个 saver 更有说服力。

跑法（从仓库根目录）：
    langgraph_cs/.venv/bin/python -m langgraph_cs.scripts.verify_persistence
退出码 0 = 跨进程持久化验证通过。
"""
import argparse
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph_cs.state import CSState

# 用一个固定 thread_id 贯穿两个进程：跨进程能读回的前提就是"同一 thread_id"。
THREAD_ID = "persist-verify-1"
# 这句话是我们要证明"重启后还在"的载荷（离线、与 LLM 无关）。
MARKER_TEXT = "我叫小明，记住我的名字"


def _echo_node(state: CSState) -> dict:
    """
    极简节点：什么都不做，只把当前 state["messages"] 原样"回执"。

    返回空 messages 列表即可——真正让消息进 state 的是 invoke 时传入的 messages
    经 add_messages reducer 累积。这里只需要图能跑通、触发一次检查点写入。
    返回 intent 占位，纯粹为了 state 字段完整、可读性好。
    """
    return {"intent": "persist-test"}


def _build_min_graph(checkpointer):
    """编译一张「START -> echo -> END」的极简图，挂上传入的 checkpointer。"""
    builder = StateGraph(CSState)
    builder.add_node("echo", _echo_node)
    builder.add_edge(START, "echo")
    builder.add_edge("echo", END)
    return builder.compile(checkpointer=checkpointer)


def _open_saver(db_path: Path) -> SqliteSaver:
    """
    打开一个长驻可用的 SqliteSaver（与 graph.make_sqlite_checkpointer 同款写法）。

    自建 sqlite3 连接（check_same_thread=False）后交给 SqliteSaver，setup() 建表；
    不在这里关闭连接——子进程随退出自然释放。
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver


def _phase_write(db_path: Path) -> None:
    """进程 A：把一句含 marker 的消息 invoke 进图，写入 SQLite 检查点。"""
    graph = _build_min_graph(_open_saver(db_path))
    config = {"configurable": {"thread_id": THREAD_ID}}
    graph.invoke({"messages": [HumanMessage(content=MARKER_TEXT)]}, config=config)
    print(f"[write] 已写入检查点：thread_id={THREAD_ID}，db={db_path}")


def _phase_read(db_path: Path) -> int:
    """
    进程 B（全新解释器）：只读回检查点，断言 marker 消息仍在。

    成功返回 0，失败返回非 0（供子进程退出码用）。
    """
    graph = _build_min_graph(_open_saver(db_path))
    config = {"configurable": {"thread_id": THREAD_ID}}

    snapshot = graph.get_state(config)
    messages = snapshot.values.get("messages", []) if snapshot and snapshot.values else []
    contents = [getattr(m, "content", "") for m in messages]

    print(f"[read] 新进程从 SQLite 读回 messages：{contents}")
    if any(MARKER_TEXT in c for c in contents):
        print("[read] 断言通过：marker 消息仍在 —— 跨进程持久化成立。")
        return 0
    print("[read] 断言失败：没读回 marker 消息（检查 db 路径 / thread_id 是否一致）。", file=sys.stderr)
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="离线验证 SqliteSaver 跨进程持久化。")
    # --phase 内部用：write 在本进程写，read 在子进程读。用户直接跑（不带 --phase）则跑完整两阶段。
    parser.add_argument("--phase", choices=["write", "read"], default=None,
                        help="内部用：分阶段在不同进程里执行。用户一般不带此参数（跑完整流程）。")
    parser.add_argument("--db", default=None,
                        help="检查点 sqlite 文件路径。默认用临时文件（验证完即弃，不污染 data/）。")
    args = parser.parse_args()

    # 默认用一个临时 db 文件：验证的是"跨进程"而非"长期保留"，临时文件最干净、可重复跑。
    db_path = Path(args.db) if args.db else Path(tempfile.gettempdir()) / "cs_persist_verify.sqlite"

    # 分阶段子调用：被父进程用 subprocess 拉起时只做单一阶段。
    if args.phase == "write":
        _phase_write(db_path)
        return
    if args.phase == "read":
        sys.exit(_phase_read(db_path))

    # ---- 完整流程：本进程写，再起一个全新子进程读，证明数据来自磁盘而非内存 ----
    print("=== SqliteSaver 跨进程持久化验证（离线，不调 LLM）===")
    # 每次跑先清掉旧的临时 db，保证"读回的是本次写入的"而非历史残留。
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()

    # 进程 A：写。直接在本进程做（拿到一个干净的写入起点）。
    _phase_write(db_path)

    # 进程 B：起一个**全新的 python 解释器**来读。-m 模块方式保证 import 路径正确。
    print("[verify] 另起一个全新 python 子进程读回（证明状态来自 SQLite 文件，而非本进程内存）……")
    proc = subprocess.run(
        [sys.executable, "-m", "langgraph_cs.scripts.verify_persistence",
         "--phase", "read", "--db", str(db_path)],
        cwd=str(Path(__file__).resolve().parents[2]),  # 仓库根，保证 langgraph_cs 包可被 import
    )

    if proc.returncode == 0:
        print("\n✅ 跨进程持久化验证通过：新进程用同一 sqlite + thread_id 读回了上一进程写入的 messages。")
    else:
        print("\n❌ 跨进程持久化验证失败（见上方 [read] 输出）。", file=sys.stderr)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
