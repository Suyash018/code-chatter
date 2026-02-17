"""
Indexer Agent — MCP Server #2

Handles repository parsing and knowledge graph population.
All tools (except get_index_status) run in the background and return
a job_id immediately.  Use get_index_status(job_id) to poll progress.

MCP Tools:
  - index_repository: Full repository indexing
  - index_file: Single file indexing (incremental Strategy B)
  - parse_python_ast: Extract AST from Python code
  - extract_entities: Identify code entities and relationships
  - get_index_status: Report job progress and graph statistics

Run as:  python -m src.agents.indexer.server        (stdio transport)
"""

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

from src.shared.database import Neo4jHandler
from src.shared.llms import get_openai_embeddings
from src.shared.logging import setup_logging
from src.agents.indexer.ast_parser import PythonASTParser
from src.agents.indexer.graph_manager import Neo4jGraphManager
from src.agents.indexer.repository import RepositoryManager
from src.agents.indexer.enrichment import LLMEnricher
from src.agents.indexer.incremental_updater import incremental_update_file

logger = setup_logging("indexer", level="INFO")

REPO_URL = "https://github.com/tiangolo/fastapi.git"
REPO_BRANCH = "master"


# ─── Job Management ──────────────────────────────────────────


@dataclass
class Job:
    """Tracks a background tool execution."""

    job_id: str
    tool_name: str
    status: str = "pending"           # pending -> running -> completed | failed
    progress: str = ""
    result: dict | None = None
    error: str | None = None
    created_at: str = ""
    completed_at: str | None = None


_jobs: dict[str, Job] = {}


