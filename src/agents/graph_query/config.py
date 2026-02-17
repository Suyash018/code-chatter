"""Graph Query Agent configuration."""

from src.shared.config import BaseAgentSettings


class GraphQuerySettings(BaseAgentSettings):
    """Settings specific to the Graph Query Agent."""

    agent_name: str = "graph_query"
    max_traversal_depth: int = 3
    max_results: int = 50
    vector_search_top_k: int = 10
    query_model: str = "gpt-5.2-2025-12-11"
    embedding_model: str = "text-embedding-3-large"

    class Config:
        env_prefix = "GRAPH_QUERY_"
