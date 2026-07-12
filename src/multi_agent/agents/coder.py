"""Coder Worker：负责代码生成、功能实现和脚本开发任务。"""

import json
import logging

from multi_agent.agents.base_worker import BaseWorker, WorkerOutput
from multi_agent.defaults.prompts import CODER_SYSTEM_PROMPT
from multi_agent.models.task import Artifact

logger = logging.getLogger(__name__)


class CoderWorker(BaseWorker):
    name = "coder"
    prompt_id = "coder"
    system_prompt = CODER_SYSTEM_PROMPT  # 回退默认 Prompt

    def _parse_output(self, raw_content: str) -> WorkerOutput:
        try:
            content = raw_content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else content
            data = json.loads(content)
            return WorkerOutput(**data)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Coder output not valid JSON, wrapping raw output: %s", e)
            return WorkerOutput(
                status="success",
                summary="Code generated (raw output)",
                artifacts=[
                    Artifact(artifact_type="code", content=raw_content)
                ],
            )
