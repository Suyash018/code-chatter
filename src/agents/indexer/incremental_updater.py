"""
Incremental Updater — Strategy B fine-grained diff logic.

Compares new AST parse against existing graph by content hash.
Only updates changed entities, preserving LLM enrichment on unchanged code.
Uses enrichment cache to restore LLM analysis for previously-seen content hashes.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from src.agents.indexer.graph_manager import Neo4jGraphManager
from src.agents.indexer.enrichment import LLMEnricher

logger = logging.getLogger("indexer-agent.incremental")


# ─── Data Structures ────────────────────────────────────────


@dataclass
class EntityDiff:
    """Diff results for a single entity category."""

    added: list[dict] = field(default_factory=list)
    modified: list[dict] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


# ─── Core Diff Logic ────────────────────────────────────────


def _compute_entity_diff(
    existing: dict[str, dict],
    new: dict[str, dict],
) -> EntityDiff:
    """
    Compare existing graph entities against newly parsed entities.

    Args:
        existing: {qualified_name: {"content_hash": ..., ...}} from graph.
        new: {qualified_name: {full parsed dict with "content_hash"}} from AST.

    Returns:
        EntityDiff with added/modified/deleted/unchanged lists.
    """
    diff = EntityDiff()
    existing_qnames = set(existing.keys())
    new_qnames = set(new.keys())

    for qname in existing_qnames - new_qnames:
        diff.deleted.append(qname)

    for qname in new_qnames - existing_qnames:
        diff.added.append(new[qname])

    for qname in existing_qnames & new_qnames:
        old_hash = existing[qname].get("content_hash")
        new_hash = new[qname].get("content_hash")
        if old_hash != new_hash:
            diff.modified.append(new[qname])
        else:
            diff.unchanged.append(qname)

    return diff


def _compute_file_diff(existing: dict, parsed: dict) -> tuple[EntityDiff, EntityDiff]:
    """
    Compute diffs for top-level classes and top-level functions.

    Returns:
        (class_diff, function_diff)
    """
    new_classes = {cls["qualified_name"]: cls for cls in parsed["classes"]}
    new_functions = {func["qualified_name"]: func for func in parsed["functions"]}

    class_diff = _compute_entity_diff(existing["classes"], new_classes)
    func_diff = _compute_entity_diff(existing["functions"], new_functions)

    return class_diff, func_diff


# ─── Modification Handlers ──────────────────────────────────


async def _rebuild_inheritance(gm: Neo4jGraphManager, cls: dict) -> None:
    """Delete and recreate INHERITS_FROM edges for a modified class."""
    qname = cls["qualified_name"]
    await gm._write(
        "MATCH (c:Class {qualified_name: $qname})-[r:INHERITS_FROM]->() DELETE r",
        {"qname": qname},
    )
    for base in cls.get("bases", []):
        await gm._write(
            """
            MATCH (c:Class {qualified_name: $qname})
            MERGE (base:Class {name: $base_name})
            ON CREATE SET base.qualified_name = $base_name,
                         base._unresolved = true
            MERGE (c)-[:INHERITS_FROM]->(base)
            """,
            {"qname": qname, "base_name": base},
        )


async def _update_modified_function(
    gm: Neo4jGraphManager,
    file_path: str,
    func: dict,
    existing_nested: dict[str, dict],
    changed_functions: list[dict],
) -> None:
    """
    Update a modified function: properties, decorators, parameters,
    and sub-diff its nested functions.
    """
    # Avoid circular import at module level — import lazily
    from src.agents.indexer.server import _store_function

    qname = func["qualified_name"]

    # Update node properties
    await gm.update_function_node(func)

    # Rebuild decorators
    await gm.delete_decorator_edges(qname)
    for dec in func.get("decorators", []):
        await gm.create_decorator_edge(qname, dec, "Function")

    # Rebuild parameters (CREATE-based, must delete first)
    await gm.delete_parameters(qname)
    for param in func.get("parameters", []):
        await gm.create_parameter_node(qname, param)

    changed_functions.append(func)

    # Sub-diff nested functions
    my_nested_existing = {
        nq: data
        for nq, data in existing_nested.items()
        if nq.startswith(qname + ".")
    }
    new_nested = {n["qualified_name"]: n for n in func.get("nested_functions", [])}
    nested_diff = _compute_entity_diff(my_nested_existing, new_nested)

    for nq in nested_diff.deleted:
        await gm.delete_function_node(nq)

    for nested in nested_diff.added:
        await _store_function(gm, file_path, nested, parent_function=qname)
        changed_functions.append(nested)

    for nested in nested_diff.modified:
        await gm.update_function_node(nested)
        nq = nested["qualified_name"]
        await gm.delete_decorator_edges(nq)
        for dec in nested.get("decorators", []):
            await gm.create_decorator_edge(nq, dec, "Function")
        await gm.delete_parameters(nq)
        for param in nested.get("parameters", []):
            await gm.create_parameter_node(nq, param)
        changed_functions.append(nested)


async def _update_modified_class(
    gm: Neo4jGraphManager,
    file_path: str,
    cls: dict,
    existing: dict,
    changed_functions: list[dict],
) -> None:
    """
    Update a modified class: properties, decorators, inheritance,
    class attributes, and sub-diff its methods.
    """
    from src.agents.indexer.server import _store_function

    qname = cls["qualified_name"]

    # Update class properties
    await gm.update_class_node(cls)

    # Rebuild decorators
    await gm.delete_decorator_edges(qname)
    for dec in cls.get("decorators", []):
        await gm.create_decorator_edge(qname, dec, "Class")

    # Rebuild inheritance edges
    await _rebuild_inheritance(gm, cls)

    # Rebuild class attributes (CREATE-based)
    await gm.delete_class_attributes(qname)
    for attr in cls.get("class_attributes", []):
        await gm.create_class_attribute_node(qname, attr)

    # Sub-diff methods within this class
    class_methods_existing = {
        mq: data
        for mq, data in existing["methods"].items()
        if data.get("class_name") == cls["name"]
    }
    new_methods = {m["qualified_name"]: m for m in cls.get("methods", [])}
    method_diff = _compute_entity_diff(class_methods_existing, new_methods)

    # Deleted methods
    for mq in method_diff.deleted:
        await gm.delete_function_node(mq)

    # Added methods
    for method in method_diff.added:
        await _store_function(gm, file_path, method, parent_class=cls["name"])
        changed_functions.append(method)
        for nested in method.get("nested_functions", []):
            changed_functions.append(nested)

    # Modified methods — delegate to function updater
    for method in method_diff.modified:
        await _update_modified_function(
            gm, file_path, method, existing["nested_functions"], changed_functions,
        )

    # Unchanged methods — no-op


# ─── Enrichment ─────────────────────────────────────────────


async def _enrich_entity_incremental(
    gm: Neo4jGraphManager,
    enricher: LLMEnricher,
    entity: dict,
    entity_type: str,
) -> str:
    """
    Enrich a single entity, checking enrichment cache first.

    Returns:
        "cached" if restored from cache, "computed" if fresh LLM call.
    """
    qname = entity["qualified_name"]
    content_hash = entity.get("content_hash", "")

    # Check cache by content hash
    if content_hash:
        cached = await gm.get_cached_enrichment(content_hash)
        if cached:
            await gm.delete_semantic_edges(qname)
            await gm.set_enrichment(qname, cached, entity_type)
            await gm.create_semantic_edges(qname, cached)
            return "cached"

    # Cache miss — make LLM call
    context = {}
    if entity_type == "function":
        func_info = {
            "qname": qname,
            "is_method": entity.get("is_method", False),
            "is_nested": entity.get("is_nested", False),
        }
        context = await enricher._build_function_context(gm, func_info)

    enrichment = await enricher.enrich_entity(entity, entity_type, context)

    await gm.delete_semantic_edges(qname)
    await gm.set_enrichment(qname, enrichment, entity_type)
    await gm.create_semantic_edges(qname, enrichment)

    if content_hash:
        await gm.cache_enrichment(content_hash, enrichment)

    return "computed"


# ─── Main Entry Point ───────────────────────────────────────


async def incremental_update_file(
    gm: Neo4jGraphManager,
    enricher: LLMEnricher | None,
    file_path: str,
    parsed: dict,
    skip_enrichment: bool = False,
) -> dict:
    """
    Perform fine-grained incremental update for a single file.

    Compares the new AST parse result against the existing graph state.
    Only touches changed entities, preserving LLM enrichment on unchanged code.

    Args:
        gm: The graph manager instance.
        enricher: The LLM enricher instance (used for modified/added entities).
        file_path: Relative path of the file being updated.
        parsed: Output of parser.parse_file() for this file.
        skip_enrichment: If True, skip LLM enrichment for changed entities.

    Returns:
        Stats dict with counts for each change category.
    """
    from src.agents.indexer.server import _store_function

    stats: dict[str, int] = defaultdict(int)

    # ── Phase 1: Compute diff ────────────────────────────────
    logger.info("Computing diff for %s", file_path)
    existing = await gm.get_file_entities(file_path)
    class_diff, func_diff = _compute_file_diff(existing, parsed)

    logger.info(
        "Diff result — classes: +%d ~%d -%d =%d | functions: +%d ~%d -%d =%d",
        len(class_diff.added), len(class_diff.modified),
        len(class_diff.deleted), len(class_diff.unchanged),
        len(func_diff.added), len(func_diff.modified),
        len(func_diff.deleted), len(func_diff.unchanged),
    )

    # ── Phase 2: Apply changes ───────────────────────────────

    # 2.0 Update file node (MERGE, idempotent)
    await gm.create_file_node(file_path, parsed["file_hash"])

    # 2.1 Deletions first
    for qname in class_diff.deleted:
        logger.info("Deleting class: %s", qname)
        await gm.delete_class_node(qname)
        stats["deleted_classes"] += 1

    for qname in func_diff.deleted:
        logger.info("Deleting function: %s", qname)
        await gm.delete_function_node(qname)
        stats["deleted_functions"] += 1

    # Collect all changed functions for call re-resolution + enrichment
    all_changed_functions: list[dict] = []
    all_changed_classes: list[dict] = []

    # 2.2 Additions
    for cls in class_diff.added:
        logger.info("Adding class: %s", cls["qualified_name"])
        await gm.create_class_node(file_path, cls)
        for attr in cls.get("class_attributes", []):
            await gm.create_class_attribute_node(cls["qualified_name"], attr)
        for method in cls.get("methods", []):
            await _store_function(gm, file_path, method, parent_class=cls["name"])
            all_changed_functions.append(method)
            for nested in method.get("nested_functions", []):
                all_changed_functions.append(nested)
        all_changed_classes.append(cls)
        stats["added_classes"] += 1

    for func in func_diff.added:
        logger.info("Adding function: %s", func["qualified_name"])
        await _store_function(gm, file_path, func)
        all_changed_functions.append(func)
        for nested in func.get("nested_functions", []):
            all_changed_functions.append(nested)
        stats["added_functions"] += 1

    # 2.3 Modifications
    for cls in class_diff.modified:
        logger.info("Modifying class: %s", cls["qualified_name"])
        await _update_modified_class(
            gm, file_path, cls, existing, all_changed_functions,
        )
        all_changed_classes.append(cls)
        stats["modified_classes"] += 1

    for func in func_diff.modified:
        logger.info("Modifying function: %s", func["qualified_name"])
        await _update_modified_function(
            gm, file_path, func, existing["nested_functions"],
            all_changed_functions,
        )
        stats["modified_functions"] += 1

    stats["unchanged_classes"] = len(class_diff.unchanged)
    stats["unchanged_functions"] = len(func_diff.unchanged)

    # ── Phase 3: Post-processing ─────────────────────────────

    # 3.1 Always rebuild imports (changes affect call resolution globally)
    await gm.delete_imports_for_file(file_path)
    for imp in parsed["imports"]:
        await gm.create_import_edge(file_path, imp)
    stats["imports_rebuilt"] = len(parsed["imports"])

    # 3.2 Re-resolve calls for added + modified functions
    for func_dict in all_changed_functions:
        calls = func_dict.get("calls", [])
        await gm.resolve_calls_for_function(func_dict["qualified_name"], calls)

    # 3.3 Enrichment for changed entities
    if not skip_enrichment and enricher is not None:
        # Enrich changed classes
        for cls in all_changed_classes:
            try:
                result = await _enrich_entity_incremental(gm, enricher, cls, "class")
                stats[f"enrichment_{result}"] += 1
            except Exception as e:
                logger.error("Enrichment failed for class %s: %s", cls["qualified_name"], e)

        # Enrich changed functions (methods, nested, top-level)
        for func_dict in all_changed_functions:
            try:
                result = await _enrich_entity_incremental(gm, enricher, func_dict, "function")
                stats[f"enrichment_{result}"] += 1
            except Exception as e:
                logger.error("Enrichment failed for function %s: %s", func_dict["qualified_name"], e)
    else:
        stats["enrichment_skipped"] = len(all_changed_classes) + len(all_changed_functions)

    logger.info(
        "Incremental update complete for %s — %s",
        file_path, dict(stats),
    )
    return dict(stats)
