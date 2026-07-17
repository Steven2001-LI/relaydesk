"""
intent_node —— 意图识别节点。

意图识别采用 LLM 单路分类。备选方案是 LLM/Embedding/关键词多路加权融合；
当前意图集与语料规模下，单路 LLM 已满足路由准确率需求且实现更简单。
若意图数量增长导致单路准确率下降，多路融合是既定扩展点。

节点契约：
  输入：完整 state（只读最后一条用户消息）
  输出：{"intent": ..., "confidence": ..., "escalated": False}  —— 只返回要更新的字段
        每轮在这里复位 escalated，避免转人工后标记随 checkpointer 跨轮残留。
"""
import json
import logging

from langgraph_cs.config import build_llm
from langgraph_cs.nodes.utils import last_user_text

logger = logging.getLogger(__name__)

# 支持的意图集合。escalation（转人工/人工升级）：当用户明确要求人工坐席、或专职 Agent
# 无法处理时，由这个意图触发 human-in-the-loop。另一种实现是关键词检测（"转人工/人工客服"
# 词表），这里选择交给 LLM 统一识别成一个意图：与其余意图共用同一条分类路径，
# 对说法变体的召回也更好。
INTENTS = ["greeting", "query", "technical", "billing", "complaint", "request", "escalation", "other"]

# 类别带一行定义：只给类别名时，LLM 会把"查退款进度"分给 query、"申请退款"分给
# request（真实测过，均以 0.95 高置信路由到无工具的 general_agent），billing 类工具
# 永远轮不到。边界规则：凡涉及具体订单/账单/退款/发票的，一律 billing 优先。
_SYSTEM_PROMPT = (
    "你是一个意图分类器。判断用户最后一句话属于以下哪一类，并给出 0~1 的置信度。\n"
    "类别定义：\n"
    "- greeting: 问候寒暄\n"
    "- technical: 技术故障、报错、配置、登录异常、服务是否正常等技术支持问题\n"
    "- billing: 订单、账单、扣费、发票、开票、退款、会员费相关的查询或办理"
    "（包括查订单状态、查退款进度、申请退款、查扣费记录）\n"
    "- complaint: 投诉或强烈不满\n"
    "- escalation: 明确要求人工客服、转人工\n"
    "- query: 平台功能、政策、流程等一般咨询（不涉及用户具体的订单/账单/工单）\n"
    "- request: 其他事务办理请求（不属于 technical 和 billing 的）\n"
    "- other: 无法归入以上类别\n"
    "边界规则：只要问题涉及具体订单、账单、退款、发票，就选 billing，"
    "不要选 query 或 request。\n"
    "只输出 JSON，格式：{\"intent\": \"<类别>\", \"confidence\": <数字>}，不要任何多余文字。"
)


def _strip_code_fence(text: str) -> str:
    """
    去掉 LLM 可能加上的 markdown 代码围栏（```json ... ```）。
    DeepSeek 即便被要求"只输出 JSON"，有时仍会包一层 ```，
    不剥掉的话 json.loads 会失败、把所有意图都降级成 other。
    """
    s = text.strip()
    if not s.startswith("```"):
        return s
    # 去掉开头的 ``` 以及紧跟其后的语言标记（如 json）
    s = s[3:]
    if s[:4].lower() == "json":
        s = s[4:]
    # 去掉结尾的 ```
    s = s.strip()
    if s.endswith("```"):
        s = s[:-3]
    return s.strip()


def intent_node(state) -> dict:
    user_text = last_user_text(state)

    try:
        llm = build_llm(temperature=0.0)  # 分类要确定性，温度调到 0
        resp = llm.invoke(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ]
        )
    except Exception as ex:  # noqa: BLE001 节点边界兜底：意图识别失败 -> 降级路由，不让单点异常拖垮整图
        logger.warning("意图识别调用 LLM 失败，降级为 other：%s", ex)
        return {"intent": "other", "confidence": 0.0, "escalated": False}

    # 解析失败就降级为 other——与低置信度降级同一原则：识别结果不可靠时走通用路径。
    intent, confidence = "other", 0.0
    try:
        data = json.loads(_strip_code_fence(resp.content))
        if data.get("intent") in INTENTS:
            intent = data["intent"]
            confidence = float(data.get("confidence", 0.0))
    except (json.JSONDecodeError, ValueError, TypeError) as ex:
        logger.warning("意图解析失败，降级为 other：%s（原始输出：%s）", ex, resp.content)

    logger.info("意图识别 -> %s (%.2f)", intent, confidence)
    return {"intent": intent, "confidence": confidence, "escalated": False}
