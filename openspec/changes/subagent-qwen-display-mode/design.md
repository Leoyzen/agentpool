# Design: subagent-qwen-display-mode

## Architecture: Option E — Handler creates converter directly

### Key insight

`SessionNotification` inherits from `AnnotatedObject`, which has `field_meta` (serialized as `_meta` in JSON-RPC). This means `_meta` is on the **notification wrapper**, not on individual update types. Stamping `_meta` is a one-line addition in `_handle_event`:

```python
notification = SessionNotification(
    session_id=effective_sid,
    update=update,
    field_meta=converter.subagent_meta,  # None for root, dict for child
)
```

This covers ALL event types from child sessions — no need to modify `convert()` or check which update types support `_meta`.

### Data flow

```
SpawnSessionStart event
  │ (tool_call_id, source_name, parent_session_id)
  ▼
_on_spawn_session_start (handler)
  │ extracts SubagentContext(parent_tool_call_id, subagent_type)
  │ creates ACPEventConverter(subagent_context=ctx)
  │ stores in self._converters[child_sid]
  ▼
start_event_consumer(child_sid)
  │
  ▼
_before_consumer_loop(child_sid)
  │ checks self._converters — already exists, returns early
  ▼
_handle_event(child_sid, envelope)
  │ converter = self._converters[child_sid]
  │ async for update in converter.convert(event):
  │     notification = SessionNotification(
  │         session_id=child_sid,
  │         update=update,
  │         field_meta=converter.subagent_meta,  ← _meta stamped here
  │     )
  │     await client.session_update(notification)
  ▼
SEED reads _meta.parentToolCallId → renders agent card
SEED reads _meta.subagentType → displays agent name
SEED reads _meta.provenance → marks as subagent
```

### SubagentContext

```python
@dataclass
class SubagentContext:
    """Parent context for a child session converter."""
    parent_tool_call_id: str
    subagent_type: str
```

Minimal — only what SEED needs. No `parent_session_id` (handler already tracks `_parent_of` for cancellation). No `depth` (not needed for display).

### Converter changes

```python
@dataclass
class ACPEventConverter:
    subagent_context: SubagentContext | None = None

    @property
    def subagent_meta(self) -> dict[str, Any] | None:
        if self.subagent_context is None:
            return None
        return {
            "parentToolCallId": self.subagent_context.parent_tool_call_id,
            "subagentType": self.subagent_context.subagent_type,
            "provenance": "subagent",
        }
```

### SpawnSessionStart handler — qwen mode

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

No `SubagentRunInfo`, no `field_meta` on the update — the `_meta` goes on the notification via `converter.subagent_meta`.

### Handler changes

```python
# _on_spawn_session_start: create child converter with context
self._converters[child_sid] = ACPEventConverter(
    subagent_display_mode=self._event_converter_template.subagent_display_mode,
    client_supports_turn_complete=...,
    subagent_context=SubagentContext(
        parent_tool_call_id=event.tool_call_id or "",
        subagent_type=event.source_name or "",
    ),
)
await self.start_event_consumer(child_sid)

# _before_consumer_loop: skip if converter already exists
if session_id in self._converters:
    return
self._converters[session_id] = ACPEventConverter(...)

# _handle_event: stamp _meta on notification
notification = SessionNotification(
    session_id=effective_sid,
    update=update,
    field_meta=converter.subagent_meta,
)
```

### Temporal ordering guarantee

```
Parent consumer loop (serial):
  1. Receives SpawnSessionStart
  2. Calls _on_spawn_session_start(parent_sid, envelope)
     → Creates converter, stores in _converters[child_sid]
     → Calls await start_event_consumer(child_sid)
       → asyncio.ensure_future(_run_consumer()) — task created, context captured
     → Returns
  3. _handle_event(parent_sid, envelope) — parent converter handles SpawnSessionStart

Later (event loop schedules child task):
  4. Child _event_consumer_loop starts
  5. _before_consumer_loop(child_sid) — sees converter exists, returns early
  6. Child events flow through _handle_event with converter.subagent_meta
```

Steps 1-3 are serial within the parent's consumer loop. Step 4 happens later when the event loop schedules the child task. The converter is already in `_converters` by then.

### Nested subagents

Each level gets its own converter with its own `SubagentContext`. `parentToolCallId` points to the immediate parent's tool call, not the root. This matches qwen-code's behavior.

### Error path cleanup

If `start_event_consumer` fails after converter creation, the converter lingers in `_converters`. This is the same pattern as the existing `_parent_of` dict — `_after_consumer_loop` cleans up `_converters[session_id]` on normal exit. For abnormal failure, the converter is harmless (it just won't be used).

## Decisions

- **D1**: `_meta` on `SessionNotification`, not on update objects — `field_meta` parameter on notification constructor, covers all event types
- **D2**: Handler creates child converter directly — no intermediate dict, reuses `_converters`
- **D3**: `SubagentContext` is minimal — only `parent_tool_call_id` and `subagent_type`, no session/depth info
- **D4**: `"qwen"` mode emits `ToolCallStart(kind="other")` — no `SubagentRunInfo`, no `field_meta` on update
- **D5**: No mixin or framework changes — all changes in ACP server only
