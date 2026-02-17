"""
Graph Manager Base

Core Neo4j connection management, query execution helpers,
schema creation, and graph clearing.
"""

import logging

from src.shared.database import Neo4jHandler

logger = logging.getLogger("indexer-agent.graph_manager")


class GraphManagerBase:
    """
    Base class for the Neo4j graph manager.

    Provides connection lifecycle, low-level query helpers,
    schema bootstrapping, and full graph clearing.
    """

    def __init__(self, handler: Neo4jHandler):
        self._handler = handler

    async def connect(self) -> None:
        """Ensure the underlying handler is connected."""
        await self._handler.connect()
        logger.info("Neo4jGraphManager ready (via Neo4jHandler)")

    async def close(self) -> None:
        """Close the underlying handler connection."""
        await self._handler.close()

    async def _run(self, query: str, params: dict | None = None) -> list[dict]:
        """Execute a Cypher query and return results."""
        return await self._handler.run(query, params)

    async def _run_single(self, query: str, params: dict | None = None) -> dict | None:
        """Execute a Cypher query and return first result or None."""
        return await self._handler.run_single(query, params)

    async def _write(self, query: str, params: dict | None = None) -> None:
        """Execute a write transaction."""
        await self._handler.write(query, params)

    # ─── Schema ────────────────────────────────────────────

    async def ensure_schema(self) -> None:
        """Create all constraints and indexes if they don't exist."""

        constraints = [
            "CREATE CONSTRAINT file_path IF NOT EXISTS FOR (f:File) REQUIRE f.path IS UNIQUE",
            "CREATE CONSTRAINT class_qname IF NOT EXISTS FOR (c:Class) REQUIRE c.qualified_name IS UNIQUE",
            "CREATE CONSTRAINT func_qname IF NOT EXISTS FOR (f:Function) REQUIRE f.qualified_name IS UNIQUE",
            "CREATE CONSTRAINT module_qname IF NOT EXISTS FOR (m:Module) REQUIRE m.qualified_name IS UNIQUE",
            "CREATE CONSTRAINT pattern_name IF NOT EXISTS FOR (p:DesignPattern) REQUIRE p.name IS UNIQUE",
            "CREATE CONSTRAINT concept_name IF NOT EXISTS FOR (c:DomainConcept) REQUIRE c.name IS UNIQUE",
            "CREATE CONSTRAINT cache_hash IF NOT EXISTS FOR (c:EnrichmentCache) REQUIRE c.content_hash IS UNIQUE",
        ]

        indexes = [
            "CREATE INDEX func_name IF NOT EXISTS FOR (f:Function) ON (f.name)",
            "CREATE INDEX class_name IF NOT EXISTS FOR (c:Class) ON (c.name)",
            "CREATE INDEX decorator_name IF NOT EXISTS FOR (d:Decorator) ON (d.name)",
            "CREATE INDEX class_attr_name IF NOT EXISTS FOR (a:ClassAttribute) ON (a.name)",
        ]

        # Vector indexes for hybrid search (requires Neo4j 5.11+)
        vector_indexes = [
            """CREATE VECTOR INDEX func_embedding IF NOT EXISTS
               FOR (n:Function) ON (n.embedding)
               OPTIONS {indexConfig: {
                 `vector.dimensions`: 3072,
                 `vector.similarity_function`: 'cosine'
               }}""",
            """CREATE VECTOR INDEX class_embedding IF NOT EXISTS
               FOR (n:Class) ON (n.embedding)
               OPTIONS {indexConfig: {
                 `vector.dimensions`: 3072,
                 `vector.similarity_function`: 'cosine'
               }}""",
        ]

        for stmt in constraints + indexes:
            try:
                await self._write(stmt)
            except Exception as e:
                logger.debug(f"Schema statement skipped: {e}")

        for stmt in vector_indexes:
            try:
                await self._write(stmt)
            except Exception as e:
                logger.warning(f"Vector index creation skipped (may need Neo4j 5.11+): {e}")

        logger.info("Neo4j schema ensured")

    async def clear_all(self) -> None:
        """Delete all nodes and relationships. Used for full re-index."""
        await self._write("MATCH (n) DETACH DELETE n")
        logger.warning("Cleared entire graph")
