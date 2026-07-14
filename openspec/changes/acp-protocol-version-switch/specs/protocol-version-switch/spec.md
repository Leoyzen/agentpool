## ADDED Requirements

### Requirement: Protocol version resolution chain

The system SHALL resolve the ACP protocol version using a priority chain: CLI parameter > environment variable > per-agent config > default (v1). The resolved version MUST be available before `initialize()` is called.

#### Scenario: CLI parameter takes precedence

- **WHEN** `agentpool serve-acp --protocol-version 2` is executed with `ACP_PROTOCOL_VERSION` env var set to `1`
- **THEN** the server SHALL use protocol version 2

#### Scenario: Environment variable used when no CLI parameter

- **WHEN** `agentpool serve-acp` is executed with `ACP_PROTOCOL_VERSION=2` env var set
- **THEN** the server SHALL use protocol version 2

#### Scenario: Per-agent config used when no env var

- **WHEN** `ACP_PROTOCOL_VERSION` env var is not set and `BaseACPAgentConfig.protocol_version = 2`
- **THEN** the server SHALL use protocol version 2 for that agent

#### Scenario: Default to v1

- **WHEN** no CLI parameter, no env var, and no per-agent config is provided
- **THEN** the server SHALL use protocol version 1

### Requirement: CLI parameter for protocol version

The `agentpool serve-acp` command SHALL accept a `--protocol-version` parameter accepting values `1` or `2`. An invalid value MUST be rejected with a clear error message before server startup.

#### Scenario: Valid v2 parameter

- **WHEN** `agentpool serve-acp --protocol-version 2` is executed
- **THEN** the server SHALL start in v2 mode and advertise protocol version 2 during initialize

#### Scenario: Invalid version value

- **WHEN** `agentpool serve-acp --protocol-version 3` is executed
- **THEN** the command SHALL exit with a non-zero status and print an error message listing valid values (1, 2)

### Requirement: Environment variable for protocol version

The system SHALL read the `ACP_PROTOCOL_VERSION` environment variable to determine the protocol version when no CLI parameter is provided. The value MUST be parsed as an integer; invalid values SHALL fall back to v1 with a warning log.

#### Scenario: Valid env var

- **WHEN** `ACP_PROTOCOL_VERSION=2` is set and no `--protocol-version` CLI parameter is provided
- **THEN** the server SHALL start in v2 mode

#### Scenario: Invalid env var value

- **WHEN** `ACP_PROTOCOL_VERSION=invalid` is set
- **THEN** the server SHALL log a warning and fall back to protocol version 1

### Requirement: Per-connection version storage

The negotiated protocol version SHALL be stored on `AgentSideConnection` (per-connection), NOT on `AgentPoolACPAgent` (which may be shared across connections). This enables concurrent v1 and v2 clients on the same server instance.

#### Scenario: Concurrent v1 and v2 clients

- **WHEN** two clients connect to the same server instance, one requesting v1 and one requesting v2
- **THEN** each connection SHALL independently negotiate and store its own version without affecting the other

#### Scenario: Version not shared across connections

- **WHEN** a v2 client disconnects and a new v1 client connects to the same server instance
- **THEN** the new client SHALL negotiate v1 independently, regardless of the previous client's version

### Requirement: PROTOCOL_VERSION connected to config system

The `AgentPoolACPAgent.PROTOCOL_VERSION` SHALL be an instance property initialized from `BaseACPAgentConfig.get_protocol_version()` during `__init__`, NOT a hardcoded `ClassVar = 1`. The `initialize()` method SHALL use this instance property for version negotiation.

#### Scenario: Config-driven version

- **WHEN** `BaseACPAgentConfig.protocol_version = 2` is set and `initialize()` is called
- **THEN** the server SHALL negotiate using version 2 as its maximum supported version

#### Scenario: No config falls back to default

- **WHEN** no config, env var, or CLI parameter is provided
- **THEN** `AgentPoolACPAgent.PROTOCOL_VERSION` SHALL default to 1

### Requirement: Version-aware initialize negotiation

During `initialize()`, the server SHALL negotiate the protocol version using `min(client_requested_version, server_configured_version)`. The server MUST store the negotiated version on the per-connection `AgentSideConnection` for the duration of the connection.

#### Scenario: Both support v2

- **WHEN** server is configured for v2 and client requests protocol version 2
- **THEN** the server SHALL respond with protocol version 2 and return v2-format capabilities (scoped `session.*` structure, `info` field)

#### Scenario: Server v2, client v1

- **WHEN** server is configured for v2 and client requests protocol version 1
- **THEN** the server SHALL respond with protocol version 1 and return v1-format capabilities (flat structure, `agent_info` field)

#### Scenario: Server v1, client v2

