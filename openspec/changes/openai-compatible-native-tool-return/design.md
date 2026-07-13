# Design: OpenAI-Compatible Native Tool Return

## Context

pydantic-ai's `OpenAIChatModel._map_user_message()` (openai.py:1481) handles `ToolReturnPart` by calling `part.model_response_str_and_user_content()`, which returns `tuple[str, list[UserContent]]`. The string is always a JSON-serialized representation of the tool return content. This works for official OpenAI models but is suboptimal for OpenAI-compatible models (GLM-5, vLLM) whose chat templates natively handle list-type content.

### Current Flow

```
Tool returns ["result1", "result2"]
  → ToolReturnPart(content=["result1", "result2"])
  → _map_user_message() calls model_response_str_and_user_content()
  → model_response_str() calls tool_return_ta.dump_json(["result1", "result2"]).decode()
  → content = '["result1", "result2"]'  (JSON string)
  → ChatCompletionToolMessageParam(role='tool', content='["result1", "result2"]')
```

### Desired Flow (with flag enabled)

```
Tool returns ["result1", "result2"]
  → ToolReturnPart(content=["result1", "result2"])
  → _map_user_message() overridden: detect list content, no files
  → content = [{"type": "text", "text": "result1"}, {"type": "text", "text": "result2"}]
  → ChatCompletionToolMessageParam(role='tool', content=[...])
```

## Design Decisions

### Decision 1: Subclass `OpenAIChatModel` (not monkey-patch or fork)

**Choice:** Create `OpenAICompatibleModel(OpenAIChatModel)` in agentpool.

**Rationale:**
- `OpenAIChatModel` already has 3 subclasses (`OllamaModel`, `OpenRouterModel`, `CerebrasModel`) — subclassing is the established pattern.
- `OpenRouterModel` overrides `_map_messages()` with super-call + post-processing, proving internal methods are overridable.
- No pydantic-ai upstream changes required — agentpool controls the subclass.
- Clean separation: the override only affects agents that explicitly use this model class.

**Rejected alternatives:**
- **Monkey-patch `OpenAIChatModel`** — Global side effects, breaks if pydantic-ai changes internals.
- **Fork pydantic-ai** — Maintenance burden, diverges from upstream.
- **Contribute to pydantic-ai upstream** — Issue #3888 is open but not merged; can't wait for upstream.

### Decision 2: Override `_map_user_message()` with full method duplication

**Choice:** Override `_map_user_message()` entirely. When the profile flag is `False` (default), delegate entirely to `super()._map_user_message(message)`. When the flag is `True`, duplicate the parent's method body, replacing only the `ToolReturnPart` branch.

**Rationale:**
- `_map_user_message()` (line 1481) takes a `ModelRequest` and iterates over `message.parts`, yielding `ChatCompletionMessageParam` items. It handles `SystemPromptPart`, `UserPromptPart`, `ToolReturnPart`, and `RetryPromptPart` in a single loop, and also accumulates `file_content` (a `list[UserContent]`) that is yielded as a final `UserPromptPart` when non-empty.
- Because the method processes all parts in a single loop and maintains state (`file_content`) across iterations, it is not possible to delegate individual parts to `super()` — calling `super()._map_user_message(message)` would process ALL parts including `ToolReturnPart`.
- Therefore, when the flag is `True`, the entire method body must be duplicated with only the `ToolReturnPart` branch modified. When the flag is `False`, a simple `super()` delegation avoids any duplication.

