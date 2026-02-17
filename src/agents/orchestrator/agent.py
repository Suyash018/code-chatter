"""
Orchestrator Agent — LangChain ReAct agent that coordinates all sub-agents.

Unlike the other agents, the orchestrator does NOT connect to its own MCP
server subprocess.  Instead, it creates its four tools in-process to avoid
nested-subprocess issues on Windows (MCP stdio pipes conflict when an MCP
server spawns child MCP servers).

The sub-agents (graph_query, code_analyst, indexer) are still each backed
by their own MCP server subprocesses — but they are spawned from the main
process via the AgentRouter, which lives here.

Tools exposed to the LLM:
1. analyze_query       — QueryAnalyzer (LLM call, in-process)
2. get_conversation_context — ContextManager (in-memory, in-process)
3. route_to_agents     — AgentRouter (spawns sub-agent MCP subprocesses)
4. synthesize_response — ResponseSynthesizer (LLM call, in-process)
"""

import json
import sys
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langchain.agents import create_agent
from langgraph.checkpoint.memory import MemorySaver

from src.agents.orchestrator.config import OrchestratorSettings
from src.agents.orchestrator.context_manager import ContextManager
from src.agents.orchestrator.query_analyzer import QueryAnalyzer
from src.agents.orchestrator.router import AgentRouter
from src.agents.orchestrator.synthesizer import ResponseSynthesizer
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


# ─── Tool factories ──────────────────────────────────────


def _build_tools(
    analyzer: QueryAnalyzer,
    context_mgr: ContextManager,
    router: AgentRouter,
    synthesizer: ResponseSynthesizer,
) -> list:
    """Create the four LangChain tools backed by in-process components."""

    @tool
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

        Args:
            query: The user's question about the FastAPI codebase.
            session_id: Optional session identifier for multi-turn context.
        """
        context_summary = ""
        if session_id:
            context_summary = context_mgr.get_context_summary(session_id)

        result = await analyzer.analyze(query, context_summary)
        return json.dumps(result, default=str)

    @tool
    def get_conversation_context(session_id: str, max_turns: int = 10) -> str:
        """Retrieve conversation history and context for a session.

        Call this when the query appears to be a follow-up (intent="follow_up"
        from analyze_query) or when you need to understand what was discussed
        previously in the conversation.

        Returns turn_count, entities_discussed, recent_turns, last_intent,
        and last_agents_called.

        Args:
            session_id: The session identifier to look up.
            max_turns: Maximum number of recent turns to include (default 10).
        """
        result = context_mgr.get_context(session_id, max_turns)
        return json.dumps(result, default=str)

    @tool
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
        - code_explanation → graph_query → code_analyst
        - dependency_query → graph_query only
        - indexing_operation → indexer only
        - general_question → graph_query → code_analyst
        (graph_query output feeds into code_analyst as context)

        Args:
            query: The user's original question.
            intent: The classified intent from analyze_query.
            entities: JSON array of entity names from analyze_query.
            session_id: Optional session identifier for context updates.
        """
        try:
            entity_list = json.loads(entities) if entities else []
        except json.JSONDecodeError:
            entity_list = []

        analysis = {"intent": intent, "entities": entity_list}
        result = await router.route(query, analysis)

        # Update conversation context
        if session_id:
            summary_parts = []
            for agent_name, output in result.get("outputs", {}).items():
                summary_parts.append(f"{agent_name}: {output[:200]}")
            context_mgr.update_context(
                session_id=session_id,
                query=query,
                intent=intent,
                entities=entity_list,
                agents_called=result.get("agents_called", []),
                summary=" | ".join(summary_parts),
            )

        return json.dumps(result, default=str)

    @tool
    async def synthesize_response(
        query: str,
        agent_outputs: str,
        errors: str = "{}",
    ) -> str:
        """Combine outputs from multiple agents into a coherent final response.

        Call this as the LAST step, after route_to_agents has collected results.
        Pass the agent_outputs and errors from the routing result.

        Args:
            query: The user's original question.
            agent_outputs: JSON object mapping agent names to their output text.
            errors: JSON object mapping agent names to error messages.
        """
        try:
            outputs_dict = json.loads(agent_outputs) if agent_outputs else {}
        except json.JSONDecodeError:
            outputs_dict = {}

        try:
            errors_dict = json.loads(errors) if errors else {}
        except json.JSONDecodeError:
            errors_dict = {}

        result = await synthesizer.synthesize(query, outputs_dict, errors_dict)
        return json.dumps(result, default=str)

    return [analyze_query, get_conversation_context, route_to_agents, synthesize_response]


# ─── Agent class ──────────────────────────────────────────


class OrchestratorAgent:
    """LangChain ReAct agent that coordinates sub-agents via local tools.

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
        agent: Any,
        router: AgentRouter,
        formatter: ResponseFormatter,
    ) -> None:
        self._agent = agent
        self._router = router
        self._formatter = formatter

    # ─── Factory ──────────────────────────────────────────

    @classmethod
    async def create(
        cls,
        settings: OrchestratorSettings | None = None,
    ) -> "OrchestratorAgent":
        """Initialise components and build the agent.

        Args:
            settings: Optional settings override.  Falls back to env vars.
        """
        settings = settings or OrchestratorSettings()

        # Create in-process components
        analyzer = QueryAnalyzer(settings)
        context_mgr = ContextManager(max_turns=settings.max_context_turns)
        router = AgentRouter(settings)
        synthesizer = ResponseSynthesizer(settings)

        # Build local tools
        tools = _build_tools(analyzer, context_mgr, router, synthesizer)
        logger.info(
            "Created %d orchestrator tools: %s",
            len(tools),
            [t.name for t in tools],
        )

        model = get_openai_model(settings.orchestrator_model)
        checkpointer = MemorySaver()

        agent = create_agent(
            model,
            tools,
            system_prompt=SYSTEM_PROMPT,
            name="orchestrator_agent",
            checkpointer=checkpointer,
        )

        formatter = ResponseFormatter()

        return cls(agent=agent, router=router, formatter=formatter)

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
        # Include session_id in the message so tools can use it
        user_content = f"[session_id={session_id}] {query}"

        result = await self._agent.ainvoke(
            {"messages": [HumanMessage(content=user_content)]},
            config={"configurable": {"thread_id": session_id}},
        )

        # Extract the final AI message
        raw_response = "I was unable to produce an answer for this query."
        messages = result.get("messages", [])
        for msg in reversed(messages):
            if hasattr(msg, "content") and msg.content and msg.type == "ai":
                if not getattr(msg, "tool_calls", None) or msg.content:
                    raw_response = msg.content
                    break

        # Format the response with response + suggestive_pills
        formatted = self._formatter.format_response(raw_response)
        return formatted

    # ─── Cleanup ──────────────────────────────────────────

    async def close(self) -> None:
        """Shut down all sub-agent connections."""
        await self._router.close()
        logger.info("Orchestrator agent shut down")
