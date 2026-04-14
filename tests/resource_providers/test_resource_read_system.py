"""System-level tests for ResourceReadProvider.

Tests the full feature as a user would experience it, covering end-to-end
scenarios including multi-provider aggregation, error recovery, and tool
registration.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.resource_providers import (
    AggregatingResourceProvider,
    ResourceDataType,
    ResourceInfo,
    ResourceProvider,
    ResourceReadProvider,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_resource(
    uri: str = "test://resource",
    name: str = "test-resource",
    mime_type: str | None = None,
    description: str | None = None,
    content: str | None = None,
) -> ResourceInfo:
    """Create a ResourceInfo with optional reader."""
    reader = None
    if content is not None:

        async def reader(uri: str) -> list[str]:
            return [content]

    return ResourceInfo(
        name=name,
        uri=uri,
        mime_type=mime_type,
        description=description,
        _reader=reader,
    )


def make_source_provider(resources: list[ResourceInfo] | None = None) -> MagicMock:
    """Create a mock source provider."""
    provider = MagicMock(spec=ResourceProvider)
    provider.get_resources = AsyncMock(return_value=resources or [])
    return provider


# ---------------------------------------------------------------------------
# TestEndToEndResourceRead
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEndToEndResourceRead:
    """Test the full resource read lifecycle end-to-end."""

    async def test_full_resource_lifecycle(self) -> None:
        """Create source with resources → wrap with ResourceReadProvider.

        Get tools → invoke tool → verify output.
        """
        resource = make_resource(
            uri="config://settings",
            name="settings",
            mime_type="application/json",
            description="App settings",
            content='{"theme": "dark"}',
        )
        source = make_source_provider([resource])
        read_provider = ResourceReadProvider(source_provider=source)

        # Get tools
        tools = await read_provider.get_tools()
        assert len(tools) == 1
        tool = tools[0]
        assert tool.name == "read_resource"

        # Invoke tool
        ctx = MagicMock()
        output = await tool.execute(ctx, uri="config://settings")

        assert isinstance(output, str)
        assert "[Resource: config://settings (application/json)]" in output
        assert '{"theme": "dark"}' in output

    async def test_mixed_resource_types(self) -> None:
        """Source with text, JSON, binary, image resources → each handled.

        Correctly via tool.
        """
        resources = [
            make_resource(
                uri="t://readme", name="readme", mime_type="text/plain", content="# Hello"
            ),
            make_resource(
                uri="t://data", name="data", mime_type="application/json", content="[1, 2, 3]"
            ),
            make_resource(uri="t://archive", name="archive", mime_type="application/zip"),
            make_resource(uri="t://photo", name="photo", mime_type="image/png", content="<img>"),
        ]
        source = make_source_provider(resources)
        read_provider = ResourceReadProvider(source_provider=source)
        tools = await read_provider.get_tools()
        tool = tools[0]
        ctx = MagicMock()

        # text/plain → content returned
        readme_out = await tool.execute(ctx, uri="t://readme")
        assert "# Hello" in readme_out

        # application/json → content returned
        data_out = await tool.execute(ctx, uri="t://data")
        assert "[1, 2, 3]" in data_out

        # application/zip → UNREADABLE → error
        archive_out = await tool.execute(ctx, uri="t://archive")
        assert "[Error reading resource" in archive_out

        # image/png → MULTIMODAL → content returned
        photo_out = await tool.execute(ctx, uri="t://photo")
        assert "<img>" in photo_out

    async def test_resource_catalog_in_tool_description(self) -> None:
        """prepare() function builds catalog with all LAZY resources listed."""
        resources = [
            make_resource(uri="t://a", name="a", description="First resource"),
            make_resource(uri="t://b", name="b", mime_type="text/plain"),
            make_resource(uri="t://c", name="c", description="Third", mime_type="application/json"),
        ]
        source = make_source_provider(resources)
        read_provider = ResourceReadProvider(source_provider=source)

        catalog = read_provider._build_resource_catalog(resources)
        assert "t://a: First resource" in catalog
        assert "t://b [text/plain]" in catalog
        assert "t://c: Third [application/json]" in catalog

    async def test_resource_change_invalidation(self) -> None:
        """Add resource to source → invalidate cache → new resource appears."""
        initial = [make_resource(uri="t://old", name="old")]
        source = make_source_provider(initial)
        read_provider = ResourceReadProvider(source_provider=source)

        # Initial read
        result1 = await read_provider.get_resources()
        assert len(result1) == 1

        # Simulate new resource being added to source
        updated = [
            make_resource(uri="t://old", name="old"),
            make_resource(uri="t://new", name="new"),
        ]
        source.get_resources = AsyncMock(return_value=updated)

        # Cache still holds old data
        result2 = await read_provider.get_resources()
        assert len(result2) == 1

        # Invalidate and re-read
        read_provider.invalidate_cache()
        result3 = await read_provider.get_resources()
        assert len(result3) == 2
        uris = {r.uri for r in result3}
        assert uris == {"t://old", "t://new"}


# ---------------------------------------------------------------------------
# TestMultiProviderScenario
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMultiProviderScenario:
    """Test ResourceReadProvider with multiple source providers."""

    async def test_two_mcp_servers(self) -> None:
        """Two source providers aggregated, ResourceReadProvider sees all.

        Resources from both.
        """
        server_a = make_source_provider([
            make_resource(uri="mcp-a://config", name="config-a"),
            make_resource(uri="mcp-a://data", name="data-a"),
        ])
        server_b = make_source_provider([
            make_resource(uri="mcp-b://config", name="config-b"),
        ])
        aggregator = AggregatingResourceProvider(providers=[server_a, server_b])
        read_provider = ResourceReadProvider(source_provider=aggregator)

        resources = await read_provider.get_resources()
        assert len(resources) == 3
        uris = {r.uri for r in resources}
        assert uris == {"mcp-a://config", "mcp-a://data", "mcp-b://config"}

    async def test_provider_isolation(self) -> None:
        """Each ResourceReadProvider only sees its source's resources."""
        source_a = make_source_provider([
            make_resource(uri="a://1", name="a1"),
        ])
        source_b = make_source_provider([
            make_resource(uri="b://1", name="b1"),
            make_resource(uri="b://2", name="b2"),
        ])

        reader_a = ResourceReadProvider(source_provider=source_a, name="reader_a")
        reader_b = ResourceReadProvider(source_provider=source_b, name="reader_b")

        res_a = await reader_a.get_resources()
        res_b = await reader_b.get_resources()

        assert len(res_a) == 1
        assert res_a[0].uri == "a://1"
        assert len(res_b) == 2
        assert {r.uri for r in res_b} == {"b://1", "b://2"}


