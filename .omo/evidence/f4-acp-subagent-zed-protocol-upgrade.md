# Scope Fidelity Audit: acp-subagent-zed-protocol-upgrade

**Date**: 2026-06-27
**Auditor**: Oracle (scope fidelity)
**Commits reviewed**: HEAD~6..HEAD (6 commits)
**Verdict**: ✅ **APPROVE**

## Commits in scope

```
5a5990140 test: add 14 subagent tests for auto-emit, completion, cancellation, legacy guardrails
9e1c64565 feat(handler): recursive cancellation + test: update existing tests + add 18 child_done_events tests
bc77a5258 refactor: Wave 3 — call site cleanup, handler completion notification, RunExecutor + core.py updates
d28f9d80b feat(context): auto-emit SpawnSessionStart in create_child_session + add complete_background_task helper
e7be3dec4 refactor(context): replace pending_background_tasks with child_done_events dict
1914b4d74 chore: remove stale structured-work-channel source files after archive
```

## Files changed

```
src/agentpool/agents/context.py
src/agentpool/orchestrator/core.py
src/agentpool/orchestrator/run_executor.py
src/agentpool/resource_providers/pool.py
src/agentpool_commands/pool.py
src/agentpool_server/acp_server/event_converter.py
src/agentpool_server/acp_server/handler.py
src/agentpool_toolsets/builtin/subagent_tools.py
src/agentpool_toolsets/builtin/workers.py
tests/acp/test_event_converter_snapshots.py
tests/acp/test_meta_guardrails.py
tests/acp/test_zed_subagent_spawn.py
tests/orchestrator/test_background_task_wakeup.py
tests/orchestrator/test_child_done_events.py
tests/orchestrator/test_session_lifecycle.py
tests/servers/acp_server/test_subagent_events.py
tests/acp/__snapshots__/test_event_converter_snapshots.ambr
openspec/changes/structured-work-channel/* (archived)
```

## Scope Constraint Verification

### 1. team.py and teamrun.py NOT modified — ✅ PASS

```
$ git diff --stat HEAD~6 -- src/agentpool/delegation/team.py src/agentpool/delegation/teamrun.py
(no output — zero changes)
```

Neither file appears in the changed files list. Team orchestration code is untouched.

### 2. MAX_SUBAGENT_DEPTH=5 (not configurable) — ✅ PASS

**Location**: `src/agentpool/agents/context.py` line 58

```python
MAX_SUBAGENT_DEPTH: int = 5
"""Maximum nesting depth for subagent delegations."""
```

This is a **module-level constant**, not a Pydantic field, not a config model attribute, not
read from YAML. It is used directly in `create_child_session()` (line 343):

```python
if child_depth > MAX_SUBAGENT_DEPTH:
    raise SubagentDepthError(...)
```

No config plumbing exists to override this value. It is hardcoded.

### 3. No multi-turn reprompting — ✅ PASS

```
$ git diff HEAD~6 -- <all changed source files> | grep -i "reprompt\|re-prompt\|multi.turn"
NO_REPROMPT_FOUND
```

No reprompting logic was added. The `complete_background_task` helper (line 136) uses
`steer_callback` for single-message notification, not multi-turn reprompting.

### 4. No foreground-to-background promotion — ✅ PASS

```
$ git diff HEAD~6 -- <all changed source files> | grep -i "promote.*background\|foreground.*background\|to_background\|background_promotion"
+                    run_mode: Literal["foreground", "background"]
```

**Investigation**: This is a **local variable** in `event_converter.py` used purely as a
display label. It maps `spawn_mechanism` values to human-readable strings:

```python
run_mode: Literal["foreground", "background"]
match spawn_mechanism:
    case "task":
        run_mode = "background"
    case "spawn":
        run_mode = "foreground"
```

This is **labeling for ACP `_meta` display**, not actual foreground-to-background task
promotion. No code promotes a running foreground task to background execution.

### 5. No ACP Proxy Chains — ✅ PASS

```
$ git diff HEAD~6 -- <all changed source files> | grep -i "proxy.chain\|proxy_chain\|chained.proxy"
NO_PROXY_CHAIN_FOUND
```

No proxy chain code was introduced.

### 6. No ACP v2 migration — ✅ PASS

```
$ git diff HEAD~6 -- <all changed source files> | grep -i "acp.v2\|acp_v2\|v2.migration\|migrate.*v2"
NO_ACP_V2_FOUND
```

