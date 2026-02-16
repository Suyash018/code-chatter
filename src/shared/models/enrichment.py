from pydantic import BaseModel, Field
from typing import Literal


class ParameterExplanation(BaseModel):
    """A single parameter and its explanation."""

    name: str = Field(description="The parameter name")
    explanation: str = Field(description="Short explanation of what this parameter does")


class FunctionEnrichment(BaseModel):
    """Structured output schema for function/method enrichment."""

    purpose: str = Field(
        description="One-line description of what this function does"
    )
    summary: str = Field(
        description="2-3 sentence detailed explanation of the implementation"
    )
    design_patterns: list[str] = Field(
        default_factory=list,
        description="Design patterns used, e.g. singleton, factory, observer, "
        "decorator, strategy, template_method, dependency_injection, "
        "middleware, mixin, registry, facade, adapter, proxy, "
        "chain_of_responsibility, command",
    )
    complexity: Literal["low", "medium", "high"] = Field(
        description="Code complexity level"
    )
    side_effects: list[str] = Field(
        default_factory=list,
        description="Side effects such as modifies_state, io_operation, "
        "raises_exception, network_call, database_write",
    )
    domain_concepts: list[str] = Field(
        default_factory=list,
        description="Domain concepts such as routing, validation, middleware, "
        "authentication, authorization, serialization, error_handling, "
        "request_processing, configuration, lifecycle",
    )
    parameters_explained: list[ParameterExplanation] = Field(
        default_factory=list,
        description="List of parameter explanations for each parameter",
    )
    data_flows_to: list[str] = Field(
        default_factory=list,
        description="Names of functions or classes that this entity sends data to "
        "(e.g. passes return values, writes to shared state consumed by another)",
    )


class ClassEnrichment(BaseModel):
    """Structured output schema for class enrichment."""

    purpose: str = Field(
        description="One-line description of the class responsibility"
    )
    summary: str = Field(
        description="2-3 sentence detailed explanation"
    )
    design_patterns: list[str] = Field(
        default_factory=list,
        description="Design patterns used, e.g. singleton, factory, observer, "
        "decorator, strategy, template_method, dependency_injection, "
        "middleware, mixin, registry, facade, adapter, proxy, "
        "chain_of_responsibility, command",
    )
    role: Literal[
        "controller", "model", "service", "utility",
        "base_class", "mixin", "protocol", "other",
    ] = Field(description="Primary architectural role of this class")
    key_methods: list[str] = Field(
        default_factory=list,
        description="Most important method names",
    )
    collaborators: list[str] = Field(
        default_factory=list,
        description="Names of other classes this works closely with",
    )
    domain_concepts: list[str] = Field(
        default_factory=list,
        description="Domain concepts such as routing, validation, middleware, "
        "authentication, authorization, serialization, error_handling, "
        "request_processing, configuration, lifecycle",
    )
    data_flows_to: list[str] = Field(
        default_factory=list,
        description="Names of functions or classes that this class sends data to "
        "(e.g. passes return values, writes to shared state consumed by another)",
    )
