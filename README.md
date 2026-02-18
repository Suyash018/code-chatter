# FastAPI Repository Chat Agent - MCP Multi-Agent System

A production-ready multi-agent system that answers questions about the FastAPI codebase using the Model Context Protocol (MCP). The system indexes the repository into a Neo4j knowledge graph and uses specialized agents to provide accurate, context-aware responses.

## Features

- **Multi-Agent Architecture**: Four specialized MCP servers working in concert
- **Knowledge Graph**: Neo4j-based graph with AST-derived structure + LLM enrichment
- **Production Ready**: Docker Compose deployment, health checks, comprehensive logging
- **FastAPI Gateway**: REST API + WebSocket support for real-time chat
- **Conversation Context**: Multi-turn conversations with session management
- **Incremental Updates**: Strategy B fine-grained diffing for efficient re-indexing
- **Observability**: Optional Langfuse integration for request tracing and LLM monitoring
- **SSE Transport**: All MCP agents use Server-Sent Events over HTTP for scalability

## Quick Start

### Prerequisites

- Docker and Docker Compose
- OpenAI API key
- Neo4j Aura instance (or local Neo4j 5.15+)
- 2GB+ RAM available
- (Optional) Langfuse account for observability

### Setup

1. **Clone the repository**

```bash
git clone <repository-url>
cd graphical-rag
```

2. **Configure environment**

```bash
cp .env.example .env
```

Edit `.env` and add your credentials:

```env
OPENAI_API_KEY=sk-your-openai-api-key-here
NEO4J_URI=neo4j+s://your-instance.databases.neo4j.io
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your-secure-password

# Optional: Enable Langfuse observability
LANGFUSE_PUBLIC_KEY=pk-...
LANGFUSE_SECRET_KEY=sk-...
LANGFUSE_HOST=https://cloud.langfuse.com
```

3. **Start services**

```bash
docker-compose -f docker-compose.cloud.yml up -d
```

> **Note**: This project uses `docker-compose.cloud.yml` which connects to cloud-hosted Neo4j Aura. For local development with a local Neo4j container, you can create a separate `docker-compose.yml` file.

4. **Wait for services to be ready**

```bash
# Check health
curl http://localhost:8000/api/agents/health
```

5. **Index the FastAPI repository**

```bash
curl -X POST http://localhost:8000/api/index \
  -H "Content-Type: application/json" \
  -d '{
    "repository_url": "https://github.com/tiangolo/fastapi",
    "clear_graph": true,
    "run_enrichment": true,
    "create_embeddings": true
  }'
```

This returns a `job_id`. Track progress:

```bash
curl http://localhost:8000/api/index/status/<job_id>
```

6. **Ask questions**

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What is the FastAPI class and how does it work?"
  }'
```

## Architecture

### System Architecture Diagram

```
┌────────────────────────────────────────────────────────────┐
│                      User / Client                         │
└──────────────────────────┬─────────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────────┐
│            FastAPI Gateway (Port 8000)                     │
│  • REST API (/api/chat, /api/index, /api/agents/health)   │
│  • WebSocket (/ws/chat)                                    │
│  • Session management                                      │
│  • Langfuse middleware (optional)                          │
└──────────────────────────┬─────────────────────────────────┘
                           │
                           │ MultiServerMCPClient (SSE)
                           ▼
┌────────────────────────────────────────────────────────────┐
│         Orchestrator Agent (Port 8001) - MCP Server        │
│  Tools: analyze_query, route_to_agents,                   │
│         get_conversation_context, synthesize_response      │
└─────┬──────────────────┬─────────────────┬────────────────┘
      │                  │                 │
      │ SSE/HTTP         │ SSE/HTTP        │ SSE/HTTP
      ▼                  ▼                 ▼
┌──────────┐      ┌─────────────┐   ┌─────────────┐
│ Indexer  │      │ Graph Query │   │Code Analyst │
│  Agent   │      │   Agent     │   │   Agent     │
│(Port8002)│      │ (Port 8003) │   │ (Port 8004) │
│          │      │             │   │             │
│5 tools   │      │  7 tools    │   │  6 tools    │
│(indexing)│      │(traversal)  │   │ (analysis)  │
└────┬─────┘      └──────┬──────┘   └──────┬──────┘
     │                   │                  │
     │    Neo4j Driver (Bolt Protocol)     │
     └───────────────────┴──────────────────┘
                         │
                         ▼
           ┌─────────────────────────────┐
           │  Neo4j Graph Database       │
           │  (Cloud Aura or Local)      │
           │                             │
           │  • Nodes: Module, Class,    │
           │    Function, Method, etc.   │
           │  • Edges: CALLS, IMPORTS,   │
           │    INHERITS_FROM, etc.      │
           │  • Properties: enrichment,  │
           │    embeddings, source code  │
           └─────────────────────────────┘
