"""
Graph Node Operations

CRUD operations for all node types in the knowledge graph:
File, Class, ClassAttribute, Function, and Parameter nodes.
"""

import logging

from src.agents.indexer.models import path_to_module

logger = logging.getLogger("indexer-agent.graph_manager")


class NodeOperationsMixin:
    """Mixin providing node CRUD operations for the graph manager."""

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
