"""
Unit tests for Orchestrator agent components.

All external dependencies (LLM, sub-agents, Neo4j) are mocked.
Run with: pytest tests/test_orchestrator/test_unit.py -v

Requires: pytest, pytest-asyncio
Configure pytest-asyncio mode in pyproject.toml:
    [tool.pytest.ini_options]
    asyncio_mode = "auto"
"""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# ──────────────────────────────────────────────────
# Test 1: QueryAnalyzer
# ──────────────────────────────────────────────────


class TestQueryAnalyzer:
    """Tests for QueryAnalyzer with mocked LLM."""

    @pytest.fixture
    def analyzer_and_model(self):
        with patch("src.agents.orchestrator.query_analyzer.get_openai_model") as mock_get:
            mock_model = AsyncMock()
            mock_get.return_value = mock_model
            from src.agents.orchestrator.query_analyzer import QueryAnalyzer
            from src.agents.orchestrator.config import OrchestratorSettings

            settings = OrchestratorSettings()
            analyzer = QueryAnalyzer(settings)
            yield analyzer, mock_model

    async def test_classify_code_explanation(self, analyzer_and_model):
        analyzer, mock_model = analyzer_and_model
        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "intent": "code_explanation",
            "entities": ["FastAPI"],
            "requires_graph": True,
            "requires_analysis": True,
            "requires_indexing": False,
            "confidence": 0.9,
        })
        mock_model.ainvoke.return_value = mock_response

        result = await analyzer.analyze("What is the FastAPI class?")

        assert result["intent"] == "code_explanation"
        assert "FastAPI" in result["entities"]
        assert result["requires_graph"] is True
        assert result["confidence"] == 0.9

    async def test_classify_dependency_query(self, analyzer_and_model):
        analyzer, mock_model = analyzer_and_model
        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "intent": "dependency_query",
            "entities": ["APIRoute"],
            "requires_graph": True,
            "requires_analysis": False,
            "requires_indexing": False,
            "confidence": 0.85,
        })
        mock_model.ainvoke.return_value = mock_response

        result = await analyzer.analyze("What does APIRoute depend on?")

        assert result["intent"] == "dependency_query"
        assert result["entities"] == ["APIRoute"]

    async def test_invalid_intent_falls_back(self, analyzer_and_model):
        analyzer, mock_model = analyzer_and_model
        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "intent": "INVALID_INTENT",
            "entities": [],
            "confidence": 0.8,
        })
        mock_model.ainvoke.return_value = mock_response

        result = await analyzer.analyze("Something weird")

        assert result["intent"] == "general_question"
        assert result["confidence"] <= 0.5

    async def test_json_parse_error_returns_fallback(self, analyzer_and_model):
        analyzer, mock_model = analyzer_and_model
        mock_response = MagicMock()
        mock_response.content = "not json at all"
        mock_model.ainvoke.return_value = mock_response

        result = await analyzer.analyze("Any query")

        assert result["intent"] == "general_question"
        assert result["confidence"] == 0.3
        assert result["entities"] == []

    async def test_follow_up_with_context(self, analyzer_and_model):
        analyzer, mock_model = analyzer_and_model
        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "intent": "follow_up",
            "entities": ["FastAPI"],
            "requires_graph": True,
            "requires_analysis": True,
            "requires_indexing": False,
            "confidence": 0.75,
        })
        mock_model.ainvoke.return_value = mock_response

        result = await analyzer.analyze(
            "What about its methods?",
            conversation_context="Entities discussed: FastAPI. Last intent: code_explanation.",
        )

        assert result["intent"] == "follow_up"
        call_args = mock_model.ainvoke.call_args[0][0]
        system_content = call_args[0]["content"]
        assert "Recent conversation context" in system_content

    async def test_markdown_fences_stripped(self, analyzer_and_model):
        analyzer, mock_model = analyzer_and_model
        mock_response = MagicMock()
        mock_response.content = (
            '```json\n{"intent": "code_explanation", "entities": ["Request"], "confidence": 0.9}\n```'
        )
        mock_model.ainvoke.return_value = mock_response

        result = await analyzer.analyze("What is Request?")

        assert result["intent"] == "code_explanation"
        assert "Request" in result["entities"]


