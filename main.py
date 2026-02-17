"""
Entry point — triggers full repository indexing directly.

This bypasses the MCP server and runs the indexing pipeline as a
standalone async operation.  Useful for bootstrapping the graph.

Usage:
    python main.py

For MCP server mode (stdio transport):
    python -m src.agents.indexer.server
"""

import asyncio

from src.agents.indexer.server import _run_index_repository_job, _create_job

REPO_URL = "https://github.com/tiangolo/fastapi.git"
REPO_BRANCH = "master"


async def main() -> None:
    job = _create_job("index_repository")
    await _run_index_repository_job(
        job,
        repo_url=REPO_URL,
        branch=REPO_BRANCH,
        skip_enrichment=False,
        clear_graph=True,
        max_workers=10,
    )
    if job.status == "completed":
        print("Indexing complete:", job.result)
    else:
        print("Indexing failed:", job.error)


if __name__ == "__main__":
    asyncio.run(main())
