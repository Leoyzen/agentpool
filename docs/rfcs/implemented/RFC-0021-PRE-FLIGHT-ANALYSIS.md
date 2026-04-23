# RFC-0021 Pre-Flight Analysis

## State Inventory

### Instance-Level Mutable State in BaseAgent

| Field | Type | Shared? | Migration Priority | Usage Count |
|-------|------|---------|-------------------|-------------|
| `_cancelled` | bool | ✅ Yes | **Critical** | 15+ locations |
| `_current_stream_task` | asyncio.Task | ✅ Yes | **Critical** | 8 locations |
| `_event_queue` | asyncio.Queue | ✅ Yes | **Critical** | 6 locations |
| `_injection_manager` | PromptInjectionManager | ✅ Yes | **High** | 9 locations |
| `_background_task` | asyncio.Task | ✅ Yes | Medium | 4 locations |
| `_formatted_system_prompt` | str | ⚠️ Shared (intentional) | **Do NOT migrate** | 3 locations |
| `_internal_fs` | IsolatedMemoryFileSystem | ⚠️ Shared (intentional) | **Do NOT migrate** | 2 locations |

### State Usage Analysis

#### 1. `_cancelled` Usage
```python
# Files accessing _cancelled:
- native_agent.py: Lines 767, 835, 848, 858, 906, 917 (finally)
- base_agent.py: Lines 229 (init), 486 (reset), 494 (set), 618 (reset), 998 (set)
- claude_code_agent.py: Line ~120
```

#### 2. `_current_stream_task` Usage
```python
# Files accessing _current_stream_task:
- native_agent.py: Lines 619 (set), 647 (reset), 959 (interrupt)
- base_agent.py: Lines 230 (init), 619 (set), 647 (reset)
```

#### 3. `_event_queue` Usage
```python
# Files accessing _event_queue:
- base_agent.py: Lines 213 (init), 350 (emit)
- context.py: Line 71 (report_progress)
- event_emitter.py: Line 350 (_emit)
```

#### 4. `_injection_manager` Usage
```python
# Files accessing _injection_manager:
- base_agent.py: Lines 231 (init), 532, 551, 555, 559, 563, 621, 625, 645, 648
```

### Subclass Audit

| Subclass | File | Custom State Access | Risk Level |
|----------|------|---------------------|------------|
| NativeAgent | native_agent/agent.py | _cancelled (6x), _current_stream_task (3x) | **High** |
| ACPAgent | acp_agent/acp_agent.py | _event_queue, _cancelled | Medium |
| AGUIAgent | agui_agent/agui_agent.py | _cancelled, _current_stream_task | Medium |
| ClaudeCodeAgent | claude_code_agent/claude_code_agent.py | _event_queue, _cancelled | Medium |
| CodexAgent | codex_agent/codex_agent.py | Needs audit | Unknown |

### Cross-Cutting Dependencies

```
AgentContext.report_progress()
  └── self.agent._event_queue.put()  # Needs run_ctx access

StreamEventEmitter._emit()
  └── self._context.agent._event_queue.put()  # Needs run_ctx access

BaseAgent.interrupt()
  └── self._cancelled = True  # Must target specific run
  └── self._current_stream_task.cancel()  # Must target specific run
```

## Critical Code Paths

### Path 1: run_stream → _run_stream_once
```
run_stream()
  └── _run_stream_once()
        └── _stream_events()
              └── agent_iteration_task()
                    └── [Uses _cancelled check every iteration]
```

### Path 2: Event Emission
```
Agent/Tool emits event
  └── StreamEventEmitter._emit()
        └── agent._event_queue.put(event)  # [Must use run_ctx queue]
```

### Path 3: Interruption
```
External interrupt()
  └── self._cancelled = True  # [Must set run_ctx.cancelled]
  └── self._current_stream_task.cancel()
```

## Known Issues

### Issue 1: finally Block Bug
**Location**: native_agent.py:917
```python
finally:
    self._cancelled = True  # Always sets cancelled, even on normal completion
```
**Impact**: Causes premature termination in concurrent scenarios
**Fix**: Only set cancelled on actual cancellation

### Issue 2: Interrupt Targeting
**Location**: base_agent.py interrupt() method
**Current**: Targets instance-level _current_stream_task
**Problem**: In concurrent scenario, which task gets interrupted?
**Fix**: Need run-specific interrupt handle

## Migration Complexity

### High Complexity (Critical Path)
1. `_cancelled` - Checked throughout iteration loop
2. `_event_queue` - Cross-cutting: used by AgentContext, StreamEventEmitter
3. `_current_stream_task` - Used for interruption

### Medium Complexity
4. `_injection_manager` - Localized to prompt injection methods
5. `_background_task` - Used in run_in_background mode

### Do Not Migrate
- `_formatted_system_prompt` - Represents shared agent personality
- `_internal_fs` - Shared filesystem is intentional feature

## Test Coverage Gaps

### Missing Tests
1. ❌ Concurrent call isolation test
2. ❌ Cancellation isolation test
3. ❌ Event queue isolation test
4. ❌ Performance regression test
5. ❌ Subclass compatibility test
6. ❌ Interruption targeting test

## Recommendations

### State Migration Order
1. **First**: `_cancelled` + `_current_stream_task` (tightly coupled)
2. **Second**: `_event_queue` (most complex, cross-cutting)
3. **Third**: `_injection_manager`
4. **Last**: `_background_task`

### Risk Mitigation
- Each migration must have passing tests before proceeding
- Maintain backward compatibility property accessors during transition
- Document shared vs isolated state clearly

## Sign-Off

**Analysis Date**: 2025-04-05
**Analyst**: yuchen.liu
**Status**: Ready for test suite creation
