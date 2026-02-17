"""Smoke test for the Code Analyst agent — exercises all 6 MCP tools."""

import asyncio
from dotenv import load_dotenv

load_dotenv()

from src.agents.code_analyst.agent import CodeAnalystAgent

QUERIES = [
    {
        "target_tool": "analyze_function",
        "query": "What does the solve_dependencies function do? Describe its purpose, complexity, and callers.",
    },
    {
        "target_tool": "analyze_class",
        "query": "Analyze the APIRoute class. What are its responsibilities, methods, and inheritance hierarchy?",
    },
    {
        "target_tool": "find_patterns",
        "query": "What design patterns are used in the FastAPI codebase? List the patterns and their implementations.",
    },
    {
        "target_tool": "get_code_snippet",
        "query": "Show me the source code of the get_openapi function along with its imports.",
    },
    {
        "target_tool": "explain_implementation",
        "query": "How does the include_router method work? Trace its call chain and data flow step by step.",
    },
    {
        "target_tool": "compare_implementations",
        "query": "Compare the Path and Query functions side by side — how do they differ in purpose, parameters, and usage?",
    },
]


async def main():
    print("=" * 60)
    print("  Code Analyst Agent — Full Tool Test")
    print("=" * 60)

    agent = await CodeAnalystAgent.create()
    print(f"Agent ready. Running {len(QUERIES)} queries...\n")

    passed = 0
    failed = 0

    for i, q in enumerate(QUERIES, 1):
        tool = q["target_tool"]
        query = q["query"]
        separator = "-" * 60
        print(f"\n{separator}")
        print(f"  [{i}/{len(QUERIES)}] Target tool: {tool}")
        print(f"  Query: {query}")
        print(separator)

        try:
            answer = await agent.invoke(query)
            is_fallback = answer == "I was unable to produce an analysis for this query."
            status = "FAIL (fallback)" if is_fallback else "PASS"
            if is_fallback:
                failed += 1
            else:
                passed += 1
            print(f"\n  Status: {status}")
            print(f"\n  Answer ({len(answer)} chars):\n")
            preview = answer[:500] + ("..." if len(answer) > 500 else "")
            print(preview)
        except Exception as e:
            failed += 1
            print(f"\n  Status: ERROR — {type(e).__name__}: {e}")

    await agent.close()

    print("\n" + "=" * 60)
    print(f"  Results: {passed} passed, {failed} failed out of {len(QUERIES)}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
