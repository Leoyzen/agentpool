"""MCPCapability — wraps a single MCP server connection as a pydantic-ai capability.

Part of the capability-native migration (M3). This capability replaces
:class:`~MCPCapability` for
the toolset + resource access use case. It implements both
:class:`~pydantic_ai.capabilities.AbstractCapability` (for tools, instructions,
lifecycle) and :class:`~agentpool.capabilities.resource_source.ResourceSource`
(for read-only resource access).

Key design:
    - ``get_toolset()`` returns a ``ToolsetFunc`` that lazily builds an
      :class:`~pydantic_ai.mcp.MCPToolset` per-run from the wrapped client.
    - ``list()`` / ``read()`` / ``exists()`` implement the ``ResourceSource``
      protocol with ``mcp://{server_name}/{path}`` URI scheme.
    - ``on_change()`` (AbstractCapability) yields ``ChangeEvent(kind="tools_changed")``
      when the MCP server sends ``notifications/tools/list_changed``.
    - ``on_change()`` (ResourceSource) returns ``None`` — MCP resource changes
      are not exposed as a stream (only tools are).
    - ``__aenter__`` / ``__aexit__`` manage the MCP client connection lifecycle.
"""

from __future__ import annotations

import asyncio
import base64
from typing import TYPE_CHECKING, Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import AgentDepsT, RunContext

