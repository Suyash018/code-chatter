"""
Simplified integration tests for chat endpoint - Query-focused tests.

This script tests the chat endpoint with specific queries at different difficulty levels:
- Simple queries (single agent)
- Medium queries (2-3 agents)
- Complex queries (multiple agents + synthesis)

Prerequisites:
    - Run `docker-compose up` before executing these tests
    - Ensure gateway is accessible at http://localhost:8000
    - Graph should already be populated with FastAPI repository data

Run with:
    python -m pytest tests/integration/test_simple_chat.py -v -s

Or run directly:
    python tests/integration/test_simple_chat.py
"""

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from httpx import Limits, Timeout


# ─── Configuration ──────────────────────────────────────────

GATEWAY_URL = "http://localhost:8000"
CHAT_ENDPOINT = f"{GATEWAY_URL}/api/chat"
HEALTH_ENDPOINT = f"{GATEWAY_URL}/api/health"


# ─── Test Queries ──────────────────────────────────────────

@dataclass
class Query:
    """Represents a single query with expected behavior."""
    text: str
    difficulty: str  # "simple", "medium", "complex"
    description: str = ""


# Simple queries (single agent)
SIMPLE_QUERIES = [
    Query("What is the FastAPI class?", "simple", "FastAPI class lookup"),
    Query("Show me the docstring for the Depends function", "simple", "Depends function docstring"),
]

# Medium queries (2-3 agents)
MEDIUM_QUERIES = [
    Query("How does FastAPI handle request validation?", "medium", "Request validation"),
    Query("What classes inherit from APIRouter?", "medium", "APIRouter inheritance"),
    Query("Find all decorators used in the routing module", "medium", "Routing decorators"),
]

# Complex queries (multiple agents + synthesis)
COMPLEX_QUERIES = [
    Query("Explain the complete lifecycle of a FastAPI request", "complex", "Request lifecycle"),
    Query("How does dependency injection work and show me examples from the codebase", "complex", "DI with examples"),
    Query("Compare how Path and Query parameters are implemented", "complex", "Path vs Query implementation"),
    Query("What design patterns are used in FastAPI's core and why?", "complex", "Core design patterns"),
]


# ─── Test Results ──────────────────────────────────────────

@dataclass
class TestResult:
    """Result of a single query test."""
    session_id: str
    turn: int
    query: str
    success: bool
    response_data: dict[str, Any] | None
    error: str | None
    duration: float
    difficulty: str


# ─── Test Runner ──────────────────────────────────────────

