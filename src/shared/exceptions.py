"""
Custom exception hierarchy for the multi-agent system.

All agent errors inherit from AgentError so they can be caught
uniformly at the orchestrator or gateway level.
"""


class AgentError(Exception):
    """Base exception for all agent errors."""

    def __init__(self, message: str, agent_name: str = "unknown"):
        self.agent_name = agent_name
        super().__init__(f"[{agent_name}] {message}")


class IndexerError(AgentError):
    """Errors raised by the Indexer Agent."""

    def __init__(self, message: str):
        super().__init__(message, agent_name="indexer")


class GraphQueryError(AgentError):
    """Errors raised by the Graph Query Agent."""

    def __init__(self, message: str):
        super().__init__(message, agent_name="graph_query")


class CodeAnalystError(AgentError):
    """Errors raised by the Code Analyst Agent."""

    def __init__(self, message: str):
        super().__init__(message, agent_name="code_analyst")


class OrchestratorError(AgentError):
    """Errors raised by the Orchestrator Agent."""

    def __init__(self, message: str):
        super().__init__(message, agent_name="orchestrator")


class DatabaseConnectionError(AgentError):
    """Failed to connect to Neo4j."""

    def __init__(self, message: str):
        super().__init__(message, agent_name="database")


class EnrichmentError(IndexerError):
    """LLM enrichment call failed after all retries."""
    pass


class ParseError(IndexerError):
    """AST parsing failed for a Python file."""
    pass
