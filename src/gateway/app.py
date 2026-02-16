"""
FastAPI Gateway — HTTP/WebSocket API layer.

External interface for the multi-agent system.
Routes requests to the Orchestrator Agent.
"""

# TODO: Implement FastAPI application
# Endpoints:
#   POST /api/chat         — Send message, receive response (streaming option)
#   POST /api/index        — Trigger repository indexing
#   GET  /api/index/status/{job_id} — Get indexing job status
#   GET  /api/agents/health — Health check for all agents
#   GET  /api/graph/statistics — Knowledge graph statistics
#   WS   /ws/chat          — Real-time chat with streaming
