"""Tests for ResourceSource protocol and AggregatedResourceSource."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING

import pytest

from agentpool.capabilities.resource_source import (
    AggregatedResourceSource,
    Resource,
    ResourceChange,
    ResourceContent,
    ResourceNotFoundError,
    ResourceSource,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


# =============================================================================
# Frozen dataclass tests
# =============================================================================


def test_resource_immutable() -> None:
    """Resource is frozen and cannot be modified after construction."""
    res = Resource(uri="mcp://server/path", name="path")
    with pytest.raises(FrozenInstanceError):
        res.uri = "other://uri"  # type: ignore[misc]


def test_resource_content_immutable() -> None:
    """ResourceContent is frozen and cannot be modified after construction."""
    rc = ResourceContent(uri="mcp://server/path", content="hello")
    with pytest.raises(FrozenInstanceError):
        rc.content = "world"  # type: ignore[misc]


def test_resource_change_immutable() -> None:
    """ResourceChange is frozen and cannot be modified after construction."""
    change = ResourceChange(uri="mcp://server/path", kind="added")
    with pytest.raises(FrozenInstanceError):
        change.kind = "modified"  # type: ignore[misc]


def test_resource_defaults() -> None:
    """Resource defaults mime_type and description."""
    res = Resource(uri="skill://my-skill", name="my-skill")
    assert res.mime_type == "application/octet-stream"
    assert res.description == ""


def test_resource_content_defaults() -> None:
    """ResourceContent defaults mime_type."""
    rc = ResourceContent(uri="skill://my-skill", content="data")
    assert rc.mime_type == "application/octet-stream"


def test_resource_change_defaults() -> None:
    """ResourceChange defaults kind to 'modified'."""
    change = ResourceChange(uri="mcp://server/path")
    assert change.kind == "modified"


# =============================================================================
# ResourceNotFoundError tests
# =============================================================================


def test_resource_not_found_error_subclass() -> None:
    """ResourceNotFoundError is an Exception subclass."""
    err = ResourceNotFoundError("mcp://missing")
    assert isinstance(err, Exception)
    assert "mcp://missing" in str(err)


# =============================================================================
# ResourceSource protocol tests
# =============================================================================


class _FakeSource:
    """Minimal ResourceSource implementation for testing."""

    def __init__(
        self,
        resources: list[Resource] | None = None,
        content_map: dict[str, ResourceContent] | None = None,
        changes: list[ResourceChange] | None = None,
    ) -> None:
        self._resources = resources or []
        self._content_map = content_map or {}
        self._changes = changes

    async def list(self) -> list[Resource]:
        return list(self._resources)

    async def read(self, uri: str) -> ResourceContent:
        if uri not in self._content_map:
            raise ResourceNotFoundError(uri)
        return self._content_map[uri]

    async def exists(self, uri: str) -> bool:
        return uri in self._content_map or any(r.uri == uri for r in self._resources)

    def on_change(self) -> AsyncIterator[ResourceChange] | None:
        if self._changes is None:
            return None

        async def _iter() -> AsyncIterator[ResourceChange]:
            for change in self._changes:
                yield change

        return _iter()


def test_resource_source_protocol_isinstance() -> None:
    """@runtime_checkable protocol isinstance checks work."""
    source = _FakeSource()
    assert isinstance(source, ResourceSource)


def test_resource_source_protocol_not_instance() -> None:
    """Objects missing methods are not ResourceSource instances."""
    assert not isinstance(42, ResourceSource)
    assert not isinstance("hello", ResourceSource)


# =============================================================================
# AggregatedResourceSource tests
# =============================================================================


def _make_mcp_source() -> _FakeSource:
    """Create a fake MCP source with mcp:// resources."""
    return _FakeSource(
        resources=[
            Resource(uri="mcp://filesystem/readme.md", name="readme.md"),
        ],
        content_map={
            "mcp://filesystem/readme.md": ResourceContent(
                uri="mcp://filesystem/readme.md",
                content="# Hello",
                mime_type="text/markdown",
            ),
        },
    )


