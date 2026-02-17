"""
Smoke test for the Indexer agent — exercises all 5 MCP tools via the
LangChain ReAct agent wrapper.

Requires:
  - OPENAI_API_KEY in .env (for the agent LLM)
  - Neo4j credentials in .env (for index_file, index_repository,
    get_index_status tools that touch the graph)

The parse_python_ast and extract_entities tools are pure parsing and
only need the agent LLM — no Neo4j required.

Run:
    python -m tests.test_indexer.test
"""

import asyncio
from dotenv import load_dotenv

load_dotenv()

from src.agents.indexer.agent import IndexerAgent

SAMPLE_CODE = '''\
"""Sample module."""

from typing import Optional


class Greeter:
    """A simple greeter class."""

    default_greeting: str = "Hello"

    def __init__(self, name: str) -> None:
        self.name = name

    def greet(self, greeting: Optional[str] = None) -> str:
        """Return a greeting string."""
        g = greeting or self.default_greeting
        return f"{g}, {self.name}!"


def make_greeter(name: str) -> Greeter:
    """Factory function for Greeter."""
    return Greeter(name)
'''

# Each entry targets one of the 5 MCP tools.
# The agent must call the tool, poll get_index_status for the job_id,
# and return results.
QUERIES = [
    {
        "target_tool": "parse_python_ast",
        "query": (
            f"Parse this Python code and tell me what classes and functions "
            f"it contains. Here is the code:\n\n```python\n{SAMPLE_CODE}\n```"
        ),
    },
    {
        "target_tool": "extract_entities",
        "query": (
            f"Extract all entities and their relationships from this code. "
            f"List the classes with their methods and attributes, and the "
            f"functions with their parameters.\n\n```python\n{SAMPLE_CODE}\n```"
        ),
    },
    {
        "target_tool": "get_index_status",
        "query": (
            "What is the current state of the knowledge graph index? "
            "Show me node counts, edge counts, and any warnings. "
            "Use get_index_status with no job_id to get the overview."
        ),
    },
    {
        "target_tool": "index_file",
        "query": (
            f"Incrementally index this file as 'sample/greeter.py' with "
            f"skip_enrichment=True. Here is the source code:\n\n"
            f"```python\n{SAMPLE_CODE}\n```"
        ),
    },
    {
        "target_tool": "index_repository",
        "query": (
            "Start indexing the FastAPI repository with skip_enrichment=True "
            "and clear_graph=False. Just kick off the job and report the "
            "job_id — do NOT wait for it to complete."
        ),
    },
]


async def main() -> None:
    print("=" * 65)
    print("  Indexer Agent — Full Tool Smoke Test")
    print("=" * 65)

    agent = await IndexerAgent.create()
    print(f"Agent ready. Running {len(QUERIES)} queries...\n")

    passed = 0
    failed = 0
    fallback_msg = "No indexing result could be produced for this instruction."

    for i, q in enumerate(QUERIES, 1):
        tool = q["target_tool"]
        query = q["query"]
        sep = "-" * 65
        print(f"\n{sep}")
        print(f"  [{i}/{len(QUERIES)}] Target tool : {tool}")
        print(f"  Query        : {query[:100]}{'...' if len(query) > 100 else ''}")
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
            preview = answer[:600] + ("..." if len(answer) > 600 else "")
            print(preview)
        except Exception as exc:
            failed += 1
            print(f"\n  Status : ERROR -- {type(exc).__name__}: {exc}")

    await agent.close()

    print("\n" + "=" * 65)
    print(
        f"  Results: {passed} passed, {failed} failed out of {len(QUERIES)}"
    )
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(main())
