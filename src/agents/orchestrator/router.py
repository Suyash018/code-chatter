"""
Agent Router — route_to_agents tool implementation.

Determines which agents should handle a given query based on intent
classification, calls them in the correct order (sequential pipeline),
and collects results.
"""

import asyncio
import os

from src.agents.orchestrator.config import OrchestratorSettings
from src.shared.logging import setup_logging

logger = setup_logging("orchestrator.router", level="INFO")

# Deterministic routing: intent → ordered list of agents to call
ROUTING_MAP: dict[str, list[str]] = {
    "code_explanation": ["graph_query", "code_analyst"],
    "code_comparison": ["graph_query", "code_analyst"],
    "pattern_search": ["graph_query", "code_analyst"],
    "dependency_query": ["graph_query"],
    "architecture_query": ["graph_query", "code_analyst"],
    "indexing_operation": ["indexer"],
    "general_question": ["graph_query", "code_analyst"],
    "follow_up": ["graph_query", "code_analyst"],
}


class AgentRouter:
    """Routes queries to sub-agents based on intent classification.

    Sub-agents are lazily initialized on first use.  Each sub-agent
    is a separate MCP subprocess managed by its own agent wrapper.
    """

    def __init__(self, settings: OrchestratorSettings) -> None:
        self._settings = settings
        self._graph_query_agent = None
        self._code_analyst_agent = None
        self._indexer_agent = None

    # ─── Lazy sub-agent initialization ────────────────────

    async def _get_graph_query_agent(self):
        if self._graph_query_agent is None:
            logger.info("Initializing GraphQueryAgent (first use)...")
            from src.agents.graph_query.agent import GraphQueryAgent

            self._graph_query_agent = await GraphQueryAgent.create()
            logger.info("GraphQueryAgent initialized successfully")
        else:
            logger.debug("Reusing existing GraphQueryAgent instance")
        return self._graph_query_agent

    async def _get_code_analyst_agent(self):
        if self._code_analyst_agent is None:
            logger.info("Initializing CodeAnalystAgent (first use)...")
            from src.agents.code_analyst.agent import CodeAnalystAgent

            self._code_analyst_agent = await CodeAnalystAgent.create()
            logger.info("CodeAnalystAgent initialized successfully")
        else:
            logger.debug("Reusing existing CodeAnalystAgent instance")
        return self._code_analyst_agent

    async def _get_indexer_agent(self):
        if self._indexer_agent is None:
            logger.info("Initializing IndexerAgent (first use)...")
            from src.agents.indexer.agent import IndexerAgent

            self._indexer_agent = await IndexerAgent.create()
            logger.info("IndexerAgent initialized successfully")
        else:
            logger.debug("Reusing existing IndexerAgent instance")
        return self._indexer_agent

    # ─── Individual agent calls ───────────────────────────

    async def _call_graph_query(
        self, query: str, entities: list[str],
    ) -> str:
        logger.info("Calling GraphQueryAgent with %d entities: %s", len(entities), entities)
        agent = await self._get_graph_query_agent()
        result = await agent.invoke(query, entities=entities)
        logger.info("GraphQueryAgent returned %d characters", len(result))
        logger.debug("GraphQueryAgent result preview: %s...", result[:200])
        return result

    async def _call_code_analyst(
        self, query: str, context: str = "",
    ) -> str:
        logger.info("Calling CodeAnalystAgent with %d characters of context", len(context))
        agent = await self._get_code_analyst_agent()
        result = await agent.invoke(query, context=context)
        logger.info("CodeAnalystAgent returned %d characters", len(result))
        logger.debug("CodeAnalystAgent result preview: %s...", result[:200])
        return result

    async def _call_indexer(self, query: str) -> str:
        logger.info("Calling IndexerAgent")
        agent = await self._get_indexer_agent()
        result = await agent.invoke(query)
        logger.info("IndexerAgent returned %d characters", len(result))
        logger.debug("IndexerAgent result preview: %s...", result[:200])
        return result

    async def _call_agent_with_retry(
        self,
        agent_name: str,
        call_fn,
        *args,
        **kwargs,
    ) -> str:
        """Call an agent with timeout and retry logic."""
        timeout = self._settings.agent_timeout_seconds
        max_retries = self._settings.max_agent_retries

        logger.info("Calling %s with timeout=%ds, max_retries=%d", agent_name, timeout, max_retries)
        last_error = None
        for attempt in range(1, max_retries + 1):
            logger.debug("%s attempt %d/%d starting...", agent_name, attempt, max_retries)
            try:
                result = await asyncio.wait_for(
                    call_fn(*args, **kwargs),
                    timeout=timeout,
                )
                logger.info(
                    "%s completed successfully on attempt %d", agent_name, attempt,
                )
                return result
            except asyncio.TimeoutError:
                last_error = f"Timed out after {timeout}s"
                logger.warning(
                    "%s attempt %d/%d: %s",
                    agent_name, attempt, max_retries, last_error,
                )
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.error(
                    "%s attempt %d/%d failed: %s",
                    agent_name, attempt, max_retries, last_error,
                    exc_info=True,
                )

        error_msg = f"{agent_name} failed after {max_retries} attempts: {last_error}"
        logger.error(error_msg)
        raise RuntimeError(error_msg)

    # ─── Main routing logic ───────────────────────────────

    async def route(
        self,
        query: str,
        analysis: dict,
    ) -> dict:
        """Route a query to the appropriate agents based on analysis.

        Executes a sequential pipeline: graph_query output feeds as
        context into code_analyst.  Errors are recorded but the
        pipeline continues with partial results.

        Args:
            query: The user's original question.
            analysis: Output from QueryAnalyzer.analyze() containing
                intent, entities, and routing flags.

        Returns:
            Dict with agents_called, outputs, graph_context, pipeline,
            and errors.
        """
        intent = analysis.get("intent", "general_question")
        entities = analysis.get("entities", [])
        pipeline = ROUTING_MAP.get(intent, ["graph_query", "code_analyst"])

        logger.info("AgentRouter.route starting - intent=%s, entities=%s", intent, entities)
        logger.info("Pipeline for intent '%s': %s", intent, pipeline)

        outputs: dict[str, str] = {}
        errors: dict[str, str] = {}
        agents_called: list[str] = []
        graph_context = ""

        for idx, agent_name in enumerate(pipeline, 1):
            logger.info("Pipeline step %d/%d: calling %s", idx, len(pipeline), agent_name)
            agents_called.append(agent_name)
            try:
                if agent_name == "graph_query":
                    result = await self._call_agent_with_retry(
                        "graph_query",
                        self._call_graph_query,
                        query, entities,
                    )
                    outputs["graph_query"] = result
                    graph_context = result
                    logger.info("Pipeline step %d/%d: graph_query completed successfully", idx, len(pipeline))

                elif agent_name == "code_analyst":
                    # Feed graph_query output as context
                    logger.info("Passing %d chars of graph context to code_analyst", len(graph_context))
                    result = await self._call_agent_with_retry(
                        "code_analyst",
                        self._call_code_analyst,
                        query, graph_context,
                    )
                    outputs["code_analyst"] = result
                    logger.info("Pipeline step %d/%d: code_analyst completed successfully", idx, len(pipeline))

                elif agent_name == "indexer":
                    result = await self._call_agent_with_retry(
                        "indexer",
                        self._call_indexer,
                        query,
                    )
                    outputs["indexer"] = result
                    logger.info("Pipeline step %d/%d: indexer completed successfully", idx, len(pipeline))

            except RuntimeError as exc:
                errors[agent_name] = str(exc)
                logger.error("Pipeline step %d/%d: %s failed - %s", idx, len(pipeline), agent_name, exc)
                logger.info("Continuing pipeline with partial results...")

        logger.info(
            "Routing complete: pipeline=%s, success=%s, errors=%s",
            pipeline,
            list(outputs.keys()),
            list(errors.keys()),
        )

        return {
            "agents_called": agents_called,
            "outputs": outputs,
            "graph_context": graph_context,
            "pipeline": pipeline,
            "errors": errors,
        }

    # ─── Cleanup ──────────────────────────────────────────

    async def close(self) -> None:
        """Shut down all initialized sub-agents."""
        if self._graph_query_agent is not None:
            await self._graph_query_agent.close()
            logger.info("Closed GraphQueryAgent")
        if self._code_analyst_agent is not None:
            await self._code_analyst_agent.close()
            logger.info("Closed CodeAnalystAgent")
        if self._indexer_agent is not None:
            await self._indexer_agent.close()
            logger.info("Closed IndexerAgent")