# ──────────────────────────────────────────────────
# Test 2: ContextManager (pure in-memory, no mocks)
# ──────────────────────────────────────────────────


class TestContextManager:
    """Tests for ContextManager — pure in-memory, no external deps."""

    @pytest.fixture
    def ctx_mgr(self):
        from src.agents.orchestrator.context_manager import ContextManager

        return ContextManager(max_turns=5)

    def test_new_session_returns_empty_context(self, ctx_mgr):
        result = ctx_mgr.get_context("new-session")
        assert result["turn_count"] == 0
        assert result["entities_discussed"] == []
        assert result["recent_turns"] == []
        assert result["last_intent"] == ""
        assert result["last_agents_called"] == []

    def test_update_and_retrieve_context(self, ctx_mgr):
        ctx_mgr.update_context(
            session_id="s1",
            query="What is FastAPI?",
            intent="code_explanation",
            entities=["FastAPI"],
            agents_called=["graph_query", "code_analyst"],
            summary="Found FastAPI class info",
        )

        result = ctx_mgr.get_context("s1")
        assert result["turn_count"] == 1
        assert "FastAPI" in result["entities_discussed"]
        assert result["last_intent"] == "code_explanation"
        assert result["last_agents_called"] == ["graph_query", "code_analyst"]
        assert len(result["recent_turns"]) == 1

    def test_multiple_turns_accumulate(self, ctx_mgr):
        for i in range(3):
            ctx_mgr.update_context(
                session_id="s2",
                query=f"Query {i}",
                intent="general_question",
                entities=[f"Entity{i}"],
                agents_called=["graph_query"],
                summary=f"Summary {i}",
            )

        result = ctx_mgr.get_context("s2")
        assert result["turn_count"] == 3
        assert len(result["entities_discussed"]) == 3

    def test_max_turns_limits_recent_turns(self, ctx_mgr):
        for i in range(8):
            ctx_mgr.update_context(
                session_id="s3",
                query=f"Query {i}",
                intent="general_question",
                entities=[],
                agents_called=["graph_query"],
                summary=f"Sum {i}",
            )

        result = ctx_mgr.get_context("s3", max_turns=3)
        assert len(result["recent_turns"]) == 3

    def test_context_summary_empty_for_new_session(self, ctx_mgr):
        assert ctx_mgr.get_context_summary("nonexistent") == ""

    def test_context_summary_nonempty_after_update(self, ctx_mgr):
        ctx_mgr.update_context(
            session_id="s4",
            query="What is X?",
            intent="code_explanation",
            entities=["X"],
            agents_called=["graph_query"],
            summary="Found X",
        )
        summary = ctx_mgr.get_context_summary("s4")
        assert "1 prior turn" in summary
        assert "X" in summary
        assert "code_explanation" in summary

    def test_entities_deduplicated_in_get_context(self, ctx_mgr):
        ctx_mgr.update_context("s5", "q1", "general_question", ["A", "B"], [], "")
        ctx_mgr.update_context("s5", "q2", "general_question", ["B", "C"], [], "")

        result = ctx_mgr.get_context("s5")
        assert result["entities_discussed"] == ["A", "B", "C"]

    def test_separate_sessions_are_isolated(self, ctx_mgr):
        ctx_mgr.update_context("alpha", "q", "general_question", ["A"], [], "")
        ctx_mgr.update_context("beta", "q", "general_question", ["B"], [], "")

        assert ctx_mgr.get_context("alpha")["entities_discussed"] == ["A"]
        assert ctx_mgr.get_context("beta")["entities_discussed"] == ["B"]


# ──────────────────────────────────────────────────
# Test 3: ResponseSynthesizer (mock LLM)
# ──────────────────────────────────────────────────


