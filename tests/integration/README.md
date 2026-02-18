# Integration Tests for Docker Compose Deployment

This directory contains integration tests that validate the complete Docker Compose deployment of the multi-agent chat system.

## Overview

The test suite makes **real HTTP requests** to the running Docker Compose services to validate:
- Multi-turn conversations (10, 20, 30, 40 turns)
- Session management and context preservation
- Agent routing and coordination
- Query complexity handling (simple, medium, complex)
- System performance and reliability

**Total test coverage**: 100 queries across 4 sessions (10+20+30+40 turns)

## Prerequisites

1. **Configure environment** (`.env` file with credentials):
   - OpenAI API key
   - Neo4j Aura connection details (URI, username, password)
   - Optional: Langfuse observability credentials

2. **Start Docker Compose services**:
   ```bash
   docker-compose -f docker-compose.cloud.yml up -d
   ```

3. **Wait for services to be healthy**:
   ```bash
   docker-compose -f docker-compose.cloud.yml ps
   ```

   All services should show status "Up" and health "healthy":
   - gateway (port 8000)
   - orchestrator (port 8001)
   - indexer (port 8002)
   - graph_query (port 8003)
   - code_analyst (port 8004)

   **Note**: Neo4j runs in the cloud (Neo4j Aura), not as a Docker container

4. **Index the FastAPI repository** (first time only):
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

   This returns a `job_id`. Wait for indexing to complete (check status):
   ```bash
   curl http://localhost:8000/api/index/status/<job_id>
   ```

   Indexing takes 5-30 minutes depending on enrichment settings.

## Running the Tests

### Option 1: Run directly with Python

```bash
python tests/integration/test_docker_compose_chat.py
```

### Option 2: Run with pytest

```bash
pytest tests/integration/test_docker_compose_chat.py -v -s
```

The `-s` flag shows real-time output as tests run.

### Option 3: Run specific session tests

You can modify the script to run only specific sessions by editing the `run_all_sessions()` method.

## Test Structure

### Session 1: Basic Exploration (10 turns)
- **Distribution**: 60% simple, 30% medium, 10% complex
- **Focus**: Basic entity lookups and simple queries
- **Purpose**: Validate basic system functionality

### Session 2: Medium Depth (20 turns)
- **Distribution**: 40% simple, 40% medium, 20% complex
- **Focus**: Multi-turn follow-ups with context carryover
- **Purpose**: Validate session management and agent coordination

### Session 3: Complex Analysis (30 turns)
- **Distribution**: 30% simple, 40% medium, 30% complex
- **Focus**: Deep architectural queries and comparisons
- **Purpose**: Validate synthesis of multi-agent outputs

### Session 4: Stress Test (40 turns)
- **Distribution**: 30% simple, 40% medium, 30% complex
- **Focus**: Long-running session with context window limits
- **Purpose**: Validate context pruning (max 20 turns retained)

## Query Difficulty Levels

Based on [requirements.md](../../requirements.md):

### Simple Queries
- Single agent invocation
- Direct entity lookups
- Examples:
  - "What is the FastAPI class?"
  - "Show me the Depends function"
  - "What depends on APIRouter?"

### Medium Queries
- 2-3 agent coordination
- Multi-turn follow-ups
- Examples:
  - "How does FastAPI handle request validation?"
  - "What classes inherit from APIRouter?"
  - "Find all decorators used in the routing module"

### Complex Queries
- Multiple agents with synthesis
- Deep architectural analysis
- Examples:
  - "Explain the complete lifecycle of a FastAPI request"
  - "How does dependency injection work and show me examples from the codebase"
  - "Compare how Path and Query parameters are implemented"

## Expected Output

The test runner provides:

1. **Real-time progress** for each turn
2. **Per-session summaries** with success rates
3. **Final comprehensive report** including:
   - Overall success rate
   - Results by difficulty level
   - Response time statistics (min, max, avg, median)
   - Intent coverage (all 8 intent types)
   - Agent coverage (all 3 agents + orchestrator)
   - Session context preservation metrics
   - Detailed failure reports

