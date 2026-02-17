"""Indexer Agent configuration."""

import os

from src.shared.config import BaseAgentSettings


class IndexerSettings(BaseAgentSettings):
    """Settings specific to the Indexer Agent."""

    agent_name: str = "indexer"
    host: str = "0.0.0.0"
    port: int = 8002
    enrichment_model: str = os.getenv("DEFAULT_MINI_MODEL", "gpt-5-mini-2025-08-07")
    embedding_model: str = os.getenv("DEFAULT_EMBEDDING_MODEL", "text-embedding-3-large")
    enrichment_batch_size: int = 30
    max_concurrent_files: int = 10

    class Config:
        env_prefix = "INDEXER_"
