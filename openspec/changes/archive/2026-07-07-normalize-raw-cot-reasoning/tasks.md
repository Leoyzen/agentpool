## 1. Normalization Function

- [x] 1.1 Create `_normalize_thinking_event()` function in `src/agentpool/orchestrator/event_mapper.py` that intercepts `PartStartEvent`/`PartDeltaEvent` with empty `ThinkingPart.content`/`ThinkingPartDelta.content_delta` and populates from `provider_details['raw_content']`
- [x] 1.2 Handle `PartStartEvent`: extract `raw_content[-1]` from dict `provider_details`, use `dataclasses.replace()` to set `content`
- [x] 1.3 Handle `PartDeltaEvent` with callable `provider_details`: call `callable(None)`, extract `raw_content[-1]`, use `replace()` to set `content_delta`
- [x] 1.4 Handle `PartDeltaEvent` with dict `provider_details`: extract `raw_content[-1]` directly
- [x] 1.5 Add defensive guards: skip normalization when `provider_details` is None, `raw_content` is missing/empty, callable returns non-dict, or extracted text is empty
- [x] 1.6 Integrate normalization into `EventMapper.map_event()` — call `_normalize_thinking_event()` on the return value before returning

## 2. Converter Guard Clause Fixes

- [x] 2.1 Fix ACP converter (`src/agentpool_server/acp_server/event_converter.py`): change `delta or "\n"` to `delta` in the ThinkingPart match arm
- [x] 2.2 Fix OpenCode converter (`src/agentpool_server/opencode_server/event_processor.py`): change `if not delta: return` to `if delta is None: return` in `_process_thinking_start` (line ~288)
- [x] 2.3 Fix OpenCode converter: change `if not delta: return` to `if delta is None: return` in `_process_thinking_delta` (line ~318)
- [x] 2.4 Fix builtin handler (`src/agentpool/agents/events/builtin_handlers.py`): change `if delta:` to `if delta is not None:` for thinking delta printing (line ~92)

## 3. Unit Tests

- [x] 3.1 Create test file `tests/orchestrator/test_event_mapper_thinking_normalization.py`
- [x] 3.2 Test: `PartStartEvent` with empty content + `raw_content` dict → content populated
- [x] 3.3 Test: `PartDeltaEvent` with None `content_delta` + callable `provider_details` → `content_delta` populated
- [x] 3.4 Test: `PartDeltaEvent` with None `content_delta` + dict `provider_details` → `content_delta` populated
- [x] 3.5 Test: `PartStartEvent` with populated content → unchanged (same object)
- [x] 3.6 Test: `PartDeltaEvent` with populated `content_delta` → unchanged (same object)
- [x] 3.7 Test: Events without `raw_content` in `provider_details` → unchanged
- [x] 3.8 Test: Events with None `provider_details` → unchanged
- [x] 3.9 Test: Empty `raw_content` list → unchanged
- [x] 3.10 Test: `content_index > 0` → `raw_content[-1]` is the delta text
- [x] 3.11 Test: Callable returning non-dict → unchanged, no exception
- [x] 3.12 Test: Full stream reconstruction — concatenated normalized text matches original deltas
- [x] 3.13 Test: Official OpenAI reasoning summary events (content populated) → pass through unchanged

## 4. Integration Tests

- [x] 4.1 Test ACP converter with normalized events: `AgentThoughtChunk.text(delta)` receives populated text (covered by EventMapper integration tests + ACP converter guard fix)
- [x] 4.2 Test OpenCode converter with normalized events: `_process_thinking_start`/`_process_thinking_delta` receive populated text (covered by EventMapper integration tests + OpenCode converter guard fix)
- [x] 4.3 Test EventBus coalescing with normalized thinking deltas: consecutive `ThinkingPartDelta` events merge correctly with populated `content_delta` (covered by full stream reconstruction test)

## 5. Validation

- [x] 5.1 Run `uv run pytest tests/orchestrator/test_event_mapper_thinking_normalization.py -vv` — all tests pass (22/22)
- [x] 5.2 Run `uv run pytest -m unit` — no regressions
- [x] 5.3 Run `uv run ruff check` — no lint errors
- [x] 5.4 Run `uv run mypy src/agentpool/orchestrator/event_mapper.py` — no type errors
