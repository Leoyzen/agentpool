"""ResourceSource protocol and AggregatedResourceSource.

Provides unified read-only data access for MCP resources, skill content,
and other data sources. Orthogonal to ``AbstractCapability`` — the same
object MAY implement both interfaces.

The protocol defines four methods:
    - ``list()`` — enumerate all available resources
    - ``read(uri)`` — read resource content by URI
    - ``exists(uri)`` — check if a resource exists
    - ``on_change()`` — subscribe to resource changes (None for static sources)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@dataclass(frozen=True, slots=True)
class Resource:
    """A resource descriptor returned by ``ResourceSource.list()``.

    Attributes:
        uri: Unique resource identifier (e.g., ``mcp://server/path``).
        name: Human-readable name of the resource.
        mime_type: MIME type of the resource content.
        description: Optional description of the resource.
    """

    uri: str
    name: str
    mime_type: str = "application/octet-stream"
    description: str = ""


@dataclass(frozen=True, slots=True)
class ResourceContent:
    """Content of a resource returned by ``ResourceSource.read()``.

    Attributes:
        uri: Unique resource identifier that was read.
        content: Resource content as text or binary.
        mime_type: MIME type of the content.
    """

    uri: str
    content: str | bytes
    mime_type: str = "application/octet-stream"


@dataclass(frozen=True, slots=True)
class ResourceChange:
    """A change event for a resource.

    Attributes:
        uri: URI of the resource that changed.
        kind: Type of change: added, removed, or modified.
    """

    uri: str
    kind: Literal["added", "removed", "modified"] = "modified"


class ResourceNotFoundError(Exception):
    """Raised when a resource URI is not found in any source.

    Args:
        uri: The URI that was not found.
    """

    def __init__(self, uri: str) -> None:
        self.uri = uri
        super().__init__(f"Resource not found: {uri!r}")


@runtime_checkable
class ResourceSource(Protocol):
    """Protocol for read-only resource access.

    Implementations provide MCP resources, skill content, or other data
    sources through a unified interface. The same object MAY also
    implement ``AbstractCapability`` — the two interfaces are orthogonal.
    """

    async def list(self) -> list[Resource]:
        """Enumerate all available resources.

        Returns:
            List of Resource descriptors. Empty if no resources available.
        """
        ...

    async def read(self, uri: str) -> ResourceContent:
        """Read resource content by URI.

        Args:
            uri: Unique resource identifier.

        Returns:
            ResourceContent with the resource data.

        Raises:
            ResourceNotFoundError: If the URI does not exist.
        """
        ...

    async def exists(self, uri: str) -> bool:
        """Check if a resource exists.

        Args:
            uri: Unique resource identifier.

        Returns:
            True if the resource exists, False otherwise.
        """
        ...

    def on_change(self) -> AsyncIterator[ResourceChange] | None:
        """Subscribe to resource change notifications.

        Returns:
            An async iterator of ResourceChange events, or None for
            static sources that never change.
        """
        ...


class AggregatedResourceSource:
    """Compose multiple ``ResourceSource`` instances into a unified interface.

    Created by ``AgentFactory`` at compile time, scoped to the agent's
    authorized resources. Not a global registry.

    - ``list()`` merges resources from all sources.
    - ``read(uri)`` routes to the first source whose ``exists()`` returns True.
    - ``exists(uri)`` returns True if any source recognizes the URI.
    - ``on_change()`` merges change streams from all dynamic sources.
    """

    def __init__(self, sources: list[ResourceSource]) -> None:
        """Initialize with a list of resource sources to aggregate.

        Args:
            sources: ResourceSource instances to compose.
        """
        self._sources: list[ResourceSource] = list(sources)

    @property
    def sources(self) -> list[ResourceSource]:
        """Get the list of composed sources."""
        return list(self._sources)

    async def list(self) -> list[Resource]:
        """List all resources from all composed sources.

        Returns:
            Merged list of Resource descriptors from every source.
        """
        merged: list[Resource] = []
        for source in self._sources:
            merged.extend(await source.list())
        return merged

    async def read(self, uri: str) -> ResourceContent:
        """Read a resource by URI, routing to the correct source.

        Tries each source's ``exists()`` method. The first source that
        recognizes the URI is used to read the content.

        Args:
            uri: Unique resource identifier.

        Returns:
            ResourceContent from the owning source.

        Raises:
            ResourceNotFoundError: If no source recognizes the URI.
        """
        for source in self._sources:
            if await source.exists(uri):
                return await source.read(uri)
        raise ResourceNotFoundError(uri)

    async def exists(self, uri: str) -> bool:
        """Check if any composed source recognizes the URI.

        Args:
            uri: Unique resource identifier.

        Returns:
            True if any source has the resource, False otherwise.
        """
        for source in self._sources:
            if await source.exists(uri):
                return True
        return False

    def on_change(self) -> AsyncIterator[ResourceChange] | None:
        """Merge change streams from all dynamic sources.

        Returns:
            An async iterator yielding changes from all sources that
            have change streams. None if all sources are static.
        """
        streams: list[AsyncIterator[ResourceChange]] = []
        for source in self._sources:
            stream = source.on_change()
            if stream is not None:
                streams.append(stream)

        if not streams:
            return None

        async def _merged() -> AsyncIterator[ResourceChange]:
            """Concurrently merge all change streams into one.

            Spawns a consumer task per stream. Each consumer iterates its
            stream and puts events into a shared queue. The merged
            generator yields from the queue. All tasks are cancelled and
            cleaned up when the consumer stops.
            """
            queue: asyncio.Queue[ResourceChange | None] = asyncio.Queue()
            tasks: list[asyncio.Task[None]] = []
            num_streams = len(streams)

            async def _consume(stream: AsyncIterator[ResourceChange]) -> None:
                try:
                    async for change in stream:
                        await queue.put(change)
                finally:
                    await queue.put(None)

            tasks = [asyncio.create_task(_consume(s)) for s in streams]

            finished_count = 0
            try:
                while finished_count < num_streams:
                    change = await queue.get()
                    if change is None:
                        finished_count += 1
                        continue
                    yield change
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

        return _merged()
