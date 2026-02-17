"""
Graph Query Agent — LangChain ReAct agent that connects to the
Graph Query MCP server and retrieves structured graph context.

This is the entry point the orchestrator calls.  It:

1. Connects to the Graph Query MCP server via ``MultiServerMCPClient``
2. Loads the seven query tools as LangChain ``BaseTool`` objects
3. Wraps them in a ``create_agent`` loop with a system prompt
4. Exposes ``invoke(query, entities)`` → structured graph context string
"""

import logging
import sys
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent

from src.agents.graph_query.config import GraphQuerySettings
from src.shared.llms.models import get_openai_model
from src.shared.logging import setup_logging
from src.shared.observability import MCPTraceContextInterceptor, is_langfuse_enabled

logger = setup_logging("graph_query.agent", level="INFO")

# ─── System prompt ────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a knowledge graph query specialist for the FastAPI codebase.
Your job is to find and retrieve relevant code entities and their
relationships from a Neo4j knowledge graph.  You do NOT explain or
analyse code — that is the Code Analyst's job.  You retrieve data.

## Knowledge Graph Schema

Nodes: File, Module, Class (96), Function (264), Parameter,
ClassAttribute (272), Decorator, DesignPattern, DomainConcept

Structural edges: CONTAINS, DEFINES_MODULE, IMPORTS (395),
INHERITS_FROM, CALLS (938), DECORATED_BY, HAS_PARAMETER, HAS_ATTRIBUTE

Semantic edges (LLM-enriched): IMPLEMENTS_PATTERN, RELATES_TO_CONCEPT,
COLLABORATES_WITH, DATA_FLOWS_TO

Identity: qualified names — e.g. "fastapi.applications.FastAPI",
"fastapi.routing.APIRoute.matches"

Embeddings: Function and Class nodes have 3072-dim cosine vectors
built from purpose + summary + docstring + domain concepts.

## Strategy

1. First, find seed entities with find_entity.
   - Known name → search_mode="exact"
   - Conceptual description → search_mode="semantic"
   - Unsure → search_mode="hybrid" (tries exact, then semantic)
2. Once you have qualified names, expand context:
   - "What does X depend on?" → get_dependencies
   - "What uses X?" → get_dependents
   - "Trace imports for module" → trace_imports
   - Specific relationship → find_related
   - Broad neighbourhood context → get_subgraph (preferred for complex Qs)
3. If a name is not found, try:
   - The simple name instead of qualified name, or vice versa
   - Semantic search mode
   - Fuzzy search mode
4. For complex questions, call get_subgraph with multiple seed entities
   (comma-separated) to get the full neighbourhood in one call.
5. Use execute_query only when the other tools cannot express your need.

## Output

Compile all tool results into a structured summary.  Include:
- Entity names and qualified names found
- Key relationships discovered
- Source code snippets when relevant
Report exactly what the graph contains.  Do not fabricate entities
or relationships that were not returned by the tools.
"""


# ─── Agent class ──────────────────────────────────────────


class GraphQueryAgent:
    """LangChain ReAct agent backed by the Graph Query MCP server.

    Usage::

        agent = await GraphQueryAgent.create()
        context = await agent.invoke(
            "How does dependency injection work?",
            entities=["Depends", "solve_dependencies"],
        )
        await agent.close()
    """

    def __init__(
        self,
        client: MultiServerMCPClient,
        agent: Any,
    ) -> None:
        self._client = client
        self._agent = agent

    # ─── Factory ──────────────────────────────────────────

    @classmethod
    async def create(
        cls,
        settings: GraphQuerySettings | None = None,
    ) -> "GraphQueryAgent":
        """Initialise the MCP client, load tools, and build the agent.

        Args:
            settings: Optional settings override.  Falls back to env vars.
        """
        import os
        settings = settings or GraphQuerySettings()

        # Connect via HTTP/SSE to the graph_query service
        graph_query_url = os.getenv("GRAPH_QUERY_URL", "http://graph_query:8003/sse")
        client = MultiServerMCPClient(
            {
                "graph_query": {
                    "url": graph_query_url,
                    "transport": "sse",
                },
            },
            tool_interceptors=[MCPTraceContextInterceptor()] if is_langfuse_enabled() else [],
        )

        tools = await client.get_tools()
        logger.info(
            "Loaded %d tools from Graph Query MCP server: %s",
            len(tools),
            [t.name for t in tools],
        )

        model = get_openai_model(settings.query_model)

        agent = create_react_agent(
            model,
            tools,
            prompt=SYSTEM_PROMPT,
            name="graph_query_agent",
        )

        return cls(client=client, agent=agent)

    # ─── Invoke ───────────────────────────────────────────

    async def invoke(
        self,
        query: str,
        entities: list[str] | None = None,
    ) -> str:
        """Run the agent on a user query and return structured graph context.

        Args:
            query: The user's question about the FastAPI codebase.
            entities: Optional list of entity names to focus the search
                      (e.g. discovered by the orchestrator's query analyser).

        Returns:
            A structured context string containing entities, relationships,
            and source snippets from the knowledge graph.
        """
        parts: list[str] = []

        if entities:
            parts.append(
                f"Focus on these entities: {', '.join(entities)}"
            )

        parts.append(f"Question: {query}")
        user_content = "\n\n".join(parts)

        result = await self._agent.ainvoke(
            {"messages": [HumanMessage(content=user_content)]},
        )

        messages = result.get("messages", [])
        for msg in reversed(messages):
            if hasattr(msg, "content") and msg.content and msg.type == "ai":
                if not getattr(msg, "tool_calls", None) or msg.content:
                    return msg.content

        return "No graph context could be retrieved for this query."

    # ─── Cleanup ──────────────────────────────────────────

    async def close(self) -> None:
        """Release the MCP client reference (sessions are per-call, no persistent connection)."""
        self._client = None
        self._agent = None
        logger.info("Graph Query agent shut down")
