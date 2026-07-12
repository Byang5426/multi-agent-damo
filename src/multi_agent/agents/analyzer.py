"""分析 Worker：负责需求分析、文档研究和方案设计任务。"""

import json
import logging

from multi_agent.agents.base_worker import BaseWorker, WorkerOutput
from multi_agent.defaults.prompts import ANALYZER_SYSTEM_PROMPT
from multi_agent.models.task import Artifact

logger = logging.getLogger(__name__)


class AnalyzerWorker(BaseWorker):
    name = "analyzer"
    prompt_id = "analyzer"
    system_prompt = ANALYZER_SYSTEM_PROMPT  # fallback

    def _parse_output(self, raw_content: str) -> WorkerOutput:
        """解析 LLM 响应，非 JSON 输出时回退包装。"""
        try:
            # 尝试从响应中提取 JSON
            content = raw_content.strip()
            # 处理 Markdown 代码块包裹
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else content
            data = json.loads(content)
            return WorkerOutput(**data)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Analyzer output not valid JSON, wrapping raw output: %s", e)
            return WorkerOutput(
                status="success",
                summary="Analysis completed (raw output)",
                artifacts=[
                    Artifact(artifact_type="analysis", content=raw_content)
                ],
            )