Example output:
```
================================================================================
DOCKER COMPOSE INTEGRATION TESTS - CHAT ENDPOINT
================================================================================
✓ Gateway is healthy

================================================================================
SESSION: Session 1: Basic Exploration (10 turns)
Session ID: 7c3e5f6a-8b2d-4e3f-9a1c-2d4e6f8a0b1c
================================================================================

Turn 1/10 [SIMPLE]: What is the FastAPI class?
  ✓ SUCCESS (3.45s)
  Intent: code_explanation | Agents: graph_query, code_analyst
  Response: The FastAPI class is the main application class in FastAPI...

...

--------------------------------------------------------------------------------
Session Summary: 10/10 successful (0 failed)
Average response time: 4.23s
--------------------------------------------------------------------------------
```

## Configuration

Edit the following constants in the test file if needed:

```python
GATEWAY_URL = "http://localhost:8000"  # Gateway base URL
TIMEOUT = 120.0                         # Request timeout in seconds
```

## Troubleshooting

### Gateway not accessible
```
✗ Cannot connect to gateway: Connection refused
```
**Solution**: Ensure Docker Compose is running:
```bash
docker-compose -f docker-compose.cloud.yml up -d
docker-compose -f docker-compose.cloud.yml logs gateway
```

### Timeout errors
```
✗ FAILED: Timeout after 120s
```
**Solution**:
- Increase `TIMEOUT` constant in the test file
- Check agent logs for performance issues:
  ```bash
  docker-compose -f docker-compose.cloud.yml logs orchestrator
  docker-compose -f docker-compose.cloud.yml logs code_analyst
  ```

### Repository not indexed
```
Intent: indexing_operation | Response: Repository not indexed yet
```
**Solution**: Run the indexing operation first (see Prerequisites #3)

### Orchestrator errors
```
HTTP 503: Orchestrator client not initialized
```
**Solution**: Wait for orchestrator to fully initialize:
```bash
docker-compose -f docker-compose.cloud.yml logs orchestrator | grep "initialized"
```

### Neo4j connection errors
```
HTTP 500: Neo4j connection failed
```
**Solution**:
- Verify Neo4j Aura credentials in `.env` file
- Check `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`
- Ensure your IP is whitelisted in Neo4j Aura console

## Interpreting Results

### Success Criteria
- **>90% success rate**: Excellent - system is production-ready
- **75-90% success rate**: Good - some issues to investigate
- **<75% success rate**: Needs attention - significant issues detected

### Key Metrics to Monitor
1. **Success rate by difficulty**: Complex queries may have lower success rates
2. **Response times**: Should be <10s for simple, <30s for medium, <60s for complex
3. **Intent coverage**: All 8 intents should be detected at least once
4. **Agent coverage**: All agents (graph_query, code_analyst, indexer) should be called
5. **Session context**: Follow-up queries should maintain context across turns

### Common Issues
- **High failure rate on complex queries**: May need better synthesis prompts
- **Slow response times**: Check Neo4j performance and agent timeout settings
- **Context not preserved**: Check orchestrator context manager logs
- **Missing intents**: Query analyzer may need refinement

## Next Steps

After running integration tests:

1. **Review failures**: Check failed query patterns
2. **Optimize slow queries**: Investigate queries >30s
3. **Improve agent prompts**: Based on response quality
4. **Scale testing**: Run with more concurrent sessions
5. **Load testing**: Use tools like `locust` for stress testing

## Related Files

- [src/gateway/routes/chat.py](../../src/gateway/routes/chat.py) - Chat endpoint implementation
- [docker-compose.cloud.yml](../../docker-compose.cloud.yml) - Service configuration
- [requirements.md](../../requirements.md) - Original assignment requirements
- [README.md](../../README.md) - Main project documentation
- [.env.example](../../.env.example) - Environment configuration template
