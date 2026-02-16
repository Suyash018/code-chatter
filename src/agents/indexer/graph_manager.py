"""
Neo4j Graph Manager

Handles all interactions with the Neo4j knowledge graph:
- Schema creation (constraints, indexes)
- Node CRUD operations
- Edge CRUD operations
- Cross-file relationship resolution
- Embedding storage and vector search
- Index state management
- Validation queries
"""

import json as _json
import logging

from src.shared.database import Neo4jHandler
from src.agents.indexer.ast_parser import path_to_module

logger = logging.getLogger("indexer-agent.graph_manager")


def _build_embedding_text(node: dict) -> str:
    """Build a text representation of a graph node for vector embedding.

    Combines the most semantically meaningful properties so that
    similarity search finds nodes by meaning, not just name.
    """
    parts = []

    label = node.get("label", "Entity")
    name = node.get("name", "")
    parts.append(f"{label}: {name}")

    if node.get("purpose"):
        parts.append(f"Purpose: {node['purpose']}")

    if node.get("summary"):
        parts.append(f"Summary: {node['summary']}")

    if node.get("docstring"):
        parts.append(f"Docstring: {node['docstring'][:500]}")

    concepts = node.get("domain_concepts")
    if concepts:
        if isinstance(concepts, list):
            parts.append(f"Concepts: {', '.join(concepts)}")
        else:
            parts.append(f"Concepts: {concepts}")

    return "\n".join(parts)


