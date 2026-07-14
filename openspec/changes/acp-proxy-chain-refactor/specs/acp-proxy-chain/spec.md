## ADDED Requirements

### Requirement: Conductor SHALL manage proxy chain lifecycle

The `Conductor` class SHALL manage the lifecycle of a proxy chain, including spawning the terminal agent subprocess, initializing proxies via `proxy/initialize`, and routing messages bidirectionally via `proxy/successor`. The Conductor SHALL inherit from `MessageNode[ChatMessage, ChatMessage[str]]` and expose a `_step` property for graph-based execution.

#### Scenario: Conductor initializes proxy chain
- **WHEN** a Conductor is created with a list of proxy configs and a terminal agent config
- **THEN** the Conductor SHALL spawn the terminal agent subprocess
- **AND** SHALL call `proxy/initialize` on each proxy in order from terminal agent toward client
- **AND** SHALL establish `proxy/successor` forwarding between adjacent proxies
- **AND** SHALL return a ready signal when the chain is fully initialized

#### Scenario: Conductor with zero proxies
- **WHEN** a Conductor is created with no proxy configs (empty `proxy_chain` list)
- **THEN** the Conductor SHALL connect directly to the terminal agent
- **AND** SHALL NOT send any `proxy/initialize` or `proxy/successor` messages

#### Scenario: Conductor cleanup on shutdown
- **WHEN** the Conductor is shut down (async context manager exit)
- **THEN** the Conductor SHALL terminate the terminal agent subprocess
- **AND** SHALL clean up all proxy connections in reverse order
- **AND** SHALL ensure no orphaned subprocesses remain

### Requirement: Proxy protocol SHALL define proxy/initialize and proxy/successor

The `Proxy` protocol SHALL define two methods following the ACP proxy chain RFD:
- `proxy_initialize()`: Called during chain setup to signal that a successor exists. Returns proxy capabilities and metadata.
- `proxy_successor(method, params, meta)`: Called to forward a message to the successor (next proxy or terminal agent). The proxy MAY inspect, modify, or block the message before forwarding.

#### Scenario: Proxy receives proxy/initialize
- **WHEN** the Conductor calls `proxy/initialize` on a proxy
- **THEN** the proxy SHALL return its capabilities (which message types it intercepts)
- **AND** SHALL prepare its internal state for chain operation

#### Scenario: Proxy forwards message via proxy/successor
- **WHEN** a proxy receives a `proxy/successor` call with method, params, and meta
- **THEN** the proxy MAY inspect the method and params
- **AND** if the proxy has interception logic for this message type, it SHALL apply the interception
- **AND** SHALL forward the (possibly modified) message to its successor
- **OR** SHALL return a blocking response if the interception denies the message

#### Scenario: Proxy passthrough for unregistered message types
- **WHEN** a proxy receives a `proxy/successor` call for a message type it does not intercept
- **THEN** the proxy SHALL forward the raw message to its successor without deserializing the params
- **AND** SHALL NOT pay any serialization/deserialization cost

### Requirement: ProxySideConnection SHALL wrap proxy wire communication

The `ProxySideConnection` class SHALL wrap a `Connection` instance to provide proxy-specific message handling. It SHALL listen for `proxy/initialize` and `proxy/successor` requests and dispatch them to the `Proxy` implementation. It SHALL be analogous to `AgentSideConnection` and `ClientSideConnection`.

#### Scenario: ProxySideConnection receives proxy/successor
- **WHEN** a `ProxySideConnection` receives a `proxy/successor` JSON-RPC request
- **THEN** it SHALL dispatch the method, params, and meta to the Proxy implementation
- **AND** SHALL return the Proxy's response to the caller

### Requirement: Conductor SHALL detect terminal agents vs proxies

The Conductor SHALL detect whether a component is a terminal agent or a proxy by checking its response to initialization. If the component responds to `initialize` (standard ACP method), it is a terminal agent. If it responds to `proxy/initialize`, it is a proxy.

#### Scenario: Terminal agent detection
- **WHEN** the Conductor initializes the chain and the first component responds to `initialize` (not `proxy/initialize`)
- **THEN** the Conductor SHALL treat it as a terminal agent
- **AND** SHALL NOT send `proxy/successor` to it
- **AND** SHALL send standard ACP methods (`session/prompt`, `session/update`) directly

#### Scenario: Proxy detection
- **WHEN** the Conductor initializes the chain and a component responds to `proxy/initialize`
- **THEN** the Conductor SHALL treat it as a proxy
- **AND** SHALL route subsequent messages through `proxy/successor`

### Requirement: YAML proxy_chain configuration

The system SHALL support a `proxy_chain` section in ACP agent configuration. Each entry SHALL have a `type` field that maps to a registered proxy implementation. When `proxy_chain` is omitted, the Conductor SHALL run with zero proxies.

#### Scenario: Agent with proxy chain
- **WHEN** an ACP agent config includes a `proxy_chain` section with one or more proxy entries
- **THEN** the Conductor SHALL instantiate each proxy in order
- **AND** SHALL initialize the chain with the terminal agent at the end

#### Scenario: Agent without proxy chain
- **WHEN** an ACP agent config does not include a `proxy_chain` section
- **THEN** the Conductor SHALL connect directly to the terminal agent with no proxies

### Requirement: Conductor SHALL use structured concurrency for subprocess management

The Conductor SHALL use anyio task groups for structured concurrency when spawning the terminal agent subprocess and managing proxy connections. Subprocess cleanup SHALL run in a `finally` block to prevent orphaned processes.

#### Scenario: Subprocess crash during operation
- **WHEN** the terminal agent subprocess crashes during operation
- **THEN** the Conductor SHALL detect the crash via the connection's task supervisor
- **AND** SHALL clean up all proxy connections
- **AND** SHALL raise an appropriate error to the caller
