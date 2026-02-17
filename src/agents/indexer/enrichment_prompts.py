"""
Enrichment Prompts

System prompt and prompt builder for LLM-based code entity enrichment.
"""

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

For data_flows_to: identify other functions or classes that this entity \
sends data to — e.g. it returns a value consumed by another function, \
passes data through a callback, writes to shared state read by another, \
or transforms a request object that flows downstream.
"""


def build_enrichment_prompt(entity: dict, entity_type: str, context: dict) -> str:
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
