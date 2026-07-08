## Context

AgentPool's ACP agent layer was built before the proxy chain concept existed. The current `ACPAgent` conflates subprocess management, ACP client communication, and event conversion into a single 872-line class. Three structural defects exist:

1. **Dead ACPTurn**: `create_turn()` casts `ACPAgentAPI` to `ACPClientProtocol`, but the API doesn't implement the required `prompt()`, `stream_events()`, `get_messages()` methods. Runtime crash is avoided only because `run_stream()` bypasses Turn entirely via `_stream_events()` inline logic.
2. **Polling-based streaming**: `poll_acp_events()` uses a 50ms timeout loop to drain a deque instead of async push.
3. **Double conversion**: When agentpool is both ACP server and client (nested), events convert ACP→native→ACP (~1600 lines) when passthrough should be zero-copy.

The proxy chain RFD (`agent-client-protocol/docs/rfds/proxy-chains.mdx`) defines a Conductor pattern that routes messages through a chain of proxies, each able to intercept and transform bidirectionally. This architecture directly solves the double conversion problem (proxies pass through untouched when no interception needed) and provides a clean extension model for hooks, context injection, and tool providers.

**Current state of the wire protocol layer**: `Connection`, `AgentSideConnection`, `ClientSideConnection` are transport-agnostic and proxy-chain-ready. The `Agent` and `Client` protocols are extensible. `AcpMcpTransport` already implements MCP-over-ACP. `ACPBridge` demonstrates the forwarding pattern that the Conductor generalizes.

## Goals / Non-Goals

**Goals:**
- Implement the Conductor + Proxy protocol per the RFD (`proxy/initialize`, `proxy/successor`)
- Make ACPTurn the single execution path for ACP agents (fix via `ACPClientAdapter`)
- Eliminate 50ms polling — replace with async push streaming
- Eliminate double conversion in passthrough scenarios
- Reuse existing hook system (`CallableHook`, `CommandHook`, `PromptHook`) as `HookProxy` components
- Migrate `ToolManagerBridge` to `ResourceProvider`
- Make `ACPAgent` output `ChatMessage[str]` instead of raw `str`
- Support YAML `proxy_chain:` configuration

**Non-Goals:**
- Implement the full ACP remote transport (Streamable HTTP/WS) — future work
- Implement session fork (`session/fork`) — separate RFD
- Ratify the proxy chain RFD — we implement against the current draft
- Refactor native (PydanticAI) agents to use proxy chains — they don't need wire-level interception
- Implement Conductor-in-proxy-mode for tree topologies — future work
- Backward compatibility for `_stream_events()` internal API — internal method, safe to change

## Decisions

### D1: Conductor as MessageNode

**Decision**: `Conductor` inherits from `MessageNode[ChatMessage, ChatMessage[str]]`.

**Rationale**: The Conductor must integrate with agentpool's graph/team system. As a `MessageNode`, it can be composed in teams, connected to other nodes, and participate in the graph-based execution model. The Conductor's `_step` property wraps the proxy chain execution as a pydantic-graph Step.

**Alternative considered**: Conductor as a standalone class with `run()`/`run_stream()` methods mimicking `MessageNode` interface. Rejected — would require duplicating graph integration logic and break the unified `MessageNode` abstraction.

### D2: ACPTurn as Single Execution Path

**Decision**: Both `TurnRunner` (via `SessionPool.receive_request()`) and `run_stream()` use `ACPTurn.execute()` as the single turn execution path. The `_stream_events()` bypass is deleted.

**Rationale**: The dual-path divergence (Path A: TurnRunner→create_turn→execute vs Path B: run_stream→_stream_events) is the root cause of ACPTurn being dead code. By making ACPTurn functional via `ACPClientAdapter`, both paths converge. This also means `TurnRunner` works with ACP agents (previously broken).

**Alternative considered**: Keep dual paths, just fix ACPTurn for the TurnRunner path. Rejected — maintaining two execution paths for the same agent type is a maintenance burden and was the original cause of the divergence.

