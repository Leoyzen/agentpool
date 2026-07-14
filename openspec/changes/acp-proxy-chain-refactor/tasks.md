## 1. ACPTurn Fix — ACPClientAdapter (Phase 1)

- [ ] 1.1 Create `src/agentpool/agents/acp_agent/adapter.py` with `ACPClientAdapter` class implementing `ACPClientProtocol`
- [ ] 1.2 Implement `ACPClientAdapter.prompt()` — launch `api.prompt()` as background task, return immediately (non-blocking)
- [ ] 1.3 Implement `ACPClientAdapter.stream_events()` — return `asyncio.Queue` that `session_update()` pushes to directly
- [ ] 1.4 Implement `ACPClientAdapter.get_messages()` — call `api.get_messages()` after prompt completes
- [ ] 1.5 Modify `ACPClientHandler.session_update()` to push notifications to async queue instead of `ACPSessionState` deque
- [ ] 1.6 Remove `poll_acp_events()` and 50ms timeout loop from `acp_agent.py`
- [ ] 1.7 Fix `ACPAgent.create_turn()` — replace `cast("ACPClientProtocol", self._api)` with `ACPClientAdapter(self._api)`
- [ ] 1.8 Fix `ACPTurn.execute()` — use `adapter.prompt()`, iterate `adapter.stream_events()`, call `adapter.get_messages()`
- [ ] 1.9 Remove `_stream_events()` inline bypass from `ACPAgent.run_stream()` — route through `ACPTurn.execute()`
- [ ] 1.10 Delete `ACPSessionState` deque class from `session_state.py`
- [ ] 1.11 Write unit tests for `ACPClientAdapter` (prompt non-blocking, stream_events queue, get_messages)
- [ ] 1.12 Write integration test: ACPAgent.run_stream() uses ACPTurn (no polling, no _stream_events bypass)

## 2. Proxy Protocol & Conductor (Phase 2)

- [ ] 2.1 Create `src/acp/proxy/__init__.py` package
- [ ] 2.2 Create `src/acp/proxy/protocol.py` — `Proxy` typing.Protocol with `proxy_initialize()` and `proxy_successor()` methods
- [ ] 2.3 Create `src/acp/proxy/connection.py` — `ProxySideConnection` wrapping `Connection` for proxy-side dispatch
- [ ] 2.4 Create `src/acp/proxy/constants.py` — wire method name constants (`PROXY_INITIALIZE`, `PROXY_SUCCESSOR`)
- [ ] 2.5 Create `src/acp/conductor.py` — `Conductor(MessageNode[ChatMessage, ChatMessage[str]])` class
- [ ] 2.6 Implement Conductor subprocess spawning using anyio task groups (structured concurrency)
- [ ] 2.7 Implement Conductor chain initialization — call `proxy/initialize` on each proxy, then `initialize` on terminal agent
- [ ] 2.8 Implement Conductor terminal agent vs proxy detection (initialize vs proxy/initialize response)
- [ ] 2.9 Implement Conductor message routing — bidirectional `proxy/successor` forwarding between adjacent proxies
- [ ] 2.10 Implement Conductor passthrough optimization — skip deserialization for unregistered message types
- [ ] 2.11 Implement Conductor `_step` property for pydantic-graph integration
- [ ] 2.12 Implement Conductor async context manager — cleanup subprocesses and connections in `finally` block
- [ ] 2.13 Write unit tests for Conductor chain initialization (zero proxies, N proxies, terminal agent detection)
- [ ] 2.14 Write unit tests for Conductor message routing (forward, passthrough, intercept)

## 3. ACPAgent Rewrite (Phase 3)

- [ ] 3.1 Rewrite `ACPAgent.__init__()` — accept optional `proxy_chain` config, create Conductor instead of direct subprocess
- [ ] 3.2 Change `ACPAgent` output type from `str` to `ChatMessage[str]` for `MessageNode` contract compliance
- [ ] 3.3 Implement `ACPAgent.create_turn()` — construct `ACPClientAdapter` from Conductor's connection, create `ACPTurn`
- [ ] 3.4 Implement `ACPAgent.run_stream()` — delegate to `ACPTurn.execute()` via graph Step
- [ ] 3.5 Add `use_conductor` feature flag to `ACPAgentConfig` for backward compatibility (default: true)
- [ ] 3.6 Create `ProxyChainConfig` Pydantic model in `src/agentpool/models/` with `type` discriminator field
- [ ] 3.7 Add `proxy_chain` optional field to `ACPAgentConfig` model
- [ ] 3.8 Update `AgentPool` to pass proxy chain config to ACPAgent during instantiation
- [ ] 3.9 Migrate `ACPAgent` from `ToolManagerBridge` to `ResourceProvider`
- [ ] 3.10 Write integration test: ACPAgent with Conductor + zero proxies (backward compat)
- [ ] 3.11 Write integration test: ACPAgent with Conductor + proxy chain
- [ ] 3.12 Verify all existing ACP agent tests pass with `use_conductor: true`

