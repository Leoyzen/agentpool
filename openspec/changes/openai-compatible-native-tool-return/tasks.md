# Tasks: OpenAI-Compatible Native Tool Return

## 1. Create `OpenAICompatibleModel` class and profile

- [ ] 1.1 Create `packages/agentpool/src/agentpool/models/openai_compatible.py`
- [ ] 1.2 Define `OpenAICompatibleModelProfile(OpenAIModelProfile)` TypedDict with `openai_chat_tool_return_as_list: bool` key
- [ ] 1.3 Define `OpenAICompatibleModel(OpenAIChatModel)` with `@dataclass(init=False)` and custom `__init__` accepting `model_name`, `base_url`, `api_key`, `provider` (`Provider[AsyncOpenAI] | None`, narrower than parent's string-literal union — documented as intentional for YAML-driven config), `tool_return_as_list` (str|bool, coerced to bool), `profile` (`ModelProfileSpec | None`), `settings`, and `**profile_overrides: str` for arbitrary `openai_*` profile overrides; construct `OpenAIProvider` from `base_url`/`api_key` when `provider` is None; merge `tool_return_as_list` + `openai_*` overrides into profile — handle three cases: `profile is None` → create new dict with `cast(ModelProfileSpec, {...})`; `isinstance(profile, dict)` → spread-merge `{**profile, **overrides}`; callable profile → wrap in lambda that injects overrides post-call
- [ ] 1.4 Define `_OPENAI_BOOL_PROFILE_KEYS` frozenset and `_coerce_profile_value(key, value)` helper that coerces string → bool for known boolean profile keys, returns string for others
- [ ] 1.5 Add `_resolved_profile` property returning `cast(OpenAICompatibleModelProfile, self.profile)` (following `OpenRouterModel._resolved_profile` pattern)
- [ ] 1.6 Override `_map_user_message()`:
  - [ ] 1.6.1 When flag is `False` (default): delegate entirely to `super()._map_user_message(message)` — zero code duplication
  - [ ] 1.6.2 When flag is `True`: duplicate the parent's method body, replacing only the `ToolReturnPart` branch:
    - For `ToolReturnPart` with non-empty list content (`isinstance(part.content, list) and part.content`) and no files (`not part.files`): use `part.content_items(mode='str')` to get serialized items, wrap each string item as `ChatCompletionContentPartTextParam(type='text', text=item)`, yield `ChatCompletionToolMessageParam` with `content=list[...]`
    - For `ToolReturnPart` with string content, empty list, or files: use parent behavior (`model_response_str_and_user_content()`)
    - For all other part types (`SystemPromptPart`, `UserPromptPart`, `RetryPromptPart`): identical to parent implementation
    - Preserve `file_content` accumulation and final `UserPromptPart` yield
    - End with `assert_never(part)` for exhaustive matching
- [ ] 1.7 Export `OpenAICompatibleModel` and `OpenAICompatibleModelProfile` from `packages/agentpool/src/agentpool/models/__init__.py`

## 2. Tests

- [ ] 2.1 Create `packages/agentpool/tests/models/test_openai_compatible.py`
- [ ] 2.2 Unit test: `OpenAICompatibleModel` is a subclass of `OpenAIChatModel`
- [ ] 2.3 Unit test: Default behavior (flag `False`/unset) delegates to super — tool return content is JSON string
- [ ] 2.4 Unit test: Flag `True` with list string items → `content` is `list[ChatCompletionContentPartTextParam]`
- [ ] 2.5 Unit test: Flag `True` with list non-string items → each item JSON-serialized and wrapped in `{"type": "text", "text": ...}`
- [ ] 2.6 Unit test: Flag `True` with string content → `content` remains plain string (not list-wrapped)
- [ ] 2.7 Unit test: Flag `True` with empty list `[]` → `content` is `''` (falls back to parent)
- [ ] 2.8 Unit test: Flag `True` with multimodal content (files) → delegates to parent behavior (file extraction + user message)
- [ ] 2.9 Unit test: Flag `True` with mixed message (UserPromptPart + ToolReturnPart + RetryPromptPart) → only ToolReturnPart with list content is modified, others match parent output
- [ ] 2.10 Unit test: `file_content` accumulation preserved when flag is `True` and ToolReturnPart has files
- [ ] 2.11 Integration test: `ImportModelConfig` resolves `OpenAICompatibleModel` with `tool_return_as_list: "true"` from YAML (string coercion to bool)
- [ ] 2.12 Integration test: `openai_*` profile overrides in `kw_args` are correctly merged into profile (e.g. `openai_system_prompt_role: "developer"`, `openai_supports_strict_tool_definition: "false"`)
- [ ] 2.13 Integration test: Non-`openai_*` unknown kwarg raises `TypeError`
- [ ] 2.14 Integration test: End-to-end agent run with `TestModel` or mock verifying the tool message content shape

## 3. Documentation

- [ ] 3.1 Add module docstring to `openai_compatible.py` explaining purpose and usage
- [ ] 3.2 Add example YAML manifest snippet in docstring showing `ImportModelConfig` usage with `tool_return_as_list: "true"`, `base_url`, and `api_key`
- [ ] 3.3 Update `packages/agentpool/src/agentpool/models/__init__.py` `__all__` if applicable
