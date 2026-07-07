# unify-hook-system - Work Plan

## TL;DR (For humans)

**What you'll get:** Hooks will actually fire when agents run through the session pool — currently they silently don't. All four hook types (before turn, after turn, before tool use, after tool use) will work reliably for both native and ACP agents, fired from a single unified location instead of being scattered across two broken paths.

**Why this approach:** The root cause is that hooks were wired through pydantic-ai's `Hooks` capability via a "stripping hack" that made them a no-op, and the SessionPool execution path never called them at all. By creating a `HookAwareTurn` mixin that fires hooks from `Turn.execute()` — the single choke point both native and ACP turns pass through — we fix the bug at its source and eliminate 400+ lines of workaround code.

**What it will NOT do:** It won't change how individual hooks work (CallableHook, CommandHook, PromptHook stay the same). It won't change the deny>ask>allow priority logic. It won't add new hook types. It won't change the event bus or graph architecture.

**Effort:** Large
**Risk:** Medium — touches the core turn execution path for both agent types; double-firing guard mitigates migration risk
**Decisions to sanity-check:** (1) hooks_fired set in AgentRunContext as the double-fire guard; (2) ACP tool hooks are advisory only (can't intercept external agent's tools); (3) Phase 4 removes deprecated APIs entirely (v0.5.0 breaking)

Your next move: approve to start execution, or request a high-accuracy dual-Momus review first. Full execution detail follows below.

---

> TL;DR (machine): Large, Medium risk — 4-phase hook system unification: rename pre_run/post_run→pre_turn/post_turn, create HookAwareTurn mixin firing all 4 hooks from Turn.execute() (post_turn in finally block), deprecate as_capability(), slim NativeAgentHookManager 661→~200 LOC, remove deprecated APIs (v0.5.0 breaking, native only — ACP standalone retains old path). 14 todos across 5 waves. Dual-Momus reviewed: 5 critical + 6 medium findings incorporated.

## Scope
### Must have
- Rename `HookEvent` Literal values: `"pre_run"`→`"pre_turn"`, `"post_run"`→`"post_turn"` in `src/agentpool/hooks/base.py:17`
- Rename `AgentHooks` fields: `pre_run`→`pre_turn`, `post_run`→`post_turn` in `src/agentpool/hooks/agent_hooks.py:30-52`
- Rename `AgentHooks` methods: `run_pre_run_hooks()`→`run_pre_turn_hooks()`, `run_post_run_hooks()`→`run_post_turn_hooks()` in `src/agentpool/hooks/agent_hooks.py:58-113`
- Rename `HooksConfig` fields: `pre_run`→`pre_turn`, `post_run`→`post_turn` with deprecated aliases in `src/agentpool_config/hooks.py`
- Create `HookAwareTurn` mixin class in `src/agentpool/orchestrator/turn.py` that fires all 4 hooks from `execute()`
- Add `hooks_fired: set[str]` field to `AgentRunContext` in `src/agentpool/agents/context.py:76`
- Integrate `HookAwareTurn` into `NativeTurn.execute()` at `src/agentpool/agents/native_agent/turn.py:95-349`
- Integrate `HookAwareTurn` into `ACPTurn.execute()` at `src/agentpool/agents/acp_agent/turn.py:116-196`
- Guard old hook firing in `src/agentpool/agents/base_agent.py:1329,1392` against double-firing
- ACP permission blocking: hooks fire before `auto_approve` check in `src/agentpool/agents/acp_agent/client_handler.py:217`
- Deprecate `as_capability()` in `src/agentpool/hooks/agent_hooks.py:307-335` and `src/agentpool/agents/native_agent/hook_manager.py:470-495`
- Slim `NativeAgentHookManager` from 661→~200 LOC (remove `_ToolInterceptCapability`, stripping hack, delegate methods)
- Remove deprecated APIs entirely (v0.5.0 breaking): `as_capability()`, old `pre_run`/`post_run` field aliases, stripping hack
- 3-tier test suite: unit (HookAwareTurn isolated), integration (NativeTurn+ACPTurn), E2E (SessionPool path)
- Update `openspec/changes/unify-hook-system/tasks.md` marking completed tasks

### Must NOT have (guardrails, anti-slop, scope boundaries)
- Do NOT change the `Hook` ABC or `Hook` subclasses (`CallableHook`, `CommandHook`, `PromptHook`) — their internal logic stays
- Do NOT change the `_run_hooks()` parallel dispatch logic (deny>ask>allow priority) in `agent_hooks.py:188-305`
- Do NOT change `HookInput`/`HookResult` TypedDicts (except `event` field values in HookEvent Literal)
- Do NOT change the pydantic-ai `Hooks` capability class itself
- Do NOT add new hook types (no `pre_message`, `post_message`, etc.)
- Do NOT change the `EventBus` or `RichAgentStreamEvent` types
- Do NOT change the graph/step architecture or `SignalEmittingGraphRun`
- Do NOT change ACP protocol-level message formats
- Do NOT remove the `hooks` parameter from agent constructors
- Do NOT use `getattr` or `hasattr` — provide full type safety per AGENTS.md rules

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: TDD for HookAwareTurn mixin (write test first, then implement); tests-after for rename/slim phases
- Framework: pytest with `@pytest.mark.unit`, `@pytest.mark.integration` markers
- Evidence: `.omo/evidence/task-<N>-unify-hook-system.<ext>`
- 3 tiers:
  1. **Unit**: `HookAwareTurn` with mock `AgentHooks`, verify all 4 hooks fire in correct order, double-fire guard works
  2. **Integration**: `NativeTurn` with `TestModel` (pydantic-ai), `ACPTurn` with fake `ACPClientProtocol` — verify hooks fire during turn execution
  3. **E2E**: `SessionPool` path via `RunHandle.start()` at `run.py:308` — verify hooks fire (this is the regression test for the original bug)

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.

