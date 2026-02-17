"""
Query Analyzer — analyze_query tool implementation.

Classifies query intent and extracts key entities from user questions.
"""

import json

from src.agents.orchestrator.config import OrchestratorSettings
from src.shared.llms.models import get_openai_model
from src.shared.logging import setup_logging

logger = setup_logging("orchestrator.query_analyzer", level="INFO")

VALID_INTENTS = {
    "code_explanation",
    "code_comparison",
    "pattern_search",
    "dependency_query",
    "architecture_query",
    "indexing_operation",
    "general_question",
    "follow_up",
}

ANALYSIS_PROMPT = """\
You are a query classifier for a FastAPI codebase Q&A system.
Analyze the user query and return a JSON object with these fields:

- intent: one of {intents}
- entities: list of code entity names mentioned or implied (class names, \
function names, module names). Empty list if none.
- requires_graph: boolean — true if answering needs knowledge graph lookups \
(entity relationships, dependencies, imports)
- requires_analysis: boolean — true if answering needs code explanation, \
pattern analysis, or implementation deep-dive
- requires_indexing: boolean — true if the query asks to index, re-index, \
or update the codebase
- confidence: float 0.0-1.0 — how confident you are in the classification

Intent descriptions:
- code_explanation: "What does X do?", "How does X work?", "Explain X"
- code_comparison: "Compare X and Y", "Difference between X and Y"
- pattern_search: "What patterns are used?", "Find design patterns in X"
- dependency_query: "What depends on X?", "What does X import?"
- architecture_query: "How is X structured?", "Show the architecture of X"
- indexing_operation: "Index the repo", "Re-index file X", "Update the graph"
- general_question: Broad questions about the codebase not fitting above
- follow_up: References to previous conversation ("What about its methods?", \
"And the other one?", "Tell me more")

{context_section}

Respond with ONLY the JSON object, no markdown fences or extra text.
"""


class QueryAnalyzer:
    """Classifies user queries by intent and extracts mentioned entities."""

    def __init__(self, settings: OrchestratorSettings) -> None:
        self._model = get_openai_model(settings.analysis_model)

    async def analyze(
        self,
        query: str,
        conversation_context: str = "",
    ) -> dict:
        """Classify a user query and extract entities.

        Args:
            query: The user's raw question.
            conversation_context: Optional summary of prior conversation
                for follow-up detection.

        Returns:
            Dict with intent, entities, requires_graph, requires_analysis,
            requires_indexing, and confidence.
        """
        logger.info("QueryAnalyzer.analyze called")
        logger.debug("Query: %s", query)
        logger.debug("Has conversation context: %s", bool(conversation_context))

        context_section = ""
        if conversation_context:
            logger.debug("Context summary: %s", conversation_context[:200])
            context_section = (
                f"Recent conversation context:\n{conversation_context}\n\n"
                "Use this to detect follow_up intent and resolve entity "
                "references like 'it', 'that class', 'the other one'."
            )

        prompt = ANALYSIS_PROMPT.format(
            intents=", ".join(sorted(VALID_INTENTS)),
            context_section=context_section,
        )

        try:
            logger.info("Invoking LLM for query analysis...")
            response = await self._model.ainvoke(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": query},
                ]
            )
            raw = response.content.strip()
            logger.debug("LLM raw response: %s", raw[:200])
            # Strip markdown fences if present
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1]
                if raw.endswith("```"):
                    raw = raw[: raw.rfind("```")]
                raw = raw.strip()

            result = json.loads(raw)

            # Validate intent
            if result.get("intent") not in VALID_INTENTS:
                result["intent"] = "general_question"
                result["confidence"] = min(result.get("confidence", 0.3), 0.5)

            # Ensure required keys
            result.setdefault("entities", [])
            result.setdefault("requires_graph", True)
            result.setdefault("requires_analysis", True)
            result.setdefault("requires_indexing", False)
            result.setdefault("confidence", 0.5)

            logger.info(
                "Analyzed query: intent=%s, entities=%s, confidence=%.2f",
                result["intent"],
                result["entities"],
                result["confidence"],
            )
            return result

        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("Query analysis failed (%s), using fallback", exc)
            return {
                "intent": "general_question",
                "entities": [],
                "requires_graph": True,
                "requires_analysis": True,
                "requires_indexing": False,
                "confidence": 0.3,
            }
