"""
Health routes — GET /api/agents/health and GET /api/graph/statistics.
"""

import os
from typing import Any

from fastapi import APIRouter, HTTPException
from langchain_mcp_adapters.client import MultiServerMCPClient
from langfuse import observe
from pydantic import BaseModel, Field

from src.shared.logging import setup_logging

logger = setup_logging("gateway.routes.health", level="INFO")

router = APIRouter()

# Global MCP clients for health checks (lazy initialization)
_agent_clients: dict[str, MultiServerMCPClient] = {}

# Agent URL mapping - SSE endpoints
AGENT_URLS = {
    "orchestrator": os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8001/sse"),
    "indexer": os.getenv("INDEXER_URL", "http://indexer:8002/sse"),
    "graph_query": os.getenv("GRAPH_QUERY_URL", "http://graph_query:8003/sse"),
    "code_analyst": os.getenv("CODE_ANALYST_URL", "http://code_analyst:8004/sse"),
}


async def _get_agent_client(agent_name: str) -> MultiServerMCPClient:
    """Lazy initialization of agent MCP clients."""
    if agent_name not in _agent_clients:
        agent_url = AGENT_URLS.get(agent_name)
        if not agent_url:
            raise ValueError(f"Unknown agent: {agent_name}")

        logger.info(f"Initializing {agent_name} MCP client at {agent_url}")
        _agent_clients[agent_name] = MultiServerMCPClient({
            agent_name: {
                "url": agent_url,
                "transport": "sse",
            }
        })

    return _agent_clients[agent_name]


@observe(name="check_agent_health", as_type="span")
async def _check_agent_health(agent_name: str) -> dict[str, Any]:
    """Check health of a single agent."""
    try:
        client = await _get_agent_client(agent_name)
        tools = await client.get_tools()

        return {
            "status": "healthy",
            "tools_count": len(tools),
            "tools": [t.name for t in tools],
        }
    except Exception as e:
        logger.error(f"Health check failed for {agent_name}: {e}")
        return {
            "status": "unhealthy",
            "error": str(e),
        }


# ─── Response Models ─────────────────────────────────────────


class AgentHealth(BaseModel):
    """Health status of a single agent."""

    agent_name: str = Field(..., description="Name of the agent")
    status: str = Field(..., description="Health status: healthy or unhealthy")
    tools_count: int | None = Field(None, description="Number of available tools")
    tools: list[str] = Field(default_factory=list, description="List of tool names")
    error: str | None = Field(None, description="Error message if unhealthy")


class AgentsHealthResponse(BaseModel):
    """Response model for GET /api/agents/health."""

    overall_status: str = Field(
        ..., description="Overall system health: healthy, degraded, or unhealthy"
    )
    agents: list[AgentHealth] = Field(..., description="Health status of each agent")
    healthy_count: int = Field(..., description="Number of healthy agents")
    total_count: int = Field(..., description="Total number of agents")


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


# ─── GET /api/agents/health ─────────────────────────────────


@router.get("/agents/health", response_model=AgentsHealthResponse)
@observe(name="get_agents_health", as_type="span")
async def get_agents_health() -> AgentsHealthResponse:
    """Health check for all MCP agents.

    Checks connectivity and tool availability for each agent:
    - Orchestrator: Central coordinator
    - Indexer: Repository indexing and graph population
    - Graph Query: Knowledge graph traversal and querying
    - Code Analyst: Code understanding and analysis

    Returns overall system health based on agent statuses:
    - healthy: All agents operational
    - degraded: Some agents operational, some failed
    - unhealthy: All agents failed or critical agents down
    """
    logger.info("Performing health check on all agents")

    agents_to_check = [
        "orchestrator",
        "indexer",
        "graph_query",
        "code_analyst",
    ]

    agent_health_results: list[AgentHealth] = []
    healthy_count = 0

    for agent_name in agents_to_check:
        health = await _check_agent_health(agent_name)
        status = health.get("status", "unhealthy")

        agent_health_results.append(
            AgentHealth(
                agent_name=agent_name,
                status=status,
                tools_count=health.get("tools_count"),
                tools=health.get("tools", []),
                error=health.get("error"),
            )
        )

        if status == "healthy":
            healthy_count += 1

    # Determine overall status
    total_count = len(agents_to_check)
    if healthy_count == total_count:
        overall_status = "healthy"
    elif healthy_count > 0:
        overall_status = "degraded"
    else:
        overall_status = "unhealthy"

    logger.info(
        f"Health check complete: {healthy_count}/{total_count} agents healthy"
    )

    return AgentsHealthResponse(
        overall_status=overall_status,
        agents=agent_health_results,
        healthy_count=healthy_count,
        total_count=total_count,
    )


# ─── GET /api/graph/statistics ──────────────────────────────


@router.get("/graph/statistics", response_model=GraphStatistics)
@observe(name="get_graph_statistics", as_type="span")
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
