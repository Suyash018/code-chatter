#!/usr/bin/env python3
"""
Quick system test script.

Tests basic functionality of the FastAPI Gateway and MCP agents.
Run this after starting services with docker-compose.
"""

import sys
import time
import requests
import json

BASE_URL = "http://localhost:8000"


def test_health():
    """Test basic health endpoint."""
    print("Testing health endpoint...", end=" ")
    try:
        response = requests.get(f"{BASE_URL}/api/health")
        response.raise_for_status()
        data = response.json()
        assert data["status"] == "healthy"
        print("✓")
        return True
    except Exception as e:
        print(f"✗ {e}")
        return False


def test_agents_health():
    """Test agent health checks."""
    print("Testing agent health...", end=" ")
    try:
        response = requests.get(f"{BASE_URL}/api/agents/health")
        response.raise_for_status()
        data = response.json()
        print(f"✓ ({data['healthy_count']}/{data['total_count']} agents healthy)")

        # Show agent details
        for agent in data["agents"]:
            status_icon = "✓" if agent["status"] == "healthy" else "✗"
            print(f"  {status_icon} {agent['agent_name']}: {agent['status']}")
            if agent.get("error"):
                print(f"    Error: {agent['error']}")

        return data["overall_status"] in ["healthy", "degraded"]
    except Exception as e:
        print(f"✗ {e}")
        return False


def test_chat():
    """Test chat endpoint with a simple query."""
    print("Testing chat endpoint...", end=" ")
    try:
        response = requests.post(
            f"{BASE_URL}/api/chat",
            json={"message": "What is the FastAPI class?"},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        assert "response" in data
        assert "session_id" in data
        print("✓")
        print(f"  Intent: {data.get('intent', 'unknown')}")
        print(f"  Agents called: {', '.join(data.get('agents_called', []))}")
        if data.get("errors"):
            print(f"  Errors: {data['errors']}")
        return True
    except Exception as e:
        print(f"✗ {e}")
        return False


def test_index_trigger():
    """Test indexing trigger (doesn't wait for completion)."""
    print("Testing index trigger...", end=" ")
    try:
        response = requests.post(
            f"{BASE_URL}/api/index",
            json={
                "repository_url": "https://github.com/tiangolo/fastapi",
                "clear_graph": False,
                "run_enrichment": False,  # Faster for testing
                "create_embeddings": False,
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        assert "job_id" in data
        print(f"✓ (job_id: {data['job_id']})")
        return True, data["job_id"]
    except Exception as e:
        print(f"✗ {e}")
        return False, None


def test_index_status(job_id):
    """Test indexing status check."""
    print(f"Testing index status for job {job_id}...", end=" ")
    try:
        response = requests.get(f"{BASE_URL}/api/index/status/{job_id}")
        response.raise_for_status()
        data = response.json()
        assert "status" in data
        print(f"✓ (status: {data['status']})")
        return True
    except Exception as e:
        print(f"✗ {e}")
        return False


def main():
    """Run all tests."""
    print("════════════════════════════════════════════════════════════")
    print("  FastAPI Repository Chat Agent - System Test")
    print("════════════════════════════════════════════════════════════")
    print()

    # Wait for service to be ready
    print("Waiting for services to be ready...")
    max_retries = 30
    for i in range(max_retries):
        try:
            requests.get(f"{BASE_URL}/api/health", timeout=2)
            print("Services are ready!")
            print()
            break
        except:
            if i == max_retries - 1:
                print("✗ Services did not become ready")
                print()
                print("Make sure services are running:")
                print("  docker-compose up -d")
                return 1
            time.sleep(2)

    # Run tests
    results = []

    results.append(("Health Check", test_health()))
    results.append(("Agent Health", test_agents_health()))
    results.append(("Chat Endpoint", test_chat()))

    index_result, job_id = test_index_trigger()
    results.append(("Index Trigger", index_result))

    if job_id:
        results.append(("Index Status", test_index_status(job_id)))

    # Summary
    print()
    print("════════════════════════════════════════════════════════════")
    print("  Test Summary")
    print("════════════════════════════════════════════════════════════")

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {name}")

    print()
    print(f"Results: {passed}/{total} tests passed")
    print()

    if passed == total:
        print("✓ All tests passed!")
        return 0
    else:
        print("✗ Some tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
