## Context

AgentPool is a unified agent orchestration framework that bridges multiple protocols (ACP, AG-UI, OpenCode, MCP) and supports native PydanticAI agents. The codebase has grown to ~1200 indexed files across 10 packages. An incomplete prior refactor left dual execution paths (`BaseAgent.run_stream()` vs `RunExecutor`), a monolithic orchestrator core (3014 LOC), and a ResourceProvider hierarchy that duplicates pdai's `Toolset` abstraction.

**Current state**:
- `orchestrator/core.py`: 3014 LOC containing `EventBus`, `SessionController`, `SessionPool`, `RunHandle`, `TurnRunner`, and helper functions
- `BaseAgent.run_stream()`: ~150 LOC standalone path using bare `async for` on a producer task + EventBus subscriber
- `RunExecutor`: explicit `agent_run.next(node)` loop that fires pdai Capability hooks
- `EventBus`: uses `anyio.create_memory_object_stream()` with hybrid backpressure (0.1s timeout → `send_nowait` → drop subscriber)
- `Team`/`TeamRun`: legacy team classes coexist with `GraphConfig` but no translator connects `teams:` YAML to `graph:` YAML
- `ResourceProvider` hierarchy: 8 provider classes (MCP, Local, Pool, Static, Aggregating, Filtering, CodeMode, Plan)
- `graph_translation.py`: referenced in `graph_config.py` docstring but **does not exist**

**Key constraint**: pdai Capability hooks (`wrap_node_run`, `before_model_request`, `after_node_run`, etc.) only fire when `RunExecutor.next(node)` is called explicitly. The legacy `BaseAgent.run_stream()` uses bare `async for node in agent_run:` which does NOT fire these hooks. Building Capabilities before unifying the run stream would cause them to silently fail on the legacy path.

**Stakeholders**: AgentPool maintainers, protocol server implementers (ACP, AG-UI, OpenCode, OpenAI API), YAML config consumers.

## Goals / Non-Goals

**Goals:**
- Eliminate duplicated abstractions by delegating to pdai native mechanisms
- Make Capability hooks fire on all run paths (unify to `RunExecutor`)
- Split monolithic `core.py` into focused modules with clear boundaries
- Enable composable agent extensions via pdai Capabilities
- Enforce architectural boundaries between core and server packages
- Complete the rename to `agentwolf` as a single atomic operation

**Non-Goals:**
- Adding new user-facing features (this is purely structural)
- Changing the YAML configuration syntax (users keep writing `teams:` and `graph:`)
- Supporting backward compatibility aliases (pre-1.0, no external consumers)
- Modifying the ACP wire protocol (only internal imports change)
- Performance optimization beyond eliminating duplicate work

## Decisions

### D1: Bottom-up phase ordering (refactor first, rename last)

**Choice**: Execute phases 1→8 in strict order. No phase starts until the previous completes.

**Rationale**: The key constraint — Capability hooks only fire on `RunExecutor.next(node)` — creates a hard dependency: Phase 6 (Capabilities) requires Phase 2 (Run Stream Unification) to be complete. Phase 8 (Rename) is last because it's a mechanical operation that would conflict with ongoing code changes.

**Alternatives considered**:
- Top-down (rename first, then refactor): Rejected — 1599 import changes would need to be re-done after each phase modifies files.
- Parallel phases: Rejected — Phases 1-3 all touch `orchestrator/core.py`; Phases 2 and 6 have a hard dependency.
- Rename mid-refactor: Rejected — would double the diff size of every phase.

### D2: No alias period for rename

**Choice**: One-shot rename from `agentpool` to `agentwolf` with no backward-compatible aliases.

**Rationale**: AgentPool is pre-1.0 with no external consumers. An alias period would add maintenance burden (dual import paths, deprecation warnings) for zero benefit.

**Alternatives considered**:
- 6-month alias period with `agentpool` as deprecated wrapper: Rejected — adds complexity for no users.
- Keep `agentpool` name: Rejected — the rename signals the architectural break from the old thick-wrapper design.

### D3: `acp` package name stays

**Choice**: The `acp` package retains its name but 2 imports that reference `agentpool` are updated to `agentwolf`.

**Rationale**: The `acp` package implements the Agent Communication Protocol standard. Its name is semantically meaningful (matches the protocol name) and doesn't carry the `agentpool` branding. Only 2 import lines need updating.

