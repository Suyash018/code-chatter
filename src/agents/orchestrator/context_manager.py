"""
Conversation Context Manager — get_conversation_context tool implementation.

Retrieves and manages conversation history for multi-turn interactions.
Tracks entities, intents, and turn summaries per session in-memory.
"""

from dataclasses import dataclass, field

from src.shared.logging import setup_logging

logger = setup_logging("orchestrator.context_manager", level="INFO")


@dataclass
class SessionContext:
    """In-memory state for a single conversation session."""

    session_id: str
    entities_mentioned: list[str] = field(default_factory=list)
    turn_summaries: list[str] = field(default_factory=list)
    last_intent: str = ""
    last_agents_called: list[str] = field(default_factory=list)
    turn_count: int = 0


class ContextManager:
    """Manages per-session conversation context in-memory.

    Lives only inside the MCP server subprocess.  Each session_id
    gets its own ``SessionContext`` tracking entities discussed,
    turn summaries, and last intent for follow-up resolution.
    """

    def __init__(self, max_turns: int = 20) -> None:
        self._sessions: dict[str, SessionContext] = {}
        self._max_turns = max_turns

    def _get_or_create(self, session_id: str) -> SessionContext:
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionContext(session_id=session_id)
        return self._sessions[session_id]

    def get_context(self, session_id: str, max_turns: int | None = None) -> dict:
        """Return conversation context for a session.

        Args:
            session_id: The session identifier.
            max_turns: Max recent turns to include (defaults to instance max).

        Returns:
            Dict with turn_count, entities_discussed, recent_turns,
            last_intent, and last_agents_called.
        """
        logger.info("ContextManager.get_context called - session_id=%s", session_id)
        ctx = self._get_or_create(session_id)
        limit = max_turns or self._max_turns

        logger.debug(
            "Retrieving context: turn_count=%d, entities=%d, limit=%d",
            ctx.turn_count, len(ctx.entities_mentioned), limit
        )

        return {
            "session_id": session_id,
            "turn_count": ctx.turn_count,
            "entities_discussed": list(dict.fromkeys(ctx.entities_mentioned)),
            "recent_turns": ctx.turn_summaries[-limit:],
            "last_intent": ctx.last_intent,
            "last_agents_called": ctx.last_agents_called,
        }

    def update_context(
        self,
        session_id: str,
        query: str,
        intent: str,
        entities: list[str],
        agents_called: list[str],
        summary: str,
    ) -> None:
        """Record a completed turn in the session context.

        Args:
            session_id: The session identifier.
            query: The user's original query.
            intent: Classified intent for this turn.
            entities: Entities extracted from this turn.
            agents_called: Which agents were invoked.
            summary: Brief summary of the response.
        """
        ctx = self._get_or_create(session_id)
        ctx.turn_count += 1
        ctx.last_intent = intent
        ctx.last_agents_called = agents_called

        # Add new entities (deduplicated in get_context)
        ctx.entities_mentioned.extend(entities)

        # Keep turn summaries bounded
        turn_entry = f"Q: {query[:200]} | Intent: {intent} | Agents: {', '.join(agents_called)}"
        if summary:
            turn_entry += f" | Summary: {summary[:300]}"
        ctx.turn_summaries.append(turn_entry)

        if len(ctx.turn_summaries) > self._max_turns * 2:
            ctx.turn_summaries = ctx.turn_summaries[-self._max_turns:]

        logger.info(
            "Updated context for session %s: turn=%d, entities=%d",
            session_id,
            ctx.turn_count,
            len(ctx.entities_mentioned),
        )

    def get_context_summary(self, session_id: str) -> str:
        """Compact text summary for LLM prompt injection.

        Used by query analyzer to detect follow-ups and resolve
        references like 'it', 'that class', etc.
        """
        logger.debug("ContextManager.get_context_summary called - session_id=%s", session_id)
        ctx = self._get_or_create(session_id)
        if ctx.turn_count == 0:
            logger.debug("No prior turns for session %s", session_id)
            return ""

        unique_entities = list(dict.fromkeys(ctx.entities_mentioned))
        parts = [
            f"Conversation has {ctx.turn_count} prior turn(s).",
            f"Entities discussed: {', '.join(unique_entities[-15:]) or 'none'}.",
            f"Last intent: {ctx.last_intent or 'none'}.",
        ]
        if ctx.turn_summaries:
            parts.append(f"Last turn: {ctx.turn_summaries[-1][:200]}")

        summary = " ".join(parts)
        logger.debug("Context summary: %s", summary[:100])
        return summary
