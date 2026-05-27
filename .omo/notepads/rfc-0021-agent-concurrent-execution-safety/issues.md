# RFC-0021 Issues & Blockers

## Active Issues


## Task 0-1: Finally Block Bug Fix - Completed

### Change Applied
File: `src/agentpool/agents/native_agent/agent.py` (line 917)

**Before (bug):**
```python
finally:
    iteration_done.set()
    self._cancelled = True  # Always sets cancelled!
```

**After (fix):**
```python
finally:
    iteration_done.set()
    # Only set cancelled if the iteration task was actually cancelled
    if iteration_task.cancelled():
        self._cancelled = True
```

### Test Results
- ✅ `test_single_call_completion` - PASSED (targeted test for this fix)
- ⚠️ `test_concurrent_event_isolation` - FAILED (pre-existing, unrelated to this bug fix)
- ✅ All other concurrent_safety tests PASSED

### Pre-existing Issues Discovered
1. `test_claude_code_with_subagent_toolset_setup` fails due to missing `dangerously_skip_permissions` attribute on `ClaudeCodeAgentConfig`
2. `test_concurrent_event_isolation` has event cross-contamination issues (Wave 1 scope)

### Evidence
- Test output: `.sisyphus/evidence/task-0-1-bug-fix.log`
- Regression output: `.sisyphus/evidence/task-0-1-regression.log`