class SimpleChatTestRunner:
    """Runs simplified chat integration tests."""

    def __init__(self):
        # Configure client with extended timeouts and connection pooling
        timeout = Timeout(
            connect=10.0,
            read=60.0,
            write=10.0,
            pool=5.0
        )
        limits = Limits(
            max_keepalive_connections=5,
            max_connections=10,
            keepalive_expiry=300.0
        )
        self.client = httpx.Client(
            timeout=timeout,
            limits=limits,
            follow_redirects=True
        )
        self.results: list[TestResult] = []

    def check_health(self) -> bool:
        """Check if the gateway is healthy."""
        print(f"\n[HEALTH CHECK]")
        print(f"Endpoint: {HEALTH_ENDPOINT}")

        try:
            response = self.client.get(HEALTH_ENDPOINT, timeout=10.0)
            print(f"Status: {response.status_code}")

            if response.status_code == 200:
                data = response.json() if response.text else {}
                print(f"Response: {data}")
                print("✓ Gateway is healthy\n")
                return True
            else:
                body = response.text[:200] if response.text else "No response body"
                print(f"Response: {body}")
                print(f"✗ Gateway health check failed: {response.status_code}\n")
                return False
        except Exception as e:
            print(f"Error type: {type(e).__name__}")
            print(f"Error: {e}")
            print(f"✗ Cannot connect to gateway")
            print(f"Make sure docker-compose is running and gateway is at {GATEWAY_URL}\n")
            return False

    def send_chat_message(
        self,
        message: str,
        session_id: str,
        verbose: bool = True
    ) -> tuple[bool, dict | None, str | None, float]:
        """Send a chat message and return (success, response_data, error, duration)."""
        start_time = time.time()

        request_data = {
            "message": message,
            "session_id": session_id,
            "stream": False
        }

        if verbose:
            print(f"\n[QUERY] {message}")

        try:
            response = self.client.post(
                CHAT_ENDPOINT,
                json=request_data,
                timeout=None
            )
            duration = time.time() - start_time

            if response.status_code == 200:
                data = response.json()
                if verbose:
                    resp_text = data.get("response", "")
                    print(f"✓ Response ({duration:.2f}s):")
                    print(f"  Intent: {data.get('intent', 'unknown')}")
                    print(f"  Agents: {', '.join(data.get('agents_called', []))}")
                    print(f"\n  Full Response:\n{resp_text}\n")
                    if data.get("suggestive_pills"):
                        print(f"  Suggestions: {data['suggestive_pills']}")
                return True, data, None, duration
            else:
                body = response.text[:300] if response.text else "No response body"
                if verbose:
                    print(f"✗ Failed ({duration:.2f}s)")
                    print(f"  Status: {response.status_code}")
                    print(f"  Body: {body}")
                error_detail = response.json().get("detail", "Unknown error") if response.text else "No response"
                return False, None, f"HTTP {response.status_code}: {error_detail}", duration

        except httpx.TimeoutException as e:
            duration = time.time() - start_time
            if verbose:
                print(f"✗ Request timed out")
            return False, None, "Request timed out", duration
        except Exception as e:
            duration = time.time() - start_time
            if verbose:
                print(f"✗ Error: {type(e).__name__}: {e}")
            return False, None, f"Request error: {str(e)}", duration

    def run_query_set(
        self,
        queries: list[Query],
        session_id: str,
        verbose: bool = True
    ) -> list[TestResult]:
        """Run a set of queries in the same session."""
        results = []

        for turn, query in enumerate(queries, 1):
            if verbose:
                print(f"\n{'─'*80}")
                print(f"Turn {turn}/{len(queries)} [{query.difficulty.upper()}] - {query.description}")

            success, response_data, error, duration = self.send_chat_message(
                query.text,
                session_id,
                verbose=verbose
            )

            result = TestResult(
                session_id=session_id,
                turn=turn,
                query=query.text,
                success=success,
                response_data=response_data,
                error=error,
                duration=duration,
                difficulty=query.difficulty
            )
            results.append(result)
            self.results.append(result)

            if not success and verbose:
                print(f"  Error: {error}")

        return results

    def run_all_tests(self, verbose: bool = True) -> None:
        """Run all chat tests organized by difficulty."""
        print("\n" + "="*80)
        print("SIMPLIFIED CHAT INTEGRATION TESTS")
        print("="*80)

        # Health check
        if not self.check_health():
            print("\n✗ Aborting tests: Gateway is not accessible")
            print("  Run: docker-compose up -d")
            print("  Wait for all services to be healthy")
            return

        # Create a single session for all queries
        session_id = str(uuid.uuid4())
        print(f"\nSession ID: {session_id}\n")

        # Run simple queries
        print("\n" + "="*80)
        print("SIMPLE QUERIES (Single Agent)")
        print("="*80)
        self.run_query_set(SIMPLE_QUERIES, session_id, verbose)

        # Run medium queries
        print("\n" + "="*80)
        print("MEDIUM QUERIES (2-3 Agents)")
        print("="*80)
        self.run_query_set(MEDIUM_QUERIES, session_id, verbose)

        # Run complex queries
        print("\n" + "="*80)
        print("COMPLEX QUERIES (Multiple Agents + Synthesis)")
        print("="*80)
        self.run_query_set(COMPLEX_QUERIES, session_id, verbose)

        # Print final report
        self.print_final_report()

    def print_final_report(self) -> None:
        """Print comprehensive test report."""
        print("\n" + "="*80)
        print("FINAL TEST REPORT")
        print("="*80 + "\n")

        total = len(self.results)
        successful = sum(1 for r in self.results if r.success)
        failed = total - successful

        # Overall stats
        print(f"Total queries: {total}")
        print(f"Successful: {successful} ({100*successful/total:.1f}%)")
        print(f"Failed: {failed} ({100*failed/total:.1f}%)")
        print()

        # By difficulty
        print("Results by difficulty:")
        for difficulty in ["simple", "medium", "complex"]:
            diff_results = [r for r in self.results if r.difficulty == difficulty]
            if diff_results:
                diff_success = sum(1 for r in diff_results if r.success)
                print(f"  {difficulty.capitalize()}: {diff_success}/{len(diff_results)} successful")
        print()

        # Performance stats
        successful_results = [r for r in self.results if r.success]
        if successful_results:
            durations = [r.duration for r in successful_results]
            print("Response time statistics (successful queries):")
            print(f"  Min: {min(durations):.2f}s")
            print(f"  Max: {max(durations):.2f}s")
            print(f"  Average: {sum(durations)/len(durations):.2f}s")
            print(f"  Median: {sorted(durations)[len(durations)//2]:.2f}s")
            print()

        # Failed queries
        if failed > 0:
            print(f"Failed queries ({failed}):")
            for r in self.results:
                if not r.success:
                    print(f"  - [{r.difficulty}] {r.query[:60]}...")
                    print(f"    Error: {r.error}")
            print()

        # Intent coverage
        intents_seen = set()
        for r in successful_results:
            if r.response_data:
                intent = r.response_data.get("intent")
                if intent:
                    intents_seen.add(intent)

        print(f"Intent coverage: {len(intents_seen)} unique intents")
        print(f"  Intents: {', '.join(sorted(intents_seen))}")
        print()

        # Agents used
        agents_seen = set()
        for r in successful_results:
            if r.response_data:
                agents = r.response_data.get("agents_called", [])
                agents_seen.update(agents)

        print(f"Agent coverage: {len(agents_seen)} unique agents")
        print(f"  Agents: {', '.join(sorted(agents_seen))}")
        print()

        # Overall verdict
        success_rate = 100 * successful / total
        print("="*80)
        if success_rate >= 90:
            print("✓ TESTS PASSED - Excellent performance!")
        elif success_rate >= 75:
            print("⚠ TESTS PASSED - Some issues detected")
        else:
            print("✗ TESTS FAILED - Significant issues detected")
        print(f"  Success rate: {success_rate:.1f}%")
        print("="*80 + "\n")

    def cleanup(self):
        """Cleanup resources."""
        self.client.close()


# ─── Main Execution ────────────────────────────────────────

def main():
    """Run the integration tests."""
    runner = SimpleChatTestRunner()

    try:
        runner.run_all_tests(verbose=True)
    except KeyboardInterrupt:
        print("\n\nTests interrupted by user")
    except Exception as e:
        print(f"\n\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        runner.cleanup()


if __name__ == "__main__":
    main()
