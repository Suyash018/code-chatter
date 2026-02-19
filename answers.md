
================================================================================
SIMPLIFIED CHAT INTEGRATION TESTS
================================================================================

[HEALTH CHECK]
Endpoint: http://localhost:8000/api/health
Status: 200
Response: {'status': 'healthy', 'service': 'FastAPI Gateway', 'version': '0.1.0'}
✓ Gateway is healthy


Session ID: a687b027-27b4-421c-a8e0-07b44068c116


================================================================================
SIMPLE QUERIES (Single Agent)
================================================================================

────────────────────────────────────────────────────────────────────────────────
Turn 1/2 [SIMPLE] - FastAPI class lookup

[QUERY] What is the FastAPI class?
✓ Response (181.12s):
  Intent: code_explanation
  Agents: graph_query, code_analyst

  Full Response:
The FastAPI class is the main application entrypoint of the FastAPI framework, built as a subclass of Starlette to facilitate efficient API development. It controls routing, middleware, OpenAPI schema generation, and documentation endpoints, while providing features such as dependency injection, interactive docs, and lifecycle event handling. It uses design patterns like Facade, Decorator, and Adapter to simplify complex underlying functionalities. FastAPI supports HTTP and WebSocket routes, custom exception handlers, middleware management, and configuration options like API metadata and docs URLs. It enables rapid, extensible, and user-friendly API development with Python.

  Suggestions: ['What is FastAPI?', 'Use cases?', 'Example code?']

────────────────────────────────────────────────────────────────────────────────
Turn 2/2 [SIMPLE] - Depends function docstring

[QUERY] Show me the docstring for the Depends function
✓ Response (34.42s):
  Intent: code_explanation
  Agents: graph_query, code_analyst

  Full Response:
The `Depends` function in FastAPI is used to declare a dependency for dependency injection. You provide it with a callable (usually a function) that FastAPI will call automatically. You should not call this function directly. The FastAPI docs provide a detailed explanation and examples, including using `Depends` with Python's `Annotated` type for cleaner code.

Summary:
- Declares a FastAPI dependency.
- Takes a callable that FastAPI calls.
- Not to be called directly by the user.
- Example usage with `Annotated`.
- More info in FastAPI documentation.

  Suggestions: ['What is dependency injection?', 'Example usage of Depends', 'Explain Annotated type']

================================================================================
MEDIUM QUERIES (2-3 Agents)
================================================================================

────────────────────────────────────────────────────────────────────────────────
Turn 1/3 [MEDIUM] - Request validation

[QUERY] How does FastAPI handle request validation?
✓ Response (68.32s):
  Intent: code_explanation
  Agents: graph_query, code_analyst

  Full Response:
FastAPI performs request validation by using Pydantic models to declare expected input schemas. When a request is received, FastAPI automatically checks the request data against these schemas—covering bodies, query parameters, headers, cookies, and path parameters. If validation fails, it raises specific exceptions (`RequestValidationError` for HTTP endpoints or `WebSocketRequestValidationError` for WebSocket routes). The FastAPI app has built-in exception handlers that catch these errors and generate clear HTTP responses (like a 422 status with detailed error messages). This integration ensures seamless, automatic validation and helpful feedback for developers and clients alike.

  Suggestions: ['How does Pydantic work?', 'Explain RequestValidationError', 'What are exception handlers?']

────────────────────────────────────────────────────────────────────────────────
Turn 2/3 [MEDIUM] - APIRouter inheritance

[QUERY] What classes inherit from APIRouter?
✓ Response (11.71s):
  Intent: dependency_query
  Agents: graph_query

  Full Response:
In the FastAPI codebase, there are no classes that inherit from APIRouter. APIRouter is defined as a standalone class in the fastapi.routing module and is not extended by any subclasses.

  Suggestions: ['What is APIRouter?', 'How to use APIRouter?']

────────────────────────────────────────────────────────────────────────────────
Turn 3/3 [MEDIUM] - Routing decorators

[QUERY] Find all decorators used in the routing module
✓ Response (38.68s):
  Intent: pattern_search
  Agents: graph_query, code_analyst

  Full Response:
The `fastapi.routing` module primarily provides decorator methods through the `APIRouter` class to register HTTP routes, WebSocket handlers, and event handlers declaratively. These include decorators like `APIRouter.get`, `APIRouter.post`, `APIRouter.websocket`, and others for different HTTP methods and WebSocket connections. Although there are no standalone decorated functions or classes in the module itself, these decorators serve as factory methods to associate user-defined functions with routes or events on the `APIRouter` instance.

  Suggestions: ['List FastAPI decorators', 'Explain APIRouter', 'What are WebSocket decorators?']

================================================================================
COMPLEX QUERIES (Multiple Agents + Synthesis)
================================================================================

