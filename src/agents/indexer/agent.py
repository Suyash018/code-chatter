"""
Indexer Agent — LangChain ReAct agent that connects to the
Indexer MCP server and manages repository indexing operations.

This is the entry point the orchestrator calls.  It:

1. Connects to the Indexer MCP server via ``MultiServerMCPClient``
2. Loads the five indexing tools as LangChain ``BaseTool`` objects
3. Wraps them in a ``create_agent`` loop with a system prompt
4. Exposes ``invoke(instruction)`` → result string
"""

import sys
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent

from src.agents.indexer.config import IndexerSettings
from src.shared.llms.models import get_openai_model
from src.shared.logging import setup_logging
from src.shared.observability import MCPTraceContextInterceptor, is_langfuse_enabled

logger = setup_logging("indexer.agent", level="INFO")

# ─── System prompt ────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a repository indexing specialist for the Graphical RAG system.
Your job is to manage the indexing of Python repositories into a Neo4j
knowledge graph.  You have access to tools that clone repositories, parse
Python ASTs, store entities in the graph, and run incremental updates.

## Tools Available

1. index_repository — Full repository indexing pipeline (clone → parse →
   store → resolve → LLM enrich → embed).  Returns a job_id.
2. index_file — Incremental single-file update using Strategy B fine-
   grained diffing.  Preserves enrichment on unchanged code.  Returns
   a job_id.
3. parse_python_ast — Parse Python source into a structured AST
   representation.  Pure analysis, no graph write.  Returns a job_id.
4. extract_entities — Identify code entities and relationships from
   source code.  High-level summary.  Returns a job_id.
5. get_index_status — Check a job's progress by job_id, or get an
   overview of all jobs plus graph statistics.  Returns directly.

## Workflow

- All tools except get_index_status run in the background and return
  a job_id immediately.
- After calling a tool, use get_index_status(job_id=<id>) to poll
  until the job reaches "completed" or "failed" status.
- For index_repository, poll periodically — it can take 5-30 minutes
  depending on repository size and whether enrichment is enabled.
- Once a job completes, get_index_status returns the full result.

## Strategy

- For initial indexing, use index_repository with clear_graph=True.
- For updates to specific files, use index_file (faster, preserves
  enrichment on unchanged entities).
- Use parse_python_ast or extract_entities for analysis without writing
  to the graph.
- Use get_index_status with no job_id to get an overview of the graph
  (node/edge counts, enrichment coverage, validation warnings).

Report exactly what operations were performed and their results.
Do not fabricate statistics — use the actual tool output.
"""


# ─── Agent class ──────────────────────────────────────────


class IndexerAgent:
    """LangChain ReAct agent backed by the Indexer MCP server.

    Usage::

        agent = await IndexerAgent.create()
        result = await agent.invoke("Index the FastAPI repository")
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
        settings: IndexerSettings | None = None,
    ) -> "IndexerAgent":
        """Initialise the MCP client, load tools, and build the agent.

        Args:
            settings: Optional settings override.  Falls back to env vars.
        """
        import os
        logger.info("Creating IndexerAgent...")
        settings = settings or IndexerSettings()
        logger.debug("Using model: %s", settings.enrichment_model)

        # Connect via HTTP/SSE to the indexer service
        indexer_url = os.getenv("INDEXER_URL", "http://indexer:8002/sse")
        logger.info("Connecting to Indexer MCP server at %s...", indexer_url)
        client = MultiServerMCPClient(
            {
                "indexer": {
                    "url": indexer_url,
                    "transport": "sse",
                },
            },
            tool_interceptors=[MCPTraceContextInterceptor()] if is_langfuse_enabled() else [],
        )

        logger.info("Loading tools from Indexer MCP server...")
        tools = await client.get_tools()
        logger.info(
            "Loaded %d tools from Indexer MCP server: %s",
            len(tools),
            [t.name for t in tools],
        )

        logger.info("Initializing LLM model and creating ReAct agent...")
        model = get_openai_model(settings.enrichment_model)

        agent = create_react_agent(
            model,
            tools,
            prompt=SYSTEM_PROMPT,
            name="indexer_agent",
        )

        logger.info("IndexerAgent created successfully")
        return cls(client=client, agent=agent)

    # ─── Invoke ───────────────────────────────────────────

    async def invoke(self, instruction: str) -> str:
        """Run the agent on an indexing instruction and return the result.

        Args:
            instruction: Natural-language instruction describing what
                to index (e.g. "Index the FastAPI repository with full
                enrichment", "Parse this Python code: ...").

        Returns:
            A summary string with the operation results.
        """
        logger.info("IndexerAgent.invoke called")
        logger.debug("Instruction: %s", instruction)

        logger.info("Invoking ReAct agent...")
        result = await self._agent.ainvoke(
            {"messages": [HumanMessage(content=instruction)]},
        )

        logger.debug("Agent returned %d messages", len(result.get("messages", [])))
        messages = result.get("messages", [])
        for msg in reversed(messages):
            if hasattr(msg, "content") and msg.content and msg.type == "ai":
                if not getattr(msg, "tool_calls", None) or msg.content:
                    logger.info("IndexerAgent.invoke completed successfully (%d chars)", len(msg.content))
                    logger.debug("Response preview: %s...", msg.content[:200])
                    return msg.content

        logger.warning("No indexing result could be produced for this instruction")
        return "No indexing result could be produced for this instruction."

    # ─── Cleanup ──────────────────────────────────────────

    async def close(self) -> None:
        """Release the MCP client reference (sessions are per-call, no persistent connection)."""
        self._client = None
        self._agent = None
        logger.info("Indexer agent shut down")
