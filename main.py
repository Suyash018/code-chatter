import asyncio

from src.database import Neo4jHandler
from src.graph_making.graph_manager import Neo4jGraphManager


async def main():
    async with Neo4jHandler() as handler:
        gm = Neo4jGraphManager(handler)

        await gm.ensure_schema()

        counts = await gm.get_node_counts()
        print("Node counts:", counts)


if __name__ == "__main__":
    asyncio.run(main())
