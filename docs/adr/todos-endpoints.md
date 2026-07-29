# Todos Endpoints Decision Record

## Context

The OpenCode Server has a `todos` dictionary on `ServerState`:

```python
# state.py:80
todos: dict[str, list[Todo]] = field(default_factory=dict)
```

However, **code analysis reveals this is dead code**. In Migration B, `ServerState.todos` will be removed (B4.9). This document analyzes the actual impact.

## Current Usage Analysis

### Code References (Actual)

| File | Line | Usage |
|------|------|-------|
| `state.py` | 80 | Field definition (initialized to `{}`) |
| `state.py` | 167 | `self.todos.setdefault(session_id, [])` in `ensure_runtime_session_state()` |
| `routes/session_routes.py` | 710 | `state.todos[session_id] = []` in `create_session()` |
| `routes/session_routes.py` | 1037 | `state.todos[new_session_id] = []` in `fork_session()` |
| `routes/session_routes.py` | 880 | `state.todos.pop(session_id, None)` in `delete_session()` |
| `routes/session_routes.py` | 1187 | `GET /{session_id}/todo` — **reads from `state.pool.todos`, NOT `state.todos`** |

### Critical Finding: `state.todos` is Dead Code

**`state.todos[session_id]` is NEVER read from.** The only todo endpoint is:

```python
# session_routes.py:1187-1212
@router.get("/{session_id}/todo")
async def get_session_todos(session_id: str, state: StateDep) -> list[Todo]:
    tracker = state.pool.todos  # <-- Reads from AgentPool.todos (TodoTracker)
    return [
        Todo(id=e.id, content=e.content, status=e.status, priority=e.priority)
        for e in tracker.entries
    ]
```

The endpoint **completely ignores `session_id`** (except for child sessions) and reads from the global `TodoTracker` on `AgentPool`.

### `state.todos` Lifecycle

```python
# state.py:167 - ensure_runtime_session_state
self.todos.setdefault(session_id, [])

# session_routes.py:710 - create_session
state.todos[session_id] = []

# session_routes.py:1037 - fork_session  
state.todos[new_session_id] = []

# session_routes.py:880 - delete_session
state.todos.pop(session_id, None)
```

No other code reads from `state.todos`.

## Decision

**Remove `ServerState.todos` with zero backward compatibility impact.**

### Rationale

1. **Dead code**: `state.todos` is written to but never read from. Removing it affects nothing.

2. **HTTP API unaffected**: The `GET /{session_id}/todo` endpoint reads from `pool.todos` (global TodoTracker), which is completely independent of `state.todos`.

3. **No CRUD endpoints exist**: Only GET exists; no POST/PUT/DELETE todo endpoints.

4. **Zero migration effort**: Remove the field and 4 lifecycle references.

### Migration Plan

1. **Migration B (B4.9)**:
   - Remove `todos` field from `ServerState` dataclass (`state.py:80`)
   - Remove `self.todos.setdefault(session_id, [])` from `ensure_runtime_session_state()` (`state.py:167`)
   - Remove `state.todos[session_id] = []` from `create_session()` (`session_routes.py:710`)
   - Remove `state.todos[new_session_id] = []` from `fork_session()` (`session_routes.py:1037`)
   - Remove `state.todos.pop(session_id, None)` from `delete_session()` (`session_routes.py:880`)
   - The `GET /{session_id}/todo` endpoint continues working unchanged

### Why the Initial Analysis Was Wrong

The initial document incorrectly assumed:
- ❌ CRUD endpoints exist (POST/PUT/DELETE) — they don't
- ❌ `state.todos` is actively used — it's dead code
- ❌ Removing it would break clients — the GET endpoint uses `pool.todos`

This was discovered during Oracle review by reading the actual `session_routes.py` code.

## Open Questions

1. **Should the `GET /{session_id}/todo` endpoint be session-scoped?**
   - Currently it returns all todos from `pool.todos` regardless of session
   - No session filtering is applied (neither for parent nor child sessions)
   - *Recommendation*: Keep current behavior; changing it is out of scope for Migration B

2. **Should todos be stored per-session instead of globally?**
   - `pool.todos` is a global TodoTracker
   - If session isolation is desired, this would be a feature change, not a migration
   - *Decision*: Defer to future feature work
