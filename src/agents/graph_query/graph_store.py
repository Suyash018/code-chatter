"""
Graph Store — Neo4j query layer for the Graph Query agent.

Uses ``langchain_neo4j.Neo4jGraph`` for all read-only Cypher queries
and ``langchain_openai.OpenAIEmbeddings`` for vector similarity search.
Each public method corresponds to one MCP tool and returns a plain dict
ready for JSON serialisation.
"""

import logging
import re
from typing import Any

from langchain_neo4j import Neo4jGraph

from src.agents.graph_query.config import GraphQuerySettings
from src.shared.exceptions import GraphQueryError
from src.shared.llms.models import get_openai_embeddings

logger = logging.getLogger("graph_query.graph_store")

# ── Security guards (these MUST stay) ─────────────────────

# Whitelist for relationship types injected into f-string Cypher
VALID_RELATIONSHIPS: set[str] = {
    "CALLS", "CONTAINS", "INHERITS_FROM", "IMPORTS",
    "DECORATED_BY", "HAS_PARAMETER", "HAS_ATTRIBUTE",
    "DEFINES_MODULE", "IMPLEMENTS_PATTERN", "RELATES_TO_CONCEPT",
    "COLLABORATES_WITH", "DATA_FLOWS_TO",
}

# Blocks write operations in execute_query
_WRITE_PATTERN = re.compile(
    r"\b(MERGE|CREATE|DELETE|DETACH|SET|REMOVE|DROP|LOAD|FOREACH)\b"
    r"|CALL\s*\{",
    re.IGNORECASE,
)

# Cypher map projection — selects only useful node properties,
# avoids returning huge fields like embedding vectors or _calls lists.
_NODE_PROJECTION = (
    "{ .qualified_name, .name, .docstring, .source, .purpose, .summary,"
    "  .design_patterns, .domain_concepts, .complexity,"
    "  .is_async, .is_method, .return_annotation, .side_effects,"
    "  .role, .key_methods, .lineno_start, .lineno_end }"
)


def _safe_rel_filter(raw: str) -> str:
    """Parse a comma-separated relationship string, validate each token
    against the whitelist, and return a Cypher ``TYPE1|TYPE2`` filter.

    Args:
        raw: Comma-separated relationship type string (e.g., "CALLS,IMPORTS").

    Returns:
        Cypher-formatted filter string (e.g., "CALLS|IMPORTS").
        Returns all valid relationships joined with "|" when raw is empty.

    Raises:
        GraphQueryError: If any relationship type is not in VALID_RELATIONSHIPS.
    """
    if not raw.strip():
        return "|".join(sorted(VALID_RELATIONSHIPS))
    types = [t.strip().upper() for t in raw.split(",") if t.strip()]
    bad = [t for t in types if t not in VALID_RELATIONSHIPS]
    if bad:
        raise GraphQueryError(
            f"Invalid relationship type(s): {bad}. "
            f"Valid: {sorted(VALID_RELATIONSHIPS)}"
        )
    return "|".join(types)


