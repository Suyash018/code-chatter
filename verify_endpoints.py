#!/usr/bin/env python3
"""
Endpoint Connectivity Verification Script

Tests all connections between services to ensure the MCP agent
architecture is correctly configured for SSE transport.
"""

import os
import sys
import time
import socket
from typing import Dict, Tuple

# ANSI colors for output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"


def check_port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    """Check if a TCP port is open."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def check_http_endpoint(url: str, timeout: float = 5.0) -> Tuple[bool, str]:
    """Check if an HTTP endpoint is accessible."""
    try:
        import requests
        response = requests.get(url, timeout=timeout)
        return True, f"Status {response.status_code}"
    except requests.exceptions.ConnectionError:
        return False, "Connection refused"
    except requests.exceptions.Timeout:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)


def print_header(text: str):
    """Print a section header."""
    print(f"\n{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}{text:^60}{RESET}")
    print(f"{BLUE}{'='*60}{RESET}\n")


def print_result(name: str, success: bool, details: str = ""):
    """Print a test result."""
    status = f"{GREEN}✓ PASS{RESET}" if success else f"{RED}✗ FAIL{RESET}"
    print(f"{status}: {name}")
    if details:
        print(f"       {details}")


def main():
    """Run all connectivity tests."""
    print(f"\n{BLUE}{'='*60}")
    print(f"  MCP Agent Endpoint Connectivity Verification")
    print(f"{'='*60}{RESET}\n")

    # Configuration
    services = {
        "Neo4j Bolt": ("localhost", 7687),
        "Neo4j HTTP": ("localhost", 7474),
        "Gateway": ("localhost", 8000),
        "Orchestrator": ("localhost", 8001),
        "Indexer": ("localhost", 8002),
        "Graph Query": ("localhost", 8003),
        "Code Analyst": ("localhost", 8004),
    }

    http_endpoints = {
        "Gateway Health": "http://localhost:8000/api/health",
        "Gateway Agents Health": "http://localhost:8000/api/agents/health",
        "Orchestrator SSE": "http://localhost:8001/sse",
        "Indexer SSE": "http://localhost:8002/sse",
        "Graph Query SSE": "http://localhost:8003/sse",
        "Code Analyst SSE": "http://localhost:8004/sse",
    }

    # Test 1: Port Availability
    print_header("1. Port Availability Check")

    all_ports_open = True
    for name, (host, port) in services.items():
        is_open = check_port_open(host, port)
        all_ports_open = all_ports_open and is_open
        print_result(f"{name} ({host}:{port})", is_open)

    if not all_ports_open:
        print(f"\n{YELLOW}⚠ Some ports are not accessible.{RESET}")
        print(f"{YELLOW}  Make sure docker-compose is running: docker-compose up -d{RESET}")
        print(f"{YELLOW}  Check logs: docker-compose logs{RESET}\n")
        return 1

    # Test 2: HTTP Endpoint Accessibility
    print_header("2. HTTP Endpoint Accessibility")

    all_http_ok = True
    for name, url in http_endpoints.items():
        success, details = check_http_endpoint(url)
        all_http_ok = all_http_ok and success
        print_result(name, success, details)

    # Test 3: Environment Variables
    print_header("3. Environment Variables Check")

    env_vars = [
        "OPENAI_API_KEY",
        "NEO4J_URI",
        "NEO4J_USERNAME",
        "NEO4J_PASSWORD",
        "ORCHESTRATOR_URL",
        "INDEXER_URL",
        "GRAPH_QUERY_URL",
        "CODE_ANALYST_URL",
    ]

    all_env_set = True
    for var in env_vars:
        value = os.getenv(var)
        is_set = value is not None and value != ""
        all_env_set = all_env_set and is_set

        if is_set:
            # Mask sensitive values
            if "KEY" in var or "PASSWORD" in var:
                display_value = f"{value[:10]}..." if len(value) > 10 else "***"
            else:
                display_value = value
            print_result(var, True, display_value)
        else:
            print_result(var, False, "Not set")

    # Test 4: Docker Container Status
    print_header("4. Docker Container Status")

    try:
        import subprocess
        result = subprocess.run(
            ["docker-compose", "ps", "--format", "json"],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(__file__) or ".",
        )

        if result.returncode == 0:
            import json
            containers = []
            for line in result.stdout.strip().split('\n'):
                if line:
                    try:
                        containers.append(json.loads(line))
                    except:
                        pass

            for container in containers:
                name = container.get("Service", "Unknown")
                state = container.get("State", "Unknown")
                health = container.get("Health", "")

                is_healthy = state == "running"
                status_text = f"State: {state}"
                if health:
                    status_text += f", Health: {health}"

                print_result(f"Container: {name}", is_healthy, status_text)
        else:
            print_result("Docker Compose Status", False, "docker-compose not available")

    except Exception as e:
        print_result("Docker Container Check", False, str(e))

    # Summary
    print_header("Summary")

    if all_ports_open and all_http_ok and all_env_set:
        print(f"{GREEN}✓ All connectivity tests passed!{RESET}\n")
        print("The system is ready to use:")
        print(f"  • Gateway API: http://localhost:8000")
        print(f"  • API Docs: http://localhost:8000/docs")
        print(f"  • Neo4j Browser: http://localhost:7474")
        print()
        return 0
    else:
        print(f"{RED}✗ Some tests failed.{RESET}\n")

        if not all_ports_open:
            print(f"{YELLOW}Port issues:{RESET}")
            print(f"  • Start services: docker-compose up -d")
            print(f"  • Check logs: docker-compose logs")

        if not all_http_ok:
            print(f"{YELLOW}HTTP endpoint issues:{RESET}")
            print(f"  • Wait for services to start (may take 30-60s)")
            print(f"  • Check health: docker-compose ps")

        if not all_env_set:
            print(f"{YELLOW}Environment variable issues:{RESET}")
            print(f"  • Copy .env.example to .env")
            print(f"  • Set OPENAI_API_KEY and NEO4J credentials")

        print()
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Interrupted by user{RESET}\n")
        sys.exit(130)
