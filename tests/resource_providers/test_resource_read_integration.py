"""Integration tests for ResourceReadProvider.

Tests the interaction between ResourceReadProvider and other components
(AggregatingResourceProvider, Tool system, cache, size control, data type
classification) WITHOUT external services.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.resource_providers import (
    AggregatingResourceProvider,
    ResourceDataType,
    ResourceInfo,
    ResourceProvider,
    ResourceReadProvider,
    ResourceSizeConfig,
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
# TestWithAggregatingProvider
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWithAggregatingProvider:
    """Test ResourceReadProvider integration with AggregatingResourceProvider."""

    async def test_read_provider_in_aggregating_chain(self) -> None:
        """AggregatingResourceProvider wraps source + ResourceReadProvider.

        Both tools and resources are discoverable.
        """
        source = make_source_provider([
            make_resource(uri="test://doc", name="doc", mime_type="text/plain", content="hello"),
        ])
        read_provider = ResourceReadProvider(source_provider=source)
        aggregator = AggregatingResourceProvider(providers=[source, read_provider])

        resources = await aggregator.get_resources()
        assert len(resources) >= 1
        assert any(r.uri == "test://doc" for r in resources)

        tools = await aggregator.get_tools()
        tool_names = [t.name for t in tools]
        assert "read_resource" in tool_names

    async def test_read_provider_forwards_resources(self) -> None:
        """get_resources() returns source provider's resources."""
        resources = [
            make_resource(uri="test://a", name="a"),
            make_resource(uri="test://b", name="b"),
        ]
        source = make_source_provider(resources)
        read_provider = ResourceReadProvider(source_provider=source)

        result = await read_provider.get_resources()
        assert result is resources

    async def test_read_provider_with_multiple_sources(self) -> None:
        """AggregatingResourceProvider with 2 source providers.

        ResourceReadProvider wrapping the aggregator sees all resources.
        """
        source_a = make_source_provider([
            make_resource(uri="test://a1", name="a1"),
            make_resource(uri="test://a2", name="a2"),
        ])
        source_b = make_source_provider([
            make_resource(uri="test://b1", name="b1"),
        ])
        aggregator = AggregatingResourceProvider(providers=[source_a, source_b])
        read_provider = ResourceReadProvider(source_provider=aggregator)

        result = await read_provider.get_resources()
        uris = {r.uri for r in result}
        assert uris == {"test://a1", "test://a2", "test://b1"}