# ---------------------------------------------------------------------------
# TestErrorRecovery
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestErrorRecovery:
    """Test error recovery scenarios."""

    async def test_reader_failure_recovery(self) -> None:
        """Resource reader raises error → formatted error in tool output.

        Other resources still work.
        """
        good_resource = make_resource(
            uri="t://good",
            name="good",
            mime_type="text/plain",
            content="OK",
        )

        async def bad_reader(uri: str) -> list[str]:
            msg = "Connection refused"
            raise RuntimeError(msg)

        bad_resource = ResourceInfo(
            name="bad",
            uri="t://bad",
            mime_type="text/plain",
            _reader=bad_reader,
        )

        source = make_source_provider([good_resource, bad_resource])
        read_provider = ResourceReadProvider(source_provider=source)
        tools = await read_provider.get_tools()
        tool = tools[0]
        ctx = MagicMock()

        # Bad resource → error string
        bad_result = await tool.execute(ctx, uri="t://bad")
        assert "[Error reading resource" in bad_result
        assert "No reader available" in bad_result

        # Good resource still works
        good_result = await tool.execute(ctx, uri="t://good")
        assert "OK" in good_result

    async def test_empty_source_recovery(self) -> None:
        """Source provider has no resources → tool still registered.

        Returns "not found" for any URI.
        """
        source = make_source_provider([])
        read_provider = ResourceReadProvider(source_provider=source)

        tools = await read_provider.get_tools()
        assert len(tools) == 1
        assert tools[0].name == "read_resource"

        ctx = MagicMock()
        result = await tools[0].execute(ctx, uri="any://uri")
        assert "[Error reading resource" in result
        assert "Resource not found" in result

    async def test_corrupt_resource_recovery(self) -> None:
        """Resource with None mime_type but text content → handled gracefully."""
        resource = make_resource(
            uri="t://corrupt",
            name="corrupt",
            mime_type=None,
            content="some text content",
        )
        source = make_source_provider([resource])
        read_provider = ResourceReadProvider(source_provider=source)

        result = await read_provider._read_resource("t://corrupt")
        # None mime → is_text_mime(None) returns True → TEXT
        assert result.data_type is ResourceDataType.TEXT
        assert result.content == "some text content"


# ---------------------------------------------------------------------------
# TestResourceReadProviderRegistration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResourceReadProviderRegistration:
    """Test ResourceReadProvider registration with ToolManager."""

    async def test_provider_with_tool_manager(self) -> None:
        """Register ResourceReadProvider with a mock ToolManager.

        Verify tools are registered correctly.
        """
        resource = make_resource(
            uri="t://doc",
            name="doc",
            mime_type="text/plain",
            content="hello",
        )
        source = make_source_provider([resource])
        read_provider = ResourceReadProvider(source_provider=source)

        tools = await read_provider.get_tools()
        assert len(tools) == 1

        tool = tools[0]
        assert tool.name == "read_resource"
        assert tool.category == "fetch"
        assert tool.source == read_provider.name

        # Verify the tool is callable
        ctx = MagicMock()
        result = await tool.execute(ctx, uri="t://doc")
        assert "hello" in result

    async def test_tool_category_and_hints(self) -> None:
        """Verify read_resource tool has correct category="fetch" and.

        read_only=True.
        """
        source = make_source_provider()
        read_provider = ResourceReadProvider(source_provider=source)

        tools = await read_provider.get_tools()
        tool = tools[0]

        assert tool.category == "fetch"
        assert tool.hints.read_only is True
