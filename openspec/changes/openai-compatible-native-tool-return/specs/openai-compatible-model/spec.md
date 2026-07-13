## ADDED Requirements

### Requirement: OpenAICompatibleModel subclass
The system SHALL provide an `OpenAICompatibleModel` class that subclasses `OpenAIChatModel` from pydantic-ai, located at `agentpool/models/openai_compatible.py`. This class SHALL inherit all functionality from `OpenAIChatModel` and override `_map_user_message()` to optionally emit native list content for tool return messages.

#### Scenario: Class inherits from OpenAIChatModel
- **WHEN** `OpenAICompatibleModel` is instantiated
- **THEN** it SHALL be a subclass of `OpenAIChatModel` and accept constructor parameters (`model_name`, `base_url`, `api_key`, `provider`, `tool_return_as_list`, `profile`, `settings`)

#### Scenario: Default behavior matches parent
- **WHEN** `OpenAICompatibleModel` is instantiated without the `openai_chat_tool_return_as_list` profile flag (or with it set to `False`)
- **THEN** `_map_user_message()` SHALL delegate entirely to `super()._map_user_message(message)` — producing identical output to `OpenAIChatModel`, with tool return content JSON-serialized to a string

### Requirement: Custom profile TypedDict for type safety
The system SHALL define an `OpenAICompatibleModelProfile` TypedDict in `agentpool/models/openai_compatible.py` that extends `OpenAIModelProfile` from pydantic-ai with the additional `openai_chat_tool_return_as_list: bool` key. This ensures type-safe access to the profile flag without modifying pydantic-ai upstream.

#### Scenario: Profile flag is type-safe
- **WHEN** `OpenAICompatibleModel` accesses `self._resolved_profile.get('openai_chat_tool_return_as_list', False)`
- **THEN** the access SHALL be type-checked by pyright/mypy without errors, because `OpenAICompatibleModelProfile` declares the key and `_resolved_profile` casts to it (following the `OpenRouterModel._resolved_profile` pattern)

### Requirement: Native list tool return when profile flag enabled
When the `openai_chat_tool_return_as_list` profile flag is `True`, and a `ToolReturnPart` has non-empty list content with no multimodal files, the model SHALL emit `ChatCompletionToolMessageParam.content` as `list[ChatCompletionContentPartTextParam]` (i.e., `[{"type": "text", "text": ...}, ...]`) instead of a JSON-serialized string.

#### Scenario: List content with string items
- **WHEN** `openai_chat_tool_return_as_list` is `True` and a `ToolReturnPart` has `content=["result1", "result2"]` and no files
- **THEN** the tool message `content` SHALL be `[{"type": "text", "text": "result1"}, {"type": "text", "text": "result2"}]`

#### Scenario: List content with non-string items
- **WHEN** `openai_chat_tool_return_as_list` is `True` and a `ToolReturnPart` has `content=[{"key": "value"}, 42]` and no files
- **THEN** each non-string item SHALL be serialized via `content_items(mode='str')` (which uses `tool_return_ta.dump_json(item).decode()`) and wrapped as `{"type": "text", "text": <serialized>}`

#### Scenario: String content unaffected
- **WHEN** `openai_chat_tool_return_as_list` is `True` and a `ToolReturnPart` has `content="plain string"` and no files
- **THEN** the tool message `content` SHALL remain a plain string `"plain string"` (not wrapped in a list)

#### Scenario: Empty list falls back to parent
- **WHEN** `openai_chat_tool_return_as_list` is `True` and a `ToolReturnPart` has `content=[]` (empty list) and no files
- **THEN** the tool message `content` SHALL be `''` (empty string), matching parent behavior via `model_response_str_and_user_content()`

#### Scenario: Multimodal content unaffected
- **WHEN** `openai_chat_tool_return_as_list` is `True` and a `ToolReturnPart` has files (multimodal content)
- **THEN** the parent's behavior SHALL be used (files extracted to user message, text serialized to string)

