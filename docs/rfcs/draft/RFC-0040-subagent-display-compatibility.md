---
rfc_id: RFC-0040
title: Subagent Display Compatibility — qwen-code Meta Protocol
status: REVIEW
author: yuchen.liu
created: 2026-06-27
last_updated: 2026-06-28
---

# RFC-0040: Subagent Display Compatibility — qwen-code Meta Protocol

## Overview

ACP clients (SEED, qwen-code SDK) display subagent "agent cards" by reading `_meta` fields (`parentToolCallId`, `subagentType`, `provenance`) from `session/update` notifications. AgentPool's `zed` mode uses draft ACP PR #855 fields (`kind="subagent"`, `SubagentRunInfo`) which these clients don't support, causing cards to disappear. This RFC proposes a `"qwen"` display mode and evaluates how to pass parent context to child session converters.

## Background

### Key architectural fact

`SessionNotification` inherits from `AnnotatedObject`, which has a `field_meta` field (serialized as `_meta` in JSON-RPC). This means `_meta` is on the **notification wrapper**, not on individual update types. The handler constructs `SessionNotification(session_id=..., update=...)` — adding `field_meta` there stamps `_meta` on all notifications regardless of update type.

### Current event flow

```
create_child_session()
  → emits SpawnSessionStart(parent_session_id, tool_call_id, source_name, ...)
    → EventBus (parent session scope)
      → Parent consumer: _on_spawn_session_start()
        → start_event_consumer(child_sid)
          → Child consumer loop (fresh converter, no parent context)
            → _handle_event(child_sid, PartDeltaEvent, ...)
              → converter.convert(event) → SessionUpdate
                → SessionNotification(session_id, update) → client
```

### The gap

The child converter is created in `_before_consumer_loop(child_sid)` with no knowledge of:
- `parentToolCallId` — the tool call that spawned this subagent
- `subagentType` — the agent name (e.g., "librarian")
- `provenance` — should be `"subagent"` for all child events

`SpawnSessionStart` carries all this info, but it's consumed by the **parent's** consumer loop — the child consumer never sees it.

### qwen-code's approach

qwen-code stamps `_meta: { parentToolCallId, subagentType, provenance: "subagent" }` on **every** `session/update` notification from a subagent session. This is done via a `SubAgentTracker` that stores parent context and a `ToolCallEmitter` that reads it.

### Display modes

