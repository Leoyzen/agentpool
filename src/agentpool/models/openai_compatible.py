"""OpenAI-compatible model with native list tool return support.

Subclass of pydantic-ai's ``OpenAIChatModel`` that optionally emits native list
content for tool return messages instead of JSON-serialized strings. This is
useful for OpenAI-compatible models (GLM-5, vLLM, etc.) whose chat templates
natively render list-type tool message content.

Example YAML manifest usage:

```yaml
model_variants:
  glm-5:
    type: import
    model: agentpool.models.openai_compatible.OpenAICompatibleModel
    kw_args:
      model_name: "glm-5"
      base_url: "https://open.bigmodel.cn/api/paas/v4/"
      api_key: "${OPENAI_API_KEY}"
      tool_return_as_list: "true"
      openai_system_prompt_role: "developer"
      openai_supports_strict_tool_definition: "false"
```
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict, cast, override

from pydantic_ai.messages import (
    ModelRequest,
    RetryPromptPart,
    SystemPromptPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.openai import (  # type: ignore[attr-defined]
    OpenAIChatModel,
    _guard_tool_call_id,
)
from pydantic_ai.profiles import ModelProfile, ModelProfileSpec
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider


try:
    from openai.types import chat
    from openai.types.chat import ChatCompletionContentPartTextParam
except ImportError:  # pragma: no cover
    pass

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openai import AsyncOpenAI
    from pydantic_ai.providers import Provider
    from pydantic_ai.settings import ModelSettings


class OpenAICompatibleModelProfile(TypedDict, total=False):
    """Profile dict for :class:`OpenAICompatibleModel`.

    Extends ``OpenAIModelProfile`` fields with the additional
    ``openai_chat_tool_return_as_list`` key.
    """

    openai_chat_tool_return_as_list: bool


_OPENAI_BOOL_PROFILE_KEYS: frozenset[str] = frozenset({
    "openai_chat_tool_return_as_list",
    "openai_supports_strict_tool_definition",
    "openai_supports_sampling_settings",
    "openai_supports_tool_choice_required",
    "openai_chat_supports_multiple_system_messages",
    "openai_chat_supports_web_search",
    "openai_chat_supports_file_urls",
    "openai_supports_encrypted_reasoning_content",
    "openai_supports_reasoning",
    "openai_supports_reasoning_effort_none",
    "openai_responses_requires_function_call_status_none",
    "openai_supports_phase",
    "supports_inline_system_prompts",
})
"""Profile keys whose values should be coerced from string to bool."""

_OPENAI_PROFILE_FIELDS: frozenset[str] = frozenset({
    "openai_chat_thinking_field",
    "openai_chat_send_back_thinking_parts",
    "openai_supports_strict_tool_definition",
    "openai_supports_sampling_settings",
    "openai_unsupported_model_settings",
    "openai_supports_tool_choice_required",
    "openai_system_prompt_role",
    "supports_inline_system_prompts",
    "openai_chat_supports_multiple_system_messages",
    "openai_chat_supports_web_search",
    "openai_chat_audio_input_encoding",
    "openai_chat_supports_file_urls",
    "openai_supports_encrypted_reasoning_content",
    "openai_supports_reasoning",
    "openai_supports_reasoning_effort_none",
    "openai_responses_requires_function_call_status_none",
    "openai_supports_phase",
})


def _coerce_profile_value(key: str, value: Any) -> Any:
    """Coerce string values to bool for known boolean profile keys.

    Args:
        key: The profile key name.
        value: The raw value (typically a string from YAML kw_args).

    Returns:
        The coerced value — ``bool`` for known boolean keys, original value otherwise.
    """
    if key in _OPENAI_BOOL_PROFILE_KEYS and isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return value


def _profile_to_dict(profile: ModelProfile) -> dict[str, Any]:
    """Extract non-default OpenAI profile fields into a dict."""
    result: dict[str, Any] = {}
    for field_name in _OPENAI_PROFILE_FIELDS:
        val = getattr(profile, field_name, None)
        if val is not None:
            result[field_name] = val
    return result


class OpenAICompatibleModel(OpenAIChatModel):
    """An ``OpenAIChatModel`` subclass that can emit native list tool return content.

    When the ``openai_chat_tool_return_as_list`` profile flag is ``True`` and a
    ``ToolReturnPart`` has non-empty list content with no multimodal files, the
    tool message ``content`` is emitted as
    ``list[ChatCompletionContentPartTextParam]`` instead of a JSON-serialized
    string. This matches the expectation of chat templates that branch on
    ``m.content is string`` vs list (e.g. GLM-5).

    All other behavior is inherited from :class:`OpenAIChatModel`.
    """

    def __init__(
        self,
        model_name: str,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        provider: Provider[AsyncOpenAI] | None = None,
        tool_return_as_list: str | bool = False,
        profile: ModelProfileSpec | None = None,
        settings: ModelSettings | None = None,
        **profile_overrides: str,
    ) -> None:
        """Initialize an OpenAI-compatible model.

        Args:
            model_name: The name of the model to use.
            base_url: Base URL for the OpenAI-compatible API. Ignored if
                ``provider`` is given.
            api_key: API key for authentication. Ignored if ``provider`` is
                given.
            provider: A pre-built ``Provider[AsyncOpenAI]``. When ``None``, an
                ``OpenAIProvider`` is constructed from ``base_url`` and
                ``api_key``.
            tool_return_as_list: Whether to emit native list tool return
                content. Accepts ``str`` (``"true"``/``"false"``) or ``bool``
                for YAML convenience.
            profile: The model profile spec to use.
            settings: Default model settings for this model instance.
            **profile_overrides: Arbitrary ``openai_*`` prefixed keys merged
                into the profile. Known boolean keys are coerced from
                string to ``bool``.
        """
        # Validate openai_* kwargs before constructing OpenAIProvider,
        # so invalid kwargs raise TypeError before any network/auth setup.
        real_overrides: dict[str, Any] = {}
        for key, value in profile_overrides.items():
            if key.startswith("openai_"):
                coerced = _coerce_profile_value(key, value)
                if key in _OPENAI_PROFILE_FIELDS:
                    real_overrides[key] = coerced
            else:
                msg = (
                    f"Unexpected keyword argument '{key}'. "
                    f"Only 'openai_*' prefixed keys are accepted as profile overrides."
                )
                raise TypeError(msg)

        if provider is None:
            provider = OpenAIProvider(base_url=base_url, api_key=api_key)

        # Coerce tool_return_as_list to bool and store on instance
        self._tool_return_as_list_enabled: bool = (
            tool_return_as_list
            if isinstance(tool_return_as_list, bool)
            else isinstance(tool_return_as_list, str)
            and tool_return_as_list.lower() in ("true", "1", "yes")
        )

        # Build the merged profile spec
        merged_profile = self._build_merged_profile(profile, real_overrides)

        super().__init__(
            model_name=model_name,
            provider=provider,
            profile=merged_profile,
            settings=settings,
        )

    @staticmethod
    def _build_merged_profile(
        profile: ModelProfileSpec | None,
        real_overrides: dict[str, Any],
    ) -> ModelProfileSpec | None:
        """Merge openai_* overrides into the profile spec."""
        if not real_overrides:
            return profile

        if profile is None:
            return OpenAIModelProfile(**real_overrides)

        if isinstance(profile, ModelProfile):
            base_dict = _profile_to_dict(profile)
            base_dict.update(real_overrides)
            return OpenAIModelProfile(**base_dict)

        # Callable profile: wrap to inject overrides post-call
        original_fn = profile

        def _wrapped(model_name: str) -> ModelProfile | None:
            result = original_fn(model_name)
            if result is None:
                return None
            result_dict = _profile_to_dict(result) if isinstance(result, ModelProfile) else {}
            result_dict.update(real_overrides)
            return OpenAIModelProfile(**result_dict)

        return _wrapped

    @property
    def _resolved_profile(self) -> OpenAICompatibleModelProfile:
        """Return the resolved profile as a typed dict for flag access."""
        result: dict[str, Any] = {}
        profile = OpenAIModelProfile.from_profile(self.profile)
        for field_name in (
            "openai_supports_strict_tool_definition",
            "openai_system_prompt_role",
        ):
            val = getattr(profile, field_name, None)
            if val is not None:
                result[field_name] = val
        result["openai_chat_tool_return_as_list"] = self._tool_return_as_list_enabled
        return cast(OpenAICompatibleModelProfile, result)

    @override
    async def _map_user_message(
        self, message: ModelRequest
    ) -> AsyncIterator[chat.ChatCompletionMessageParam]:
        if not self._tool_return_as_list_enabled:
            # Flag disabled: delegate entirely to parent
            async for item in super()._map_user_message(message):
                yield item
            return

        # Flag enabled: duplicate parent logic, replacing ToolReturnPart branch
        file_content: list[Any] = []
        for part in message.parts:
            if isinstance(part, SystemPromptPart):
                system_prompt_role = OpenAIModelProfile.from_profile(
                    self.profile
                ).openai_system_prompt_role
                if system_prompt_role == "developer":
                    yield chat.ChatCompletionDeveloperMessageParam(
                        role="developer", content=part.content
                    )
                elif system_prompt_role == "user":
                    yield chat.ChatCompletionUserMessageParam(role="user", content=part.content)
                else:
                    yield chat.ChatCompletionSystemMessageParam(role="system", content=part.content)
            elif isinstance(part, UserPromptPart):
                yield await self._map_user_prompt(part)
            elif isinstance(part, ToolReturnPart):
                if isinstance(part.content, list) and part.content and not part.files:
                    # Native list content: emit list[ChatCompletionContentPartTextParam]
                    content_parts: list[ChatCompletionContentPartTextParam] = [
                        ChatCompletionContentPartTextParam(type="text", text=item)
                        for item in part.content_items(mode="str")
                        if isinstance(item, str)
                    ]
                    yield chat.ChatCompletionToolMessageParam(
                        role="tool",
                        tool_call_id=_guard_tool_call_id(t=part),
                        content=content_parts,
                    )
                else:
                    # String content, empty list, or files: use parent behavior
                    tool_text, tool_file_content = part.model_response_str_and_user_content()
                    file_content.extend(tool_file_content)
                    yield chat.ChatCompletionToolMessageParam(
                        role="tool",
                        tool_call_id=_guard_tool_call_id(t=part),
                        content=tool_text,
                    )
            elif isinstance(part, RetryPromptPart):
                if part.tool_name is None:
                    yield chat.ChatCompletionUserMessageParam(
                        role="user", content=part.model_response()
                    )
                else:
                    yield chat.ChatCompletionToolMessageParam(
                        role="tool",
                        tool_call_id=_guard_tool_call_id(t=part),
                        content=part.model_response(),
                    )
            else:
                from typing import assert_never

                assert_never(part)
        if file_content:
            yield await self._map_user_prompt(UserPromptPart(content=file_content))
