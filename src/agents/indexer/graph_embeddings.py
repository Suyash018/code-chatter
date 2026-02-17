"""
Graph Embedding Operations

Vector embedding generation and storage for semantic search
across Function and Class nodes.
"""

import logging

logger = logging.getLogger("indexer-agent.graph_manager")


def _build_embedding_text(node: dict) -> str:
    """Build a text representation of a graph node for vector embedding.

    Combines the most semantically meaningful properties so that
    similarity search finds nodes by meaning, not just name.
    """
    parts = []

    label = node.get("label", "Entity")
    name = node.get("name", "")
    parts.append(f"{label}: {name}")

    if node.get("purpose"):
        parts.append(f"Purpose: {node['purpose']}")

    if node.get("summary"):
        parts.append(f"Summary: {node['summary']}")

    if node.get("docstring"):
        parts.append(f"Docstring: {node['docstring'][:500]}")

    concepts = node.get("domain_concepts")
    if concepts:
        if isinstance(concepts, list):
            parts.append(f"Concepts: {', '.join(concepts)}")
        else:
            parts.append(f"Concepts: {concepts}")

    return "\n".join(parts)


class EmbeddingOperationsMixin:
    """Mixin providing vector embedding management for the graph manager."""

    # ─── Embeddings ────────────────────────────────────────

    async def set_embedding(self, qualified_name: str, embedding: list[float]) -> None:
        """Store vector embedding on a node."""
        await self._write(
            """
            MATCH (n {qualified_name: $qname})
            SET n.embedding = $embedding
            """,
            {"qname": qualified_name, "embedding": embedding},
        )

    async def create_all_embeddings(self, embeddings_model, batch_size: int = 50) -> int:
        """
        Generate and store vector embeddings for all Function and Class nodes.

        Uses enrichment properties (purpose, summary) when available,
        falling back to docstring and name.

        Args:
            embeddings_model: LangChain embeddings model with aembed_documents().
            batch_size: Number of texts to embed per API call.

        Returns:
            Number of nodes embedded.
        """
        nodes = await self._run(
            """
            MATCH (n)
            WHERE (n:Function OR n:Class) AND n.qualified_name IS NOT NULL
            RETURN n.qualified_name AS qname,
                   n.name AS name,
                   n.docstring AS docstring,
                   n.purpose AS purpose,
                   n.summary AS summary,
                   n.domain_concepts AS domain_concepts,
                   labels(n)[0] AS label
            """
        )

        if not nodes:
            logger.info("No nodes to embed")
            return 0

        logger.info("Generating embeddings for %d nodes...", len(nodes))
        embedded_count = 0

        for i in range(0, len(nodes), batch_size):
            batch = nodes[i : i + batch_size]

            texts = []
            qnames = []
            for node in batch:
                texts.append(_build_embedding_text(node))
                qnames.append(node["qname"])

            try:
                vectors = await embeddings_model.aembed_documents(texts)
            except Exception as e:
                logger.error("Embedding batch %d failed: %s", i // batch_size, e)
                continue

            for qname, vector in zip(qnames, vectors):
                await self.set_embedding(qname, vector)
                embedded_count += 1

            logger.info(
                "Embedded %d/%d nodes",
                min(i + batch_size, len(nodes)),
                len(nodes),
            )

        logger.info("Embedding complete: %d nodes", embedded_count)
        return embedded_count