**Implementation approach:**
```python
async def _map_user_message(self, message: ModelRequest) -> AsyncIterator[ChatCompletionMessageParam]:
    if not self._resolved_profile.get('openai_chat_tool_return_as_list', False):
        # Flag disabled: delegate entirely to parent
        async for item in super()._map_user_message(message):
            yield item
        return

    # Flag enabled: duplicate parent logic, replacing ToolReturnPart branch
    file_content: list[UserContent] = []
    for part in message.parts:
        if isinstance(part, SystemPromptPart):
            # ... same as parent
        elif isinstance(part, UserPromptPart):
            # ... same as parent
        elif isinstance(part, ToolReturnPart):
            if isinstance(part.content, list) and not part.files and part.content:
                # NEW: construct list[ChatCompletionContentPartTextParam]
                content_parts = []
                for item in part.content_items(mode='str'):
                    if isinstance(item, str):
                        content_parts.append(ChatCompletionContentPartTextParam(type='text', text=item))
                yield chat.ChatCompletionToolMessageParam(
                    role='tool',
                    tool_call_id=_guard_tool_call_id(t=part),
                    content=content_parts,
                )
            else:
                # String content, empty list, or files: use parent behavior
                tool_text, tool_file_content = part.model_response_str_and_user_content()
                file_content.extend(tool_file_content)
                yield chat.ChatCompletionToolMessageParam(
                    role='tool',
                    tool_call_id=_guard_tool_call_id(t=part),
                    content=tool_text,
                )
        elif isinstance(part, RetryPromptPart):
            # ... same as parent
        else:
            assert_never(part)
    if file_content:
        yield await self._map_user_prompt(UserPromptPart(content=file_content))
```

**Key details preserved from parent:**
- `file_content` accumulation across all parts
- Final `UserPromptPart` yield when `file_content` is non-empty
- `_guard_tool_call_id()` for tool call ID extraction
- `assert_never(part)` for exhaustive matching

This is acceptable because:
1. The parent method is stable (well-established API).
2. We only diverge in one branch (`ToolReturnPart` with non-empty list content, no files).
3. `OpenRouterModel` already overrides at a similar level (`_map_messages()`).
4. When flag is `False`, zero code duplication — pure super delegation.

### Decision 3: Custom profile TypedDict for type safety

**Choice:** Define a custom `OpenAICompatibleModelProfile(OpenAIModelProfile)` TypedDict in agentpool that adds the `openai_chat_tool_return_as_list: bool` key.

**Rationale:**
- `OpenAIModelProfile` in pydantic-ai is a `TypedDict(total=False)`. `total=False` means all *declared* keys are optional — it does NOT mean arbitrary keys are accepted. Static type checkers (pyright strict, mypy strict) will flag access to undeclared keys.
- The original design's claim that "TypedDict(total=False) allows custom keys" was incorrect for type checking.
- Defining a custom TypedDict in agentpool is the type-safe approach:

```python
from pydantic_ai.profiles.openai import OpenAIModelProfile

class OpenAICompatibleModelProfile(OpenAIModelProfile, total=False):
    """Profile for OpenAI-compatible models with extended flags."""
    openai_chat_tool_return_as_list: bool
```

- The model class accesses the profile via a `_resolved_profile` property (following the `OpenRouterModel` pattern at `openrouter.py:706-707`), which casts `self.profile` to `OpenAICompatibleModelProfile`:

```python
@property
def _resolved_profile(self) -> OpenAICompatibleModelProfile:
    return cast(OpenAICompatibleModelProfile, self.profile)
```

- This mirrors the parent's own pattern: `OpenAIChatModel.profile` (line 806) uses `cast(OpenAIModelProfile, _profile)`, and `OpenRouterModel._resolved_profile` (line 707) uses `cast(OpenRouterModelProfile, self.profile)`. Using `cast()` here is consistent with the established pydantic-ai pattern for TypedDict profile subclasses.
- At runtime, profile data from YAML (plain dict) is compatible with this TypedDict.

**Alternatives rejected:**
- **Access via `dict(self.profile).get(...)`** — Works but loses type safety; ugly workaround.
- **Override the `profile` property itself** — The parent's `profile` is a `@cached_property` with complex logic (line 796-806); overriding it risks breaking that logic. A separate `_resolved_profile` property is safer.
- **Modify pydantic-ai's `OpenAIModelProfile` upstream** — Out of scope; requires upstream PR.

### Decision 4: Manifest configuration via `ImportModelConfig` with `openai_*` kwarg passthrough

**Choice:** Add explicit constructor parameters (`base_url`, `api_key`, `tool_return_as_list`) plus `**profile_overrides` to capture arbitrary `openai_*` prefixed kwargs from YAML. All `openai_*` keys in `kw_args` are automatically merged into the profile dict with type coercion.

