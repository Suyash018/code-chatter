"""
Response Synthesizer — synthesize_response tool implementation.

Combines outputs from multiple agents into a coherent response.
"""

import json

from src.agents.orchestrator.config import OrchestratorSettings
from src.shared.llms.models import get_openai_model
from src.shared.logging import setup_logging

logger = setup_logging("orchestrator.synthesizer", level="INFO")

MAX_AGENT_OUTPUT_CHARS = 8000

SYNTHESIS_PROMPT = """\
You are a response synthesizer for a FastAPI codebase Q&A system.
You receive the original user query and outputs from one or more specialist \
agents that analyzed the codebase. Your job is to merge their outputs into a \
single, coherent, well-structured answer.

Guidelines:
- Combine information from all agents without redundancy.
- Preserve specific code entity names, qualified names, and code snippets.
- If agents provide overlapping information, merge it seamlessly.
- If there were errors from some agents, work with what's available and \
briefly note any gaps.
- Structure the response with clear sections if the answer is complex.
- Be concise but thorough — don't lose important details from agent outputs.
- If no agent outputs are available, say you couldn't find the information.

Respond with the synthesized answer directly — no JSON wrapping needed.
"""


class ResponseSynthesizer:
    """Merges outputs from multiple agents into a coherent response."""

    def __init__(self, settings: OrchestratorSettings) -> None:
        logger.info("Initializing ResponseSynthesizer with model: %s", settings.synthesis_model)
        self._model = get_openai_model(settings.synthesis_model)

    async def synthesize(
        self,
        query: str,
        agent_outputs: dict[str, str],
        errors: dict[str, str] | None = None,
    ) -> dict:
        """Synthesize a unified response from agent outputs.

        Args:
            query: The user's original question.
            agent_outputs: Mapping of agent_name → output text.
            errors: Optional mapping of agent_name → error message.

        Returns:
            Dict with response, agents_used, and had_errors.

        Raises:
            This method catches LLM errors and returns a fallback response
            (concatenated raw agent outputs) instead of raising. The dict
            will have had_errors=True if synthesis fails.
        """
        logger.info("ResponseSynthesizer.synthesize called")
        logger.debug("Query: %s", query)
        logger.info("Agent outputs to synthesize: %s", list(agent_outputs.keys()))
        logger.info("Errors reported: %s", list(errors.keys()) if errors else "none")

        errors = errors or {}

        # Truncate long outputs
        truncated = {}
        for agent_name, output in agent_outputs.items():
            if len(output) > MAX_AGENT_OUTPUT_CHARS:
                logger.debug("Truncating %s output from %d to %d chars",
                           agent_name, len(output), MAX_AGENT_OUTPUT_CHARS)
                truncated[agent_name] = output[:MAX_AGENT_OUTPUT_CHARS] + "\n... [truncated]"
            else:
                truncated[agent_name] = output

        # Build user message with all agent outputs
        parts = [f"User query: {query}\n"]

        if truncated:
            parts.append("Agent outputs:")
            for agent_name, output in truncated.items():
                parts.append(f"\n--- {agent_name} ---\n{output}")
        else:
            parts.append("No agent outputs were available.")

        if errors:
            parts.append("\nAgent errors:")
            for agent_name, error in errors.items():
                parts.append(f"  {agent_name}: {error}")

        user_content = "\n".join(parts)

        # Graceful fallback when no outputs available
        if not truncated and not errors:
            logger.warning("No agent outputs or errors available for synthesis")
            return {
                "response": "I couldn't find information to answer this query. "
                "The knowledge graph may not have been indexed yet.",
                "agents_used": [],
                "had_errors": False,
            }

        try:
            logger.info("Invoking LLM for synthesis (input: %d chars)...", len(user_content))
            response = await self._model.ainvoke(
                [
                    {"role": "system", "content": SYNTHESIS_PROMPT},
                    {"role": "user", "content": user_content},
                ]
            )
            logger.debug("LLM returned %d characters", len(response.content))

            logger.info(
                "Synthesized response from %d agent outputs (%d errors)",
                len(agent_outputs),
                len(errors),
            )

            return {
                "response": response.content,
                "agents_used": list(agent_outputs.keys()),
                "had_errors": bool(errors),
            }

        except Exception as exc:
            logger.error("Synthesis LLM call failed: %s", exc)
            # Fall back to concatenating raw outputs
            fallback_parts = []
            for agent_name, output in truncated.items():
                fallback_parts.append(f"**{agent_name}:**\n{output}")
            if errors:
                fallback_parts.append(
                    "Some agents encountered errors: "
                    + ", ".join(f"{k}: {v}" for k, v in errors.items())
                )

            return {
                "response": "\n\n".join(fallback_parts) if fallback_parts else str(exc),
                "agents_used": list(agent_outputs.keys()),
                "had_errors": True,
            }
