## Why

The current ACPAgent implementation was built before the ACP proxy chain concept existed. It conflates subprocess management, ACP client communication, and event conversion into a single monolithic class with three critical issues: (1) `ACPTurn` — the designed Turn abstraction — is non-functional dead code due to a missing adapter, (2) streaming uses a 50ms polling loop instead of async push, and (3) nesting ACP server + client causes ~1600 lines of bidirectional event conversion (ACP→native→ACP) that should be zero-copy passthrough. The proxy chain RFD (`docs/rfds/proxy-chains.mdx` in agent-client-protocol) defines a conductor pattern that directly solves these structural problems.

## What Changes

- **NEW**: `Conductor` class — manages proxy chain lifecycle, routes `proxy/successor` messages, spawns subprocesses
- **NEW**: `Proxy` protocol (`typing.Protocol`) — defines `proxy_initialize()` + `proxy_successor()` per RFD
- **NEW**: `ProxySideConnection` — wire-protocol wrapper for proxy components (analogous to `AgentSideConnection`/`ClientSideConnection`)
- **NEW**: `ACPClientAdapter` — bridges `ACPAgentAPI` (blocking prompt + notification deque) to `ACPClientProtocol` (stream interface), making `ACPTurn` functional
- **NEW**: `HookProxy` — wraps existing `CallableHook`/`CommandHook`/`PromptHook` as proxy chain components, reusing the entire hook system
- **NEW**: Built-in proxy implementations: `ContextInjectionProxy`, `ToolProviderProxy` (reusing `AcpMcpTransport`), `PermissionHookProxy`
- **NEW**: YAML `proxy_chain:` configuration section for defining ordered proxy chains
- **REWRITE**: `ACPAgent` — split into Conductor (subprocess management) + ACPTurn (turn cycle). Delete `_stream_events()` inline logic, `poll_acp_events()`, `ACPSessionState` deque
- **REWRITE**: `ACPClientHandler.session_update()` — push directly to async stream (eliminate `TimeoutableEvent` polling)
- **FIX**: `ACPTurn` — remove `cast()` hack, use `ACPClientAdapter` for real `ACPClientProtocol` compliance
- **FIX**: `ACPAgent` output type `str` → `ChatMessage[str]` for `MessageNode` contract compliance
- **DELETE**: `poll_acp_events()` and 50ms timeout loop
- **DELETE**: Legacy `ACPSession.process_prompt()` dual path (consolidate to `ACPProtocolHandler`)
- **MIGRATE**: `ACPAgent` from `ToolManagerBridge` (deprecated) to `ResourceProvider`
- **BREAKING**: `ACPAgent.create_turn()` now returns a functional `ACPTurn` (previously would crash at runtime)
- **BREAKING**: `ACPAgent._stream_events()` signature changes — conductor-driven, no inline polling

## Capabilities

### New Capabilities
- `acp-proxy-chain`: Conductor pattern, proxy/initialize + proxy/successor protocol, proxy chain lifecycle management
- `acp-proxy-impls`: Built-in proxy implementations (context injection, tool provider, permission hooks) and HookProxy adapter for reusing existing hook system
- `acp-client-adapter`: ACPClientAdapter bridging ACPAgentAPI to ACPClientProtocol, making ACPTurn functional with async push streaming

### Modified Capabilities
- `acp-server`: Server-side ACP agent (`AgentPoolACPAgent`) becomes terminal agent behind conductor; legacy `ACPSession.process_prompt()` dual path removed
- `acp-single-execution-path`: ACPTurn becomes the single execution path for ACP agents (eliminates path A/B divergence between TurnRunner and run_stream)
- `session-orchestration`: TurnRunner now works with ACP agents via functional ACPTurn (previously broken due to missing ACPClientProtocol implementation)

## Impact

- **`src/acp/`**: New `conductor.py`, `proxy/` package (protocol, connection, impls). Existing `Connection`, `AgentSideConnection`, `ClientSideConnection` unchanged (additive only).
- **`src/agentpool/agents/acp_agent/`**: Major rewrite of `acp_agent.py`, `client_handler.py`. New `adapter.py`. Delete `turn.py` dead code patterns (ACPTurn moves to use adapter). Simplify `acp_converters.py` (passthrough eliminates most conversion).
- **`src/agentpool_server/acp_server/`**: `AgentPoolACPAgent` refactored as terminal agent. `ACPProtocolHandler` unchanged (already works). `ACPEventConverter` becomes a proxy component.
- **`src/agentpool/hooks/`**: No changes to hook implementations. New `HookProxy` adapter in `src/acp/proxy/impls/` wraps them.
- **`src/agentpool/models/`**: New `ProxyChainConfig` model. `ACPAgentConfig` updated with optional `proxy_chain` field.
- **YAML configs**: New `proxy_chain:` section. Existing configs unchanged (backward compatible — no proxy_chain = direct conductor→agent).
- **Dependencies**: No new external dependencies. Reuses existing `anyio`, `pydantic`, `acp` library.