class Neo4jGraphManager:
    """
    Manages the Neo4j knowledge graph for the codebase.

    Provides typed methods for every graph operation the indexer needs.
    All methods are async for non-blocking I/O.

    Accepts a shared ``Neo4jHandler`` so that the driver lifecycle is
    managed centrally rather than per-manager.
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

    # ─── File Nodes ────────────────────────────────────────

    async def create_file_node(self, file_path: str, content_hash: str) -> None:
        """Create or update a File node."""
        module_name = path_to_module(file_path)

        await self._write(
            """
            MERGE (f:File {path: $path})
            SET f.name = $name,
                f.content_hash = $hash,
                f.module_name = $module,
                f.indexed_at = datetime()
            WITH f
            MERGE (m:Module {qualified_name: $module})
            MERGE (f)-[:DEFINES_MODULE]->(m)
            """,
            {
                "path": file_path,
                "name": file_path.replace("\\", "/").split("/")[-1],
                "hash": content_hash,
                "module": module_name,
            },
        )

    async def delete_file_subgraph(self, file_path: str) -> dict:
        """
        Delete a file and everything it contains.
        Returns counts of deleted entities.
        """
        # Count before deletion
        counts = await self._run_single(
            """
            MATCH (f:File {path: $path})
            OPTIONAL MATCH (f)-[:CONTAINS]->(entity)
            OPTIONAL MATCH (entity)-[:CONTAINS]->(child)
            RETURN count(DISTINCT entity) as entities, count(DISTINCT child) as children
            """,
            {"path": file_path},
        )

        # Delete nested functions inside methods inside classes (3 levels deep)
        await self._write(
            """
            MATCH (f:File {path: $path})-[:CONTAINS]->(c)-[:CONTAINS]->(m:Function)-[:CONTAINS]->(nested:Function)
            OPTIONAL MATCH (nested)-[:HAS_PARAMETER]->(p:Parameter)
            DETACH DELETE p, nested
            """,
            {"path": file_path},
        )

        # Delete class attributes
        await self._write(
            """
            MATCH (f:File {path: $path})-[:CONTAINS]->(c:Class)-[:HAS_ATTRIBUTE]->(a:ClassAttribute)
            DETACH DELETE a
            """,
            {"path": file_path},
        )

        # Delete parameters of methods/functions
        await self._write(
            """
            MATCH (f:File {path: $path})-[:CONTAINS]->(entity)-[:CONTAINS]->(m:Function)-[:HAS_PARAMETER]->(p:Parameter)
            DETACH DELETE p
            """,
            {"path": file_path},
        )
        await self._write(
            """
            MATCH (f:File {path: $path})-[:CONTAINS]->(fn:Function)-[:HAS_PARAMETER]->(p:Parameter)
            DETACH DELETE p
            """,
            {"path": file_path},
        )

        # Delete children of entities (methods of classes, nested functions)
        await self._write(
            """
            MATCH (f:File {path: $path})-[:CONTAINS]->(entity)-[:CONTAINS]->(child)
            DETACH DELETE child
            """,
            {"path": file_path},
        )

        # Delete direct children of file
        await self._write(
            """
            MATCH (f:File {path: $path})-[:CONTAINS]->(entity)
            DETACH DELETE entity
            """,
            {"path": file_path},
        )

        # Delete the file node itself
        await self._write(
            """
            MATCH (f:File {path: $path})
            OPTIONAL MATCH (f)-[:DEFINES_MODULE]->(m:Module)
            DETACH DELETE f
            """,
            {"path": file_path},
        )

        return {
            "deleted_entities": counts["entities"] if counts else 0,
            "deleted_children": counts["children"] if counts else 0,
        }

    # ─── Class Nodes ───────────────────────────────────────

    async def create_class_node(self, file_path: str, cls: dict) -> None:
        """Create a Class node and link it to its File."""
        await self._write(
            """
            MATCH (f:File {path: $file_path})
            MERGE (c:Class {qualified_name: $qname})
            SET c.name = $name,
                c.source = $source,
                c.content_hash = $hash,
                c.lineno_start = $start,
                c.lineno_end = $end,
                c.docstring = $docstring
            MERGE (f)-[:CONTAINS]->(c)
            """,
            {
                "file_path": file_path,
                "qname": cls["qualified_name"],
                "name": cls["name"],
                "source": cls["source"],
                "hash": cls["content_hash"],
                "start": cls["lineno_start"],
                "end": cls["lineno_end"],
                "docstring": cls.get("docstring", ""),
            },
        )

        # Decorators
        for dec in cls.get("decorators", []):
            await self.create_decorator_edge(cls["qualified_name"], dec, "Class")

        # Inheritance
        for base in cls.get("bases", []):
            await self._write(
                """
                MATCH (c:Class {qualified_name: $qname})
                MERGE (base:Class {name: $base_name})
                ON CREATE SET base.qualified_name = $base_name,
                             base._unresolved = true
                MERGE (c)-[:INHERITS_FROM]->(base)
                """,
                {"qname": cls["qualified_name"], "base_name": base},
            )

    async def update_class_node(self, cls: dict) -> None:
        """Update an existing Class node's properties in place."""
        await self._write(
            """
            MATCH (c:Class {qualified_name: $qname})
            SET c.source = $source,
                c.content_hash = $hash,
                c.lineno_start = $start,
                c.lineno_end = $end,
                c.docstring = $docstring
            """,
            {
                "qname": cls["qualified_name"],
                "source": cls["source"],
                "hash": cls["content_hash"],
                "start": cls["lineno_start"],
                "end": cls["lineno_end"],
                "docstring": cls.get("docstring", ""),
            },
        )

    async def delete_class_node(self, qualified_name: str) -> None:
        """Delete a class, all its methods, nested functions, class attributes, and parameters."""
        # Delete nested functions inside methods (and their parameters)
        await self._write(
            """
            MATCH (c:Class {qualified_name: $qname})-[:CONTAINS]->(m:Function)-[:CONTAINS]->(nested:Function)
            OPTIONAL MATCH (nested)-[:HAS_PARAMETER]->(p:Parameter)
            DETACH DELETE p, nested
            """,
            {"qname": qualified_name},
        )
        # Delete methods' parameters
        await self._write(
            """
            MATCH (c:Class {qualified_name: $qname})-[:CONTAINS]->(m:Function)-[:HAS_PARAMETER]->(p)
            DETACH DELETE p
            """,
            {"qname": qualified_name},
        )
        # Delete methods
        await self._write(
            """
            MATCH (c:Class {qualified_name: $qname})-[:CONTAINS]->(m:Function)
            DETACH DELETE m
            """,
            {"qname": qualified_name},
        )
        # Delete class attributes
        await self._write(
            """
            MATCH (c:Class {qualified_name: $qname})-[:HAS_ATTRIBUTE]->(a:ClassAttribute)
            DETACH DELETE a
            """,
            {"qname": qualified_name},
        )
        # Delete class
        await self._write(
            "MATCH (c:Class {qualified_name: $qname}) DETACH DELETE c",
            {"qname": qualified_name},
        )

    # ─── Class Attribute Nodes ─────────────────────────────

    async def create_class_attribute_node(
        self, class_qname: str, attr: dict
    ) -> None:
        """
        Create a ClassAttribute node linked to its Class via HAS_ATTRIBUTE.

        These represent dataclass fields, Pydantic model fields,
        and plain class-level assignments (AnnAssign / Assign).
        """
        await self._write(
            """
            MATCH (c:Class {qualified_name: $class_qname})
            CREATE (a:ClassAttribute {
                name: $name,
                type_annotation: $type_ann,
                default_value: $default_val,
                lineno: $lineno
            })
            CREATE (c)-[:HAS_ATTRIBUTE]->(a)
            """,
            {
                "class_qname": class_qname,
                "name": attr["name"],
                "type_ann": attr.get("type_annotation"),
                "default_val": attr.get("default_value"),
                "lineno": attr.get("lineno"),
            },
        )

    async def delete_class_attributes(self, class_qname: str) -> None:
        """Delete all ClassAttribute nodes for a class."""
        await self._write(
            """
            MATCH (c:Class {qualified_name: $qname})-[:HAS_ATTRIBUTE]->(a:ClassAttribute)
            DETACH DELETE a
            """,
            {"qname": class_qname},
        )

    # ─── Function Nodes ────────────────────────────────────

    async def create_function_node(
        self,
        file_path: str,
        func: dict,
        parent_class: str | None = None,
        parent_function: str | None = None,
    ) -> None:
        """
        Create a Function node and link it to its parent.

        Parents can be:
        - File (top-level function)
        - Class (method)
        - Function (nested function)
        """
        calls = func.get("calls", [])
        is_nested = func.get("is_nested", False)

        if parent_function:
            # Nested function — link to parent function
            await self._write(
                """
                MATCH (parent:Function {qualified_name: $parent_qname})
                MERGE (fn:Function {qualified_name: $qname})
                SET fn.name = $name,
                    fn.source = $source,
                    fn.content_hash = $hash,
                    fn.lineno_start = $start,
                    fn.lineno_end = $end,
                    fn.is_async = $is_async,
                    fn.is_method = false,
                    fn.is_nested = true,
                    fn.docstring = $docstring,
                    fn.return_annotation = $return_ann,
                    fn._calls = $calls
                MERGE (parent)-[:CONTAINS]->(fn)
                """,
                {
                    "parent_qname": parent_function,
                    "qname": func["qualified_name"],
                    "name": func["name"],
                    "source": func["source"],
                    "hash": func["content_hash"],
                    "start": func["lineno_start"],
                    "end": func["lineno_end"],
                    "is_async": func.get("is_async", False),
                    "docstring": func.get("docstring", ""),
                    "return_ann": func.get("return_annotation"),
                    "calls": calls,
                },
            )
        elif parent_class:
            # Method — link to class
            await self._write(
                """
                MATCH (f:File {path: $file_path})-[:CONTAINS]->(c:Class {name: $class_name})
                MERGE (fn:Function {qualified_name: $qname})
                SET fn.name = $name,
                    fn.source = $source,
                    fn.content_hash = $hash,
                    fn.lineno_start = $start,
                    fn.lineno_end = $end,
                    fn.is_async = $is_async,
                    fn.is_method = true,
                    fn.is_nested = false,
                    fn.docstring = $docstring,
                    fn.return_annotation = $return_ann,
                    fn._calls = $calls
                MERGE (c)-[:CONTAINS]->(fn)
                """,
                {
                    "file_path": file_path,
                    "class_name": parent_class,
                    "qname": func["qualified_name"],
                    "name": func["name"],
                    "source": func["source"],
                    "hash": func["content_hash"],
                    "start": func["lineno_start"],
                    "end": func["lineno_end"],
                    "is_async": func.get("is_async", False),
                    "docstring": func.get("docstring", ""),
                    "return_ann": func.get("return_annotation"),
                    "calls": calls,
                },
            )
        else:
            # Top-level function — link to file
            await self._write(
                """
                MATCH (f:File {path: $file_path})
                MERGE (fn:Function {qualified_name: $qname})
                SET fn.name = $name,
                    fn.source = $source,
                    fn.content_hash = $hash,
                    fn.lineno_start = $start,
                    fn.lineno_end = $end,
                    fn.is_async = $is_async,
                    fn.is_method = false,
                    fn.is_nested = $is_nested,
                    fn.docstring = $docstring,
                    fn.return_annotation = $return_ann,
                    fn._calls = $calls
                MERGE (f)-[:CONTAINS]->(fn)
                """,
                {
                    "file_path": file_path,
                    "qname": func["qualified_name"],
                    "name": func["name"],
                    "source": func["source"],
                    "hash": func["content_hash"],
                    "start": func["lineno_start"],
                    "end": func["lineno_end"],
                    "is_async": func.get("is_async", False),
                    "is_nested": is_nested,
                    "docstring": func.get("docstring", ""),
                    "return_ann": func.get("return_annotation"),
                    "calls": calls,
                },
            )

    async def update_function_node(self, func: dict) -> None:
        """Update an existing Function node's properties in place."""
        calls = func.get("calls", [])
        await self._write(
            """
            MATCH (fn:Function {qualified_name: $qname})
            SET fn.source = $source,
                fn.content_hash = $hash,
                fn.lineno_start = $start,
                fn.lineno_end = $end,
                fn.is_async = $is_async,
                fn.is_nested = $is_nested,
                fn.docstring = $docstring,
                fn.return_annotation = $return_ann,
                fn._calls = $calls
            """,
            {
                "qname": func["qualified_name"],
                "source": func["source"],
                "hash": func["content_hash"],
                "start": func["lineno_start"],
                "end": func["lineno_end"],
                "is_async": func.get("is_async", False),
                "is_nested": func.get("is_nested", False),
                "docstring": func.get("docstring", ""),
                "return_ann": func.get("return_annotation"),
                "calls": calls,
            },
        )

    async def delete_function_node(self, qualified_name: str) -> None:
        """Delete a function, its nested functions, and parameters."""
        # Delete nested functions' parameters first
        await self._write(
            """
            MATCH (fn:Function {qualified_name: $qname})-[:CONTAINS]->(nested:Function)-[:HAS_PARAMETER]->(p)
            DETACH DELETE p
            """,
            {"qname": qualified_name},
        )
        # Delete nested functions
        await self._write(
            """
            MATCH (fn:Function {qualified_name: $qname})-[:CONTAINS]->(nested:Function)
            DETACH DELETE nested
            """,
            {"qname": qualified_name},
        )
        # Delete parameters
        await self._write(
            """
            MATCH (fn:Function {qualified_name: $qname})-[:HAS_PARAMETER]->(p)
            DETACH DELETE p
            """,
            {"qname": qualified_name},
        )
        # Delete function itself
        await self._write(
            "MATCH (fn:Function {qualified_name: $qname}) DETACH DELETE fn",
            {"qname": qualified_name},
        )

    # ─── Parameter Nodes ───────────────────────────────────

    async def create_parameter_node(
        self, function_qname: str, param: dict
    ) -> None:
        """Create a Parameter node linked to its Function."""
        await self._write(
            """
            MATCH (fn:Function {qualified_name: $func_qname})
            CREATE (p:Parameter {
                name: $name,
                type_annotation: $type_ann,
                default_value: $default_val,
                position: $position,
                kind: $kind
            })
            CREATE (fn)-[:HAS_PARAMETER]->(p)
            """,
            {
                "func_qname": function_qname,
                "name": param["name"],
                "type_ann": param.get("type_annotation"),
                "default_val": param.get("default_value"),
                "position": param.get("position", 0),
                "kind": param.get("kind", "positional_or_keyword"),
            },
        )

    async def delete_parameters(self, function_qname: str) -> None:
        """Delete all parameter nodes for a function."""
        await self._write(
            """
            MATCH (fn:Function {qualified_name: $qname})-[:HAS_PARAMETER]->(p)
            DETACH DELETE p
            """,
            {"qname": function_qname},
        )

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

    # ─── Enrichment ────────────────────────────────────────

    async def set_enrichment(
        self, qualified_name: str, enrichment: dict, entity_type: str = "function"
    ) -> None:
        """
        Store LLM enrichment on a node.

        Stores common fields (purpose, summary, patterns, complexity, concepts)
        plus entity-type-specific fields:
        - function: side_effects, parameters_explained
        - class: role, key_methods
        """
        # Common fields
        await self._write(
            """
            MATCH (n {qualified_name: $qname})
            SET n.purpose = $purpose,
                n.summary = $summary,
                n.design_patterns = $patterns,
                n.complexity = $complexity,
                n.domain_concepts = $concepts,
                n.enriched_at = datetime(),
                n.enrichment_hash = n.content_hash
            """,
            {
                "qname": qualified_name,
                "purpose": enrichment.get("purpose", ""),
                "summary": enrichment.get("summary", ""),
                "patterns": enrichment.get("design_patterns", []),
                "complexity": enrichment.get("complexity", "unknown"),
                "concepts": enrichment.get("domain_concepts", []),
            },
        )

        # Entity-type-specific fields
        if entity_type == "function":
            await self._write(
                """
                MATCH (n:Function {qualified_name: $qname})
                SET n.side_effects = $side_effects,
                    n.parameters_explained = $params_explained
                """,
                {
                    "qname": qualified_name,
                    "side_effects": enrichment.get("side_effects", []),
                    "params_explained": _json.dumps(
                        {
                            p["name"]: p["explanation"]
                            for p in enrichment.get("parameters_explained", [])
                        }
                        if isinstance(enrichment.get("parameters_explained"), list)
                        else enrichment.get("parameters_explained", {})
                    ),
                },
            )
        elif entity_type == "class":
            await self._write(
                """
                MATCH (n:Class {qualified_name: $qname})
                SET n.role = $role,
                    n.key_methods = $key_methods
                """,
                {
                    "qname": qualified_name,
                    "role": enrichment.get("role", ""),
                    "key_methods": enrichment.get("key_methods", []),
                },
            )

    async def create_semantic_edges(self, qualified_name: str, enrichment: dict) -> None:
        """Create semantic edges based on LLM enrichment output."""
        # Design pattern nodes
        for pattern in enrichment.get("design_patterns", []):
            await self._write(
                """
                MATCH (n {qualified_name: $qname})
                MERGE (p:DesignPattern {name: $pattern})
                MERGE (n)-[:IMPLEMENTS_PATTERN]->(p)
                """,
                {"qname": qualified_name, "pattern": pattern},
            )

        # Domain concept nodes
        for concept in enrichment.get("domain_concepts", []):
            await self._write(
                """
                MATCH (n {qualified_name: $qname})
                MERGE (c:DomainConcept {name: $concept})
                MERGE (n)-[:RELATES_TO_CONCEPT]->(c)
                """,
                {"qname": qualified_name, "concept": concept},
            )

        # Collaborators (class-level)
        for collab in enrichment.get("collaborators", []):
            await self._write(
                """
                MATCH (n {qualified_name: $qname})
                MATCH (c:Class {name: $collab_name})
                WHERE n <> c
                MERGE (n)-[:COLLABORATES_WITH]->(c)
                """,
                {"qname": qualified_name, "collab_name": collab},
            )

        # Data flow edges (from Paper 3 — data-flow awareness)
        for target in enrichment.get("data_flows_to", []):
            await self._write(
                """
                MATCH (n {qualified_name: $qname})
                MATCH (t)
                WHERE (t:Function OR t:Class) AND t.name = $target_name AND n <> t
                MERGE (n)-[:DATA_FLOWS_TO]->(t)
                """,
                {"qname": qualified_name, "target_name": target},
            )

    async def delete_semantic_edges(self, qualified_name: str) -> None:
        """Delete all semantic edges for a node before re-enrichment."""
        await self._write(
            """
            MATCH (n {qualified_name: $qname})-[r]->()
            WHERE type(r) IN ['IMPLEMENTS_PATTERN', 'RELATES_TO_CONCEPT',
                              'COLLABORATES_WITH', 'DATA_FLOWS_TO']
            DELETE r
            """,
            {"qname": qualified_name},
        )

    # ─── Enrichment Cache ──────────────────────────────────

    async def get_cached_enrichment(self, content_hash: str) -> dict | None:
        """Look up enrichment from cache by content hash."""
        result = await self._run_single(
            "MATCH (c:EnrichmentCache {content_hash: $hash}) RETURN c.enrichment_json as data",
            {"hash": content_hash},
        )
        if result and result.get("data"):
            return _json.loads(result["data"])
        return None

    async def cache_enrichment(self, content_hash: str, enrichment: dict) -> None:
        """Store enrichment in cache."""
        await self._write(
            """
            MERGE (c:EnrichmentCache {content_hash: $hash})
            SET c.enrichment_json = $data,
                c.cached_at = datetime()
            """,
            {"hash": content_hash, "data": _json.dumps(enrichment)},
        )

    # ─── Embeddings ────────────────────────────────────────

    async def set_embedding(self, qualified_name: str, embedding: list[float]) -> None:
        """Store vector embedding on a node."""
        await self._write(
            """
            MATCH (n {qualified_name: $qname})
            SET n.embedding = $embedding
            """,
            {"qname": qualified_name, "embedding": embedding},
        )

    async def create_all_embeddings(self, embeddings_model, batch_size: int = 50) -> int:
        """
        Generate and store vector embeddings for all Function and Class nodes.

        Uses enrichment properties (purpose, summary) when available,
        falling back to docstring and name.

        Args:
            embeddings_model: LangChain embeddings model with aembed_documents().
            batch_size: Number of texts to embed per API call.

        Returns:
            Number of nodes embedded.
        """
        nodes = await self._run(
            """
            MATCH (n)
            WHERE (n:Function OR n:Class) AND n.qualified_name IS NOT NULL
            RETURN n.qualified_name AS qname,
                   n.name AS name,
                   n.docstring AS docstring,
                   n.purpose AS purpose,
                   n.summary AS summary,
                   n.domain_concepts AS domain_concepts,
                   labels(n)[0] AS label
            """
        )

        if not nodes:
            logger.info("No nodes to embed")
            return 0

        logger.info("Generating embeddings for %d nodes...", len(nodes))
        embedded_count = 0

        for i in range(0, len(nodes), batch_size):
            batch = nodes[i : i + batch_size]

            texts = []
            qnames = []
            for node in batch:
                texts.append(_build_embedding_text(node))
                qnames.append(node["qname"])

            try:
                vectors = await embeddings_model.aembed_documents(texts)
            except Exception as e:
                logger.error("Embedding batch %d failed: %s", i // batch_size, e)
                continue

            for qname, vector in zip(qnames, vectors):
                await self.set_embedding(qname, vector)
                embedded_count += 1

            logger.info(
                "Embedded %d/%d nodes",
                min(i + batch_size, len(nodes)),
                len(nodes),
            )

        logger.info("Embedding complete: %d nodes", embedded_count)
        return embedded_count

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
