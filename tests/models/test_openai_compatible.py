"""Tests for OpenAICompatibleModel."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from pydantic_ai.messages import (
    ModelRequest,
    RetryPromptPart,
    SystemPromptPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.profiles.openai import OpenAIModelProfile
import pytest

from agentpool.models.openai_compatible import (
    OpenAICompatibleModel,
    _coerce_profile_value,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_provider() -> MagicMock:
    """Create a mock provider with a real OpenAIModelProfile."""
    provider = MagicMock()
    provider.model_profile = OpenAIModelProfile()
    return provider


async def _collect(async_iter: Any) -> list[Any]:
    """Collect all items from an async iterator into a list."""
    return [item async for item in async_iter]


# ---------------------------------------------------------------------------
# 1. Subclass relationship
# ---------------------------------------------------------------------------


def test_is_subclass_of_openai_chat_model() -> None:
    """OpenAICompatibleModel should be a subclass of OpenAIChatModel."""
    from pydantic_ai.models.openai import OpenAIChatModel

    assert issubclass(OpenAICompatibleModel, OpenAIChatModel)


# ---------------------------------------------------------------------------
# 2. Constructor and profile handling
# ---------------------------------------------------------------------------


def test_constructor_accepts_base_url_and_api_key() -> None:
    """Constructor should accept base_url, api_key, and tool_return_as_list."""
    with patch("agentpool.models.openai_compatible.OpenAIProvider") as mock_provider_cls:
        mock_provider_cls.return_value = _mock_provider()
        model = OpenAICompatibleModel(
            model_name="test-model",
            base_url="https://api.example.com/v1",
            api_key="test-key",
            tool_return_as_list="true",
        )
        mock_provider_cls.assert_called_once_with(
            base_url="https://api.example.com/v1", api_key="test-key"
        )
        assert model.model_name == "test-model"


def test_tool_return_as_list_string_true() -> None:
    """tool_return_as_list='true' should set the flag to True."""
    with patch("agentpool.models.openai_compatible.OpenAIProvider") as mock_cls:
        mock_cls.return_value = _mock_provider()
        model = OpenAICompatibleModel(
            model_name="test",
            tool_return_as_list="true",
        )
        assert model._resolved_profile.get("openai_chat_tool_return_as_list") is True


def test_tool_return_as_list_string_false() -> None:
    """tool_return_as_list='false' should set the flag to False."""
    with patch("agentpool.models.openai_compatible.OpenAIProvider") as mock_cls:
        mock_cls.return_value = _mock_provider()
        model = OpenAICompatibleModel(
            model_name="test",
            tool_return_as_list="false",
        )
        assert model._resolved_profile.get("openai_chat_tool_return_as_list") is False


def test_tool_return_as_list_bool() -> None:
    """tool_return_as_list=True (bool) should set the flag to True."""
    with patch("agentpool.models.openai_compatible.OpenAIProvider") as mock_cls:
        mock_cls.return_value = _mock_provider()
        model = OpenAICompatibleModel(
            model_name="test",
            tool_return_as_list=True,
        )
        assert model._resolved_profile.get("openai_chat_tool_return_as_list") is True


def test_tool_return_as_list_default_false() -> None:
    """Default (no flag) should be False."""
    with patch("agentpool.models.openai_compatible.OpenAIProvider") as mock_cls:
        mock_cls.return_value = _mock_provider()
        model = OpenAICompatibleModel(model_name="test")
        assert model._resolved_profile.get("openai_chat_tool_return_as_list") is False


def test_openai_profile_overrides_passed_through() -> None:
    """openai_* prefixed kwargs should be merged into the profile."""
    with patch("agentpool.models.openai_compatible.OpenAIProvider") as mock_cls:
        mock_cls.return_value = _mock_provider()
        model = OpenAICompatibleModel(
            model_name="test",
            tool_return_as_list="true",
            openai_system_prompt_role="developer",
            openai_supports_strict_tool_definition="false",
        )
        profile = OpenAIModelProfile.from_profile(model.profile)
        assert profile.openai_system_prompt_role == "developer"
        assert profile.openai_supports_strict_tool_definition is False


def test_non_openai_kwarg_raises_type_error() -> None:
    """Non-openai_* unknown kwargs should raise TypeError."""
    with patch("agentpool.models.openai_compatible.OpenAIProvider") as mock_cls:
        mock_cls.return_value = _mock_provider()
        with pytest.raises(TypeError, match="Unexpected keyword argument"):
            OpenAICompatibleModel(
                model_name="test",
                foo_bar="baz",  # type: ignore[call-arg]
            )


def test_profile_dict_merged_with_overrides() -> None:
    """When profile is a ModelProfile, overrides should be merged in."""
    with patch("agentpool.models.openai_compatible.OpenAIProvider") as mock_cls:
        mock_cls.return_value = _mock_provider()
        model = OpenAICompatibleModel(
            model_name="test",
            tool_return_as_list="true",
            profile=OpenAIModelProfile(openai_system_prompt_role="developer"),
        )
        profile = OpenAIModelProfile.from_profile(model.profile)
        assert profile.openai_system_prompt_role == "developer"
        assert model._resolved_profile.get("openai_chat_tool_return_as_list") is True


# ---------------------------------------------------------------------------
# 3. _coerce_profile_value helper
# ---------------------------------------------------------------------------


def test_coerce_bool_true() -> None:
    """String 'true' should coerce to True for boolean keys."""
    assert _coerce_profile_value("openai_supports_strict_tool_definition", "true") is True


def test_coerce_bool_false() -> None:
    """String 'false' should coerce to False for boolean keys."""
    assert _coerce_profile_value("openai_supports_strict_tool_definition", "false") is False


def test_coerce_non_bool_key_unchanged() -> None:
    """Non-boolean keys should return the value unchanged."""
    assert _coerce_profile_value("openai_system_prompt_role", "developer") == "developer"


def test_coerce_non_string_value_unchanged() -> None:
    """Non-string values should be returned unchanged even for boolean keys."""
    assert _coerce_profile_value("openai_supports_strict_tool_definition", True) is True
    assert _coerce_profile_value("openai_supports_strict_tool_definition", 1) == 1


# ---------------------------------------------------------------------------
# 4. _map_user_message behavior
# ---------------------------------------------------------------------------


@pytest.fixture
def model_flag_disabled() -> OpenAICompatibleModel:
    """Model with tool_return_as_list disabled (default)."""
    with patch("agentpool.models.openai_compatible.OpenAIProvider") as mock_cls:
        mock_cls.return_value = _mock_provider()
        return OpenAICompatibleModel(model_name="test", tool_return_as_list="false")


@pytest.fixture
def model_flag_enabled() -> OpenAICompatibleModel:
    """Model with tool_return_as_list enabled."""
    with patch("agentpool.models.openai_compatible.OpenAIProvider") as mock_cls:
        mock_cls.return_value = _mock_provider()
        return OpenAICompatibleModel(model_name="test", tool_return_as_list="true")


async def test_flag_disabled_delegates_to_super(
    model_flag_disabled: OpenAICompatibleModel,
) -> None:
    """When flag is False, _map_user_message should delegate to parent."""
    part = ToolReturnPart(
        tool_name="test_tool",
        content=["item1", "item2"],
        tool_call_id="call_123",
    )
    message = ModelRequest(parts=[part])

    # Mock the parent's _map_user_message to verify delegation
    with (
        patch.object(
            OpenAIChatModel,
            "_map_user_message",
            return_value=AsyncIteratorMock([MagicMock()]),
        ) as mock_super,
    ):
        await _collect(model_flag_disabled._map_user_message(message))
        mock_super.assert_called_once_with(message)


async def test_flag_enabled_list_string_items(
    model_flag_enabled: OpenAICompatibleModel,
) -> None:
    """Flag True + list of strings -> content is list of text parts."""
    part = ToolReturnPart(
        tool_name="test_tool",
        content=["result1", "result2"],
        tool_call_id="call_123",
    )
    message = ModelRequest(parts=[part])

    results = await _collect(model_flag_enabled._map_user_message(message))

    assert len(results) == 1
    tool_msg = results[0]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "call_123"
    content = tool_msg["content"]
    assert isinstance(content, list)
    assert len(content) == 2
    assert content[0]["type"] == "text"
    assert content[0]["text"] == "result1"
    assert content[1]["type"] == "text"
    assert content[1]["text"] == "result2"


async def test_flag_enabled_list_non_string_items(
    model_flag_enabled: OpenAICompatibleModel,
) -> None:
    """Flag True + list with non-string items -> each JSON-serialized and wrapped."""
    part = ToolReturnPart(
        tool_name="test_tool",
        content=[{"key": "value"}, 42],
        tool_call_id="call_123",
    )
    message = ModelRequest(parts=[part])

    results = await _collect(model_flag_enabled._map_user_message(message))

    assert len(results) == 1
    tool_msg = results[0]
    content = tool_msg["content"]
    assert isinstance(content, list)
    assert len(content) == 2
    assert content[0]["type"] == "text"
    # Non-string items are JSON-serialized via content_items(mode='str')
    assert '"key"' in content[0]["text"]
    assert "value" in content[0]["text"]
    assert content[1]["type"] == "text"
    assert content[1]["text"] == "42"


async def test_flag_enabled_string_content(
    model_flag_enabled: OpenAICompatibleModel,
) -> None:
    """Flag True + string content -> content remains plain string."""
    part = ToolReturnPart(
        tool_name="test_tool",
        content="plain string",
        tool_call_id="call_123",
    )
    message = ModelRequest(parts=[part])

    results = await _collect(model_flag_enabled._map_user_message(message))

    assert len(results) == 1
    tool_msg = results[0]
    content = tool_msg["content"]
    assert isinstance(content, str)
    assert content == "plain string"


async def test_flag_enabled_empty_list(
    model_flag_enabled: OpenAICompatibleModel,
) -> None:
    """Flag True + empty list -> falls back to parent (empty string)."""
    part = ToolReturnPart(
        tool_name="test_tool",
        content=[],
        tool_call_id="call_123",
    )
    message = ModelRequest(parts=[part])

    results = await _collect(model_flag_enabled._map_user_message(message))

    assert len(results) == 1
    tool_msg = results[0]
    content = tool_msg["content"]
    # Empty list falls back to parent behavior which serializes to ''
    assert isinstance(content, str)


async def test_flag_enabled_system_prompt_part(
    model_flag_enabled: OpenAICompatibleModel,
) -> None:
    """Flag True + SystemPromptPart -> mapped identically to parent."""
    part = SystemPromptPart(content="You are a helpful assistant.")
    message = ModelRequest(parts=[part])

    results = await _collect(model_flag_enabled._map_user_message(message))

    assert len(results) == 1
    sys_msg = results[0]
    assert sys_msg["role"] == "system"
    assert sys_msg["content"] == "You are a helpful assistant."


async def test_flag_enabled_system_prompt_developer_role(
    model_flag_enabled: OpenAICompatibleModel,
) -> None:
    """Flag True + SystemPromptPart with developer role -> developer message."""
    with patch("agentpool.models.openai_compatible.OpenAIProvider") as mock_cls:
        mock_cls.return_value = _mock_provider()
        model = OpenAICompatibleModel(
            model_name="test",
            tool_return_as_list="true",
            openai_system_prompt_role="developer",
        )
    part = SystemPromptPart(content="You are a developer.")
    message = ModelRequest(parts=[part])

    results = await _collect(model._map_user_message(message))

    assert len(results) == 1
    assert results[0]["role"] == "developer"


async def test_flag_enabled_retry_prompt_with_tool_name(
    model_flag_enabled: OpenAICompatibleModel,
) -> None:
    """Flag True + RetryPromptPart with tool_name -> tool message."""
    part = RetryPromptPart(
        tool_name="test_tool",
        content="Retry this",
        tool_call_id="call_123",
    )
    message = ModelRequest(parts=[part])

    results = await _collect(model_flag_enabled._map_user_message(message))

    assert len(results) == 1
    tool_msg = results[0]
    assert tool_msg["role"] == "tool"


async def test_flag_enabled_retry_prompt_without_tool_name(
    model_flag_enabled: OpenAICompatibleModel,
) -> None:
    """Flag True + RetryPromptPart without tool_name -> user message."""
    part = RetryPromptPart(
        tool_name=None,
        content="Retry this",
    )
    message = ModelRequest(parts=[part])

    results = await _collect(model_flag_enabled._map_user_message(message))

    assert len(results) == 1
    user_msg = results[0]
    assert user_msg["role"] == "user"


async def test_flag_enabled_mixed_message(
    model_flag_enabled: OpenAICompatibleModel,
) -> None:
    """Flag True + mixed message parts -> only ToolReturnPart with list is modified."""
    from pydantic_ai.messages import ModelRequestPart

    parts: list[ModelRequestPart] = [
        UserPromptPart(content="Run the tool"),
        ToolReturnPart(
            tool_name="test_tool",
            content=["result1", "result2"],
            tool_call_id="call_1",
        ),
        ToolReturnPart(
            tool_name="other_tool",
            content="string result",
            tool_call_id="call_2",
        ),
    ]
    message = ModelRequest(parts=parts)

    results = await _collect(model_flag_enabled._map_user_message(message))

    # Should have: user prompt, tool msg 1 (list), tool msg 2 (string)
    assert len(results) == 3
    # First: user message
    assert results[0]["role"] == "user"
    # Second: tool message with list content
    assert results[1]["role"] == "tool"
    assert isinstance(results[1]["content"], list)
    assert len(results[1]["content"]) == 2
    # Third: tool message with string content
    assert results[2]["role"] == "tool"
    assert isinstance(results[2]["content"], str)


# ---------------------------------------------------------------------------
# 5. Integration: ImportModelConfig resolution
# ---------------------------------------------------------------------------


def test_import_model_config_resolves_model() -> None:
    """ImportModelConfig should resolve OpenAICompatibleModel from YAML."""
    from llmling_models_config import ImportModelConfig

    config = ImportModelConfig(
        model="agentpool.models.openai_compatible.OpenAICompatibleModel",
        kw_args={
            "model_name": "glm-5",
            "base_url": "https://open.bigmodel.cn/api/paas/v4/",
            "api_key": "test-key",
            "tool_return_as_list": "true",
            "openai_system_prompt_role": "developer",
            "openai_supports_strict_tool_definition": "false",
        },
    )
    model = config.get_model()
    assert isinstance(model, OpenAICompatibleModel)
    assert model._resolved_profile.get("openai_chat_tool_return_as_list") is True
    profile = OpenAIModelProfile.from_profile(model.profile)
    assert profile.openai_system_prompt_role == "developer"
    assert profile.openai_supports_strict_tool_definition is False


def test_import_model_config_non_openai_kwarg_raises() -> None:
    """ImportModelConfig with non-openai_* kwarg should raise TypeError."""
    from llmling_models_config import ImportModelConfig

    config = ImportModelConfig(
        model="agentpool.models.openai_compatible.OpenAICompatibleModel",
        kw_args={
            "model_name": "test",
            "foo_bar": "baz",
        },
    )
    with pytest.raises(TypeError, match="Unexpected keyword argument"):
        config.get_model()


# ---------------------------------------------------------------------------
# Helper: AsyncIteratorMock
# ---------------------------------------------------------------------------


# Import at module level for patch target
from pydantic_ai.models.openai import OpenAIChatModel  # noqa: E402


class AsyncIteratorMock:
    """Mock that acts as an async iterator yielding provided items."""

    def __init__(self, items: list[Any]) -> None:
        self._items = items
        self._index = 0

    def __aiter__(self) -> AsyncIteratorMock:
        return self

    async def __anext__(self) -> Any:
        if self._index >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._index]
        self._index += 1
        return item
