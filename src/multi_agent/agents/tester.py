"""Tester Worker - handles testing and review tasks."""

import json
import logging

from multi_agent.agents.base_worker import BaseWorker, WorkerOutput
from multi_agent.defaults.prompts import TESTER_SYSTEM_PROMPT
from multi_agent.models.task import Artifact

logger = logging.getLogger(__name__)


class TesterWorker(BaseWorker):
    name = "tester"
    prompt_id = "tester"
    system_prompt = TESTER_SYSTEM_PROMPT  # fallback

    def _parse_output(self, raw_content: str) -> WorkerOutput:
        try:
            content = raw_content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else content
            data = json.loads(content)
            return WorkerOutput(**data)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Tester output not valid JSON, wrapping raw output: %s", e)
            return WorkerOutput(
                status="success",
                summary="Test report generated (raw output)",
                artifacts=[
                    Artifact(artifact_type="test_report", content=raw_content)
                ],
            )
