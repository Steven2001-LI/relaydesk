"""
一键重建 mock 业务库 langgraph_cs/data/business.sqlite。

用法（从仓库根运行，确保能 import 到 langgraph_cs 包）：
    langgraph_cs/.venv/bin/python -m langgraph_cs.scripts.seed_business_db

效果：
    删除旧 business.sqlite，重建 orders / bills / tickets 三张表，并写入确定性演示数据。
    后续工具层可直接调用 langgraph_cs.business.db 里的查询/写入函数。
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from langgraph_cs.business import db as business_db

logger = logging.getLogger(__name__)

ITEMS = [
    "智能门锁 Pro",
    "云客服企业版月费",
    "降噪蓝牙耳机",
    "家用空气净化器",
    "人体工学办公椅",
    "便携投影仪",
    "无线充电台灯",
    "儿童学习平板",
    "旗舰扫地机器人",
    "智能体脂秤",
    "恒温电水壶",
    "机械键盘青轴版",
    "4K 会议摄像头",
    "智能猫眼门铃",
    "高速固态硬盘 2TB",
    "轻薄笔记本电脑",
    "护眼显示器 27 寸",
    "厨房净水器滤芯",
    "运动手环尊享版",
    "智能窗帘电机",
    "无线电竞鼠标",
    "移动电源 20000mAh",
    "智能音箱 Mini",
    "家用除湿机",
    "车载空气净化器",
    "商务双肩包",
    "咖啡机清洁套装",
    "智能摄像头云存储",
    "洗地机旗舰版",
    "桌面扩展坞",
]
AMOUNTS = [
    899.0,
    199.0,
    529.0,
    1299.0,
    1699.0,
    2399.0,
    269.0,
    1599.0,
    3299.0,
    189.0,
    229.0,
    499.0,
    699.0,
    399.0,
    1099.0,
    6299.0,
    1499.0,
    259.0,
    399.0,
    799.0,
    459.0,
    219.0,
    299.0,
    1399.0,
    699.0,
    369.0,
    129.0,
    59.0,
    2699.0,
    599.0,
]
RETURN_ORDER_SEQS = [3, 9, 16, 22, 28]
INVOICE_STATUSES = ["未开票", "开票中", "已开票"]


def _iso(day: datetime, hour: int, minute: int = 0) -> str:
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()


def build_seed_rows() -> tuple[list[dict], list[dict], list[dict]]:
    """构造确定性 seed 数据：30 订单、40 账单、10 工单。"""
    start = datetime(2026, 5, 2)
    orders: list[dict] = []
    for seq, (item, amount) in enumerate(zip(ITEMS, AMOUNTS, strict=True), start=1):
        day = start + timedelta(days=(seq - 1) * 2)
        if seq in RETURN_ORDER_SEQS:
            status = "退货中"
        elif seq % 5 == 0:
            status = "待发货"
        elif seq % 2 == 0:
            status = "已发货"
        else:
            status = "已签收"
        logistics_no = None if status == "待发货" else f"SF{202606000000 + seq:012d}"
        orders.append(
            {
                "order_id": f"ORD-{day:%Y%m%d}-{seq:03d}",
                "user_id": f"user_{((seq - 1) % 10) + 1:03d}",
                "item": item,
                "amount_yuan": amount,
                "status": status,
                "logistics_no": logistics_no,
                "created_at": _iso(day, 10, seq % 60),
            }
        )

    bills: list[dict] = []
    bill_seq = 1
    for order in orders:
        day = datetime.fromisoformat(order["created_at"])
        bills.append(
            {
                "bill_id": f"BILL-{day:%Y%m%d}-{bill_seq:03d}",
                "user_id": order["user_id"],
                "order_id": order["order_id"],
                "amount_yuan": order["amount_yuan"],
                "bill_type": "订单支付",
                "invoice_status": INVOICE_STATUSES[(bill_seq - 1) % len(INVOICE_STATUSES)],
                "created_at": _iso(day, 11, bill_seq % 60),
            }
        )
        bill_seq += 1

    membership_rows = [
        ("user_001", 29.9, "2026-05-15T09:20:00", "已开票"),
        ("user_003", 99.0, "2026-06-01T09:25:00", "未开票"),
        ("user_005", 29.9, "2026-06-15T09:30:00", "开票中"),
        ("user_007", 199.0, "2026-07-01T09:35:00", "已开票"),
        ("user_009", 29.9, "2026-07-03T09:40:00", "未开票"),
    ]
    for user_id, amount, created_at, invoice_status in membership_rows:
        day = datetime.fromisoformat(created_at)
        bills.append(
            {
                "bill_id": f"BILL-{day:%Y%m%d}-{bill_seq:03d}",
                "user_id": user_id,
                "order_id": None,
                "amount_yuan": amount,
                "bill_type": "会员扣费",
                "invoice_status": invoice_status,
                "created_at": created_at,
            }
        )
        bill_seq += 1

    return_orders = [order for order in orders if order["status"] == "退货中"]
    for idx, order in enumerate(return_orders, start=1):
        created_day = datetime.fromisoformat(order["created_at"]) + timedelta(days=2)
        bills.append(
            {
                "bill_id": f"BILL-{created_day:%Y%m%d}-{bill_seq:03d}",
                "user_id": order["user_id"],
                "order_id": order["order_id"],
                "amount_yuan": -round(order["amount_yuan"], 2),
                "bill_type": "退款",
                "invoice_status": INVOICE_STATUSES[idx % len(INVOICE_STATUSES)],
                "created_at": _iso(created_day, 15, idx * 3),
            }
        )
        bill_seq += 1

    tickets = [
        {
            "ticket_id": "TKT-20260508-001",
            "user_id": "user_003",
            "ticket_type": "refund",
            "status": "待审批",
            "detail": "用户反馈 ORD-20260506-003 尺码不合适，申请原路退款。",
            "created_at": "2026-05-08T16:10:00",
        },
        {
            "ticket_id": "TKT-20260520-002",
            "user_id": "user_009",
            "ticket_type": "refund",
            "status": "处理中",
            "detail": "用户咨询 ORD-20260518-009 退货物流已寄回，等待仓库验收。",
            "created_at": "2026-05-20T16:20:00",
        },
        {
            "ticket_id": "TKT-20260603-003",
            "user_id": "user_006",
            "ticket_type": "refund",
            "status": "已完成",
            "detail": "ORD-20260601-016 退货已入库，退款已提交银行处理。",
            "created_at": "2026-06-03T16:30:00",
        },
        {
            "ticket_id": "TKT-20260615-004",
            "user_id": "user_002",
            "ticket_type": "refund",
            "status": "已驳回",
            "detail": "ORD-20260613-022 超出售后时效，退款申请被驳回。",
            "created_at": "2026-06-15T16:40:00",
        },
        {
            "ticket_id": "TKT-20260627-005",
            "user_id": "user_008",
            "ticket_type": "refund",
            "status": "处理中",
            "detail": "ORD-20260625-028 退货件已签收，财务复核退款金额。",
            "created_at": "2026-06-27T16:50:00",
        },
        {
            "ticket_id": "TKT-20260512-006",
            "user_id": "user_004",
            "ticket_type": "tech",
            "status": "处理中",
            "detail": "用户反馈智能门锁 App 蓝牙配对失败，需要技术排查。",
            "created_at": "2026-05-12T13:05:00",
        },
        {
            "ticket_id": "TKT-20260604-007",
            "user_id": "user_010",
            "ticket_type": "tech",
            "status": "已完成",
            "detail": "会议摄像头固件升级后画面恢复正常。",
            "created_at": "2026-06-04T14:15:00",
        },
        {
            "ticket_id": "TKT-20260702-008",
            "user_id": "user_001",
            "ticket_type": "tech",
            "status": "待审批",
            "detail": "用户申请远程协助配置智能窗帘定时场景。",
            "created_at": "2026-07-02T10:25:00",
        },
        {
            "ticket_id": "TKT-20260611-009",
            "user_id": "user_005",
            "ticket_type": "complaint",
            "status": "已驳回",
            "detail": "用户投诉配送延迟，核查为不可抗力天气原因。",
            "created_at": "2026-06-11T18:30:00",
        },
        {
            "ticket_id": "TKT-20260703-010",
            "user_id": "user_007",
            "ticket_type": "complaint",
            "status": "已完成",
            "detail": "用户反馈发票抬头开错，已重新开具并短信通知。",
            "created_at": "2026-07-03T11:45:00",
        },
    ]
    return orders, bills, tickets


def seed_database(db_path=None) -> dict[str, int]:
    """删除旧库后重建并灌入确定性 seed 数据；返回各表写入行数。"""
    target = (Path(db_path) if db_path is not None else business_db.DEFAULT_DB_PATH).resolve()
    business_db._close_conn(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()

    orders, bills, tickets = build_seed_rows()
    conn = sqlite3.connect(str(target), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        business_db.init_schema(conn)
        conn.executemany(
            """
            INSERT INTO orders(order_id, user_id, item, amount_yuan, status, logistics_no, created_at)
            VALUES(:order_id, :user_id, :item, :amount_yuan, :status, :logistics_no, :created_at)
            """,
            orders,
        )
        conn.executemany(
            """
            INSERT INTO bills(bill_id, user_id, order_id, amount_yuan, bill_type, invoice_status, created_at)
            VALUES(:bill_id, :user_id, :order_id, :amount_yuan, :bill_type, :invoice_status, :created_at)
            """,
            bills,
        )
        conn.executemany(
            """
            INSERT INTO tickets(ticket_id, user_id, ticket_type, status, detail, created_at)
            VALUES(:ticket_id, :user_id, :ticket_type, :status, :detail, :created_at)
            """,
            tickets,
        )
        conn.commit()
    finally:
        conn.close()
    return {"orders": len(orders), "bills": len(bills), "tickets": len(tickets)}


def demo_examples() -> list[str]:
    return [
        "user_003 有退货中订单 ORD-20260506-003，可演示查询退款进度。",
        "user_009 有退货中订单 ORD-20260518-009，退款工单仍在处理中。",
        "user_001 有技术工单 TKT-20260702-008，可演示查询工单状态。",
        "user_007 有会员扣费账单 BILL-20260701-034，可演示账单/发票查询。",
    ]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    counts = seed_database()
    logger.info(
        "灌库完成：orders=%d, bills=%d, tickets=%d -> %s",
        counts["orders"],
        counts["bills"],
        counts["tickets"],
        business_db.DEFAULT_DB_PATH,
    )
    print("演示用查询示例：")
    for example in demo_examples():
        print(f"- {example}")


if __name__ == "__main__":
    main()
