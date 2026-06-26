## ADDED Requirements

### Requirement: create_child_session auto-emits SpawnSessionStart
`AgentContext.create_child_session()` SHALL automatically construct and emit a `SpawnSessionStart` event after creating the child session. Callers SHALL NOT manually construct or emit `SpawnSessionStart`.

#### Scenario: Auto-emit with tool_call_id
- **WHEN** `create_child_session(agent_name="expert", agent_type="native")` is called from a tool context with `ctx.tool_call_id = "tc-123"`
- **THEN** the emitted `SpawnSessionStart` SHALL have `tool_call_id="tc-123"`
- **AND** the event SHALL be emitted via `self.events.emit_event()` (not `self.node._events`)

#### Scenario: Auto-emit with explicit tool_call_id override
- **WHEN** `create_child_session(agent_name="expert", agent_type="native", tool_call_id="custom-tc")` is called
- **THEN** the emitted `SpawnSessionStart` SHALL have `tool_call_id="custom-tc"`

#### Scenario: Depth auto-computed
- **WHEN** `create_child_session()` is called and `self.run_ctx.depth = 0`
- **THEN** the emitted `SpawnSessionStart` SHALL have `depth=1`

### Requirement: MAX_SUBAGENT_DEPTH enforced at create_child_session
`create_child_session()` SHALL reject child session creation when `child_depth > MAX_SUBAGENT_DEPTH` (where `MAX_SUBAGENT_DEPTH = 1`).

#### Scenario: Depth limit exceeded
- **WHEN** `create_child_session()` is called with `self.run_ctx.depth = 1` (child_depth would be 2)
- **THEN** a `SubagentDepthError` SHALL be raised
- **AND** no `SpawnSessionStart` event SHALL be emitted

#### Scenario: Depth limit not exceeded
- **WHEN** `create_child_session()` is called with `self.run_ctx.depth = 0` (child_depth would be 1)
- **THEN** the child session SHALL be created and `SpawnSessionStart` SHALL be emitted

### Requirement: No getattr in create_child_session implementation
`create_child_session()` SHALL access `tool_call_id` and `depth` via direct typed field access (`self.tool_call_id`, `self.run_ctx.depth`), NOT via `getattr()`.

#### Scenario: Type-safe field access
- **WHEN** `create_child_session()` reads `tool_call_id`
- **THEN** it SHALL use `self.tool_call_id` (typed field on `AgentContext`)
- **AND** it SHALL NOT use `getattr(self, 'tool_call_id', None)`

### Requirement: Team/teamrun unaffected by auto-emit
`team.py` and `teamrun.py` SHALL NOT be affected by `create_child_session()` auto-emit because they use `yield` in async generators and call `session_pool.create_session()` directly.

#### Scenario: Team yield pattern preserved
- **WHEN** `Team.wrap_stream()` yields a `SpawnSessionStart`
- **THEN** the event SHALL be yielded in the async generator (not emitted via `ctx.events.emit_event()`)
- **AND** `create_child_session()` SHALL NOT be called by team code
