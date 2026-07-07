## Why

When using `OpenAIResponsesModel` with raw CoT providers (vLLM, LM Studio, litellm bridge, gpt-oss via OpenRouter), reasoning content is not displayed to users. pydantic-ai intentionally keeps `ThinkingPart.content` empty for these providers — raw reasoning is stored only in `provider_details['raw_content']` (by design per [pydantic-ai docs](https://pydantic.dev/docs/ai/advanced-features/thinking/#openai-responses)). AgentPool's protocol converters only read `content`/`content_delta`, ignoring `provider_details['raw_content']`, so reasoning is silently dropped across all protocols (ACP, OpenCode, AG-UI, builtin handlers).

## What Changes

- Add a centralized event normalization step in the event pipeline that intercepts `ThinkingPart`/`ThinkingPartDelta` events and populates `content`/`content_delta` from `provider_details['raw_content']` when they are empty
- For `PartStartEvent`: `provider_details` is already a resolved dict — extract `raw_content[-1]` directly
- For `PartDeltaEvent`: `provider_details` may be a callable closure (`_make_raw_content_updater`) — call it with `None` to extract the current delta text without needing accumulated state
- Official OpenAI reasoning summaries (where `content` is already populated) are not modified — normalization only triggers when `content`/`content_delta` is empty/None
- Fix ACP converter's `delta or "\n"` to `delta` (no longer needed after normalization, but removes lossy fallback)
- Fix OpenCode converter's `if not delta: return` to `if delta is None: return` (preserve empty strings, only skip None)

## Capabilities

### New Capabilities

- `raw-cot-reasoning-normalization`: Normalization of ThinkingPart/ThinkingPartDelta events to extract reasoning text from provider_details['raw_content'] when content is empty, ensuring all protocol converters receive populated content

### Modified Capabilities

- `event-coalescing`: EventBus coalescing logic may need to run after normalization — verify ordering and ensure coalescing operates on normalized events

## Impact

- **Code**: `src/agentpool/orchestrator/event_mapper.py` (normalization interception point), `src/agentpool_server/acp_server/event_converter.py` (remove `or "\n"` fallback), `src/agentpool_server/opencode_server/event_processor.py` (fix guard clauses), `src/agentpool/agents/events/builtin_handlers.py` (cosmetic fix)
- **Dependencies**: No new dependencies. Relies on existing pydantic-ai types (`ThinkingPart`, `ThinkingPartDelta`, `ProviderDetailsDelta`)
- **APIs**: No public API changes. Normalization is internal to the event pipeline
- **Compatibility**: No breaking changes. Official OpenAI reasoning summary path is unaffected. Raw CoT providers gain reasoning visibility they previously lacked
- **Testing**: New unit tests for normalization function; regression tests for official OpenAI reasoning path
