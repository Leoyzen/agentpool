# AG-UI Bypass Audit Report

## Objective

Verify that AG-UI server routes do not depend on `_should_bypass_session_pool()` for correct operation. This audit determines whether the AG-UI bypass can be safely removed in Migration B (B5.1).

## Scope

All AG-UI server files under `src/agentpool_server/agui_server/`.

## Methodology

1. Identify all code paths that call `agent.run_stream()` or `agent.run()`
2. Check if the code path goes through `_should_bypass_session_pool()`
3. Verify whether the route sets the `_bypass_session_pool` ContextVar
4. Determine pass/fail verdict for each route

## Audit Results

### Files Analyzed

| File | Lines | Purpose |
|------|-------|---------|
| `server.py` | 139 | HTTP route definitions, request dispatch |
| `base_agent_adapter.py` | 183 | AG-UI protocol adapter for BaseAgent |
| `skill_tools.py` | ~100 | Skill command bridge for AG-UI |

### Route-by-Route Analysis

#### Route 1: Agent Streaming Endpoint (`server.py:96`)

**Code path:**
```python
# server.py:96-109
async def agent_handler(request, agent_name):
    from starlette.responses import JSONResponse
    pool_agent = self.pool.all_agents.get(agent_name)
    if pool_agent is None:
        return JSONResponse({"error": f"Agent {agent_name!r} not found"}, status_code=404)
    try:
        return await BaseAgentAGUIAdapter.dispatch_request(
            request, agent=pool_agent
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
```

**Downstream call:**
```python
# base_agent_adapter.py:125
async for agent_event in self.agent.run_stream(prompt, store_history=False):
```

**Bypass mechanism:**
```python
# base_agent_adapter.py:114-117
# NOTE: AG-UI uses direct agent.run_stream() to preserve its
# specialized event-handling path. BaseAgent._should_bypass_session_pool()
# detects AG-UI callers via stack inspection and bypasses SessionPool
# delegation, ensuring AG-UI events flow directly without interception.
```

**ContextVar check:** ❌ **NOT SET**
- The AG-UI adapter does NOT set `_bypass_session_pool` ContextVar
- It relies entirely on `_should_bypass_session_pool()` stack inspection
- The stack inspection checks for `"agui"` substring in any module name, and `"agui_server"` in the filename

**Verdict:** 🔴 **FAIL**

**Impact if bypass is removed:**
- `agent.run_stream()` would delegate to `SessionPool.run_stream()`
- SessionPool would create a new session for the AG-UI request
- Events would flow through EventBus instead of directly to AG-UI adapter
- AG-UI protocol events (AGUIEventStream) would be intercepted by SessionPool
- **Result**: AG-UI streaming would break or produce incorrect events

---

#### Route 2: Skill Tool Execution (`skill_tools.py`)

**Code path:**
```python
# skill_tools.py (~100)
# Skill commands are format converters only - they do NOT execute agents
```

**Analysis:**
- `skill_tools.py` is a **schema converter** (`SkillCommand` → AG-UI `Tool` format)
- It does **NOT** use `BaseAgentAGUIAdapter` — zero references in file
- It does **NOT** call `agent.run_stream()` — zero references in file
- It does **NOT** trigger the AG-UI bypass at all

**Verdict:** N/A — Not an agent execution path. No bypass involvement.

---

### Summary

| Route | File | Bypass Method | ContextVar | Verdict |
|-------|------|---------------|------------|---------|
| Agent streaming | `server.py` | Stack inspection | ❌ Not set | 🔴 **FAIL** |
| Skill execution | `skill_tools.py` | N/A (schema converter) | N/A | N/A |

**Total routes audited:** 1
**Pass:** 0
**Fail:** 1
**N/A:** 1 (not an agent execution path)

## Root Cause

AG-UI uses `agent.run_stream()` directly because:

1. **Protocol-specific event transformation**: AG-UI requires `AGUIEventStream` to transform `RichAgentStreamEvent` → `BaseEvent`. SessionPool's EventBus would deliver raw events without this transformation.

2. **Stateless protocol**: AG-UI clients send full history with each request. SessionPool's session management would accumulate duplicate history.

3. **No ContextVar setup**: The AG-UI adapter was written before the ContextVar bypass mechanism was designed. It relies on the older stack inspection approach.

## Mitigation Options

### Option A: Keep AG-UI Bypass Permanent (Recommended)

Document AG-UI bypass as a permanent feature:

```python
# base_agent.py
async def run_stream(self, prompt, **kwargs):
    # AG-UI bypass is permanent - AG-UI protocol requires direct agent access
    # for protocol-specific event transformation (AGUIEventStream).
    # See docs/audit/agui-bypass-audit.md for details.
    if self._should_bypass_session_pool():
        # Falls through to legacy run_stream() implementation below
        # which directly accesses agent.run_stream() without SessionPool
        return await self._legacy_run_stream(prompt, **kwargs)
    return await SessionPool.run_stream(...)
```

**Pros:**
- Minimal code change
- AG-UI continues to work exactly as before
- No risk of breaking AG-UI protocol compatibility

**Cons:**
- Stack inspection remains in codebase
- One more special case to maintain

### Option B: Add ContextVar to AG-UI Adapter

Modify `BaseAgentAGUIAdapter.run_stream()` to set the ContextVar:

```python
# base_agent_adapter.py
async def run_stream(self):
    from agentpool.agents.base_agent import _bypass_session_pool_var
    
    _bypass_session_pool_var.set(True)
    try:
        async for event in self.agent.run_stream(prompt, store_history=False):
            yield event
    finally:
        _bypass_session_pool_var.set(False)
```

**Pros:**
- Consistent with OpenCode bypass mechanism
- Could eventually remove stack inspection entirely

**Cons:**
- Still requires bypass to exist (just changes detection method)
- AG-UI still bypasses SessionPool, so no functional improvement
- Risk of introducing bugs in AG-UI event flow

### Option C: Route AG-UI Through SessionPool (Not Recommended)

Create a SessionPool-compatible wrapper for AG-UI:

```python
class AGUISessionPoolAdapter:
    async def run_stream(self, session_id, prompt):
        # Subscribe to EventBus, transform events to AG-UI format
        queue = await SessionPool.event_bus.subscribe(session_id)
        # ... run agent through SessionPool ...
        # ... transform events ...
```

**Pros:**
- Removes bypass entirely
- Unified execution path

**Cons:**
- Major refactoring of AG-UI protocol handling
- Complex event transformation pipeline
- High risk of breaking AG-UI compatibility
- Significant effort for marginal gain

## Recommendation

**Adopt Option A: Keep AG-UI bypass permanent.**

Rationale:
- AG-UI is a separate protocol with different requirements from OpenCode
- The bypass is well-documented and isolated to one module
- Removing it provides no functional benefit to OpenCode Server
- The cost of Option C far exceeds the benefit

## Updated Spec Reference

Add to `openspec/specs/sessionpool-only-execution/spec.md` (Removed Requirements section):

```markdown
### Requirement: Remove AG-UI bypass from `_should_bypass_session_pool()`
**Status:** Rejected per AG-UI audit (docs/audit/agui-bypass-audit.md)

**Reason:** AG-UI protocol requires direct agent access for protocol-specific
event transformation (AGUIEventStream). Routing AG-UI through SessionPool would
require a complex adapter layer with high risk of breaking AG-UI compatibility.

**Decision:** The AG-UI bypass is documented as permanent. Stack inspection
for AG-UI modules (`agentpool_server.agui_server`) remains in
`_should_bypass_session_pool()`. Only the SessionPool-internal bypass
(formerly detected via stack inspection) is replaced by the ContextVar mechanism.
```
