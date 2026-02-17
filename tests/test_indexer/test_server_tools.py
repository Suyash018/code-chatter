"""
Unit tests for Indexer Agent MCP server tools.

Tests the pure tools (parse_python_ast, extract_entities) that don't
require Neo4j, plus the job lifecycle management.
"""

import asyncio
import pytest

from src.agents.indexer.ast_parser import PythonASTParser
from src.agents.indexer.server import (
    Job,
    _create_job,
    _jobs,
    _run_parse_ast_job,
    _run_extract_entities_job,
)


# ─── Fixtures ────────────────────────────────────────────────


SAMPLE_SOURCE = '''\
"""Sample module for testing."""

import os
from typing import Optional


class Animal:
    """A base animal class."""

    name: str
    sound: str = "..."

    def __init__(self, name: str) -> None:
        self.name = name

    def speak(self) -> str:
        return f"{self.name} says {self.sound}"


class Dog(Animal):
    """A dog that barks."""

    sound: str = "Woof"

    def fetch(self, item: str) -> str:
        return f"{self.name} fetches {item}"


def greet(animal: Animal, greeting: str = "Hello") -> str:
    """Greet an animal."""
    result = animal.speak()
    return f"{greeting}, {result}"


async def async_greet(animal: Animal) -> str:
    """Async version of greet."""
    return animal.speak()
'''

INVALID_SOURCE = "def broken(:\n    pass"


@pytest.fixture(autouse=True)
def clear_jobs():
    """Clear the global job registry before each test."""
    _jobs.clear()
    yield
    _jobs.clear()


# ─── parse_python_ast tests ─────────────────────────────────


class TestParseAst:
    """Tests for the parse_python_ast background worker."""

    def test_basic_parse(self):
        """Parse valid source and verify structure."""
        job = _create_job("parse_python_ast")
        asyncio.run(_run_parse_ast_job(job, SAMPLE_SOURCE, "sample/module.py"))

        assert job.status == "completed"
        result = job.result
        assert result is not None

        assert "classes" in result
        assert "functions" in result
        assert "imports" in result
        assert "calls" in result
        assert result["file_path"] == "sample/module.py"

    def test_classes_extracted(self):
        """Verify classes are correctly extracted."""
        job = _create_job("parse_python_ast")
        asyncio.run(_run_parse_ast_job(job, SAMPLE_SOURCE, "sample/module.py"))

        classes = job.result["classes"]
        assert len(classes) == 2

        names = {c["name"] for c in classes}
        assert names == {"Animal", "Dog"}

        dog = next(c for c in classes if c["name"] == "Dog")
        assert "Animal" in dog["bases"]

    def test_functions_extracted(self):
        """Verify top-level functions are extracted."""
        job = _create_job("parse_python_ast")
        asyncio.run(_run_parse_ast_job(job, SAMPLE_SOURCE, "sample/module.py"))

        functions = job.result["functions"]
        names = {f["name"] for f in functions}
        assert "greet" in names
        assert "async_greet" in names

        async_fn = next(f for f in functions if f["name"] == "async_greet")
        assert async_fn["is_async"] is True

    def test_methods_extracted(self):
        """Verify class methods are extracted."""
        job = _create_job("parse_python_ast")
        asyncio.run(_run_parse_ast_job(job, SAMPLE_SOURCE, "sample/module.py"))

        animal = next(c for c in job.result["classes"] if c["name"] == "Animal")
        method_names = {m["name"] for m in animal["methods"]}
        assert "__init__" in method_names
        assert "speak" in method_names

    def test_imports_extracted(self):
        """Verify imports are captured."""
        job = _create_job("parse_python_ast")
        asyncio.run(_run_parse_ast_job(job, SAMPLE_SOURCE, "sample/module.py"))

        imports = job.result["imports"]
        modules = {imp["module"] for imp in imports}
        assert "os" in modules
        assert "typing" in modules

    def test_content_hashes_present(self):
        """Verify content hashes exist on all entities."""
        job = _create_job("parse_python_ast")
        asyncio.run(_run_parse_ast_job(job, SAMPLE_SOURCE, "sample/module.py"))

        assert job.result["file_hash"]
        for cls in job.result["classes"]:
            assert cls["content_hash"]
        for func in job.result["functions"]:
            assert func["content_hash"]

    def test_syntax_error(self):
        """Parse invalid source returns parse_error."""
        job = _create_job("parse_python_ast")
        asyncio.run(_run_parse_ast_job(job, INVALID_SOURCE, "broken.py"))

        assert job.status == "completed"
        assert "parse_error" in job.result

    def test_qualified_names(self):
        """Verify qualified names use module path."""
        job = _create_job("parse_python_ast")
        asyncio.run(_run_parse_ast_job(job, SAMPLE_SOURCE, "sample/module.py"))

        animal = next(c for c in job.result["classes"] if c["name"] == "Animal")
        assert animal["qualified_name"] == "sample.module.Animal"

        greet = next(f for f in job.result["functions"] if f["name"] == "greet")
        assert greet["qualified_name"] == "sample.module.greet"


