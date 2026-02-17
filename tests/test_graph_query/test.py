"""
Smoke test for the Graph Query agent — exercises all 7 MCP tools.

Requires a running Neo4j instance with the FastAPI codebase already indexed.
Set NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD (and OPENAI_API_KEY) in .env
before running.

Run:
    python -m tests.test_graph_query.test
"""

import asyncio
from dotenv import load_dotenv

load_dotenv()

from src.agents.graph_query.agent import GraphQueryAgent

# Each entry targets one of the 7 MCP tools exposed by the server.
QUERIES = [
    {
        "target_tool": "find_entity",
        "query": (
            "Find the FastAPI class in the codebase. "
            "Show its qualified name, purpose, and what module it belongs to."
        ),
    },
    {
        "target_tool": "get_dependencies",
        "query": (
            "What does the APIRoute class depend on? "
            "List its direct dependencies — methods it calls, classes it inherits from, "
            "and decorators applied to it."
        ),
    },
    {
        "target_tool": "get_dependents",
        "query": (
            "What functions call solve_dependencies? "
            "Show all callers and the modules they live in."
        ),
    },
    {
        "target_tool": "trace_imports",
        "query": (
            "Trace the import chain for the fastapi.routing module. "
            "Show what it imports and which modules import it."
        ),
    },
    {
        "target_tool": "find_related",
        "query": (
            "What design patterns does the FastAPI class implement? "
            "Also list any domain concepts it relates to."
        ),
    },
    {
        "target_tool": "execute_query",
        "query": (
            "How many classes in the codebase inherit from APIRouter? "
            "Use a custom graph query to count them and list their names."
        ),
    },
    {
        "target_tool": "get_subgraph",
        "query": (
            "Explain how dependency injection works in FastAPI. "
            "Use the entities Depends, solve_dependencies, and Dependant as seeds "
            "and expand two hops in both directions to get the full picture."
        ),
    },
]


async def main() -> None:
    print("=" * 65)
    print("  Graph Query Agent — Full Tool Smoke Test")
    print("=" * 65)

    agent = await GraphQueryAgent.create()
    print(f"Agent ready. Running {len(QUERIES)} queries…\n")

    passed = 0
    failed = 0
    fallback_msg = "No graph context could be retrieved for this query."

    for i, q in enumerate(QUERIES, 1):
        tool = q["target_tool"]
        query = q["query"]
        sep = "-" * 65
        print(f"\n{sep}")
        print(f"  [{i}/{len(QUERIES)}] Target tool : {tool}")
        print(f"  Query        : {query[:100]}{'…' if len(query) > 100 else ''}")
        print(sep)

        try:
            answer = await agent.invoke(query)
            is_fallback = answer == fallback_msg
            status = "FAIL (fallback)" if is_fallback else "PASS"
            if is_fallback:
                failed += 1
            else:
                passed += 1

            print(f"\n  Status : {status}")
            print(f"\n  Answer ({len(answer)} chars):\n")
            preview = answer[:600] + ("…" if len(answer) > 600 else "")
            print(preview)
        except Exception as exc:
            failed += 1
            print(f"\n  Status : ERROR — {type(exc).__name__}: {exc}")

    await agent.close()

    print("\n" + "=" * 65)
    print(
        f"  Results: {passed} passed, {failed} failed out of {len(QUERIES)}"
    )
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(main())
