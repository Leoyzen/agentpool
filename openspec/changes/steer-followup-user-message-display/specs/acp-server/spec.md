## ADDED Requirements

### Requirement: ACP _meta.delivery extracted at acp_agent.py:prompt() and passed through call chain

`handle_prompt()` does NOT receive `_meta` directly. `_meta` is extracted at `acp_agent.py:prompt()` (line ~698) for trace context but NOT forwarded to `handle_prompt()`. The fix: `acp_agent.py:prompt()` SHALL extract `delivery` from `_meta` and pass it as a `delivery` parameter through `handle_prompt()` → `send_message()` → `_route_message()`.

ACP `acp_agent.py:prompt()` SHALL read `_meta.delivery` from the `PromptRequest` to determine the routing priority. Values: `"steer"` SHALL map to `priority="asap"`, `"followup"` SHALL map to `priority="when_idle"`. When `_meta.delivery` is absent, the default SHALL be `"when_idle"`.

The `handle_prompt()` SHALL pass the generated `message_id` to `send_message()` for deduplication with `UserMessageInsertedEvent`.

#### Scenario: ACP client sends steer via _meta.delivery
- **WHEN** an ACP client sends `session/prompt` with `_meta.delivery="steer"` on a busy session
- **THEN** `acp_agent.py:prompt()` extracts `delivery="steer"` from `_meta`
- **AND** passes `delivery` to `handle_prompt()` → `send_message()` → `_route_message()`
- **AND** `_route_message()` routes the message as steer (priority `"asap"`)

#### Scenario: ACP client sends followup via _meta.delivery
- **WHEN** an ACP client sends `session/prompt` with `_meta.delivery="followup"` on a busy session
- **THEN** `acp_agent.py:prompt()` extracts `delivery="followup"` from `_meta`
- **AND** passes `delivery` to `handle_prompt()` → `send_message()` → `_route_message()`
- **AND** `_route_message()` routes the message as followup (priority `"when_idle"`)

#### Scenario: ACP client without _meta.delivery defaults to followup
- **WHEN** an ACP client sends `session/prompt` without `_meta.delivery` on a busy session
- **THEN** `acp_agent.py:prompt()` defaults to `priority="when_idle"`
- **AND** the message is queued for the next turn

### Requirement: ACPEventConverter accepts protocol_version in constructor

`ACPEventConverter.__init__()` SHALL accept a `protocol_version: int = 1` parameter, passed from the ACP agent instance (which stores the negotiated protocol version at `acp_agent.py:380`). The converter SHALL use `protocol_version` to determine whether to emit `UserMessageChunk` (v1, `protocol_version < 2`) or `UserMessage` (v2, `protocol_version >= 2`).

#### Scenario: ACPEventConverter initialized with v1 protocol
- **WHEN** `ACPEventConverter(protocol_version=1)` is constructed
- **AND** `UserMessageInsertedEvent` arrives
- **THEN** the converter emits `UserMessageChunk` with `TextContentBlock`

#### Scenario: ACPEventConverter initialized with v2 protocol
- **WHEN** `ACPEventConverter(protocol_version=2)` is constructed
- **AND** `UserMessageInsertedEvent` arrives
- **THEN** the converter emits `UserMessage` with `content=[TextContentBlock(...)]`

### Requirement: ACPEventConverter handles UserMessageInsertedEvent

`ACPEventConverter` SHALL handle `UserMessageInsertedEvent` in its `convert()` method. For ACP v1, it SHALL emit `UserMessageChunk` with a `TextContentBlock` containing the event's `content`. For ACP v2, it SHALL emit `UserMessage` (whole-message upsert) with `content=[TextContentBlock(text=content)]`.

The converter SHALL check the shared dedup set before emitting. If the `message_id` is already in the set, it SHALL yield no updates.

The `message_id` from the event SHALL be used as the ACP `messageId` for the `UserMessageChunk` / `UserMessage`.

When `content` is `list[Any]` (multi-modal), the converter SHALL convert each element to the appropriate `ContentBlock` (e.g., `str` → `TextContentBlock`, dict with image → `ImageContentBlock`).

