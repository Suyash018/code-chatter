"""
Integration tests for Docker Compose deployment - Complete test suite.

This script runs a complete end-to-end test suite:
1. Index Agent Tests: Indexes the FastAPI repository into Neo4j (15-30 minutes)
2. Chat Tests: 40-turn conversation tests using the populated graph

The script validates:
- Repository indexing pipeline (POST /api/index)
- Job status polling (GET /api/index/status/{job_id})
- Graph population and statistics (GET /api/graph/statistics)
- Multi-turn conversations (POST /api/chat)
- Session management and context preservation
- Multi-agent coordination

Prerequisites:
    - Run `docker-compose up` before executing these tests
    - Ensure all services are healthy (gateway, indexer, neo4j, orchestrator)
    - Gateway should be accessible at http://localhost:8000
    - OpenAI API key must be configured for enrichment
    - Sufficient time for full indexing (15-30 minutes)

Run with:
    python -m pytest tests/integration/test_docker_compose_chat.py -v -s

Or run directly:
    python tests/integration/test_docker_compose_chat.py
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from httpx import Limits, Timeout


# ─── Configuration ──────────────────────────────────────────

GATEWAY_URL = "http://localhost:8000"
CHAT_ENDPOINT = f"{GATEWAY_URL}/api/chat"
TIMEOUT = 120.0  # 2 minutes per request

# ─── Index Testing Configuration ────────────────────────────
INDEX_ENDPOINT = f"{GATEWAY_URL}/api/index"
STATUS_ENDPOINT = f"{GATEWAY_URL}/api/index/status"
GRAPH_STATS_ENDPOINT = f"{GATEWAY_URL}/api/graph/statistics"
HEALTH_ENDPOINT = f"{GATEWAY_URL}/api/health"

# Repository configuration for indexing
FASTAPI_REPO_URL = "https://github.com/tiangolo/fastapi.git"
FASTAPI_BRANCH = "master"

# Polling configuration
POLL_INTERVAL_SECONDS = 5
MAX_WAIT_MINUTES = 30

# Validation thresholds for FastAPI repository
MIN_FILES = 100
MIN_CLASSES = 300
MIN_FUNCTIONS = 1500
MIN_ENRICHMENT_COVERAGE = 80.0  # percentage


@dataclass
class IndexTestResult:
    """Result of an index test operation."""
    test_name: str
    success: bool
    duration: float
    error: str | None = None
    data: dict[str, Any] | None = None


@dataclass
class IndexingJobResult:
    """Result of the complete indexing job."""
    job_id: str
    success: bool
    duration: float
    files_indexed: int
    total_nodes: int
    total_edges: int
    enrichment_coverage: float
    error: str | None = None


# ─── Test Queries by Difficulty ────────────────────────────

@dataclass
class Query:
    """Represents a single query with expected behavior."""
    text: str
    difficulty: str  # "simple", "medium", "complex"
    expected_intent: str | None = None
    description: str = ""


# Simple queries (single agent, straightforward)
SIMPLE_QUERIES = [
    # Code explanation - Classes
    Query("What is the FastAPI class?", "simple", "code_explanation", "FastAPI class lookup"),
    Query("Show me the Depends function", "simple", "code_explanation", "Depends function lookup"),
    Query("What is the Response object?", "simple", "code_explanation", "Response object"),
    Query("Show me the APIRoute class", "simple", "code_explanation", "APIRoute class"),
    Query("What is the Request class?", "simple", "code_explanation", "Request class"),
    Query("Show me the docstring for the Request class", "simple", "code_explanation", "Request docstring"),
    Query("What is the BackgroundTasks class?", "simple", "code_explanation", "BackgroundTasks class"),
    Query("Show me the WebSocket class", "simple", "code_explanation", "WebSocket class"),
    Query("What is the HTTPException class?", "simple", "code_explanation", "HTTPException class"),
    Query("Show me the UploadFile class", "simple", "code_explanation", "UploadFile class"),
    Query("What is the Cookie function?", "simple", "code_explanation", "Cookie function"),
    Query("Show me the Header function", "simple", "code_explanation", "Header function"),
    Query("What is the Body function?", "simple", "code_explanation", "Body function"),
    Query("Show me the File function?", "simple", "code_explanation", "File function"),
    Query("What is the Form function?", "simple", "code_explanation", "Form function"),

    # Dependency queries
    Query("What depends on APIRouter?", "simple", "dependency_query", "APIRouter dependents"),
    Query("What classes inherit from APIRouter?", "simple", "dependency_query", "APIRouter inheritance"),
    Query("What imports does the main module have?", "simple", "dependency_query", "Main module imports"),
    Query("What does FastAPI depend on?", "simple", "dependency_query", "FastAPI dependencies"),
    Query("What modules import the routing module?", "simple", "dependency_query", "Routing module usage"),
    Query("What depends on the Request class?", "simple", "dependency_query", "Request dependents"),
    Query("Show me what imports Starlette", "simple", "dependency_query", "Starlette imports"),
    Query("What depends on Pydantic models?", "simple", "dependency_query", "Pydantic usage"),
    Query("What classes inherit from BaseModel?", "simple", "dependency_query", "BaseModel inheritance"),
    Query("What imports the dependencies module?", "simple", "dependency_query", "Dependencies module usage"),

    # Pattern searches
    Query("Find all decorators in the routing module", "simple", "pattern_search", "Routing decorators"),
    Query("List all validators in FastAPI", "simple", "pattern_search", "Validators"),
    Query("Show me @app.get decorators", "simple", "pattern_search", "@app.get usage"),
    Query("Find all @app.post decorators", "simple", "pattern_search", "@app.post usage"),
    Query("Show me async def functions", "simple", "pattern_search", "Async functions"),
    Query("Find all exception handlers", "simple", "pattern_search", "Exception handlers"),
    Query("Show me middleware decorators", "simple", "pattern_search", "Middleware decorators"),
    Query("Find all startup event handlers", "simple", "pattern_search", "Startup handlers"),
    Query("Show me response model decorators", "simple", "pattern_search", "Response model decorators"),
    Query("Find all dependency injection points", "simple", "pattern_search", "DI usage points"),

    # General questions
    Query("How does routing work?", "simple", "general_question", "Routing basics"),
    Query("What is middleware?", "simple", "general_question", "Middleware concept"),
    Query("Explain async support", "simple", "general_question", "Async support"),
    Query("What is CORS?", "simple", "general_question", "CORS concept"),
    Query("What are path parameters?", "simple", "general_question", "Path parameters"),
    Query("What are query parameters?", "simple", "general_question", "Query parameters"),
    Query("What is request body parsing?", "simple", "general_question", "Body parsing"),
    Query("What are response models?", "simple", "general_question", "Response models"),
    Query("What is automatic documentation?", "simple", "general_question", "Auto docs"),
    Query("What are background tasks?", "simple", "general_question", "Background tasks"),

    # Architecture queries
    Query("Show the module structure", "simple", "architecture_query", "Module structure"),
    Query("What are the main components?", "simple", "architecture_query", "Main components"),
    Query("Show me the core modules", "simple", "architecture_query", "Core modules"),
    Query("What is the package structure?", "simple", "architecture_query", "Package structure"),
    Query("Show me the application layers", "simple", "architecture_query", "Application layers"),
]

# Medium queries (2-3 agents, multi-turn follow-ups)
MEDIUM_QUERIES = [
    # Code explanation with detail
    Query("How does FastAPI handle request validation?", "medium", "code_explanation", "Request validation"),
    Query("How does dependency injection work in FastAPI?", "medium", "code_explanation", "Dependency injection"),
    Query("How are Path parameters implemented?", "medium", "code_explanation", "Path parameters impl"),
    Query("How are Query parameters implemented?", "medium", "code_explanation", "Query parameters impl"),
    Query("Explain WebSocket support in FastAPI", "medium", "code_explanation", "WebSocket support"),
    Query("How does FastAPI handle async operations?", "medium", "code_explanation", "Async operations"),
    Query("Show me the exception handling system", "medium", "code_explanation", "Exception handling"),
    Query("How does FastAPI parse request bodies?", "medium", "code_explanation", "Body parsing"),
    Query("How are response models validated?", "medium", "code_explanation", "Response validation"),
    Query("How does file upload work in FastAPI?", "medium", "code_explanation", "File uploads"),
    Query("How are form data handled?", "medium", "code_explanation", "Form handling"),
    Query("How does FastAPI handle cookies?", "medium", "code_explanation", "Cookie handling"),
    Query("How are headers processed?", "medium", "code_explanation", "Header processing"),
    Query("How does background task execution work?", "medium", "code_explanation", "Background tasks"),
    Query("How are HTTP exceptions raised and caught?", "medium", "code_explanation", "HTTP exceptions"),
    Query("How does FastAPI generate OpenAPI schemas?", "medium", "code_explanation", "OpenAPI generation"),
    Query("How are default values handled in parameters?", "medium", "code_explanation", "Default values"),
    Query("How does automatic type conversion work?", "medium", "code_explanation", "Type conversion"),
    Query("How are optional parameters handled?", "medium", "code_explanation", "Optional parameters"),
    Query("How does FastAPI serialize responses?", "medium", "code_explanation", "Response serialization"),

    # Architecture queries
    Query("Explain the routing system in FastAPI", "medium", "architecture_query", "Routing system"),
    Query("What are the main components of FastAPI?", "medium", "architecture_query", "Main components"),
    Query("What is the relationship between FastAPI and Starlette?", "medium", "architecture_query", "FastAPI-Starlette relationship"),
    Query("How does middleware work in FastAPI?", "medium", "architecture_query", "Middleware architecture"),
    Query("What security features does FastAPI provide?", "medium", "architecture_query", "Security features"),
    Query("How is the request-response lifecycle organized?", "medium", "architecture_query", "Request lifecycle"),
    Query("What is the dependency injection architecture?", "medium", "architecture_query", "DI architecture"),
    Query("How is the validation system structured?", "medium", "architecture_query", "Validation structure"),
    Query("What is the routing architecture?", "medium", "architecture_query", "Routing architecture"),
    Query("How are endpoints organized in FastAPI?", "medium", "architecture_query", "Endpoint organization"),
    Query("What is the middleware pipeline structure?", "medium", "architecture_query", "Middleware pipeline"),
    Query("How does the application startup work?", "medium", "architecture_query", "Startup process"),
    Query("How does the application shutdown work?", "medium", "architecture_query", "Shutdown process"),
    Query("What is the error handling hierarchy?", "medium", "architecture_query", "Error hierarchy"),
    Query("How are static files served?", "medium", "architecture_query", "Static file serving"),

    # Pattern searches with context
    Query("Show me examples of route decorators", "medium", "pattern_search", "Route decorator examples"),
    Query("What design patterns are used in the routing module?", "medium", "pattern_search", "Routing patterns"),
    Query("Find all uses of dependency injection in core modules", "medium", "pattern_search", "DI patterns"),
    Query("Show me validation decorator patterns", "medium", "pattern_search", "Validation patterns"),
    Query("What are the common middleware patterns?", "medium", "pattern_search", "Middleware patterns"),
    Query("Find examples of async context managers", "medium", "pattern_search", "Async context patterns"),
    Query("Show me error handling patterns", "medium", "pattern_search", "Error patterns"),
    Query("What are the authentication patterns used?", "medium", "pattern_search", "Auth patterns"),
    Query("Find all factory pattern implementations", "medium", "pattern_search", "Factory patterns"),
    Query("Show me singleton patterns in FastAPI", "medium", "pattern_search", "Singleton patterns"),

    # Dependency queries with detail
    Query("What validation libraries are used?", "medium", "dependency_query", "Validation libraries"),
    Query("What are FastAPI's main external dependencies?", "medium", "dependency_query", "External dependencies"),
    Query("How does FastAPI integrate with Pydantic?", "medium", "dependency_query", "Pydantic integration"),
    Query("What testing libraries does FastAPI support?", "medium", "dependency_query", "Testing libraries"),
    Query("What are the dependencies of the routing module?", "medium", "dependency_query", "Routing dependencies"),
    Query("How does FastAPI depend on Starlette components?", "medium", "dependency_query", "Starlette dependencies"),
    Query("What serialization libraries are used?", "medium", "dependency_query", "Serialization libraries"),
    Query("What are the security library dependencies?", "medium", "dependency_query", "Security dependencies"),

    # Comparison queries
    Query("What's the difference between Path and Query parameters?", "medium", "code_comparison", "Path vs Query"),
    Query("Compare sync and async route handlers", "medium", "code_comparison", "Sync vs Async"),
    Query("What's the difference between Body and Form?", "medium", "code_comparison", "Body vs Form"),
    Query("Compare depends and security dependencies", "medium", "code_comparison", "Depends vs Security"),
    Query("What's the difference between Response and JSONResponse?", "medium", "code_comparison", "Response types"),
    Query("Compare APIRouter and FastAPI classes", "medium", "code_comparison", "Router vs App"),
    Query("What's the difference between startup and lifespan events?", "medium", "code_comparison", "Event types"),
    Query("Compare middleware and dependencies", "medium", "code_comparison", "Middleware vs Dependencies"),
]

# Complex queries (multiple agents, synthesis required)
COMPLEX_QUERIES = [
    # Deep architectural analysis
    Query("Explain the complete lifecycle of a FastAPI request from endpoint to response", "complex", "architecture_query", "Request lifecycle"),
    Query("Trace the complete flow of request validation from input to Pydantic models", "complex", "architecture_query", "Validation flow"),
    Query("Explain how FastAPI achieves high performance and what optimizations are used", "complex", "architecture_query", "Performance optimizations"),
    Query("How does the OpenAPI schema generation work in FastAPI?", "complex", "architecture_query", "OpenAPI generation"),
    Query("Explain the security implementation including OAuth2, API keys, and HTTP auth", "complex", "architecture_query", "Security implementation"),
    Query("Trace the complete dependency injection flow from declaration to execution", "complex", "architecture_query", "DI flow"),
    Query("Explain the complete middleware pipeline from request to response", "complex", "architecture_query", "Middleware pipeline"),
    Query("How does FastAPI handle WebSocket connections throughout their lifecycle?", "complex", "architecture_query", "WebSocket lifecycle"),
    Query("Trace the complete error handling flow from exception to response", "complex", "architecture_query", "Error handling flow"),
    Query("Explain how background tasks are queued, executed, and managed", "complex", "architecture_query", "Background task management"),
    Query("How does FastAPI integrate with ASGI servers from startup to shutdown?", "complex", "architecture_query", "ASGI integration"),
    Query("Trace the complete authentication and authorization flow", "complex", "architecture_query", "Auth flow"),
    Query("Explain how FastAPI generates and serves interactive API documentation", "complex", "architecture_query", "API docs generation"),
    Query("How does the type system work across parameters, models, and responses?", "complex", "architecture_query", "Type system"),
    Query("Explain the complete startup and configuration process", "complex", "architecture_query", "Startup process"),

    # Deep comparisons
    Query("Compare how Path and Query parameters are implemented and explain the differences", "complex", "code_comparison", "Path vs Query deep"),
    Query("Compare the implementation of synchronous vs asynchronous route handlers", "complex", "code_comparison", "Sync vs Async impl"),
    Query("Compare all parameter types (Path, Query, Body, Header, Cookie, Form, File) and their implementations", "complex", "code_comparison", "All parameter types"),
    Query("Compare dependency injection vs middleware for cross-cutting concerns", "complex", "code_comparison", "DI vs Middleware"),
    Query("Compare different response types and when to use each", "complex", "code_comparison", "Response types"),
    Query("Compare FastAPI's routing with Starlette's routing implementation", "complex", "code_comparison", "FastAPI vs Starlette routing"),
    Query("Compare validation at different layers: parameters, body, response", "complex", "code_comparison", "Validation layers"),
    Query("Compare different authentication methods and their use cases", "complex", "code_comparison", "Auth methods"),
    Query("Compare background tasks vs async tasks for long-running operations", "complex", "code_comparison", "Task execution models"),
    Query("Compare different error handling strategies across the framework", "complex", "code_comparison", "Error strategies"),

    # Pattern analysis
    Query("What design patterns are used in FastAPI's core and why were they chosen?", "complex", "pattern_search", "Core patterns"),
    Query("What are all the decorator patterns used throughout FastAPI and how do they work together?", "complex", "pattern_search", "Decorator ecosystem"),
    Query("Identify all factory patterns in FastAPI and explain their purposes", "complex", "pattern_search", "Factory patterns"),
    Query("What singleton patterns exist and how are they implemented?", "complex", "pattern_search", "Singleton patterns"),
    Query("Identify all dependency injection patterns and their variations", "complex", "pattern_search", "DI patterns"),
    Query("What observer patterns are used for event handling?", "complex", "pattern_search", "Observer patterns"),
    Query("Identify all adapter patterns for external library integration", "complex", "pattern_search", "Adapter patterns"),
    Query("What builder patterns are used for complex object construction?", "complex", "pattern_search", "Builder patterns"),
    Query("Identify all strategy patterns for algorithm selection", "complex", "pattern_search", "Strategy patterns"),
    Query("What proxy patterns are used for lazy loading or access control?", "complex", "pattern_search", "Proxy patterns"),

    # Implementation deep dives
    Query("How does dependency injection work? Show me the implementation and real examples from the codebase", "complex", "code_explanation", "DI implementation"),
    Query("Explain the complete Pydantic integration: from models to validation to serialization", "complex", "code_explanation", "Pydantic integration"),
    Query("How does automatic API documentation work from code to interactive UI?", "complex", "code_explanation", "Auto documentation"),
    Query("Explain the complete routing mechanism: from URL patterns to handler execution", "complex", "code_explanation", "Routing mechanism"),
    Query("How does FastAPI achieve automatic type conversion and validation?", "complex", "code_explanation", "Type conversion"),
    Query("Explain the complete security system: from decorators to token validation", "complex", "code_explanation", "Security system"),
    Query("How does the response model system work for automatic serialization?", "complex", "code_explanation", "Response models"),
    Query("Explain the complete WebSocket implementation from connection to message handling", "complex", "code_explanation", "WebSocket impl"),
    Query("How does FastAPI handle file uploads from multipart forms to disk?", "complex", "code_explanation", "File upload system"),
    Query("Explain the complete middleware system from registration to execution", "complex", "code_explanation", "Middleware system"),

    # Multi-faceted analysis
    Query("Analyze the testing architecture: what makes FastAPI testable and how are tests structured?", "complex", "architecture_query", "Testing architecture"),
    Query("How does FastAPI balance developer experience with performance?", "complex", "architecture_query", "DX vs Performance"),
    Query("What makes FastAPI's automatic documentation better than other frameworks?", "complex", "architecture_query", "Documentation advantage"),
    Query("How does FastAPI handle backwards compatibility while adding new features?", "complex", "architecture_query", "Compatibility strategy"),
    Query("What are the extension points in FastAPI and how can developers customize behavior?", "complex", "architecture_query", "Extension points"),

    # Cross-cutting concerns
    Query("How does FastAPI handle errors consistently across sync, async, and WebSocket code?", "complex", "architecture_query", "Error consistency"),
    Query("Trace data flow from HTTP request bytes to Python objects and back", "complex", "architecture_query", "Data flow"),
    Query("How does FastAPI maintain type safety throughout the request-response cycle?", "complex", "architecture_query", "Type safety"),
    Query("Explain how FastAPI coordinates between Starlette, Pydantic, and its own code", "complex", "architecture_query", "Framework coordination"),
    Query("How does FastAPI optimize memory usage for large requests and responses?", "complex", "architecture_query", "Memory optimization"),
]

# Follow-up queries for context testing
FOLLOW_UP_QUERIES = [
    # Asking for details
    "What are its methods?",
    "What are its attributes?",
    "What are its parameters?",
    "Give me more details",
    "Can you explain that further?",
    "Tell me more about that",
    "What else should I know?",
    "Elaborate on that",

    # Asking for examples
    "Show me an example",
    "Show me the code",
    "Show me real usage examples",
    "Give me a practical example",
    "Show me how it's used",
    "Can you show me that in code?",
    "Give me a code snippet",

    # Asking for implementation
    "How is it implemented?",
    "How does that work?",
    "How is that done?",
    "What's the implementation?",
    "Show me the internals",
    "How does it work under the hood?",
    "What's happening internally?",

    # Asking for relationships
    "What does it inherit from?",
    "What depends on it?",
    "What uses it?",
    "What are its dependencies?",
    "What does it depend on?",
    "What's related to it?",
    "What inherits from it?",

    # Asking for comparisons
    "What are the differences?",
    "How does it compare?",
    "How does it compare to alternatives?",
    "What are the similarities?",
    "Which should I use?",
    "When should I use it?",
    "What's the difference between them?",

    # Asking for alternatives
    "What are the alternatives?",
    "Are there other options?",
    "What else can I use?",
    "What's another way to do it?",
    "Are there similar features?",

    # Asking for best practices
    "What are the best practices?",
    "How should I use it?",
    "What's the recommended approach?",
    "Are there any gotchas?",
    "What should I avoid?",
    "What are common mistakes?",

    # Asking for limitations
    "Are there any limitations?",
    "What are its drawbacks?",
    "What doesn't it support?",
    "Are there any constraints?",
    "What are the trade-offs?",

    # Asking for usage patterns
    "Show me usage patterns",
    "How is it commonly used?",
    "What are typical use cases?",
    "Show me common patterns",
    "How do developers use it?",

    # Context-dependent references
    "What about the other one?",
    "How does this relate to what we discussed?",
    "Can you compare them?",
    "What's the connection?",
    "How do they work together?",
]


# ─── Test Session Definitions ──────────────────────────────

def build_session_queries(num_turns: int) -> list[Query]:
    """Build a list of queries for a session with specified number of turns.

    Distributes queries across difficulty levels with follow-ups for context testing.
    """
    queries = []

    # Distribute difficulty levels
    if num_turns <= 10:
        # 60% simple, 30% medium, 10% complex
        num_simple = int(num_turns * 0.6)
        num_medium = int(num_turns * 0.3)
        num_complex = num_turns - num_simple - num_medium
    elif num_turns <= 20:
        # 40% simple, 40% medium, 20% complex
        num_simple = int(num_turns * 0.4)
        num_medium = int(num_turns * 0.4)
        num_complex = num_turns - num_simple - num_medium
    else:
        # 30% simple, 40% medium, 30% complex
        num_simple = int(num_turns * 0.3)
        num_medium = int(num_turns * 0.4)
        num_complex = num_turns - num_simple - num_medium

    # Select queries from each difficulty
    import random
    random.seed(42)  # Reproducible selection

    selected_simple = random.sample(SIMPLE_QUERIES * 10, min(num_simple, len(SIMPLE_QUERIES) * 10))
    selected_medium = random.sample(MEDIUM_QUERIES * 10, min(num_medium, len(MEDIUM_QUERIES) * 10))
    selected_complex = random.sample(COMPLEX_QUERIES * 10, min(num_complex, len(COMPLEX_QUERIES) * 10))

    queries.extend(selected_simple)
    queries.extend(selected_medium)
    queries.extend(selected_complex)

    # Add follow-ups to test context (insert after every 2-3 queries)
    enhanced_queries = []
    follow_up_idx = 0
    for i, query in enumerate(queries):
        enhanced_queries.append(query)
        # Add follow-up after every 2-3 main queries
        if (i + 1) % 3 == 0 and len(enhanced_queries) < num_turns and follow_up_idx < len(FOLLOW_UP_QUERIES):
            follow_up = Query(
                text=FOLLOW_UP_QUERIES[follow_up_idx],
                difficulty="follow_up",
                expected_intent="follow_up",
                description="Context-dependent follow-up"
            )
            enhanced_queries.append(follow_up)
            follow_up_idx += 1

    return enhanced_queries[:num_turns]


# ─── Test Execution ────────────────────────────────────────

@dataclass
class TestResult:
    """Result of a single query test."""
    session_id: str
    turn: int
    query: str
    success: bool
    response_data: dict[str, Any] | None
    error: str | None
    duration: float
    difficulty: str


class IntegrationTestRunner:
    """Runs integration tests against the Docker Compose deployment."""

    def __init__(self):
        # Configure client with extended timeouts and connection pooling
        # Keep-alive helps prevent connection drops during long polling
        timeout = Timeout(
            connect=10.0,    # Connection timeout
            read=60.0,       # Read timeout (per read operation, not total)
            write=10.0,      # Write timeout
            pool=5.0         # Pool connection acquisition timeout
        )
        limits = Limits(
            max_keepalive_connections=5,
            max_connections=10,
            keepalive_expiry=300.0  # Keep connections alive for 5 minutes
        )
        self.client = httpx.Client(
            timeout=timeout,
            limits=limits,
            follow_redirects=True
        )
        self.results: list[TestResult] = []
        self.index_result: IndexingJobResult | None = None

    def check_health(self) -> bool:
        """Check if the gateway is healthy."""
        endpoint = f"{GATEWAY_URL}/api/health"
        print(f"\n[DEBUG] Health check")
        print(f"  Endpoint: {endpoint}")

        try:
            response = self.client.get(endpoint, timeout=10.0)
            print(f"  Status: {response.status_code}")

            if response.status_code == 200:
                data = response.json() if response.text else {}
                print(f"  Response: {data}")
                print("✓ Gateway is healthy")
                return True
            else:
                body = response.text[:200] if response.text else "No response body"
                print(f"  Response: {body}")
                print(f"✗ Gateway health check failed: {response.status_code}")
                return False
        except Exception as e:
            print(f"  Error type: {type(e).__name__}")
            print(f"  Error: {e}")
            print(f"✗ Cannot connect to gateway")
            print(f"  Make sure docker-compose is running and gateway is at {GATEWAY_URL}")
            return False

    def check_index_health(self) -> bool:
        """Check if index-related endpoints are accessible."""
        print(f"\n[DEBUG] Index health check")
        print(f"  Endpoint: {STATUS_ENDPOINT}")

        try:
            response = self.client.get(STATUS_ENDPOINT, timeout=10.0)
            print(f"  Status: {response.status_code}")

            if response.status_code == 200:
                data = response.json() if response.text else {}
                print(f"  Response: {data}")
                print("✓ Index status endpoint is accessible")
                return True
            else:
                body = response.text[:200] if response.text else "No response body"
                print(f"  Response: {body}")
                print(f"✗ Index status endpoint failed: {response.status_code}")
                return False
        except Exception as e:
            print(f"  Error type: {type(e).__name__}")
            print(f"  Error: {e}")
            print(f"✗ Cannot connect to index endpoint")
            return False

    def trigger_indexing(self) -> tuple[bool, str | None, str | None, float]:
        """
        Trigger full repository indexing.
        Returns: (success, job_id, error, duration)
        """
        start_time = time.time()

        request_data = {
            "repository_url": FASTAPI_REPO_URL,
            "repository_name": "fastapi",
            "clear_graph": True,
            "run_enrichment": True,
            "create_embeddings": True,
            "incremental": False
        }

        print(f"\n[DEBUG] Triggering indexing")
        print(f"  Endpoint: {INDEX_ENDPOINT}")
        print(f"  Method: POST")
        print(f"  Request data: {json.dumps(request_data, indent=2)}")
        print(f"  Timeout: 60.0s")

        try:
            response = self.client.post(
                INDEX_ENDPOINT,
                json=request_data,
                timeout=60.0
            )
            duration = time.time() - start_time

            print(f"  Response status: {response.status_code}")
            print(f"  Response time: {duration:.2f}s")

            if response.status_code == 200:
                data = response.json()
                print(f"  Response data: {json.dumps(data, indent=2)}")
                job_id = data.get("job_id")
                return True, job_id, None, duration
            else:
                body = response.text[:500] if response.text else "No response body"
                print(f"  Response body: {body}")
                error = response.json().get("detail", "Unknown error") if response.text else "No response"
                return False, None, f"HTTP {response.status_code}: {error}", duration

        except httpx.TimeoutException as e:
            duration = time.time() - start_time
            print(f"  Error: Timeout after 60 seconds")
            print(f"  Error type: {type(e).__name__}")
            return False, None, f"Timeout: {str(e)}", duration
        except Exception as e:
            duration = time.time() - start_time
            print(f"  Error type: {type(e).__name__}")
            print(f"  Error: {e}")
            return False, None, f"Request error: {str(e)}", duration

    def poll_job_status(
        self,
        job_id: str,
        verbose: bool = True
    ) -> tuple[bool, dict | None, float]:
        """
        Poll job status until completion or timeout.
        Returns: (success, result_data, duration)
        """
        start_time = time.time()
        max_wait_seconds = MAX_WAIT_MINUTES * 60
        last_progress = ""
        status_url = f"{STATUS_ENDPOINT}/{job_id}"

        if verbose:
            print(f"\n{'='*80}")
            print(f"Polling job status: {job_id}")
            print(f"Endpoint: {status_url}")
            print(f"Max wait time: {MAX_WAIT_MINUTES} minutes")
            print(f"Poll interval: {POLL_INTERVAL_SECONDS} seconds")
            print(f"{'='*80}\n")

        poll_count = 0
        while True:
            elapsed = time.time() - start_time
            poll_count += 1

            if elapsed > max_wait_seconds:
                if verbose:
                    print(f"\n✗ Timeout after {MAX_WAIT_MINUTES} minutes ({poll_count} polls)")
                    print(f"  Last endpoint: {status_url}")
                return False, None, elapsed

            try:
                if verbose and (poll_count == 1 or poll_count % 10 == 0):
                    print(f"[DEBUG] Poll #{poll_count} - Making GET request to: {status_url}")
                    print(f"[DEBUG] Timeout: 30.0s\n")

                response = self.client.get(
                    status_url,
                    timeout=30.0
                )

                if verbose and (poll_count == 1 or poll_count % 10 == 0):
                    print(f"[DEBUG] Response status code: {response.status_code}")
                    print(f"[DEBUG] Response headers: {dict(response.headers)}")

                if response.status_code != 200:
                    error_body = response.text[:500] if response.text else "No response body"
                    if verbose:
                        print(f"\n✗ HTTP error: {response.status_code}")
                        print(f"  Endpoint: {status_url}")
                        print(f"  Response: {error_body}")
                    return False, None, elapsed

                data = response.json()

                if verbose and (poll_count == 1 or poll_count % 10 == 0):
                    print(f"[DEBUG] Response data keys: {list(data.keys())}")
                    print(f"[DEBUG] Response data: {json.dumps(data, indent=2)}\n")

                status = data.get("status")
                progress = data.get("progress", {})

                # Display progress if changed
                progress_str = progress.get("current_phase", "")
                if progress_str and progress_str != last_progress:
                    elapsed_min = int(elapsed / 60)
                    elapsed_sec = int(elapsed % 60)
                    percent = progress.get("percent_complete", 0)
                    if verbose:
                        print(f"[{elapsed_min:02d}:{elapsed_sec:02d}] {progress_str} ({percent:.1f}%)")
                    last_progress = progress_str

                # Check completion
                if status == "completed":
                    result = data.get("result", {})
                    if verbose:
                        print(f"\n✓ Indexing completed in {elapsed/60:.1f} minutes")
                        print(f"  Total polls: {poll_count}")
                    return True, result, elapsed

                elif status == "failed":
                    error = data.get("error", "Unknown error")
                    if verbose:
                        print(f"\n✗ Indexing failed: {error}")
                        print(f"  Total polls: {poll_count}")
                        print(f"  Endpoint: {status_url}")
                    return False, None, elapsed

                # Still running, wait before next poll
                time.sleep(POLL_INTERVAL_SECONDS)

            except httpx.TimeoutException as e:
                if verbose:
                    print(f"\n✗ Request timeout after 30 seconds")
                    print(f"  Poll number: {poll_count}")
                    print(f"  Endpoint: {status_url}")
                    print(f"  Error: {e}")
                return False, None, elapsed
            except httpx.RemoteProtocolError as e:
                # Connection was closed - retry with exponential backoff
                retry_delay = min(5.0 * (1.5 ** min(poll_count % 5, 4)), 30.0)
                if verbose:
                    print(f"\n⚠ Server connection closed (poll #{poll_count})")
                    print(f"  Error: {e}")
                    print(f"  Retrying in {retry_delay:.1f}s...")
                time.sleep(retry_delay)
                continue
            except httpx.ReadError as e:
                # Connection aborted - retry with exponential backoff
                retry_delay = min(5.0 * (1.5 ** min(poll_count % 5, 4)), 30.0)
                if verbose:
                    print(f"\n⚠ Connection aborted (poll #{poll_count})")
                    print(f"  Error type: {type(e).__name__}")
                    print(f"  Error: {e}")
                    print(f"  Retrying in {retry_delay:.1f}s...")
                time.sleep(retry_delay)
                continue
            except httpx.ConnectError as e:
                # Failed to connect - retry with exponential backoff
                retry_delay = min(5.0 * (1.5 ** min(poll_count % 5, 4)), 30.0)
                if verbose:
                    print(f"\n⚠ Connection failed (poll #{poll_count})")
                    print(f"  Error: {e}")
                    print(f"  Retrying in {retry_delay:.1f}s...")
                time.sleep(retry_delay)
                continue
            except httpx.HTTPError as e:
                # Generic HTTP error - log and fail
                if verbose:
                    print(f"\n✗ HTTP error during polling")
                    print(f"  Poll number: {poll_count}")
                    print(f"  Endpoint: {status_url}")
                    print(f"  Error type: {type(e).__name__}")
                    print(f"  Error: {e}")
                return False, None, elapsed
            except Exception as e:
                if verbose:
                    print(f"\n✗ Unexpected error during polling")
                    print(f"  Poll number: {poll_count}")
                    print(f"  Endpoint: {status_url}")
                    print(f"  Error type: {type(e).__name__}")
                    print(f"  Error: {e}")
                    import traceback
                    print(f"\n  Traceback:")
                    traceback.print_exc()
                return False, None, elapsed

    def validate_graph_statistics(
        self,
        expected_min_nodes: int = 0,
        verbose: bool = True
    ) -> tuple[bool, dict | None, list[str]]:
        """
        Fetch and validate graph statistics.
        Returns: (success, stats_data, warnings)
        """
        if verbose:
            print(f"\n[DEBUG] Fetching graph statistics")
            print(f"  Endpoint: {GRAPH_STATS_ENDPOINT}")
            print(f"  Timeout: 30.0s")

        try:
            response = self.client.get(GRAPH_STATS_ENDPOINT, timeout=30.0)

            if verbose:
                print(f"  Status: {response.status_code}")

            if response.status_code != 200:
                body = response.text[:200] if response.text else "No response body"
                if verbose:
                    print(f"  Response: {body}")
                return False, None, [f"HTTP {response.status_code}"]

            data = response.json()

            if verbose:
                print(f"  Response data: {json.dumps(data, indent=2)}")

            warnings = []

            # Extract counts
            total_nodes = data.get("total_nodes", 0)
            total_edges = data.get("total_edges", 0)
            enrichment_coverage = data.get("enrichment_coverage", 0.0)

            # Validate
            if total_nodes < expected_min_nodes:
                warnings.append(f"Too few nodes: {total_nodes} (expected >= {expected_min_nodes})")

            if total_edges == 0:
                warnings.append("No edges found in graph")

            if enrichment_coverage < MIN_ENRICHMENT_COVERAGE:
                warnings.append(f"Low enrichment coverage: {enrichment_coverage:.1f}% (expected >= {MIN_ENRICHMENT_COVERAGE}%)")

            if verbose and not warnings:
                print(f"✓ Graph validation passed")
                print(f"  Nodes: {total_nodes:,}")
                print(f"  Edges: {total_edges:,}")
                print(f"  Enrichment: {enrichment_coverage:.1f}%")

            return True, data, warnings

        except httpx.TimeoutException as e:
            if verbose:
                print(f"  Error: Timeout after 30 seconds")
                print(f"  Error type: {type(e).__name__}")
            return False, None, [f"Timeout: {str(e)}"]
        except Exception as e:
            if verbose:
                print(f"  Error type: {type(e).__name__}")
                print(f"  Error: {e}")
            return False, None, [f"Request error: {str(e)}"]

    def run_index_tests(self, verbose: bool = True) -> IndexingJobResult:
        """
        Run complete index test suite:
        1. Health checks
        2. Trigger indexing
        3. Poll until completion
        4. Validate graph

        Returns IndexingJobResult with all metrics.
        """
        if verbose:
            print("\n" + "="*80)
            print("INDEX AGENT INTEGRATION TESTS")
            print("="*80)

        # Step 1: Health check
        if verbose:
            print("\n[HEALTH CHECK]")

        if not self.check_index_health():
            return IndexingJobResult(
                job_id="",
                success=False,
                duration=0,
                files_indexed=0,
                total_nodes=0,
                total_edges=0,
                enrichment_coverage=0.0,
                error="Health check failed"
            )

        # Step 2: Trigger indexing
        if verbose:
            print(f"\n[TRIGGER INDEXING]")
            print(f"Repository: {FASTAPI_REPO_URL}")
            print(f"Configuration: full index, enrichment: true, embeddings: true")

        success, job_id, error, trigger_duration = self.trigger_indexing()

        if not success:
            return IndexingJobResult(
                job_id="",
                success=False,
                duration=trigger_duration,
                files_indexed=0,
                total_nodes=0,
                total_edges=0,
                enrichment_coverage=0.0,
                error=error
            )

        if verbose:
            print(f"✓ Indexing job started: {job_id}")

        # Step 3: Poll until completion
        if verbose:
            print(f"\n[POLLING JOB STATUS]")

        success, result, poll_duration = self.poll_job_status(job_id, verbose)

        if not success:
            return IndexingJobResult(
                job_id=job_id,
                success=False,
                duration=poll_duration,
                files_indexed=0,
                total_nodes=0,
                total_edges=0,
                enrichment_coverage=0.0,
                error="Indexing job failed or timed out"
            )

        # Step 4: Validate graph statistics
        if verbose:
            print(f"\n[VALIDATE GRAPH]")

        stats_success, stats, warnings = self.validate_graph_statistics(
            expected_min_nodes=1000,
            verbose=verbose
        )

        if warnings and verbose:
            print("\n⚠ Validation warnings:")
            for warning in warnings:
                print(f"  - {warning}")

        # Extract metrics
        files_indexed = result.get("files", 0)
        node_counts = result.get("node_counts", {})
        edge_counts = result.get("edge_counts", {})
        total_nodes = sum(node_counts.values()) if node_counts else 0
        total_edges = sum(edge_counts.values()) if edge_counts else 0

        # Calculate enrichment coverage
        enriched = result.get("enriched", 0)
        total_entities = result.get("classes", 0) + result.get("functions", 0)
        enrichment_coverage = (100 * enriched / total_entities) if total_entities > 0 else 0.0

        # Print summary
        if verbose:
            print(f"\n{'='*80}")
            print("INDEX TEST SUMMARY")
            print(f"{'='*80}")
            print(f"Files indexed: {files_indexed}")
            print(f"Total nodes: {total_nodes:,}")
            print(f"Total edges: {total_edges:,}")
            print(f"Enrichment coverage: {enrichment_coverage:.1f}%")
            print(f"Duration: {poll_duration/60:.1f} minutes")
            print(f"Status: {'✓ SUCCESS' if not warnings else '⚠ WARNINGS'}")
            print(f"{'='*80}\n")

        return IndexingJobResult(
            job_id=job_id,
            success=True,
            duration=poll_duration,
            files_indexed=files_indexed,
            total_nodes=total_nodes,
            total_edges=total_edges,
            enrichment_coverage=enrichment_coverage,
            error=None if not warnings else "; ".join(warnings)
        )

    def send_chat_message(
        self,
        message: str,
        session_id: str,
        verbose: bool = False
    ) -> tuple[bool, dict | None, str | None, float]:
        """Send a chat message and return (success, response_data, error, duration)."""
        start_time = time.time()

        request_data = {
            "message": message,
            "session_id": session_id,
            "stream": False
        }

        if verbose:
            print(f"\n[DEBUG] Sending chat message")
            print(f"  Endpoint: {CHAT_ENDPOINT}")
            print(f"  Method: POST")
            print(f"  Message: {message[:100]}{'...' if len(message) > 100 else ''}")
            print(f"  Session ID: {session_id}")
            print(f"  Timeout: {TIMEOUT}s")

        try:
            response = self.client.post(
                CHAT_ENDPOINT,
                json=request_data,
                timeout=TIMEOUT
            )
            duration = time.time() - start_time

            if verbose:
                print(f"  Status: {response.status_code}")
                print(f"  Response time: {duration:.2f}s")

            if response.status_code == 200:
                data = response.json()
                if verbose:
                    # Print abbreviated response
                    resp_text = data.get("response", "")
                    print(f"  Response: {resp_text[:200]}{'...' if len(resp_text) > 200 else ''}")
                    print(f"  Intent: {data.get('intent', 'unknown')}")
                    print(f"  Agents: {data.get('agents_called', [])}")
                return True, data, None, duration
            else:
                body = response.text[:300] if response.text else "No response body"
                if verbose:
                    print(f"  Response body: {body}")
                error_detail = response.json().get("detail", "Unknown error") if response.text else "No response"
                return False, None, f"HTTP {response.status_code}: {error_detail}", duration

        except httpx.TimeoutException as e:
            duration = time.time() - start_time
            if verbose:
                print(f"  Error: Timeout after {TIMEOUT} seconds")
                print(f"  Error type: {type(e).__name__}")
            return False, None, f"Timeout after {TIMEOUT}s", duration
        except Exception as e:
            duration = time.time() - start_time
            if verbose:
                print(f"  Error type: {type(e).__name__}")
                print(f"  Error: {e}")
            return False, None, f"Request error: {str(e)}", duration

    def run_session(
        self,
        session_name: str,
        num_turns: int,
        verbose: bool = True
    ) -> list[TestResult]:
        """Run a complete session with specified number of turns."""
        session_id = str(uuid.uuid4())
        queries = build_session_queries(num_turns)
        session_results = []

        print(f"\n{'='*80}")
        print(f"SESSION: {session_name} ({num_turns} turns)")
        print(f"Session ID: {session_id}")
        print(f"{'='*80}\n")

        for turn, query in enumerate(queries, 1):
            if verbose:
                print(f"\nTurn {turn}/{num_turns} [{query.difficulty.upper()}]: {query.text}")

            success, response_data, error, duration = self.send_chat_message(
                query.text,
                session_id,
                verbose=verbose
            )

            result = TestResult(
                session_id=session_id,
                turn=turn,
                query=query.text,
                success=success,
                response_data=response_data,
                error=error,
                duration=duration,
                difficulty=query.difficulty
            )
            session_results.append(result)
            self.results.append(result)

            if success:
                response_preview =  response_data
                intent = response_data.get("intent", "unknown")
                agents = ", ".join(response_data.get("agents_called", []))
                errors = response_data.get("errors", {})

                status = "✓ SUCCESS"
                if errors:
                    status = "⚠ PARTIAL"

                if verbose:
                    print(f"  {status} ({duration:.2f}s)")
                    print(f"  Intent: {intent} | Agents: {agents}")
                    if errors:
                        print(f"  Errors: {list(errors.keys())}")
                    print(f"  Response: {response_preview['response']}")
                    print(f"  Suggestive pills: {response_preview['suggestive_pills']}")
                    print()
            else:
                if verbose:
                    print(f"  ✗ FAILED ({duration:.2f}s)")
                    print(f"  Error: {error}")
                    print()

        # Session summary
        successful = sum(1 for r in session_results if r.success)
        failed = len(session_results) - successful
        avg_duration = sum(r.duration for r in session_results) / len(session_results)

        print(f"\n{'-'*80}")
        print(f"Session Summary: {successful}/{len(session_results)} successful ({failed} failed)")
        print(f"Average response time: {avg_duration:.2f}s")
        print(f"{'-'*80}\n")

        return session_results

    def run_all_sessions(self) -> None:
        """
        Run complete test suite:
        1. Index agent tests (populate Neo4j)
        2. Chat tests (40 turns)
        """
        print("\n" + "="*80)
        print("DOCKER COMPOSE INTEGRATION TESTS - COMPLETE SUITE")
        print("="*80)

        # Check health first
        if not self.check_health():
            print("\n✗ Aborting tests: Gateway is not accessible")
            print("  Run: docker-compose up -d")
            print("  Wait for all services to be healthy")
            return

        # Phase 1: Run index tests to populate graph
        self.index_result = self.run_index_tests(verbose=True)

        if not self.index_result.success:
            print("\n✗ Index tests failed - Cannot proceed with chat tests")
            print(f"  Error: {self.index_result.error}")
            print("\n  Please check:")
            print("    - Neo4j is running and accessible")
            print("    - OpenAI API key is configured")
            print("    - Repository URL is accessible")
            print("    - Indexer service logs: docker logs graphical-rag-indexer")
            return

        if self.index_result.error:  # Warnings present
            print(f"\n⚠ Index tests completed with warnings: {self.index_result.error}")
            print("  Proceeding with chat tests...\n")

        time.sleep(3)  # Brief pause between index and chat tests

        # Phase 2: Run chat tests (40 turns)
        print("\n" + "="*80)
        print("CHAT ENDPOINT TESTS (40 TURNS)")
        print("="*80)

        self.run_session("Stress Test (40 turns)", 40, verbose=True)

        # Final report
        self.print_final_report()

    def print_final_report(self) -> None:
        """Print comprehensive test report."""
        print("\n" + "="*80)
        print("FINAL TEST REPORT")
        print("="*80 + "\n")

        # Index phase summary
        if self.index_result:
            print("INDEX PHASE:")
            print(f"  Job ID: {self.index_result.job_id}")
            print(f"  Files indexed: {self.index_result.files_indexed}")
            print(f"  Graph nodes: {self.index_result.total_nodes:,}")
            print(f"  Graph edges: {self.index_result.total_edges:,}")
            print(f"  Enrichment: {self.index_result.enrichment_coverage:.1f}%")
            print(f"  Duration: {self.index_result.duration/60:.1f} minutes")
            print(f"  Status: {'✓ SUCCESS' if self.index_result.success else '✗ FAILED'}")
            print()

        # Chat phase summary
        print("CHAT PHASE:")
        total = len(self.results)
        successful = sum(1 for r in self.results if r.success)
        failed = total - successful

        # Overall stats
        print(f"Total queries: {total}")
        print(f"Successful: {successful} ({100*successful/total:.1f}%)")
        print(f"Failed: {failed} ({100*failed/total:.1f}%)")
        print()

        # By difficulty
        print("Results by difficulty:")
        for difficulty in ["simple", "medium", "complex", "follow_up"]:
            diff_results = [r for r in self.results if r.difficulty == difficulty]
            if diff_results:
                diff_success = sum(1 for r in diff_results if r.success)
                print(f"  {difficulty.capitalize()}: {diff_success}/{len(diff_results)} successful")
        print()

        # Performance stats
        successful_results = [r for r in self.results if r.success]
        if successful_results:
            durations = [r.duration for r in successful_results]
            print("Response time statistics (successful queries):")
            print(f"  Min: {min(durations):.2f}s")
            print(f"  Max: {max(durations):.2f}s")
            print(f"  Average: {sum(durations)/len(durations):.2f}s")
            print(f"  Median: {sorted(durations)[len(durations)//2]:.2f}s")
            print()

        # Session context preservation
        print("Session context preservation:")
        sessions = {}
        for r in self.results:
            if r.session_id not in sessions:
                sessions[r.session_id] = []
            sessions[r.session_id].append(r)

        for i, (session_id, session_results) in enumerate(sessions.items(), 1):
            session_success = sum(1 for r in session_results if r.success)
            print(f"  Session {i}: {session_success}/{len(session_results)} successful")
        print()

        # Failed queries
        if failed > 0:
            print(f"Failed queries ({failed}):")
            for r in self.results:
                if not r.success:
                    print(f"  - Turn {r.turn}: {r.query[:60]}...")
                    print(f"    Error: {r.error}")
            print()

        # Intent coverage (from successful queries)
        intents_seen = set()
        for r in successful_results:
            if r.response_data:
                intent = r.response_data.get("intent")
                if intent:
                    intents_seen.add(intent)

        print(f"Intent coverage: {len(intents_seen)} unique intents detected")
        print(f"  Intents: {', '.join(sorted(intents_seen))}")
        print()

        # Agents used
        agents_seen = set()
        for r in successful_results:
            if r.response_data:
                agents = r.response_data.get("agents_called", [])
                agents_seen.update(agents)

        print(f"Agent coverage: {len(agents_seen)} unique agents called")
        print(f"  Agents: {', '.join(sorted(agents_seen))}")
        print()

        # Overall verdict
        success_rate = 100 * successful / total
        print("="*80)
        if success_rate >= 90:
            print("✓ TESTS PASSED - Excellent performance!")
        elif success_rate >= 75:
            print("⚠ TESTS PASSED - Some issues detected")
        else:
            print("✗ TESTS FAILED - Significant issues detected")
        print(f"  Success rate: {success_rate:.1f}%")
        print("="*80 + "\n")

    def cleanup(self):
        """Cleanup resources."""
        self.client.close()


# ─── Main Execution ────────────────────────────────────────

def main():
    """Run the integration tests."""
    runner = IntegrationTestRunner()

    try:
        runner.run_all_sessions()
    except KeyboardInterrupt:
        print("\n\nTests interrupted by user")
    except Exception as e:
        print(f"\n\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        runner.cleanup()


if __name__ == "__main__":
    main()
