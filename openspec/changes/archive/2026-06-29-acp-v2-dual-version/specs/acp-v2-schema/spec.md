## ADDED Requirements

### Requirement: v2 SessionUpdate types SHALL include whole-message upserts

v2 schema SHALL define `user_message`, `agent_message`, and `agent_thought` session update variants as whole-message upserts keyed by `messageId`. Each variant SHALL have required `messageId` and optional `content` (array of ContentBlock) and `_meta` fields. `content` replaces the entire content array when present; omission leaves content unchanged; `null` or `[]` clears content.

#### Scenario: Whole-message upsert replaces accumulated chunks

- **WHEN** agent sends `agent_message_chunk` with `messageId=m1` content `[{text:"A"}]`, then `agent_message_chunk` with `messageId=m1` content `[{text:"B"}]`, then `agent_message` with `messageId=m1` content `[{text:"C"}]`
- **THEN** final message content SHALL be `[{text:"C"}]` (replacement, not append)

#### Scenario: Chunks append after whole-message upsert

- **WHEN** agent sends `agent_message` with `messageId=m1` content `[{text:"A"}]`, then `agent_message_chunk` with `messageId=m1` content `[{text:"B"}]`
- **THEN** final message content SHALL be `[{text:"A"}, {text:"B"}]` (append after upsert baseline)

#### Scenario: Meta-only update preserves content

- **WHEN** agent sends `agent_message` with `messageId=m1` and `_meta={source:"replay"}` but no `content` field
- **THEN** existing content for `messageId=m1` SHALL remain unchanged

### Requirement: v2 SHALL unify tool_call and tool_call_update into single upsert

v2 schema SHALL remove the `tool_call` session update variant and use `tool_call_update` as the single tool-call notification. `tool_call_update` SHALL be an upsert keyed by `toolCallId`. Fields `title`, `kind`, `status`, `content`, `locations`, `rawInput`, `rawOutput` SHALL be three-state patch fields: omitted = unchanged, `null` = clear, concrete value = replace.

#### Scenario: First tool_call_update creates a new tool call

- **WHEN** agent sends `tool_call_update` with `toolCallId=tc1`, `title="Reading file"`, `kind="read"`, `status="pending"` for a previously unseen `toolCallId`
- **THEN** client SHALL create a new tool call display with those fields

#### Scenario: Omitted field preserves previous value

- **WHEN** agent sends `tool_call_update` with `toolCallId=tc1` and only `status="in_progress"` (title/kind omitted)
- **THEN** client SHALL keep previous title and kind, only update status

#### Scenario: Null field explicitly clears value

- **WHEN** agent sends `tool_call_update` with `toolCallId=tc1` and `rawOutput=null`
- **THEN** client SHALL clear the rawOutput field for that tool call

### Requirement: v2 SHALL support tool_call_content_chunk for streaming

v2 schema SHALL define `tool_call_content_chunk` session update with required `toolCallId` and required single `content` item. Clients SHALL append the chunk's content item to the current content for that `toolCallId`.

#### Scenario: Content chunk appends to tool call

- **WHEN** tool call `tc1` has content `[{text:"line1"}]` and agent sends `tool_call_content_chunk` with `toolCallId=tc1` content `{text:"line2"}`
- **THEN** tool call content SHALL become `[{text:"line1"}, {text:"line2"}]`

#### Scenario: tool_call_update content replaces accumulated chunks

- **WHEN** chunks accumulated content `[{text:"A"},{text:"B"}]` for `tc1` and agent sends `tool_call_update` with `toolCallId=tc1` content `[{text:"C"}]`
- **THEN** tool call content SHALL become `[{text:"C"}]` (replacement)

### Requirement: v2 SHALL define state_update notification

v2 schema SHALL define `state_update` session update with required `state` field (`"running"` | `"idle"` | `"requires_action"`) and optional `stopReason` field (only on `idle` state).

#### Scenario: Running state signals turn start

- **WHEN** agent begins processing a turn
- **THEN** agent SHALL send `state_update` with `state="running"`

#### Scenario: Idle state with stopReason signals turn end

- **WHEN** agent finishes a turn normally
- **THEN** agent SHALL send `state_update` with `state="idle"` and `stopReason="end_turn"`

