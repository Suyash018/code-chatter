# Deployment Guide

This guide explains how to deploy the FastAPI Repository Chat Agent multi-agent system using Docker.

## Architecture Overview

The system consists of:

1. **Neo4j Database** - Knowledge graph storage
2. **FastAPI Gateway** - HTTP/WebSocket API layer
3. **MCP Agent Servers** (embedded in gateway):
   - Orchestrator Agent - Central coordinator
   - Indexer Agent - Repository indexing
   - Graph Query Agent - Knowledge graph querying
   - Code Analyst Agent - Code analysis

## Prerequisites

- Docker Engine 20.10+
- Docker Compose 2.0+
- At least 4GB RAM available for containers
- OpenAI API key

## Quick Start

### 1. Configure Environment

Copy the example environment file:

```bash
cp .env.example .env
```

Edit `.env` and configure:

```env
# OpenAI API Key (required)
OPENAI_API_KEY=sk-your-openai-api-key-here

# Neo4j credentials (update as needed)
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your-secure-password

# Gateway configuration
GATEWAY_HOST=0.0.0.0
GATEWAY_PORT=8000
```

### 2. Start Services

```bash
docker-compose up -d
```

This will:
- Pull the Neo4j image
- Build the gateway image (includes all MCP agents)
- Start Neo4j with health checks
- Start the gateway once Neo4j is healthy

### 3. Verify Health

Check that all services are running:

```bash
docker-compose ps
```

Check agent health:

```bash
curl http://localhost:8000/api/agents/health
```

### 4. Index a Repository

Trigger indexing of the FastAPI repository:

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

Response:
```json
{
  "job_id": "abc-123-def-456",
  "status": "running",
  "message": "Indexing job started"
}
```

### 5. Check Indexing Progress

```bash
curl http://localhost:8000/api/index/status/abc-123-def-456
```

### 6. Ask Questions

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What is the FastAPI class?"
  }'
```

## Service Management

### View Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f gateway
docker-compose logs -f neo4j
```

### Restart Services

```bash
# Restart all
docker-compose restart

# Restart specific service
docker-compose restart gateway
```

### Stop Services

```bash
docker-compose stop
```

### Remove Everything

```bash
# Stop and remove containers, networks
docker-compose down

# Also remove volumes (deletes Neo4j data)
docker-compose down -v
```

## API Endpoints

### Chat

- **POST /api/chat** - Send message, receive response
- **WebSocket /ws/chat** - Real-time streaming chat

### Indexing

- **POST /api/index** - Trigger repository indexing
- **GET /api/index/status/{job_id}** - Get indexing job status
- **GET /api/index/status** - Get overview of all jobs

### Health & Monitoring

- **GET /api/health** - Simple health check
- **GET /api/agents/health** - Detailed agent health status
- **GET /api/graph/statistics** - Knowledge graph statistics

### Interactive API Docs

Visit http://localhost:8000/docs for Swagger UI documentation.

## Configuration Options

### Gateway Settings

Environment variables with `GATEWAY_` prefix:

- `GATEWAY_HOST` - Bind address (default: 0.0.0.0)
- `GATEWAY_PORT` - Port number (default: 8000)

### Orchestrator Settings

Environment variables with `ORCHESTRATOR_` prefix:

- `ORCHESTRATOR_SYNTHESIS_MODEL` - Model for response synthesis
- `ORCHESTRATOR_MAX_AGENT_RETRIES` - Retry count for failed agents
- `ORCHESTRATOR_AGENT_TIMEOUT_SECONDS` - Agent timeout in seconds

### Indexer Settings

Environment variables with `INDEXER_` prefix:

- `INDEXER_ENRICHMENT_MODEL` - Model for LLM enrichment
- `INDEXER_EMBEDDING_MODEL` - Model for vector embeddings
- `INDEXER_ENRICHMENT_BATCH_SIZE` - Batch size for enrichment

### Graph Query Settings

