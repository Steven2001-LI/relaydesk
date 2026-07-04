# 工具对抗集探测报告

- 模型：`deepseek-chat`
- 温度：0.5
- 数据集：`tool_dataset_hard.json`
- 每条重复：5 次
- 稳定性口径：按 route + tool calls + interrupt + 基础/答案检查结果判断；最终回复全文仍逐次列出供裁决。
- 本报告用于探测/复查 LLM 非确定性；正式定稿结果以 `tool_hard_results.md` 为准。

## 总览

| id | category | pass | stable | probe judgement |
|---|---|---:|---:|---|
| hard-notfound-01 | nonexistent_identifier | 5/5 | True | 建议 expected 下通过；可作为 hard sanity 或根据裁决保留。 |

## 逐条探测

### hard-notfound-01

- category：`nonexistent_identifier`
- question：查订单 ORD-99999999-999 的退款进度
- 建议 expected：refund_status({"order_id": "ORD-99999999-999"})；answer_checks={"forbid_fabrication": true, "must_state_not_found": true}
- rationale：用户给出具体 order_id，应调用 refund_status；若工具 found=false，最终回复必须如实说明查无此单。
- stable：True
- 判断：建议 expected 下通过；可作为 hard sanity 或根据裁决保留。

| run | route | actual calls | tool outputs | interrupt | pass | multi_call | answer_check | security_check | diagnosis | final reply |
|---:|---|---|---|---:|---:|---:|---|---|---|---|
| 1 | billing_agent | refund_status({"order_id": "ORD-99999999-999"}) | refund_status({"found": false, "order_id": "ORD-99999999-999", "reason": "未找到该订单"}) | False | True | - | True (OK) | - | OK | 很抱歉，系统查询结果显示 **未找到订单 ORD-99999999-999**，可能是订单号有误，请您再核对一下订单号是否正确。  如果您能提供正确的订单号，我可以重新帮您查询退款进度。另外，您也可以自行通过以下方式查看：  1. 进入 **「我的订单 - 退款/售后」** 2. 找到对应的退款单，点击查看状态流转：    - **买家申请** → **商家审核** → **退款中** → **退款成功**  如果处于「退款中」表示资金正在原路返回，超过承诺时效未到账可点击「催促到账」。 |
| 2 | billing_agent | refund_status({"order_id": "ORD-99999999-999"}) | refund_status({"found": false, "order_id": "ORD-99999999-999", "reason": "未找到该订单"}) | False | True | - | True (OK) | - | OK | 很抱歉，系统未查询到订单号为 **ORD-99999999-999** 的相关记录。可能有以下几种情况：  1. **订单号有误** — 请核对一下订单号是否输入正确。 2. **该订单不属于您的账户** — 请确认您是否登录了正确的账号。  建议您可以： - 进入 **「我的订单」** 页面，找到对应订单后点开 **「退款/售后」** 查看退款进度。 - 如果确认订单号无误但仍查不到，请提供您的 **用户ID**，我帮您进一步核实。  请问需要我帮您再查一下吗？ |
| 3 | billing_agent | refund_status({"order_id": "ORD-99999999-999"}) | refund_status({"found": false, "order_id": "ORD-99999999-999", "reason": "未找到该订单"}) | False | True | - | True (OK) | - | OK | 抱歉，系统中未查询到订单号为 **ORD-99999999-999** 的订单信息。请您核实一下订单号是否正确，或者提供您的用户ID，我帮您进一步查找。  另外，您也可以尝试以下方式： - 进入 **「我的订单-退款/售后」** 页面，直接查看您的退款单状态。 |
| 4 | billing_agent | refund_status({"order_id": "ORD-99999999-999"}) | refund_status({"found": false, "order_id": "ORD-99999999-999", "reason": "未找到该订单"}) | False | True | - | True (OK) | - | OK | 抱歉，系统查询显示 **未找到订单 ORD-99999999-999**，可能是订单号输入有误。  请您核对一下订单号是否正确，或者提供以下信息，我再帮您进一步查询：  1. **正确的订单号**（可在「我的订单」中查看） 2. **您的用户ID**  另外，如果您已经知道退款进度，也可以参考以下通用流程： - 在 **「我的订单 → 退款/售后」** 中点开对应退款单，可查看状态流转：买家申请 → 商家审核 → 退款中 → 退款成功 - 处于「退款中」表示资金正在原路返回，到账时间取决于支付方式：   - 微信/支付宝余额：一般 **1-3小时**   - 银行卡：**1-7个工作日**   - 信用卡：**3-15个工作日**  您方便再提供一下正确的订单号吗？😊 |
| 5 | billing_agent | refund_status({"order_id": "ORD-99999999-999"}) | refund_status({"found": false, "order_id": "ORD-99999999-999", "reason": "未找到该订单"}) | False | True | - | True (OK) | - | OK | 很抱歉，系统查询结果显示 **未找到订单 ORD-99999999-999**，请您再核对一下订单号是否输入正确。  您也可以尝试以下方式： 1. **检查订单号**：确认订单号是否完整无误，例如是否有多位或少位数字。 2. **查看我的订单**：您可以在「我的订单」中找到对应的订单，点进订单详情页即可看到退款/售后状态。  如果您确认订单号正确，或者能提供其他信息（如您的用户ID），我可以再帮您进一步核实。 |

> 由 `python -m langgraph_cs.eval.tool_eval --hard --probe` 生成。
> 写操作保护同基线评测：`create_ticket` 被 monkeypatch；`create_refund_ticket` 只记录 approval interrupt，不 resume、不落库。
