"""
Langfuse observability integration.

Provides tracing and observation for all API requests.
Only activates when LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are provided in .env
"""

import functools
import os
from contextlib import asynccontextmanager
from typing import Any, Callable, Optional

from fastapi import Request, Response
from langfuse import Langfuse
from langfuse.decorators import langfuse_context, observe
from starlette.middleware.base import BaseHTTPMiddleware

from src.shared.logging import setup_logging

logger = setup_logging("shared.observability", level="INFO")

# Global Langfuse client
_langfuse_client: Optional[Langfuse] = None
_langfuse_enabled: bool = False


def init_langfuse() -> Optional[Langfuse]:
    """
    Initialize Langfuse client if environment variables are set.

    Required environment variables:
    - LANGFUSE_PUBLIC_KEY
    - LANGFUSE_SECRET_KEY
    - LANGFUSE_HOST (optional, defaults to https://cloud.langfuse.com)

    Returns:
        Langfuse client if initialized, None otherwise
    """
    global _langfuse_client, _langfuse_enabled

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

    if not public_key or not secret_key:
        logger.info("Langfuse not configured - observability disabled")
        _langfuse_enabled = False
        return None

    try:
        _langfuse_client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
        )
        _langfuse_enabled = True
        logger.info(f"Langfuse initialized successfully - host: {host}")
        return _langfuse_client

    except Exception as e:
        logger.error(f"Failed to initialize Langfuse: {e}")
        _langfuse_enabled = False
        return None


def is_langfuse_enabled() -> bool:
    """Check if Langfuse is enabled."""
    return _langfuse_enabled


def get_langfuse_client() -> Optional[Langfuse]:
    """Get the global Langfuse client."""
    return _langfuse_client


def shutdown_langfuse():
    """Flush and shutdown Langfuse client."""
    global _langfuse_client

    if _langfuse_client:
        logger.info("Shutting down Langfuse - flushing pending traces")
        try:
            _langfuse_client.flush()
        except Exception as e:
            logger.error(f"Error flushing Langfuse: {e}")
        finally:
            _langfuse_client = None


class LangfuseMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware for automatic request tracing with Langfuse.

    Traces all HTTP requests and responses, capturing:
    - Request method, path, headers, query params
    - Response status code and headers
    - Request duration
    - User information (if available)
    - Session information (if available)
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Trace the HTTP request/response cycle."""

        if not is_langfuse_enabled():
            return await call_next(request)

        # Extract request metadata
        method = request.method
        path = request.url.path
        query_params = dict(request.query_params)

        # Extract session_id if available (from query params or request body)
        session_id = query_params.get("session_id")

        # Extract user information if available
        user_id = request.headers.get("X-User-ID")

        # Start a trace for this request
        try:
            trace = langfuse_context.update_current_trace(
                name=f"{method} {path}",
                metadata={
                    "method": method,
                    "path": path,
                    "query_params": query_params,
                    "headers": dict(request.headers),
                },
                session_id=session_id,
                user_id=user_id,
                tags=["http", "api", method.lower()],
            )

            # Process the request
            response = await call_next(request)

            # Update trace with response information
            langfuse_context.update_current_trace(
                output={
                    "status_code": response.status_code,
                    "headers": dict(response.headers),
                },
                tags=["http", "api", method.lower(), f"status_{response.status_code}"],
            )

            return response

        except Exception as e:
            # Log error to trace
            logger.exception(f"Error in Langfuse middleware: {e}")

            if is_langfuse_enabled():
                langfuse_context.update_current_trace(
                    level="ERROR",
                    output={"error": str(e)},
                )

            raise


def trace_function(
    name: Optional[str] = None,
    capture_input: bool = True,
    capture_output: bool = True,
    as_type: str = "span",
):
    """
    Decorator for tracing functions with Langfuse.

    Args:
        name: Custom name for the trace (defaults to function name)
        capture_input: Whether to capture function arguments
        capture_output: Whether to capture function return value
        as_type: Type of trace ("span", "generation", "event")

    Usage:
        @trace_function(name="analyze_query", as_type="generation")
        async def analyze_query(query: str) -> dict:
            ...
    """
    def decorator(func: Callable) -> Callable:
        if not is_langfuse_enabled():
            # If Langfuse is disabled, return the original function
            return func

        # Use Langfuse's observe decorator
        traced_func = observe(
            name=name or func.__name__,
            capture_input=capture_input,
            capture_output=capture_output,
            as_type=as_type,
        )(func)

        return traced_func

    return decorator


def trace_llm_call(
    name: str,
    model: str,
    input_data: Any,
    output_data: Any,
    metadata: Optional[dict] = None,
    usage: Optional[dict] = None,
):
    """
    Manually log an LLM call to Langfuse.

    Args:
        name: Name of the LLM call
        model: Model identifier
        input_data: Input to the LLM
        output_data: Output from the LLM
        metadata: Additional metadata
        usage: Token usage information (prompt_tokens, completion_tokens, total_tokens)
    """
    if not is_langfuse_enabled():
        return

    try:
        client = get_langfuse_client()
        if client:
            generation = langfuse_context.update_current_observation(
                name=name,
                input=input_data,
                output=output_data,
                model=model,
                metadata=metadata,
                usage=usage,
            )
            return generation
    except Exception as e:
        logger.error(f"Failed to log LLM call to Langfuse: {e}")


@asynccontextmanager
async def trace_context(
    name: str,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    metadata: Optional[dict] = None,
):
    """
    Context manager for creating a traced code block.

    Usage:
        async with trace_context("process_query", session_id=session_id):
            # Your code here
            result = await some_operation()
    """
    if not is_langfuse_enabled():
        yield
        return

    try:
        langfuse_context.update_current_trace(
            name=name,
            session_id=session_id,
            user_id=user_id,
            metadata=metadata,
        )
        yield
    except Exception as e:
        logger.error(f"Error in trace context: {e}")
        if is_langfuse_enabled():
            langfuse_context.update_current_trace(
                level="ERROR",
                output={"error": str(e)},
            )
        raise
    finally:
        pass


def create_trace_score(
    name: str,
    value: float,
    comment: Optional[str] = None,
):
    """
    Add a score to the current trace.

    Useful for tracking quality metrics, user feedback, etc.

    Args:
        name: Score name (e.g., "user_rating", "relevance", "accuracy")
        value: Score value (typically 0-1 or 1-5)
        comment: Optional comment
    """
    if not is_langfuse_enabled():
        return

    try:
        langfuse_context.score_current_trace(
            name=name,
            value=value,
            comment=comment,
        )
    except Exception as e:
        logger.error(f"Failed to add score to trace: {e}")
