"""Analyzer Worker - handles analysis and research tasks."""

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
        """Parse LLM response, with fallback for non-JSON output."""
        try:
            # Try to extract JSON from the response
            content = raw_content.strip()
            # Handle markdown code blocks
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