### D3: ACPClientAdapter Design

**Decision**: `ACPClientAdapter` wraps `ACPAgentAPI` to implement a **modified** `ACPClientProtocol`. The protocol is redefined to support non-blocking semantics:
- `prompt()`: Launches `api.prompt()` as a background task (fire-and-forget), returns `None` (not `PromptResponse`)
- `stream_events()`: Returns an `AsyncIterator[SessionUpdate]` (no `response` parameter) that yields items from an `asyncio.Queue` as `client_handler.session_update()` pushes them. When the background prompt task completes, the adapter signals stream completion.
- `stop_reason` property: Returns the `PromptResponse.stop_reason` after streaming completes (accessed internally when the background task finishes)
- `get_messages()`: Calls `api.get_messages()` after prompt completes

**Rationale**: The original `ACPClientProtocol` required `prompt()` to return `PromptResponse` and `stream_events()` to take a `response` parameter. This assumes synchronous completion — `prompt()` blocks until all notifications arrive, then `stream_events(response)` iterates them. But `ACPAgentAPI.prompt()` is blocking and the adapter needs to invert this: return immediately from `prompt()`, stream events as they arrive, then expose `stop_reason` after completion.

The protocol change is internal to this change's scope — `ACPClientProtocol` is only implemented by `ACPClientAdapter` and consumed by `ACPTurn`. The `PromptResponse` is stored internally by the adapter when the background task completes, and `stop_reason` is exposed as a read-only property.

The async queue SHALL have a `max_buffer_size` of 1000 (matching the current `anyio.create_memory_object_stream` value) to prevent unbounded memory growth if the consumer is slower than the ACP server's notification rate.

**Alternative considered**: Make `ACPAgentAPI` natively async with streaming. Rejected — would require deep changes to the ACP client library; the adapter is a localized bridge.

**Alternative considered**: Return a future/placeholder `PromptResponse` from `prompt()` that resolves when the background task completes. Rejected — adds complexity for callers that would need to await the future; the `stop_reason` property is simpler.

### D4: HookProxy Adapter Pattern

**Decision**: `HookProxy` implements the `Proxy` protocol and wraps existing `Hook` instances. It handles **all 4 hook types** by mapping ACP wire messages to `HookInput` events:

- `session/prompt` → `HookInput(event="pre_turn")` — hook can inject context (`additional_context`), deny (block prompt), or modify prompt before forwarding
- `session/update` with `ToolCallStart` → `HookInput(event="pre_tool_use")` — hook can modify tool input (`modified_input`) or deny (block tool call before it reaches terminal agent)
- `session/update` with `ToolCallComplete` → `HookInput(event="post_tool_use")` — hook can replace tool output (`modified_output`)
- JSON-RPC response to `session/prompt` request → `HookInput(event="post_turn")` — hook can modify agent response (`modified_output`). The proxy correlates the `session/prompt` request ID with its JSON-RPC response to determine turn completion (not individual `AgentMessageChunk` updates, which arrive throughout the turn).

`HookResult.decision=="deny"` → proxy stops forwarding (blocking, not advisory). `HookResult.additional_context` → prepended to prompt. `HookResult.modified_input` → replaces tool input. `HookResult.modified_output` → replaces output.

**Hook semantics are per-turn, not per-run-loop**: The `pre_turn`/`post_turn` names (renamed from `pre_run`/`post_run` by `unify-hook-system`) reflect the correct per-turn semantic. In a multi-turn `RunHandle` (with steer/followup), these fire for **each turn**, not just the first and last. HookProxy naturally implements this because `session/prompt` and `session/update` flow through the proxy chain on every turn.

