## ADDED Requirements

### Requirement: HookProxy SHALL wrap existing Hook implementations as proxy components

The `HookProxy` class SHALL implement the `Proxy` protocol and wrap one or more `Hook` instances. It SHALL handle **all 4 hook types** (`pre_turn`, `post_turn`, `pre_tool_use`, `post_tool_use`) by mapping ACP wire messages to `HookInput` events and applying `HookResult` modifications back to the ACP message stream. The existing `Hook` base class, `CallableHook`, `CommandHook`, `PromptHook`, `HookInput`, and `HookResult` types SHALL be reused without modification.

Hook semantics are **per-turn** (as established by `unify-hook-system`): `pre_turn` fires before each prompt is forwarded to the terminal agent, `post_turn` fires after each turn's response is received. In a multi-turn `RunHandle` (with steer/followup), these fire for each turn, not just the first and last.

#### Scenario: HookProxy intercepts session/prompt as pre_turn
- **WHEN** a `HookProxy` with a `pre_turn` hook receives a `session/prompt` message via `proxy/successor`
- **THEN** the proxy SHALL construct `HookInput(event="pre_turn", prompt=<prompt_text>, agent_name=<agent_name>)`
- **AND** SHALL execute the wrapped hook(s)
- **AND** if `HookResult.decision == "deny"`, SHALL NOT forward the message and SHALL return a denial response (blocking, not advisory)
- **AND** if `HookResult.additional_context` is set, SHALL prepend it to the prompt before forwarding
- **AND** if `HookResult.decision == "allow"` (default), SHALL forward the (possibly modified) message to the successor

#### Scenario: HookProxy intercepts tool call as pre_tool_use
- **WHEN** a `HookProxy` with a `pre_tool_use` hook receives a `session/update` message containing a `ToolCallStart` update
- **THEN** the proxy SHALL construct `HookInput(event="pre_tool_use", tool_name=<name>, tool_input=<args>)`
- **AND** SHALL execute the wrapped hook(s)
- **AND** if `HookResult.modified_input` is set, SHALL replace the tool input in the update before forwarding
- **AND** if `HookResult.decision == "deny"`, SHALL NOT forward the tool call (blocking — tool call never reaches terminal agent)

#### Scenario: HookProxy intercepts tool result as post_tool_use
- **WHEN** a `HookProxy` with a `post_tool_use` hook receives a `session/update` message containing a `ToolCallComplete` update
- **THEN** the proxy SHALL construct `HookInput(event="post_tool_use", tool_name=<name>, tool_output=<result>)`
- **AND** SHALL execute the wrapped hook(s)
- **AND** if `HookResult.modified_output` is set, SHALL replace the tool output in the update before forwarding
- **AND** if `HookResult.additional_context` is set, SHALL inject it into the conversation

#### Scenario: HookProxy intercepts agent response as post_turn
- **WHEN** a `HookProxy` with a `post_turn` hook receives a `session/update` message containing a final `AgentMessageChunk` update
- **THEN** the proxy SHALL construct `HookInput(event="post_turn", result=<message_content>)`
- **AND** SHALL execute the wrapped hook(s)
- **AND** if `HookResult.modified_output` is set, SHALL replace the output content in the update before forwarding

#### Scenario: HookProxy with no matching hooks for message type
- **WHEN** a `HookProxy` receives a message type that no wrapped hook matches
- **THEN** the proxy SHALL forward the message without modification (passthrough)

### Requirement: HookProxy and HookAwareTurn SHALL coexist via _hooks=None

Two hook firing mechanisms coexist for ACP agents. The Conductor controls which mechanism is active by controlling whether hooks are passed to `ACPTurn`.

- **HookAwareTurn (v1)**: Fires all 4 hook types in-process within `ACPTurn.execute()`. Tool hooks are advisory (cannot block subprocess). Active when no `HookProxy` is in the proxy chain. Implemented by `unify-hook-system`.
- **HookProxy (v2)**: Fires all 4 hook types at wire-level in the proxy chain. All hooks are blocking (intercepts before terminal agent). Active when `HookProxy` is in the proxy chain. Implemented by this change.

