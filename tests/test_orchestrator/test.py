"""
Smoke test for the Orchestrator agent — exercises multi-turn conversation.

Requires a running Neo4j instance with the FastAPI codebase already indexed.
Set NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD (and OPENAI_API_KEY) in .env
before running.

Run:
    python -m tests.test_orchestrator.test
"""

import asyncio

from dotenv import load_dotenv

load_dotenv()

from src.agents.orchestrator.agent import OrchestratorAgent

SESSION_ID = "smoke-test-session"

QUERIES = [
    {
        "label": "Initial question",
        "query": "What is the FastAPI class?",
    },
    {
        "label": "Follow-up (same session)",
        "query": "What are its methods?",
    },
    {
        "label": "Dependency query",
        "query": "What does APIRoute depend on?",
    },
]


async def main() -> None:
    print("=" * 65)
    print("  Orchestrator Agent — Multi-Turn Smoke Test")
    print("=" * 65)

    agent = await OrchestratorAgent.create()
    print(f"Agent ready. Running {len(QUERIES)} queries…\n")

    passed = 0
    failed = 0

    for i, q in enumerate(QUERIES, 1):
        label = q["label"]
        query = q["query"]
        sep = "-" * 65
        print(f"\n{sep}")
        print(f"  [{i}/{len(QUERIES)}] {label}")
        print(f"  Query: {query}")
        print(sep)

        try:
            result = await agent.invoke(query, session_id=SESSION_ID)

            # result is a dict: {"response": "...", "suggestive_pills": [...]}
            response = result.get("response", "")
            pills = result.get("suggestive_pills", [])

            is_fallback = (
                not response
                or response == "I was unable to produce an answer for this query."
            )
            status = "FAIL (fallback)" if is_fallback else "PASS"

            if is_fallback:
                failed += 1
            else:
                passed += 1

            print(f"\n  Status : {status}")
            print(f"\n  Response ({len(response)} chars):\n")
            preview = response[:600] + ("…" if len(response) > 600 else "")
            print(preview)
            if pills:
                print(f"\n  Suggestive pills: {pills}")
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
