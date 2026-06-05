"""Capability configuration models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CapabilityConfig(BaseModel):
    """Configuration for a pydantic-ai capability loaded from YAML."""

    type: str
    """Import path to the capability class (e.g. 'pydantic_ai.capabilities.Instrumentation')."""

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
