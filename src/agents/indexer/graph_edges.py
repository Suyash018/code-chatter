"""
Graph Edge Operations

CRUD operations for relationship types in the knowledge graph:
Decorator edges, Import edges, and cross-file relationship resolution.
"""

import logging

from src.agents.indexer.models import path_to_module

logger = logging.getLogger("indexer-agent.graph_manager")


class EdgeOperationsMixin:
    """Mixin providing edge CRUD and relationship resolution for the graph manager."""

    # ─── Decorator Edges ───────────────────────────────────

    async def create_decorator_edge(
        self,
        entity_qname: str,
        decorator: dict,
        entity_label: str = "Function",
    ) -> None:
        """Create a Decorator node and DECORATED_BY edge."""
        await self._write(
            f"""
            MATCH (e:{entity_label} {{qualified_name: $qname}})
            MERGE (d:Decorator {{name: $dec_name}})
            ON CREATE SET d.arguments = $dec_args
            MERGE (e)-[:DECORATED_BY]->(d)
            """,
            {
                "qname": entity_qname,
                "dec_name": decorator["name"],
                "dec_args": decorator.get("arguments"),
            },
        )

    async def delete_decorator_edges(self, entity_qname: str) -> None:
        """Delete all DECORATED_BY edges from an entity."""
        await self._write(
            """
            MATCH (e {qualified_name: $qname})-[r:DECORATED_BY]->()
            DELETE r
            """,
            {"qname": entity_qname},
        )

    # ─── Import Edges ──────────────────────────────────────

    async def create_import_edge(self, file_path: str, imp: dict) -> None:
        """
        Create import relationship between modules.

        Handles new parser flags:
        - is_type_checking: marks edge, skip for type-checking-only
        - is_conditional: stores condition expression
        - is_try_except / is_fallback: marks optional dependencies
        """
        source_module = imp.get("source_module", "")
        target_module = imp["module"]

        if not target_module:
            return

        # Store all import flags on the edge
        is_type_checking = imp.get("is_type_checking", False)
        is_conditional = imp.get("is_conditional", False)
        is_try_except = imp.get("is_try_except", False)
        is_fallback = imp.get("is_fallback", False)

        await self._write(
            """
            MERGE (src:Module {qualified_name: $src_mod})
            MERGE (tgt:Module {qualified_name: $tgt_mod})
            MERGE (src)-[r:IMPORTS]->(tgt)
            SET r.names = $names,
                r.aliases = $aliases,
                r.is_relative = $is_relative,
                r.is_type_checking = $is_type_checking,
                r.is_conditional = $is_conditional,
                r.condition = $condition,
                r.is_try_except = $is_try_except,
                r.is_fallback = $is_fallback
            """,
            {
                "src_mod": source_module,
                "tgt_mod": target_module,
                "names": imp.get("names", []),
                "aliases": str(imp.get("aliases", {})),
                "is_relative": imp.get("is_relative", False),
                "is_type_checking": is_type_checking,
                "is_conditional": is_conditional,
                "condition": imp.get("condition"),
                "is_try_except": is_try_except,
                "is_fallback": is_fallback,
            },
        )

    async def delete_imports_for_file(self, file_path: str) -> None:
        """Delete all import edges originating from a file's module."""
        module_name = path_to_module(file_path)
        await self._write(
            """
            MATCH (m:Module {qualified_name: $mod})-[r:IMPORTS]->()
            DELETE r
            """,
            {"mod": module_name},
        )

    # ─── Cross-file Relationship Resolution ────────────────

    async def resolve_all_relationships(self) -> int:
        """
        Resolve CALLS edges by matching call names to Function nodes.
        Also resolves INHERITS_FROM for unresolved base classes.
        Returns the number of edges created.

        Uses a 3-pass strategy to avoid false edges from ambiguous names:
          1. Same-file: caller and callee share the same File ancestor
          2. Import-based: caller's module imports the callee's module
          3. Unique global: callee name is globally unique (one function)
        """
        edge_count = 0

        # Pass 1: Same-file call resolution (strongest signal)
        result = await self._run(
            """
            MATCH (caller:Function)
            WHERE caller._calls IS NOT NULL AND size(caller._calls) > 0
            MATCH (f:File)-[:CONTAINS*1..3]->(caller)
            WITH caller, f
            UNWIND caller._calls AS callee_name
            MATCH (f)-[:CONTAINS*1..3]->(callee:Function {name: callee_name})
            WHERE caller <> callee
            MERGE (caller)-[:CALLS]->(callee)
            RETURN count(*) as created
            """
        )
        if result:
            edge_count += result[0].get("created", 0)

        # Pass 2: Cross-file via import relationships
        result = await self._run(
            """
            MATCH (caller:Function)
            WHERE caller._calls IS NOT NULL AND size(caller._calls) > 0
            MATCH (f1:File)-[:CONTAINS*1..3]->(caller)
            MATCH (f1)-[:DEFINES_MODULE]->(src:Module)-[:IMPORTS]->(tgt:Module)<-[:DEFINES_MODULE]-(f2:File)
            WITH caller, f2
            UNWIND caller._calls AS callee_name
            MATCH (f2)-[:CONTAINS*1..3]->(callee:Function {name: callee_name})
            WHERE caller <> callee AND NOT (caller)-[:CALLS]->(callee)
            MERGE (caller)-[:CALLS]->(callee)
            RETURN count(*) as created
            """
        )
        if result:
            edge_count += result[0].get("created", 0)

        # Pass 3: Globally unique name match (skip ambiguous names)
        result = await self._run(
            """
            MATCH (caller:Function)
            WHERE caller._calls IS NOT NULL AND size(caller._calls) > 0
            UNWIND caller._calls AS callee_name
            WITH caller, callee_name
            WHERE NOT (caller)-[:CALLS]->(:Function {name: callee_name})
            MATCH (callee:Function {name: callee_name})
            WHERE caller <> callee
            WITH caller, callee_name, collect(DISTINCT callee) AS candidates
            WHERE size(candidates) = 1
            WITH caller, candidates[0] AS callee
            MERGE (caller)-[:CALLS]->(callee)
            RETURN count(*) as created
            """
        )
        if result:
            edge_count += result[0].get("created", 0)

        # Resolve unresolved base classes
        result = await self._run(
            """
            MATCH (c:Class)-[:INHERITS_FROM]->(base:Class {_unresolved: true})
            MATCH (resolved:Class {name: base.name})
            WHERE resolved._unresolved IS NULL
            WITH c, base, resolved
            MERGE (c)-[:INHERITS_FROM]->(resolved)
            WITH base
            WHERE NOT ()-[:INHERITS_FROM]->(base)
            DETACH DELETE base
            RETURN count(*) as resolved
            """
        )
        if result:
            edge_count += result[0].get("resolved", 0)

        logger.info(f"Resolved {edge_count} cross-file relationships")
        return edge_count

    async def resolve_calls_for_function(self, qualified_name: str, calls: list[str]) -> None:
        """Resolve CALLS edges for a specific function using same-file, import, and unique-name strategies."""
        await self._write(
            "MATCH (f:Function {qualified_name: $qname})-[r:CALLS]->() DELETE r",
            {"qname": qualified_name},
        )

        if not calls:
            return

        # Same-file matches
        await self._write(
            """
            MATCH (caller:Function {qualified_name: $qname})
            MATCH (f:File)-[:CONTAINS*1..3]->(caller)
            WITH caller, f, $calls AS call_list
            UNWIND call_list AS callee_name
            MATCH (f)-[:CONTAINS*1..3]->(callee:Function {name: callee_name})
            WHERE caller <> callee
            MERGE (caller)-[:CALLS]->(callee)
            """,
            {"qname": qualified_name, "calls": calls},
        )

        # Import-based cross-file matches
        await self._write(
            """
            MATCH (caller:Function {qualified_name: $qname})
            MATCH (f1:File)-[:CONTAINS*1..3]->(caller)
            MATCH (f1)-[:DEFINES_MODULE]->(src:Module)-[:IMPORTS]->(tgt:Module)<-[:DEFINES_MODULE]-(f2:File)
            WITH caller, f2, $calls AS call_list
            UNWIND call_list AS callee_name
            MATCH (f2)-[:CONTAINS*1..3]->(callee:Function {name: callee_name})
            WHERE caller <> callee AND NOT (caller)-[:CALLS]->(callee)
            MERGE (caller)-[:CALLS]->(callee)
            """,
            {"qname": qualified_name, "calls": calls},
        )

        # Unique global name matches for remaining unresolved calls
        await self._write(
            """
            MATCH (caller:Function {qualified_name: $qname})
            WITH caller, $calls AS call_list
            UNWIND call_list AS callee_name
            WITH caller, callee_name
            WHERE NOT (caller)-[:CALLS]->(:Function {name: callee_name})
            MATCH (callee:Function {name: callee_name})
            WHERE caller <> callee
            WITH caller, callee_name, collect(DISTINCT callee) AS candidates
            WHERE size(candidates) = 1
            WITH caller, candidates[0] AS callee
            MERGE (caller)-[:CALLS]->(callee)
            """,
            {"qname": qualified_name, "calls": calls},
        )
