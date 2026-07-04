"""
mock 业务库的连接与查询层。

SQLite 文件默认落在 langgraph_cs/data/business.sqlite。这个文件是运行时产物，
可由 seed_business_db 脚本随时重建，不需要入库。
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "business.sqlite"

_CONNS: dict[Path, sqlite3.Connection] = {}
_TICKET_ID_RE = re.compile(r"^TKT-\d{8}-(\d+)$")


def _normalize_path(db_path=None) -> Path:
    return (Path(db_path) if db_path is not None else DEFAULT_DB_PATH).resolve()


def _row_to_dict(row) -> dict | None:
    if row is None:
        return None
    return dict(row)


def _close_conn(db_path=None) -> None:
    """关闭并移除缓存连接。seed 脚本重建库文件前使用。"""
    path = _normalize_path(db_path)
    conn = _CONNS.pop(path, None)
    if conn is not None:
        conn.close()


def get_conn(db_path=None) -> sqlite3.Connection:
    """
    返回 SQLite 长驻连接；默认使用 langgraph_cs/data/business.sqlite。

    db_path 仅用于测试注入临时库。连接按路径缓存，避免每次查询重复打开。
    """
    path = _normalize_path(db_path)
    if path not in _CONNS:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        _CONNS[path] = conn
    return _CONNS[path]


def init_schema(conn: sqlite3.Connection) -> None:
    """幂等创建订单、账单、工单三张业务表。"""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS orders(
          order_id     TEXT PRIMARY KEY,
          user_id      TEXT NOT NULL,
          item         TEXT NOT NULL,
          amount_yuan  REAL NOT NULL,
          status       TEXT NOT NULL,
          logistics_no TEXT,
          created_at   TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS bills(
          bill_id        TEXT PRIMARY KEY,
          user_id        TEXT NOT NULL,
          order_id       TEXT,
          amount_yuan    REAL NOT NULL,
          bill_type      TEXT NOT NULL,
          invoice_status TEXT NOT NULL,
          created_at     TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tickets(
          ticket_id   TEXT PRIMARY KEY,
          user_id     TEXT NOT NULL,
          ticket_type TEXT NOT NULL,
          status      TEXT NOT NULL,
          detail      TEXT NOT NULL,
          created_at  TEXT NOT NULL
        );
        """
    )
    conn.commit()


def get_order(order_id, db_path=None) -> dict | None:
    conn = get_conn(db_path)
    row = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
    return _row_to_dict(row)


def list_orders(user_id, db_path=None) -> list[dict]:
    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC, order_id DESC",
        (user_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_bill(bill_id, db_path=None) -> dict | None:
    conn = get_conn(db_path)
    row = conn.execute("SELECT * FROM bills WHERE bill_id = ?", (bill_id,)).fetchone()
    return _row_to_dict(row)


def list_bills(user_id, db_path=None) -> list[dict]:
    conn = get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM bills WHERE user_id = ? ORDER BY created_at DESC, bill_id DESC",
        (user_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_refund_status(order_id, db_path=None) -> dict | None:
    """
    汇总订单的退款账单与 refund 工单状态。

    订单不存在时返回 None；订单存在但没有退款记录时返回 has_refund=False。
    """
    order = get_order(order_id, db_path=db_path)
    if order is None:
        return None

    conn = get_conn(db_path)
    refund_bill = conn.execute(
        """
        SELECT * FROM bills
        WHERE order_id = ? AND bill_type = '退款'
        ORDER BY created_at DESC, bill_id DESC
        LIMIT 1
        """,
        (order_id,),
    ).fetchone()
    refund_ticket = conn.execute(
        """
        SELECT * FROM tickets
        WHERE user_id = ? AND ticket_type = 'refund' AND detail LIKE ?
        ORDER BY created_at DESC, ticket_id DESC
        LIMIT 1
        """,
        (order["user_id"], f"%{order_id}%"),
    ).fetchone()

    bill = _row_to_dict(refund_bill)
    ticket = _row_to_dict(refund_ticket)
    return {
        "order_id": order["order_id"],
        "user_id": order["user_id"],
        "order_status": order["status"],
        "has_refund": bool(bill or ticket),
        "refund_bill_id": bill["bill_id"] if bill else None,
        "refund_amount_yuan": bill["amount_yuan"] if bill else None,
        "refund_invoice_status": bill["invoice_status"] if bill else None,
        "refund_created_at": bill["created_at"] if bill else None,
        "ticket_id": ticket["ticket_id"] if ticket else None,
        "ticket_status": ticket["status"] if ticket else None,
        "ticket_detail": ticket["detail"] if ticket else None,
    }


def _next_ticket_id(conn: sqlite3.Connection) -> str:
    rows = conn.execute("SELECT ticket_id FROM tickets").fetchall()
    max_seq = 0
    for row in rows:
        match = _TICKET_ID_RE.match(row["ticket_id"])
        if match:
            max_seq = max(max_seq, int(match.group(1)))
    today = datetime.now().strftime("%Y%m%d")
    return f"TKT-{today}-{max_seq + 1:03d}"


def create_ticket(user_id, ticket_type, detail, db_path=None) -> dict:
    conn = get_conn(db_path)
    now = datetime.now().isoformat(timespec="seconds")
    ticket = {
        "ticket_id": _next_ticket_id(conn),
        "user_id": user_id,
        "ticket_type": ticket_type,
        "status": "待审批",
        "detail": detail,
        "created_at": now,
    }
    conn.execute(
        """
        INSERT INTO tickets(ticket_id, user_id, ticket_type, status, detail, created_at)
        VALUES(:ticket_id, :user_id, :ticket_type, :status, :detail, :created_at)
        """,
        ticket,
    )
    conn.commit()
    return ticket


def get_ticket(ticket_id, db_path=None) -> dict | None:
    conn = get_conn(db_path)
    row = conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
    return _row_to_dict(row)
