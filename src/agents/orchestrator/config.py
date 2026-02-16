"""Orchestrator Agent configuration."""

from src.shared.config import BaseAgentSettings


class OrchestratorSettings(BaseAgentSettings):
    """Settings specific to the Orchestrator Agent."""

    agent_name: str = "orchestrator"
    max_agent_retries: int = 2
    synthesis_model: str = "gpt-5.2-2025-12-11"

    class Config:
        env_prefix = "ORCHESTRATOR_"