#### Scenario: ACP v1 client receives steer user message
- **WHEN** `UserMessageInsertedEvent(delivery="steer", content="additional info", message_id="msg_789")` arrives at `ACPEventConverter`
- **AND** the protocol version is v1
- **AND** `"msg_789"` is not in the shared dedup set
- **THEN** the converter yields `UserMessageChunk(message_id="msg_789", content=TextContentBlock(text="additional info"))`
- **AND** adds `"msg_789"` to the dedup set

#### Scenario: ACP v2 client receives steer user message
- **WHEN** `UserMessageInsertedEvent(delivery="steer", content="additional info", message_id="msg_789")` arrives at `ACPEventConverter`
- **AND** the protocol version is v2
- **AND** `"msg_789"` is not in the shared dedup set
- **THEN** the converter yields `UserMessage(message_id="msg_789", content=[TextContentBlock(text="additional info")])`
- **AND** adds `"msg_789"` to the dedup set

#### Scenario: ACP dedup skips already-displayed message
- **WHEN** `UserMessageInsertedEvent(message_id="msg_123")` arrives at `ACPEventConverter`
- **AND** `"msg_123"` is already in the shared dedup set (from `handle_prompt()`'s prior `_emit_user_message_chunks()`)
- **THEN** the converter yields no updates
- **AND** does not add a duplicate to the dedup set

### Requirement: _emit_user_message_chunks() generates message_id first and registers in shared dedup set

`_emit_user_message_chunks()` SHALL generate `message_id` FIRST (before emitting chunks), register it in the shared dedup set, emit to client, then pass `message_id` through `send_message(message_id=mid)` → `_route_message(message_id=mid)`. This ensures the `ACPEventConverter` can check the dedup set and skip messages already emitted by the protocol handler.

The shared dedup set SHALL be accessible by BOTH `_emit_user_message_chunks()` (protocol handler path) AND `ACPEventConverter` (EventBus path).

#### Scenario: _emit_user_message_chunks() registers message_id before emitting
- **WHEN** `handle_prompt()` calls `_emit_user_message_chunks()`
- **THEN** `_emit_user_message_chunks()` generates `message_id = str(uuid.uuid4())`
- **AND** registers `message_id` in the shared dedup set
- **AND** emits `UserMessageChunk` to the client
- **AND** passes `message_id` to `send_message()` → `_route_message()`

#### Scenario: ACPEventConverter deduplicates against shared set
- **WHEN** `_route_message()` publishes `UserMessageInsertedEvent(message_id=same_id)` 
- **AND** `ACPEventConverter` receives the event via EventBus
- **THEN** the converter finds `same_id` in the shared dedup set
- **AND** skips emission (already displayed by `_emit_user_message_chunks()`)

### Requirement: UserMessage Pydantic model added to ACP schema

`src/acp/schema/session_updates.py` SHALL define a `UserMessage` Pydantic model corresponding to the ACP v2 `user_message` SessionUpdate variant. Fields: `message_id: str` (required), `content: list[ContentBlock] | None` (optional, patch semantics), `meta: dict[str, Any] | None` (optional).

The model SHALL use `session_update: Literal["user_message"] = "user_message"` as the discriminator field.

The `SessionUpdate` union SHALL include `UserMessage` alongside the existing `UserMessageChunk`.

#### Scenario: UserMessage model serializes to v2 wire format
- **WHEN** `UserMessage(message_id="msg_1", content=[TextContentBlock(text="hello")])` is serialized
- **THEN** the JSON output contains `"sessionUpdate": "user_message"`, `"messageId": "msg_1"`, and `"content": [{"type": "text", "text": "hello"}]`

#### Scenario: UserMessage added to SessionUpdate union
- **WHEN** the `SessionUpdate` union is instantiated with a `UserMessage` instance
- **THEN** the discriminator field `session_update` resolves to `"user_message"`
- **AND** the union dispatch correctly identifies the `UserMessage` variant
