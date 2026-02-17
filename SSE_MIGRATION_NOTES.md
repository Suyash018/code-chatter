# MCP Server Migration: stdio → SSE Transport

This document explains the migration from stdio transport to SSE (Server-Sent Events) transport for all MCP agent servers.

## Architecture Change

### Before (stdio transport)
- MCP agents spawned as subprocesses on-demand
- Communication via stdin/stdout
- All agent code packaged in gateway container
- Agents terminate after each request

### After (SSE transport)
- Each MCP agent runs as a long-running HTTP service
- Communication via HTTP/SSE protocol
- Each agent runs in its own Docker container
- Agents persist between requests

## Benefits of SSE Transport

1. **Service Independence**: Each agent can be scaled, updated, or restarted independently
2. **Better Resource Management**: Agents stay warm, no cold-start penalty
3. **Easier Monitoring**: Each agent has its own health endpoint
4. **Production-Ready**: Standard HTTP load balancing and service mesh integration
5. **Horizontal Scaling**: Can run multiple instances of each agent behind a load balancer

## Changes Made

### 1. MCP Server Files Updated

All four agent servers now use SSE transport:

**Orchestrator** ([src/agents/orchestrator/server.py](src/agents/orchestrator/server.py)):
```python
mcp.run(transport="sse", host="0.0.0.0", port=8001)
```

**Indexer** ([src/agents/indexer/server.py](src/agents/indexer/server.py)):
```python
mcp.run(transport="sse", host="0.0.0.0", port=8002)
```

**Graph Query** ([src/agents/graph_query/server.py](src/agents/graph_query/server.py)):
```python
mcp.run(transport="sse", host="0.0.0.0", port=8003)
```

**Code Analyst** ([src/agents/code_analyst/server.py](src/agents/code_analyst/server.py)):
```python
mcp.run(transport="sse", host="0.0.0.0", port=8004)
```

### 2. Agent Configurations Updated

Added `host` and `port` settings to each agent config:

- `OrchestratorSettings`: host=0.0.0.0, port=8001
- `IndexerSettings`: host=0.0.0.0, port=8002
- `GraphQuerySettings`: host=0.0.0.0, port=8003
- `CodeAnalystSettings`: host=0.0.0.0, port=8004

### 3. Docker Infrastructure Updated

**Dockerfiles**: Added curl for health checks and exposed ports
- [Dockerfile.orchestrator](Dockerfile.orchestrator): EXPOSE 8001
- [Dockerfile.indexer](Dockerfile.indexer): EXPOSE 8002
- [Dockerfile.graph_query](Dockerfile.graph_query): EXPOSE 8003
- [Dockerfile.code_analyst](Dockerfile.code_analyst): EXPOSE 8004

**docker-compose.yml**: Each agent runs as a separate service
```yaml
orchestrator:
  ports:
    - "8001:8001"
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:8001/health"]

indexer:
  ports:
    - "8002:8002"
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:8002/health"]

# etc...
```

### 4. Gateway and Client Code Updated

**Gateway** ([src/gateway/app.py](src/gateway/app.py)):
```python
orchestrator_url = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8001")
orchestrator_client = MultiServerMCPClient({
    "orchestrator": {
        "url": orchestrator_url,
        "transport": "sse",
    }
})
```

**Agent Wrappers**: All agent wrapper classes updated to connect via HTTP:
- [IndexerAgent](src/agents/indexer/agent.py)
- [GraphQueryAgent](src/agents/graph_query/agent.py)
- [CodeAnalystAgent](src/agents/code_analyst/agent.py)

**Routes**: Updated to use HTTP URLs:
- [index.py](src/gateway/routes/index.py)
- [health.py](src/gateway/routes/health.py)

### 5. Environment Variables Added

[.env.example](.env.example) now includes agent URLs:
```env
ORCHESTRATOR_URL=http://orchestrator:8001
INDEXER_URL=http://indexer:8002
GRAPH_QUERY_URL=http://graph_query:8003
CODE_ANALYST_URL=http://code_analyst:8004
```