Environment variables with `GRAPH_QUERY_` prefix:

- `GRAPH_QUERY_MAX_TRAVERSAL_DEPTH` - Max hops for graph traversal

### Code Analyst Settings

Environment variables with `CODE_ANALYST_` prefix:

- `CODE_ANALYST_ANALYSIS_MODEL` - Model for code analysis

## Production Considerations

### Security

1. **Change Neo4j password**: Use a strong password in production
2. **Secrets management**: Use Docker secrets or external secret managers
3. **API authentication**: Add authentication middleware to the gateway
4. **Network security**: Configure firewall rules, use private networks
5. **HTTPS**: Put gateway behind a reverse proxy (nginx, traefik) with TLS

### Performance

1. **Neo4j memory**: Adjust heap and pagecache based on graph size
2. **Connection pooling**: Configure Neo4j driver pool size
3. **Gateway workers**: Run multiple gateway instances behind a load balancer
4. **Caching**: Add Redis for conversation context and response caching

### Monitoring

1. **Logs**: Use centralized logging (ELK, Loki, CloudWatch)
2. **Metrics**: Export Prometheus metrics from gateway
3. **Tracing**: Add OpenTelemetry for distributed tracing
4. **Alerts**: Set up alerts for health check failures

### Backup

1. **Neo4j backups**: Schedule regular graph database backups
2. **Volume snapshots**: Create snapshots of Docker volumes
3. **Config backups**: Version control .env files (without secrets)

## Troubleshooting

### Gateway won't start

Check logs:
```bash
docker-compose logs gateway
```

Common issues:
- Neo4j not ready: Wait for health check to pass
- Missing environment variables: Check .env file
- Port already in use: Change GATEWAY_PORT

### Agent health checks failing

Check if MCP servers can be spawned:
```bash
docker-compose exec gateway python -m src.agents.orchestrator.server --help
```

Verify Python dependencies:
```bash
docker-compose exec gateway pip list
```

### Neo4j connection errors

Verify Neo4j is accessible:
```bash
docker-compose exec neo4j cypher-shell -u neo4j -p your-password "RETURN 1"
```

Check connection from gateway:
```bash
docker-compose exec gateway python -c "from neo4j import GraphDatabase; driver = GraphDatabase.driver('bolt://neo4j:7687', auth=('neo4j', 'your-password')); driver.verify_connectivity()"
```

### Indexing fails

Check indexer logs in gateway:
```bash
docker-compose logs gateway | grep indexer
```

Common issues:
- Git clone failure: Check repository URL and network access
- Out of memory: Increase Docker memory limit
- OpenAI API errors: Verify API key and quota

## Development vs Production

### Development

```bash
# Use docker-compose with live code mounting
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up
```

Features:
- Hot reload on code changes
- Debug logging enabled
- Source code mounted as volume

### Production

```bash
# Use optimized production configuration
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Features:
- Multi-stage builds for smaller images
- Health checks and restart policies
- Resource limits
- Read-only filesystems where possible

## Scaling

### Horizontal Scaling

Run multiple gateway instances:

```yaml
gateway:
  deploy:
    replicas: 3
```

Add a load balancer (nginx example):

```nginx
upstream gateway {
    server gateway:8000;
}

server {
    listen 80;
    location / {
        proxy_pass http://gateway;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### Vertical Scaling

Adjust Neo4j memory:

```yaml
neo4j:
  environment:
    - NEO4J_server_memory_heap_max__size=4G
    - NEO4J_server_memory_pagecache_size=2G
```

Set container resource limits:

```yaml
gateway:
  deploy:
    resources:
      limits:
        cpus: '2'
        memory: 4G
      reservations:
        cpus: '1'
        memory: 2G
```

## Support

For issues and questions:
- Check logs: `docker-compose logs -f`
- Review configuration: Verify .env and docker-compose.yml
- Test endpoints: Use curl or Swagger UI at /docs
- Check system resources: `docker stats`
