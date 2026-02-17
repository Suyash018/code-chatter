#!/bin/bash

# FastAPI Repository Chat Agent - Startup Script

set -e

echo "════════════════════════════════════════════════════════════"
echo "  FastAPI Repository Chat Agent - MCP Multi-Agent System"
echo "════════════════════════════════════════════════════════════"
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    echo "❌ Error: .env file not found"
    echo ""
    echo "Please create .env from .env.example:"
    echo "  cp .env.example .env"
    echo ""
    echo "Then edit .env and add your OpenAI API key and Neo4j password"
    exit 1
fi

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Error: Docker is not running"
    echo ""
    echo "Please start Docker Desktop and try again"
    exit 1
fi

# Check if docker-compose is available
if ! command -v docker-compose &> /dev/null; then
    echo "❌ Error: docker-compose not found"
    echo ""
    echo "Please install Docker Compose"
    exit 1
fi

echo "✓ Prerequisites check passed"
echo ""

# Start services
echo "Starting services..."
echo ""

docker-compose up -d

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  Services Started Successfully"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "Gateway:    http://localhost:8000"
echo "API Docs:   http://localhost:8000/docs"
echo "Neo4j:      http://localhost:7474"
echo ""
echo "Checking service health..."
sleep 10

# Wait for gateway to be ready
TIMEOUT=60
ELAPSED=0
echo -n "Waiting for gateway to be ready"
while [ $ELAPSED -lt $TIMEOUT ]; do
    if curl -s http://localhost:8000/api/health > /dev/null 2>&1; then
        echo " ✓"
        break
    fi
    echo -n "."
    sleep 2
    ELAPSED=$((ELAPSED + 2))
done

if [ $ELAPSED -ge $TIMEOUT ]; then
    echo " ✗"
    echo ""
    echo "⚠️  Gateway did not become ready in time"
    echo "Check logs: docker-compose logs gateway"
    exit 1
fi

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  System Ready"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "Next steps:"
echo ""
echo "1. Index the FastAPI repository:"
echo "   curl -X POST http://localhost:8000/api/index \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{"
echo "       \"repository_url\": \"https://github.com/tiangolo/fastapi\","
echo "       \"clear_graph\": true,"
echo "       \"run_enrichment\": true"
echo "     }'"
echo ""
echo "2. Check agent health:"
echo "   curl http://localhost:8000/api/agents/health"
echo ""
echo "3. Ask a question:"
echo "   curl -X POST http://localhost:8000/api/chat \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{\"message\": \"What is the FastAPI class?\"}'"
echo ""
echo "4. View logs:"
echo "   docker-compose logs -f"
echo ""
echo "5. Stop services:"
echo "   docker-compose down"
echo ""
