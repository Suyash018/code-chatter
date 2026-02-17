@echo off
REM FastAPI Repository Chat Agent - Startup Script (Windows)

echo ================================================================
echo   FastAPI Repository Chat Agent - MCP Multi-Agent System
echo ================================================================
echo.

REM Check if .env exists
if not exist .env (
    echo Error: .env file not found
    echo.
    echo Please create .env from .env.example:
    echo   copy .env.example .env
    echo.
    echo Then edit .env and add your OpenAI API key and Neo4j password
    exit /b 1
)

REM Check if Docker is running
docker info >nul 2>&1
if errorlevel 1 (
    echo Error: Docker is not running
    echo.
    echo Please start Docker Desktop and try again
    exit /b 1
)

REM Check if docker-compose is available
docker-compose --version >nul 2>&1
if errorlevel 1 (
    echo Error: docker-compose not found
    echo.
    echo Please install Docker Compose
    exit /b 1
)

echo Prerequisites check passed
echo.

REM Start services
echo Starting services...
echo.

docker-compose up -d

echo.
echo ================================================================
echo   Services Started Successfully
echo ================================================================
echo.
echo Gateway:    http://localhost:8000
echo API Docs:   http://localhost:8000/docs
echo Neo4j:      http://localhost:7474
echo.
echo Checking service health...
timeout /t 10 /nobreak >nul

REM Wait for gateway to be ready
set TIMEOUT=60
set ELAPSED=0
echo Waiting for gateway to be ready
:wait_loop
if %ELAPSED% GEQ %TIMEOUT% goto timeout_error
curl -s http://localhost:8000/api/health >nul 2>&1
if errorlevel 1 (
    echo .
    timeout /t 2 /nobreak >nul
    set /a ELAPSED+=2
    goto wait_loop
)

echo Gateway is ready!
echo.
echo ================================================================
echo   System Ready
echo ================================================================
echo.
echo Next steps:
echo.
echo 1. Index the FastAPI repository:
echo    curl -X POST http://localhost:8000/api/index ^
echo      -H "Content-Type: application/json" ^
echo      -d "{\"repository_url\": \"https://github.com/tiangolo/fastapi\", \"clear_graph\": true}"
echo.
echo 2. Check agent health:
echo    curl http://localhost:8000/api/agents/health
echo.
echo 3. Ask a question:
echo    curl -X POST http://localhost:8000/api/chat ^
echo      -H "Content-Type: application/json" ^
echo      -d "{\"message\": \"What is the FastAPI class?\"}"
echo.
echo 4. View logs:
echo    docker-compose logs -f
echo.
echo 5. Stop services:
echo    docker-compose down
echo.
goto :eof

:timeout_error
echo.
echo Warning: Gateway did not become ready in time
echo Check logs: docker-compose logs gateway
exit /b 1
