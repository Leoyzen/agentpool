## Context

The `eliminate-pool-level-agents` branch (HEAD `539539aae`, 1378 files changed) builds on the `feat/run-turn-separation` branch's turn separation architecture but removes pool-level agent registration. The `feat/run-turn-separation` branch already fixed 10 root causes (RC-1 through RC-10) across 8 commits. However, the `eliminate-pool-level-agents` branch introduces regressions:

1. **RC-2 regression**: The `receive()` → `get()` fix (commit `e1b992fe4`) did not carry over to `global_routes.py:270`, causing 58 opencode_server test failures.
2. **RC-6 regression**: RunHandle cleanup callbacks (commit `3d6434aa8`) are not invoked, causing 5 `test_run_lifecycle.py` failures.
3. **New regressions from pool-level agent removal**: Worker/subagent tools (20 failures), base agent API (14 errors), executor (7 failures), cross-provider session lifecycle (7 failures), messaging/signal system (7 failures).

The full regression analysis is documented in `.omo/reports/regression-analysis-eliminate-pool-level-agents.md`.

## Goals / Non-Goals

**Goals:**
- Restore all previously-working behavior from `feat/run-turn-separation` (fix RC-2, RC-6 regressions)
- Adapt pool-dependent code paths to work without pool-level agent registry (new regressions)
- Clear all auto-fixable ruff/mypy issues
- Achieve ~99% test pass rate (excluding flaky performance benchmarks)

**Non-Goals:**
- New feature development
- Architecture redesign (the queue-based cancel scope fix from RC-1 is preserved)
- Fixing flaky performance benchmarks (TC-10) — these will be marked `@pytest.mark.flaky`
- Rewriting `test_concurrent_safety.py` (already skipped, logic verified independently)
- Upgrading external dependencies (pydantic-ai deprecation warnings from `llmling_models`)

## Decisions

### D1: EventBus stream API — direct method replacement

**Decision**: Replace `receive()` → `get()`, `receive_nowait()` → `get_nowait()`, `send_nowait()` → `put_nowait()` in all call sites.

**Rationale**: The EventBus stream type changed from a custom async stream to `asyncio.Queue`. The `asyncio.Queue` API uses `get()`/`get_nowait()`/`put()`/`put_nowait()`. This is a mechanical fix with zero risk.

**Alternatives considered**:
- *Add a `receive()` compatibility wrapper on `asyncio.Queue`*: Rejected — adds unnecessary indirection for a simple API rename.

### D2: RunHandle cleanup callbacks — restore from `feat/run-turn-separation`

**Decision**: Restore the cleanup callback invocation in `RunHandle.complete()` and `RunHandle.fail()` that was added in commit `3d6434aa8` but lost in the `eliminate-pool-level-agents` branch.

**Rationale**: The cleanup callback pattern is already designed and tested. The fix is a straightforward restoration.

### D3: Pool-less agent operation — runtime agent registry

**Decision**: Create a lightweight `RuntimeAgentRegistry` (dict-based) that subagent tools populate at tool-creation time. `get_or_create_session_agent()` checks the runtime registry before falling back to manifest lookup.

**Rationale**: This follows the recommendation from the `run-turn-separation` analysis (RC-7, Option C). When pool-level agent registration is removed, subagent tools still know their target agent at creation time — registering there is the earliest correct lifecycle stage.

**Alternatives considered**:
- *Lazy resolution with fallback to default config*: Rejected — masks configuration errors (typos silently create default agents).
- *Register to manifest at runtime*: Rejected — pollutes the immutable YAML manifest.

### D4: Base agent API — ephemeral session without pool

**Decision**: `BaseAgent` SHALL generate an ephemeral session ID when `agent_pool is None` in the `eliminate-pool-level-agents` architecture. The run context APIs (`get_active_run_context`, `is_turn_active`) SHALL work with a local `_run_context` variable when no pool session exists.

**Rationale**: The `eliminate-pool-level-agents` refactor removed pool-level agent storage but did not update the standalone path. The fix restores the ephemeral session pattern that existed before pool centralization.

### D5: Static analysis cleanup — batch auto-fix

**Decision**: Run `ruff check --fix src/` and `ruff format src/` to clear 21 auto-fixable ruff issues + 64 format issues. Remove 51 redundant `# type: ignore` comments identified by mypy. Do NOT fix optional-dependency import errors (composio, apprise) — these are guarded by try/except at runtime.

**Rationale**: Mechanical fixes with zero behavioral risk. Optional dependency imports are expected to fail when the package isn't installed.

## Risks / Trade-offs

- **[Risk] D3 RuntimeAgentRegistry duplicates manifest data** → Mitigation: Registry only stores agents created programmatically (not from YAML). YAML agents are still looked up in manifest. No duplication.
- **[Risk] D4 ephemeral session may leak if not cleaned up** → Mitigation: Use `async with` context manager pattern; session is scoped to the `run()` call.
- **[Risk] D5 ruff auto-fix may change semantics** → Mitigation: Only apply safe fixes (`--fix` without `--unsafe-fixes`). Review diff before committing.
- **[Risk] Fixing RC-2 may expose secondary issues masked by the crash** → Mitigation: Run full opencode_server test suite after fix to identify any newly-visible failures.
- **[Trade-off] Not fixing flaky performance tests** → Acceptable: these are timing-sensitive benchmarks that vary by hardware/CI load.