**Alternatives considered**:
- Rename to `agentwolf_acp`: Rejected — breaks the semantic connection to the ACP standard.
- Move into `agentwolf` package as `agentwolf.acp`: Rejected — the `acp` package is also used standalone by external consumers of the protocol.

### D4: CodeModeResourceProvider deprecated (not migrated)

**Choice**: `CodeModeResourceProvider` and `RemoteCodeModeResourceProvider` are deprecated. The single-meta-tool pattern (wrapping all tools into one Python execution tool) cannot be expressed by `ToolsetFactory` which produces standard pdai Toolsets.

**Rationale**: `CodeModeResourceProvider` wraps all tools from aggregated providers into a single `execute_tool` function that runs Python code. This is fundamentally different from pdai's Toolset model (list of individual tools). Migrating it would require either a new Toolset variant or keeping the provider pattern just for this one case.

**Alternatives considered**:
- Implement as a custom pdai `Toolset` subclass: Rejected — the meta-tool pattern requires generating dynamic tool descriptions and execution namespaces, which doesn't fit Toolset's static tool list model.
- Keep as `ToolsetFactory` with special case: Rejected — adds complexity for a single use case.

### D5: PlanProvider kept as stateful pdai toolset

**Choice**: `PlanProvider` is migrated to a pdai `Toolset` (not `ToolsetFactory`) because it needs `RunContext.deps` for stateful plan management.

**Rationale**: `PlanProvider` exposes plan management tools (`get_plan_entry`, `set_plan_entry`) that require access to the agent's run context deps. pdai's `Toolset` protocol supports `RunContext`-aware tools, while `ToolsetFactory` is a stateless factory that produces tool lists.

**Alternatives considered**:
- Migrate to `ToolsetFactory`: Rejected — would lose `RunContext.deps` access.
- Drop plan management: Rejected — core feature.

### D6: Skill injection gap — SkillBridgeCapability → SkillActivationCapability

**Choice**: Phase 5 creates `SkillBridgeCapability` as an interim bridge to inject skills into agent prompts. Phase 6 replaces it with `SkillActivationCapability` which fully integrates with pdai's Capability lifecycle.

**Rationale**: Skill injection currently happens via `SkillsInstructionProvider` which produces XML blocks injected into the system prompt. In Phase 5, when `ResourceProvider` is removed, this injection path breaks. `SkillBridgeCapability` preserves the old behavior as a Capability. In Phase 6, `SkillActivationCapability` reimagines skill injection using pdai's `before_model_request` hook for dynamic, per-turn skill activation.

**Alternatives considered**:
- Skip `SkillBridgeCapability`, build `SkillActivationCapability` directly in Phase 5: Rejected — Phase 5 already has enough scope (replacing entire ResourceProvider hierarchy). Adding Capability implementation there would delay Phase 5 completion.
- Keep `SkillBridgeCapability` permanently: Rejected — it's a bridge with known limitations (static injection, no per-turn activation).

### D7: EventBus `block` policy removed from publish path

**Choice**: The `block` overflow policy is removed from the EventBus publish path. Only `drop_oldest`, `drop_newest`, and `drop_subscriber` policies are supported.

**Rationale**: A `block` policy on `publish()` would cause the agent run loop to block when a subscriber is slow, creating a deadlock when the subscriber is the same run loop (which it often is — the stream consumer feeds events back into the run). This is a correctness issue, not a performance optimization.

**Alternatives considered**:
- Keep `block` with a timeout: Rejected — any timeout long enough to be useful risks deadlock; any timeout short enough to avoid deadlock is too short for backpressure.
- Make `block` opt-in per subscriber: Rejected — the publish path doesn't know which subscriber would block.

### D8: EventBus uses asyncio.Queue, not anyio memory streams

**Choice**: Replace `anyio.create_memory_object_stream()` with `asyncio.Queue` for EventBus subscriber queues.

**Rationale**: anyio memory streams have hybrid backpressure that's hard to reason about (send blocks, then `send_nowait`, then subscriber drop). `asyncio.Queue` with `maxsize` provides clear, predictable behavior: `put_nowait()` raises `QueueFull`, which maps cleanly to overflow policies. `asyncio.Queue` is also more familiar to Python developers and has better introspection support (`qsize()`, `empty()`, `full()`).