Wave 1 (Phase 1 — Rename + HookAwareTurn + Integration): Todos 1-7
Wave 2 (Phase 2 — Deprecation): Todos 8-9
Wave 3 (Phase 3 — Slim + Cleanup): Todos 10-11
Wave 4 (Phase 4 — Remove deprecated): Todo 12
Wave 5 (Docs + Final): Todos 13-14

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 (Rename HookEvent + AgentHooks) | — | 2,3,4,5,8 | — |
| 2 (Rename HooksConfig) | 1 | 3,4 | 3 (after 1) |
| 3 (HookAwareTurn mixin + context) | 1 | 4,5,6,7 | 2 |
| 4 (NativeTurn integration) | 1,2,3 | 5,7 | — |
| 5 (ACPTurn integration) | 1,2,3 | 7 | 4 |
| 6 (Guard old base_agent.py path) | 3 | 7 | 4,5 |
| 7 (Phase 1 tests: 3-tier) | 3,4,5,6 | 8 | — |
| 8 (Deprecate as_capability) | 7 | 10 | 9 |
| 9 (Deprecate old field aliases) | 7 | 12 | 8 |
| 10 (Slim NativeAgentHookManager) | 8 | 11 | — |
| 11 (Dead code cleanup) | 10 | 12 | — |
| 12 (Remove deprecated APIs) | 9,11 | 13 | — |
| 13 (Update tasks.md) | 12 | 14 | — |
| 14 (Full test suite + lint + mypy) | 13 | F1-F4 | — |

## Todos
> Implementation + Test = ONE todo. Never separate.

- [x] 1. Rename HookEvent Literal + AgentHooks fields and methods + NativeAgentHookManager delegates
  What to do / Must NOT do: 
    (a) Rename `HookEvent` Literal values in `src/agentpool/hooks/base.py:17` from `"pre_run"`→`"pre_turn"`, `"post_run"`→`"post_turn"`. 
    (b) Rename `AgentHooks` dataclass fields at `src/agentpool/hooks/agent_hooks.py:30-52` from `pre_run`→`pre_turn`, `post_run`→`post_turn`. 
     (c) Rename methods `run_pre_run_hooks()`→`run_pre_turn_hooks()` (lines 58-83), `run_post_run_hooks()`→`run_post_turn_hooks()` (lines 85-113). Update all internal `event=` string values in HookInput construction. **CRITICAL (Momus C3)**: Add `duration_ms: float = 0.0` parameter to `run_post_turn_hooks()` signature (spec requirement). **CRITICAL (Momus M6)**: Add deprecated method aliases: keep `run_pre_run_hooks()` as a wrapper that emits `DeprecationWarning` then calls `run_pre_turn_hooks()`; same for `run_post_run_hooks()`→`run_post_turn_hooks()`. These aliases are removed in Phase 4 (Todo 12).
     (d) **CRITICAL (Metis G3.4)**: Also rename delegate methods in `NativeAgentHookManager` at `src/agentpool/agents/native_agent/hook_manager.py:497-627`: `run_pre_run_hooks()`→`run_pre_turn_hooks()`, `run_post_run_hooks()`→`run_post_turn_hooks()`. These delegate to `AgentHooks` methods which are now renamed — if not updated, the delegation breaks. Add deprecated aliases here too (wrappers calling new names + DeprecationWarning).
    (e) **CRITICAL (Metis G3.5/G5.1)**: Update `HooksConfig.get_agent_hooks()` at `src/agentpool_config/hooks.py:284-290` to use new field names: `AgentHooks(pre_turn=..., post_turn=...)` instead of `AgentHooks(pre_run=..., post_run=...)`. Also update `cfg.get_hook("pre_run")` → `cfg.get_hook("pre_turn")` and `cfg.get_hook("post_run")` → `cfg.get_hook("post_turn")` at lines 285-288.
    (f) Update `as_capability()` and `_wrap_*` helpers at `src/agentpool/hooks/agent_hooks.py:307-435` to call renamed methods.
    Do NOT rename `pre_tool_use` or `post_tool_use`. Do NOT change `_run_hooks()` logic (lines 188-305). Do NOT remove old names yet — add aliases (see Todo 2).
  Parallelization: Wave 1 | Blocked by: — | Blocks: 2,3,4,5,8
  References (executor has NO interview context - be exhaustive):
    - `src/agentpool/hooks/base.py:17` — `HookEvent = Literal["pre_run", "post_run", "pre_tool_use", "post_tool_use"]`
    - `src/agentpool/hooks/agent_hooks.py:30-52` — `AgentHooks` dataclass with `pre_run`, `post_run`, `pre_tool_use`, `post_tool_use` fields
    - `src/agentpool/hooks/agent_hooks.py:58-83` — `run_pre_run_hooks()` method, constructs HookInput with `event="pre_run"`
    - `src/agentpool/hooks/agent_hooks.py:85-113` — `run_post_run_hooks()` method, constructs HookInput with `event="post_run"`
    - `src/agentpool/hooks/agent_hooks.py:115-147` — `run_pre_tool_hooks()` method (unchanged)
    - `src/agentpool/hooks/agent_hooks.py:149-186` — `run_post_tool_hooks()` method (unchanged)
    - `src/agentpool/hooks/agent_hooks.py:188-305` — `_run_hooks()` static method (DO NOT CHANGE)
    - `src/agentpool/hooks/agent_hooks.py:307-435` — `as_capability()` and `_wrap_*` helpers (update method names called)
    - `src/agentpool/agents/native_agent/hook_manager.py:497-627` — delegate methods `run_pre_run_hooks()`, `run_post_run_hooks()`, `run_pre_tool_hooks()`, `run_post_tool_hooks()` — MUST rename pre_run→pre_turn, post_run→post_turn
    - `src/agentpool_config/hooks.py:284-290` — `get_agent_hooks()` constructs `AgentHooks(pre_run=..., post_run=...)` — MUST update to `pre_turn`/`post_turn`
    - `src/agentpool_config/hooks.py:285-288` — `cfg.get_hook("pre_run")` and `cfg.get_hook("post_run")` — MUST update to `"pre_turn"`/`"post_turn"`
  Acceptance criteria (agent-executable): `uv run ruff check src/agentpool/hooks/ src/agentpool/agents/native_agent/hook_manager.py src/agentpool_config/hooks.py` passes clean. `uv run mypy src/agentpool/hooks/ src/agentpool/agents/native_agent/hook_manager.py src/agentpool_config/hooks.py` passes clean. `uv run pytest tests/ -k "hook" -x` passes (existing tests may need updates for new names).
  QA scenarios (name the exact tool + invocation): 
    - Happy: `uv run pytest tests/ -k "hook" -vv` — all hook tests pass with new names
    - Failure: grep for any remaining `pre_run` or `post_run` string literals in `src/agentpool/hooks/` and `src/agentpool/agents/native_agent/hook_manager.py` — should only appear in deprecated aliases (added in Todo 2)
    - Evidence: `.omo/evidence/task-1-unify-hook-system.txt`
  Commit: Y | refactor(hooks): rename pre_run/post_run to pre_turn/post_turn in HookEvent, AgentHooks, NativeAgentHookManager, and HooksConfig

