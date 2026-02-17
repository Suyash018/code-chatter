"""
Python AST Parser

Deterministic extraction of code entities from Python source files.
Extracts classes, functions, methods, parameters, decorators, imports,
call relationships, and computes content hashes for each entity.
"""

import ast
import hashlib
import textwrap
import logging
from typing import Any

from src.agents.indexer.models import (
    ParsedParameter,
    ParsedDecorator,
    ParsedImport,
    ParsedFunction,
    ParsedClass,
    path_to_module,
)

logger = logging.getLogger("indexer-agent.ast_parser")


class PythonASTParser:
    """
    Deterministic AST parser for Python files.

    Extracts all code entities, their properties, and relationships.
    Every entity gets a content hash for change detection in Strategy B.
    """

    def parse_file(self, source: str, file_path: str) -> dict[str, Any]:
        """
        Parse a Python file and extract all entities.

        Args:
            source: Python source code as string.
            file_path: Relative path of the file (used for qualified names).

        Returns:
            Dictionary with file_hash, classes, functions, imports, calls.
        """
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            logger.warning(f"Syntax error in {file_path}: {e}")
            return {
                "file_path": file_path,
                "file_hash": self._compute_hash(source),
                "classes": [],
                "functions": [],
                "imports": [],
                "calls": [],
                "parse_error": str(e),
            }

        source_lines = source.splitlines()
        module_name = self._path_to_module(file_path)
        is_package = file_path.endswith("__init__.py")

        classes = []
        functions = []
        imports = []
        all_calls = []

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                parsed_class = self._parse_class(
                    node, source_lines, module_name
                )
                classes.append(parsed_class)
                # Collect calls from all methods
                for method in parsed_class["methods"]:
                    all_calls.extend(
                        {"caller": method["qualified_name"], "callee": c}
                        for c in method["calls"]
                    )
                    # Collect calls from nested functions inside methods
                    for nested in method.get("nested_functions", []):
                        all_calls.extend(
                            {"caller": nested["qualified_name"], "callee": c}
                            for c in nested["calls"]
                        )

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                parsed_func = self._parse_function(
                    node, source_lines, module_name
                )
                functions.append(parsed_func)
                all_calls.extend(
                    {"caller": parsed_func["qualified_name"], "callee": c}
                    for c in parsed_func["calls"]
                )
                # Collect calls from nested functions
                for nested in parsed_func.get("nested_functions", []):
                    all_calls.extend(
                        {"caller": nested["qualified_name"], "callee": c}
                        for c in nested["calls"]
                    )

            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                parsed_imports = self._parse_import(node, module_name, is_package)
                imports.extend(parsed_imports)

            # Handle TYPE_CHECKING blocks
            elif isinstance(node, ast.If):
                if self._is_type_checking_block(node):
                    for item in node.body:
                        if isinstance(item, (ast.Import, ast.ImportFrom)):
                            parsed_imports = self._parse_import(item, module_name, is_package)
                            for imp in parsed_imports:
                                imp["is_type_checking"] = True
                            imports.extend(parsed_imports)
                else:
                    # Handle other conditional imports (e.g. sys.version_info checks)
                    for item in node.body:
                        if isinstance(item, (ast.Import, ast.ImportFrom)):
                            parsed_imports = self._parse_import(item, module_name, is_package)
                            for imp in parsed_imports:
                                imp["is_conditional"] = True
                                imp["condition"] = ast.unparse(node.test)
                            imports.extend(parsed_imports)
                    for item in (node.orelse if isinstance(node.orelse, list) else []):
                        if isinstance(item, (ast.Import, ast.ImportFrom)):
                            parsed_imports = self._parse_import(item, module_name, is_package)
                            for imp in parsed_imports:
                                imp["is_conditional"] = True
                                imp["condition"] = f"not ({ast.unparse(node.test)})"
                            imports.extend(parsed_imports)

            # Handle try/except imports (optional dependencies)
            elif isinstance(node, ast.Try):
                # try body
                for item in node.body:
                    if isinstance(item, (ast.Import, ast.ImportFrom)):
                        parsed_imports = self._parse_import(item, module_name, is_package)
                        for imp in parsed_imports:
                            imp["is_try_except"] = True
                        imports.extend(parsed_imports)
                # except handler bodies (fallback imports)
                for handler in node.handlers:
                    for item in handler.body:
                        if isinstance(item, (ast.Import, ast.ImportFrom)):
                            parsed_imports = self._parse_import(item, module_name, is_package)
                            for imp in parsed_imports:
                                imp["is_try_except"] = True
                                imp["is_fallback"] = True
                            imports.extend(parsed_imports)

        return {
            "file_path": file_path,
            "file_hash": self._compute_hash(source),
            "module_name": module_name,
            "classes": classes,
            "functions": functions,
            "imports": imports,
            "calls": all_calls,
        }

    # ─── Class Parsing ─────────────────────────────────────

    def _parse_class(
        self,
        node: ast.ClassDef,
        source_lines: list[str],
        module_name: str,
    ) -> dict[str, Any]:
        """Extract a class definition with all its methods and class attributes."""

        qualified_name = f"{module_name}.{node.name}"
        source = self._extract_source(node, source_lines)
        content_hash = self._compute_hash(source)

        # Base classes
        bases = []
        for base in node.bases:
            bases.append(self._node_to_name(base))

        # Decorators
        decorators = [self._parse_decorator(d) for d in node.decorator_list]

        # Docstring
        docstring = ast.get_docstring(node)

        # Methods
        methods = []
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method = self._parse_function(
                    item, source_lines, qualified_name, is_method=True
                )
                methods.append(method)

        # Class-level attributes (dataclass fields, Pydantic model fields, etc.)
        # These are ast.AnnAssign (annotated) or ast.Assign nodes at class body level
        class_attributes = []
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                attr = {
                    "name": item.target.id,
                    "type_annotation": self._node_to_name(item.annotation) if item.annotation else None,
                    "default_value": self._node_to_name(item.value) if item.value else None,
                    "lineno": item.lineno,
                }
                class_attributes.append(attr)
            elif isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        attr = {
                            "name": target.id,
                            "type_annotation": None,
                            "default_value": self._node_to_name(item.value) if item.value else None,
                            "lineno": item.lineno,
                        }
                        class_attributes.append(attr)

        return {
            "name": node.name,
            "qualified_name": qualified_name,
            "source": source,
            "content_hash": content_hash,
            "lineno_start": node.lineno,
            "lineno_end": node.end_lineno or node.lineno,
            "bases": bases,
            "docstring": docstring or "",
            "decorators": decorators,
            "methods": methods,
            "class_attributes": class_attributes,
        }

    # ─── Function Parsing ──────────────────────────────────

    def _parse_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        source_lines: list[str],
        parent_name: str,
        is_method: bool = False,
    ) -> dict[str, Any]:
        """Extract a function or method definition, including nested functions."""

        qualified_name = f"{parent_name}.{node.name}"
        source = self._extract_source(node, source_lines)

        # Source now includes decorator lines via _extract_source,
        # so decorator changes are detected automatically
        content_hash = self._compute_hash(source)

        # Parameters
        parameters = self._parse_parameters(node.args)

        # Return annotation
        return_annotation = None
        if node.returns:
            return_annotation = self._node_to_name(node.returns)

        # Decorators
        decorators = [self._parse_decorator(d) for d in node.decorator_list]

        # Docstring
        docstring = ast.get_docstring(node)

        # Calls (static analysis — walk body for Call nodes)
        calls = self._extract_calls(node)

        is_async = isinstance(node, ast.AsyncFunctionDef)

        # Nested functions (direct children only, not deeply nested)
        nested_functions = []
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nested = self._parse_function(
                    child, source_lines, qualified_name, is_method=False
                )
                nested["is_nested"] = True
                nested_functions.append(nested)

        return {
            "name": node.name,
            "qualified_name": qualified_name,
            "source": source,
            "content_hash": content_hash,
            "lineno_start": node.lineno,
            "lineno_end": node.end_lineno or node.lineno,
            "is_async": is_async,
            "is_method": is_method,
            "docstring": docstring or "",
            "return_annotation": return_annotation,
            "parameters": parameters,
            "decorators": decorators,
            "calls": calls,
            "nested_functions": nested_functions,
        }

    # ─── Parameter Parsing ─────────────────────────────────

    def _parse_parameters(self, args: ast.arguments) -> list[dict]:
        """Extract all parameters from a function signature."""
        params = []
        position = 0

        # Positional-only args (before /) + regular args
        # defaults apply to the last N of (posonlyargs + args) combined
        all_positional = list(args.posonlyargs) + list(args.args)
        defaults_offset = len(all_positional) - len(args.defaults)

        for i, arg in enumerate(all_positional):
            if arg.arg in ("self", "cls"):
                continue

            type_ann = None
            if arg.annotation:
                type_ann = self._node_to_name(arg.annotation)

            default_val = None
            default_idx = i - defaults_offset
            if default_idx >= 0 and default_idx < len(args.defaults):
                default_val = self._node_to_name(args.defaults[default_idx])

            params.append({
                "name": arg.arg,
                "type_annotation": type_ann,
                "default_value": default_val,
                "position": position,
                "kind": "positional_only" if i < len(args.posonlyargs) else "positional_or_keyword",
            })
            position += 1

        # *args
        if args.vararg:
            type_ann = None
            if args.vararg.annotation:
                type_ann = self._node_to_name(args.vararg.annotation)
            params.append({
                "name": f"*{args.vararg.arg}",
                "type_annotation": type_ann,
                "default_value": None,
                "position": position,
                "kind": "var_positional",
            })
            position += 1

        # Keyword-only args
        for i, arg in enumerate(args.kwonlyargs):
            type_ann = None
            if arg.annotation:
                type_ann = self._node_to_name(arg.annotation)

            default_val = None
            if i < len(args.kw_defaults) and args.kw_defaults[i] is not None:
                default_val = self._node_to_name(args.kw_defaults[i])

            params.append({
                "name": arg.arg,
                "type_annotation": type_ann,
                "default_value": default_val,
                "position": position,
                "kind": "keyword_only",
            })
            position += 1

        # **kwargs
        if args.kwarg:
            type_ann = None
            if args.kwarg.annotation:
                type_ann = self._node_to_name(args.kwarg.annotation)
            params.append({
                "name": f"**{args.kwarg.arg}",
                "type_annotation": type_ann,
                "default_value": None,
                "position": position,
                "kind": "var_keyword",
            })

        return params

    # ─── Import Parsing ────────────────────────────────────

    def _parse_import(
        self,
        node: ast.Import | ast.ImportFrom,
        module_name: str,
        is_package: bool = False,
    ) -> list[dict[str, Any]]:
        """Extract import statement(s), resolving relative imports.
        Returns a list since `import X, Y` produces multiple import records."""

        if isinstance(node, ast.Import):
            # import X, Y — each is a separate module import
            results = []
            for alias in node.names:
                results.append({
                    "module": alias.name,
                    "names": [alias.name],
                    "aliases": {alias.name: alias.asname} if alias.asname else {},
                    "is_from_import": False,
                    "is_relative": False,
                    "level": 0,
                    "source_module": module_name,
                    "is_type_checking": False,
                })
            return results

        elif isinstance(node, ast.ImportFrom):
            # from X import Y, Z — single import record with multiple names
            raw_module = node.module or ""
            level = node.level or 0
            is_relative = level > 0

            # Resolve relative imports to absolute module paths
            resolved_module = raw_module
            if is_relative:
                resolved_module = self._resolve_relative_import(
                    module_name, raw_module, level, is_package
                )

            names = []
            aliases = {}
            for alias in node.names:
                names.append(alias.name)
                if alias.asname:
                    aliases[alias.name] = alias.asname

            return [{
                "module": resolved_module,
                "names": names,
                "aliases": aliases,
                "is_from_import": True,
                "is_relative": is_relative,
                "level": level,
                "source_module": module_name,
                "is_type_checking": False,
            }]

        return []

    def _resolve_relative_import(
        self, current_module: str, target: str, level: int,
        is_package: bool = False,
    ) -> str:
        """
        Resolve a relative import to an absolute module path.

        For __init__.py files (is_package=True), the module IS the package,
        so level=1 stays at the same level.

        For regular files, the module is inside a package,
        so level=1 goes up to the parent package.

        Examples:
            fastapi/__init__.py (is_package=True, module="fastapi"):
              from .applications import X -> fastapi.applications

            fastapi/routing.py (is_package=False, module="fastapi.routing"):
              from .applications import X -> fastapi.applications

            fastapi/dependencies/utils.py (is_package=False, module="fastapi.dependencies.utils"):
              from .models import X -> fastapi.dependencies.models
              from ..params import X -> fastapi.params
        """
        parts = current_module.split(".")

        if is_package:
            # __init__.py: module name IS the package
            # level=1 means "this package", so strip (level - 1) components
            strip = level - 1
        else:
            # regular file: last component is the module, not a package
            # level=1 means "parent package", so strip 'level' components
            strip = level

        if strip >= len(parts):
            return target

        base_parts = parts[: len(parts) - strip] if strip > 0 else parts

        if target:
            return ".".join(base_parts + [target])
        else:
            return ".".join(base_parts)

    # ─── Call Extraction ───────────────────────────────────

    def _extract_calls(self, node: ast.AST) -> list[str]:
        """
        Walk a function body and extract all function/method call names.
        Does NOT descend into nested function/class definitions — those
        get their own call lists via recursive _parse_function.
        Returns unresolved names — resolution happens at graph level.
        """
        calls = set()
        # Use a worklist instead of ast.walk to control descent
        worklist = list(ast.iter_child_nodes(node))
        while worklist:
            child = worklist.pop()

            if isinstance(child, ast.Call):
                name = self._call_to_name(child.func)
                if name:
                    calls.add(name)

            # Skip nested function/class bodies — they have their own scope
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue

            # Descend into all other nodes (if/for/with/try/etc.)
            worklist.extend(ast.iter_child_nodes(child))

        return sorted(calls)

    def _call_to_name(self, node: ast.expr) -> str | None:
        """Convert a Call node's func to a string name."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            value_name = self._call_to_name(node.value)
            if value_name:
                return f"{value_name}.{node.attr}"
            return node.attr
        return None

    # ─── Decorator Parsing ─────────────────────────────────

    def _parse_decorator(self, node: ast.expr) -> dict[str, str]:
        """Extract decorator name and arguments."""
        if isinstance(node, ast.Name):
            return {"name": node.id, "arguments": None}
        elif isinstance(node, ast.Attribute):
            return {"name": self._node_to_name(node), "arguments": None}
        elif isinstance(node, ast.Call):
            name = self._node_to_name(node.func)
            # Stringify arguments
            args_parts = []
            for arg in node.args:
                args_parts.append(self._node_to_name(arg))
            for kw in node.keywords:
                args_parts.append(
                    f"{kw.arg}={self._node_to_name(kw.value)}"
                )
            arguments = ", ".join(args_parts) if args_parts else None
            return {"name": name, "arguments": arguments}
        return {"name": ast.dump(node), "arguments": None}

    # ─── Utility Methods ───────────────────────────────────

    def _is_type_checking_block(self, node: ast.If) -> bool:
        """Check if an if-block is `if TYPE_CHECKING:`."""
        test = node.test
        if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
            return True
        if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
            return True
        return False

    def _node_to_name(self, node: ast.AST) -> str:
        """Convert an AST node to its string representation."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return f"{self._node_to_name(node.value)}.{node.attr}"
        elif isinstance(node, ast.Constant):
            return repr(node.value)
        elif isinstance(node, ast.Subscript):
            return f"{self._node_to_name(node.value)}[{self._node_to_name(node.slice)}]"
        elif isinstance(node, ast.Tuple):
            return ", ".join(self._node_to_name(e) for e in node.elts)
        elif isinstance(node, ast.List):
            return "[" + ", ".join(self._node_to_name(e) for e in node.elts) + "]"
        elif isinstance(node, ast.BinOp):
            return f"{self._node_to_name(node.left)} | {self._node_to_name(node.right)}"
        elif isinstance(node, ast.Call):
            return self._node_to_name(node.func)
        elif isinstance(node, ast.Starred):
            return f"*{self._node_to_name(node.value)}"
        try:
            return ast.unparse(node)
        except Exception:
            return ast.dump(node)

    def _extract_source(self, node: ast.AST, source_lines: list[str]) -> str:
        """Extract source code for a node from the file's lines.
        Includes decorator lines if the node has a decorator_list."""
        # Start from the first decorator if any, else from the node itself
        decorator_list = getattr(node, "decorator_list", [])
        if decorator_list:
            start = decorator_list[0].lineno - 1  # 0-indexed
        else:
            start = node.lineno - 1
        end = getattr(node, "end_lineno", node.lineno)
        lines = source_lines[start:end]
        if lines:
            # Dedent to normalize indentation
            return textwrap.dedent("\n".join(lines))
        return ""

    def _compute_hash(self, content: str) -> str:
        """Compute SHA-256 hash of content for change detection."""
        normalized = content.strip()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    def _path_to_module(self, file_path: str) -> str:
        """Convert a file path to a Python module name. Delegates to module-level function."""
        return path_to_module(file_path)