**Alternatives considered**:
- Keep anyio memory streams, add overflow policy wrapper: Rejected — adds a layer around an already complex abstraction.
- Use `queue.Queue` (thread-safe): Rejected — EventBus is async-only; `asyncio.Queue` is the correct primitive.

## Risks / Trade-offs

### R1: Phase 2 breaks all protocol servers simultaneously
**Risk**: Deprecating `BaseAgent.run_stream()` affects ACP, AG-UI, OpenCode, and OpenAI API servers at once. If `RunExecutor` has any behavioral difference (event ordering, error handling, cancellation), all servers break.
**Mitigation**: Phase 2 starts with a comprehensive test suite comparing `RunExecutor` output to `BaseAgent.run_stream()` output. All protocol server integration tests must pass before Phase 2 is complete.

### R2: Phase 4 translator may not cover all team configs
**Risk**: The `teams:` → `graph:` translator must handle `TeamMemberConfig` with `prompt_template`, `shared_prompt`, `member_timeout`, `member_retry_attempts`, and `member_retry_delay`. Missing any of these causes silent behavior changes.
**Mitigation**: Phase 4 starts with a exhaustive test suite covering all `TeamConfig` fields. The translator is tested against all existing `teams:` YAML configs in `site/examples/`.

### R3: Phase 5 ResourceProvider removal has wide blast radius
**Risk**: `MCPResourceProvider` has 25 callers, `LocalResourceProvider` has 44 callers. Replacing them with `ToolsetFactory` changes the tool acquisition API for every consumer.
**Mitigation**: Phase 5 creates `ToolsetFactory` adapters that internally call the old provider methods, allowing incremental migration. Adapters are removed only after all callers are migrated.

### R4: Phase 6 Capability implementations may conflict with existing hooks
**Risk**: AgentPool already has a hooks system (`pre_run`, `post_run`, `pre_tool_use`, `post_tool_use`). pdai Capabilities overlap with some of these (e.g., `LoopDetectionCapability` vs `pre_run` depth check). Running both could cause double-execution or conflicts.
**Mitigation**: Phase 6 audits each existing hook and either migrates it to a Capability or documents why both are needed. No hook and Capability should serve the same purpose.

### R5: Phase 8 rename may miss non-Python files
**Risk**: 1599 imports is the Python count. The rename also affects YAML configs, Markdown docs, CLI entry points, `pyproject.toml`, and shell scripts. Missing any causes inconsistent branding.
**Mitigation**: Phase 8 uses a project-wide `sed`/`ast-grep` replacement, then a grep for any remaining `agentpool` references. A checklist of file types is maintained.

### R6: Phase 7 import-linter may surface more than 4 violations
**Risk**: The issue mentions 4 known core→app import violations, but `import-linter` may surface additional violations that were unknown.
**Mitigation**: Phase 7 runs `import-linter` first to get the full list. Unknown violations are triaged — if easy to fix, they're fixed in Phase 7; if complex, they're documented as known issues.

### R7: EventBus Queue migration changes backpressure semantics
**Risk**: `asyncio.Queue` with `maxsize` blocks on `put()` by default. If any code path uses `put()` instead of `put_nowait()`, the run loop could block.
**Mitigation**: Phase 3 audit ensures all EventBus publish paths use `put_nowait()` with overflow policy handling. No `put()` (blocking) calls are allowed in the publish path.

### R8: Graph-based streaming may change event timing
**Risk**: `RunExecutor` drives `Graph.iter()` in a background task and pushes events to a queue. This indirection may change event timing compared to `BaseAgent.run_stream()`'s direct producer/consumer pattern.
**Mitigation**: Event ordering tests in `tests/agents/` are the gate. Any timing-sensitive tests are updated with tolerance or rewritten to test ordering, not exact timing.

### R9: Capability hook firing depends on RunExecutor implementation detail
**Risk**: The entire Phase 6 depends on `RunExecutor` calling `agent_run.next(node)` explicitly. If a future pdai change makes `async for` fire hooks too, the distinction disappears — but if pdai changes `next()` to NOT fire hooks, Phase 6 breaks.
**Mitigation**: Pin pdai version. Add a test that verifies Capability hooks fire when `RunExecutor.next()` is called.