class GraphStore:
    """Read-only query interface over the enriched FastAPI knowledge graph."""

    def __init__(self, settings: GraphQuerySettings | None = None):
        settings = settings or GraphQuerySettings()
        self._graph = Neo4jGraph(
            url=settings.neo4j_uri,
            username=settings.neo4j_username,
            password=settings.neo4j_password,
            database=settings.neo4j_database,
            refresh_schema=False,
        )
        self._embeddings = get_openai_embeddings(settings.embedding_model)
        self._settings = settings

    # ─── Core helpers ─────────────────────────────────────

    def _query(self, cypher: str, params: dict | None = None) -> list[dict[str, Any]]:
        return self._graph.query(cypher, params or {})

    def _resolve_single(self, name: str) -> dict[str, Any] | None:
        """Find a single entity by qualified_name or name."""
        for label in ("Function", "Class", "Module"):
            rows = self._query(
                f"MATCH (n:{label} {{qualified_name: $name}}) "
                f"RETURN n {_NODE_PROJECTION} AS entity, "
                f"       labels(n)[0] AS type LIMIT 1",
                {"name": name},
            )
            if rows:
                rows[0]["entity"]["type"] = rows[0]["type"]
                return rows[0]["entity"]

        for label in ("Function", "Class", "Module"):
            rows = self._query(
                f"MATCH (n:{label} {{name: $name}}) "
                f"RETURN n {_NODE_PROJECTION} AS entity, "
                f"       labels(n)[0] AS type LIMIT 1",
                {"name": name},
            )
            if rows:
                rows[0]["entity"]["type"] = rows[0]["type"]
                return rows[0]["entity"]

        return None

    def _vector_search(self, query_text: str, top_k: int = 10) -> list[dict[str, Any]]:
        """Embed query text and search both Function and Class vector indexes."""
        embedding = self._embeddings.embed_query(query_text)
        all_results: list[dict[str, Any]] = []

        for index_name, label in [("func_embedding", "Function"),
                                   ("class_embedding", "Class")]:
            try:
                rows = self._query(
                    f"CALL db.index.vector.queryNodes('{index_name}', $k, $embedding) "
                    f"YIELD node, score "
                    f"RETURN node {_NODE_PROJECTION} AS entity, "
                    f"       score, '{label}' AS type "
                    f"ORDER BY score DESC",
                    {"k": top_k, "embedding": embedding},
                )
                for row in rows:
                    entity = row["entity"]
                    entity["type"] = row["type"]
                    entity["similarity_score"] = round(row["score"], 4)
                    all_results.append(entity)
            except Exception as exc:
                logger.warning("Vector search on %s failed: %s", index_name, exc)

        all_results.sort(key=lambda r: r.get("similarity_score", 0), reverse=True)

        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for r in all_results:
            qn = r.get("qualified_name", "")
            if qn and qn not in seen:
                seen.add(qn)
                deduped.append(r)
        return deduped[:top_k]

    # ─── Tool 1: find_entity ──────────────────────────────

    def find_entity(
        self,
        name: str,
        entity_type: str = "any",
        search_mode: str = "hybrid",
        include_source: bool = False,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Locate code entities by name, fuzzy match, or semantic similarity."""
        labels = [entity_type.capitalize()] if entity_type != "any" else [
            "Function", "Class", "Module", "File",
        ]
        results: list[dict[str, Any]] = []
        seen: set[str] = set()

        def _collect(rows: list[dict[str, Any]]) -> None:
            for row in rows:
                e = row["entity"]
                e["type"] = row.get("type", "Unknown")
                qn = e.get("qualified_name") or e.get("name", "")
                if qn not in seen:
                    seen.add(qn)
                    if not include_source:
                        e.pop("source", None)
                    results.append(e)

        # Exact match
        if search_mode in ("exact", "hybrid"):
            for label in labels:
                _collect(self._query(
                    f"MATCH (n:{label} {{qualified_name: $name}}) "
                    f"RETURN n {_NODE_PROJECTION} AS entity, "
                    f"       labels(n)[0] AS type LIMIT 1",
                    {"name": name},
                ))
            if len(results) < limit:
                for label in labels:
                    _collect(self._query(
                        f"MATCH (n:{label} {{name: $name}}) "
                        f"RETURN n {_NODE_PROJECTION} AS entity, "
                        f"       labels(n)[0] AS type LIMIT $lim",
                        {"name": name, "lim": limit},
                    ))

        # Fuzzy match
        if search_mode == "fuzzy":
            for label in labels:
                _collect(self._query(
                    f"MATCH (n:{label}) "
                    "WHERE toLower(n.name) CONTAINS toLower($name) "
                    "   OR toLower(n.qualified_name) CONTAINS toLower($name) "
                    f"RETURN n {_NODE_PROJECTION} AS entity, "
                    f"       labels(n)[0] AS type LIMIT $lim",
                    {"name": name, "lim": limit},
                ))

        # Semantic (vector) search
        if search_mode in ("semantic", "hybrid") and len(results) < limit:
            for hit in self._vector_search(name, top_k=limit):
                qn = hit.get("qualified_name", "")
                if qn and qn not in seen:
                    seen.add(qn)
                    if not include_source:
                        hit.pop("source", None)
                    results.append(hit)

        return results[:limit]

    # ─── Tool 2: get_dependencies ─────────────────────────

    def get_dependencies(
        self,
        qualified_name: str,
        relationship_types: str = "",
        depth: int = 1,
        include_source: bool = False,
    ) -> dict[str, Any]:
        """Find outgoing dependencies of an entity."""
        entity = self._resolve_single(qualified_name)
        if not entity:
            return {"error": f"Entity not found: {qualified_name}"}

        depth = max(1, min(depth, self._settings.max_traversal_depth))
        rel_filter = _safe_rel_filter(relationship_types)
        source_field = ", target.source AS source" if include_source else ""

        rows = self._query(
            f"MATCH path = (source {{qualified_name: $qname}})"
            f"-[:{rel_filter}*1..{depth}]->(target) "
            "WHERE target.qualified_name IS NOT NULL "
            "RETURN DISTINCT target.qualified_name AS qualified_name, "
            "       target.name AS name, labels(target)[0] AS type, "
            f"       target.purpose AS purpose, length(path) AS distance"
            f"{source_field}",
            {"qname": qualified_name},
        )

        return {
            "entity": {
                "qualified_name": qualified_name,
                "name": entity.get("name"),
                "type": entity.get("type"),
            },
            "direction": "outgoing",
            "depth": depth,
            "dependencies": rows,
            "count": len(rows),
        }

    # ─── Tool 3: get_dependents ───────────────────────────

    def get_dependents(
        self,
        qualified_name: str,
        relationship_types: str = "",
        depth: int = 1,
        include_source: bool = False,
    ) -> dict[str, Any]:
        """Find incoming dependents of an entity (reverse traversal)."""
        entity = self._resolve_single(qualified_name)
        if not entity:
            return {"error": f"Entity not found: {qualified_name}"}

        depth = max(1, min(depth, self._settings.max_traversal_depth))
        rel_filter = _safe_rel_filter(relationship_types)
        source_field = ", src.source AS source" if include_source else ""

        rows = self._query(
            f"MATCH path = (src)"
            f"-[:{rel_filter}*1..{depth}]->"
            "(target {qualified_name: $qname}) "
            "WHERE src.qualified_name IS NOT NULL "
            "RETURN DISTINCT src.qualified_name AS qualified_name, "
            "       src.name AS name, labels(src)[0] AS type, "
            f"       src.purpose AS purpose, length(path) AS distance"
            f"{source_field}",
            {"qname": qualified_name},
        )

        return {
            "entity": {
                "qualified_name": qualified_name,
                "name": entity.get("name"),
                "type": entity.get("type"),
            },
            "direction": "incoming",
            "depth": depth,
            "dependents": rows,
            "count": len(rows),
        }

    # ─── Tool 4: trace_imports ────────────────────────────

    def trace_imports(
        self,
        module_name: str,
        direction: str = "outgoing",
        depth: int = 3,
        include_names: bool = True,
    ) -> dict[str, Any]:
        """Follow module import chains."""
        depth = max(1, min(depth, 5))

        module = self._query(
            "MATCH (m:Module {qualified_name: $name}) "
            "RETURN m.qualified_name AS qualified_name LIMIT 1",
            {"name": module_name},
        )
        if not module:
            module = self._query(
                "MATCH (m:Module) "
                "WHERE toLower(m.qualified_name) CONTAINS toLower($name) "
                "RETURN m.qualified_name AS qualified_name LIMIT 1",
                {"name": module_name},
            )
        if not module:
            return {"error": f"Module not found: {module_name}"}

        resolved = module[0]["qualified_name"]
        result: dict[str, Any] = {"module": resolved, "direction": direction}

        names_field = ", r.names AS names, r.aliases AS aliases" if include_names else ""
        flags_field = (
            ", r.is_relative AS is_relative"
            ", r.is_type_checking AS is_type_checking"
            ", r.is_conditional AS is_conditional"
            ", r.is_try_except AS is_try_except"
        )

        if direction in ("outgoing", "both"):
            result["imports"] = self._query(
                f"MATCH path = (src:Module {{qualified_name: $name}})"
                f"-[:IMPORTS*1..{depth}]->(tgt:Module) "
                "UNWIND range(0, length(path)-1) AS idx "
                "WITH relationships(path)[idx] AS r, "
                "     nodes(path)[idx] AS from_mod, "
                "     nodes(path)[idx+1] AS to_mod "
                "RETURN DISTINCT from_mod.qualified_name AS from_module, "
                f"       to_mod.qualified_name AS to_module{names_field}{flags_field}",
                {"name": resolved},
            )

        if direction in ("incoming", "both"):
            result["imported_by"] = self._query(
                f"MATCH path = (src:Module)"
                f"-[:IMPORTS*1..{depth}]->"
                "(tgt:Module {qualified_name: $name}) "
                "UNWIND range(0, length(path)-1) AS idx "
                "WITH relationships(path)[idx] AS r, "
                "     nodes(path)[idx] AS from_mod, "
                "     nodes(path)[idx+1] AS to_mod "
                "RETURN DISTINCT from_mod.qualified_name AS from_module, "
                f"       to_mod.qualified_name AS to_module{names_field}{flags_field}",
                {"name": resolved},
            )

        return result

    # ─── Tool 5: find_related ─────────────────────────────

    def find_related(
        self,
        entity_name: str,
        relationship_type: str,
        direction: str = "both",
        target_type: str = "",
        limit: int = 25,
    ) -> dict[str, Any]:
        """Get entities connected by a specific relationship type."""
        rel_filter = _safe_rel_filter(relationship_type)
        if "|" in rel_filter and relationship_type.count(",") == 0:
            pass  # single type is fine
        # Security: rel_filter is already validated

        entity = self._resolve_single(entity_name)
        if not entity:
            return {"error": f"Entity not found: {entity_name}"}

        qname = entity["qualified_name"]
        target_label = f":{target_type}" if target_type else ""
        results: list[dict[str, Any]] = []

        if direction in ("outgoing", "both"):
            results.extend(self._query(
                f"MATCH (source {{qualified_name: $qname}})"
                f"-[r:{rel_filter}]->(target{target_label}) "
                "RETURN target.qualified_name AS qualified_name, "
                "       target.name AS name, labels(target)[0] AS type, "
                "       target.purpose AS purpose, "
                "       properties(r) AS rel_properties, "
                "       'outgoing' AS direction "
                "LIMIT $lim",
                {"qname": qname, "lim": limit},
            ))

        if direction in ("incoming", "both"):
            remaining = max(0, limit - len(results))
            if remaining > 0:
                results.extend(self._query(
                    f"MATCH (source{target_label})"
                    f"-[r:{rel_filter}]->(target {{qualified_name: $qname}}) "
                    "RETURN source.qualified_name AS qualified_name, "
                    "       source.name AS name, labels(source)[0] AS type, "
                    "       source.purpose AS purpose, "
                    "       properties(r) AS rel_properties, "
                    "       'incoming' AS direction "
                    "LIMIT $lim",
                    {"qname": qname, "lim": remaining},
                ))

        return {
            "entity": {"qualified_name": qname, "name": entity.get("name"),
                        "type": entity.get("type")},
            "relationship_type": relationship_type,
            "related": results,
            "count": len(results),
        }

    # ─── Tool 6: execute_query ────────────────────────────

    def execute_query(
        self,
        cypher: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run a validated read-only Cypher query.

        Args:
            cypher: Cypher query string (must be read-only).
            params: Optional query parameters.

        Returns:
            Dict with 'records' (query results), 'count', and 'truncated' flag.

        Raises:
            GraphQueryError: If query contains write operations (MERGE, CREATE,
                DELETE, SET, REMOVE, DROP, LOAD, FOREACH, CALL {}) or if
                Cypher execution fails.
        """
        if _WRITE_PATTERN.search(cypher):
            raise GraphQueryError(
                "Write operations are not allowed. "
                "Query contains forbidden keywords "
                "(CREATE/MERGE/DELETE/SET/REMOVE/DROP/LOAD/FOREACH)."
            )

        max_results = self._settings.max_results
        has_limit = bool(re.search(r"\bLIMIT\b", cypher, re.IGNORECASE))
        safe_cypher = cypher if has_limit else f"{cypher}\nLIMIT {max_results}"

        try:
            rows = self._query(safe_cypher, params)
        except Exception as exc:
            raise GraphQueryError(f"Cypher execution failed: {exc}") from exc

        return {
            "records": rows,
            "count": len(rows),
            "truncated": len(rows) >= max_results and not has_limit,
        }

    # ─── Tool 7: get_subgraph ─────────────────────────────

    def get_subgraph(
        self,
        entity_names: list[str],
        hops: int = 2,
        include_source: bool = True,
    ) -> dict[str, Any]:
        """Bidirectional graph expansion from seed entities."""
        hops = max(1, min(hops, self._settings.max_traversal_depth))

        seed_qnames: list[str] = []
        seed_info: list[dict[str, Any]] = []
        not_found: list[str] = []

        for name in entity_names:
            entity = self._resolve_single(name)
            if entity:
                seed_qnames.append(entity["qualified_name"])
                seed_info.append({
                    "qualified_name": entity["qualified_name"],
                    "name": entity.get("name"),
                    "type": entity.get("type"),
                })
            else:
                not_found.append(name)

        if not seed_qnames:
            return {"error": f"No seed entities found for: {entity_names}"}

        source_field = ", n.source AS source" if include_source else ""

        # Outgoing expansion
        outgoing = self._query(
            "MATCH (seed)-[*1.." + str(hops) + "]->(n) "
            "WHERE seed.qualified_name IN $seeds "
            "  AND n.qualified_name IS NOT NULL "
            "RETURN DISTINCT n.qualified_name AS qualified_name, "
            "       n.name AS name, labels(n)[0] AS type, "
            "       n.purpose AS purpose, n.summary AS summary, "
            f"       n.docstring AS docstring{source_field}",
            {"seeds": seed_qnames},
        )

        # Incoming expansion
        incoming = self._query(
            "MATCH (n)-[*1.." + str(hops) + "]->(seed) "
            "WHERE seed.qualified_name IN $seeds "
            "  AND n.qualified_name IS NOT NULL "
            "RETURN DISTINCT n.qualified_name AS qualified_name, "
            "       n.name AS name, labels(n)[0] AS type, "
            "       n.purpose AS purpose, n.summary AS summary, "
            f"       n.docstring AS docstring{source_field}",
            {"seeds": seed_qnames},
        )

        # Deduplicate nodes
        nodes: dict[str, dict[str, Any]] = {}
        for node in outgoing + incoming:
            qn = node["qualified_name"]
            if qn not in nodes:
                nodes[qn] = node

        # Ensure seeds are included
        for seed in seed_info:
            qn = seed["qualified_name"]
            if qn not in nodes:
                raw = self._resolve_single(qn)
                if raw:
                    entry = {
                        "qualified_name": qn, "name": raw.get("name"),
                        "type": raw.get("type"), "purpose": raw.get("purpose"),
                        "summary": raw.get("summary"), "docstring": raw.get("docstring"),
                    }
                    if include_source:
                        entry["source"] = raw.get("source")
                    nodes[qn] = entry

        max_nodes = self._settings.max_results
        node_list = list(nodes.values())[:max_nodes]
        final_qnames = [n["qualified_name"] for n in node_list]

        # Edges between subgraph nodes
        edges = self._query(
            "MATCH (a)-[r]->(b) "
            "WHERE a.qualified_name IN $qnames "
            "  AND b.qualified_name IN $qnames "
            "RETURN a.qualified_name AS source, "
            "       type(r) AS relationship, "
            "       b.qualified_name AS target",
            {"qnames": final_qnames},
        )

        # Source snippet map
        source_snippets: dict[str, str] = {}
        if include_source:
            for n in node_list:
                src = n.get("source")
                if src:
                    source_snippets[n["qualified_name"]] = src

        return {
            "seeds": seed_info,
            "seeds_not_found": not_found,
            "nodes": node_list,
            "edges": edges,
            "source_snippets": source_snippets,
            "statistics": {
                "node_count": len(node_list),
                "edge_count": len(edges),
                "seed_count": len(seed_qnames),
                "hops": hops,
                "truncated": len(nodes) > max_nodes,
            },
        }
