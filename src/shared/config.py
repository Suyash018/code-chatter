"""
Base configuration for all MCP agents.

Uses Pydantic Settings for environment-based configuration.
Each agent extends BaseAgentSettings with its own prefix.
"""

from pydantic_settings import BaseSettings


class BaseAgentSettings(BaseSettings):
    """Base settings shared by all MCP agent servers."""

    agent_name: str = "base"

    # Neo4j connection
    neo4j_uri: str = ""
    neo4j_username: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"

    # OpenAI
    openai_api_key: str = ""

    # Logging
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"
