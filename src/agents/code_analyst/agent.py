"""
Code Analyst Agent — LangChain ReAct agent that connects to the
Code Analyst MCP server and reasons about code using an LLM.

This is the entry point the orchestrator calls.  It:

1. Connects to the Code Analyst MCP server via ``MultiServerMCPClient``
2. Loads the six analysis tools as LangChain ``BaseTool`` objects
3. Wraps them in a ``create_agent`` loop with a system prompt
4. Exposes ``invoke(query, context)`` → natural-language answer
"""

import logging
import sys
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent

from src.agents.code_analyst.config import CodeAnalystSettings
from src.shared.llms import get_openai_model
from src.shared.logging import setup_logging
from src.shared.observability import MCPTraceContextInterceptor, is_langfuse_enabled

logger = setup_logging("code_analyst.agent", level="INFO")

# ─── System prompt ────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are a code analysis expert for the FastAPI repository. "
    "You have access to tools that query an enriched Neo4j knowledge graph "
    "containing every class, function, method, parameter, decorator, import, "
    "and their relationships in the FastAPI codebase.\n\n"
    "The graph has been enriched with LLM-generated semantic analysis: "
    "every function and class has a purpose summary, complexity rating, "
    "design patterns, and domain concepts. Relationships include calls, "
    "data flow, inheritance, and collaboration.\n\n"
    "Strategy for answering:\n"
    "- 'What does X do?' → use analyze_function or analyze_class\n"
    "- 'How does X work?' → use explain_implementation "
    "(traces data flow and call chains)\n"
    "- 'What patterns are used?' → use find_patterns\n"
    "- 'Show me the code for X' → use get_code_snippet\n"
    "- 'Compare X and Y' → use compare_implementations\n"
    "- For complex questions → call multiple tools, then synthesize "
    "a complete answer\n\n"
    "Guidelines:\n"
    "- Always cite specific function and class names from tool output.\n"
    "- Include short code snippets when they help illustrate the answer.\n"
    "- If context is provided with the query, focus your analysis on "
    "the mentioned entities first.\n"
    "- If a tool returns found=False or 'not found in graph', stop immediately "
    "and move on — do NOT retry with alternative names or qualified names.\n"
    "- Call at most 3 tools per query. Stop calling tools as soon as you have "
    "enough information to answer the question.\n"
    "- If all looked-up entities are not found, provide a concise summary based "
    "on the graph_query context you already have, without making further calls."
)


# ─── Agent class ──────────────────────────────────────────


class CodeAnalystAgent:
    """LangChain ReAct agent backed by the Code Analyst MCP server.

    Usage::

        agent = await CodeAnalystAgent.create()
        answer = await agent.invoke("What is the FastAPI class?")
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
        settings: CodeAnalystSettings | None = None,
    ) -> "CodeAnalystAgent":
        """Initialise the MCP client, load tools, and build the agent.

        Args:
            settings: Optional settings override.  Falls back to env vars.
        """
        import os
        logger.info("Creating CodeAnalystAgent...")
        settings = settings or CodeAnalystSettings()
        logger.info("Using analysis model: %s", settings.analysis_model)

        # Connect via HTTP/SSE to the code_analyst service
        code_analyst_url = os.getenv("CODE_ANALYST_URL", "http://code_analyst:8004/sse")
        logger.info("Connecting to Code Analyst MCP server at %s...", code_analyst_url)
        client = MultiServerMCPClient(
            {
                "code_analyst": {
                    "url": code_analyst_url,
                    "transport": "sse",
                },
            },
            tool_interceptors=[MCPTraceContextInterceptor()] if is_langfuse_enabled() else [],
        )

        logger.info("Loading tools from Code Analyst MCP server...")
        tools = await client.get_tools()
        logger.info(
            "Loaded %d tools from Code Analyst MCP server: %s",
            len(tools),
            [t.name for t in tools],
        )

        logger.info("Initializing LLM model and creating ReAct agent...")
        model = get_openai_model(settings.analysis_model)

        agent = create_react_agent(
            model,
            tools,
            prompt=SYSTEM_PROMPT,
            name="code_analyst_agent",
        )

        logger.info("CodeAnalystAgent created successfully")
        return cls(client=client, agent=agent)

    # ─── Invoke ───────────────────────────────────────────

    async def invoke(
        self,
        query: str,
        context: str = "",
    ) -> str:
        """Run the agent on a user query and return a natural-language answer.

        Args:
            query: The user's question about the FastAPI codebase.
            context: Optional additional context, e.g. entity names
                     discovered by the Graph Query Agent.

        Returns:
            A synthesised natural-language answer string.
        """
        logger.info("CodeAnalystAgent.invoke called")
        logger.debug("Query: %s", query)
        logger.info("Context provided: %d characters", len(context))
        if context:
            logger.debug("Context preview: %s...", context[:200])

        user_content = query
        if context:
            user_content = f"Context: {context}\n\nQuestion: {query}"

        logger.info("Invoking ReAct agent...")
        result = await self._agent.ainvoke(
            {"messages": [HumanMessage(content=user_content)]},
        )

        # Extract the final AI message from the conversation
        logger.debug("Agent returned %d messages", len(result.get("messages", [])))
        messages = result.get("messages", [])

        for msg in reversed(messages):
            if hasattr(msg, "content") and msg.content and msg.type == "ai":
                # Skip messages that are pure tool calls with no text
                if not getattr(msg, "tool_calls", None) or msg.content:
                    logger.info("CodeAnalystAgent.invoke completed successfully (%d chars)", len(msg.content))
                    logger.debug("Response preview: %s...", msg.content[:200])
                    return msg.content

        logger.warning("No analysis could be produced for this query")
        return "I was unable to produce an analysis for this query."

    # ─── Cleanup ──────────────────────────────────────────

    async def close(self) -> None:
        """Release the MCP client reference (sessions are per-call, no persistent connection)."""
        self._client = None
        self._agent = None
        logger.info("Code Analyst agent shut down")
