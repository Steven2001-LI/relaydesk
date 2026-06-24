"""
intent_node —— 意图识别节点。

教学版：只用 LLM 单路识别（朴素但好懂）。
对照旧版 core/intent_recognizer.py：那里是 LLM(70%) + Embedding(20%) + 关键词(10%)
三路加权融合。等你把单路跑通、理解了节点怎么读写 state，阶段进阶时再把另外两路加回来，
就能在简历里讲"为什么要三路融合、各自补什么短板"。

节点契约：
  输入：完整 state（我们只关心最后一条用户消息）
  输出：{"intent": ..., "confidence": ...}  —— 只返回要更新的字段
"""
import json
import logging

from langgraph_cs.config import build_llm
from langgraph_cs.nodes.utils import last_user_text

logger = logging.getLogger(__name__)

# 支持的意图集合（精简版，对照旧版 IntentCategory）。
# 阶段 3 新增 escalation（转人工/人工升级）：当用户明确要求人工坐席、或专职 Agent 无法处理时，
# 由这个意图触发 human-in-the-loop。对照旧版里 _needs_escalation 的关键词检测
# （转人工 / 人工客服 / escalate / 无法处理）——这里交给 LLM 统一识别成一个意图。
INTENTS = ["greeting", "query", "technical", "billing", "complaint", "request", "escalation", "other"]

_SYSTEM_PROMPT = (
    "你是一个意图分类器。判断用户最后一句话属于以下哪一类，并给出 0~1 的置信度。\n"
    f"可选类别：{', '.join(INTENTS)}\n"
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
    llm = build_llm(temperature=0.0)  # 分类要确定性，温度调到 0

    resp = llm.invoke(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ]
    )

    # 朴素解析 + 兜底：解析失败就降级为 other（对照旧版的"低置信度降级"思路）。
    intent, confidence = "other", 0.0
    try:
        data = json.loads(_strip_code_fence(resp.content))
        if data.get("intent") in INTENTS:
            intent = data["intent"]
            confidence = float(data.get("confidence", 0.0))
    except (json.JSONDecodeError, ValueError, TypeError) as ex:
        logger.warning("意图解析失败，降级为 other：%s（原始输出：%s）", ex, resp.content)

    logger.info("意图识别 -> %s (%.2f)", intent, confidence)
    return {"intent": intent, "confidence": confidence}
