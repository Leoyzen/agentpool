"""Resource-as-Tool bridge provider.

Exposes MCP resources as callable tools so LLMs can read resource content
on demand. Implements the Lazy (Tier 2) strategy from the Resource-as-Tool
research: a single read_resource tool with URI parameter and dynamic
description listing available resources.

Phase 2 adds Eager (Tier 1) mode: small/important resources are automatically
injected into the LLM context as instructions, bypassing the need for the
LLM to explicitly call the read_resource tool.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentpool.log import get_logger
from agentpool.resource_providers import ResourceProvider
from agentpool.resource_providers.resource_read_types import (
    DefaultEagerReadStrategy,
    EagerConfig,
    ReadTier,
    ResourceDataType,
    ResourceReadError,
    ResourceReadResult,
    ResourceSizeConfig,
    ResourceSizeController,
    SizeCheckResult,
    TransformChain,
)


if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai import RunContext
    from pydantic_ai.tools import ToolDefinition

    from agentpool.agents.context import AgentContext
    from agentpool.prompts.instructions import InstructionFunc
    from agentpool.resource_providers.resource_info import ResourceInfo
    from agentpool.resource_providers.resource_read_types import ReadStrategy
    from agentpool.tools.base import Tool

logger = get_logger(__name__)

# Maximum length for the resource catalog in tool description
_MAX_CATALOG_LENGTH = 2000


class ResourceReadProvider(ResourceProvider):
    """Bridges MCP resources to agent tools so LLMs can read resource content.

    This provider wraps a source ResourceProvider (typically MCPResourceProvider
    or AggregatingResourceProvider) and exposes a read_resource tool that allows
    the LLM to read any available resource by URI.

    Phase 1 implements Lazy (Tier 2) mode:
    - Single read_resource tool with URI parameter
    - Dynamic description listing available resources via prepare()
    - Size control and error handling
    - Data type classification (TEXT / MULTIMODAL / UNREADABLE / PROBE_NEEDED)

    Phase 2 adds Eager (Tier 1) mode:
    - Resources classified as EAGER by the read strategy are automatically
      injected into the LLM context as instructions
    - Only LAZY resources appear in the read_resource tool catalog
    - Configurable via EagerConfig and ReadStrategy

    Usage:
        # Wrap an existing MCP provider (lazy mode only)
        mcp_provider = MCPResourceProvider(server="my-server")
        read_provider = ResourceReadProvider(source_provider=mcp_provider)

        # With eager mode enabled
        eager_config = EagerConfig(eager_mime_types=("text/plain",))
        read_provider = ResourceReadProvider(
            source_provider=mcp_provider,
            eager_config=eager_config,
        )

        # Register with ToolManager
        tool_manager.add_provider(read_provider)

    Attributes:
        source_provider: The provider whose resources are exposed as tools
        size_config: Configuration for size limits and truncation
        eager_config: Configuration for eager resource injection
    """

    kind = "resource_read"  # type: ignore[assignment]  # matches extended ProviderKind

    def __init__(
        self,
        source_provider: ResourceProvider,
        name: str = "resource_read",
        owner: str | None = None,
        size_config: ResourceSizeConfig | None = None,
        eager_config: EagerConfig | None = None,
        read_strategy: ReadStrategy | None = None,
        transform_chain: TransformChain | None = None,
        rate_limit: int | None = None,
    ) -> None:
        """Initialize ResourceReadProvider.

        Args:
            source_provider: Provider whose resources will be exposed as tools
            name: Name for this provider instance
            owner: Optional owner (typically agent name)
            size_config: Size control configuration, uses defaults if None
            eager_config: Eager injection configuration, uses defaults if None
            read_strategy: Strategy for classifying resources as EAGER/LAZY/SKIP
            transform_chain: Chain of content transforms applied before size truncation
            rate_limit: Maximum read_resource calls per run. Falls back to
                size_config.rate_limit if not specified. 0 for unlimited.
        """
        super().__init__(name=name, owner=owner)
        self.source_provider = source_provider
        self.size_config = size_config or ResourceSizeConfig()
        self.eager_config = eager_config or EagerConfig()
        self._read_strategy = read_strategy or DefaultEagerReadStrategy(self.eager_config)
        self._transform_chain = transform_chain or TransformChain()
        self._resources_cache: list[ResourceInfo] | None = None
        self._eager_cache: dict[str, str] | None = None
        self._read_count: int = 0
        self._rate_limit: int = (
            rate_limit if rate_limit is not None else self.size_config.rate_limit
        )
        self._size_controller = ResourceSizeController(
            config=self.size_config,
            rate_limit=self._rate_limit,
        )

    async def get_resources(self) -> list[ResourceInfo]:
        """Forward resources from the source provider.

        The resources themselves are still owned by the source provider.
        This forwarding ensures aggregating providers can discover them.
        """
        if self._resources_cache is None:
            self._resources_cache = await self.source_provider.get_resources()
        return self._resources_cache

    async def get_tools(self) -> Sequence[Tool]:
        """Return a single read_resource tool for reading any available resource."""
        return [self._create_read_resource_tool()]

    async def get_instructions(self) -> list[InstructionFunc]:
        """Eagerly inject small resources into LLM context as instructions.

        Only resources classified as EAGER by the read strategy are included.
        Content is read and cached, respecting max_eager_chars budget.
        """
        resources = await self.get_resources()
        eager_resources = [r for r in resources if self._read_strategy.decide(r) is ReadTier.EAGER]

        if not eager_resources:
            return []

        # Build instruction function that injects eager resources
        async def eager_instruction(ctx: AgentContext) -> str:  # type: ignore[type-arg]
            return await self._build_eager_content(eager_resources)

        return [eager_instruction]

    async def _build_eager_content(self, resources: list[ResourceInfo]) -> str:
        """Build the eager instruction content from qualifying resources.

        Reads each resource, formats it, and respects the max_eager_chars budget.
        """
        parts: list[str] = []
        total_chars = 0
        max_chars = self.eager_config.max_eager_chars

        for resource in resources:
            try:
                content_parts = await resource.read()
                content = "\n".join(content_parts)
            except RuntimeError:
                continue

            entry = self._format_eager_entry(resource, content)

            if max_chars > 0 and total_chars + len(entry) > max_chars:
                # Budget exceeded — skip remaining
                remaining = len(resources) - len(parts) - 1  # -1 for current
                if remaining > 0:
                    parts.append(f"... {remaining} more eager resources omitted (budget exceeded)")
                break

            parts.append(entry)
            total_chars += len(entry)

        if not parts:
            return ""

        header = "The following resources are available and pre-loaded for your reference:"
        return header + "\n\n" + "\n\n".join(parts)

    def _format_eager_entry(self, resource: ResourceInfo, content: str) -> str:
        """Format a single resource entry for eager injection."""
        if self.eager_config.include_metadata:
            meta_parts = [f"Resource: {resource.uri}"]
            if resource.mime_type:
                meta_parts.append(f"Type: {resource.mime_type}")
            if resource.description:
                meta_parts.append(f"Description: {resource.description}")
            return "\n".join(meta_parts) + "\n" + content
        return content

    def _create_read_resource_tool(self) -> Tool:
        """Create the read_resource tool with dynamic description.

        The tool accepts a URI parameter and reads the corresponding resource.
        A prepare() function dynamically updates the tool description to list
        available resources each time the LLM considers calling it.
        """

        async def read_resource(ctx: AgentContext, uri: str) -> str:
            """Read content from a resource by URI.

            Args:
                ctx: Agent context (injected by framework)
                uri: URI of the resource to read

            Returns:
                Resource content as text, or structured error message
            """
            try:
                result = await self._read_resource(uri)
                return self._format_result(result)
            except ResourceReadError as e:
                return self._format_error(e)

        async def prepare_read_resource(
            ctx: RunContext[AgentContext],
            tool_def: ToolDefinition,
        ) -> ToolDefinition:
            """Dynamically update tool description with available resource catalog."""
            resources = await self.get_resources()
            # Filter: only include LAZY resources in tool description
            lazy_resources = [
                r for r in resources if self._read_strategy.decide(r) is ReadTier.LAZY
            ]
            catalog = self._build_resource_catalog(lazy_resources)
            description = (
                "Read content from an MCP resource by its URI. "
                "Use this tool when you need to access resource content "
                "that was referenced in the conversation.\n\n"
                f"Available resources:\n{catalog}"
            )
            from pydantic_ai.tools import ToolDefinition as PydanticToolDefinition

            return PydanticToolDefinition(
                name=tool_def.name,
                description=description,
                parameters_json_schema=tool_def.parameters_json_schema,
            )

        return self.create_tool(
            fn=read_resource,
            name_override="read_resource",
            description_override="Read content from an MCP resource by its URI.",
            category="fetch",
            read_only=True,
            prepare=prepare_read_resource,
        )

    def _build_resource_catalog(self, resources: list[ResourceInfo]) -> str:
        """Build a concise catalog of available resources for tool description.

        Args:
            resources: List of resources to catalog

        Returns:
            Formatted string listing resources with URI and description
        """
        lines: list[str] = []
        remaining = _MAX_CATALOG_LENGTH

        for resource in resources:
            line = f"- {resource.uri}"
            if resource.description:
                line += f": {resource.description}"
            if resource.mime_type:
                line += f" [{resource.mime_type}]"

            if len(line) + 1 > remaining:
                lines.append(f"... and {len(resources) - len(lines)} more resources")
                break

            lines.append(line)
            remaining -= len(line) + 1

        return "\n".join(lines)

    async def _read_resource(self, uri: str) -> ResourceReadResult:
        """Read a resource with full error handling and size control.

        Implements the size control strategy:
        1. Validate URI exists in available resources
        2. Classify data type and check readability
        3. Rate limit check
        4. Read content
        5. Post-read size gate: refuse content exceeding max_read_bytes
        6. Apply content transforms
        7. Apply post-read truncation if needed

        Args:
            uri: URI of the resource to read

        Returns:
            ResourceReadResult with content and metadata

        Raises:
            ResourceReadError: If resource cannot be read
        """
        # Rate limiting via controller
        if not self._size_controller.check_rate_limit():
            raise ResourceReadError(
                uri=uri,
                reason=f"Rate limit exceeded ({self._rate_limit} reads per run). "
                f"Use read_resource judiciously — only for resources you truly need.",
                data_type=ResourceDataType.UNREADABLE,
            )
        self._size_controller.increment_read_count()
        self._read_count += 1

        # Find the resource by URI
        resources = await self.get_resources()
        resource = next((r for r in resources if r.uri == uri), None)

        if resource is None:
            available = [r.uri for r in resources]
            raise ResourceReadError(
                uri=uri,
                reason=f"Resource not found. Available: {available}",
                data_type=ResourceDataType.UNREADABLE,
            )

        # Classify data type
        data_type = self._classify_data_type(resource)

        # Check readability
        if data_type == ResourceDataType.UNREADABLE:
            raise ResourceReadError(
                uri=uri,
                reason=f"Resource has unreadable binary content (MIME: {resource.mime_type})",
                data_type=data_type,
            )

        # Pre-read size check using ResourceInfo.size when available
        if resource.size is not None:
            size_check = self._size_controller.pre_read_check(resource.size)
            if size_check == SizeCheckResult.DENY_TOO_LARGE:
                raise ResourceReadError(
                    uri=uri,
                    reason=f"Resource too large: {resource.size} bytes exceeds "
                    f"max_read_bytes limit of {self.size_config.max_read_bytes}. "
                    f"Consider using a transform to reduce size, or increase the limit.",
                    data_type=data_type,
                )

        # Handle MULTIMODAL resources — provide metadata, not raw binary
        if data_type == ResourceDataType.MULTIMODAL:
            content_parts = await resource.read()
            raw_text = "\n".join(content_parts)
            # Check if the content is just a stub placeholder
            is_stub = raw_text.startswith("[Binary data:") and raw_text.endswith("bytes]")
            if is_stub:
                return ResourceReadResult(
                    uri=uri,
                    content=f"[Multimodal resource: {resource.mime_type}] "
                    f"URI: {uri} — This is a binary resource that cannot be rendered as text. "
                    f"Use the URI to reference this resource in multimodal-capable contexts.",
                    data_type=ResourceDataType.MULTIMODAL,
                    mime_type=resource.mime_type,
                )
            # If somehow we got real text content for a multimodal type, return it
            return ResourceReadResult(
                uri=uri,
                content=raw_text,
                data_type=ResourceDataType.MULTIMODAL,
                mime_type=resource.mime_type,
            )

        # Read the resource content
        try:
            content_parts = await resource.read()
        except RuntimeError as e:
            raise ResourceReadError(
                uri=uri,
                reason=f"No reader available: {e}",
                data_type=data_type,
            ) from e

        # Combine content parts
        full_content = "\n".join(content_parts)

        # Post-read size gate: refuse content that exceeds max_read_bytes
        max_read_bytes = self.size_config.max_read_bytes
        if max_read_bytes > 0 and len(full_content.encode("utf-8")) > max_read_bytes:
            raise ResourceReadError(
                uri=uri,
                reason=f"Resource content too large: {len(full_content.encode('utf-8'))} bytes "
                f"exceeds max_read_bytes limit of {max_read_bytes}. "
                f"Consider using a transform to reduce size, or increase the limit.",
                data_type=data_type,
            )

        original_size = len(full_content)

        # Apply content transforms (before size-control truncation)
        if self._transform_chain.transforms:
            full_content = await self._transform_chain.apply(
                full_content,
                ResourceReadResult(
                    uri=uri,
                    content=full_content,
                    data_type=data_type,
                    mime_type=resource.mime_type,
                ),
            )
            # Track original size before truncation (post-transform)
            original_size = len(full_content)

        # Apply post-read truncation
        truncated = False
        max_chars = self.size_config.max_content_chars
        if max_chars > 0 and original_size > max_chars:
            full_content = full_content[:max_chars] + self.size_config.truncate_message.format(
                original=original_size,
                limit=max_chars,
            )
            truncated = True

        return ResourceReadResult(
            uri=uri,
            content=full_content,
            data_type=data_type,
            truncated=truncated,
            original_size=original_size if truncated else None,
            mime_type=resource.mime_type,
        )

    def _classify_data_type(self, resource: ResourceInfo) -> ResourceDataType:
        """Classify a resource's data type based on its MIME type.

        Uses the centralized mime_utils module for classification.
        Falls back to PROBE_NEEDED for unknown MIME types.

        Args:
            resource: Resource to classify

        Returns:
            ResourceDataType classification
        """
        from agentpool.mime_utils import is_binary_mime, is_text_mime

        mime_type = resource.mime_type

        # Known text types
        if is_text_mime(mime_type):
            return ResourceDataType.TEXT

        # Known binary types that LLMs can handle as multimodal
        if mime_type and (
            mime_type.startswith(("image/", "audio/")) or mime_type == "application/pdf"
        ):
            return ResourceDataType.MULTIMODAL

        # Known binary types that are unreadable for LLMs
        if is_binary_mime(mime_type):
            return ResourceDataType.UNREADABLE

        # Unknown — would need content probing
        return ResourceDataType.PROBE_NEEDED

    def _format_result(self, result: ResourceReadResult) -> str:
        """Format a successful read result for LLM consumption.

        Args:
            result: The read result to format

        Returns:
            Formatted string for the LLM
        """
        parts: list[str] = []

        if result.mime_type:
            parts.append(f"[Resource: {result.uri} ({result.mime_type})]")
        else:
            parts.append(f"[Resource: {result.uri}]")

        parts.append(result.content)

        if result.truncated:
            parts.append(f"[Content truncated: {result.original_size} → shown chars limited]")

        return "\n".join(parts)

    def _format_error(self, error: ResourceReadError) -> str:
        """Format a read error for LLM consumption.

        Provides structured error information so the LLM can reason
        about alternatives (e.g., try a different resource, ask for help).

        Args:
            error: The read error to format

        Returns:
            Formatted error string for the LLM
        """
        return (
            f"[Error reading resource {error.uri!r}: {error.reason}] "
            f"[Data type: {error.data_type.value}]"
        )

    def invalidate_cache(self) -> None:
        """Invalidate the resources cache.

        Call this when the source provider's resources may have changed,
        e.g., after receiving a resources_changed signal.
        """
        self._resources_cache = None
        self._eager_cache = None

    def reset_read_count(self) -> None:
        """Reset the read count (e.g., at the start of a new agent run)."""
        self._read_count = 0
        self._size_controller.reset_read_count()