## Port Allocation

| Service | Port | Description |
|---------|------|-------------|
| Gateway | 8000 | FastAPI HTTP/WebSocket API |
| Orchestrator | 8001 | MCP Server (SSE) |
| Indexer | 8002 | MCP Server (SSE) |
| Graph Query | 8003 | MCP Server (SSE) |
| Code Analyst | 8004 | MCP Server (SSE) |
| Neo4j HTTP | 7474 | Neo4j Browser |
| Neo4j Bolt | 7687 | Neo4j Database Protocol |

## Service Dependencies

The docker-compose.yml enforces proper startup order:

```
Neo4j (starts first)
  ↓
All MCP Agents (start after Neo4j is healthy)
  ↓
Gateway (starts after all agents are healthy)
```

## Health Checks

Each MCP server exposes an SSE endpoint at `/sse` that can be used for health checks:
- Docker health checks (configured to ping `/sse`)
- Load balancer health probes
- Monitoring systems
- Manual verification

**Note**: FastMCP with SSE transport doesn't automatically provide a `/health` endpoint.
Health checks ping the actual SSE endpoint at `/sse` instead.

Example:
```bash
curl http://localhost:8001/sse  # Orchestrator
curl http://localhost:8002/sse  # Indexer
curl http://localhost:8003/sse  # Graph Query
curl http://localhost:8004/sse  # Code Analyst
```

The docker-compose.yml health checks use:
```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8001/sse"]
```

## Testing the Migration

1. **Start all services**:
   ```bash
   docker-compose up -d
   ```

2. **Verify all agents are healthy**:
   ```bash
   curl http://localhost:8000/api/agents/health
   ```

3. **Test agent communication**:
   ```bash
   curl -X POST http://localhost:8000/api/chat \
     -H "Content-Type: application/json" \
     -d '{"message": "What is the FastAPI class?"}'
   ```

4. **Check agent logs**:
   ```bash
   docker-compose logs orchestrator
   docker-compose logs indexer
   docker-compose logs graph_query
   docker-compose logs code_analyst
   ```

## Rollback (if needed)

To rollback to stdio transport:

1. Revert server files to use `mcp.run(transport="stdio")`
2. Revert client code to use `command` and `args` instead of `url`
3. Revert docker-compose.yml to remove separate agent services
4. Revert gateway Dockerfile to include all agent code

## Production Considerations

### Scaling

Horizontal scaling example:
```yaml
orchestrator:
  deploy:
    replicas: 3
```

Add a load balancer (nginx, traefik, etc.) in front of agents.

### Monitoring

- Prometheus metrics can be added to each agent
- Health check endpoints provide basic availability monitoring
- Structured logging with correlation IDs for request tracing

### Security

- Add authentication between services (JWT, mTLS)
- Use private Docker networks
- Don't expose agent ports publicly (only gateway port 8000)
- Use secrets management for API keys

## Troubleshooting

**Agent won't start**:
- Check logs: `docker-compose logs <agent_name>`
- Verify environment variables in .env
- Ensure port is not already in use

**Health check failing**:
- Verify agent is running: `docker-compose ps`
- Test SSE endpoint manually: `curl http://localhost:800X/sse`
- Check firewall rules
- **Note**: Health checks use `/sse` not `/health` (FastMCP SSE endpoint)

**Gateway can't connect to agents**:
- Verify agent URLs in docker-compose.yml environment section
- Check Docker network: `docker network inspect graphical-rag-network`
- Ensure agents started before gateway (check depends_on)
- Test agent connectivity: `python verify_endpoints.py`

## Summary

The migration to SSE transport provides a production-ready architecture where each MCP agent runs as an independent, long-running service. This enables better scalability, monitoring, and operational management compared to the stdio subprocess approach.

All agents now communicate via HTTP/SSE, making the system compatible with standard cloud-native infrastructure (Kubernetes, service meshes, load balancers, etc.).
