"""
Indexer Agent — MCP Server #2

Full indexing pipeline for the Graphical RAG system.
Orchestrates: clone -> discover -> parse -> store -> resolve -> enrich -> embed

MCP Tools:
  - index_repository: Full repository indexing
  - index_file: Single file indexing (incremental)
  - parse_python_ast: Extract AST from Python code
  - extract_entities: Identify code entities and relationships
  - get_index_status: Report indexing progress and statistics
"""

import asyncio
import logging
import sys
import time

from src.shared.database import Neo4jHandler
from src.shared.llms import get_openai_embeddings
from src.agents.indexer.ast_parser import PythonASTParser
from src.agents.indexer.graph_manager import Neo4jGraphManager
from src.agents.indexer.repository import RepositoryManager
from src.agents.indexer.enrichment import LLMEnricher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-30s  %(levelname)-7s  %(message)s",
)
logger = logging.getLogger("indexer-pipeline")

REPO_URL = "https://github.com/tiangolo/fastapi.git"
REPO_BRANCH = "master"


async def _store_function(
    gm: Neo4jGraphManager,
    file_path: str,
    func: dict,
    parent_class: str | None = None,
    parent_function: str | None = None,
) -> int:
    """
    Store a function/method node plus its decorators, parameters, and nested
    functions.  Returns the count of Function nodes created (including nested).
    """
    count = 1

    await gm.create_function_node(
        file_path, func,
        parent_class=parent_class,
        parent_function=parent_function,
    )

    for dec in func.get("decorators", []):
        await gm.create_decorator_edge(func["qualified_name"], dec, "Function")

    for param in func.get("parameters", []):
        await gm.create_parameter_node(func["qualified_name"], param)

    for nested in func.get("nested_functions", []):
        count += await _store_function(
            gm, file_path, nested,
            parent_function=func["qualified_name"],
        )

    return count


async def _store_file(
    gm: Neo4jGraphManager,
    parser: PythonASTParser,
    repo_mgr: RepositoryManager,
    repo_path,
    file_path: str,
) -> dict:
    """
    Read, parse, and store all entities from a single file.

    Returns a stats dict with counts or a parse_error key.
    """
    source = await repo_mgr.read_file(repo_path, file_path)
    parsed = parser.parse_file(source, file_path)

    if "parse_error" in parsed:
        return {"parse_error": parsed["parse_error"]}

    # File node
    await gm.create_file_node(file_path, parsed["file_hash"])

    class_count = 0
    func_count = 0

    # ── Classes ──────────────────────────────────────────────
    for cls in parsed["classes"]:
        await gm.create_class_node(file_path, cls)
        class_count += 1

        # Class attributes
        for attr in cls.get("class_attributes", []):
            await gm.create_class_attribute_node(cls["qualified_name"], attr)

        # Methods (and their nested functions)
        for method in cls.get("methods", []):
            func_count += await _store_function(
                gm, file_path, method, parent_class=cls["name"],
            )

    # ── Top-level functions ──────────────────────────────────
    for func in parsed["functions"]:
        func_count += await _store_function(gm, file_path, func)

    # ── Import edges ─────────────────────────────────────────
    for imp in parsed["imports"]:
        await gm.create_import_edge(file_path, imp)

    return {
        "classes": class_count,
        "functions": func_count,
        "imports": len(parsed["imports"]),
        "calls": len(parsed["calls"]),
    }


