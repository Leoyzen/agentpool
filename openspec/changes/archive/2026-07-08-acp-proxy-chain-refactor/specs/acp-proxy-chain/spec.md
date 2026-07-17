## ADDED Requirements

### Requirement: Conductor SHALL manage proxy chain lifecycle

The `Conductor` class SHALL manage the lifecycle of a proxy chain, including spawning the terminal agent subprocess, initializing proxies via `proxy/initialize`, and routing messages bidirectionally via `proxy/successor`. The Conductor SHALL inherit from `MessageNode[ChatMessage, ChatMessage[str]]` and expose a `_step` property for graph-based execution.

#### Scenario: Conductor initializes proxy chain
- **WHEN** a Conductor is created with a list of proxy configs and a terminal agent config
- **THEN** the Conductor SHALL spawn the terminal agent subprocess
- **AND** SHALL call `proxy/initialize` on each proxy in order from client toward terminal agent (P1 first, P2 next, ..., terminal agent last)
- **AND** SHALL establish `proxy/successor` forwarding between adjacent proxies
- **AND** SHALL send `initialize` (standard ACP method) to the terminal agent (last component)
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
- `proxy_initialize()`: Called during chain setup to signal that a successor exists. Returns proxy capabilities including `intercepted_methods` list (message types the proxy intercepts).
- `proxy_successor(method, params, meta)`: Called to forward a message to the successor (next proxy or terminal agent). The proxy MAY inspect, modify, or block the message before forwarding.

#### Scenario: Proxy receives proxy/initialize
- **WHEN** the Conductor calls `proxy/initialize` on a proxy
- **THEN** the proxy SHALL return its capabilities including `intercepted_methods` (list of ACP method names it intercepts)
- **AND** SHALL prepare its internal state for chain operation

#### Scenario: Proxy forwards message via proxy/successor
- **WHEN** a proxy receives a `proxy/successor` call with method, params, and meta
- **THEN** the proxy MAY inspect the method and params
- **AND** if the proxy has interception logic for this message type (declared in `intercepted_methods`), it SHALL apply the interception
- **AND** SHALL forward the (possibly modified) message to its successor
- **OR** SHALL return a blocking response if the interception denies the message

#### Scenario: Proxy passthrough for unregistered message types
- **WHEN** a proxy receives a `proxy/successor` call for a message type not in its `intercepted_methods` list
- **THEN** the proxy SHALL forward the raw message to its successor without deserializing the params
- **AND** SHALL NOT pay any serialization/deserialization cost

### Requirement: ProxySideConnection SHALL wrap proxy wire communication

The `ProxySideConnection` class SHALL wrap a `Connection` instance to provide proxy-specific message handling. It SHALL listen for `proxy/initialize` and `proxy/successor` requests and dispatch them to the `Proxy` implementation. It SHALL be analogous to `AgentSideConnection` and `ClientSideConnection`.

#### Scenario: ProxySideConnection receives proxy/successor
- **WHEN** a `ProxySideConnection` receives a `proxy/successor` JSON-RPC request
- **THEN** it SHALL dispatch the method, params, and meta to the Proxy implementation
- **AND** SHALL return the Proxy's response to the caller

### Requirement: Conductor SHALL determine terminal vs proxy by chain position

The Conductor SHALL determine which components are proxies vs terminal agent based on **chain position** from configuration. The last component in the chain is the terminal agent; all others are proxies. The Conductor sends `proxy/initialize` to all proxy components and `initialize` to the terminal agent (last component). The Conductor does NOT detect terminal vs proxy status from responses — it knows from configuration.

#### Scenario: Terminal agent receives initialize
- **WHEN** the Conductor initializes the chain and the component is the last in the chain (terminal agent)
- **THEN** the Conductor SHALL send `initialize` (standard ACP method)
- **AND** SHALL NOT send `proxy/initialize` or `proxy/successor` to it
- **AND** SHALL send standard ACP methods (`session/prompt`, `session/update`) directly

#### Scenario: Proxy receives proxy/initialize
- **WHEN** the Conductor initializes the chain and the component is not the last (proxy)
- **THEN** the Conductor SHALL send `proxy/initialize`
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

### Requirement: Proxy chain error propagation

Errors in a proxy SHALL produce a JSON-RPC error response forwarded back through the chain to the client. The Conductor SHALL NOT silently skip failed proxies — a security hook proxy failing silently is dangerous.

#### Scenario: Proxy exception during proxy/successor
- **WHEN** a proxy raises an exception during `proxy/successor` processing
- **THEN** the Conductor SHALL construct a JSON-RPC error response with the exception details
- **AND** SHALL forward the error response back through the chain to the predecessor
- **AND** SHALL NOT skip the proxy or continue with default behavior

#### Scenario: Proxy crash during initialization
- **WHEN** a proxy crashes during `proxy/initialize`
- **THEN** the Conductor SHALL abort chain initialization
- **AND** SHALL clean up all already-initialized proxies and the terminal agent
- **AND** SHALL raise an initialization error to the caller

### Requirement: Proxy hot-swap is out of scope

The Conductor SHALL NOT support hot-swapping proxies at runtime (adding/removing proxies without restarting the chain). This is explicitly out of scope for this change. The design should not preclude it, but it will not be implemented.

#### Scenario: Hot-swap not supported
- **WHEN** a user attempts to modify the proxy chain at runtime
- **THEN** the system SHALL raise `NotImplementedError("Proxy hot-swap is not supported")`
- **AND** the proxy chain SHALL remain unchanged