────────────────────────────────────────────────────────────────────────────────
Turn 1/4 [COMPLEX] - Request lifecycle

[QUERY] Explain the complete lifecycle of a FastAPI request
✓ Response (100.13s):
  Intent: general_question
  Agents: graph_query, code_analyst

  Full Response:
The lifecycle of a FastAPI request includes initialization, middleware stacking, ASGI application call, routing, dependency injection, exception handling, response generation, OpenAPI schema generation, and asynchronous resource cleanup. FastAPI initializes the app and router, builds a middleware stack, routes incoming requests to path operations, validates parameters, handles exceptions, encodes responses, serves API docs, and ensures async resources are properly cleaned up after requests. This flow ensures a robust and efficient API framework.

  Suggestions: ['Explain dependency injection', 'How does routing work?', 'Describe middleware stack']

────────────────────────────────────────────────────────────────────────────────
Turn 2/4 [COMPLEX] - DI with examples

[QUERY] How does dependency injection work and show me examples from the codebase
✓ Response (108.21s):
  Intent: code_explanation
  Agents: graph_query, code_analyst

  Full Response:
Dependency injection (DI) in FastAPI allows developers to declare dependencies on callable functions or classes using the `Depends` marker. FastAPI analyzes these dependencies recursively to build a dependency graph and executes them in order to inject their results into endpoint functions. The core concepts include:

1. **Declaration**: Using `Depends` to mark parameters as dependencies.
2. **Analysis**: FastAPI internally uses the `get_dependant` function to inspect callables, their parameters, nested dependencies, and build a metadata model called `Dependant`.
3. **Metadata Model (`Dependant`)**: Stores details about parameters, nested deps, caching, lifecycle, and security scopes.
4. **Runtime Resolution**: FastAPI resolves dependencies by calling them in order, caching results as specified, and injecting returned values into endpoint handlers.

Example:
```python
from fastapi import FastAPI, Depends

app = FastAPI()

async def common_parameters(q: str = None, skip: int = 0, limit: int = 100):
    return {"q": q, "skip": skip, "limit": limit}

@app.get("/items/")
async def read_items(commons = Depends(common_parameters)):
    return commons
```
This system enables reusable, composable logic such as shared query parameters, authentication, or database sessions across endpoints.

  Suggestions: ['Explain Depends usage', 'Show example code', 'What is Dependant class?']

────────────────────────────────────────────────────────────────────────────────
Turn 3/4 [COMPLEX] - Path vs Query implementation

[QUERY] Compare how Path and Query parameters are implemented
✓ Response (110.83s):
  Intent: code_comparison
  Agents: graph_query, code_analyst

  Full Response:
In FastAPI, Path and Query parameters serve distinct roles in request handling but share similarities in metadata and validation capabilities. Path parameters are always required and are part of the URL path, while Query parameters are optional by default and come from the query string. Both use wrapper functions to provide rich metadata, validation, and OpenAPI support. Path uses adapter and facade design patterns; Query uses factory and facade patterns. Together, they enable clear, validated, and well-documented API parameter declarations.

  Suggestions: ['Explain Path usage', 'Explain Query usage', 'Show example code']

────────────────────────────────────────────────────────────────────────────────
Turn 4/4 [COMPLEX] - Core design patterns

[QUERY] What design patterns are used in FastAPI's core and why?
✓ Response (68.15s):
  Intent: pattern_search
  Agents: graph_query, code_analyst

  Full Response:
FastAPI uses several design patterns such as Decorator for route registration, Facade for simplifying the API interface, Registry for centralized management, Adapter for integrating user code with Starlette and ASGI, Dependency Injection for modular components, Middleware pattern for request pipeline processing, Factory for dynamic route creation, Lazy Initialization and Singleton for OpenAPI schema caching, and Context Management for resource handling. These combine to make FastAPI modular, extensible, and user-friendly for building async Python APIs.

  Suggestions: ['Explain Decorator Pattern', 'What is Dependency Injection?', 'Describe Middleware Pattern']

================================================================================
FINAL TEST REPORT
================================================================================

Total queries: 9
Successful: 9 (100.0%)
Failed: 0 (0.0%)

Results by difficulty:
  Simple: 2/2 successful
  Medium: 3/3 successful
  Complex: 4/4 successful

Response time statistics (successful queries):
  Min: 11.71s
  Max: 181.12s
  Average: 80.17s
  Median: 68.32s

Intent coverage: 5 unique intents
  Intents: code_comparison, code_explanation, dependency_query, general_question, pattern_search

Agent coverage: 2 unique agents
  Agents: code_analyst, graph_query

================================================================================
✓ TESTS PASSED - Excellent performance!
  Success rate: 100.0%
================================================================================