# ---------------------------------------------------------------------------
# TestResourceReadToolIntegration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResourceReadToolIntegration:
    """Test ResourceReadProvider tool invocation integration."""

    async def test_read_resource_tool_invocation(self) -> None:
        """Create provider, get tool, call the tool function directly with.

        A mock AgentContext and valid URI → returns formatted content.
        """
        resource = make_resource(
            uri="test://doc",
            name="doc",
            mime_type="text/plain",
            content="Hello world",
        )
        source = make_source_provider([resource])
        provider = ResourceReadProvider(source_provider=source)

        tools = await provider.get_tools()
        tool = tools[0]
        assert tool.name == "read_resource"

        ctx = MagicMock()
        result = await tool.execute(ctx, uri="test://doc")
        assert isinstance(result, str)
        assert "Hello world" in result
        assert "[Resource: test://doc (text/plain)]" in result

    async def test_read_resource_tool_error_handling(self) -> None:
        """Call tool with invalid URI → returns formatted error string (not exception)."""
        source = make_source_provider([
            make_resource(uri="test://exists", name="exists", mime_type="text/plain"),
        ])
        provider = ResourceReadProvider(source_provider=source)

        tools = await provider.get_tools()
        tool = tools[0]

        ctx = MagicMock()
        result = await tool.execute(ctx, uri="test://nonexistent")
        assert isinstance(result, str)
        assert "[Error reading resource" in result
        assert "test://nonexistent" in result
        assert "Resource not found" in result

    async def test_read_resource_tool_with_various_mime_types(self) -> None:
        """text/plain, application/json, image/png, application/octet-stream.

        Verify correct behavior for each.
        """
        text_res = make_resource(
            uri="test://text",
            name="text",
            mime_type="text/plain",
            content="plain text",
        )
        json_res = make_resource(
            uri="test://json",
            name="json",
            mime_type="application/json",
            content='{"key": "val"}',
        )
        image_res = make_resource(
            uri="test://image",
            name="image",
            mime_type="image/png",
            content="<binary>",
        )
        binary_res = make_resource(
            uri="test://binary",
            name="binary",
            mime_type="application/octet-stream",
            content="<raw>",
        )

        source = make_source_provider([text_res, json_res, image_res, binary_res])
        provider = ResourceReadProvider(source_provider=source)
        tools = await provider.get_tools()
        tool = tools[0]
        ctx = MagicMock()

        # text/plain → readable, content returned
        text_result = await tool.execute(ctx, uri="test://text")
        assert "plain text" in text_result
        assert "[Resource:" in text_result

        # application/json → readable (text category), content returned
        json_result = await tool.execute(ctx, uri="test://json")
        assert '{"key": "val"}' in json_result

        # image/png → MULTIMODAL, still returns content (as-is currently)
        image_result = await tool.execute(ctx, uri="test://image")
        assert "[Resource:" in image_result

        # application/octet-stream → UNREADABLE → error returned
        binary_result = await tool.execute(ctx, uri="test://binary")
        assert "[Error reading resource" in binary_result
        assert "unreadable" in binary_result.lower()


# ---------------------------------------------------------------------------
# TestCacheIntegration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCacheIntegration:
    """Test cache invalidation and concurrency integration."""

    async def test_invalidate_and_re_read(self) -> None:
        """Read resource, invalidate cache, read again → source called twice."""
        resources = [
            make_resource(uri="test://doc", name="doc", mime_type="text/plain", content="v1"),
        ]
        source = make_source_provider(resources)
        provider = ResourceReadProvider(source_provider=source)

        # First read populates cache
        await provider.get_resources()
        assert source.get_resources.await_count == 1

        # Second read uses cache
        await provider.get_resources()
        assert source.get_resources.await_count == 1

        # Invalidate and re-read
        provider.invalidate_cache()
        await provider.get_resources()
        assert source.get_resources.await_count == 2

    async def test_concurrent_reads(self) -> None:
        """Multiple concurrent reads don't corrupt cache — each returns valid data.

        Cache eventually stabilizes to a single consistent view.
        """
        call_count = 0

        async def counting_get_resources() -> list[ResourceInfo]:
            nonlocal call_count
            call_count += 1
            # Small sleep to simulate real async work and increase chance of
            # concurrent entry into get_resources()
            await asyncio.sleep(0.01)
            return [make_resource(uri="test://doc", name="doc")]

        source = MagicMock(spec=ResourceProvider)
        source.get_resources = counting_get_resources
        provider = ResourceReadProvider(source_provider=source)

        # Fire multiple concurrent reads
        results = await asyncio.gather(
            provider.get_resources(),
            provider.get_resources(),
            provider.get_resources(),
        )

        # All results should be valid lists with the expected resource
        for result in results:
            assert isinstance(result, list)
            assert len(result) == 1
            assert result[0].uri == "test://doc"

        # After all concurrent reads settle, cache is populated
        # and subsequent reads return the cached value
        final = await provider.get_resources()
        assert final is provider._resources_cache

        # Source was called multiple times due to concurrent cache misses,
        # but no corruption or exceptions occurred
        assert call_count >= 1