**Rationale:**
- `ImportModelConfig.kw_args` is typed as `dict[str, str]` (verified in `llmling_models_config`), so nested dicts like `profile: {openai_chat_tool_return_as_list: true}` cannot be passed via YAML.
- The parent `OpenAIChatModel.__init__` (line 739) takes `provider: Provider[AsyncOpenAI]`, not `base_url`/`api_key` directly. Adding these as convenience params on the subclass simplifies YAML configuration.
- The `tool_return_as_list` flag is passed as a string (`"true"`/`"false"`) from YAML and coerced to `bool` in the constructor.
- `OpenAIModelProfile` has 15+ `openai_*` fields (e.g. `openai_system_prompt_role`, `openai_supports_strict_tool_definition`, `openai_chat_supports_web_search`, `openai_chat_thinking_field`, etc.). Exposing each as a separate constructor param would be unwieldy. Instead, `**profile_overrides` captures any `openai_*` kwarg and merges it into the profile with automatic type coercion.
- This follows the `OpenRouterModel` pattern (line 687-703) which adds convenience params, but extends it with flexible profile passthrough.

**Type coercion:** Known boolean profile keys have their string values (`"true"`, `"false"`, `"1"`, `"0"`, `"yes"`, `"no"`) coerced to `bool`. Non-boolean keys keep string values. The set of known boolean keys is:

```python
_OPENAI_BOOL_PROFILE_KEYS = frozenset({
    'openai_chat_tool_return_as_list',
    'openai_supports_strict_tool_definition',
    'openai_supports_tool_choice_required',
    'openai_chat_supports_multiple_system_messages',
    'openai_chat_supports_web_search',
    'openai_chat_supports_file_urls',
    'openai_supports_encrypted_reasoning_content',
    'openai_supports_reasoning',
    'openai_supports_reasoning_effort_none',
    'openai_chat_supports_document_input',
    'openai_chat_supports_max_completion_tokens',
    'openai_responses_requires_function_call_status_none',
    'openai_supports_phase',
})
```

**Constructor signature:**
```python
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
):
    if provider is None:
        provider = OpenAIProvider(base_url=base_url, api_key=api_key)
    if isinstance(tool_return_as_list, str):
        tool_return_as_list = tool_return_as_list.lower() in ('true', '1', 'yes')

    # Collect all openai_* overrides (from profile_overrides + tool_return_as_list)
    overrides: dict[str, Any] = {}
    if tool_return_as_list:
        overrides['openai_chat_tool_return_as_list'] = True
    for key, value in profile_overrides.items():
        if key.startswith('openai_'):
            overrides[key] = _coerce_profile_value(key, value)

    # Merge overrides into profile dict
    if overrides:
        if profile is None:
            profile = cast(ModelProfileSpec, overrides)
        elif isinstance(profile, dict):
            profile = {**profile, **overrides}
        else:
            # profile is a callable — wrap to inject overrides post-call
            original_profile = profile
            def _wrapped_profile(base: ModelProfile) -> ModelProfile:
                result = original_profile(base)
                return {**result, **overrides}  # type: ignore[typeddict-item]
            profile = _wrapped_profile
    super().__init__(model_name, provider=provider, profile=profile, settings=settings)
```

**Coercion helper:**
```python
def _coerce_profile_value(key: str, value: str) -> str | bool:
    """Coerce string values to bool for known boolean profile keys."""
    if key in _OPENAI_BOOL_PROFILE_KEYS:
        return value.lower() in ('true', '1', 'yes')
    return value
```

**Note on `provider` shorthand:** The parent `OpenAIChatModel.__init__` accepts `provider` as `OpenAIChatCompatibleProvider | Literal['openai', 'openai-chat', 'gateway'] | Provider[AsyncOpenAI]`. This subclass narrows it to `Provider[AsyncOpenAI] | None` because the primary use case is YAML-driven configuration where `base_url`/`api_key` are passed as strings. Programmatic users who need string-based provider inference should use `OpenAIChatModel` directly.

