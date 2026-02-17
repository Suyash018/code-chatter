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

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from langchain.agents import create_agent

from src.agents.code_analyst.config import CodeAnalystSettings
from src.shared.logging import setup_logging

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
    "- If a tool returns an error (entity not found), try alternative "
    "names or qualified names before giving up."
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
        settings = settings or CodeAnalystSettings()

        # Connect to the Code Analyst MCP server over stdio
        client = MultiServerMCPClient(
            {
                "code_analyst": {
                    "command": sys.executable,
                    "args": ["-m", "src.agents.code_analyst.server"],
                    "transport": "stdio",
                },
            }
        )

        tools = await client.get_tools()
        logger.info(
            "Loaded %d tools from Code Analyst MCP server: %s",
            len(tools),
            [t.name for t in tools],
        )

        model = ChatOpenAI(
            model=settings.analysis_model,
            api_key=settings.openai_api_key,
        )

        agent = create_agent(
            model,
            tools,
            system_prompt=SYSTEM_PROMPT,
            name="code_analyst_agent",
        )

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
        user_content = query
        if context:
            user_content = f"Context: {context}\n\nQuestion: {query}"

        result = await self._agent.ainvoke(
            {"messages": [HumanMessage(content=user_content)]},
        )

        # Extract the final AI message from the conversation
        messages = result.get("messages", [])

        print("\n\n\n Messages from code analyst agent:\n\n",str(messages),"\n\n\n==============end of messages=============\n\n\n")
        for msg in reversed(messages):
            if hasattr(msg, "content") and msg.content and msg.type == "ai":
                # Skip messages that are pure tool calls with no text
                if not getattr(msg, "tool_calls", None) or msg.content:
                    return msg.content

        return "I was unable to produce an analysis for this query."

    # ─── Cleanup ──────────────────────────────────────────

    async def close(self) -> None:
        """Tear down the MCP client connection."""
        logger.info("Code Analyst agent shut down")
