# Knowledge Graph Indexing & Construction

> This document details the indexing pipeline architecture, AST parser implementation, and research foundation for the FastAPI Repository Chat Agent's knowledge graph construction.

---

## Research Foundation

Three academic papers inform the indexing and graph construction approach:

### Paper 1: LLMxCPG (USENIX Security '25)
**Citation**: Lekssays et al., "LLMxCPG: Leveraging Large Language Models and Code Property Graphs for Vulnerability Detection"

**Link**: USENIX Security Symposium 2025

**Key Takeaway**: **Graph-guided slicing** — use the graph to extract a small, relevant subgraph before sending it to the LLM, reducing context size by 67-90% while preserving what matters.

### Paper 2: Reliable Graph-RAG for Codebases
**Citation**: Chinthareddy, "Reliable Graph-RAG: Making Code RAG Systems More Accurate and Efficient"

**Link**: [arXiv:2601.08773](https://arxiv.org/abs/2601.08773)

**Key Findings**:
- **DKB (Deterministic Knowledge Base)** vs LLM-extracted KB comparison
- DKB builds in seconds vs minutes
- 100% corpus coverage (LLM-KB missed 377 files on Shopizer benchmark)
- ~20x lower cost than LLM-based graph extraction
- Comparable accuracy on code understanding queries

**Decision Impact**: Use deterministic AST-based graph extraction as foundation, not LLM extraction.

### Paper 3: Autonomous Issue Resolver with Data Transformation Graphs
**Citation**: "DTG: Autonomous Issue Resolution with Data Transformation Graphs"

**Link**: [arXiv:2512.08492](https://arxiv.org/abs/2512.08492)

**Key Takeaway**: **Data-flow awareness** — tracking how data transforms through the system helps answer lifecycle questions like "how does a request flow through FastAPI."

**Decision Impact**: Add DATA_FLOWS_TO edges during LLM enrichment to capture data transformation chains.

---

## Core Indexing Architecture

### Two-Layer Graph Structure

**Layer 1: Deterministic AST Extraction** (Fast, Cheap, Complete)
- **Parser**: Python `ast` module
- **Extracted Entities**: Classes, functions, methods, parameters, decorators, imports, class attributes
- **Extracted Relationships**: CALLS, CONTAINS, INHERITS_FROM, IMPORTS, DECORATED_BY, HAS_PARAMETER
- **Performance**: Seconds to parse entire FastAPI codebase
- **Coverage**: 100% of Python files, no skipped entities
- **Cost**: Zero LLM API calls

**Layer 2: LLM Enrichment** (Semantic, Expensive, Optional)
- **Enriched Properties**: Purpose summaries, complexity ratings, side effects, domain concepts
- **Semantic Relationships**: IMPLEMENTS_PATTERN, COLLABORATES_WITH, DATA_FLOWS_TO, RELATES_TO_CONCEPT
- **Pattern Detection**: Factory, dependency injection, decorator, middleware, singleton
- **Cost Optimization**: Content-hash-based caching to avoid re-enriching unchanged code

**Rationale**: Pure AST cannot infer "this function validates input" or "this is dependency injection." LLM enrichment adds semantic understanding while AST ensures completeness and structural accuracy.

---

## Graph Schema

### Node Types

| Node Type | Identity Key | Properties |
|-----------|-------------|------------|
| **File** | `path` | path, content_hash, last_modified, commit_hash |
| **Module** | `name` | name, docstring, file_path |
| **Class** | `qualified_name` | name, qualified_name, docstring, bases, content_hash, source_code, enrichment (purpose, complexity, patterns, concepts) |
| **Function** | `qualified_name` | name, qualified_name, is_async, docstring, content_hash, source_code, enrichment (purpose, complexity, side_effects, concepts) |
| **Method** | `qualified_name` | Same as Function + class_name, is_static, is_classmethod, is_property |
| **Parameter** | `function_qualified_name.param_name` | name, type_hint, default_value, kind (positional-only, positional-or-keyword, keyword-only, var-positional, var-keyword) |
| **Decorator** | `name` | name, module |
| **DesignPattern** | `name` | name, description |
| **DomainConcept** | `name` | name, description |
| **EnrichmentCache** | `content_hash` | content_hash, enrichment_json, created_at |

### Relationship Types

| Relationship | Source → Target | Description |
|-------------|----------------|-------------|
| **CONTAINS** | File → Module, Module → Class, Class → Method | Structural containment |
| **DEFINES_MODULE** | File → Module | File defines module |
| **IMPORTS** | Module → Module | Import dependencies (properties: symbol_names, is_relative, is_type_checking, is_conditional, is_fallback) |
| **INHERITS_FROM** | Class → Class | Class inheritance |
| **CALLS** | Function/Method → Function/Method | Function call relationships |
| **DECORATED_BY** | Function/Class → Decorator | Decorator application |
| **HAS_PARAMETER** | Function/Method → Parameter | Function parameters |
| **IMPLEMENTS_PATTERN** | Class/Function → DesignPattern | Design pattern implementation (LLM-enriched) |
| **RELATES_TO_CONCEPT** | Class/Function → DomainConcept | Domain concept association (LLM-enriched) |
| **COLLABORATES_WITH** | Class → Class | Class collaboration (LLM-enriched) |
| **DATA_FLOWS_TO** | Function → Function | Data transformation flow (LLM-enriched) |

---

## Incremental Updates: Strategy B (Fine-Grained Diffing)

### Three Strategies Evaluated

**Strategy A: Delete & Recreate**
- Delete entire file subgraph
- Re-parse file completely
- Recreate all nodes and relationships
- ✓ Simple, always correct
- ✗ Wastes LLM enrichment cost on unchanged functions

**Strategy B: Fine-Grained Diff** ← **Chosen**
- Compare new AST against existing graph by content hash
- Only update changed entities
- Preserve enrichment on unchanged code
- ✓ Cost-efficient (reuses cached enrichment)
- ✗ More complex implementation

**Strategy C: Hybrid (A+B)**
- Use B for simple changes, fall back to A for complex refactors
- ✗ Rejected: Inherits complexity of both, adds arbitrary thresholds, difficult to debug

### Strategy B Implementation Details

**Content Hash Change Detection**:
1. Parse new file AST
2. Extract source code for each entity (includes decorators)
3. Compute SHA-256 hash, truncate to 16 hex chars
4. Query Neo4j for existing entity by qualified_name
5. Compare content_hash:
   - **Match**: Entity unchanged, skip LLM enrichment, reuse cached enrichment
   - **Mismatch**: Entity changed, re-enrich with LLM, update cache

**Enrichment Caching**:
- `EnrichmentCache` nodes keyed by `content_hash`
- Before LLM call, check: `MATCH (c:EnrichmentCache {content_hash: $hash})`
- If found, restore enrichment properties without API call
- If not found, call LLM, store result in cache
- Cache is content-addressable and shared across all files

**Benefits**:
- Unchanged functions across file moves/renames reuse enrichment
- Refactoring that doesn't change function body preserves semantic annotations
- Cost savings scale with codebase size (FastAPI: ~200 files)

---

## AST Parser: Validation & Bug Fixes

The AST parser was validated against the real FastAPI codebase (47 Python files). Two rounds of auditing found and fixed **11 bugs**.

### Round 1 Bugs (4 total)

| Bug | Impact | Fix |
|-----|--------|-----|
| Class attributes not extracted | 272 attributes invisible to graph | Walk class body for `Assign`/`AnnAssign` nodes |
| Nested functions missed | 16 functions invisible | Recurse into function bodies for nested `FunctionDef` |
| Relative imports unresolved | 68 imports had wrong module path | Resolve dots using parent package from file path |
| TYPE_CHECKING imports not separated | False runtime dependency edges | Check if import is inside `if TYPE_CHECKING:` block |

### Round 2 Bugs (7 total)

| Bug | Impact | Fix |
|-----|--------|-----|
| Call double-counting | 69 false call relationships | Replace `ast.walk()` with worklist that skips nested scope boundaries |
| Class content hash ignores decorators | Strategy B blind to decorator changes | Include decorators in source extraction |
| Source extraction missing decorators | LLM enrichment never saw `@dataclass`, `@app.get()` | Use `decorator_list[0].lineno` as start line |
| `import X, Y` only captures first | Loop exits on first iteration | Changed return type to `list[dict]`, append all aliases |
| Conditional imports missed | 2 imports in `if sys.version_info` invisible | Added else branch to `ast.If` handler |
| Try/except imports missed | 5 optional dependencies invisible (`ujson`, `orjson`) | Added `ast.Try` handler in parse loop |
| Positional-only params missed | `args.posonlyargs` not read | Combined `posonlyargs + args`, added `kind` field |

### Final Validated Metrics (FastAPI Codebase)

| Metric | Count |
|--------|-------|
| Files parsed | 47 (0 errors) |
| Classes | 96 |
| Class attributes | 272 |
| Functions (top-level) | 108 |
| Methods | 140 |
| Nested functions | 16 |
| Imports | 395 |
| - Relative imports | 68 |
| - TYPE_CHECKING imports | 1 |
| - Conditional imports | 2 |
| - Try/except imports | 5 |
| Call relationships | 938 |

---

## Known Design Limitations (Not Bugs)

These are intentional limitations of the AST parser:

1. **Module-level variables (42)**: TypeVars, logger instances, constants. Not graph entities.
2. **Module-level calls (4)**: `Schema.model_rebuild()`, `main()` — no caller to attribute them to.
3. **`self.method()` resolution (20 calls)**: Need type inference to resolve which class's method is being called. Captured as unresolved call names.
4. **Metaclass keyword args**: `class Foo(metaclass=ABCMeta)` — keywords not in bases list.
5. **Star imports**: `from X import *` captured but can't resolve individual names without runtime.
6. **Nested classes**: Classes inside functions — 0 in FastAPI codebase.

---

## Indexer Implementation Modules

| Module | Responsibility |
|--------|---------------|
| `ast_parser.py` | Deterministic AST extraction (handles all 11 bug fixes) |
| `graph_manager.py` | Neo4j interactions — schema, CRUD, cross-file resolution, caching |
| `enrichment.py` | LLM enrichment logic — batched calls, JSON parsing, semantic edges |
| `repository.py` | Git operations — clone, discover Python files, read files |
| `incremental_updater.py` | Strategy B diff logic — content hash comparison, cache reuse |
| `server.py` | MCP server with 5 tools: `index_repository`, `index_file`, `parse_python_ast`, `extract_entities`, `get_index_status` |
| `config.py` | Pydantic Settings for environment-based configuration |

---

## Key Technical Details

### Content Hashing
- **Algorithm**: SHA-256, truncated to 16 hex chars
- **Input**: Normalized (stripped) source code
- **For functions**: Source includes decorators (starts from first decorator line)
- **Purpose**: Primary change detection mechanism in Strategy B

### Qualified Names
- **Format**: `module_name.class_name.function_name`
- **Example**: `fastapi.routing.APIRouter.get`
- **Special case**: `__init__.py` files strip `__init__` (e.g., `fastapi/__init__.py` → module `fastapi`)
- **Identity**: Used as primary key for classes, functions, methods
- **Collision-free**: No collisions across entire FastAPI codebase

### Import Resolution
- **Relative imports**: Resolved using file's package path
- **Example**: File `fastapi/routing.py` with `from .dependencies import Depends`
  - Level 1 (one dot) → parent package `fastapi`
  - Resolved to `fastapi.dependencies`
- **Absolute imports**: Used as-is

### Call Extraction
- **Algorithm**: Worklist-based traversal (not `ast.walk()`)
- **Scope boundaries**: Stops at nested `FunctionDef`, `ClassDef`, `Lambda`
- **Prevents**: Parent function claiming calls that belong to nested functions
- **Result**: Accurate call graph with correct attribution

---

## Indexing Pipeline Flow

```
1. Repository Cloning
   ├─> Git clone to local directory
   └─> Store commit hash for versioning

2. File Discovery
   ├─> Recursive walk for *.py files
   └─> Skip __pycache__, .venv, tests (configurable)

3. AST Parsing (per file)
   ├─> Parse with ast.parse()
   ├─> Extract: classes, functions, methods, parameters, decorators, imports, calls
   ├─> Compute content hashes (SHA-256)
   └─> Resolve qualified names

4. Graph Population
   ├─> Create/update File, Module nodes
   ├─> MERGE entities by qualified_name
   ├─> Check content_hash for changes
   └─> Create structural relationships (CONTAINS, CALLS, INHERITS_FROM, etc.)

5. Cross-File Resolution
   ├─> Resolve import targets to actual modules
   ├─> Link CALLS edges to target function nodes
   └─> Build complete import dependency graph

6. LLM Enrichment (optional)
   ├─> For each entity (or changed entity if Strategy B):
   │   ├─> Check EnrichmentCache by content_hash
   │   ├─> If cache hit: restore enrichment
   │   └─> If cache miss: call LLM, store result
   ├─> Add semantic properties (purpose, complexity, concepts)
   ├─> Create semantic edges (IMPLEMENTS_PATTERN, DATA_FLOWS_TO)
   └─> Link to DesignPattern, DomainConcept nodes

7. Vector Embeddings (optional)
   ├─> Generate embeddings for: docstrings, purpose summaries, source code
   ├─> Store in Neo4j vector index
   └─> Enable semantic similarity search

8. Validation & Statistics
   ├─> Count nodes by type
   ├─> Count edges by type
   ├─> Report enrichment coverage
   └─> Log any parsing warnings
```

---

## Performance Characteristics

**FastAPI Repository (~200 files)**:
- **AST Parsing**: ~2-5 seconds (Layer 1 only)
- **Full Indexing with Enrichment**: ~5-30 minutes (depending on LLM API latency)
- **Incremental Update (10% files changed)**: ~1-3 minutes (Strategy B with cache reuse)
- **Graph Size**: ~500 nodes, ~1000 edges
- **Enrichment API Calls**: ~200-300 (one per class/function)
- **Cost**: $0.50-$2.00 per full index (GPT-4 pricing, varies by model)

**Strategy B Cost Savings Example**:
- File change: 1 function modified out of 20
- Without cache: 20 LLM calls
- With cache: 1 LLM call (19 cached)
- **Savings**: 95% reduction in enrichment cost

---

## Configuration

### Environment Variables

```env
# Neo4j Connection
NEO4J_URI=neo4j+s://your-instance.databases.neo4j.io
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your-password

# OpenAI API
OPENAI_API_KEY=sk-your-key-here
ENRICHMENT_MODEL=gpt-4o-mini  # Model for enrichment

# Indexer Settings
INDEXER_HOST=0.0.0.0
INDEXER_PORT=8002
INDEXER_MAX_WORKERS=4  # Parallel file processing

# Enrichment Options
SKIP_ENRICHMENT=false  # Set true for AST-only indexing
CREATE_EMBEDDINGS=true  # Generate vector embeddings
CLEAR_GRAPH=false  # Clear existing graph before indexing
```

---

## Decision Summary

| Aspect | Decision | Rationale |
|--------|----------|-----------|
| **Graph Construction** | Deterministic AST (DKB) | 20x cheaper, 100% coverage, seconds vs minutes |
| **Parser** | Python `ast` module | Native typed nodes for Python, simpler than Tree-sitter |
| **Semantic Layer** | LLM enrichment (second pass) | AST can't infer purpose, patterns, domain concepts |
| **Update Strategy** | Strategy B (fine-grained diff) | Best cost/accuracy tradeoff with content-hash caching |
| **Change Detection** | Content hash (SHA-256, 16 chars) | Simple, deterministic, catches all changes |
| **Node Identity** | Deterministic qualified names | Enables MERGE, stable across re-indexing |
| **Call Extraction** | Worklist with scope boundaries | Prevents double-counting across nested functions |
| **Source Extraction** | Starts from first decorator line | LLM enrichment needs to see decorators |
| **Enrichment Caching** | Content-addressable cache | Reuse enrichment for unchanged code, reduce cost |

---

## References

1. Lekssays et al., "LLMxCPG: Leveraging Large Language Models and Code Property Graphs for Vulnerability Detection," USENIX Security Symposium, 2025.

2. Chinthareddy, "Reliable Graph-RAG: Making Code RAG Systems More Accurate and Efficient," arXiv:2601.08773, 2026. [Link](https://arxiv.org/abs/2601.08773)

3. "DTG: Autonomous Issue Resolution with Data Transformation Graphs," arXiv:2512.08492, 2025. [Link](https://arxiv.org/abs/2512.08492)

---

**Last Updated**: 2026-02-19
