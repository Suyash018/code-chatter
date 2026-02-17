"""
Chat routes — POST /api/chat and WebSocket /ws/chat.
"""

import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from langfuse import observe
from pydantic import BaseModel, Field

from src.shared.logging import setup_logging
from src.shared.observability import extract_trace_context, is_langfuse_enabled

logger = setup_logging("gateway.routes.chat", level="INFO")

router = APIRouter()


# ─── Request/Response Models ────────────────────────────────


class ChatRequest(BaseModel):
    """Request model for POST /api/chat."""

    message: str = Field(..., description="User message/query")
    session_id: str | None = Field(
        None, description="Optional session ID for conversation context"
    )
    stream: bool = Field(False, description="Enable streaming response")


class ChatResponse(BaseModel):
    """Response model for POST /api/chat."""

    session_id: str = Field(..., description="Session ID for this conversation")
    response: str = Field(..., description="Agent's response")
    intent: str | None = Field(None, description="Detected query intent")
    entities: list[str] = Field(default_factory=list, description="Extracted entities")
    agents_called: list[str] = Field(
        default_factory=list, description="Agents that processed this query"
    )
    errors: dict[str, str] = Field(
        default_factory=dict, description="Any agent errors that occurred"
    )


# ─── Helper Functions ───────────────────────────────────────


@observe(name="call_orchestrator_tool", as_type="span")
async def _call_orchestrator_tool(
    client: Any, tool_name: str, **kwargs
) -> dict:
    """Call an orchestrator tool and return parsed JSON result."""
    try:
        tools = await client.get_tools()
        tool = next((t for t in tools if t.name == tool_name), None)

        if not tool:
            raise HTTPException(
                status_code=500,
                detail=f"Orchestrator tool '{tool_name}' not found"
            )

        result = await tool.ainvoke(kwargs)

        # Log the result type and content for debugging
        logger.debug(f"Tool result type: {type(result)}, content: {result}")

        # Handle different result types from MCP tools
        if isinstance(result, str):
            return json.loads(result)
        elif isinstance(result, dict):
            return result
        elif isinstance(result, list) and len(result) > 0:
            # MCP tools sometimes return a list of content blocks
            # Extract the text content from the first block
            first_item = result[0]
            if isinstance(first_item, dict) and "text" in first_item:
                return json.loads(first_item["text"])
            elif isinstance(first_item, str):
                return json.loads(first_item)
            else:
                logger.error(f"Unexpected list item format: {first_item}")
                return result
        else:
            logger.error(f"Unexpected result type: {type(result)}")
            return result

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse tool result: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Invalid JSON response from orchestrator: {e}"
        )
    except Exception as e:
        logger.error(f"Error calling orchestrator tool '{tool_name}': {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Orchestrator error: {str(e)}"
        )


@observe(name="process_chat_message", as_type="generation")
async def _process_chat_message(
    client: Any, message: str, session_id: str
) -> ChatResponse:
    """Process a chat message through the orchestrator pipeline."""

    # Step 1: Analyze the query
    logger.info(f"Analyzing query for session {session_id}")
    analysis = await _call_orchestrator_tool(
        client,
        "analyze_query",
        query=message,
        session_id=session_id,
    )

    intent = analysis.get("intent", "general_question")
    entities = analysis.get("entities", [])

    logger.info(f"Query analysis: intent={intent}, entities={entities}")

    # Step 2: Route to agents
    logger.info(f"Routing query to agents")
    routing_result = await _call_orchestrator_tool(
        client,
        "route_to_agents",
        query=message,
        intent=intent,
        entities=json.dumps(entities),
        session_id=session_id,
    )

    agents_called = routing_result.get("agents_called", [])
    outputs = routing_result.get("outputs", {})
    errors = routing_result.get("errors", {})

    logger.info(f"Agents called: {agents_called}, errors: {list(errors.keys())}")

    # Step 3: Synthesize response
    logger.info(f"Synthesizing response")
    synthesis = await _call_orchestrator_tool(
        client,
        "synthesize_response",
        query=message,
        agent_outputs=json.dumps(outputs),
        errors=json.dumps(errors),
    )

    final_response = synthesis.get("response", "I couldn't generate a response.")

    return ChatResponse(
        session_id=session_id,
        response=final_response,
        intent=intent,
        entities=entities,
        agents_called=agents_called,
        errors=errors,
    )


# ─── POST /api/chat ─────────────────────────────────────────


@router.post("/chat", response_model=ChatResponse)
@observe(name="chat_endpoint", as_type="generation")
async def chat(request_body: ChatRequest, request: Request) -> ChatResponse:
    """Send a message and receive a response from the multi-agent system.

    This endpoint processes user queries through the orchestrator, which:
    1. Analyzes the query intent and extracts entities
    2. Routes the query to appropriate specialist agents
    3. Synthesizes a coherent response from agent outputs

    Supports multi-turn conversations via session_id for context retention.
    """
    client = request.app.state.orchestrator_client

    if not client:
        raise HTTPException(
            status_code=503,
            detail="Orchestrator client not initialized"
        )

    # Generate session_id if not provided
    session_id = request_body.session_id or str(uuid.uuid4())

    logger.info(
        f"Processing chat message (session={session_id}, stream={request_body.stream})"
    )

    try:
        response = await _process_chat_message(
            client,
            request_body.message,
            session_id,
        )
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Unexpected error processing chat message: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


# ─── WebSocket /ws/chat ─────────────────────────────────────


@router.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """Real-time chat with streaming responses via WebSocket.

    Supports multi-turn conversations with the same session_id.

    Message format (client → server):
    {
        "message": "What is the FastAPI class?",
        "session_id": "optional-uuid"
    }

    Message format (server → client):
    {
        "type": "response",
        "session_id": "uuid",
        "response": "The FastAPI class is...",
        "intent": "code_explanation",
        "entities": ["FastAPI"],
        "agents_called": ["graph_query", "code_analyst"],
        "errors": {}
    }

    Error format:
    {
        "type": "error",
        "error": "Error message"
    }
    """
    await websocket.accept()
    logger.info("WebSocket connection established")

    client = websocket.app.state.orchestrator_client

    if not client:
        await websocket.send_json({
            "type": "error",
            "error": "Orchestrator client not initialized"
        })
        await websocket.close()
        return

    try:
        while True:
            # Receive message
            data = await websocket.receive_json()
            message = data.get("message", "")
            session_id = data.get("session_id") or str(uuid.uuid4())

            if not message:
                await websocket.send_json({
                    "type": "error",
                    "error": "Empty message"
                })
                continue

            logger.info(f"WebSocket message received (session={session_id})")

            try:
                # Process the message
                response = await _process_chat_message(
                    client,
                    message,
                    session_id,
                )

                # Send response
                await websocket.send_json({
                    "type": "response",
                    **response.model_dump()
                })

            except HTTPException as e:
                await websocket.send_json({
                    "type": "error",
                    "error": e.detail
                })
            except Exception as e:
                logger.exception(f"Error processing WebSocket message: {e}")
                await websocket.send_json({
                    "type": "error",
                    "error": str(e)
                })

    except WebSocketDisconnect:
        logger.info("WebSocket connection closed")
    except Exception as e:
        logger.exception(f"WebSocket error: {e}")
        try:
            await websocket.close()
        except:
            pass
