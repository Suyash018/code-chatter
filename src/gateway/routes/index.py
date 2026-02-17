"""
Index routes — POST /api/index and GET /api/index/status/{job_id}.
"""

import json
import os
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from langchain_mcp_adapters.client import MultiServerMCPClient
from langfuse.decorators import observe
from pydantic import BaseModel, Field

from src.shared.logging import setup_logging

logger = setup_logging("gateway.routes.index", level="INFO")

router = APIRouter()

# Global indexer client (lazy initialization)
_indexer_client: MultiServerMCPClient | None = None


async def _get_indexer_client() -> MultiServerMCPClient:
    """Lazy initialization of the Indexer MCP client."""
    global _indexer_client

    if _indexer_client is None:
        indexer_url = os.getenv("INDEXER_URL", "http://indexer:8002")
        logger.info(f"Initializing Indexer MCP client at {indexer_url}")
        _indexer_client = MultiServerMCPClient({
            "indexer": {
                "url": indexer_url,
                "transport": "sse",
            }
        })

    return _indexer_client


@observe(name="call_indexer_tool", as_type="span")
async def _call_indexer_tool(tool_name: str, **kwargs) -> dict:
    """Call an indexer tool and return parsed JSON result."""
    try:
        client = await _get_indexer_client()
        tools = await client.get_tools()
        tool = next((t for t in tools if t.name == tool_name), None)

        if not tool:
            raise HTTPException(
                status_code=500,
                detail=f"Indexer tool '{tool_name}' not found"
            )

        result = await tool.ainvoke(kwargs)
        return json.loads(result) if isinstance(result, str) else result

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse tool result: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Invalid JSON response from indexer: {e}"
        )
    except Exception as e:
        logger.error(f"Error calling indexer tool '{tool_name}': {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Indexer error: {str(e)}"
        )


# ─── Request/Response Models ────────────────────────────────


class IndexRequest(BaseModel):
    """Request model for POST /api/index."""

    repository_url: str = Field(
        ..., description="Git repository URL to index"
    )
    repository_name: str | None = Field(
        None, description="Optional repository name (derived from URL if not provided)"
    )
    clear_graph: bool = Field(
        False, description="Clear existing graph before indexing"
    )
    run_enrichment: bool = Field(
        True, description="Run LLM enrichment (adds semantic layer)"
    )
    create_embeddings: bool = Field(
        True, description="Create vector embeddings for similarity search"
    )
    incremental: bool = Field(
        False, description="Perform incremental update (only changed files)"
    )


class IndexResponse(BaseModel):
    """Response model for POST /api/index."""

    job_id: str = Field(..., description="Job ID for tracking indexing progress")
    status: str = Field(..., description="Initial job status")
    message: str = Field(..., description="Human-readable status message")


class IndexStatusResponse(BaseModel):
    """Response model for GET /api/index/status/{job_id}."""

    job_id: str = Field(..., description="Job ID")
    status: str = Field(
        ..., description="Job status: pending, running, completed, failed"
    )
    progress: dict[str, Any] = Field(
        default_factory=dict, description="Progress information"
    )
    result: dict[str, Any] | None = Field(
        None, description="Final result (only present if status is completed)"
    )
    error: str | None = Field(
        None, description="Error message (only present if status is failed)"
    )


# ─── POST /api/index ────────────────────────────────────────


@router.post("/index", response_model=IndexResponse)
@observe(name="trigger_indexing", as_type="generation")
async def trigger_indexing(
    request: IndexRequest,
    background_tasks: BackgroundTasks,
) -> IndexResponse:
    """Trigger repository indexing.

    Supports both full indexing and incremental updates.

    Full indexing pipeline:
    1. Clone the repository
    2. Parse Python files into AST
    3. Extract entities and relationships
    4. Store in Neo4j knowledge graph
    5. (Optional) Run LLM enrichment for semantic layer
    6. (Optional) Create vector embeddings

    Incremental update:
    - Uses Strategy B fine-grained diffing
    - Only updates changed files
    - Preserves enrichment on unchanged entities

    Returns a job_id that can be used to track progress via
    GET /api/index/status/{job_id}.
    """
    logger.info(
        f"Indexing request: repo={request.repository_url}, "
        f"incremental={request.incremental}, enrichment={request.run_enrichment}"
    )

    try:
        if request.incremental:
            # For incremental updates, we need specific file paths
            # This is a simplified version - in production you might
            # detect changed files via git or file system watching
            raise HTTPException(
                status_code=400,
                detail="Incremental indexing requires file paths. "
                       "Use full indexing for now."
            )

        # Trigger full repository indexing
        result = await _call_indexer_tool(
            "index_repository",
            repository_url=request.repository_url,
            repository_name=request.repository_name or "",
            clear_graph=request.clear_graph,
            run_enrichment=request.run_enrichment,
            create_embeddings=request.create_embeddings,
        )

        job_id = result.get("job_id", "unknown")
        status = result.get("status", "pending")
        message = result.get("message", "Indexing job started")

        logger.info(f"Indexing job created: job_id={job_id}, status={status}")

        return IndexResponse(
            job_id=job_id,
            status=status,
            message=message,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Unexpected error triggering indexing: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


# ─── GET /api/index/status/{job_id} ─────────────────────────


@router.get("/index/status/{job_id}", response_model=IndexStatusResponse)
@observe(name="get_indexing_status", as_type="span")
async def get_indexing_status(job_id: str) -> IndexStatusResponse:
    """Get the status of an indexing job.

    Job statuses:
    - pending: Job is queued but not yet started
    - running: Job is currently executing
    - completed: Job finished successfully
    - failed: Job encountered an error

    For running jobs, the progress field includes:
    - current_phase: Which phase is running (parsing, storing, enriching, etc.)
    - files_processed: Number of files processed so far
    - total_files: Total number of files to process
    - percent_complete: Percentage (0-100)

    For completed jobs, the result field includes:
    - files_indexed: Total files processed
    - entities_created: Nodes created in the graph
    - relationships_created: Edges created in the graph
    - enrichment_coverage: Percentage of entities enriched
    - duration_seconds: Total job duration
    """
    logger.info(f"Checking status for job_id={job_id}")

    try:
        result = await _call_indexer_tool(
            "get_index_status",
            job_id=job_id,
        )

        status = result.get("status", "unknown")
        progress = result.get("progress", {})
        job_result = result.get("result")
        error = result.get("error")

        logger.info(f"Job {job_id} status: {status}")

        return IndexStatusResponse(
            job_id=job_id,
            status=status,
            progress=progress,
            result=job_result,
            error=error,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Unexpected error getting indexing status: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


# ─── GET /api/index/status (overview) ───────────────────────


@router.get("/index/status", response_model=dict)
@observe(name="get_indexing_overview", as_type="span")
async def get_indexing_overview() -> dict:
    """Get an overview of all indexing jobs and graph statistics.

    Returns:
    - active_jobs: List of currently running jobs
    - recent_jobs: Recently completed/failed jobs
    - graph_statistics: Node/edge counts, enrichment coverage
    """
    logger.info("Getting indexing overview")

    try:
        result = await _call_indexer_tool("get_index_status")
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Unexpected error getting indexing overview: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )
