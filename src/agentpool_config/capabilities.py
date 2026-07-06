"""Capability configuration models.

Typed config models for each of the 6 built-in capabilities, forming a
discriminated union that validates YAML inputs at load time.

Backward-compatible: if ``type`` is a Python import path (not one of the
known short names), the original ``GenericCapabilityConfig`` is used.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


KNOWN_CAPABILITY_TYPES: frozenset[str] = frozenset({
    "loop_detection",
    "token_budget",
    "tool_output_budget",
    "dynamic_context",
    "skill_activation",
    "memory",
})

IMPORT_MAP: dict[str, str] = {
    "loop_detection": "agentpool.capabilities.loop_detection.LoopDetectionCapability",
    "token_budget": "agentpool.capabilities.token_budget.TokenBudgetCapability",
    "tool_output_budget": ("agentpool.capabilities.tool_output_budget.ToolOutputBudgetCapability"),
    "dynamic_context": "agentpool.capabilities.dynamic_context.DynamicContextCapability",
    "skill_activation": "agentpool.capabilities.skill_activation.SkillActivationCapability",
    "memory": "agentpool.capabilities.memory.MemoryCapability",
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


class DynamicContextCapabilityConfig(BaseModel):
    """Config for ``DynamicContextCapability``."""

    type: Literal["dynamic_context"] = "dynamic_context"
    max_messages: int = 50
    """Max messages before compaction triggers."""
    compaction_threshold: float = 0.8
    """Fraction of ``max_messages`` that triggers compaction (0 < t <= 1)."""


class SkillActivationCapabilityConfig(BaseModel):
    """Config for ``SkillActivationCapability``."""

    type: Literal["skill_activation"] = "skill_activation"


class MemoryCapabilityConfig(BaseModel):
    """Config for ``MemoryCapability``."""

    type: Literal["memory"] = "memory"


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
    | DynamicContextCapabilityConfig
    | SkillActivationCapabilityConfig
    | MemoryCapabilityConfig,
    Field(discriminator="type"),
]

CapabilityConfig = BuiltinCapabilityConfig | GenericCapabilityConfig


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
        case LoopDetectionCapabilityConfig():
            return _import_and_instantiate(IMPORT_MAP["loop_detection"], config)
        case TokenBudgetCapabilityConfig():
            return _import_and_instantiate(IMPORT_MAP["token_budget"], config)
        case ToolOutputBudgetCapabilityConfig():
            return _import_and_instantiate(IMPORT_MAP["tool_output_budget"], config)
        case DynamicContextCapabilityConfig():
            return _import_and_instantiate(IMPORT_MAP["dynamic_context"], config)
        case SkillActivationCapabilityConfig():
            return _import_and_instantiate(IMPORT_MAP["skill_activation"], config)
        case MemoryCapabilityConfig():
            return _import_and_instantiate(IMPORT_MAP["memory"], config)
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