from agentpool.capabilities.change_event import ChangeEvent
from agentpool.capabilities.resource_source import (
    Resource,
    ResourceContent,
    ResourceNotFoundError,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from types import TracebackType

    from pydantic_ai.toolsets import AbstractToolset

    from agentpool.mcp_server.client import MCPClient


class MCPCapability(AbstractCapability[AgentDepsT]):
    """Wraps a single MCP server connection as a pydantic-ai capability.

    Provides:
    - Tools via :meth:`get_toolset` (lazily builds ``MCPToolset`` per-run).
    - Resource access via :meth:`list`, :meth:`read`, :meth:`exists`.
    - Change notifications via :meth:`on_change` (yields ``ChangeEvent``).
    - Lifecycle via ``__aenter__`` / ``__aexit__``.

    The same object implements both ``AbstractCapability`` and
    ``ResourceSource`` — the two interfaces are orthogonal.
    """

    def __init__(
        self,
        client: MCPClient,
        *,
        name: str | None = None,
    ) -> None:
        """Initialize the capability.

        Args:
            client: The ``MCPClient`` wrapping a single MCP server.
            name: Optional name override. Defaults to the server config name.
        """
        self._client = client
        resolved_name = name or client.config.name
        if resolved_name is None:
            resolved_name = "mcp"
        self._name: str = resolved_name
        self._uri_prefix_str = f"mcp://{self._name}/"
        self._change_queue: asyncio.Queue[ChangeEvent] = asyncio.Queue()

    # ---- Properties ----

    @property
    def name(self) -> str:
        """Return the capability name."""
        return self._name

    @property
    def client(self) -> MCPClient:
        """Return the wrapped MCP client."""
        return self._client

    # ---- URI helpers ----

    def _strip_prefix(self, uri: str) -> str | None:
        """Strip the ``mcp://{server_name}/`` prefix from a URI.

        Args:
            uri: URI to strip.

        Returns:
            The original MCP resource URI, or ``None`` if the prefix
            doesn't match.
        """
        if not uri.startswith(self._uri_prefix_str):
            return None
        return uri[len(self._uri_prefix_str) :]

    # ---- AbstractCapability overrides ----

    def get_toolset(self) -> Any:
        """Return a ``ToolsetFunc`` that lazily builds an ``MCPToolset``.

        The returned async callable is invoked once per agent run. It
        constructs an :class:`~pydantic_ai.mcp.MCPToolset` from the
        wrapped client's underlying FastMCP client, enabling native
        pydantic-ai tool integration.

        Returns:
            A ``ToolsetFunc`` or ``None`` if no tools are available.
        """
        from pydantic_ai.mcp import MCPToolset

        async def _build_toolset(
            ctx: RunContext[AgentDepsT],
        ) -> AbstractToolset[AgentDepsT] | None:
            del ctx  # Tools are server-scoped, not run-scoped.
            return MCPToolset(
                client=self._client._client,
                id=self._name,
                include_instructions=True,
            )

        return _build_toolset

    def get_instructions(self) -> None:
        """Return instructions for the system prompt, or ``None``.

        MCP server instructions are handled by the ``MCPToolset`` itself
        when ``include_instructions=True`` is set. This method returns
        ``None`` to avoid duplication.
        """

    async def on_change(self) -> AsyncIterator[ChangeEvent]:
        """Yield ``ChangeEvent`` when the MCP server's tools change.

        Subscribes to the ``notifications/tools/list_changed`` notification
        via the client's ``_tool_change_callback``. When the server sends
        a tool list change notification, this generator yields a
        ``ChangeEvent(kind="tools_changed")``.

        This is an async generator::

            async for event in cap.on_change():
                print(event.capability_name, event.kind)

        Yields:
            ``ChangeEvent`` with ``kind="tools_changed"``.
        """
        original_callback = self._client._tool_change_callback

        async def _on_tools_changed() -> None:
            await self._change_queue.put(
                ChangeEvent(capability_name=self._name, kind="tools_changed"),
            )

        self._client._tool_change_callback = _on_tools_changed

        try:
            while True:
                event = await self._change_queue.get()
                yield event
        finally:
            self._client._tool_change_callback = original_callback

    # ---- ResourceSource protocol (structural) ----

    async def list(self) -> list[Resource]:
        """Enumerate all resources from the MCP server.

        Resources are returned with ``mcp://{server_name}/{path}`` URIs,
        where ``{path}`` is the original MCP resource URI.

        Returns:
            List of ``Resource`` descriptors. Empty if no resources available.
        """
        from mcp.types import Resource as MCPResource  # noqa: TC002

        mcp_resources: list[MCPResource] = await self._client.list_resources()
        result: list[Resource] = []
        for res in mcp_resources:
            original_uri = str(res.uri)
            wrapped_uri = f"{self._uri_prefix_str}{original_uri}"
            mime_type = res.mimeType if res.mimeType is not None else "application/octet-stream"
            description = res.description if res.description is not None else ""
            result.append(
                Resource(
                    uri=wrapped_uri,
                    name=res.name,
                    mime_type=mime_type,
                    description=description,
                ),
            )
        return result

    async def read(self, uri: str) -> ResourceContent:
        """Read resource content by URI.

        Strips the ``mcp://{server_name}/`` prefix and calls the MCP
        ``resources/read`` method on the underlying client.

        Args:
            uri: Resource URI in ``mcp://{server_name}/{path}`` format.

        Returns:
            ``ResourceContent`` with the resource data.

        Raises:
            ResourceNotFoundError: If the URI doesn't match the prefix or
                the resource doesn't exist on the server.
        """
        from mcp.types import (
            BlobResourceContents,
            TextResourceContents,
        )

        original_uri = self._strip_prefix(uri)
        if original_uri is None:
            raise ResourceNotFoundError(uri)
        try:
            contents = await self._client.read_resource(original_uri)
        except Exception:  # noqa: BLE001
            raise ResourceNotFoundError(uri) from None
        if not contents:
            raise ResourceNotFoundError(uri)
        first = contents[0]
        content: str | bytes
        mime_type = "application/octet-stream"
        if first.mimeType is not None:
            mime_type = first.mimeType
        match first:
            case TextResourceContents(text=text):
                content = text
            case BlobResourceContents(blob=blob_str):
                content = base64.b64decode(blob_str)
            case _:
                content = str(first)
        return ResourceContent(
            uri=uri,
            content=content,
            mime_type=mime_type,
        )

    async def exists(self, uri: str) -> bool:
        """Check if a resource exists.

        Args:
            uri: Resource URI in ``mcp://{server_name}/{path}`` format.

        Returns:
            True if the resource exists on the server, False otherwise.
        """
        original_uri = self._strip_prefix(uri)
        if original_uri is None:
            return False
        try:
            resources = await self._client.list_resources()
        except Exception:  # noqa: BLE001
            return False
        return any(str(r.uri) == original_uri for r in resources)

    # ---- Lifecycle ----

    async def __aenter__(self) -> MCPCapability[AgentDepsT]:
        """Enter async context, connecting the MCP client."""
        await self._client.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit async context, disconnecting the MCP client."""
        await self._client.__aexit__(exc_type, exc_val, exc_tb)
