"""
Code Analyst Agent — MCP Server #4

Exposes six read-only tools that query the enriched Neo4j knowledge graph
via ``langchain_neo4j.Neo4jGraph``.  Each tool's docstring is designed to
be read by the orchestrator LLM so it knows *when* and *how* to call it.

Run as:  python -m src.agents.code_analyst.server        (stdio transport)
"""

import json
import logging

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from src.agents.code_analyst.config import CodeAnalystSettings
from src.agents.code_analyst.graph_context import GraphContextRetriever
from src.shared.logging import setup_logging

logger = setup_logging("code_analyst", level="INFO")

# ─── Shared resources (lazy init) ─────────────────────────

# Configure transport security to allow Docker service names
transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=False,
    allowed_hosts=["code_analyst", "code_analyst:8004", "localhost", "127.0.0.1", "0.0.0.0"],
    allowed_origins=["*"],
)

mcp = FastMCP("CodeAnalyst", transport_security=transport_security)

_settings: CodeAnalystSettings | None = None
_retriever: GraphContextRetriever | None = None


def _get_settings() -> CodeAnalystSettings:
    """Lazy-initialise settings from environment variables."""
    global _settings
    if _settings is None:
        _settings = CodeAnalystSettings()
    return _settings


def _get_retriever() -> GraphContextRetriever:
    """Lazy-initialise the retriever on first tool call."""
    global _retriever
    if _retriever is None:
        _retriever = GraphContextRetriever(_get_settings())
    return _retriever


# ─── Tool 1 ──────────────────────────────────────────────


@mcp.tool()
def analyze_function(
    name: str,
    depth: int = 1,
    include_source: bool = True,
) -> str:
    """Deep analysis of a function/method from the FastAPI knowledge graph.

    Use when asked what a function does, its complexity, side effects,
    callers, or callees.  Returns enriched metadata including purpose,
    summary, complexity rating, side effects, design patterns, domain
    concepts, parameters with explanations, callers (up to *depth* hops),
    callees, decorators, data-flow targets, and file location.

    Args:
        name: Function name (e.g. "solve_dependencies") or fully qualified
              name (e.g. "fastapi.dependencies.utils.solve_dependencies").
              If ambiguous, qualified_name gives an exact match.
        depth: Number of hops to traverse for caller/callee chains.
               0 = just this function, 1 = immediate neighbours, 2+ = deeper.
        include_source: Whether to include the full source code.
    """
    logger.info("[analyze_function] INPUT  name=%r, depth=%d, include_source=%s", name, depth, include_source)
    result = _get_retriever().get_function_analysis(name, depth, include_source)
    output = json.dumps(result, default=str)
    return output


# ─── Tool 2 ──────────────────────────────────────────────


@mcp.tool()
def analyze_class(
    name: str,
    include_methods: bool = True,
    include_attributes: bool = True,
    include_inheritance: bool = True,
) -> str:
    """Comprehensive class analysis from the FastAPI knowledge graph.

    Use when asked about a class's role, responsibilities, methods,
    attributes, or inheritance hierarchy.  Returns purpose, summary,
    architectural role, key methods, design patterns, domain concepts,
    method details (name, purpose, complexity, is_async), class
    attributes (name, type, default), decorators, base classes,
    subclasses, collaborators, and file location.

    Args:
        name: Class name (e.g. "APIRoute") or qualified name
              (e.g. "fastapi.routing.APIRoute").
        include_methods: Include per-method details.
        include_attributes: Include class-level attributes.
        include_inheritance: Include base classes (up) and subclasses (down).
    """
    logger.info("[analyze_class] INPUT  name=%r, include_methods=%s, include_attributes=%s, include_inheritance=%s", name, include_methods, include_attributes, include_inheritance)
    result = _get_retriever().get_class_analysis(
        name, include_methods, include_attributes, include_inheritance,
    )
    output = json.dumps(result, default=str)
    return output


# ─── Tool 3 ──────────────────────────────────────────────


