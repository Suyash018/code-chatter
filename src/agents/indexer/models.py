"""
Parsed Entity Models

Data classes representing code entities extracted from Python AST parsing.
Used by the AST parser, graph manager, and other indexer components.
"""

from dataclasses import dataclass, field


@dataclass
class ParsedParameter:
    """A function/method parameter."""

    name: str
    type_annotation: str | None = None
    default_value: str | None = None
    position: int = 0


@dataclass
class ParsedDecorator:
    """A decorator applied to a function or class."""

    name: str
    arguments: str | None = None  # String repr of args


@dataclass
class ParsedImport:
    """An import statement."""

    module: str
    names: list[str] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)
    is_from_import: bool = False
    is_relative: bool = False
    level: int = 0  # Number of dots in relative import


@dataclass
class ParsedFunction:
    """A function or method extracted from AST."""

    name: str
    qualified_name: str
    source: str
    content_hash: str
    lineno_start: int
    lineno_end: int
    is_async: bool = False
    is_method: bool = False
    docstring: str | None = None
    return_annotation: str | None = None
    parameters: list[dict] = field(default_factory=list)
    decorators: list[dict] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)


@dataclass
class ParsedClass:
    """A class extracted from AST."""

    name: str
    qualified_name: str
    source: str
    content_hash: str
    lineno_start: int
    lineno_end: int
    bases: list[str] = field(default_factory=list)
    docstring: str | None = None
    decorators: list[dict] = field(default_factory=list)
    methods: list[dict] = field(default_factory=list)


def path_to_module(file_path: str) -> str:
    """
    Convert a file path to a Python module name.
    e.g., 'fastapi/routing.py' -> 'fastapi.routing'
         'fastapi\\__init__.py' -> 'fastapi'

    Handles both forward slashes and backslashes (Windows).
    """
    path = file_path.replace(".py", "")
    path = path.replace("/__init__", "").replace("\\__init__", "")
    return path.replace("/", ".").replace("\\", ".").strip(".")