No ACP v2 migration code was introduced.

### 7. _meta not feature-flagged — ✅ PASS

```
$ git diff HEAD~6 -- src/agentpool_server/acp_server/event_converter.py | grep -i "flag\|enable\|disable\|toggle\|feature"
NO_FLAG_OR_TOGGLE_FOUND
```

The `_meta` field is always built via `_build_subagent_field_meta()` and always included
in the ACP event. There is no conditional gating, no feature flag, no environment variable
check. The `subagent_display_mode` config field ("legacy"/"zed") controls **rendering
strategy** (which events to emit), not whether `_meta` is included.

### 8. MAX_DELEGATION_DEPTH not renamed — ✅ PASS

**Location**: `src/agentpool/agents/exceptions.py` line 59

```python
MAX_DELEGATION_DEPTH: int = 10
```

```
$ git diff HEAD~6 -- src/agentpool/agents/exceptions.py
(empty — file not modified)
```

`exceptions.py` was not touched. `MAX_DELEGATION_DEPTH` remains at 10, unchanged,
as a separate concept from `MAX_SUBAGENT_DEPTH` (5).

### 9. subagent_display_mode options unchanged — ✅ PASS

**Location**: `src/agentpool_config/pool_server.py` line 175

```python
subagent_display_mode: Literal["legacy", "zed"] = Field(
    default="legacy",
    title="Subagent display mode",
)
```

```
$ git diff HEAD~6 -- src/agentpool_config/pool_server.py
POOL_SERVER_NOT_MODIFIED
```

`pool_server.py` was not modified. Only `"legacy"` and `"zed"` options exist. A
`field_validator` coerces deprecated values (`"inline"`, `"tool_box"`) to `"legacy"`
with a deprecation warning — this is pre-existing, not added by this change.

### 10. No getattr/hasattr in touched files — ✅ PASS

```
$ git diff HEAD~6 -- <all changed source files> | grep "^+.*getattr\|^+.*hasattr"
NO_ADDED_GETATTR_HASATTR
```

**Zero new getattr/hasattr calls were added.** In fact, the implementation **removed 7
getattr calls**, replacing them with type-safe direct attribute access:

Removed (context.py):
- `getattr(self.node, "agent_pool", None)` → `self.node.agent_pool`
- `getattr(self.run_ctx, "event_bus", None)` → `self.run_ctx.event_bus`

Removed (workers.py):
- `getattr(ctx.context, "run_ctx", None)` → direct access
- `getattr(agent_ctx, "session_id", "")` → direct access
- `getattr(event, "spawn_mechanism", None)` → direct access
- `getattr(worker, "agent_type", ...)` (x2) → direct access

**Pre-existing getattr calls** (not in scope, not modified):
- `context.py:255`: `getattr(self.agent, "tool_confirmation_mode", "per_tool")` — in
  `handle_confirmation()`, a pre-existing method not touched by this change.
- `handler.py:457`: `getattr(cmd, "supports_node", None)` — pre-existing.
- `workers.py:131,173,238`: Pre-existing getattr calls in code paths not modified.

## Summary

| # | Constraint | Status |
|---|---|---|
| 1 | team.py / teamrun.py not modified | ✅ PASS |
| 2 | MAX_SUBAGENT_DEPTH=5 hardcoded | ✅ PASS |
| 3 | No multi-turn reprompting | ✅ PASS |
| 4 | No foreground-to-background promotion | ✅ PASS |
| 5 | No ACP proxy chains | ✅ PASS |
| 6 | No ACP v2 migration | ✅ PASS |
| 7 | _meta not feature-flagged | ✅ PASS |
| 8 | MAX_DELEGATION_DEPTH unchanged (10) | ✅ PASS |
| 9 | subagent_display_mode: only "legacy"/"zed" | ✅ PASS |
| 10 | No new getattr/hasattr (7 removed) | ✅ PASS |

## VERDICT: ✅ APPROVE

All 10 scope constraints are satisfied. The implementation stayed within bounds:
- No team orchestration code was touched.
- Depth limits remain hardcoded constants (5 for subagent, 10 for delegation).
- No out-of-scope features (reprompting, promotion, proxy chains, ACP v2) were added.
- `_meta` is unconditional, not behind any flag.
- The implementation actually **improved type safety** by removing 7 getattr calls.
