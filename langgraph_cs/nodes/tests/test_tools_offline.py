"""
tools.py 的**离线**单测（不联网、不访问真实业务库）。

策略：monkeypatch tools_mod.business_db 上的函数，让每个工具覆盖命中、未命中和异常兜底。

运行：
    langgraph_cs/.venv/bin/python -m langgraph_cs.nodes.tests.test_tools_offline
"""
import json

from langgraph_cs.nodes import tools as tools_mod


def _call(tool_obj, args=None):
    return json.loads(tool_obj.invoke(args or {}))


def _patch(obj, name, value):
    orig = getattr(obj, name)
    setattr(obj, name, value)

    def restore():
        setattr(obj, name, orig)

    return restore


def _boom(*args, **kwargs):
    raise RuntimeError("模拟业务库异常")


def test_query_bill_hit_miss_error():
    restore = _patch(
        tools_mod.business_db,
        "get_bill",
        lambda bill_id: {"bill_id": bill_id, "invoice_status": "已开票"} if bill_id == "BILL-1" else None,
    )
    try:
        hit = _call(tools_mod.query_bill, {"bill_id": "BILL-1"})
        miss = _call(tools_mod.query_bill, {"bill_id": "BILL-X"})
    finally:
        restore()

    restore = _patch(tools_mod.business_db, "get_bill", _boom)
    try:
        err = _call(tools_mod.query_bill, {"bill_id": "BILL-ERR"})
    finally:
        restore()

    assert hit["found"] is True and hit["bill"]["bill_id"] == "BILL-1", hit
    assert miss["found"] is False and miss["bill_id"] == "BILL-X", miss
    assert "error" in err, err
    print("✓ query_bill：命中 / 未命中 found=false / 异常 error JSON")


def test_query_bill_by_user_id():
    restore = _patch(
        tools_mod.business_db,
        "list_bills",
        lambda user_id: [{"bill_id": "BILL-2", "user_id": user_id}] if user_id == "user_001" else [],
    )
    try:
        hit = _call(tools_mod.query_bill, {"user_id": "user_001"})
        miss = _call(tools_mod.query_bill, {"user_id": "user_999"})
    finally:
        restore()

    assert hit["found"] is True and hit["bills"][0]["bill_id"] == "BILL-2", hit
    assert miss["found"] is False and miss["user_id"] == "user_999", miss
    print("✓ query_bill(user_id)：可列用户账单，空列表返回 found=false")


def test_refund_status_hit_miss_error():
    def fake_status(order_id):
        if order_id == "ORD-1":
            return {"order_id": order_id, "has_refund": True, "ticket_status": "处理中"}
        return None

    restore = _patch(tools_mod.business_db, "get_refund_status", fake_status)
    try:
        hit = _call(tools_mod.refund_status, {"order_id": "ORD-1"})
        miss = _call(tools_mod.refund_status, {"order_id": "ORD-X"})
    finally:
        restore()

    restore = _patch(tools_mod.business_db, "get_refund_status", _boom)
    try:
        err = _call(tools_mod.refund_status, {"order_id": "ORD-ERR"})
    finally:
        restore()

    assert hit["found"] is True and hit["refund_status"]["ticket_status"] == "处理中", hit
    assert miss["found"] is False and miss["order_id"] == "ORD-X", miss
    assert "error" in err, err
    print("✓ refund_status：命中 / 未命中 found=false / 异常 error JSON")


def test_create_refund_ticket_hit_miss_error():
    restore = _patch(
        tools_mod.business_db,
        "create_ticket",
        lambda user_id, ticket_type, detail: {
            "ticket_id": "TKT-1",
            "user_id": user_id,
            "ticket_type": ticket_type,
            "status": "待审批",
            "detail": detail,
        },
    )
    try:
        hit = _call(
            tools_mod.create_refund_ticket,
            {"user_id": "user_001", "order_id": "ORD-1", "reason": "商品损坏"},
        )
        miss = _call(tools_mod.create_refund_ticket, {"user_id": "user_001", "order_id": "ORD-1"})
    finally:
        restore()

    restore = _patch(tools_mod.business_db, "create_ticket", _boom)
    try:
        err = _call(
            tools_mod.create_refund_ticket,
            {"user_id": "user_001", "order_id": "ORD-1", "reason": "商品损坏"},
        )
    finally:
        restore()

    assert hit["found"] is True and hit["ticket"]["ticket_type"] == "refund", hit
    assert "ORD-1" in hit["ticket"]["detail"], hit
    assert miss["found"] is False and "reason" in miss["missing"], miss
    assert "error" in err, err
    print("✓ create_refund_ticket：创建 / 缺字段 found=false / 异常 error JSON")


def test_create_ticket_hit_miss_error():
    restore = _patch(
        tools_mod.business_db,
        "create_ticket",
        lambda user_id, ticket_type, detail: {
            "ticket_id": "TKT-2",
            "user_id": user_id,
            "ticket_type": ticket_type,
            "status": "待审批",
            "detail": detail,
        },
    )
    try:
        hit = _call(tools_mod.create_ticket, {"user_id": "user_002", "detail": "登录失败"})
        miss = _call(tools_mod.create_ticket, {"user_id": "user_002"})
    finally:
        restore()

    restore = _patch(tools_mod.business_db, "create_ticket", _boom)
    try:
        err = _call(tools_mod.create_ticket, {"user_id": "user_002", "detail": "登录失败"})
    finally:
        restore()

    assert hit["found"] is True and hit["ticket"]["ticket_type"] == "tech", hit
    assert miss["found"] is False and "detail" in miss["missing"], miss
    assert "error" in err, err
    print("✓ create_ticket：创建 / 缺字段 found=false / 异常 error JSON")


def test_check_service_status_hit_error():
    ok = _call(tools_mod.check_service_status)

    restore = _patch(tools_mod, "_service_status_payload", _boom)
    try:
        err = _call(tools_mod.check_service_status)
    finally:
        restore()

    assert ok["found"] is True and ok["services"], ok
    assert "error" in err, err
    print("✓ check_service_status：返回 mock 服务大盘 / 异常 error JSON")


def _run_all():
    tests = [
        test_query_bill_hit_miss_error,
        test_query_bill_by_user_id,
        test_refund_status_hit_miss_error,
        test_create_refund_ticket_hit_miss_error,
        test_create_ticket_hit_miss_error,
        test_check_service_status_hit_error,
    ]
    for test in tests:
        test()
    print("\n全部 tools 离线用例通过 ✅（mock business_db，不触碰真实业务库）")


if __name__ == "__main__":
    _run_all()
