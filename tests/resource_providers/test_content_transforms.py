"""Tests for content_transforms module."""

from __future__ import annotations

from agentpool.resource_providers.content_transforms import (
    HeadTailTransform,
    PrefixFilterTransform,
    TruncationTransform,
)
from agentpool.resource_providers.resource_read_types import (
    ResourceDataType,
    ResourceReadResult,
    TransformChain,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_result(content: str = "") -> ResourceReadResult:
    """Create a minimal ResourceReadResult for transform tests."""
    return ResourceReadResult(
        uri="test://resource",
        content=content,
        data_type=ResourceDataType.TEXT,
    )


# ---------------------------------------------------------------------------
# TestTruncationTransform
# ---------------------------------------------------------------------------


class TestTruncationTransform:
    """Test TruncationTransform — boundary-aware truncation."""

    async def test_no_truncation_when_short(self) -> None:
        transform = TruncationTransform(max_chars=100)
        result = make_result("short content")
        assert await transform.transform("short content", result) == "short content"

    async def test_truncation_at_newline_boundary(self) -> None:
        content = "line1\nline2\nline3\nline4\nline5"
        transform = TruncationTransform(max_chars=20, boundary_search=15)
        result = make_result(content)
        output = await transform.transform(content, result)
        # Should truncate at a newline boundary, not mid-line
        assert output.endswith("...")
        assert "\n" in output[:-3]  # Content before suffix contains a newline

    async def test_truncation_without_boundary(self) -> None:
        content = "a" * 200
        transform = TruncationTransform(max_chars=100, boundary_search=50)
        result = make_result(content)
        output = await transform.transform(content, result)
        assert output == "a" * 100 + "..."

    async def test_custom_suffix(self) -> None:
        content = "a" * 200
        transform = TruncationTransform(max_chars=100, suffix=" [TRUNCATED]")
        result = make_result(content)
        output = await transform.transform(content, result)
        assert output.endswith(" [TRUNCATED]")
        assert len(output) == 100 + len(" [TRUNCATED]")

    async def test_exact_length(self) -> None:
        content = "a" * 100
        transform = TruncationTransform(max_chars=100)
        result = make_result(content)
        assert await transform.transform(content, result) == content


# ---------------------------------------------------------------------------
# TestHeadTailTransform
# ---------------------------------------------------------------------------


class TestHeadTailTransform:
    """Test HeadTailTransform — head/tail content splitting."""

    async def test_short_content_unchanged(self) -> None:
        content = "short content"
        transform = HeadTailTransform(head_chars=5000, tail_chars=5000)
        result = make_result(content)
        assert await transform.transform(content, result) == content

    async def test_head_tail_split(self) -> None:
        head_chars = 10
        tail_chars = 10
        separator = "\n...OMITTED...\n"
        transform = HeadTailTransform(
            head_chars=head_chars, tail_chars=tail_chars, separator=separator
        )
        content = "a" * 50
        result = make_result(content)
        output = await transform.transform(content, result)
        assert output.startswith("a" * head_chars)
        assert output.endswith("a" * tail_chars)
        assert "...OMITTED..." in output

    async def test_custom_separator(self) -> None:
        transform = HeadTailTransform(head_chars=5, tail_chars=5, separator="<<MID>>")
        content = "abcdefghij" * 10
        result = make_result(content)
        output = await transform.transform(content, result)
        assert "<<MID>>" in output

    async def test_boundary_case(self) -> None:
        head_chars = 5
        tail_chars = 5
        separator = "---"
        # Content exactly at threshold: head + tail + separator
        threshold = head_chars + tail_chars + len(separator)
        content = "a" * threshold
        transform = HeadTailTransform(
            head_chars=head_chars, tail_chars=tail_chars, separator=separator
        )
        result = make_result(content)
        assert await transform.transform(content, result) == content


# ---------------------------------------------------------------------------
# TestPrefixFilterTransform
# ---------------------------------------------------------------------------


class TestPrefixFilterTransform:
    """Test PrefixFilterTransform — line prefix filtering."""

    async def test_no_filtering_when_no_matches(self) -> None:
        content = "hello\nworld\nfoo bar"
        transform = PrefixFilterTransform()
        result = make_result(content)
        assert await transform.transform(content, result) == content

    async def test_filter_hash_comments(self) -> None:
        content = "# comment\ncode line\n# another comment\nmore code"
        transform = PrefixFilterTransform(exclude_prefixes=("#",))
        result = make_result(content)
        output = await transform.transform(content, result)
        assert "# comment" not in output
        assert "# another comment" not in output
        assert "code line" in output
        assert "more code" in output

    async def test_filter_double_slash(self) -> None:
        content = "// comment\ncode\n// another\nmore"
        transform = PrefixFilterTransform(exclude_prefixes=("//",))
        result = make_result(content)
        output = await transform.transform(content, result)
        assert "// comment" not in output
        assert "// another" not in output
        assert "code" in output
        assert "more" in output

    async def test_preserves_non_comment_lines(self) -> None:
        content = "real code\n  # indented comment\nmore code"
        transform = PrefixFilterTransform(exclude_prefixes=("#",))
        result = make_result(content)
        output = await transform.transform(content, result)
        assert "real code" in output
        assert "more code" in output
        # Indented comment is filtered because lstrip() removes leading spaces
        assert "# indented comment" not in output

    async def test_custom_prefixes(self) -> None:
        content = ";; elisp comment\ncode\n;; another\nmore"
        transform = PrefixFilterTransform(exclude_prefixes=(";;",))
        result = make_result(content)
        output = await transform.transform(content, result)
        assert ";; elisp comment" not in output
        assert ";; another" not in output
        assert "code" in output
        assert "more" in output


# ---------------------------------------------------------------------------
# TestTransformChain
# ---------------------------------------------------------------------------


class TestTransformChain:
    """Test TransformChain — sequential transform application."""

    async def test_empty_chain(self) -> None:
        chain = TransformChain()
        result = make_result("hello")
        assert await chain.apply("hello", result) == "hello"

    async def test_single_transform(self) -> None:
        transform = TruncationTransform(max_chars=5)
        chain = TransformChain([transform])
        result = make_result("hello world")
        output = await chain.apply("hello world", result)
        assert output == "hello..."

    async def test_multiple_transforms_sequential(self) -> None:
        # First filter comments, then truncate
        filter_transform = PrefixFilterTransform(exclude_prefixes=("#",))
        truncate_transform = TruncationTransform(max_chars=10)
        chain = TransformChain([filter_transform, truncate_transform])
        content = "# comment\ncode line here that is long"
        result = make_result(content)
        output = await chain.apply(content, result)
        # After filtering: "code line here that is long"
        # After truncation: first 10 chars + "..."
        assert "#" not in output
        assert output.endswith("...")

    async def test_add_transform(self) -> None:
        chain = TransformChain()
        assert chain.transforms == []

        transform = TruncationTransform(max_chars=5)
        chain.add(transform)
        assert len(chain.transforms) == 1

        result = make_result("hello world")
        output = await chain.apply("hello world", result)
        assert output == "hello..."

    async def test_transforms_property_returns_copy(self) -> None:
        transform = TruncationTransform(max_chars=5)
        chain = TransformChain([transform])
        transforms = chain.transforms
        transforms.clear()
        # Original should be unaffected
        assert len(chain.transforms) == 1


# ---------------------------------------------------------------------------
# TestIntegrationWithProvider
# ---------------------------------------------------------------------------


class TestIntegrationWithProvider:
    """Test ResourceReadProvider integration with TransformChain."""

    async def test_provider_with_transform_chain(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from agentpool.resource_providers.base import ResourceProvider
        from agentpool.resource_providers.resource_info import ResourceInfo
        from agentpool.resource_providers.resource_read_provider import ResourceReadProvider

        # Create a resource with comment-heavy content
        content = "# comment 1\n# comment 2\nactual content here"
        resource_info: ResourceInfo = ResourceInfo(
            name="test",
            uri="test://doc",
            mime_type="text/plain",
            _reader=None,
        )

        async def reader(uri: str) -> list[str]:
            return [content]

        resource_info._reader = reader  # type: ignore[assignment]

        source = MagicMock(spec=ResourceProvider)
        source.get_resources = AsyncMock(return_value=[resource_info])

        # Create provider with prefix filter transform
        chain = TransformChain([PrefixFilterTransform(exclude_prefixes=("#",))])
        provider = ResourceReadProvider(
            source_provider=source,
            transform_chain=chain,
        )

        result = await provider._read_resource("test://doc")
        # Comments should be filtered out
        assert "# comment" not in result.content
        assert "actual content here" in result.content

    async def test_transform_before_truncation(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from agentpool.resource_providers.base import ResourceProvider
        from agentpool.resource_providers.resource_info import ResourceInfo
        from agentpool.resource_providers.resource_read_provider import ResourceReadProvider
        from agentpool.resource_providers.resource_read_types import ResourceSizeConfig

        # Content: 200 chars of comments + 50 chars of real content
        comment_lines = "# " + "x" * 47  # 50 chars per comment line
        comments = "\n".join([comment_lines] * 4)  # 200 chars of comments
        real_content = "a" * 50
        content = comments + "\n" + real_content

        resource_info: ResourceInfo = ResourceInfo(
            name="test",
            uri="test://doc",
            mime_type="text/plain",
            _reader=None,
        )

        async def reader(uri: str) -> list[str]:
            return [content]

        resource_info._reader = reader  # type: ignore[assignment]

        source = MagicMock(spec=ResourceProvider)
        source.get_resources = AsyncMock(return_value=[resource_info])

        # Transform: filter comments, then size-control truncation at 100 chars
        chain = TransformChain([PrefixFilterTransform(exclude_prefixes=("#",))])
        config = ResourceSizeConfig(max_content_chars=100)
        provider = ResourceReadProvider(
            source_provider=source,
            size_config=config,
            transform_chain=chain,
        )

        result = await provider._read_resource("test://doc")
        # After filter: only real_content remains (50 chars)
        # 50 chars < 100 char limit → no truncation needed
        assert result.truncated is False
        assert "a" * 50 in result.content
        # Comments should be gone
        assert "# " not in result.content
