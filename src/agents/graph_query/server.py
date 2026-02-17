"""
Graph Query Agent — MCP Server #3

Exposes seven read-only tools that query the enriched Neo4j knowledge graph
via ``langchain_neo4j.Neo4jGraph``.  Each tool's docstring is designed to
be read by the orchestrator LLM so it knows *when* and *how* to call it.

Run as:  python -m src.agents.graph_query.server        (stdio transport)
"""

import json
import logging

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from src.agents.graph_query.config import GraphQuerySettings
from src.agents.graph_query.graph_store import GraphStore
from src.shared.logging import setup_logging

logger = setup_logging("graph_query", level="INFO")

# ─── Shared resources (lazy init) ─────────────────────────

# Configure transport security to allow Docker service names
transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=False,
    allowed_hosts=["graph_query", "graph_query:8003", "localhost", "127.0.0.1", "0.0.0.0"],
    allowed_origins=["*"],
)

mcp = FastMCP("GraphQuery", transport_security=transport_security)

_settings: GraphQuerySettings | None = None
_store: GraphStore | None = None


def _get_settings() -> GraphQuerySettings:
    """Lazy-initialise settings from environment variables."""
    global _settings
    if _settings is None:
        _settings = GraphQuerySettings()
    return _settings


def _get_store() -> GraphStore:
    """Lazy-initialise the graph store on first tool call."""
    global _store
    if _store is None:
        _store = GraphStore(_get_settings())
    return _store


# ─── Tool 1 ──────────────────────────────────────────────


@mcp.tool()
def find_entity(
    name: str,
    entity_type: str = "any",
    search_mode: str = "hybrid",
    include_source: bool = False,
    limit: int = 10,
) -> str:
    """Locate code entities in the FastAPI knowledge graph by name or meaning.

    Use this as your FIRST tool when you need to find a specific class,
    function, module, or file.  Also use it for conceptual searches like
    "request validation" or "middleware handling".

    Resolution order for 'hybrid' mode:
      1. Exact qualified_name match (e.g. "fastapi.applications.FastAPI")
      2. Exact name match (e.g. "FastAPI")
      3. Semantic vector similarity on enriched embeddings

    Args:
        name: What to search for.  Examples:
              - Exact: "FastAPI", "solve_dependencies", "APIRoute"
              - Qualified: "fastapi.routing.APIRoute"
              - Conceptual: "dependency injection", "request lifecycle"
        entity_type: Filter results.  One of:
              "function", "class", "module", "file", "any".
        search_mode: Search strategy:
              "exact"    — name or qualified_name must match exactly
              "fuzzy"    — case-insensitive substring (CONTAINS)
              "semantic" — vector similarity on enriched embeddings
              "hybrid"   — exact first, then backfill with semantic
        include_source: Include full source code (can be large).
        limit: Maximum number of results to return.
    """
    result = _get_store().find_entity(
        name, entity_type, search_mode, include_source, limit,
    )
    return json.dumps(result, default=str)


# ─── Tool 2 ──────────────────────────────────────────────


@mcp.tool()
def get_dependencies(
    qualified_name: str,
    relationship_types: str = "",
    depth: int = 1,
    include_source: bool = False,
) -> str:
    """Find what a code entity depends on (outgoing relationships).

    Use when asked "what does X call?", "what does X inherit from?",
    "what does X import?", or "what are X's dependencies?".

    If relationship_types is empty, auto-selects based on entity type:
      - Function → CALLS, DECORATED_BY, DATA_FLOWS_TO
      - Class    → INHERITS_FROM, COLLABORATES_WITH, DECORATED_BY,
                    IMPLEMENTS_PATTERN, DATA_FLOWS_TO
      - Module   → IMPORTS

    Args:
        qualified_name: Entity's qualified name (e.g.
              "fastapi.routing.APIRoute").  Use find_entity first if
              you only have a simple name.
        relationship_types: Comma-separated list to filter.  Options:
              CALLS, IMPORTS, INHERITS_FROM, DECORATED_BY,
              DATA_FLOWS_TO, IMPLEMENTS_PATTERN, COLLABORATES_WITH,
              RELATES_TO_CONCEPT.  Empty = auto-detect.
        depth: Traversal hops (1 = direct, 2+ = transitive).
        include_source: Include source code of dependency targets.
    """
    result = _get_store().get_dependencies(
        qualified_name, relationship_types, depth, include_source,
    )
    return json.dumps(result, default=str)


# ─── Tool 3 ──────────────────────────────────────────────


@mcp.tool()
def get_dependents(
    qualified_name: str,
    relationship_types: str = "",
    depth: int = 1,
    include_source: bool = False,
) -> str:
    """Find what depends on this entity (incoming / reverse relationships).

    Use when asked "what calls X?", "what inherits from X?",
    "what imports X?", or "what are X's dependents / usages?".

    Auto-selects relationship types if empty:
      - Function → CALLS (callers), DATA_FLOWS_TO (data sources)
      - Class    → INHERITS_FROM (subclasses), COLLABORATES_WITH
      - Module   → IMPORTS (importers)

    Args:
        qualified_name: Entity's qualified name.
        relationship_types: Comma-separated filter (same options as
              get_dependencies).  Empty = auto-detect.
        depth: Reverse traversal hops.
        include_source: Include source code of dependent entities.
    """
    result = _get_store().get_dependents(
        qualified_name, relationship_types, depth, include_source,
    )
    return json.dumps(result, default=str)