When `HookProxy` is in the chain, the Conductor SHALL pass `_hooks=None` to `ACPTurn`. `HookAwareTurn`'s guard (`if self._hooks is None: return None`) skips all hook firing. This approach avoids interaction with the per-turn `hooks_fired` clearing logic from `unify-hook-system`.

#### Scenario: HookProxy active, HookAwareTurn disabled
- **WHEN** a Conductor has a `HookProxy` in the proxy chain
- **THEN** the Conductor SHALL pass `_hooks=None` to `ACPTurn`
- **AND** `HookAwareTurn` on `ACPTurn` SHALL skip all hook firing (guard: `_hooks is None`)
- **AND** hooks SHALL fire at wire-level via `HookProxy` (blocking)

#### Scenario: No HookProxy, HookAwareTurn active
- **WHEN** a Conductor has no `HookProxy` in the proxy chain
- **THEN** the Conductor SHALL pass the agent's `AgentHooks` to `ACPTurn`
- **AND** `HookAwareTurn` on `ACPTurn` SHALL fire all 4 hook types in-process (advisory for tool hooks)

#### Scenario: Conductor auto-inserts HookProxy
- **WHEN** an ACP agent has hooks configured and no explicit `HookProxy` in `proxy_chain`
- **THEN** the Conductor SHALL auto-insert a `HookProxy` at chain position 0 (closest to client)
- **AND** the auto-inserted `HookProxy` SHALL wrap the agent's configured hooks
- **AND** the Conductor SHALL pass `_hooks=None` to `ACPTurn` (HookAwareTurn disabled)

### Requirement: ContextInjectionProxy SHALL inject system context into prompts

The `ContextInjectionProxy` SHALL intercept `session/prompt` messages and prepend configured context (AGENTS.md content, skill instructions, system prompt customizations) to the prompt text before forwarding to the successor. This is separate from `HookProxy`'s `pre_run` `additional_context` — `ContextInjectionProxy` handles declarative context sources (files, skills), while `HookProxy` handles dynamic hook-driven context injection.

#### Scenario: Context injection with AGENTS.md
- **WHEN** a `ContextInjectionProxy` with `agents_md: true` receives a `session/prompt`
- **THEN** the proxy SHALL read the AGENTS.md file from the agent's working directory
- **AND** SHALL prepend the content to the prompt as system context
- **AND** SHALL forward the modified prompt to the successor

#### Scenario: Context injection with skills
- **WHEN** a `ContextInjectionProxy` with configured skills receives a `session/prompt`
- **THEN** the proxy SHALL load skill instructions from the configured skill paths
- **AND** SHALL inject them as context metadata in the prompt
- **AND** SHALL forward the modified prompt to the successor

### Requirement: ToolProviderProxy SHALL expose tools via MCP-over-ACP

The `ToolProviderProxy` SHALL intercept `session/prompt` or `session/update` messages to inject tool definitions. It SHALL reuse `AcpMcpTransport` and `AcpMcpConnectionManager` for MCP-over-ACP communication. Tools provided by this proxy SHALL be available to the terminal agent as if they were native tools.

#### Scenario: Tool provider injects tools
- **WHEN** a `ToolProviderProxy` with configured MCP servers is initialized
- **THEN** the proxy SHALL connect to the configured MCP servers via `AcpMcpTransport`
- **AND** SHALL advertise tool capabilities during `proxy/initialize`
- **AND** SHALL intercept tool call requests from the terminal agent and route them to the appropriate MCP server

#### Scenario: Tool provider handles tool call
- **WHEN** the terminal agent requests a tool call that belongs to the proxy's MCP servers
- **THEN** the proxy SHALL route the tool call to the appropriate MCP server via `AcpMcpConnectionManager`
- **AND** SHALL return the tool result to the terminal agent

### Requirement: Proxy implementations SHALL be registrable via type discriminator

Each built-in proxy implementation SHALL register a unique `type` string that maps to its class. The YAML configuration `proxy_chain[].type` field SHALL use this string to instantiate the correct proxy class. New proxy implementations SHALL be registrable via an entry point or registration function.

#### Scenario: Proxy type registration
- **WHEN** the system loads proxy chain configuration
- **THEN** it SHALL look up each `type` value in the proxy registry
- **AND** SHALL instantiate the corresponding proxy class with the config entry
- **AND** SHALL raise a clear error if the type is not registered