### R10: Phase ordering creates a long delivery window
**Risk**: 8 phases, each blocking the next, means the full refactor takes weeks. Intermediate states may be unstable (e.g., after Phase 5 but before Phase 6, skill injection uses `SkillBridgeCapability` which is explicitly temporary).
**Mitigation**: Each phase is independently testable and shippable. The `SkillBridgeCapability` interim is explicitly marked as temporary with a migration path documented.

## Migration Plan

**Phase-by-phase migration**:

1. **Phase 1 (Core Split)**: Pure file move + re-exports. No behavior change. All imports from `orchestrator.core` still work via `__init__.py` re-exports. Rollback: revert file moves.

2. **Phase 2 (Run Stream)**: `BaseAgent.run_stream()` delegates to `RunExecutor`. All protocol servers updated to use `RunExecutor` directly. Rollback: restore `BaseAgent.run_stream()` standalone path.

3. **Phase 3 (EventBus)**: `EventBus` internal transport changes. Public API (`subscribe`, `publish`, `unsubscribe`) unchanged. Rollback: restore anyio memory streams.

4. **Phase 4 (Team Cleanup)**: Translator runs at config load time. `Team`/`TeamRun` removed only after all `teams:` configs translate successfully. Rollback: restore `Team`/`TeamRun` classes.

5. **Phase 5 (ToolProvider)**: `ToolsetFactory` adapters wrap old providers. Migration is incremental — each caller updated one at a time. Rollback: restore `ResourceProvider` hierarchy.

6. **Phase 6 (Capabilities)**: New `capabilities/` package. Agents opt-in to Capabilities via config. Rollback: remove `capabilities/` package and config fields.

7. **Phase 7 (Server Boundaries)**: `import-linter` added as dev dependency. Import violations fixed. Rollback: remove `import-linter` config and revert import fixes.

8. **Phase 8 (Rename)**: Single atomic commit. `agentpool` → `agentwolf` across all files. Rollback: revert the commit.

**Overall rollback strategy**: Each phase is a separate commit (or set of commits). If any phase causes regression, revert that phase's commits. Phases are ordered so that reverting a later phase doesn't require reverting earlier phases.

## Open Questions

1. **Should `SkillActivationCapability` support skill uninstallation mid-run?** The current `SkillsInstructionProvider` injects skills statically at run start. `SkillActivationCapability` could dynamically add/remove skills during a run via `before_model_request`. Is this desired, or should skill activation remain per-run?

2. **What overflow policy should be the EventBus default?** `drop_oldest` preserves newest events (good for streaming) but loses history. `drop_newest` preserves history but drops live events. `drop_subscriber` is the most aggressive. The current anyio implementation effectively does `drop_oldest` then `drop_subscriber`. Should the default match current behavior?

3. **Should `LoopDetectionCapability` use depth or message count?** Depth tracking (counting nested agent calls) is more precise but requires graph-level instrumentation. Message count is simpler but may false-positive on legitimate long conversations.

4. **Should `ToolsetFactory` be a `Protocol` or an `ABC`?** Protocol supports structural typing (duck typing), ABC supports nominal typing (explicit inheritance). pdai uses Protocol for its Toolset. Matching pdai is consistent but loses the explicit registration check.

5. **Should Phase 8 also rename the GitHub repository?** The issue doesn't specify. Renaming the repo affects git remotes, CI, and issue/PR links. Could be done as a follow-up.

6. **Should `MemoryCapability` use the existing storage system or a new memory store?** AgentPool has `agentpool_storage/` with SQL, Zed, Claude, and OpenCode providers. `MemoryCapability` could reuse these or define its own simpler key-value store.

7. **How should `TokenBudgetCapability` interact with model-specific token counting?** Different models count tokens differently (BPE vs word-level). Should the Capability delegate to the model's tokenizer, or use a generic approximation?

8. **Should the `teams:` → `graph:` translator be lossless?** `TeamConfig` has fields like `member_retry_attempts` and `member_retry_delay` that `GraphConfig` doesn't have equivalents for. These could be dropped, or `GraphConfig` could be extended to support them.

9. **Should Phase 7 add `import-linter` to CI or just as a local check?** Adding to CI enforces boundaries but may block PRs that inadvertently violate them. Starting as a local check with a pre-commit hook is safer.
