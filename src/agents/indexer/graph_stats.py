"""
Graph Statistics and Queries

Read-only query methods for retrieving entity data, index state,
node/edge counts, enrichment stats, and validation warnings.
"""

import logging

logger = logging.getLogger("indexer-agent.graph_manager")


class StatsOperationsMixin:
    """Mixin providing query and statistics methods for the graph manager."""

    # ─── Query: Existing Entities for a File ───────────────

    async def get_file_entities(self, file_path: str) -> dict:
        """
        Get all entities for a file from the graph.
        Used by Strategy B to diff against new AST parse.

        Returns classes, top-level functions, methods, nested functions,
        and class attributes so the diff is comprehensive.
        """
        classes = await self._run(
            """
            MATCH (f:File {path: $path})-[:CONTAINS]->(c:Class)
            RETURN c.name as name, c.qualified_name as qualified_name,
                   c.content_hash as content_hash, labels(c) as labels
            """,
            {"path": file_path},
        )

        # Top-level functions (directly under file)
        functions = await self._run(
            """
            MATCH (f:File {path: $path})-[:CONTAINS]->(fn:Function)
            RETURN fn.name as name, fn.qualified_name as qualified_name,
                   fn.content_hash as content_hash, fn.is_method as is_method,
                   fn.is_nested as is_nested
            """,
            {"path": file_path},
        )

        # Methods inside classes
        methods = await self._run(
            """
            MATCH (f:File {path: $path})-[:CONTAINS]->(c:Class)-[:CONTAINS]->(m:Function)
            RETURN m.name as name, m.qualified_name as qualified_name,
                   m.content_hash as content_hash, c.name as class_name
            """,
            {"path": file_path},
        )

        # Nested functions (inside methods or top-level functions)
        nested_functions = await self._run(
            """
            MATCH (f:File {path: $path})-[:CONTAINS]->()-[:CONTAINS*1..2]->(n:Function {is_nested: true})
            RETURN n.name as name, n.qualified_name as qualified_name,
                   n.content_hash as content_hash
            """,
            {"path": file_path},
        )

        # Class attributes
        class_attributes = await self._run(
            """
            MATCH (f:File {path: $path})-[:CONTAINS]->(c:Class)-[:HAS_ATTRIBUTE]->(a:ClassAttribute)
            RETURN a.name as name, c.qualified_name as class_qname,
                   a.type_annotation as type_annotation,
                   a.default_value as default_value,
                   a.lineno as lineno
            """,
            {"path": file_path},
        )

        return {
            "classes": {c["qualified_name"]: c for c in classes},
            "functions": {f["qualified_name"]: f for f in functions},
            "methods": {m["qualified_name"]: m for m in methods},
            "nested_functions": {n["qualified_name"]: n for n in nested_functions},
            "class_attributes": class_attributes,
        }

    # ─── Index State ───────────────────────────────────────

    async def get_index_state(self) -> dict | None:
        """Get the current index state."""
        return await self._run_single("MATCH (s:IndexState) RETURN s { .* } as state")

    async def update_index_state(self, **kwargs) -> None:
        """Update the index state metadata node."""
        props = ", ".join(f"s.{k} = ${k}" for k in kwargs)
        await self._write(
            f"MERGE (s:IndexState) SET {props}, s.updated_at = datetime()",
            kwargs,
        )

    # ─── Statistics ────────────────────────────────────────

    async def get_node_counts(self) -> dict:
        """Get counts of each node type."""
        result = await self._run_single(
            """
            MATCH (n)
            WITH labels(n)[0] as label, count(n) as cnt
            RETURN collect({label: label, count: cnt}) as counts
            """
        )
        if result and result.get("counts"):
            return {item["label"]: item["count"] for item in result["counts"]}
        return {}

    async def get_edge_counts(self) -> dict:
        """Get counts of each relationship type."""
        result = await self._run_single(
            """
            MATCH ()-[r]->()
            WITH type(r) as rel_type, count(r) as cnt
            RETURN collect({type: rel_type, count: cnt}) as counts
            """
        )
        if result and result.get("counts"):
            return {item["type"]: item["count"] for item in result["counts"]}
        return {}

    async def get_enrichment_stats(self) -> dict:
        """Get enrichment coverage stats."""
        result = await self._run_single(
            """
            MATCH (f:Function)
            WITH count(f) as total,
                 count(CASE WHEN f.enrichment_hash IS NOT NULL THEN 1 END) as enriched,
                 count(CASE WHEN f.enrichment_hash <> f.content_hash THEN 1 END) as stale
            RETURN total, enriched, stale
            """
        )
        return result or {"total": 0, "enriched": 0, "stale": 0}

    async def get_validation_warnings(self) -> list[str]:
        """Run validation checks and return warnings."""
        warnings = []

        # Orphan nodes
        orphans = await self._run(
            """
            MATCH (n)
            WHERE (n:Function OR n:Class) AND NOT ()-[:CONTAINS]->(n)
            RETURN n.qualified_name as qname
            LIMIT 20
            """
        )
        if orphans:
            warnings.append(f"Found {len(orphans)} orphan nodes: {[o['qname'] for o in orphans]}")

        # Stale enrichment
        stale = await self._run(
            """
            MATCH (n:Function)
            WHERE n.enrichment_hash IS NOT NULL AND n.enrichment_hash <> n.content_hash
            RETURN count(n) as count
            """
        )
        if stale and stale[0]["count"] > 0:
            warnings.append(f"{stale[0]['count']} nodes have stale enrichment")

        return warnings
