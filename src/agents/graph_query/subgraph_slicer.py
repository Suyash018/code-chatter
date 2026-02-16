"""
Subgraph Slicer — Bidirectional graph expansion (info.md section 6).

Expands 2-3 hops bidirectionally from seed nodes to produce a
context-rich subgraph slice for the Code Analyst agent.
"""

# TODO: Implement bidirectional expansion logic
# Key operations:
#   - Find seed nodes via vector search + direct lookup
#   - Expand N hops in both directions (callers, callees, inheritance)
#   - Return subgraph slice with source snippets
