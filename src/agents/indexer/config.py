"""Indexer Agent configuration."""

from src.shared.config import BaseAgentSettings


class IndexerSettings(BaseAgentSettings):
    """Settings specific to the Indexer Agent."""

    agent_name: str = "indexer"
    enrichment_model: str = "gpt-5-mini-2025-08-07"
    embedding_model: str = "text-embedding-3-large"
    enrichment_batch_size: int = 30
    max_concurrent_files: int = 10

    class Config:
        env_prefix = "INDEXER_"
