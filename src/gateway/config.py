"""Gateway configuration."""

from src.shared.config import BaseAgentSettings


class GatewaySettings(BaseAgentSettings):
    """Settings specific to the FastAPI Gateway."""

    agent_name: str = "gateway"
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["*"]

    class Config:
        env_prefix = "GATEWAY_"