def _create_job(tool_name: str) -> Job:
    """Create and register a new background job."""
    job = Job(
        job_id=uuid.uuid4().hex[:12],
        tool_name=tool_name,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    _jobs[job.job_id] = job
    return job


def _job_to_dict(job: Job) -> dict:
    """Serialize a Job for JSON output."""
    d = {
        "job_id": job.job_id,
        "tool_name": job.tool_name,
        "status": job.status,
        "progress": job.progress,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
    }
    if job.result is not None:
        d["result"] = job.result
    if job.error is not None:
        d["error"] = job.error
    return d


# ─── FastMCP setup ───────────────────────────────────────────


mcp = FastMCP("Indexer")


# ─── Shared resources (lazy init) ────────────────────────────


_handler: Neo4jHandler | None = None
_gm: Neo4jGraphManager | None = None
_parser: PythonASTParser | None = None


async def _get_graph_manager() -> Neo4jGraphManager:
    """Lazy-initialise the Neo4j handler and graph manager on first use."""
    global _handler, _gm
    if _gm is None:
        _handler = Neo4jHandler()
        await _handler.connect()
        _gm = Neo4jGraphManager(_handler)
        await _gm.ensure_schema()
    return _gm


def _get_parser() -> PythonASTParser:
    """Lazy-initialise the AST parser."""
    global _parser
    if _parser is None:
        _parser = PythonASTParser()
    return _parser


# ─── Internal helpers (kept for incremental_updater.py) ──────


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

    NOTE: incremental_updater.py imports this function via lazy import.
    Do NOT rename or move it.
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

    await gm.create_file_node(file_path, parsed["file_hash"])

    class_count = 0
    func_count = 0

    for cls in parsed["classes"]:
        await gm.create_class_node(file_path, cls)
        class_count += 1

        for attr in cls.get("class_attributes", []):
            await gm.create_class_attribute_node(cls["qualified_name"], attr)

        for method in cls.get("methods", []):
            func_count += await _store_function(
                gm, file_path, method, parent_class=cls["name"],
            )

    for func in parsed["functions"]:
        func_count += await _store_function(gm, file_path, func)

    for imp in parsed["imports"]:
        await gm.create_import_edge(file_path, imp)

    return {
        "classes": class_count,
        "functions": func_count,
        "imports": len(parsed["imports"]),
        "calls": len(parsed["calls"]),
    }


# ─── Background workers ─────────────────────────────────────


async def _run_index_repository_job(
    job: Job,
    repo_url: str,
    branch: str,
    skip_enrichment: bool,
    clear_graph: bool,
    max_workers: int,
) -> None:
    """Execute the full indexing pipeline, updating job progress along the way."""
    try:
        job.status = "running"
        parser = _get_parser()
        gm = await _get_graph_manager()

        # Step 0: Clear
        if clear_graph:
            job.progress = "Clearing existing graph..."
            logger.info("Clearing existing graph for full re-index...")
            await gm.clear_all()
            await asyncio.sleep(10)

        # Steps 1-3: Clone, discover, parse
        with RepositoryManager() as repo_mgr:
            job.progress = "Cloning repository..."
            logger.info("Cloning repository: %s (branch: %s)", repo_url, branch)
            repo_path = await repo_mgr.clone(repo_url, branch)
            commit_hash = await repo_mgr.get_head_commit(repo_path)
            logger.info("HEAD commit: %s", commit_hash)

            job.progress = "Discovering Python files..."
            files = await repo_mgr.discover_python_files(repo_path)
            logger.info("Discovered %d Python files", len(files))

            job.progress = f"Parsing and storing {len(files)} files..."
            total_classes = 0
            total_functions = 0
            total_imports = 0
            total_calls = 0
            parse_errors = 0

            semaphore = asyncio.Semaphore(max_workers)
            done_count = {"n": 0}

            async def _process_one(fp: str) -> tuple[str, dict | None]:
                async with semaphore:
                    done_count["n"] += 1
                    job.progress = f"Parsing file {done_count['n']}/{len(files)}: {fp}"
                    logger.info("[%d/%d] Processing %s", done_count["n"], len(files), fp)
                    try:
                        return fp, await _store_file(gm, parser, repo_mgr, repo_path, fp)
                    except Exception as e:
                        logger.warning("Failed to process %s: %s", fp, e)
                        return fp, None

            results = await asyncio.gather(*(_process_one(fp) for fp in files))

            for fp, stats in results:
                if stats is None:
                    parse_errors += 1
                    continue
                if "parse_error" in stats:
                    logger.warning("Parse error in %s: %s", fp, stats["parse_error"])
                    parse_errors += 1
                    continue
                total_classes += stats["classes"]
                total_functions += stats["functions"]
                total_imports += stats["imports"]
                total_calls += stats["calls"]

        logger.info("Temporary clone directory cleaned up")

        # Step 4: Resolve cross-file relationships
        job.progress = "Resolving cross-file relationships..."
        logger.info("Resolving cross-file relationships...")
        resolved = await gm.resolve_all_relationships()

        await gm.update_index_state(
            repo_url=repo_url,
            branch=branch,
            commit_hash=commit_hash,
            files_indexed=len(files),
            status="indexed",
        )

        # Step 5: Enrichment
        enriched_count = 0
        if not skip_enrichment:
            job.progress = "Running LLM enrichment..."
            logger.info("Starting LLM enrichment...")
            enricher = LLMEnricher()
            enriched_count = await enricher.enrich_all_nodes(gm)
            logger.info("Enriched %d entities", enriched_count)
            await gm.update_index_state(status="enriched")
        else:
            logger.info("Skipping LLM enrichment (skip_enrichment=True)")

        # Step 6: Vector embeddings
        embedded_count = 0
        if not skip_enrichment:
            job.progress = "Generating vector embeddings..."
            logger.info("Generating vector embeddings...")
            embeddings_model = get_openai_embeddings()
            embedded_count = await gm.create_all_embeddings(embeddings_model)
            logger.info("Embedded %d entities", embedded_count)
            await gm.update_index_state(status="embedded")
        else:
            logger.info("Skipping embeddings (skip_enrichment=True)")

        # Summary
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

        job.result = summary
        job.status = "completed"
        job.progress = "Indexing complete"
        job.completed_at = datetime.now(timezone.utc).isoformat()

    except Exception as e:
        logger.error("index_repository job failed: %s", e, exc_info=True)
        job.status = "failed"
        job.error = str(e)
        job.completed_at = datetime.now(timezone.utc).isoformat()


async def _run_index_file_job(
    job: Job,
    file_path: str,
    source_code: str,
    skip_enrichment: bool,
) -> None:
    """Execute incremental file update in background."""
    try:
        job.status = "running"
        job.progress = f"Parsing {file_path}..."

        gm = await _get_graph_manager()
        parser = _get_parser()

        # Get source code if not provided
        if not source_code:
            job.progress = f"Reading {file_path} from cloned repository..."
            repo_mgr = RepositoryManager()
            repo_path = await repo_mgr.get_repo_path()
            source_code = await repo_mgr.read_file(repo_path, file_path)

        parsed = parser.parse_file(source_code, file_path)

        if "parse_error" in parsed:
            job.result = {"error": parsed["parse_error"], "file": file_path}
            job.status = "completed"
            job.progress = "Parse error encountered"
            job.completed_at = datetime.now(timezone.utc).isoformat()
            return

        job.progress = f"Running incremental update for {file_path}..."
        enricher = LLMEnricher() if not skip_enrichment else None
        stats = await incremental_update_file(
            gm, enricher, file_path, parsed,
            skip_enrichment=skip_enrichment,
        )

        job.result = {"file": file_path, "stats": dict(stats)}
        job.status = "completed"
        job.progress = "Incremental update complete"
        job.completed_at = datetime.now(timezone.utc).isoformat()

    except Exception as e:
        logger.error("index_file job failed: %s", e, exc_info=True)
        job.status = "failed"
        job.error = str(e)
        job.completed_at = datetime.now(timezone.utc).isoformat()


async def _run_parse_ast_job(
    job: Job,
    source_code: str,
    file_path: str,
) -> None:
    """Parse Python AST in background."""
    try:
        job.status = "running"
        job.progress = "Parsing AST..."

        parser = _get_parser()
        parsed = parser.parse_file(source_code, file_path)

        job.result = parsed
        job.status = "completed"
        job.progress = "Parsing complete"
        job.completed_at = datetime.now(timezone.utc).isoformat()

    except Exception as e:
        logger.error("parse_python_ast job failed: %s", e, exc_info=True)
        job.status = "failed"
        job.error = str(e)
        job.completed_at = datetime.now(timezone.utc).isoformat()


async def _run_extract_entities_job(
    job: Job,
    source_code: str,
    file_path: str,
) -> None:
    """Extract entity summary in background."""
    try:
        job.status = "running"
        job.progress = "Extracting entities..."

        parser = _get_parser()
        parsed = parser.parse_file(source_code, file_path)

        if "parse_error" in parsed:
            job.result = {"error": parsed["parse_error"]}
            job.status = "completed"
            job.progress = "Parse error encountered"
            job.completed_at = datetime.now(timezone.utc).isoformat()
            return

        entities = {
            "file_path": file_path,
            "module_name": parsed.get("module_name", ""),
            "classes": [],
            "functions": [],
            "imports": len(parsed.get("imports", [])),
            "call_relationships": len(parsed.get("calls", [])),
        }

        for cls in parsed.get("classes", []):
            entities["classes"].append({
                "name": cls["name"],
                "qualified_name": cls["qualified_name"],
                "bases": cls.get("bases", []),
                "methods": [m["name"] for m in cls.get("methods", [])],
                "class_attributes": [a["name"] for a in cls.get("class_attributes", [])],
                "decorators": [d["name"] for d in cls.get("decorators", [])],
            })

        for func in parsed.get("functions", []):
            entities["functions"].append({
                "name": func["name"],
                "qualified_name": func["qualified_name"],
                "is_async": func.get("is_async", False),
                "parameters": [p["name"] for p in func.get("parameters", [])],
                "decorators": [d["name"] for d in func.get("decorators", [])],
                "calls": func.get("calls", []),
                "nested_functions": [n["name"] for n in func.get("nested_functions", [])],
            })

        job.result = entities
        job.status = "completed"
        job.progress = "Entity extraction complete"
        job.completed_at = datetime.now(timezone.utc).isoformat()

    except Exception as e:
        logger.error("extract_entities job failed: %s", e, exc_info=True)
        job.status = "failed"
        job.error = str(e)
        job.completed_at = datetime.now(timezone.utc).isoformat()


# ─── MCP Tool 1 ─────────────────────────────────────────────


@mcp.tool()
async def index_repository(
    repo_url: str = "https://github.com/tiangolo/fastapi.git",
    branch: str = "master",
    skip_enrichment: bool = False,
    clear_graph: bool = False,
    max_workers: int = 10,
) -> str:
    """Full repository indexing pipeline.

    Clones the repository, discovers all Python files, parses each file's
    AST, stores all entities and relationships in the Neo4j knowledge graph,
    resolves cross-file call/inheritance edges, runs LLM enrichment, and
    generates vector embeddings.

    This is a long-running operation (5-30 minutes depending on repo size
    and enrichment).  Returns a job_id — use get_index_status to poll.

    Args:
        repo_url: Git clone URL of the repository to index.
        branch: Git branch to index.
        skip_enrichment: Skip LLM enrichment and embedding generation.
            Useful for quick structural-only indexing. Default is False.
        clear_graph: Clear existing graph before indexing.  Required for
            full re-index to avoid duplicate Parameter/ClassAttribute nodes.
            Default is False.
        max_workers: Maximum concurrent file processing tasks.
    """
    job = _create_job("index_repository")
    asyncio.create_task(
        _run_index_repository_job(
            job, repo_url, branch, skip_enrichment, clear_graph, max_workers,
        )
    )
    return json.dumps({"job_id": job.job_id, "status": "pending"})


# ─── MCP Tool 2 ─────────────────────────────────────────────


@mcp.tool()
async def index_file(
    file_path: str,
    source_code: str = "",
    skip_enrichment: bool = False,
) -> str:
    """Incrementally index a single file using Strategy B fine-grained diffing.

    Compares the new AST parse against the existing graph state for this
    file.  Only updates changed entities, preserving LLM enrichment on
    unchanged code.  Uses enrichment cache to restore analysis for
    previously-seen content hashes.

    Returns a job_id — use get_index_status to poll.

    If source_code is provided, it is used directly.  Otherwise, the file
    is read from the previously cloned repository.

    Args:
        file_path: Relative path of the file within the repository
            (e.g. "fastapi/routing.py").
        source_code: Python source code to index.  If empty, reads from
            the previously cloned repository.
        skip_enrichment: Skip LLM enrichment for changed entities.
            Default is False.
    """
    job = _create_job("index_file")
    asyncio.create_task(
        _run_index_file_job(job, file_path, source_code, skip_enrichment)
    )
    return json.dumps({"job_id": job.job_id, "status": "pending"})


# ─── MCP Tool 3 ─────────────────────────────────────────────


@mcp.tool()
async def parse_python_ast(
    source_code: str,
    file_path: str = "unnamed.py",
) -> str:
    """Extract the Abstract Syntax Tree from Python source code.

    Returns a structured representation of all classes, functions, methods,
    parameters, decorators, imports, and call relationships found in the
    source code.  Each entity includes a content hash for change detection.

    This is a pure parsing operation — it does NOT write to the graph.
    Returns a job_id — use get_index_status to poll.

    Args:
        source_code: Python source code to parse.
        file_path: Virtual file path used for generating qualified names
            (e.g. "fastapi/routing.py" produces module "fastapi.routing").
    """
    job = _create_job("parse_python_ast")
    asyncio.create_task(_run_parse_ast_job(job, source_code, file_path))
    return json.dumps({"job_id": job.job_id, "status": "pending"})


# ─── MCP Tool 4 ─────────────────────────────────────────────


@mcp.tool()
async def extract_entities(
    source_code: str,
    file_path: str = "unnamed.py",
) -> str:
    """Identify code entities and their relationships from Python source code.

    Parses the AST and returns a high-level summary organised by entity
    type: classes with their methods, attributes, and bases; functions with
    their parameters, calls, and nested functions; plus import and call
    relationship counts.

    This is a pure analysis operation — it does NOT write to the graph.
    Returns a job_id — use get_index_status to poll.

    Args:
        source_code: Python source code to analyse.
        file_path: Virtual file path for qualified name generation.
    """
    job = _create_job("extract_entities")
    asyncio.create_task(
        _run_extract_entities_job(job, source_code, file_path)
    )
    return json.dumps({"job_id": job.job_id, "status": "pending"})


# ─── MCP Tool 5 ─────────────────────────────────────────────


@mcp.tool()
async def get_index_status(
    job_id: str = "",
) -> str:
    """Check job progress and graph statistics.

    If job_id is provided, returns that specific job's status, progress
    message, and result (if completed) or error (if failed).

    If job_id is empty, returns an overview of all jobs plus current
    graph statistics: node counts, edge counts, enrichment coverage,
    and validation warnings.

    This is the only tool that returns results directly (not background).

    Args:
        job_id: ID of a specific job to check.  Empty returns overview.
    """
    # Specific job lookup
    if job_id:
        job = _jobs.get(job_id)
        if job is None:
            return json.dumps({"error": f"Job '{job_id}' not found"})
        return json.dumps(_job_to_dict(job), default=str)

    # Overview: all jobs + graph stats
    overview: dict = {
        "jobs": [_job_to_dict(j) for j in _jobs.values()],
    }

    # Try to get graph stats (may fail if Neo4j not connected yet)
    try:
        gm = await _get_graph_manager()
        index_state = await gm.get_index_state()
        overview["index_state"] = index_state.get("state") if index_state else None
        overview["node_counts"] = await gm.get_node_counts()
        overview["edge_counts"] = await gm.get_edge_counts()
        overview["enrichment"] = await gm.get_enrichment_stats()
        overview["warnings"] = await gm.get_validation_warnings()
    except Exception as e:
        overview["graph_error"] = str(e)

    return json.dumps(overview, default=str)


# ─── Entry point ─────────────────────────────────────────────


if __name__ == "__main__":
    logger.info("Starting Indexer MCP server (stdio transport)")
    mcp.run(transport="stdio")
