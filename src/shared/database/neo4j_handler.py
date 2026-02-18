"""
Neo4j Connection Handler

Centralised Neo4j driver management.
Reads credentials from environment variables and exposes an async driver
that can be shared across the application (graph manager, enricher, etc.).
"""

import os
import logging
from typing import Any

from dotenv import load_dotenv
from neo4j import AsyncGraphDatabase, AsyncDriver

load_dotenv()

logger = logging.getLogger("graphical-rag.neo4j_handler")


class Neo4jHandler:
    """
    Manages a single async Neo4j driver backed by .env configuration.

    Usage
    -----
    handler = Neo4jHandler()          # reads from .env
    await handler.connect()
    results = await handler.run("MATCH (n) RETURN n LIMIT 5")
    await handler.close()

    The handler can also be used as an async context-manager:

        async with Neo4jHandler() as handler:
            await handler.run(...)
    """

    def __init__(
        self,
        uri: str | None = None,
        username: str | None = None,
        password: str | None = None,
        database: str | None = None,
    ):
        self._uri = uri or os.getenv("NEO4J_URI")
        self._username = username or os.getenv("NEO4J_USERNAME")
        self._password = password or os.getenv("NEO4J_PASSWORD")
        self._database = database or os.getenv("NEO4J_DATABASE", "neo4j")
        self._driver: AsyncDriver | None = None

        if not self._uri:
            raise ValueError("NEO4J_URI is not set (env or argument)")
        if not self._username:
            raise ValueError("NEO4J_USERNAME is not set (env or argument)")
        if not self._password:
            raise ValueError("NEO4J_PASSWORD is not set (env or argument)")

    # ─── Lifecycle ──────────────────────────────────────────

    async def connect(self) -> "Neo4jHandler":
        """Create the async driver and verify connectivity.

        Returns:
            Self for method chaining.

        Raises:
            Exception: If Neo4j connection cannot be established or verified.
        """
        if self._driver is not None:
            return self

        self._driver = AsyncGraphDatabase.driver(
            self._uri, auth=(self._username, self._password)
        )
        try:
            await self._driver.verify_connectivity()
            logger.info("Connected to Neo4j at %s (db=%s)", self._uri, self._database)
        except Exception:
            logger.error("Failed to connect to Neo4j at %s", self._uri)
            raise
        return self

    async def close(self) -> None:
        """Close the underlying driver."""
        if self._driver:
            await self._driver.close()
            self._driver = None
            logger.info("Neo4j connection closed")

    async def __aenter__(self) -> "Neo4jHandler":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    # ─── Properties ─────────────────────────────────────────

    @property
    def driver(self) -> AsyncDriver:
        """Return the raw async driver (for code that needs direct access).

        Returns:
            Neo4j AsyncDriver instance.

        Raises:
            RuntimeError: If handler is not connected (call connect() first).
        """
        if self._driver is None:
            raise RuntimeError("Neo4jHandler is not connected — call connect() first")
        return self._driver

    @property
    def database(self) -> str:
        """Return the configured database name."""
        return self._database

    @property
    def uri(self) -> str:
        """Return the configured Neo4j URI."""
        return self._uri

    @property
    def username(self) -> str:
        """Return the configured Neo4j username."""
        return self._username

    # ─── Convenience Query Helpers ──────────────────────────

    async def run(self, query: str, params: dict[str, Any] | None = None) -> list[dict]:
        """Execute a Cypher query and return all results as dicts.

        Args:
            query: Cypher query string.
            params: Optional query parameters.

        Returns:
            List of result records as dictionaries.

        Raises:
            RuntimeError: If handler is not connected (call connect() first).
            Exception: If query execution fails (invalid syntax, database error, etc.).
        """
        async with self.driver.session(database=self._database) as session:
            result = await session.run(query, params or {})
            return [record.data() async for record in result]

    async def run_single(self, query: str, params: dict[str, Any] | None = None) -> dict | None:
        """Execute a Cypher query and return the first result, or None.

        Args:
            query: Cypher query string.
            params: Optional query parameters.

        Returns:
            First result record as a dict, or None if no results.

        Raises:
            RuntimeError: If handler is not connected (call connect() first).
            Exception: If query execution fails (invalid syntax, database error, etc.).
        """
        results = await self.run(query, params)
        return results[0] if results else None

    async def write(self, query: str, params: dict[str, Any] | None = None) -> None:
        """Execute a write transaction (no return value).

        Args:
            query: Cypher write query (CREATE, MERGE, SET, DELETE, etc.).
            params: Optional query parameters.

        Raises:
            RuntimeError: If handler is not connected (call connect() first).
            Exception: If write operation fails (constraint violations, syntax errors, etc.).
        """
        async with self.driver.session(database=self._database) as session:
            await session.run(query, params or {})

    async def verify(self) -> bool:
        """Quick health-check: returns True if the database is reachable."""
        try:
            await self._driver.verify_connectivity()
            return True
        except Exception:
            return False