# ---------------------------------------------------------------------------
# TestSizeControlIntegration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSizeControlIntegration:
    """Test size control integration through the full pipeline."""

    async def test_end_to_end_truncation(self) -> None:
        """Large resource content → truncated in tool output."""
        long_content = "A" * 5000
        resource = make_resource(
            uri="test://long",
            name="long",
            mime_type="text/plain",
            content=long_content,
        )
        source = make_source_provider([resource])
        config = ResourceSizeConfig(max_content_chars=100)
        provider = ResourceReadProvider(source_provider=source, size_config=config)

        tools = await provider.get_tools()
        tool = tools[0]
        ctx = MagicMock()

        result = await tool.execute(ctx, uri="test://long")
        assert isinstance(result, str)
        assert "[Content truncated:" in result
        assert result.startswith("[Resource: test://long (text/plain)]\n" + "A" * 100)

    async def test_custom_size_config_propagation(self) -> None:
        """Custom ResourceSizeConfig → respected in tool output."""
        content = "B" * 300
        resource = make_resource(
            uri="test://custom",
            name="custom",
            mime_type="text/plain",
            content=content,
        )
        source = make_source_provider([resource])
        config = ResourceSizeConfig(
            max_content_chars=50,
            truncate_message="... CUT [{original} -> {limit}]",
        )
        provider = ResourceReadProvider(source_provider=source, size_config=config)

        result = await provider._read_resource("test://custom")
        assert result.truncated is True
        assert "... CUT [300 -> 50]" in result.content

    async def test_unlimited_size(self) -> None:
        """max_content_chars=-1 → no truncation."""
        content = "X" * 200_000
        resource = make_resource(
            uri="test://huge",
            name="huge",
            mime_type="text/plain",
            content=content,
        )
        source = make_source_provider([resource])
        config = ResourceSizeConfig(max_content_chars=-1)
        provider = ResourceReadProvider(source_provider=source, size_config=config)

        result = await provider._read_resource("test://huge")
        assert result.truncated is False
        assert result.content == content


# ---------------------------------------------------------------------------
# TestDataTypeClassificationIntegration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDataTypeClassificationIntegration:
    """Test data type classification through the full read pipeline."""

    async def test_text_resource_full_flow(self) -> None:
        """text/plain resource → TEXT classification → readable → content returned."""
        resource = make_resource(
            uri="test://txt",
            name="txt",
            mime_type="text/plain",
            content="text content",
        )
        source = make_source_provider([resource])
        provider = ResourceReadProvider(source_provider=source)

        result = await provider._read_resource("test://txt")
        assert result.data_type is ResourceDataType.TEXT
        assert result.content == "text content"
        assert result.truncated is False

    async def test_binary_resource_full_flow(self) -> None:
        """application/octet-stream → UNREADABLE → error returned."""
        resource = make_resource(
            uri="test://bin",
            name="bin",
            mime_type="application/octet-stream",
        )
        source = make_source_provider([resource])
        provider = ResourceReadProvider(source_provider=source)

        from agentpool.resource_providers import ResourceReadError

        with pytest.raises(ResourceReadError) as exc_info:
            await provider._read_resource("test://bin")

        assert exc_info.value.data_type is ResourceDataType.UNREADABLE

    async def test_image_resource_flow(self) -> None:
        """image/png → MULTIMODAL → content returned (as-is currently)."""
        resource = make_resource(
            uri="test://img",
            name="img",
            mime_type="image/png",
            content="base64data",
        )
        source = make_source_provider([resource])
        provider = ResourceReadProvider(source_provider=source)

        result = await provider._read_resource("test://img")
        assert result.data_type is ResourceDataType.MULTIMODAL
        assert result.content == "base64data"

    async def test_unknown_resource_flow(self) -> None:
        """application/x-custom → PROBE_NEEDED → still readable.

        PROBE_NEEDED allows reading.
        """
        resource = make_resource(
            uri="test://custom",
            name="custom",
            mime_type="application/x-custom",
            content="custom data",
        )
        source = make_source_provider([resource])
        provider = ResourceReadProvider(source_provider=source)

        result = await provider._read_resource("test://custom")
        assert result.data_type is ResourceDataType.PROBE_NEEDED
        assert result.content == "custom data"
