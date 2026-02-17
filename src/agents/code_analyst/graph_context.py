"""
Graph Context Retriever — Neo4j query layer for the Code Analyst agent.

Uses ``langchain_neo4j.Neo4jGraph`` for all read-only Cypher queries
against the enriched knowledge graph.  Each public method corresponds
to one MCP tool and returns a plain dict ready for JSON serialisation.
"""

import logging
from typing import Any

from langchain_neo4j import Neo4jGraph

from src.agents.code_analyst.config import CodeAnalystSettings
from src.shared.exceptions import CodeAnalystError

logger = logging.getLogger("code_analyst.graph_context")


class GraphContextRetriever:
    """Read-only query interface over the enriched FastAPI knowledge graph."""

    def __init__(self, settings: CodeAnalystSettings | None = None):
        settings = settings or CodeAnalystSettings()
        self._graph = Neo4jGraph(
            url=settings.neo4j_uri,
            username=settings.neo4j_username,
            password=settings.neo4j_password,
            database=settings.neo4j_database,
            refresh_schema=False,
        )

    # ─── Helpers ──────────────────────────────────────────

    def _query(self, cypher: str, params: dict | None = None) -> list[dict[str, Any]]:
        """Execute a Cypher query via Neo4jGraph."""
        return self._graph.query(cypher, params or {})

    def resolve_entity(self, name: str) -> dict[str, Any]:
        """Find a Function or Class node by qualified_name or name.

        Resolution order:
          1. Exact qualified_name match (Function, then Class)
          2. Exact name match (Function, then Class)
          3. Case-insensitive name match as fallback

        Raises ``CodeAnalystError`` when nothing is found.
        """
        # 1. Exact qualified_name
        for label in ("Function", "Class"):
            rows = self._query(
                f"MATCH (n:{label} {{qualified_name: $name}}) "
                f"RETURN n {{ .*, _label: '{label}' }} AS entity LIMIT 1",
                {"name": name},
            )
            if rows:
                return rows[0]["entity"]

        # 2. Exact name
        for label in ("Function", "Class"):
            rows = self._query(
                f"MATCH (n:{label} {{name: $name}}) "
                f"RETURN n {{ .*, _label: '{label}' }} AS entity LIMIT 1",
                {"name": name},
            )
            if rows:
                return rows[0]["entity"]

        # 3. Case-insensitive fallback
        for label in ("Function", "Class"):
            rows = self._query(
                f"MATCH (n:{label}) WHERE toLower(n.name) = toLower($name) "
                f"RETURN n {{ .*, _label: '{label}' }} AS entity LIMIT 1",
                {"name": name},
            )
            if rows:
                return rows[0]["entity"]

        raise CodeAnalystError(f"Entity not found: '{name}'")

    def _get_file_path(self, qualified_name: str) -> str | None:
        """Return the file path containing this entity."""
        rows = self._query(
            "MATCH (f:File)-[:CONTAINS*1..3]->(n {qualified_name: $qname}) "
            "RETURN f.path AS path LIMIT 1",
            {"qname": qualified_name},
        )
        return rows[0]["path"] if rows else None

    def _get_parent_class(self, qualified_name: str) -> str | None:
        """Return the parent class qualified_name if this is a method."""
        rows = self._query(
            "MATCH (c:Class)-[:CONTAINS]->(n:Function {qualified_name: $qname}) "
            "RETURN c.qualified_name AS parent LIMIT 1",
            {"qname": qualified_name},
        )
        return rows[0]["parent"] if rows else None

    # ─── Tool 1: analyze_function ─────────────────────────

    def get_function_analysis(
        self,
        name: str,
        depth: int = 1,
        include_source: bool = True,
    ) -> dict[str, Any]:
        """Retrieve deep analysis of a function from the knowledge graph.

        Gathers the function's enrichment properties, parameters, decorators,
        caller/callee chains (up to *depth* hops), data-flow targets, patterns,
        domain concepts, and file/class location.
        """
        entity = self.resolve_entity(name)
        qname = entity["qualified_name"]

        result: dict[str, Any] = {
            "qualified_name": qname,
            "name": entity.get("name"),
            "purpose": entity.get("purpose"),
            "summary": entity.get("summary"),
            "complexity": entity.get("complexity"),
            "is_async": entity.get("is_async"),
            "is_method": entity.get("is_method"),
            "return_annotation": entity.get("return_annotation"),
            "docstring": entity.get("docstring"),
        }

        if include_source:
            result["source"] = entity.get("source")

        # Enrichment list fields
        result["side_effects"] = entity.get("side_effects", [])
        result["design_patterns"] = entity.get("design_patterns", [])
        result["domain_concepts"] = entity.get("domain_concepts", [])
        result["parameters_explained"] = entity.get("parameters_explained")

        # Parameters via HAS_PARAMETER
        result["parameters"] = self._query(
            "MATCH (f:Function {qualified_name: $qname})-[:HAS_PARAMETER]->(p:Parameter) "
            "RETURN p.name AS name, p.type_annotation AS type, "
            "       p.default_value AS default, p.kind AS kind "
            "ORDER BY p.position",
            {"qname": qname},
        )

        # Decorators via DECORATED_BY
        result["decorators"] = self._query(
            "MATCH (f:Function {qualified_name: $qname})-[:DECORATED_BY]->(d:Decorator) "
            "RETURN d.name AS name, d.arguments AS arguments",
            {"qname": qname},
        )

        # Callers (reverse CALLS, up to depth hops)
        if depth >= 1:
            result["callers"] = self._query(
                f"MATCH (caller:Function)-[:CALLS*1..{int(depth)}]->"
                "(f:Function {qualified_name: $qname}) "
                "RETURN DISTINCT caller.qualified_name AS qualified_name, "
                "       caller.name AS name, caller.purpose AS purpose",
                {"qname": qname},
            )
        else:
            result["callers"] = []

        # Callees (CALLS out, up to depth hops)
        if depth >= 1:
            result["callees"] = self._query(
                f"MATCH (f:Function {{qualified_name: $qname}})-[:CALLS*1..{int(depth)}]->"
                "(callee:Function) "
                "RETURN DISTINCT callee.qualified_name AS qualified_name, "
                "       callee.name AS name, callee.purpose AS purpose",
                {"qname": qname},
            )
        else:
            result["callees"] = []

        # DATA_FLOWS_TO
        result["data_flows_to"] = self._query(
            "MATCH (f:Function {qualified_name: $qname})-[:DATA_FLOWS_TO]->(t) "
            "RETURN t.qualified_name AS qualified_name, t.name AS name, "
            "       labels(t)[0] AS type",
            {"qname": qname},
        )

        # Patterns and concepts (from edges, not just node properties)
        result["patterns"] = self._query(
            "MATCH (f:Function {qualified_name: $qname})-[:IMPLEMENTS_PATTERN]->(p:DesignPattern) "
            "RETURN p.name AS name",
            {"qname": qname},
        )

        result["concepts"] = self._query(
            "MATCH (f:Function {qualified_name: $qname})-[:RELATES_TO_CONCEPT]->(c:DomainConcept) "
            "RETURN c.name AS name",
            {"qname": qname},
        )

        # Location context
        result["file_path"] = self._get_file_path(qname)
        result["parent_class"] = self._get_parent_class(qname)

        return result

    # ─── Tool 2: analyze_class ────────────────────────────

    def get_class_analysis(
        self,
        name: str,
        include_methods: bool = True,
        include_attributes: bool = True,
        include_inheritance: bool = True,
    ) -> dict[str, Any]:
        """Retrieve comprehensive analysis of a class from the knowledge graph.

        Gathers the class's enrichment properties, methods, attributes,
        decorators, inheritance chain, collaborators, patterns, and location.
        """
        entity = self.resolve_entity(name)
        if entity.get("_label") != "Class":
            # If we resolved a function, try to find a class with this name
            rows = self._query(
                "MATCH (c:Class) WHERE c.qualified_name = $name OR c.name = $name "
                "RETURN c { .*, _label: 'Class' } AS entity LIMIT 1",
                {"name": name},
            )
            if not rows:
                raise CodeAnalystError(f"Class not found: '{name}'")
            entity = rows[0]["entity"]

        qname = entity["qualified_name"]

        result: dict[str, Any] = {
            "qualified_name": qname,
            "name": entity.get("name"),
            "source": entity.get("source"),
            "purpose": entity.get("purpose"),
            "summary": entity.get("summary"),
            "role": entity.get("role"),
            "key_methods": entity.get("key_methods", []),
            "docstring": entity.get("docstring"),
            "design_patterns": entity.get("design_patterns", []),
            "domain_concepts": entity.get("domain_concepts", []),
        }

        # Decorators
        result["decorators"] = self._query(
            "MATCH (c:Class {qualified_name: $qname})-[:DECORATED_BY]->(d:Decorator) "
            "RETURN d.name AS name, d.arguments AS arguments",
            {"qname": qname},
        )

        # Methods via CONTAINS
        if include_methods:
            result["methods"] = self._query(
                "MATCH (c:Class {qualified_name: $qname})-[:CONTAINS]->(m:Function) "
                "RETURN m.name AS name, m.qualified_name AS qualified_name, "
                "       m.purpose AS purpose, m.complexity AS complexity, "
                "       m.is_async AS is_async, m.docstring AS docstring "
                "ORDER BY m.lineno_start",
                {"qname": qname},
            )
        else:
            result["methods"] = []

        # Class attributes via HAS_ATTRIBUTE
        if include_attributes:
            result["attributes"] = self._query(
                "MATCH (c:Class {qualified_name: $qname})-[:HAS_ATTRIBUTE]->(a:ClassAttribute) "
                "RETURN a.name AS name, a.type_annotation AS type, "
                "       a.default_value AS default "
                "ORDER BY a.lineno",
                {"qname": qname},
            )
        else:
            result["attributes"] = []

        # Inheritance chain
        if include_inheritance:
            result["bases"] = self._query(
                "MATCH (c:Class {qualified_name: $qname})-[:INHERITS_FROM*1..5]->(base:Class) "
                "RETURN DISTINCT base.qualified_name AS qualified_name, "
                "       base.name AS name, base.purpose AS purpose",
                {"qname": qname},
            )
            result["subclasses"] = self._query(
                "MATCH (sub:Class)-[:INHERITS_FROM*1..5]->(c:Class {qualified_name: $qname}) "
                "RETURN DISTINCT sub.qualified_name AS qualified_name, "
                "       sub.name AS name, sub.purpose AS purpose",
                {"qname": qname},
            )
        else:
            result["bases"] = []
            result["subclasses"] = []

        # Collaborators
        result["collaborators"] = self._query(
            "MATCH (c:Class {qualified_name: $qname})-[:COLLABORATES_WITH]->(other:Class) "
            "RETURN other.qualified_name AS qualified_name, other.name AS name, "
            "       other.purpose AS purpose",
            {"qname": qname},
        )

        # Data flow
        result["data_flows_to"] = self._query(
            "MATCH (c:Class {qualified_name: $qname})-[:DATA_FLOWS_TO]->(t) "
            "RETURN t.qualified_name AS qualified_name, t.name AS name, "
            "       labels(t)[0] AS type",
            {"qname": qname},
        )

        # Patterns and concepts (edges)
        result["patterns"] = self._query(
            "MATCH (c:Class {qualified_name: $qname})-[:IMPLEMENTS_PATTERN]->(p:DesignPattern) "
            "RETURN p.name AS name",
            {"qname": qname},
        )

        result["concepts"] = self._query(
            "MATCH (c:Class {qualified_name: $qname})-[:RELATES_TO_CONCEPT]->(dc:DomainConcept) "
            "RETURN dc.name AS name",
            {"qname": qname},
        )

        # Location
        result["file_path"] = self._get_file_path(qname)

        return result

    # ─── Tool 3: find_patterns ────────────────────────────

    def get_patterns(
        self,
        pattern_name: str = "",
        module_scope: str = "",
        include_source: bool = False,
    ) -> list[dict[str, Any]]:
        """Find design patterns in the codebase.

        Returns a list of patterns, each with its implementing entities.
        Can be filtered by pattern name and/or module scope.
        """
        source_field = ", entity.source AS source" if include_source else ""

        if pattern_name and module_scope:
            # Specific pattern within a module scope
            rows = self._query(
                "MATCH (f:File)-[:CONTAINS*1..3]->(entity)-[:IMPLEMENTS_PATTERN]->"
                "(p:DesignPattern {name: $pattern}) "
                "WHERE f.path CONTAINS $scope OR f.module_name CONTAINS $scope "
                f"RETURN p.name AS pattern, entity.qualified_name AS qualified_name, "
                f"       entity.name AS name, labels(entity)[0] AS type, "
                f"       entity.purpose AS purpose{source_field}",
                {"pattern": pattern_name, "scope": module_scope},
            )
        elif pattern_name:
            # Specific pattern, all modules
            rows = self._query(
                "MATCH (entity)-[:IMPLEMENTS_PATTERN]->"
                "(p:DesignPattern {name: $pattern}) "
                f"RETURN p.name AS pattern, entity.qualified_name AS qualified_name, "
                f"       entity.name AS name, labels(entity)[0] AS type, "
                f"       entity.purpose AS purpose{source_field}",
                {"pattern": pattern_name},
            )
        elif module_scope:
            # All patterns within a module scope
            rows = self._query(
                "MATCH (f:File)-[:CONTAINS*1..3]->(entity)-[:IMPLEMENTS_PATTERN]->"
                "(p:DesignPattern) "
                "WHERE f.path CONTAINS $scope OR f.module_name CONTAINS $scope "
                f"RETURN p.name AS pattern, entity.qualified_name AS qualified_name, "
                f"       entity.name AS name, labels(entity)[0] AS type, "
                f"       entity.purpose AS purpose{source_field}",
                {"scope": module_scope},
            )
        else:
            # All patterns with counts
            rows = self._query(
                "MATCH (entity)-[:IMPLEMENTS_PATTERN]->(p:DesignPattern) "
                f"RETURN p.name AS pattern, entity.qualified_name AS qualified_name, "
                f"       entity.name AS name, labels(entity)[0] AS type, "
                f"       entity.purpose AS purpose{source_field}",
            )

        # Group by pattern name
        patterns: dict[str, dict[str, Any]] = {}
        for row in rows:
            pname = row["pattern"]
            if pname not in patterns:
                patterns[pname] = {"name": pname, "entities": [], "count": 0}

            entity_info: dict[str, Any] = {
                "qualified_name": row["qualified_name"],
                "name": row["name"],
                "type": row["type"],
                "purpose": row.get("purpose"),
            }
            if include_source:
                entity_info["source"] = row.get("source")

            patterns[pname]["entities"].append(entity_info)
            patterns[pname]["count"] += 1

        return sorted(patterns.values(), key=lambda p: p["count"], reverse=True)

    # ─── Tool 4: get_code_snippet ─────────────────────────

    def get_code_snippet(
        self,
        name: str,
        neighborhood: int = 1,
        include_imports: bool = False,
    ) -> dict[str, Any]:
        """Extract source code with surrounding graph context.

        Returns the entity's source, its file/class location, and
        source code of related entities within *neighborhood* hops.
        """
        entity = self.resolve_entity(name)
        qname = entity["qualified_name"]
        label = entity.get("_label", "Function")

        result: dict[str, Any] = {
            "qualified_name": qname,
            "name": entity.get("name"),
            "type": label,
            "source": entity.get("source"),
            "file_path": self._get_file_path(qname),
            "parent_class": self._get_parent_class(qname) if label == "Function" else None,
        }

        # Neighborhood: related entities with their source
        neighbors: list[dict[str, Any]] = []

        if neighborhood >= 1 and label == "Function":
            # Callees
            callees = self._query(
                f"MATCH (f:Function {{qualified_name: $qname}})-[:CALLS*1..{int(neighborhood)}]->"
                "(callee:Function) "
                "RETURN DISTINCT callee.qualified_name AS qualified_name, "
                "       callee.name AS name, callee.source AS source, "
                "       'callee' AS relationship",
                {"qname": qname},
            )
            neighbors.extend(callees)

            # Callers
            callers = self._query(
                f"MATCH (caller:Function)-[:CALLS*1..{int(neighborhood)}]->"
                "(f:Function {qualified_name: $qname}) "
                "RETURN DISTINCT caller.qualified_name AS qualified_name, "
                "       caller.name AS name, caller.source AS source, "
                "       'caller' AS relationship",
                {"qname": qname},
            )
            neighbors.extend(callers)

        if neighborhood >= 1 and label == "Class":
            # Class methods
            methods = self._query(
                "MATCH (c:Class {qualified_name: $qname})-[:CONTAINS]->(m:Function) "
                "RETURN m.qualified_name AS qualified_name, m.name AS name, "
                "       m.source AS source, 'method' AS relationship "
                "ORDER BY m.lineno_start",
                {"qname": qname},
            )
            neighbors.extend(methods)

        result["neighborhood"] = neighbors

        # File imports
        if include_imports:
            result["imports"] = self._query(
                "MATCH (f:File)-[:CONTAINS*1..3]->({qualified_name: $qname}) "
                "MATCH (f)-[:DEFINES_MODULE]->(m:Module)-[r:IMPORTS]->(target:Module) "
                "RETURN target.qualified_name AS module, r.names AS names",
                {"qname": qname},
            )
        else:
            result["imports"] = []

        return result

    # ─── Tool 5: explain_implementation ───────────────────

    def get_implementation_details(
        self,
        name: str,
        follow_data_flow: bool = True,
        follow_calls: bool = True,
        max_depth: int = 3,
    ) -> dict[str, Any]:
        """Explain how code works by tracing data-flow and call chains.

        Returns enrichment properties plus ordered chains of downstream
        entities (data flow) and called functions (execution chain).
        """
        entity = self.resolve_entity(name)
        qname = entity["qualified_name"]

        result: dict[str, Any] = {
            "qualified_name": qname,
            "name": entity.get("name"),
            "purpose": entity.get("purpose"),
            "summary": entity.get("summary"),
            "docstring": entity.get("docstring"),
            "parameters_explained": entity.get("parameters_explained"),
        }

        # Parameters with full detail
        result["parameters"] = self._query(
            "MATCH (f:Function {qualified_name: $qname})-[:HAS_PARAMETER]->(p:Parameter) "
            "RETURN p.name AS name, p.type_annotation AS type, "
            "       p.default_value AS default, p.kind AS kind "
            "ORDER BY p.position",
            {"qname": qname},
        )

        # Decorators
        result["decorators"] = self._query(
            "MATCH (n {qualified_name: $qname})-[:DECORATED_BY]->(d:Decorator) "
            "RETURN d.name AS name, d.arguments AS arguments",
            {"qname": qname},
        )

        # Domain concepts
        result["domain_concepts"] = self._query(
            "MATCH (n {qualified_name: $qname})-[:RELATES_TO_CONCEPT]->(c:DomainConcept) "
            "RETURN c.name AS name",
            {"qname": qname},
        )

        # Data flow chain
        if follow_data_flow:
            result["data_flow_chain"] = self._query(
                f"MATCH path = (n {{qualified_name: $qname}})-[:DATA_FLOWS_TO*1..{int(max_depth)}]->(target) "
                "UNWIND nodes(path)[1..] AS step "
                "RETURN DISTINCT step.qualified_name AS qualified_name, "
                "       step.name AS name, step.purpose AS purpose, "
                "       labels(step)[0] AS type",
                {"qname": qname},
            )
        else:
            result["data_flow_chain"] = []

        # Call chain
        if follow_calls:
            result["call_chain"] = self._query(
                f"MATCH path = (n:Function {{qualified_name: $qname}})-[:CALLS*1..{int(max_depth)}]->(callee:Function) "
                "UNWIND nodes(path)[1..] AS step "
                "RETURN DISTINCT step.qualified_name AS qualified_name, "
                "       step.name AS name, step.purpose AS purpose",
                {"qname": qname},
            )
        else:
            result["call_chain"] = []

        # Location context
        result["file_path"] = self._get_file_path(qname)
        result["parent_class"] = self._get_parent_class(qname)

        return result

    # ─── Tool 6: compare_implementations ──────────────────

    def compare_entities(
        self,
        name_a: str,
        name_b: str,
        include_source: bool = True,
        include_relationships: bool = True,
    ) -> dict[str, Any]:
        """Compare two code entities side by side.

        Fetches both entities' properties, enrichment, and optionally
        their relationship context (callers, callees, patterns, concepts).
        """
        entity_a = self._build_comparison_profile(
            name_a, include_source, include_relationships,
        )
        entity_b = self._build_comparison_profile(
            name_b, include_source, include_relationships,
        )
        return {"entity_a": entity_a, "entity_b": entity_b}

    def _build_comparison_profile(
        self,
        name: str,
        include_source: bool,
        include_relationships: bool,
    ) -> dict[str, Any]:
        """Build a comparison profile for a single entity."""
        entity = self.resolve_entity(name)
        qname = entity["qualified_name"]
        label = entity.get("_label", "Function")

        profile: dict[str, Any] = {
            "qualified_name": qname,
            "name": entity.get("name"),
            "type": label,
            "purpose": entity.get("purpose"),
            "summary": entity.get("summary"),
            "complexity": entity.get("complexity"),
            "docstring": entity.get("docstring"),
            "design_patterns": entity.get("design_patterns", []),
            "domain_concepts": entity.get("domain_concepts", []),
        }

        if include_source:
            profile["source"] = entity.get("source")

        if label == "Function":
            profile["is_async"] = entity.get("is_async")
            profile["return_annotation"] = entity.get("return_annotation")
            profile["side_effects"] = entity.get("side_effects", [])

        if label == "Class":
            profile["role"] = entity.get("role")
            profile["key_methods"] = entity.get("key_methods", [])

        # Parameters / Attributes
        profile["parameters"] = self._query(
            "MATCH (n {qualified_name: $qname})-[:HAS_PARAMETER]->(p:Parameter) "
            "RETURN p.name AS name, p.type_annotation AS type, p.kind AS kind "
            "ORDER BY p.position",
            {"qname": qname},
        )

        # Decorators
        profile["decorators"] = self._query(
            "MATCH (n {qualified_name: $qname})-[:DECORATED_BY]->(d:Decorator) "
            "RETURN d.name AS name",
            {"qname": qname},
        )

        if include_relationships:
            # Callers / Callees
            profile["callers"] = self._query(
                "MATCH (caller:Function)-[:CALLS]->(n:Function {qualified_name: $qname}) "
                "RETURN caller.name AS name, caller.qualified_name AS qualified_name",
                {"qname": qname},
            )
            profile["callees"] = self._query(
                "MATCH (n:Function {qualified_name: $qname})-[:CALLS]->(callee:Function) "
                "RETURN callee.name AS name, callee.qualified_name AS qualified_name",
                {"qname": qname},
            )

            # Patterns and concepts (edges)
            profile["patterns"] = self._query(
                "MATCH (n {qualified_name: $qname})-[:IMPLEMENTS_PATTERN]->(p:DesignPattern) "
                "RETURN p.name AS name",
                {"qname": qname},
            )
            profile["concepts"] = self._query(
                "MATCH (n {qualified_name: $qname})-[:RELATES_TO_CONCEPT]->(c:DomainConcept) "
                "RETURN c.name AS name",
                {"qname": qname},
            )

            # Class-specific relationships
            if label == "Class":
                profile["bases"] = self._query(
                    "MATCH (c:Class {qualified_name: $qname})-[:INHERITS_FROM]->(base:Class) "
                    "RETURN base.name AS name, base.qualified_name AS qualified_name",
                    {"qname": qname},
                )
                profile["collaborators"] = self._query(
                    "MATCH (c:Class {qualified_name: $qname})-[:COLLABORATES_WITH]->(other:Class) "
                    "RETURN other.name AS name, other.qualified_name AS qualified_name",
                    {"qname": qname},
                )

        profile["file_path"] = self._get_file_path(qname)

        return profile