- **WHEN** server is configured for v1 and client requests protocol version 2
- **THEN** the server SHALL respond with protocol version 1 (server's max) and return v1-format capabilities

### Requirement: Unified v2 prompt lifecycle (non-blocking, unified with turn_complete)

In v2 mode, `session/prompt` SHALL return immediately with an empty `PromptResponse` (no `stop_reason`). The turn completion MUST be communicated via `state_update` notification with `idle` state and `stop_reason`. This path SHALL be unified with the existing `client_supports_turn_complete=True` path — v2 mode implies `turn_complete` behavior, no separate code branch. In v2 mode, `state_update(idle)` SHALL REPLACE `TurnCompleteUpdate` — the converter SHALL suppress `TurnCompleteUpdate` emission when `_negotiated_version == 2` and emit `state_update` instead.

#### Scenario: v2 prompt returns immediately

- **WHEN** a v2 client sends `session/prompt` and the agent begins processing
- **THEN** the server SHALL return an empty `PromptResponse` immediately without waiting for the turn to complete

#### Scenario: v2 turn completion via notification

- **WHEN** the agent finishes processing a prompt in v2 mode
- **THEN** the server SHALL send a `session/update` notification with `state_update` containing `state: "idle"` and `stop_reason`

#### Scenario: v2 client also declaring turn_complete capability

- **WHEN** a v2 client also declares `turn_complete=True` in its capabilities
- **THEN** the server SHALL NOT produce duplicate notifications — v2 mode and `turn_complete` are unified into a single non-blocking path

#### Scenario: v1 prompt still blocks

- **WHEN** a v1 client sends `session/prompt`
- **THEN** the server SHALL block the response until the turn completes and return `PromptResponse(stop_reason=...)` as before

#### Scenario: state_update replaces TurnCompleteUpdate in v2

- **WHEN** the agent finishes processing in v2 mode
- **THEN** the server SHALL send `state_update(idle)` and SHALL NOT send `TurnCompleteUpdate` — `state_update` replaces `TurnCompleteUpdate` in v2 mode

### Requirement: v2 method routing

In v2 mode, the `_agent_handler()` SHALL route v2 method names (`auth/login`, `auth/logout`) to their corresponding handlers. The `AgentMethod` Literal type SHALL be updated to include `auth/login`, `auth/logout`, and the already-implemented `session/set_config_option`.

#### Scenario: v2 auth/login routed

- **WHEN** a v2 client sends `auth/login` method
- **THEN** the server SHALL route it to the authentication handler (same logic as v1 `authenticate`)

#### Scenario: v2 auth/logout routed

- **WHEN** a v2 client sends `auth/logout` method
- **THEN** the server SHALL route it to the logout handler (no-op that clears authentication state) and return a success response

#### Scenario: v1 methods still work in v1 mode

- **WHEN** a v1 client sends `session/set_mode` method
- **THEN** the server SHALL handle it normally as before

### Requirement: Deprecation redirect for removed v1 methods

In v2 mode, when a client sends a removed v1 method (`session/set_mode`, `session/load`, `authenticate`), the server SHALL log a deprecation warning and redirect to the v2 equivalent. The server SHALL NOT return `method_not_found` for these methods.

#### Scenario: session/set_mode redirected to session/set_config_option

- **WHEN** a v2 client sends `session/set_mode` with `mode_id="acceptEdits"`
- **THEN** the server SHALL log a deprecation warning and process it as `session/set_config_option` with the mode mapped to a config option

#### Scenario: authenticate redirected to auth/login

- **WHEN** a v2 client sends `authenticate` method
- **THEN** the server SHALL log a deprecation warning and execute the same authentication logic as `auth/login`

#### Scenario: session/load NOT redirected to session/resume

- **WHEN** a v2 client sends `session/load` method
- **THEN** the server SHALL log a deprecation warning and execute the original `session/load` logic (including history replay), NOT redirect to `session/resume`

### Requirement: v2 state_update notifications

In v2 mode, the `ACPEventConverter` SHALL activate the `V2_EXTENSION` hooks (`_on_state_change`, `_on_out_of_turn_update`) to emit `state_update` notifications when the agent state transitions (idle → running → idle).

#### Scenario: State transition to running

- **WHEN** the agent starts processing a prompt in v2 mode
- **THEN** the server SHALL send a `session/update` notification with `state_update` containing `state: "running"`

#### Scenario: State transition to idle

- **WHEN** the agent finishes processing in v2 mode
- **THEN** the server SHALL send a `session/update` notification with `state_update` containing `state: "idle"` and `stop_reason`

#### Scenario: v1 mode does not send state_update

- **WHEN** the agent processes a prompt in v1 mode
- **THEN** the server SHALL NOT send `state_update` notifications (existing `TurnCompleteUpdate` behavior unchanged)

### Requirement: Session storage protocol_version metadata

The session storage format SHALL include a `protocol_version` field. When `load_session()` or `resume_session()` restores a session, the server SHALL compare the stored version with the current negotiated version and log a warning if they differ.

#### Scenario: Version mismatch on resume

- **WHEN** a session created under v1 is resumed on a server configured for v2
- **THEN** the server SHALL log a warning indicating the version mismatch and proceed using the current negotiated version

#### Scenario: Version match on resume

- **WHEN** a session created under v2 is resumed on a server configured for v2
- **THEN** the server SHALL proceed without warning

### Requirement: v2 alpha compatibility version marker

The system SHALL define `ACP_V2_COMPAT_VERSION = "2.0.0-alpha.0"` in `acp/settings.py`. When v2 mode is enabled, the server SHALL log this version at startup.

#### Scenario: v2 startup logs compat version

- **WHEN** the server starts in v2 mode
- **THEN** the startup log SHALL include `ACP_V2_COMPAT_VERSION = "2.0.0-alpha.0"`

### Requirement: v1 zero-regression guarantee

Enabling v2 support infrastructure SHALL NOT change any v1 behavior. All v2 code paths MUST be gated by `_negotiated_version == 2` condition checks.

#### Scenario: v1 mode unaffected by v2 code

- **WHEN** the server is configured for v1 and processes any ACP method
- **THEN** the behavior SHALL be identical to before this change was implemented

### Requirement: AgentMethod Literal type completeness

The `AgentMethod` Literal type in `schema/messages.py` SHALL include all methods handled by `_agent_handler()`, including `session/set_config_option` (already handled but missing from the type), and new v2 methods `auth/login` and `auth/logout`.

#### Scenario: session/set_config_option in AgentMethod

- **WHEN** a developer inspects the `AgentMethod` Literal type
- **THEN** it SHALL include `"session/set_config_option"` alongside existing methods
