"""Types for the Resource-as-Tool bridge.

Provides data classification, size control, error handling, result types,
and read strategy types for reading MCP resources through the agent tool
interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool.resource_providers.resource_info import ResourceInfo


class SizeCheckResult(StrEnum):
    """Result of a pre-read size check."""

    ALLOW = "allow"
    """Resource is within size limits, proceed with reading."""

    DENY_TOO_LARGE = "deny_too_large"
    """Resource exceeds max_read_bytes, refuse to read."""

    DENY_RATE_LIMITED = "deny_rate_limited"
    """Read count exceeds rate limit."""

    WARN_LARGE = "warn_large"
    """Resource is large but within limits. Consider transforms."""


class ResourceSizeController:
    """Controller that enforces size limits and rate limiting.

    Encapsulates the decision logic for whether a resource read should
    proceed, combining pre-read checks with post-read truncation config.
    """

    def __init__(
        self,
        config: ResourceSizeConfig | None = None,
        rate_limit: int = 10,
    ) -> None:
        self.config = config or ResourceSizeConfig()
        self.rate_limit = rate_limit
        self._read_count: int = 0

    def pre_read_check(self, content_size_bytes: int) -> SizeCheckResult:
        """Check if a resource read should proceed based on content size.

        Args:
            content_size_bytes: Size of the resource content in bytes

        Returns:
            SizeCheckResult indicating whether to allow, deny, or warn
        """
        max_bytes = self.config.max_read_bytes
        if max_bytes > 0 and content_size_bytes > max_bytes:
            return SizeCheckResult.DENY_TOO_LARGE

        # Warn for content approaching the limit (80% threshold)
        if max_bytes > 0 and content_size_bytes > max_bytes * 0.8:
            return SizeCheckResult.WARN_LARGE

        return SizeCheckResult.ALLOW

    def check_rate_limit(self) -> bool:
        """Check if the rate limit has been exceeded.

        Returns:
            True if the rate limit has NOT been exceeded (read is allowed)
        """
        if self.rate_limit <= 0:
            return True
        return self._read_count < self.rate_limit

    def increment_read_count(self) -> int:
        """Increment the read counter and return the new count."""
        self._read_count += 1
        return self._read_count

    def reset_read_count(self) -> None:
        """Reset the read counter (e.g., at the start of a new agent run)."""
        self._read_count = 0


class ReadTier(StrEnum):
    """Strategy tier for resource exposure."""

    EAGER = "eager"
    """Inject resource content directly into LLM instructions."""

    LAZY = "lazy"
    """Expose resource as a tool parameter the LLM can call on demand."""

    SKIP = "skip"
    """Do not expose this resource at all."""


class ResourceDataType(StrEnum):
    """Classification of resource content type for read strategy decisions.

    Determines how the resource content should be handled when exposed
    to the LLM through tools or instructions.
    """

    TEXT = "text"
    """Text content that can be directly included in LLM context."""

    MULTIMODAL = "multimodal"
    """Binary content that has a known media type (images, audio, etc.)
    and can be represented as structured content blocks."""

    UNREADABLE = "unreadable"
    """Binary content with no known representation for LLM consumption.
    Will be summarized as metadata only (size, MIME type)."""

    PROBE_NEEDED = "probe_needed"
    """MIME type is ambiguous — content probing required to determine
    if content is text or binary."""


@dataclass(frozen=True, slots=True)
class ResourceSizeConfig:
    """Configuration for resource size control.

    Implements size control and rate limiting:
    1. Rate limiting: limit number of read_resource calls per agent run
    2. Post-read size gate: refuse content exceeding max_read_bytes
    3. Post-read truncation: truncate content to max_content_chars
    """

    max_read_bytes: int = 1_000_000
    """Maximum bytes to read from a resource (pre-read gate). -1 for unlimited."""

    max_content_chars: int = 100_000
    """Maximum characters to include in LLM context (post-read truncation).
    -1 for unlimited."""

    rate_limit: int = 10
    """Maximum number of read_resource calls per agent run. 0 or -1 for unlimited."""

    truncate_message: str = "... [truncated: {original} chars → {limit} chars]"
    """Message appended when content is truncated.
    Supports {original} and {limit} placeholders."""


@dataclass(frozen=True, slots=True)
class ResourceReadResult:
    """Structured result from reading a resource.

    Carries the content, metadata, and size information needed
    for both tool responses and future ContentTransform pipelines.
    """

    uri: str
    """URI of the resource that was read."""

    content: str
    """Text content from the resource (possibly truncated)."""

    data_type: ResourceDataType
    """Classified data type of the resource content."""

    truncated: bool = False
    """Whether the content was truncated due to size limits."""

    original_size: int | None = None
    """Original size in characters before truncation, if truncated."""

    mime_type: str | None = None
    """MIME type of the resource, if known."""

    error: str | None = None
    """Error message if the read partially failed, None on full success."""


class ResourceReadError(Exception):
    """Structured error for resource read failures.

    Provides machine-readable error information so the LLM
    can make informed decisions about alternative approaches.
    """

    def __init__(
        self,
        uri: str,
        reason: str,
        data_type: ResourceDataType = ResourceDataType.UNREADABLE,
    ) -> None:
        self.uri = uri
        self.reason = reason
        self.data_type = data_type
        super().__init__(f"Cannot read resource {uri!r}: {reason}")

    uri: str
    """URI of the resource that failed to read."""

    reason: str
    """Human-readable reason for the failure."""

    data_type: ResourceDataType
    """Classified data type of the resource."""


class ContentTransform(Protocol):
    """Protocol for content transformation pipeline (Phase 3 interface).

    Implementations can perform truncation, compression, summarization,
    or any other content transformation on resource content before
    it reaches the LLM.
    """

    async def transform(self, content: str, result: ResourceReadResult) -> str:
        """Transform resource content.

        Args:
            content: The raw content to transform
            result: The read result with metadata for context-aware transforms

        Returns:
            Transformed content string
        """
        ...


class TransformChain:
    """Chain of ContentTransform instances applied sequentially.

    Transforms are applied in order — the output of each transform
    becomes the input of the next.
    """

    def __init__(self, transforms: Sequence[ContentTransform] = ()) -> None:
        self._transforms = list(transforms)

    @property
    def transforms(self) -> list[ContentTransform]:
        return list(self._transforms)

    async def apply(self, content: str, result: ResourceReadResult) -> str:
        """Apply all transforms sequentially.

        Args:
            content: The raw content to transform
            result: The read result with metadata for context-aware transforms

        Returns:
            Transformed content after all transforms applied
        """
        transformed = content
        for transform in self._transforms:
            transformed = await transform.transform(transformed, result)
        return transformed

    def add(self, transform: ContentTransform) -> None:
        """Add a transform to the end of the chain."""
        self._transforms.append(transform)


class ReadStrategy(Protocol):
    """Protocol for deciding how a resource should be exposed to the LLM.

    Implementations determine whether a resource should be:
    - Eagerly injected as instructions (Tier 1)
    - Lazily exposed as a tool parameter (Tier 2)
    - Skipped entirely
    """

    def decide(self, resource: ResourceInfo) -> ReadTier:
        """Decide how to expose a resource.

        Args:
            resource: The resource to evaluate

        Returns:
            ReadTier indicating the exposure strategy
        """
        ...


@dataclass(frozen=True, slots=True)
class EagerConfig:
    """Configuration for eager (Tier 1) resource injection.

    Controls which resources are automatically injected into the LLM
    context as instructions, and how they are formatted.
    """

    max_eager_chars: int = 10_000
    """Maximum total characters across all eagerly injected resources."""

    eager_mime_types: tuple[str, ...] = (
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/json",
    )
    """MIME types eligible for eager injection. Empty tuple means no resources are eager."""

    eager_uri_prefixes: tuple[str, ...] = ()
    """URI prefixes eligible for eager injection. Empty tuple means no prefix filter."""

    include_metadata: bool = True
    """Whether to include resource metadata (URI, MIME type) in injected instructions."""


class DefaultEagerReadStrategy:
    """Default strategy: resources with matching MIME types and small size are EAGER.

    Logic:
    1. If eager_mime_types is empty, everything is LAZY
    2. If resource MIME matches eager_mime_types → EAGER
    3. If resource URI starts with any eager_uri_prefixes → EAGER
    4. Otherwise → LAZY
    """

    def __init__(self, config: EagerConfig | None = None) -> None:
        self._config = config or EagerConfig()

    def decide(self, resource: ResourceInfo) -> ReadTier:
        # If no eager filters configured, everything is lazy
        if not self._config.eager_mime_types and not self._config.eager_uri_prefixes:
            return ReadTier.LAZY

        # Check URI prefix match
        if self._config.eager_uri_prefixes and any(
            resource.uri.startswith(prefix) for prefix in self._config.eager_uri_prefixes
        ):
            return ReadTier.EAGER

        # Check MIME type match (None mime_type doesn't match eager list)
        if self._config.eager_mime_types and resource.mime_type in self._config.eager_mime_types:
            return ReadTier.EAGER

        return ReadTier.LAZY
