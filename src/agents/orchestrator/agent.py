"""
Orchestrator Agent — LangChain ReAct agent that connects to the
Orchestrator MCP server and coordinates all sub-agents.

This is the entry point the FastAPI gateway calls.  It:

1. Connects to the Orchestrator MCP server via ``MultiServerMCPClient``
2. Loads the four orchestration tools as LangChain ``BaseTool`` objects
3. Wraps them in a ``create_react_agent`` loop with a system prompt
4. Exposes ``invoke(query, session_id)`` → formatted response dict

The Orchestrator MCP server (server.py) owns the sub-agent routing
internally — when route_to_agents is called, the server spawns MCP
connections to graph_query, code_analyst, and indexer servers.
"""

import sys
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

from src.agents.orchestrator.config import OrchestratorSettings
from src.agents.response_formatter.format import ResponseFormatter
from src.shared.llms.models import get_openai_model
from src.shared.logging import setup_logging

logger = setup_logging("orchestrator.agent", level="INFO")

# ─── System prompt ────────────────────────────────────────

SYSTEM_PROMPT = """\
You are the orchestrator agent for a FastAPI codebase Q&A system.
You coordinate specialist agents to answer questions about the FastAPI
source code using a Neo4j knowledge graph.

## Your Tools

1. analyze_query — Classify the query intent and extract code entities.
2. get_conversation_context — Retrieve prior conversation history for a session.
3. route_to_agents — Send the query to specialist agents and collect results.
4. synthesize_response — Merge agent outputs into a coherent answer.

## Workflow (follow this for EVERY query)

1. ALWAYS call analyze_query first to understand the query intent and entities.
2. If the intent is "follow_up" or the session has prior turns, call
   get_conversation_context to retrieve history and resolve references.
3. Call route_to_agents with the intent and entities from step 1.
   This dispatches to the right agents (graph_query, code_analyst, indexer).
4. Call synthesize_response with the agent outputs and any errors from step 3.
5. Return the synthesized response to the user.

## Response Format

Your final response will be formatted into the following structure:
{
    "response": "The response to show the user.",
    "suggestive_pills": ["Follow-up question 1", "Follow-up question 2"]
}

- The "response" field contains your full answer to the user's query.
- The "suggestive_pills" field contains up to 3 short follow-up questions
  (each under 5 words) that the user might want to ask next based on the
  current answer.

Keep this format in mind when crafting your synthesized response. Make sure
your answer is self-contained in the response field and the suggestive pills
are relevant follow-up questions.

## Important Notes

- Always pass the session_id to analyze_query and route_to_agents so that
  conversation context is maintained across turns.
- The route_to_agents tool handles the sequential pipeline internally:
  graph_query runs first, then its output feeds into code_analyst.
- If analyze_query returns low confidence, proceed anyway — the agents
  will still attempt to answer.
- Never fabricate information. Only report what the agents return.
"""


# ─── Agent class ──────────────────────────────────────────


class OrchestratorAgent:
    """LangChain ReAct agent backed by the Orchestrator MCP server.

    Connects to the Orchestrator MCP server (server.py) which exposes
    four tools: analyze_query, get_conversation_context, route_to_agents,
    and synthesize_response.  The server internally manages sub-agent
    MCP connections to graph_query, code_analyst, and indexer.

    Uses MemorySaver to maintain conversation history across turns
    within the same session_id.

    Usage::

        agent = await OrchestratorAgent.create()
        answer = await agent.invoke("What is the FastAPI class?")
        follow_up = await agent.invoke(
            "What are its methods?", session_id="session-1"
        )
        await agent.close()
    """

    def __init__(
        self,
        client: MultiServerMCPClient,
        agent: Any,
        formatter: ResponseFormatter,
    ) -> None:
        self._client = client
        self._agent = agent
        self._formatter = formatter

    # ─── Factory ──────────────────────────────────────────

    @classmethod
    async def create(
        cls,
        settings: OrchestratorSettings | None = None,
    ) -> "OrchestratorAgent":
        """Initialise the MCP client, load tools, and build the agent.

        Args:
            settings: Optional settings override.  Falls back to env vars.
        """
        logger.info("Creating OrchestratorAgent...")
        settings = settings or OrchestratorSettings()
        logger.debug("Using model: %s", settings.orchestrator_model)

        # Connect to the Orchestrator MCP server over stdio
        logger.info("Connecting to Orchestrator MCP server via stdio...")
        client = MultiServerMCPClient(
            {
                "orchestrator": {
                    "command": sys.executable,
                    "args": ["-m", "src.agents.orchestrator.server"],
                    "transport": "stdio",
                },
            }
        )

        logger.info("Loading tools from Orchestrator MCP server...")
        tools = await client.get_tools()
        logger.info(
            "Loaded %d tools from Orchestrator MCP server: %s",
            len(tools),
            [t.name for t in tools],
        )

        logger.info("Initializing LLM model and creating ReAct agent...")
        model = get_openai_model(settings.orchestrator_model)
        checkpointer = MemorySaver()

        agent = create_react_agent(
            model,
            tools,
            prompt=SYSTEM_PROMPT,
            name="orchestrator_agent",
            checkpointer=checkpointer,
        )

        formatter = ResponseFormatter()
        logger.info("OrchestratorAgent created successfully")

        return cls(client=client, agent=agent, formatter=formatter)

    # ─── Invoke ───────────────────────────────────────────

    async def invoke(
        self,
        query: str,
        session_id: str = "default",
    ) -> dict:
        """Run the orchestrator on a user query and return the formatted answer.

        Args:
            query: The user's question about the FastAPI codebase.
            session_id: Session identifier for conversation continuity.
                Uses MemorySaver with thread_id = session_id so the
                agent sees full message history across turns.

        Returns:
            A dict with "response" (str) and "suggestive_pills" (list[str]).
        """
        logger.info("OrchestratorAgent.invoke called - session_id=%s", session_id)
        logger.debug("Query: %s", query)

        # Include session_id in the message so tools can use it
        user_content = f"[session_id={session_id}] {query}"

        logger.info("Invoking ReAct agent...")
        result = await self._agent.ainvoke(
            {"messages": [HumanMessage(content=user_content)]},
            config={"configurable": {"thread_id": session_id}},
        )
        logger.debug("Agent returned %d messages in conversation", len(result.get("messages", [])))

        # Extract the final AI message
        logger.debug("Extracting final AI response from %d messages", len(result.get("messages", [])))
        raw_response = "I was unable to produce an answer for this query."
        messages = result.get("messages", [])
        for msg in reversed(messages):
            if hasattr(msg, "content") and msg.content and msg.type == "ai":
                if not getattr(msg, "tool_calls", None) or msg.content:
                    raw_response = msg.content
                    logger.debug("Found AI response: %s...", raw_response[:100])
                    break

        # Format the response with response + suggestive_pills
        logger.info("Formatting response...")
        formatted = await self._formatter.format_response(raw_response)
        logger.info("OrchestratorAgent.invoke completed successfully")
        logger.debug("Formatted response keys: %s", list(formatted.keys()))
        return formatted

    # ─── Cleanup ──────────────────────────────────────────

    async def close(self) -> None:
        """Release the MCP client reference (sessions are per-call, no persistent connection)."""
        self._client = None
        self._agent = None
        logger.info("Orchestrator agent shut down")