async def index_repository(
    repo_url: str = REPO_URL,
    branch: str = REPO_BRANCH,
    skip_enrichment: bool = False,
    clear_graph: bool = True,
    max_workers: int = 10,
) -> dict:
    """
    Full bootstrap index of a repository.

    Steps:
      0. Clear existing graph (Parameter/ClassAttribute nodes use CREATE,
         so a re-run without clearing would duplicate them)
      1. Clone (or update) the repo
      2. Discover all Python files
      3. Parse each file and store entities in Neo4j
      4. Resolve cross-file relationships (CALLS, INHERITS_FROM)
      5. Run LLM enrichment (unless skipped)
      6. Generate vector embeddings (unless skipped)

    Returns:
        Summary dict with counts.
    """
    parser = PythonASTParser()

    async with Neo4jHandler() as handler:
        gm = Neo4jGraphManager(handler)

        # ── Step 0: Ensure schema + clear existing graph ─────
        await gm.ensure_schema()
        if clear_graph:
            logger.info("Clearing existing graph for full re-index...")
            await gm.clear_all()
            time.sleep(10)

        # ── Steps 1-3: Clone, discover, parse (temp dir cleaned up after) ──
        with RepositoryManager() as repo_mgr:
            # ── Step 1: Clone ────────────────────────────────
            logger.info("Cloning repository: %s (branch: %s)", repo_url, branch)
            repo_path = await repo_mgr.clone(repo_url, branch)
            commit_hash = await repo_mgr.get_head_commit(repo_path)
            logger.info("HEAD commit: %s", commit_hash)

            # ── Step 2: Discover Python files ────────────────
            files = await repo_mgr.discover_python_files(repo_path)
            logger.info("Discovered %d Python files", len(files))

            # ── Step 3: Parse + Store (concurrent) ─────────────
            total_classes = 0
            total_functions = 0
            total_imports = 0
            total_calls = 0
            parse_errors = 0

            semaphore = asyncio.Semaphore(max_workers)
            progress = {"done": 0}

            async def _process_one(file_path: str) -> tuple[str, dict | None]:
                async with semaphore:
                    progress["done"] += 1
                    logger.info(
                        "[%d/%d] Processing %s",
                        progress["done"], len(files), file_path,
                    )
                    try:
                        stats = await _store_file(
                            gm, parser, repo_mgr, repo_path, file_path,
                        )
                    except Exception as e:
                        logger.warning("Failed to process %s: %s", file_path, e)
                        return file_path, None
                    return file_path, stats

            results = await asyncio.gather(
                *(_process_one(fp) for fp in files),
            )

            for file_path, stats in results:
                if stats is None:
                    parse_errors += 1
                    continue
                if "parse_error" in stats:
                    logger.warning(
                        "Parse error in %s: %s", file_path, stats["parse_error"],
                    )
                    parse_errors += 1
                    continue
                total_classes += stats["classes"]
                total_functions += stats["functions"]
                total_imports += stats["imports"]
                total_calls += stats["calls"]

        logger.info("Temporary clone directory cleaned up")

        # ── Step 4: Resolve cross-file relationships ─────────
        logger.info("Resolving cross-file relationships...")
        resolved = await gm.resolve_all_relationships()

        await gm.update_index_state(
            repo_url=repo_url,
            branch=branch,
            commit_hash=commit_hash,
            files_indexed=len(files),
            status="indexed",
        )

        # ── Step 5: Enrichment ───────────────────────────────
        enriched_count = 0
        if not skip_enrichment:
            logger.info("Starting LLM enrichment...")
            enricher = LLMEnricher()
            enriched_count = await enricher.enrich_all_nodes(gm)
            logger.info("Enriched %d entities", enriched_count)
            await gm.update_index_state(status="enriched")
        else:
            logger.info("Skipping LLM enrichment (--skip-enrichment)")

        # ── Step 6: Vector embeddings ────────────────────────
        embedded_count = 0
        if not skip_enrichment:
            logger.info("Generating vector embeddings...")
            embeddings_model = get_openai_embeddings()
            embedded_count = await gm.create_all_embeddings(embeddings_model)
            logger.info("Embedded %d entities", embedded_count)
            await gm.update_index_state(status="embedded")
        else:
            logger.info("Skipping embeddings (--skip-enrichment)")

        # ── Summary ──────────────────────────────────────────
        node_counts = await gm.get_node_counts()
        edge_counts = await gm.get_edge_counts()

        summary = {
            "files": len(files),
            "classes": total_classes,
            "functions": total_functions,
            "imports": total_imports,
            "calls_raw": total_calls,
            "calls_resolved": resolved,
            "enriched": enriched_count,
            "embedded": embedded_count,
            "parse_errors": parse_errors,
            "node_counts": node_counts,
            "edge_counts": edge_counts,
        }

        logger.info("=" * 50)
        logger.info("INDEXING COMPLETE")
        logger.info("=" * 50)
        logger.info(
            "Files: %d | Classes: %d | Functions: %d",
            len(files), total_classes, total_functions,
        )
        logger.info(
            "Imports: %d | Calls resolved: %d | Enriched: %d | Embedded: %d",
            total_imports, resolved, enriched_count, embedded_count,
        )
        if parse_errors:
            logger.warning("Parse errors: %d", parse_errors)
        logger.info("Node counts: %s", node_counts)
        logger.info("Edge counts: %s", edge_counts)

        return summary


if __name__ == "__main__":
    asyncio.run(index_repository(skip_enrichment=False, clear_graph=True))