```

**Communication Flow**:
1. User sends query to Gateway REST/WebSocket endpoint
2. Gateway forwards to Orchestrator via SSE transport
3. Orchestrator analyzes query and routes to specialist agents
4. Agents query Neo4j database and return results
5. Orchestrator synthesizes final response
6. Gateway returns response to user

### Agent Responsibilities & MCP Tools

#### 1. Orchestrator Agent (Port 8001)
**Purpose**: Central coordinator that routes queries and synthesizes responses

**MCP Tools (4)**:
- `analyze_query` - Classifies query intent and extracts code entities
- `route_to_agents` - Routes to specialist agents based on intent
- `get_conversation_context` - Retrieves session history for multi-turn context
- `synthesize_response` - Combines multiple agent outputs into coherent response

#### 2. Indexer Agent (Port 8002)
**Purpose**: Repository parsing and knowledge graph population

**MCP Tools (5)**:
- `index_repository` - Full repository indexing (clone → parse → enrich → embed)
- `index_file` - Incremental single-file indexing with Strategy B diffing
- `parse_python_ast` - Pure AST parsing without graph writes
- `extract_entities` - High-level entity summary from Python source
- `get_index_status` - Check job progress or get graph statistics

**Features**: Background async jobs with polling, enrichment caching, content-hash change detection

#### 3. Graph Query Agent (Port 8003)
**Purpose**: Knowledge graph traversal and relationship queries

**MCP Tools (7)**:
- `find_entity` - Locate entities by exact name, fuzzy match, or semantic similarity
- `get_dependencies` - Find outgoing relationships (what entity depends on)
- `get_dependents` - Find incoming relationships (what depends on entity)
- `trace_imports` - Follow module import chains with metadata
- `find_related` - Get entities connected by specific relationship type
- `execute_query` - Run custom read-only Cypher queries
- `get_subgraph` - Bidirectional graph expansion from seed entities

**Features**: Vector similarity search, transitive traversal, auto-relationship type selection

#### 4. Code Analyst Agent (Port 8004)
**Purpose**: Deep code understanding and pattern analysis

**MCP Tools (6)**:
- `analyze_function` - Deep function/method analysis with call chains
- `analyze_class` - Comprehensive class analysis with inheritance
- `find_patterns` - Detect design patterns (factory, dependency injection, etc.)
- `get_code_snippet` - Extract source code with surrounding context
- `explain_implementation` - Trace data flow and execution chains
- `compare_implementations` - Side-by-side comparison of two entities

**Features**: GraphContextRetriever for enriched analysis, data-flow tracking, pattern recognition

## API Reference

### Interactive Documentation

Visit http://localhost:8000/docs for full Swagger UI documentation.

### Key Endpoints

#### POST /api/chat

Send a message and receive a response.

**Request:**
```json
{
  "message": "How does FastAPI handle dependency injection?",
  "session_id": "optional-uuid",
  "stream": false
}
```

**Response:**
```json
{
  "session_id": "uuid",
  "response": "FastAPI uses the Depends class...",
  "intent": "code_explanation",
  "entities": ["Depends", "FastAPI"],
  "agents_called": ["graph_query", "code_analyst"],
  "errors": {}
}
```

#### WebSocket /ws/chat

Real-time streaming chat.

**Client → Server:**
```json
{
  "message": "What is APIRoute?",
  "session_id": "optional-uuid"
}
```

**Server → Client:**
```json
{
  "type": "response",
  "session_id": "uuid",
  "response": "APIRoute is a class that...",
  "intent": "code_explanation",
  "entities": ["APIRoute"],
  "agents_called": ["graph_query", "code_analyst"]
}
```

#### POST /api/index

Trigger repository indexing.

**Request:**
```json
{
  "repository_url": "https://github.com/tiangolo/fastapi",
  "clear_graph": true,
  "run_enrichment": true,
  "create_embeddings": true
}
```

**Response:**
```json
{
  "job_id": "abc-123",
  "status": "running",
  "message": "Indexing job started"
}
```

#### GET /api/index/status/{job_id}

Check indexing progress.

**Response:**
```json
{
  "job_id": "abc-123",
  "status": "running",
  "progress": {
    "current_phase": "enriching",
    "files_processed": 45,
    "total_files": 200,
    "percent_complete": 22.5
  }
}
```

#### GET /api/agents/health

Health check for all agents.

**Response:**
```json
{
  "overall_status": "healthy",
  "agents": [
    {
      "agent_name": "orchestrator",
      "status": "healthy",
      "tools_count": 4,
      "tools": ["analyze_query", "route_to_agents", ...]
    }
  ],
  "healthy_count": 4,
  "total_count": 4
}
```

## Sample Queries

### Simple (Single Agent)

- "What is the FastAPI class?"
- "Show me the docstring for the Depends function"
- "Find the APIRouter class"

### Medium (2-3 Agents)

- "How does FastAPI handle request validation?"
- "What classes inherit from APIRouter?"
- "Find all decorators used in the routing module"
- "What does the Depends class do?"

### Complex (Multi-Agent Synthesis)

- "Explain the complete lifecycle of a FastAPI request"
- "How does dependency injection work and show me examples"
- "Compare how Path and Query parameters are implemented"
- "What design patterns are used in FastAPI's core and why?"

## Development

### Project Structure

```
.
├── src/
│   ├── agents/                  # MCP Agent implementations
│   │   ├── orchestrator/        # Central coordinator
│   │   ├── indexer/             # Repository indexing
│   │   ├── graph_query/         # Graph traversal
│   │   ├── code_analyst/        # Code analysis
│   │   └── response_formatter/  # Response formatting
│   ├── gateway/                 # FastAPI application
│   │   ├── app.py               # Main FastAPI app
│   │   ├── config.py            # Gateway settings
│   │   └── routes/              # API routes
│   └── shared/                  # Shared utilities
│       ├── config.py            # Base settings
│       ├── logging.py           # Logging setup
│       ├── database/            # Neo4j handler
│       └── llms/                # LLM models
├── tests/                       # Test suite
├── docker-compose.yml           # Service orchestration
├── Dockerfile.*                 # Dockerfiles for each service
├── pyproject.toml               # Dependencies
├── .env.example                 # Environment template
└── README.md                    # This file
```

### Local Development

Install dependencies:

```bash
pip install -e .
```

Set up environment:

```bash
cp .env.example .env
# Edit .env with your credentials
```

Run Neo4j locally:

```bash
docker run -d \
  --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password \
  neo4j:5.15.0
