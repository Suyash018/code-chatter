# Project Briefing: FastAPI Codebase Knowledge Graph Chat Agent

> This document summarizes all architectural decisions, design rationale, and implementation findings from our design sessions. It is intended to give Claude Code (or any developer) a complete understanding of what was decided and why, without needing to read any code. The assignment document will be provided separately.

---

## 1. Project Goal

Build a multi-agent MCP (Model Context Protocol) system that can answer natural language questions about the FastAPI codebase. The system indexes the repository into a Neo4j knowledge graph and uses specialized agents to query the graph and reason about the code.

---

## 2. Research Foundation

Three academic papers were studied to inform the architecture:

**Paper 1 — LLMxCPG (USENIX Security '25, Lekssays et al.)**
Uses Code Property Graphs with LLMs for vulnerability detection. Key takeaway borrowed: **graph-guided slicing** — use the graph to extract a small, relevant subgraph before sending it to the LLM, reducing context size by 67-90% while preserving what matters.

**Paper 2 — Reliable Graph-RAG for Codebases (arXiv 2601.08773, Chinthareddy)**
Benchmarks three approaches for code RAG: vector-only, LLM-extracted knowledge graph (LLM-KB), and deterministic AST-derived knowledge graph (DKB). Key findings that drove our decisions: DKB builds in seconds vs minutes, has complete corpus coverage (LLM-KB missed 377 files on Shopizer), costs ~20x less, and performs comparably on accuracy.

**Paper 3 — Autonomous Issue Resolver / DTG (arXiv 2512.08492)**
Proposes Data Transformation Graphs where data states are nodes and functions are edges. Key takeaway borrowed: **data-flow awareness** — tracking how data transforms through the system helps answer lifecycle questions like "how does a request flow through FastAPI."

---

## 3. Core Architectural Decisions

### Decision 1: Deterministic AST Graph as Foundation (DKB Approach)

**Chosen:** Use Python's built-in `ast` module for deterministic graph extraction. Do NOT use an LLM to construct the graph.

**Rationale:** Paper 2 showed DKB dominates on speed, cost, coverage, and reliability. For a Python codebase, `ast` module gives typed AST nodes natively — even easier than Tree-sitter. The graph must be 100% complete (no skipped files) because answer quality depends on graph quality.

### Decision 2: Two-Layer Graph (AST + LLM Enrichment)

**Chosen:** Hybrid approach — deterministic AST extraction for structure, then LLM enrichment as a second pass for semantics.

**Layer 1 (AST — deterministic, fast, cheap):** Classes, functions, methods, parameters, decorators, imports, call relationships, inheritance chains. All extracted from syntax tree.

**Layer 2 (LLM — semantic, expensive, optional):** Purpose summaries, design pattern detection, complexity ratings, domain concepts, semantic relationships like IMPLEMENTS_PATTERN, COLLABORATES_WITH, DATA_FLOWS_TO. Added as properties on existing nodes and new edge types.

**Rationale:** Pure AST can't infer "this function validates input" or "this is dependency injection." LLM-KB edges out on accuracy for semantic/architectural queries. But using LLM only to *enrich* an existing graph (not build from scratch) keeps cost reasonable and ensures completeness.

### Decision 3: Strategy B (Fine-Grained Diff) for Incremental Updates

**Chosen:** Strategy B with content-hash-based enrichment caching.

Three incremental update strategies were evaluated in depth:

**Strategy A — Delete & Recreate:** Delete entire file subgraph, re-parse, recreate. Simple and always correct, but wastes LLM enrichment money on unchanged functions.

**Strategy B — Fine-Grained Diff:** Compare new AST against existing graph by content hash. Only update changed entities. Preserves enrichment on unchanged code. More complex but cost-efficient.

**Strategy C — Hybrid (A+B):** Use B for simple changes, fall back to A for complex refactors. Rejected — inherits complexity of both, adds arbitrary thresholds, and debugging becomes nearly impossible because the same commit can trigger different codepaths.

**Key optimization decided:** Cache LLM enrichment by content hash. When Strategy B rebuilds nodes, check if the function body actually changed. If the content hash matches, skip the LLM call and restore cached enrichment. This gives Strategy A's correctness with Strategy B's cost savings.

**Tradeoffs acknowledged:**
- Strategy B is more complex to implement (AST diffing, rename detection)
- Risk of stale semantics if change impact is misjudged
- But for FastAPI's scale (~200 files), the cost savings justify the complexity
- A reverse dependency index was considered and rejected — it becomes stale itself and requires the same AST parsing you'd do anyway

### Decision 4: Neo4j as the Graph Database

**Chosen:** Neo4j with Cypher queries, native vector indexes for hybrid search.

**Graph Schema (nodes):** File, Module, Class, Function, Parameter, Decorator, DesignPattern, DomainConcept, EnrichmentCache, IndexState

**Graph Schema (edges):** CONTAINS, DEFINES_MODULE, IMPORTS, INHERITS_FROM, CALLS, DECORATED_BY, HAS_PARAMETER, IMPLEMENTS_PATTERN, RELATES_TO_CONCEPT, COLLABORATES_WITH, DATA_FLOWS_TO

**Node identity strategy:** Deterministic composite keys (not auto-generated IDs):
- File: `path`
- Class: `module_name.class_name`
- Function: `module_name.class_name.function_name` (qualified name)
- Uses MERGE in Cypher to match existing nodes correctly

### Decision 5: Multi-Agent Architecture (MCP Servers)

Five components, each an MCP server:

**Orchestrator Agent (MCP Server #1):** Coordinates all other agents. Routes indexing requests to Indexer, query requests to Graph Query → Code Analyst pipeline. Synthesizes final answers.

**Indexer Agent (MCP Server #2):** Owns the entire graph construction pipeline. Tools: `index_repository` (full bootstrap), `index_file` (incremental Strategy B), `parse_python_ast`, `extract_entities`, `get_index_status`. Also runs LLM enrichment and vector embedding creation.

**Graph Query Agent (MCP Server #3):** Read-only. Queries Neo4j for structural traversal and vector similarity search. Tools: `find_entity`, `get_dependencies`, `get_dependents`, `trace_imports`, `find_related`, `execute_query`. Produces the "subgraph slice" that the Code Analyst reasons over.

**Code Analyst Agent (MCP Server #4):** Receives subgraph slices and reasons about them. Tools: `analyze_function`, `explain_implementation`, `find_patterns`. Does NOT touch Neo4j directly.

**FastAPI Gateway:** HTTP/WebSocket layer. `POST /api/index` → Orchestrator → Indexer. `POST /api/chat` → Orchestrator → Graph Query → Code Analyst → response.

### Decision 6: Query-Time Flow (Bidirectional Graph Expansion)

Borrowed from Paper 2's DKB approach:

1. User query arrives at Orchestrator
2. Orchestrator identifies entities mentioned (e.g., "FastAPI class", "Depends")
3. Graph Query Agent finds seed nodes via vector search + direct lookup
4. Expands 2-3 hops bidirectionally in the graph (callers, callees, inheritance)
5. Returns subgraph slice (the "graph-guided context" from Paper 1)
6. Code Analyst Agent receives slice + source snippets, generates answer

This combines three retrieval methods: graph traversal (structural navigation), vector similarity (fuzzy matching), and semantic properties (LLM-enriched understanding). All three live in the same Neo4j database.

---

## 4. AST Parser — Bugs Found and Fixed

The AST parser was validated against the real FastAPI codebase (47 Python files). Two rounds of auditing found and fixed 11 bugs total. This section documents what went wrong so you know what edge cases to handle.

### Round 1 (4 bugs)

| Bug | Impact | Fix |
|-----|--------|-----|
| Class attributes not extracted | 272 attributes invisible to graph | Walk class body for Assign/AnnAssign nodes |
| Nested functions missed | 16 functions invisible | Recurse into function bodies for nested FunctionDef |
| Relative imports unresolved | 68 imports had wrong module path | Resolve dots using parent package from file path |
| TYPE_CHECKING imports not separated | False runtime dependency edges | Check if import is inside `if TYPE_CHECKING:` block |

### Round 2 (7 bugs)

| Bug | Impact | Fix |
|-----|--------|-----|
| Call double-counting | 69 false call relationships | Replace `ast.walk()` with worklist that skips nested scope boundaries (FunctionDef, ClassDef) |
| Class content hash ignores decorators | Strategy B blind to decorator changes | Include decorators in source extraction (start from first decorator line) |
| Source extraction missing decorators | LLM enrichment never saw `@dataclass`, `@app.get()` etc. | Use `decorator_list[0].lineno` as start line instead of `node.lineno` |
| `import X, Y` only captures first | `for alias in node.names: return` exits on first iteration | Changed return type to `list[dict]`, loop appends all |
| Conditional imports missed | 2 imports in `if sys.version_info` blocks invisible | Added else branch to `ast.If` handler for non-TYPE_CHECKING conditionals |
| Try/except imports missed | 5 optional dependency imports invisible (`ujson`, `orjson`, etc.) | Added `ast.Try` handler in parse_file main loop |
| Positional-only params missed | `args.posonlyargs` not read, only `args.args` | Combined `posonlyargs + args` list, added `kind` field to all params |

### Final Validated Metrics (FastAPI codebase)

| Metric | Count |
|--------|-------|
| Files parsed | 47 (0 errors) |
| Classes | 96 |
| Class attributes | 272 |
| Functions (top-level) | 108 |
| Methods | 140 |
| Nested functions | 16 |
| Imports | 395 (68 relative, 1 TYPE_CHECKING, 2 conditional, 5 try/except) |
| Call relationships | 938 (after fixing double-counting from 1007) |

### Known Design Limitations (Not Bugs)

These are things the parser intentionally does not handle:

1. **Module-level variables (42):** TypeVars, logger instances, constants. Not graph entities.
2. **Module-level calls (4):** `Schema.model_rebuild()`, `main()` — no caller to attribute them to.
3. **`self.method()` resolution (20 calls):** Need type inference to resolve which class's method is being called. Captured as unresolved call names.
4. **Metaclass keyword args:** `class Foo(metaclass=ABCMeta)` — keywords not in bases list. 0 in FastAPI.
5. **Star imports:** `from X import *` captured but can't resolve individual names without runtime.
6. **Nested classes:** Classes inside functions — 0 in FastAPI.

---

## 5. Implementation Modules

The Indexer Agent consists of 7 modules plus config:

| Module | Responsibility |
|--------|---------------|
| `server.py` | MCP server entry point, tool definitions, request routing |
| `ast_parser.py` | Deterministic AST extraction (the heavily audited component) |
| `graph_manager.py` | All Neo4j interactions — schema, CRUD, cross-file resolution, caching |
| `enrichment.py` | LLM enrichment logic — batched calls, JSON parsing, semantic edge creation |
| `repository.py` | Git operations — clone, discover Python files, read files, get commit hash |
| `incremental_updater.py` | Strategy B diff logic — content hash comparison, selective re-enrichment |
| `config.py` | Pydantic Settings for environment-based configuration |
| `docker-compose.yml` | Neo4j + Indexer Agent container setup |

---

## 6. Key Technical Details

**Content hashing:** SHA-256, truncated to 16 hex chars. Applied to normalized (stripped) source code. For functions, source now includes decorators. Used as the primary change detection mechanism in Strategy B.

**Qualified names:** `module_name.class_name.function_name` format. For `__init__.py` files, the `__init__` part is stripped (e.g., `fastapi/__init__.py` → module `fastapi`). No collisions across entire FastAPI codebase.

**Enrichment caching:** EnrichmentCache nodes in Neo4j keyed by content_hash. Before making an LLM call, check if the content hash already has cached enrichment. This is what makes Strategy B cost-effective — unchanged functions reuse cached enrichment without any API calls.

**Import resolution:** Relative imports (e.g., `from .routing import APIRouter`) are resolved using the file's package path. The level (number of dots) determines how many parent directories to traverse.

**Call extraction:** Uses a worklist-based traversal (not `ast.walk()`) that stops at nested scope boundaries. This prevents a parent function from claiming calls that belong to nested functions.

---

## 7. What Still Needs Building

The current deliverable is the **Indexer Agent** (fully implemented + audited AST parser). The remaining components are:

1. **Graph Query Agent** — Neo4j read queries, vector search, subgraph slicing
2. **Code Analyst Agent** — LLM-based reasoning over code slices
3. **Orchestrator Agent** — Multi-agent coordination, conversation context
4. **FastAPI Gateway** — HTTP/WebSocket API layer
5. **Vector embeddings** — Integration with embedding model (placeholder exists in graph_manager)
6. **System prompts** — Each agent needs a detailed system prompt (Indexer Agent's is done)

---

## 8. Summary of All Decisions (Quick Reference)

| Decision | Chosen | Rejected Alternative | Why |
|----------|--------|---------------------|-----|
| Graph construction | Deterministic AST (DKB) | LLM-extracted graph (LLM-KB) | 20x cheaper, 100% coverage, seconds vs minutes |
| Parser | Python `ast` module | Tree-sitter | Native typed nodes for Python, simpler |
| Semantic layer | LLM enrichment (second pass) | Pure AST only | AST can't infer purpose, patterns, concepts |
| Graph database | Neo4j | — | Native graph + vector index, Cypher query language |
| Update strategy | Strategy B (fine-grained diff) | Strategy A (delete/recreate), Strategy C (hybrid) | Best cost/accuracy tradeoff with content hash caching |
| Node identity | Deterministic qualified names | Auto-generated IDs | Enables MERGE, stable across re-indexing |
| Change detection | Content hash (SHA-256, 16 chars) | AST node comparison | Simple, deterministic, catches all changes |
| Call extraction | Worklist with scope boundaries | `ast.walk()` | Prevents double-counting across nested functions |
| Source extraction | Starts from first decorator line | Starts from `def`/`class` keyword | LLM enrichment needs to see decorators |
| Import handling | All variants (relative, conditional, try/except) | Top-level only | FastAPI uses all patterns, completeness matters |
| Architecture | 5 MCP servers + FastAPI gateway | Monolithic | Assignment requirement, clean separation of concerns |
| Query strategy | Bidirectional graph expansion + vector | Vector-only or graph-only | Combines structural navigation with fuzzy matching |