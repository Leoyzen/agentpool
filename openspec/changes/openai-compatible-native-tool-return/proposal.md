# Proposal: OpenAI-Compatible Native Tool Return

## Summary

Subclass `OpenAIChatModel` in agentpool to override tool return handling for OpenAI-compatible models (e.g., GLM-5, vLLM-hosted models) whose chat templates natively render list-type tool message content. Currently pydantic-ai always serializes list-type tool return values to JSON strings, causing unnecessary escape characters and loss of structural semantics in models that support native list content.

## Motivation

When a tool returns a `list` value (e.g., `["result1", "result2"]`), pydantic-ai's `OpenAIChatModel` serializes it to a JSON string `'["result1", "result2"]'` via `tool_return_ta.dump_json(value).decode()`. This string is then placed as the `content` field of a `ChatCompletionToolMessageParam`.

The OpenAI SDK's `ChatCompletionToolMessageParam.content` type actually supports `str | Iterable[ChatCompletionContentPartTextParam]`, but pydantic-ai only uses the `str` branch.

Models like GLM-5 have chat templates that explicitly branch on `m.content is string` vs list:

```jinja
{%- if m.content is string -%}
    {{- '<|tool_return|>' }} {{- m.content }} {{- '<|/tool_return|>' }}
{%- else -%}
    {% for tr in m.content %}
    <|tool_return|>{{ tr.output if tr.output is defined else tr }}<|/tool_return|>
    {% endfor -%}
{% endif -%}
```

When pydantic-ai sends `'["result1", "result2"]'` (a string), the template renders a single `<|tool_return|>` block containing escaped JSON text. If the content were a native list, the template would iterate and create multiple `<|tool_return|>` blocks — the intended behavior for multi-item tool results.

### Problems Caused

1. **Unnecessary escaping** — List data like `[{"name": "Alice"}, {"name": "Bob"}]` becomes `'[{"name": "Alice"}, {"name": "Bob"}]'` — the model sees literal JSON with quotes/brackets as text tokens rather than structured content.
2. **Chat template mismatch** — Models with list-aware templates (GLM-5, some vLLM deployments) cannot exercise their native multi-block rendering path.
3. **No escape hatch** — There is no way to configure pydantic-ai to send native list content for OpenAI-compatible endpoints. Issue [#3888](https://github.com/pydantic/pydantic-ai/issues/3888) proposes a `model_response_str` protocol but is still open.

## Proposal

Create an `OpenAICompatibleModel` subclass of `OpenAIChatModel` in agentpool that:

1. **Overrides `_map_user_message()`** to intercept `ToolReturnPart` handling — when the tool return content is a list with no multimodal files, construct `list[ChatCompletionContentPartTextParam]` (i.e., `[{"type": "text", "text": item}, ...]`) instead of a JSON-serialized string.
2. **Uses a profile flag** (`openai_chat_tool_return_as_list: bool`) to control whether native list content is emitted, defaulting to `False` for backward compatibility.
3. **Is configurable via manifest YAML** using `ImportModelConfig` or a dedicated config type, allowing agents to opt into this behavior per-model.

### Non-Goals

- Modifying pydantic-ai upstream (this is an agentpool-level override)
- Supporting the Responses API (which already handles list content for multimodal returns)
- Changing behavior for non-OpenAI providers (Anthropic, Google, etc.)
- Auto-detecting whether a model supports list tool content (explicit configuration only)

## References

- pydantic-ai Issue #3888: https://github.com/pydantic/pydantic-ai/issues/3888
- pydantic-ai Issue #2034: https://github.com/pydantic/pydantic-ai/issues/2034
- pydantic-ai PR #3826: https://github.com/pydantic/pydantic-ai/pull/3826
- agentpool Issue #112: https://github.com/Leoyzen/agentpool/issues/112
- GLM-5 chat template: https://www.modelscope.cn/models/ZhipuAI/GLM-5/resolve/master/chat_template.jinja
