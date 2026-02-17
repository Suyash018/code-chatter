"""
FastAPI Gateway — HTTP/WebSocket API layer.

External interface for the multi-agent system.
Routes requests to the Orchestrator Agent via HTTP (SSE transport).
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langchain_mcp_adapters.client import MultiServerMCPClient

from src.gateway.config import GatewaySettings
from src.gateway.routes import chat, health, index
from src.shared.logging import setup_logging
from src.shared.observability import (
    LangfuseMiddleware,
    MCPTraceContextInterceptor,
    init_langfuse,
    is_langfuse_enabled,
    shutdown_langfuse,
)

logger = setup_logging("gateway.app", level="INFO")

# Global settings
settings = GatewaySettings()

# Global orchestrator MCP client (initialized on startup)
orchestrator_client: MultiServerMCPClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for the FastAPI app.

    Initializes the Orchestrator MCP client on startup,
    shuts it down on shutdown.
    """
    global orchestrator_client

    logger.info("Starting FastAPI Gateway")

    # Initialize Langfuse observability
    init_langfuse()
    if is_langfuse_enabled():
        logger.info("Langfuse observability enabled")
    else:
        logger.info("Langfuse observability disabled")

    # Get orchestrator URL from environment
    orchestrator_url = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8001")
    logger.info(f"Connecting to Orchestrator MCP server at {orchestrator_url}")

    # Initialize the orchestrator client with HTTP/SSE transport
    # Add trace context interceptor for linked tracing across MCP boundaries
    orchestrator_client = MultiServerMCPClient(
        {
            "orchestrator": {
                "url": orchestrator_url,
                "transport": "sse",
            }
        },
        tool_interceptors=[MCPTraceContextInterceptor()] if is_langfuse_enabled() else [],
    )

    # Store client in app state for route access
    app.state.orchestrator_client = orchestrator_client

    logger.info("Gateway initialized successfully")

    yield

    # Cleanup
    logger.info("Shutting down FastAPI Gateway")
    shutdown_langfuse()
    orchestrator_client = None


# Create FastAPI app
app = FastAPI(
    title="FastAPI Repository Chat Agent - MCP Multi-Agent System",
    description="Multi-agent system for answering questions about the FastAPI codebase",
    version="0.1.0",
    lifespan=lifespan,
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add Langfuse observability middleware
app.add_middleware(LangfuseMiddleware)

# Register routers
app.include_router(chat.router, prefix="/api", tags=["Chat"])
app.include_router(index.router, prefix="/api", tags=["Indexing"])
app.include_router(health.router, prefix="/api", tags=["Health"])

# Root endpoint
@app.get("/", tags=["Root"])
async def root():
    """Root endpoint with basic info."""
    return {
        "name": "FastAPI Repository Chat Agent",
        "version": "0.1.0",
        "status": "operational",
        "endpoints": {
            "chat": "/api/chat",
            "websocket": "/ws/chat",
            "index": "/api/index",
            "health": "/api/health",
            "statistics": "/api/graph/statistics",
        }
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.gateway.app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )
