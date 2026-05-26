"""Provider schema definitions for ACP protocol."""

from __future__ import annotations

from enum import StrEnum

from acp.schema.base import AnnotatedObject


LlmProtocol = str
"""LLM protocol identifier.

Known protocols include: "openai", "anthropic", "google", "mistral",
"cohere", "azure_openai", "bedrock". Unknown protocols are represented
as plain strings for forward compatibility.
"""


class ProviderStatus(StrEnum):
    """Status of a provider."""

    enabled = "enabled"
    """Provider is active and available for use."""

    disabled = "disabled"
    """Provider has been disabled and should not be used."""


class ProviderInfo(AnnotatedObject):
    """Information about a configurable LLM provider.

    Used in ACP `providers/list` responses to advertise
    available providers and their configuration.
    """

    id: str
    """Unique identifier for the provider (e.g., "openai", "anthropic")."""

    name: str
    """Human-readable display name for the provider."""

    protocol: LlmProtocol
    """The LLM protocol this provider implements."""

    base_url: str | None = None
    """Optional custom base URL for the provider API."""

    api_key_id: str | None = None
    """Optional identifier for the API key (not the key itself)."""

    status: ProviderStatus = ProviderStatus.enabled
    """Current status of the provider."""
