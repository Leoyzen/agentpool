"""Tests for resource_read_types module."""

from __future__ import annotations

import dataclasses

import pytest

from agentpool.resource_providers.resource_info import ResourceInfo
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
)


class TestResourceDataType:
    """Test ResourceDataType StrEnum values and behavior."""

    def test_text_value(self) -> None:
        assert ResourceDataType.TEXT == "text"

    def test_multimodal_value(self) -> None:
        assert ResourceDataType.MULTIMODAL == "multimodal"

    def test_unreadable_value(self) -> None:
        assert ResourceDataType.UNREADABLE == "unreadable"

    def test_probe_needed_value(self) -> None:
        assert ResourceDataType.PROBE_NEEDED == "probe_needed"

    def test_is_str_enum(self) -> None:
        assert isinstance(ResourceDataType.TEXT, str)
        assert isinstance(ResourceDataType.MULTIMODAL, str)
        assert isinstance(ResourceDataType.UNREADABLE, str)
        assert isinstance(ResourceDataType.PROBE_NEEDED, str)

    def test_all_values(self) -> None:
        members = list(ResourceDataType)
        assert len(members) == 4
        assert ResourceDataType.TEXT in members
        assert ResourceDataType.MULTIMODAL in members
        assert ResourceDataType.UNREADABLE in members
        assert ResourceDataType.PROBE_NEEDED in members


class TestResourceSizeConfig:
    """Test ResourceSizeConfig frozen dataclass defaults and behavior."""

    def test_default_values(self) -> None:
        config = ResourceSizeConfig()
        assert config.max_read_bytes == 1_000_000
        assert config.max_content_chars == 100_000
        assert config.truncate_message == "... [truncated: {original} chars → {limit} chars]"

    def test_custom_values(self) -> None:
        config = ResourceSizeConfig(
            max_read_bytes=500,
            max_content_chars=200,
            truncate_message="cut off",
        )
        assert config.max_read_bytes == 500
        assert config.max_content_chars == 200
        assert config.truncate_message == "cut off"

    def test_frozen(self) -> None:
        config = ResourceSizeConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            config.max_read_bytes = 0  # type: ignore[misc]

    def test_negative_one_unlimited(self) -> None:
        config = ResourceSizeConfig(max_read_bytes=-1, max_content_chars=-1)
        assert config.max_read_bytes == -1
        assert config.max_content_chars == -1


