"""Test ResourceReadProvider class."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.resource_providers.base import ResourceProvider
from agentpool.resource_providers.resource_info import ResourceInfo
from agentpool.resource_providers.resource_read_provider import ResourceReadProvider
from agentpool.resource_providers.resource_read_types import (
    DefaultEagerReadStrategy,
    EagerConfig,
    ReadTier,
    ResourceDataType,
    ResourceReadError,
    ResourceReadResult,
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
    size: int | None = None,
) -> ResourceInfo:
    """Create a ResourceInfo with optional reader."""
    reader = None
    if content is not None:

        async def reader(uri: str) -> list[str]:  # type: ignore[assignment]
            return [content]

    return ResourceInfo(
        name=name,
        uri=uri,
        mime_type=mime_type,
        description=description,
        size=size,
        _reader=reader,
    )


def make_source_provider(resources: list[ResourceInfo] | None = None) -> MagicMock:
    """Create a mock source provider."""
    provider = MagicMock(spec=ResourceProvider)
    provider.get_resources = AsyncMock(return_value=resources or [])
    return provider


# ---------------------------------------------------------------------------
# TestResourceReadProviderInit
# ---------------------------------------------------------------------------


class TestResourceReadProviderInit:
    """Test ResourceReadProvider construction and attributes."""

    def test_default_init(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        assert provider.name == "resource_read"
        assert provider.owner is None
        assert provider.size_config == ResourceSizeConfig()

    def test_custom_init(self) -> None:
        source = make_source_provider()
        config = ResourceSizeConfig(max_content_chars=500)
        provider = ResourceReadProvider(
            source_provider=source,
            name="custom_reader",
            owner="my-agent",
            size_config=config,
        )
        assert provider.name == "custom_reader"
        assert provider.owner == "my-agent"
        assert provider.size_config is config

    def test_source_provider_stored(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        assert provider.source_provider is source

    def test_kind(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        assert provider.kind == "resource_read"


# ---------------------------------------------------------------------------
# TestGetResources
# ---------------------------------------------------------------------------


class TestGetResources:
    """Test get_resources() — forwarding and caching."""

    async def test_forward_resources(self) -> None:
        resources = [
            make_resource(uri="test://a", name="a"),
            make_resource(uri="test://b", name="b"),
        ]
        source = make_source_provider(resources)
        provider = ResourceReadProvider(source_provider=source)

        result = await provider.get_resources()
        assert result is resources
        source.get_resources.assert_awaited_once()

    async def test_caching(self) -> None:
        resources = [make_resource(uri="test://a", name="a")]
        source = make_source_provider(resources)
        provider = ResourceReadProvider(source_provider=source)

        first = await provider.get_resources()
        second = await provider.get_resources()
        assert first is second
        # Source should only be called once due to caching
        source.get_resources.assert_awaited_once()

    async def test_empty_resources(self) -> None:
        source = make_source_provider([])
        provider = ResourceReadProvider(source_provider=source)

        result = await provider.get_resources()
        assert result == []

    async def test_invalidate_cache(self) -> None:
        resources = [make_resource(uri="test://a", name="a")]
        source = make_source_provider(resources)
        provider = ResourceReadProvider(source_provider=source)

        await provider.get_resources()
        assert source.get_resources.await_count == 1

        provider.invalidate_cache()
        assert provider._resources_cache is None

        await provider.get_resources()
        assert source.get_resources.await_count == 2


# ---------------------------------------------------------------------------
# TestGetTools
# ---------------------------------------------------------------------------


class TestGetTools:
    """Test get_tools() — returns single read_resource tool."""

    async def test_returns_single_tool(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        tools = await provider.get_tools()
        assert len(tools) == 1

    async def test_tool_name(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        tools = await provider.get_tools()
        assert tools[0].name == "read_resource"

    async def test_tool_category(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        tools = await provider.get_tools()
        assert tools[0].category == "fetch"

    async def test_tool_read_only(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        tools = await provider.get_tools()
        assert tools[0].hints.read_only is True


# ---------------------------------------------------------------------------
# TestBuildResourceCatalog
# ---------------------------------------------------------------------------


class TestBuildResourceCatalog:
    """Test _build_resource_catalog() — formatting and truncation."""

    def test_empty_catalog(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        result = provider._build_resource_catalog([])
        assert result == ""

    def test_single_resource(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        resources = [make_resource(uri="test://data", name="data")]
        result = provider._build_resource_catalog(resources)
        assert result == "- test://data"

    def test_resource_with_description(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        resources = [make_resource(uri="test://data", name="data", description="A data file")]
        result = provider._build_resource_catalog(resources)
        assert result == "- test://data: A data file"

    def test_resource_with_mime_type(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        resources = [make_resource(uri="test://data", name="data", mime_type="text/plain")]
        result = provider._build_resource_catalog(resources)
        assert result == "- test://data [text/plain]"

    def test_resource_with_description_and_mime_type(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        resources = [
            make_resource(
                uri="test://data", name="data", description="A file", mime_type="text/csv"
            )
        ]
        result = provider._build_resource_catalog(resources)
        assert result == "- test://data: A file [text/csv]"

    def test_truncation_at_max_length(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)

        # Create enough resources to exceed the 2000-char catalog limit
        resources = [
            make_resource(uri=f"test://resource-with-a-long-identifier-{i:04d}", name=f"r{i}")
            for i in range(200)
        ]
        result = provider._build_resource_catalog(resources)
        assert len(result) <= 2100  # Allow small margin for truncation line
        assert "... and " in result
        assert "more resources" in result


# ---------------------------------------------------------------------------
# TestReadResource
# ---------------------------------------------------------------------------


class TestReadResource:
    """Test _read_resource() — URI validation, data type classification, etc."""

    async def test_read_text_resource(self) -> None:
        resource = make_resource(
            uri="test://doc", name="doc", mime_type="text/plain", content="Hello world"
        )
        source = make_source_provider([resource])
        provider = ResourceReadProvider(source_provider=source)

        result = await provider._read_resource("test://doc")
        assert result.uri == "test://doc"
        assert result.content == "Hello world"
        assert result.data_type is ResourceDataType.TEXT
        assert result.mime_type == "text/plain"
        assert result.truncated is False

    async def test_read_json_resource(self) -> None:
        resource = make_resource(
            uri="test://api", name="api", mime_type="application/json", content='{"key": "value"}'
        )
        source = make_source_provider([resource])
        provider = ResourceReadProvider(source_provider=source)

        result = await provider._read_resource("test://api")
        assert result.uri == "test://api"
        assert result.data_type is ResourceDataType.TEXT
        assert result.content == '{"key": "value"}'

    async def test_resource_not_found(self) -> None:
        source = make_source_provider([
            make_resource(uri="test://exists", name="exists"),
        ])
        provider = ResourceReadProvider(source_provider=source)

        with pytest.raises(ResourceReadError) as exc_info:
            await provider._read_resource("test://missing")

        assert exc_info.value.uri == "test://missing"
        assert "Resource not found" in exc_info.value.reason
        assert "test://exists" in exc_info.value.reason

    async def test_unreadable_binary(self) -> None:
        resource = make_resource(
            uri="test://binary", name="binary", mime_type="application/octet-stream"
        )
        source = make_source_provider([resource])
        provider = ResourceReadProvider(source_provider=source)

        with pytest.raises(ResourceReadError) as exc_info:
            await provider._read_resource("test://binary")

        assert exc_info.value.uri == "test://binary"
        assert exc_info.value.data_type is ResourceDataType.UNREADABLE
        assert "unreadable" in exc_info.value.reason.lower()

    async def test_no_reader_available(self) -> None:
        # ResourceInfo with no _reader will raise RuntimeError on read()
        resource = ResourceInfo(name="norea", uri="test://norea", mime_type="text/plain")
        source = make_source_provider([resource])
        provider = ResourceReadProvider(source_provider=source)

        with pytest.raises(ResourceReadError) as exc_info:
            await provider._read_resource("test://norea")

        assert exc_info.value.uri == "test://norea"
        assert "No reader available" in exc_info.value.reason

    async def test_truncation(self) -> None:
        long_content = "x" * 2000
        resource = make_resource(
            uri="test://long", name="long", mime_type="text/plain", content=long_content
        )
        source = make_source_provider([resource])
        config = ResourceSizeConfig(max_content_chars=100)
        provider = ResourceReadProvider(source_provider=source, size_config=config)

        result = await provider._read_resource("test://long")
        assert result.truncated is True
        assert result.original_size == 2000
        assert len(result.content) > 100  # content + truncate_message
        assert result.content.startswith("x" * 100)

    async def test_no_truncation(self) -> None:
        short_content = "short"
        resource = make_resource(
            uri="test://short", name="short", mime_type="text/plain", content=short_content
        )
        source = make_source_provider([resource])
        provider = ResourceReadProvider(source_provider=source)

        result = await provider._read_resource("test://short")
        assert result.truncated is False
        assert result.original_size is None
        assert result.content == "short"

    async def test_custom_size_config(self) -> None:
        long_content = "A" * 500
        resource = make_resource(
            uri="test://custom", name="custom", mime_type="text/plain", content=long_content
        )
        source = make_source_provider([resource])
        config = ResourceSizeConfig(
            max_content_chars=50,
            truncate_message="... [cut: {original} -> {limit}]",
        )
        provider = ResourceReadProvider(source_provider=source, size_config=config)

        result = await provider._read_resource("test://custom")
        assert result.truncated is True
        assert result.original_size == 500
        assert "... [cut: 500 -> 50]" in result.content


# ---------------------------------------------------------------------------
# TestClassifyDataType
# ---------------------------------------------------------------------------


class TestClassifyDataType:
    """Test _classify_data_type() — all 4 data type paths."""

    def test_text_plain(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        resource = make_resource(uri="t://a", name="a", mime_type="text/plain")
        assert provider._classify_data_type(resource) is ResourceDataType.TEXT

    def test_text_html(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        resource = make_resource(uri="t://a", name="a", mime_type="text/html")
        assert provider._classify_data_type(resource) is ResourceDataType.TEXT

    def test_application_json(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        resource = make_resource(uri="t://a", name="a", mime_type="application/json")
        assert provider._classify_data_type(resource) is ResourceDataType.TEXT

    def test_application_xml(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        resource = make_resource(uri="t://a", name="a", mime_type="application/xml")
        assert provider._classify_data_type(resource) is ResourceDataType.TEXT

    def test_image_png(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        resource = make_resource(uri="t://a", name="a", mime_type="image/png")
        assert provider._classify_data_type(resource) is ResourceDataType.MULTIMODAL

    def test_audio_mp3(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        resource = make_resource(uri="t://a", name="a", mime_type="audio/mpeg")
        assert provider._classify_data_type(resource) is ResourceDataType.MULTIMODAL

    def test_application_octet_stream(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        resource = make_resource(uri="t://a", name="a", mime_type="application/octet-stream")
        assert provider._classify_data_type(resource) is ResourceDataType.UNREADABLE

    def test_application_zip(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        resource = make_resource(uri="t://a", name="a", mime_type="application/zip")
        assert provider._classify_data_type(resource) is ResourceDataType.UNREADABLE

    def test_none_mime(self) -> None:
        """is_text_mime(None) returns True, so None → TEXT."""
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        resource = make_resource(uri="t://a", name="a", mime_type=None)
        assert provider._classify_data_type(resource) is ResourceDataType.TEXT

    def test_unknown_mime(self) -> None:
        """application/x-custom is not text, not image/audio, not known binary → PROBE_NEEDED."""
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        resource = make_resource(uri="t://a", name="a", mime_type="application/x-custom")
        assert provider._classify_data_type(resource) is ResourceDataType.PROBE_NEEDED

    def test_application_pdf(self) -> None:
        """PDF is classified as MULTIMODAL (consumable by some LLMs)."""
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        resource = make_resource(uri="t://a", name="a", mime_type="application/pdf")
        assert provider._classify_data_type(resource) is ResourceDataType.MULTIMODAL


# ---------------------------------------------------------------------------
# TestFormatResult
# ---------------------------------------------------------------------------


class TestFormatResult:
    """Test _format_result() — with and without mime_type, with and without truncation."""

    def test_with_mime_type(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        result = ResourceReadResult(
            uri="test://doc",
            content="Hello",
            data_type=ResourceDataType.TEXT,
            mime_type="text/plain",
        )
        formatted = provider._format_result(result)
        assert "[Resource: test://doc (text/plain)]" in formatted
        assert "Hello" in formatted

    def test_without_mime_type(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        result = ResourceReadResult(
            uri="test://doc",
            content="Hello",
            data_type=ResourceDataType.TEXT,
        )
        formatted = provider._format_result(result)
        assert "[Resource: test://doc]" in formatted
        assert "Hello" in formatted

    def test_truncated_result(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        result = ResourceReadResult(
            uri="test://doc",
            content="truncated content...",
            data_type=ResourceDataType.TEXT,
            truncated=True,
            original_size=5000,
        )
        formatted = provider._format_result(result)
        assert "[Content truncated:" in formatted
        assert "5000" in formatted

    def test_non_truncated_result(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        result = ResourceReadResult(
            uri="test://doc",
            content="full content",
            data_type=ResourceDataType.TEXT,
            truncated=False,
        )
        formatted = provider._format_result(result)
        assert "[Content truncated:" not in formatted


# ---------------------------------------------------------------------------
# TestFormatError
# ---------------------------------------------------------------------------


class TestFormatError:
    """Test _format_error() — error formatting."""

    def test_error_format(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        error = ResourceReadError(
            uri="test://missing",
            reason="Resource not found",
            data_type=ResourceDataType.UNREADABLE,
        )
        formatted = provider._format_error(error)
        assert "'test://missing'" in formatted
        assert "Resource not found" in formatted
        assert "unreadable" in formatted

    def test_error_with_custom_data_type(self) -> None:
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        error = ResourceReadError(
            uri="test://unknown",
            reason="need probe",
            data_type=ResourceDataType.PROBE_NEEDED,
        )
        formatted = provider._format_error(error)
        assert "'test://unknown'" in formatted
        assert "need probe" in formatted
        assert "probe_needed" in formatted


# ---------------------------------------------------------------------------
# TestInvalidateCache
# ---------------------------------------------------------------------------


class TestInvalidateCache:
    """Test invalidate_cache() — cache invalidation."""

    async def test_invalidate_clears_cache(self) -> None:
        resources = [make_resource(uri="test://a", name="a")]
        source = make_source_provider(resources)
        provider = ResourceReadProvider(source_provider=source)

        # Populate cache
        await provider.get_resources()
        assert provider._resources_cache is not None

        # Invalidate
        provider.invalidate_cache()
        assert provider._resources_cache is None

        # Next call should hit source again
        await provider.get_resources()
        assert source.get_resources.await_count == 2

    async def test_invalidate_clears_eager_cache(self) -> None:
        resources = [make_resource(uri="test://a", name="a")]
        source = make_source_provider(resources)
        provider = ResourceReadProvider(source_provider=source)

        provider._eager_cache = {"test://a": "content"}
        provider.invalidate_cache()
        assert provider._eager_cache is None


# ---------------------------------------------------------------------------
# TestEagerMode
# ---------------------------------------------------------------------------


class TestEagerMode:
    """Test eager (Tier 1) resource injection via get_instructions()."""

    async def test_no_eager_resources_when_config_empty(self) -> None:
        """With no eager MIME types or URI prefixes, get_instructions() returns []."""
        resources = [make_resource(uri="test://a", name="a", mime_type="text/plain", content="hi")]
        source = make_source_provider(resources)
        config = EagerConfig(eager_mime_types=(), eager_uri_prefixes=())
        provider = ResourceReadProvider(source_provider=source, eager_config=config)

        instructions = await provider.get_instructions()
        assert instructions == []

    async def test_eager_resources_with_mime_match(self) -> None:
        """text/plain resource with matching eager_mime_types appears in instructions."""
        resources = [
            make_resource(
                uri="test://doc",
                name="doc",
                mime_type="text/plain",
                description="A doc",
                content="Hello world",
            )
        ]
        source = make_source_provider(resources)
        config = EagerConfig(eager_mime_types=("text/plain",))
        provider = ResourceReadProvider(source_provider=source, eager_config=config)

        instructions = await provider.get_instructions()
        assert len(instructions) == 1

        # Call the instruction function
        mock_ctx = MagicMock()
        content = await instructions[0](mock_ctx)
        assert "Hello world" in content
        assert "test://doc" in content

    async def test_eager_resources_with_uri_prefix(self) -> None:
        """Resource with matching URI prefix is EAGER."""
        resources = [
            make_resource(
                uri="important://config",
                name="config",
                mime_type="application/octet-stream",
                content="config-data",
            )
        ]
        source = make_source_provider(resources)
        config = EagerConfig(
            eager_mime_types=(),
            eager_uri_prefixes=("important://",),
        )
        provider = ResourceReadProvider(source_provider=source, eager_config=config)

        instructions = await provider.get_instructions()
        assert len(instructions) == 1

        mock_ctx = MagicMock()
        content = await instructions[0](mock_ctx)
        assert "config-data" in content

    async def test_eager_respects_char_budget(self) -> None:
        """Many resources exceeding max_eager_chars → budget respected, remaining noted."""
        resources = [
            make_resource(
                uri=f"test://r{i}", name=f"r{i}", mime_type="text/plain", content="x" * 600
            )
            for i in range(5)
        ]
        source = make_source_provider(resources)
        config = EagerConfig(eager_mime_types=("text/plain",), max_eager_chars=1000)
        provider = ResourceReadProvider(source_provider=source, eager_config=config)

        instructions = await provider.get_instructions()
        assert len(instructions) == 1

        mock_ctx = MagicMock()
        content = await instructions[0](mock_ctx)
        assert "budget exceeded" in content

    async def test_eager_skips_unreadable(self) -> None:
        """Resource with no reader is skipped gracefully."""
        readable = make_resource(
            uri="test://readable", name="readable", mime_type="text/plain", content="ok"
        )
        # ResourceInfo with no _reader
        unreadable = ResourceInfo(name="noreader", uri="test://noreader", mime_type="text/plain")
        resources = [readable, unreadable]
        source = make_source_provider(resources)
        config = EagerConfig(eager_mime_types=("text/plain",))
        provider = ResourceReadProvider(source_provider=source, eager_config=config)

        instructions = await provider.get_instructions()
        assert len(instructions) == 1

        mock_ctx = MagicMock()
        content = await instructions[0](mock_ctx)
        assert "ok" in content
        assert "test://noreader" not in content

    async def test_eager_includes_metadata(self) -> None:
        """include_metadata=True → URI + MIME + description in output."""
        resources = [
            make_resource(
                uri="test://doc",
                name="doc",
                mime_type="text/plain",
                description="A doc",
                content="Hello",
            )
        ]
        source = make_source_provider(resources)
        config = EagerConfig(eager_mime_types=("text/plain",), include_metadata=True)
        provider = ResourceReadProvider(source_provider=source, eager_config=config)

        instructions = await provider.get_instructions()
        mock_ctx = MagicMock()
        content = await instructions[0](mock_ctx)
        assert "Resource: test://doc" in content
        assert "Type: text/plain" in content
        assert "Description: A doc" in content
        assert "Hello" in content

    async def test_eager_excludes_metadata(self) -> None:
        """include_metadata=False → only content (no URI/MIME/description)."""
        resources = [
            make_resource(
                uri="test://doc",
                name="doc",
                mime_type="text/plain",
                description="A doc",
                content="Hello",
            )
        ]
        source = make_source_provider(resources)
        config = EagerConfig(eager_mime_types=("text/plain",), include_metadata=False)
        provider = ResourceReadProvider(source_provider=source, eager_config=config)

        instructions = await provider.get_instructions()
        mock_ctx = MagicMock()
        content = await instructions[0](mock_ctx)
        # With metadata off, no URI/MIME/description — only content
        assert "Resource:" not in content
        assert "Type:" not in content
        assert "Description:" not in content
        assert "Hello" in content

    async def test_lazy_resources_in_tool_description(self) -> None:
        """EAGER resources excluded from read_resource tool catalog."""
        resources = [
            make_resource(uri="test://eager", name="eager", mime_type="text/plain", content="e"),
            make_resource(uri="test://lazy", name="lazy", mime_type="image/png"),
        ]
        source = make_source_provider(resources)
        config = EagerConfig(eager_mime_types=("text/plain",))
        provider = ResourceReadProvider(source_provider=source, eager_config=config)

        catalog = provider._build_resource_catalog(resources)
        # Full catalog includes both
        assert "test://eager" in catalog
        assert "test://lazy" in catalog

        # Filtered catalog for tool description: only LAZY
        lazy_resources = [
            r for r in resources if provider._read_strategy.decide(r) is ReadTier.LAZY
        ]
        lazy_catalog = provider._build_resource_catalog(lazy_resources)
        assert "test://eager" not in lazy_catalog
        assert "test://lazy" in lazy_catalog


# ---------------------------------------------------------------------------
# TestDefaultEagerReadStrategy
# ---------------------------------------------------------------------------


class TestDefaultEagerReadStrategy:
    """Test DefaultEagerReadStrategy classification logic."""

    def test_empty_config_returns_lazy(self) -> None:
        """No eager filters → everything LAZY."""
        config = EagerConfig(eager_mime_types=(), eager_uri_prefixes=())
        strategy = DefaultEagerReadStrategy(config)
        resource = make_resource(uri="test://a", name="a", mime_type="text/plain")
        assert strategy.decide(resource) is ReadTier.LAZY

    def test_mime_type_match_returns_eager(self) -> None:
        """MIME type match → EAGER."""
        config = EagerConfig(eager_mime_types=("text/plain", "application/json"))
        strategy = DefaultEagerReadStrategy(config)
        resource = make_resource(uri="test://a", name="a", mime_type="text/plain")
        assert strategy.decide(resource) is ReadTier.EAGER

    def test_uri_prefix_match_returns_eager(self) -> None:
        """URI prefix match → EAGER."""
        config = EagerConfig(eager_uri_prefixes=("important://", "config://"))
        strategy = DefaultEagerReadStrategy(config)
        resource = make_resource(uri="important://settings", name="settings")
        assert strategy.decide(resource) is ReadTier.EAGER

    def test_no_match_returns_lazy(self) -> None:
        """No MIME or URI match → LAZY."""
        config = EagerConfig(eager_mime_types=("text/plain",), eager_uri_prefixes=("important://",))
        strategy = DefaultEagerReadStrategy(config)
        resource = make_resource(uri="test://a", name="a", mime_type="image/png")
        assert strategy.decide(resource) is ReadTier.LAZY

    def test_none_mime_type_not_eager(self) -> None:
        """None mime_type does not match any eager_mime_types → LAZY."""
        config = EagerConfig(eager_mime_types=("text/plain",))
        strategy = DefaultEagerReadStrategy(config)
        resource = make_resource(uri="test://a", name="a", mime_type=None)
        assert strategy.decide(resource) is ReadTier.LAZY


# ---------------------------------------------------------------------------
# TestRateLimiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    """Test rate limiting in _read_resource()."""

    async def test_rate_limit_allows_within_limit(self) -> None:
        """Reads within rate_limit should succeed."""
        resource = make_resource(uri="test://a", name="a", mime_type="text/plain", content="hello")
        source = make_source_provider([resource])
        provider = ResourceReadProvider(source_provider=source, rate_limit=3)

        for _ in range(3):
            result = await provider._read_resource("test://a")
            assert result.content == "hello"

    async def test_rate_limit_blocks_over_limit(self) -> None:
        """Reads exceeding rate_limit should raise ResourceReadError."""
        resource = make_resource(uri="test://a", name="a", mime_type="text/plain", content="hello")
        source = make_source_provider([resource])
        provider = ResourceReadProvider(source_provider=source, rate_limit=2)

        await provider._read_resource("test://a")
        await provider._read_resource("test://a")

        with pytest.raises(ResourceReadError) as exc_info:
            await provider._read_resource("test://a")
        assert "Rate limit exceeded" in exc_info.value.reason

    async def test_rate_limit_unlimited(self) -> None:
        """rate_limit=0 means unlimited reads."""
        resource = make_resource(uri="test://a", name="a", mime_type="text/plain", content="hello")
        source = make_source_provider([resource])
        provider = ResourceReadProvider(source_provider=source, rate_limit=0)

        for _ in range(20):
            result = await provider._read_resource("test://a")
            assert result.content == "hello"

    async def test_reset_read_count(self) -> None:
        """reset_read_count() allows new reads after limit was hit."""
        resource = make_resource(uri="test://a", name="a", mime_type="text/plain", content="hello")
        source = make_source_provider([resource])
        provider = ResourceReadProvider(source_provider=source, rate_limit=1)

        await provider._read_resource("test://a")
        with pytest.raises(ResourceReadError):
            await provider._read_resource("test://a")

        provider.reset_read_count()
        result = await provider._read_resource("test://a")
        assert result.content == "hello"

    async def test_rate_limit_default(self) -> None:
        """Default rate_limit is 10 (from ResourceSizeConfig default)."""
        source = make_source_provider()
        provider = ResourceReadProvider(source_provider=source)
        assert provider._rate_limit == 10

    async def test_rate_limit_falls_back_to_size_config(self) -> None:
        """When rate_limit param is None, size_config.rate_limit is used."""
        source = make_source_provider()
        config = ResourceSizeConfig(rate_limit=5)
        provider = ResourceReadProvider(source_provider=source, size_config=config)
        assert provider._rate_limit == 5

    async def test_explicit_rate_limit_overrides_size_config(self) -> None:
        """Explicit rate_limit param overrides size_config.rate_limit."""
        source = make_source_provider()
        config = ResourceSizeConfig(rate_limit=5)
        provider = ResourceReadProvider(source_provider=source, size_config=config, rate_limit=3)
        assert provider._rate_limit == 3


# ---------------------------------------------------------------------------
# TestPreReadSizeGate
# ---------------------------------------------------------------------------


class TestPreReadSizeGate:
    """Test post-read size gate (max_read_bytes enforcement)."""

    async def test_content_within_limit(self) -> None:
        """Content within max_read_bytes should pass."""
        resource = make_resource(uri="test://a", name="a", mime_type="text/plain", content="small")
        source = make_source_provider([resource])
        config = ResourceSizeConfig(max_read_bytes=1000)
        provider = ResourceReadProvider(source_provider=source, size_config=config)

        result = await provider._read_resource("test://a")
        assert result.content == "small"

    async def test_content_exceeds_limit(self) -> None:
        """Content exceeding max_read_bytes should raise ResourceReadError."""
        big_content = "X" * 5000
        resource = make_resource(
            uri="test://a", name="a", mime_type="text/plain", content=big_content
        )
        source = make_source_provider([resource])
        config = ResourceSizeConfig(max_read_bytes=1000)
        provider = ResourceReadProvider(source_provider=source, size_config=config)

        with pytest.raises(ResourceReadError) as exc_info:
            await provider._read_resource("test://a")
        assert "too large" in exc_info.value.reason.lower()

    async def test_unlimited_read_bytes(self) -> None:
        """max_read_bytes=-1 means no size gate."""
        big_content = "X" * 200_000
        resource = make_resource(
            uri="test://a", name="a", mime_type="text/plain", content=big_content
        )
        source = make_source_provider([resource])
        config = ResourceSizeConfig(max_read_bytes=-1, max_content_chars=-1)
        provider = ResourceReadProvider(source_provider=source, size_config=config)

        result = await provider._read_resource("test://a")
        assert result.content == big_content


# ---------------------------------------------------------------------------
# TestMultimodalHandling
# ---------------------------------------------------------------------------


class TestMultimodalHandling:
    """Test MULTIMODAL resource handling."""

    async def test_multimodal_with_stub_binary(self) -> None:
        """Image resource with stub '[Binary data: N bytes]' → metadata message."""
        resource = make_resource(
            uri="test://img", name="img", mime_type="image/png", content="[Binary data: 1024 bytes]"
        )
        source = make_source_provider([resource])
        provider = ResourceReadProvider(source_provider=source)

        result = await provider._read_resource("test://img")
        assert result.data_type is ResourceDataType.MULTIMODAL
        assert "[Multimodal resource: image/png]" in result.content
        assert "binary resource" in result.content.lower()

    async def test_multimodal_with_real_text(self) -> None:
        """Image resource with actual text content → returned as-is."""
        resource = make_resource(
            uri="test://img", name="img", mime_type="image/png", content="base64data..."
        )
        source = make_source_provider([resource])
        provider = ResourceReadProvider(source_provider=source)

        result = await provider._read_resource("test://img")
        assert result.data_type is ResourceDataType.MULTIMODAL
        assert result.content == "base64data..."

    async def test_multimodal_tool_output(self) -> None:
        """MULTIMODAL stub → tool returns proper formatted message."""
        resource = make_resource(
            uri="test://img", name="img", mime_type="image/png", content="[Binary data: 2048 bytes]"
        )
        source = make_source_provider([resource])
        provider = ResourceReadProvider(source_provider=source)
        tools = await provider.get_tools()
        tool = tools[0]
        ctx = MagicMock()

        output = await tool.execute(ctx, uri="test://img")
        assert "Multimodal resource" in output


# ---------------------------------------------------------------------------
# TestPreReadSizeCheck
# ---------------------------------------------------------------------------


class TestPreReadSizeCheck:
    """Test pre-read size check using ResourceInfo.size via ResourceSizeController."""

    async def test_pre_read_deny_oversized(self) -> None:
        """Resource with known size exceeding max_read_bytes is rejected before reading."""
        resource = make_resource(
            uri="test://big",
            name="big",
            mime_type="text/plain",
            content="should not be read",
            size=5_000_000,  # Exceeds default max_read_bytes=1M
        )
        source = make_source_provider([resource])
        config = ResourceSizeConfig(max_read_bytes=1_000_000)
        provider = ResourceReadProvider(source_provider=source, size_config=config)

        with pytest.raises(ResourceReadError) as exc_info:
            await provider._read_resource("test://big")
        assert "too large" in exc_info.value.reason.lower()

    async def test_pre_read_allow_within_limit(self) -> None:
        """Resource with known size within max_read_bytes proceeds normally."""
        resource = make_resource(
            uri="test://ok",
            name="ok",
            mime_type="text/plain",
            content="hello",
            size=100,  # Well within default max_read_bytes
        )
        source = make_source_provider([resource])
        config = ResourceSizeConfig(max_read_bytes=1_000_000)
        provider = ResourceReadProvider(source_provider=source, size_config=config)

        result = await provider._read_resource("test://ok")
        assert result.content == "hello"

    async def test_pre_read_no_size_skips_check(self) -> None:
        """Resource without size field falls through to post-read gate."""
        big_content = "X" * 5000
        resource = make_resource(
            uri="test://nosize",
            name="nosize",
            mime_type="text/plain",
            content=big_content,
            size=None,  # No size info
        )
        source = make_source_provider([resource])
        config = ResourceSizeConfig(max_read_bytes=1000)
        provider = ResourceReadProvider(source_provider=source, size_config=config)

        # Falls through pre-read check, but caught by post-read gate
        with pytest.raises(ResourceReadError) as exc_info:
            await provider._read_resource("test://nosize")
        assert "too large" in exc_info.value.reason.lower()
