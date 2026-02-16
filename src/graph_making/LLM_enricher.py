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

from src.LLMS import get_openai_model
from src.models import FunctionEnrichment, ClassEnrichment

logger = logging.getLogger("indexer-agent.enrichment")

ENRICHMENT_SYSTEM_PROMPT = """\
You are a code analysis expert. Given a Python code entity (function or class) \
with its context, produce a structured analysis.

Valid design patterns: singleton, factory, builder, observer, decorator, \
strategy, template_method, dependency_injection, middleware, mixin, \
registry, facade, adapter, proxy, chain_of_responsibility, command.

Valid domain concepts: routing, validation, middleware, authentication, \
authorization, dependency_injection, serialization, error_handling, \
request_processing, response_building, websocket, cors, testing, \
configuration, lifecycle, openapi, documentation.
"""


def _build_enrichment_prompt(entity: dict, entity_type: str, context: dict) -> str:
    """
    Build the prompt for enriching a single entity.

    Includes all available structural context so the LLM can make
    informed semantic judgments.
    """
    parts = [f"Analyze this Python {entity_type}:\n"]

    # Source code (always present — includes decorators since parser fix)
    parts.append(f"```python\n{entity.get('source', '')}\n```\n")

    # Async flag
    if entity.get("is_async"):
        parts.append("This is an async function.\n")

    # Docstring (may already be in source, but highlight it)
    if entity.get("docstring"):
        parts.append(f"Docstring: {entity['docstring']}\n")

    # Decorators
    if entity.get("decorators"):
        dec_strs = []
        for d in entity["decorators"]:
            s = d["name"]
            if d.get("arguments"):
                s += f"({d['arguments']})"
            dec_strs.append(s)
        parts.append(f"Decorators: {', '.join(dec_strs)}\n")

    # Parameters with types (for functions)
    if entity.get("parameters"):
        param_strs = []
        for p in entity["parameters"]:
            s = p["name"]
            if p.get("type_annotation"):
                s += f": {p['type_annotation']}"
            if p.get("default_value"):
                s += f" = {p['default_value']}"
            kind = p.get("kind", "")
            if kind and kind not in ("positional_or_keyword",):
                s += f"  [{kind}]"
            param_strs.append(s)
        parts.append(f"Parameters: {', '.join(param_strs)}\n")

    # Base classes (for classes)
    if entity.get("bases"):
        parts.append(f"Inherits from: {', '.join(entity['bases'])}\n")

    # Class attributes (for classes)
    if entity.get("class_attributes"):
        attr_strs = []
        for attr in entity["class_attributes"][:20]:  # cap at 20 for prompt size
            s = attr["name"]
            if attr.get("type_annotation"):
                s += f": {attr['type_annotation']}"
            if attr.get("default_value"):
                s += f" = {attr['default_value']}"
            attr_strs.append(s)
        parts.append(f"Class attributes: {', '.join(attr_strs)}\n")

    # Methods list (for classes — names only, source is too large)
    if entity.get("methods"):
        method_names = [m["name"] for m in entity["methods"]]
        parts.append(f"Methods ({len(method_names)}): {', '.join(method_names)}\n")

    # Nested functions (names only)
    if entity.get("nested_functions"):
        nested_names = [n["name"] for n in entity["nested_functions"]]
        parts.append(f"Nested functions: {', '.join(nested_names)}\n")

    # Context: parent class (for methods)
    if context.get("parent_class"):
        parts.append(f"This is a method of class: {context['parent_class']}\n")

    # Context: parent function (for nested functions)
    if context.get("parent_function"):
        parts.append(f"This is a nested function inside: {context['parent_function']}\n")

    # Calls made by this entity
    calls = entity.get("calls") or context.get("callees", [])
    if calls:
        # calls may be list of strings or list of dicts
        call_names = []
        for c in calls[:15]:
            if isinstance(c, dict):
                call_names.append(c.get("callee", c.get("name", "")))
            else:
                call_names.append(str(c))
        parts.append(f"Calls: {', '.join(call_names)}\n")

    # Context: callers (who calls this entity)
    if context.get("callers"):
        parts.append(f"Called by: {', '.join(context['callers'][:10])}\n")

    return "\n".join(parts)


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
        batch_size: int = 10,
        max_retries: int = 3,
    ):
        base_model = model or get_openai_model()
        self._function_chain = base_model.with_structured_output(FunctionEnrichment)
        self._class_chain = base_model.with_structured_output(ClassEnrichment)
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
        prompt = _build_enrichment_prompt(entity, entity_type, context)

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

        # Fallback: return minimal enrichment
        logger.error(f"All enrichment attempts failed for {entity.get('qualified_name')}")
        return FunctionEnrichment(
            purpose=entity.get("docstring", "")[:200] or "Unable to enrich",
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
        from src.graph_making.graph_manager import Neo4jGraphManager

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

        for i in range(0, len(functions), self._batch_size):
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

        # ─── Classes ─────────────────────────────────────────────
        classes = await gm._run(
            """
            MATCH (c:Class)
            WHERE c.enrichment_hash IS NULL OR c.enrichment_hash <> c.content_hash
            RETURN c.qualified_name AS qname, c.source AS source,
                   c.content_hash AS content_hash, c.docstring AS docstring
            """
        )

        for i in range(0, len(classes), self._batch_size):
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