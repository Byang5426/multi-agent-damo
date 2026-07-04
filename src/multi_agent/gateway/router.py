"""Gateway router: intent classification, injection detection, and routing."""

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

# ── Prompt injection detection ──

INJECTION_PATTERNS = [
    r"忘记你的(所有|全部|之前).*(规则|指令|设定)",
    r"忽略(以上|之前|所有).*(规则|指令|设定|限制)",
    r"你现在是(?!.*\?)",  # "You are now..." without a question mark
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
    """Check if text contains prompt injection patterns."""
    text_lower = text.lower()
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return True
    return False


# Actual prompt content is in multi_agent.defaults.prompts
# ROUTING_SYSTEM_PROMPT imported as fallback default


class GatewayRouter:
    """Classifies user requests and routes them to appropriate handlers."""

    def __init__(self):
        self._llm = ChatOpenAI(
            model=settings.gateway_model,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
            temperature=0,
        )

    async def route(self, user_input: str) -> RouteDecision:
        """Classify a user request and return routing decision.

        Returns a RouteDecision with route='blocked' if injection is detected.
        """
        # Check for prompt injection first
        if detect_injection(user_input):
            logger.warning("Prompt injection detected: %s", user_input[:100])
            return RouteDecision(
                route="blocked",
                reason="Potential prompt injection detected",
            )

        try:
            routing_prompt = await load_prompt("gateway_routing", ROUTING_SYSTEM_PROMPT)
            response = await self._llm.ainvoke(
                [
                    SystemMessage(content=routing_prompt),
                    HumanMessage(content=user_input),
                ],
                response_format={"type": "json_object"},
            )
            data = RouteDecision.model_validate_json(response.content)
            return data
        except Exception as e:
            logger.error("Router LLM call failed: %s, falling back to 'instant'", e)
            return RouteDecision(
                route="instant",
                reason=f"Router fallback due to error: {e}",
                suggested_worker="analyzer",
            )
