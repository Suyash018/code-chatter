"""
Dependency Tracer — get_dependencies, get_dependents, trace_imports tool implementations.

Traverses the knowledge graph to find dependency chains and import relationships.

NOTE: This file is an architectural stub. The actual implementation of dependency
tracing functionality is located in graph_store.py (GraphStore class).
The graph_query MCP server (server.py) exposes these tools directly without
separate module files.

Historical Context:
-------------------
This file was created during initial architecture planning to separate graph
traversal concerns. The implementation consolidated all traversal logic into
GraphStore to:
- Share the Neo4jGraph connection efficiently
- Avoid code duplication for common traversal patterns
- Simplify the call stack for debugging

Current Implementation:
-----------------------
- get_dependencies() → GraphStore.get_dependencies() in graph_store.py:222-276
- get_dependents() → GraphStore.get_dependents() in graph_store.py:278-332
- trace_imports() → GraphStore.trace_imports() in graph_store.py:334-362

This stub is retained for:
1. Documentation of original architectural intent
2. Potential future refactoring if module separation is needed
3. Reference for understanding the system's evolution

If you need to modify dependency tracing logic, edit graph_store.py instead.
"""

# This file intentionally left empty - see docstring above for details.
