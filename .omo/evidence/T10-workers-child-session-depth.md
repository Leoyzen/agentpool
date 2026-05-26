# T10: WorkersTools Child Sessions and Depth Propagation

## Changes Summary

### Modified Files
1. `src/agentpool_toolsets/builtin/workers.py` — depth propagation, create_child_session, DelegationDepthError guard
2. `src/agentpool/delegation/team.py` — fix parent_session_id kwargs conflict, use parent_session_id_kwarg in resolution
3. `src/agentpool/delegation/teamrun.py` — fix parent_session_id kwargs conflict, proper variable naming
4. `tests/tools/test_workers.py` — 4 new tests for depth, child sessions, DelegationDepthError

### Checklist Verification
- [x] `ctx.create_child_session()` used in `_create_agent_tool()` and `_create_node_tool()`
- [x] All hardcoded `depth=1` replaced with computed `child_depth` from `ctx.run_ctx.depth`
- [x] MAX_DELEGATION_DEPTH enforced before child session creation
- [x] Existing worker event behavior and message history options preserved
- [x] Worker child sessions persist with correct parent
- [x] Worker spawn depth equals parent depth + 1

### Test Results
- 13/13 relevant tests pass (excluding 2 pre-existing model-availability failures)
- All team tests pass (28/28)
- All subagent child session tests pass (8/8)
- LSP diagnostics clean on all changed files
