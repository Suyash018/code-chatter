"""
Orchestrator Agent — MCP Server #1

Central coordinator that routes queries and synthesizes responses.
Manages conversation context and coordinates sequential agent calls.

Run as:  python -m src.agents.orchestrator.server        (stdio transport)
"""

import json

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from src.agents.orchestrator.config import OrchestratorSettings
from src.agents.orchestrator.context_manager import ContextManager
from src.agents.orchestrator.query_analyzer import QueryAnalyzer
from src.agents.orchestrator.router import AgentRouter
from src.agents.orchestrator.synthesizer import ResponseSynthesizer
from src.shared.logging import setup_logging

logger = setup_logging("orchestrator.server", level="INFO")

# ─── Shared resources (lazy init) ─────────────────────────

# Configure transport security to allow Docker service names
# In Docker internal networks, services communicate using service names
# (e.g., orchestrator:8001) which would normally fail DNS rebinding protection
transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=False,  # Disable for Docker internal network
    allowed_hosts=[
        "orchestrator",
        "orchestrator:8001",
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
    ],
    allowed_origins=["*"],  # Allow all origins for development
)

mcp = FastMCP("Orchestrator", transport_security=transport_security)

_settings: OrchestratorSettings | None = None
_analyzer: QueryAnalyzer | None = None
_router: AgentRouter | None = None
_context_mgr: ContextManager | None = None
_synthesizer: ResponseSynthesizer | None = None


def _get_settings() -> OrchestratorSettings:
    global _settings
    if _settings is None:
        _settings = OrchestratorSettings()
    return _settings


def _get_analyzer() -> QueryAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = QueryAnalyzer(_get_settings())
    return _analyzer


def _get_router() -> AgentRouter:
    global _router
    if _router is None:
        _router = AgentRouter(_get_settings())
    return _router


def _get_context_mgr() -> ContextManager:
    global _context_mgr
    if _context_mgr is None:
        _context_mgr = ContextManager(max_turns=_get_settings().max_context_turns)
    return _context_mgr


def _get_synthesizer() -> ResponseSynthesizer:
    global _synthesizer
    if _synthesizer is None:
        _synthesizer = ResponseSynthesizer(_get_settings())
    return _synthesizer


# ─── Tool 1: analyze_query ────────────────────────────────


@mcp.tool()
async def analyze_query(query: str, session_id: str = "") -> str:
    """Classify the user's query intent and extract key code entities.

    This should be the FIRST tool you call for every user query.
    It determines the query intent (what kind of question it is)
    and identifies code entities mentioned (class names, function names,
    module names).

    The analysis result tells you:
    - intent: The type of question (code_explanation, dependency_query, etc.)
    - entities: Code entities mentioned (e.g. ["FastAPI", "APIRoute"])
    - requires_graph: Whether a knowledge graph lookup is needed
    - requires_analysis: Whether code analysis is needed
    - requires_indexing: Whether indexing operations are needed
    - confidence: How confident the classification is (0.0 to 1.0)

    For follow-up queries (referencing prior conversation like "What about
    its methods?"), the analyzer uses conversation context to resolve
    references. Pass a session_id if this is part of a multi-turn conversation.

    Args:
        query: The user's question about the FastAPI codebase.
               Examples:
               - "What is the FastAPI class?"
               - "What depends on APIRoute?"
               - "Compare Request and WebSocket"
               - "Index the repository"
               - "What about its methods?" (follow-up)
        session_id: Optional session identifier for multi-turn context.
                    If provided, conversation history is used to detect
                    follow-up queries and resolve entity references.
    """
    context_summary = ""
    if session_id:
        context_summary = _get_context_mgr().get_context_summary(session_id)

    result = await _get_analyzer().analyze(query, context_summary)
    return json.dumps(result, default=str)


# ─── Tool 2: route_to_agents ─────────────────────────────


