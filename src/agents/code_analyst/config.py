"""Code Analyst Agent configuration."""

import os

from src.shared.config import BaseAgentSettings


class CodeAnalystSettings(BaseAgentSettings):
    """Settings specific to the Code Analyst Agent."""

    agent_name: str = "code_analyst"
    analysis_model: str = os.getenv("DEFAULT_MODEL", "gpt-5.2-2025-12-11")
    max_source_context_lines: int = 200

    class Config:
        env_prefix = "CODE_ANALYST_"