```

Run the gateway:

```bash
python -m src.gateway.app
```

Run tests:

```bash
pytest
```

### Running Individual Agents

Each MCP server runs as an SSE server over HTTP:

```bash
# Orchestrator (port 8001)
ORCHESTRATOR_HOST=0.0.0.0 ORCHESTRATOR_PORT=8001 python -m src.agents.orchestrator.server

# Indexer (port 8002)
INDEXER_HOST=0.0.0.0 INDEXER_PORT=8002 python -m src.agents.indexer.server

# Graph Query (port 8003)
GRAPH_QUERY_HOST=0.0.0.0 GRAPH_QUERY_PORT=8003 python -m src.agents.graph_query.server

# Code Analyst (port 8004)
CODE_ANALYST_HOST=0.0.0.0 CODE_ANALYST_PORT=8004 python -m src.agents.code_analyst.server
```

Each agent exposes `/sse` endpoint for MCP communication and `/health` for health checks.

## Observability & Monitoring

The system includes optional **Langfuse integration** for comprehensive observability:

### Features
- **Automatic Request Tracing**: All HTTP requests traced via middleware
- **LLM Call Monitoring**: Token usage, latencies, model parameters
- **Distributed Tracing**: W3C Trace Context propagation across MCP agents
- **Session Tracking**: Multi-turn conversations linked via session_id
- **Performance Metrics**: Request duration, agent routing decisions

### Setup

Add credentials to `.env`:
```env
LANGFUSE_PUBLIC_KEY=pk-...
LANGFUSE_SECRET_KEY=sk-...
LANGFUSE_HOST=https://cloud.langfuse.com
```

If these variables are not set, observability is automatically disabled and the system runs without tracing.

### Accessing Traces

Visit [cloud.langfuse.com](https://cloud.langfuse.com) to view:
- Request traces with nested agent calls
- LLM generation details (prompts, completions, costs)
- Session timelines showing conversation flow
- Performance dashboards and analytics

## Design Decisions

### Architecture Patterns

**Two-Layer Graph Structure**:
- **Layer 1 (AST)**: Structural nodes from Python AST (classes, functions, calls, imports)
- **Layer 2 (Enrichment)**: LLM-generated semantic annotations (patterns, concepts, data flows)

**Incremental Updates**:
- Strategy B fine-grained diffing with content-hash caching
- Only changed entities are re-parsed and re-enriched
- Enrichment cache prevents redundant LLM calls

**Agent Communication**:
- SSE (Server-Sent Events) transport over HTTP for scalability
- All agents run as independent services
- Orchestrator coordinates via FastMCP client with MultiServerMCPClient

**Observability**:
- OpenTelemetry trace context injection into MCP calls via `MCPTraceContextInterceptor`
- Langfuse decorators (`@observe`, `trace_function`) for automatic span creation
- W3C Trace Context format for distributed tracing

## Configuration

All settings use Pydantic Settings with environment variable overrides:

### Global Defaults

```env
DEFAULT_MODEL=gpt-5.2-2025-12-11
DEFAULT_MINI_MODEL=gpt-5-mini-2025-08-07
DEFAULT_EMBEDDING_MODEL=text-embedding-3-large
```

### Agent-Specific Overrides

```env
INDEXER_ENRICHMENT_MODEL=gpt-5-mini-2025-08-07
CODE_ANALYST_ANALYSIS_MODEL=gpt-5.2-2025-12-11
ORCHESTRATOR_SYNTHESIS_MODEL=gpt-5.2-2025-12-11
GRAPH_QUERY_MAX_TRAVERSAL_DEPTH=3
```

## Testing

Run the test suite:

```bash
pytest
```

Run with coverage:

```bash
pytest --cov=src --cov-report=html
```

Run specific test categories:

```bash
# Agent tests
pytest tests/agents/