class TestResponseSynthesizer:
    """Tests for ResponseSynthesizer with mocked LLM."""

    @pytest.fixture
    def synth_and_model(self):
        with patch("src.agents.orchestrator.synthesizer.get_openai_model") as mock_get:
            mock_model = AsyncMock()
            mock_get.return_value = mock_model
            from src.agents.orchestrator.synthesizer import ResponseSynthesizer
            from src.agents.orchestrator.config import OrchestratorSettings

            settings = OrchestratorSettings()
            synth = ResponseSynthesizer(settings)
            yield synth, mock_model

    async def test_synthesize_with_outputs(self, synth_and_model):
        synth, mock_model = synth_and_model
        mock_response = MagicMock()
        mock_response.content = "FastAPI is a modern web framework..."
        mock_model.ainvoke.return_value = mock_response

        result = await synth.synthesize(
            query="What is FastAPI?",
            agent_outputs={
                "graph_query": "Found FastAPI class",
                "code_analyst": "FastAPI is...",
            },
        )

        assert result["response"] == "FastAPI is a modern web framework..."
        assert set(result["agents_used"]) == {"graph_query", "code_analyst"}
        assert result["had_errors"] is False

    async def test_synthesize_no_outputs_no_errors(self, synth_and_model):
        synth, _ = synth_and_model

        result = await synth.synthesize(query="Anything", agent_outputs={})

        assert "couldn't find information" in result["response"]
        assert result["agents_used"] == []

    async def test_synthesize_with_errors(self, synth_and_model):
        synth, mock_model = synth_and_model
        mock_response = MagicMock()
        mock_response.content = "Partial answer from graph_query..."
        mock_model.ainvoke.return_value = mock_response

        result = await synth.synthesize(
            query="What is X?",
            agent_outputs={"graph_query": "Found X"},
            errors={"code_analyst": "Timed out"},
        )

        assert result["had_errors"] is True

    async def test_synthesize_llm_failure_falls_back(self, synth_and_model):
        synth, mock_model = synth_and_model
        mock_model.ainvoke.side_effect = Exception("LLM down")

        result = await synth.synthesize(
            query="What is Y?",
            agent_outputs={"graph_query": "Y is a class"},
        )

        assert "graph_query" in result["response"]
        assert result["had_errors"] is True

    async def test_long_outputs_truncated(self, synth_and_model):
        synth, mock_model = synth_and_model
        mock_response = MagicMock()
        mock_response.content = "Synthesized"
        mock_model.ainvoke.return_value = mock_response

        long_output = "x" * 10000
        await synth.synthesize(
            query="test",
            agent_outputs={"graph_query": long_output},
        )

        call_args = mock_model.ainvoke.call_args[0][0]
        user_content = call_args[1]["content"]
        assert "[truncated]" in user_content


# ──────────────────────────────────────────────────
# Test 4: AgentRouter (mock sub-agents)
# ──────────────────────────────────────────────────


