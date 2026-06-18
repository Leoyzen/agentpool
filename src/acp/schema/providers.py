"""Provider schema definitions for ACP protocol."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from acp.schema.base import AnnotatedObject


LlmProtocol = str
"""LLM protocol identifier.

Known protocols include: "openai", "anthropic", "google", "mistral",
"cohere", "azure_openai", "bedrock". Unknown protocols are represented
as plain strings for forward compatibility.
"""


class ProviderCurrentConfig(AnnotatedObject):
    """Current provider configuration."""

    api_type: LlmProtocol
    """The LLM protocol this provider uses."""

    base_url: str
    """Base URL for the provider API."""

    headers: dict[str, str] | None = None
    """Optional custom headers for the provider API."""


class ProviderInfo(AnnotatedObject):
    """Information about a configurable LLM provider.

    Used in ACP `providers/list` responses to advertise
    available providers and their configuration.
    """

    id: str
    """Unique identifier for the provider (e.g., "openai", "anthropic")."""

    supported: list[LlmProtocol] = Field(default_factory=list)
    """List of LLM protocols this provider supports."""

    required: bool = False
    """Whether this provider is required (cannot be disabled)."""

    current: ProviderCurrentConfig | None = None
    """Current configuration if the provider is active."""


class ProvidersCapabilities(AnnotatedObject):
    """Capabilities related to the providers protocol surface."""

    # Empty for now — presence indicates providers/* methods are supported.
    # Future: max_providers, configurable_fields, etc.

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        """Override to return empty dict (capabilities object with no fields)."""
        return {}


class ProviderStatus:
    """Status of a provider (internal AgentPool use, not part of ACP spec)."""

    enabled = "enabled"
    """Provider is active and available for use."""

    disabled = "disabled"
    """Provider has been disabled and should not be used."""