#### Scenario: Requires_action signals user input needed

- **WHEN** agent needs user permission or elicitation to continue
- **THEN** agent SHALL send `state_update` with `state="requires_action"`

### Requirement: v2 SHALL define plan_update with tagged content

v2 schema SHALL replace v1 `plan` session update with `plan_update`. `plan_update` SHALL carry a `plan` object with required `type` discriminator (stable: `"items"`), required `id` field, and `entries` array. v1 `sessionUpdate: "plan"` SHALL be rejected in v2.

#### Scenario: Item-based plan update

- **WHEN** agent sends `plan_update` with `plan={type:"items", id:"plan-1", entries:[{content:"Step 1", priority:"high", status:"pending"}]}`
- **THEN** client SHALL display the plan with the given entries and plan ID

### Requirement: v2 SHALL unify capabilities field

v2 initialize request and response SHALL use a single `capabilities` field replacing v1's separate `clientCapabilities` and `agentCapabilities`. Support markers SHALL be objects (`{}` = supported, omitted/null = unsupported) rather than booleans. Session-scoped capabilities (`prompt`, `mcp`, `load`) SHALL be nested under `session`.

#### Scenario: Client declares capabilities in v2

- **WHEN** v2 client sends `initialize` with `capabilities={session:{prompt:{}}, auth:{}}`
- **THEN** agent SHALL recognize session prompt and auth as supported capabilities

#### Scenario: Agent declares capabilities in v2

- **WHEN** v2 agent responds to `initialize` with `capabilities={session:{load:{}, mcp:{stdio:{}, http:{}}}}`
- **THEN** client SHALL recognize session load and MCP stdio/http as supported

### Requirement: v2 SHALL use role-agnostic info field

v2 initialize request and response SHALL use a single `info` field (with `name`, `title`, `version`) replacing v1's `clientInfo` and `agentInfo`.

#### Scenario: Client provides info in v2 initialize

- **WHEN** v2 client sends `initialize` with `info={name:"zed", title:"Zed", version:"1.0.0"}`
- **THEN** agent SHALL accept the implementation info without role-specific field names

### Requirement: v2 SHALL group authentication methods

v2 SHALL rename `authenticate` to `auth/login` and `logout` to `auth/logout`. `auth/logout` SHALL be required for v2 agents (no capability marker needed). Generated request/response type names SHALL follow `LoginAuthRequest`/`LoginAuthResponse` and `LogoutAuthRequest`/`LogoutAuthResponse` naming.

#### Scenario: Login via auth/login

- **WHEN** v2 client calls `auth/login` with `methodId="oauth"`
- **THEN** agent SHALL process authentication and return `LoginAuthResponse`

#### Scenario: Logout via auth/logout is always available

- **WHEN** v2 client calls `auth/logout`
- **THEN** agent SHALL process logout without requiring a capability marker check

### Requirement: v2 SHALL require messageId on streamed chunks

v2 `user_message_chunk`, `agent_message_chunk`, and `agent_thought_chunk` SHALL require `messageId` field (non-optional).

#### Scenario: Chunk without messageId is rejected

- **WHEN** v2 agent attempts to send `agent_message_chunk` without `messageId`
- **THEN** schema validation SHALL fail

### Requirement: v2 SHALL remove session modes API

v2 schema SHALL remove `session/set_mode` method, `current_mode_update` notification, `SessionMode`/`SessionModeState` types, and `modes` field from session responses. Mode-like state SHALL be represented via Session Config Options.

#### Scenario: v2 session response has no modes field

- **WHEN** v2 agent responds to `session/new`
- **THEN** response SHALL NOT contain a `modes` field

### Requirement: v2 SHALL remove client filesystem and terminal surface

v2 schema SHALL remove `clientCapabilities.fs`, `clientCapabilities.terminal`, `fs/read_text_file`, `fs/write_text_file`, all `terminal/*` methods, and terminal tool-call content. `clientCapabilities.auth.terminal` SHALL remain.

#### Scenario: v2 initialize has no fs or terminal capabilities

- **WHEN** v2 client sends `initialize`
- **THEN** capabilities SHALL NOT include `fs` or top-level `terminal` fields
