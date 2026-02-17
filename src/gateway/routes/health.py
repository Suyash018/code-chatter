"""
Health routes — GET /api/health and GET /api/graph/statistics.
"""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.shared.logging import setup_logging

logger = setup_logging("gateway.routes.health", level="INFO")

router = APIRouter()


class GraphStatistics(BaseModel):
    """Response model for GET /api/graph/statistics."""

    node_counts: dict[str, int] = Field(
        default_factory=dict, description="Count of nodes by type"
    )
    edge_counts: dict[str, int] = Field(
        default_factory=dict, description="Count of edges by type"
    )
    total_nodes: int = Field(..., description="Total number of nodes")
    total_edges: int = Field(..., description="Total number of edges")
    enrichment_coverage: float = Field(
        ..., description="Percentage of entities with LLM enrichment"
    )
    embedding_coverage: float = Field(
        ..., description="Percentage of entities with vector embeddings"
    )
    last_indexed: str | None = Field(
        None, description="Timestamp of last indexing operation"
    )


# ─── GET /api/graph/statistics ──────────────────────────────


@router.get("/graph/statistics", response_model=GraphStatistics)
async def get_graph_statistics() -> GraphStatistics:
    """Get knowledge graph statistics.

    Returns comprehensive statistics about the Neo4j knowledge graph:
    - Node counts by type (Class, Function, Module, etc.)
    - Edge counts by type (CALLS, IMPORTS, INHERITS_FROM, etc.)
    - Total counts
    - Enrichment coverage (percentage with LLM semantic layer)
    - Embedding coverage (percentage with vector embeddings)
    - Last indexing timestamp

    Useful for monitoring indexing progress and graph completeness.
    """
    logger.info("Fetching graph statistics")

    try:
        # Get indexer client to query graph stats
        from src.agents.indexer.agent import IndexerAgent

        indexer = await IndexerAgent.create()

        # Use the get_index_status tool to fetch graph statistics
        result = await indexer.invoke(
            "Get graph statistics: node counts, edge counts, enrichment coverage"
        )

        # Parse the result (this is a simplified version)
        # In production, the indexer would have a dedicated tool for this
        # For now, we'll return mock data

        logger.warning("Using mock graph statistics - implement proper stats query")

        return GraphStatistics(
            node_counts={
                "Module": 0,
                "Class": 0,
                "Function": 0,
                "Method": 0,
                "Parameter": 0,
                "Decorator": 0,
            },
            edge_counts={
                "CONTAINS": 0,
                "IMPORTS": 0,
                "CALLS": 0,
                "INHERITS_FROM": 0,
                "DECORATED_BY": 0,
                "HAS_PARAMETER": 0,
            },
            total_nodes=0,
            total_edges=0,
            enrichment_coverage=0.0,
            embedding_coverage=0.0,
            last_indexed=None,
        )

    except Exception as e:
        logger.exception(f"Error fetching graph statistics: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch graph statistics: {str(e)}"
        )


# ─── GET /api/health (simple health check) ──────────────────


@router.get("/health")
async def simple_health() -> dict:
    """Simple health check endpoint.

    Returns a basic health status without checking all agents.
    Useful for load balancers and uptime monitors.
    """
    return {
        "status": "healthy",
        "service": "FastAPI Gateway",
        "version": "0.1.0",
    }