**Rationale**: The RFD says proxies subsume hooks. But rewriting all hook implementations would waste existing, tested code. The adapter pattern preserves the hook system while elevating it to wire-protocol level. Same hook can be used for both native agents (via `HookAwareTurn` in-process) and ACP agents (via `HookProxy` at wire level). HookProxy is strictly superior to in-process hooks for ACP agents because it intercepts messages **before** they reach the terminal agent subprocess — enabling true blocking, not just advisory warnings.

**Alternative considered**: Replace hooks entirely with proxy implementations. Rejected — existing hooks (`CallableHook`, `CommandHook`, `PromptHook`) are tested and in use. Rewriting them as proxies would be a larger scope change with no benefit.

### D5: Passthrough Optimization

**Decision**: When a proxy has no interception logic for a given message type, it forwards the message without deserializing/reserializing. Proxies declare their intercepted message types during `proxy/initialize` — the response includes a `intercepted_methods` list (e.g., `["session/prompt", "session/update"]`). The Conductor tracks this registration and short-circuits the chain for message types no proxy intercepts.

**Rationale**: This solves the double conversion problem. In the current architecture, nesting ACP server + client causes ~1600 lines of ACP→native→ACP conversion. With proxy chains, a passthrough proxy forwards the raw JSON-RPC message without parsing. Only proxies that explicitly register interest in a message type during initialization pay the deserialization cost.

**Alternative considered**: Always deserialize and re-serialize. Rejected — defeats the purpose of proxy chains for passthrough scenarios.

**Alternative considered**: Inspect every message at every proxy. Rejected — adds latency even when no interception is needed.

### D6: Terminal Agent Detection by Chain Position

**Decision**: Terminal agents implement the existing `acp.Agent` protocol (no changes). The Conductor determines which components are proxies vs terminal agent based on **chain position** from configuration — the last component in the chain is the terminal agent, all others are proxies. The Conductor sends `proxy/initialize` to all proxy components and `initialize` to the terminal agent (the last component). Terminal agents don't know about proxy chains — they just handle `session/prompt` and emit `session/update`.

**Rationale**: This follows the RFD exactly. The RFD specifies: "The conductor MUST send `proxy/initialize` to all proxy components" and "The conductor MUST send `initialize` to the final agent component." The conductor decides which method to send based on chain position — it doesn't detect from responses. The spec's earlier framing of "checking response to initialization" was incorrect.

**Alternative considered**: Create a `TerminalAgent` protocol. Rejected — the existing `Agent` protocol already defines the terminal agent interface. Adding a new protocol would be redundant.

**Alternative considered**: Auto-detect by sending `proxy/initialize` first and falling back to `initialize`. Rejected — adds complexity and latency for no benefit when chain position is known from configuration.

### D7: YAML Configuration

**Decision**: New `proxy_chain:` section in agent config:

```yaml
agents:
  my_agent:
    type: acp
    command: goose
    args: [acp]
    proxy_chain:
      - type: context_injection
        agents_md: true
        skills: [code-review, debugging]
      - type: tool_provider
        mcp_servers: [filesystem, git]
      - type: hook
        event: pre_tool_use
        command: ./security-check.sh
```

When `proxy_chain` is omitted, the Conductor runs with zero proxies (direct conductor→agent).

**Rationale**: Declarative configuration matches agentpool's YAML-first philosophy. Each proxy entry maps to a registered proxy implementation. The `type` field discriminates which proxy class to instantiate.

**Alternative considered**: Programmatic configuration only. Rejected — agentpool is YAML-first; programmatic API can be added later if needed.

### D8: EventBus and Conductor Coexistence

**Decision**: EventBus handles framework-level events (`RichAgentStreamEvent`), Conductor handles ACP wire-level messages (JSON-RPC). ACPTurn is the bridge — it receives ACP messages from the Conductor and converts them to `RichAgentStreamEvent` for the EventBus.

**Rationale**: EventBus is the existing event distribution system for protocol servers. Conductor operates at a different abstraction layer (wire protocol). Mixing them would conflate concerns. ACPTurn already does this conversion — it just needs to be functional (which D3 solves).

