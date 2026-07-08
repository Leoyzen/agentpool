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

**Decision**: `ACPClientAdapter` wraps `ACPAgentAPI` to implement `ACPClientProtocol`. It:
- `prompt()`: Launches `api.prompt()` as a background task (fire-and-forget), returns immediately
- `stream_events()`: Returns an `asyncio.Queue` that `client_handler.session_update()` pushes to directly (no polling)
- `get_messages()`: Calls `api.get_messages()` after prompt completes

**Rationale**: This is the ~50-line adapter that was identified as missing. The key insight is that `ACPAgentAPI.prompt()` is blocking (returns `PromptResponse` after all notifications), but `ACPClientProtocol.prompt()` should be non-blocking (returns immediately, notifications arrive via `stream_events()`). The adapter bridges this by making `prompt()` fire-and-forget and routing notifications to an async queue.

**Alternative considered**: Make `ACPAgentAPI` natively async with streaming. Rejected — would require deep changes to the ACP client library; the adapter is a localized bridge.

### D4: HookProxy Adapter Pattern

**Decision**: `HookProxy` implements the `Proxy` protocol and wraps existing `Hook` instances. It handles **all 4 hook types** by mapping ACP wire messages to `HookInput` events:

- `session/prompt` → `HookInput(event="pre_turn")` — hook can inject context (`additional_context`), deny (block prompt), or modify prompt before forwarding
- `session/update` with `ToolCallStart` → `HookInput(event="pre_tool_use")` — hook can modify tool input (`modified_input`) or deny (block tool call before it reaches terminal agent)
- `session/update` with `ToolCallComplete` → `HookInput(event="post_tool_use")` — hook can replace tool output (`modified_output`)
- `session/update` with `AgentMessageChunk` (final) → `HookInput(event="post_turn")` — hook can modify agent response (`modified_output`)

`HookResult.decision=="deny"` → proxy stops forwarding (blocking, not advisory). `HookResult.additional_context` → prepended to prompt. `HookResult.modified_input` → replaces tool input. `HookResult.modified_output` → replaces output.

**Hook semantics are per-turn, not per-run-loop**: The `pre_turn`/`post_turn` names (renamed from `pre_run`/`post_run` by `unify-hook-system`) reflect the correct per-turn semantic. In a multi-turn `RunHandle` (with steer/followup), these fire for **each turn**, not just the first and last. HookProxy naturally implements this because `session/prompt` and `session/update` flow through the proxy chain on every turn.

**Rationale**: The RFD says proxies subsume hooks. But rewriting all hook implementations would waste existing, tested code. The adapter pattern preserves the hook system while elevating it to wire-protocol level. Same hook can be used for both native agents (via `HookAwareTurn` in-process) and ACP agents (via `HookProxy` at wire level). HookProxy is strictly superior to in-process hooks for ACP agents because it intercepts messages **before** they reach the terminal agent subprocess — enabling true blocking, not just advisory warnings.

**Alternative considered**: Replace hooks entirely with proxy implementations. Rejected — existing hooks (`CallableHook`, `CommandHook`, `PromptHook`) are tested and in use. Rewriting them as proxies would be a larger scope change with no benefit.

### D5: Passthrough Optimization

**Decision**: When a proxy has no interception logic for a given message type, it forwards the message without deserializing/reserializing. The Conductor tracks which proxies are "transparent" for which message types and short-circuits the chain.

**Rationale**: This solves the double conversion problem. In the current architecture, nesting ACP server + client causes ~1600 lines of ACP→native→ACP conversion. With proxy chains, a passthrough proxy forwards the raw JSON-RPC message without parsing. Only proxies that explicitly register interest in a message type pay the deserialization cost.

**Alternative considered**: Always deserialize and re-serialize. Rejected — defeats the purpose of proxy chains for passthrough scenarios.

### D6: Terminal Agent Interface

**Decision**: Terminal agents implement the existing `acp.Agent` protocol (no changes). The Conductor detects terminal agents by checking `proxy_initialized` capability during `initialize()` — if the agent responds to `initialize` (not `proxy/initialize`), it's a terminal agent.

**Rationale**: This follows the RFD exactly. Terminal agents don't know about proxy chains — they just handle `session/prompt` and emit `session/update`. The Conductor manages the chain and forwards unwrapped messages to the terminal agent.

**Alternative considered**: Create a `TerminalAgent` protocol. Rejected — the existing `Agent` protocol already defines the terminal agent interface. Adding a new protocol would be redundant.

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

**[Large refactoring scope]** → ~2-3 weeks of work across 6 phases. Mitigation: phased delivery — Phase 1 (ACPTurn fix) is independently shippable and immediately useful. Each subsequent phase builds on the previous without breaking.

**[Backward compatibility]** → `ACPAgent._stream_events()` signature changes. `create_turn()` behavior changes (previously crashed, now works). Mitigation: these are internal methods. The public `run()`/`run_stream()` API remains stable. Users who depended on `_stream_events()` behavior are depending on a workaround.

**[HookProxy message mapping complexity]** → Mapping ACP wire messages to hook lifecycle events requires understanding both systems. Mitigation: comprehensive tests for each message type → hook event mapping. The mapping is finite (4 hook events × ~6 ACP message types).

**[Conductor subprocess management]** → Conductor now manages subprocess lifecycle instead of ACPAgent. If the Conductor crashes, subprocesses may orphan. Mitigation: Conductor uses task groups (anyio) for structured concurrency; subprocess cleanup runs in finally block.

## Migration Plan

1. **Phase 1** (shippable independently): Fix ACPTurn via ACPClientAdapter. Replace polling with async push. This alone fixes 2 of 3 critical issues.
2. **Phase 2-3**: Implement Conductor + Proxy protocol. Rewrite ACPAgent to use Conductor. Old ACPAgent code remains until new path is verified.
3. **Phase 4**: Add built-in proxy implementations (HookProxy, ContextInjectionProxy, ToolProviderProxy).
4. **Phase 5**: Refactor server-side `AgentPoolACPAgent` as terminal agent.
5. **Phase 6**: Delete dead code, legacy paths, migrate ToolManagerBridge → ResourceProvider.

**Rollback**: Phases 1-3 can be feature-flagged via `use_conductor: true` in agent config. If issues arise, set `use_conductor: false` to fall back to the old `_stream_events()` path. Flag removed in Phase 6 after confidence is established.

## Open Questions

- Should the Conductor support hot-swapping proxies at runtime (add/remove proxy without restarting the chain)? Currently out of scope, but the design should not preclude it.
- How should proxy chains interact with the graph-based team execution? If a team member is an ACP agent with a proxy chain, does the chain execute within the Step's `call()` method? (Answer: yes — the Conductor's `_step` property handles this.)
