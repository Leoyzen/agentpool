"""Capability configuration models.

Typed config models for each of the 6 built-in capabilities, forming a
discriminated union that validates YAML inputs at load time.

Three resolution paths:

1. **Built-in short names** (``loop_detection``, ``token_budget``, etc.)
   validated against typed config models.
2. **Entry-point names** (e.g. ``mermaid_lint``) resolved via the
   ``agentpool.capabilities`` entry-point group using
   :class:`EntryPointCapabilityConfig`.
3. **Python import paths** (e.g. ``pydantic_ai.capabilities.Instrumentation``)
   resolved via :class:`GenericCapabilityConfig` using ``__import__``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


KNOWN_CAPABILITY_TYPES: frozenset[str] = frozenset({
    "loop_detection",
    "token_budget",
    "tool_output_budget",
    "dcp",
    "skill_activation",
    "memory",
    "modality_filter",
})

IMPORT_MAP: dict[str, str] = {
    "loop_detection": "agentpool.capabilities.loop_detection.LoopDetectionCapability",
    "token_budget": "agentpool.capabilities.token_budget.TokenBudgetCapability",
    "tool_output_budget": ("agentpool.capabilities.tool_output_budget.ToolOutputBudgetCapability"),
    "dcp": "agentpool.capabilities.dcp.DynamicContextPruningCapability",
    "skill_activation": "agentpool.capabilities.skill_manager_cap:SkillManagerCap",
    "memory": "agentpool.capabilities.memory.MemoryCapability",
    "modality_filter": "agentpool.capabilities.modality_filter.ModalityFilterCapability",
}


# ---------------------------------------------------------------------------
# Typed config models for built-in capabilities
# ---------------------------------------------------------------------------


class LoopDetectionCapabilityConfig(BaseModel):
    """Config for ``LoopDetectionCapability``."""

    type: Literal["loop_detection"] = "loop_detection"
    max_depth: int = 10
    """Maximum delegation depth before raising ``LoopDetectionError``."""


class TokenBudgetCapabilityConfig(BaseModel):
    """Config for ``TokenBudgetCapability``."""

    type: Literal["token_budget"] = "token_budget"
    max_tokens: int = 100_000
    """Maximum cumulative token usage per agent run."""


class ToolOutputBudgetCapabilityConfig(BaseModel):
    """Config for ``ToolOutputBudgetCapability``."""

    type: Literal["tool_output_budget"] = "tool_output_budget"
    max_output_chars: int = 10_000
    """Maximum characters per tool output before truncation."""
    truncation_suffix: str = "\n[Tool output truncated by ToolOutputBudgetCapability]"
    """Suffix appended to truncated tool output to indicate truncation."""


class DCPCapabilityConfig(BaseModel):
    """Config for ``DynamicContextPruningCapability`` (DCP).

    DCP provides dynamic context pruning to manage context window usage
    by tracking watermarks, deduplicating tool outputs, and injecting
    nudges to steer the model toward context-efficient behavior.
    """

    type: Literal["dcp"] = "dcp"
    enabled: bool = True
    """Master switch — when ``False``, DCP is loaded but performs no pruning."""
    expose_tools: bool = True
    """Whether to expose DCP meta-tools (e.g. ``dcp_status``) to the model."""
    max_context_tokens: int = 128_000
    """Maximum context window size in tokens used for watermark calculations."""
    info_threshold: float = 0.60
    """Context fill ratio that triggers the ``info`` watermark level."""
    warning_threshold: float = 0.75
    """Context fill ratio that triggers the ``warning`` watermark level."""
    critical_threshold: float = 0.90
    """Context fill ratio that triggers the ``critical`` watermark level."""
    auto_dedup: bool = True
    """Automatically deduplicate repeated tool outputs."""
    auto_strategy_threshold: str = "info"
    """Watermark level at which automatic pruning strategies activate.

    One of ``"info"``, ``"warning"``, ``"critical"``.
    """
    purge_error_steps: int = 3
    """Number of error steps to retain before purging older ones."""
    nudge_turn_frequency: int = 3
    """Inject a context nudge every N turns (0 disables turn-based nudges)."""
    nudge_step_frequency: int = 0
    """Inject a context nudge every N tool-call steps (0 disables step-based nudges)."""
    nudge_role: Literal["system", "user"] = "user"
    """Message role for injected nudges."""
    nudge_visible: bool = True
    """Whether nudge messages are visible to the frontend via EventBus."""
    inject_role: Literal["system", "user"] = "user"
    """Message role for injected pruning notifications."""
    clear_thinking_enabled: bool = True
    """Whether to clear thinking/reasoning blocks from older messages."""
    meta_tool_retention: int = 1
    """Number of recent meta-tool invocations to retain in context."""
    step_protection: int = 2
    """Number of recent steps protected from pruning."""
    protected_tool_patterns: list[str] = Field(default_factory=list)
    """Glob patterns matching tool names that should never be pruned."""
    protected_tools: set[str] = Field(default_factory=set)
    """Literal tool names that should never be pruned."""


class SkillActivationCapabilityConfig(BaseModel):
    """Config for ``SkillActivationCapability``."""

    type: Literal["skill_activation"] = "skill_activation"


class MemoryCapabilityConfig(BaseModel):
    """Config for ``MemoryCapability``."""

    type: Literal["memory"] = "memory"


class ModalityFilterCapabilityConfig(BaseModel):
    """Config for ``ModalityFilterCapability``.

    Provides per-modality degradation strategies for models that lack
    certain input modalities. When a tool returns content the model
    cannot process (e.g. image for a text-only model), the capability
    degrades it according to the configured strategy.
    """

    type: Literal["modality_filter"] = "modality_filter"
    image_strategy: Literal["describe", "drop", "pass"] = "describe"
    """Degradation strategy for unsupported image content."""
    audio_strategy: Literal["describe", "drop", "pass"] = "describe"
    """Degradation strategy for unsupported audio content."""
    video_strategy: Literal["describe", "drop", "pass"] = "describe"
    """Degradation strategy for unsupported video content."""
    document_strategy: Literal["describe", "drop", "pass"] = "describe"
    """Degradation strategy for unsupported document content."""


# ---------------------------------------------------------------------------
# Entry-point-based config
# ---------------------------------------------------------------------------


class EntryPointCapabilityConfig(BaseModel):
    """Configuration for a capability loaded via entry-point name.

    Used when ``type`` matches a name registered in the
    ``agentpool.capabilities`` entry-point group (e.g. ``"mermaid_lint"``).
    The entry-point registry maps short names to capability classes,
    enabling external packages to register capabilities without requiring
    users to know the full Python import path.
    """

    type: str
    """Entry-point name registered under ``agentpool.capabilities``."""

    args: dict[str, Any] = Field(default_factory=dict)
    """Arguments to pass to the capability constructor."""

    def build(self) -> Any:
        """Resolve the entry-point name and instantiate the capability.

        Uses :mod:`importlib.metadata` directly to discover entry points
        in the ``agentpool.capabilities`` group, avoiding a dependency
        on the :mod:`agentpool` package (which would violate the
        import-linter contract that ``agentpool_config`` must not import
        from ``agentpool``).

        Returns:
            Instantiated capability object.

        Raises:
            ValueError: If the entry-point name is not registered.
            ImportError: If the capability class cannot be loaded.
        """
        from importlib.metadata import entry_points

        eps = entry_points(group="agentpool.capabilities")
        for ep in eps:
            if ep.name == self.type:
                cls = ep.load()
                return cls(**self.args)

        available = sorted({ep.name for ep in eps})
        msg = (
            f"Unknown entry-point capability: {self.type!r}. "
            f"Available: {', '.join(available) if available else '(none)'}"
        )
        raise ValueError(msg)


# ---------------------------------------------------------------------------
# Generic / import-path-based config (backward compatible)
# ---------------------------------------------------------------------------


class GenericCapabilityConfig(BaseModel):
    """Configuration for a pydantic-ai capability loaded from YAML via import path.

    Used when ``type`` is a Python import path (e.g.
    ``'pydantic_ai.capabilities.Instrumentation'``) rather than a short name.
    """

    type: str
    """Import path to the capability class."""

    args: dict[str, Any] = Field(default_factory=dict)
    """Arguments to pass to the capability constructor."""

    def build(self) -> Any:
        """Import and instantiate the capability.

        Returns:
            Instantiated capability object.

        Raises:
            ImportError: If the module cannot be imported.
            ValueError: If the type path is invalid or the class not found.
        """
        try:
            module_path, class_name = self.type.rsplit(".", 1)
        except ValueError:
            msg = f"Invalid capability type path: {self.type!r}"
            raise ValueError(msg) from None

        try:
            module = __import__(module_path, fromlist=[class_name])
        except ImportError as e:
            msg = f"Cannot import module for capability {self.type!r}: {e}"
            raise ImportError(msg) from e

        try:
            cls = getattr(module, class_name)
        except AttributeError:
            msg = f"Class {class_name!r} not found in module {module_path!r}"
            raise ValueError(msg) from None

        return cls(**self.args)


# ---------------------------------------------------------------------------
# Discriminated union
# ---------------------------------------------------------------------------


BuiltinCapabilityConfig = Annotated[
    LoopDetectionCapabilityConfig
    | TokenBudgetCapabilityConfig
    | ToolOutputBudgetCapabilityConfig
    | DCPCapabilityConfig
    | SkillActivationCapabilityConfig
    | MemoryCapabilityConfig
    | ModalityFilterCapabilityConfig,
    Field(discriminator="type"),
]

CapabilityConfig = BuiltinCapabilityConfig | EntryPointCapabilityConfig | GenericCapabilityConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def is_known_capability_type(raw_type: str) -> bool:
    """Check if a type string is a known short capability name.

    Args:
        raw_type: The ``type`` field value from a YAML dict.

    Returns:
        ``True`` if it's a known short name (``"loop_detection"``, etc.).
    """
    return raw_type in KNOWN_CAPABILITY_TYPES


def build_capability(config: CapabilityConfig) -> Any:  # noqa: PLR0911, RET503
    """Build a capability from any config variant.

    For typed built-in configs, imports the corresponding capability class
    and constructs it with the config's fields. For generic configs, uses
    ``GenericCapabilityConfig.build()``.

    Args:
        config: A validated capability config (built-in or generic).

    Returns:
        An instantiated pydantic-ai ``AbstractCapability``.

    Raises:
        ImportError: If the module cannot be imported.
        ValueError: If the type is unknown or the class not found.
    """
    match config:
        case GenericCapabilityConfig():
            return config.build()
        case EntryPointCapabilityConfig():
            return config.build()
        case LoopDetectionCapabilityConfig():
            return _import_and_instantiate(IMPORT_MAP["loop_detection"], config)
        case TokenBudgetCapabilityConfig():
            return _import_and_instantiate(IMPORT_MAP["token_budget"], config)
        case ToolOutputBudgetCapabilityConfig():
            return _import_and_instantiate(IMPORT_MAP["tool_output_budget"], config)
        case DCPCapabilityConfig():
            return _import_and_instantiate(IMPORT_MAP["dcp"], config)
        case SkillActivationCapabilityConfig():
            return _import_and_instantiate(IMPORT_MAP["skill_activation"], config)
        case MemoryCapabilityConfig():
            return _import_and_instantiate(IMPORT_MAP["memory"], config)
        case ModalityFilterCapabilityConfig():
            return _import_and_instantiate(IMPORT_MAP["modality_filter"], config)
        case _ as unreachable:
            from typing import assert_never

            assert_never(unreachable)


def _import_and_instantiate(import_path: str, config: BaseModel) -> Any:
    """Import a capability class and construct it from a config model.

    Args:
        import_path: The fully qualified import path (module.ClassName).
        config: A typed config model. All fields except ``type`` are passed
            as constructor kwargs.

    Returns:
        An instantiated capability object.

    Raises:
        ImportError: If the module cannot be imported.
        ValueError: If the class is not found.
    """
    try:
        module_path, class_name = import_path.rsplit(".", 1)
    except ValueError:
        msg = f"Invalid import path: {import_path!r}"
        raise ValueError(msg) from None

    try:
        module = __import__(module_path, fromlist=[class_name])
    except ImportError as e:
        msg = f"Cannot import module for capability {import_path!r}: {e}"
        raise ImportError(msg) from e

    try:
        cls = getattr(module, class_name)
    except AttributeError:
        msg = f"Class {class_name!r} not found in module {module_path!r}"
        raise ValueError(msg) from None

    # Pass all fields except "type" as constructor kwargs
    kwargs = {k: v for k, v in config.model_dump(exclude={"type"}).items() if v is not None}
    return cls(**kwargs)