**Example YAML (with profile overrides):**
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
      # Any openai_* key is auto-merged into the profile:
      openai_system_prompt_role: "developer"
      openai_supports_strict_tool_definition: "false"
      openai_chat_supports_web_search: "true"
      openai_chat_thinking_field: "reasoning"
```

**Non-`openai_*` kwargs:** Keys that don't start with `openai_` and aren't named constructor params will raise `TypeError` (standard Python behavior for unexpected kwargs). This prevents silent typos.

### Decision 5: Content serialization strategy for list items

**Choice:** Use `part.content_items(mode='str')` to get serialized string items, then wrap each in `ChatCompletionContentPartTextParam(type='text', text=item)`.

**Rationale:**
- `content_items(mode='str')` already handles the serialization of non-string items via `tool_return_ta.dump_json(item).decode()`, and passes through string items as-is. This is the same serialization the parent uses, ensuring consistency.
- Each item is wrapped as `{"type": "text", "text": <serialized>}` — the format that `ChatCompletionContentPartTextParam` expects and that GLM-5's chat template handles natively.
- Multimodal content (files) is excluded by the `not part.files` check and the `content_items` method's handling of `MultiModalContent` items (they pass through as non-string, but we filter them via the `isinstance(item, str)` check in the loop since `MultiModalContent` is not a `str`).

### Decision 6: File handling unchanged

**Choice:** When `ToolReturnPart` has files (`part.files` is non-empty), always use parent behavior.

**Rationale:**
- The parent already handles file extraction correctly (files → user message, text → tool message).
- Mixing native list content with file extraction adds complexity without clear benefit.
- File-containing tool returns are rare and already work via the existing path.

### Decision 7: Empty list handling

**Choice:** When `part.content` is an empty list (`[]`), fall back to parent behavior (sends `content=''`).

**Rationale:**
- An empty list has no items to wrap into `ChatCompletionContentPartTextParam` — sending `content=[]` to the OpenAI API may be rejected.
- The parent's `model_response_str()` handles empty lists correctly by returning `''` (via `_unwrap_data()` → `None` → `''`).
- The condition `isinstance(part.content, list) and not part.files and part.content` ensures empty lists fall through to the parent branch.

## Architecture

```
agentpool/src/agentpool/models/
├── __init__.py
├── openai_compatible.py    # NEW: OpenAICompatibleModel(OpenAIChatModel) + OpenAICompatibleModelProfile
└── ...

agentpool/tests/models/
├── __init__.py
├── test_openai_compatible.py  # NEW: unit + integration tests
└── ...
```

### Class diagram

```
OpenAIModelProfile (pydantic-ai, TypedDict)
  └── OpenAICompatibleModelProfile (agentpool, adds openai_chat_tool_return_as_list)

OpenAIChatModel (pydantic-ai)
  └── OpenAICompatibleModel (agentpool)
        - profile type: OpenAICompatibleModelProfile (via _resolved_profile property)
        - Overrides _map_user_message()
        - Reads profile flag via self._resolved_profile.get('openai_chat_tool_return_as_list', False)
        - Constructor: base_url, api_key, tool_return_as_list + **profile_overrides (openai_* passthrough)
        - _OPENAI_BOOL_PROFILE_KEYS: frozenset of known boolean profile keys for type coercion
        - _coerce_profile_value(key, value): string→bool for known bool keys
```

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| pydantic-ai changes `_map_user_message()` signature or internals | Override breaks | Pin pydantic-ai version; add integration test that detects signature changes |
| Model doesn't support list content in tool messages | API error at runtime | Profile flag defaults to `False`; only enable for known-compatible models |
| List items contain complex nested objects | Unexpected serialization | Use `content_items(mode='str')` — same serialization as parent |
| Backward compatibility for existing agents | No impact — opt-in via profile flag | Flag defaults to `False`; when `False`, pure super delegation (zero duplication) |
| `file_content` accumulation lost in override | Multimodal files not sent | Explicitly preserved in duplicated method body; covered by test 3.7 |
| Empty list content causes API error | `content=[]` rejected by API | Empty lists fall back to parent behavior (`content=''`) |