# ─── Tool 4 ──────────────────────────────────────────────


@mcp.tool()
def trace_imports(
    module_name: str,
    direction: str = "outgoing",
    depth: int = 3,
    include_names: bool = True,
) -> str:
    """Follow module import chains in the knowledge graph.

    Use when asked "what does module X import?", "what modules import X?",
    "trace the import chain for X", or "show the dependency tree".

    Import edges carry metadata: symbol names imported, whether it is
    relative, TYPE_CHECKING-only, conditional, or try/except fallback.

    Args:
        module_name: Module qualified name (e.g. "fastapi.routing",
              "fastapi.dependencies.utils").  Use find_entity with
              entity_type="module" if unsure of the exact name.
        direction: "outgoing" = what this module imports,
                   "incoming" = what imports this module,
                   "both"     = both directions.
        depth: How many hops to follow (1-5).
        include_names: Include the specific symbol names imported
              at each edge (e.g. ["APIRoute", "Request"]).
    """
    result = _get_store().trace_imports(
        module_name, direction, depth, include_names,
    )
    return json.dumps(result, default=str)


# ─── Tool 5 ──────────────────────────────────────────────


@mcp.tool()
def find_related(
    entity_name: str,
    relationship_type: str,
    direction: str = "both",
    target_type: str = "",
    limit: int = 25,
) -> str:
    """Get entities connected by a specific relationship type.

    Use for targeted graph exploration: "what patterns does X implement?",
    "what decorators are on X?", "what domain concepts relate to X?",
    "what does this class contain?", "what attributes does this class have?".

    Args:
        entity_name: Name or qualified name of the entity.
        relationship_type: Exactly one of:
              CALLS, CONTAINS, INHERITS_FROM, IMPORTS, DECORATED_BY,
              HAS_PARAMETER, HAS_ATTRIBUTE, IMPLEMENTS_PATTERN,
              RELATES_TO_CONCEPT, COLLABORATES_WITH, DATA_FLOWS_TO.
        direction: "outgoing" (entity→target), "incoming" (target→entity),
                   "both".
        target_type: Filter target node label.  One of:
              Function, Class, Module, File, Decorator, Parameter,
              ClassAttribute, DesignPattern, DomainConcept.  Empty = any.
        limit: Maximum results.
    """
    result = _get_store().find_related(
        entity_name, relationship_type, direction, target_type, limit,
    )
    return json.dumps(result, default=str)


# ─── Tool 6 ──────────────────────────────────────────────


@mcp.tool()
def execute_query(
    cypher: str,
    params: str = "{}",
) -> str:
    """Run a custom read-only Cypher query against the knowledge graph.

    Use as a last resort when the other tools cannot express what you
    need.  The query MUST be read-only — any write keywords (CREATE,
    MERGE, DELETE, SET, REMOVE, DROP) will be rejected.

    Useful for: aggregations, complex path queries, counting,
    or combining multiple match clauses.

    Example:
        cypher: "MATCH (c:Class)-[:INHERITS_FROM*1..3]->(base:Class)
                 RETURN c.name, collect(base.name) AS chain"
        params: "{}"

    Args:
        cypher: A valid read-only Cypher query string.
              Use $param_name for parameterised values.
        params: JSON-encoded dict of query parameters.
              E.g. '{"name": "FastAPI", "depth": 2}'.
    """
    parsed_params = json.loads(params) if params else {}
    result = _get_store().execute_query(cypher, parsed_params)
    return json.dumps(result, default=str)


# ─── Tool 7 ──────────────────────────────────────────────


@mcp.tool()
def get_subgraph(
    entity_names: str,
    hops: int = 2,
    include_source: bool = True,
) -> str:
    """Bidirectional graph expansion from seed entities.

    This is the primary tool for building rich context slices.  It finds
    the seed entities, then expands N hops in BOTH directions across all
    structural and semantic relationships, collecting every node and edge
    in the expansion.

    Use when you need comprehensive context for a complex question —
    e.g. "explain the request lifecycle" (seeds: Request,APIRoute,
    solve_dependencies) with hops=2 gives the full neighbourhood.

    Returns: nodes with properties, edges with types, and source code
    snippets — the "graph-guided context" for the Code Analyst.

    Args:
        entity_names: Comma-separated seed entity names or qualified
              names.  E.g. "FastAPI,APIRoute,Depends" or
              "fastapi.applications.FastAPI,fastapi.params.Depends".
        hops: Expansion radius in each direction (1-3).
              1 = immediate neighbours, 2 = two hops (recommended),
              3 = wide context (may be large).
        include_source: Include source code for each entity in the slice.
    """
    names = [n.strip() for n in entity_names.split(",") if n.strip()]
    result = _get_store().get_subgraph(names, hops, include_source)
    return json.dumps(result, default=str)


# ─── Entry point ──────────────────────────────────────────

# Create the ASGI app for uvicorn
app = mcp.sse_app

if __name__ == "__main__":
    import uvicorn

    settings = _get_settings()
    host = getattr(settings, 'host', '0.0.0.0')
    port = getattr(settings, 'port', 8003)

    logger.info(f"Starting Graph Query MCP server (SSE transport on {host}:{port})")

    # For SSE transport, use uvicorn with the module path
    uvicorn.run(
        "src.agents.graph_query.server:app",
        host=host,
        port=port,
        log_level="info",
    )