# ─── extract_entities tests ─────────────────────────────────


class TestExtractEntities:
    """Tests for the extract_entities background worker."""

    def test_entity_summary(self):
        """Verify entity summary structure."""
        job = _create_job("extract_entities")
        asyncio.run(_run_extract_entities_job(job, SAMPLE_SOURCE, "sample/module.py"))

        assert job.status == "completed"
        result = job.result

        assert result["file_path"] == "sample/module.py"
        assert result["module_name"] == "sample.module"
        assert len(result["classes"]) == 2
        assert len(result["functions"]) == 2
        assert result["imports"] == 2

    def test_class_details(self):
        """Verify class summary includes methods and attributes."""
        job = _create_job("extract_entities")
        asyncio.run(_run_extract_entities_job(job, SAMPLE_SOURCE, "sample/module.py"))

        animal = next(c for c in job.result["classes"] if c["name"] == "Animal")
        assert "__init__" in animal["methods"]
        assert "speak" in animal["methods"]
        assert "name" in animal["class_attributes"]
        assert "sound" in animal["class_attributes"]

        dog = next(c for c in job.result["classes"] if c["name"] == "Dog")
        assert dog["bases"] == ["Animal"]
        assert "fetch" in dog["methods"]

    def test_function_details(self):
        """Verify function summary includes parameters and calls."""
        job = _create_job("extract_entities")
        asyncio.run(_run_extract_entities_job(job, SAMPLE_SOURCE, "sample/module.py"))

        greet = next(f for f in job.result["functions"] if f["name"] == "greet")
        assert "animal" in greet["parameters"]
        assert "greeting" in greet["parameters"]
        assert greet["is_async"] is False
        assert "speak" in greet["calls"] or "animal.speak" in greet["calls"]

    def test_syntax_error(self):
        """Syntax error returns error dict."""
        job = _create_job("extract_entities")
        asyncio.run(_run_extract_entities_job(job, INVALID_SOURCE, "broken.py"))

        assert job.status == "completed"
        assert "error" in job.result

    def test_call_relationship_count(self):
        """Verify call relationship count is reasonable."""
        job = _create_job("extract_entities")
        asyncio.run(_run_extract_entities_job(job, SAMPLE_SOURCE, "sample/module.py"))

        assert job.result["call_relationships"] >= 0


# ─── Job lifecycle tests ────────────────────────────────────


class TestJobLifecycle:
    """Tests for the job management system."""

    def test_create_job(self):
        """New job starts with pending status."""
        job = _create_job("test_tool")
        assert job.status == "pending"
        assert job.tool_name == "test_tool"
        assert job.job_id in _jobs
        assert job.created_at
        assert job.result is None
        assert job.error is None

    def test_job_completes(self):
        """Job transitions to completed after successful run."""
        job = _create_job("parse_python_ast")
        asyncio.run(_run_parse_ast_job(job, "x = 1", "simple.py"))

        assert job.status == "completed"
        assert job.completed_at is not None
        assert job.result is not None
        assert job.error is None

    def test_job_unique_ids(self):
        """Multiple jobs get unique IDs."""
        j1 = _create_job("tool_a")
        j2 = _create_job("tool_b")
        j3 = _create_job("tool_c")
        assert len({j1.job_id, j2.job_id, j3.job_id}) == 3

    def test_jobs_registry(self):
        """Jobs are stored in the global registry."""
        j1 = _create_job("tool_a")
        j2 = _create_job("tool_b")
        assert len(_jobs) == 2
        assert _jobs[j1.job_id] is j1
        assert _jobs[j2.job_id] is j2
