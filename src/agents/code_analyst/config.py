"""Code Analyst Agent configuration."""

from src.shared.config import BaseAgentSettings


class CodeAnalystSettings(BaseAgentSettings):
    """Settings specific to the Code Analyst Agent."""

    agent_name: str = "code_analyst"
    analysis_model: str = "gpt-5.2-2025-12-11"
    max_source_context_lines: int = 200
