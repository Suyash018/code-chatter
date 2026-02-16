"""
Entry point — delegates to the Indexer Agent server.

Usage:
    python main.py
    python -m src.agents.indexer.server
"""

import asyncio
from src.agents.indexer.server import index_repository

if __name__ == "__main__":
    asyncio.run(index_repository(skip_enrichment=False, clear_graph=True))
