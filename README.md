# FastAPI Repository Chat Agent - MCP Multi-Agent System

A production-ready multi-agent system that answers questions about the FastAPI codebase using the Model Context Protocol (MCP). The system indexes the repository into a Neo4j knowledge graph and uses specialized agents to provide accurate, context-aware responses.

## Features

- **Multi-Agent Architecture**: Five specialized MCP servers working in concert
- **Knowledge Graph**: Neo4j-based graph with AST-derived structure + LLM enrichment
- **Production Ready**: Docker Compose deployment, health checks, comprehensive logging
- **FastAPI Gateway**: REST API + WebSocket support for real-time chat
- **Conversation Context**: Multi-turn conversations with session management
- **Incremental Updates**: Strategy B fine-grained diffing for efficient re-indexing

## Quick Start

### Prerequisites

- Docker and Docker Compose
- OpenAI API key
- 4GB+ RAM available

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

Edit `.env` and add your OpenAI API key:

```env
OPENAI_API_KEY=sk-your-openai-api-key-here
NEO4J_PASSWORD=your-secure-password
```

3. **Start services**

```bash
docker-compose up -d
```

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

### System Components

```
┌─────────────────────────────────────────────────────────┐
│                    User / Client                        │
└─────────────────────────┬───────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│              FastAPI Gateway (Port 8000)                │
│  • REST API endpoints                                   │
│  • WebSocket support                                    │
│  • Session management                                   │
└─────────────────────────┬───────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│           Orchestrator Agent (MCP Server)               │
│  • Query analysis                                       │
│  • Agent routing                                        │
│  • Response synthesis                                   │
│  • Conversation context                                 │
└────┬─────────────────┬──────────────────┬───────────────┘
     │                 │                  │
     ▼                 ▼                  ▼
┌──────────┐    ┌─────────────┐    ┌─────────────┐
│ Indexer  │    │ Graph Query │    │Code Analyst │
│  Agent   │    │   Agent     │    │   Agent     │
│ (MCP)    │    │   (MCP)     │    │   (MCP)     │
└────┬─────┘    └──────┬──────┘    └──────┬──────┘
     │                 │                   │
     └─────────────────┴───────────────────┘
                       │
                       ▼
          ┌────────────────────────┐
          │   Neo4j Graph Database │
          │  • AST-derived nodes   │
          │  • LLM enrichment      │
          │  • Vector embeddings   │
          └────────────────────────┘
```

### Agent Responsibilities

#### 1. Orchestrator Agent
- Analyzes query intent
- Routes to appropriate specialist agents
- Manages conversation context
- Synthesizes final responses

#### 2. Indexer Agent
- Clones and parses repositories
- Extracts AST entities (classes, functions, imports)
- Populates Neo4j knowledge graph
- Runs LLM enrichment for semantics
- Creates vector embeddings
- Handles incremental updates

#### 3. Graph Query Agent
- Executes Cypher queries
- Traces dependencies and import chains
- Performs vector similarity search
- Extracts relevant subgraphs for analysis

#### 4. Code Analyst Agent
- Analyzes function implementations
- Detects design patterns
- Explains complex code logic
- Compares implementations

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

Each MCP server can be run standalone (stdio transport):

```bash
# Orchestrator
python -m src.agents.orchestrator.server

# Indexer
python -m src.agents.indexer.server

# Graph Query
python -m src.agents.graph_query.server

# Code Analyst
python -m src.agents.code_analyst.server
```

## Design Decisions

See [info.md](info.md) for comprehensive architectural documentation, including:

- Research foundation (3 academic papers)
- Two-layer graph architecture (AST + LLM enrichment)
- Strategy B incremental updates with content-hash caching
- AST parser validation (11 bugs found and fixed)
- Known limitations and trade-offs

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

See [DEPLOYMENT.md](DEPLOYMENT.md) for detailed deployment instructions, including:

- Production configuration
- Security considerations
- Monitoring and logging
- Scaling strategies
- Backup procedures
- Troubleshooting

## Performance

- **Indexing**: ~5-30 minutes for FastAPI repo (depending on enrichment)
- **Query latency**: 2-5 seconds (depends on query complexity)
- **Graph size**: FastAPI = ~500 nodes, ~1000 edges
- **Memory**: Neo4j needs 2-4GB, gateway needs 1-2GB

## Known Limitations

1. **Module-level variables**: Not extracted (TypeVars, constants)
2. **Self.method() resolution**: Requires type inference
3. **Star imports**: Captured but not fully resolved
4. **Incremental indexing**: Currently requires manual file paths

See [info.md](info.md) section 4 for complete list of AST parser limitations.

## License

[Add your license here]

## Contributors

[Add contributors here]

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
