## Context

AgentPool's protocol converters (ACP, OpenCode, AG-UI, builtin handlers) read `ThinkingPart.content` / `ThinkingPartDelta.content_delta` to display reasoning text. On pydantic-ai's main branch, raw CoT providers (vLLM, LM Studio, litellm bridge, gpt-oss via OpenRouter) emit `ResponseReasoningTextDeltaEvent` events that call `handle_thinking_delta()` **without** the `content=` parameter. This means:

- `PartStartEvent(part=ThinkingPart(content='', provider_details={'raw_content': [delta]}))` — content is empty
- `PartDeltaEvent(delta=ThinkingPartDelta(content_delta=None, provider_details=callable))` — content_delta is None

pydantic-ai's author confirmed this is by design: raw reasoning should be accessed via `provider_details['raw_content']`, not `content`. The fix must be in AgentPool.

### Current event pipeline

```
pydantic-ai stream
  → EventMapper.map_event()           ← pydantic-ai events → AgentPool events
    → StreamEventEmitter._emit()       ← publishes to EventBus
      → EventBus.publish()             ← drops PartDeltaEvent where delta is None
        → subscriber drain_and_merge() ← coalesces consecutive same-type deltas
          → _handle_event()            ← protocol converter (ACP/OpenCode/AG-UI)
```

### Key discovery: callable evaluation is simpler than expected

`_make_raw_content_updater(delta, index)` creates a closure that, when called with `None`, returns `{'raw_content': [delta]}`. This means we can extract the current delta text **without tracking accumulated state** — just call `callable(None)` and read `raw_content[-1]`.

For `PartStartEvent`, the parts manager already resolves the callable at creation time (`_parts_manager.py:262`), so `provider_details` is already a dict.

## Goals / Non-Goals

**Goals:**

- Reasoning content from raw CoT providers is visible in all protocol clients (ACP, OpenCode, AG-UI, builtin handlers)
- Official OpenAI reasoning summaries (where `content` is already populated) are not affected
- Single point of fix — all protocol converters benefit automatically
- No new dependencies, no public API changes

**Non-Goals:**

- Modifying pydantic-ai (author confirmed behavior is by design)
- Handling non-OpenAI raw CoT providers (Anthropic, Google — they populate content directly)
- Changing the EventBus coalescing logic (it already works correctly with populated `content_delta`)
- Exposing `provider_details['raw_content']` to end users (normalization populates `content`, converters read `content`)

## Decisions

### Decision 1: Normalize in `EventMapper.map_event()` — not in EventBus or converters

**Choice**: Add normalization logic in `EventMapper.map_event()`, after creating the AgentPool event but before returning it.

**Alternatives considered**:

1. **EventBus level** — normalize in `EventBus.publish()` before sending to subscribers. Rejected: EventBus is a generic transport layer; it shouldn't know about ThinkingPart internals. Also, EventBus spec says "The only preprocessing SHALL be dropping PartDeltaEvent instances where delta is None" — adding normalization would violate this spec.

2. **Per-converter** — normalize in each protocol converter. Rejected: requires 3+ implementations, high maintenance burden, easy to miss a converter.

3. **StreamEventEmitter level** — normalize in `StreamEventEmitter._emit()`. Rejected: StreamEventEmitter is a thin publish wrapper; adding domain logic there mixes concerns.

**Rationale**: `EventMapper` is already the boundary between pydantic-ai and AgentPool event types. It's the natural place to normalize pydantic-ai-specific quirks before they enter the AgentPool event stream. All downstream consumers (EventBus, coalescing, converters) see normalized events.

### Decision 2: Use `dataclasses.replace()` for event mutation

**Choice**: Use `replace(part, content=text)` and `replace(event, part=new_part)` to create modified copies.

**Rationale**: pydantic-ai's `ThinkingPart`, `ThinkingPartDelta`, `PartStartEvent`, and `PartDeltaEvent` are dataclasses. `replace()` is the idiomatic way to create modified copies. It's safe, doesn't mutate the original, and preserves all other fields.

### Decision 3: Call callable with `None` to extract delta text

**Choice**: For `ThinkingPartDelta` with callable `provider_details`, call `provider_details(None)` and extract `raw_content[-1]`.

**Rationale**:
- `_make_raw_content_updater(delta, index)(None)` returns `{'raw_content': [delta]}`
- `raw_content[-1]` is the current delta text
- No side effects: the callable creates a new dict, doesn't modify any shared state
- Works for `content_index > 0`: the callable pads with empty strings up to `index`, so `raw_content[-1]` is always the delta
- Verified by test script (27/27 tests passed)

### Decision 4: Only normalize when content is empty/None

**Choice**: Normalization triggers only when `content == ''` (for PartStartEvent) or `content_delta is None` (for PartDeltaEvent). Events with populated content pass through unchanged.

**Rationale**: Official OpenAI reasoning summaries populate `content`/`content_delta` directly. Normalization must not interfere with this path. The guard condition ensures zero impact on existing behavior.

### Decision 5: Fix converter guard clauses as secondary cleanup

**Choice**: After normalization is in place, fix the ACP converter's `delta or "\n"` → `delta` and OpenCode converter's `if not delta: return` → `if delta is None: return`.

**Rationale**: With normalization, `content`/`content_delta` will always be populated for raw CoT providers. But the converter guards should still be correct for robustness:
- `delta or "\n"` is lossy (replaces empty string with newline)
- `if not delta:` is overly broad (treats `""` same as `None`, contradicting its own comment about preserving whitespace)

## Risks / Trade-offs

**[Risk] `_make_raw_content_updater` implementation changes in future pydantic-ai versions** → The callable's behavior of returning `{'raw_content': [delta]}` when called with `None` is an implementation detail, not a public API. If pydantic-ai changes the closure, normalization breaks silently.

**Mitigation**: Add a defensive check — if `callable(None)` doesn't return a dict with `raw_content`, skip normalization and return the original event. Add a test that asserts the callable behavior to detect changes early.

**[Risk] Non-raw-CoT providers that legitimately have empty content** → If a provider sends `ThinkingPart(content='')` intentionally (not raw CoT), normalization would incorrectly populate content from `provider_details`.

**Mitigation**: Only normalize when `provider_details` contains `raw_content`. Official OpenAI reasoning summaries either populate `content` or don't have `raw_content` in `provider_details`. The guard `content == '' and provider_details.get('raw_content')` is specific enough.

**[Trade-off] Normalization adds a small overhead per thinking event** → Each `PartStartEvent`/`PartDeltaEvent` with ThinkingPart goes through a match statement and potential `replace()` call.

**Assessment**: Negligible. Thinking events are infrequent compared to text deltas. The match statement is O(1). `replace()` creates a shallow copy — cheap for dataclasses.