- [x] 2. Rename HooksConfig fields with deprecated aliases
  What to do / Must NOT do: In `src/agentpool_config/hooks.py`, rename `HooksConfig` fields `pre_run`→`pre_turn`, `post_run`→`post_turn`. Add backward-compatible aliases using Pydantic's `Field(alias="pre_run")` pattern or `model_config = ConfigDict(populate_by_name=True)` so existing YAML configs using `pre_run:`/`post_run:` still work. Add a `DeprecationWarning` in `__init__` or a validator when the old alias is used. Do NOT remove old alias support (that's Phase 4, Todo 12). Do NOT change `pre_tool_use`/`post_tool_use` fields.
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 3,4
  References:
    - `src/agentpool_config/hooks.py` — `HooksConfig` class with `pre_run`, `post_run`, `pre_tool_use`, `post_tool_use` fields and `get_agent_hooks()` method (295 lines total)
    - `src/agentpool/hooks/agent_hooks.py:30-52` — `AgentHooks` dataclass (already renamed in Todo 1)
  Acceptance criteria: `uv run pytest tests/ -k "config" -x` passes. A test with YAML `hooks: { pre_run: [...] }` still loads but emits DeprecationWarning. A test with YAML `hooks: { pre_turn: [...] }` loads without warning.
  QA scenarios:
    - Happy: `uv run pytest tests/ -k "hook" -vv` passes
    - Failure: `uv run python -c "import warnings; warnings.simplefilter('error'); from agentpool_config.hooks import HooksConfig; HooksConfig(pre_run=[])"` raises DeprecationWarning
    - Evidence: `.omo/evidence/task-2-unify-hook-system.txt`
  Commit: Y | refactor(hooks): rename HooksConfig fields with deprecated aliases