@mcp.tool()
def find_patterns(
    pattern_name: str = "",
    module_scope: str = "",
    include_source: bool = False,
) -> str:
    """Find design patterns in the FastAPI codebase.

    Use when asked about design patterns, best practices, or architectural
    decisions.  Can find a specific pattern (e.g. "factory",
    "dependency_injection"), patterns within a module scope, or list all
    patterns with counts.  Returns each pattern with its implementing
    entities (name, type, purpose, and optionally source code).

    Args:
        pattern_name: Specific pattern to find (e.g. "factory",
                      "dependency_injection", "decorator", "middleware").
                      Empty string returns all patterns with counts.
        module_scope: Limit results to entities inside this module or file
                      path (e.g. "fastapi.routing" or "fastapi/routing.py").
        include_source: Include source code of implementing entities.
    """
    logger.info("[find_patterns] INPUT  pattern_name=%r, module_scope=%r, include_source=%s", pattern_name, module_scope, include_source)
    result = _get_retriever().get_patterns(pattern_name, module_scope, include_source)
    output = json.dumps(result, default=str)
    return output


# ─── Tool 4 ──────────────────────────────────────────────


@mcp.tool()
def get_code_snippet(
    name: str,
    neighborhood: int = 1,
    include_imports: bool = False,
) -> str:
    """Extract source code with surrounding graph context.

    Use when you need to show actual code to the user or need source code
    to include in your answer.  Returns the entity's source code plus
    related entities' source within *neighborhood* hops (callers, callees,
    or methods), file path, parent class context, and optionally the
    file's import statements.

    Args:
        name: Entity name or qualified name.
        neighborhood: Hops of related code to include.
                      0 = just this entity, 1 = immediate callers/callees
                      with their source, 2 = two hops of surrounding code.
        include_imports: Include the file's import statements.
    """
    logger.info("[get_code_snippet] INPUT  name=%r, neighborhood=%d, include_imports=%s", name, neighborhood, include_imports)
    result = _get_retriever().get_code_snippet(name, neighborhood, include_imports)
    output = json.dumps(result, default=str)
    return output


# ─── Tool 5 ──────────────────────────────────────────────


@mcp.tool()
def explain_implementation(
    name: str,
    follow_data_flow: bool = True,
    follow_calls: bool = True,
    max_depth: int = 3,
) -> str:
    """Explain how code works by tracing data flow and call chains.

    Use for "how does X work?" or "what happens when X is called?"
    questions.  Traces DATA_FLOWS_TO edges to show downstream data
    movement and CALLS edges to show the execution chain, each up to
    *max_depth* hops.  Returns purpose, summary, parameter explanations,
    decorators, domain concepts, an ordered data-flow chain, and an
    ordered call chain.

    Args:
        name: Entity name or qualified name.
        follow_data_flow: Trace DATA_FLOWS_TO edges to show downstream
                          data movement.
        follow_calls: Trace CALLS edges to show the execution chain.
        max_depth: Maximum hops when tracing chains (1-5).
    """
    logger.info("[explain_implementation] INPUT  name=%r, follow_data_flow=%s, follow_calls=%s, max_depth=%d", name, follow_data_flow, follow_calls, max_depth)
    result = _get_retriever().get_implementation_details(
        name, follow_data_flow, follow_calls, max_depth,
    )
    output = json.dumps(result, default=str)
    return output


# ─── Tool 6 ──────────────────────────────────────────────


@mcp.tool()
def compare_implementations(
    name_a: str,
    name_b: str,
    include_source: bool = True,
    include_relationships: bool = True,
) -> str:
    """Compare two code entities (functions or classes) side by side.

    Use for comparison questions like "how do Path and Query differ?" or
    "compare APIRoute and APIRouter".  Fetches both entities' full
    properties and enrichment (purpose, complexity, patterns, concepts,
    parameters, decorators) and optionally their relationship context
    (callers, callees, inheritance, collaborators).

    Args:
        name_a: First entity name or qualified name.
        name_b: Second entity name or qualified name.
        include_source: Include full source code of both entities.
        include_relationships: Include callers, callees, patterns, and
                               concepts for both entities.
    """
    logger.info("[compare_implementations] INPUT  name_a=%r, name_b=%r, include_source=%s, include_relationships=%s", name_a, name_b, include_source, include_relationships)
    result = _get_retriever().compare_entities(
        name_a, name_b, include_source, include_relationships,
    )
    output = json.dumps(result, default=str)
    return output


# ─── Entry point ──────────────────────────────────────────

# Create the ASGI app for uvicorn
app = mcp.sse_app

if __name__ == "__main__":
    import uvicorn

    settings = _get_settings()
    host = getattr(settings, 'host', '0.0.0.0')
    port = getattr(settings, 'port', 8004)

    logger.info(f"Starting Code Analyst MCP server (SSE transport on {host}:{port})")

    # For SSE transport, use uvicorn with the module path
    uvicorn.run(
        "src.agents.code_analyst.server:app",
        host=host,
        port=port,
        log_level="info",
    )
