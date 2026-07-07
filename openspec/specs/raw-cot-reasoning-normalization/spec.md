# raw-cot-reasoning-normalization Specification

## Purpose
TBD - created by archiving change normalize-raw-cot-reasoning. Update Purpose after archive.
## Requirements
### Requirement: EventMapper normalizes ThinkingPart events with empty content from raw CoT providers

The EventMapper SHALL normalize `PartStartEvent` and `PartDeltaEvent` events containing `ThinkingPart`/`ThinkingPartDelta` when `content`/`content_delta` is empty/None and `provider_details` contains `raw_content`. Normalization SHALL populate `content`/`content_delta` from `provider_details['raw_content']` so that downstream protocol converters receive populated values.

#### Scenario: PartStartEvent with empty content normalized from raw_content

- **WHEN** a `PartStartEvent` with `ThinkingPart(content='')` and `provider_details={'raw_content': ['delta_text']}` is mapped by EventMapper
- **THEN** the returned event SHALL have `ThinkingPart(content='delta_text')`
- **AND** `provider_details` SHALL be preserved unchanged

#### Scenario: PartDeltaEvent with None content_delta normalized from callable provider_details

- **WHEN** a `PartDeltaEvent` with `ThinkingPartDelta(content_delta=None)` and callable `provider_details` is mapped by EventMapper
- **AND** calling the callable with `None` returns `{'raw_content': ['delta_text']}`
- **THEN** the returned event SHALL have `ThinkingPartDelta(content_delta='delta_text')`
- **AND** the callable `provider_details` SHALL be preserved unchanged on the delta

#### Scenario: PartDeltaEvent with None content_delta normalized from dict provider_details

- **WHEN** a `PartDeltaEvent` with `ThinkingPartDelta(content_delta=None)` and dict `provider_details={'raw_content': ['delta_text']}` is mapped by EventMapper
- **THEN** the returned event SHALL have `ThinkingPartDelta(content_delta='delta_text')`

#### Scenario: PartStartEvent with populated content not modified

- **WHEN** a `PartStartEvent` with `ThinkingPart(content='reasoning summary')` is mapped by EventMapper
- **THEN** the returned event SHALL be the original event unchanged
- **AND** no normalization SHALL be applied

#### Scenario: PartDeltaEvent with populated content_delta not modified

- **WHEN** a `PartDeltaEvent` with `ThinkingPartDelta(content_delta='reasoning delta')` is mapped by EventMapper
- **THEN** the returned event SHALL be the original event unchanged

#### Scenario: Events without raw_content in provider_details not modified

- **WHEN** a `PartStartEvent` with `ThinkingPart(content='')` and `provider_details={'other_key': 'value'}` (no `raw_content` key) is mapped
- **THEN** the returned event SHALL be the original event unchanged

#### Scenario: Events with None provider_details not modified

- **WHEN** a `PartDeltaEvent` with `ThinkingPartDelta(content_delta=None, provider_details=None)` is mapped
- **THEN** the returned event SHALL be the original event unchanged

#### Scenario: Empty raw_content list not modified

- **WHEN** a `PartStartEvent` with `ThinkingPart(content='')` and `provider_details={'raw_content': []}` is mapped
- **THEN** the returned event SHALL be the original event unchanged

#### Scenario: content_index greater than zero handled correctly

- **WHEN** a `PartDeltaEvent` with `ThinkingPartDelta(content_delta=None)` and callable `provider_details` created by `_make_raw_content_updater(delta='text', index=2)` is mapped
- **AND** the callable is called with `None`
- **THEN** the returned `raw_content` SHALL be `['', '', 'text']`
- **AND** `content_delta` SHALL be populated with `'text'` (the last element)

### Requirement: Normalization preserves event types and structure

The normalization SHALL NOT change the event type (`PartStartEvent` remains `PartStartEvent`, `PartDeltaEvent` remains `PartDeltaEvent`). The normalization SHALL NOT add new fields, remove existing fields, or change the `index` field. Only `content` (on `ThinkingPart`) or `content_delta` (on `ThinkingPartDelta`) SHALL be modified.

#### Scenario: Normalized PartStartEvent retains type and index

- **WHEN** a `PartStartEvent(index=3, part=ThinkingPart(content=''))` is normalized
- **THEN** the result SHALL be a `PartStartEvent` (not a subclass or wrapper)
- **AND** `index` SHALL be `3`
- **AND** `part` SHALL be a `ThinkingPart`

#### Scenario: Normalized PartDeltaEvent retains type and index

- **WHEN** a `PartDeltaEvent(index=2, delta=ThinkingPartDelta(content_delta=None))` is normalized
- **THEN** the result SHALL be a `PartDeltaEvent`
- **AND** `index` SHALL be `2`
- **AND** `delta` SHALL be a `ThinkingPartDelta`

### Requirement: Normalization is defensive against unexpected callable behavior

When calling a callable `provider_details` with `None` returns a result that does not contain `raw_content` or returns a non-dict, the normalization SHALL silently skip normalization and return the original event unchanged. No exception SHALL propagate.

#### Scenario: Callable returns dict without raw_content

- **WHEN** a `PartDeltaEvent` with callable `provider_details` that returns `{'other': 'value'}` when called with `None` is mapped
- **THEN** the original event SHALL be returned unchanged

#### Scenario: Callable returns non-dict

- **WHEN** a `PartDeltaEvent` with callable `provider_details` that returns `None` or a string when called with `None` is mapped
- **THEN** the original event SHALL be returned unchanged
- **AND** no exception SHALL be raised

### Requirement: Full stream reconstruction produces complete reasoning text

When a sequence of raw CoT streaming events is normalized, concatenating all `content` and `content_delta` values SHALL produce the complete reasoning text that the provider originally sent.

#### Scenario: Multi-delta stream reconstruction

- **WHEN** a raw CoT provider sends deltas `["The user", " asks about", " Python", " testing."]`
- **AND** these produce 1 `PartStartEvent` (with `content=''`) and 3 `PartDeltaEvent` (with `content_delta=None`)
- **AND** all events are normalized by EventMapper
- **THEN** concatenating `PartStartEvent.part.content` + all `PartDeltaEvent.delta.content_delta` SHALL equal `"The user asks about Python testing."`

### Requirement: ACP converter does not substitute newlines for empty thinking deltas

The ACP event converter SHALL NOT replace empty thinking deltas with newline characters. The `delta or "\n"` fallback SHALL be removed or changed to pass `delta` directly, since normalization ensures `delta` is populated for raw CoT providers.

#### Scenario: Empty string thinking delta passed through

- **WHEN** a `ThinkingPartDelta(content_delta='')` reaches the ACP converter
- **THEN** `AgentThoughtChunk.text('')` SHALL be yielded (not `AgentThoughtChunk.text('\n')`)

### Requirement: OpenCode converter preserves empty string thinking deltas

The OpenCode event processor SHALL distinguish between `None` (no delta, skip) and `""` (empty string, process). The guard `if not delta: return` SHALL be changed to `if delta is None: return` in both `_process_thinking_start` and `_process_thinking_delta`.

#### Scenario: None content_delta skipped

- **WHEN** a `ThinkingPartDelta(content_delta=None)` reaches the OpenCode converter
- **THEN** the handler SHALL return early without producing events

#### Scenario: Empty string content_delta processed

- **WHEN** a `ThinkingPartDelta(content_delta='')` reaches the OpenCode converter
- **THEN** the handler SHALL process it normally (not return early)