- [x] 3. Create HookAwareTurn mixin and add hooks_fired to AgentRunContext
  What to do / Must NOT do: 
     (a) Add `hooks_fired: set[str] = field(default_factory=set)` to `AgentRunContext` dataclass at `src/agentpool/agents/context.py:76`. Place it near line 94 (after `cancelled: bool = False`). **CRITICAL (Momus C2)**: Also add clearing logic — `hooks_fired` must be cleared at the START of each turn. In `RunHandle.start()` at `src/agentpool/orchestrator/run.py:253` (where `cancelled` is reset), add `run_ctx.hooks_fired.clear()`. In `_run_stream_once()` at `src/agentpool/agents/base_agent.py:1245`, add the same clearing at the start. Without this, turn 1's keys block turn 2+ hook firing.
     (b) Create `HookAwareTurn` mixin class in `src/agentpool/orchestrator/turn.py` (after the `Turn` ABC, around line 73). 
     **CRITICAL (Metis G2.1 — MRO)**: `HookAwareTurn` is a pure mixin that does NOT inherit from `Turn`. Usage: `class NativeTurn(HookAwareTurn, Turn)` and `class ACPTurn(HookAwareTurn, Turn)`. This ensures `Turn`'s abstract methods are resolved by the host class, while `HookAwareTurn`'s concrete methods are mixed in.
     **CRITICAL (Metis G7.2 — env access)**: `HookAwareTurn` must NOT access `self._agent` (ACPTurn doesn't have it). Instead, add an abstract property `_hook_env: ExecutionEnvironment | None` to `HookAwareTurn` that host classes must implement. `NativeTurn` returns `self._agent.env`, `ACPTurn` returns `self._agent_env` (new attribute, set from `ACPAgent.env` in `create_turn()`).
     **CRITICAL (Momus M3 — agent_name/prompt sourcing)**: Add abstract properties `_hook_agent_name: str` and `_hook_prompt: str` to `HookAwareTurn`. `NativeTurn` returns `self._agent.name` and `str(self._prompts)`. `ACPTurn` returns `self._agent_name` and `str(self._prompts)`. These are needed to construct `HookInput` with `agent_name` and `prompt` fields.
     **CRITICAL (Metis G1.1/G1.2 — hooks attribute)**: `HookAwareTurn` declares `_hooks: AgentHooks | None = None` as a class-level type annotation. Host classes set it in `__init__` via a new `hooks` parameter.
     The mixin provides:
       - `async def _fire_pre_turn_hooks(self) -> HookResult | None` — checks `"pre_turn" not in self._run_ctx.hooks_fired`, fires `self._hooks.run_pre_turn_hooks(agent_name=self._hook_agent_name, prompt=self._hook_prompt, session_id=self._run_ctx.session_id, env=self._hook_env)` with env from `self._hook_env`, adds `"pre_turn"` to `hooks_fired` set. Returns the `HookResult`.
       - `async def _fire_post_turn_hooks(self, result: ChatMessage | None) -> HookResult | None` — checks `"post_turn" not in hooks_fired`, fires `self._hooks.run_post_turn_hooks(...)` with `result` and `duration_ms`, adds to set. **CRITICAL (Momus C1 — post_turn in finally)**: This method MUST be called in a `finally` block in the host class's `execute()` method, NOT after `_final_message` is set. Pass `self._final_message` (which may be `None` if the turn errored or was cancelled before completion). This ensures post_turn fires even on error/cancellation, per spec requirement: "post_turn hooks SHALL fire even if the turn was cancelled or errored."
       - `async def _fire_pre_tool_hooks(self, tool_name, tool_input, tool_call_id: str | None = None) -> HookResult | None` — fires `self._hooks.run_pre_tool_hooks(...)`. Returns result for deny-checking. **CRITICAL (Momus M7)**: Guard key is `f"pre_tool_use:{tool_call_id}"` if `tool_call_id` is available, else `"pre_tool_use:{tool_name}"`. This prevents double-firing between ACP `request_permission()` and `ACPTurn.execute()` for the same tool call.
       - `async def _fire_post_tool_hooks(self, tool_name, tool_input, tool_output, duration_ms, tool_call_id: str | None = None) -> HookResult | None` — fires `self._hooks.run_post_tool_hooks(...)`. Guard key is `f"post_tool_use:{tool_call_id}"` or `f"post_tool_use:{tool_name}"`.
       - All methods are no-ops if `self._hooks` is None.
    **CRITICAL (Metis G2.2 — deny behavior)**: When `_fire_pre_turn_hooks()` returns a result with `decision="deny"`, the host class's `execute()` must: (1) set `self._run_ctx.cancelled = True`, (2) construct an empty cancel message, (3) yield `StreamCompleteEvent(cancelled=True)`, (4) return early. This matches the existing pattern at `base_agent.py:1336-1347`.
    Do NOT make HookAwareTurn inherit from Turn (it's a mixin). Do NOT call hooks directly in the mixin — always delegate to `self._hooks.run_*_turn_hooks()`. Do NOT change the Turn ABC. Do NOT use `getattr` or `hasattr` — use typed access via declared class variables and abstract properties.
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 4,5,6,7
  References:
    - `src/agentpool/orchestrator/turn.py:19-73` — `Turn` ABC class, `execute()` abstract method at line 36, properties at lines 47-73
    - `src/agentpool/agents/context.py:76-180` — `AgentRunContext` dataclass, `cancelled` at line 94, `run_id` at line 97, `session_id` at line 112
    - `src/agentpool/hooks/agent_hooks.py:30-186` — `AgentHooks` with `run_pre_turn_hooks()`, `run_post_turn_hooks()`, `run_pre_tool_hooks()`, `run_post_tool_hooks()` (renamed in Todo 1)
    - `src/agentpool/hooks/base.py:20-68` — `HookInput` and `HookResult` TypedDicts
    - `src/agentpool/agents/base_agent.py:1336-1347` — existing deny pattern: sets `run_ctx.cancelled = True`, creates cancel message, yields `StreamCompleteEvent(cancelled=True)`
  Acceptance criteria: `uv run ruff check src/agentpool/orchestrator/turn.py src/agentpool/agents/context.py` passes. `uv run mypy src/agentpool/orchestrator/turn.py src/agentpool/agents/context.py` passes.
  QA scenarios:
    - Happy: `uv run pytest tests/ -k "context" -vv` passes
    - Failure: `uv run mypy src/agentpool/orchestrator/turn.py` — no type errors
    - Evidence: `.omo/evidence/task-3-unify-hook-system.txt`
  Commit: Y | feat(hooks): add HookAwareTurn mixin and hooks_fired guard to AgentRunContext

- [x] 4. Integrate HookAwareTurn into NativeTurn
  What to do / Must NOT do: 
     **CRITICAL (Metis G1.1 — hooks not stored)**: `NativeTurn.__init__` at `src/agentpool/agents/native_agent/turn.py:68-93` does NOT currently have a `hooks` parameter. Add `hooks: AgentHooks | None = None` parameter to `__init__` and set `self._hooks = hooks`.
     **CRITICAL (Metis G7.2 — env access)**: Implement `_hook_env` property: `return self._agent.env`.
     **CRITICAL (Momus M3 — agent_name/prompt)**: Implement `_hook_agent_name` property: `return self._agent.name`. Implement `_hook_prompt` property: `return str(self._prompts)`.
     Make `NativeTurn` inherit from `HookAwareTurn` (MRO: `class NativeTurn(HookAwareTurn, Turn)`).
     In `NativeTurn.execute()` at `src/agentpool/agents/native_agent/turn.py:95-349`:
     - At the START of `execute()` (before line 105): call `pre_turn_result = await self._fire_pre_turn_hooks()`. **If `pre_turn_result` has `decision="deny"`**: set `self._run_ctx.cancelled = True`, construct empty cancel message, yield `StreamCompleteEvent(cancelled=True)`, return early (matching pattern at `base_agent.py:1336-1347`).
     - **CRITICAL (Momus C1 — post_turn in finally)**: Call `await self._fire_post_turn_hooks(self._final_message)` in the `finally` block (line 271-273). `_final_message` may be `None` if the turn errored before completion — that's acceptable, pass it as-is.
     - **CRITICAL (Momus C5 — native tool hooks delegated to _ToolInterceptCapability)**: Do NOT call `_fire_pre_tool_hooks()` or `_fire_post_tool_hooks()` from `NativeTurn.execute()`. Native tool hooks are already handled by `_ToolInterceptCapability` in `NativeAgentHookManager` (lines 274, 346). Calling them from HookAwareTurn too would cause double-firing. HookAwareTurn's tool hook methods exist for ACP only.
     **CRITICAL (Momus T1 — wrong file for create_turn)**: Update `NativeAgent.create_turn()` at `src/agentpool/agents/native_agent/agent.py:1201-1225` (NOT `base_agent.py:1220-1225` which is `_native_runner`) to pass `hooks=self.hooks` to `NativeTurn` constructor.
     Do NOT remove the old hook firing in `base_agent.py` (that's guarded in Todo 6). Do NOT change the `agentlet.iter()` or `agent_run.next()` loop structure. Do NOT use `getattr` or `hasattr`.
  Parallelization: Wave 1 | Blocked by: 1,2,3 | Blocks: 7
  References:
     - `src/agentpool/agents/native_agent/turn.py:51` — `NativeTurn(Turn)` class declaration → change to `NativeTurn(HookAwareTurn, Turn)`
     - `src/agentpool/agents/native_agent/turn.py:68-93` — `__init__`, sets `self._run_ctx = run_ctx` at line 89 → add `hooks` param and `self._hooks = hooks`
     - `src/agentpool/agents/native_agent/turn.py:95-349` — `execute()` method, `agentlet` at line 105, `agentlet.iter()` at line 161, `finally` block at 271-273, `_final_message` set ~line 339
     - `src/agentpool/agents/native_agent/agent.py:1201-1225` — `NativeAgent.create_turn()` method → add `hooks=self.hooks` to `NativeTurn()` call (NOT base_agent.py:1220-1225 which is `_native_runner`)
     - `src/agentpool/agents/base_agent.py:264` — `self.hooks = hooks` attribute assignment in `__init__` (NOT a @property)
     - `src/agentpool/agents/base_agent.py:1336-1347` — existing deny pattern to follow for pre_turn deny
     - `src/agentpool/orchestrator/turn.py` — `HookAwareTurn` mixin (created in Todo 3)
     - `src/agentpool/agents/context.py:76` — `AgentRunContext` with `hooks_fired` field (added in Todo 3)
  Acceptance criteria: `uv run ruff check src/agentpool/agents/native_agent/turn.py` passes. `uv run mypy src/agentpool/agents/native_agent/turn.py` passes. `uv run pytest tests/ -k "native" -k "turn" -vv` passes.
  QA scenarios:
    - Happy: `uv run pytest tests/ -k "native" -k "turn" -vv` passes
    - Failure: `uv run mypy src/agentpool/agents/native_agent/turn.py` — no type errors
    - Evidence: `.omo/evidence/task-4-unify-hook-system.txt`
  Commit: Y | feat(hooks): integrate HookAwareTurn into NativeTurn with hooks parameter and deny handling

- [x] 5. Integrate HookAwareTurn into ACPTurn
  What to do / Must NOT do: 
     **CRITICAL (Metis G1.2 — hooks not stored)**: `ACPTurn.__init__` at `src/agentpool/agents/acp_agent/turn.py:100-114` does NOT currently have a `hooks` parameter. Add `hooks: AgentHooks | None = None` parameter to `__init__` and set `self._hooks = hooks`.
     **CRITICAL (Metis G7.2 — env access)**: Add `env: ExecutionEnvironment | None = None` parameter to `__init__`, store as `self._agent_env`. Implement `_hook_env` property: `return self._agent_env`.
     **CRITICAL (Momus M3 — agent_name/prompt)**: Implement `_hook_agent_name` property: `return self._agent_name`. Implement `_hook_prompt` property: `return str(self._prompts)`.
     Make `ACPTurn` inherit from `HookAwareTurn` (MRO: `class ACPTurn(HookAwareTurn, Turn)`).
     In `ACPTurn.execute()` at `src/agentpool/agents/acp_agent/turn.py:116-196`:
     - At the START of `execute()` (before line 139): call `pre_turn_result = await self._fire_pre_turn_hooks()`. If `decision="deny"`: set `self._run_ctx.cancelled = True`, construct cancel message, yield `StreamCompleteEvent(cancelled=True)`, return early.
     - **CRITICAL (Momus C1 — post_turn in finally)**: Call `await self._fire_post_turn_hooks(self._final_message)` in a `finally` block at the end of `execute()`. `_final_message` may be `None` if the turn errored — pass as-is. This ensures post_turn fires even on error/cancellation, per spec.
     - When a tool-related ACP event is detected in the streaming loop (line 152-154): call `await self._fire_pre_tool_hooks(tool_name, tool_input, tool_call_id)` and `await self._fire_post_tool_hooks(...)` as advisory hooks. **CRITICAL (Momus M7)**: Use `tool_call_id`-scoped guard keys (`f"pre_tool_use:{tool_call_id}"`) to prevent double-firing between `request_permission()` and `ACPTurn.execute()` for the same tool call. These are advisory — they log and augment but cannot prevent the external agent from calling tools.
     **CRITICAL (Metis G1.2 — create_turn update)**: Update `ACPAgent.create_turn()` at `src/agentpool/agents/acp_agent/acp_agent.py:632-660` to pass `hooks=self.hooks` and `env=self.env` to `ACPTurn` constructor.
     **CRITICAL (Metis G1.4 — run_ctx access for permission blocking)**: In `src/agentpool/agents/acp_agent/client_handler.py:208-217`, `request_permission()` does NOT have direct `run_ctx` access. Access it via `self._agent.get_active_run_context()` (confirmed to exist at `base_agent.py:715`). Fire `pre_tool_hooks` BEFORE the `auto_approve` check at line 217. If any hook returns `decision="deny"`, block the permission request (return denied response). Use `tool_call_id`-scoped guard key to prevent double-firing with `ACPTurn.execute()`.
     Do NOT change the ACP protocol messages. Do NOT change `ACPClientProtocol`. ACP tool hooks are advisory — they log and augment but cannot prevent the external agent from calling tools (only permission blocking can prevent). Do NOT use `getattr` or `hasattr`.
  Parallelization: Wave 1 | Blocked by: 1,2,3 | Blocks: 7
  References:
    - `src/agentpool/agents/acp_agent/turn.py:34` — `ACPClientProtocol` Protocol (DO NOT CHANGE)
    - `src/agentpool/agents/acp_agent/turn.py:92` — `ACPTurn(Turn)` class → change to `ACPTurn(HookAwareTurn, Turn)`
    - `src/agentpool/agents/acp_agent/turn.py:100-114` — `__init__`, sets `self._run_ctx = run_ctx` at line 112 → add `hooks` and `env` params
    - `src/agentpool/agents/acp_agent/turn.py:116-196` — `execute()` method, `prompt()` at line 139, `stream_events()` at line 152, event yield at line 154
    - `src/agentpool/agents/acp_agent/acp_agent.py:632-660` — `create_turn()` method → add `hooks=self.hooks, env=self.env` to `ACPTurn()` call
     - `src/agentpool/agents/acp_agent/acp_agent.py:157,159,180` — `auto_approve`, `hooks` params
     - `src/agentpool/agents/acp_agent/client_handler.py:208-217` — `request_permission()`, auto_approve check at line 217 → fire hooks before this check
     - `src/agentpool/agents/base_agent.py:264` — `self.hooks = hooks` attribute assignment in `__init__` (NOT a @property)
     - `src/agentpool/agents/base_agent.py:715` — `get_active_run_context()` method (confirmed to exist)
  Acceptance criteria: `uv run ruff check src/agentpool/agents/acp_agent/` passes. `uv run mypy src/agentpool/agents/acp_agent/` passes.
  QA scenarios:
    - Happy: `uv run pytest tests/ -k "acp" -k "turn" -vv` passes
    - Failure: `uv run mypy src/agentpool/agents/acp_agent/turn.py` — no type errors
    - Evidence: `.omo/evidence/task-5-unify-hook-system.txt`
  Commit: Y | feat(hooks): integrate HookAwareTurn into ACPTurn with hooks parameter, advisory tool hooks, and permission blocking

- [x] 6. Guard old hook firing path in base_agent.py
  What to do / Must NOT do: In `src/agentpool/agents/base_agent.py`, wrap the old hook firings at lines 1329 and 1392 with a check: `if "pre_turn" not in self._run_ctx.hooks_fired: ...` and `if "post_turn" not in self._run_ctx.hooks_fired: ...`. This prevents double-firing when both the old path (`_run_stream_once`) and new path (`Turn.execute`) are active. The old path fires when agents are used standalone (not through SessionPool). Do NOT remove the old hook firing code — it's removed in Phase 4 (Todo 11/12). Do NOT change `_run_stream_once` structure.
  Parallelization: Wave 1 | Blocked by: 3 | Blocks: 7
  References:
    - `src/agentpool/agents/base_agent.py:1245` — `_run_stream_once()` method (standalone path)
    - `src/agentpool/agents/base_agent.py:1329` — `pre_run_result = await self.hooks.run_pre_run_hooks(...)` — needs guard + rename call to `run_pre_turn_hooks()`
    - `src/agentpool/agents/base_agent.py:1392` — `await self.hooks.run_post_run_hooks(...)` — needs guard + rename call to `run_post_turn_hooks()`
    - `src/agentpool/agents/context.py:76` — `AgentRunContext.hooks_fired` (added in Todo 3)
  Acceptance criteria: `uv run ruff check src/agentpool/agents/base_agent.py` passes. `uv run mypy src/agentpool/agents/base_agent.py` passes.
  QA scenarios:
    - Happy: `uv run pytest tests/ -k "base_agent" -vv` passes
    - Failure: Verify no double-firing by running a test that goes through both paths and checking hooks_fired set contains each event only once
    - Evidence: `.omo/evidence/task-6-unify-hook-system.txt`
  Commit: Y | fix(hooks): guard old hook firing path against double-firing with hooks_fired set

- [x] 7. Write Phase 1 test suite (3-tier with smoke test matrix)
  What to do / Must NOT do: Create comprehensive tests:
    - **Unit** (`tests/hooks/test_hook_aware_turn.py`): Test `HookAwareTurn` mixin in isolation. Create a minimal host class that inherits `HookAwareTurn`, inject mock `AgentHooks` with mock `Hook` objects. Verify: (a) all 4 hooks fire in correct order, (b) `hooks_fired` set prevents double-firing, (c) hooks are no-op when `self._hooks` is None, (d) pre_turn fires before execute body, (e) post_turn fires in `finally` block even when execute raises, (f) `tool_call_id`-scoped guard keys prevent double-firing between `request_permission()` and `ACPTurn.execute()`.
    - **Integration** (`tests/agents/native_agent/test_native_turn_hooks.py`): Test `NativeTurn` with `TestModel` from pydantic-ai. Verify hooks fire during turn execution with a tool call. Test that `pre_turn` → tool call (via `_ToolInterceptCapability`) → `post_turn` order is maintained. Verify tool hooks are NOT fired from `HookAwareTurn` for native agents (they're handled by `_ToolInterceptCapability`).
    - **Integration** (`tests/agents/acp_agent/test_acp_turn_hooks.py`): Test `ACPTurn` with a fake `ACPClientProtocol` implementation. Verify hooks fire during ACP turn execution. Test permission blocking: a `deny` hook result blocks permission. Test advisory tool hooks fire during streaming.
    - **E2E** (`tests/orchestrator/test_session_pool_hooks.py`): Test the SessionPool path via `RunHandle.start()` at `run.py:308`. This is the regression test — verify hooks fire when going through the session pool, which was the original bug. Create an agent, create a session, send a request, verify all hooks fired. **CRITICAL**: Test `hooks_fired` clearing between turns — send 2 requests in sequence and verify turn 2 hooks fire (not blocked by turn 1's `hooks_fired` keys).
    - **CRITICAL (Momus M8 — smoke test matrix)**: Create `tests/hooks/test_hook_smoke_matrix.py` with a 16-cell test grid: {pre_turn, post_turn, pre_tool_use, post_tool_use} × {native standalone, native SessionPool, ACP standalone, ACP SessionPool}. Each cell verifies the corresponding hook type fires in the corresponding mode. Use `TestModel` for native, fake `ACPClientProtocol` for ACP. Mark ACP SessionPool tests with `@pytest.mark.skipif` if `ACPAgentAPI` gap prevents running them (Metis G4.5).
    Do NOT use real LLM calls — use `TestModel` or mocks. Do NOT test hook types (CallableHook, CommandHook, PromptHook) — those have existing tests.
  Parallelization: Wave 1 | Blocked by: 3,4,5,6 | Blocks: 8
  References:
    - `src/agentpool/orchestrator/turn.py` — `HookAwareTurn` mixin (Todo 3)
    - `src/agentpool/agents/native_agent/turn.py` — `NativeTurn` (Todo 4)
    - `src/agentpool/agents/acp_agent/turn.py` — `ACPTurn` (Todo 5)
    - `src/agentpool/orchestrator/run.py:197-308` — `RunHandle.start()` and `turn.execute()` call
    - `tests/conftest.py` — test fixtures, TestModel setup
  Acceptance criteria: `uv run pytest tests/hooks/test_hook_aware_turn.py tests/agents/native_agent/test_native_turn_hooks.py tests/agents/acp_agent/test_acp_turn_hooks.py tests/orchestrator/test_session_pool_hooks.py -vv` all pass.
  QA scenarios:
    - Happy: All 4 test files pass with `uv run pytest -vv`
    - Failure: Remove HookAwareTurn integration from NativeTurn — E2E test should fail (hooks don't fire)
    - Evidence: `.omo/evidence/task-7-unify-hook-system.txt`
  Commit: Y | test(hooks): add 3-tier test suite for HookAwareTurn (unit, integration, E2E)

- [x] 8. Deprecate as_capability() in AgentHooks and NativeAgentHookManager
  What to do / Must NOT do: Add `DeprecationWarning` to `as_capability()` method in `src/agentpool/hooks/agent_hooks.py:307-335` and `src/agentpool/agents/native_agent/hook_manager.py:470-495`. Warning message: "as_capability() is deprecated; hooks now fire via HookAwareTurn in Turn.execute(). Will be removed in v0.5.0." Do NOT remove the methods. Do NOT change their behavior — they still work (poorly) but now warn.
  Parallelization: Wave 2 | Blocked by: 7 | Blocks: 10
  References:
    - `src/agentpool/hooks/agent_hooks.py:307-335` — `as_capability()` method with `_wrap_*` helpers
    - `src/agentpool/agents/native_agent/hook_manager.py:470-495` — `as_capability()` method with stripping hack (lines 483-486 strip `_registry` entries)
  Acceptance criteria: `uv run pytest tests/ -k "capability" -W error::DeprecationWarning -vv` — tests that call `as_capability()` raise DeprecationWarning.
  QA scenarios:
    - Happy: `uv run pytest tests/ -k "hook" -vv` passes (no warnings from non-deprecated paths)
    - Failure: `uv run python -c "import warnings; warnings.simplefilter('error'); from agentpool.hooks.agent_hooks import AgentHooks; AgentHooks().as_capability()"` raises DeprecationWarning
    - Evidence: `.omo/evidence/task-8-unify-hook-system.txt`
  Commit: Y | deprecate(hooks): add DeprecationWarning to as_capability() in AgentHooks and NativeAgentHookManager

- [x] 9. Deprecate old field aliases in HooksConfig
  What to do / Must NOT do: If not already done in Todo 2, ensure that using `pre_run`/`post_run` as YAML keys emits a `DeprecationWarning`. This may already be implemented in Todo 2's alias validator — verify and strengthen if needed. Add a deprecation notice to the docstrings. Do NOT remove the aliases.
  Parallelization: Wave 2 | Blocked by: 7 | Blocks: 12
  References:
    - `src/agentpool_config/hooks.py` — `HooksConfig` with alias support (Todo 2)
  Acceptance criteria: `uv run pytest tests/ -k "config" -W error::DeprecationWarning -vv` — using old field names raises warning.
  QA scenarios:
    - Happy: `uv run pytest tests/ -k "config" -vv` passes
    - Failure: Loading a YAML with `pre_run:` key raises DeprecationWarning
    - Evidence: `.omo/evidence/task-9-unify-hook-system.txt`
  Commit: Y | deprecate(hooks): strengthen deprecation warnings for old HooksConfig field aliases

- [x] 10. Slim NativeAgentHookManager (remove _ToolInterceptCapability and stripping hack)
  What to do / Must NOT do: In `src/agentpool/agents/native_agent/hook_manager.py` (661 lines → target ~200):
    - **CRITICAL (Momus M10 — subclass check)**: Before removing methods, search the codebase for any classes that inherit from `NativeAgentHookManager`. If subclasses exist and override removed methods, add thin shim methods that emit `DeprecationWarning` and delegate to the new path (via `HookAwareTurn`). Use `grep -r "NativeAgentHookManager" src/ --include="*.py" | grep "class.*NativeAgentHookManager"` to find subclasses.
    - Remove `_ToolInterceptCapability` class entirely (it was a workaround for as_capability() being broken) — BUT only if native tool hooks are now handled by `HookAwareTurn`'s `_fire_pre_tool_hooks()`/`_fire_post_tool_hooks()` being called from `_ToolInterceptCapability`'s replacement or from the existing code path. Verify that native tool hooks still fire after removal by running `uv run pytest tests/ -k "tool_hook" -vv`.
    - Remove the stripping hack in `as_capability()` (lines 483-486 that set `base_hooks._registry[...] = []`)
    - Remove delegate methods that are now handled by HookAwareTurn: `run_pre_run_hooks()`, `run_post_run_hooks()`, `run_pre_tool_hooks()`, `run_post_tool_hooks()` (lines 497-627) — keep deprecated alias wrappers if subclasses need them (see subclass check above).
    - Keep: `__init__`, lifecycle management, hook loading from config, hook matching
    - The `as_capability()` method should now either: (a) return None and emit DeprecationWarning, or (b) be removed entirely if no code path still calls it
    Do NOT remove the `NativeAgentHookManager` class itself. Do NOT change how hooks are loaded from config. Do NOT remove the `agent_hooks` property.
  Parallelization: Wave 3 | Blocked by: 8 | Blocks: 11
  References:
    - `src/agentpool/agents/native_agent/hook_manager.py:470-495` — `as_capability()` with stripping hack
    - `src/agentpool/agents/native_agent/hook_manager.py:497-627` — delegate methods (run_pre_run_hooks etc.)
    - `src/agentpool/agents/native_agent/hook_manager.py:274,346` — `_ToolInterceptCapability` calls to run_pre_tool_hooks/run_post_tool_hooks
  Acceptance criteria: `uv run ruff check src/agentpool/agents/native_agent/hook_manager.py` passes. `uv run mypy src/agentpool/agents/native_agent/hook_manager.py` passes. File is ~200 lines. `uv run pytest tests/ -k "hook" -vv` passes.
  QA scenarios:
    - Happy: `uv run pytest tests/ -k "hook" -vv` passes
    - Failure: `wc -l src/agentpool/agents/native_agent/hook_manager.py` — should be ~200 lines (max 250)
    - Evidence: `.omo/evidence/task-10-unify-hook-system.txt`
  Commit: Y | refactor(hooks): slim NativeAgentHookManager from 661 to ~200 LOC, remove _ToolInterceptCapability

- [x] 11. Dead code cleanup (remove unused imports, functions, variables, broken tests)
  What to do / Must NOT do: After slimming in Todo 10, search for and remove:
    - Unused imports in `src/agentpool/agents/native_agent/hook_manager.py`
    - Unused imports in `src/agentpool/hooks/agent_hooks.py` (if `_wrap_*` helpers are no longer needed)
    - Any dead code paths in `src/agentpool/agents/base_agent.py` that referenced old hook manager methods
    - Any dead code in `src/agentpool/orchestrator/` that referenced old hook patterns
    - **CRITICAL (Momus M9 — broken test cleanup)**: Search for and remove/update tests that assert hooks DON'T fire in SessionPool mode (these tests validated the bug) or that validate the stripping hack behavior. Use `grep -r "pre_run\|post_run\|as_capability\|stripping" tests/ --include="*.py"` to find affected tests (~22 files reference old names). Update tests to assert hooks DO fire in SessionPool mode. Remove tests that validated the stripping hack.
    - **CRITICAL (Momus L12)**: Verify `__init__.py` exports are correct — check that `src/agentpool/hooks/__init__.py` exports the new method names and that no imports of old names remain.
    Run `uv run ruff check --select F401 src/` to find unused imports. Run `uv run ruff check --select F811 src/` to find redefined names.
    Do NOT remove code that is still referenced. Do NOT remove deprecated aliases (those are removed in Todo 12).
  Parallelization: Wave 3 | Blocked by: 10 | Blocks: 12
  References:
    - `src/agentpool/agents/native_agent/hook_manager.py` — after slimming
    - `src/agentpool/hooks/agent_hooks.py` — may have unused `_wrap_*` helpers
    - `src/agentpool/agents/base_agent.py` — may reference old hook manager methods
  Acceptance criteria: `uv run ruff check src/` passes clean (no F401, F811). `uv run mypy src/` passes clean.
  QA scenarios:
    - Happy: `uv run ruff check src/` passes clean
    - Failure: `uv run ruff check --select F401,F811 src/` returns no findings
    - Evidence: `.omo/evidence/task-11-unify-hook-system.txt`
  Commit: Y | cleanup(hooks): remove dead code after NativeAgentHookManager slimming

- [x] 12. Remove deprecated APIs entirely (v0.5.0 breaking)
  What to do / Must NOT do: This is the breaking change phase:
    - Remove `as_capability()` method from `AgentHooks` in `src/agentpool/hooks/agent_hooks.py:307-335`
    - Remove `as_capability()` method from `NativeAgentHookManager` in `src/agentpool/agents/native_agent/hook_manager.py`
    - Remove `_wrap_before_run()`, `_wrap_after_run()`, `_wrap_before_tool_execute()`, `_wrap_after_tool_execute()` helpers (lines 337-435)
    - Remove `pre_run`/`post_run` field aliases from `HooksConfig` in `src/agentpool_config/hooks.py` (only `pre_turn`/`post_turn` remain)
    - Remove deprecated method aliases on `AgentHooks` (`run_pre_run_hooks()`, `run_post_run_hooks()` wrappers added in Todo 1)
    - **CRITICAL (Momus C4 — native only for _run_stream_once)**: Remove old hook firing in `src/agentpool/agents/base_agent.py:1329,1392` for NATIVE agents ONLY (the guarded path — now fully replaced by HookAwareTurn). Do NOT remove ACP standalone hook firing — `ACPAgent._stream_events()` still relies on it until a future refactoring moves ACP standalone to use `ACPTurn.execute()` with HookAwareTurn. Wrap the removal with a type check or conditional that only applies to native agents.
    - Remove the `hooks_fired` guard checks (no longer needed since old path is gone) — for native only
    - Remove `hooks_fired` field from `AgentRunContext` if no longer used (or keep if useful for other purposes — ACP still uses it)
    Do NOT remove `pre_tool_use`/`post_tool_use` — those names are unchanged. Do NOT remove the `hooks` parameter from agent constructors. Do NOT remove ACP standalone hook firing.
  Parallelization: Wave 4 | Blocked by: 9,11 | Blocks: 13
  References:
    - `src/agentpool/hooks/agent_hooks.py:307-435` — `as_capability()` and `_wrap_*` helpers
    - `src/agentpool/agents/native_agent/hook_manager.py` — `as_capability()` (already slimmed in Todo 10)
    - `src/agentpool_config/hooks.py` — deprecated aliases
    - `src/agentpool/agents/base_agent.py:1329,1392` — old hook firing (guarded in Todo 6)
    - `src/agentpool/agents/context.py:76` — `hooks_fired` field
  Acceptance criteria: `uv run ruff check src/` passes clean. `uv run mypy src/` passes clean. `uv run pytest -vv` passes clean. No `DeprecationWarning` from hook code. grep for `pre_run` and `post_run` in `src/` returns no matches (except in unrelated contexts).
  QA scenarios:
    - Happy: `uv run pytest -vv` passes clean
    - Failure: `grep -r "pre_run\|post_run" src/agentpool/hooks/ src/agentpool_config/hooks.py` returns no matches
    - Evidence: `.omo/evidence/task-12-unify-hook-system.txt`
  Commit: Y | breaking(hooks): remove deprecated as_capability(), old field aliases, and old hook firing path

- [ ] 13. Update openspec tasks.md and migration documentation
  What to do / Must NOT do: 
    (a) Update `openspec/changes/unify-hook-system/tasks.md` to mark all completed tasks as `[x]`. Add any new tasks discovered during implementation. Update the change status in `.openspec.yaml` if all tasks are complete.
    (b) **CRITICAL (Momus M11 — migration docs)**: Update `AGENTS.md` Hooks & Events System section to reflect the rename (pre_run→pre_turn, post_run→post_turn) and HookAwareTurn architecture. Add a migration guide section documenting: (1) YAML config rename `pre_run:`→`pre_turn:`, (2) `as_capability()` removed — hooks now fire via HookAwareTurn, (3) v0.5.0 breaking changes. Add v0.5.0 release notes draft.
    Do NOT update unrelated AGENTS.md sections.
  Parallelization: Wave 5 | Blocked by: 12 | Blocks: 14
  References:
    - `openspec/changes/unify-hook-system/tasks.md` — 80+ tasks across 11 sections
    - `openspec/changes/unify-hook-system/.openspec.yaml` — metadata
  Acceptance criteria: All completed tasks in `tasks.md` are marked `[x]`. File is valid markdown.
  QA scenarios:
    - Happy: `grep -c "\[ \]" openspec/changes/unify-hook-system/tasks.md` returns 0 (or only future-work items)
    - Evidence: `.omo/evidence/task-13-unify-hook-system.txt`
  Commit: Y | docs(hooks): update openspec tasks.md marking completed tasks

- [ ] 14. Full test suite + lint + mypy validation
  What to do / Must NOT do: Run the complete validation suite:
    - `uv run pytest -vv` (all tests pass)
    - `uv run pytest -m unit,integration -vv` (unit and integration tests pass)
    - `uv run ruff check src/` (no lint errors)
    - `uv run ruff format --check src/` (formatting is clean)
    - `uv run --no-group docs mypy src/` (no type errors)
    - `uv run pytest -W error::DeprecationWarning -vv` (no deprecation warnings from hook code)
    Fix any issues found. Do NOT suppress warnings. Do NOT skip tests.
  Parallelization: Wave 5 | Blocked by: 13 | Blocks: F1-F4
  References:
    - All modified files
  Acceptance criteria: All commands pass clean. No errors, no warnings.
  QA scenarios:
    - Happy: All 4 commands pass
    - Failure: Any command fails — fix and re-run
    - Evidence: `.omo/evidence/task-14-unify-hook-system.txt`
  Commit: N | (part of final commit or separate validation commit)

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit — verify all openspec requirements met: read `openspec/changes/unify-hook-system/specs/` and confirm each requirement has a passing test
- [ ] F2. Code quality review — `uv run ruff check src/` + `uv run mypy src/` pass clean, no dead code, no TODOs left
- [ ] F3. Real manual QA — `uv run pytest -m unit,integration -vv` passes, no warnings from hook code, hooks fire in SessionPool path
- [ ] F4. Scope fidelity — no changes outside scope; Hook ABC, _run_hooks, HookInput/HookResult, EventBus, graph architecture unchanged

## Commit strategy
- One commit per todo (12 implementation commits + 1 docs commit + 1 validation)
- Commit types: `refactor(hooks)` for renames/slim/cleanup, `feat(hooks)` for HookAwareTurn, `fix(hooks)` for guard, `deprecate(hooks)` for deprecation, `breaking(hooks)` for Phase 4 removal, `test(hooks)` for tests, `docs(hooks)` for docs
- Each commit message references: `refs: openspec/changes/unify-hook-system`

## Success criteria
1. All 4 hook types fire reliably from `Turn.execute()` for both `NativeTurn` and `ACPTurn`
2. Hooks fire in the SessionPool path (`RunHandle.start()` → `turn.execute()`) — the original bug is fixed
3. `hooks_fired` set prevents double-firing during migration (removed in Phase 4 when old path is removed)
4. ACP agents have advisory tool hooks and blocking permission hooks
5. `NativeAgentHookManager` is ~200 LOC (down from 661)
6. `as_capability()` and old field aliases are removed (v0.5.0)
7. `uv run pytest` passes clean
8. `uv run ruff check src/` passes clean
9. `uv run mypy src/` passes clean
