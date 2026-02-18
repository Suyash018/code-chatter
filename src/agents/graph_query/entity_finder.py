"""
Entity Finder — find_entity and find_related tool implementations.

Locates code entities by name, qualified name, or semantic similarity.

NOTE: This file is an architectural stub. The actual implementation of entity
finding functionality is located in graph_store.py (GraphStore class).
The graph_query MCP server (server.py) exposes these tools directly without
separate module files.

Historical Context:
-------------------
This file was created during initial architecture planning to separate concerns
(entity finding, dependency tracing, query execution, subgraph slicing) into
distinct modules. However, the implementation consolidated all query logic
into GraphStore for simplicity and to avoid circular dependencies.

Current Implementation:
-----------------------
- find_entity() → GraphStore.find_entity() in graph_store.py:89-190
- find_related() → GraphStore.find_related() in graph_store.py:364-412

This stub is retained for:
1. Documentation of original architectural intent
2. Potential future refactoring if module separation is needed
3. Reference for understanding the system's evolution

If you need to modify entity finding logic, edit graph_store.py instead.
"""

# This file intentionally left empty - see docstring above for details.
