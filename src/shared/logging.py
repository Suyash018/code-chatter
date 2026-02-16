"""
Structured logging with correlation IDs across agents.

Provides a consistent logging setup for all MCP servers
so that requests can be traced across agent boundaries.
"""

import logging
import uuid


def setup_logging(agent_name: str, level: str = "INFO") -> logging.Logger:
    """
    Configure structured logging for an agent.

    Args:
        agent_name: Name of the agent (used as logger prefix).
        level: Log level string (e.g. 'INFO', 'DEBUG').

    Returns:
        Configured logger instance.
    """
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(name)-30s  %(levelname)-7s  %(message)s",
    )
    return logging.getLogger(agent_name)


def generate_correlation_id() -> str:
    """Generate a unique correlation ID for request tracing across agents."""
    return uuid.uuid4().hex[:12]
