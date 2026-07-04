"""
mock 业务库的**离线**单测（只使用临时 SQLite，不触碰真实 business.sqlite）。

运行：
    langgraph_cs/.venv/bin/python -m langgraph_cs.business.tests.test_business_offline
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from langgraph_cs.business import db as db_mod
from langgraph_cs.scripts import seed_business_db


def _count(db_path, table):
    conn = db_mod.get_conn(db_path)
    return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]


def _real_db_snapshot():
    path = db_mod.DEFAULT_DB_PATH
    if not path.exists():
        return None
    stat = path.stat()
    return (stat.st_size, stat.st_mtime_ns)


def _seed_temp_db():
    tmp = tempfile.TemporaryDirectory()
    db_path = Path(tmp.name) / "business.sqlite"
    counts = seed_business_db.seed_database(db_path)
    return tmp, db_path, counts


def test_seed_counts_and_real_db_untouched():
    """seed 到临时库后，各表行数符合预期，且真实库路径未被触碰。"""
    before = _real_db_snapshot()
    tmp, db_path, counts = _seed_temp_db()
    try:
        assert db_path != db_mod.DEFAULT_DB_PATH, db_path
        assert counts == {"orders": 30, "bills": 40, "tickets": 10}, counts
        assert _count(db_path, "orders") == 30
        assert _count(db_path, "bills") == 40
        assert _count(db_path, "tickets") == 10
    finally:
        tmp.cleanup()
    after = _real_db_snapshot()
    assert after == before, (before, after)
    print("✓ seed 临时库：orders=30 / bills=40 / tickets=10，未触碰真实 business.sqlite")


def test_get_order_hit_and_miss():
    """get_order 命中返回 dict，未命中返回 None。"""
    tmp, db_path, _ = _seed_temp_db()
    try:
        order = db_mod.get_order("ORD-20260506-003", db_path=db_path)
        missing = db_mod.get_order("ORD-NOT-FOUND", db_path=db_path)
    finally:
        tmp.cleanup()

    assert order is not None and order["user_id"] == "user_003", order
    assert order["status"] == "退货中", order
    assert missing is None, missing
    print("✓ get_order：命中返回订单 dict，未命中返回 None")


def test_get_refund_status_for_returning_order():
    """退货中订单能汇总退款账单与 refund 工单信息。"""
    tmp, db_path, _ = _seed_temp_db()
    try:
        status = db_mod.get_refund_status("ORD-20260506-003", db_path=db_path)
    finally:
        tmp.cleanup()

    assert status is not None, status
    assert status["has_refund"] is True, status
    assert status["refund_amount_yuan"] < 0, status
    assert status["ticket_status"] == "待审批", status
    assert status["ticket_id"] == "TKT-20260508-001", status
    print("✓ get_refund_status：退货中订单返回退款账单金额与 refund 工单状态")


def test_create_ticket_is_unique_and_readable():
    """create_ticket 生成不冲突 ticket_id，初始待审批，并可被 get_ticket 读回。"""
    tmp, db_path, _ = _seed_temp_db()
    try:
        conn = db_mod.get_conn(db_path)
        existing_ids = {row["ticket_id"] for row in conn.execute("SELECT ticket_id FROM tickets").fetchall()}
        ticket = db_mod.create_ticket("user_004", "tech", "用户反馈路由器联网失败，需要人工排查。", db_path=db_path)
        loaded = db_mod.get_ticket(ticket["ticket_id"], db_path=db_path)
    finally:
        tmp.cleanup()

    assert ticket["ticket_id"] not in existing_ids, ticket
    assert ticket["status"] == "待审批", ticket
    assert loaded == ticket, (ticket, loaded)
    print("✓ create_ticket：新 ticket_id 不冲突，status=待审批，get_ticket 可读回")


def _run_all():
    tests = [
        test_seed_counts_and_real_db_untouched,
        test_get_order_hit_and_miss,
        test_get_refund_status_for_returning_order,
        test_create_ticket_is_unique_and_readable,
    ]
    for test in tests:
        test()
    print("\n全部 business 离线用例通过 ✅（临时 SQLite，未触碰真实业务库）")


if __name__ == "__main__":
    _run_all()