#### Scenario: Flag disabled falls back to parent
- **WHEN** `openai_chat_tool_return_as_list` is `False` (or unset)
- **THEN** `_map_user_message()` SHALL delegate entirely to `super()._map_user_message(message)` — all `ToolReturnPart` content is JSON-serialized to string

### Requirement: Non-ToolReturnPart messages handled identically to parent
When the profile flag is `True`, all non-`ToolReturnPart` message parts (`SystemPromptPart`, `UserPromptPart`, `RetryPromptPart`) SHALL be handled identically to the parent `OpenAIChatModel._map_user_message()`, including `file_content` accumulation and the final `UserPromptPart` yield for multimodal files.

#### Scenario: UserPromptPart handling
- **WHEN** a `ModelRequest` contains a `UserPromptPart` and the flag is `True`
- **THEN** the part SHALL be mapped identically to the parent's implementation

#### Scenario: SystemPromptPart handling
- **WHEN** a `ModelRequest` contains a `SystemPromptPart` and the flag is `True`
- **THEN** the part SHALL be mapped identically to the parent's implementation

#### Scenario: RetryPromptPart handling
- **WHEN** a `ModelRequest` contains a `RetryPromptPart` and the flag is `True`
- **THEN** the part SHALL be mapped identically to the parent's implementation (using `part.model_response()`, not affected by the list-content override)

#### Scenario: file_content accumulation preserved
- **WHEN** the flag is `True` and a `ToolReturnPart` with files is processed
- **THEN** the `file_content` list SHALL be accumulated across all parts and a final `UserPromptPart` with the file content SHALL be yielded, identical to parent behavior

### Requirement: Manifest configuration via ImportModelConfig with `openai_*` kwarg passthrough
The system SHALL support configuring `OpenAICompatibleModel` through the existing `ImportModelConfig` mechanism in agent manifests. Because `ImportModelConfig.kw_args` is typed as `dict[str, str]`, the `tool_return_as_list` flag SHALL be passed as a string (`"true"`/`"false"`) and coerced to `bool` by the constructor. The constructor SHALL accept `base_url` and `api_key` as convenience parameters and construct an `OpenAIProvider` internally when `provider` is not explicitly given. Additionally, any `openai_*` prefixed key in `kw_args` SHALL be automatically captured via `**profile_overrides` and merged into the profile dict, with known boolean keys coerced from string to `bool`. Non-`openai_*` keys that are not named constructor params SHALL raise `TypeError`.

#### Scenario: ImportModelConfig with tool_return_as_list flag
- **WHEN** a manifest defines a model variant with `type: import`, `model: agentpool.models.openai_compatible.OpenAICompatibleModel`, and `kw_args` containing `tool_return_as_list: "true"`, `base_url`, `api_key`, and `model_name`
- **THEN** the resolved model SHALL be an instance of `OpenAICompatibleModel` with the `openai_chat_tool_return_as_list` profile flag set to `True`

#### Scenario: openai_* profile overrides in kw_args
- **WHEN** a manifest defines `kw_args` with `openai_system_prompt_role: "developer"` and `openai_supports_strict_tool_definition: "false"`
- **THEN** the resolved model's profile SHALL contain `openai_system_prompt_role` set to `"developer"` (string) and `openai_supports_strict_tool_definition` set to `False` (bool, coerced from string `"false"`)

#### Scenario: Non-openai_* unknown kwarg raises TypeError
- **WHEN** a manifest defines `kw_args` with a key that does not start with `openai_` and is not a named constructor parameter (e.g. `foo_bar: "baz"`)
- **THEN** the constructor SHALL raise `TypeError` (standard Python behavior for unexpected kwargs)

#### Scenario: Model variant referencing import config
- **WHEN** an agent references a model variant name that resolves to an `ImportModelConfig` for `OpenAICompatibleModel`
- **THEN** the agent SHALL use the `OpenAICompatibleModel` instance with the configured profile
