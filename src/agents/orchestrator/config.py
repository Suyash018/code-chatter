"""Orchestrator Agent configuration."""

import os

from src.shared.config import BaseAgentSettings


class OrchestratorSettings(BaseAgentSettings):
    """Settings specific to the Orchestrator Agent."""

    agent_name: str = "orchestrator"
    max_agent_retries: int = 2
    synthesis_model: str = os.getenv("DEFAULT_MODEL", "gpt-5.2-2025-12-11")
    analysis_model: str = os.getenv("DEFAULT_MODEL", "gpt-5.2-2025-12-11")
    orchestrator_model: str = os.getenv("DEFAULT_MODEL", "gpt-5.2-2025-12-11")
    agent_timeout_seconds: int = 120
    max_context_turns: int = 20

    class Config:
        env_prefix = "ORCHESTRATOR_"