| Mode | kind | SubagentRunInfo | `_meta` on notification | Target client |
|------|------|-----------------|-------------------------|---------------|
| `legacy` | No ToolCallStart | ❌ | ❌ | Plain text |
| `zed` | `"subagent"` | ✅ | ❌ | Zed (draft PR #855) |
| `qwen` (proposed) | `"other"` | ❌ | `parentToolCallId` + `subagentType` + `provenance` | SEED / qwen-code SDK |

## Problem Statement

1. `zed` mode breaks SEED — `kind="subagent"` is not in the ACP spec, clients silently drop the `ToolCallStart`
2. Reverting to `kind="other"` (hotfix) restores basic functionality but loses subagent card display
3. The child session converter has no way to know its parent's `tool_call_id` or `source_name` — this info exists only in the `SpawnSessionStart` event, consumed by the parent's consumer loop

## Goals

- Add `"qwen"` display mode that stamps `_meta` fields on all subagent notifications
- Pass parent context from `_on_spawn_session_start` to the child converter
- Zero changes to `EventBus`, `EventEnvelope`, or `RichAgentStreamEvent`
- Support nested subagents (depth > 1)

## Non-Goals

- Migrating SEED to support `kind="subagent"`
- Changing the `zed` mode behavior
- Adding `_meta` to the framework event layer
- Multi-turn reprompting or foreground-to-background promotion

## Evaluation Criteria

| Criterion | Weight | Description |
|-----------|--------|-------------|
| Elegance | High | Minimal moving parts, easy to reason about |
| Protocol isolation | High | ACP concerns stay in ACP server |
| Simplicity | High | Few files changed, clear data flow |
| Correctness | High | Nested subagents, concurrent children, error paths |

## Options Analysis

### Option A: Handler dict + converter constructor

Handler stores `SubagentContext` in a dict in `_on_spawn_session_start`. `_before_consumer_loop` pops it and passes to converter constructor.

**Advantages**: Follows existing `_parent_of` dict pattern. No mixin changes. Protocol-layer only.

**Disadvantages**: Handler accumulates dicts. Dict entries can leak on error paths. `_before_consumer_loop` is a mixin method — override works but the pattern is indirect.

**Effort**: ~3 files, ~40 lines

### Option B: ContextVar propagation

Set a `ContextVar[SubagentContext | None]` in `_on_spawn_session_start` before calling `start_event_consumer`. The child task (created via `asyncio.ensure_future`) inherits the context via `contextvars.copy_context()`.

**Advantages**: No handler-level mutable state. Implicit flow. No mixin changes.

**Disadvantages**: Implicit data flow — hard to debug. Relies on `asyncio.ensure_future` copying context (implementation detail). Debuggability is poor — contextvar value is invisible in handler state.

**Effort**: ~2 files, ~25 lines

### Option C: Mixin parameter

Change `start_event_consumer` to accept an optional `spawn_envelope` parameter, passed through to `_before_consumer_loop`.

**Advantages**: Explicit parameter passing. No mutable state.

**Disadvantages**: Changes mixin signature — affects all protocols (OpenCode, AG-UI, OpenAI API). Couples mixin to "spawn" concept. Other protocols must accept and ignore the parameter.

**Effort**: ~5 files, ~60 lines

### Option D: Converter self-population

Child converter inspects first event for `SpawnSessionStart` and extracts parent context.

**Disadvantages**: **Fatal flaw** — child consumer subscribes with `scope="session"`, never receives `SpawnSessionStart` (published to parent's session stream). Architecturally broken.

**Effort**: N/A

### Option E: Handler creates converter directly (recommended)

`_on_spawn_session_start` creates the child converter with `SubagentContext` extracted from the `SpawnSessionStart` event, stores it in `_converters[child_sid]`. `_before_consumer_loop` checks if a converter already exists; if so, skips creation.

```
_on_spawn_session_start(parent_sid, envelope):
  event = envelope.event  # SpawnSessionStart
  converter = ACPEventConverter(
      subagent_display_mode=...,
      subagent_context=SubagentContext(
          parent_tool_call_id=event.tool_call_id,
          subagent_type=event.source_name,
      ),
  )
  self._converters[child_sid] = converter
  await self.start_event_consumer(child_sid)

_before_consumer_loop(child_sid):
  if child_sid in self._converters:
      return  # Already created by _on_spawn_session_start
  self._converters[child_sid] = ACPEventConverter(...)

_handle_event(session_id, envelope):
  converter = self._converters.get(effective_sid)
  async for update in converter.convert(envelope.event):
      notification = SessionNotification(
          session_id=effective_sid,
          update=update,
          field_meta=converter.subagent_meta,  # None for root, dict for child
      )
      await self.client.session_update(notification)
```

**Advantages**:
- **No dict** — converter is stored in the existing `_converters` dict, no new dict needed
- **No mixin changes** — `_before_consumer_loop` just checks for existing converter
- **One-line `_meta` stamping** — `field_meta=converter.subagent_meta` on `SessionNotification`
- **No `convert()` refactor** — converter holds context, exposes property, handler stamps on notification
- **Clear data flow** — context goes event → handler → converter → notification, all visible
- **Temporal ordering guaranteed** — `_on_spawn_session_start` runs in parent's consumer loop before child task starts

**Disadvantages**:
- `_before_consumer_loop` gains an early-return check (minor complexity)
- `_on_spawn_session_start` takes on converter creation responsibility (previously only `_before_consumer_loop` did this)
- If `start_event_consumer` fails after converter creation, converter lingers in `_converters` (same issue as existing `_parent_of` dict — pre-existing pattern)

**Effort**: ~2 files, ~30 lines

## Comparison Matrix

| Criterion | A (Dict) | B (ContextVar) | C (Mixin param) | D (Self-populate) | E (Direct create) |
|-----------|----------|-----------------|-------------------|---------------------|---------------------|
| Elegance | Medium | High | Medium | N/A | **High** |
| Protocol isolation | ✅ | ✅ | ❌ | ✅ | **✅** |
| Simplicity | 3 files | 2 files | 5 files | N/A | **2 files** |
| No new mutable state | ❌ (new dict) | ✅ | ✅ | N/A | **✅ (reuses `_converters`)** |
| Correctness | ✅ | ✅ | ✅ | ❌ | **✅** |
| Debuggability | High | Low | High | N/A | **High** |

## Recommendation

**Option E** — Handler creates child converter directly in `_on_spawn_session_start`.

This is the simplest approach because:
1. `_meta` is on `SessionNotification`, not on update objects — stamping is one line in `_handle_event`
2. The converter is stored in the existing `_converters` dict — no new dict needed
3. `_before_consumer_loop` just checks if converter exists — no mixin signature change
4. Context flows explicitly: `SpawnSessionStart` event → handler extracts → converter stores → handler stamps on notification

## Technical Design

### SubagentContext (new dataclass in event_converter.py)

```python
@dataclass
class SubagentContext:
    """Parent context for a child session converter."""
    parent_tool_call_id: str
    subagent_type: str
```

### ACPEventConverter changes

```python
@dataclass
class ACPEventConverter:
    # ... existing fields ...
    subagent_context: SubagentContext | None = None

    @property
    def subagent_meta(self) -> dict[str, Any] | None:
        """Build _meta dict for subagent notifications. None for root sessions."""
        if self.subagent_context is None:
            return None
        return {
            "parentToolCallId": self.subagent_context.parent_tool_call_id,
            "subagentType": self.subagent_context.subagent_type,
            "provenance": "subagent",
        }
```

### ACPProtocolHandler changes

```python
# _on_spawn_session_start: create child converter with context
async def _on_spawn_session_start(self, session_id, envelope):
    event = envelope.event
    if isinstance(event, SpawnSessionStart):
        child_sid = event.child_session_id
        if child_sid and child_sid != session_id:
            # ... existing spawn_mechanism check ...

            # Create child converter with subagent context
            self._converters[child_sid] = ACPEventConverter(
                subagent_display_mode=self._event_converter_template.subagent_display_mode,
                client_supports_turn_complete=...,
                subagent_context=SubagentContext(
                    parent_tool_call_id=event.tool_call_id or "",
                    subagent_type=event.source_name or "",
                ),
            )

            await self.start_event_consumer(child_sid)
            # ... rest of existing logic ...

# _before_consumer_loop: skip if converter already exists
async def _before_consumer_loop(self, session_id):
    if session_id in self._converters:
        return  # Created by _on_spawn_session_start
    self._converters[session_id] = ACPEventConverter(...)

# _handle_event: stamp _meta on notification
async def _handle_event(self, session_id, envelope):
    converter = self._converters.get(effective_sid) or self._converters.get(session_id)
    if converter is None:
        return
    async for update in converter.convert(envelope.event):
        notification = SessionNotification(
            session_id=effective_sid,
            update=update,
            field_meta=converter.subagent_meta,
        )
        await self.client.session_update(notification)
```

### SpawnSessionStart handler in converter (qwen mode)

```python
case SpawnSessionStart(...):
    if self.subagent_display_mode == "legacy":
        # ... existing inline text ...
    elif self.subagent_display_mode == "zed":
        # ... existing ToolCallStart(kind="subagent") ...
    elif self.subagent_display_mode == "qwen":
        tool_call_id = event.tool_call_id or str(uuid.uuid4())
        yield ToolCallStart(
            tool_call_id=tool_call_id,
            title=f"{source_name}: {description}" if description else source_name,
            kind="other",
            status="pending",
        )
```

### Files changed

| File | Changes |
|------|---------|
| `event_converter.py` | Add `SubagentContext`, `subagent_context` field, `subagent_meta` property, `"qwen"` mode branch |
| `handler.py` | Create converter in `_on_spawn_session_start`, early-return in `_before_consumer_loop`, stamp `field_meta` in `_handle_event` |

## Open Questions

1. **Should `"qwen"` become the default mode?** SEED is the primary client today, but `legacy` is the safe default for unknown clients.

2. **Client capability detection?** Could the ACP `initialize` handshake auto-select `zed` vs `qwen` mode based on client capabilities. Separate concern from context passing.

3. **Nested subagents**: Each level gets its own converter with its own `subagent_context`. `parentToolCallId` points to the immediate parent, not the root. This matches qwen-code's behavior.

## Decision Record

_Pending — awaiting stakeholder approval._

## Errata & Fix (2026-06-28)

After implementing Option E and testing with SEED, two bugs were discovered that prevent subagent cards from rendering. Both are addressed in the `fix-qwen-meta-stamping` OpenSpec change.

### Bug 1: `_meta` stamped on wrong object

**Original design (wrong)**: The RFC states `_meta` is on `SessionNotification` (the wrapper), and the handler stamps `field_meta=converter.subagent_meta` on the notification. Section "Key architectural fact" (line 20) explicitly says: "`_meta` is on the **notification wrapper**, not on individual update types."

**qwen-code's actual behavior (correct)**: qwen-code stamps `_meta` directly on the **SessionUpdate object** (`update._meta`), not on the notification wrapper. Specifically:
- `ToolCallEmitter.emitStart()` (ToolCallEmitter.ts:94) sets `_meta: { toolName, ...subagentMeta, provenance }` on the update payload
- `Session.sendUpdate()` (Session.ts:1890-1897) builds `SessionNotification(sessionId, update)` WITHOUT `field_meta`
- The normalizer's `extractParentToolCallId(update)` reads `update._meta.parentToolCallId`

**Fix**: Move `_meta` stamping from `SessionNotification.field_meta` (handler) to `SessionUpdate.field_meta` (converter). The converter's `convert()` method stamps `field_meta=self.subagent_meta` on each yielded update via a `_stamp_meta()` helper. The handler removes `field_meta=converter.subagent_meta` from `SessionNotification` construction.

**RFC sections affected**: "Key architectural fact" (line 20) is incorrect — `_meta` should go on the update, not the notification. Option E's `_handle_event` code (line 154) shows `field_meta=converter.subagent_meta` on the notification — this is wrong. The display mode table (line 51-55) column "`_meta` on notification" should be "`_meta` on update".

### Bug 2: Tool call ID collision

**Original design (wrong)**: The qwen mode `SpawnSessionStart` handler (line 280) uses `tool_call_id = event.tool_call_id or str(uuid.uuid4())` — reusing the parent's tool call ID for the child's `ToolCallStart`.

**qwen-code's actual behavior (correct)**: qwen-code's normalizer has a self-reference guard (`rawParentToolCallId !== toolCallId ? rawParentToolCallId : undefined`). When the child's `toolCallId` equals `parentToolCallId`, the normalizer drops the `parentToolCallId` — defeating the correlation. qwen-code uses the parent's `callId` only for `SubAgentTracker.parentToolCallId`, not for the child's own tool call IDs.

**Fix**: In qwen mode's `SpawnSessionStart` handler, always generate `tool_call_id = str(uuid.uuid4())`. The parent's `event.tool_call_id` is carried in `SubagentContext.parent_tool_call_id` → `_meta.parentToolCallId`, never as the child's `tool_call_id`.

**RFC sections affected**: Option E's qwen mode code (line 280) shows `tool_call_id = event.tool_call_id or str(uuid.uuid4())` — this is wrong. Should be `tool_call_id = str(uuid.uuid4())`.

### Updated display mode table

| Mode | kind | SubagentRunInfo | `_meta` on **update** | `tool_call_id` source | Target client |
|------|------|-----------------|------------------------|----------------------|---------------|
| `legacy` | No ToolCallStart | N/A | N/A | N/A | Plain text |
| `zed` | `"other"` (hotfixed) | ✅ | `subagent_session_info` | `event.tool_call_id` or UUID | Zed (draft PR #855) |
| `qwen` | `"other"` | ❌ | `parentToolCallId` + `subagentType` + `provenance` | **Unique UUID** (never parent's ID) | SEED / qwen-code SDK |

### Updated data flow (post-fix)

```
_on_spawn_session_start(parent_sid, envelope):
  event = envelope.event  # SpawnSessionStart
  converter = ACPEventConverter(
      subagent_display_mode=...,
      subagent_context=SubagentContext(
          parent_tool_call_id=event.tool_call_id,  # Parent's ID → _meta.parentToolCallId
          subagent_type=event.source_name,
      ),
  )
  self._converters[child_sid] = converter
  await self.start_event_consumer(child_sid)

_before_consumer_loop(child_sid):
  if child_sid in self._converters:
      return  # Already created by _on_spawn_session_start
  self._converters[child_sid] = ACPEventConverter(...)

_handle_event(session_id, envelope):
  converter = self._converters.get(effective_sid)
  async for update in converter.convert(envelope.event):
      # update.field_meta already stamped by converter._stamp_meta()
      notification = SessionNotification(
          session_id=effective_sid,
          update=update,  # _meta is INSIDE the update, not on the notification
      )
      await self.client.session_update(notification)

# In converter.convert(), qwen mode SpawnSessionStart:
elif self.subagent_display_mode == "qwen":
    tool_call_id = str(uuid.uuid4())  # Unique ID, NEVER event.tool_call_id
    yield self._stamp_meta(ToolCallStart(
        tool_call_id=tool_call_id,
        title=f"{source_name}: {description}" if description else source_name,
        kind="other",
        status="pending",
    ))

# _stamp_meta helper:
def _stamp_meta(self, update):
    if self.subagent_context is not None:
        update.field_meta = self.subagent_meta
    return update
```

### zed mode compatibility note

The zed mode's `ToolCallStart` already sets `field_meta` with `subagent_session_info` at construction time (event_converter.py line 717). The `_stamp_meta()` helper must NOT overwrite this existing `field_meta`. The helper should either:
- Only stamp when `update.field_meta is None` (merge strategy), OR
- Only stamp for qwen mode (mode-gated strategy)

The `fix-qwen-meta-stamping` design uses the "only stamp when `subagent_context is not None`" approach, which works because zed mode's `ToolCallStart` is yielded from the **parent** converter (which has `subagent_context=None`), while child event updates flow through the **child** converter (which has `subagent_context` set but doesn't yield `SpawnSessionStart`).
