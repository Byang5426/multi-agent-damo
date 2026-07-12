"""Gateway 路由器：意图分类、注入检测与请求路由。"""

import json
import logging
import re
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from multi_agent.config import settings
from multi_agent.defaults.prompts import ROUTING_SYSTEM_PROMPT
from multi_agent.prompt_loader import load_prompt

logger = logging.getLogger(__name__)

# ── Prompt 注入检测正则模式 ──

INJECTION_PATTERNS = [
    r"忘记你的(所有|全部|之前).*(规则|指令|设定)",
    r"忽略(以上|之前|所有).*(规则|指令|设定|限制)",
    r"你现在是(?!.*\?)",  # "你现在是..." 但不带问号
    r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions|rules|prompts)",
    r"you\s+are\s+now\s+(a|an)\s+",
    r"disregard\s+(your|all|previous)\s+",
    r"pretend\s+(you\s+are|to\s+be)\s+",
    r"act\s+as\s+if\s+you\s+(have\s+no|don't\s+have)\s+",
    r"override\s+(your|system)\s+(instructions|rules)",
]


class RouteDecision(BaseModel):
    """Gateway路由决策结果"""

    route: str = Field(description="路由目标: instant(即时), project(项目), blocked(拦截)")
    reason: str = Field(default="", description="路由决策的理由")
    suggested_worker: Optional[str] = Field(
        default=None, description="即时任务推荐的Worker: analyzer/coder/tester"
    )


def detect_injection(text: str) -> bool:
    """检查文本是否包含 Prompt 注入模式。"""
    text_lower = text.lower()
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return True
    return False


# 实际 Prompt 内容在 multi_agent.defaults.prompts 中
# ROUTING_SYSTEM_PROMPT 作为回退默认值导入


class GatewayRouter:
    """对用户请求进行分类，路由到合适的处理链路。"""

    def __init__(self):
        self._llm = ChatOpenAI(
            model=settings.gateway_model,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
            temperature=0,
        )

    async def route(self, user_input: str) -> RouteDecision:
        """对用户请求进行分类并返回路由决策。

        如果检测到注入攻击，返回 route='blocked' 的决策。
        """
        # 优先检查 Prompt 注入
        if detect_injection(user_input):
            logger.warning("Prompt injection detected: %s", user_input[:100])
            return RouteDecision(
                route="blocked",
                reason="Potential prompt injection detected",
            )

        routing_prompt = await load_prompt("gateway_routing", ROUTING_SYSTEM_PROMPT)
        messages = [
            SystemMessage(content=routing_prompt),
            HumanMessage(content=user_input),
        ]

        # 最多重试一次，确保 LLM 分类成功
        last_error = None
        for attempt in range(2):
            try:
                response = await self._llm.ainvoke(
                    messages,
                    response_format={"type": "json_object"},
                )
                data = _parse_route_response(response.content)
                return data
            except Exception as e:
                last_error = e
                logger.warning("Router 解析失败 (attempt %d): %s", attempt + 1, e)

        # AI 分类全部失败，降级为 instant + analyzer
        logger.error("Router LLM call failed after retries: %s", last_error)
        return RouteDecision(
            route="instant",
            reason=f"Router fallback due to error: {last_error}",
            suggested_worker="analyzer",
        )


def _parse_route_response(content: str) -> RouteDecision:
    """从 LLM 输出中解析 RouteDecision，兼容多种非标准格式。"""
    text = content.strip()

    # 1. 直接尝试解析
    if text.startswith("{"):
        return RouteDecision.model_validate_json(text)

    # 2. LLM 返回了数组，取第一个对象元素
    if text.startswith("["):
        arr = json.loads(text)
        for item in arr:
            if isinstance(item, dict):
                return RouteDecision.model_validate(item)
        raise ValueError(f"数组中无有效对象: {text[:80]}")

    # 3. 文本中嵌入 JSON，用正则提取第一个 {...}
    match = re.search(r"\{[^{}]+\}", text, re.DOTALL)
    if match:
        return RouteDecision.model_validate_json(match.group())

    raise ValueError(f"无法从 LLM 输出中提取 JSON: {text[:120]}")