class TestResourceReadResult:
    """Test ResourceReadResult frozen dataclass construction and defaults."""

    def test_construction_with_required_fields(self) -> None:
        result = ResourceReadResult(
            uri="file:///test.txt",
            content="hello",
            data_type=ResourceDataType.TEXT,
        )
        assert result.uri == "file:///test.txt"
        assert result.content == "hello"
        assert result.data_type is ResourceDataType.TEXT

    def test_default_optional_fields(self) -> None:
        result = ResourceReadResult(
            uri="file:///test.txt",
            content="hello",
            data_type=ResourceDataType.TEXT,
        )
        assert result.truncated is False
        assert result.original_size is None
        assert result.mime_type is None
        assert result.error is None

    def test_all_fields(self) -> None:
        result = ResourceReadResult(
            uri="file:///image.png",
            content="<image data>",
            data_type=ResourceDataType.MULTIMODAL,
            truncated=True,
            original_size=5000,
            mime_type="image/png",
            error="partial read",
        )
        assert result.uri == "file:///image.png"
        assert result.content == "<image data>"
        assert result.data_type is ResourceDataType.MULTIMODAL
        assert result.truncated is True
        assert result.original_size == 5000
        assert result.mime_type == "image/png"
        assert result.error == "partial read"

    def test_frozen(self) -> None:
        result = ResourceReadResult(
            uri="file:///test.txt",
            content="hello",
            data_type=ResourceDataType.TEXT,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.content = "changed"  # type: ignore[misc]


class TestResourceReadError:
    """Test ResourceReadError exception construction and attributes."""

    def test_construction(self) -> None:
        error = ResourceReadError(uri="file:///secret.bin", reason="binary content")
        assert error.uri == "file:///secret.bin"
        assert error.reason == "binary content"
        assert error.data_type is ResourceDataType.UNREADABLE

    def test_custom_data_type(self) -> None:
        error = ResourceReadError(
            uri="file:///unknown",
            reason="need probe",
            data_type=ResourceDataType.PROBE_NEEDED,
        )
        assert error.data_type is ResourceDataType.PROBE_NEEDED

    def test_message_format(self) -> None:
        error = ResourceReadError(uri="file:///data.bin", reason="cannot decode")
        message = str(error)
        assert "file:///data.bin" in message
        assert "cannot decode" in message

    def test_is_exception(self) -> None:
        error = ResourceReadError(uri="file:///x", reason="fail")
        assert isinstance(error, Exception)

    def test_attributes(self) -> None:
        error = ResourceReadError(
            uri="mcp://server/resource",
            reason="timeout",
            data_type=ResourceDataType.MULTIMODAL,
        )
        assert error.uri == "mcp://server/resource"
        assert error.reason == "timeout"
        assert error.data_type is ResourceDataType.MULTIMODAL


class TestContentTransform:
    """Test ContentTransform protocol structural subtyping."""

    def test_protocol_structural_subtyping(self) -> None:
        class UppercaseTransform:
            async def transform(self, content: str, result: ResourceReadResult) -> str:
                return content.upper()

        instance = UppercaseTransform()
        assert callable(instance.transform)
        # Verify the method signature accepts the expected parameters
        import inspect

        sig = inspect.signature(instance.transform)
        params = list(sig.parameters)
        assert "content" in params
        assert "result" in params


class TestReadTier:
    """Test ReadTier StrEnum values and behavior."""

    def test_eager_value(self) -> None:
        assert ReadTier.EAGER == "eager"

    def test_lazy_value(self) -> None:
        assert ReadTier.LAZY == "lazy"

    def test_skip_value(self) -> None:
        assert ReadTier.SKIP == "skip"

    def test_is_str_enum(self) -> None:
        assert isinstance(ReadTier.EAGER, str)
        assert isinstance(ReadTier.LAZY, str)
        assert isinstance(ReadTier.SKIP, str)

    def test_all_values(self) -> None:
        members = list(ReadTier)
        assert len(members) == 3
        assert ReadTier.EAGER in members
        assert ReadTier.LAZY in members
        assert ReadTier.SKIP in members


class TestEagerConfig:
    """Test EagerConfig frozen dataclass defaults and behavior."""

    def test_default_values(self) -> None:
        config = EagerConfig()
        assert config.max_eager_chars == 10_000
        assert config.eager_mime_types == (
            "text/plain",
            "text/markdown",
            "text/csv",
            "application/json",
        )
        assert config.eager_uri_prefixes == ()
        assert config.include_metadata is True

    def test_custom_values(self) -> None:
        config = EagerConfig(
            max_eager_chars=5000,
            eager_mime_types=("text/plain",),
            eager_uri_prefixes=("config://",),
            include_metadata=False,
        )
        assert config.max_eager_chars == 5000
        assert config.eager_mime_types == ("text/plain",)
        assert config.eager_uri_prefixes == ("config://",)
        assert config.include_metadata is False

    def test_frozen(self) -> None:
        config = EagerConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            config.max_eager_chars = 0  # type: ignore[misc]

    def test_empty_mime_types(self) -> None:
        config = EagerConfig(eager_mime_types=())
        assert config.eager_mime_types == ()


class TestDefaultEagerReadStrategy:
    """Test DefaultEagerReadStrategy classification logic."""

    def _make_resource(
        self,
        uri: str = "test://resource",
        mime_type: str | None = None,
    ) -> ResourceInfo:
        return ResourceInfo(name="test", uri=uri, mime_type=mime_type)

    def test_empty_config_returns_lazy(self) -> None:
        config = EagerConfig(eager_mime_types=(), eager_uri_prefixes=())
        strategy = DefaultEagerReadStrategy(config)
        resource = self._make_resource(mime_type="text/plain")
        assert strategy.decide(resource) is ReadTier.LAZY

    def test_mime_type_match_returns_eager(self) -> None:
        config = EagerConfig(eager_mime_types=("text/plain",))
        strategy = DefaultEagerReadStrategy(config)
        resource = self._make_resource(mime_type="text/plain")
        assert strategy.decide(resource) is ReadTier.EAGER

    def test_uri_prefix_match_returns_eager(self) -> None:
        config = EagerConfig(eager_uri_prefixes=("important://",))
        strategy = DefaultEagerReadStrategy(config)
        resource = self._make_resource(uri="important://settings")
        assert strategy.decide(resource) is ReadTier.EAGER

    def test_no_match_returns_lazy(self) -> None:
        config = EagerConfig(eager_mime_types=("text/plain",), eager_uri_prefixes=("important://",))
        strategy = DefaultEagerReadStrategy(config)
        resource = self._make_resource(uri="test://a", mime_type="image/png")
        assert strategy.decide(resource) is ReadTier.LAZY

    def test_none_mime_type_not_eager(self) -> None:
        config = EagerConfig(eager_mime_types=("text/plain",))
        strategy = DefaultEagerReadStrategy(config)
        resource = self._make_resource(mime_type=None)
        assert strategy.decide(resource) is ReadTier.LAZY

    def test_default_config_constructor(self) -> None:
        strategy = DefaultEagerReadStrategy()
        resource = self._make_resource(mime_type="text/plain")
        assert strategy.decide(resource) is ReadTier.EAGER


class TestSizeCheckResult:
    """Test SizeCheckResult StrEnum values."""

    def test_values(self) -> None:
        assert SizeCheckResult.ALLOW == "allow"
        assert SizeCheckResult.DENY_TOO_LARGE == "deny_too_large"
        assert SizeCheckResult.DENY_RATE_LIMITED == "deny_rate_limited"
        assert SizeCheckResult.WARN_LARGE == "warn_large"


class TestResourceSizeController:
    """Test ResourceSizeController decision logic."""

    def test_pre_read_check_allow(self) -> None:
        ctrl = ResourceSizeController()
        assert ctrl.pre_read_check(100) is SizeCheckResult.ALLOW

    def test_pre_read_check_deny(self) -> None:
        config = ResourceSizeConfig(max_read_bytes=1000)
        ctrl = ResourceSizeController(config=config)
        assert ctrl.pre_read_check(2000) is SizeCheckResult.DENY_TOO_LARGE

    def test_pre_read_check_warn(self) -> None:
        config = ResourceSizeConfig(max_read_bytes=1000)
        ctrl = ResourceSizeController(config=config)
        # 900 > 800 (80% of 1000) but < 1000
        assert ctrl.pre_read_check(900) is SizeCheckResult.WARN_LARGE

    def test_pre_read_check_unlimited(self) -> None:
        config = ResourceSizeConfig(max_read_bytes=-1)
        ctrl = ResourceSizeController(config=config)
        assert ctrl.pre_read_check(999_999_999) is SizeCheckResult.ALLOW

    def test_rate_limit_within(self) -> None:
        ctrl = ResourceSizeController(rate_limit=5)
        assert ctrl.check_rate_limit() is True

    def test_rate_limit_exceeded(self) -> None:
        ctrl = ResourceSizeController(rate_limit=2)
        ctrl.increment_read_count()
        ctrl.increment_read_count()
        assert ctrl.check_rate_limit() is False

    def test_rate_limit_unlimited(self) -> None:
        ctrl = ResourceSizeController(rate_limit=0)
        for _ in range(100):
            ctrl.increment_read_count()
        assert ctrl.check_rate_limit() is True

    def test_increment_read_count(self) -> None:
        ctrl = ResourceSizeController()
        assert ctrl.increment_read_count() == 1
        assert ctrl.increment_read_count() == 2

    def test_reset_read_count(self) -> None:
        ctrl = ResourceSizeController()
        ctrl.increment_read_count()
        ctrl.increment_read_count()
        ctrl.reset_read_count()
        assert ctrl._read_count == 0
