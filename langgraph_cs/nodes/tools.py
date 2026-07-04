"""
Agent 可调用的业务工具。

这些工具是 LLM 能看到的"客服系统接口"：账单/退款/工单等用户个人数据必须走工具查询，
不要让模型凭空编造。工具内部统一返回 JSON 字符串，异常也转成 {"error": "..."}，
避免单个业务接口失败拖垮整张图。
"""
from __future__ import annotations

import json

from langchain_core.tools import tool
from langgraph.types import interrupt

from langgraph_cs.business import db as business_db


def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False)


def _error(ex: Exception) -> str:
    return _json({"error": str(ex)})


@tool
def query_bill(bill_id: str = "", user_id: str = "") -> str:
    """
    查询用户账单、扣费记录或发票状态时调用。

    已知具体 bill_id 时优先按 bill_id 查单笔账单；只有 user_id 时列出该用户近期账单。
    缺少 bill_id 和 user_id 时不要猜测，应先向用户询问必要标识。
    """
    try:
        bill_id = (bill_id or "").strip()
        user_id = (user_id or "").strip()
        if bill_id:
            bill = business_db.get_bill(bill_id)
            if bill is None:
                return _json({"found": False, "bill_id": bill_id, "reason": "未找到该账单"})
            return _json({"found": True, "query_type": "bill_id", "bill": bill})
        if user_id:
            bills = business_db.list_bills(user_id)
            if not bills:
                return _json({"found": False, "user_id": user_id, "reason": "该用户暂无账单"})
            return _json({"found": True, "query_type": "user_id", "user_id": user_id, "bills": bills})
        return _json({"found": False, "reason": "缺少 bill_id 或 user_id"})
    except Exception as ex:  # noqa: BLE001 工具边界兜底：业务库失败 -> JSON error，不抛出
        return _error(ex)


@tool
def refund_status(order_id: str = "") -> str:
    """
    用户询问某个订单的退款进度、退款金额、退款工单状态时调用。

    必须有 order_id；缺少订单号时先询问用户，不要根据商品名或上下文猜测订单。
    """
    try:
        order_id = (order_id or "").strip()
        if not order_id:
            return _json({"found": False, "reason": "缺少 order_id"})
        status = business_db.get_refund_status(order_id)
        if status is None:
            return _json({"found": False, "order_id": order_id, "reason": "未找到该订单"})
        if not status.get("has_refund"):
            return _json({"found": False, "order_id": order_id, "refund_status": status, "reason": "暂无退款记录"})
        return _json({"found": True, "refund_status": status})
    except Exception as ex:  # noqa: BLE001
        return _error(ex)


@tool
def create_refund_ticket(user_id: str = "", order_id: str = "", reason: str = "") -> str:
    """
    用户明确要求为某个订单申请退款、退货、退款跟进时调用。

    必须先拿到 user_id、order_id 和退款原因；参数齐备后先触发人工审批 interrupt，
    只有审批 resume 严格返回 {"approved": True, ...} 才真正创建 refund 工单。
    """
    user_id = (user_id or "").strip()
    order_id = (order_id or "").strip()
    reason = (reason or "").strip()
    missing = [name for name, value in {"user_id": user_id, "order_id": order_id, "reason": reason}.items() if not value]
    if missing:
        return _json({"found": False, "reason": "缺少必要信息", "missing": missing})

    # Demo 假设同一轮只审批一笔退款；若模型并行发起多个 create_refund_ticket，
    # 会产生多个 interrupt，resume 语义不在本步覆盖范围。
    # resume 后 ToolNode 会重跑同批工具：只读工具（如 refund_status）可能执行两次但无害；
    # billing 工具集中唯一写操作是本函数，且写入被审批结果门控，因此不会产生重复副作用。
    resume = interrupt(
        {
            "kind": "approval",
            "action": "create_refund_ticket",
            "params": {"user_id": user_id, "order_id": order_id, "reason": reason},
            "prompt": f"待人工审批：{user_id} 申请退款 订单 {order_id}（原因：{reason}）",
        }
    )

    if not isinstance(resume, dict):
        note = str(resume) if resume is not None else "人工审批未通过"
        return _json({"created": False, "rejected": True, "note": note})

    note = str(resume.get("note") or "").strip()
    if resume.get("approved") is not True:
        return _json({"created": False, "rejected": True, "note": note or "人工审批未通过"})

    try:
        detail = f"订单 {order_id} 退款申请：{reason}"
        if note:
            detail += f"；审批备注：{note}"
        ticket = business_db.create_ticket(user_id=user_id, ticket_type="refund", detail=detail)
        return _json({"found": True, "created": True, "approved": True, "ticket": ticket})
    except Exception as ex:  # noqa: BLE001
        return _error(ex)


@tool
def create_ticket(user_id: str = "", detail: str = "") -> str:
    """
    用户报告技术故障、需要技术支持介入或创建报障工单时调用。

    必须先拿到 user_id 和故障详情；本工具创建 tech 工单，初始状态为"待审批"。
    """
    try:
        user_id = (user_id or "").strip()
        detail = (detail or "").strip()
        missing = [name for name, value in {"user_id": user_id, "detail": detail}.items() if not value]
        if missing:
            return _json({"found": False, "reason": "缺少必要信息", "missing": missing})
        ticket = business_db.create_ticket(user_id=user_id, ticket_type="tech", detail=detail)
        return _json({"found": True, "created": True, "ticket": ticket})
    except Exception as ex:  # noqa: BLE001
        return _error(ex)


def _service_status_payload() -> dict:
    return {
        "found": True,
        "checked_at": "2026-07-04T10:00:00",
        "services": [
            {"name": "登录服务", "status": "正常", "detail": "账号登录、验证码校验可用"},
            {"name": "支付服务", "status": "正常", "detail": "订单支付与退款入账通道可用"},
            {"name": "短信通道", "status": "已恢复", "detail": "2026-07-03 18:20-18:45 曾有延迟，现已恢复"},
            {"name": "工单系统", "status": "正常", "detail": "客服工单创建与查询可用"},
        ],
    }


@tool
def check_service_status() -> str:
    """
    用户询问登录、支付、短信、工单等平台服务是否异常或是否有大盘故障时调用。

    这是写死的 mock 服务状态，不连接业务库；适合回答"是不是系统故障"这类技术支持问题。
    """
    try:
        return _json(_service_status_payload())
    except Exception as ex:  # noqa: BLE001
        return _error(ex)


BILLING_TOOLS = [query_bill, refund_status, create_refund_ticket]
TECHNICAL_TOOLS = [create_ticket, check_service_status]
ALL_TOOLS = [*BILLING_TOOLS, *TECHNICAL_TOOLS]
