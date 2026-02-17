"""
Neo4j Graph Manager

Composes the full Neo4jGraphManager from domain-specific mixin classes.
Each mixin handles a specific set of graph operations.

Import this module to get the complete graph manager:
    from src.agents.indexer.graph_manager import Neo4jGraphManager
"""

from src.agents.indexer.graph_base import GraphManagerBase
from src.agents.indexer.graph_nodes import NodeOperationsMixin
from src.agents.indexer.graph_edges import EdgeOperationsMixin
from src.agents.indexer.graph_enrichment import EnrichmentOperationsMixin
from src.agents.indexer.graph_embeddings import EmbeddingOperationsMixin
from src.agents.indexer.graph_stats import StatsOperationsMixin


class Neo4jGraphManager(
    GraphManagerBase,
    NodeOperationsMixin,
    EdgeOperationsMixin,
    EnrichmentOperationsMixin,
    EmbeddingOperationsMixin,
    StatsOperationsMixin,
):
    """
    Full Neo4j graph manager combining all operation mixins.

    Manages the Neo4j knowledge graph for the codebase.
    Provides typed methods for every graph operation the indexer needs.
    All methods are async for non-blocking I/O.

    Accepts a shared ``Neo4jHandler`` so that the driver lifecycle is
    managed centrally rather than per-manager.
    """

    pass