class TestAgentRouter:
    """Tests for AgentRouter with mocked sub-agents."""

    @pytest.fixture
    def router_and_mocks(self):
        from src.agents.orchestrator.router import AgentRouter
        from src.agents.orchestrator.config import OrchestratorSettings

        settings = OrchestratorSettings()
        r = AgentRouter(settings)

        mock_gq = AsyncMock()
        mock_gq.invoke = AsyncMock(return_value="graph context data")
        mock_gq.close = AsyncMock()
        r._graph_query_agent = mock_gq

        mock_ca = AsyncMock()
        mock_ca.invoke = AsyncMock(return_value="code analysis result")
        mock_ca.close = AsyncMock()
        r._code_analyst_agent = mock_ca

        mock_idx = AsyncMock()
        mock_idx.invoke = AsyncMock(return_value="indexing complete")
        mock_idx.close = AsyncMock()
        r._indexer_agent = mock_idx

        return r, mock_gq, mock_ca, mock_idx

    async def test_code_explanation_routes_to_gq_and_ca(self, router_and_mocks):
        r, mock_gq, mock_ca, _ = router_and_mocks
        result = await r.route(
            "What is FastAPI?",
            {"intent": "code_explanation", "entities": ["FastAPI"]},
        )

        assert result["pipeline"] == ["graph_query", "code_analyst"]
        assert "graph_query" in result["outputs"]
        assert "code_analyst" in result["outputs"]
        assert result["errors"] == {}

    async def test_dependency_query_routes_to_gq_only(self, router_and_mocks):
        r, mock_gq, mock_ca, _ = router_and_mocks
        result = await r.route(
            "What depends on X?",
            {"intent": "dependency_query", "entities": ["X"]},
        )

        assert result["pipeline"] == ["graph_query"]
        assert "graph_query" in result["outputs"]
        assert "code_analyst" not in result["outputs"]

    async def test_indexing_routes_to_indexer_only(self, router_and_mocks):
        r, _, _, mock_idx = router_and_mocks
        result = await r.route(
            "Index the repo",
            {"intent": "indexing_operation", "entities": []},
        )

        assert result["pipeline"] == ["indexer"]
        assert "indexer" in result["outputs"]

    async def test_agent_failure_records_error_continues(self, router_and_mocks):
        r, mock_gq, mock_ca, _ = router_and_mocks
        mock_gq.invoke = AsyncMock(side_effect=RuntimeError("Neo4j down"))

        result = await r.route(
            "What is X?",
            {"intent": "code_explanation", "entities": []},
        )

        assert "graph_query" in result["errors"]
        assert "code_analyst" in result["outputs"]

    async def test_unknown_intent_uses_default_pipeline(self, router_and_mocks):
        r, mock_gq, mock_ca, _ = router_and_mocks
        result = await r.route(
            "Random question",
            {"intent": "unknown_intent", "entities": []},
        )

        assert result["pipeline"] == ["graph_query", "code_analyst"]

    async def test_close_shuts_down_all_agents(self, router_and_mocks):
        r, mock_gq, mock_ca, mock_idx = router_and_mocks
        await r.close()

        mock_gq.close.assert_called_once()
        mock_ca.close.assert_called_once()
        mock_idx.close.assert_called_once()

    async def test_graph_context_passed_to_code_analyst(self, router_and_mocks):
        r, mock_gq, mock_ca, _ = router_and_mocks
        mock_gq.invoke = AsyncMock(return_value="FastAPI inherits from Starlette")

        await r.route(
            "How does FastAPI work?",
            {"intent": "code_explanation", "entities": ["FastAPI"]},
        )

        ca_call = mock_ca.invoke.call_args
        assert "FastAPI inherits from Starlette" in str(ca_call)


# ──────────────────────────────────────────────────
# Test 5: OrchestratorAgent (mock agent + formatter)
# ──────────────────────────────────────────────────


class TestOrchestratorAgentIntegration:
    """Integration test for OrchestratorAgent with mocked internals."""

    async def test_invoke_returns_formatted_response(self):
        from src.agents.orchestrator.agent import OrchestratorAgent

        mock_client = MagicMock()
        mock_agent = AsyncMock()
        mock_ai_msg = MagicMock()
        mock_ai_msg.content = "FastAPI is a framework"
        mock_ai_msg.type = "ai"
        mock_ai_msg.tool_calls = None
        mock_agent.ainvoke.return_value = {"messages": [mock_ai_msg]}

        mock_formatter = AsyncMock()
        mock_formatter.format_response.return_value = {
            "response": "FastAPI is a framework",
            "suggestive_pills": ["How does routing work?"],
        }

        orch = OrchestratorAgent(
            client=mock_client,
            agent=mock_agent,
            formatter=mock_formatter,
        )

        result = await orch.invoke("What is FastAPI?", session_id="test-session")

        assert "response" in result
        assert "suggestive_pills" in result
        mock_agent.ainvoke.assert_called_once()

    async def test_invoke_fallback_when_no_ai_message(self):
        from src.agents.orchestrator.agent import OrchestratorAgent

        mock_client = MagicMock()
        mock_agent = AsyncMock()
        mock_agent.ainvoke.return_value = {"messages": []}

        mock_formatter = AsyncMock()
        mock_formatter.format_response.return_value = {
            "response": "I was unable to produce an answer for this query.",
            "suggestive_pills": [],
        }

        orch = OrchestratorAgent(
            client=mock_client,
            agent=mock_agent,
            formatter=mock_formatter,
        )

        result = await orch.invoke("test")
        mock_formatter.format_response.assert_called_once_with(
            "I was unable to produce an answer for this query."
        )

    async def test_close_releases_references(self):
        from src.agents.orchestrator.agent import OrchestratorAgent

        mock_client = MagicMock()
        mock_agent = MagicMock()
        mock_formatter = MagicMock()

        orch = OrchestratorAgent(
            client=mock_client,
            agent=mock_agent,
            formatter=mock_formatter,
        )

        await orch.close()
        assert orch._client is None
        assert orch._agent is None
