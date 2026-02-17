"""
LLM Enrichment Module

Sends code entities to an LLM for semantic analysis.
Returns structured enrichment data (purpose, patterns, concepts).
Supports caching via content hash to avoid re-enriching unchanged code.
"""

import asyncio
import logging
from typing import Any

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI

from src.shared.llms import get_openai_mini_model
from src.shared.models import FunctionEnrichment, ClassEnrichment
from src.agents.indexer.enrichment_prompts import (
    ENRICHMENT_SYSTEM_PROMPT,
    build_enrichment_prompt,
)

logger = logging.getLogger("indexer-agent.enrichment")


class LLMEnricher:
    """
    Enriches code entities with semantic information via LLM calls.

    Features:
    - Batched async requests for throughput
    - Content hash caching to skip unchanged entities
    - Structured JSON output parsing with retry
    - Enriches nested functions with parent context
    """

    def __init__(
        self,
        model: ChatOpenAI | None = None,
        batch_size: int = 30,
        max_retries: int = 3,
    ):
        base_model = model or get_openai_mini_model()
        self._function_chain = base_model.with_structured_output(
            FunctionEnrichment
        )
        self._class_chain = base_model.with_structured_output(
            ClassEnrichment
        )
        self._batch_size = batch_size
        self._max_retries = max_retries

    async def enrich_entity(
        self,
        entity: dict,
        entity_type: str,
        context: dict | None = None,
    ) -> dict:
        """
        Enrich a single entity with LLM analysis.

        Args:
            entity: Parsed entity dict (from AST parser or reconstructed from graph).
            entity_type: 'function' or 'class'.
            context: Optional context (parent class, callers, callees, parent_function).

        Returns:
            Structured enrichment dict.
        """
        context = context or {}
        prompt = build_enrichment_prompt(entity, entity_type, context)

        for attempt in range(self._max_retries):
            try:
                result = await self._call_structured(prompt, entity_type)
                return result.model_dump()
            except Exception as e:
                logger.warning(
                    f"Enrichment attempt {attempt + 1} failed for "
                    f"{entity.get('qualified_name', '?')}: {e}"
                )
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(1.0 * (attempt + 1))

        # Fallback: return minimal enrichment matching the entity type
        logger.error(f"All enrichment attempts failed for {entity.get('qualified_name')}")
        docstring_snippet = entity.get("docstring", "") or "Unable to enrich"
        if entity_type == "class":
            return ClassEnrichment(
                purpose=docstring_snippet,
                summary="",
                role="other",
            ).model_dump()
        return FunctionEnrichment(
            purpose=docstring_snippet,
            summary="",
            complexity="low",
        ).model_dump()

    async def enrich_all_nodes(self, graph_manager) -> int:
        """
        Enrich all unenriched Function and Class nodes in the graph.

        Uses the enrichment cache to skip entities whose content hasn't changed.
        Enriches nested functions with parent context.
        Returns the number of entities enriched.
        """
        from src.agents.indexer.graph_manager import Neo4jGraphManager

        gm: Neo4jGraphManager = graph_manager
        enriched_count = 0

        # ─── Functions (including methods and nested functions) ───
        functions = await gm._run(
            """
            MATCH (f:Function)
            WHERE f.enrichment_hash IS NULL OR f.enrichment_hash <> f.content_hash
            RETURN f.qualified_name AS qname, f.source AS source,
                   f.content_hash AS content_hash, f.docstring AS docstring,
                   f.is_method AS is_method, f.is_nested AS is_nested,
                   f.is_async AS is_async
            """
        )
        total_functions = len(functions)
        logger.info("Enrichment: %d functions to process", total_functions)

        for i in range(0, total_functions, self._batch_size):
            batch = functions[i : i + self._batch_size]
            tasks = []

            for func in batch:
                # Check cache first
                cached = await gm.get_cached_enrichment(func["content_hash"])
                if cached:
                    await gm.set_enrichment(func["qname"], cached, "function")
                    await gm.create_semantic_edges(func["qname"], cached)
                    enriched_count += 1
                    continue

                # Build context from graph
                context = await self._build_function_context(gm, func)

                # Reconstruct entity dict from graph properties
                entity = {
                    "source": func["source"],
                    "qualified_name": func["qname"],
                    "content_hash": func["content_hash"],
                    "docstring": func.get("docstring", ""),
                    "is_async": func.get("is_async", False),
                }

                # Fetch decorators from graph
                decorators = await gm._run(
                    "MATCH (f:Function {qualified_name: $qname})-[:DECORATED_BY]->(d) RETURN d.name AS name",
                    {"qname": func["qname"]},
                )
                entity["decorators"] = [{"name": d["name"]} for d in decorators]

                # Fetch parameters from graph
                params = await gm._run(
                    """
                    MATCH (f:Function {qualified_name: $qname})-[:HAS_PARAMETER]->(p:Parameter)
                    RETURN p.name AS name, p.type_annotation AS type_annotation,
                           p.default_value AS default_value, p.kind AS kind,
                           p.position AS position
                    ORDER BY p.position
                    """,
                    {"qname": func["qname"]},
                )
                entity["parameters"] = params

                tasks.append(self._enrich_and_store(gm, entity, "function", context))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    logger.error(f"Enrichment task failed: {r}")
                else:
                    enriched_count += 1
            logger.info(
                "Enrichment progress: %d/%d functions done",
                min(i + self._batch_size, total_functions),
                total_functions,
            )

        # ─── Classes ─────────────────────────────────────────────
        classes = await gm._run(
            """
            MATCH (c:Class)
            WHERE c.enrichment_hash IS NULL OR c.enrichment_hash <> c.content_hash
            RETURN c.qualified_name AS qname, c.source AS source,
                   c.content_hash AS content_hash, c.docstring AS docstring
            """
        )
        total_classes = len(classes)
        logger.info("Enrichment: %d classes to process", total_classes)

        for i in range(0, total_classes, self._batch_size):
            batch = classes[i : i + self._batch_size]
            tasks = []

            for cls in batch:
                cached = await gm.get_cached_enrichment(cls["content_hash"])
                if cached:
                    await gm.set_enrichment(cls["qname"], cached, "class")
                    await gm.create_semantic_edges(cls["qname"], cached)
                    enriched_count += 1
                    continue

                # Build rich context for class
                entity = {
                    "source": cls["source"],
                    "qualified_name": cls["qname"],
                    "content_hash": cls["content_hash"],
                    "docstring": cls.get("docstring", ""),
                }

                # Fetch bases
                bases = await gm._run(
                    "MATCH (c:Class {qualified_name: $qname})-[:INHERITS_FROM]->(b) RETURN b.name AS name",
                    {"qname": cls["qname"]},
                )
                entity["bases"] = [b["name"] for b in bases]

                # Fetch methods (names only for prompt context)
                methods = await gm._run(
                    "MATCH (c:Class {qualified_name: $qname})-[:CONTAINS]->(m:Function) RETURN m.name AS name",
                    {"qname": cls["qname"]},
                )
                entity["methods"] = [{"name": m["name"]} for m in methods]

                # Fetch class attributes
                class_attrs = await gm._run(
                    """
                    MATCH (c:Class {qualified_name: $qname})-[:HAS_ATTRIBUTE]->(a:ClassAttribute)
                    RETURN a.name AS name, a.type_annotation AS type_annotation,
                           a.default_value AS default_value
                    """,
                    {"qname": cls["qname"]},
                )
                entity["class_attributes"] = class_attrs

                # Fetch decorators
                decorators = await gm._run(
                    "MATCH (c:Class {qualified_name: $qname})-[:DECORATED_BY]->(d) RETURN d.name AS name",
                    {"qname": cls["qname"]},
                )
                entity["decorators"] = [{"name": d["name"]} for d in decorators]

                tasks.append(self._enrich_and_store(gm, entity, "class", {}))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    logger.error(f"Enrichment task failed: {r}")
                else:
                    enriched_count += 1
            logger.info(
                "Enrichment progress: %d/%d classes done",
                min(i + self._batch_size, total_classes),
                total_classes,
            )

        logger.info(
            "Enrichment complete: %d total entities enriched", enriched_count
        )
        return enriched_count

    async def _build_function_context(self, gm, func: dict) -> dict:
        """Build context dict for a function from the graph."""
        context: dict[str, Any] = {}

        # Callers
        callers = await gm._run(
            "MATCH (c:Function)-[:CALLS]->(f:Function {qualified_name: $qname}) RETURN c.name AS name LIMIT 10",
            {"qname": func["qname"]},
        )
        if callers:
            context["callers"] = [c["name"] for c in callers]

        # Callees
        callees = await gm._run(
            "MATCH (f:Function {qualified_name: $qname})-[:CALLS]->(c:Function) RETURN c.name AS name LIMIT 10",
            {"qname": func["qname"]},
        )
        if callees:
            context["callees"] = [c["name"] for c in callees]

        # Parent class (for methods)
        if func.get("is_method"):
            parent = await gm._run_single(
                "MATCH (c:Class)-[:CONTAINS]->(f:Function {qualified_name: $qname}) RETURN c.qualified_name AS qname, c.name AS name",
                {"qname": func["qname"]},
            )
            if parent:
                context["parent_class"] = parent["qname"]

        # Parent function (for nested functions)
        if func.get("is_nested"):
            parent_fn = await gm._run_single(
                "MATCH (p:Function)-[:CONTAINS]->(f:Function {qualified_name: $qname}) RETURN p.qualified_name AS qname",
                {"qname": func["qname"]},
            )
            if parent_fn:
                context["parent_function"] = parent_fn["qname"]

        return context

    async def _enrich_and_store(
        self, gm, entity: dict, entity_type: str, context: dict
    ) -> None:
        """Enrich an entity and store results in graph + cache."""
        enrichment = await self.enrich_entity(entity, entity_type, context)
        qname = entity["qualified_name"]

        # Delete old semantic edges before creating new ones
        await gm.delete_semantic_edges(qname)

        # Store enrichment on node (entity_type-aware)
        await gm.set_enrichment(qname, enrichment, entity_type)

        # Create semantic edges
        await gm.create_semantic_edges(qname, enrichment)

        # Cache for future use
        content_hash = entity.get("content_hash", "")
        if content_hash:
            await gm.cache_enrichment(content_hash, enrichment)

    async def _call_structured(
        self, prompt: str, entity_type: str,
    ) -> FunctionEnrichment | ClassEnrichment:
        """Invoke the LLM with structured output bound to a Pydantic schema."""
        messages = [
            SystemMessage(content=ENRICHMENT_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]
        chain = self._function_chain if entity_type == "function" else self._class_chain
        return await chain.ainvoke(messages)

    async def close(self) -> None:
        """No-op kept for interface compatibility."""
        pass
