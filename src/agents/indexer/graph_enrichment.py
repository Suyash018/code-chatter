"""
Graph Enrichment Operations

Storage of LLM enrichment data, semantic edges (design patterns,
domain concepts, collaborators, data flow), and enrichment caching.
"""

import json as _json
import logging

logger = logging.getLogger("indexer-agent.graph_manager")


class EnrichmentOperationsMixin:
    """Mixin providing enrichment storage and caching for the graph manager."""

    # ─── Enrichment ────────────────────────────────────────

    async def set_enrichment(
        self, qualified_name: str, enrichment: dict, entity_type: str = "function"
    ) -> None:
        """
        Store LLM enrichment on a node.

        Stores common fields (purpose, summary, patterns, complexity, concepts)
        plus entity-type-specific fields:
        - function: side_effects, parameters_explained
        - class: role, key_methods
        """
        # Common fields
        await self._write(
            """
            MATCH (n {qualified_name: $qname})
            SET n.purpose = $purpose,
                n.summary = $summary,
                n.design_patterns = $patterns,
                n.complexity = $complexity,
                n.domain_concepts = $concepts,
                n.enriched_at = datetime(),
                n.enrichment_hash = n.content_hash
            """,
            {
                "qname": qualified_name,
                "purpose": enrichment.get("purpose", ""),
                "summary": enrichment.get("summary", ""),
                "patterns": enrichment.get("design_patterns", []),
                "complexity": enrichment.get("complexity", "unknown"),
                "concepts": enrichment.get("domain_concepts", []),
            },
        )

        # Entity-type-specific fields
        if entity_type == "function":
            await self._write(
                """
                MATCH (n:Function {qualified_name: $qname})
                SET n.side_effects = $side_effects,
                    n.parameters_explained = $params_explained
                """,
                {
                    "qname": qualified_name,
                    "side_effects": enrichment.get("side_effects", []),
                    "params_explained": _json.dumps(
                        {
                            p["name"]: p["explanation"]
                            for p in enrichment.get("parameters_explained", [])
                        }
                        if isinstance(enrichment.get("parameters_explained"), list)
                        else enrichment.get("parameters_explained", {})
                    ),
                },
            )
        elif entity_type == "class":
            await self._write(
                """
                MATCH (n:Class {qualified_name: $qname})
                SET n.role = $role,
                    n.key_methods = $key_methods
                """,
                {
                    "qname": qualified_name,
                    "role": enrichment.get("role", ""),
                    "key_methods": enrichment.get("key_methods", []),
                },
            )

    async def create_semantic_edges(self, qualified_name: str, enrichment: dict) -> None:
        """Create semantic edges based on LLM enrichment output."""
        # Design pattern nodes
        for pattern in enrichment.get("design_patterns", []):
            await self._write(
                """
                MATCH (n {qualified_name: $qname})
                MERGE (p:DesignPattern {name: $pattern})
                MERGE (n)-[:IMPLEMENTS_PATTERN]->(p)
                """,
                {"qname": qualified_name, "pattern": pattern},
            )

        # Domain concept nodes
        for concept in enrichment.get("domain_concepts", []):
            await self._write(
                """
                MATCH (n {qualified_name: $qname})
                MERGE (c:DomainConcept {name: $concept})
                MERGE (n)-[:RELATES_TO_CONCEPT]->(c)
                """,
                {"qname": qualified_name, "concept": concept},
            )

        # Collaborators (class-level)
        for collab in enrichment.get("collaborators", []):
            await self._write(
                """
                MATCH (n {qualified_name: $qname})
                MATCH (c:Class {name: $collab_name})
                WHERE n <> c
                MERGE (n)-[:COLLABORATES_WITH]->(c)
                """,
                {"qname": qualified_name, "collab_name": collab},
            )

        # Data flow edges (from Paper 3 — data-flow awareness)
        for target in enrichment.get("data_flows_to", []):
            await self._write(
                """
                MATCH (n {qualified_name: $qname})
                MATCH (t)
                WHERE (t:Function OR t:Class) AND t.name = $target_name AND n <> t
                MERGE (n)-[:DATA_FLOWS_TO]->(t)
                """,
                {"qname": qualified_name, "target_name": target},
            )

    async def delete_semantic_edges(self, qualified_name: str) -> None:
        """Delete all semantic edges for a node before re-enrichment."""
        await self._write(
            """
            MATCH (n {qualified_name: $qname})-[r]->()
            WHERE type(r) IN ['IMPLEMENTS_PATTERN', 'RELATES_TO_CONCEPT',
                              'COLLABORATES_WITH', 'DATA_FLOWS_TO']
            DELETE r
            """,
            {"qname": qualified_name},
        )

    # ─── Enrichment Cache ──────────────────────────────────

    async def get_cached_enrichment(self, content_hash: str) -> dict | None:
        """Look up enrichment from cache by content hash."""
        result = await self._run_single(
            "MATCH (c:EnrichmentCache {content_hash: $hash}) RETURN c.enrichment_json as data",
            {"hash": content_hash},
        )
        if result and result.get("data"):
            return _json.loads(result["data"])
        return None

    async def cache_enrichment(self, content_hash: str, enrichment: dict) -> None:
        """Store enrichment in cache."""
        await self._write(
            """
            MERGE (c:EnrichmentCache {content_hash: $hash})
            SET c.enrichment_json = $data,
                c.cached_at = datetime()
            """,
            {"hash": content_hash, "data": _json.dumps(enrichment)},
        )
