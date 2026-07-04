# 工具调用质量评测结果

- 模型：`deepseek-chat`
- 温度：0.5
- 运行方式：单次运行；LLM 有非确定性，数字用于回归与方向性判断。
- 区间：基线集多次复跑为 **22–24/24**，本文件是区间内的单次采样；摇摆集中在 `tech-ticket-01` / `missing-id-04` 两条技术样本对无参 `check_service_status` 的过度触发。
- 样本数：24（正例 14 / 负例 10）

## 汇总

- 总通过率：**22/24 = 91.7%**
- 正例工具选择准确率：**92.9%**
- tool_hit 子集参数准确率：**100.0%**

## should-call 混淆矩阵

| 维度 | 数量 |
|---|---:|
| 该调且调了 | 14 |
| 该调没调 | 0 |
| 不该调却调了 | 1 |
| 不该调也没调 | 9 |

## 按 category 分组

| category | n | pass | should_call | called | tool_hit | args_ok |
|---|---:|---:|---:|---:|---:|---:|
| billing | 9 | 9 | 9 | 9 | 9 | 9 |
| missing_info | 4 | 3 | 0 | 1 | 0 | 0 |
| policy | 4 | 4 | 0 | 0 | 0 | 0 |
| smalltalk | 2 | 2 | 0 | 0 | 0 | 0 |
| technical | 5 | 4 | 5 | 5 | 4 | 4 |

## 逐条明细

| id | category | route | expected | actual calls | tool_hit | args_ok | pass | diagnosis |
|---|---|---|---|---|---:|---:|---:|---|
| bill-query-01 | billing | billing_agent | query_bill({"user_id": "user_003"}) | query_bill({"user_id": "user_003"}) | True | True | True | OK |
| bill-query-02 | billing | billing_agent | query_bill({"bill_id": "BILL-20260701-034"}) | query_bill({"bill_id": "BILL-20260701-034"}) | True | True | True | OK |
| bill-query-03 | billing | billing_agent | query_bill({"user_id": "user_007"}) | query_bill({"user_id": "user_007"}) | True | True | True | OK |
| refund-status-01 | billing | billing_agent | refund_status({"order_id": "ORD-20260506-003"}) | refund_status({"order_id": "ORD-20260506-003"}) | True | True | True | OK |
| refund-status-02 | billing | billing_agent | refund_status({"order_id": "ORD-20260518-009"}) | refund_status({"order_id": "ORD-20260518-009"}) | True | True | True | OK |
| refund-status-03 | billing | billing_agent | refund_status({"order_id": "ORD-20260625-028"}) | refund_status({"order_id": "ORD-20260625-028"}) | True | True | True | OK |
| refund-create-01 | billing | billing_agent | create_refund_ticket({"order_id": "ORD-20260508-004", "reason": "*", "user_id": "user_004"}) | create_refund_ticket({"order_id": "ORD-20260508-004", "reason": "空气净化器外壳有裂痕，属于商品质量问题", "user_id": "user_004"}) → refund_status({"order_id": "ORD-20260508-004"}) | True | True | True | OK |
| refund-create-02 | billing | billing_agent | create_refund_ticket({"order_id": "ORD-20260520-010", "reason": "*", "user_id": "user_010"}) | create_refund_ticket({"order_id": "ORD-20260520-010", "reason": "未发货，不想要了，申请仅退款", "user_id": "user_010"}) → refund_status({"order_id": "ORD-20260520-010"}) | True | True | True | OK |
| refund-create-03 | billing | billing_agent | create_refund_ticket({"order_id": "ORD-20260522-011", "reason": "*", "user_id": "user_001"}) | create_refund_ticket({"order_id": "ORD-20260522-011", "reason": "买错型号，申请退货退款", "user_id": "user_001"}) → refund_status({"order_id": "ORD-20260522-011"}) | True | True | True | OK |
| tech-ticket-01 | technical | technical_agent | create_ticket({"detail": "*", "user_id": "user_004"}) | check_service_status({}) | False | False | False | wrong tool: got check_service_status |
| tech-ticket-02 | technical | technical_agent | create_ticket({"detail": "*", "user_id": "user_001"}) | check_service_status({}) → create_ticket({"detail": "用户 user_001 反馈智能窗帘定时场景配置不了，需要远程协助排查。已确认服务大盘各服务正常，非全局故障。", "user_id": "user_001"}) | True | True | True | OK |
| tech-ticket-03 | technical | technical_agent | create_ticket({"detail": "*", "user_id": "user_010"}) | check_service_status({}) → create_ticket({"detail": "会议摄像头在升级后出现黑屏问题，无法正常使用。用户已确认升级操作，但问题仍未解决，需要技术支持排查。", "user_id": "user_010"}) | True | True | True | OK |
| service-status-01 | technical | technical_agent | check_service_status({}) | check_service_status({}) | True | True | True | OK |
| service-status-02 | technical | technical_agent | check_service_status({}) | check_service_status({}) | True | True | True | OK |
| policy-01 | policy | billing_agent | no tool | - | - | - | True | TN: no tool call |
| policy-02 | policy | billing_agent | no tool | - | - | - | True | TN: no tool call |
| policy-03 | policy | general_agent | no tool | - | - | - | True | TN: no tool call |
| policy-04 | policy | billing_agent | no tool | - | - | - | True | TN: no tool call |
| missing-id-01 | missing_info | billing_agent | no tool | - | - | - | True | TN: no tool call |
| missing-id-02 | missing_info | billing_agent | no tool | - | - | - | True | TN: no tool call |
| missing-id-03 | missing_info | billing_agent | no tool | - | - | - | True | TN: no tool call |
| missing-id-04 | missing_info | technical_agent | no tool | check_service_status({}) | - | - | False | FP: unexpected tool call |
| smalltalk-01 | smalltalk | general_agent | no tool | - | - | - | True | TN: no tool call |
| smalltalk-02 | smalltalk | general_agent | no tool | - | - | - | True | TN: no tool call |

> 由 `python -m langgraph_cs.eval.tool_eval --write-md` 生成。读工具使用真实业务库；
> 写工具 `create_ticket` 在评测期间被 monkeypatch 为记录器，避免污染演示库；
> `create_refund_ticket` 通过 approval interrupt 捕获参数，不 resume、不落库。