## 4. Built-in Proxy Implementations (Phase 4)

- [ ] 4.1 Create proxy type registry — map string type discriminators to proxy classes
- [ ] 4.2 Create `src/acp/proxy/impls/__init__.py` package
- [ ] 4.3 Implement `HookProxy` — wrap `Hook` instances, handle ALL 4 hook types at wire level
- [ ] 4.4 Implement `HookProxy` pre_turn mapping — `session/prompt` → `HookInput(event="pre_turn")`, apply `additional_context`/`decision` (blocking deny)
- [ ] 4.5 Implement `HookProxy` pre_tool_use mapping — `session/update` ToolCallStart → `HookInput(event="pre_tool_use")`, apply `modified_input`, blocking deny
- [ ] 4.6 Implement `HookProxy` post_tool_use mapping — `session/update` ToolCallComplete → `HookInput(event="post_tool_use")`, apply `modified_output`
- [ ] 4.7 Implement `HookProxy` post_turn mapping — `session/update` AgentMessageChunk → `HookInput(event="post_turn")`, apply `modified_output`
- [ ] 4.8 Implement `HookProxy`/`HookAwareTurn` coexistence — Conductor passes `_hooks=None` to ACPTurn when HookProxy is in chain (HookAwareTurn guard skips); passes agent's `AgentHooks` when no HookProxy
- [ ] 4.9 Implement Conductor auto-insert HookProxy — when agent has hooks configured and no explicit HookProxy in chain, auto-insert at position 0
- [ ] 4.10 Implement `ContextInjectionProxy` — intercept `session/prompt`, prepend AGENTS.md content and skill instructions (separate from HookProxy)
- [ ] 4.11 Implement `ToolProviderProxy` — reuse `AcpMcpTransport`/`AcpMcpConnectionManager` for tool injection via MCP-over-ACP
- [ ] 4.12 Register all built-in proxies in the type registry
- [ ] 4.13 Write unit tests for `HookProxy` (all 4 hook type mappings, deny/allow/modify flows, blocking semantics)
- [ ] 4.14 Write unit tests for `HookProxy`/`HookAwareTurn` coexistence (hooks_fired guard, no double-firing)
- [ ] 4.15 Write unit tests for `ContextInjectionProxy` (AGENTS.md injection, skills injection)
- [ ] 4.16 Write unit tests for `ToolProviderProxy` (MCP tool injection, tool call routing)

## 5. Server-Side Adaptation (Phase 5)

- [ ] 5.1 Refactor `AgentPoolACPAgent` to operate as terminal agent behind Conductor (respond to `initialize`, not `proxy/initialize`)
- [ ] 5.2 Remove legacy `ACPSession.process_prompt()` dual path — consolidate to `ACPProtocolHandler.handle_prompt()`
- [ ] 5.3 Refactor `ACPEventConverter` to optionally operate as a proxy component in the chain
- [ ] 5.4 Verify `ACPProtocolHandler` (ProtocolEventConsumerMixin) works unchanged with terminal agent mode
- [ ] 5.5 Write integration test: AgentPoolACPAgent as terminal agent in Conductor chain
- [ ] 5.6 Write integration test: nested agentpool (server + client) with zero conversion (passthrough)

## 6. Cleanup & Migration (Phase 6)

- [ ] 6.1 Delete `ACPSessionState` class and all references
- [ ] 6.2 Delete `poll_acp_events()` function and all references
- [ ] 6.3 Delete `_stream_events()` method from ACPAgent
- [ ] 6.4 Delete `cast("ACPClientProtocol", self._api)` and all dead code in `turn.py`
- [ ] 6.5 Remove `use_conductor` feature flag (make Conductor the only path)
- [ ] 6.6 Simplify `acp_converters.py` — passthrough scenario should be zero conversion
- [ ] 6.7 Remove `ToolManagerBridge` usage and deprecated imports
- [ ] 6.8 Remove `AgentHooks` deprecation warnings related to old ACP path
- [ ] 6.9 Update `AGENTS.md` documentation with proxy chain architecture
- [ ] 6.10 Add YAML config examples for `proxy_chain:` section
- [ ] 6.11 Run full test suite — verify no regressions
- [ ] 6.12 Run `mypy src/` — verify type safety (no `as any`, no `cast` hacks)
- [ ] 6.13 Run `ruff check src/` — verify lint clean
