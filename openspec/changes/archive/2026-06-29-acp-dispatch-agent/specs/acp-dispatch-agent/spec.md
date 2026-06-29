## ADDED Requirements

### Requirement: DispatchAgent SHALL route based on protocolVersion in initialize

DispatchAgent SHALL inspect the `protocolVersion` field in the `initialize` request and create the corresponding v1 or v2 agent as its internal delegate. Version 1 requests SHALL create `AgentPoolACPAgent`; version >= 2 requests SHALL create `AgentPoolACPAgentV2`.

#### Scenario: v1 client gets v1 delegate

- **WHEN** DispatchAgent receives `initialize` with `protocolVersion=1`
- **THEN** it SHALL create `AgentPoolACPAgent` as delegate and return its `initialize` response with `protocolVersion=1`

#### Scenario: v2 client gets v2 delegate

- **WHEN** DispatchAgent receives `initialize` with `protocolVersion=2`
- **THEN** it SHALL create `AgentPoolACPAgentV2` as delegate and return its `initialize` response with `protocolVersion=2`

#### Scenario: Unsupported version raises error

- **WHEN** DispatchAgent receives `initialize` with `protocolVersion=0`
- **THEN** it SHALL raise `RequestError` with code `-32602`

### Requirement: DispatchAgent SHALL delegate all methods after initialize

After `initialize` determines the delegate, all subsequent method calls (`new_session`, `prompt`, `cancel`, `close_session`, `load_session`, `list_sessions`, `fork_session`, `resume_session`, `set_session_config_option`, `ext_method`, `ext_notification`, `close`) SHALL be forwarded to the delegate agent.

#### Scenario: Prompt delegated to v1 agent

- **WHEN** DispatchAgent was initialized with v1 and receives `session/prompt`
- **THEN** it SHALL call `delegate.prompt(params)` and return the v1 `PromptResponse` with `stopReason`

#### Scenario: Prompt delegated to v2 agent

- **WHEN** DispatchAgent was initialized with v2 and receives `session/prompt`
- **THEN** it SHALL call `delegate.prompt(params)` and return the v2 `PromptResponse` (empty result, immediate return)

### Requirement: DispatchAgent SHALL respond to both v1 and v2 method names

DispatchAgent SHALL expose both v1 method names (`authenticate`, `logout`, `set_session_mode`) and v2 method names (`auth_login`, `auth_logout`) so that ACP library's `hasattr`-based dispatch finds the method regardless of client version.

#### Scenario: v1 client calls authenticate

- **WHEN** v1 client calls `authenticate` method
- **THEN** DispatchAgent SHALL delegate to v1 agent's `authenticate` method

#### Scenario: v2 client calls auth/login

- **WHEN** v2 client calls `auth/login` method
- **THEN** DispatchAgent SHALL delegate to v2 agent's `auth_login` method

### Requirement: DispatchAgent SHALL use __getattr__ for dynamic delegation fallback

DispatchAgent SHALL implement `__getattr__` to forward any unrecognized attribute access to the delegate agent, ensuring no method is missed by ACP library's `hasattr` dispatch.

#### Scenario: Unknown method forwarded to delegate

- **WHEN** ACP library calls `hasattr(agent, "some_method")` and DispatchAgent doesn't explicitly define it
- **THEN** `__getattr__` SHALL forward to `self._delegate`, returning `True` if delegate has the method

### Requirement: DispatchAgent SHALL degrade v2 to v1 when SessionPool unavailable

When `protocolVersion >= 2` is requested but `pool.manifest.acp.use_session_pool` is `False`, DispatchAgent SHALL create v1 agent as delegate but return `protocolVersion=2` in the initialize response, indicating v2 protocol version with v1-compatible behavior.

#### Scenario: v2 requested but SessionPool disabled

- **WHEN** client sends `initialize` with `protocolVersion=2` and pool has `use_session_pool=False`
- **THEN** DispatchAgent SHALL create `AgentPoolACPAgent` (v1) as delegate
- **AND** return `protocolVersion=2` in the response
- **AND** include `_meta.fallback=true` in the response

#### Scenario: v2 requested with SessionPool enabled

- **WHEN** client sends `initialize` with `protocolVersion=2` and pool has `use_session_pool=True`
- **THEN** DispatchAgent SHALL create `AgentPoolACPAgentV2` as delegate
- **AND** return `protocolVersion=2` in the response

### Requirement: server.py SHALL use DispatchAgent as the agent factory

`ACPServer._start_async()` SHALL create `DispatchAgent` via `functools.partial` instead of directly creating `AgentPoolACPAgent`. The temporary version-routing comments SHALL be removed.

#### Scenario: Server creates DispatchAgent

- **WHEN** ACP server starts and accepts a connection
- **THEN** the factory SHALL create a `DispatchAgent` instance, not a direct `AgentPoolACPAgent`
