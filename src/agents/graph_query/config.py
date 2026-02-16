"""Graph Query Agent configuration."""

from src.shared.config import BaseAgentSettings


class GraphQuerySettings(BaseAgentSettings):
    """Settings specific to the Graph Query Agent."""

    agent_name: str = "graph_query"
    max_traversal_depth: int = 3
    max_results: int = 50
    vector_search_top_k: int = 10

    class Config:
        env_prefix = "GRAPH_QUERY_"