def _make_skill_source() -> _FakeSource:
    """Create a fake skill source with skill:// resources."""
    return _FakeSource(
        resources=[
            Resource(uri="skill://my-skill", name="my-skill"),
        ],
        content_map={
            "skill://my-skill": ResourceContent(
                uri="skill://my-skill",
                content="Skill instructions",
                mime_type="text/markdown",
            ),
        },
    )


async def test_aggregated_list_merges() -> None:
    """Aggregated list() merges resources from all sources."""
    agg = AggregatedResourceSource([_make_mcp_source(), _make_skill_source()])
    resources = await agg.list()
    uris = {r.uri for r in resources}
    assert uris == {"mcp://filesystem/readme.md", "skill://my-skill"}


async def test_aggregated_read_routes_by_scheme() -> None:
    """Aggregated read() routes to the correct source by URI."""
    agg = AggregatedResourceSource([_make_mcp_source(), _make_skill_source()])
    content = await agg.read("mcp://filesystem/readme.md")
    assert content.content == "# Hello"
    assert content.mime_type == "text/markdown"

    content2 = await agg.read("skill://my-skill")
    assert content2.content == "Skill instructions"


async def test_unknown_uri_raises() -> None:
    """Unknown URI raises ResourceNotFoundError."""
    agg = AggregatedResourceSource([_make_mcp_source(), _make_skill_source()])
    with pytest.raises(ResourceNotFoundError):
        await agg.read("unknown://resource")


async def test_aggregated_exists_checks_all() -> None:
    """Aggregated exists() returns True if any source recognizes the URI."""
    agg = AggregatedResourceSource([_make_mcp_source(), _make_skill_source()])
    assert await agg.exists("mcp://filesystem/readme.md") is True
    assert await agg.exists("skill://my-skill") is True
    assert await agg.exists("unknown://resource") is False


async def test_aggregated_exists_empty_sources() -> None:
    """Aggregated exists() returns False when no sources are composed."""
    agg = AggregatedResourceSource([])
    assert await agg.exists("any://uri") is False


async def test_aggregated_list_empty_sources() -> None:
    """Aggregated list() returns empty list when no sources are composed."""
    agg = AggregatedResourceSource([])
    assert await agg.list() == []


async def test_aggregated_on_change_merges_streams() -> None:
    """Aggregated on_change() merges change streams from all sources."""
    mcp_source = _FakeSource(
        changes=[ResourceChange(uri="mcp://server/file", kind="added")],
    )
    skill_source = _FakeSource(
        changes=[ResourceChange(uri="skill://my-skill", kind="modified")],
    )
    agg = AggregatedResourceSource([mcp_source, skill_source])
    stream = agg.on_change()
    assert stream is not None
    changes = [change async for change in stream]
    uris = {c.uri for c in changes}
    assert uris == {"mcp://server/file", "skill://my-skill"}


def test_aggregated_on_change_all_static_returns_none() -> None:
    """Aggregated on_change() returns None when all sources are static."""
    agg = AggregatedResourceSource([_make_mcp_source(), _make_skill_source()])
    assert agg.on_change() is None


async def test_aggregated_on_change_partial_static() -> None:
    """Aggregated on_change() returns stream when at least one source has changes."""
    static_source = _FakeSource(changes=None)
    dynamic_source = _FakeSource(
        changes=[ResourceChange(uri="mcp://server/file", kind="added")],
    )
    agg = AggregatedResourceSource([static_source, dynamic_source])
    stream = agg.on_change()
    assert stream is not None
    changes = [change async for change in stream]
    assert len(changes) == 1
    assert changes[0].uri == "mcp://server/file"


def test_aggregated_is_resource_source() -> None:
    """AggregatedResourceSource satisfies the ResourceSource protocol."""
    agg = AggregatedResourceSource([])
    assert isinstance(agg, ResourceSource)