# Integration tests
pytest tests/integration/

# Smoke tests
pytest tests/smoke/
```

## Deployment

### Production Considerations

**Neo4j**:
- Use Neo4j Aura for managed hosting
- Configure connection pooling in `NEO4J_URI`
- Set appropriate `NEO4J_PASSWORD` strength

**Agent Scaling**:
- Each agent can be scaled independently via Docker replicas
- Use load balancer (e.g., nginx) in front of multiple agent instances
- Gateway automatically distributes MCP calls across agent URLs

**Security**:
- Never commit `.env` file with real credentials
- Use Docker secrets for production credential management
- Enable HTTPS/TLS for all external-facing endpoints
- Restrict Neo4j network access to agent services only

**Monitoring**:
- Enable Langfuse for production tracing
- Configure health check intervals in `docker-compose.cloud.yml`
- Set up alerts on agent health endpoint failures
- Monitor Neo4j memory and query performance

**Backup**:
- Regular Neo4j database snapshots (Neo4j Aura automatic)
- Version control all configuration files
- Document indexing job configurations

## Performance

- **Indexing**: ~30 minutes for FastAPI repo (depending on enrichment)
- **Query latency**: 20-50 seconds (depends on query complexity)
- **Graph size**: FastAPI = ~5000 nodes, ~10000 edges
- **Memory**: gateway needs 1-2GB

## Known Limitations

### AST Parser Limitations
1. **Module-level variables**: TypeVars and constants not extracted
2. **Self.method() resolution**: Requires type inference (not implemented)
3. **Star imports**: Captured but not fully resolved (`from module import *`)
4. **Dynamic imports**: `importlib` and `__import__()` not tracked
5. **Metaclasses**: Not parsed or represented in graph

### Indexing Limitations
1. **Incremental indexing**: `index_file` requires manual file path specification
2. **Language support**: Python only (no multi-language support)
3. **Large files**: Files >10,000 LOC may timeout during enrichment
4. **Binary files**: Non-text files skipped during repository scanning

### Query Limitations
1. **Vector search accuracy**: Depends on embedding model quality
2. **Transitive queries**: Deep traversals (depth >3) can be slow on large graphs
3. **Concurrent indexing**: Multiple simultaneous `index_repository` jobs may conflict



## Acknowledgments

Built using:
- [FastAPI](https://fastapi.tiangolo.com/) - Web framework
- [Neo4j](https://neo4j.com/) - Graph database
- [LangChain](https://langchain.com/) - LLM orchestration
- [MCP](https://modelcontextprotocol.io/) - Model Context Protocol
- [OpenAI](https://openai.com/) - LLM models

Research foundation:
- LLMxCPG (USENIX Security '25) - Graph-guided slicing
- Reliable Graph-RAG (arXiv 2601.08773) - DKB vs LLM-KB comparison
- Autonomous Issue Resolver (arXiv 2512.08492) - Data-flow awareness