@mcp.tool()
async def route_to_agents(
    query: str,
    intent: str,
    entities: str = "[]",
    session_id: str = "",
) -> str:
    """Route the query to the appropriate specialist agents and collect results.

    Call this AFTER analyze_query. Pass the intent and entities from the
    analysis result. This tool calls the right agents in sequence:

    Pipeline by intent:
    - code_explanation → graph_query → code_analyst (graph output feeds analyst)
    - code_comparison → graph_query → code_analyst
    - pattern_search → graph_query → code_analyst
    - dependency_query → graph_query only
    - architecture_query → graph_query → code_analyst
    - indexing_operation → indexer only
    - general_question → graph_query → code_analyst
    - follow_up → graph_query → code_analyst

    The graph_query agent retrieves structural context from the knowledge
    graph, which is then passed to the code_analyst for deeper analysis.

    Each agent call has a timeout and retry mechanism. If an agent fails,
    the pipeline continues with partial results and the error is recorded.

    Args:
        query: The user's original question.
        intent: The classified intent from analyze_query.
                One of: code_explanation, code_comparison, pattern_search,
                dependency_query, architecture_query, indexing_operation,
                general_question, follow_up.
        entities: JSON array of entity names from analyze_query.
                  E.g. '["FastAPI", "APIRoute"]'. Default is empty list.
        session_id: Optional session identifier. If provided, conversation
                    context is updated after routing completes.
    """
    try:
        entity_list = json.loads(entities) if entities else []
    except json.JSONDecodeError:
        entity_list = []

    analysis = {
        "intent": intent,
        "entities": entity_list,
    }

    result = await _get_router().route(query, analysis)

    # Update conversation context if session_id provided
    if session_id:
        # Build a brief summary from outputs
        summary_parts = []
        for agent_name, output in result.get("outputs", {}).items():
            summary_parts.append(f"{agent_name}: {output[:200]}")
        summary = " | ".join(summary_parts)

        _get_context_mgr().update_context(
            session_id=session_id,
            query=query,
            intent=intent,
            entities=entity_list,
            agents_called=result.get("agents_called", []),
            summary=summary,
        )

    return json.dumps(result, default=str)


# ─── Tool 3: get_conversation_context ────────────────────


@mcp.tool()
def get_conversation_context(
    session_id: str,
    max_turns: int = 10,
) -> str:
    """Retrieve conversation history and context for a session.

    Call this when the query appears to be a follow-up (intent="follow_up"
    from analyze_query) or when you need to understand what was discussed
    previously in the conversation.

    Returns:
    - turn_count: Number of prior turns in this session
    - entities_discussed: All code entities mentioned across turns
    - recent_turns: Summaries of recent turns (query, intent, agents used)
    - last_intent: The intent of the most recent turn
    - last_agents_called: Which agents handled the last turn

    Use this information to:
    1. Resolve ambiguous references ("it", "that class", "the method")
    2. Provide continuity in multi-turn conversations
    3. Avoid redundant agent calls for recently discussed topics

    Args:
        session_id: The session identifier to look up.
        max_turns: Maximum number of recent turns to include (default 10).
    """
    result = _get_context_mgr().get_context(session_id, max_turns)
    return json.dumps(result, default=str)


# ─── Tool 4: synthesize_response ─────────────────────────


@mcp.tool()
async def synthesize_response(
    query: str,
    agent_outputs: str,
    errors: str = "{}",
) -> str:
    """Combine outputs from multiple agents into a coherent final response.

    Call this as the LAST step, after route_to_agents has collected results.
    Pass the agent_outputs and errors from the routing result.

    This tool uses an LLM to merge outputs from graph_query, code_analyst,
    and/or indexer into a single well-structured answer that:
    - Combines information without redundancy
    - Preserves code entity names and code snippets
    - Notes any gaps from agent errors
    - Structures complex answers with clear sections

    Args:
        query: The user's original question.
        agent_outputs: JSON object mapping agent names to their output text.
                       E.g. '{"graph_query": "Found FastAPI class...",
                              "code_analyst": "The FastAPI class is..."}'
        errors: JSON object mapping agent names to error messages.
                E.g. '{"indexer": "Timed out after 120s"}'.
                Default is empty object "{}".
    """
    try:
        outputs_dict = json.loads(agent_outputs) if agent_outputs else {}
    except json.JSONDecodeError:
        outputs_dict = {}

    try:
        errors_dict = json.loads(errors) if errors else {}
    except json.JSONDecodeError:
        errors_dict = {}

    result = await _get_synthesizer().synthesize(query, outputs_dict, errors_dict)
    return json.dumps(result, default=str)


# ─── Entry point ──────────────────────────────────────────

# Create the ASGI app for uvicorn with disabled host validation
# The FastMCP SSE server validates Host headers for security, but in Docker
# internal networks we use service names (e.g., orchestrator:8001) which
# don't pass validation. We need to wrap the app to allow Docker service names.
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

# Allow all hosts in Docker environment (internal network only)
app = mcp.sse_app

if __name__ == "__main__":
    import uvicorn

    settings = _get_settings()
    host = getattr(settings, 'host', '0.0.0.0')
    port = getattr(settings, 'port', 8001)

    logger.info(f"Starting Orchestrator MCP server (SSE transport on {host}:{port})")

    # For SSE transport, use uvicorn with the module path
    # Set server_header to False to avoid host validation issues in Docker
    uvicorn.run(
        "src.agents.orchestrator.server:app",
        host=host,
        port=port,
        log_level="info",
        server_header=False,
        forwarded_allow_ips="*",  # Allow forwarded headers from Docker network
    )
