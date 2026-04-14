"""Real integration tests for ResourceReadProvider with actual provider implementations.

Uses StaticResourceProvider and AggregatingResourceProvider instead of mocks.
Only AgentContext is mocked (as MagicMock) since it's a framework object,
not a provider under test.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentpool.resource_providers import (
    AggregatingResourceProvider,
    EagerConfig,
    PrefixFilterTransform,
    ResourceDataType,
    ResourceInfo,
    ResourceReadError,
    ResourceReadProvider,
    ResourceSizeConfig,
    StaticResourceProvider,
    TransformChain,
    TruncationTransform,
)


def make_real_resource(
    uri: str = "test://resource",
    name: str = "test-resource",
    mime_type: str | None = None,
    description: str | None = None,
    content: str | None = None,
    size: int | None = None,
) -> ResourceInfo:
    """Create a ResourceInfo with a real reader function."""
    reader = None
    if content is not None:

        async def reader(uri: str) -> list[str]:
            return [content]

    return ResourceInfo(
        name=name,
        uri=uri,
        mime_type=mime_type,
        description=description,
        size=size,
        _reader=reader,
    )


@pytest.mark.integration
class TestStaticProviderIntegration:
    """Integration tests using real StaticResourceProvider as source."""

    async def test_read_from_static_provider(self) -> None:
        """ResourceReadProvider wrapping a StaticResourceProvider.

        Resources from the static provider are readable via the read_resource tool.
        """
        resource = make_real_resource(
            uri="static://config",
            name="config",
            mime_type="application/json",
            content='{"theme": "dark"}',
        )
        static_provider = StaticResourceProvider(name="static", resources=[resource])
        read_provider = ResourceReadProvider(source_provider=static_provider)

        # Resources are forwarded
        resources = await read_provider.get_resources()
        assert len(resources) == 1
        assert resources[0].uri == "static://config"

        # Tool works
        tools = await read_provider.get_tools()
        assert len(tools) == 1
        assert tools[0].name == "read_resource"

        # Actually read via tool
        ctx = MagicMock()  # Only AgentContext is mocked, not the provider
        result = await tools[0].execute(ctx, uri="static://config")
        assert '{"theme": "dark"}' in result

    async def test_static_provider_with_multiple_resources(self) -> None:
        """Multiple resources in StaticResourceProvider, each readable."""
        resources = [
            make_real_resource(
                uri="s://readme", name="readme", mime_type="text/markdown", content="# Hello"
            ),
            make_real_resource(
                uri="s://data", name="data", mime_type="application/json", content="[1,2,3]"
            ),
            make_real_resource(
                uri="s://config", name="config", mime_type="text/plain", content="debug=true"
            ),
        ]
        static_provider = StaticResourceProvider(name="static", resources=resources)
        read_provider = ResourceReadProvider(source_provider=static_provider)

        tools = await read_provider.get_tools()
        tool = tools[0]
        ctx = MagicMock()

        readme_result = await tool.execute(ctx, uri="s://readme")
        assert "# Hello" in readme_result

        data_result = await tool.execute(ctx, uri="s://data")
        assert "[1,2,3]" in data_result

        config_result = await tool.execute(ctx, uri="s://config")
        assert "debug=true" in config_result

    async def test_add_resource_to_static_after_creation(self) -> None:
        """Add resources after creation, then read via ResourceReadProvider."""
        static_provider = StaticResourceProvider(name="static")
        read_provider = ResourceReadProvider(source_provider=static_provider)

        # Initially empty
        resources = await read_provider.get_resources()
        assert len(resources) == 0

        # Add resource and invalidate cache
        new_resource = make_real_resource(
            uri="s://new", name="new", mime_type="text/plain", content="new content"
        )
        static_provider.add_resource(new_resource)
        read_provider.invalidate_cache()

        # Now visible
        resources = await read_provider.get_resources()
        assert len(resources) == 1

        tools = await read_provider.get_tools()
        ctx = MagicMock()
        result = await tools[0].execute(ctx, uri="s://new")
        assert "new content" in result


@pytest.mark.integration
class TestAggregatingProviderIntegration:
    """Integration tests using real AggregatingResourceProvider."""

    async def test_aggregate_two_static_providers(self) -> None:
        """Two StaticResourceProviders aggregated, ResourceReadProvider sees all resources."""
        provider_a = StaticResourceProvider(
            name="server-a",
            resources=[
                make_real_resource(
                    uri="a://config",
                    name="config-a",
                    mime_type="application/json",
                    content='{"a": 1}',
                ),
                make_real_resource(
                    uri="a://readme",
                    name="readme-a",
                    mime_type="text/plain",
                    content="Hello from A",
                ),
            ],
        )
        provider_b = StaticResourceProvider(
            name="server-b",
            resources=[
                make_real_resource(
                    uri="b://config",
                    name="config-b",
                    mime_type="application/json",
                    content='{"b": 2}',
                ),
            ],
        )
        aggregator = AggregatingResourceProvider(providers=[provider_a, provider_b])
        read_provider = ResourceReadProvider(source_provider=aggregator)

        # All resources visible
        resources = await read_provider.get_resources()
        assert len(resources) == 3
        uris = {r.uri for r in resources}
        assert uris == {"a://config", "a://readme", "b://config"}

        # All readable via tool
        tools = await read_provider.get_tools()
        tool = tools[0]
        ctx = MagicMock()

        result_a = await tool.execute(ctx, uri="a://config")
        assert '{"a": 1}' in result_a

        result_b = await tool.execute(ctx, uri="b://config")
        assert '{"b": 2}' in result_b

    async def test_aggregator_tool_aggregation(self) -> None:
        """Tools from both static and read providers are aggregated."""
        static_provider = StaticResourceProvider(
            name="source",
            resources=[make_real_resource(uri="s://data", name="data", content="hello")],
        )
        read_provider = ResourceReadProvider(source_provider=static_provider)
        aggregator = AggregatingResourceProvider(providers=[static_provider, read_provider])

        tools = await aggregator.get_tools()
        tool_names = [t.name for t in tools]
        assert "read_resource" in tool_names

    async def test_read_provider_reads_from_aggregated_sources(self) -> None:
        """ResourceReadProvider wrapping an aggregator can read from any source."""
        provider_a = StaticResourceProvider(
            name="a",
            resources=[make_real_resource(uri="a://x", name="x", content="from A")],
        )
        provider_b = StaticResourceProvider(
            name="b",
            resources=[make_real_resource(uri="b://y", name="y", content="from B")],
        )
        aggregator = AggregatingResourceProvider(providers=[provider_a, provider_b])
        read_provider = ResourceReadProvider(source_provider=aggregator)

        tools = await read_provider.get_tools()
        tool = tools[0]
        ctx = MagicMock()

        result_a = await tool.execute(ctx, uri="a://x")
        assert "from A" in result_a

        result_b = await tool.execute(ctx, uri="b://y")
        assert "from B" in result_b


@pytest.mark.integration
class TestEagerModeIntegration:
    """Integration tests for Eager mode with real providers."""

    async def test_eager_injection_with_static_provider(self) -> None:
        """Eager resources from StaticResourceProvider are injected as instructions."""
        resources = [
            make_real_resource(
                uri="s://important",
                name="important",
                mime_type="text/plain",
                content="CRITICAL INFO",
            ),
            make_real_resource(
                uri="s://large",
                name="large",
                mime_type="application/json",
                content='{"data": "big"}',
            ),
        ]
        static_provider = StaticResourceProvider(name="static", resources=resources)
        eager_config = EagerConfig(eager_mime_types=("text/plain",))
        read_provider = ResourceReadProvider(
            source_provider=static_provider,
            eager_config=eager_config,
        )

        # Get instructions
        instructions = await read_provider.get_instructions()
        assert len(instructions) == 1

        # Execute the instruction function
        ctx = MagicMock()
        content = await instructions[0](ctx)
        assert "CRITICAL INFO" in content
        assert "s://important" in content

    async def test_eager_and_lazy_coexist(self) -> None:
        """Eager resources are injected as instructions, lazy resources remain as tool."""
        resources = [
            make_real_resource(
                uri="s://eager", name="eager", mime_type="text/plain", content="eager content"
            ),
            make_real_resource(
                uri="s://lazy", name="lazy", mime_type="application/json", content='{"lazy": true}'
            ),
        ]
        static_provider = StaticResourceProvider(name="static", resources=resources)
        eager_config = EagerConfig(eager_mime_types=("text/plain",))
        read_provider = ResourceReadProvider(
            source_provider=static_provider,
            eager_config=eager_config,
        )

        # Eager: text/plain -> instruction
        instructions = await read_provider.get_instructions()
        assert len(instructions) == 1

        # Lazy: application/json -> still in tool catalog
        tools = await read_provider.get_tools()
        assert len(tools) == 1
        tool = tools[0]
        ctx = MagicMock()
        result = await tool.execute(ctx, uri="s://lazy")
        assert '{"lazy": true}' in result


@pytest.mark.integration
class TestTransformChainIntegration:
    """Integration tests for ContentTransform pipeline with real providers."""

    async def test_truncation_transform_with_static_provider(self) -> None:
        """TruncationTransform reduces content from static provider."""
        long_content = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5"
        resource = make_real_resource(
            uri="s://log", name="log", mime_type="text/plain", content=long_content
        )
        static_provider = StaticResourceProvider(name="static", resources=[resource])

        transform_chain = TransformChain([TruncationTransform(max_chars=20)])
        read_provider = ResourceReadProvider(
            source_provider=static_provider,
            transform_chain=transform_chain,
        )

        tools = await read_provider.get_tools()
        ctx = MagicMock()
        result = await tools[0].execute(ctx, uri="s://log")
        assert "Line 1" in result
        assert "Line 2" in result

    async def test_prefix_filter_transform(self) -> None:
        """PrefixFilterTransform removes comment lines from static provider."""
        content = "# Comment\nReal line\n# Another comment\nData line"
        resource = make_real_resource(
            uri="s://cfg", name="cfg", mime_type="text/plain", content=content
        )
        static_provider = StaticResourceProvider(name="static", resources=[resource])

        transform_chain = TransformChain([PrefixFilterTransform(exclude_prefixes=("#",))])
        read_provider = ResourceReadProvider(
            source_provider=static_provider,
            transform_chain=transform_chain,
        )

        result = await read_provider._read_resource("s://cfg")
        assert "# Comment" not in result.content
        assert "Real line" in result.content
        assert "Data line" in result.content


@pytest.mark.integration
class TestSizeControlIntegration:
    """Integration tests for size control with real providers."""

    async def test_truncation_with_static_provider(self) -> None:
        """Content from static provider is truncated when exceeding max_content_chars."""
        long_content = "A" * 5000
        resource = make_real_resource(
            uri="s://big", name="big", mime_type="text/plain", content=long_content
        )
        static_provider = StaticResourceProvider(name="static", resources=[resource])
        config = ResourceSizeConfig(max_content_chars=100)
        read_provider = ResourceReadProvider(source_provider=static_provider, size_config=config)

        result = await read_provider._read_resource("s://big")
        assert result.truncated is True
        assert result.original_size == 5000
        assert len(result.content) > 100  # content + truncate message

    async def test_binary_resource_error_with_static_provider(self) -> None:
        """Binary resource from static provider returns structured error."""
        resource = make_real_resource(
            uri="s://bin", name="bin", mime_type="application/octet-stream"
        )
        static_provider = StaticResourceProvider(name="static", resources=[resource])
        read_provider = ResourceReadProvider(source_provider=static_provider)

        with pytest.raises(ResourceReadError) as exc_info:
            await read_provider._read_resource("s://bin")
        assert exc_info.value.data_type is ResourceDataType.UNREADABLE