### D9: HookProxy and HookAwareTurn Coexistence

**Decision**: Two hook firing mechanisms coexist for ACP agents. The Conductor controls which mechanism is active by controlling whether hooks are passed to `ACPTurn`:

| Mechanism | Scope | Firing Location | Capability | When Active |
|---|---|---|---|---|
| `HookAwareTurn` (v1) | All 4 hook types | `ACPTurn.execute()` (in-process) | Advisory (can't block subprocess) | No HookProxy in chain |
| `HookProxy` (v2) | All 4 hook types | Proxy chain (wire-level) | Blocking (intercepts before terminal agent) | HookProxy in chain |

**Activation rules**:
- When Conductor has a `HookProxy` in the chain: Conductor passes `_hooks=None` to `ACPTurn`. `HookAwareTurn`'s guard (`if self._hooks is None: return None`) skips all hook firing. Hooks fire at wire-level via `HookProxy`.
- When no `HookProxy` in chain: Conductor passes the agent's `AgentHooks` to `ACPTurn`. `HookAwareTurn` fires all 4 hook types in-process (advisory for tool hooks).
- Conductor **auto-inserts** `HookProxy` at chain position 0 when agent has hooks configured and no explicit `HookProxy` in `proxy_chain`.

**Why not use `hooks_fired` guard**: The `unify-hook-system` spec clears `hooks_fired` per-turn (to support multi-turn runs). If HookProxy set keys at chain init, they'd be cleared in turn 2+. Passing `_hooks=None` is simpler and doesn't interact with the per-turn clearing logic. The `hooks_fired` guard remains solely for the `_run_stream_once()` → `Turn.execute()` migration in `unify-hook-system`.

**Migration path**: `unify-hook-system` implements `HookAwareTurn` (v1) first, including Section 11 "Future Work" which describes building the `ACPClientAPI` adapter. `acp-proxy-chain-refactor` Phase 1 implements that adapter (`ACPClientAdapter`), making `ACPTurn.execute()` the single ACP execution path. Phase 4 adds `HookProxy` (v2). Eventually, when all ACP agents use Conductor, `HookAwareTurn` on `ACPTurn` can be removed (kept only for native `NativeTurn`).

**Rationale**: Both mechanisms serve the same hooks (`CallableHook`, `CommandHook`, `PromptHook`) — they differ only in WHERE interception happens (in-process vs wire). The `_hooks=None` approach is cleaner than `hooks_fired` because it doesn't require coordination with the per-turn clearing logic.

**Alternative considered**: Use `hooks_fired` guard as originally proposed. Rejected — per-turn clearing in `unify-hook-system` would require HookProxy to re-set keys every turn, creating unnecessary coupling. Passing `_hooks=None` is a single assignment at Conductor construction time.

## Risks / Trade-offs

**[RFD not ratified]** → We implement against the current draft. If `proxy/initialize` or `proxy/successor` method names change, only the wire method names need updating — the Conductor's internal architecture is stable. Mitigation: isolate wire method names in a single constants module.

**[No Python reference implementation]** → The RFD has a working Rust impl (`sacp-conductor`, `sacp-proxy`) but no Python reference. We're the first Python implementation. Mitigation: follow the RFD spec closely, use the Rust impl as reference for edge cases.

**[Two unratified RFDs dependency]** → `ToolProviderProxy` (Phase 4) depends on MCP-over-ACP transport, which is itself a separate unratified RFD. Building on two unratified specs compounds the risk. Mitigation: defer `ToolProviderProxy` to a separate change if MCP-over-ACP RFD is not ratified by Phase 4 implementation time. Mark `ToolProviderProxy` as experimental.

**[Large refactoring scope]** → ~4-5 weeks of work across 6 phases (updated from initial 2-3 week estimate after architecture review). Mitigation: phased delivery — Phase 1 (ACPTurn fix) is independently shippable and immediately useful. Each subsequent phase builds on the previous without breaking.

**[Backward compatibility]** → `ACPAgent._stream_events()` signature changes. `create_turn()` behavior changes (previously crashed, now works). Mitigation: these are internal methods. The public `run()`/`run_stream()` API remains stable. Users who depended on `_stream_events()` behavior are depending on a workaround.

**[HookProxy message mapping complexity]** → Mapping ACP wire messages to hook lifecycle events requires understanding both systems. The `post_turn` hook requires JSON-RPC request/response correlation (tracking `session/prompt` request IDs and matching them with responses). Mitigation: comprehensive tests for each message type → hook event mapping, including request/response correlation.

**[Conductor subprocess management]** → Conductor now manages subprocess lifecycle instead of ACPAgent. If the Conductor crashes, subprocesses may orphan. Mitigation: Conductor uses task groups (anyio) for structured concurrency; subprocess cleanup runs in finally block.

**[ACPSessionState deletion scope]** → `ACPSessionState` tracks more than the update deque — it holds `current_model_id`, `models`, `modes`, `config_options`, `available_commands`. Deleting the entire class (task 6.1) would break model switching, mode switching, and command population. Mitigation: Only delete the deque mechanism. Preserve model/mode/config state in a renamed `ACPState` dataclass or migrate to `ACPClientAdapter`.

**[ACPClientHandler state update routing]** → The current `ACPClientHandler.session_update()` routes state updates (mode, model, config, commands) differently from stream data — it returns early for state updates and only queues stream data. The adapter design must preserve this bifurcation. Mitigation: Spec requires that `session_update()` continues to process state updates in-place and only pushes stream-data updates (text chunks, tool calls, thoughts) to the async queue.

**[Unbounded queue in ACPClientAdapter]** → The async queue could grow unbounded if the consumer is slower than the ACP server's notification rate. Mitigation: The queue SHALL have a `max_buffer_size` of 1000 (matching the current `anyio.create_memory_object_stream` value).

**[Proxy chain error propagation]** → If a proxy throws during `proxy/successor`, the conductor must decide how to handle it. Mitigation: Proxy exceptions produce a JSON-RPC error response forwarded back through the chain. The conductor does NOT silently skip failed proxies (a security hook proxy failing silently is dangerous).

## Migration Plan

1. **Phase 1** (shippable independently): Fix ACPTurn via ACPClientAdapter. Replace polling with async push. This alone fixes 2 of 3 critical issues.
2. **Phase 2-3**: Implement Conductor + Proxy protocol. Rewrite ACPAgent to use Conductor. Old ACPAgent code remains until new path is verified.
3. **Phase 4**: Add built-in proxy implementations (HookProxy, ContextInjectionProxy, ToolProviderProxy).
4. **Phase 5**: Refactor server-side `AgentPoolACPAgent` as terminal agent.
5. **Phase 6**: Delete dead code, legacy paths, migrate ToolManagerBridge → ResourceProvider.

**Rollback**: Phases 1-3 can be feature-flagged via `use_conductor: true` in agent config. If issues arise, set `use_conductor: false` to fall back to the old `_stream_events()` path. Flag removed in Phase 6 after confidence is established.

## Open Questions

- **Proxy hot-swap (out of scope)**: Should the Conductor support hot-swapping proxies at runtime (add/remove proxy without restarting the chain)? This is explicitly **out of scope** for this change. The design should not preclude it, but it will not be implemented. Future work.
- **Concurrency: multiple concurrent prompts**: ACP sessions typically allow one active prompt at a time. If `adapter.prompt()` is called while a previous prompt is still streaming, the adapter SHALL raise a `RuntimeError("Prompt already in progress")`. This matches the current behavior where `ACPAgentAPI.prompt()` blocks until completion.
- **Proxy chains in team composition**: How should proxy chains interact with the graph-based team execution? If a team member is an ACP agent with a proxy chain, does the chain execute within the Step's `call()` method? (Answer: yes — the Conductor's `_step` property handles this.)
