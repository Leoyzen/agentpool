## ADDED Requirements

### Requirement: ACPTurn fires all 4 hook types via HookAwareTurn

`ACPTurn.execute()` SHALL fire all 4 hook types via the `HookAwareTurn` mixin:
- `pre_turn` hooks SHALL fire before the ACP prompt is sent to the subprocess
- `post_turn` hooks SHALL fire after the ACP response completes (in `finally` block)
- `pre_tool_use` hooks (advisory) SHALL fire when a `ToolCallStart` event is received from the ACP subprocess
- `post_tool_use` hooks SHALL fire when a `ToolCallComplete` event is received

- `pre_tool_use` hooks fired on `ToolCallStart` SHALL be advisory — the hook result `decision="deny"` SHALL be logged as a warning but SHALL NOT block tool execution (the subprocess has already started)
- `post_tool_use` hooks fired on `ToolCallComplete` SHALL be able to modify the tool output via `modified_output` in the `HookResult`
- `ACPTurn` SHALL inherit from `HookAwareTurn` mixin
- `ACPAgent` SHALL pass its `AgentHooks` instance to `ACPTurn` during construction (ACPAgent already accepts `hooks` param at line 159)

**Known gap (future work)**: `ACPTurn.execute()` is only called in SessionPool mode (via `RunHandle.start()`). In standalone mode, `ACPAgent._stream_events()` uses an inline implementation that does NOT call `ACPTurn.execute()`. For ACP standalone, `pre_turn`/`post_turn` hooks continue to fire in `_run_stream_once()` (retained). Additionally, `ACPAgent.create_turn()` (`acp_agent.py:648-652`) has a TODO noting that `ACPAgentAPI` does not fully implement `ACPClientProtocol` (missing `stream_events()` and `get_messages()`), so `ACPTurn.execute()` may require an adapter before it works in SessionPool mode. These are tracked as future work (design.md Future Work, tasks.md section 11).

#### Scenario: pre_turn fires before ACP prompt
- **WHEN** ACPTurn.execute() begins
- **THEN** `fire_pre_turn_hooks()` is called with the prompt
- **AND** if the hook returns `decision="deny"`, the ACP prompt is not sent

#### Scenario: post_turn fires after ACP response
- **WHEN** the ACP subprocess completes the response
- **THEN** `fire_post_turn_hooks()` is called with the result
- **AND** if the hook returns `modified_output`, the result is replaced

#### Scenario: Advisory pre_tool_use on ToolCallStart
- **WHEN** ACPTurn receives a ToolCallStart event from the subprocess
- **THEN** `fire_pre_tool_hooks()` is called with the tool name and raw input
- **AND** if the hook returns `decision="deny"`, a warning is logged
- **AND** tool execution is NOT blocked (subprocess already executing)

#### Scenario: post_tool_use modifies output on ToolCallComplete
- **WHEN** ACPTurn receives a ToolCallComplete event
- **THEN** `fire_post_tool_hooks()` is called with the tool name and output
- **AND** if the hook returns `modified_output`, the tool output is replaced in the event

#### Scenario: ACP agent passes hooks to ACPTurn
- **WHEN** an ACP agent is constructed with a hooks configuration
- **THEN** the AgentHooks instance is passed to ACPTurn during turn creation
- **AND** ACPTurn uses the hooks via the HookAwareTurn mixin

### Requirement: ACP permission request triggers blocking pre_tool_use hooks

When the ACP subprocess sends a `session/request_permission` for a tool call, `ACPClientHandler.request_permission()` SHALL fire `pre_tool_use` hooks with blocking semantics. If any hook returns `decision="deny"`, the permission response SHALL deny the tool execution.

- The hook SHALL receive `tool_name` and `tool_input` from the permission request
- Hooks SHALL fire **before** the `auto_approve` check in `request_permission()` (line 217 of `client_handler.py`). Priority chain: hooks → auto_approve → callback → input_provider
- If `decision="deny"`, the permission response SHALL be `allowed=False` with the hook's `reason`
- If `decision="allow"`, the permission response SHALL be `allowed=True`
- If `decision="ask"`, the default permission behavior SHALL apply (forward to user)

#### Scenario: Hook denies ACP tool via permission request
- **WHEN** the ACP subprocess sends a session/request_permission for tool "bash"
- **AND** a pre_tool_use hook returns `decision="deny"` with reason "Command not allowed"
- **THEN** the permission response SHALL be `allowed=False`
- **AND** the reason SHALL contain "Command not allowed"
- **AND** the subprocess does not execute the tool

#### Scenario: Hook allows ACP tool via permission request
- **WHEN** the ACP subprocess sends a session/request_permission for tool "read"
- **AND** a pre_tool_use hook returns `decision="allow"`
- **THEN** the permission response SHALL be `allowed=True`
- **AND** the subprocess executes the tool

### Requirement: HookAwareTurn disables when HookProxy is active

When an ACP proxy chain with a `HookProxy` component is active (from the `acp-proxy-chain-refactor` change), `ACPTurn`'s `HookAwareTurn` SHALL disable its hook firing to prevent double-firing. The `hooks_fired` guard on `AgentRunContext` SHALL be used to detect that hooks were already fired at the wire level by `HookProxy`.

#### Scenario: HookAwareTurn skips when HookProxy active
- **WHEN** an ACP agent has a proxy chain with a HookProxy
- **AND** HookProxy fires `pre_turn` at the wire level
- **THEN** ACPTurn's `fire_pre_turn_hooks()` SHALL check `hooks_fired` and skip
- **AND** no double-firing occurs
