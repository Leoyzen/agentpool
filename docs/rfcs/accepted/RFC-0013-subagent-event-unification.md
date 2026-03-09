---
rfc_id: RFC-0013
title: Subagent Event Stream Unification for OpenCode Protocol
status: DRAFT
author: AgentPool Team
reviewers: []
created: 2026-02-13
last_updated: 2026-02-13
---

# RFC-0013: Subagent Event Stream Unification for OpenCode Protocol

## Overview

This RFC proposes a unified event handling architecture for subagent execution within the AgentPool OpenCode server. The current implementation loses critical subagent events during streaming, preventing real-time status updates in OpenCode clients. Additionally, the code handling subagent events is significantly duplicative of the main agent event handling logic.

This proposal aims to:
1. Ensure **all subagent events** (text deltas, tool calls, progress updates) are correctly propagated to the OpenCode SSE stream
2. **Eliminate code redundancy** by unifying the event processing pipeline for both main agents and subagents
3. Maintain **backward compatibility** with existing ACP and other protocol implementations

## Background & Context

### OpenCode Protocol Requirements

Based on the [OpenCode Attach Remote Protocol Specification](https://github.com/opencode/opencode/blob/main/docs/protocol.md), the OpenCode TUI expects real-time updates for subagent sessions through:

1. **Parent Session Tool Part**: A tool-type Part in the parent session's assistant message that represents the subagent task
2. **Independent Child Session**: A separate session (created with `parentID`) containing the full conversation and execution details
3. **Unified SSE Stream**: Events from both parent and child sessions flow through a single `/event` SSE endpoint

As documented in the protocol:
> "When subagent executes tool calls, the main session's UI needs to display 'X toolcalls' updates in real-time. This is achieved through the global SSE stream."

The protocol flow requires:
```
1. MAIN SESSION triggers Task tool
   └─> Creates child session with parentID=mainSessionID
   └─> Returns: metadata: { sessionId: childSessionId }

2. CHILD SESSION executes tools
   └─> Each tool call emits: message.part.updated {part}
   └─> part.sessionID = CHILD session ID (not parent ID)

3. SERVER broadcasts via SSE (/event)
   └─> All events flow through single global stream

4. CLIENT receives event
   └─> Updates store.part[messageID] = [...]
   └─> Events with child sessionID stored under child key
```

### Current Implementation Issues

#### Issue 1: Lost Subagent Events

The current implementation in `stream_adapter.py` handles `SubAgentEvent` only for specific event types:

```python
# From stream_adapter.py _on_subagent method
case StreamCompleteEvent(message=msg):
    # Handles completion...
case ToolCallCompleteEvent(tool_name=tool_name, tool_result=result):
    # Only handles completed tool calls
```

**Missing event types** include:
- `PartDeltaEvent` (streaming text/thinking content)
- `PartStartEvent` (start of text/thinking parts)
- `ToolCallStartEvent` (tool invocation start)
- `ToolCallProgressEvent` (tool execution progress)
- `RunStartedEvent` (subagent session start)
- `RunErrorEvent` (subagent errors)

This results in OpenCode clients only seeing static "completed" states without the rich streaming experience available for main agents.

#### Issue 2: Code Duplication

The current implementation duplicates logic across:
1. `_handle_event` (main agent events): ~300 lines handling 10+ event types
2. `_on_subagent` (subagent events): ~150 lines handling only 3 event types

Both methods need to:
- Create/update TextPart for streaming content
- Track ToolPart states (running → completed)
- Handle timing metadata
- Emit PartUpdatedEvent/MessageUpdatedEvent

The duplication leads to maintenance overhead and inconsistent behavior between main agent and subagent streams.

### Related Code Paths

| File | Purpose |
|------|---------|
| `subagent_tools.py` | Spawns subagent via `task` tool, emits `SubAgentEvent` wrappers |
| `event_manager.py` | Routes events between parent/child session EventManagers |
| `stream_adapter.py` | Converts `RichAgentStreamEvent` to OpenCode `Event` objects |
| `state.py` | Manages session state, provides `ensure_session()` for child sessions |

## Problem Statement

**Primary Problem**: Subagent streaming events are lost in the OpenCode server because the `_on_subagent` event handler only processes a subset of the total event types.

**Secondary Problem**: The event handling logic for subagents duplicates (incompletely) the comprehensive handling in the main agent's `_handle_event` method, creating maintenance burden and inconsistent behavior.

### Evidence

1. **User Experience Gap**: When a subagent runs, the OpenCode UI shows a static "task" tool part with no updates until completion. Users cannot see:
   - Streaming text responses from the subagent
   - Tool calls being executed by the subagent
   - Progress or error states during execution

2. **Code Inspection**: The `_on_subagent` method in `stream_adapter.py` handles only:
   - `RunStartedEvent`: Creates a ToolPart for the subagent
   - `StreamCompleteEvent`: Updates subagent state, creates child session messages
   - `ToolCallCompleteEvent`: Only when child_session_id is present

   It does NOT handle:
   - `PartDeltaEvent` (both text and thinking)
   - `ToolCallStartEvent` / `ToolCallProgressEvent`
   - `RunErrorEvent`

3. **Protocol Incompatibility**: Per the OpenCode protocol reference implementation, child session tool calls should emit `message.part.updated` events with `part.sessionID` set to the child session ID. Current implementation misses these entirely.

## Goals & Non-Goals

### Goals

| ID | Goal | Priority |
|----|------|----------|
| G1 | Subagent text/thinking streaming must appear in real-time in child session | P0 |
| G2 | Subagent tool calls must be visible with their status transitions (pending → running → completed/error) | P0 |
| G3 | Main agent and subagent event handling logic should share common code paths | P1 |
| G4 | Parent session should show aggregated tool call counts from child sessions | P1 |
| G5 | Implementation must maintain backward compatibility with existing ACP/MCP servers | P0 |

### Non-Goals

| ID | Non-Goal | Rationale |
|----|----------|-----------|
| NG1 | Change ACP/MCP protocol behavior | This RFC focuses on OpenCode server enhancement; other protocols should remain unaffected |
| NG2 | Implement bidirectional parent-child event propagation | Currently, events flow child→parent only; parent→child is out of scope |
| NG3 | Modify the SubAgentEvent data structure | The Event class should remain stable; we're improving how it's processed |
| NG4 | Add new storage backends | Use existing session/message storage mechanisms |

## Evaluation Criteria

| Criterion | Weight | Description |
|-----------|--------|-------------|
| Protocol Compliance | High | Must correctly implement OpenCode Attach protocol subagent specifications |
| Code Maintainability | High | Should reduce LOC and eliminate duplication between main/subagent handling |
| Backward Compatibility | Critical | Must not break existing ACP, MCP, AG-UI, or direct API usage |
| Performance | Medium | Event routing overhead should be minimal (<5% latency increase) |
| Testability | Medium | Should enable comprehensive unit tests for event routing |

## Options Analysis

### Option 1: Extend _on_subagent with Missing Handlers (Status Quo Extension)

**Description**: Add explicit handler cases for missing event types (PartDeltaEvent, ToolCallStartEvent, etc.) to the existing `_on_subagent` method.

**Implementation Approach**:
- Copy existing handler logic from `_handle_event` into `_on_subagent`
- Modify to route events to child session's messages instead of parent session

**Advantages**:
- Minimal architectural changes
- Straightforward to implement

**Disadvantages**:
- Significantly increases code duplication (estimated +200 lines)
- Creates maintenance burden (changes to _handle_event must be mirrored)
- High risk of inconsistencies between main/subagent behavior

**Evaluation**:
| Criterion | Score | Notes |
|-----------|-------|-------|
| Protocol Compliance | 5/5 | Can achieve full compliance |
| Code Maintainability | 1/5 | Major duplication increase |
| Backward Compatibility | 5/5 | No structural changes |
| Performance | 4/5 | Minimal overhead |
| Testability | 2/5 | Duplicated tests required |

**Effort Estimate**: Medium (~3 days)

---

### Option 2: Unified Event Processor with Session Context (Recommended)

**Description**: Refactor event handling into a session-aware processor class that can operate on either parent or child session context. Both main agent and subagent events route through the same processor, but with different context objects.

**Implementation Approach**:
1. Create `EventProcessorContext` dataclass that encapsulates:
   - Target session ID
   - Target message ID
   - State reference
   - Event emitter callback
   - ToolPart tracking dictionary

2. Create `EventProcessor` class with methods:
   - `process_text_delta(ctx, delta)` → creates/updates TextPart in ctx.session
   - `process_tool_start(ctx, tool_name, tool_call_id, ...)` → creates ToolPart
   - `process_tool_progress(ctx, tool_call_id, ...)` → updates ToolPart
   - `process_tool_complete(ctx, tool_call_id, result, ...)` → finalizes ToolPart
   - `process_thinking_delta(ctx, ...)` → creates/updates ReasoningPart

3. Modify `OpenCodeStreamAdapter`:
   - Main agent events: `processor.process(event, main_context)`
   - Subagent events: `processor.process(event, child_context)`

4. For subagent container representation in parent:
   - Maintain a lightweight ToolPart in parent session (the "task" tool)
   - This ToolPart tracks subagent state (running → completed)
   - Actual subagent content goes to child session

**Advantages**:
- Single implementation for all event types
- ~50% reduction in total event handling code
- Consistent behavior between main agent and subagent
- Clear separation between event processing and session routing
- Easy to add new event types (one place to modify)

**Disadvantages**:
- Requires refactoring of existing `_handle_event` logic
- More complex initial implementation
- Need to ensure streaming text/thinking properly route to child session

**Evaluation**:
| Criterion | Score | Notes |
|-----------|-------|-------|
| Protocol Compliance | 5/5 | Full protocol compliance |
| Code Maintainability | 5/5 | Major reduction in duplication |
| Backward Compatibility | 5/5 | No API changes, internal refactor |
| Performance | 5/5 | No additional overhead |
| Testability | 5/5 | Processor can be unit tested independently |

**Effort Estimate**: Medium-High (~5 days)

---

### Option 3: Separate Subagent Stream Adapter

**Description**: Create a dedicated `SubagentStreamAdapter` class that is instantiated for each subagent session, handling events independently.

**Implementation Approach**:
- When subagent starts, create new `SubagentStreamAdapter(child_session_id, parent_adapter)`
- Subagent adapter manages child session state independently
- Parent adapter receives aggregated state updates from subagent adapter

**Advantages**:
- Clean separation of concerns
- Subagent handling is isolated and testable

**Disadvantages**:
- More complex lifecycle management (create/destroy adapters)
- Potential memory/performance overhead with many nested subagents
- Still requires coordination between parent and child adapters
- Doesn't fully solve duplication (may duplicate EventProcessor logic)

**Evaluation**:
| Criterion | Score | Notes |
|-----------|-------|-------|
| Protocol Compliance | 5/5 | Can achieve compliance |
| Code Maintainability | 3/5 | Additional complexity in lifecycle |
| Backward Compatibility | 5/5 | Internal implementation change |
| Performance | 3/5 | Multiple adapter instances |
| Testability | 4/5 | Good isolation for testing |

**Effort Estimate**: High (~7 days)

---

## Recommendation

**Option 2: Unified Event Processor with Session Context**

This option provides the best balance of maintainability improvements and protocol compliance. While requiring more initial effort than Option 1, it eliminates technical debt and provides a foundation for future streaming enhancements.

### Key Design Decisions

1. **Context-Based Processing**: By parameterizing the target session/message, the same processor handles main agent and subagent events uniformly.

2. **Parent-Child Coordination**: The parent session displays an aggregated view (task tool part with counter), while the child session contains the detailed execution log.

3. **Backward Compatibility**: Existing protocol implementations (ACP, MCP) use their own conversion logic and are unaffected by OpenCode server changes.

## Technical Design

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     OpenCodeStreamAdapter (Before)                       │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────────┐    ┌─────────────────┐                             │
│  │  _handle_event  │    │   _on_subagent  │                             │
│  │  (300+ lines)   │    │   (150 lines)   │                             │
│  └────────┬────────┘    └────────┬────────┘                             │
│           │                      │                                      │
│           ▼                      ▼                                      │
│  ┌─────────────────┐    ┌─────────────────┐                             │
│  │  Direct state   │    │  Limited state  │                             │
│  │  modifications  │    │  modifications  │                             │
│  └─────────────────┘    └─────────────────┘                             │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                     OpenCodeStreamAdapter (After)                        │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    EventProcessor                                │    │
│  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐             │    │
│  │  │process_text  │ │process_tool  │ │process_think │             │    │
│  │  │_delta        │ │_start        │ │_delta        │             │    │
│  │  └──────────────┘ └──────────────┘ └──────────────┘             │    │
│  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐             │    │
│  │  │process_tool  │ │process_tool  │ │...           │             │    │
│  │  │_progress     │ │_complete     │ │              │             │    │
│  │  └──────────────┘ └──────────────┘ └──────────────┘             │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                               ▲                                          │
│                               │                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │              OpenCodeStreamAdapter                               │    │
│  │  ┌─────────────────┐          ┌─────────────────┐               │    │
│  │  │  main context   │          │  child context  │               │    │
│  │  │  (parent)       │          │  (subagent)     │               │    │
│  │  └────────┬────────┘          └────────┬────────┘               │    │
│  │           │                            │                        │    │
│  │           └────────────┬───────────────┘                        │    │
│  │                        ▼                                         │    │
│  │           ┌─────────────────────┐                               │    │
│  │           │  route_to_processor │                               │    │
│  │           │  (event, context)   │                               │    │
│  │           └─────────────────────┘                               │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Data Models

#### EventProcessorContext

```python
@dataclass
class EventProcessorContext:
    """Context for event processing, identifying target session and message."""
    
    session_id: str
    """Target session ID (parent or child)."""
    
    message_id: str
    """Target message ID within the session."""
    
    state: ServerState
    """Server state for accessing messages and sessions."""
    
    working_dir: str
    """Working directory for path context."""
    
    on_file_paths: Callable[[list[str]], None] | None
    """Optional callback for LSP path discovery."""
    
    # Mutable tracking state (initialized fresh per context)
    text_part: TextPart | None = field(default=None, init=False)
    reasoning_part: ReasoningPart | None = field(default=None, init=False)
    tool_parts: dict[str, ToolPart] = field(default_factory=dict, init=False)
    tool_outputs: dict[str, str] = field(default_factory=dict, init=False)
    tool_inputs: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)
    response_text: str = field(default="", init=False)
    stream_start_ms: int = field(default_factory=now_ms, init=False)
```

#### EventProcessor

```python
class EventProcessor:
    """Unified processor for RichAgentStreamEvent objects.
    
    Processes events into OpenCode models and emits SSE events.
    Stateless - all mutable state lives in EventProcessorContext.
    """
    
    def __init__(self, state: ServerState):
        self.state = state
    
    async def process(
        self, 
        event: RichAgentStreamEvent[Any], 
        ctx: EventProcessorContext
    ) -> AsyncIterator[Event]:
        """Process a single event in the given context."""
        match event:
            case PartDeltaEvent(delta=TextPartDelta(content_delta=delta)) if delta:
                async for e in self._process_text_delta(ctx, delta):
                    yield e
            case ToolCallStartEvent():
                async for e in self._process_tool_start(ctx, event):
                    yield e
            # ... etc
    
    async def _process_text_delta(
        self, 
        ctx: EventProcessorContext, 
        delta: str
    ) -> AsyncIterator[Event]:
        """Create/update TextPart in ctx.session_id's message."""
        # Implementation creates/updates TextPart in ctx's target
        ctx.response_text += delta
        # ... emit PartUpdatedEvent
```

### Event Flow Specification

#### Main Agent Event Flow

```
Input: RichAgentStreamEvent from agent.run_stream()
↓
EventProcessor.process(event, main_context)
  ├─ session_id = parent_session_id
  ├─ message_id = assistant_msg_id (parent)
  └─ Updates: state.messages[parent_session_id][assistant_msg]
↓
Emit: PartUpdatedEvent / MessageUpdatedEvent
  └─ Event.session_id = parent_session_id
```

#### Subagent Event Flow

```
Input: SubAgentEvent from subagent.run_stream()
↓
Extract: wrapped_event, child_session_id
↓
Ensure child session exists: state.ensure_session(child_session_id, parent_id)
↓
# Create/update container ToolPart in PARENT session
EventProcessor.process_container(container_ctx, subagent_state)
  ├─ session_id = parent_session_id
  ├─ message_id = assistant_msg_id (parent)
  └─ Represents: "task" tool showing subagent status
↓
# Process actual content in CHILD session
EventProcessor.process(wrapped_event, child_context)
  ├─ session_id = child_session_id
  ├─ message_id = child_assistant_msg_id
  └─ Updates: state.messages[child_session_id][child_assistant_msg]
↓
Emit: PartUpdatedEvent / MessageUpdatedEvent
  ├─ For container: Event.session_id = parent_session_id
  └─ For content: Event.session_id = child_session_id
```

### Container Part Specification

The parent session contains a ToolPart representing the subagent:

```python
ToolPart(
    id=container_part_id,  # Unique per subagent instance
    message_id=parent_assistant_msg_id,
    session_id=parent_session_id,
    tool="task",
    call_id=unique_call_id,
    state=ToolStateRunning(
        title=f"Subagent: {source_name}",
        input={
            "description": description,
            "subagent_type": source_type,
            "prompt": prompt,
        },
        metadata={
            "sessionId": child_session_id,
            "title": source_name,
            # Future: "toolCallCount": n  (aggregated from child)
        }
    )
)
```

### OpenCode Protocol Compliance

Per [Section 6.6 of the OpenCode Protocol](https://github.com/opencode/opencode/blob/main/docs/protocol.md#66-subagent-tool-call-monitoring):

1. **Part Event Format**: All `PartUpdatedEvent` objects must include `session_id` field
   - Parent container events: `session_id = parent_session_id`
   - Child content events: `session_id = child_session_id`

2. **Message Event Format**: `MessageUpdatedEvent` for child session messages uses `session_id = child_session_id`

3. **Metadata Structure**: ToolPart metadata includes `sessionId` for UI navigation

4. **Single SSE Stream**: All events flow through `/event` endpoint; clients filter by `session_id`

## Implementation Plan

### Phase 1: Create EventProcessor Infrastructure (2 days)

- [ ] Create `EventProcessorContext` dataclass
- [ ] Create `EventProcessor` class with all handler methods
- [ ] Migrate existing `_handle_event` logic to `EventProcessor`
- [ ] Update `OpenCodeStreamAdapter` to use `EventProcessor` for main agent
- [ ] Unit tests for `EventProcessor`

### Phase 2: Subagent Integration (2 days)

- [ ] Enhance `_on_subagent` to use `EventProcessor` with child context
- [ ] Implement container ToolPart lifecycle (running → completed)
- [ ] Ensure all SubAgentEvent wrapped types are processed
- [ ] Handle nested subagents (depth > 1)
- [ ] Integration tests for subagent event flow

### Phase 3: Cleanup and Validation (1 day)

- [ ] Remove redundant code from `_on_subagent`
- [ ] Verify backward compatibility (ACP/MCP tests)
- [ ] Verify OpenCode protocol compliance with test client
- [ ] Update documentation

### Dependencies

- None blocking; this is an internal refactoring

### Rollback Strategy

1. The change is localized to `stream_adapter.py` and new `event_processor.py`
2. Rollback: Revert to previous `stream_adapter.py` version
3. Data safety: No schema changes; only event emission timing/behavior changes

## Open Questions

1. **Backpressure Handling**: Should we implement backpressure for high-frequency subagent events flowing to the parent container? Currently, every child event updates the parent container state.

2. **Nested Subagent Depth**: Should we limit nesting depth for container tracking? Currently, we use `depth` parameter but don't enforce a maximum.

3. **Tool Call Aggregation**: Should the parent container track and display "X tool calls" count from the child session? This would require counting ToolPart objects in the child session.

4. **Error Propagation**: When a subagent encounters a `RunErrorEvent`, should this:
   a) Only update the child session state?
   b) Also mark the parent container as failed?
   c) Emit an error event on the parent session?

5. **Session Cleanup**: Should completed child sessions be automatically cleaned up from memory after some time to prevent unbounded growth?

## Decision Record

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-02-13 | Adopt Option 2 (Unified Event Processor) | Best balance of maintainability, compliance, and future extensibility |
| 2026-02-13 | Keep container-part pattern in parent session | Required for OpenCode protocol; allows UI navigation via `sessionId` metadata |

## References

- [OpenCode Attach Protocol Spec](./09-Attach远程协议详解.md) (original Chinese)
- [AgentPool Stream Adapter](./stream_adapter.py)
- [AgentPool Subagent Tools](./subagent_tools.py)
- [AgentPool Event Manager](./event_manager.py)
